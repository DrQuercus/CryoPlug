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


def test_map_operations_resample(manager, synthetic):
    """Map operations resample a map to another pixel size (Fourier) or onto the grid of another map."""
    from cryoplug.mrc import MapVolume
    p = manager.create_project("Map operations")
    puid = p["uid"]
    imp = _run(manager, puid, "import_cryosparc", {"job_dir": str(synthetic["cryosparc_job"])})
    src = {"job": imp["uid"], "output": "map_sharp"}
    orig = MapVolume.read(manager.project_dir(puid) / next(o for o in imp["outputs"] if o["name"] == "map_sharp")["path"])
    coarse = _run(manager, puid, "map_tools", {"resample_pixel_size": 2 * float(orig.voxel[0])}, {"map": src})
    out = MapVolume.read(manager.project_dir(puid) / coarse["outputs"][0]["path"])
    assert out.shape_xyz[0] == orig.shape_xyz[0] // 2 and abs(out.voxel[0] - 2 * orig.voxel[0]) < 1e-4
    assert "resampled from" in log_of(manager, puid, coarse["uid"])
    back = _run(manager, puid, "map_tools", {"lowpass": 0, "normalize": False},
                {"map": {"job": coarse["uid"], "output": "map"}, "reference": src})
    again = MapVolume.read(manager.project_dir(puid) / back["outputs"][0]["path"])
    assert again.data.shape == orig.data.shape and abs(again.voxel[0] - orig.voxel[0]) < 1e-6
    job = manager.create_job(puid, "map_tools", {"resample_pixel_size": 2.0}, {"map": src, "reference": src})
    import pytest
    with pytest.raises(Exception, match="not both"):
        manager.queue_job(puid, job["uid"])
