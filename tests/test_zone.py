"""Map zone around a model (ChimeraX `volume zone`) used by the 3D viewer."""
from __future__ import annotations

import shutil

import numpy as np
from fastapi.testclient import TestClient

from cryoplug.modelio import atom_arrays, read_structure
from cryoplug.mrc import MapVolume, zone_mask
from cryoplug.server.app import create_app


def test_zone_mask_matches_brute_force():
    rng = np.random.default_rng(1)
    vol = MapVolume(data=np.zeros((14, 12, 10), np.float32), voxel=np.array([1.3, 0.9, 1.1]), origin=np.array([-2.0, 1.0, 0.5]))
    xyz = vol.origin + rng.uniform(0, 1, (7, 3)) * np.array([10 * 1.3, 12 * 0.9, 14 * 1.1])
    radius = 2.4
    got = zone_mask(vol, xyz, radius)
    z, y, x = np.meshgrid(*(np.arange(n) for n in vol.data.shape), indexing="ij")
    centres = np.stack([x, y, z], axis=-1) * vol.voxel + vol.origin
    dist = np.sqrt(((centres[..., None, :] - xyz) ** 2).sum(-1)).min(-1)
    assert np.array_equal(got, dist <= radius)
    assert got.any() and not got.all()


def test_zone_endpoint(manager, synthetic, tmp_path):
    p = manager.create_project("Zone")
    pdir = manager.project_dir(p["uid"])
    shutil.copy(synthetic["map"], pdir / "map.mrc")
    shutil.copy(synthetic["model"], pdir / "model.pdb")
    full = MapVolume.read(synthetic["map"])
    xyz = atom_arrays(read_structure(synthetic["model"]))["xyz"]
    app = create_app(manager.config, start_scheduler=False, manager=manager)
    with TestClient(app, base_url="http://localhost:39500") as client:
        url = f"/api/projects/{p['uid']}/zone"
        r = client.get(url, params={"map": "map.mrc", "model": "model.pdb", "radius": 3, "max_box": 0})
        assert r.status_code == 200, r.text
        (tmp_path / "zone.mrc").write_bytes(r.content)
        zone = MapVolume.read(tmp_path / "zone.mrc")
        mask = zone_mask(full, xyz, 3.0)
        assert zone.data.shape == full.data.shape and np.allclose(zone.origin, full.origin)
        assert np.allclose(zone.data[mask], full.data[mask])
        assert zone.data[~mask].max() < full.data.min()  # hidden at every contour level
        # cached: same bytes again; binned variant follows the preview size
        assert client.get(url, params={"map": "map.mrc", "model": "model.pdb", "radius": 3, "max_box": 0}).content == r.content
        small = client.get(url, params={"map": "map.mrc", "model": "model.pdb", "radius": 3, "max_box": 64})
        assert small.status_code == 200
        assert client.get(url, params={"map": "map.mrc", "model": "model.pdb", "radius": 80}).status_code == 400
        assert client.get(url, params={"map": "map.mrc", "model": "nope.pdb"}).status_code == 404
        (pdir / "bad.pdb").write_text("not a model\n")
        assert client.get(url, params={"map": "map.mrc", "model": "bad.pdb"}).status_code == 400
