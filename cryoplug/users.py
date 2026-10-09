"""User accounts, as in CryoSPARC: passwords, sessions, login throttling and each user's folders.

Accounts live in the CryoPlug database.  As soon as one exists, everyone logs in with a user name and a
password, on localhost too.  Administrators manage the accounts in Settings (or with ``cryoplug user``):
role, password, the folder where the user's projects are created and the folders the user may read data
from (file browser, CryoSPARC imports, maps, models, sequence databases).  Users see their own projects
and the projects shared with them; administrators see everything.

Jobs still run under the Unix account that started CryoPlug, as CryoSPARC jobs run as the cryosparc
user: the folder restrictions apply to what can be chosen in CryoPlug, not to the programs themselves.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from cryoplug.config import Config
from cryoplug.db import Database

ROLES = ("admin", "user")
USERNAME_RE = re.compile(r"[a-z0-9][a-z0-9._@-]{1,63}")
GLOB_CHARS = re.compile(r"[*?\[]")

# Settings stored in the database and edited in Settings › Access: (default, minimum, maximum).
SETTINGS = {
    "session_days": (14, 1, 365),
    "min_password_length": (8, 6, 64),
}
SETTING_LABELS = {"session_days": "Session duration (days)", "min_password_length": "Minimum password length"}

# scrypt is memory-hard (32 MiB per hash here): guessing passwords from a stolen database is slow.
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 15, 8, 1
SCRYPT_MAXMEM = 64 * 1024 * 1024
PBKDF2_ITERATIONS = 600_000

PUBLIC_FIELDS = ("id", "username", "full_name", "email", "role", "projects_dir", "allowed_paths", "disabled",
                 "must_change_password", "created_at", "last_login_at", "password_changed_at")


class UserError(ValueError):
    """Invalid account request (shown to the user)."""


# ------------------------------------------------------------------ passwords
def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _unb64(text: str) -> bytes:
    return base64.b64decode(text.encode())


# At most this many password hashes at once (32 MiB each): a flood of logins queues instead of exhausting memory.
_HASH_SLOTS = threading.BoundedSemaphore(4)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    if hasattr(hashlib, "scrypt"):
        with _HASH_SLOTS:
            digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, maxmem=SCRYPT_MAXMEM, dklen=32)
        return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(digest)}"
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)  # pragma: no cover
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${_b64(salt)}${_b64(digest)}"  # pragma: no cover


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, *rest = (stored or "").split("$")
        if scheme == "scrypt":
            n, r, p, salt, digest = rest
            expected = _unb64(digest)
            with _HASH_SLOTS:
                actual = hashlib.scrypt(password.encode(), salt=_unb64(salt), n=int(n), r=int(r), p=int(p),
                                        maxmem=SCRYPT_MAXMEM, dklen=len(expected))
        elif scheme == "pbkdf2_sha256":
            iterations, salt, digest = rest
            expected = _unb64(digest)
            actual = hashlib.pbkdf2_hmac("sha256", password.encode(), _unb64(salt), int(iterations))
        else:
            return False
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


_dummy_hash: str | None = None


def _dummy() -> str:
    """A hash to check passwords against when the account does not exist (same time either way)."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password(secrets.token_hex(8))
    return _dummy_hash


def generate_password(length: int = 16) -> str:
    alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O, 1/l/I
    return "".join(secrets.choice(alphabet) for _ in range(length))


# ------------------------------------------------------------------ validation
def normalize_username(name: Any) -> str:
    return str(name or "").strip().lower()


def check_username(name: Any) -> str:
    username = normalize_username(name)
    if not USERNAME_RE.fullmatch(username):
        raise UserError("User name: 2 to 64 characters among lower-case letters, digits, '.', '_', '-' and '@', "
                        "starting with a letter or a digit")
    return username


def _check_email(value: Any) -> str:
    email = str(value or "").strip()
    if email and (len(email) > 200 or " " in email or "@" not in email.strip("@")):
        raise UserError(f"Not an e-mail address: {email}")
    return email


def clean_folder(value: Any, what: str = "Folder") -> str:
    text = os.path.expandvars(os.path.expanduser(str(value or "").strip()))
    if not text:
        return ""
    if not os.path.isabs(text):
        raise UserError(f"{what}: give a full path starting with / ({text})")
    return os.path.normpath(text)


def clean_folders(values: Any) -> list[str]:
    if isinstance(values, str):
        values = values.splitlines()
    folders = [clean_folder(v, "Allowed folder") for v in (values or [])]
    folders = list(dict.fromkeys(f for f in folders if f))
    if len(folders) > 50:
        raise UserError("At most 50 allowed folders per user")
    return folders


def path_allowed(value: Any, roots: list[Path] | None) -> bool:
    """Whether a path chosen by a user lies inside one of their folders (``None``: no restriction).

    Symbolic links are resolved first and '..' is refused; for a pattern (frames_*.mrc) its fixed folder is checked."""
    if roots is None:
        return True
    text = os.path.expanduser(str(value or "").strip())
    if not text:
        return True
    if ".." in Path(text).parts:
        return False  # after a wildcard it would only be resolved when the pattern is expanded
    wildcard = GLOB_CHARS.search(text)
    if wildcard:
        text = os.path.dirname(text[:wildcard.start()]) or "/"
    if not os.path.isabs(text):
        return False  # would depend on the server's working directory
    real = os.path.realpath(text)
    for root in roots:
        top = os.path.realpath(str(root))
        if real == top or real.startswith(top.rstrip(os.sep) + os.sep):
            return True
    return False


def public(user: dict[str, Any] | None) -> dict[str, Any] | None:
    if user is None:
        return None
    return {k: user.get(k) for k in PUBLIC_FIELDS}


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ------------------------------------------------------------------ throttling
class LoginThrottle:
    """Slows down password guessing on an open port.

    After ``per_user`` failed logins for one user name from one address, or ``per_ip`` failures from one
    address, within ``window`` seconds, further attempts from there are refused for ``lockout`` seconds.
    Keys include the client address, so that nobody can lock a colleague out from elsewhere.  Loopback
    addresses only have the per-user limit: behind an SSH tunnel or a reverse proxy everyone shares them.

    Each attempt counts as a failure from the start (``begin``) until ``success``, so that attempts sent in
    parallel cannot all get through before the first failures are recorded."""

    def __init__(self, per_user: int = 5, per_ip: int = 20, window: float = 600.0, lockout: float = 300.0):
        self.per_user, self.per_ip, self.window, self.lockout = per_user, per_ip, window, lockout
        self._failures: dict[str, list[float]] = {}
        self._blocked: dict[str, float] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _limits(ip: str, username: str, per_ip: int, per_user: int) -> list[tuple[str, int]]:
        shared = ip.startswith("127.") or ip in ("::1", "localhost", "testclient", "")
        return [(f"user:{ip}:{username}", per_user)] + ([] if shared else [(f"ip:{ip}", per_ip)])

    def wait(self, ip: str, username: str) -> float:
        """Seconds before a new attempt is accepted (0: now)."""
        now = time.monotonic()
        with self._lock:
            return self._wait(ip, username, now)

    def _wait(self, ip: str, username: str, now: float) -> float:
        return max([0.0, *(self._blocked.get(k, 0.0) - now for k, _ in self._limits(ip, username, 0, 0))])

    def begin(self, ip: str, username: str) -> float:
        """Record an attempt (a failure until ``success``). Returns the seconds to wait instead (0: go on)."""
        now = time.monotonic()
        with self._lock:
            wait = self._wait(ip, username, now)
            if wait > 0:
                return wait
            if len(self._failures) > 10_000:  # bounded memory under a flood of user names
                self._failures = {k: v for k, v in self._failures.items() if v and now - v[-1] < self.window}
                self._blocked = {k: v for k, v in self._blocked.items() if v > now}
            for key, limit in self._limits(ip, username, self.per_ip, self.per_user):
                times = [t for t in self._failures.get(key, []) if now - t < self.window] + [now]
                if len(times) >= limit:
                    self._blocked[key] = now + self.lockout
                    times = []
                self._failures[key] = times
            return 0.0

    def success(self, ip: str, username: str) -> None:
        """The attempt recorded by ``begin`` was right: it does not count."""
        with self._lock:
            for key, _ in self._limits(ip, username, 0, 0):
                times = self._failures.get(key)
                if key.startswith("user:"):
                    self._failures.pop(key, None)
                elif times:
                    times.pop()


# ------------------------------------------------------------------ accounts
class Users:
    def __init__(self, db: Database, config: Config):
        self.db = db
        self.config = config
        self.throttle = LoginThrottle()
        self._lock = threading.RLock()
        self._exists: tuple[float, bool] = (float("-inf"), False)

    # ------------------------------------------------------------- state
    def exist(self) -> bool:
        """Whether accounts are in use (checked on every request: cached for 2 s, which also picks up
        accounts created with ``cryoplug user add`` while the server runs)."""
        checked, value = self._exists
        if time.monotonic() - checked > 2.0:
            value = self.db.has_users()
            self._exists = (time.monotonic(), value)
        return value

    def settings(self) -> dict[str, int]:
        stored = self.db.get_meta("auth_settings", {}) or {}
        out = {}
        for key, (default, low, high) in SETTINGS.items():
            try:
                out[key] = min(high, max(low, int(stored.get(key, default))))
            except (TypeError, ValueError):
                out[key] = default
        return out

    def update_settings(self, **changes: Any) -> dict[str, int]:
        current = self.settings()
        for key, value in changes.items():
            if key not in SETTINGS:
                raise UserError(f"Unknown setting '{key}'")
            _, low, high = SETTINGS[key]
            try:
                value = int(value)
            except (TypeError, ValueError):
                raise UserError(f"{SETTING_LABELS[key]}: a whole number is expected") from None
            if not low <= value <= high:
                raise UserError(f"{SETTING_LABELS[key]} must be between {low} and {high}")
            current[key] = value
        self.db.set_meta("auth_settings", current)
        return current

    # ------------------------------------------------------------- folders
    def projects_dir(self, user: dict[str, Any]) -> Path:
        """Where the user's new projects go (by default a folder named after them in [server] projects_root)."""
        return Path(user.get("projects_dir") or self.config.projects_root / user["username"]).expanduser()

    def roots(self, user: dict[str, Any] | None) -> list[Path] | None:
        """Folders a user may browse and read data from (``None``: no restriction, for administrators)."""
        if not user or user.get("role") == "admin":
            return None
        return [self.projects_dir(user), *(Path(p) for p in user.get("allowed_paths") or [])]

    # ------------------------------------------------------------- accounts
    def get(self, username: Any) -> dict[str, Any] | None:
        return self.db.get_user(normalize_username(username))

    def require(self, username: Any) -> dict[str, Any]:
        user = self.get(username)
        if user is None:
            raise UserError(f"No user '{normalize_username(username)}'")
        return user

    def list(self) -> list[dict[str, Any]]:
        return self.db.list_users()

    def generate_password(self) -> str:
        """A random password, long enough for the current minimum."""
        return generate_password(max(16, self.settings()["min_password_length"]))

    def check_password(self, password: Any, username: str = "") -> str:
        password = str(password or "")
        minimum = self.settings()["min_password_length"]
        if len(password) < minimum:
            raise UserError(f"The password needs at least {minimum} characters")
        if len(password) > 256:
            raise UserError("The password is too long (256 characters at most)")
        if username and password.strip().lower() == username.lower():
            raise UserError("The password cannot be the user name")
        return password

    def _last_admin(self, user: dict[str, Any]) -> bool:
        return user["role"] == "admin" and not user["disabled"] and self.db.count_users("admin", enabled_only=True) <= 1

    def create(self, username: Any, password: Any, *, role: str = "user", full_name: Any = "", email: Any = "",
               projects_dir: Any = "", allowed_paths: Any = (), must_change_password: bool = False) -> dict[str, Any]:
        with self._lock:
            username = check_username(username)
            if role not in ROLES:
                raise UserError(f"Role must be one of {', '.join(ROLES)}")
            if not self.db.has_users() and role != "admin":
                raise UserError("The first account must be an administrator")
            if self.db.get_user(username):
                raise UserError(f"The user '{username}' already exists")
            password = self.check_password(password, username)
            now = time.time()
            user = self.db.create_user(
                username=username, password_hash=hash_password(password), role=role, full_name=str(full_name or "").strip()[:120],
                email=_check_email(email), projects_dir=clean_folder(projects_dir, "Projects folder"),
                allowed_paths=clean_folders(allowed_paths), must_change_password=int(bool(must_change_password)),
                created_at=now, password_changed_at=now)
            self._exists = (float("-inf"), False)
            return user

    def update(self, username: Any, /, **fields: Any) -> dict[str, Any]:
        with self._lock:
            user = self.require(username)
            data: dict[str, Any] = {}
            if "full_name" in fields:
                data["full_name"] = str(fields["full_name"] or "").strip()[:120]
            if "email" in fields:
                data["email"] = _check_email(fields["email"])
            if "projects_dir" in fields:
                data["projects_dir"] = clean_folder(fields["projects_dir"], "Projects folder")
            if "allowed_paths" in fields:
                data["allowed_paths"] = clean_folders(fields["allowed_paths"])
            if "role" in fields and fields["role"] != user["role"]:
                if fields["role"] not in ROLES:
                    raise UserError(f"Role must be one of {', '.join(ROLES)}")
                if self._last_admin(user):
                    raise UserError("CryoPlug needs at least one active administrator")
                data["role"] = fields["role"]
            if "disabled" in fields and bool(fields["disabled"]) != user["disabled"]:
                if fields["disabled"] and self._last_admin(user):
                    raise UserError("CryoPlug needs at least one active administrator")
                data["disabled"] = int(bool(fields["disabled"]))
            unknown = set(fields) - {"full_name", "email", "projects_dir", "allowed_paths", "role", "disabled"}
            if unknown:
                raise UserError(f"Cannot change {', '.join(sorted(unknown))}")
            if data:
                self.db.update_user(user["username"], **data)
            if data.get("disabled"):
                self.db.delete_user_sessions(user["id"])  # logged out at once
            return self.require(user["username"])

    def set_password(self, username: Any, password: Any, *, must_change: bool = False, keep_session: str | None = None) -> None:
        """New password; every session of the account ends, except ``keep_session`` (the one changing it)."""
        user = self.require(username)
        password = self.check_password(password, user["username"])
        self.db.update_user(user["username"], password_hash=hash_password(password), must_change_password=int(bool(must_change)),
                            password_changed_at=time.time())
        self.db.delete_user_sessions(user["id"], keep=keep_session)

    def delete(self, username: Any, transfer_to: Any = "") -> None:
        with self._lock:
            user = self.require(username)
            if self._last_admin(user):
                raise UserError("CryoPlug needs at least one active administrator")
            heir = ""
            if transfer_to:
                heir = self.require(transfer_to)["username"]
                if heir == user["username"]:
                    raise UserError("Give the projects to another user")
            self.db.reassign_projects(user["username"], heir)
            self.db.delete_user(user["username"])
            self._exists = (float("-inf"), False)

    def adopt_projects(self, username: str) -> int:
        """Projects without an owner (made before accounts existed) go to ``username``."""
        count = 0
        for project in self.db.list_projects(include_archived=True):
            if not project["owner"]:
                self.db.update_project(project["uid"], owner=username)
                count += 1
        return count

    def authenticate(self, username: Any, password: Any) -> dict[str, Any] | None:
        """The account if the password is right and the account active (as slow when the account does not exist)."""
        name = normalize_username(username)
        user = self.db.get_user(name) if USERNAME_RE.fullmatch(name) else None
        if user is None:
            verify_password(str(password or ""), _dummy())
            return None
        if not verify_password(str(password or ""), user["password_hash"]) or user["disabled"]:
            return None
        return user

    # ------------------------------------------------------------- sessions
    def new_session(self, user: dict[str, Any], ip: str = "", agent: str = "") -> str:
        """A random session token for the browser cookie (only its hash is stored)."""
        token = secrets.token_urlsafe(32)
        now = time.time()
        self.db.add_session(_token_hash(token), user["id"], now + self.settings()["session_days"] * 86400, ip[:64], agent[:300])
        self.db.update_user(user["username"], last_login_at=now)
        self.db.purge_sessions()
        return token

    def session_user(self, token: str | None) -> dict[str, Any] | None:
        if not token or len(token) > 128:
            return None
        key = _token_hash(token)
        session = self.db.get_session(key)
        if session is None:
            return None
        now = time.time()
        if session["expires_at"] <= now:
            self.db.delete_session(key)
            return None
        user = self.db.get_user_by_id(session["user_id"])
        if user is None or user["disabled"]:
            return None
        if now - (session["last_seen_at"] or 0) > 60:
            self.db.touch_session(key, now)
        user["session"] = key
        return user

    def end_session(self, token: str | None) -> None:
        if token:
            self.db.delete_session(_token_hash(token))

    def sessions(self, user: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"id": s["token_hash"][:12], "current": s["token_hash"] == user.get("session"), "created_at": s["created_at"],
                 "last_seen_at": s["last_seen_at"], "expires_at": s["expires_at"], "ip": s["ip"], "agent": s["agent"]}
                for s in self.db.list_sessions(user["id"])]

    def end_other_sessions(self, user: dict[str, Any]) -> None:
        self.db.delete_user_sessions(user["id"], keep=user.get("session"))
