"""Compute lanes: run workers on the local machine or submit them to a cluster scheduler."""
from __future__ import annotations

import os
import re
import shlex
import signal
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from cryoplug.config import LaneConfig

CLUSTER_DONE_STATES = {"COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL", "BOOT_FAIL", "DEADLINE", "PREEMPTED"}


def worker_command(python: str, job_dir: Path) -> list[str]:
    return [python, "-m", "cryoplug", "worker", str(job_dir)]


def fill_template(template: str, values: dict[str, Any]) -> str:
    """Replace {name} placeholders that we know, leaving anything else (e.g. ${SLURM_JOB_ID}) untouched."""
    return re.sub(r"\{(\w+)\}", lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), template)


class LocalLane:
    def __init__(self, cfg: LaneConfig):
        self.cfg = cfg
        self.python = cfg.python or sys.executable
        self._procs: dict[int, subprocess.Popen] = {}

    def launch(self, job: dict[str, Any], job_dir: Path, gpus: list[int]) -> dict[str, Any]:
        env = os.environ.copy()
        if gpus:
            env["CUDA_VISIBLE_DEVICES"] = ",".join(str(g) for g in gpus)
        env["CRYOPLUG_JOB"] = f"{job['project_uid']}/{job['uid']}"
        with open(job_dir / "worker.log", "ab") as log:
            proc = subprocess.Popen(worker_command(self.python, job_dir), cwd=str(job_dir), env=env, stdout=log,
                                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        self._procs[proc.pid] = proc
        return {"pid": proc.pid, "cluster_job_id": None}

    def is_alive(self, job: dict[str, Any]) -> bool:
        pid = job.get("pid")
        if not pid:
            return False
        proc = self._procs.get(pid)
        if proc is not None:
            alive = proc.poll() is None
            if not alive:
                self._procs.pop(pid, None)
            return alive
        try:  # worker started by a previous server instance
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def kill(self, job: dict[str, Any]) -> None:
        pid = job.get("pid")
        if not pid:
            return
        try:
            os.killpg(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return

        def hard_kill() -> None:
            try:
                os.killpg(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

        timer = threading.Timer(20.0, hard_kill)
        timer.daemon = True
        timer.start()


class ClusterLane:
    def __init__(self, cfg: LaneConfig):
        self.cfg = cfg
        self.python = cfg.python or sys.executable

    def _shell(self, command: str, timeout: float = 60) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", "-c", command], capture_output=True, text=True, timeout=timeout)

    def render_script(self, job: dict[str, Any], job_dir: Path, resources: dict[str, Any]) -> str:
        values = {
            "project_uid": job["project_uid"],
            "job_uid": job["uid"],
            "job_type": job["type"],
            "job_title": job.get("title", ""),
            "job_dir": str(job_dir),
            "num_gpus": int(resources.get("num_gpus", 0)),
            "num_cpus": int(resources.get("num_cpus", 1)),
            "worker_cmd": shlex.join(worker_command(self.python, job_dir)),
        }
        return fill_template(self.cfg.script_template, values)

    def launch(self, job: dict[str, Any], job_dir: Path, gpus: list[int], resources: dict[str, Any] | None = None) -> dict[str, Any]:
        script = job_dir / "cluster_submit.sh"
        script.write_text(self.render_script(job, job_dir, resources or {}))
        script.chmod(0o755)
        cmd = fill_template(self.cfg.submit_cmd, {"script": shlex.quote(str(script))})
        proc = self._shell(cmd)
        out = (proc.stdout + proc.stderr).strip()
        (job_dir / "cluster_submit.log").write_text(f"$ {cmd}\n{out}\n")
        if proc.returncode != 0:
            raise RuntimeError(f"Cluster submission failed: {out[-500:]}")
        m = re.search(r"(\d+)", proc.stdout)
        if not m:
            raise RuntimeError(f"Could not parse the cluster job id from: {out[-300:]}")
        return {"pid": None, "cluster_job_id": m.group(1)}

    def is_alive(self, job: dict[str, Any]) -> bool:
        cid = job.get("cluster_job_id")
        if not cid:
            return False
        try:
            proc = self._shell(fill_template(self.cfg.status_cmd, {"cluster_job_id": cid}), timeout=30)
        except subprocess.TimeoutExpired:
            return True  # scheduler busy: assume still there
        state = proc.stdout.strip().split("\n")[0].strip().upper() if proc.stdout.strip() else ""
        if not state:
            return False
        return state not in CLUSTER_DONE_STATES

    def kill(self, job: dict[str, Any]) -> None:
        cid = job.get("cluster_job_id")
        if cid:
            self._shell(fill_template(self.cfg.kill_cmd, {"cluster_job_id": cid}), timeout=30)


def make_lane(cfg: LaneConfig) -> LocalLane | ClusterLane:
    return ClusterLane(cfg) if cfg.type == "cluster" else LocalLane(cfg)
