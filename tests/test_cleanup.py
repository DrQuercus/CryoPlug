"""Project storage: the final-result flag, the cleanup plan (what each option clears or deletes and what it saves),
the cleanup itself (children before parents, confirmed by the project id), the measuring of the folders, and the API.
The jobs are made by hand (their files written, their state set) so that every category is there without running
a whole RELION pipeline."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from cryoplug.cleanup import PREPROCESSING_KINDS, clean_options, default_options
from cryoplug.manager import ManagerError
from cryoplug.server.app import create_app


def _fake_job(manager, puid, kind, inputs=None, status="completed", files=None, outputs=None, title=None):
    """A job of ``kind`` as if it had run: its folder holds ``files`` ({name: bytes}), its outputs ({name: (type, file)})
    name some of them."""
    job = manager.create_job(puid, kind, {}, inputs or {}, title)
    jdir = manager.job_dir(puid, job["uid"])
    jdir.mkdir(parents=True, exist_ok=True)
    for name, size in (files or {}).items():
        (jdir / name).write_bytes(b"x" * size)
    outs = []
    for name, (otype, fname) in (outputs or {}).items():
        outs.append({"name": name, "type": otype, "label": name, "path": f"{job['uid']}/{fname}", "files": [f"{job['uid']}/{fname}"]})
    manager.db.update_job(puid, job["uid"], status=status, outputs=outs, disk_bytes=sum((files or {}).values()))
    return manager.job(puid, job["uid"])


@pytest.fixture()
def storage(manager):
    """A project with every category: preprocessing (motion correction → CTF → extraction), a classification with
    iterations, a refinement marked final, a failed and a killed job, a running one with its parent."""
    p = manager.create_project("Storage")
    puid = p["uid"]
    ref = lambda j, out="out": {"job": j["uid"], "output": out}  # noqa: E731
    movies = _fake_job(manager, puid, "relion_import", files={"movies.star": 100}, outputs={"out": ("movies", "movies.star")})
    mc = _fake_job(manager, puid, "relion_motioncorr", {"movies": ref(movies)}, files={"mic_001.mrc": 4000, "mic_002.mrc": 4000},
                   outputs={"out": ("micrographs", "mic_001.mrc")})
    ctf = _fake_job(manager, puid, "relion_ctffind", {"micrographs": ref(mc)}, files={"ctf.star": 300},
                    outputs={"out": ("micrographs", "ctf.star")})
    ext = _fake_job(manager, puid, "relion_extract", {"micrographs": ref(ctf)}, files={"particles.mrcs": 9000, "particles.star": 200},
                    outputs={"out": ("particles", "particles.star")})
    c2d = _fake_job(manager, puid, "relion_class2d", {"particles": ref(ext)},
                    files={"run_it001_data.star": 500, "run_it001_classes.mrcs": 2000, "run_it002_data.star": 500,
                           "run_it002_classes.mrcs": 2000, "run_it003_data.star": 500, "run_it003_classes.mrcs": 2000},
                    outputs={"out": ("particles", "run_it003_data.star")})
    refine = _fake_job(manager, puid, "relion_refine3d", {"particles": ref(c2d)},
                       files={"run_it001_half1.mrc": 3000, "run_it002_half1.mrc": 3000, "run_class001.mrc": 3000},
                       outputs={"out": ("half_maps", "run_class001.mrc")})
    post = _fake_job(manager, puid, "relion_postprocess", {"half_maps": ref(refine)}, files={"postprocess.mrc": 3000},
                     outputs={"out": ("map", "postprocess.mrc")}, title="Final map")
    failed = _fake_job(manager, puid, "relion_class3d", {"particles": ref(ext)}, status="failed", files={"run_it001_data.star": 700})
    killed = _fake_job(manager, puid, "relion_class2d", {"particles": ref(ext)}, status="killed", files={"run_it001_data.star": 600})
    side = _fake_job(manager, puid, "relion_class2d", {"particles": ref(ext)}, files={"run_it001_data.star": 800, "run_it002_data.star": 800},
                     outputs={"out": ("particles", "run_it002_data.star")}, title="Side branch")
    running = _fake_job(manager, puid, "relion_refine3d", {"particles": ref(side)}, status="running", files={"run_it001_half1.mrc": 100})
    manager.update_job(puid, post["uid"], final=True)
    return {"puid": puid, "movies": movies, "mc": mc, "ctf": ctf, "ext": ext, "c2d": c2d, "refine": refine, "post": post,
            "failed": failed, "killed": killed, "side": side, "running": running}


def _by_uid(plan):
    return {e["uid"]: e for e in plan["entries"]}


def test_options_are_cleaned():
    assert clean_options(None) == default_options()
    opts = clean_options({"preprocessing": False, "intermediate": False, "nonfinal": True, "killed": "", "failed": "delete", "exclude": ["J2"]})
    assert not any(opts["preprocessing"].values()) and opts["nonfinal"] == "clear" and opts["killed"] == "" and opts["exclude"] == ["J2"]
    assert clean_options({"preprocessing": {"ctf": True}})["preprocessing"] == {k: k == "ctf" for k in PREPROCESSING_KINDS}
    with pytest.raises(ValueError, match="nonfinal"):
        clean_options({"nonfinal": "burn"})


def test_final_flag(manager, storage):
    puid = storage["puid"]
    post, refine = storage["post"], storage["refine"]
    assert manager.job(puid, post["uid"])["final"] is True and manager.job(puid, refine["uid"])["final"] is False
    manager.update_job(puid, post["uid"], final=False)
    assert manager.job(puid, post["uid"])["final"] is False
    manager.update_job(puid, post["uid"], final=1)
    assert manager.job(puid, post["uid"])["final"] is True


def test_plan_with_the_default_options(manager, storage):
    puid = storage["puid"]
    plan = manager.cleanup_plan(puid)
    by = _by_uid(plan)
    # preprocessing cleared, killed and failed deleted, the iterations of the classification trimmed
    assert by[storage["mc"]["uid"]]["action"] == "clear" and by[storage["mc"]["uid"]]["category"] == "preprocessing"
    assert by[storage["mc"]["uid"]]["kind"] == "motioncorr" and by[storage["mc"]["uid"]]["savings"] == 8000
    assert by[storage["ctf"]["uid"]]["kind"] == "ctf" and by[storage["ext"]["uid"]]["kind"] == "extract"
    assert by[storage["failed"]["uid"]]["action"] == "delete" and by[storage["killed"]["uid"]]["action"] == "delete"
    c2d = by[storage["c2d"]["uid"]]
    assert c2d["action"] == "intermediate" and c2d["savings"] == 5000 and c2d["last_iteration"] == 3  # iterations 1 and 2
    refine = by[storage["refine"]["uid"]]
    assert refine["action"] == "intermediate" and refine["savings"] == 3000 and refine["kept"] is True  # it001 of 2
    # the final result, the import and the running job's parent are left alone; the running job is protected
    assert storage["post"]["uid"] not in by and storage["movies"]["uid"] not in by and storage["running"]["uid"] not in by
    assert {p["uid"]: p["reason"] for p in plan["protected"]} == {storage["running"]["uid"]: "running",
                                                                    storage["side"]["uid"]: "used by a running job"}
    assert plan["finals"] == [storage["post"]["uid"]] and plan["kept"] == 7  # post and everything upstream
    s = plan["summary"]
    assert s["clear"]["jobs"] == 3 and s["clear"]["bytes"] == 8000 + 300 + 9200
    assert s["delete"]["jobs"] == 2 and s["delete"]["bytes"] == 1300
    assert s["intermediate"]["jobs"] == 2 and s["intermediate"]["bytes"] == 8000
    assert s["total"]["jobs"] == 7 and s["total"]["bytes"] == s["clear"]["bytes"] + s["delete"]["bytes"] + s["intermediate"]["bytes"]
    assert plan["available"]["killed"] == 1 and plan["available"]["failed"] == 1 and plan["available"]["preprocessing"]["ctf"] == 1
    assert plan["intermediate_bytes"] == 8800 and plan["disk_bytes"] > 0 and plan["unmeasured"] == 0  # the side branch counted too
    assert plan["measuring"] == {"measuring": False} and plan["last_cleanup"] is None


def test_plan_options(manager, storage):
    puid = storage["puid"]
    # non-final jobs: those neither final nor upstream of the final map (the side branch is protected by its running child)
    plan = manager.cleanup_plan(puid, {"preprocessing": False, "intermediate": False, "killed": "", "failed": "", "nonfinal": "delete"})
    by = _by_uid(plan)
    assert set(by) == {storage["failed"]["uid"], storage["killed"]["uid"]} and all(e["action"] == "delete" for e in by.values())
    assert all(e["category"] == "nonfinal" for e in by.values())
    # only CTF among the preprocessing kinds, one job excluded
    plan = manager.cleanup_plan(puid, {"preprocessing": {"ctf": True}, "intermediate": True, "killed": "", "failed": "",
                                       "exclude": [storage["c2d"]["uid"]]})
    by = _by_uid(plan)
    assert by[storage["ctf"]["uid"]]["category"] == "preprocessing" and storage["mc"]["uid"] not in by
    assert storage["c2d"]["uid"] not in by and by[storage["refine"]["uid"]]["action"] == "intermediate"
    # a failed job that feeds nothing can be deleted; one cleared instead when the option says so
    plan = manager.cleanup_plan(puid, {"failed": "clear", "killed": "clear"})
    by = _by_uid(plan)
    assert by[storage["failed"]["uid"]]["action"] == "clear" and by[storage["killed"]["uid"]]["action"] == "clear"
    with pytest.raises(ManagerError, match="nonfinal"):
        manager.cleanup_plan(puid, {"nonfinal": "burn"})


def test_delete_needs_every_child_gone(manager, storage):
    """Without a final result, 'delete non-final jobs' would delete the whole chain; a job feeding a job that stays is
    cleared instead of deleted."""
    puid = storage["puid"]
    manager.update_job(puid, storage["post"]["uid"], final=False)
    plan = manager.cleanup_plan(puid, {"preprocessing": False, "intermediate": False, "killed": "", "failed": "", "nonfinal": "delete"})
    by = _by_uid(plan)
    # the extraction feeds the side branch (protected, not deleted): it is downgraded to clear, as are its parents
    assert by[storage["ext"]["uid"]]["action"] == "clear" and by[storage["ext"]["uid"]].get("downgraded") is True
    assert by[storage["mc"]["uid"]]["action"] == "clear" and by[storage["movies"]["uid"]]["action"] == "clear"
    # the chain classification → refinement → post-processing feeds nothing that stays: deleted
    assert by[storage["c2d"]["uid"]]["action"] == "delete" and by[storage["post"]["uid"]]["action"] == "delete"


def test_cleanup_runs_the_plan(manager, storage):
    puid = storage["puid"]
    pdir = manager.project_dir(puid)
    with pytest.raises(ManagerError, match="Type the project id"):
        manager.cleanup_project(puid, None, "P99")
    before = {j["uid"] for j in manager.db.list_jobs(puid)}
    res = manager.cleanup_project(puid, None, puid)
    assert sorted(res["deleted"]) == sorted([storage["failed"]["uid"], storage["killed"]["uid"]])
    assert sorted(res["cleared"]) == sorted([storage["mc"]["uid"], storage["ctf"]["uid"], storage["ext"]["uid"]])
    assert sorted(res["trimmed"]) == sorted([storage["c2d"]["uid"], storage["refine"]["uid"]])
    assert res["errors"] == [] and res["freed_bytes"] == res["planned"]["total"]["bytes"] == 8000 + 9500 + 1300 + 8000
    after = {j["uid"] for j in manager.db.list_jobs(puid)}
    assert before - after == {storage["failed"]["uid"], storage["killed"]["uid"]}
    assert not (pdir / storage["failed"]["uid"]).exists()
    mc = manager.job(puid, storage["mc"]["uid"])
    assert mc["status"] == "building" and mc["outputs"] == [] and not any((pdir / mc["uid"]).glob("*.mrc"))
    c2d_dir = pdir / storage["c2d"]["uid"]
    assert sorted(f.name for f in c2d_dir.glob("run_it*")) == ["run_it003_classes.mrcs", "run_it003_data.star"]
    assert manager.job(puid, storage["c2d"]["uid"])["status"] == "completed"
    assert (pdir / storage["post"]["uid"] / "postprocess.mrc").exists()  # the final result untouched
    record = manager.db.get_meta(f"cleanup:{puid}")
    assert record["deleted"] == 2 and record["cleared"] == 3 and record["trimmed"] == 2 and record["freed_bytes"] == res["freed_bytes"]
    plan = manager.cleanup_plan(puid)
    assert plan["summary"]["total"]["jobs"] == 0 and plan["last_cleanup"]["freed_bytes"] == res["freed_bytes"]


def test_measure_project(manager, storage):
    puid = storage["puid"]
    manager.db.update_job(puid, storage["mc"]["uid"], disk_bytes=None)
    assert manager.cleanup_plan(puid)["unmeasured"] == 1
    prog = manager.measure_project(puid, wait=True)
    assert prog["measuring"] is False and prog["total"] == prog["done"] >= 9
    assert manager.job(puid, storage["mc"]["uid"])["disk_bytes"] >= 8000  # the files written, plus the job's own records
    plan = manager.cleanup_plan(puid)
    assert plan["unmeasured"] == 0 and plan["measured_at"] > 0 and plan["measuring"]["measuring"] is False


def test_storage_api(manager, storage):
    puid = storage["puid"]
    app = create_app(manager.config, start_scheduler=False, manager=manager)
    with TestClient(app, base_url="http://localhost:39500") as client:
        job = client.patch(f"/api/projects/{puid}/jobs/{storage['refine']['uid']}", json={"final": True}).json()
        assert job["final"] is True
        plan = client.post(f"/api/projects/{puid}/cleanup/estimate", json={"options": {"nonfinal": "clear"}}).json()
        assert plan["finals"] == [storage["refine"]["uid"], storage["post"]["uid"]] and plan["summary"]["total"]["jobs"] > 0
        bad = client.post(f"/api/projects/{puid}/cleanup", json={"options": {}, "confirm": "nope"})
        assert bad.status_code == 400 and "project id" in bad.json()["detail"]
        prog = client.post(f"/api/projects/{puid}/measure", json={"wait": True}).json()
        assert prog["measuring"] is False
        res = client.post(f"/api/projects/{puid}/cleanup", json={"options": {"preprocessing": False, "intermediate": False,
                                                                             "killed": "delete", "failed": "delete"}, "confirm": puid}).json()
        assert sorted(res["deleted"]) == sorted([storage["failed"]["uid"], storage["killed"]["uid"]]) and res["cleared"] == []
        assert client.get(f"/api/projects/{puid}/jobs/{storage['failed']['uid']}").status_code == 404
        listed = json.loads(client.get("/api/projects").text)
        assert any(p["uid"] == puid and p["disk_bytes"] > 0 for p in listed)


def test_agent_tools_estimate_and_clean(manager, storage):
    from test_agent import ApiAdapter, call

    from cryoplug.agent import tools as toolmod
    puid = storage["puid"]
    app = create_app(manager.config, start_scheduler=False, manager=manager)
    with TestClient(app, base_url="http://localhost:39500") as client:
        s = toolmod.Session(ApiAdapter(client))
        text = call(s, "estimate_cleanup", project=puid).text
        assert "Clear: 3 jobs" in text and "Delete: 2 jobs" in text and storage["mc"]["uid"] in text and "nothing is changed" in text.lower()
        text = call(s, "estimate_cleanup", project=puid, options={"preprocessing": False, "intermediate": False, "killed": "", "failed": "",
                                                                   "nonfinal": "clear"}).text
        assert "Clear: 2 jobs" in text and "Final results: " + storage["post"]["uid"] in text
        assert call(s, "cleanup_project", project=puid, confirm="P9").error
        text = call(s, "cleanup_project", project=puid, confirm=puid,
                    options={"preprocessing": False, "intermediate": True, "killed": "", "failed": "delete"}).text
        assert "1 deleted" in text and "2 trimmed" in text and storage["failed"]["uid"] in text
        assert client.get(f"/api/projects/{puid}/jobs/{storage['failed']['uid']}").status_code == 404
        assert "estimate_cleanup" in toolmod.select_tools("read") and "cleanup_project" not in toolmod.select_tools("read")

