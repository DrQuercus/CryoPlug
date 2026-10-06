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
    with TestClient(app) as client:
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
