"""Background scheduler: launches queued jobs when inputs and resources are ready, tracks running workers."""
from __future__ import annotations

import json
import logging
import threading
import time
import traceback
from typing import Any

from cryoplug.jobs.base import TERMINAL_STATUSES
from cryoplug.manager import Manager

log = logging.getLogger("cryoplug.scheduler")

LAUNCH_GRACE = 30.0  # seconds a launched worker has to write its first state before liveness is enforced


class Scheduler:
    def __init__(self, manager: Manager, interval: float = 2.0):
        self.manager = manager
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="cryoplug-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # pragma: no cover - keep the loop alive
                log.error("Scheduler error:\n%s", traceback.format_exc())
            self._stop.wait(self.interval)

    # ----------------------------------------------------------------- tick
    def tick(self) -> None:
        with self.manager.lock:
            self.sync_active()
            self.launch_ready()

    def sync_active(self) -> None:
        m = self.manager
        for job in m.db.list_jobs(statuses=["launched", "running"]):
            state = m.sync_job_state(job)
            status = (state or {}).get("status")
            if status in TERMINAL_STATUSES or status == "waiting":
                continue
            lane = m.lanes.get(job.get("lane") or "")
            if lane is None:
                m.db.update_job(job["project_uid"], job["uid"], status="failed", error=f"Lane '{job.get('lane')}' no longer exists",
                                ended_at=time.time(), gpus=[])
                continue
            launched_at = job.get("started_at") or job.get("queued_at") or 0
            if state is None and time.time() - launched_at < LAUNCH_GRACE:
                continue
            if not lane.is_alive(job):
                # re-read: the worker may have finished between the two checks
                state = m.sync_job_state(job)
                if (state or {}).get("status") in TERMINAL_STATUSES or (state or {}).get("status") == "waiting":
                    continue
                m.db.update_job(job["project_uid"], job["uid"], status="failed", ended_at=time.time(), gpus=[],
                                error="The worker process exited unexpectedly (see worker.log / cluster log)",
                                message="Worker lost")

    def launch_ready(self) -> None:
        m = self.manager
        queued = sorted(m.db.list_jobs(statuses=["queued"]), key=lambda j: j.get("queued_at") or 0)
        if not queued:
            return
        active = m.db.list_jobs(statuses=["launched", "running"])
        for job in queued:
            inputs, waiting, broken = m.resolve_inputs(job)
            if broken:
                self._message(job, "Blocked: " + "; ".join(broken))
                continue
            if waiting:
                self._message(job, "Waiting for " + ", ".join(sorted(set(waiting))))
                continue
            try:
                lane_cfg = m.config.lane(job.get("lane"))
            except KeyError:
                self._message(job, f"Unknown lane '{job.get('lane')}'")
                continue
            on_lane = [j for j in active if j.get("lane") == lane_cfg.name]
            if len(on_lane) >= lane_cfg.max_jobs:
                self._message(job, f"Waiting for a free slot on lane '{lane_cfg.name}'")
                continue
            need = int((job.get("resources") or {}).get("num_gpus", 0))
            gpus: list[int] = []
            if lane_cfg.type == "local" and lane_cfg.gpus and need > 0:
                used = {g for j in on_lane for g in (j.get("gpus") or [])}
                free = [g for g in lane_cfg.gpus if g not in used]
                if len(free) < need:
                    self._message(job, f"Waiting for {need} free GPU(s) on lane '{lane_cfg.name}'")
                    continue
                gpus = free[:need]
            self._launch(job, inputs, gpus)
            job = m.job(job["project_uid"], job["uid"])
            if job["status"] == "launched":
                active.append(job)

    def _message(self, job: dict[str, Any], message: str) -> None:
        if job.get("message") != message:
            self.manager.db.update_job(job["project_uid"], job["uid"], message=message)

    def _launch(self, job: dict[str, Any], inputs: dict[str, Any], gpus: list[int]) -> None:
        m = self.manager
        puid, juid = job["project_uid"], job["uid"]
        jdir = m.job_dir(puid, juid)
        jdir.mkdir(parents=True, exist_ok=True)
        try:
            spec = m.build_spec(job, inputs, gpus)
            for name in ("state.json", "report.json"):
                (jdir / name).unlink(missing_ok=True)
            (jdir / "job.json").write_text(json.dumps(spec, indent=1, default=str))
            lane = m.lanes[job["lane"]]
            if lane.cfg.type == "cluster":
                info = lane.launch(job, jdir, gpus, spec["resources"])  # type: ignore[call-arg]
            else:
                info = lane.launch(job, jdir, gpus)
        except Exception as exc:
            log.error("Launch of %s/%s failed: %s", puid, juid, exc)
            m.db.update_job(puid, juid, status="failed", error=f"Launch failed: {exc}", ended_at=time.time())
            return
        m.db.update_job(puid, juid, status="launched", pid=info.get("pid"), cluster_job_id=info.get("cluster_job_id"),
                        gpus=gpus, started_at=time.time(), ended_at=None, message=f"Launched on {job['lane']}"
                        + (f" (GPU {','.join(map(str, gpus))})" if gpus else ""))
