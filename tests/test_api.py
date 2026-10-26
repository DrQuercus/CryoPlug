"""HTTP API tests (FastAPI TestClient) and a concurrency regression test."""
from __future__ import annotations

import threading
import time

from fastapi.testclient import TestClient

from conftest import run_until_done
from cryoplug.scheduler import Scheduler
from cryoplug.server.app import create_app


def test_api_basic_flow(manager, synthetic):
    app = create_app(manager.config, start_scheduler=False, manager=manager)
    with TestClient(app, base_url="http://localhost:39500") as client:
        info = client.get("/api/info").json()
        assert info["lanes"][0]["name"] == "local" and "Import" in info["categories"]
        types = {t["name"]: t for t in client.get("/api/jobtypes").json()}
        assert types["modelangelo_build"]["tool_status"] == "found"
        assert types["map_fsc"]["tool_status"] == "builtin"
        assert types["modelangelo_build"]["inputs"][0]["prefer"][0] == "map_sharp"
        assert client.get("/").status_code == 200 and "CryoPlug" in client.get("/").text

        p = client.post("/api/projects", json={"title": "API test"}).json()
        r = client.post(f"/api/projects/{p['uid']}/jobs", json={"type": "import_maps", "params": {
            "half_map_a": str(synthetic["half_a"]), "half_map_b": str(synthetic["half_b"]), "map_sharp": str(synthetic["map"]),
            "mask": str(synthetic["mask"])}, "queue": True})
        assert r.status_code == 200, r.text
        job = r.json()
        assert job["status"] == "queued"
        bad = client.post(f"/api/projects/{p['uid']}/jobs", json={"type": "map_fsc", "params": {"nope": 1}})
        assert bad.status_code == 400 and "Unknown parameter" in bad.json()["detail"]
        assert client.get(f"/api/projects/{p['uid']}/jobs/J99").status_code == 404

        run_until_done(manager, p["uid"], [job["uid"]])
        detail = client.get(f"/api/projects/{p['uid']}/jobs/{job['uid']}").json()
        assert detail["status"] == "completed"
        out = {o["name"]: o for o in detail["outputs"]}
        f = client.get(f"/api/projects/{p['uid']}/file", params={"path": out["map_sharp"]["path"]})
        assert f.status_code == 200 and len(f.content) > 1000
        prev = client.get(f"/api/projects/{p['uid']}/preview", params={"path": out["map_sharp"]["path"], "max_box": 64})
        assert prev.status_code == 200
        assert client.get(f"/api/projects/{p['uid']}/file", params={"path": "../../etc/passwd"}).status_code == 404
        log = client.get(f"/api/projects/{p['uid']}/jobs/{job['uid']}/log").json()
        assert "Output 'half_maps'" in log["text"]
        files = client.get(f"/api/projects/{p['uid']}/jobs/{job['uid']}/files").json()
        assert any(e["name"] == "job.log" for e in files)
        fs = client.get("/api/fs", params={"path": str(synthetic["cryosparc_job"])}).json()
        assert any(e["name"].endswith("half_A.mrc") for e in fs["entries"])
        assert client.get("/api/fs", params={"path": "/etc"}).status_code == 403
        wf = client.get("/api/workflows").json()
        assert {w["id"] for w in wf} >= {"denovo_modelangelo", "alphafold_docking", "map_enhancement", "validate_deposit"}
        q = client.get("/api/queue").json()
        assert q["lanes"][0]["max_jobs"] == 4


def test_concurrent_create_and_schedule(manager):
    """Jobs created/queued from API threads while the scheduler runs must never be 'not found'."""
    p = manager.create_project("Concurrency")
    sched = Scheduler(manager, interval=0.05)
    sched.start()
    uids: list[str] = []
    errors: list[Exception] = []

    def creator():
        try:
            for _ in range(15):
                j = manager.create_job(p["uid"], "custom_command", {"command": "true"})
                manager.queue_job(p["uid"], j["uid"])
                uids.append(j["uid"])
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=creator) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    sched.stop()
    assert not errors
    run_until_done(manager, p["uid"], uids, timeout=180)
    for u in uids:
        job = manager.job(p["uid"], u)
        assert job["status"] == "completed", (u, job["error"])
    time.sleep(0)


def test_workspaces_tags_and_chain_clone(manager, synthetic):
    """Workspaces group jobs (CryoSPARC), tags label them, and a selection clones as a linked chain."""
    app = create_app(manager.config, start_scheduler=False, manager=manager)
    with TestClient(app, base_url="http://localhost:39500") as client:
        p = client.post("/api/projects", json={"title": "Workspaces"}).json()
        base = f"/api/projects/{p['uid']}"
        imp = client.post(f"{base}/jobs", json={"type": "import_maps", "params": {
            "half_map_a": str(synthetic["half_a"]), "half_map_b": str(synthetic["half_b"]), "map_sharp": str(synthetic["map"])}}).json()
        fsc = client.post(f"{base}/jobs", json={"type": "map_fsc", "inputs": {"half_maps": {"job": imp["uid"], "output": "half_maps"}}}).json()
        other = client.post(f"{base}/jobs", json={"type": "custom_command", "params": {"command": "true"}}).json()

        assert client.post(f"{base}/workspaces", json={"title": "  "}).status_code == 400
        w1 = client.post(f"{base}/workspaces", json={"title": "Maps", "description": "the half maps", "jobs": [imp["uid"], fsc["uid"]]}).json()
        assert (w1["uid"], w1["num_jobs"]) == ("W1", 2)
        w2 = client.post(f"{base}/workspaces", json={"title": "Scratch"}).json()
        assert w2["uid"] == "W2" and w2["num_jobs"] == 0
        # a job created in a workspace; an unknown workspace is refused
        made = client.post(f"{base}/jobs", json={"type": "custom_command", "params": {"command": "true"}, "workspaces": ["W2"]}).json()
        assert made["workspaces"] == ["W2"]
        bad = client.post(f"{base}/jobs", json={"type": "custom_command", "params": {"command": "true"}, "workspaces": ["W9"]})
        assert bad.status_code == 400 and "No workspace W9" in bad.json()["detail"]
        # membership: add, remove, a job in two workspaces (kept in workspace order)
        r = client.post(f"{base}/workspaces/W2/jobs", json={"add": [fsc["uid"], other["uid"]], "remove": [made["uid"]]}).json()
        assert r["num_jobs"] == 2
        jobs = {j["uid"]: j for j in client.get(f"{base}/jobs").json()}
        assert jobs[fsc["uid"]]["workspaces"] == ["W1", "W2"] and jobs[made["uid"]]["workspaces"] == []
        # tags: trimmed, no repeats (letter case ignored), whatever the state of the job
        t = client.patch(f"{base}/jobs/{fsc['uid']}", json={"tags": [" best ", "Best", "to  check", ""]}).json()
        assert t["tags"] == ["best", "to check"]
        listed = {w["uid"]: w for w in client.get(f"{base}/workspaces").json()}
        assert listed["W1"]["num_jobs"] == 2 and listed["W2"]["num_jobs"] == 2 and listed["W1"]["description"] == "the half maps"
        assert client.patch(f"{base}/workspaces/W2", json={"title": "Tests"}).json()["title"] == "Tests"

        # clone the chain import → FSC: the copy of the FSC job reads the copy of the import
        copies = client.post(f"{base}/clone", json={"jobs": [fsc["uid"], imp["uid"]], "workspaces": ["W2"]}).json()
        assert [c["type"] for c in copies] == ["import_maps", "map_fsc"]
        assert copies[1]["inputs"]["half_maps"] == {"job": copies[0]["uid"], "output": "half_maps"}
        assert all(c["workspaces"] == ["W2"] and c["status"] == "building" for c in copies)
        assert copies[1]["tags"] == ["best", "to check"]
        # a single clone keeps the workspaces of the job, or goes where it is asked
        single = client.post(f"{base}/jobs/{fsc['uid']}/clone").json()
        assert single["workspaces"] == ["W1", "W2"] and single["inputs"]["half_maps"]["job"] == imp["uid"]
        assert client.post(f"{base}/jobs/{fsc['uid']}/clone", json={"workspaces": ["W1"]}).json()["workspaces"] == ["W1"]

        # deleting a workspace keeps its jobs
        assert client.delete(f"{base}/workspaces/W1").status_code == 200
        jobs = {j["uid"]: j for j in client.get(f"{base}/jobs").json()}
        assert jobs[fsc["uid"]]["workspaces"] == ["W2"] and jobs[imp["uid"]]["workspaces"] == []
        assert [w["uid"] for w in client.get(f"{base}/workspaces").json()] == ["W2"]
        assert client.get(f"{base}/workspaces").status_code == 200
        assert client.delete(f"{base}/workspaces/W1").status_code == 404
        # a new workspace never reuses a number still used by another
        assert client.post(f"{base}/workspaces", json={"title": "Again"}).json()["uid"] == "W3"
