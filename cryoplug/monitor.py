"""Live view of the hardware, as htop and nvtop show it: CPU, memory, GPUs, storage, network and processes.

Everything is read from /proc (Linux) and nvidia-smi.  A background thread samples every 2 s while someone looks
at the Resources page (it stops a minute after the last request) and keeps five minutes of history for the
graphs.  Processes started by CryoPlug workers are named after their job (P1/J5).
"""
from __future__ import annotations

import logging
import os
import pwd
import re
import shutil
import socket
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

log = logging.getLogger("cryoplug.monitor")

INTERVAL = 2.0
HISTORY = 150  # 5 minutes
IDLE_STOP = 60.0
GPU_TIMEOUT = 8.0  # seconds given to nvidia-smi
GPU_RETRY = 300.0  # after nvidia-smi hung, seconds before asking it again
CLK_TCK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
PAGE_SIZE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096
PSEUDO_FS = {"proc", "sysfs", "devtmpfs", "devpts", "tmpfs", "cgroup", "cgroup2", "securityfs", "pstore", "debugfs",
             "tracefs", "configfs", "fusectl", "mqueue", "hugetlbfs", "bpf", "autofs", "binfmt_misc", "rpc_pipefs",
             "nsfs", "efivarfs", "squashfs", "ramfs", "fuse.gvfsd-fuse", "fuse.portal", "overlay", "selinuxfs", "nfsd"}
DISK_RE = re.compile(r"(sd[a-z]+|nvme\d+n\d+|vd[a-z]+|xvd[a-z]+|hd[a-z]+|mmcblk\d+|md\d+)")
GPU_FIELDS = ("index", "uuid", "name", "utilization.gpu", "utilization.memory", "memory.used", "memory.total",
              "temperature.gpu", "power.draw", "power.limit", "fan.speed")


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def _num(value: str) -> float | None:
    try:
        return float(value.strip())
    except (ValueError, AttributeError):
        return None  # "[N/A]", "[Not Supported]"


def nvidia_smi_path(config) -> str | None:
    return getattr(config, "nvidia_smi", "") or shutil.which("nvidia-smi")


def _run(cmd: list[str], timeout: float) -> tuple[int, str, str] | None:
    """(return code, stdout, stderr) of a short command, or None if it did not answer in time. Unlike
    subprocess.run, never waits without limit: a wedged GPU driver can leave nvidia-smi stuck in the kernel,
    where even SIGKILL waits."""
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.communicate(timeout=1)
        except subprocess.TimeoutExpired:
            pass  # cannot be reaped yet: left behind rather than blocking the caller
        return None
    return proc.returncode, out, err


def detect_gpus(nvidia_smi: str | None) -> list[dict[str, Any]]:
    """The NVIDIA GPUs of this machine (index as nvidia-smi numbers them, name, memory)."""
    if not nvidia_smi:
        return []
    try:
        out = _run([nvidia_smi, "--query-gpu=index,name,memory.total,uuid", "--format=csv,noheader,nounits"], timeout=10)
    except OSError:
        return []
    if out is None or out[0] != 0:
        return []
    gpus = []
    for line in out[1].splitlines():
        fields = [f.strip() for f in line.split(",")]
        if len(fields) >= 4 and fields[0].isdigit():
            gpus.append({"index": int(fields[0]), "name": fields[1], "memory_total": _num(fields[2]), "uuid": fields[3]})
    return gpus


class Monitor:
    def __init__(self, config, manager=None):
        self.config = config
        self.manager = manager  # to name the CryoPlug jobs of worker processes
        self.nvidia_smi = nvidia_smi_path(config)
        self.available = Path("/proc/stat").exists()
        self.history: deque[dict[str, Any]] = deque(maxlen=HISTORY)
        self.latest: dict[str, Any] | None = None
        self._lock = threading.Lock()  # one sample at a time
        self._start_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._last_request = 0.0
        self._prev: dict[str, Any] = {}
        self._filesystems_cache: tuple[float, list[dict[str, Any]]] = (0.0, [])
        self._statvfs_waiting: dict[str, threading.Thread] = {}  # mount -> statvfs still waiting for its answer
        self._gpu_hung_until = 0.0  # nvidia-smi did not answer: not asked again before then
        self._projects: tuple[float, dict[str, str]] = (0.0, {})
        self._users: dict[int, str] = {}
        self._cpu_model = ""

    # ------------------------------------------------------------- public
    def snapshot(self, history: bool = False) -> dict[str, Any]:
        self._last_request = time.monotonic()
        with self._start_lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, name="cryoplug-monitor", daemon=True)
                self._thread.start()
        latest = self.latest
        if latest is None or time.time() - latest["time"] > 3 * INTERVAL or (self.available and (latest.get("cpu") or {}).get("total") is None):
            # first look (or the sampler just stopped): sample now, twice for the rates; while a slow sample
            # holds the lock (nvidia-smi taking its time), the last one is served
            if self._lock.acquire(timeout=GPU_TIMEOUT + 2):
                try:
                    if self.latest is None or time.time() - self.latest["time"] > 3 * INTERVAL:
                        self._sample()
                    if self.available and (self.latest.get("cpu") or {}).get("total") is None:
                        time.sleep(0.3)
                        self._sample()
                finally:
                    self._lock.release()
            latest = self.latest
        data = dict(latest or {})
        if history:
            data["history"] = list(self.history)
        return data

    def gpu_memory(self, max_age: float = 30.0) -> dict[int, float]:
        """Memory used on each GPU (MiB): from a recent sample, otherwise asked to nvidia-smi now (the automatic
        GPU choice of the scheduler must also see the programs started outside CryoPlug)."""
        latest = self.latest
        if latest and time.time() - latest["time"] <= max_age and latest.get("gpus"):
            return {g["index"]: g.get("memory_used") or 0.0 for g in latest["gpus"]}
        out = self._nvidia_smi(["--query-gpu=index,memory.used", "--format=csv,noheader,nounits"], timeout=5)
        used = {}
        for line in out[1].splitlines() if out and out[0] == 0 else []:
            fields = [f.strip() for f in line.split(",")]
            if len(fields) >= 2 and fields[0].isdigit() and _num(fields[1]) is not None:
                used[int(fields[0])] = _num(fields[1])
        return used

    def _nvidia_smi(self, args: list[str], timeout: float) -> tuple[int, str, str] | str | None:
        """nvidia-smi's answer; None without nvidia-smi; an error message when it fails or hangs (then it is left
        alone for a few minutes, so that stuck processes do not pile up)."""
        if not self.nvidia_smi:
            return None
        if time.monotonic() < self._gpu_hung_until:
            return "nvidia-smi does not answer (GPU driver busy or stuck); trying again in a few minutes"
        try:
            out = _run([self.nvidia_smi, *args], timeout=timeout)
        except OSError as exc:
            return f"nvidia-smi: {exc}"
        if out is None:
            self._gpu_hung_until = time.monotonic() + GPU_RETRY
            return f"nvidia-smi did not answer within {timeout:g} s (GPU driver busy or stuck)"
        return out

    # ------------------------------------------------------------- loop
    def _loop(self) -> None:
        while time.monotonic() - self._last_request < IDLE_STOP:
            try:
                with self._lock:
                    self._sample()
            except Exception:  # pragma: no cover - keep the sampler alive
                log.exception("Monitor sample failed")
            time.sleep(INTERVAL)
        self._prev.clear()  # rates restart from scratch at the next look

    def _sample(self) -> None:
        now, mono = time.time(), time.monotonic()
        memory = self._memory()
        gpus, gpu_error, gpu_procs = self._gpus()
        sample: dict[str, Any] = {
            "time": now,
            "available": self.available,
            "hostname": socket.gethostname(),
            "uptime": _num(_read("/proc/uptime").split(" ")[0]) if self.available else None,
            "cpu": self._cpu(),
            "memory": memory,
            "gpus": gpus,
            "gpu_error": gpu_error,
            "network": self._network(mono),
            "disk_io": self._disk_io(mono),
            "filesystems": self._filesystems(),
        }
        sample["processes"] = self._processes(mono, memory.get("total") or 0, gpu_procs)
        try:
            sample["load"] = list(os.getloadavg())
        except OSError:
            sample["load"] = None
        self.latest = sample
        cpu = sample["cpu"] or {}
        self.history.append({
            "t": round(now, 1), "cpu": cpu.get("total"), "mem": memory.get("percent"),
            "gpus": [[g.get("utilization"), g.get("memory_percent")] for g in gpus],
            "rx": sample["network"].get("rx"), "tx": sample["network"].get("tx"),
            "read": sample["disk_io"].get("read"), "write": sample["disk_io"].get("write"),
        })

    # ------------------------------------------------------------- parts
    def _cpu(self) -> dict[str, Any] | None:
        text = _read("/proc/stat")
        if not text:
            return None
        rows = {}
        for line in text.splitlines():
            if line.startswith("cpu"):
                name, *values = line.split()
                values = [int(v) for v in values[:8]]
                rows[name] = (values[3] + values[4], sum(values))  # idle + iowait, total
        prev = self._prev.get("cpu")
        self._prev["cpu"] = rows

        def percent(name: str) -> float | None:
            if not prev or name not in prev:
                return None
            idle, total = rows[name][0] - prev[name][0], rows[name][1] - prev[name][1]
            return round(100.0 * (1 - idle / total), 1) if total > 0 else 0.0

        cores = sorted((n for n in rows if n != "cpu"), key=lambda n: int(n[3:]))
        if not self._cpu_model:
            match = re.search(r"model name\s*:\s*(.+)", _read("/proc/cpuinfo"))
            self._cpu_model = match.group(1).strip() if match else ""
        return {"total": percent("cpu"), "cores": [percent(n) for n in cores], "count": len(cores), "model": self._cpu_model}

    def _memory(self) -> dict[str, Any]:
        info = {}
        for line in _read("/proc/meminfo").splitlines():
            key, _, rest = line.partition(":")
            value = rest.strip().split(" ")[0]
            if value.isdigit():
                info[key] = int(value) * 1024
        total = info.get("MemTotal", 0)
        if not total:
            return {}
        available = info.get("MemAvailable", info.get("MemFree", 0))
        cache = max(0, info.get("Buffers", 0) + info.get("Cached", 0) + info.get("SReclaimable", 0) - info.get("Shmem", 0))
        used = total - available
        swap_total = info.get("SwapTotal", 0)
        swap_used = swap_total - info.get("SwapFree", 0)
        return {"total": total, "used": used, "available": available, "cache": cache, "percent": round(100.0 * used / total, 1),
                "swap_total": swap_total, "swap_used": swap_used}

    def _gpus(self) -> tuple[list[dict[str, Any]], str | None, dict[int, list[dict[str, Any]]]]:
        out = self._nvidia_smi([f"--query-gpu={','.join(GPU_FIELDS)}", "--format=csv,noheader,nounits"], timeout=GPU_TIMEOUT)
        if out is None:
            return [], None, {}
        if isinstance(out, str):
            return [], out, {}
        if out[0] != 0:
            return [], (out[2] or out[1] or "nvidia-smi failed").strip()[-300:], {}
        apps = self._nvidia_smi(["--query-compute-apps=pid,gpu_uuid,used_memory", "--format=csv,noheader,nounits"], timeout=GPU_TIMEOUT)
        gpus, by_uuid = [], {}
        for line in out[1].splitlines():
            f = [x.strip() for x in line.split(",")]
            if len(f) < len(GPU_FIELDS) or not f[0].isdigit():
                continue
            used, total = _num(f[5]), _num(f[6])
            gpu = {"index": int(f[0]), "uuid": f[1], "name": f[2], "utilization": _num(f[3]), "memory_utilization": _num(f[4]),
                   "memory_used": used, "memory_total": total,
                   "memory_percent": round(100.0 * used / total, 1) if used is not None and total else None,
                   "temperature": _num(f[7]), "power": _num(f[8]), "power_limit": _num(f[9]), "fan": _num(f[10]), "processes": []}
            gpus.append(gpu)
            by_uuid[gpu["uuid"]] = gpu
        procs: dict[int, list[dict[str, Any]]] = {}
        for line in apps[1].splitlines() if isinstance(apps, tuple) and apps[0] == 0 else []:
            f = [x.strip() for x in line.split(",")]
            if len(f) >= 3 and f[0].isdigit() and f[1] in by_uuid:
                entry = {"pid": int(f[0]), "gpu": by_uuid[f[1]]["index"], "memory": _num(f[2])}
                by_uuid[f[1]]["processes"].append(entry)
                procs.setdefault(entry["pid"], []).append(entry)
        return gpus, None, procs

    def _rates(self, key: str, mono: float, values: dict[str, int]) -> dict[str, float | None]:
        prev = self._prev.get(key)
        self._prev[key] = (mono, values)
        if not prev or mono <= prev[0]:
            return {k: None for k in values}
        dt = mono - prev[0]
        return {k: max(0.0, (v - prev[1].get(k, v)) / dt) for k, v in values.items()}

    def _network(self, mono: float) -> dict[str, float | None]:
        rx = tx = 0
        for line in _read("/proc/net/dev").splitlines()[2:]:
            name, _, rest = line.partition(":")
            fields = rest.split()
            if name.strip() == "lo" or len(fields) < 9:
                continue
            rx += int(fields[0])
            tx += int(fields[8])
        return self._rates("net", mono, {"rx": rx, "tx": tx})

    def _disk_io(self, mono: float) -> dict[str, float | None]:
        read = write = 0
        for line in _read("/proc/diskstats").splitlines():
            fields = line.split()
            if len(fields) >= 10 and DISK_RE.fullmatch(fields[2]):
                read += int(fields[5]) * 512
                write += int(fields[9]) * 512
        return self._rates("disk", mono, {"read": read, "write": write})

    def _filesystems(self) -> list[dict[str, Any]]:
        checked, cached = self._filesystems_cache
        if time.monotonic() - checked < 30 and cached:
            return cached
        roles = {"projects": str(Path(self.config.projects_root).expanduser()), "CryoPlug data": str(Path(self.config.data_dir).expanduser())}
        seen, result = set(), []
        mounts = []
        for line in _read("/proc/mounts").splitlines():
            parts = line.split()
            if len(parts) < 3:
                continue
            device, mount, fstype = parts[0], parts[1].replace("\\040", " "), parts[2]
            if fstype in PSEUDO_FS and not (fstype == "overlay" and mount == "/"):
                continue
            if mount.startswith(("/proc", "/sys", "/dev", "/run", "/snap", "/boot/efi")) or device in seen:
                continue
            seen.add(device)
            mounts.append((device, mount, fstype))
        for device, mount, fstype in mounts:
            st = self._statvfs(mount)
            if st == "hung":
                result.append({"mount": mount, "device": device, "type": fstype, "error": "not responding"})
                continue
            if st is None or not st.f_blocks:
                continue
            total = st.f_blocks * st.f_frsize
            used = (st.f_blocks - st.f_bfree) * st.f_frsize
            result.append({"mount": mount, "device": device, "type": fstype, "total": total, "used": used,
                           "free": st.f_bavail * st.f_frsize, "percent": round(100.0 * used / total, 1), "roles": []})
        for role, path in roles.items():  # the deepest mount point containing the path
            owner = max((fs for fs in result if "total" in fs and (path == fs["mount"] or path.startswith(fs["mount"].rstrip("/") + "/"))),
                        key=lambda fs: len(fs["mount"]), default=None)
            if owner is not None:
                owner["roles"].append(role)
        result.sort(key=lambda fs: (not fs.get("roles"), fs["mount"]))
        self._filesystems_cache = (time.monotonic(), result)
        return result

    def _statvfs(self, path: str, timeout: float = 2.0) -> os.statvfs_result | None | str:
        """os.statvfs that gives up on a filesystem that does not answer (a stale NFS mount blocks forever).
        A mount keeps at most one waiting thread: it is asked again once that one got its answer."""
        waiting = self._statvfs_waiting.get(path)
        if waiting is not None:
            if waiting.is_alive():
                return "hung"
            del self._statvfs_waiting[path]
        result: dict[str, Any] = {}

        def run() -> None:
            try:
                result["value"] = os.statvfs(path)
            except OSError:
                result["value"] = None

        thread = threading.Thread(target=run, name="cryoplug-statvfs", daemon=True)
        thread.start()
        thread.join(timeout)
        if thread.is_alive():
            self._statvfs_waiting[path] = thread
            return "hung"
        return result.get("value")

    def _project_dirs(self) -> dict[str, str]:
        checked, mapping = self._projects
        if self.manager is not None and time.monotonic() - checked > 30:
            try:
                mapping = {str(Path(p["dir"])): p["uid"] for p in self.manager.db.list_projects(include_archived=True)}
            except Exception:  # pragma: no cover
                mapping = {}
            self._projects = (time.monotonic(), mapping)
        return mapping

    def _user(self, uid: int) -> str:
        if uid not in self._users:
            try:
                self._users[uid] = pwd.getpwuid(uid).pw_name
            except KeyError:
                self._users[uid] = str(uid)
        return self._users[uid]

    def _processes(self, mono: float, mem_total: int, gpu_procs: dict[int, list[dict[str, Any]]]) -> dict[str, Any]:
        if not self.available:
            return {"count": 0, "running": 0, "top": []}
        prev = self._prev.get("procs") or {}
        prev_time = self._prev.get("procs_time")
        rows: dict[int, tuple] = {}
        children: dict[int, list[int]] = {}
        for name in os.listdir("/proc"):
            if not name.isdigit():
                continue
            stat = _read(f"/proc/{name}/stat")
            close = stat.rfind(")")
            if close < 0:
                continue
            fields = stat[close + 2:].split()
            if len(fields) < 22:
                continue
            pid = int(name)
            comm = stat[stat.find("(") + 1:close]
            ppid, ticks, start, rss = int(fields[1]), int(fields[11]) + int(fields[12]), int(fields[19]), int(fields[21]) * PAGE_SIZE
            rows[pid] = (comm, ppid, ticks, start, rss, fields[0])
            children.setdefault(ppid, []).append(pid)
        self._prev["procs"] = {pid: (r[2], r[3]) for pid, r in rows.items()}
        self._prev["procs_time"] = mono
        dt = mono - prev_time if prev_time else 0.0

        def cpu_of(pid: int) -> float:
            before = prev.get(pid)
            row = rows[pid]
            if not before or before[1] != row[3] or dt <= 0:
                return 0.0
            return round(100.0 * (row[2] - before[0]) / (dt * CLK_TCK), 1)

        # CryoPlug workers ("python -m cryoplug worker <job dir>") and everything they started
        jobs: dict[int, tuple[str, str]] = {}
        projects = self._project_dirs()
        for pid, row in rows.items():
            if row[0].startswith("python") or row[0] == "cryoplug":
                cmd = _read(f"/proc/{pid}/cmdline").split("\0")
                if "worker" in cmd and any(c == "cryoplug" or c.endswith("/cryoplug") for c in cmd):
                    job_dir = Path(cmd[-1] if cmd[-1] else cmd[-2])
                    puid = projects.get(str(job_dir.parent), "")
                    label = (f"{puid}/{job_dir.name}" if puid else job_dir.name, puid)
                    stack = [pid]
                    while stack:
                        p = stack.pop()
                        if p not in jobs:
                            jobs[p] = label
                            stack.extend(children.get(p, []))
        cpu = {pid: cpu_of(pid) for pid in rows}
        top = sorted(rows, key=lambda p: (-cpu[p], -rows[p][4]))[:20]
        extra = [p for p in rows if (p in jobs or p in gpu_procs) and p not in top]
        chosen = top + sorted(extra, key=lambda p: -cpu[p])[:25]
        out = []
        for pid in chosen:
            comm, ppid, ticks, start, rss, state = rows[pid]
            status = _read(f"/proc/{pid}/status")
            match = re.search(r"^Uid:\s+(\d+)", status, re.M)
            cmd = _read(f"/proc/{pid}/cmdline").replace("\0", " ").strip() or f"[{comm}]"
            job = jobs.get(pid)
            out.append({"pid": pid, "user": self._user(int(match.group(1))) if match else "?", "name": comm,
                        "cmd": cmd[:400], "cpu": cpu[pid], "memory": rss,
                        "memory_percent": round(100.0 * rss / mem_total, 1) if mem_total else None,
                        "time": round(ticks / CLK_TCK), "state": state, "job": job[0] if job else None,
                        "project": job[1] if job else None,
                        "gpu_memory": sum(e.get("memory") or 0 for e in gpu_procs.get(pid, [])) or None,
                        "gpus": sorted({e["gpu"] for e in gpu_procs.get(pid, [])})})
        # name the processes on each GPU too
        by_pid = {p["pid"]: p for p in out}
        for entries in gpu_procs.values():
            for entry in entries:
                row = rows.get(entry["pid"])
                entry["name"] = row[0] if row else "?"
                entry["job"] = (jobs.get(entry["pid"]) or (None, None))[0]
                entry["project"] = (jobs.get(entry["pid"]) or (None, None))[1]
                entry["user"] = by_pid[entry["pid"]]["user"] if entry["pid"] in by_pid else None
        return {"count": len(rows), "running": sum(1 for r in rows.values() if r[5] == "R"), "top": out}
