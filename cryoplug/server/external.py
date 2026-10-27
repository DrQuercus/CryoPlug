"""Logging in with the session of another web application of the server (``[external_login]``), e.g. Open WebUI:
whoever is logged in there opens the CryoPlug account with the same e-mail address (or user name), without a second
login.

CryoPlug never sees a password.  It forwards the session it is presented — the application's cookie, which browsers
send to every port of the same host name, or its token in ``Authorization: Bearer``, as Open WebUI sends to tool
servers with its "session" authentication — to the application's "who am I" address, and reads whose it is from the
answer (Open WebUI: ``GET /api/v1/auths/`` answers the account, role included, for a valid session).  The session goes
to that address only: directly, never through an HTTP proxy, never following a redirection; the answer is trusted for
``cache_seconds`` (logging out there closes CryoPlug too after that delay).

The application only says who the person is: what they may do is the CryoPlug account's (an administrator there is
not one here).  Accounts awaiting approval are refused (``roles``); with ``create_accounts``, people without a
CryoPlug account get one (role user) at their first visit.  Tokens act as API tokens (no account or settings
changes); a browser session acts as a login.  Logging out of CryoPlug stops using the application's session in that
browser until the person chooses to continue with it on the login page.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from typing import Any

from fastapi import Request

from cryoplug import __version__
from cryoplug.config import ExternalLoginConfig
from cryoplug.users import TOKEN_PREFIX, USERNAME_RE, UserError, Users

log = logging.getLogger("cryoplug.auth")

CREDENTIAL = re.compile(r"[!#-+\--:<-\[\]-~]{8,8192}")  # cookie-octets (RFC 6265): no space, quote, comma, ; or \
INVALID_SECONDS = 60      # a session refused is not asked about again before this
UNREACHABLE_SECONDS = 5   # nor when the application did not answer
FAILURES_PER_IP = 300     # refused sessions from one address within FAILURE_WINDOW, after which it is not asked
FAILURE_WINDOW = 600.0
MAX_ANSWER = 1_000_000


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirection would carry the session elsewhere (urllib keeps Cookie and Authorization): refused."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def field_value(data: Any, path: str) -> Any:
    """``data["a"]["b"]`` for the dotted path "a.b" (None when missing)."""
    for part in path.split("."):
        if not isinstance(data, dict):
            return None
        data = data.get(part)
    return data


class ExternalLogin:
    def __init__(self, settings: ExternalLoginConfig, users: Users, port: int):
        self.settings = settings
        self.users = users
        self.off_cookie = f"cryoplug_external_off_{port}"  # set by logging out: the application's cookie is not used
        self._cache: dict[str, tuple[float, dict[str, Any] | None]] = {}
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()
        self._create_lock = threading.Lock()
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)

    # ------------------------------------------------------------- the request
    def cookie(self, request: Request, ignore_off: bool = False) -> str:
        """The application's session cookie sent by the browser (none after logging out of CryoPlug)."""
        if not self.settings.cookie or (request.cookies.get(self.off_cookie) and not ignore_off):
            return ""
        return request.cookies.get(self.settings.cookie, "")

    def bearer(self, request: Request) -> str:
        """A token in Authorization: Bearer that is not CryoPlug's (its API tokens start with cpt_)."""
        if not self.settings.bearer:
            return ""
        scheme, _, value = request.headers.get("authorization", "").partition(" ")
        value = value.strip()
        return value if scheme.lower() == "bearer" and not value.startswith(TOKEN_PREFIX) else ""

    def identify(self, request: Request, ignore_off: bool = False) -> dict[str, Any] | None:
        """The CryoPlug account of the application's session the request presents (None: none, or not valid)."""
        from cryoplug.server.auth import client_ip

        for kind, value in (("cookie", self.cookie(request, ignore_off)), ("bearer", self.bearer(request))):
            if value:
                found = self.check(kind, value, client_ip(request))
                if found and found.get("user_id"):
                    user = self.users.db.get_user_by_id(found["user_id"])
                    if user is not None and not user["disabled"]:
                        return self._as_user(user, kind)
        return None

    def browser_session(self, request: Request) -> dict[str, Any] | None:
        """For the login page: whose application session the browser has ({"who", "username", "reason"}), even after
        logging out of CryoPlug (None: no valid session)."""
        from cryoplug.server.auth import client_ip

        value = self.cookie(request, ignore_off=True)
        found = self.check("cookie", value, client_ip(request)) if value else None
        if not found:
            return None
        user = self.users.db.get_user_by_id(found["user_id"]) if found.get("user_id") else None
        if user is not None and user["disabled"]:
            return {"who": found["who"], "username": "", "reason": "the CryoPlug account is disabled"}
        return {"who": found["who"], "username": user["username"] if user else "", "reason": found.get("reason", "")}

    def _as_user(self, user: dict[str, Any], kind: str) -> dict[str, Any]:
        user = dict(user)
        user["external"] = self.settings.name
        user["must_change_password"] = False  # they log in there, not with a CryoPlug password
        if kind == "bearer":  # an assistant tool acting for the person: the restrictions of API tokens
            user["token_label"] = f"{self.settings.name} session"
        return user

    # ------------------------------------------------------------- checking a session
    def check(self, kind: str, value: str, ip: str = "") -> dict[str, Any] | None:
        """{"who", "user_id" (None: no CryoPlug account to open), "reason"} for a valid session of the application,
        None for an invalid one.  Answers are cached; refused sessions from one address are limited."""
        if not CREDENTIAL.fullmatch(value or ""):
            return None
        key = hashlib.sha256(f"{kind}\0{value}".encode()).hexdigest()
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(key)
            if hit and hit[0] > now:
                return hit[1]
            recent = [t for t in self._failures.get(ip, []) if now - t < FAILURE_WINDOW]
            if len(recent) >= FAILURES_PER_IP:
                return None
        status, info = self._ask(kind, value)
        if info is None:
            with self._lock:
                self._remember(key, None, UNREACHABLE_SECONDS if status == 0 else INVALID_SECONDS)
                if status != 0:
                    if len(self._failures) > 10_000:
                        self._failures = {k: v for k, v in self._failures.items() if v and now - v[-1] < FAILURE_WINDOW}
                    self._failures[ip] = [t for t in self._failures.get(ip, []) if now - t < FAILURE_WINDOW] + [now]
            return None
        user, who, reason = self.account(info)
        found = {"who": who, "user_id": user["id"] if user else None, "reason": reason}
        if reason and who:
            log.info("%s session of %s not accepted: %s", self.settings.name, who, reason)
        with self._lock:
            self._remember(key, found, self.settings.cache_seconds)
        return found

    def _remember(self, key: str, value: dict[str, Any] | None, seconds: float) -> None:
        now = time.monotonic()
        if len(self._cache) > 5000:
            self._cache = {k: v for k, v in self._cache.items() if v[0] > now}
        if seconds > 0:
            self._cache[key] = (now + seconds, value)

    def _ask(self, kind: str, value: str) -> tuple[int, dict[str, Any] | None]:
        """Ask the application whose session this is: (HTTP status, 0 when unreachable; its answer when 200)."""
        headers = {"Accept": "application/json", "User-Agent": f"CryoPlug/{__version__}"}
        if kind == "cookie":
            headers["Cookie"] = f"{self.settings.cookie}={value}"
        else:
            headers["Authorization"] = f"Bearer {value}"
        try:
            with self._opener.open(urllib.request.Request(self.settings.url, headers=headers), timeout=self.settings.timeout) as resp:
                status, data = resp.status, resp.read(MAX_ANSWER + 1)
        except urllib.error.HTTPError as exc:
            exc.close()
            return exc.code, None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            log.warning("%s does not answer at %s: %s", self.settings.name, self.settings.url, exc)
            return 0, None
        if status != 200 or len(data) > MAX_ANSWER:
            return status, None
        try:
            info = json.loads(data)
        except ValueError:
            return status, None
        return (status, info) if isinstance(info, dict) else (status, None)

    # ------------------------------------------------------------- the CryoPlug account
    def account(self, info: dict[str, Any]) -> tuple[dict[str, Any] | None, str, str]:
        """(the CryoPlug account to open, who the application says it is, why none)."""
        s = self.settings
        who = field_value(info, s.user_field)
        who = who.strip() if isinstance(who, str) else ""
        if not who:
            return None, "", f"the answer has no {s.user_field}"
        if s.roles:
            role = field_value(info, s.role_field)
            if str(role) not in s.roles:
                return None, who, f"its {s.role_field} is {role!r} (accepted: {', '.join(s.roles)})"
        user = self._find(who)
        if user is None and s.create_accounts:
            with self._create_lock:
                user = self._find(who) or self._create(info, who)
        if user is None:
            how = "e-mail address" if s.match == "email" else "user name"
            return None, who, f"no CryoPlug account has this {how}"
        return user, who, ""

    def _find(self, who: str) -> dict[str, Any] | None:
        if self.settings.match == "username":
            return self.users.get(who)
        same = [u for u in self.users.list() if (u.get("email") or "").strip().lower() == who.lower()]
        if len(same) > 1:
            log.warning("Several CryoPlug accounts have the e-mail address %s: none is opened", who)
            return None
        return same[0] if same else None

    def _create(self, info: dict[str, Any], who: str) -> dict[str, Any] | None:
        """A new account (role user, a random password nobody knows) named after the person."""
        if not self.users.exist():  # the first account is an administrator, made on the server
            return None
        by_email = self.settings.match == "email"
        email = who if by_email else str(field_value(info, "email") or "")
        base = who.split("@")[0] if by_email else who
        names = [base, who, *(f"{base}{i}" for i in range(2, 10))] if by_email else [who]
        for raw in names:
            name = re.sub(r"[^a-z0-9._@-]", "", raw.strip().lower())
            if not USERNAME_RE.fullmatch(name) or self.users.get(name):
                continue
            try:
                user = self.users.create(name, secrets.token_urlsafe(24), role="user",
                                         full_name=str(field_value(info, "name") or ""), email=email)
            except UserError as exc:
                log.warning("No CryoPlug account made for %s: %s", who, exc)
                return None
            log.info("CryoPlug account %s made for %s (%s)", name, who, self.settings.name)
            return user
        return None
