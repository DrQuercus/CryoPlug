"""Compute resources (as in CryoSPARC): lanes edited in Settings, GPUs chosen per job, SLURM options, the live
monitor of the hardware and who may see what."""
from __future__ import annotations

import dataclasses
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import _script, run_until_done
from cryoplug.config import LaneConfig
from cryoplug.lanes import ClusterLane, generated_script
from cryoplug.manager import Manager, ManagerError
from cryoplug.monitor import Monitor, detect_gpus
from cryoplug.scheduler import Scheduler
from cryoplug.server.app import create_app
from test_users import PASSWORD, login, make_client, no_login_delay, setup_accounts  # noqa: F401 (fixture)

LOCAL = "http://localhost:39500"
SLURM = {"name": "slurm", "type": "cluster", "description": "GPU nodes", "max_jobs": 20, "partition": "gpu",
         "partitions": ["gpu", "gpu-long"], "account": "lab", "time_limit": "24:00:00", "mem": "64G",
         "setup": "module load cuda/12.2", "extra_sbatch": "--constraint=a100\n#SBATCH --exclude=node12"}


@pytest.fixture()
def fake_nvidia_smi(tmp_path) -> Path:
    """Two GPUs; the test process holds 2 GB on GPU 0."""
    path = tmp_path / "nvidia-smi"
    _script(path, f"""
case "$1" in
  --query-gpu=index,name,memory.total,uuid*)
    echo "0, NVIDIA RTX A6000, 49140, GPU-aaaa"; echo "1, NVIDIA RTX A6000, 49140, GPU-bbbb";;
  --query-gpu=*)
    echo "0, GPU-aaaa, NVIDIA RTX A6000, 87, 40, 20480, 49140, 71, 231.50, 300.00, 64"
    echo "1, GPU-bbbb, NVIDIA RTX A6000, 0, 0, 410, 49140, 35, [N/A], 300.00, [N/A]";;
  --query-compute-apps=*)
    echo "{os.getpid()}, GPU-aaaa, 2048";;
esac
""")
    return path


@pytest.fixture()
def fake_slurm(tmp_path, monkeypatch) -> Path:
    """sinfo / squeue / sbatch stand-ins on PATH."""
    d = tmp_path / "slurm"
    d.mkdir()
    _script(d / "sinfo", """
if [ "$1" = "--version" ]; then echo "slurm 23.11.4"; exit 0; fi
case "$*" in
  *"%P|"*) echo 'gpu*|up|2-00:00:00|6|mixed|152/40/0/192|gpu:a100:4|512000'
           echo 'gpu*|up|2-00:00:00|2|idle|0/64/0/64|gpu:a100:4|512000'
           echo 'gpu-long|up|7-00:00:00|4|allocated|128/0/0/128|gpu:v100:4|384000';;
  *) printf 'gpu*\\ngpu*\\ngpu-long\\ncpu\\n';;
esac
""")
    _script(d / "squeue", "printf 'RUNNING|alice|gpu\\nRUNNING|alice|gpu\\nPENDING|bob|gpu-long\\n'\n")
    _script(d / "sbatch", "echo 'Submitted batch job 777'\n")
    monkeypatch.setenv("PATH", f"{d}{os.pathsep}{os.environ['PATH']}")
    return d


def gpu_job(manager, puid, *, requested=None, lane=None, command="printenv CUDA_VISIBLE_DEVICES > gpus.txt"):
    return manager.create_job(puid, "custom_command", {"command": command, "use_gpu": True}, lane=lane, requested=requested)


# ------------------------------------------------------------------ lane settings
def test_lane_settings_are_checked():
    lane = LaneConfig.from_dict({"name": "gpu-box", "gpus": "0, 2 1", "max_jobs": "3"})
    assert lane.gpus == [0, 1, 2] and lane.max_jobs == 3 and lane.type == "local"
    cluster = LaneConfig.from_dict({**SLURM, "partitions": "gpu, gpu-long"})
    assert cluster.partitions == ["gpu", "gpu-long"] and cluster.sbatch_lines() == ["#SBATCH --constraint=a100", "#SBATCH --exclude=node12"]
    for bad, message in (({"name": "two words"}, "Lane name"), ({"type": "pbs"}, "type must be"), ({"gpus": [64]}, "from 0 to 63"),
                         ({"gpus": "a"}, "whole numbers"), ({"max_jobs": 0}, "between 1"), ({"partition": "gpu;rm"}, "invalid partition"),
                         ({"time_limit": "2 days"}, "time limit"), ({"mem": "lots"}, "memory"), ({"colour": "red"}, "unknown setting")):
        with pytest.raises(ValueError, match=message):
            LaneConfig.from_dict({"name": "x", **bad})


def test_slurm_script_written_from_the_settings(tmp_path):
    cfg = LaneConfig.from_dict(SLURM)
    job = {"project_uid": "P1", "uid": "J7", "type": "modelangelo_build", "title": "Build"}
    script = ClusterLane(cfg).render_script(job, tmp_path / "J7", {"num_gpus": 2, "num_cpus": 8})
    for line in ("#SBATCH --job-name=cryoplug_P1_J7", "#SBATCH --cpus-per-task=8", "#SBATCH --partition=gpu", "#SBATCH --account=lab",
                 "#SBATCH --time=24:00:00", "#SBATCH --mem=64G", "#SBATCH --gres=gpu:2", "#SBATCH --constraint=a100",
                 "#SBATCH --exclude=node12", "module load cuda/12.2"):
        assert line in script, line
    assert script.rstrip().endswith(f"-m cryoplug worker {tmp_path / 'J7'}")
    assert "--qos" not in script
    # the choices of the job win over the lane defaults; no GPU, no --gres
    script = ClusterLane(cfg).render_script(job, tmp_path / "J7", {"num_gpus": 0, "num_cpus": 2, "partition": "gpu-long",
                                                                     "time": "2-00:00:00", "mem": "128G"})
    assert "--partition=gpu-long" in script and "--time=2-00:00:00" in script and "--mem=128G" in script and "--gres" not in script
    # an own script (other schedulers) is filled in, leaving shell variables alone
    own = LaneConfig.from_dict({**SLURM, "script_template": "#PBS -l ngpus={num_gpus}\necho ${PBS_JOBID} {partition}\n{worker_cmd}\n"})
    text = ClusterLane(own).render_script(job, tmp_path / "J7", {"num_gpus": 1, "num_cpus": 4})
    assert "#PBS -l ngpus=1" in text and "${PBS_JOBID} gpu" in text and "worker" in text
    assert generated_script(LaneConfig.from_dict({"name": "c", "type": "cluster", "time_limit": ""}), {
        "project_uid": "P1", "job_uid": "J1", "job_dir": "/x", "num_gpus": 0, "num_cpus": 1, "partition": "", "account": "",
        "qos": "", "time": "", "mem": "", "gres": "gpu", "setup": "", "worker_cmd": "w"}).count("#SBATCH") == 4


def test_lanes_edited_in_settings(manager, tmp_path):
    p = manager.create_project("Lanes")
    assert manager.lanes_source == "config"
    local = manager.lanes["local"]
    manager.save_lanes([{**manager.config.lanes[0].to_dict(), "max_jobs": 1, "description": "Workstation"}, SLURM])
    assert manager.lanes_source == "settings" and [lane.name for lane in manager.config.lanes] == ["local", "slurm"]
    assert manager.lanes["local"] is local and local.cfg.max_jobs == 1  # kept: it knows its running workers
    assert isinstance(manager.lanes["slurm"], ClusterLane)
    # stored: a restart keeps them, over the lanes of the configuration file
    again = Manager(dataclasses.replace(manager.config, lanes=list(manager.file_lanes)))
    assert [lane.name for lane in again.config.lanes] == ["local", "slurm"] and again.config.lane("local").max_jobs == 1
    for bad, message in (([], "at least one"), ([SLURM, SLURM], "Two lanes"), ([{"name": "x", "type": "nope"}], "type must be")):
        with pytest.raises(ManagerError, match=message):
            manager.save_lanes(bad)
    # a lane with queued jobs cannot disappear; a running one cannot change type
    job = gpu_job(manager, p["uid"], lane="slurm")
    manager.queue_job(p["uid"], job["uid"])
    with pytest.raises(ManagerError, match="still has active jobs"):
        manager.save_lanes([manager.config.lanes[0].to_dict()])
    manager.db.update_job(p["uid"], job["uid"], status="running")
    with pytest.raises(ManagerError, match="before changing its type"):
        manager.save_lanes([manager.config.lanes[0].to_dict(), {**SLURM, "type": "local"}])
    manager.db.update_job(p["uid"], job["uid"], status="killed")
    # back to config.toml; a job prepared for a lane that is gone runs on the default lane
    other = gpu_job(manager, p["uid"], lane="slurm")
    manager.save_lanes(None)
    assert manager.lanes_source == "config" and list(manager.lanes) == ["local"]
    assert manager.queue_job(p["uid"], other["uid"])["lane"] == "local"


def test_compute_choices_of_a_job(manager):
    p = manager.create_project("Choices")
    puid = p["uid"]
    manager.save_lanes([manager.config.lanes[0].to_dict(), SLURM])
    job = gpu_job(manager, puid, requested={"gpu_ids": [1], "num_cpus": "6"})
    assert job["requested"] == {"gpu_ids": [1], "num_cpus": 6}
    assert job["resources"] == {"num_gpus": 1, "num_cpus": 6, "gpu_ids": [1]}
    for requested, message in (({"gpu_ids": [0, 1]}, "uses 1 GPU: choose 1"), ({"gpu_ids": [5]}, "manages GPU 0, 1 only"),
                               ({"num_cpus": 0}, "between 1 and 1024"), ({"num_cpus": "many"}, "whole number"),
                               ({"partition": "gpu"}, "only applies to cluster lanes"), ({"cores": 2}, "Unknown resource")):
        with pytest.raises(ManagerError, match=message):
            gpu_job(manager, puid, requested=requested)
    with pytest.raises(ManagerError, match="does not use a GPU"):
        manager.create_job(puid, "map_fsc", {}, requested={"gpu_ids": [0]})
    for requested, message in (({"partition": "debug"}, "not offered"), ({"time": "1 day"}, "Time limit"), ({"mem": "64 GB"}, "Memory")):
        with pytest.raises(ManagerError, match=message):
            gpu_job(manager, puid, lane="slurm", requested=requested)
    on_cluster = gpu_job(manager, puid, lane="slurm", requested={"partition": "gpu-long", "time": "12:00:00", "mem": "128G"})
    assert on_cluster["resources"]["partition"] == "gpu-long"
    with pytest.raises(ManagerError, match="does not hand out specific GPUs"):
        gpu_job(manager, puid, lane="slurm", requested={"gpu_ids": [0]})
    # another lane afterwards: the choices that no longer apply are dropped, the others kept
    moved = manager.update_job(puid, job["uid"], lane="slurm")
    assert moved["requested"] == {"num_cpus": 6} and moved["resources"] == {"num_gpus": 1, "num_cpus": 6}
    cleared = manager.update_job(puid, job["uid"], requested={})
    assert cleared["requested"] == {} and cleared["resources"] == {"num_gpus": 1, "num_cpus": 1}
    # GPUs and CPUs set by parameters (cryoDRGN --multigpu, Phenix nproc)
    drgn = manager.create_job(puid, "cryodrgn_train", {"gpus": 2}, requested={"gpu_ids": [0, 1]})
    assert drgn["resources"]["num_gpus"] == 2 and drgn["requested"]["gpu_ids"] == [0, 1]
    rsr = manager.create_job(puid, "phenix_real_space_refine", {"nproc": 12})
    assert rsr["resources"]["num_cpus"] == 12


def test_scheduler_gives_the_chosen_or_least_loaded_gpu(manager):
    p = manager.create_project("GPUs")
    puid = p["uid"]
    chosen = gpu_job(manager, puid, requested={"gpu_ids": [1], "num_cpus": 3},
                     command="printenv CUDA_VISIBLE_DEVICES > gpus.txt; printenv OMP_NUM_THREADS > threads.txt")
    manager.queue_job(puid, chosen["uid"])
    run_until_done(manager, puid, [chosen["uid"]])
    jdir = manager.job_dir(puid, chosen["uid"])
    assert manager.job(puid, chosen["uid"])["status"] == "completed"
    assert (jdir / "gpus.txt").read_text().strip() == "1" and (jdir / "threads.txt").read_text().strip() == "3"

    # a chosen GPU taken by another job: the job waits and says why
    first, second = gpu_job(manager, puid, requested={"gpu_ids": [1]}), gpu_job(manager, puid, requested={"gpu_ids": [1]})
    for j in (first, second):
        manager.queue_job(puid, j["uid"])
    manager.db.update_job(puid, first["uid"], status="running", gpus=[1], started_at=time.time())
    Scheduler(manager).launch_ready()
    waiting = manager.job(puid, second["uid"])
    assert waiting["status"] == "queued" and waiting["message"] == f"Waiting for GPU 1 (used by {puid}/{first['uid']})"
    manager.kill_job(puid, second["uid"])
    manager.db.update_job(puid, first["uid"], status="killed", gpus=[])

    # automatic: the free GPU with the least memory in use (another program fills GPU 0)
    class FakeMonitor:
        def gpu_memory(self, max_age=30.0):
            return {0: 30000.0, 1: 410.0}
    manager.monitor = FakeMonitor()
    auto = gpu_job(manager, puid)
    manager.queue_job(puid, auto["uid"])
    run_until_done(manager, puid, [auto["uid"]])
    assert manager.job(puid, auto["uid"])["status"] == "completed"
    assert (manager.job_dir(puid, auto["uid"]) / "gpus.txt").read_text().strip() == "1"


def test_cpu_threads_only_when_chosen(manager):
    p = manager.create_project("Threads")
    job = manager.create_job(p["uid"], "custom_command", {"command": "printenv OMP_NUM_THREADS > threads.txt || echo none > threads.txt"})
    manager.queue_job(p["uid"], job["uid"])
    run_until_done(manager, p["uid"], [job["uid"]])
    expected = os.environ.get("OMP_NUM_THREADS", "none")
    assert (manager.job_dir(p["uid"], job["uid"]) / "threads.txt").read_text().strip() == expected


# ------------------------------------------------------------------ live monitor
def test_monitor_reads_the_hardware(manager, fake_nvidia_smi, tmp_path):
    manager.config.nvidia_smi = str(fake_nvidia_smi)
    p = manager.create_project("Monitor")
    job_dir = manager.job_dir(p["uid"], "J5")
    job_dir.mkdir(parents=True)
    worker = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", "-m", "cryoplug", "worker", str(job_dir)])
    try:
        time.sleep(0.2)
        mon = Monitor(manager.config, manager)
        snap = mon.snapshot(history=True)
        assert snap["available"] and snap["cpu"]["total"] is not None and snap["cpu"]["count"] == len(snap["cpu"]["cores"])
        assert snap["memory"]["total"] > snap["memory"]["used"] > 0 and 0 <= snap["memory"]["percent"] <= 100
        g0, g1 = snap["gpus"]
        assert (g0["name"], g0["utilization"], g0["memory_used"], g0["temperature"], g0["power"]) == ("NVIDIA RTX A6000", 87.0, 20480.0, 71.0, 231.5)
        assert g0["memory_percent"] == round(100 * 20480 / 49140, 1) and g1["power"] is None and g1["fan"] is None
        assert g0["processes"] == [{"pid": os.getpid(), "gpu": 0, "memory": 2048.0, "name": g0["processes"][0]["name"],
                                    "job": None, "project": None, "user": g0["processes"][0]["user"]}]
        procs = {pr["pid"]: pr for pr in snap["processes"]["top"]}
        assert procs[os.getpid()]["gpus"] == [0] and procs[os.getpid()]["gpu_memory"] == 2048.0
        assert procs[worker.pid]["job"] == f"{p['uid']}/J5" and procs[worker.pid]["project"] == p["uid"]
        roles = {role for fs in snap["filesystems"] for role in fs.get("roles", [])}
        assert roles == {"projects", "CryoPlug data"}
        assert snap["history"] and set(snap["history"][-1]) == {"t", "cpu", "mem", "gpus", "rx", "tx", "read", "write"}
        assert mon.gpu_memory() == {0: 20480.0, 1: 410.0}
        assert [g["index"] for g in detect_gpus(str(fake_nvidia_smi))] == [0, 1]
    finally:
        worker.kill()
        worker.wait()


def test_monitor_without_gpus_or_with_a_broken_nvidia_smi(manager, tmp_path):
    manager.config.nvidia_smi = ""
    mon = Monitor(manager.config, manager)
    mon.nvidia_smi = None
    snap = mon.snapshot()
    assert snap["gpus"] == [] and snap["gpu_error"] is None and mon.gpu_memory() == {}
    broken = tmp_path / "nvidia-smi"
    _script(broken, "echo 'NVIDIA-SMI has failed because it could not communicate with the NVIDIA driver' >&2; exit 9\n")
    mon.nvidia_smi = str(broken)
    gpus, error, _ = mon._gpus()
    assert gpus == [] and "could not communicate" in error


# ------------------------------------------------------------------ SLURM overview
def test_cluster_overview_and_test(fake_slurm):
    lane = ClusterLane(LaneConfig.from_dict({**SLURM, "partitions": ["gpu", "gpu-long", "debug"]}))
    info = lane.overview()
    parts = {p["name"]: p for p in info["partitions"]}
    assert info["available"] and set(parts) == {"gpu", "gpu-long"}
    assert parts["gpu"]["default"] and parts["gpu"]["nodes"] == {"mixed": 6, "idle": 2}
    assert parts["gpu"]["cpus"] == {"allocated": 152, "idle": 104, "other": 0, "total": 256} and parts["gpu"]["gres"] == ["gpu:a100:4"]
    assert info["jobs"] == {"running": 2, "pending": 1, "other": 0} and tuple(info["users"][0]) == ("alice", 2)
    result = lane.test()
    assert not result["ok"] and result["partitions"] == ["gpu", "gpu-long", "cpu"] and "Unknown partition(s): debug" in result["output"]
    assert ClusterLane(LaneConfig.from_dict(SLURM)).test()["ok"]
    other = ClusterLane(LaneConfig.from_dict({**SLURM, "submit_cmd": "qsub {script}"}))
    assert other.overview()["available"] is False


# ------------------------------------------------------------------ API
def test_compute_api(manager, fake_nvidia_smi, fake_slurm):
    manager.config.nvidia_smi = str(fake_nvidia_smi)
    app = create_app(manager.config, start_scheduler=False, manager=manager)
    with TestClient(app, base_url=LOCAL) as client:
        data = client.get("/api/lanes").json()
        assert data["source"] == "config" and data["file_lanes"] == ["local"] and data["lanes"][0]["gpus"] == [0, 1]
        r = client.put("/api/lanes", json={"lanes": [data["lanes"][0], SLURM]})
        assert r.status_code == 200 and r.json()["source"] == "settings"
        info = client.get("/api/info").json()
        assert [(lane["name"], lane["partitions"], lane["time_limit"]) for lane in info["lanes"]] == [
            ("local", [], "48:00:00"), ("slurm", ["gpu", "gpu-long"], "24:00:00")]
        assert client.put("/api/lanes", json={"lanes": [{"name": "bad name"}]}).status_code == 400
        hw = client.get("/api/hardware").json()
        assert [g["name"] for g in hw["gpus"]] == ["NVIDIA RTX A6000"] * 2 and hw["cpus"] == os.cpu_count()
        local = client.post("/api/lanes/test", json={"lane": {"name": "x", "gpus": [0, 3]}}).json()
        assert not local["ok"] and "GPU 0: NVIDIA RTX A6000 (48 GB)" in local["output"] and "Not found on this machine: GPU 3" in local["output"]
        assert client.post("/api/lanes/test", json={"lane": SLURM}).json()["ok"]
        script = client.post("/api/lanes/preview", json={"lane": SLURM}).json()["script"]
        assert "#SBATCH --gres=gpu:1" in script and "#SBATCH --partition=gpu" in script
        assert client.post("/api/lanes/preview", json={"lane": {"name": "x"}}).status_code == 400
        cluster = client.get("/api/cluster").json()
        assert cluster[0]["lane"] == "slurm" and cluster[0]["users"]

        p = client.post("/api/projects", json={"title": "API compute"}).json()
        r = client.post(f"/api/projects/{p['uid']}/jobs", json={"type": "custom_command", "params": {"command": "true", "use_gpu": True},
                                                               "resources": {"gpu_ids": [0], "num_cpus": 2}})
        assert r.status_code == 200 and r.json()["requested"] == {"gpu_ids": [0], "num_cpus": 2}
        juid = r.json()["uid"]
        r = client.patch(f"/api/projects/{p['uid']}/jobs/{juid}", json={"lane": "slurm", "resources": {"partition": "gpu-long"}})
        assert r.status_code == 200 and r.json()["requested"] == {"partition": "gpu-long"}
        bad = client.post(f"/api/projects/{p['uid']}/jobs", json={"type": "custom_command", "params": {"command": "true", "use_gpu": True},
                                                                 "resources": {"gpu_ids": [0, 1]}})
        assert bad.status_code == 400 and "choose 1" in bad.json()["detail"]
        snap = client.get("/api/monitor", params={"history": True}).json()
        assert len(snap["gpus"]) == 2 and "history" in snap
        assert client.put("/api/lanes", json={"reset": True}).json()["source"] == "config"


def test_compute_permissions(manager, synthetic, tmp_path, fake_nvidia_smi, fake_slurm):
    manager.config.nvidia_smi = str(fake_nvidia_smi)
    manager.save_lanes([manager.config.lanes[0].to_dict(), SLURM])
    admin, _ = setup_accounts(manager, tmp_path, synthetic)
    try:
        p = admin.post("/api/projects", json={"title": "Admin's project"}).json()
        job_dir = manager.job_dir(p["uid"], "J3")
        job_dir.mkdir(parents=True)
        worker = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", "-m", "cryoplug", "worker", str(job_dir)])
        job = manager.create_job(p["uid"], "custom_command", {"command": "true", "use_gpu": True})
        manager.db.update_job(p["uid"], job["uid"], status="running", lane="local", gpus=[1], started_at=time.time())
        try:
            time.sleep(0.2)
            with make_client(manager) as bob:
                login(bob, "bob")
                for method, url, body in (("get", "/api/lanes", None), ("put", "/api/lanes", {"reset": True}), ("get", "/api/hardware", None),
                                          ("post", "/api/lanes/test", {"lane": SLURM}), ("post", "/api/lanes/preview", {"lane": SLURM})):
                    r = getattr(bob, method)(url, **({"json": body} if body is not None else {}))
                    assert r.status_code == 403, (url, r.status_code)
                # live usage: everyone sees the machine, but not what other people run
                theirs = {pr["pid"]: pr for pr in bob.get("/api/monitor").json()["processes"]["top"]}
                assert theirs[worker.pid]["job"] is None and theirs[worker.pid]["cmd"] == theirs[worker.pid]["name"]
                mine = {pr["pid"]: pr for pr in admin.get("/api/monitor").json()["processes"]["top"]}
                assert mine[worker.pid]["job"] == f"{p['uid']}/J3" and "worker" in mine[worker.pid]["cmd"]
                lanes = {lane["name"]: lane for lane in bob.get("/api/queue").json()["lanes"]}
                assert lanes["local"]["gpu_jobs"]["1"]["hidden"] and lanes["local"]["gpu_jobs"]["1"]["uid"] == ""
                assert admin.get("/api/queue").json()["lanes"][0]["gpu_jobs"]["1"]["uid"] == job["uid"]
                assert "users" not in bob.get("/api/cluster").json()[0] and admin.get("/api/cluster").json()[0]["users"]
                # bob may still choose GPUs for his own jobs
                mine_p = bob.post("/api/projects", json={"title": "Bob"}).json()
                r = bob.post(f"/api/projects/{mine_p['uid']}/jobs", json={"type": "modelangelo_build", "resources": {"gpu_ids": [1]}})
                assert r.status_code == 200 and r.json()["requested"]["gpu_ids"] == [1]
        finally:
            worker.kill()
            worker.wait()
    finally:
        admin.__exit__(None, None, None)
