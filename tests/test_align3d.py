"""Align 3D maps: the frame found on the maps, applied to the half maps, the mask and the particle poses."""
from __future__ import annotations

import mrcfile
import numpy as np
import pytest
from conftest import log_of, run_until_done

from cryoplug import particles as pt
from cryoplug import poses as ps
from cryoplug.mrc import MapVolume


def _run(manager, puid, jtype, params, inputs=None, ok=True):
    job = manager.create_job(puid, jtype, params, inputs or {})
    manager.queue_job(puid, job["uid"])
    run_until_done(manager, puid, [job["uid"]], timeout=300)
    done = manager.job(puid, job["uid"])
    if ok:
        assert done["status"] == "completed", f"{done['error']}\n{log_of(manager, puid, job['uid'])}"
    return done


def _outs(job):
    return {o["name"]: o for o in job["outputs"]}


def _chiral(d=32):
    zz, yy, xx = (np.indices((d, d, d)) - d // 2).astype(float) * (32 / d)
    blobs = [((6, 0, 0), 3.0, 1.0), ((-4, 7, 1), 2.2, 0.8), ((0, -3, 8), 1.8, 0.6), ((-2, -6, -5), 2.6, 0.9)]
    return sum(a * np.exp(-((xx - x) ** 2 + (yy - y) ** 2 + (zz - z) ** 2) / (2 * s * s))
               for (x, y, z), s, a in blobs).astype(np.float32)


def _project(v, rot, t=(0.0, 0.0)):
    """image(x) = Σ_z v(Rᵀ (x + t, z)): the projection convention of the poses, in pixels about the centre."""
    from scipy import ndimage
    n = v.shape[0]
    g = (np.indices((n, n, n)) - n // 2).astype(float)
    xyz = np.stack([g[2] + t[0], g[1] + t[1], g[0]]).reshape(3, -1)
    src = rot.T @ xyz
    vals = ndimage.map_coordinates(v, np.stack([src[2], src[1], src[0]]) + n // 2, order=1).reshape(n, n, n)
    return vals.sum(axis=0)


def _particles_star(path, stack, euler, origin_a, apix, box):
    rows = "".join(f"{i + 1:06d}@{stack} 1 {e[0]:.6f} {e[1]:.6f} {e[2]:.6f} {o[0]:.6f} {o[1]:.6f} {i % 2 + 1}\n"
                   for i, (e, o) in enumerate(zip(euler, origin_a)))
    path.write_text("data_optics\n\nloop_\n_rlnOpticsGroup #1\n_rlnOpticsGroupName #2\n_rlnImagePixelSize #3\n"
                    f"_rlnImageSize #4\n_rlnImageDimensionality #5\n_rlnVoltage #6\n_rlnSphericalAberration #7\n"
                    f"_rlnAmplitudeContrast #8\n1 opticsGroup1 {apix} {box} 2 300 2.7 0.1\n\n"
                    "data_particles\n\nloop_\n_rlnImageName #1\n_rlnOpticsGroup #2\n_rlnAngleRot #3\n_rlnAngleTilt #4\n"
                    "_rlnAnglePsi #5\n_rlnOriginXAngst #6\n_rlnOriginYAngst #7\n_rlnRandomSubset #8\n" + rows)


@pytest.mark.parametrize("search", ["global", "local"])
def test_align_3d_maps_moves_maps_and_poses(manager, search):
    p = manager.create_project(f"Align {search}")
    puid = p["uid"]
    pdir = manager.project_dir(puid)
    d, apix = 32, 2.0
    ref = _chiral(d)
    angle = [[0.6, -0.4, 0.9]] if search == "global" else [[0.05, -0.08, 0.06]]
    true = ps.frame_dict(ps.rotvec_to_matrix(np.array(angle))[0], False,
                         np.array([0.04, -0.02, 0.03]) if search == "global" else np.array([0.02, 0.0, -0.015]))
    moving = ps.frame_volume(ref, true, inverse=True)  # the same particle in another frame
    rng = np.random.default_rng(1)
    MapVolume(data=ref, voxel=np.full(3, apix)).write(pdir / "ref.mrc")
    MapVolume(data=moving, voxel=np.full(3, apix)).write(pdir / "mov.mrc")
    for h in (1, 2):
        MapVolume(data=moving + rng.normal(0, 0.01, moving.shape).astype(np.float32), voxel=np.full(3, apix)).write(pdir / f"mov_half{h}.mrc")
    mask = (np.sqrt(((np.indices((d,) * 3) - d // 2) ** 2).sum(0)) < 10).astype(np.float32)
    MapVolume(data=mask, voxel=np.full(3, apix)).write(pdir / "mov_mask.mrc")
    # particles refined against the moving map
    n = 10
    rot, trans = ps.uniform_rotations(n), rng.normal(0, 0.03, (n, 2))
    with mrcfile.new(str(pdir / "parts.mrcs"), overwrite=True) as mrc:
        mrc.set_data(np.zeros((n, d, d), np.float32))
    _particles_star(pdir / "parts.star", "parts.mrcs", ps.matrix_to_relion(rot), trans * d * apix, apix, d)

    imp = _run(manager, puid, "import_maps", {"map": str(pdir / "mov.mrc"), "half_map_a": str(pdir / "mov_half1.mrc"),
                                             "half_map_b": str(pdir / "mov_half2.mrc"), "mask": str(pdir / "mov_mask.mrc"),
                                             "compute_fsc": False})
    imp_ref = _run(manager, puid, "import_maps", {"map": str(pdir / "ref.mrc"), "compute_fsc": False})
    imp_parts = _run(manager, puid, "import_particles", {"path": str(pdir / "parts.star")})
    io = _outs(imp)
    job = _run(manager, puid, "align_3d_maps", {"search": search, "rotations": 1500, "search_box": 24},
               {"map": {"job": imp["uid"], "output": "map"}, "reference": {"job": imp_ref["uid"], "output": "map"},
                "half_maps": {"job": imp["uid"], "output": "half_maps"},
                "mask": {"job": imp["uid"], "output": next(k for k, o in io.items() if o["type"] == "mask")},
                "particles": {"job": imp_parts["uid"], "output": "particles"}})
    out = _outs(job)
    assert {"map", "half_maps", "mask", "particles"} <= set(out)
    hl = {h["label"]: h["value"] for h in job["highlights"]}
    assert float(hl["Correlation"]) > 0.95
    aligned = MapVolume.read(pdir / out["map"]["path"]).data
    assert np.corrcoef(aligned.ravel(), ref.ravel())[0, 1] > 0.95
    half = MapVolume.read(pdir / out["half_maps"]["files"][1]).data
    assert np.corrcoef(half.ravel(), ref.ravel())[0, 1] > 0.95
    moved_mask = MapVolume.read(pdir / out["mask"]["path"]).data
    assert moved_mask.min() >= 0 and moved_mask.max() <= 1 and abs(moved_mask.sum() - mask.sum()) / mask.sum() < 0.1
    frame = out["map"]["meta"]["frame"]
    assert ps.rotation_angle(np.asarray(frame["rotation"]).T @ np.asarray(true["rotation"])) < 3

    # the poses now refer to the reference: projections of the reference with them = those of the moving map before
    star = pdir / out["particles"]["path"]
    euler = np.array([[float(v) for v in pt.star_column(star, c)] for c in ("_rlnAngleRot", "_rlnAngleTilt", "_rlnAnglePsi")]).T
    origin = np.array([[float(v) for v in pt.star_column(star, c)] for c in ("_rlnOriginXAngst", "_rlnOriginYAngst")]).T
    rot2, trans2 = ps.relion_to_matrix(euler), origin / apix / d
    expected, expected_t = ps.apply_frame(rot, trans, true)
    assert np.max(ps.rotation_angle(np.einsum("nji,njk->nik", rot2, expected))) < 3
    assert np.abs(trans2 - expected_t).max() * d < 0.6  # pixels
    for i in range(3):
        a, b = _project(moving, rot[i], trans[i] * d), _project(ref, rot2[i], trans2[i] * d)
        assert np.corrcoef(a.ravel(), b.ravel())[0, 1] > 0.97
    assert pt.star_column(star, "_rlnRandomSubset") == [str(i % 2 + 1) for i in range(n)]  # half sets kept
    assert out["particles"]["meta"]["n_particles"] == n


def test_align_3d_maps_finds_the_mirror(manager):
    p = manager.create_project("Align mirror")
    puid = p["uid"]
    pdir = manager.project_dir(puid)
    ref = _chiral(28)
    true = ps.frame_dict(ps.rotvec_to_matrix(np.array([[-0.5, 0.8, 0.2]]))[0], True, np.array([0.02, 0.0, -0.02]))
    MapVolume(data=ref, voxel=np.full(3, 2.0)).write(pdir / "ref.mrc")
    MapVolume(data=ps.frame_volume(ref, true, inverse=True), voxel=np.full(3, 2.0)).write(pdir / "mov.mrc")
    imp = _run(manager, puid, "import_maps", {"map": str(pdir / "mov.mrc"), "compute_fsc": False})
    imp_ref = _run(manager, puid, "import_maps", {"map": str(pdir / "ref.mrc"), "compute_fsc": False})
    inputs = {"map": {"job": imp["uid"], "output": "map"}, "reference": {"job": imp_ref["uid"], "output": "map"}}
    same_hand = _run(manager, puid, "align_3d_maps", {"rotations": 1200, "search_box": 20}, inputs)
    both = _run(manager, puid, "align_3d_maps", {"rotations": 1200, "search_box": 20, "try_mirror": True}, inputs)
    corr = lambda j: float({h["label"]: h["value"] for h in j["highlights"]}["Correlation"])  # noqa: E731
    assert _outs(both)["map"]["meta"]["frame"]["mirror"] is True and corr(both) > 0.95 > corr(same_hand)
    assert {h["label"]: h["value"] for h in both["highlights"]}["Mirror"] == "yes"


def test_orientation_diagnostics_cfar_and_scf(manager):
    """Directional FSC with the particles: cFAR from the conical FSCs and SCF from the viewing directions, as CryoSPARC's
    Orientation Diagnostics (side views only: SCF 0.81, Baldwin & Lyumkis 2019)."""
    p = manager.create_project("Orientation")
    puid = p["uid"]
    pdir = manager.project_dir(puid)
    rng = np.random.default_rng(0)
    n = 40

    def halves(name, sigma_z):
        sig = rng.normal(size=(n, n, n)).astype(np.float32)
        f = np.fft.rfftn(sig)
        kz = np.fft.fftfreq(n)[:, None, None]
        f *= np.exp(-(kz ** 2) / (2 * sigma_z ** 2))  # less signal along z when sigma_z is small
        sig = np.fft.irfftn(f, s=sig.shape, axes=(0, 1, 2)).astype(np.float32)
        sig /= sig.std()
        for h in (1, 2):
            MapVolume(data=sig + rng.normal(0, 0.5, sig.shape).astype(np.float32), voxel=np.full(3, 1.0)).write(pdir / f"{name}_{h}.mrc")
        return _run(manager, puid, "import_maps", {"half_map_a": str(pdir / f"{name}_1.mrc"), "half_map_b": str(pdir / f"{name}_2.mrc"),
                                                    "compute_fsc": False})

    aniso, iso = halves("aniso", 0.05), halves("iso", 10.0)
    phi = rng.uniform(-180, 180, 3000)
    euler = np.stack([phi, np.full(3000, 90.0), rng.uniform(-180, 180, 3000)], 1)
    with mrcfile.new(str(pdir / "side.mrcs"), overwrite=True) as mrc:
        mrc.set_data(np.zeros((3000, 8, 8), np.float32))
    _particles_star(pdir / "side.star", "side.mrcs", euler, np.zeros((3000, 2)), 1.0, 8)
    parts = _run(manager, puid, "import_particles", {"path": str(pdir / "side.star")})
    job = _run(manager, puid, "directional_fsc", {"sampling": 30},
               {"half_maps": {"job": aniso["uid"], "output": "half_maps"}, "particles": {"job": parts["uid"], "output": "particles"}})
    hl = {h["label"]: h for h in job["highlights"]}
    assert float(hl["cFAR"]["value"]) < 0.5 and hl["cFAR"]["status"] != "good"
    assert abs(float(hl["SCF"]["value"]) - 0.81) < 0.02 and hl["SCF"]["status"] == "warn"
    report = _outs(job)["report"]["meta"]
    assert report["cfar"] < 0.5 and abs(report["scf"] - 0.81) < 0.02
    sections = __import__("json").loads((pdir / job["uid"] / "report.json").read_text())["sections"]
    sampling = next(s for s in sections if s["title"] == "Fourier sampling by direction")
    top = sampling["values"][0]  # elevation 90°: along z, sampled by every side view
    assert min(top) > 1.5 and min(min(row) for row in sampling["values"]) < 1.0
    even = _run(manager, puid, "directional_fsc", {"sampling": 30}, {"half_maps": {"job": iso["uid"], "output": "half_maps"}})
    assert float({h["label"]: h["value"] for h in even["highlights"]}["cFAR"]) > 0.7
    assert "SCF" not in {h["label"] for h in even["highlights"]}


def test_check_particles_sets_corrupt_images_aside(manager):
    p = manager.create_project("Check particles")
    puid = p["uid"]
    pdir = manager.project_dir(puid)
    rng = np.random.default_rng(4)
    imgs = rng.normal(0, 1, (30, 16, 16)).astype(np.float32)
    imgs[3, 2, 2] = np.nan
    imgs[7] = 0.0
    imgs[11] *= 60.0  # extreme spread
    with mrcfile.new(str(pdir / "good.mrcs"), overwrite=True) as mrc:
        mrc.set_data(imgs)
    rows = [f"{i + 1:06d}@good.mrcs" for i in range(30)] + ["000031@good.mrcs", "000001@gone.mrcs"]
    (pdir / "check.star").write_text(
        "data_optics\n\nloop_\n_rlnOpticsGroup #1\n_rlnOpticsGroupName #2\n_rlnImagePixelSize #3\n_rlnImageSize #4\n"
        "_rlnImageDimensionality #5\n_rlnVoltage #6\n_rlnSphericalAberration #7\n_rlnAmplitudeContrast #8\n"
        "1 opticsGroup1 1.0 16 2 300 2.7 0.1\n\ndata_particles\n\nloop_\n_rlnImageName #1\n_rlnOpticsGroup #2\n"
        + "".join(f"{r} 1\n" for r in rows))
    parts = _run(manager, puid, "import_particles", {"path": str(pdir / "check.star")})
    job = _run(manager, puid, "check_particles", {"outlier_sigma": 8}, {"particles": {"job": parts["uid"], "output": "particles"}})
    out = _outs(job)
    assert out["particles"]["meta"]["n_particles"] == 27 and out["rejected"]["meta"]["n_particles"] == 5
    table = next(s for s in __import__("json").loads((pdir / job["uid"] / "report.json").read_text())["sections"]
                 if s["title"] == "Checks")
    counts = {r[0]: r[1] for r in table["rows"]}
    assert counts == {"Stack or image missing": "2", "NaN or infinite values": "1", "Blank (constant) image": "1",
                      "Outlier (mean or spread)": "1", "Fine": "27"}
    names = pt.star_column(pdir / out["particles"]["path"], "_rlnImageName")  # read from the project folder, as RELION
    assert all((pdir / n.partition("@")[2]).is_file() for n in names) and names[0] == "000001@good.mrcs"
    plain = _run(manager, puid, "check_particles", {}, {"particles": {"job": parts["uid"], "output": "particles"}})
    assert _outs(plain)["particles"]["meta"]["n_particles"] == 28  # the outlier kept without the test


def test_average_power_spectra_in_the_class_frame(manager):
    """Particles of a striped class (layer lines along y) seen at random in-plane angles, made as RELION's projector
    does (image(p) = class(Aᵀ p)): their power spectra turned back into the class frame add up on the ky axis."""
    from scipy import ndimage
    p = manager.create_project("Power spectra")
    puid = p["uid"]
    pdir = manager.project_dir(puid)
    n, rng = 48, np.random.default_rng(5)
    yy, xx = np.indices((n, n)) - n // 2
    ref = (np.cos(2 * np.pi * yy / 6.0) * np.exp(-(xx ** 2 + yy ** 2) / (2 * 14.0 ** 2))).astype(np.float32)
    psi = rng.uniform(-180, 180, 60)
    imgs = []
    for a in np.radians(psi):
        rx = np.cos(a) * xx - np.sin(a) * yy  # Aᵀ (x, y)
        ry = np.sin(a) * xx + np.cos(a) * yy
        imgs.append(ndimage.map_coordinates(ref, [ry + n // 2, rx + n // 2], order=1) + rng.normal(0, 0.3, (n, n)))
    with mrcfile.new(str(pdir / "fil.mrcs"), overwrite=True) as mrc:
        mrc.set_data(np.stack(imgs).astype(np.float32))
    rows = "".join(f"{i + 1:06d}@fil.mrcs 1 1 {a:.4f}\n" for i, a in enumerate(psi))
    (pdir / "fil.star").write_text(
        "data_optics\n\nloop_\n_rlnOpticsGroup #1\n_rlnOpticsGroupName #2\n_rlnImagePixelSize #3\n_rlnImageSize #4\n"
        "_rlnImageDimensionality #5\n_rlnVoltage #6\n_rlnSphericalAberration #7\n_rlnAmplitudeContrast #8\n"
        f"1 opticsGroup1 2.0 {n} 2 300 2.7 0.1\n\ndata_particles\n\nloop_\n_rlnImageName #1\n_rlnOpticsGroup #2\n"
        "_rlnClassNumber #3\n_rlnAnglePsi #4\n" + rows)
    parts = _run(manager, puid, "import_particles", {"path": str(pdir / "fil.star")})
    job = _run(manager, puid, "average_power_spectra", {}, {"particles": {"job": parts["uid"], "output": "particles"}})
    with mrcfile.open(str(pdir / _outs(job)["spectra"]["path"])) as mrc:
        ps = np.asarray(mrc.data)[0]
    c = n // 2
    on_axis = ps[:, c - 1:c + 2].sum() / ps.sum()
    assert on_axis > 0.5  # the layer lines line up on the ky axis
    peak = np.unravel_index(int(np.argmax(ps)), ps.shape)
    assert abs(peak[1] - c) <= 1 and abs(abs(peak[0] - c) - n / 6.0) <= 1.5
    # the same particles without turning their spectra: no line
    from cryoplug.jobs.relion_particles import aligned_power_spectrum
    plain = sum(aligned_power_spectrum(im, 0.0) for im in imgs)
    assert plain[:, c - 1:c + 2].sum() / plain.sum() < on_axis / 2
    assert _run(manager, puid, "average_power_spectra", {"classes": "4"}, {"particles": {"job": parts["uid"], "output": "particles"}},
                ok=False)["status"] == "failed"


def test_recentre_on_a_region(manager):
    """A blob away from the centre moved to the centre of the box, the particle shifts changed the same way."""
    p = manager.create_project("Recentre")
    puid = p["uid"]
    pdir = manager.project_dir(puid)
    d, apix = 32, 2.0
    zz, yy, xx = np.indices((d, d, d)) - d // 2
    vol = (np.exp(-((xx - 6) ** 2 + (yy + 4) ** 2 + (zz - 3) ** 2) / 8.0) + 0.6 * np.exp(-(xx ** 2 + yy ** 2 + zz ** 2) / 18.0)).astype(np.float32)
    mask = (((xx - 6) ** 2 + (yy + 4) ** 2 + (zz - 3) ** 2) < 16).astype(np.float32)
    MapVolume(data=vol, voxel=np.full(3, apix)).write(pdir / "map.mrc")
    MapVolume(data=mask, voxel=np.full(3, apix)).write(pdir / "mask.mrc")
    rng = np.random.default_rng(2)
    n = 8
    rot, trans = ps.uniform_rotations(n), rng.normal(0, 0.02, (n, 2))
    with mrcfile.new(str(pdir / "p.mrcs"), overwrite=True) as mrc:
        mrc.set_data(np.zeros((n, d, d), np.float32))
    _particles_star(pdir / "p.star", "p.mrcs", ps.matrix_to_relion(rot), trans * d * apix, apix, d)
    imp = _run(manager, puid, "import_maps", {"map": str(pdir / "map.mrc"), "mask": str(pdir / "mask.mrc"), "compute_fsc": False})
    parts = _run(manager, puid, "import_particles", {"path": str(pdir / "p.star")})
    mask_out = next(k for k, o in _outs(imp).items() if o["type"] == "mask")
    job = _run(manager, puid, "recentre_particles", {"centre": "mask"},
               {"map": {"job": imp["uid"], "output": "map"}, "mask": {"job": imp["uid"], "output": mask_out},
                "particles": {"job": parts["uid"], "output": "particles"}})
    out = _outs(job)
    moved_mask = MapVolume.read(pdir / out["mask"]["path"]).data
    from cryoplug.jobs.align3d import centre_of_mass
    assert np.abs(centre_of_mass(moved_mask) - d // 2).max() < 0.3  # the region now at the centre
    moved = MapVolume.read(pdir / out["map"]["path"]).data
    star = pdir / out["particles"]["path"]
    euler = np.array([[float(v) for v in pt.star_column(star, c)] for c in ("_rlnAngleRot", "_rlnAngleTilt", "_rlnAnglePsi")]).T
    origin = np.array([[float(v) for v in pt.star_column(star, c)] for c in ("_rlnOriginXAngst", "_rlnOriginYAngst")]).T
    rot2, trans2 = ps.relion_to_matrix(euler), origin / apix / d
    assert np.max(ps.rotation_angle(np.einsum("nji,njk->nik", rot2, rot))) < 0.01  # orientations unchanged
    r = np.sqrt(xx ** 2 + yy ** 2 + zz ** 2)
    for i in range(3):  # the same images from the moved map with the new shifts (inside the box)
        a, b = _project(vol * (r < 12), rot[i], trans[i] * d), _project(moved * (r < 12), rot2[i], trans2[i] * d)
        assert np.corrcoef(a.ravel(), b.ravel())[0, 1] > 0.9
    point = _run(manager, puid, "recentre_particles", {"centre": "point", "point": "22, 12, 19"}, {"map": {"job": imp["uid"], "output": "map"}})
    assert {h["label"]: h["value"] for h in point["highlights"]}["Shift"] == f"{np.sqrt(36 + 16 + 9) * apix:.1f} Å"
    bad = manager.create_job(puid, "recentre_particles", {"centre": "mask"}, {"map": {"job": imp["uid"], "output": "map"}})
    with pytest.raises(Exception, match="Connect the mask"):
        manager.queue_job(puid, bad["uid"])
