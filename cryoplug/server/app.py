"""HTTP API and static web interface."""
from __future__ import annotations

import hashlib
import os
import re
import socket
import stat
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from cryoplug import __version__
from cryoplug.config import MOLSTAR_VERSION, Config
from cryoplug.jobs import CATEGORIES, DATA_TYPES, all_job_types, get_job_type
from cryoplug.manager import Manager, ManagerError, NotFound
from cryoplug.scheduler import Scheduler
from cryoplug.server.accounts import author, can_access, can_manage, current_user, install as install_accounts, is_admin, require_admin
from cryoplug.server.auth import install as install_auth, is_loopback
from cryoplug.users import UserError, Users, path_allowed

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
PROJECT_PATH = re.compile(r"^/api/projects/([^/]+)")


def create_app(config: Config, start_scheduler: bool = True, manager: Manager | None = None) -> FastAPI:
    manager = manager or Manager(config)
    scheduler = Scheduler(manager)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if start_scheduler:
            scheduler.start()
        yield
        scheduler.stop()

    app = FastAPI(title="CryoPlug", version=__version__, lifespan=lifespan)
    app.state.manager = manager
    app.state.scheduler = scheduler

    # ------------------------------------------------------------- auth
    users = Users(manager.db, config)
    app.state.users = users

    def project_gate(user: dict[str, Any], request: Request) -> bool:
        """A project (and everything in it) exists only for its owner, its members and administrators."""
        match = PROJECT_PATH.match(request.url.path)
        if not match or is_admin(user):
            return True
        project = manager.db.get_project(match.group(1))
        return project is not None and can_access(user, project)

    auth = install_auth(app, config, users, project_gate)
    app.state.auth = auth
    install_accounts(app, manager, auth)

    @app.exception_handler(NotFound)
    async def not_found(request: Request, exc: NotFound):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(ManagerError)
    async def bad_request(request: Request, exc: ManagerError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(UserError)
    async def bad_account_request(request: Request, exc: UserError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    def roots(request: Request) -> list[Path] | None:
        """The folders the user may read data from (None: anywhere, for administrators)."""
        return users.roots(current_user(request))

    # ------------------------------------------------------------- info
    def molstar_urls() -> dict[str, str]:
        local = config.viewer_dir / "molstar.js"
        if local.exists():
            return {"js": "/viewer-assets/molstar.js", "css": "/viewer-assets/molstar.css", "local": True}
        cdn = f"https://cdn.jsdelivr.net/npm/molstar@{MOLSTAR_VERSION}/build/viewer"
        return {"js": config.molstar_js or f"{cdn}/molstar.js", "css": config.molstar_css or f"{cdn}/molstar.css", "local": False}

    @app.get("/api/info")
    def info(request: Request) -> dict[str, Any]:
        user = current_user(request)
        folders = users.roots(user)
        return {
            "version": __version__,
            "hostname": socket.gethostname(),
            "config_path": str(config.config_path) if config.config_path and is_admin(user) else None,
            "projects_root": str(config.projects_root if user.get("builtin") else users.projects_dir(user)),
            "browse_roots": [str(r) for r in folders] if folders is not None else config.browse_roots,
            "display": config.display,
            "auth": auth.mode,
            "user": app.state.describe_user(user),
            "password_min": users.settings()["min_password_length"],
            "listen": {"host": config.host, "port": config.port, "network": not is_loopback(config.host)},
            "lanes": [{"name": l.name, "type": l.type, "description": l.description, "max_jobs": l.max_jobs, "gpus": l.gpus}
                      for l in config.lanes],
            "categories": CATEGORIES,
            "data_types": DATA_TYPES,
            "molstar": molstar_urls(),
        }

    @app.get("/api/jobtypes")
    def jobtypes() -> list[dict[str, Any]]:
        out = []
        for jt in all_job_types():
            s = jt.schema()
            s["tool_status"] = manager.tool_status.get(jt.tool, {}).get("status", "unknown") if jt.tool else "builtin"
            out.append(s)
        return out

    @app.get("/api/tools")
    def tools() -> list[dict[str, Any]]:
        return manager.tools_overview()

    @app.post("/api/tools/check")
    def check_tools(request: Request) -> list[dict[str, Any]]:
        require_admin(request)
        manager.check_tools()
        return manager.tools_overview()

    @app.get("/api/workflows")
    def workflows() -> list[dict[str, Any]]:
        from cryoplug.workflows import workflows_overview
        return workflows_overview()

    @app.get("/api/queue")
    def queue(request: Request) -> dict[str, Any]:
        user = current_user(request)
        overview = manager.queue_overview()
        if not is_admin(user):
            # Other people's jobs show what occupies the lanes and GPUs, not their projects.
            overview["jobs"] = [j if can_access(user, j) else {
                "hidden": True, "project_uid": "", "uid": "", "project_title": "", "message": "", "type": j["type"],
                "title": _type_title(j["type"]), "status": j["status"], "lane": j.get("lane"), "gpus": j.get("gpus") or [],
                "owner": j.get("owner") or "", "created_at": j.get("created_at"), "queued_at": j.get("queued_at"),
                "started_at": j.get("started_at"), "ended_at": j.get("ended_at")} for j in overview["jobs"]]
        return overview

    def _type_title(name: str) -> str:
        try:
            return get_job_type(name).title
        except KeyError:
            return name

    # --------------------------------------------------------- filesystem
    @app.get("/api/fs")
    def browse(request: Request, path: str = Query(""), show_hidden: bool = False) -> dict[str, Any]:
        user = current_user(request)
        mine = users.roots(user)
        if mine is None:
            folders = [Path(r).resolve() for r in config.browse_roots]
            start = Path.home() if path_allowed(Path.home(), folders) else folders[0]
        else:
            try:  # a new user's projects folder appears with their first project: make it now, to browse
                users.projects_dir(user).mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
            folders = [r.resolve() for r in mine]
            start = next((r for r in folders if r.is_dir()), None)
            if start is None:
                raise HTTPException(404, "None of your folders exists: ask an administrator (Settings › Users)")
        target = Path(path).expanduser().resolve() if path else start
        if not path_allowed(target, folders):
            raise HTTPException(403, "Outside your folders" if mine is not None else "Outside the allowed browse roots")
        if target.is_file():
            target = target.parent
        if not target.is_dir():
            raise HTTPException(404, f"Not a directory: {target}")
        entries = []
        try:
            names = sorted(os.listdir(target), key=str.lower)
        except PermissionError:
            raise HTTPException(403, f"Permission denied: {target}") from None
        for name in names:
            if name.startswith(".") and not show_hidden:
                continue
            p = target / name
            try:
                st = p.stat()
            except OSError:
                continue
            entries.append({"name": name, "path": str(p), "is_dir": stat.S_ISDIR(st.st_mode), "size": st.st_size, "mtime": st.st_mtime})
        entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
        parent = str(target.parent) if target.parent != target and path_allowed(target.parent, folders) else None
        return {"path": str(target), "parent": parent, "entries": entries[:5000], "roots": [str(r) for r in folders]}

    # ----------------------------------------------------------- projects
    @app.get("/api/projects")
    def list_projects(request: Request, archived: bool = False) -> list[dict[str, Any]]:
        user = current_user(request)
        return [p for p in manager.db.list_projects(include_archived=archived) if can_access(user, p)]

    @app.post("/api/projects")
    def create_project(request: Request, body: dict = Body(...)) -> dict[str, Any]:
        user = current_user(request)
        return manager.create_project(body.get("title", ""), body.get("description", ""), body.get("parent") or None,
                                      owner=author(user), roots=users.roots(user),
                                      default_parent=None if user.get("builtin") else users.projects_dir(user))

    @app.get("/api/projects/{puid}")
    def get_project(puid: str) -> dict[str, Any]:
        return manager.project(puid)

    def managed_project(request: Request, puid: str) -> dict[str, Any]:
        project = manager.project(puid)
        if not can_manage(current_user(request), project):
            raise HTTPException(403, "Only the owner of the project or an administrator can do this")
        return project

    @app.patch("/api/projects/{puid}")
    def update_project(puid: str, request: Request, body: dict = Body(...)) -> dict[str, Any]:
        project = managed_project(request, puid)
        changes = {k: body[k] for k in ("title", "description", "archived") if k in body}
        owner = project["owner"]
        if "owner" in body:
            if not is_admin(current_user(request)):
                raise HTTPException(403, "Only an administrator can give a project to someone else")
            owner = users.require(body["owner"])["username"] if body["owner"] else ""
            changes["owner"] = owner
        if "members" in body or "owner" in body:
            members = [users.require(m)["username"] for m in (body["members"] if "members" in body else project["members"]) or []]
            changes["members"] = [m for m in dict.fromkeys(members) if m != owner]
        return manager.update_project(puid, **changes)

    @app.delete("/api/projects/{puid}")
    def delete_project(puid: str, request: Request, delete_files: bool = False) -> dict[str, Any]:
        managed_project(request, puid)
        manager.delete_project(puid, delete_files)
        return {"deleted": puid}

    @app.get("/api/people")
    def people(request: Request) -> list[dict[str, Any]]:
        """Accounts one can share a project with (names only)."""
        current_user(request)
        return [{"username": u["username"], "full_name": u["full_name"]} for u in users.list() if not u["disabled"]]

    # --------------------------------------------------------------- jobs
    @app.get("/api/projects/{puid}/jobs")
    def list_jobs(puid: str) -> list[dict[str, Any]]:
        manager.project(puid)
        return manager.db.list_jobs(puid)

    @app.post("/api/projects/{puid}/jobs")
    def create_job(puid: str, request: Request, body: dict = Body(...)) -> dict[str, Any]:
        job = manager.create_job(puid, body.get("type", ""), body.get("params"), body.get("inputs"), body.get("title"),
                                 body.get("lane"), body.get("notes", ""), created_by=author(current_user(request)), roots=roots(request))
        if body.get("queue"):
            try:
                job = manager.queue_job(puid, job["uid"], body.get("lane"), roots=roots(request))
            except ManagerError as exc:
                raise ManagerError(f"{job['uid']} was created but not queued: {exc}") from None
        return job

    @app.get("/api/projects/{puid}/jobs/{juid}")
    def get_job(puid: str, juid: str) -> dict[str, Any]:
        return manager.job_detail(puid, juid)

    @app.patch("/api/projects/{puid}/jobs/{juid}")
    def update_job(puid: str, juid: str, request: Request, body: dict = Body(...)) -> dict[str, Any]:
        return manager.update_job(puid, juid, roots=roots(request),
                                  **{k: body.get(k) for k in ("params", "inputs", "title", "notes", "lane") if k in body})

    @app.post("/api/projects/{puid}/jobs/{juid}/queue")
    def queue_job(puid: str, juid: str, request: Request, body: dict = Body(default={})) -> dict[str, Any]:
        return manager.queue_job(puid, juid, (body or {}).get("lane"), roots=roots(request))

    @app.post("/api/projects/{puid}/jobs/{juid}/kill")
    def kill_job(puid: str, juid: str) -> dict[str, Any]:
        return manager.kill_job(puid, juid)

    @app.post("/api/projects/{puid}/jobs/{juid}/clear")
    def clear_job(puid: str, juid: str) -> dict[str, Any]:
        return manager.clear_job(puid, juid)

    @app.post("/api/projects/{puid}/jobs/{juid}/clone")
    def clone_job(puid: str, juid: str, request: Request) -> dict[str, Any]:
        return manager.clone_job(puid, juid, created_by=author(current_user(request)), roots=roots(request))

    @app.delete("/api/projects/{puid}/jobs/{juid}")
    def delete_job(puid: str, juid: str, force: bool = False) -> dict[str, Any]:
        manager.delete_job(puid, juid, force)
        return {"deleted": juid}

    @app.get("/api/projects/{puid}/jobs/{juid}/log")
    def job_log(puid: str, juid: str, offset: int = 0) -> dict[str, Any]:
        return manager.read_log(puid, juid, offset)

    @app.get("/api/projects/{puid}/jobs/{juid}/files")
    def job_files(puid: str, juid: str, sub: str = "") -> list[dict[str, Any]]:
        return manager.list_files(puid, juid, sub)

    # -------------------------------------------------------- interactive
    @app.post("/api/projects/{puid}/jobs/{juid}/interactive/launch")
    def launch(puid: str, juid: str) -> dict[str, Any]:
        return manager.launch_interactive(puid, juid)

    @app.post("/api/projects/{puid}/jobs/{juid}/interactive/upload")
    def upload(puid: str, juid: str, file: UploadFile = File(...)) -> dict[str, Any]:
        return manager.save_upload(puid, juid, file.filename or "", file.file)

    @app.get("/api/projects/{puid}/jobs/{juid}/interactive/bundle")
    def bundle(puid: str, juid: str) -> FileResponse:
        path = manager.session_bundle(puid, juid)
        return FileResponse(path, filename=path.name, media_type="application/zip")

    @app.post("/api/projects/{puid}/jobs/{juid}/interactive/finish")
    def finish(puid: str, juid: str, body: dict = Body(default={})) -> dict[str, Any]:
        return manager.finish_interactive(puid, juid, (body or {}).get("path"))

    # -------------------------------------------------------------- files
    @app.get("/api/projects/{puid}/file")
    def project_file(puid: str, path: str, download: bool = False) -> FileResponse:
        p = manager.safe_path(puid, path)
        if p.is_dir():
            raise HTTPException(400, "Is a directory")
        media = None
        if p.suffix.lower() in (".mrc", ".map", ".ccp4", ".mrcs"):
            media = "application/octet-stream"
        elif p.suffix.lower() in (".log", ".out", ".txt", ".sh", ".py", ".cxc", ".md", ".fasta", ".csv", ".pdb", ".cif", ".mmcif", ".xml"):
            media = "text/plain; charset=utf-8"
        return FileResponse(p, media_type=media, filename=p.name if download else None,
                            content_disposition_type="attachment" if download else "inline")

    @app.get("/api/projects/{puid}/preview")
    def map_preview(puid: str, path: str, max_box: int = 200) -> FileResponse:
        """Binned copy of a map for the browser viewer (cached)."""
        from cryoplug.mrc import MapVolume, bin_map
        p = manager.safe_path(puid, path)
        factor = _view_factor(p, max_box)
        if factor <= 1:
            return FileResponse(p, media_type="application/octet-stream")
        out = _cache_file(puid, f"{p.resolve()}:{p.stat().st_mtime}:{factor}")
        if not out.exists():
            bin_map(MapVolume.read(p), factor).write(out)
        return FileResponse(out, media_type="application/octet-stream")

    @app.get("/api/projects/{puid}/zone")
    def map_zone(puid: str, map: str, model: str, radius: float = 3.0, max_box: int = 200) -> FileResponse:
        """The map (binned like the preview; max_box=0 for full size) restricted to within ``radius``
        of the model's atoms, as ChimeraX `volume zone` (cached)."""
        from cryoplug.modelio import atom_arrays, read_structure
        from cryoplug.mrc import MapVolume, bin_map, zone_map
        if not 0.5 <= radius <= 30:
            raise HTTPException(400, "radius must be between 0.5 and 30 A")
        mp, md = manager.safe_path(puid, map), manager.safe_path(puid, model)
        factor = _view_factor(mp, max_box) if max_box > 0 else 1
        out = _cache_file(puid, f"zone:{mp.resolve()}:{mp.stat().st_mtime}:{md.resolve()}:{md.stat().st_mtime}:{radius:.2f}:{factor}")
        if not out.exists():
            try:
                xyz = atom_arrays(read_structure(md))["xyz"]
            except Exception as exc:  # noqa: BLE001 - unreadable model
                raise HTTPException(400, f"Cannot read the model: {exc}") from None
            if not len(xyz):
                raise HTTPException(400, "The model has no atoms")
            zone_map(bin_map(MapVolume.read(mp), factor), xyz, radius).write(out)
        return FileResponse(out, media_type="application/octet-stream")

    def _view_factor(p: Path, max_box: int) -> int:
        from cryoplug.mrc import MapVolume, preview_factor
        return preview_factor(MapVolume.read(p, header_only=True).shape_xyz, max(64, min(max_box, 512)))

    def _cache_file(puid: str, key: str) -> Path:
        cache = manager.project_dir(puid) / ".cryoplug_cache"
        cache.mkdir(exist_ok=True)
        return cache / f"{hashlib.sha1(key.encode()).hexdigest()[:16]}.mrc"

    # ----------------------------------------------------------- workflows
    @app.post("/api/projects/{puid}/workflows/{wid}")
    def instantiate(puid: str, wid: str, request: Request, body: dict = Body(default={})) -> dict[str, Any]:
        try:
            return manager.instantiate_workflow(puid, wid, body.get("overrides"), body.get("include"), bool(body.get("queue")),
                                                body.get("lane"), created_by=author(current_user(request)), roots=roots(request))
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from None

    @app.get("/api/jobtypes/{name}")
    def jobtype(name: str) -> dict[str, Any]:
        try:
            return get_job_type(name).schema()
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from None

    # ------------------------------------------------------------- static
    if config.viewer_dir.exists():
        app.mount("/viewer-assets", StaticFiles(directory=str(config.viewer_dir)), name="viewer-assets")
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
    return app
