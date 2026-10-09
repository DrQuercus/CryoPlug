"""Access control: login page, sessions, user accounts, access token and request checks.

CryoPlug is protected in one of three ways:

* **user accounts** (as in CryoSPARC), as soon as one exists in the database: everyone logs in with a
  user name and a password, on localhost too (see :mod:`cryoplug.users`);
* a **shared password** (``[server] password``);
* a random **access token** kept in ``data_dir/access_token``, as in Jupyter, when the server listens on
  the network (or ``[server] auth = "always"``) without accounts or password: the start-up banner (and
  ``cryoplug url``) prints links that log in directly.

Without any of them the server only answers on localhost.  Requests that change something must come from
the CryoPlug page itself (Origin check), and without a login only loopback host names are accepted, so that a
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
import math
import os
import secrets
import socket
import threading
import time
from pathlib import Path
from string import Template
from typing import Any, Callable
from urllib.parse import urlencode, urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from cryoplug.config import Config
from cryoplug.users import Users, normalize_username

SESSION_DAYS = 30  # shared password / access token sessions (accounts: Settings › Access)
PUBLIC_PATHS = {"/login", "/logout", "/favicon.svg", "/css/app.css"}
# All an account that must choose a new password can reach, besides the page itself.
PASSWORD_CHANGE_PATHS = {"/api/info", "/api/me", "/api/me/password"}
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
WILDCARD_HOSTS = {"", "0.0.0.0", "::"}

# Whoever opens CryoPlug without accounts (localhost, shared password or access token) owns the server.
OWNER: dict[str, Any] = {"id": None, "username": "", "full_name": "", "role": "admin", "builtin": True}


def is_loopback(host: str) -> bool:
    host = host.strip("[]").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def auth_required(config: Config) -> bool:
    """Whether a shared password or the access token is needed (when no account exists)."""
    return bool(config.password) or config.auth == "always" or not is_loopback(config.host)


def auth_mode(config: Config, has_users: bool) -> str | None:
    """"users", "password", "token", or None (localhost without login)."""
    if has_users:
        return "users"
    if config.password:
        return "password"
    return "token" if auth_required(config) else None


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


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


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


def _basic_credentials(value: str) -> tuple[str, str]:
    try:
        user, _, password = base64.b64decode(value).decode().partition(":")
    except (binascii.Error, UnicodeDecodeError):
        return "", ""
    return user, password


class Auth:
    def __init__(self, config: Config, users: Users):
        self.config = config
        self.users = users
        self.cookie_name = f"cryoplug_session_{config.port}"
        self._token = access_token(config) if self.mode == "token" else ""
        self._key: tuple[str, bytes] | None = None
        # Scripts using Basic authentication: a right password is remembered for a few minutes (keyed by a hash
        # of the header), instead of hashing it again (32 MiB of memory) at every request.
        self._basic: dict[str, tuple[int, float, float]] = {}
        self._basic_lock = threading.Lock()

    # ------------------------------------------------------------- mode
    @property
    def mode(self) -> str | None:
        """"users", "password", "token", or None (localhost without login)."""
        return auth_mode(self.config, self.users.exist())

    @property
    def required(self) -> bool:
        return self.mode is not None

    @property
    def token(self) -> str:
        if self.mode != "token":
            return ""
        if not self._token:
            self._token = access_token(self.config)
        return self._token

    # ------------------------------------------------- shared password / token
    @property
    def credential(self) -> str:
        return self.config.password or self.token

    def check(self, value: str) -> bool:
        credential = self.credential
        return bool(value) and bool(credential) and hmac.compare_digest(value.encode(), credential.encode())

    def _sign(self, message: str) -> str:
        credential = self.credential
        if self._key is None or self._key[0] != credential:
            secret = _secret_file(self.config.data_dir / "session_secret", 32)
            # Changing the password (or resetting the token) invalidates every session.
            self._key = (credential, hashlib.sha256(f"{secret}:{credential}".encode()).digest())
        return hmac.new(self._key[1], message.encode(), hashlib.sha256).hexdigest()

    def new_session(self, lifetime: float = SESSION_DAYS * 86400) -> str:
        expires = str(int(time.time() + lifetime))
        return f"{expires}.{self._sign(expires)}"

    def valid_session(self, value: str | None) -> bool:
        expires, _, signature = (value or "").partition(".")
        if not expires.isdigit() or not hmac.compare_digest(signature.encode(), self._sign(expires).encode()):
            return False
        return int(expires) > time.time()

    # ------------------------------------------------------------- requests
    def identify(self, request: Request, mode: str) -> dict[str, Any] | None:
        """Who sends the request (None: nobody logged in). May be slow (password hashing): not on the event loop."""
        cookie = request.cookies.get(self.cookie_name)
        scheme, _, value = request.headers.get("authorization", "").partition(" ")
        scheme = scheme.lower()
        if mode == "users":
            user = self.users.session_user(cookie) if cookie else None
            if user is None and scheme == "basic":  # scripts: curl -u name:password
                user = self._basic_user(request, value)
            return user
        if self.valid_session(cookie):
            return OWNER
        if scheme == "bearer" and self.check(value.strip()):
            return OWNER
        if scheme == "basic" and self.check(_basic_credentials(value)[1]):
            return OWNER
        return None

    def _basic_user(self, request: Request, header: str) -> dict[str, Any] | None:
        key = hashlib.sha256(header.encode()).hexdigest()
        now = time.monotonic()
        with self._basic_lock:
            known = self._basic.get(key)
        if known and known[1] > now:
            user = self.users.db.get_user_by_id(known[0])
            # still valid unless the account was disabled or its password changed meanwhile
            if user and not user["disabled"] and user["password_changed_at"] == known[2]:
                return user
        name, password = _basic_credentials(header)
        name, ip = normalize_username(name), client_ip(request)
        if self.users.throttle.begin(ip, name) > 0:
            return None
        user = self.users.authenticate(name, password)
        if user is None:
            return None
        self.users.throttle.success(ip, name)
        with self._basic_lock:
            if len(self._basic) > 1000:
                self._basic = {k: v for k, v in self._basic.items() if v[1] > now}
            self._basic[key] = (user["id"], now + 300, user["password_changed_at"])
        return user

    def login_response(self, request: Request, target: str, token: str | None = None) -> Response:
        response = RedirectResponse(target, status_code=303)
        self.set_cookie(response, request, token)
        return response

    def set_cookie(self, response: Response, request: Request, token: str | None = None) -> None:
        """Session cookie: an account session token, or a signed session for the shared password / token."""
        days = self.users.settings()["session_days"] if token else SESSION_DAYS
        response.set_cookie(self.cookie_name, token or self.new_session(), max_age=days * 86400, path="/",
                            httponly=True, samesite="lax", secure=request.url.scheme == "https")


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
    $fields
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

PASSWORD_FIELD = """<div class="field">
      <label for="password">$label</label>
      <input id="password" name="password" type="password" autocomplete="current-password" required $focus>
    </div>"""


def login_page(auth: Auth, target: str, error: str = "", fragment: str = "", username: str = "", mode: str | None = None) -> str:
    mode = mode or auth.mode
    if mode == "users":
        fields = ('<div class="field">\n      <label for="username">User name</label>\n      '
                  f'<input id="username" name="username" type="text" autocomplete="username" autocapitalize="none" '
                  f'spellcheck="false" required value="{html.escape(username, quote=True)}" {"" if username else "autofocus"}>\n    </div>\n    '
                  + Template(PASSWORD_FIELD).substitute(label="Password", focus="autofocus" if username else ""))
        hint = "Forgot your password? Ask a CryoPlug administrator to set a new one."
    elif mode == "password":
        fields = Template(PASSWORD_FIELD).substitute(label="Password", focus="autofocus")
        hint = "The password set in <code>[server] password</code> of the CryoPlug configuration."
    else:
        fields = Template(PASSWORD_FIELD).substitute(label="Access token", focus="autofocus")
        hint = ("Printed in the terminal when the server started. "
                "Run <code>cryoplug url</code> on the server to show it again.")
    return LOGIN_PAGE.substitute(
        host=html.escape(socket.gethostname()), fields=fields, hint=hint, next=html.escape(_safe_next(target), quote=True),
        fragment=html.escape(_safe_next("/", fragment)[1:], quote=True),
        error=f'<div class="alert error" role="alert">{html.escape(error)}</div>' if error else "")


def install(app: FastAPI, config: Config, users: Users,
            gate: Callable[[dict[str, Any], Request], bool] | None = None) -> Auth:
    """Add the access-control middleware and the /login and /logout routes.

    ``gate(user, request)`` may refuse a request of a logged-in user (answered 404, as if it did not exist)."""
    auth = Auth(config, users)

    def authorize(request: Request, mode: str) -> tuple[dict[str, Any] | None, str]:
        user = auth.identify(request, mode)
        if user is None:
            return None, "login"
        path = request.url.path
        if user.get("must_change_password") and path.startswith("/api/") and path not in PASSWORD_CHANGE_PATHS:
            return user, "password"
        if gate is not None and not gate(user, request):
            return user, "hidden"
        return user, ""

    @app.middleware("http")
    async def access_control(request: Request, call_next):
        if request.method in UNSAFE_METHODS and not _same_origin(request):
            return JSONResponse({"detail": "Cross-origin request refused"}, status_code=403)
        mode = auth.mode
        if mode is None:
            if not is_loopback(_request_host(request)):
                return JSONResponse({"detail": "CryoPlug listens on localhost only: open it at http://localhost "
                                               "(set [server] host = \"0.0.0.0\" to use it from other computers)"},
                                    status_code=403)
            request.state.user = OWNER
            return await call_next(request)
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)
        user, refusal = await run_in_threadpool(authorize, request, mode)
        if user is not None:
            request.state.user = user
            if refusal == "password":
                return JSONResponse({"detail": "Choose a new password first", "code": "password_change"}, status_code=403)
            if refusal == "hidden":
                return JSONResponse({"detail": "Not found"}, status_code=404)
            return await call_next(request)
        rest = [(k, v) for k, v in request.query_params.multi_items() if k != "token"]
        here = request.url.path + (f"?{urlencode(rest)}" if rest else "")
        token = request.query_params.get("token")
        if token and request.method == "GET" and mode == "token":
            if auth.check(token):
                return auth.login_response(request, here)
            await asyncio.sleep(1)
        if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
            return RedirectResponse(f"/login?{urlencode({'next': here})}", status_code=303)
        return JSONResponse({"detail": "Authentication required"}, status_code=401)

    # Added last, so it wraps every answer (also the refusals above): no framing by other sites, no MIME sniffing.
    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response

    @app.get("/login", include_in_schema=False)
    def show_login(next: str = "/") -> Response:
        if not auth.required:
            return RedirectResponse("/", status_code=303)
        return HTMLResponse(login_page(auth, next))

    @app.post("/login", include_in_schema=False)
    async def login(request: Request) -> Response:
        mode = auth.mode
        if mode is None:
            return RedirectResponse("/", status_code=303)
        form = await request.form()
        raw_next, fragment = str(form.get("next", "/")), str(form.get("hash", ""))
        target = _safe_next(raw_next, fragment)
        password = str(form.get("password", ""))
        if mode != "users":
            if auth.check(password):
                return auth.login_response(request, target)
            await asyncio.sleep(1)  # slows down password guessing
            label = "password" if mode == "password" else "access token"
            return HTMLResponse(login_page(auth, raw_next, error=f"Wrong {label}.", fragment=fragment, mode=mode), status_code=401)

        name, ip = normalize_username(form.get("username", "")), client_ip(request)
        wait = users.throttle.begin(ip, name)
        if wait > 0:
            return HTMLResponse(login_page(auth, raw_next, error=f"Too many failed attempts: try again in {math.ceil(wait / 60)} min.",
                                           fragment=fragment, username=name, mode=mode), status_code=429)
        user = await run_in_threadpool(users.authenticate, name, password)
        if user is None:
            await asyncio.sleep(1)
            return HTMLResponse(login_page(auth, raw_next, error="Wrong user name or password.", fragment=fragment,
                                           username=name, mode=mode), status_code=401)
        users.throttle.success(ip, name)
        token = await run_in_threadpool(users.new_session, user, ip, request.headers.get("user-agent", ""))
        return auth.login_response(request, target, token)

    @app.post("/logout", include_in_schema=False)
    def logout(request: Request) -> Response:
        if auth.users.exist():
            users.end_session(request.cookies.get(auth.cookie_name))
        response = JSONResponse({"ok": True})
        response.delete_cookie(auth.cookie_name, path="/")
        return response

    return auth
