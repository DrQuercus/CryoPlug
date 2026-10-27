"""The RELION 5 tomography pipeline, tilt series to subtomogram average, with fake RELION 5.1 programs
(tests/fakes/relion_tomo.py) that accept only RELION's options, run from the project folder and write RELION's tilt-series
sets, tomograms, particle and optimisation sets: import, motion correction, CTF estimation, tilt-image exclusion,
alignment (IMOD, AreTomo2), reconstruction, cryoCARE denoising, picking, coordinate import, extraction, particle
reconstruction, initial model, 3D classification, auto-refinement, CTF refinement and frame alignment."""
from __future__ import annotations

import json

import numpy as np
import pytest
from conftest import log_of, run_until_done

from cryoplug import relion as rl
from cryoplug import tomo as tm
from cryoplug.mrc import MapVolume


def _run(manager, puid, jtype, params, inputs=None, ok=True, status="completed"):
    job = manager.create_job(puid, jtype, params, inputs or {})
    manager.queue_job(puid, job["uid"])
    run_until_done(manager, puid, [job["uid"]], timeout=180)
    done = manager.job(puid, job["uid"])
    if ok:
        assert done["status"] == status, f"{done['error']}\n{log_of(manager, puid, job['uid'])}"
    return done


def _outs(job):
    return {o["name"]: o for o in job["outputs"]}


def _ref(job, output):
    return {"job": job["uid"], "output": output}


def _sections(manager, puid, job, kind):
    report = json.loads((manager.project_dir(puid) / job["uid"] / "report.json").read_text())
    return [s for s in report.get("sections", []) if s["kind"] == kind]


def _set(manager, puid, job, output):
    pdir = manager.project_dir(puid)
    return tm.read_set(pdir / _outs(job)[output]["path"], pdir)


@pytest.fixture()
def tomo(manager, tilt_series):
    """Tilt series imported, motion-corrected (with even/odd sums) and CTF-estimated."""
    p = manager.create_project("RELION tomography")
    puid = p["uid"]
    ts = tilt_series
    imp = _run(manager, puid, "relion_tomo_import", {
        "movies": str(ts["frames"] / "*.mrc"), "mdocs": str(ts["mdoc"] / "*.mdoc"), "pixel_size": ts["pixel"],
        "dose": ts["dose"], "tilt_axis": 85.0})
    mc = _run(manager, puid, "relion_tomo_motioncorr", {"threads": 2, "even_odd": True}, {"tiltseries": _ref(imp, "tiltseries")})
    ctf = _run(manager, puid, "relion_tomo_ctffind", {"mpi": 1}, {"tiltseries": _ref(mc, "tiltseries")})
    return {"puid": puid, "import": imp, "motioncorr": mc, "ctf": ctf, "data": ts}


def test_tilt_series_to_tomograms(manager, tomo):
    """Import (doses from the mdoc order, an image named in an mdoc file but missing), motion correction, CTF estimation,
    exclusion by limits and by hand, alignment with IMOD and AreTomo2, reconstruction for denoising, cryoCARE."""
    puid, imp, mc, ctf, data = tomo["puid"], tomo["import"], tomo["motioncorr"], tomo["ctf"], tomo["data"]
    pdir = manager.project_dir(puid)
    n = data["n_tilts"]
    # import: two tilt series in acquisition order, the pre-exposure dose × the order (--dose-per-tilt-image)
    tset = _set(manager, puid, imp, "tiltseries")
    assert tset.names == ["TS_01", "TS_02"] and [len(t) for t in tset.series.values()] == [n, n]
    t1 = tset.series["TS_01"]
    assert list(t1.floats("rlnMicrographPreExposure")) == [3.0 * i for i in range(n)]
    assert list(t1.floats("rlnTomoNominalStageTiltAngle"))[:3] == [0.0, 6.0, -6.0]
    assert t1.column("rlnMicrographMovieName")[0].startswith(f"{imp['uid']}/frames/")
    assert tset.value("TS_01", "rlnTomoHand") == "-1" and tset.value("TS_01", "rlnMicrographOriginalPixelSize") == "2.000000"
    log = log_of(manager, puid, imp["uid"])
    assert "1 tilt image(s) named in the mdoc files are not among the" in log
    assert "--tilt-image-movie-pattern" in log and "--invert-defocus-handedness" in log and "--prefix" not in log
    meta = _outs(imp)["tiltseries"]["meta"]
    assert meta["n_tomograms"] == 2 and meta["n_tilts"] == 2 * n and meta["tilt_range"] == [-24.0, 24.0]
    assert not meta["has_motion"]
    # motion correction: the tilt images, their even/odd sums, the pixel size of the tilt series
    tset = _set(manager, puid, mc, "tiltseries")
    t1 = tset.series["TS_01"]
    assert t1.has("rlnMicrographNameEven") and t1.has("rlnAccumMotionTotal")
    assert all((pdir / f).exists() for f in t1.column("rlnMicrographName") + t1.column("rlnMicrographNameOdd"))
    assert tset.value("TS_01", "rlnTomoTiltSeriesPixelSize") == "2.000000"
    # the tilt-series files of a C++ program: its folder followed by the input name (no jobNNN/ folder to strip)
    assert tset.value("TS_01", "rlnTomoTiltSeriesStarFile") == f"{mc['uid']}/{imp['uid']}/tilt_series/TS_01.star"
    assert "--even_odd_split" in log_of(manager, puid, mc["uid"])
    (browser,) = _sections(manager, puid, mc, "tiltseries")
    index = json.loads((pdir / browser["data"]).read_text())
    assert [s["name"] for s in index["series"]] == ["TS_01", "TS_02"] and not browser["editable"]
    tilts = index["series"][0]["tilts"]
    assert [t["tilt"] for t in tilts] == sorted(t["tilt"] for t in tilts) and tilts[0]["image"].endswith(".mrc")
    # CTF estimation: defocus around the nominal one, the fit worse with the dose; the last image failed (25 Å)
    tset = _set(manager, puid, ctf, "tiltseries")
    t1 = tset.series["TS_01"]
    assert t1.has("rlnDefocusU") and t1.has("rlnCtfImage") and max(t1.floats("rlnCtfMaxResolution")) == 25.0
    log = log_of(manager, puid, ctf["uid"])
    assert "--localsearch_nominal_defocus 10000" in log and "--exp_factor_dose 100" in log
    assert (pdir / ctf["uid"] / "power_spectra_fits.star").exists()
    plots = {s["title"] for s in _sections(manager, puid, ctf, "plot")}
    assert "Defocus against tilt angle" in plots and "CTF fit resolution against tilt angle" in plots

    # exclusion by limits: |tilt| ≤ 20° and a CTF fit to 20 Å at least
    ex = _run(manager, puid, "relion_tomo_exclude", {"mode": "limits", "max_tilt": 20.0, "max_ctf_res": 20.0},
              {"tiltseries": _ref(ctf, "tiltseries")})
    kept = _set(manager, puid, ex, "tiltseries")
    for name, t in kept.series.items():
        assert np.abs(t.floats("rlnTomoNominalStageTiltAngle")).max() <= 20 and t.floats("rlnCtfMaxResolution").max() < 20
    assert _outs(ex)["tiltseries"]["meta"]["excluded"] == 2 * n - kept.n_tilts()
    assert kept.value("TS_01", "rlnTomoTiltSeriesStarFile") == f"{ex['uid']}/tilt_series/TS_01.star"
    # by hand: the job waits with the excluder; the selection saved meanwhile, then Done
    ex2 = _run(manager, puid, "relion_tomo_exclude", {"max_tilt": 20.0}, {"tiltseries": _ref(ctf, "tiltseries")},
               status="waiting")
    (tool,) = _sections(manager, puid, ex2, "tiltseries")
    assert tool["waiting"] and tool["editable"]
    index = json.loads((pdir / tool["data"]).read_text())
    pre = {s["name"]: [t["row"] for t in s["tilts"] if t["excluded"]] for s in index["series"]}
    assert all(len(rows) == 2 for rows in pre.values())  # ±24° pre-selected by the limit
    choice = {"excluded": {"TS_01": pre["TS_01"] + [0], "TS_02": pre["TS_02"]}}
    manager.write_session(puid, ex2["uid"], "exclude", choice)
    done = manager.finish_interactive(puid, ex2["uid"])
    kept2 = _set(manager, puid, done, "tiltseries")
    assert len(kept2.series["TS_01"]) == n - 3 and len(kept2.series["TS_02"]) == n - 2
    assert 0.0 not in list(kept2.series["TS_01"].floats("rlnTomoNominalStageTiltAngle"))
    (tool,) = _sections(manager, puid, done, "tiltseries")
    assert not tool["waiting"] and not tool["editable"]

    # alignment: IMOD patch tracking, then AreTomo2 with its CTF and reconstruction
    al = _run(manager, puid, "relion_tomo_align", {"method": "imod_patch", "thickness": 150.0},
              {"tiltseries": _ref(ex, "tiltseries")})
    log = log_of(manager, puid, al["uid"])
    assert "--imod_patchtrack" in log and "--batchtomo_exe" in log and "--gpu" not in log
    assert al["resources"].get("num_gpus", 0) == 0
    aligned = _set(manager, puid, al, "tiltseries")
    assert aligned.series["TS_01"].has("rlnTomoXTilt") and aligned.value("TS_01", "rlnIMODResidualErrorMean") is not None
    assert _outs(al)["tiltseries"]["meta"]["aligned"]
    are = _run(manager, puid, "relion_tomo_align", {"method": "aretomo", "aretomo_ctf": True, "reconstruct": True,
                                                     "vol_z": 64, "out_bin": 4}, {"tiltseries": _ref(ex, "tiltseries")})
    log = log_of(manager, puid, are["uid"])
    assert "--aretomo2" in log and "--aretomo_reconstruct" in log and "--aretomo_VolZ 64" in log and "--gpu 0" in log
    o = _outs(are)
    assert set(o) == {"tomograms"} and o["tomograms"]["meta"]["binning"] == 4.0
    vol = MapVolume.read(pdir / o["tomograms"]["meta"]["tomograms"]["TS_01"])
    assert vol.shape_xyz == (32, 32, 16)

    # reconstruction for denoising (no Fourier inversion): only the two halves, as RELION writes them
    bad = manager.create_job(puid, "relion_tomo_reconstruct", {"split": True}, {"tiltseries": _ref(al, "tiltseries")})
    with pytest.raises(Exception, match="without Fourier inversion"):
        manager.queue_job(puid, bad["uid"])
    rec = _run(manager, puid, "relion_tomo_reconstruct", {"split": True, "fourier": False, "binned_pixel": 8.0,
                                                           "thickness": 64}, {"tiltseries": _ref(al, "tiltseries")})
    log = log_of(manager, puid, rec["uid"])
    assert "--generate_split_tomograms" in log and "--w 128 --h 128 --d 64" in log and "--binned_angpix 8" in log
    tomos = _set(manager, puid, rec, "tomograms")
    g = tomos.global_table
    assert g.has("rlnTomoReconstructedTomogramHalf1") and not g.has("rlnTomoReconstructedTomogram")
    meta = _outs(rec)["tomograms"]["meta"]
    assert meta["reconstructed"] and meta["halves"] and meta["binning"] == 4.0 and meta["binned_pixel_size"] == 8.0
    assert meta["size"] == [128, 128, 64]
    (viewer,) = _sections(manager, puid, rec, "tomograms")
    index = json.loads((pdir / viewer["data"]).read_text())
    assert [t["name"] for t in index["tomograms"]] == ["TS_01", "TS_02"]
    assert index["tomograms"][0]["path"].endswith("rec_TS_01_half1.mrc") and index["tomograms"][0]["shape"] == [32, 32, 16]
    assert _outs(rec)["tomograms"].get("thumbnail")

    # cryoCARE: training on one tomogram, then denoising with the model of the training job
    tr = _run(manager, puid, "relion_tomo_denoise", {"mode": "train", "training": "TS_01", "subvolumes": 200},
              {"tomograms": _ref(rec, "tomograms")})
    log = log_of(manager, puid, tr["uid"])
    assert "cryoCARE:train" in log and "--training-tomograms TS_01" in log and "--cryocare-path" in log
    assert (pdir / tr["uid"] / "denoising_model.tar.gz").exists()
    assert _outs(tr)["tomograms"]["meta"]["denoising_model"] == f"{tr['uid']}/denoising_model.tar.gz"
    den = _run(manager, puid, "relion_tomo_denoise", {"mode": "predict", "tiles": "1,1,1"},
               {"tomograms": _ref(tr, "tomograms")})
    log = log_of(manager, puid, den["uid"])
    assert "cryoCARE:predict" in log and f"--model-file {tr['uid']}/denoising_model.tar.gz" in log and "--n-tiles 1 1 1" in log
    meta = _outs(den)["tomograms"]["meta"]
    assert meta["denoised"]["TS_01"] == f"{den['uid']}/tomograms/rec_TS_01.mrc"
    (viewer,) = _sections(manager, puid, den, "tomograms")
    index = json.loads((pdir / viewer["data"]).read_text())
    assert index["tomograms"][0]["denoised"] == f"{den['uid']}/tomograms/rec_TS_01.mrc"
    # a single tomogram (--tn): RELION writes no tomogram set, CryoPlug does
    one = _run(manager, puid, "relion_tomo_reconstruct", {"tomo_name": "TS_02", "binned_pixel": 8.0, "thickness": 64},
               {"tiltseries": _ref(al, "tiltseries")})
    assert "--tn TS_02" in log_of(manager, puid, one["uid"])
    single = _set(manager, puid, one, "tomograms")
    assert single.names == ["TS_02"] and single.value("TS_02", "rlnTomoReconstructedTomogram").endswith("rec_TS_02.mrc")


@pytest.fixture()
def tomograms(manager, tomo):
    """The tilt series aligned (IMOD patch tracking) and reconstructed at 8 Å/px (Fourier inversion)."""
    puid = tomo["puid"]
    al = _run(manager, puid, "relion_tomo_align", {"method": "imod_patch"}, {"tiltseries": _ref(tomo["ctf"], "tiltseries")})
    rec = _run(manager, puid, "relion_tomo_reconstruct", {"binned_pixel": 8.0, "thickness": 64},
               {"tiltseries": _ref(al, "tiltseries")})
    return {**tomo, "align": al, "reconstruct": rec}


def _true_picks(names, binning=4.0):
    """Clicks on the particles of the synthetic tomograms (binned voxels)."""
    from tomo_data import particle_positions
    return {n: [[round(float(v), 2) for v in row] for row in tm.to_binned_voxels(particle_positions(n), [128, 128, 64], 2.0, binning)]
            for n in names}


def test_picking_to_subtomogram_average(manager, tomograms):
    """Picking (particles, spheres, filaments; earlier picks edited), coordinate import, extraction as 2D stacks,
    particle reconstruction, initial model, auto-refinement and 3D classification of subtomograms (relion_refine --ios),
    post-processing, CTF refinement and frame alignment (the particles and tilt series they update)."""
    puid, rec = tomograms["puid"], tomograms["reconstruct"]
    pdir = manager.project_dir(puid)
    names = ["TS_01", "TS_02"]
    truth = _true_picks(names)

    # picking single particles: the job waits with the picker on the tomograms
    pick = _run(manager, puid, "relion_tomo_pick", {"diameter": 40.0}, {"tomograms": _ref(rec, "tomograms")}, status="waiting")
    (tool,) = _sections(manager, puid, pick, "tomopick")
    assert tool["waiting"] and tool["editable"] and tool["mode"] == "particles"
    index = json.loads((pdir / tool["data"]).read_text())
    assert [t["name"] for t in index["tomograms"]] == names and index["diameter_px"] == 5.0 and index["initial"] == {}
    with pytest.raises(Exception, match="No particle was picked"):
        manager.finish_interactive(puid, pick["uid"], json.dumps({"particles": {}}))
    manager.write_session(puid, pick["uid"], "picks", {"particles": truth})
    done = manager.finish_interactive(puid, pick["uid"])  # Done: the saved picks
    out = _outs(done)["particles"]
    assert out["type"] == "tomo_particles" and out["meta"]["n_particles"] == 24 and out["meta"]["per_tomogram"] == {"TS_01": 12, "TS_02": 12}
    assert not out["meta"]["extracted"] and not out["meta"]["has_poses"]
    files = tm.read_optimisation_set(pdir / out["path"])
    assert files["tomograms"] == _outs(rec)["tomograms"]["path"]
    parts = rl.read_star(pdir / files["particles"])["particles"]
    from tomo_data import particle_positions
    xyz = tm.centered_positions(parts.select([i for i, n in enumerate(parts.column("rlnTomoName")) if n == "TS_01"]))
    assert np.allclose(xyz, particle_positions("TS_01"), atol=0.2)  # the clicks back to Å from the centre
    ann = rl.read_star(pdir / done["uid"] / "annotations" / "TS_01_particles.star")
    assert len(next(iter(ann.values()))) == 12  # the annotations of RELION's picker, in binned voxels
    (tool,) = _sections(manager, puid, done, "tomopick")
    assert not tool["waiting"] and not tool["editable"]
    # earlier picks shown again to edit them
    edit = _run(manager, puid, "relion_tomo_pick", {}, {"tomograms": _ref(rec, "tomograms"), "particles": _ref(done, "particles")},
                status="waiting")
    (tool,) = _sections(manager, puid, edit, "tomopick")
    initial = json.loads((pdir / tool["data"]).read_text())["initial"]
    assert np.allclose(initial["TS_01"], truth["TS_01"], atol=0.05)
    fewer = {"particles": {"TS_01": truth["TS_01"][:5], "TS_02": []}}
    edited = manager.finish_interactive(puid, edit["uid"], json.dumps(fewer))
    assert _outs(edited)["particles"]["meta"]["per_tomogram"] == {"TS_01": 5}
    # spheres (a centre and a radius in voxels): particles on the surface, Z along the normal, tilt prior 90°
    sph = _run(manager, puid, "relion_tomo_pick", {"mode": "spheres", "spacing": 20.0}, {"tomograms": _ref(rec, "tomograms")},
               status="waiting")
    done_s = manager.finish_interactive(puid, sph["uid"], json.dumps({"spheres": {"TS_01": [[16, 16, 8, 3.0]]}}))
    ps = rl.read_star(pdir / tm.read_optimisation_set(pdir / _outs(done_s)["particles"]["path"])["particles"])["particles"]
    assert len(ps) == 18 and ps.has("rlnTomoSubtomogramRot") and set(ps.column("rlnAngleTiltPrior")) == {"90.000000"}
    radius = np.linalg.norm(tm.centered_positions(ps) - tm.from_binned_voxels(np.array([[16, 16, 8]]), [128, 128, 64], 2.0, 4.0), axis=1)
    assert np.allclose(radius, 24.0, atol=0.01)
    # filaments: particles every 40 Å along the curve through the clicks, polarity unknown (flip ratio 0.5)
    fil = _run(manager, puid, "relion_tomo_pick", {"mode": "filaments", "spacing": 40.0}, {"tomograms": _ref(rec, "tomograms")},
               status="waiting")
    done_f = manager.finish_interactive(puid, fil["uid"], json.dumps({"filaments": {"TS_02": [[[8, 8, 8], [16, 12, 8], [24, 20, 8]]]}}))
    pf = rl.read_star(pdir / tm.read_optimisation_set(pdir / _outs(done_f)["particles"]["path"])["particles"])["particles"]
    assert len(pf) >= 4 and set(pf.column("rlnHelicalTubeID")) == {"1"} and set(pf.column("rlnAnglePsiFlipRatio")) == {"0.500000"}
    steps = np.linalg.norm(np.diff(tm.centered_positions(pf), axis=0), axis=1)
    assert np.allclose(steps, 40.0, rtol=0.05)
    # coordinates picked elsewhere: text files of X Y Z in unbinned pixels, one per tomogram
    coords = pdir.parent / "coords"
    coords.mkdir(exist_ok=True)
    for n in names:
        px = tm.to_binned_voxels(particle_positions(n), [128, 128, 64], 2.0, 1.0)
        (coords / f"{n}_coords.txt").write_text("\n".join(" ".join(f"{v:.2f}" for v in row) for row in px) + "\n")
    imp = _run(manager, puid, "relion_tomo_import_coords", {"files": str(coords / "*_coords.txt"), "remove": "_coords"},
               {"tomograms": _ref(rec, "tomograms")})
    assert "--remove_substring _coords" in log_of(manager, puid, imp["uid"])
    assert _outs(imp)["particles"]["meta"]["per_tomogram"] == {"TS_01": 12, "TS_02": 12}

    # extraction: 2D stacks of the tilt images seeing each particle, 128 Å boxes binned to 64 px (2 Å/px)
    ex = _run(manager, puid, "relion_tomo_extract", {"box_A": 128.0, "binned_px": 64, "threads": 2},
              {"particles": _ref(done, "particles")})
    log = log_of(manager, puid, ex["uid"])
    assert "--box_size_A 128 --precrop_size_A -1 --binned_size_pix 64" in log and "--stack2d" in log and "--float16" in log
    meta = _outs(ex)["particles"]["meta"]
    assert meta["extracted"] and meta["stack2d"] and meta["box"] == 64 and meta["pixel_size"] == 2.0 and meta["n_particles"] == 24
    first = rl.read_star(pdir / meta["particles_star"])["particles"]
    assert first.column("rlnImageName")[0] == f"{ex['uid']}/Subtomograms/TS_01/1_stack2d.mrcs"
    assert (pdir / first.column("rlnImageName")[0]).exists() and first.has("rlnTomoVisibleFrames")
    assert _sections(manager, puid, ex, "image")
    # the average of the particles in their (unknown) orientations: no half sets yet, one map
    avg = _run(manager, puid, "relion_tomo_reconstruct_particle", {"box": 64, "threads": 2}, {"particles": _ref(ex, "particles")})
    assert set(_outs(avg)) == {"map"} and "no half maps" in log_of(manager, puid, avg["uid"])

    # initial model, then auto-refinement of the subtomograms: relion_refine --ios
    ini = _run(manager, puid, "relion_tomo_initial_model", {"iterations": 20, "threads": 1, "gpus": 0, "sigma_tilt": 5.0},
               {"particles": _ref(ex, "particles")})
    log = log_of(manager, puid, ini["uid"])
    assert f"--ios {_outs(ex)['particles']['path']}" in log and "--denovo_3dref" in log and "--sigma_tilt 5" in log
    (refine_line,) = [line for line in log.splitlines() if "relion_refine " in line and "--denovo_3dref" in line]
    assert " --i " not in refine_line
    oi = _outs(ini)
    assert oi["particles"]["type"] == "tomo_particles" and oi["particles"]["path"].endswith("run_it020_optimisation_set.star")
    ref = _run(manager, puid, "relion_tomo_refine3d", {"threads": 1, "gpus": 0},
               {"particles": _ref(ini, "particles"), "map": _ref(ini, "map")})
    log = log_of(manager, puid, ref["uid"])
    assert "--ios" in log and "--auto_refine --split_random_halves" in log
    orf = _outs(ref)
    assert {"map", "half_maps", "particles"} <= set(orf) and orf["particles"]["type"] == "tomo_particles"
    assert orf["particles"]["path"].endswith("run_optimisation_set.star") and orf["particles"]["meta"]["half_sets"]
    files = tm.read_optimisation_set(pdir / orf["particles"]["path"])
    assert files["particles"] == f"{ref['uid']}/run_data.star" and files["tomograms"] == _outs(rec)["tomograms"]["path"]
    # 3D classification of the refined subtomograms: each class a set of subtomogram particles
    c3d = _run(manager, puid, "relion_tomo_class3d", {"classes": 2, "iterations": 3, "threads": 1, "gpus": 0},
               {"particles": _ref(ref, "particles"), "map": _ref(ref, "map")})
    oc = _outs(c3d)
    assert oc["class_1"]["type"] == "tomo_particles" and oc["particles"]["type"] == "tomo_particles"
    assert oc["class_1"]["meta"]["n_particles"] + oc["class_2"]["meta"]["n_particles"] == 24
    # with the half sets of the refinement: two half maps and their FSC
    avg2 = _run(manager, puid, "relion_tomo_reconstruct_particle", {"box": 64, "threads": 2}, {"particles": _ref(ref, "particles")})
    assert {"map", "half_maps"} <= set(_outs(avg2)) and _outs(avg2)["map"]["meta"]["resolution"] > 0

    # post-processing, then CTF refinement and frame alignment against the half maps
    mask = _run(manager, puid, "relion_mask_create", {"lowpass": 10, "extend": 2, "soft_edge": 3, "threads": 1},
                {"map": _ref(ref, "map")})
    post = _run(manager, puid, "relion_postprocess", {}, {"half_maps": _ref(ref, "half_maps"), "mask": _ref(mask, "mask")})
    refs = {"half_maps": _ref(ref, "half_maps"), "mask": _ref(mask, "mask"), "postprocess": _ref(post, "map_sharp")}
    bad = manager.create_job(puid, "relion_tomo_ctf_refine", {"per_tomo_scale": True}, {"particles": _ref(ref, "particles"), **refs})
    with pytest.raises(Exception, match="per tilt image or per tomogram"):
        manager.queue_job(puid, bad["uid"])
    ctf = _run(manager, puid, "relion_tomo_ctf_refine", {"per_frame_scale": False, "per_tomo_scale": True, "threads": 2},
               {"particles": _ref(ref, "particles"), **refs})
    log = log_of(manager, puid, ctf["uid"])
    assert "--ref1" in log and "--ref2" in log and "--mask" in log and f"--fsc {post['uid']}/postprocess.star" in log
    assert "--do_defocus --d0 -3000 --d1 3000" in log and "--do_scale --per_tomogram_scale" in log
    assert "--per_tomo_scale" not in log and "--theme classic" in log
    o = _outs(ctf)
    assert o["particles"]["type"] == "tomo_particles" and o["tomograms"]["type"] == "tomograms"
    files = tm.read_optimisation_set(pdir / o["particles"]["path"])
    assert files["tomograms"] == f"{ctf['uid']}/tomograms.star" and files["particles"] == f"{ref['uid']}/run_data.star"
    refined = _set(manager, puid, ctf, "tomograms")
    assert refined.series["TS_01"].has("rlnCtfScalefactor")
    assert "Refined defoci" in {s["title"] for s in _sections(manager, puid, ctf, "table")}
    fa = _run(manager, puid, "relion_tomo_frame_align", {"motion": True, "threads": 2},
              {"particles": _ref(ctf, "particles"), **refs})
    log = log_of(manager, puid, fa["uid"])
    assert "--motion --s_vel 0.2 --s_div 5000" in log and "--r 5" in log
    files = tm.read_optimisation_set(pdir / _outs(fa)["particles"]["path"])
    assert files["trajectories"] == f"{fa['uid']}/motion.star" and files["particles"] == f"{fa['uid']}/particles.star"
    assert _outs(fa)["particles"]["meta"]["trajectories"] == f"{fa['uid']}/motion.star"
    # the polished particles go back into a refinement, with their trajectories
    ref2 = _run(manager, puid, "relion_tomo_refine3d", {"threads": 1, "gpus": 0},
                {"particles": _ref(fa, "particles"), "map": _ref(ref, "map")})
    files = tm.read_optimisation_set(pdir / _outs(ref2)["particles"]["path"])
    assert files["trajectories"] == f"{fa['uid']}/motion.star" and files["tomograms"] == f"{fa['uid']}/tomograms.star"

    # the methods paragraph a deposition writes from this history, step by step, with the papers to cite
    from cryoplug.methods import Processing
    proc = Processing(manager.ancestors(puid, ref2["uid"]) + [manager.job(puid, ref2["uid"])])
    text = " ".join(proc.sentences)
    for phrase in ("2 tilt series (pixel size", "were processed with the tomography pipeline of RELION-5",
                   "saving even and odd frame sums", "The CTF of every tilt image was estimated with CTFFIND",
                   "Tilt series were aligned with IMOD (patch tracking)", "Tomograms were reconstructed at 8 Å/voxel",
                   "24 particles were picked in the tomograms", "24 pseudo-subtomograms were extracted in boxes of 64 pixels",
                   "Subtomogram auto-refinement of 24 particles", "CTF parameters of the tilt series were refined (defocus, "
                   "per-tomogram scale)", "and the motion of the particles were refined by Bayesian polishing"):
        assert phrase in text, phrase
    assert text.index("aligned") < text.index("reconstructed") < text.index("picked") < text.index("extracted")
    for ref_ in ("Burt A. et al. (2024) FEBS Open Bio", "IMOD (batchruntomo):", "CTFFIND:", "Zivanov J., Nakane T., "
                 "Scheres S.H.W. (2019) IUCrJ"):
        assert any(ref_ in c for c in proc.citations), ref_
    assert proc.stats["initial_particles"] == 24 and proc.stats["final_particles"] == 24 and proc.stats["n_tilt_series"] == 2


def test_tilt_series_to_average_workflow(manager, tilt_series):
    """The workflow template: the chain runs to the exclusion of tilt images and waits, then to the picking (with
    cryoCARE before it) and waits, then on to the post-processed map."""
    p = manager.create_project("RELION tomography workflow")
    puid = p["uid"]
    ts = tilt_series
    res = manager.instantiate_workflow(puid, "relion_tomo", queue=True, overrides={
        "import": {"movies": str(ts["frames"] / "*.mrc"), "mdocs": str(ts["mdoc"] / "*.mdoc"), "pixel_size": ts["pixel"],
                   "dose": ts["dose"]},
        "motion": {"threads": 2}, "ctf": {"mpi": 1}, "align": {"method": "imod_patch"},
        "reconstruct": {"binned_pixel": 8.0, "thickness": 64}, "denoise": {"training": "TS_01", "tiles": "1,1,1"},
        "pick": {"diameter": 40.0}, "extract": {"box_A": 128.0, "binned_px": 64, "threads": 2},
        "inimodel": {"iterations": 20, "threads": 1, "gpus": 0}, "refine": {"threads": 1, "gpus": 0},
        "mask": {"lowpass": 10, "extend": 2, "soft_edge": 3, "threads": 1}})
    assert not res["problems"], res["problems"]
    by_type = {j["type"]: j for j in res["jobs"]}
    assert "relion_tomo_denoise" in by_type and "relion_tomo_ctf_refine" not in by_type  # optional: on / off by default
    assert by_type["relion_tomo_pick"]["inputs"]["tomograms"]["job"] == by_type["relion_tomo_denoise"]["uid"]
    uids = [j["uid"] for j in res["jobs"]]
    exclude = by_type["relion_tomo_exclude"]
    run_until_done(manager, puid, uids[:uids.index(exclude["uid"]) + 1], timeout=300)
    assert manager.job(puid, exclude["uid"])["status"] == "waiting"
    done = manager.finish_interactive(puid, exclude["uid"])  # Done as it opened: the 20 Å limit of the workflow
    assert done["status"] == "completed" and _outs(done)["tiltseries"]["meta"]["excluded"] == 2
    pick = by_type["relion_tomo_pick"]
    run_until_done(manager, puid, uids[:uids.index(pick["uid"]) + 1], timeout=300)
    assert manager.job(puid, pick["uid"])["status"] == "waiting"
    assert manager.job(puid, by_type["relion_tomo_extract"]["uid"])["status"] == "queued"  # waits for the picks
    done = manager.finish_interactive(puid, pick["uid"], json.dumps({"particles": _true_picks(["TS_01", "TS_02"])}))
    assert done["status"] == "completed"
    run_until_done(manager, puid, uids, timeout=300)
    states = {j["type"] + j["uid"]: manager.job(puid, j["uid"])["status"] for j in res["jobs"]}
    assert all(s == "completed" for s in states.values()), states
    post = _outs(manager.job(puid, by_type["relion_postprocess"]["uid"]))
    assert post["map_sharp"]["meta"]["resolution"] > 0


def test_import_prefix_and_checks(manager, tilt_series, tmp_path):
    """The prefix added after relion_python_tomo_import (RELION 5.1.1 finds no tilt series with --prefix); an mdoc file
    naming no tilt image among the files refused (RELION would pair the others with the wrong mdoc files); images
    already motion-corrected go straight to the CTF estimation."""
    import shutil
    p = manager.create_project("Tomography import")
    puid = p["uid"]
    ts = tilt_series
    common = {"movies": str(ts["frames"] / "*.mrc"), "pixel_size": ts["pixel"], "dose": ts["dose"]}
    imp = _run(manager, puid, "relion_tomo_import", {**common, "mdocs": str(ts["mdoc"] / "*.mdoc"), "prefix": "s1"})
    tset = _set(manager, puid, imp, "tiltseries")
    assert tset.names == ["s1_TS_01", "s1_TS_02"] and len(tset.series["s1_TS_01"]) == ts["n_tilts"]
    assert tset.value("s1_TS_01", "rlnTomoTiltSeriesStarFile") == f"{imp['uid']}/tilt_series/s1_TS_01.star"
    assert "--prefix" not in log_of(manager, puid, imp["uid"])
    mdocs = tmp_path / "mdocs"
    shutil.copytree(ts["mdoc"], mdocs)
    (mdocs / "TS_09.mrc.mdoc").write_text("[ZValue = 0]\nTiltAngle = 0.0\nSubFramePath = D:\\frames\\TS_09_000_0.0.mrc\n")
    bad = _run(manager, puid, "relion_tomo_import", {**common, "mdocs": str(mdocs / "*.mdoc")}, ok=False)
    assert bad["status"] == "failed" and "No tilt image among the files for TS_09.mrc.mdoc" in bad["error"]
    missing = manager.create_job(puid, "relion_tomo_import", {"movies": "", "mdocs": ""})
    with pytest.raises(Exception, match="Give the tilt image files"):
        manager.queue_job(puid, missing["uid"])
    # single images: the motion correction refuses them, the CTF estimation takes them
    mc = _run(manager, puid, "relion_tomo_import", {**common, "mdocs": str(ts["mdoc"] / "*.mdoc"), "motion_corrected": True})
    tset = _set(manager, puid, mc, "tiltseries")
    assert tset.series["TS_01"].has("rlnMicrographName") and not tset.series["TS_01"].has("rlnMicrographMovieName")
    assert tset.value("TS_01", "rlnTomoTiltSeriesPixelSize") == "2.000000"
    assert _sections(manager, puid, mc, "tiltseries")  # the browser of the tilt images
    no = _run(manager, puid, "relion_tomo_motioncorr", {}, {"tiltseries": _ref(mc, "tiltseries")}, ok=False)
    assert no["status"] == "failed" and "already motion-corrected" in no["error"]
    ctf = _run(manager, puid, "relion_tomo_ctffind", {}, {"tiltseries": _ref(mc, "tiltseries")})
    assert _set(manager, puid, ctf, "tiltseries").series["TS_01"].has("rlnDefocusU")
