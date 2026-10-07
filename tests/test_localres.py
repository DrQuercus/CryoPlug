"""Local resolution: the Phenix job, its statistics, and CryoSPARC local resolution maps."""
from __future__ import annotations

import csv
import shutil

import numpy as np

from conftest import log_of, run_until_done
from cryoplug import locres as lr
from cryoplug.mrc import MapVolume


def _run(manager, puid, jtype, params, inputs):
    job = manager.create_job(puid, jtype, params, inputs)
    manager.queue_job(puid, job["uid"])
    run_until_done(manager, puid, [job["uid"]])
    done = manager.job(puid, job["uid"])
    assert done["status"] == "completed", f"{done['error']}\n{log_of(manager, puid, job['uid'])}"
    return done


def test_statistics_helpers():
    values = np.array([0.0, 3.0, 3.5, 4.0, 4.5, 250.0, np.nan])
    s = lr.summary(values)
    assert s["n"] == 4 and s["median"] == 3.75  # fill values (0, 250, NaN) ignored
    assert lr.display_range(s) == [round(s["p5"], 1), round(s["p95"], 1)]
    rows = [{"chain": "A", "num": i, "icode": "", "name": "ALA", "value": 3.0 + i / 10} for i in range(10)]
    rows += [{"chain": "B", "num": 1, "icode": "", "name": "GLY", "value": 2.5}, {"chain": "B", "num": 2, "icode": "", "name": "GLY", "value": None}]
    chains = lr.per_chain(rows)
    assert [c["chain"] for c in chains] == ["B", "A"] and chains[0]["n"] == 1


def test_local_resolution_job(manager, synthetic):
    p = manager.create_project("Local resolution")
    puid = p["uid"]
    imp = manager.create_job(puid, "import_cryosparc", {"job_dir": str(synthetic["cryosparc_job"])})
    mdl = manager.create_job(puid, "import_model", {"source": "file", "path": str(synthetic["model"])})
    for j in (imp, mdl):
        manager.queue_job(puid, j["uid"])
    run_until_done(manager, puid, [imp["uid"], mdl["uid"]])
    done = _run(manager, puid, "local_resolution", {"nproc": 2},
                {"half_maps": {"job": imp["uid"], "output": "half_maps"}, "model": {"job": mdl["uid"], "output": "model"}})
    out = {o["name"]: o for o in done["outputs"]}
    loc = out["local_resolution"]
    assert loc["type"] == "locres" and loc["path"].endswith("local_resolution_map.ccp4") and loc.get("thumbnail")
    imported = {o["name"]: o for o in manager.job(puid, imp["uid"])["outputs"]}
    assert loc["meta"]["colour_map"] == imported["map_sharp"]["path"]  # sharpened map of the half maps' job
    lo, hi = loc["meta"]["display_range"]
    assert 2.5 <= lo < hi < 6 and "resolution" not in loc["meta"]
    log = log_of(manager, puid, done["uid"])
    assert "nproc=2" in log and "method=" not in log  # Phenix defaults are not passed
    proj = manager.project_dir(puid)
    with open(proj / out["per_residue"]["files"][0]) as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 22 and all(2.5 <= float(r["local_resolution_A"]) < 6 for r in rows)
    titles = [s["title"] for s in manager.job_detail(puid, done["uid"])["report"]["sections"]]
    assert any("per chain" in t for t in titles) and any("Distribution" in t for t in titles)
    assert "Local res." in {h["label"] for h in done["highlights"]}


def test_cryosparc_local_resolution_map_is_typed_and_ranged(manager, synthetic, tmp_path):
    src = tmp_path / "J20"
    shutil.copytree(synthetic["cryosparc_job"], src)
    ref = MapVolume.read(synthetic["map"])
    z, y, x = np.indices(ref.data.shape)
    ref.like(3.0 + 0.05 * x).write(src / "J20_map_locres.mrc")
    p = manager.create_project("Locres import")
    imp = _run(manager, p["uid"], "import_cryosparc", {"job_dir": str(src)}, {})
    out = {o["name"]: o for o in imp["outputs"]}
    assert out["map_locres"]["type"] == "locres"
    assert out["map_locres"]["meta"]["colour_map"] == out["map_sharp"]["path"]
    lo, hi = out["map_locres"]["meta"]["display_range"]
    assert 3.0 <= lo < hi <= 3.0 + 0.05 * 47
