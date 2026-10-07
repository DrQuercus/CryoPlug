"""Composite maps from focused (local refinement) maps, with their half maps found next to the maps."""
from __future__ import annotations

from conftest import log_of, run_until_done


def _run(manager, puid, jtype, params, inputs=None):
    job = manager.create_job(puid, jtype, params, inputs or {})
    manager.queue_job(puid, job["uid"])
    run_until_done(manager, puid, [job["uid"]])
    done = manager.job(puid, job["uid"])
    assert done["status"] == "completed", f"{done['error']}\n{log_of(manager, puid, job['uid'])}"
    return done


def test_composite_map_with_half_maps(manager, synthetic):
    p = manager.create_project("Composite")
    puid = p["uid"]
    consensus = _run(manager, puid, "import_cryosparc", {"job_dir": str(synthetic["cryosparc_job"])})["uid"]
    local1 = _run(manager, puid, "import_cryosparc", {"job_dir": str(synthetic["cryosparc_job"])})["uid"]
    local2 = _run(manager, puid, "import_cryosparc", {"job_dir": str(synthetic["cryosparc_job"])})["uid"]
    model = _run(manager, puid, "import_model", {"source": "file", "path": str(synthetic["model"])})["uid"]
    done = _run(manager, puid, "composite_map", {}, {
        "model": {"job": model, "output": "model"}, "reference": {"job": consensus, "output": "map_sharp"},
        "focused_1": {"job": local1, "output": "map_sharp"}, "focused_2": {"job": local2, "output": "map_sharp"}})
    log = log_of(manager, puid, done["uid"])
    cmd = next(line for line in log.splitlines() if "$ " in line and "combine_focused_maps" in line).split()
    assert sum(a.startswith("map_file=") for a in cmd) == 3 and sum(a.startswith("half_map_1_file=") for a in cmd) == 3
    assert sum(a.startswith("model_file=") for a in cmd) == 1 and any(a.startswith("resolution=") for a in cmd)
    out = {o["name"]: o for o in done["outputs"]}
    assert out["map"]["path"].endswith("combined_map.ccp4")
    assert [f.rsplit("/", 1)[-1] for f in out["half_maps"]["files"]] == ["combined_half_map_A.ccp4", "combined_half_map_B.ccp4"]
    assert out["map"]["meta"]["composite_of"] == [f"{consensus}.map_sharp", f"{local1}.map_sharp", f"{local2}.map_sharp"]
    assert out["map"]["meta"]["resolution"] > 0  # inherited from the consensus map
    # the composite half maps feed the half-map jobs (FSC, local resolution)
    fsc = _run(manager, puid, "map_fsc", {}, {"half_maps": {"job": done["uid"], "output": "half_maps"}})
    assert fsc["outputs"][0]["type"] == "fsc"


def test_composite_map_without_half_maps(manager, synthetic):
    p = manager.create_project("Composite, maps only")
    puid = p["uid"]
    consensus = _run(manager, puid, "import_cryosparc", {"job_dir": str(synthetic["cryosparc_job"])})["uid"]
    model = _run(manager, puid, "import_model", {"source": "file", "path": str(synthetic["model"])})["uid"]
    filtered = _run(manager, puid, "map_tools", {"lowpass": 6}, {"map": {"job": consensus, "output": "map_sharp"}})["uid"]
    done = _run(manager, puid, "composite_map", {}, {
        "model": {"job": model, "output": "model"}, "reference": {"job": consensus, "output": "map_sharp"},
        "focused_1": {"job": filtered, "output": "map"}})
    assert {o["name"] for o in done["outputs"]} == {"map"}
    assert "No half maps found next to" in log_of(manager, puid, done["uid"])
