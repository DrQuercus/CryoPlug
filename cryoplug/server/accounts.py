"""Accounts API: the logged-in user (Settings › My account) and, for administrators, the users and the
access settings (Settings › Users, Settings › Access).  Also the permission helpers used by the other routes."""
from __future__ import annotations

from collections import Counter
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from cryoplug.manager import Manager
from cryoplug.server.auth import OWNER, Auth, client_ip, is_loopback
from cryoplug.users import SETTINGS, UserError, normalize_username, public, verify_password


# ------------------------------------------------------------------ permissions
def current_user(request: Request) -> dict[str, Any]:
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(401, "Authentication required")
    return user


def is_admin(user: dict[str, Any]) -> bool:
    return user.get("role") == "admin"


def require_admin(request: Request) -> dict[str, Any]:
    user = current_user(request)
    if not is_admin(user):
        raise HTTPException(403, "Only administrators can do this")
    return user


def can_access(user: dict[str, Any], project: dict[str, Any]) -> bool:
    """Owner, members and administrators work in a project."""
    name = user.get("username")
    return is_admin(user) or (bool(name) and (project.get("owner") == name or name in project.get("members", [])))


def can_manage(user: dict[str, Any], project: dict[str, Any]) -> bool:
    """Owner and administrators rename, share, archive or remove a project."""
    return is_admin(user) or (bool(user.get("username")) and project.get("owner") == user["username"])


def author(user: dict[str, Any]) -> str:
    """Recorded as the creator of jobs and projects (nobody without accounts)."""
    return "" if user.get("builtin") else user["username"]


# ------------------------------------------------------------------ routes
def install(app: FastAPI, manager: Manager, auth: Auth) -> None:
    users = auth.users
    config = manager.config

    def describe(user: dict[str, Any]) -> dict[str, Any]:
        if user.get("builtin"):
            return {**{k: OWNER[k] for k in ("username", "full_name", "role")}, "builtin": True}
        info = public(user)
        info["projects_folder"] = str(users.projects_dir(user))
        roots = users.roots(user)
        info["folders"] = [str(r) for r in roots] if roots is not None else None
        return info

    def account(request: Request) -> dict[str, Any]:
        user = current_user(request)
        if user.get("builtin"):
            raise HTTPException(400, "CryoPlug is used without user accounts: create one in Settings › Users")
        return user

    def owned() -> Counter:
        return Counter(p["owner"] for p in manager.db.list_projects(include_archived=True))

    def listed(user: dict[str, Any], counts: Counter | None = None) -> dict[str, Any]:
        info = public(user)
        info["projects_folder"] = str(users.projects_dir(user))
        info["num_projects"] = (counts if counts is not None else owned())[user["username"]]
        return info

    def new_password(body: dict[str, Any]) -> tuple[str, bool]:
        """The password given, or a generated one (shown once to the administrator)."""
        password = str(body.get("password") or "")
        if password:
            return password, False
        return users.generate_password(), True

    # --------------------------------------------------------- my account
    app.state.describe_user = describe

    @app.get("/api/me")
    def me(request: Request) -> dict[str, Any]:
        return describe(current_user(request))

    @app.patch("/api/me")
    def update_me(request: Request, body: dict = Body(...)) -> dict[str, Any]:
        user = account(request)
        fields = {k: body[k] for k in ("full_name", "email") if k in body}
        if set(body) - set(fields):
            raise HTTPException(403, "Only an administrator can change this")
        return describe(users.update(user["username"], **fields))

    @app.post("/api/me/password")
    def change_password(request: Request, body: dict = Body(...)) -> dict[str, Any]:
        user = account(request)
        ip = client_ip(request)
        if users.throttle.begin(ip, user["username"]) > 0:
            raise HTTPException(429, "Too many attempts: try again in a few minutes")
        current, new = str(body.get("current") or ""), str(body.get("new") or "")
        if not verify_password(current, user["password_hash"]):
            raise HTTPException(400, "The current password is wrong")
        users.throttle.success(ip, user["username"])
        if new == current:
            raise UserError("Choose a password different from the current one")
        users.set_password(user["username"], new, keep_session=user.get("session"))
        return {"ok": True}

    @app.get("/api/me/sessions")
    def my_sessions(request: Request) -> list[dict[str, Any]]:
        return users.sessions(account(request))

    @app.post("/api/me/sessions/end-others")
    def end_other_sessions(request: Request) -> dict[str, Any]:
        users.end_other_sessions(account(request))
        return {"ok": True}

    # --------------------------------------------------------- users (admin)
    @app.get("/api/users")
    def list_users(request: Request) -> list[dict[str, Any]]:
        require_admin(request)
        counts = owned()
        return [listed(u, counts) for u in users.list()]

    @app.post("/api/users")
    def create_user(request: Request, body: dict = Body(...)) -> JSONResponse:
        require_admin(request)
        first = not manager.db.has_users()
        password, generated = new_password(body)
        user = users.create(body.get("username"), password, role=str(body.get("role") or ("admin" if first else "user")),
                            full_name=body.get("full_name", ""), email=body.get("email", ""), projects_dir=body.get("projects_dir", ""),
                            allowed_paths=body.get("allowed_paths") or [],
                            must_change_password=bool(body.get("must_change_password")) and not first)
        if first:  # whoever turns accounts on becomes this administrator: the projects made so far are theirs
            users.adopt_projects(user["username"])
        response = JSONResponse({"user": listed(user), "password": password if generated else None, "first": first})
        if first:  # and they stay logged in, now with their account
            auth.set_cookie(response, request, users.new_session(user, client_ip(request), request.headers.get("user-agent", "")))
        return response

    @app.patch("/api/users/{username}")
    def update_user(username: str, request: Request, body: dict = Body(...)) -> dict[str, Any]:
        require_admin(request)
        return listed(users.update(username, **body))

    @app.post("/api/users/{username}/password")
    def reset_password(username: str, request: Request, body: dict = Body(default={})) -> dict[str, Any]:
        admin = require_admin(request)
        body = body or {}
        password, generated = new_password(body)
        target = users.require(username)
        own = target["username"] == admin.get("username")
        users.set_password(target["username"], password, must_change=bool(body.get("must_change_password", True)) and not own,
                           keep_session=admin.get("session") if own else None)
        return {"ok": True, "password": password if generated else None}

    @app.delete("/api/users/{username}")
    def delete_user(username: str, request: Request, transfer_to: str = "") -> dict[str, Any]:
        require_admin(request)
        users.delete(username, transfer_to)
        return {"deleted": normalize_username(username)}

    # --------------------------------------------------------- access settings (admin)
    @app.get("/api/settings")
    def get_settings(request: Request) -> dict[str, Any]:
        require_admin(request)
        return {
            "values": users.settings(),
            "limits": {k: {"default": d, "min": lo, "max": hi} for k, (d, lo, hi) in SETTINGS.items()},
            "mode": auth.mode,
            "accounts": users.exist(),
            "num_users": len(users.list()),
            "https": bool(config.ssl_certfile),
            "listen": {"host": config.host, "port": config.port, "network": not is_loopback(config.host)},
            "shared_password": bool(config.password),
            "projects_root": str(config.projects_root),
            "config_path": str(config.config_path) if config.config_path else None,
        }

    @app.patch("/api/settings")
    def update_settings(request: Request, body: dict = Body(...)) -> dict[str, Any]:
        require_admin(request)
        users.update_settings(**body)
        return get_settings(request)
