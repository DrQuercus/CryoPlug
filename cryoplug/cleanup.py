"""Project storage: what can be cleared or deleted, the estimate and the cleanup itself (CryoSPARC's Clean Up Data).

The job folders hold three kinds of space one can give back: the intermediate results of iterative programs (every
iteration of a RELION run but the last), the preprocessing results that can be made again from the movies (corrected
micrographs, CTF images, particle stacks), and the jobs nobody needs any more: those killed or failed, and those that
are neither marked as a final result nor upstream of one. A plan assigns one action to each job of a project from
the options chosen (clear its files, delete it, or clear its intermediate results), with what it saves; the cleanup
applies the plan, children before parents, under the project id typed by the person as confirmation.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from cryoplug.jobs import get_job_type
from cryoplug.jobs.base import ACTIVE_STATUSES, folder_size

OVER = ("completed", "failed", "killed")
# key: (label, job types). "Clear preprocessing jobs" of CryoSPARC: results that can be made again from the movies.
PREPROCESSING_KINDS: dict[str, tuple[str, tuple[str, ...]]] = {
    "motioncorr": ("Motion correction", ("relion_motioncorr", "relion_tomo_motioncorr")),
    "ctf": ("CTF estimation", ("relion_ctffind", "relion_tomo_ctffind")),
    "extract": ("Particle extraction", ("relion_extract", "relion_tomo_extract", "relion_tomo_reconstruct_particle")),
    "downsample": ("Downsampled particles", ("relion_downsample",)),
    "restack": ("Restacked particles", ("relion_restack",)),
    "tomograms": ("Tomograms (reconstructed, denoised)", ("relion_tomo_reconstruct", "relion_tomo_denoise")),
    "denoise": ("Denoised micrographs", ("relion_topaz_denoise",)),
}
PREPROCESSING_TYPES = {t: k for k, (_, types) in PREPROCESSING_KINDS.items() for t in types}
ACTIONS = ("clear", "intermediate", "delete")
ACTION_LABELS = {"clear": "Clear", "intermediate": "Clear intermediate results", "delete": "Delete"}
CATEGORIES = ("preprocessing", "intermediate", "nonfinal", "killed", "failed")


def default_options() -> dict[str, Any]:
    """What the dialog ticks to start with, as CryoSPARC's: preprocessing and intermediate results cleared, killed and
    failed jobs deleted, the non-final jobs untouched."""
    return {"preprocessing": {k: True for k in PREPROCESSING_KINDS}, "intermediate": True, "nonfinal": "",
            "killed": "delete", "failed": "delete", "exclude": []}


def clean_options(options: dict[str, Any] | None) -> dict[str, Any]:
    """Options as the plan reads them: unknown keys dropped, the choices of a category "", "clear" or "delete"."""
    base = default_options()
    given = options or {}
    pre = given.get("preprocessing", base["preprocessing"])
    if isinstance(pre, bool):
        pre = {k: pre for k in PREPROCESSING_KINDS}
    elif not isinstance(pre, dict):
        pre = base["preprocessing"]
    out: dict[str, Any] = {"preprocessing": {k: bool(pre.get(k, False)) for k in PREPROCESSING_KINDS},
                           "intermediate": bool(given.get("intermediate", base["intermediate"]))}
    for key in ("nonfinal", "killed", "failed"):
        value = given.get(key, base[key])
        if value is True:
            value = "clear"
        if value not in ("", "clear", "delete", None, False):
            raise ValueError(f"{key}: '' (leave), 'clear' or 'delete' expected, not {value!r}")
        out[key] = value or ""
    out["exclude"] = [str(u) for u in (given.get("exclude") or [])]
    return out


def intermediate_files(jdir: Path, pattern: str, used: set[Path]) -> tuple[list[Path], int, int]:
    """The files of every iteration of a RELION run but the last, unless an output uses them: (files, bytes, last)."""
    rx = re.compile(pattern)
    found = []
    for f in jdir.iterdir() if jdir.exists() else []:
        m = rx.fullmatch(f.name)
        if m and f.is_file() and not f.is_symlink():
            found.append((int(m.group("it")), f))
    last = max((it for it, _ in found), default=0)
    doomed = [f for it, f in found if it < last and f.resolve() not in used]
    size = 0
    for f in doomed:
        try:
            size += f.stat().st_size
        except OSError:
            continue
    return doomed, size, last


def used_files(pdir: Path, job: dict[str, Any]) -> set[Path]:
    return {(pdir / f).resolve() for o in job.get("outputs") or []
            for f in [o.get("path"), o.get("thumbnail"), *(o.get("files") or [])] if f}


def upstream_of(uids: set[str], by: dict[str, dict[str, Any]]) -> set[str]:
    """The jobs given and every job they come from, through the inputs."""
    keep, todo = set(), list(uids)
    while todo:
        uid = todo.pop()
        if uid in keep or uid not in by:
            continue
        keep.add(uid)
        todo.extend(r["job"] for r in by[uid]["inputs"].values())
    return keep


class Planner:
    """The cleanup plan of a project, from the manager's point of view (its database and folders)."""

    def __init__(self, manager: Any, puid: str, options: dict[str, Any] | None = None):
        self.m = manager
        self.puid = puid
        self.options = clean_options(options)
        self.pdir = manager.project_dir(puid)

    # ------------------------------------------------------------------ the plan
    def plan(self) -> dict[str, Any]:
        jobs = sorted(self.m.db.list_jobs(self.puid), key=lambda j: j["num"])
        by = {j["uid"]: j for j in jobs}
        children: dict[str, list[str]] = {j["uid"]: [] for j in jobs}
        for j in jobs:
            for r in j["inputs"].values():
                children.setdefault(r["job"], []).append(j["uid"])
        finals = {j["uid"] for j in jobs if j.get("final")}
        keep = upstream_of(finals, by)
        opts = self.options
        exclude = set(opts["exclude"])
        entries: dict[str, dict[str, Any]] = {}
        protected: dict[str, str] = {}  # uid -> why it is left alone
        sizes: dict[str, int] = {}

        def size_of(j: dict[str, Any]) -> int:
            if j["uid"] not in sizes:
                if j.get("disk_bytes") is None:
                    sizes[j["uid"]] = folder_size(self.pdir / j["uid"]) if (self.pdir / j["uid"]).exists() else 0
                else:
                    sizes[j["uid"]] = int(j["disk_bytes"])
            return sizes[j["uid"]]

        def entry(j: dict[str, Any], category: str, action: str, savings: int, **extra: Any) -> None:
            jt = get_job_type(j["type"])
            entries[j["uid"]] = {"uid": j["uid"], "num": j["num"], "title": j["title"], "type": j["type"], "type_title": jt.title,
                                 "status": j["status"], "category": category, "action": action, "disk_bytes": size_of(j),
                                 "savings": savings, "dependents": len(children.get(j["uid"], [])), "final": bool(j.get("final")),
                                 "kept": j["uid"] in keep, **extra}

        for j in jobs:
            uid = j["uid"]
            if uid in exclude:
                continue
            if j["status"] in ACTIVE_STATUSES or j["status"] == "waiting":
                protected[uid] = "running"
                continue
            if any(by[c]["status"] in ACTIVE_STATUSES or by[c]["status"] == "waiting" for c in children.get(uid, []) if c in by):
                protected[uid] = "used by a running job"
                continue
            if j["status"] == "building":
                continue  # nothing on disk worth the name
            jt = get_job_type(j["type"])
            if j["status"] == "killed" and opts["killed"]:
                entry(j, "killed", opts["killed"], size_of(j))
            elif j["status"] == "failed" and opts["failed"]:
                entry(j, "failed", opts["failed"], size_of(j))
            elif j["type"] in PREPROCESSING_TYPES and opts["preprocessing"].get(PREPROCESSING_TYPES[j["type"]]):
                entry(j, "preprocessing", "clear", size_of(j), kind=PREPROCESSING_TYPES[j["type"]])
            elif opts["nonfinal"] and uid not in keep and j["status"] in OVER:
                entry(j, "nonfinal", opts["nonfinal"], size_of(j))
            elif opts["intermediate"] and jt.intermediate and j["status"] in OVER:
                _, saved, last = intermediate_files(self.pdir / uid, jt.intermediate, used_files(self.pdir, j))
                if saved > 0:
                    entry(j, "intermediate", "intermediate", saved, last_iteration=last)

        # a job is deleted only when every job it feeds goes too: otherwise its files are cleared and it stays
        changed = True
        while changed:
            changed = False
            for uid, e in entries.items():
                if e["action"] == "delete" and any(c not in entries or entries[c]["action"] != "delete" for c in children.get(uid, [])):
                    e["action"], e["downgraded"] = "clear", True
                    changed = True

        # what the categories would hold whatever the options: the counts shown next to the boxes
        available = {
            "preprocessing": {k: sum(1 for j in jobs if j["type"] in types and j["status"] in OVER and j["uid"] not in protected)
                              for k, (_, types) in PREPROCESSING_KINDS.items()},
            "intermediate": sum(1 for j in jobs if get_job_type(j["type"]).intermediate and j["status"] in OVER and j["uid"] not in protected),
            "nonfinal": sum(1 for j in jobs if j["uid"] not in keep and j["status"] in OVER and j["uid"] not in protected),
            "killed": sum(1 for j in jobs if j["status"] == "killed" and j["uid"] not in protected),
            "failed": sum(1 for j in jobs if j["status"] == "failed" and j["uid"] not in protected),
        }
        summary = {a: {"jobs": 0, "bytes": 0} for a in ACTIONS}
        for e in entries.values():
            summary[e["action"]]["jobs"] += 1
            summary[e["action"]]["bytes"] += e["savings"]
        summary["total"] = {"jobs": len(entries), "bytes": sum(e["savings"] for e in entries.values())}
        measured = [j for j in jobs if j["status"] in OVER]
        total_bytes = sum(int(j["disk_bytes"] or 0) for j in jobs)
        return {
            "project": self.puid, "options": opts, "jobs": len(jobs), "disk_bytes": total_bytes,
            "unmeasured": sum(1 for j in measured if j.get("disk_bytes") is None),
            "intermediate_bytes": self.intermediate_total(jobs),
            "finals": sorted(finals, key=lambda u: by[u]["num"]), "kept": len(keep),
            "entries": sorted(entries.values(), key=lambda e: e["num"]),
            "protected": [{"uid": u, "reason": r} for u, r in protected.items()],
            "available": available, "summary": summary,
            "labels": {"preprocessing": {k: v[0] for k, v in PREPROCESSING_KINDS.items()}, "actions": ACTION_LABELS},
            "measuring": self.m.measuring_progress(self.puid), "measured_at": self.m.db.get_meta(f"measured:{self.puid}"),
            "last_cleanup": self.m.db.get_meta(f"cleanup:{self.puid}"),
        }

    def intermediate_total(self, jobs: list[dict[str, Any]]) -> int:
        """Bytes of the intermediate results of the whole project (the header's "Intermediate results")."""
        total = 0
        for j in jobs:
            jt = get_job_type(j["type"])
            if jt.intermediate and j["status"] in OVER:
                total += intermediate_files(self.pdir / j["uid"], jt.intermediate, used_files(self.pdir, j))[1]
        return total

    # --------------------------------------------------------------- the cleanup
    def execute(self, confirm: str) -> dict[str, Any]:
        if (confirm or "").strip() != self.puid:
            raise ValueError(f"Type the project id ({self.puid}) to confirm the cleanup")
        plan = self.plan()
        done: dict[str, list[str]] = {"deleted": [], "cleared": [], "trimmed": []}
        errors: list[dict[str, str]] = []
        freed = 0
        # children before parents: a deleted job never leaves a child pointing at it
        for e in sorted((e for e in plan["entries"] if e["action"] == "delete"), key=lambda e: -e["num"]):
            try:
                self.m.delete_job(self.puid, e["uid"], force=True)
                done["deleted"].append(e["uid"])
                freed += e["savings"]
            except Exception as exc:  # noqa: BLE001 - one job failing never stops the others
                errors.append({"uid": e["uid"], "error": str(exc)})
        for e in (e for e in plan["entries"] if e["action"] == "clear"):
            try:
                self.m.clear_job(self.puid, e["uid"])
                done["cleared"].append(e["uid"])
                freed += e["savings"]
            except Exception as exc:  # noqa: BLE001
                errors.append({"uid": e["uid"], "error": str(exc)})
        for e in (e for e in plan["entries"] if e["action"] == "intermediate"):
            try:
                res = self.m.clear_intermediate(self.puid, e["uid"])
                done["trimmed"].append(e["uid"])
                freed += int(res.get("freed_bytes") or 0)
            except Exception as exc:  # noqa: BLE001
                errors.append({"uid": e["uid"], "error": str(exc)})
        record = {"at": time.time(), "freed_bytes": freed, "deleted": len(done["deleted"]), "cleared": len(done["cleared"]),
                  "trimmed": len(done["trimmed"]), "errors": len(errors)}
        self.m.db.set_meta(f"cleanup:{self.puid}", record)
        return {**done, "freed_bytes": freed, "errors": errors, "planned": plan["summary"], "record": record}
