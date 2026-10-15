"""RELION: CryoSPARC to STAR conversion (pyem's conventions), grids of re-centred particles, and the RELION jobs
(local refinement, signal subtraction, focused classification, post-processing, reconstruction) with a fake RELION."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from conftest import log_of, run_until_done

from cryoplug import particles as pt
from cryoplug import poses as ps
from cryoplug import relion as rl
from cryoplug.mrc import MapVolume


def _run(manager, puid, jtype, params, inputs=None, ok=True):
    job = manager.create_job(puid, jtype, params, inputs or {})
    manager.queue_job(puid, job["uid"])
    run_until_done(manager, puid, [job["uid"]], timeout=180)
    done = manager.job(puid, job["uid"])
    if ok:
        assert done["status"] == "completed", f"{done['error']}\n{log_of(manager, puid, job['uid'])}"
    return done


def _outs(job):
    return {o["name"]: o for o in job["outputs"]}


# ------------------------------------------------------------------ library
def _rot2euler(r):
    """pyem's geom.rot2euler (Shoemake, RELION conventions), for reference."""
    eps = np.finfo(np.double).eps
    abs_sb = np.sqrt(r[0, 2] ** 2 + r[1, 2] ** 2)
    if abs_sb > 16 * eps:
        gamma = np.arctan2(r[1, 2], -r[0, 2])
        alpha = np.arctan2(r[2, 1], r[2, 0])
        sign_sb = np.sign(-r[0, 2]) / np.cos(gamma) if abs(np.sin(gamma)) < eps else (
            np.sign(r[1, 2]) if np.sin(gamma) > 0 else -np.sign(r[1, 2]))
        beta = np.arctan2(sign_sb * abs_sb, r[2, 2])
    else:
        alpha, beta, gamma = 0.0, (0.0 if r[2, 2] > 0 else np.pi), np.arctan2(-r[1, 0], r[0, 0])
    return np.degrees([alpha, beta, gamma])


def _expmap(e):
    """pyem's geom.expmap: the transpose of the usual Rodrigues rotation."""
    theta = np.linalg.norm(e)
    w = e / theta
    k = np.array([[0, w[2], -w[1]], [-w[2], 0, w[0]], [w[1], -w[0], 0]])
    return np.eye(3) + np.sin(theta) * k + (1 - np.cos(theta)) * k @ k


def test_cs_to_star_follows_pyem(cs_particles, tmp_path):
    main, through = pt.find_particle_files(cs_particles["refine"])
    data = pt.merged_particles(main, through)
    linker = rl.StackLinker(tmp_path / "J1" / "Particles", tmp_path)
    tables, info = rl.cs_to_star(data, cs_particles["root"], linker)
    optics, parts = tables
    assert info["n_particles"] == 300 and info["box"] == 32 and info["pixel_size"] == 2.0 and info["has_poses"]
    assert optics.value("rlnImageSize") == "32" and float(optics.value("rlnImagePixelSize")) == 2.0
    assert optics.value("rlnImageDimensionality") == "2" and optics.value("rlnCtfDataAreCtfPremultiplied") == "0"
    # Euler angles: those of pyem (rot2euler of its expmap), compared as rotations
    euler = np.column_stack([parts.floats(c) for c in ("rlnAngleRot", "rlnAngleTilt", "rlnAnglePsi")])
    for i in range(0, 300, 37):
        ref = _rot2euler(_expmap(np.asarray(data["alignments3D/pose"][i], dtype=np.float64)))
        assert np.allclose(ps.relion_to_matrix(euler[i:i + 1])[0], ps.relion_to_matrix(ref[None])[0], atol=1e-5)
    # origins in Å: CryoSPARC shift (pixels) × pixel size, same sign
    assert np.allclose(parts.floats("rlnOriginXAngst"), np.asarray(data["alignments3D/shift"])[:, 0] * 2.0, atol=1e-4)
    # CTF angles in degrees; defocus kept
    assert np.allclose(parts.floats("rlnDefocusU"), data["ctf/df1_A"], atol=1e-3)
    # image names: N@stack linked as .mrcs into the job folder, the leading '>' of CryoSPARC removed
    names = parts.column("rlnImageName")
    assert names[0].startswith("000001@J1/Particles/") and names[0].endswith(".mrcs")
    link = tmp_path / names[0].split("@")[1]
    assert link.is_symlink() and link.resolve() == (cs_particles["root"] / "J2/imported/stack_A.mrcs").resolve()
    assert len({n.split("@")[1] for n in names}) == 2
    # written and read back
    out = rl.write_star(tmp_path / "p.star", tables)
    back = rl.read_star(out)
    assert len(rl.particle_table(back)) == 300 and rl.grid_from_star(back) == (32, 2.0)


def test_grids_of_recentred_particles_are_exact():
    rng = np.random.default_rng(0)
    vol = rng.normal(size=(48, 48, 48)).astype(np.float32)
    frame = {"box": 24, "center": [5, -3, 2], "parent_box": 48, "pixel_size": 1.0}
    local = rl.to_grid(vol, 1.0, 24, 1.0, frame["center"])
    back = rl.to_parent(local, frame)
    zc, yc, xc = 24 + 2, 24 - 3, 24 + 5
    region = (slice(zc - 12, zc + 12), slice(yc - 12, yc + 12), slice(xc - 12, xc + 12))
    assert np.array_equal(back[region], vol[region]) and np.abs(back).sum() == pytest.approx(np.abs(back[region]).sum())
    mask = np.zeros((48, 48, 48), np.float32)
    mask[24, 20, 30] = 1
    assert np.allclose(rl.center_of_mass(mask), [6, -4, 0])
    assert [rl.good_box(x) for x in (65, 101, 127)] == [70, 108, 128]
    # another pixel size: the centre stays the centre
    z, y, x = np.indices((24, 24, 24)) - 12
    blob = np.exp(-(x ** 2 + y ** 2 + z ** 2) / 8.0).astype(np.float32)
    fine = rl.to_grid(blob, 2.0, 48, 1.0)
    assert np.unravel_index(np.argmax(fine), fine.shape) == (24, 24, 24)


def test_star_list_and_loop_tables(tmp_path):
    general = rl.StarTable("general", ["rlnFinalResolution", "rlnMaskName"], [["3.2", "mask file.mrc"]], loop=False)
    loop = rl.StarTable("fsc")
    loop.set_column("rlnResolution", [0.1, 0.2, 0.3])
    loop.set_column("rlnFourierShellCorrelationCorrected", [0.9, 0.5, 0.1])
    rl.write_star(tmp_path / "pp.star", [general, loop])
    res = rl.postprocess_results(tmp_path / "pp.star")
    assert res["resolution"] == 3.2 and res["curves"]["corrected"] == [0.9, 0.5, 0.1]
    assert rl.read_star(tmp_path / "pp.star")["general"].value("rlnMaskName") == "mask file.mrc"
    assert rl.resolution_at([0.1, 0.2, 0.3], [0.9, 0.5, 0.1]) == pytest.approx(1 / 0.2895, rel=1e-3)


# --------------------------------------------------------------------- jobs
@pytest.fixture()
def relion_project(manager, cs_particles, synthetic):
    p = manager.create_project("RELION")
    puid = p["uid"]
    parts = _run(manager, puid, "import_particles", {"path": str(cs_particles["refine"])})
    maps = _run(manager, puid, "import_cryosparc", {"job_dir": str(synthetic["cryosparc_job"]), "import_particles": False})
    return puid, parts, maps


def test_relion_focused_refinement_chain(manager, relion_project, tmp_path):
    puid, parts, maps = relion_project
    pdir = manager.project_dir(puid)
    ref = {"particles": {"job": parts["uid"], "output": "particles"}, "map": {"job": maps["uid"], "output": "map"},
           "mask": {"job": maps["uid"], "output": "mask"}}
    refine = _run(manager, puid, "relion_local_refine", {"mpi": 3, "threads": 2, "gpus": 1}, ref)
    o = _outs(refine)
    assert set(o) >= {"map", "half_maps", "map_sharp", "mask", "fsc", "particles"}
    log = log_of(manager, puid, refine["uid"])
    assert "mpirun -n 3" in log and "--auto_refine" in log and "--firstiter_cc" in log and "--solvent_correct_fsc" in log
    assert "--healpix_order 4 --auto_local_healpix_order 4" in log and "--gpu ''" in log
    assert o["map"]["meta"]["resolution"] == 3.6 and o["map"]["meta"]["relion_map"]
    assert o["particles"]["meta"]["relion"]["optimiser"].endswith("run_optimiser.star")
    assert refine["resources"]["num_cpus"] == 6 and refine["resources"]["num_gpus"] == 1
    # RELION runs from the project folder: the converted particles name stacks linked into the job folder
    commands = (pdir / refine["uid"] / "commands.sh").read_text()
    assert f"cd {pdir}" in commands and f"--i {refine['uid']}/particles.star" in commands

    # subtraction around an off-centre mask (a sphere 10, -6, 4 Å from the centre), re-centred and re-boxed
    z, y, x = (np.indices((48, 48, 48)) - 24).astype(float)
    sphere = (np.sqrt((x - 10) ** 2 + (y + 6) ** 2 + (z - 4) ** 2) < 7).astype(np.float32)
    MapVolume(data=sphere, voxel=np.ones(3)).write(tmp_path / "focus_mask.mrc")
    focus_mask = _run(manager, puid, "import_maps", {"mask": str(tmp_path / "focus_mask.mrc"), "compute_fsc": False})
    sub = _run(manager, puid, "relion_subtract", {"mpi": 1, "new_box": 16},
               {"particles": {"job": refine["uid"], "output": "particles"},
                "mask": {"job": focus_mask["uid"], "output": "mask"}})
    so = _outs(sub)
    frame = so["particles"]["meta"]["relion_frame"]
    assert frame == {"box": 16, "center": [5, -3, 2], "parent_box": 32, "pixel_size": 2.0}
    sub_log = log_of(manager, puid, sub["uid"])
    assert "relion_particle_subtract_mpi" in sub_log  # 2 processes for a refinement
    assert "--center_x 5 --center_y -3 --center_z 2 --new_box 16" in sub_log
    assert {"map", "mask"} <= set(so) and so["particles"]["meta"]["box"] == 16

    # focused refinement of the subtracted particles: maps come back on the consensus grid
    focus = _run(manager, puid, "relion_local_refine", {"mpi": 3, "blush": True},
                 {"particles": {"job": sub["uid"], "output": "particles"}, "map": {"job": sub["uid"], "output": "map"},
                  "mask": {"job": sub["uid"], "output": "mask"}})
    fo = _outs(focus)
    vol = MapVolume.read(pdir / fo["map"]["path"])
    assert vol.shape_xyz == (32, 32, 32) and vol.pixel_size == pytest.approx(2.0)
    # the fake refinement returns its reference: put back, it matches the consensus map cut at the new centre
    consensus = MapVolume.read(pdir / so["map"]["path"])
    expected = rl.to_grid(consensus.data, consensus.pixel_size, 32, 2.0)
    zc, yc, xc = 16 + 2, 16 - 3, 16 + 5
    region = (slice(zc - 8, zc + 8), slice(yc - 8, yc + 8), slice(xc - 8, xc + 8))
    assert np.allclose(vol.data[region], expected[region], atol=1e-5)
    assert np.count_nonzero(vol.data) == np.count_nonzero(vol.data[region])
    assert fo["map"]["meta"]["symmetry"] == "C1" and "--blush" in log_of(manager, puid, focus["uid"])
    assert fo["particles"]["meta"]["relion_frame"] == frame
    ref_box = MapVolume.read(pdir / focus["uid"] / "reference.mrc")
    assert ref_box.shape_xyz == (frame["box"],) * 3  # the reference was cut out on the grid of the particles

    # focused classification without alignment
    cls = _run(manager, puid, "relion_class3d", {"classes": 2, "iterations": 3, "mpi": 1},
               {"particles": {"job": sub["uid"], "output": "particles"}, "map": {"job": focus["uid"], "output": "map"},
                "mask": {"job": sub["uid"], "output": "mask"}})
    co = _outs(cls)
    assert {"maps", "particles", "class_1", "class_2", "map_1", "map_2"} <= set(co)
    assert co["class_1"]["meta"]["n_particles"] + co["class_2"]["meta"]["n_particles"] == 300
    assert "--skip_align" in log_of(manager, puid, cls["uid"])
    assert "--firstiter_cc" not in log_of(manager, puid, cls["uid"])  # a RELION map is on absolute greyscale


def test_relion_subtraction_needs_a_relion_refinement(manager, relion_project):
    puid, parts, maps = relion_project
    bad = _run(manager, puid, "relion_subtract", {},
               {"particles": {"job": parts["uid"], "output": "particles"}, "mask": {"job": maps["uid"], "output": "mask"}},
               ok=False)
    assert bad["status"] == "failed" and "RELION local refinement" in bad["error"]


def test_relion_symmetry_expansion(manager, relion_project):
    puid, parts, maps = relion_project
    refine = _run(manager, puid, "relion_local_refine", {"symmetry": "C3", "postprocess": False},
                  {"particles": {"job": parts["uid"], "output": "particles"}, "map": {"job": maps["uid"], "output": "map"}})
    assert "post-processed" not in " ".join(o["label"].lower() for o in refine["outputs"])
    sub = _run(manager, puid, "relion_subtract", {"expand_symmetry": True, "recenter": False, "mpi": 2},
               {"particles": {"job": refine["uid"], "output": "particles"}, "mask": {"job": maps["uid"], "output": "mask"}})
    meta = _outs(sub)["particles"]["meta"]
    assert meta["n_particles"] == 900 and meta["symmetry_expanded"] == "C3" and "relion_frame" not in meta


def test_relion_postprocess_and_reconstruct(manager, relion_project):
    puid, parts, maps = relion_project
    pp = _run(manager, puid, "relion_postprocess", {"locres": True},
              {"half_maps": {"job": maps["uid"], "output": "half_maps"}, "mask": {"job": maps["uid"], "output": "mask"}})
    o = _outs(pp)
    assert o["map_sharp"]["meta"]["resolution"] == 3.6 and {"fsc", "map_masked", "map_locres"} <= set(o)
    assert "--auto_bfac" in log_of(manager, puid, pp["uid"])
    rec = _run(manager, puid, "relion_reconstruct", {},
               {"particles": {"job": parts["uid"], "output": "particles"}})
    assert {"map", "half_maps", "fsc"} <= set(_outs(rec))
    assert "random half sets" in log_of(manager, puid, rec["uid"])


def test_relion_resources_and_validation():
    from cryoplug.jobs import get_job_type
    jt = get_job_type("relion_local_refine")
    assert jt.resources({"mpi": 5, "threads": 6, "gpus": 2}) == {"num_gpus": 2, "num_cpus": 30}
    assert jt.resources({"mpi": 3, "threads": 4, "gpus": 0}) == {"num_gpus": 0, "num_cpus": 12}
    with pytest.raises(ValueError):
        jt.coerce_params({"mpi": 2})  # gold-standard refinement: 3 processes at least
    assert get_job_type("relion_postprocess").resources({}) == {"num_gpus": 0, "num_cpus": 1}


def test_relion_star_import_is_relinked(manager, relion_project, tmp_path):
    """A STAR file of another RELION project: its stacks are linked, the paths resolved from its folder."""
    puid, parts, maps = relion_project
    pdir = manager.project_dir(puid)
    refine = _run(manager, puid, "relion_local_refine", {"postprocess": False},
                  {"particles": {"job": parts["uid"], "output": "particles"}, "map": {"job": maps["uid"], "output": "map"}})
    star = pdir / refine["uid"] / "run_data.star"
    other = tmp_path / "other_project"
    other.mkdir()
    imp = _run(manager, puid, "import_particles", {"path": str(star), "datadir": str(pdir)})
    rec = _run(manager, puid, "relion_reconstruct", {}, {"particles": {"job": imp["uid"], "output": "particles"}})
    assert (pdir / rec["uid"] / "Particles").is_dir()
    assert Path(pdir / rec["uid"] / "particles.star").exists()
