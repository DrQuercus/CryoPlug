"""Mask creation: soft edge geometry, selections, automatic threshold, island removal and the job itself."""
from __future__ import annotations

import gemmi
import numpy as np
import pytest

from conftest import build_helix, log_of, run_until_done
from cryoplug import masks as mk
from cryoplug.mrc import MapVolume


def test_soft_edge_follows_the_dilation_and_the_raised_cosine():
    core = np.zeros((41, 41, 41), dtype=bool)
    core[20, 20, 20] = True
    voxel = np.array([1.0, 1.0, 1.0])
    mask = mk.soft_edge(core, voxel, dilate=4.0, soft=6.0)
    line = mask[20, 20, 20:]  # distance = index along x
    assert np.all(line[:5] == 1.0)  # within the dilation radius
    assert line[7] == pytest.approx(0.5, abs=1e-6)  # middle of the soft edge (4 + 3 Å)
    assert np.all(np.diff(line[4:11]) < 0) and np.all(line[10:] == 0.0)
    assert mask.min() >= 0.0 and mask.max() == 1.0


def test_big_boxes_get_the_same_edge_from_a_binned_distance_map():
    core = np.zeros((60, 60, 60), dtype=bool)
    core[25:35, 20:40, 28:32] = True
    voxel = np.array([1.2, 1.2, 1.2])
    exact = mk.distance_from(core, voxel, max_side=1000)
    coarse = mk.distance_from(core, voxel, max_side=20)  # binned by 3
    near = exact < 15
    assert np.abs(exact - coarse)[near].max() < 3 * 1.2  # within one binned voxel
    assert coarse[core].max() < 3 * 1.2


def test_selection_syntax():
    assert mk.parse_selection("A, B C:10-250 /D:-5--1") == [("A", None, None), ("B", None, None), ("C", 10, 250), ("D", -5, -1)]
    assert mk.parse_selection("") == []
    with pytest.raises(ValueError, match="Cannot read"):
        mk.parse_selection("A:10-x")
    st = build_helix()
    whole = mk.select_atoms(st)
    part = mk.select_atoms(st, "A:1-10", "A:5")
    assert len(part["xyz"]) == 9 * 5 and whole["chains"] == ["A"]
    assert part["mass_da"] < whole["mass_da"] and mk.select_atoms(st, "Z")["missing_chains"] == ["Z"]


@pytest.mark.parametrize("res, max_side, tol", [(3.0, 320, 0.08), (10.0, 320, 0.05), (25.0, 20, 0.06)])
def test_molmap_follows_the_chimerax_formula(monkeypatch, res, max_side, tol):
    """The fast molmap (atoms spread on a finer grid for narrow Gaussians, the map's grid, or a binned grid for big boxes)
    against the sum of Gaussians ChimeraX computes, and the contour enclosing 95 % of the density."""
    monkeypatch.setattr(mk, "MOLMAP_MAX_SIDE", max_side)
    rng = np.random.default_rng(3)
    vol = MapVolume(data=np.zeros((48, 52, 56), np.float32), voxel=np.array([1.1, 1.0, 0.9]), origin=np.array([5.0, -3.0, 2.0]))
    xyz = vol.origin + rng.uniform(12, 40, size=(30, 3))
    xyz[0] = [3.0, 20.0, 25.0]  # just outside the box: its tail counts inside
    weights = rng.choice([6.0, 7.0, 8.0, 16.0], size=30)
    z, y, x = np.indices(vol.data.shape)
    grid = [vol.origin[0] + x * vol.voxel[0], vol.origin[1] + y * vol.voxel[1], vol.origin[2] + z * vol.voxel[2]]
    sigma = res * mk.SIGMA_FACTOR
    exact = np.zeros(vol.data.shape)
    for p, w in zip(xyz, weights):
        r2 = sum((g - c) ** 2 for g, c in zip(grid, p))
        exact += np.where(r2 <= (5 * sigma) ** 2, w * np.exp(-r2 / (2 * sigma ** 2)), 0.0)
    exact *= (2 * np.pi) ** -1.5 * sigma ** -3  # ChimeraX normalises: each atom integrates to its atomic number
    fast = mk.molmap(vol, xyz, weights, res)
    assert np.abs(fast - exact).max() < tol * exact.max()
    level = mk.level_enclosing(fast, 0.95)
    assert fast[fast >= level].sum() == pytest.approx(0.95 * fast[fast > 0].sum(), rel=0.01)
    assert np.mean((fast >= level) == (exact >= mk.level_enclosing(exact, 0.95))) > 0.99
    far = mk.molmap(vol, xyz + 500.0, weights, res)  # atoms far outside the box
    assert not far.any() and mk.level_enclosing(far, 0.95) is None


def test_molmap_weights_are_atomic_numbers():
    st = build_helix(4)
    assert mk.select_atoms(st)["weights"].tolist() == [7, 6, 6, 8, 6] * 4
    res = st[0]["A"][0]
    alt = gemmi.Atom()
    alt.name, alt.element, alt.pos, alt.occ, alt.altloc = "CB", gemmi.Element("C"), res[4].pos, 0.4, "B"
    res[4].altloc, res[4].occ = "A", 0.6
    res.add_atom(alt)
    assert mk.select_atoms(st)["weights"][:6].tolist() == pytest.approx([7, 6, 6, 8, 3.6, 2.4])  # conformers share the atom


def test_otsu_and_mass_levels():
    rng = np.random.default_rng(1)
    values = np.concatenate([rng.normal(0, 0.1, 90_000), rng.normal(1, 0.1, 10_000)])
    assert 0.3 < mk.otsu_threshold(values) < 0.7
    level = mk.level_for_mass(values, values.size, mass_da=10_000 / mk.A3_PER_DA, voxel_volume=1.0)
    assert np.count_nonzero(values >= level) == pytest.approx(10_000, rel=0.01)


def test_island_removal():
    core = np.zeros((30, 30, 30), dtype=bool)
    core[5:15, 5:15, 5:15] = True  # 1000 voxels
    core[25, 25, 25] = True  # dust
    cleaned, removed = mk.remove_islands(core, np.ones(3), min_mass_kda=0.1)
    assert removed == 1 and cleaned.sum() == 1000
    largest, removed = mk.remove_islands(core, np.ones(3), keep_largest=True)
    assert removed == 1 and largest.sum() == 1000


def _mask_job(manager, puid, imp, params, **inputs):
    links = {"map": {"job": imp, "output": "map_sharp"}, **inputs}
    job = manager.create_job(puid, "create_mask", params, links)
    manager.queue_job(puid, job["uid"])
    run_until_done(manager, puid, [job["uid"]])
    done = manager.job(puid, job["uid"])
    assert done["status"] == "completed", f"{done['error']}\n{log_of(manager, puid, job['uid'])}"
    out = done["outputs"][0]
    return done, MapVolume.read(manager.project_dir(puid) / out["path"])


def test_create_mask_job(manager, synthetic):
    p = manager.create_project("Masks")
    puid = p["uid"]
    imp = manager.create_job(puid, "import_cryosparc", {"job_dir": str(synthetic["cryosparc_job"])})
    mdl = manager.create_job(puid, "import_model", {"source": "file", "path": str(synthetic["model"])})
    for j in (imp, mdl):
        manager.queue_job(puid, j["uid"])
    run_until_done(manager, puid, [imp["uid"], mdl["uid"]])
    ref = MapVolume.read(synthetic["map"])
    xyz = mk.select_atoms(build_helix())["xyz"]

    # from the density, automatic threshold (Otsu), soft edge
    done, density = _mask_job(manager, puid, imp["uid"], {"lowpass": 8, "dilate": 2, "soft": 4})
    assert density.data.shape == ref.data.shape and np.allclose(density.voxel, ref.voxel)
    assert density.data.min() >= 0 and density.data.max() == 1
    assert np.any((density.data > 0.05) & (density.data < 0.95))  # soft edge present
    assert np.mean(density.interpolate(xyz) >= 0.5) > 0.95  # the molecule is inside
    kinds = [s["kind"] for s in manager.job_detail(puid, done["uid"])["report"]["sections"]]
    assert {"metrics", "plot", "image", "text"} <= set(kinds)
    assert done["outputs"][0]["type"] == "mask" and done["outputs"][0].get("thumbnail")

    # around part of the model, then subtracted from the whole molecule
    model = {"model": {"job": mdl["uid"], "output": "model"}}
    done, part = _mask_job(manager, puid, imp["uid"], {"source": "model_atoms", "chains": "A:1-8", "dilate": 1, "soft": 3}, **model)
    first = mk.select_atoms(build_helix(), "A:1-8")["xyz"]
    assert np.all(part.interpolate(first) >= 0.99)
    assert mk.statistics(part.data, part.voxel)["volume_nm3"] < mk.statistics(density.data, density.voxel)["volume_nm3"]
    mask_link = {"mask": {"job": done["uid"], "output": "mask"}}
    _done, rest = _mask_job(manager, puid, imp["uid"], {"source": "model_atoms", "combine": "subtract", "dilate": 1, "soft": 3},
                            **model, **mask_link)
    assert np.all(rest.interpolate(first) <= 0.01)
    last = mk.select_atoms(build_helix(), "A:15-22")["xyz"]
    assert np.all(rest.interpolate(last) >= 0.99)

    # density simulated from the model (molmap), cut at the contour enclosing 95 % of it; the simulated map kept
    done, mm = _mask_job(manager, puid, imp["uid"], {"source": "model_map", "molmap_resolution": 8, "dilate": 1, "soft": 3}, **model)
    assert np.mean(mm.interpolate(xyz) >= 0.5) > 0.99
    outs = {o["name"]: o for o in done["outputs"]}
    sim = MapVolume.read(manager.project_dir(puid) / outs["model_map"]["path"])
    assert outs["model_map"]["type"] == "map" and outs["model_map"]["meta"]["resolution"] == 8
    assert sim.data.shape == ref.data.shape
    weights = mk.select_atoms(build_helix())["weights"]
    assert sim.data.sum() * np.prod(sim.voxel) == pytest.approx(weights.sum(), rel=0.02)  # each atom integrates to its Z
    metrics = next(s for s in manager.job_detail(puid, done["uid"])["report"]["sections"] if s["kind"] == "metrics")
    assert "enclosing 95 % of its density" in str(metrics) and outs["mask"]["meta"]["molmap_resolution"] == 8

    # density near the model, threshold from the mass of the selected atoms
    done, near = _mask_job(manager, puid, imp["uid"], {"source": "density_near_model", "near_distance": 4}, **model)
    metrics = next(s for s in manager.job_detail(puid, done["uid"])["report"]["sections"] if s["kind"] == "metrics")
    assert "mass of the selected atoms" in str(metrics)
    assert np.mean(near.interpolate(xyz) >= 0.5) > 0.9


def test_mask_job_checks_its_inputs(manager, synthetic):
    p = manager.create_project("Mask checks")
    imp = manager.create_job(p["uid"], "import_cryosparc", {"job_dir": str(synthetic["cryosparc_job"])})
    job = manager.create_job(p["uid"], "create_mask", {"source": "model_atoms", "chains": "A:x"},
                             {"map": {"job": imp["uid"], "output": "map_sharp"}})
    text = " ".join(manager.queue_problems(manager.job(p["uid"], job["uid"])))
    assert "Connect a model" in text and "Cannot read" in text
    job = manager.create_job(p["uid"], "create_mask", {"source": "model_map"}, {"map": {"job": imp["uid"], "output": "map_sharp"}})
    assert "Connect a model" in " ".join(manager.queue_problems(manager.job(p["uid"], job["uid"])))
