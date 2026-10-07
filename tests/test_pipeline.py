"""End-to-end tests: real worker processes, fake external programs."""
from __future__ import annotations

import io
import json
import time

import pytest

from conftest import SEQUENCE, log_of, run_until_done
from cryoplug.manager import ManagerError
from cryoplug.scheduler import Scheduler


def _import(manager, synthetic):
    p = manager.create_project("Test project")
    imp = manager.create_job(p["uid"], "import_cryosparc", {"job_dir": str(synthetic["cryosparc_job"]), "symmetry": "C1"})
    manager.queue_job(p["uid"], imp["uid"])
    seq = manager.create_job(p["uid"], "import_sequence", {"fasta": f">a\n{SEQUENCE}\n"})
    manager.queue_job(p["uid"], seq["uid"])
    run_until_done(manager, p["uid"], [imp["uid"], seq["uid"]])
    return p, manager.job(p["uid"], imp["uid"]), manager.job(p["uid"], seq["uid"])


def test_import_cryosparc_and_fsc(manager, synthetic):
    p, imp, seq = _import(manager, synthetic)
    assert imp["status"] == "completed", log_of(manager, p["uid"], imp["uid"])
    outs = {o["name"]: o for o in imp["outputs"]}
    assert {"half_maps", "map", "map_sharp", "mask", "fsc"} <= set(outs)
    assert outs["map_sharp"]["path"].endswith("J12_005_volume_map_sharp.mrc")  # latest iteration, not J12_003
    res = outs["half_maps"]["meta"]["resolution"]
    assert 2.0 < res < 8.0
    assert outs["map"]["meta"]["pixel_size"] == pytest.approx(1.0)
    assert "thumbnail" in outs["map"]
    assert seq["status"] == "completed"
    assert seq["outputs"][0]["meta"]["kinds"] == ["protein"]
    detail = manager.job_detail(p["uid"], imp["uid"])
    kinds = [s["kind"] for s in detail["report"]["sections"]]
    assert "plot" in kinds and "table" in kinds


def test_full_chain_with_fake_tools(manager, synthetic):
    p, imp, seq = _import(manager, synthetic)
    puid = p["uid"]
    loc = manager.create_job(puid, "locscale", {"mode": "model_free"},
                             {"half_maps": {"job": imp["uid"], "output": "half_maps"}, "mask": {"job": imp["uid"], "output": "mask"}})
    build = manager.create_job(puid, "modelangelo_build", {},
                               {"map": {"job": loc["uid"], "output": "map"}, "sequence": {"job": seq["uid"], "output": "sequence"}})
    rsr = manager.create_job(puid, "phenix_real_space_refine", {"macro_cycles": 2},
                             {"model": {"job": build["uid"], "output": "model"}, "map": {"job": imp["uid"], "output": "map_sharp"}})
    val = manager.create_job(puid, "mapmodel_validation", {},
                             {"model": {"job": rsr["uid"], "output": "model"}, "map": {"job": imp["uid"], "output": "map_sharp"},
                              "fsc": {"job": imp["uid"], "output": "fsc"}})
    chk = manager.create_job(puid, "predeposition_check", {},
                             {"model": {"job": rsr["uid"], "output": "model"}, "map": {"job": imp["uid"], "output": "map_sharp"},
                              "half_maps": {"job": imp["uid"], "output": "half_maps"}, "mask": {"job": imp["uid"], "output": "mask"},
                              "sequence": {"job": seq["uid"], "output": "sequence"}})
    pkg = manager.create_job(puid, "deposition_package", {"make_zip": True},
                             {"model": {"job": rsr["uid"], "output": "model"}, "map": {"job": imp["uid"], "output": "map_sharp"},
                              "half_maps": {"job": imp["uid"], "output": "half_maps"}, "mask": {"job": imp["uid"], "output": "mask"},
                              "fsc": {"job": imp["uid"], "output": "fsc"}, "checklist": {"job": chk["uid"], "output": "report"}})
    uids = [loc["uid"], build["uid"], rsr["uid"], val["uid"], chk["uid"], pkg["uid"]]
    for u in uids:  # queue everything at once: dependencies are honoured by the scheduler
        manager.queue_job(puid, u)
    run_until_done(manager, puid, uids, timeout=240)
    for u in uids:
        j = manager.job(puid, u)
        assert j["status"] == "completed", f"{u} {j['type']}: {j['error']}\n{log_of(manager, puid, u)}"

    # GPU jobs got a GPU from the lane and the command was run inside the tool environment
    loc_log = log_of(manager, puid, loc["uid"])
    assert "fake locscale" in loc_log and "-gpus" in loc_log
    # LocScale wrote its map and processing files in its own folder, not next to its inputs (the import job)
    proj = manager.project_dir(puid)
    loc_out = manager.job(puid, loc["uid"])["outputs"][0]
    assert loc_out["path"] == f"{loc['uid']}/locscale_model_free.mrc"
    assert (proj / loc["uid"] / "processing_files").is_dir()
    assert not list((proj / imp["uid"]).glob("locscale*")) and not (proj / imp["uid"] / "processing_files").exists()
    assert "moved to" not in loc_log  # nothing had to be rescued
    ma_log = log_of(manager, puid, build["uid"])
    assert "-pf" in ma_log and "--device 0" in ma_log
    # resolution propagated from the import (auto resolution)
    assert "resolution=" in log_of(manager, puid, rsr["uid"])
    # phenix metrics parsed
    hl = {h["label"]: h for h in manager.job(puid, rsr["uid"])["highlights"]}
    assert hl["MolProbity score"]["value"] == "1.42" and hl["MolProbity score"]["status"] == "good"
    # built-in validation
    v = manager.job(puid, val["uid"])
    meta = v["outputs"][0]["meta"]
    assert meta["q_mean"] > 0.4
    assert meta["cc_mask"] > 0.7
    assert meta["atom_inclusion"] > 0.5
    # checklist + package
    c = manager.job(puid, chk["uid"])
    assert c["outputs"][0]["meta"]["fail"] == 0, json.dumps(json.loads((manager.job_dir(puid, chk["uid"]) / "predeposition_checklist.json").read_text()), indent=1)
    dep = manager.job_dir(puid, pkg["uid"]) / "deposition"
    for name in ("model.cif", "primary_map.mrc", "half_map_1.mrc", "half_map_2.mrc", "mask.mrc", "fsc.xml",
                 "CHECKLIST.md", "methods_draft.md", "table1_draft.md"):
        assert (dep / name).exists(), name
    methods = (dep / "methods_draft.md").read_text()
    assert "ModelAngelo" in methods and "LocScale" in methods and "real_space_refine" in methods
    table = (dep / "table1_draft.md").read_text()
    assert "1.42" in table  # MolProbity score collected from the refinement job
    assert (manager.job_dir(puid, pkg["uid"]) / "deposition_package.zip").exists()


def test_interactive_isolde_session(manager, synthetic):
    p, imp, _ = _import(manager, synthetic)
    puid = p["uid"]
    mdl = manager.create_job(puid, "import_model", {"source": "file", "path": str(synthetic["model"])})
    manager.queue_job(puid, mdl["uid"])
    run_until_done(manager, puid, [mdl["uid"]])
    iso = manager.create_job(puid, "isolde_session", {},
                             {"model": {"job": mdl["uid"], "output": "model"}, "map": {"job": imp["uid"], "output": "map_sharp"}})
    manager.queue_job(puid, iso["uid"])
    run_until_done(manager, puid, [iso["uid"]])
    job = manager.job(puid, iso["uid"])
    assert job["status"] == "waiting", log_of(manager, puid, iso["uid"])
    script = (manager.job_dir(puid, iso["uid"]) / "isolde_session.py").read_text()
    assert "clipper associate #2 toModel #1" in script and "isolde start" in script
    with pytest.raises(ManagerError):
        manager.finish_interactive(puid, iso["uid"])  # nothing saved yet
    manager.save_upload(puid, iso["uid"], "isolde_model.pdb", io.BytesIO(synthetic["model"].read_bytes()))
    zpath = manager.session_bundle(puid, iso["uid"])
    assert zpath.exists() and zpath.stat().st_size > 0
    detail = manager.job_detail(puid, iso["uid"])
    assert detail["candidates"][0]["path"] == "isolde_model.pdb"
    done = manager.finish_interactive(puid, iso["uid"])
    assert done["status"] == "completed"
    assert done["outputs"][0]["type"] == "model"


def test_failure_kill_and_clear(manager, synthetic):
    p = manager.create_project("Fail project")
    puid = p["uid"]
    bad = manager.create_job(puid, "custom_command", {"command": "echo hello; exit 3"})
    slow = manager.create_job(puid, "custom_command", {"command": "sleep 60"})
    manager.queue_job(puid, bad["uid"])
    manager.queue_job(puid, slow["uid"])
    sched = Scheduler(manager)
    end = time.time() + 30
    while time.time() < end and manager.job(puid, slow["uid"])["status"] != "running":
        sched.tick()
        time.sleep(0.2)
    assert manager.job(puid, slow["uid"])["status"] == "running"
    manager.kill_job(puid, slow["uid"])
    run_until_done(manager, puid, [bad["uid"], slow["uid"]])
    b = manager.job(puid, bad["uid"])
    assert b["status"] == "failed" and "code 3" in b["error"]
    assert "hello" in log_of(manager, puid, bad["uid"])
    assert manager.job(puid, slow["uid"])["status"] == "killed"
    cleared = manager.clear_job(puid, bad["uid"])
    assert cleared["status"] == "building" and cleared["outputs"] == []


def test_queue_validation_and_dependencies(manager, synthetic):
    p = manager.create_project("Validation")
    puid = p["uid"]
    j = manager.create_job(puid, "modelangelo_build", {})
    with pytest.raises(ManagerError, match="required"):
        manager.queue_job(puid, j["uid"])
    with pytest.raises(ManagerError, match="Unknown parameter"):
        manager.create_job(puid, "map_fsc", {"bogus": 1})
    imp = manager.create_job(puid, "import_maps", {"map": str(synthetic["map"])})
    with pytest.raises(ManagerError, match="expects"):
        manager.update_job(puid, j["uid"], inputs={"sequence": {"job": imp["uid"], "output": "map"}})
    manager.update_job(puid, j["uid"], inputs={"map": {"job": imp["uid"], "output": "map"}})
    with pytest.raises(ManagerError, match="Used as input"):
        manager.delete_job(puid, imp["uid"])
    clone = manager.clone_job(puid, j["uid"])
    assert clone["inputs"] == manager.job(puid, j["uid"])["inputs"]


def test_workflow_instantiation(manager, synthetic):
    p = manager.create_project("Workflow")
    res = manager.instantiate_workflow(p["uid"], "denovo_modelangelo",
                                       overrides={"import": {"job_dir": str(synthetic["cryosparc_job"])},
                                                  "seq": {"fasta": f">a\n{SEQUENCE}"}},
                                       include=["locscale"], queue=False)
    types = [j["type"] for j in res["jobs"]]
    assert "isolde_session" not in types and "phenix_validation_cryoem" not in types
    by_type = {}
    for j in res["jobs"]:
        by_type.setdefault(j["type"], []).append(j)
    final = by_type["phenix_real_space_refine"][1]
    first = by_type["phenix_real_space_refine"][0]
    assert final["inputs"]["model"]["job"] == first["uid"]  # ISOLDE skipped -> falls back to refine1
    assert by_type["modelangelo_build"][0]["inputs"]["map"]["job"] == by_type["locscale"][0]["uid"]
