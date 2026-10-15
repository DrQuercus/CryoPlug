"""Masks drawn in the 3D viewer: the recipe engine (shapes, regions, zones, modes), the quick preview of the viewer
and the job computing the mask on the full grid."""
from __future__ import annotations

import gzip
import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from conftest import log_of, run_until_done
from cryoplug import maskdraw as md
from cryoplug.mrc import MapVolume


def _two_blobs() -> MapVolume:
    z, y, x = (np.indices((64, 64, 64)) - 32).astype(float)
    dens = np.exp(-((x - 10) ** 2 + y ** 2 + z ** 2) / 30) + np.exp(-((x + 12) ** 2 + y ** 2 + z ** 2) / 30)
    # x in Å = 3 + 1.5 i: blob 1 at i = 42 (66 Å), blob 2 at i = 20 (33 Å); y and z centres at 48 Å
    return MapVolume(data=dens.astype(np.float32), voxel=np.ones(3) * 1.5, origin=np.array([3.0, 0.0, 0.0]))


def test_shapes_and_modes_in_physical_coordinates():
    vol = _two_blobs()
    recipe = md.validate({"ops": [
        {"shape": "region", "mode": "add", "seed": [66.0, 48.0, 50.5], "level": 0.3},  # picked on the surface of blob 1
        {"shape": "sphere", "mode": "add", "center": [33.0, 48.0, 48.0], "radius": 4},
        {"shape": "box", "mode": "erase", "center": [33.0, 48.0, 48.0], "size": [2, 2, 2]},
        {"shape": "cylinder", "mode": "add", "start": [40.0, 48.0, 48.0], "end": [58.0, 48.0, 48.0], "radius": 1.5},
    ]})
    core = md.draw(recipe, vol)
    assert core[32, 32, 42] and not core[32, 32, 20] and core[32, 32, 22]  # region, erased box, sphere
    assert core[32, 32, 31] and not core[32, 40, 31]  # cylinder axis, not beyond its radius
    assert not core[32, 32, 5]  # nothing else
    # keep only: intersect with a sphere around blob 1
    keep = md.validate({"ops": recipe["ops"] + [{"shape": "sphere", "mode": "keep", "center": [66.0, 48.0, 48.0], "radius": 6}]})
    kept = md.draw(keep, vol)
    assert kept[32, 32, 42] and not kept[32, 32, 22] and kept.sum() < core.sum()
    # the soft mask: 1 in the core, raised cosine outside
    soft = md.finish(core, vol.voxel, {**recipe, "dilate": 1.5, "soft": 3.0})
    assert soft[32, 32, 42] == 1.0 and 0 < soft.min() + 1 and soft.max() == 1.0
    assert soft[core].min() == 1.0


def test_density_level_zone_and_validation():
    vol = _two_blobs()
    both = md.draw(md.validate({"ops": [{"shape": "map", "mode": "add", "level": 0.3}]}), vol)
    assert both[32, 32, 42] and both[32, 32, 20]
    xyz = np.array([[33.0, 48.0, 48.0]])
    zone = md.draw(md.validate({"ops": [{"shape": "zone", "mode": "add", "model": "m.pdb", "selection": "A", "radius": 3}]}),
                   vol, models=lambda path, sel: xyz)
    assert zone[32, 32, 20] and zone.sum() < 100
    with pytest.raises(ValueError, match="unknown shape"):
        md.validate({"ops": [{"shape": "blob"}]})
    with pytest.raises(ValueError, match="radius"):
        md.validate({"ops": [{"shape": "sphere", "center": [0, 0, 0], "radius": -1}]})
    with pytest.raises(ValueError, match="same point"):
        md.validate({"ops": [{"shape": "cylinder", "start": [1, 1, 1], "end": [1, 1, 1], "radius": 2}]})
    with pytest.raises(ValueError, match="Cannot read"):
        md.validate({"ops": [{"shape": "zone", "model": "m.pdb", "selection": "A:1-x"}]})
    assert "sphere r = 4.0" in md.describe(md.validate({"ops": [{"shape": "sphere", "center": [1, 2, 3], "radius": 4}]})["ops"][0])


def test_model_map_operation():
    """A model map (molmap) in a drawing: Gaussians on the atoms cut at the contour enclosing a share of their density;
    a larger share or a lower resolution gives a larger region; erasing it from the map density."""
    vol = _two_blobs()
    xyz = np.array([[64.0, 48.0, 48.0], [66.0, 48.0, 48.0], [68.0, 48.0, 48.0]])  # on blob 1 (i = 42)

    def atoms(path, selection):
        assert path == "m.pdb" and selection == "A:1-3"
        return xyz, np.array([6.0, 7.0, 8.0])

    def op(**kw):
        return {"shape": "molmap", "model": "m.pdb", "selection": "A:1-3", "resolution": 8, **kw}

    core = md.draw(md.validate({"ops": [op()]}), vol, atoms=atoms)
    assert core[32, 32, 42] and not core[32, 32, 20] and not core[32, 32, 52]
    wider = md.draw(md.validate({"ops": [op(enclose=99)]}), vol, atoms=atoms)
    blurred = md.draw(md.validate({"ops": [op(resolution=15)]}), vol, atoms=atoms)
    assert wider.sum() > core.sum() and blurred.sum() > core.sum() and wider[core].all()
    rest = md.draw(md.validate({"ops": [{"shape": "map", "mode": "add", "level": 0.3}, op(mode="erase", enclose=99)]}), vol, atoms=atoms)
    assert not rest[32, 32, 42] and rest[32, 32, 20]
    assert "model map of A:1-3 of m.pdb at 8 Å (molmap)" in md.describe(md.validate({"ops": [op()]})["ops"][0])
    assert md.validate({"ops": [{"shape": "molmap", "model": "m.pdb"}]})["ops"][0]["enclose"] == 95.0  # ChimeraX's contour
    with pytest.raises(ValueError, match="resolution"):
        md.validate({"ops": [op(resolution=0.5)]})
    with pytest.raises(ValueError, match="contour"):
        md.validate({"ops": [op(enclose=100)]})
    with pytest.raises(ValueError, match="no model"):
        md.validate({"ops": [{"shape": "molmap", "resolution": 8}]})
    with pytest.raises(ValueError, match="need the model"):
        md.draw(md.validate({"ops": [op()]}), vol)


def test_draw_mask_job_and_preview(manager, synthetic):
    from cryoplug.server.app import create_app
    p = manager.create_project("Drawn mask")
    puid = p["uid"]
    imp = manager.create_job(puid, "import_cryosparc", {"job_dir": str(synthetic["cryosparc_job"]), "import_particles": False})
    manager.queue_job(puid, imp["uid"])
    run_until_done(manager, puid, [imp["uid"]])
    imp = manager.job(puid, imp["uid"])
    map_out = next(o for o in imp["outputs"] if o["name"] == "map")
    vol = MapVolume.read(manager.project_dir(puid) / map_out["path"])
    centre = (vol.origin + np.array(vol.shape_xyz) / 2 * vol.voxel).tolist()  # the helix lies along z at the centre
    ops = [{"shape": "sphere", "mode": "add", "center": centre, "radius": 9},
           {"shape": "box", "mode": "erase", "center": [centre[0], centre[1], centre[2] + 8], "size": [30, 30, 6]}]

    # an empty drawing cannot be queued; the message says where to draw
    empty = manager.create_job(puid, "draw_mask", {}, {"map": {"job": imp["uid"], "output": "map"}})
    with pytest.raises(Exception, match="3D viewer"):
        manager.queue_job(puid, empty["uid"])

    app = create_app(manager.config, start_scheduler=False, manager=manager)
    with TestClient(app, base_url="http://localhost:39500") as client:
        r = client.post(f"/api/projects/{puid}/mask-preview", json={"map": map_out["path"], "ops": ops, "dilate": 2, "max_box": 64})
        assert r.status_code == 200, r.text
        assert r.headers["content-encoding"] == "gzip"
        raw = r.content if r.content[:4] != b"\x1f\x8b\x08\x00" else gzip.decompress(r.content)
        assert len(raw) > 1024
        bad = client.post(f"/api/projects/{puid}/mask-preview", json={"map": map_out["path"], "ops": [{"shape": "nope"}]})
        assert bad.status_code == 400 and "unknown shape" in bad.json()["detail"]
        outside = client.post(f"/api/projects/{puid}/mask-preview", json={"map": "../etc/passwd", "ops": ops})
        assert outside.status_code == 404
        # saving from the viewer: the job made in the builder gets the drawing, then is queued
        upd = client.patch(f"/api/projects/{puid}/jobs/{empty['uid']}", json={"params": {"recipe": json.dumps({"ops": ops}), "dilate": 2, "soft": 4}})
        assert upd.status_code == 200, upd.text
        assert client.post(f"/api/projects/{puid}/jobs/{empty['uid']}/queue", json={}).status_code == 200
    run_until_done(manager, puid, [empty["uid"]])
    job = manager.job(puid, empty["uid"])
    assert job["status"] == "completed", log_of(manager, puid, empty["uid"])
    out = next(o for o in job["outputs"] if o["name"] == "mask")
    mask = MapVolume.read(manager.project_dir(puid) / out["path"])
    assert mask.shape_xyz == vol.shape_xyz and mask.data.max() == 1.0 and mask.data.min() >= 0.0
    c = [s // 2 for s in mask.data.shape]
    assert mask.data[c[0], c[1], c[2]] == 1.0  # centre of the sphere
    assert mask.data[c[0] + 8, c[1], c[2]] < 0.5  # the erased slab (+8 Å along z at 1 Å/px)
    assert out["meta"]["source"] == "drawn" and len(out["meta"]["center_of_mass"]) == 3
    report = manager.job_detail(puid, empty["uid"])["report"]["sections"]
    assert any(s["kind"] == "table" and s["title"] == "Drawing" for s in report)


def test_model_map_drawn_on_a_map(manager, synthetic):
    """A model map drawn in the viewer: previewed by the server, then computed by the job on the full grid."""
    from cryoplug.server.app import create_app
    p = manager.create_project("Model map mask")
    puid = p["uid"]
    imp = manager.create_job(puid, "import_cryosparc", {"job_dir": str(synthetic["cryosparc_job"]), "import_particles": False})
    mdl = manager.create_job(puid, "import_model", {"source": "file", "path": str(synthetic["model"])})
    for j in (imp, mdl):
        manager.queue_job(puid, j["uid"])
    run_until_done(manager, puid, [imp["uid"], mdl["uid"]])
    map_out = next(o for o in manager.job(puid, imp["uid"])["outputs"] if o["name"] == "map")
    model_out = next(o for o in manager.job(puid, mdl["uid"])["outputs"] if o["name"] == "model")
    ops = [{"shape": "molmap", "mode": "add", "model": model_out["path"], "selection": "A:1-10", "resolution": 10, "enclose": 95}]
    app = create_app(manager.config, start_scheduler=False, manager=manager)
    with TestClient(app, base_url="http://localhost:39500") as client:
        r = client.post(f"/api/projects/{puid}/mask-preview", json={"map": map_out["path"], "ops": ops, "dilate": 1, "max_box": 48})
        assert r.status_code == 200, r.text
        outside = client.post(f"/api/projects/{puid}/mask-preview",
                              json={"map": map_out["path"], "ops": [{**ops[0], "model": "../../etc/hosts"}]})
        assert outside.status_code == 404
    job = manager.create_job(puid, "draw_mask", {"recipe": json.dumps({"ops": ops}), "dilate": 1, "soft": 3},
                             {"map": {"job": imp["uid"], "output": "map"}, "model": {"job": mdl["uid"], "output": "model"}})
    manager.queue_job(puid, job["uid"])
    run_until_done(manager, puid, [job["uid"]])
    done = manager.job(puid, job["uid"])
    assert done["status"] == "completed", log_of(manager, puid, job["uid"])
    mask = MapVolume.read(manager.project_dir(puid) / done["outputs"][0]["path"])
    from conftest import build_helix
    from cryoplug import masks as mk
    helix = build_helix()
    assert np.all(mask.interpolate(mk.select_atoms(helix, "A:1-10")["xyz"]) >= 0.99)  # the chosen residues inside
    assert np.mean(mask.interpolate(mk.select_atoms(helix, "A:18-22")["xyz"]) < 0.5) > 0.9  # far residues outside
    table = next(s for s in manager.job_detail(puid, job["uid"])["report"]["sections"] if s["kind"] == "table")
    assert "molmap" in str(table)
