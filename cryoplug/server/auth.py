"""Access control: login page, session cookies, access token and request checks.

A login is required as soon as the server listens on a non-loopback address,
a password is configured, or ``[server] auth = "always"``.  Without a password
a random access token kept in ``data_dir/access_token`` is used, as in Jupyter:
the start-up banner (and ``cryoplug url``) prints links that log in directly.

Requests that change something must come from the CryoPlug page itself (Origin
check), and without a login only loopback host names are accepted, so that a
web page opened in the same browser cannot drive the API (CSRF, DNS rebinding).
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import html
import ipaddress
import os
import secrets
import socket
import time
from pathlib import Path
from string import Template
from urllib.parse import urlencode, urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from cryoplug.config import Config

SESSION_DAYS = 30
PUBLIC_PATHS = {"/login", "/logout", "/favicon.svg", "/css/app.css"}
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
WILDCARD_HOSTS = {"", "0.0.0.0", "::"}


def is_loopback(host: str) -> bool:
    host = host.strip("[]").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def auth_required(config: Config) -> bool:
    return bool(config.password) or config.auth == "always" or not is_loopback(config.host)


def _secret_file(path: Path, nbytes: int) -> str:
    """Read a random secret from ``path``, creating it (mode 600) on first use."""
    try:
        value = path.read_text().strip()
        if value:
            return value
    except FileNotFoundError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    value = secrets.token_urlsafe(nbytes)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(value + "\n")
    return value


def access_token(config: Config, reset: bool = False) -> str:
    """The access token used when no password is configured."""
    path = config.data_dir / "access_token"
    if reset:
        path.unlink(missing_ok=True)
    return _secret_file(path, 24)


def _lan_addresses() -> list[str]:
    found = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # no packet is sent: this only selects the default-route interface
            found.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        found += [info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)]
    except OSError:
        pass
    return [a for a in dict.fromkeys(found) if not a.startswith("127.")]


def access_urls(config: Config, token: str = "") -> list[str]:
    """URLs to open in a browser, with the access token when one is used."""
    scheme = "https" if config.ssl_certfile else "http"
    if config.host in WILDCARD_HOSTS:
        names = [socket.gethostname(), *_lan_addresses()]
    elif is_loopback(config.host):
        names = ["localhost"]
    else:
        names = [config.host]
    query = f"?{urlencode({'token': token})}" if token else ""
    return [f"{scheme}://{f'[{n}]' if ':' in n else n}:{config.port}/{query}" for n in dict.fromkeys(names)]


def _request_host(request: Request) -> str:
    return urlsplit("//" + request.headers.get("host", "")).hostname or ""


def _same_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    if origin is None:  # not sent by scripts (curl); browsers send it on every POST
        return True
    netloc = urlsplit(origin).netloc.lower()
    allowed = {request.headers.get("host", "").lower(), request.headers.get("x-forwarded-host", "").lower()}
    return bool(netloc) and netloc in allowed


def _safe_next(target: str, fragment: str = "") -> str:
    if not target.startswith("/") or target.startswith("//") or "\\" in target or any(c in target for c in "\r\n"):
        target = "/"
    if fragment.startswith("#") and not any(c in fragment for c in "\r\n"):
        target += fragment
    return target


class Auth:
    def __init__(self, config: Config):
        self.config = config
        self.required = auth_required(config)
        self.mode = ("password" if config.password else "token") if self.required else None
        self.token = access_token(config) if self.mode == "token" else ""
        self.cookie_name = f"cryoplug_session_{config.port}"
        self._key = b""
        if self.required:
            secret = _secret_file(config.data_dir / "session_secret", 32)
            # Changing the password (or resetting the token) invalidates every session.
            self._key = hashlib.sha256(f"{secret}:{self.credential}".encode()).digest()

    @property
    def credential(self) -> str:
        return self.config.password or self.token

    def check(self, value: str) -> bool:
        return bool(value) and hmac.compare_digest(value.encode(), self.credential.encode())

    def _sign(self, message: str) -> str:
        return hmac.new(self._key, message.encode(), hashlib.sha256).hexdigest()

    def new_session(self, lifetime: float = SESSION_DAYS * 86400) -> str:
        expires = str(int(time.time() + lifetime))
        return f"{expires}.{self._sign(expires)}"

    def valid_session(self, value: str | None) -> bool:
        expires, _, signature = (value or "").partition(".")
        if not expires.isdigit() or not hmac.compare_digest(signature.encode(), self._sign(expires).encode()):
            return False
        return int(expires) > time.time()

    def authorized(self, request: Request) -> bool:
        if self.valid_session(request.cookies.get(self.cookie_name)):
            return True
        scheme, _, value = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() == "bearer":
            return self.check(value.strip())
        if scheme.lower() == "basic":
            try:
                return self.check(base64.b64decode(value).decode().partition(":")[2])
            except (binascii.Error, UnicodeDecodeError):
                return False
        return False

    def login_response(self, request: Request, target: str) -> Response:
        response = RedirectResponse(target, status_code=303)
        response.set_cookie(self.cookie_name, self.new_session(), max_age=SESSION_DAYS * 86400, path="/",
                            httponly=True, samesite="lax", secure=request.url.scheme == "https")
        return response


LOGIN_PAGE = Template("""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CryoPlug · Log in</title>
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="/css/app.css">
<script>try { const t = localStorage.getItem('cryoplug.theme'); if (t) document.documentElement.setAttribute('data-theme', t); } catch (e) {}</script>
</head>
<body>
<main class="login-page">
  <form class="login-card" method="post" action="/login">
    <div class="login-brand">
      <img src="/favicon.svg" alt="" width="34" height="34">
      <div><h1>CryoPlug</h1><div class="muted small">$host</div></div>
    </div>
    $error
    <div class="field">
      <label for="password">$label</label>
      <input id="password" name="password" type="password" autocomplete="current-password" required autofocus>
    </div>
    <input type="hidden" name="next" value="$next">
    <input type="hidden" name="hash" id="hash" value="$fragment">
    <button class="btn primary" type="submit">Log in</button>
    <p class="login-hint">$hint</p>
  </form>
</main>
<script>if (location.hash) document.getElementById('hash').value = location.hash;</script>
</body>
</html>
""")


def login_page(auth: Auth, target: str, error: bool = False, fragment: str = "") -> str:
    if auth.mode == "password":
        label, hint = "Password", "The password set in <code>[server] password</code> of the CryoPlug configuration."
    else:
        label = "Access token"
        hint = ("Printed in the terminal when the server started. "
                "Run <code>cryoplug url</code> on the server to show it again.")
    return LOGIN_PAGE.substitute(
        host=html.escape(socket.gethostname()), label=label, hint=hint, next=html.escape(_safe_next(target), quote=True),
        fragment=html.escape(_safe_next("/", fragment)[1:], quote=True),
        error=f'<div class="alert error" role="alert">Wrong {label.lower()}.</div>' if error else "")


def install(app: FastAPI, config: Config) -> Auth:
    """Add the access-control middleware and the /login and /logout routes."""
    auth = Auth(config)

    @app.middleware("http")
    async def access_control(request: Request, call_next):
        if request.method in UNSAFE_METHODS and not _same_origin(request):
            return JSONResponse({"detail": "Cross-origin request refused"}, status_code=403)
        if not auth.required:
            if not is_loopback(_request_host(request)):
                return JSONResponse({"detail": "CryoPlug listens on localhost only: open it at http://localhost "
                                               "(set [server] host = \"0.0.0.0\" to use it from other computers)"},
                                    status_code=403)
            return await call_next(request)
        if request.url.path in PUBLIC_PATHS or auth.authorized(request):
            return await call_next(request)
        rest = [(k, v) for k, v in request.query_params.multi_items() if k != "token"]
        here = request.url.path + (f"?{urlencode(rest)}" if rest else "")
        token = request.query_params.get("token")
        if token and request.method == "GET":
            if auth.check(token):
                return auth.login_response(request, here)
            await asyncio.sleep(1)
        if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
            return RedirectResponse(f"/login?{urlencode({'next': here})}", status_code=303)
        return JSONResponse({"detail": "Authentication required"}, status_code=401)

    @app.get("/login", include_in_schema=False)
    def show_login(next: str = "/") -> Response:
        if not auth.required:
            return RedirectResponse("/", status_code=303)
        return HTMLResponse(login_page(auth, next))

    @app.post("/login", include_in_schema=False)
    async def login(request: Request) -> Response:
        if not auth.required:
            return RedirectResponse("/", status_code=303)
        form = await request.form()
        target = _safe_next(str(form.get("next", "/")), str(form.get("hash", "")))
        if auth.check(str(form.get("password", ""))):
            return auth.login_response(request, target)
        await asyncio.sleep(1)  # slows down password guessing
        return HTMLResponse(login_page(auth, str(form.get("next", "/")), error=True, fragment=str(form.get("hash", ""))),
                            status_code=401)

    @app.post("/logout", include_in_schema=False)
    def logout() -> Response:
        response = JSONResponse({"ok": True})
        response.delete_cookie(auth.cookie_name, path="/")
        return response

    return auth
