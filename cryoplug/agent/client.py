"""HTTP client of a running CryoPlug server, for scripts and AI agents (``cryoplug mcp``, ``cryoplug ai call``).

Where to connect and with which credential, the first found:

* the URL: ``url`` (``--url``), ``$CRYOPLUG_URL``, else the server of the local configuration (``[server] host``
  and ``port``; ``$CRYOPLUG_CONFIG`` or ``--config`` choose the file);
* the credential, sent as ``Authorization: Bearer``: ``token`` (``--token``), ``$CRYOPLUG_TOKEN``, the file named
  by ``$CRYOPLUG_TOKEN_FILE`` or, for the server of the local configuration only, its ``[server] password`` or its
  access token (``data_dir/access_token``, readable by the Unix account that runs CryoPlug).  With user accounts,
  the credential is an API token of the account (Settings › AI agents, or ``cryoplug user token NAME``).

A server on this machine is reached without the HTTP proxy of the environment; ``$CRYOPLUG_CA_FILE`` names the
certificate (authority) of a server using HTTPS with its own certificate.
"""
from __future__ import annotations

import hashlib
import json
import os
import ssl
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

from cryoplug import __version__

WILDCARD_HOSTS = {"", "0.0.0.0", "::"}

AUTH_HELP = {
    "users": ("CryoPlug uses personal accounts: create an API token in Settings › AI agents (or run "
              "`cryoplug user token NAME` on the server) and give it to the AI client as CRYOPLUG_TOKEN."),
    "token": ("CryoPlug needs its access token: run the AI client as the Unix account that runs CryoPlug on the "
              "server machine (it is read from data_dir/access_token), or set CRYOPLUG_TOKEN to the token printed by "
              "`cryoplug url`."),
    "password": "CryoPlug is protected by a password: set CRYOPLUG_TOKEN to the [server] password.",
}


class AgentError(Exception):
    """A problem reported as is to the agent (and to the person it works for)."""


class ApiError(AgentError):
    def __init__(self, status: int, message: str, path: str = "", payload: dict[str, Any] | None = None):
        super().__init__(message)
        self.status = status
        self.path = path
        self.payload = payload or {}  # the decoded answer (e.g. the offset of an upload, with 409)


@dataclass
class Connection:
    url: str
    token: str = ""
    source: str = ""  # where the credential comes from, for the messages
    cafile: str = ""
    local: bool = False  # the server of the local configuration

    @property
    def loopback(self) -> bool:
        host = (urlsplit(self.url).hostname or "").lower()
        return host == "localhost" or host.startswith("127.") or host == "::1"


def local_url(cfg: Any) -> str:
    """The address of the server of a configuration, as seen from the same machine."""
    host = cfg.host
    if host in WILDCARD_HOSTS:
        host = "::1" if host == "::" else "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{'https' if cfg.ssl_certfile else 'http'}://{host}:{cfg.port}"


def connection(config_path: str | os.PathLike | None = None, url: str | None = None, token: str | None = None) -> Connection:
    """Where and how to connect (see the module documentation)."""
    cafile = os.environ.get("CRYOPLUG_CA_FILE", "")
    url = (url or os.environ.get("CRYOPLUG_URL") or "").strip().rstrip("/")
    cfg = None
    if not url:
        from cryoplug.config import load_config

        try:
            cfg = load_config(config_path)
        except (OSError, ValueError) as exc:
            raise AgentError(f"Cannot read the CryoPlug configuration: {exc}. Set CRYOPLUG_URL instead.") from None
        url = local_url(cfg)
    elif not url.startswith(("http://", "https://")):
        url = f"http://{url}"
    conn = Connection(url=url, cafile=cafile, local=cfg is not None)
    if token:
        conn.token, conn.source = token.strip(), "--token"
    elif os.environ.get("CRYOPLUG_TOKEN", "").strip():
        conn.token, conn.source = os.environ["CRYOPLUG_TOKEN"].strip(), "CRYOPLUG_TOKEN"
    elif os.environ.get("CRYOPLUG_TOKEN_FILE"):
        path = Path(os.environ["CRYOPLUG_TOKEN_FILE"]).expanduser()
        try:
            conn.token, conn.source = path.read_text().strip(), str(path)
        except OSError as exc:
            raise AgentError(f"CRYOPLUG_TOKEN_FILE: cannot read {path}: {exc}") from None
    elif cfg is not None:  # the local server: its own credential, never sent to another address
        if cfg.password:
            conn.token, conn.source = cfg.password, "[server] password"
        else:
            path = cfg.data_dir / "access_token"
            try:
                conn.token, conn.source = path.read_text().strip(), str(path)
            except OSError:
                pass  # no token yet (localhost without login), or not readable by this account
        if not conn.cafile and cfg.ssl_certfile and Path(cfg.ssl_certfile).expanduser().is_file():
            conn.cafile = str(Path(cfg.ssl_certfile).expanduser())
    return conn


def api_error(status: int, body: bytes, reason: str = "", path: str = "", credential: str = "") -> ApiError:
    """The error of an HTTP answer, explained for the agent (``credential``: where the refused credential came from)."""
    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    detail = payload.get("detail")
    if isinstance(detail, list):  # request validation errors
        detail = "; ".join(str(d.get("msg", d)) if isinstance(d, dict) else str(d) for d in detail)
    message = str(detail or reason or f"HTTP {status}")
    if status == 401:
        message = AUTH_HELP.get(str(payload.get("auth")), "CryoPlug asks for a login (set CRYOPLUG_TOKEN).")
        if credential:
            message = f"The credential from {credential} was refused. {message}"
    elif status == 403 and payload.get("code") == "password_change":
        message = "The account must choose a new password first: log in once in the web interface."
    return ApiError(status, message, path, payload)


class Client:
    """JSON requests to the CryoPlug HTTP API (the routes of :mod:`cryoplug.server.app`)."""

    def __init__(self, conn: Connection, timeout: float = 60.0):
        self.conn = conn
        self.timeout = timeout
        handlers: list[Any] = []
        if conn.loopback:
            handlers.append(urllib.request.ProxyHandler({}))  # never through the proxy of the environment
        if conn.url.startswith("https://"):
            context = ssl.create_default_context(cafile=conn.cafile or None)
            handlers.append(urllib.request.HTTPSHandler(context=context))
        self._opener = urllib.request.build_opener(*handlers)

    @property
    def url(self) -> str:
        return self.conn.url

    def request(self, method: str, path: str, params: dict[str, Any] | None = None, body: Any = None,
                raw: bool = False, timeout: float | None = None, max_bytes: int | None = None,
                data: bytes | None = None) -> Any:
        """The decoded JSON answer, or (bytes, content type) with ``raw`` (at most ``max_bytes``); ``data`` is sent
        as is (a chunk of a file) instead of the JSON ``body``. Raises :class:`ApiError`."""
        url = self.conn.url + quote(path, safe="/:@-._~")
        query = {k: v for k, v in (params or {}).items() if v is not None}
        if query:
            url += "?" + urlencode({k: (str(v).lower() if isinstance(v, bool) else v) for k, v in query.items()})
        headers = {"Accept": "*/*" if raw else "application/json", "User-Agent": f"cryoplug-agent/{__version__}"}
        if data is not None:
            headers["Content-Type"] = "application/octet-stream"
        elif body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if self.conn.token:
            headers["Authorization"] = f"Bearer {self.conn.token}"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with self._opener.open(req, timeout=timeout or self.timeout) as resp:
                content = resp.read(max_bytes) if max_bytes else resp.read()
                ctype = resp.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            try:
                payload = exc.read()
            except OSError:
                payload = b""
            raise api_error(exc.code, payload, str(exc.reason or ""), path, self.conn.source if self.conn.token else "") from None
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, ssl.SSLError):
                raise AgentError(f"HTTPS certificate of {self.conn.url} not trusted ({reason}): set CRYOPLUG_CA_FILE to "
                                 "the server certificate (or its authority)") from None
            raise AgentError(f"Cannot reach CryoPlug at {self.conn.url} ({reason}). Is the server running "
                             "(`cryoplug start`)? Set CRYOPLUG_URL if it runs elsewhere.") from None
        except TimeoutError:
            raise AgentError(f"CryoPlug ({self.conn.url}) did not answer within {timeout or self.timeout:.0f} s") from None
        if raw:
            return content, ctype
        return json.loads(content) if content else None

    # ------------------------------------------------------------ shortcuts
    def get(self, path: str, /, **params: Any) -> Any:
        return self.request("GET", path, params=params)

    def post(self, path: str, /, body: Any = None, **params: Any) -> Any:
        return self.request("POST", path, params=params, body={} if body is None else body)

    def patch(self, path: str, body: Any) -> Any:
        return self.request("PATCH", path, body=body)


# ------------------------------------------------------------------ uploads
UPLOAD_CHUNK = 16 * 1024 * 1024
_MB = 1 << 20


def file_fingerprint(path: Path) -> str:
    """What identifies a file between two uploads: name, size, modification time (ms) and a hash of its first and
    last MB — the same as the web interface computes over HTTPS, so either can resume the other's upload."""
    st = path.stat()
    fingerprint = f"{path.name}:{st.st_size}:{int(st.st_mtime * 1000)}"
    if not st.st_size:
        return fingerprint
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        digest.update(fh.read(_MB))
        if st.st_size > _MB:
            fh.seek(st.st_size - _MB)
            digest.update(fh.read(_MB))
    return f"{fingerprint}:{digest.hexdigest()[:24]}"


def text_hash(text: str) -> str:
    """cyrb53 over the UTF-16 code units of ``text``, as the web interface identifies a folder by its listing: both
    then resume the same folder upload."""
    h1, h2 = 0xDEADBEEF, 0x41C6CE57
    units = text.encode("utf-16-le")
    for i in range(0, len(units), 2):
        ch = units[i] | units[i + 1] << 8
        h1 = (h1 ^ ch) * 2654435761 & 0xFFFFFFFF
        h2 = (h2 ^ ch) * 1597334677 & 0xFFFFFFFF
    h1 = ((h1 ^ h1 >> 16) * 2246822507 & 0xFFFFFFFF) ^ ((h2 ^ h2 >> 13) * 3266489909 & 0xFFFFFFFF)
    h2 = ((h2 ^ h2 >> 16) * 2246822507 & 0xFFFFFFFF) ^ ((h1 ^ h1 >> 13) * 3266489909 & 0xFFFFFFFF)
    return format(4294967296 * (0x1FFFFF & h2) + h1, "x")


def folder_fingerprint(root: Path, files: list[Path]) -> str:
    """The fingerprint of a folder upload: its listing (relative paths with the folder's name, and sizes), sorted as
    JavaScript sorts strings (UTF-16 code units)."""
    listing = [f"{root.name}/{p.relative_to(root).as_posix()}:{p.stat().st_size}" for p in files]
    return text_hash("|".join(sorted(listing, key=lambda s: s.encode("utf-16-be"))))


def upload_file(client: Any, puid: str, path: str | Path, folder: str = "",
                progress: Callable[[int, int], None] | None = None) -> dict[str, Any]:
    """Send a local file into the uploads folder of a project, by chunks; an interrupted upload resumes, an identical
    file already there is not sent again. Returns the server's description (rel, path, size, reused)."""
    path = Path(path)
    size = path.stat().st_size
    info = client.request("POST", f"/api/projects/{puid}/uploads",
                          body={"name": path.name, "size": size, "folder": folder, "fingerprint": file_fingerprint(path)})
    chunk = int(info.get("chunk_size") or UPLOAD_CHUNK)
    offset = int(info["offset"])
    failures = 0
    with open(path, "rb") as fh:
        while not info["done"]:
            fh.seek(offset)
            try:
                info = client.request("PUT", f"/api/projects/{puid}/uploads", params={"rel": info["rel"], "offset": offset},
                                      data=fh.read(chunk), timeout=600)
                offset, failures = int(info["offset"]), 0
            except AgentError as exc:
                status = exc.status if isinstance(exc, ApiError) else 0
                if status == 409 and "offset" in exc.payload:  # a chunk already there: go on from the server's offset
                    offset = int(exc.payload["offset"])
                    continue
                if 0 < status < 500:
                    raise
                failures += 1  # network trouble, or the server restarting: wait and send the same chunk again
                if failures > 5:
                    raise
                time.sleep(min(30, 2 ** failures))
                continue
            if progress:
                progress(offset, size)
    return info


def upload_folder(client: Any, puid: str, root: str | Path,
                  progress: Callable[[int, int], None] | None = None) -> dict[str, Any]:
    """Send a local folder (its files, keeping their relative paths; hidden files left out) into a subfolder of its
    own in the uploads of a project. Returns {folder, path, files, size}."""
    root = Path(root)
    files = sorted(p for p in root.rglob("*") if p.is_file() and not any(x.startswith(".") for x in p.relative_to(root).parts))
    if not files:
        raise AgentError(f"No file in {root}")
    reserved = client.request("POST", f"/api/projects/{puid}/uploads",
                              body={"reserve_folder": root.name, "fingerprint": folder_fingerprint(root, files)})
    total = sum(p.stat().st_size for p in files)
    done = 0
    for p in files:
        sub = p.relative_to(root).parent.as_posix()
        folder = reserved["folder"] if sub == "." else f"{reserved['folder']}/{sub}"
        upload_file(client, puid, p, folder, (lambda sent, _size, base=done: progress(base + sent, total)) if progress else None)
        done += p.stat().st_size
    return {"folder": reserved["folder"], "path": reserved["path"], "files": len(files), "size": total}
