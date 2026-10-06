"""HTTP API and static web interface."""
from __future__ import annotations

import hashlib
import os
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
from cryoplug.server.auth import install as install_auth, is_loopback

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


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
    auth = install_auth(app, config)
    app.state.auth = auth

    @app.exception_handler(NotFound)
    async def not_found(request: Request, exc: NotFound):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(ManagerError)
    async def bad_request(request: Request, exc: ManagerError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    # ------------------------------------------------------------- info
    def molstar_urls() -> dict[str, str]:
        local = config.viewer_dir / "molstar.js"
        if local.exists():
            return {"js": "/viewer-assets/molstar.js", "css": "/viewer-assets/molstar.css", "local": True}
        cdn = f"https://cdn.jsdelivr.net/npm/molstar@{MOLSTAR_VERSION}/build/viewer"
        return {"js": config.molstar_js or f"{cdn}/molstar.js", "css": config.molstar_css or f"{cdn}/molstar.css", "local": False}

    @app.get("/api/info")
    def info() -> dict[str, Any]:
        return {
            "version": __version__,
            "hostname": socket.gethostname(),
            "config_path": str(config.config_path) if config.config_path else None,
            "projects_root": str(config.projects_root),
            "browse_roots": config.browse_roots,
            "display": config.display,
            "auth": auth.mode,
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
    def check_tools() -> list[dict[str, Any]]:
        manager.check_tools()
        return manager.tools_overview()

    @app.get("/api/workflows")
    def workflows() -> list[dict[str, Any]]:
        from cryoplug.workflows import workflows_overview
        return workflows_overview()

    @app.get("/api/queue")
    def queue() -> dict[str, Any]:
        return manager.queue_overview()

    # --------------------------------------------------------- filesystem
    @app.get("/api/fs")
    def browse(path: str = Query(""), show_hidden: bool = False) -> dict[str, Any]:
        roots = [Path(r).resolve() for r in config.browse_roots]
        target = Path(path).expanduser().resolve() if path else (Path.home() if any(Path.home().resolve().is_relative_to(r) for r in roots) else roots[0])
        if not any(target == r or target.is_relative_to(r) for r in roots):
            raise HTTPException(403, "Outside the allowed browse roots")
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
        parent = str(target.parent) if any(target.parent == r or target.parent.is_relative_to(r) for r in roots) and target.parent != target else None
        return {"path": str(target), "parent": parent, "entries": entries[:5000], "roots": [str(r) for r in roots]}

    # ----------------------------------------------------------- projects
    @app.get("/api/projects")
    def list_projects(archived: bool = False) -> list[dict[str, Any]]:
        return manager.db.list_projects(include_archived=archived)

    @app.post("/api/projects")
    def create_project(body: dict = Body(...)) -> dict[str, Any]:
        return manager.create_project(body.get("title", ""), body.get("description", ""), body.get("parent") or None)

    @app.get("/api/projects/{puid}")
    def get_project(puid: str) -> dict[str, Any]:
        return manager.project(puid)

    @app.patch("/api/projects/{puid}")
    def update_project(puid: str, body: dict = Body(...)) -> dict[str, Any]:
        return manager.update_project(puid, **body)

    @app.delete("/api/projects/{puid}")
    def delete_project(puid: str, delete_files: bool = False) -> dict[str, Any]:
        manager.delete_project(puid, delete_files)
        return {"deleted": puid}

    # --------------------------------------------------------------- jobs
    @app.get("/api/projects/{puid}/jobs")
    def list_jobs(puid: str) -> list[dict[str, Any]]:
        manager.project(puid)
        return manager.db.list_jobs(puid)

    @app.post("/api/projects/{puid}/jobs")
    def create_job(puid: str, body: dict = Body(...)) -> dict[str, Any]:
        job = manager.create_job(puid, body.get("type", ""), body.get("params"), body.get("inputs"), body.get("title"),
                                 body.get("lane"), body.get("notes", ""))
        if body.get("queue"):
            try:
                job = manager.queue_job(puid, job["uid"], body.get("lane"))
            except ManagerError as exc:
                raise ManagerError(f"{job['uid']} was created but not queued: {exc}") from None
        return job

    @app.get("/api/projects/{puid}/jobs/{juid}")
    def get_job(puid: str, juid: str) -> dict[str, Any]:
        return manager.job_detail(puid, juid)

    @app.patch("/api/projects/{puid}/jobs/{juid}")
    def update_job(puid: str, juid: str, body: dict = Body(...)) -> dict[str, Any]:
        return manager.update_job(puid, juid, **{k: body.get(k) for k in ("params", "inputs", "title", "notes", "lane") if k in body})

    @app.post("/api/projects/{puid}/jobs/{juid}/queue")
    def queue_job(puid: str, juid: str, body: dict = Body(default={})) -> dict[str, Any]:
        return manager.queue_job(puid, juid, (body or {}).get("lane"))

    @app.post("/api/projects/{puid}/jobs/{juid}/kill")
    def kill_job(puid: str, juid: str) -> dict[str, Any]:
        return manager.kill_job(puid, juid)

    @app.post("/api/projects/{puid}/jobs/{juid}/clear")
    def clear_job(puid: str, juid: str) -> dict[str, Any]:
        return manager.clear_job(puid, juid)

    @app.post("/api/projects/{puid}/jobs/{juid}/clone")
    def clone_job(puid: str, juid: str) -> dict[str, Any]:
        return manager.clone_job(puid, juid)

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
        from cryoplug.mrc import MapVolume, bin_map, preview_factor
        p = manager.safe_path(puid, path)
        header = MapVolume.read(p, header_only=True)
        factor = preview_factor(header.shape_xyz, max(64, min(max_box, 512)))
        if factor <= 1:
            return FileResponse(p, media_type="application/octet-stream")
        cache = manager.project_dir(puid) / ".cryoplug_cache"
        cache.mkdir(exist_ok=True)
        key = hashlib.sha1(f"{p.resolve()}:{p.stat().st_mtime}:{factor}".encode()).hexdigest()[:16]
        out = cache / f"{key}.mrc"
        if not out.exists():
            bin_map(MapVolume.read(p), factor).write(out)
        return FileResponse(out, media_type="application/octet-stream")

    # ----------------------------------------------------------- workflows
    @app.post("/api/projects/{puid}/workflows/{wid}")
    def instantiate(puid: str, wid: str, body: dict = Body(default={})) -> dict[str, Any]:
        try:
            return manager.instantiate_workflow(puid, wid, body.get("overrides"), body.get("include"), bool(body.get("queue")),
                                                body.get("lane"))
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
