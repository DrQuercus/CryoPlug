"""Business logic: projects, jobs, inputs, interactive sessions and workflows."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path
from typing import Any, BinaryIO

from cryoplug import __version__
from cryoplug import tools as toolmod
from cryoplug.config import SLURM_MEM_RE, SLURM_TIME_RE, Config, LaneConfig
from cryoplug.db import Database
from cryoplug.jobs import get_job_type
from cryoplug.jobs.base import ACTIVE_STATUSES, JobContext, JobError, split_paths
from cryoplug.lanes import make_lane
from cryoplug.users import path_allowed


class ManagerError(Exception):
    """Invalid request (mapped to HTTP 400)."""


class NotFound(ManagerError):
    """Missing project/job/file (mapped to HTTP 404)."""


def slugify(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")
    return slug[:40] or "project"


def _rm_contents(directory: Path, keep: tuple[str, ...] = ()) -> None:
    for p in directory.iterdir():
        if p.name in keep:
            continue
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p, ignore_errors=True)
        else:
            try:
                p.unlink()
            except OSError:
                pass


REQUESTABLE = ("gpu_ids", "num_cpus", "partition", "time", "mem")


class Manager:
    def __init__(self, config: Config):
        self.config = config
        config.data_dir.mkdir(parents=True, exist_ok=True)
        self.db = Database(config.db_path)
        self.lock = threading.RLock()
        self.monitor = None  # set by the server: live GPU usage for the automatic GPU choice
        self.file_lanes = list(config.lanes)  # [[lanes]] of the configuration file
        stored = self.db.get_meta("lanes")
        if stored:  # edited in Settings › Compute: they take precedence
            try:
                config.lanes = [LaneConfig.from_dict(dict(d)) for d in stored]
            except (ValueError, TypeError):
                pass
        self.lanes = {lane.name: make_lane(lane) for lane in config.lanes}
        self.tool_status: dict[str, dict[str, Any]] = self.db.get_meta("tool_status", {}) or {}

    # =============================================================== lanes
    @property
    def lanes_source(self) -> str:
        return "settings" if self.db.get_meta("lanes") else "config"

    def save_lanes(self, raw: list[dict[str, Any]] | None) -> list[LaneConfig]:
        """Replace the lanes (Settings › Compute), or go back to those of the configuration file (``None``)."""
        with self.lock:
            if raw is None:
                lanes = list(self.file_lanes)
            else:
                try:
                    lanes = [LaneConfig.from_dict(dict(d)) for d in raw]
                except (ValueError, TypeError) as exc:
                    raise ManagerError(str(exc)) from None
            if not lanes:
                raise ManagerError("Keep at least one lane")
            names = [lane.name for lane in lanes]
            duplicate = next((n for n in names if names.count(n) > 1), None)
            if duplicate:
                raise ManagerError(f"Two lanes are named '{duplicate}'")
            new = {lane.name: lane for lane in lanes}
            for job in self.db.list_jobs(statuses=["queued", "launched", "running"]):
                name = job.get("lane") or ""
                if name and name not in new:
                    raise ManagerError(f"Lane '{name}' still has active jobs ({job['project_uid']}/{job['uid']}): "
                                       "wait for them or stop them before removing or renaming it")
                old = self.lanes.get(name)
                if old is not None and job["status"] != "queued" and old.cfg.type != new[name].type:
                    raise ManagerError(f"Lane '{name}' has running jobs: wait for them before changing its type")
            self.db.set_meta("lanes", [lane.to_dict() for lane in lanes] if raw is not None else None)
            self.config.lanes = lanes
            current = self.lanes
            rebuilt = {}
            for cfg in lanes:
                lane = current.get(cfg.name)
                if lane is not None and lane.cfg.type == cfg.type:  # keep it: it knows its running workers
                    lane.cfg = cfg
                    lane.python = cfg.python or sys.executable
                    if hasattr(lane, "_overview"):
                        lane._overview = None
                else:
                    lane = make_lane(cfg)
                rebuilt[cfg.name] = lane
            self.lanes = rebuilt
            return lanes

    def gpu_load(self) -> dict[int, float]:
        """Memory used on each GPU (MiB), from the live monitor when it is running."""
        return self.monitor.gpu_memory(max_age=30.0) if self.monitor is not None else {}

    def clean_requested(self, jt, params: dict[str, Any], lane_name: str | None, requested: dict[str, Any] | None,
                        lenient: bool = False) -> dict[str, Any]:
        """Compute choices of a job (GPUs, CPUs, SLURM partition / time / memory) checked against its lane.

        ``lenient`` drops the choices that do not apply to the lane (the lane was changed afterwards)."""
        if not requested:
            return {}
        if not isinstance(requested, dict):
            raise ManagerError("Resources must be given as an object")
        unknown = set(requested) - set(REQUESTABLE)
        if unknown:
            raise ManagerError(f"Unknown resource(s): {', '.join(sorted(unknown))}")
        if lenient and lane_name and lane_name not in self.lanes:  # its lane was deleted or renamed: the default one
            lane_name = None
        lane = self._lane_cfg(lane_name)
        need = int(jt.resources(params).get("num_gpus", 0))
        out: dict[str, Any] = {}

        def refuse(message: str) -> None:
            if not lenient:
                raise ManagerError(message)

        cpus = requested.get("num_cpus")
        if cpus not in (None, ""):
            try:
                cpus = int(cpus)
            except (TypeError, ValueError):
                raise ManagerError("CPUs: a whole number") from None
            if not 1 <= cpus <= 1024:
                raise ManagerError("CPUs: between 1 and 1024")
            if jt.cpu_param:  # e.g. Phenix nproc: the program starts that many processes
                refuse(f"{jt.title} takes its CPUs from the parameter '{jt.param(jt.cpu_param).to_dict()['label']}'")
            else:
                out["num_cpus"] = cpus
        ids = requested.get("gpu_ids") or []
        if ids:
            try:
                ids = sorted({int(g) for g in ids})
            except (TypeError, ValueError):
                raise ManagerError("GPU ids are whole numbers") from None
            if lane.type != "local" or not lane.gpus:
                refuse(f"Lane '{lane.name}' does not hand out specific GPUs: leave the GPU choice automatic")
            elif need == 0:
                refuse(f"{jt.title} does not use a GPU")
            elif any(g not in lane.gpus for g in ids):
                refuse(f"Lane '{lane.name}' manages GPU {', '.join(map(str, lane.gpus))} only")
            elif len(ids) != need:
                refuse(f"{jt.title} uses {need} GPU{'s' if need > 1 else ''}: choose {need}")
            else:
                out["gpu_ids"] = ids
        for key in ("partition", "time", "mem"):
            value = str(requested.get(key) or "").strip()
            if not value:
                continue
            if lane.type != "cluster":
                refuse(f"The {key} only applies to cluster lanes")
                continue
            if key == "partition":
                allowed = lane.all_partitions
                if value not in allowed:
                    refuse(f"Partition '{value}' is not offered by lane '{lane.name}' ({', '.join(allowed) or 'none'})")
                    continue
            elif key == "time" and not SLURM_TIME_RE.fullmatch(value):
                raise ManagerError("Time limit as SLURM writes it (12:00:00, 2-00:00:00...)")
            elif key == "mem" and not SLURM_MEM_RE.fullmatch(value):
                raise ManagerError("Memory as 64G, 128000M...")
            out[key] = value
        return out

    def job_resources(self, jt, params: dict[str, Any], requested: dict[str, Any]) -> dict[str, Any]:
        """What the job asks the lane for: the job type's needs, with the user's choices."""
        return {**jt.resources(params), **requested}

    # =============================================================== tools
    def check_tools(self) -> dict[str, dict[str, Any]]:
        status = toolmod.check_all(self.config)
        self.tool_status = status
        self.db.set_meta("tool_status", status)
        return status

    def tools_overview(self) -> list[dict[str, Any]]:
        out = []
        for key, tdef in toolmod.TOOLS.items():
            entry = tdef.to_dict()
            entry["config"] = self.config.tool(key).to_dict()
            entry["status"] = self.tool_status.get(key, {"status": "unknown"})
            out.append(entry)
        return out

    # ============================================================ projects
    def project(self, uid: str) -> dict[str, Any]:
        p = self.db.get_project(uid)
        if not p:
            raise NotFound(f"Project {uid} not found")
        return p

    def project_dir(self, uid: str) -> Path:
        return Path(self.project(uid)["dir"])

    def create_project(self, title: str, description: str = "", parent: str | None = None, owner: str = "",
                       roots: list[Path] | None = None, default_parent: Path | None = None) -> dict[str, Any]:
        """New project folder CP-<title> in ``parent``; ``roots`` limits where (a user's folders)."""
        title = (title or "").strip()
        if not title:
            raise ManagerError("Project title is required")
        parent_dir = Path(parent or default_parent or self.config.projects_root).expanduser()
        if not path_allowed(parent_dir, roots):
            raise ManagerError(f"{parent_dir} is outside your folders: create the project in your projects folder"
                               f"{f' ({default_parent})' if default_parent else ''}")
        try:
            parent_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ManagerError(f"Cannot create {parent_dir}: {exc}") from None
        if not os.access(parent_dir, os.W_OK):
            raise ManagerError(f"Directory not writable: {parent_dir}")
        base = parent_dir / f"CP-{slugify(title)}"
        pdir, n = base, 2
        while pdir.exists():
            pdir = Path(f"{base}-{n}")
            n += 1
        pdir.mkdir(parents=True)
        proj = self.db.create_project(title, description, str(pdir), owner)
        (pdir / "cryoplug_project.json").write_text(json.dumps({"uid": proj["uid"], "title": title, "description": description,
                                                                "owner": owner, "created_at": proj["created_at"]}, indent=1))
        return proj

    def update_project(self, uid: str, **fields: Any) -> dict[str, Any]:
        self.project(uid)
        allowed = {k: v for k, v in fields.items() if k in ("title", "description", "archived", "owner", "members")}
        self.db.update_project(uid, **allowed)
        return self.project(uid)

    def delete_project(self, uid: str, delete_files: bool = False) -> None:
        proj = self.project(uid)
        if self.db.list_jobs(uid, statuses=ACTIVE_STATUSES):
            raise ManagerError("Kill the running/queued jobs of this project first")
        self.db.delete_project(uid)
        if delete_files:
            shutil.rmtree(proj["dir"], ignore_errors=True)

    # ================================================================ jobs
    def job(self, puid: str, juid: str) -> dict[str, Any]:
        j = self.db.get_job(puid, juid)
        if not j:
            raise NotFound(f"Job {puid}/{juid} not found")
        return j

    def job_dir(self, puid: str, juid: str) -> Path:
        return self.project_dir(puid) / juid

    def _check_inputs(self, puid: str, jt, inputs: dict[str, Any] | None) -> dict[str, dict[str, str]]:
        clean: dict[str, dict[str, str]] = {}
        for slot_name, ref in (inputs or {}).items():
            if not ref:
                continue
            try:
                slot = jt.slot(slot_name)
            except KeyError:
                raise ManagerError(f"{jt.name} has no input '{slot_name}'") from None
            if not isinstance(ref, dict) or not ref.get("job") or not ref.get("output"):
                raise ManagerError(f"Input '{slot_name}' must be {{job, output}}")
            parent = self.db.get_job(puid, ref["job"])
            if not parent:
                raise ManagerError(f"Input '{slot_name}': job {ref['job']} does not exist")
            out_type = None
            for o in parent["outputs"]:
                if o["name"] == ref["output"]:
                    out_type = o["type"]
            if out_type is None:
                ptype = get_job_type(parent["type"])
                for o in ptype.outputs:
                    if o.name == ref["output"]:
                        out_type = o.type
                if out_type is None and parent["status"] == "completed":
                    raise ManagerError(f"Job {ref['job']} has no output '{ref['output']}'")
            if out_type is not None and out_type not in slot.types:
                raise ManagerError(f"Input '{slot_name}' expects {'/'.join(slot.types)}, got {out_type} ({ref['job']}.{ref['output']})")
            clean[slot_name] = {"job": ref["job"], "output": ref["output"]}
        return clean

    @staticmethod
    def _check_user_params(jt, params: dict[str, Any], roots: list[Path] | None, authoring: bool = True,
                           previous: dict[str, Any] | None = None) -> None:
        """What a user who is not an administrator (``roots``: their folders; None = administrator) may ask for.

        Files and folders must lie in their folders.  When ``authoring`` (creating or editing a job), job types and
        parameters that can run code on the server or reach other folders (custom command, ChimeraX commands, extra
        program arguments) are refused, unless left as they were (``previous``: the job edited or cloned)."""
        if roots is None:
            return
        if authoring and jt.admin_only:
            raise ManagerError(f"{jt.title} runs any command on the server: only administrators can use it")
        for p in jt.params:
            value = params.get(p.name)
            if not value:
                continue
            label = p.label or p.name
            if authoring and p.admin_only and value != (previous or {}).get(p.name, p.default):
                raise ManagerError(f"{label}: only administrators can set this (it can run code or reach any folder on the server)")
            if p.type == "path" or p.path_kind == "files":
                for v in [value] if p.type == "path" else split_paths(value):
                    if not path_allowed(v, roots):
                        raise ManagerError(f"{label}: {v} is outside your folders "
                                           "(an administrator can add its folder to yours in Settings › Users)")

    def create_job(self, puid: str, job_type: str, params: dict | None = None, inputs: dict | None = None,
                   title: str | None = None, lane: str | None = None, notes: str = "", created_by: str = "",
                   roots: list[Path] | None = None, inherited: dict[str, Any] | None = None,
                   requested: dict[str, Any] | None = None) -> dict[str, Any]:
        self.project(puid)
        try:
            jt = get_job_type(job_type)
        except KeyError as exc:
            raise ManagerError(str(exc)) from None
        try:
            clean_params = jt.coerce_params(params)
        except ValueError as exc:
            raise ManagerError(str(exc)) from None
        self._check_user_params(jt, clean_params, roots, previous=inherited)
        clean_inputs = self._check_inputs(puid, jt, inputs)
        if lane:
            self._lane_cfg(lane)
        chosen = self.clean_requested(jt, clean_params, lane, requested)
        job = self.db.create_job(puid, job_type, (title or "").strip() or jt.title, clean_params, clean_inputs,
                                 lane, self.job_resources(jt, clean_params, chosen), created_by)
        if notes or chosen:
            self.db.update_job(puid, job["uid"], **({"notes": notes} if notes else {}), **({"requested": chosen} if chosen else {}))
        jdir = self.job_dir(puid, job["uid"])
        jdir.mkdir(parents=True, exist_ok=True)
        return self.job(puid, job["uid"])

    def update_job(self, puid: str, juid: str, roots: list[Path] | None = None, **fields: Any) -> dict[str, Any]:
        with self.lock:
            job = self.job(puid, juid)
            jt = get_job_type(job["type"])
            updates: dict[str, Any] = {}
            if "notes" in fields:
                updates["notes"] = str(fields["notes"] or "")
            if "title" in fields and fields["title"] is not None:
                updates["title"] = str(fields["title"]).strip() or jt.title
            for key in ("params", "inputs", "lane", "requested"):
                if key in fields and fields[key] is not None and job["status"] != "building":
                    raise ManagerError("Parameters and inputs can only be changed while the job is in building state (clear it first)")
                if key in fields and fields[key] is not None and roots is not None and jt.admin_only:
                    raise ManagerError(f"{jt.title} runs any command on the server: only administrators can change it")
            if fields.get("params") is not None:
                merged = {**job["params"], **fields["params"]}
                try:
                    updates["params"] = jt.coerce_params(merged)
                except ValueError as exc:
                    raise ManagerError(str(exc)) from None
                self._check_user_params(jt, updates["params"], roots, previous=job["params"])
                updates["resources"] = jt.resources(updates["params"])
            if fields.get("inputs") is not None:
                updates["inputs"] = self._check_inputs(puid, jt, fields["inputs"])
            if fields.get("lane") is not None:
                if fields["lane"]:
                    self._lane_cfg(fields["lane"])
                updates["lane"] = fields["lane"] or None
            if any(fields.get(k) is not None for k in ("params", "lane", "requested")):
                params = updates.get("params", job["params"])
                lane = updates.get("lane", job.get("lane"))
                if fields.get("requested") is not None:
                    chosen = self.clean_requested(jt, params, lane, fields["requested"])
                else:  # another lane or other parameters: keep what still applies
                    chosen = self.clean_requested(jt, params, lane, job.get("requested"), lenient=True)
                updates["requested"] = chosen
                updates["resources"] = self.job_resources(jt, params, chosen)
            if updates:
                self.db.update_job(puid, juid, **updates)
            return self.job(puid, juid)

    def _lane_cfg(self, name: str | None):
        try:
            return self.config.lane(name)
        except KeyError as exc:
            raise ManagerError(str(exc)) from None

    def queue_problems(self, job: dict[str, Any]) -> list[str]:
        jt = get_job_type(job["type"])
        connected = {}
        problems = []
        for slot, ref in job["inputs"].items():
            parent = self.db.get_job(job["project_uid"], ref["job"])
            if not parent:
                problems.append(f"Input '{slot}': job {ref['job']} was deleted")
                continue
            if parent["status"] in ("failed", "killed"):
                problems.append(f"Input '{slot}': job {ref['job']} is {parent['status']}")
            connected[slot] = ref["output"]
        problems += jt.check(job["params"], connected)
        return problems

    def queue_job(self, puid: str, juid: str, lane: str | None = None, roots: list[Path] | None = None) -> dict[str, Any]:
        with self.lock:
            job = self.job(puid, juid)
            if job["status"] != "building":
                raise ManagerError(f"Only jobs in building state can be queued (status: {job['status']})")
            problems = self.queue_problems(job)
            if problems:
                raise ManagerError("; ".join(problems))
            lane_name = lane or job.get("lane") or self.config.lanes[0].name
            if not lane and lane_name not in self.lanes:  # its lane was deleted or renamed since: the default lane
                lane_name = self.config.lanes[0].name
            self._lane_cfg(lane_name)
            jt = get_job_type(job["type"])
            self._check_user_params(jt, job["params"], roots, authoring=False)  # folders again: the job may be someone else's
            chosen = self.clean_requested(jt, job["params"], lane_name, job.get("requested"), lenient=True)
            self.db.update_job(puid, juid, status="queued", lane=lane_name, queued_at=time.time(), message="Queued",
                               error="", progress=0.0, requested=chosen, resources=self.job_resources(jt, job["params"], chosen))
            return self.job(puid, juid)

    def kill_job(self, puid: str, juid: str) -> dict[str, Any]:
        with self.lock:
            job = self.job(puid, juid)
            if job["status"] == "queued":
                self.db.update_job(puid, juid, status="building", message="Removed from queue", queued_at=None)
                return self.job(puid, juid)
            if job["status"] == "waiting":
                self.db.update_job(puid, juid, status="killed", ended_at=time.time(), message="Session cancelled")
                return self.job(puid, juid)
            if job["status"] not in ("launched", "running"):
                raise ManagerError(f"Job is not running (status: {job['status']})")
            lane = self.lanes.get(job["lane"] or "")
            if lane:
                lane.kill(job)
            self.db.update_job(puid, juid, status="killed", ended_at=time.time(), message="Killed by user", gpus=[])
            return self.job(puid, juid)

    def clear_job(self, puid: str, juid: str) -> dict[str, Any]:
        with self.lock:
            job = self.job(puid, juid)
            if job["status"] in ("launched", "running"):
                raise ManagerError("Kill the job before clearing it")
            jdir = self.job_dir(puid, juid)
            if jdir.exists():
                _rm_contents(jdir)
            self.db.update_job(puid, juid, status="building", outputs=[], highlights=[], progress=0.0, message="",
                               error="", pid=None, cluster_job_id=None, gpus=[], queued_at=None, started_at=None,
                               ended_at=None)
            return self.job(puid, juid)

    def dependents(self, puid: str, juid: str) -> list[str]:
        return [j["uid"] for j in self.db.list_jobs(puid) if any(r["job"] == juid for r in j["inputs"].values())]

    def delete_job(self, puid: str, juid: str, force: bool = False) -> None:
        with self.lock:
            job = self.job(puid, juid)
            deps = self.dependents(puid, juid)
            if deps and not force:
                raise ManagerError(f"Used as input by {', '.join(deps)} (delete them first or force)")
            if job["status"] in ("launched", "running"):
                self.kill_job(puid, juid)
            shutil.rmtree(self.job_dir(puid, juid), ignore_errors=True)
            self.db.delete_job(puid, juid)

    def clone_job(self, puid: str, juid: str, created_by: str = "", roots: list[Path] | None = None) -> dict[str, Any]:
        job = self.job(puid, juid)
        valid_inputs = {s: r for s, r in job["inputs"].items() if self.db.get_job(puid, r["job"])}
        lane = job.get("lane") if job.get("lane") in self.lanes else None  # a deleted lane: the default one
        return self.create_job(puid, job["type"], job["params"], valid_inputs, job["title"], lane,
                               created_by=created_by, roots=roots, inherited=job["params"],
                               requested=self.clean_requested(get_job_type(job["type"]), job["params"], job.get("lane"),
                                                              job.get("requested"), lenient=True))

    # ------------------------------------------------------------ details
    def job_detail(self, puid: str, juid: str) -> dict[str, Any]:
        job = self.job(puid, juid)
        jdir = self.job_dir(puid, juid)
        report_path = jdir / "report.json"
        job["report"] = {"sections": []}
        if report_path.exists():
            try:
                job["report"] = json.loads(report_path.read_text())
            except (OSError, ValueError):
                pass
        inputs = {}
        for slot, ref in job["inputs"].items():
            parent = self.db.get_job(puid, ref["job"])
            out = next((o for o in (parent or {}).get("outputs", []) if o["name"] == ref["output"]), None)
            inputs[slot] = {**ref, "title": parent["title"] if parent else "(deleted)",
                            "status": parent["status"] if parent else "missing", "output_info": out}
        job["input_details"] = inputs
        job["dependents"] = self.dependents(puid, juid)
        job["job_dir"] = str(jdir)
        if job["status"] == "building":
            job["problems"] = self.queue_problems(job)
        if job["status"] == "waiting":
            from cryoplug.jobs.interactive import list_candidate_models
            job["candidates"] = list_candidate_models(jdir) if jdir.exists() else []
        return job

    def read_log(self, puid: str, juid: str, offset: int = 0, limit: int = 400_000) -> dict[str, Any]:
        path = self.job_dir(puid, juid) / "job.log"
        if not path.exists():
            return {"text": "", "offset": 0, "size": 0, "truncated": False}
        size = path.stat().st_size
        truncated = False
        if offset > size:
            offset = 0
        if offset == 0 and size > limit:
            offset, truncated = size - limit, True
        with open(path, "rb") as fh:
            fh.seek(offset)
            data = fh.read(limit)
        return {"text": data.decode("utf-8", errors="replace"), "offset": offset + len(data), "size": size, "truncated": truncated}

    def safe_path(self, puid: str, rel: str) -> Path:
        """A path inside the project directory (symlinked inputs are allowed to point elsewhere)."""
        relp = Path(rel)
        if relp.is_absolute() or ".." in relp.parts:
            raise NotFound("Invalid path")
        p = self.project_dir(puid) / relp
        if not p.exists():
            raise NotFound(f"File not found: {rel}")
        return p

    def list_files(self, puid: str, juid: str, sub: str = "") -> list[dict[str, Any]]:
        base = self.safe_path(puid, f"{juid}/{sub}".rstrip("/"))
        if not base.is_dir():
            raise ManagerError("Not a directory")
        entries = []
        root = self.project_dir(puid)
        for p in sorted(base.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            try:
                st = p.stat()
            except OSError:
                continue
            rel = os.path.relpath(Path(base, p.name), root)
            entries.append({"name": p.name, "path": rel, "is_dir": p.is_dir(), "size": st.st_size, "mtime": st.st_mtime,
                            "symlink": p.is_symlink()})
        return entries

    # ------------------------------------------------------ input resolution
    def resolve_inputs(self, job: dict[str, Any]) -> tuple[dict[str, Any], list[str], list[str]]:
        """Return (resolved inputs, jobs still pending, problems)."""
        puid = job["project_uid"]
        pdir = self.project_dir(puid)
        jt = get_job_type(job["type"])
        resolved: dict[str, Any] = {}
        waiting, broken = [], []
        for slot, ref in job["inputs"].items():
            parent = self.db.get_job(puid, ref["job"])
            if not parent:
                broken.append(f"{ref['job']} deleted")
                continue
            if parent["status"] in ("failed", "killed"):
                broken.append(f"{ref['job']} {parent['status']}")
                continue
            if parent["status"] != "completed":
                waiting.append(ref["job"])
                continue
            out = next((o for o in parent["outputs"] if o["name"] == ref["output"]), None)
            if out is None:
                if jt.slot(slot).required:
                    broken.append(f"{ref['job']} has no output '{ref['output']}'")
                continue  # optional input whose source output was not produced: ignore
            resolved[slot] = {
                "type": out["type"],
                "label": out.get("label", ""),
                "source": f"{ref['job']}.{ref['output']}",
                "path": str(pdir / out["path"]),
                "files": [str(pdir / f) for f in out.get("files", [out["path"]])],
                "meta": out.get("meta", {}),
                # the other outputs of the same job (e.g. the half maps that go with a map)
                "siblings": {o["name"]: {"type": o["type"], "label": o.get("label", ""), "source": f"{ref['job']}.{o['name']}",
                                         "path": str(pdir / o["path"]), "files": [str(pdir / f) for f in o.get("files", [o["path"]])],
                                         "meta": o.get("meta", {})}
                             for o in parent["outputs"] if o["name"] != ref["output"]},
            }
        return resolved, waiting, broken

    def ancestors(self, puid: str, juid: str) -> list[dict[str, Any]]:
        """Jobs upstream of ``juid``, plus completed jobs that validated the same model (for methods/statistics)."""
        pdir = self.project_dir(puid)
        seen: dict[str, dict[str, Any]] = {}
        job = self.job(puid, juid)
        stack = [r["job"] for r in job["inputs"].values()]
        model_ref = job["inputs"].get("model")
        if model_ref:
            for other in self.db.list_jobs(puid, statuses=["completed"]):
                if other["uid"] != juid and other["inputs"].get("model") == model_ref:
                    stack.append(other["uid"])
        while stack:
            uid = stack.pop()
            if uid in seen:
                continue
            j = self.db.get_job(puid, uid)
            if not j:
                continue
            seen[uid] = j
            stack.extend(r["job"] for r in j["inputs"].values())
        out = []
        for j in sorted(seen.values(), key=lambda x: x["num"]):
            outputs = [{**o, "files": [str(pdir / f) for f in o.get("files", [o["path"]])]} for o in j["outputs"]]
            out.append({"uid": j["uid"], "type": j["type"], "title": j["title"], "status": j["status"],
                        "params": j["params"], "outputs": outputs, "highlights": j["highlights"]})
        return out

    def build_spec(self, job: dict[str, Any], inputs: dict[str, Any], gpus: list[int]) -> dict[str, Any]:
        jt = get_job_type(job["type"])
        params = jt.coerce_params(job["params"])
        tools = {key: toolmod.job_tool_spec(self.config, key, self.tool_status.get(key)) for key in toolmod.TOOLS}
        return {
            "cryoplug_version": __version__,
            "project_uid": job["project_uid"],
            "uid": job["uid"],
            "type": job["type"],
            "title": job["title"],
            "project_dir": str(self.project_dir(job["project_uid"])),
            "params": params,
            "inputs": inputs,
            "tools": tools,
            "resources": {**self.job_resources(jt, params, job.get("requested") or {}), "gpus": gpus},
            "ancestors": self.ancestors(job["project_uid"], job["uid"]),
        }

    # ------------------------------------------------------- state syncing
    def sync_job_state(self, job: dict[str, Any]) -> dict[str, Any] | None:
        """Copy a worker's state.json into the database. Returns the state read."""
        path = self.job_dir(job["project_uid"], job["uid"]) / "state.json"
        if not path.exists():
            return None
        try:
            state = json.loads(path.read_text())
        except (OSError, ValueError):
            return None
        updates: dict[str, Any] = {
            "outputs": state.get("outputs", []),
            "highlights": state.get("highlights", []),
            "progress": float(state.get("progress") or 0),
            "message": str(state.get("message") or ""),
        }
        status = state.get("status")
        if status in ("running", "waiting", "completed", "failed", "killed"):
            updates["status"] = status
        if state.get("started_at"):
            updates["started_at"] = state["started_at"]
        if state.get("ended_at"):
            updates["ended_at"] = state["ended_at"]
        if state.get("error"):
            updates["error"] = str(state["error"])
        if status in ("waiting", "completed", "failed", "killed"):
            updates["gpus"] = []
        if job["status"] == "killed":  # do not resurrect a job the user killed
            updates.pop("status", None)
        self.db.update_job(job["project_uid"], job["uid"], **updates)
        return state

    # ============================================================ interactive
    def _context(self, puid: str, juid: str) -> tuple[JobContext, Any]:
        job = self.job(puid, juid)
        jt = get_job_type(job["type"])
        if not jt.interactive:
            raise ManagerError("Not an interactive job")
        jdir = self.job_dir(puid, juid)
        spec_path = jdir / "job.json"
        if not spec_path.exists():
            raise ManagerError("The session has not been prepared yet")
        spec = json.loads(spec_path.read_text())
        return JobContext(spec, jdir), jt()

    def launch_interactive(self, puid: str, juid: str) -> dict[str, Any]:
        job = self.job(puid, juid)
        if job["status"] != "waiting":
            raise ManagerError("The session is not ready (job must be waiting)")
        ctx, jt = self._context(puid, juid)
        cmd = jt.launch_command(ctx)
        tool_spec = ctx.tools.get(jt.tool or "", {})
        env = toolmod.tool_env(tool_spec)
        if self.config.display:
            env["DISPLAY"] = self.config.display
        if not env.get("DISPLAY") and not env.get("WAYLAND_DISPLAY"):
            raise ManagerError("No display configured on the server ([interactive] display in the config). "
                               "Download the session bundle and run it on your workstation instead.")
        log = open(ctx.path(f"{jt.tool}_gui.log"), "ab")
        try:
            proc = subprocess.Popen(toolmod.wrap_command(tool_spec, cmd, jt.tool or "gui"), cwd=str(ctx.job_dir), env=env,
                                    stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        except OSError as exc:
            raise ManagerError(f"Could not start {cmd[0]}: {exc}") from None
        finally:
            log.close()
        ctx.log(f"Launched {' '.join(cmd)} on display {env.get('DISPLAY')} (pid {proc.pid})")
        return {"pid": proc.pid, "display": env.get("DISPLAY"), "command": cmd}

    def save_upload(self, puid: str, juid: str, filename: str, fh: BinaryIO) -> dict[str, Any]:
        job = self.job(puid, juid)
        if job["status"] not in ("waiting", "building"):
            raise ManagerError("Uploads are only accepted by waiting interactive jobs")
        name = os.path.basename(filename or "").strip().replace(" ", "_")
        if not re.fullmatch(r"[A-Za-z0-9._-]+", name) or not name.lower().endswith((".pdb", ".cif", ".mmcif", ".ent")):
            raise ManagerError("Upload a .pdb/.cif model with a simple file name")
        if name.startswith("input_"):
            name = "uploaded_" + name
        dest = self.job_dir(puid, juid) / name
        with open(dest, "wb") as out:
            shutil.copyfileobj(fh, out)
        return {"path": name, "size": dest.stat().st_size}

    def session_bundle(self, puid: str, juid: str) -> Path:
        ctx, jt = self._context(puid, juid)
        zpath = ctx.path(f"{puid}_{juid}_{jt.name}.zip")
        with zipfile.ZipFile(zpath, "w", allowZip64=True) as zf:
            for arcname, src in jt.bundle_files(ctx):
                if src.is_file():
                    zf.write(src, f"{puid}_{juid}/{arcname}",
                             compress_type=zipfile.ZIP_STORED if arcname.endswith((".mrc", ".map", ".ccp4")) else zipfile.ZIP_DEFLATED)
        return zpath

    def finish_interactive(self, puid: str, juid: str, choice: str | None = None) -> dict[str, Any]:
        with self.lock:
            job = self.job(puid, juid)
            if job["status"] != "waiting":
                raise ManagerError("Only waiting interactive jobs can be finished")
            ctx, jt = self._context(puid, juid)
            try:
                jt.finalize(ctx, choice)
            except JobError as exc:
                raise ManagerError(str(exc)) from None
            ctx.write_state(status="completed", ended_at=time.time(), progress=1.0, message="Completed")
            ctx.log("Session finished")
            self.sync_job_state(self.job(puid, juid))
            return self.job(puid, juid)

    # ============================================================== queue
    def queue_overview(self) -> dict[str, Any]:
        jobs = self.db.list_jobs(statuses=["queued", "launched", "running", "waiting"])
        projects = {p["uid"]: p for p in self.db.list_projects(include_archived=True)}
        for j in jobs:
            project = projects.get(j["project_uid"]) or {}
            j["project_title"] = project.get("title", "")
            j["owner"] = project.get("owner", "")
            j["members"] = project.get("members", [])
        lanes = []
        for cfg in self.config.lanes:
            on_lane = [j for j in jobs if j.get("lane") == cfg.name and j["status"] in ("launched", "running")]
            used = sorted({g for j in on_lane for g in (j.get("gpus") or [])})
            lanes.append({"name": cfg.name, "type": cfg.type, "description": cfg.description, "max_jobs": cfg.max_jobs,
                          "running": len(on_lane), "queued": sum(1 for j in jobs if j.get("lane") == cfg.name and j["status"] == "queued"),
                          "gpus": cfg.gpus, "gpus_used": used,
                          "gpu_jobs": {str(g): {"project_uid": j["project_uid"], "uid": j["uid"], "type": j["type"], "title": j["title"],
                                                "owner": j["owner"]} for j in on_lane for g in (j.get("gpus") or [])},
                          "partition": cfg.partition, "partitions": cfg.all_partitions})
        return {"lanes": lanes, "jobs": jobs}

    # ============================================================ workflows
    def instantiate_workflow(self, puid: str, workflow_id: str, overrides: dict[str, dict] | None = None,
                             include: list[str] | None = None, queue: bool = False, lane: str | None = None,
                             created_by: str = "", roots: list[Path] | None = None) -> dict[str, Any]:
        from cryoplug.workflows import get_workflow
        wf = get_workflow(workflow_id)
        overrides = overrides or {}
        include_set = set(include) if include is not None else {n["id"] for n in wf["nodes"] if n.get("default", True)}
        active = [n for n in wf["nodes"] if not n.get("optional") or n["id"] in include_set]
        created: dict[str, dict[str, Any]] = {}
        for node in active:
            inputs = {}
            for slot, ref in (node.get("inputs") or {}).items():
                alternatives = ref if ref and isinstance(ref[0], (list, tuple)) else [ref]
                for alt in alternatives:
                    src_node, out = alt
                    if src_node in created:
                        inputs[slot] = {"job": created[src_node]["uid"], "output": out}
                        break
            params = {**(node.get("params") or {}), **(overrides.get(node["id"]) or {})}
            created[node["id"]] = self.create_job(puid, node["type"], params, inputs, node.get("title"), lane,
                                                  created_by=created_by, roots=roots)
        queued, problems = [], {}
        if queue:
            for node in active:
                job = created[node["id"]]
                try:
                    self.queue_job(puid, job["uid"], lane, roots=roots)
                    queued.append(job["uid"])
                except ManagerError as exc:
                    problems[job["uid"]] = str(exc)
        return {"jobs": [self.job(puid, created[n["id"]]["uid"]) for n in active], "queued": queued, "problems": problems}
