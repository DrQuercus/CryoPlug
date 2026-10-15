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
