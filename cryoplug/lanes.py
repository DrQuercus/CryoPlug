"""Compute lanes: run workers on the local machine or submit them to a cluster scheduler."""
from __future__ import annotations

import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
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

    def launch(self, job: dict[str, Any], job_dir: Path, gpus: list[int], resources: dict[str, Any] | None = None) -> dict[str, Any]:
        env = os.environ.copy()
        # GPU numbers as nvidia-smi shows them (CUDA's default order puts the fastest GPU first)
        env.setdefault("CUDA_DEVICE_ORDER", "PCI_BUS_ID")
        if gpus:
            env["CUDA_VISIBLE_DEVICES"] = ",".join(str(g) for g in gpus)
        if (job.get("requested") or {}).get("num_cpus"):  # CPUs chosen for the job: libraries use that many threads
            threads = str((resources or {}).get("num_cpus") or job["requested"]["num_cpus"])
            for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
                env[name] = threads
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


def generated_script(cfg: LaneConfig, values: dict[str, Any]) -> str:
    """The SLURM script written from the lane's fields (the per-job choices are already in ``values``)."""
    lines = ["#!/bin/bash",
             "#SBATCH --job-name=cryoplug_{project_uid}_{job_uid}",
             "#SBATCH --output={job_dir}/cluster_stdout.log",
             "#SBATCH --error={job_dir}/cluster_stdout.log",
             "#SBATCH --cpus-per-task={num_cpus}"]
    if values["partition"]:
        lines.append("#SBATCH --partition={partition}")
    if values["account"]:
        lines.append("#SBATCH --account={account}")
    if values["qos"]:
        lines.append("#SBATCH --qos={qos}")
    if values["time"]:
        lines.append("#SBATCH --time={time}")
    if values["mem"]:
        lines.append("#SBATCH --mem={mem}")
    if int(values["num_gpus"]) > 0 and values["gres"]:
        lines.append("#SBATCH --gres={gres}:{num_gpus}")
    lines += cfg.sbatch_lines()
    lines.append("")
    if cfg.setup.strip():
        lines.append(cfg.setup.rstrip())
    lines.append("{worker_cmd}")
    return fill_template("\n".join(lines) + "\n", values)


class ClusterLane:
    def __init__(self, cfg: LaneConfig):
        self.cfg = cfg
        self.python = cfg.python or sys.executable
        self._overview: tuple[float, dict[str, Any]] | None = None

    def _shell(self, command: str, timeout: float = 60) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", "-c", command], capture_output=True, text=True, timeout=timeout)

    @property
    def is_slurm(self) -> bool:
        return self.cfg.submit_cmd.strip().startswith("sbatch")

    def render_script(self, job: dict[str, Any], job_dir: Path, resources: dict[str, Any]) -> str:
        cfg = self.cfg
        values = {
            "project_uid": job["project_uid"],
            "job_uid": job["uid"],
            "job_type": job["type"],
            "job_title": re.sub(r"[^A-Za-z0-9 ._+-]", "_", job.get("title", ""))[:80],  # user text: nothing the shell reads
            "job_dir": str(job_dir),
            "num_gpus": int(resources.get("num_gpus", 0)),
            "num_cpus": int(resources.get("num_cpus", 1)),
            "partition": resources.get("partition") or cfg.partition,
            "time": resources.get("time") or cfg.time_limit,
            "mem": resources.get("mem") or cfg.mem,
            "account": cfg.account,
            "qos": cfg.qos,
            "gres": cfg.gres,
            "setup": cfg.setup,
            "worker_cmd": shlex.join(worker_command(self.python, job_dir)),
        }
        if cfg.custom_script:
            return fill_template(cfg.script_template, values)
        return generated_script(cfg, values)

    def partition_flag(self) -> str:
        parts = self.cfg.all_partitions
        return f" -p {shlex.quote(','.join(parts))}" if parts else ""

    def overview(self, max_age: float = 20.0) -> dict[str, Any]:
        """Partitions, nodes and queue of a SLURM lane (sinfo / squeue), cached for ``max_age`` seconds."""
        if self._overview and time.monotonic() - self._overview[0] < max_age:
            return self._overview[1]
        result = self._read_overview()
        self._overview = (time.monotonic(), result)
        return result

    def _read_overview(self) -> dict[str, Any]:
        if not self.is_slurm:
            return {"available": False, "message": "The overview is available for SLURM lanes (sbatch)."}
        try:
            info = self._shell(f"sinfo -h -o '%P|%a|%l|%D|%T|%C|%G|%m'{self.partition_flag()}", timeout=15)
            queue = self._shell(f"squeue -h -o '%T|%u|%P'{self.partition_flag()}", timeout=15)
        except subprocess.TimeoutExpired:
            return {"available": False, "message": "sinfo / squeue did not answer within 15 s."}
        if info.returncode != 0:
            return {"available": False, "message": (info.stderr or info.stdout or "sinfo failed").strip()[-400:]}
        partitions: dict[str, dict[str, Any]] = {}
        for line in info.stdout.splitlines():
            fields = line.strip().split("|")
            if len(fields) < 8:
                continue
            name, avail, limit, nodes, node_state, cpus, gres, mem = fields[:8]
            default = name.endswith("*")
            name = name.rstrip("*")
            part = partitions.setdefault(name, {"name": name, "default": default, "available": avail, "time_limit": limit,
                                                "nodes": {}, "cpus": {"allocated": 0, "idle": 0, "other": 0, "total": 0},
                                                "gres": [], "memory_mb": mem})
            try:
                part["nodes"][node_state] = part["nodes"].get(node_state, 0) + int(nodes)
            except ValueError:
                pass
            try:
                a, i, o, t = (int(x) for x in cpus.split("/"))
                for key, value in zip(("allocated", "idle", "other", "total"), (a, i, o, t), strict=True):
                    part["cpus"][key] += value
            except ValueError:
                pass
            if gres and gres != "(null)" and gres not in part["gres"]:
                part["gres"].append(gres)
        jobs = {"running": 0, "pending": 0, "other": 0}
        users: dict[str, int] = {}
        if queue.returncode == 0:
            for line in queue.stdout.splitlines():
                fields = line.strip().split("|")
                if len(fields) < 2:
                    continue
                state = fields[0].upper()
                jobs["running" if state == "RUNNING" else "pending" if state == "PENDING" else "other"] += 1
                users[fields[1]] = users.get(fields[1], 0) + 1
        return {"available": True, "partitions": list(partitions.values()), "jobs": jobs,
                "users": sorted(users.items(), key=lambda kv: -kv[1])[:8]}

    def test(self) -> dict[str, Any]:
        """Is the scheduler reachable from the server? Lists the partitions it knows."""
        if not self.is_slurm:
            try:
                out = self._shell(shlex.quote((self.cfg.submit_cmd.split() or ["true"])[0]) + " --version 2>&1 || true", timeout=15)
            except subprocess.TimeoutExpired:
                return {"ok": False, "output": "No answer within 15 s"}
            return {"ok": True, "output": out.stdout.strip()[-400:], "partitions": []}
        try:
            version = self._shell("sinfo --version", timeout=15)
            parts = self._shell("sinfo -h -o '%P'", timeout=15)
        except subprocess.TimeoutExpired:
            return {"ok": False, "output": "sinfo did not answer within 15 s"}
        if version.returncode != 0:
            return {"ok": False, "output": (version.stderr or version.stdout or "sinfo not found").strip()[-400:]}
        names = list(dict.fromkeys(p.strip().rstrip("*") for p in parts.stdout.splitlines() if p.strip()))  # one line per node state
        missing = [p for p in self.cfg.all_partitions if p not in names]
        out = version.stdout.strip() + (f"\nPartitions: {', '.join(names)}" if names else "")
        if missing:
            out += f"\nUnknown partition(s): {', '.join(missing)}"
        return {"ok": not missing, "output": out, "partitions": names}

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
