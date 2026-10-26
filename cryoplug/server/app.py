"""HTTP API and static web interface."""
from __future__ import annotations

import os
import re
import socket
import stat
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from cryoplug import __version__
from cryoplug.agent.http import install as install_mcp
from cryoplug.config import MOLSTAR_VERSION, Config
from cryoplug.jobs import CATEGORIES, DATA_TYPES, all_job_types, get_job_type
from cryoplug.config import LaneConfig
from cryoplug.lanes import ClusterLane
from cryoplug.manager import Manager, ManagerError, NotFound
from cryoplug.monitor import Monitor, detect_gpus
from cryoplug.scheduler import Scheduler
from cryoplug.server.accounts import author, can_access, can_manage, current_user, install as install_accounts, is_admin, require_admin
from cryoplug.server.auth import install as install_auth, is_loopback
from cryoplug.server.mapview import MapViews, map_header, map_response
from cryoplug.uploads import CHUNK_MAX, CHUNK_SIZE, OffsetMismatch, UploadError, Uploads
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
            threading.Thread(target=manager.backfill_sizes, name="sizes", daemon=True).start()
        yield
        scheduler.stop()

    app = FastAPI(title="CryoPlug", version=__version__, lifespan=lifespan)
    app.state.manager = manager
    app.state.scheduler = scheduler
    monitor = Monitor(config, manager)
    mapviews = MapViews(manager.project_dir)
    manager.monitor = monitor
    app.state.monitor = monitor

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
    install_mcp(app)  # POST /mcp: the MCP server for AI clients that connect to a URL

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
            "lanes": [{"name": l.name, "type": l.type, "description": l.description, "max_jobs": l.max_jobs, "gpus": l.gpus,
                       "partition": l.partition, "partitions": l.all_partitions,
                       "time_limit": l.time_limit, "mem": l.mem} for l in config.lanes],
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
            hidden = {(j["project_uid"], j["uid"]) for j in overview["jobs"] if not can_access(user, j)}
            for lane in overview["lanes"]:
                lane["gpu_jobs"] = {g: {"project_uid": "", "uid": "", "type": e["type"], "title": _type_title(e["type"]),
                                        "owner": e["owner"], "hidden": True} if (e["project_uid"], e["uid"]) in hidden else e
                                    for g, e in lane["gpu_jobs"].items()}
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

    # ------------------------------------------------------- live usage
    @app.get("/api/monitor")
    def live_usage(request: Request, history: bool = False) -> dict[str, Any]:
        """CPU, memory, GPUs, storage, network and processes (htop / nvtop), sampled every 2 s while watched."""
        user = current_user(request)
        snap = monitor.snapshot(history=history)
        if is_admin(user):
            return snap
        # Other people's processes: their program name only, not their command line or job.
        projects = {p["uid"]: p for p in manager.db.list_projects(include_archived=True)}
        mine = lambda puid: bool(puid) and puid in projects and can_access(user, projects[puid])  # noqa: E731
        snap = dict(snap)
        procs = dict(snap.get("processes") or {})
        procs["top"] = [p if mine(p.get("project")) else {**p, "cmd": p["name"], "job": None, "project": None}
                        for p in procs.get("top", [])]
        snap["processes"] = procs
        snap["gpus"] = [{**g, "processes": [e if mine(e.get("project")) else {**e, "job": None, "project": None}
                                            for e in g.get("processes", [])]} for g in snap.get("gpus", [])]
        return snap

    @app.get("/api/cluster")
    def cluster(request: Request) -> list[dict[str, Any]]:
        """Partitions, nodes and queue of each SLURM lane (sinfo / squeue)."""
        user = current_user(request)
        out = []
        for lane in list(manager.lanes.values()):
            if isinstance(lane, ClusterLane):
                info = dict(lane.overview())
                if not is_admin(user):
                    info.pop("users", None)
                out.append({"lane": lane.cfg.name, "description": lane.cfg.description, **info})
        return out

    # ------------------------------------------------------ lanes (admin)
    def lanes_payload() -> dict[str, Any]:
        return {"lanes": [lane.to_dict() for lane in config.lanes], "source": manager.lanes_source,
                "file_lanes": [lane.name for lane in manager.file_lanes]}

    @app.get("/api/lanes")
    def get_lanes(request: Request) -> dict[str, Any]:
        require_admin(request)
        return lanes_payload()

    @app.put("/api/lanes")
    def put_lanes(request: Request, body: dict = Body(...)) -> dict[str, Any]:
        require_admin(request)
        if body.get("reset"):
            manager.save_lanes(None)
        elif isinstance(body.get("lanes"), list):
            manager.save_lanes(body["lanes"])
        else:
            raise ManagerError("Send the lanes as a list (or reset: true for those of the configuration file)")
        return lanes_payload()

    @app.get("/api/hardware")
    def hardware(request: Request) -> dict[str, Any]:
        require_admin(request)
        mem = monitor.snapshot().get("memory") or {}
        return {"gpus": detect_gpus(monitor.nvidia_smi), "nvidia_smi": monitor.nvidia_smi, "cpus": os.cpu_count(),
                "memory": mem.get("total"), "hostname": socket.gethostname()}

    def lane_from(body: dict) -> LaneConfig:
        try:
            return LaneConfig.from_dict(dict(body.get("lane") or {}))
        except (ValueError, TypeError) as exc:
            raise ManagerError(str(exc)) from None

    @app.post("/api/lanes/test")
    def test_lane(request: Request, body: dict = Body(...)) -> dict[str, Any]:
        """Is the lane usable from the server? (sinfo for SLURM, nvidia-smi for the local GPUs)."""
        require_admin(request)
        cfg = lane_from(body)
        if cfg.type == "cluster":
            return ClusterLane(cfg).test()
        gpus = detect_gpus(monitor.nvidia_smi)
        missing = [g for g in cfg.gpus if g not in {x["index"] for x in gpus}]
        lines = [f"GPU {g['index']}: {g['name']} ({(g['memory_total'] or 0) / 1024:.0f} GB)" for g in gpus] or ["No NVIDIA GPU found (nvidia-smi)"]
        if missing:
            lines.append(f"Not found on this machine: GPU {', '.join(map(str, missing))}")
        return {"ok": not missing, "output": "\n".join(lines), "gpus": gpus}

    @app.post("/api/lanes/preview")
    def preview_lane(request: Request, body: dict = Body(...)) -> dict[str, Any]:
        """The submission script of a cluster lane, for an example GPU job."""
        require_admin(request)
        cfg = lane_from(body)
        if cfg.type != "cluster":
            raise ManagerError("Only cluster lanes submit a script")
        job = {"project_uid": "P1", "uid": "J7", "type": "modelangelo_build", "title": "ModelAngelo build"}
        script = ClusterLane(cfg).render_script(job, config.projects_root / "CP-example" / "J7",
                                                {"num_gpus": int(body.get("num_gpus", 1)), "num_cpus": int(body.get("num_cpus", 4))})
        return {"script": script}

    # --------------------------------------------------------- filesystem
    @app.get("/api/fs")
    def browse(request: Request, path: str = Query(""), show_hidden: bool = False, project: str = "") -> dict[str, Any]:
        user = current_user(request)
        mine = users.roots(user)
        extra: list[Path] = []
        if project:  # from a project: its folder too (the files uploaded into it), for its members
            proj = manager.db.get_project(project)
            if proj and can_access(user, proj):
                extra.append(Path(proj["dir"]).resolve())
        if mine is None:
            folders = [Path(r).resolve() for r in config.browse_roots]
            folders += [e for e in extra if not path_allowed(e, folders)]
            start = Path.home() if path_allowed(Path.home(), folders) else folders[0]
        else:
            try:  # a new user's projects folder appears with their first project: make it now, to browse
                users.projects_dir(user).mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
            folders = [r.resolve() for r in mine]
            folders += [e for e in extra if not path_allowed(e, folders)]
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
                                 body.get("lane"), body.get("notes", ""), created_by=author(current_user(request)), roots=roots(request),
                                 requested=body.get("resources"), workspaces=body.get("workspaces"), tags=body.get("tags"))
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
        fields = {k: body.get(k) for k in ("params", "inputs", "title", "notes", "lane", "workspaces", "tags") if k in body}
        if "resources" in body:
            fields["requested"] = body.get("resources") or {}
        return manager.update_job(puid, juid, roots=roots(request), **fields)

    @app.post("/api/projects/{puid}/jobs/{juid}/queue")
    def queue_job(puid: str, juid: str, request: Request, body: dict = Body(default={})) -> dict[str, Any]:
        return manager.queue_job(puid, juid, (body or {}).get("lane"), roots=roots(request))

    @app.post("/api/projects/{puid}/jobs/{juid}/kill")
    def kill_job(puid: str, juid: str) -> dict[str, Any]:
        return manager.kill_job(puid, juid)

    @app.post("/api/projects/{puid}/jobs/{juid}/control")
    def control_job(puid: str, juid: str, body: dict = Body(...)) -> dict[str, Any]:
        """A request to a running job that takes it, e.g. {"action": "stop"} (finish after the current cycle),
        {"action": "pause"}, {"action": "thresholds", "value": {"ctf_res": [null, 6]}}."""
        return manager.control_job(puid, juid, str(body.get("action") or ""), body.get("value"))

    @app.post("/api/projects/{puid}/jobs/{juid}/clear")
    def clear_job(puid: str, juid: str) -> dict[str, Any]:
        return manager.clear_job(puid, juid)

    @app.post("/api/projects/{puid}/jobs/{juid}/continue")
    def continue_job(puid: str, juid: str, request: Request, body: dict = Body(default={})) -> dict[str, Any]:
        """A failed or killed RELION refinement or classification queued again from its last iteration."""
        return manager.continue_job(puid, juid, (body or {}).get("lane"), roots=roots(request))

    @app.post("/api/projects/{puid}/jobs/{juid}/clear-intermediate")
    def clear_intermediate(puid: str, juid: str) -> dict[str, Any]:
        """Disk space back: the files of every iteration of a finished RELION run but the last."""
        return manager.clear_intermediate(puid, juid)

    @app.post("/api/projects/{puid}/jobs/{juid}/measure")
    def measure_job(puid: str, juid: str) -> dict[str, Any]:
        return {"disk_bytes": manager.measure_job(puid, juid)}

    @app.post("/api/projects/{puid}/jobs/{juid}/complete")
    def mark_complete(puid: str, juid: str) -> dict[str, Any]:
        """A failed or killed iterative job completed with the outputs of the last iteration it wrote."""
        return manager.mark_complete(puid, juid)

    @app.post("/api/projects/{puid}/jobs/{juid}/clone")
    def clone_job(puid: str, juid: str, request: Request, body: dict = Body(default={})) -> dict[str, Any]:
        return manager.clone_job(puid, juid, created_by=author(current_user(request)), roots=roots(request),
                                 workspaces=(body or {}).get("workspaces"))

    @app.post("/api/projects/{puid}/clone")
    def clone_jobs(puid: str, request: Request, body: dict = Body(...)) -> list[dict[str, Any]]:
        """Several jobs cloned together ({"jobs": [...]}): the inputs between them link the clones."""
        return manager.clone_jobs(puid, list(body.get("jobs") or []), created_by=author(current_user(request)),
                                  roots=roots(request), workspaces=body.get("workspaces"))

    # ---------------------------------------------------------- workspaces
    @app.get("/api/projects/{puid}/workspaces")
    def list_workspaces(puid: str) -> list[dict[str, Any]]:
        return manager.workspaces(puid)

    @app.post("/api/projects/{puid}/workspaces")
    def create_workspace(puid: str, request: Request, body: dict = Body(...)) -> dict[str, Any]:
        return manager.create_workspace(puid, body.get("title", ""), body.get("description", ""),
                                        created_by=author(current_user(request)), jobs=body.get("jobs"))

    @app.patch("/api/projects/{puid}/workspaces/{wuid}")
    def update_workspace(puid: str, wuid: str, body: dict = Body(...)) -> dict[str, Any]:
        return manager.update_workspace(puid, wuid, title=body.get("title"), description=body.get("description"))

    @app.delete("/api/projects/{puid}/workspaces/{wuid}")
    def delete_workspace(puid: str, wuid: str) -> dict[str, Any]:
        manager.delete_workspace(puid, wuid)
        return {"deleted": wuid}

    @app.post("/api/projects/{puid}/workspaces/{wuid}/jobs")
    def workspace_jobs(puid: str, wuid: str, body: dict = Body(...)) -> dict[str, Any]:
        """Jobs put in ({"add": [...]}) or taken out of ({"remove": [...]}) the workspace."""
        return manager.workspace_jobs(puid, wuid, add=body.get("add"), remove=body.get("remove"))

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

    # ------------------------------------------------------------ uploads
    # Files sent from the user's computer, by chunks, into <project>/uploads (see cryoplug.uploads).
    @app.get("/api/projects/{puid}/uploads")
    def list_uploads(puid: str) -> dict[str, Any]:
        up = Uploads(manager.project_dir(puid))
        return {"folder": str(up.root), "chunk_size": CHUNK_SIZE, "files": up.list()}

    @app.post("/api/projects/{puid}/uploads")
    def start_upload(puid: str, request: Request, body: dict = Body(...)) -> dict[str, Any]:
        """Announce a file (name, size, fingerprint, folder): where it goes and from which byte to send it. With
        ``reserve_folder``: a subfolder of its own for a folder being uploaded."""
        up = Uploads(manager.project_dir(puid))
        try:
            if body.get("reserve_folder"):
                rel = up.reserve_folder(body["reserve_folder"], str(body.get("fingerprint") or ""), body.get("folder") or "")
                return {"folder": rel, "path": str(up.root / rel)}
            started = up.start(body.get("name"), body.get("size"), body.get("folder") or "", body.get("fingerprint") or "",
                               author(current_user(request)))
        except UploadError as exc:
            raise HTTPException(400, str(exc)) from None
        return {**started, "chunk_size": CHUNK_SIZE}

    @app.put("/api/projects/{puid}/uploads")
    async def upload_chunk(puid: str, request: Request, rel: str, offset: int) -> Response:
        """A chunk of the file ``rel`` starting at byte ``offset`` (raw body). 409 with the right offset when the
        upload stands elsewhere (a chunk lost or sent twice)."""
        if int(request.headers.get("content-length") or 0) > CHUNK_MAX:
            raise HTTPException(413, f"Chunks of at most {CHUNK_MAX // 2**20} MB")
        up = Uploads(manager.project_dir(puid))
        pieces, total = [], 0
        async for piece in request.stream():
            total += len(piece)
            if total > CHUNK_MAX:
                raise HTTPException(413, f"Chunks of at most {CHUNK_MAX // 2**20} MB")
            pieces.append(piece)
        try:
            done = await run_in_threadpool(up.write, rel, offset, b"".join(pieces), author(current_user(request)))
        except OffsetMismatch as exc:
            return JSONResponse({"detail": str(exc), "offset": exc.offset}, status_code=409)
        except UploadError as exc:
            raise HTTPException(400, str(exc)) from None
        return JSONResponse(done)

    @app.delete("/api/projects/{puid}/uploads")
    def cancel_upload(puid: str, rel: str) -> dict[str, Any]:
        try:
            Uploads(manager.project_dir(puid)).cancel(rel)
        except UploadError as exc:
            raise HTTPException(400, str(exc)) from None
        return {"ok": True}

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
    def map_preview(request: Request, puid: str, path: str, max_box: int = 200, enc: str = "f32", lowpass: float = 0.0) -> Response:
        """Copy of a map for the browser viewer (cached): binned to at most ``max_box`` voxels per side (0: full size),
        low-pass filtered to ``lowpass`` A if asked, as float32 (f32), 16-bit (i16, half the transfer, decoded by the
        viewer) or 8-bit (i8, read as such by Mol*: a quick first look)."""
        return map_response(request, mapviews.preview(puid, manager.safe_path(puid, path), max_box, enc, lowpass))

    @app.get("/api/projects/{puid}/zone")
    def map_zone(request: Request, puid: str, map: str, model: str, radius: float = 3.0, max_box: int = 200,
                 enc: str = "f32", lowpass: float = 0.0) -> Response:
        """The map (binned and encoded like the preview; max_box=0 for full size) restricted to within ``radius``
        of the model's atoms, as ChimeraX `volume zone` (cached)."""
        mp, md = manager.safe_path(puid, map), manager.safe_path(puid, model)
        return map_response(request, mapviews.zone(puid, mp, md, radius, max_box, enc, lowpass))

    @app.post("/api/projects/{puid}/mask-preview")
    def mask_preview(puid: str, body: dict = Body(...)) -> Response:
        """The mask being drawn in the 3D viewer (shapes, regions, zones of a recipe) computed on a binned copy of its
        map, at most ``max_box`` voxels per side: 0/1 values as an 8-bit MRC file, gzip-compressed."""
        from cryoplug import maskdraw
        path = manager.safe_path(puid, str(body.get("map") or ""))
        try:
            recipe = maskdraw.validate({"ops": body.get("ops") or [], "dilate": body.get("dilate", 0), "soft": 0})
            max_box = max(48, min(int(body.get("max_box") or 128), 256))
            data = mapviews.mask_preview(puid, path, recipe, max_box, lambda rel: manager.safe_path(puid, rel))
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from None
        return Response(content=data, media_type="application/octet-stream",
                        headers={"Content-Encoding": "gzip", "Cache-Control": "no-store"})

    @app.post("/api/projects/{puid}/fit")
    def fit_in_map(puid: str, body: dict = Body(...)) -> dict[str, Any]:
        """Rigid-body fit for the 3D viewer, as ChimeraX's fitmap: a model, map or mask (``moving``: kind, path, its
        current transform ``matrix`` in the viewer, contour ``level`` of a map, ``chains`` of a model) into the
        ``target`` map (moved by ``target_matrix`` in the viewer, contour ``target_level``), locally or from
        ``search`` random placements. Returns the new transform of the moving item and the statistics of the fit."""
        import numpy as np

        from cryoplug import fitting as ft
        from cryoplug.masks import select_atoms
        from cryoplug.modelio import read_structure
        target_path = manager.safe_path(puid, str(body.get("target") or ""))
        moving = body.get("moving") or {}
        moving_path = manager.safe_path(puid, str(moving.get("path") or ""))
        try:
            item_matrix = ft.as_matrix(moving.get("matrix") or np.eye(4).tolist())
            target_matrix = ft.as_matrix(body.get("target_matrix") or np.eye(4).tolist())
            resolution = float(body.get("resolution") or 0)
            metric = str(body.get("metric") or "overlap")
            search = int(body.get("search") or 0)
            radius = float(body.get("radius") or 0) or None
            if metric not in ft.METRICS:
                raise ValueError(f"metric must be one of {', '.join(ft.METRICS)}")
            if not 0 <= search <= 100 or not 0 <= resolution <= 60:
                raise ValueError("search: 0 to 100 random placements; resolution: 0 to 60 Å")
            target = ft.Target(mapviews.read(target_path))
            level_t = body.get("target_level")
            level_t = float(level_t) if level_t is not None else ft.default_level(target.vol)
            if moving.get("kind") == "model":
                sel = select_atoms(read_structure(moving_path), str(moving.get("chains") or ""))
                if not len(sel["xyz"]):
                    raise ValueError("No atom selected")
                if resolution > 0:
                    points, weights = ft.simulated_points(sel["xyz"], sel["weights"], resolution)
                elif metric != "overlap":
                    raise ValueError("Correlation needs a resolution for a model (its simulated map); without one the "
                                     "atoms are fitted by the average map value")
                else:
                    points, weights = sel["xyz"], None
            else:
                vol = mapviews.read(moving_path)
                level = moving.get("level")
                points, weights = ft.map_points(vol, float(level) if level is not None else ft.default_level(vol),
                                                max_points=30_000)
            start = np.linalg.inv(target_matrix) @ item_matrix  # fitted in the target map's own coordinates
            shift, rotate = body.get("shift", True) is not False, body.get("rotate", True) is not False
            result = ft.fit(points, weights, target, metric, start=start, search=search, radius=radius, level=level_t,
                            shift=shift, rotate=rotate, workers=min(4, os.cpu_count() or 1))
            if moving.get("kind") == "model" and resolution > 0 and body.get("polish"):  # then the atoms, for the exact position
                result["polish"] = ft.fit(sel["xyz"], None, target, "overlap", start=result["matrix"], level=level_t,
                                          shift=shift, rotate=rotate)
        except (ValueError, TypeError, OSError) as exc:
            raise HTTPException(400, str(exc)) from None

        def clean(stats: dict[str, Any]) -> dict[str, Any]:
            return {k: (round(v, 6) if isinstance(v, float) else v) for k, v in stats.items()}

        names = str(moving.get("label") or moving_path.name), str(body.get("target_label") or target_path.name)
        polish = result.get("polish")
        return {
            "matrix": ft.matrix_rows(target_matrix @ (polish or result)["matrix"]),
            "stats": clean(result["stats"]), "before": clean(result["before"]),
            "polish": {"stats": clean(polish["stats"]), "before": clean(polish["before"])} if polish else None,
            "message": ft.message(*names, result) + (f"\nThen on the atoms:\n{ft.message(*names, polish)}" if polish else ""),
            "fits": [{"matrix": ft.matrix_rows(target_matrix @ f["matrix"]), "score": round(ft.score_of(f["stats"]), 6),
                      "hits": f["hits"], "shift": round(f["stats"]["shift"], 3), "angle": round(f["stats"]["angle"], 3),
                      "inside": round(f["stats"].get("inside_fraction", 1.0), 4)} for f in result["fits"][:20]],
        }

    @app.get("/api/projects/{puid}/map-info")
    def map_info_endpoint(puid: str, path: str) -> dict[str, Any]:
        """Header of a map (box, voxel size, origin, file size): the viewer's detail choices."""
        try:
            return map_header(manager.safe_path(puid, path))
        except (OSError, ValueError) as exc:
            raise HTTPException(400, f"Not a readable map: {exc}") from None

    @app.get("/api/projects/{puid}/jobs/{juid}/particle-images")
    def particle_images(puid: str, juid: str, ids: str, output: str = "latent", size: int = 96, per_row: int = 8) -> Response:
        """Contact sheet of the images of some particles of a latent output (the latent explorer) or of a particles
        output (particle curation), by their number in it."""
        from cryoplug.jobs.heterogeneity import particle_montage_png
        job = manager.job(puid, juid)
        out = next((o for o in job.get("outputs") or [] if o.get("name") == output), None)
        if out is None or out.get("type") not in ("latent", "particles"):
            raise HTTPException(404, f"{juid} has no latent or particles output '{output}'")
        try:
            wanted = [int(x) for x in ids.split(",") if x.strip()][:64]
        except ValueError:
            raise HTTPException(400, "ids must be particle numbers separated by commas") from None
        if not wanted:
            raise HTTPException(400, "No particle given")
        try:
            png = particle_montage_png(manager.project_dir(puid), out, wanted, max(32, min(size, 192)),
                                       outputs=job.get("outputs") or [], per_row=max(1, min(per_row, 16)))
        except (ValueError, OSError, IndexError, KeyError) as exc:
            raise HTTPException(400, f"Particle images not available: {exc}") from None
        return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})

    @app.get("/api/projects/{puid}/micrograph")
    def micrograph_picture(puid: str, path: str, size: int = 1024, lowpass: float = 20.0, apix: float = 0.0) -> Response:
        """A micrograph as a PNG: binned by ceil(largest side / size), low-pass filtered to ``lowpass`` Å, cached."""
        from cryoplug.server import micrographview
        p = manager.safe_path(puid, path.split(":")[0])
        try:
            png = micrographview.micrograph_png(manager.project_dir(puid), p, size, lowpass, apix)
        except (OSError, ValueError) as exc:
            raise HTTPException(400, f"Cannot read {path}: {exc}") from None
        return FileResponse(png, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})

    @app.get("/api/projects/{puid}/ctf-image")
    def ctf_picture(puid: str, path: str, size: int = 384) -> Response:
        """The CTFFIND diagnostic image (power spectrum and fitted CTF) of a micrograph as a PNG, cached."""
        from cryoplug.server import micrographview
        p = manager.safe_path(puid, path.split(":")[0])
        try:
            png = micrographview.ctf_png(manager.project_dir(puid), p, size)
        except (OSError, ValueError) as exc:
            raise HTTPException(400, f"Cannot read {path}: {exc}") from None
        return FileResponse(png, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})

    @app.get("/api/projects/{puid}/drift")
    def drift(puid: str, path: str) -> dict[str, Any]:
        """The drift of a movie: the global shift of each frame (pixels) from its RELION motion file."""
        from cryoplug import relion as rl
        p = manager.safe_path(puid, path)
        if p.suffix != ".star":
            raise HTTPException(400, "Not a RELION motion file (.star)")
        try:
            shifts = rl.read_star(p).get("global_shift")
            if shifts is None or not shifts.rows:
                return {"x": [], "y": []}
            return {"x": [round(float(v), 3) for v in shifts.floats("rlnMicrographShiftX")],
                    "y": [round(float(v), 3) for v in shifts.floats("rlnMicrographShiftY")]}
        except (OSError, ValueError, KeyError) as exc:
            raise HTTPException(400, f"Cannot read {path}: {exc}") from None

    @app.get("/api/projects/{puid}/jobs/{juid}/session")
    def get_session(puid: str, juid: str, name: str = "state") -> dict[str, Any]:
        """Work in progress of an interactive tool (picks edited, exposures accepted...)."""
        return manager.read_session(puid, juid, name)

    @app.put("/api/projects/{puid}/jobs/{juid}/session")
    def put_session(puid: str, juid: str, name: str = "state", body: dict = Body(...)) -> dict[str, Any]:
        return manager.write_session(puid, juid, name, body)

    # ----------------------------------------------------------- workflows
    @app.post("/api/projects/{puid}/workflows/{wid}")
    def instantiate(puid: str, wid: str, request: Request, body: dict = Body(default={})) -> dict[str, Any]:
        try:
            return manager.instantiate_workflow(puid, wid, body.get("overrides"), body.get("include"), bool(body.get("queue")),
                                                body.get("lane"), created_by=author(current_user(request)), roots=roots(request),
                                                workspaces=body.get("workspaces"))
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
