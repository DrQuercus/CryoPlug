"""Rigid-body fitting (ChimeraX's fitmap algorithm): known displacements recovered for atoms, simulated maps and
maps, the search, the placement helpers, the job and the endpoint of the 3D viewer."""
from __future__ import annotations

import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from conftest import build_helix, log_of, run_until_done
from cryoplug import fitting as ft
from cryoplug import masks as mk
from cryoplug.modelio import read_structure
from cryoplug.mrc import MapVolume


def _density(resolution: float = 4.0):
    sel = mk.select_atoms(build_helix())
    grid = MapVolume(data=np.zeros((48, 48, 48), np.float32), voxel=np.array([1.0, 1.0, 1.0]))
    dens = mk.molmap(grid, sel["xyz"], sel["weights"], resolution)
    dens += np.random.default_rng(0).normal(0, dens.std() * 0.2, dens.shape).astype(np.float32)
    return sel, grid.like(dens)


def _rmsd(m, xyz):
    return float(np.sqrt(((ft.apply(m, xyz) - xyz) ** 2).sum(1).mean()))


def test_local_fits_recover_a_known_displacement():
    sel, vol = _density()
    xyz, target = sel["xyz"], ft.Target(vol)
    level = float(np.quantile(vol.data, 0.95))
    moved = ft.translation([2.0, -1.5, 1.5]) @ ft.rotation([1, 2, 0.5], 10, xyz.mean(0))
    assert _rmsd(moved, xyz) > 2.5
    atoms = ft.fit(xyz, None, target, "overlap", start=moved, level=level)  # ChimeraX's default for atoms
    assert _rmsd(atoms["matrix"], xyz) < 0.3
    assert atoms["stats"]["average_map_value"] > 2 * atoms["before"]["average_map_value"]
    assert atoms["stats"]["shift"] > 1 and atoms["stats"]["angle"] > 5 and atoms["stats"]["outside_contour"] < len(xyz) * 0.2
    pts, w = ft.simulated_points(xyz, sel["weights"], 6.0)
    for metric in ("overlap", "correlation", "cam"):
        sim = ft.fit(pts, w, target, metric, start=moved)
        assert _rmsd(sim["matrix"], xyz) < 0.4, metric
        assert sim["stats"]["correlation"] > sim["before"]["correlation"]
    pts, w = ft.map_points(vol, level)  # the map itself, moved: map-in-map
    mm = ft.fit(pts, w, target, "correlation", start=moved)
    assert _rmsd(mm["matrix"], xyz) < 0.2 and mm["stats"]["correlation"] > 0.99
    fixed = ft.fit(xyz, None, target, "overlap", start=moved, rotate=False)  # shifts only: the angle stays
    assert abs(ft.angle_of(fixed["matrix"]) - 10) < 1e-6
    assert "average map value" in ft.message("#2", "#1", atoms)
    with pytest.raises(ValueError, match="Correlation needs"):
        ft.fit(xyz, None, target, "correlation")


def test_search_finds_a_far_placement():
    sel, vol = _density()
    xyz, target = sel["xyz"], ft.Target(vol)
    level = float(np.quantile(vol.data, 0.95))
    far = ft.translation([6.0, -5.0, 4.0])  # random shifts within 10 Å of it (a noisy map spans the whole box)
    res = ft.fit(xyz, None, target, "overlap", start=far, search=8, radius=10, level=level, rotate=False, seed=3)
    assert _rmsd(res["matrix"], xyz) < 0.3 and res["fits"][0]["hits"] >= 1
    scores = [ft.score_of(f["stats"]) for f in res["fits"]]
    assert scores == sorted(scores, reverse=True)
    assert all(f["stats"]["inside_fraction"] >= 0.1 for f in res["fits"])
    again = ft.fit(xyz, None, target, "overlap", start=far, search=8, radius=10, level=level, rotate=False, seed=3, workers=3)
    assert np.allclose(again["matrix"], res["matrix"])  # the threads do not change the result
    with pytest.raises(ValueError, match="random placements"):
        ft.fit_search(xyz, None, target, 0)


def test_matrices_and_placement_helpers():
    m = ft.translation([4, -3, 2]) @ ft.rotation([0.3, 1, 0.2], 35, [20, 10, 25])
    assert np.allclose(ft.as_matrix(m.tolist()), m) and np.allclose(ft.as_matrix(m[:3].ravel().tolist()), m)
    assert ft.is_identity(ft.as_matrix(np.eye(4).ravel().tolist()))
    with pytest.raises(ValueError, match="no scaling"):
        ft.as_matrix(np.diag([2.0, 1, 1, 1]).tolist())
    with pytest.raises(ValueError, match="4×4"):
        ft.as_matrix([1, 2, 3])
    assert "Rotation angle (degrees) 35" in ft.describe(m)
    z, y, x = np.indices((40, 44, 48))
    vol = MapVolume(data=np.exp(-((x - 30) ** 2 + (y - 15) ** 2 + (z - 20) ** 2) / 8.0).astype(np.float32),
                    voxel=np.array([1.2, 1.1, 1.0]), origin=np.array([3.0, -2.0, 5.0]))
    peak = vol.origin + np.array([30, 15, 20]) * vol.voxel
    placed = ft.place_map(vol, m, vol)
    k = np.unravel_index(np.argmax(placed.data), placed.data.shape)
    assert np.linalg.norm(placed.origin + np.array(k[::-1]) * placed.voxel - ft.apply(m, peak[None])[0]) < 1.2
    shifted = ft.place_map(vol, ft.translation([4, -3, 2]))  # no target grid, no rotation: only the origin moves
    assert np.array_equal(shifted.data, vol.data) and np.allclose(shifted.origin, vol.origin + [4, -3, 2])
    assert ft.default_level(MapVolume(data=(placed.data > 0.5).astype(np.float32), voxel=np.ones(3))) == 0.5


def _project(manager, synthetic, name):
    p = manager.create_project(name)
    puid = p["uid"]
    imp = manager.create_job(puid, "import_cryosparc", {"job_dir": str(synthetic["cryosparc_job"]), "import_particles": False})
    mdl = manager.create_job(puid, "import_model", {"source": "file", "path": str(synthetic["model"])})
    for j in (imp, mdl):
        manager.queue_job(puid, j["uid"])
    run_until_done(manager, puid, [imp["uid"], mdl["uid"]])
    return puid, imp["uid"], mdl["uid"]


def _done(manager, puid, job):
    manager.queue_job(puid, job["uid"])
    run_until_done(manager, puid, [job["uid"]])
    done = manager.job(puid, job["uid"])
    assert done["status"] == "completed", f"{done['error']}\n{log_of(manager, puid, job['uid'])}"
    return done


def test_fit_in_map_job(manager, synthetic):
    puid, imp, mdl = _project(manager, synthetic, "Rigid-body fit")
    xyz = mk.select_atoms(build_helix())["xyz"]
    moved = ft.translation([1.5, -1.0, 1.2]) @ ft.rotation([0, 1, 1], 8, xyz.mean(0))
    links = {"model": {"job": mdl, "output": "model"}, "map": {"job": imp, "output": "map_sharp"}}
    # the position set in the viewer, then fitted back into the density
    done = _done(manager, puid, manager.create_job(puid, "fit_in_map", {"matrix": json.dumps(moved.tolist())}, links))
    out = done["outputs"][0]
    placed = mk.select_atoms(read_structure(manager.project_dir(puid) / out["path"]))["xyz"]
    assert out["type"] == "model" and float(np.sqrt(((placed - xyz) ** 2).sum(1).mean())) < 0.5
    assert out["meta"]["fit"]["average_map_value"] > 0 and out["meta"]["fitted_in"].endswith("map_sharp.mrc")
    report = manager.job_detail(puid, done["uid"])["report"]["sections"]
    assert any(s["kind"] == "text" and "Matrix rotation and translation" in str(s) for s in report)
    assert {h["label"] for h in done["highlights"]} >= {"Avg map value", "Inside", "Moved"}
    # the position only (no fit): the model moves exactly by it; with a resolution, the simulated map is fitted
    done = _done(manager, puid, manager.create_job(puid, "fit_in_map", {"matrix": json.dumps(moved.tolist()), "fit": False}, links))
    placed = mk.select_atoms(read_structure(manager.project_dir(puid) / done["outputs"][0]["path"]))["xyz"]
    assert np.allclose(placed, ft.apply(moved, xyz), atol=2e-3)
    done = _done(manager, puid, manager.create_job(puid, "fit_in_map", {"matrix": json.dumps(moved.tolist()), "resolution": 6,
                                                                          "metric": "correlation"}, links))
    assert done["outputs"][0]["meta"]["fit"]["correlation"] > 0.7
    placed = mk.select_atoms(read_structure(manager.project_dir(puid) / done["outputs"][0]["path"]))["xyz"]
    assert float(np.sqrt(((placed - xyz) ** 2).sum(1).mean())) < 0.5  # simulated map, then the atoms
    metrics = next(s for s in manager.job_detail(puid, done["uid"])["report"]["sections"] if s["kind"] == "metrics")
    assert "Then on the atoms" in str(metrics) and "Then on the atoms" in log_of(manager, puid, done["uid"])
    # a mask placed: on the grid of the target map, values kept within 0-1
    mask_links = {"moving_map": {"job": imp, "output": "mask"}, "map": {"job": imp, "output": "map_sharp"}}
    done = _done(manager, puid, manager.create_job(puid, "fit_in_map", {"matrix": json.dumps(ft.translation([2, 0, 0]).tolist()),
                                                                          "fit": False}, mask_links))
    out = done["outputs"][0]
    mask = MapVolume.read(manager.project_dir(puid) / out["path"])
    src = MapVolume.read(synthetic["mask"])
    assert out["type"] == "mask" and mask.data.shape == src.data.shape and 0 <= mask.data.min() and mask.data.max() <= 1
    assert np.allclose(mask.data[:, :, 12:40], src.data[:, :, 10:38], atol=1e-5)  # shifted by 2 voxels along x
    # inputs checked before queueing
    bad = manager.create_job(puid, "fit_in_map", {"metric": "cam"}, {"model": {"job": mdl, "output": "model"}})
    text = " ".join(manager.queue_problems(manager.job(puid, bad["uid"])))
    assert "target map" in text and "Correlation needs a resolution" in text


def test_fit_endpoint_for_the_viewer(manager, synthetic):
    from cryoplug.server.app import create_app
    puid, imp, mdl = _project(manager, synthetic, "Viewer fit")
    map_path = next(o for o in manager.job(puid, imp)["outputs"] if o["name"] == "map_sharp")["path"]
    model_path = next(o for o in manager.job(puid, mdl)["outputs"] if o["name"] == "model")["path"]
    xyz = mk.select_atoms(build_helix())["xyz"]
    moved = ft.translation([1.5, -1.0, 1.2]) @ ft.rotation([0, 1, 1], 8, xyz.mean(0))
    app = create_app(manager.config, start_scheduler=False, manager=manager)
    with TestClient(app, base_url="http://localhost:39500") as client:
        body = {"target": map_path, "moving": {"kind": "model", "path": model_path, "matrix": moved.tolist()}}
        r = client.post(f"/api/projects/{puid}/fit", json=body)
        assert r.status_code == 200, r.text
        got = np.array(r.json()["matrix"])
        assert _rmsd(got, xyz) < 0.5 and r.json()["stats"]["steps"] > 0 and "Fit" in r.json()["message"]
        # the target map moved in the viewer too: the fitted model follows it
        tm = ft.translation([5.0, 0, 0])
        r = client.post(f"/api/projects/{puid}/fit", json={**body, "target_matrix": tm.tolist(),
                                                            "moving": {**body["moving"], "matrix": (tm @ moved).tolist()}})
        assert r.status_code == 200 and _rmsd(np.linalg.inv(tm) @ np.array(r.json()["matrix"]), xyz) < 0.5
        r = client.post(f"/api/projects/{puid}/fit", json={**body, "search": 4, "radius": 3})
        assert r.status_code == 200 and len(r.json()["fits"]) >= 1 and r.json()["polish"] is None
        r = client.post(f"/api/projects/{puid}/fit", json={**body, "resolution": 6, "metric": "correlation", "polish": True})
        assert r.status_code == 200 and r.json()["polish"]["stats"]["steps"] > 0 and "Then on the atoms" in r.json()["message"]
        assert _rmsd(np.array(r.json()["matrix"]), xyz) < 0.5
        assert client.post(f"/api/projects/{puid}/fit", json={**body, "metric": "nope"}).status_code == 400
        assert client.post(f"/api/projects/{puid}/fit", json={**body, "metric": "correlation"}).status_code == 400
        outside = client.post(f"/api/projects/{puid}/fit", json={**body, "target": "../../etc/passwd"})
        assert outside.status_code == 404
