"""The RELION single-particle pipeline, movies to map, with fake RELION 5.1 programs (tests/fakes/relion_spa.py) that
accept only RELION's options, run from the project folder and write RELION's files: import, motion correction, CTF,
curation, picking (LoG, Topaz), extraction, 2D classification and selection, initial model, 3D classification,
auto-refine, mask, CTF refinement, polishing, local resolution, subsets, multi-body refinement and DynaMight."""
from __future__ import annotations

import json

import numpy as np
import pytest
from conftest import log_of, run_until_done

from cryoplug import relion as rl
from cryoplug.jobs import get_job_type
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


def _command(manager, puid, job):
    """The program lines of the job (as logged before they run)."""
    return log_of(manager, puid, job["uid"])


@pytest.fixture()
def spa(manager, movies):
    """Movies imported, motion-corrected and CTF-estimated."""
    p = manager.create_project("RELION SPA")
    puid = p["uid"]
    imp = _run(manager, puid, "relion_import", {"movies": str(movies["folder"] / "mic_*.mrc"), "pixel_size": 1.0,
                                                 "dose_per_frame": 1.5, "gain": str(movies["gain"])})
    mc = _run(manager, puid, "relion_motioncorr", {"threads": 2, "patches_x": 1, "patches_y": 1},
              {"movies": _ref(imp, "movies")})
    ctf = _run(manager, puid, "relion_ctffind", {"mpi": 2}, {"micrographs": _ref(mc, "micrographs")})
    return {"puid": puid, "import": imp, "motioncorr": mc, "ctf": ctf, "movies": movies}


def test_relion_preprocessing(manager, spa):
    puid, imp, mc, ctf = spa["puid"], spa["import"], spa["motioncorr"], spa["ctf"]
    pdir = manager.project_dir(puid)
    # import: the folder of the movies linked into the job folder, the optics group of relion_import
    meta = _outs(imp)["movies"]["meta"]
    assert meta["n_movies"] == 4 and meta["frames"] == 6 and meta["dims"] == [384, 384]
    assert meta["gain"] == f"{imp['uid']}/gain_reference.mrc" and meta["dose_per_frame"] == 1.5
    tables = rl.read_star(pdir / imp["uid"] / "movies.star")
    assert tables["optics"].labels[:2] == ["rlnOpticsGroupName", "rlnOpticsGroup"]
    assert tables["movies"].column("rlnMicrographMovieName")[0] == f"{imp['uid']}/movies/Movies/mic_001.mrc"
    assert (pdir / imp["uid"] / "movies" / "Movies").is_symlink()

    # motion correction: the command of RELION's GUI, the gain and dose of the import
    log = _command(manager, puid, mc)
    assert f"--i {imp['uid']}/movies.star --o {mc['uid']}/ --first_frame_sum 1 --last_frame_sum -1 --use_own --j 2 --float16" in log
    assert f"--gainref {imp['uid']}/gain_reference.mrc --gain_rot 0 --gain_flip 0 --dose_weighting --grouping_for_ps 3" in log
    assert "--dose_per_frame 1.5" in log and "--patch_x 1 --patch_y 1" in log
    mo = _outs(mc)["micrographs"]
    assert mo["meta"]["n_micrographs"] == 4 and mo["meta"]["has_motion"] and mo["meta"]["power_spectra"]
    # RELION names the outputs after the whole movie path (without extension)
    first = rl.micrograph_table(rl.read_star(pdir / mo["path"])).column("rlnMicrographName")[0]
    assert first == f"{mc['uid']}/{imp['uid']}/movies/Movies/mic_001.mrc" and (pdir / first).exists()
    plots = [s["title"] for s in _sections(manager, puid, mc, "plot")]
    assert "Accumulated motion per micrograph" in plots and any(t.startswith("Drift of") for t in plots)
    assert mo["thumbnail"]

    # CTF: CTFFIND on the power spectra of motion correction, under MPI
    log = _command(manager, puid, ctf)
    assert "mpirun -n 2" in log and "relion_run_ctffind_mpi" in log and "--use_given_ps" in log
    assert "--is_ctffind4" in log and "--fast_search" in log and "--ctffind_exe" in log
    co = _outs(ctf)["micrographs"]
    assert co["meta"]["has_ctf"] and co["meta"]["n_micrographs"] == 4 and co["meta"]["defocus_range"][0] == 14000
    assert co["meta"]["motion_star"] == mo["path"]  # relion_run_ctffind drops the motion: remembered for later
    ctab = rl.micrograph_table(rl.read_star(pdir / co["path"]))
    assert ctab.column("rlnCtfImage")[0] == f"{ctf['uid']}/{mc['uid']}/{imp['uid']}/movies/Movies/mic_001_PS.ctf:mrc"
    assert any(s["title"].startswith("CTF fits") for s in _sections(manager, puid, ctf, "image"))

    # curation: the worst CTF fit (12 Å) is rejected; the motion comes from the motion correction
    cur = _run(manager, puid, "relion_curate", {"mode": "thresholds", "max_ctf_resolution": 8.0, "max_motion": 1000.0},
               {"micrographs": _ref(ctf, "micrographs")})
    o = _outs(cur)
    assert o["micrographs"]["meta"]["n_micrographs"] == 3 and o["rejected"]["meta"]["n_micrographs"] == 1
    assert o["micrographs"]["meta"]["motion_star"] == mo["path"]
    plots = {s["title"]: s for s in _sections(manager, puid, cur, "plot")}
    assert plots["CTF fit resolution"]["style"] == "bars" and plots["CTF fit resolution"]["vlines"][0]["x"] == 8.0
    assert "Total motion" in plots
    bad = _run(manager, puid, "relion_curate", {"mode": "thresholds"}, {"micrographs": _ref(ctf, "micrographs")}, ok=False)
    assert bad["status"] == "failed" and "at least one threshold" in bad["error"]


def test_interactive_curation_and_exposure_browser(manager, spa):
    """The curation waits with the exposure browser: limits dragged on the distributions and micrographs accepted or
    rejected by hand; the work is saved while it waits; Done keeps the accepted micrographs. Motion correction and CTF
    estimation show the same browser, read-only."""
    puid, mc, ctf = spa["puid"], spa["motioncorr"], spa["ctf"]
    pdir = manager.project_dir(puid)
    # read-only browsers: the motion of the movies, then the CTF fits (with the motion files of motion correction)
    (mc_tool,) = _sections(manager, puid, mc, "exposures")
    data = json.loads((pdir / mc_tool["data"]).read_text())
    assert [m["key"] for m in data["metrics"]][:1] == ["motion"] and data["ctf_images"] is None and len(data["names"]) == 4
    (ctf_tool,) = _sections(manager, puid, ctf, "exposures")
    assert ctf_tool["curate"] and not ctf_tool.get("waiting")
    data = json.loads((pdir / ctf_tool["data"]).read_text())
    keys = [m["key"] for m in data["metrics"]]
    assert keys[:4] == ["ctf_res", "defocus", "astigmatism", "ctf_fom"] and "motion" in keys and "ice" in keys
    assert data["ctf_images"][0].endswith("mic_001_PS.ctf:mrc") and data["motion_files"][0].endswith("mic_001.star")
    res = dict(zip(data["names"], next(m for m in data["metrics"] if m["key"] == "ctf_res")["values"]))
    worst = max(res, key=res.get)

    cur = _run(manager, puid, "relion_curate", {"max_ctf_resolution": 8.0}, {"micrographs": _ref(ctf, "micrographs")},
               status="waiting")
    (tool,) = _sections(manager, puid, cur, "exposures")
    assert tool["waiting"] and tool["editable"] and tool["thresholds"] == {"ctf_res": [None, 8.0]}
    # work in progress, saved while the job waits
    best = min(res, key=res.get)
    session = {"thresholds": {"ctf_res": [None, 8.0], "motion": [None, 1000.0]}, "overrides": {worst: "accept", best: "reject"}}
    manager.write_session(puid, cur["uid"], "curate", session)
    assert manager.read_session(puid, cur["uid"], "curate") == session
    with pytest.raises(Exception, match="No micrograph kept"):  # nothing kept: the job keeps waiting
        manager.finish_interactive(puid, cur["uid"], json.dumps({"thresholds": {"ctf_res": [None, 1.0]}}))
    assert manager.job(puid, cur["uid"])["status"] == "waiting"
    done = manager.finish_interactive(puid, cur["uid"])  # Done without data: the session
    o = _outs(done)
    kept = rl.micrograph_table(rl.read_star(pdir / o["micrographs"]["path"])).column("rlnMicrographName")
    assert worst in kept and best not in kept and len(kept) == 3 and o["rejected"]["meta"]["n_micrographs"] == 1
    (table,) = [s for s in _sections(manager, puid, done, "table") if s["title"] == "Rejected by"]
    cells = {r[0]: r[1] for r in table["rows"]}
    assert cells["CTF fit resolution ≤ 8 Å"] == "1" and cells["Rejected by hand"] == "1"
    assert cells["Accepted by hand (outside the limits)"] == "1" and cells["Kept"] == "3 of 4"
    (tool,) = _sections(manager, puid, done, "exposures")  # the browser shows the decisions, read-only
    assert not tool["waiting"] and not tool["editable"] and len(tool["decided"]) == 4 and tool["decided"].count("1") == 3
    plots = {s["title"]: s for s in _sections(manager, puid, done, "plot")}
    assert plots["Total motion"]["vlines"] == [{"x": 1000.0, "label": "≤ 1000"}]
    assert "Defocus" in plots and "CTF fit resolution" in plots
    # an agent's Done with no data and no session: the limits of the parameters
    cur2 = _run(manager, puid, "relion_curate", {"max_ctf_resolution": 8.0, "min_defocus": 1000.0},
                {"micrographs": _ref(ctf, "micrographs")}, status="waiting")
    done2 = manager.finish_interactive(puid, cur2["uid"])
    assert _outs(done2)["micrographs"]["meta"]["n_micrographs"] == 3
    # a metric the micrographs do not have
    bad = _run(manager, puid, "relion_curate", {"max_astigmatism": 100.0}, {"micrographs": _ref(mc, "micrographs")}, ok=False)
    assert bad["status"] == "failed" and "Astigmatism: the micrographs have no rlnCtfAstigmatism column" in bad["error"]


def test_relion_pipeline_movies_to_map(manager, spa):
    puid, mc, ctf = spa["puid"], spa["motioncorr"], spa["ctf"]
    pdir = manager.project_dir(puid)
    cur = _run(manager, puid, "relion_curate", {"mode": "thresholds", "max_ctf_resolution": 8.0},
               {"micrographs": _ref(ctf, "micrographs")})
    mics = _ref(cur, "micrographs")

    # LoG picking (particles are 60 Å across, dark on a light background)
    pick = _run(manager, puid, "relion_autopick", {"method": "log", "diameter_min": 50, "diameter_max": 70, "mpi": 2},
                {"micrographs": mics})
    log = _command(manager, puid, pick)
    assert "--LoG --LoG_diam_min 50 --LoG_diam_max 70 --shrink 0 --lowpass 20 --LoG_adjust_threshold 0" in log
    assert "--odir" in log and "--pickname autopick" in log and "relion_autopick_mpi" in log
    co = _outs(pick)["coordinates"]
    assert co["meta"]["n_micrographs"] == 3 and co["meta"]["n_particles"] >= 25 and co["thumbnail"]
    picked = rl.coordinate_files(pdir / co["path"])
    assert picked[0][1].endswith("mic_001_autopick.star") and (pdir / picked[0][1]).exists()

    # extraction: 64 px boxes down-scaled to 32 px (2 Å/px), inverted and normalised
    ext = _run(manager, puid, "relion_extract", {"box": 64, "rescale": 32, "mpi": 1},
               {"micrographs": mics, "coordinates": _ref(pick, "coordinates")})
    log = _command(manager, puid, ext)
    assert "--extract --extract_size 64 --float16 --scale 32 --norm --bg_radius 12" in log and "--invert_contrast" in log
    po = _outs(ext)["particles"]
    n_parts = po["meta"]["n_particles"]
    assert n_parts >= 20 and po["meta"]["box"] == 32 and po["meta"]["pixel_size"] == 2.0 and po["thumbnail"]
    assert po["meta"]["relion"]["kind"] == "extract"

    # 2D classification (VDAM): class averages drawn, particles keep their class
    c2d = _run(manager, puid, "relion_class2d", {"classes": 4, "vdam_batches": 20, "threads": 2, "gpus": 0},
               {"particles": _ref(ext, "particles")})
    log = _command(manager, puid, c2d)
    assert "--grad --class_inactivity_threshold 0.1 --grad_write_iter 10 --iter 20" in log and "mpirun" not in log
    assert "--psi_step 12 --offset_range 5 --offset_step 2" in log and "--center_classes" in log
    assert f"--particle_diameter {round(0.9 * 32 * 2.0):g}" in log  # 90 % of the box
    galleries = _sections(manager, puid, c2d, "classes2d")
    assert len(galleries) == 1 and galleries[0]["iteration"] == 20 and galleries[0]["actions"]
    records = galleries[0]["classes"]
    assert len(records) == 4 and sum(r["particles"] for r in records) == n_parts
    assert all((pdir / r["image"]).exists() for r in records)
    o = _outs(c2d)
    assert o["classes"]["type"] == "class_averages" and o["classes"]["meta"]["relion"]["optimiser"].endswith("_optimiser.star")
    full = sorted((r for r in records if r["particles"]), key=lambda r: -r["particles"])
    keep = [r["class"] for r in full[:3]]

    # selection: listed classes
    sel = _run(manager, puid, "relion_select2d", {"mode": "list", "classes": ", ".join(map(str, keep))},
               {"classes": _ref(c2d, "classes")})
    so = _outs(sel)
    n_sel = sum(r["particles"] for r in full[:3])
    assert so["particles"]["meta"]["n_particles"] == n_sel and so["class_averages"]["meta"]["n_classes"] == 3
    avgs = rl.read_star(pdir / so["class_averages"]["path"])["model_classes"]
    assert len(avgs) == 3 and avgs.has("rlnReferenceImage")
    if n_sel < n_parts:
        assert so["rejected"]["meta"]["n_particles"] == n_parts - n_sel

    # selection: clicked in the gallery (the job waits), then Done
    wait = _run(manager, puid, "relion_select2d", {"mode": "interactive"}, {"classes": _ref(c2d, "classes")},
                status="waiting")
    gallery = _sections(manager, puid, wait, "classes2d")[0]
    assert gallery["selectable"] and gallery["waiting"]
    with pytest.raises(Exception, match="No class 99"):
        manager.finish_interactive(puid, wait["uid"], json.dumps({"classes": [99]}))
    done = manager.finish_interactive(puid, wait["uid"], json.dumps({"classes": [keep[0]]}))
    assert done["status"] == "completed" and _outs(done)["particles"]["meta"]["n_particles"] == full[0]["particles"]
    assert _sections(manager, puid, done, "classes2d")[-1]["selected"] == [keep[0]]

    # selection: RELION's class ranker
    auto = _run(manager, puid, "relion_select2d", {"mode": "auto", "min_score": 0.5}, {"classes": _ref(c2d, "classes")})
    assert "relion_class_ranker" in _command(manager, puid, auto) and _outs(auto)["particles"]["meta"]["n_particles"] > 0
    assert "--auto_select --min_score 0.5" in _command(manager, puid, auto)

    # initial model (VDAM), aligned on the symmetry axes
    ini = _run(manager, puid, "relion_initial_model", {"iterations": 20, "symmetry": "C1", "threads": 2, "gpus": 0},
               {"particles": _ref(sel, "particles")})
    log = _command(manager, puid, ini)
    assert "--grad --denovo_3dref" in log and "relion_align_symmetry" in log and "--select_largest_class" in log
    io = _outs(ini)
    vol = MapVolume.read(pdir / io["map"]["path"])
    assert vol.shape_xyz == (32, 32, 32) and vol.pixel_size == pytest.approx(2.0) and io["map"]["meta"]["relion_map"]

    # 3D classification with global searches
    c3d = _run(manager, puid, "relion_class3d_global", {"classes": 2, "iterations": 3, "threads": 2, "gpus": 0},
               {"particles": _ref(sel, "particles"), "map": _ref(ini, "map")})
    log = _command(manager, puid, c3d)
    assert "--K 2" in log and "--healpix_order 2" in log and "--ini_high 60" in log
    assert "--firstiter_cc" not in log  # the initial model of RELION is on its absolute greyscale
    o3 = _outs(c3d)
    assert {"maps", "particles", "map_1", "map_2"} <= set(o3)
    assert sum(o3[f"class_{c}"]["meta"]["n_particles"] for c in (1, 2) if f"class_{c}" in o3) == n_sel

    # 3D auto-refine: gold standard with 3 MPI processes
    ref = _run(manager, puid, "relion_refine3d", {"threads": 2, "gpus": 0},
               {"particles": _ref(sel, "particles"), "map": _ref(c3d, "map_1")})
    log = _command(manager, puid, ref)
    assert "mpirun -n 3" in log and "--auto_refine --split_random_halves" in log and "--firstiter_cc" not in log
    assert "--healpix_order 2 --auto_local_healpix_order 4 --offset_range 5 --offset_step 2 --sym C1" in log
    ro = _outs(ref)
    assert {"map", "half_maps", "fsc", "particles"} <= set(ro)
    assert ro["particles"]["meta"]["relion"]["optimiser"].endswith("run_optimiser.star")
    assert ref["resources"]["num_cpus"] == 6

    # mask around the refined map
    mask = _run(manager, puid, "relion_mask_create", {"lowpass": 10, "extend": 2, "soft_edge": 3, "threads": 1},
                {"map": _ref(ref, "map")})
    assert "--ini_threshold" in _command(manager, puid, mask) and _outs(mask)["mask"]["meta"]["threshold"] > 0
    masked = {"half_maps": _ref(ref, "half_maps"), "mask": _ref(mask, "mask")}

    # refinement again with the mask: post-processed
    ref2 = _run(manager, puid, "relion_refine3d", {"threads": 1, "gpus": 0, "absolute_greyscale": "no"},
                {"particles": _ref(sel, "particles"), "map": _ref(ref, "map"), "mask": _ref(mask, "mask")})
    assert "map_sharp" in _outs(ref2) and "--solvent_mask" in _command(manager, puid, ref2)
    assert "--firstiter_cc" in _command(manager, puid, ref2)

    # CTF refinement: the three passes of the tutorial
    ctfr = _run(manager, puid, "relion_ctf_refine", {"threads": 2}, {"particles": _ref(ref, "particles"), **masked})
    log = _command(manager, puid, ctfr)
    assert "--fit_aniso --kmin_mag 30" in log and "--fit_beamtilt --kmin_tilt 30 --odd_aberr_max_n 3 --fit_aberr" in log
    assert "--fit_defocus --kmin_defocus 30 --fit_mode fpmff" in log
    assert all((pdir / ctfr["uid"] / f"pass{i}" / "particles_ctf_refine.star").exists() for i in (1, 2, 3))
    post = rl.read_star(pdir / ctfr["uid"] / "postprocess.star")["general"]
    assert post.value("rlnUnfilteredMapHalf1") == f"{ctfr['uid']}/half1_unfil.mrc"
    optics = rl.read_star(pdir / _outs(ctfr)["particles"]["path"])["optics"]
    assert optics.has("rlnMagMat00") and optics.has("rlnBeamTiltX") and optics.has("rlnEvenZernike")
    assert any(s["title"] == "Defocus change per particle" for s in _sections(manager, puid, ctfr, "plot"))

    # Bayesian polishing: training, then polishing with the trained parameters; the motion of the movies comes
    # from the motion correction before the CTF estimation
    train = _run(manager, puid, "relion_polish", {"mode": "train", "threads": 2},
                 {"particles": _ref(ref, "particles"), "micrographs": mics, **masked})
    log = _command(manager, puid, train)
    assert f"--corr_mic {_outs(mc)['micrographs']['path']}" in log and "--params3" in log
    assert _outs(train)["polish_params"]["meta"]["sigma_div"] == 1785
    pol = _run(manager, puid, "relion_polish", {"threads": 2},
               {"particles": _ref(ref, "particles"), "micrographs": mics, **masked, "params": _ref(train, "polish_params")})
    log = _command(manager, puid, pol)
    assert f"--params_file {train['uid']}/opt_params_all_groups.txt --combine_frames" in log
    shiny = _outs(pol)["particles"]
    assert shiny["meta"]["n_particles"] == n_sel and shiny["path"].endswith("shiny.star")
    assert any(s["title"].startswith("Frame weighting") for s in _sections(manager, puid, pol, "plot"))

    # local resolution
    loc = _run(manager, puid, "relion_locres", {"bfactor": -50}, masked)
    assert "--locres" in _command(manager, puid, loc) and _outs(loc)["locres"]["meta"]["resolution"] > 3
    assert "map" in _outs(loc)

    # subsets and join: two halves joined again give every particle once
    split = _run(manager, puid, "relion_subset", {"operation": "split", "subsets": 2},
                 {"data": _ref(ref, "particles")})
    so = _outs(split)
    assert so["subset_1"]["meta"]["n_particles"] + so["subset_2"]["meta"]["n_particles"] == n_sel
    join = _run(manager, puid, "relion_join", {}, {"set_1": _ref(split, "subset_1"), "set_2": _ref(split, "subset_2")})
    assert _outs(join)["particles"]["meta"]["n_particles"] == n_sel
    dup = _run(manager, puid, "relion_subset", {"operation": "duplicates", "min_distance": 30},
               {"data": _ref(join, "particles")})
    assert "--remove_duplicates 30" in _command(manager, puid, dup)
    mix = _run(manager, puid, "relion_join", {}, {"set_1": _ref(join, "particles"), "set_2": mics}, ok=False)
    assert mix["status"] == "failed" and "one kind" in mix["error"]

    # Topaz: trained on the selected particles, then picking with the trained model
    topaz = _run(manager, puid, "relion_topaz_train", {"particle_diameter": 60},
                 {"micrographs": mics, "particles": _ref(sel, "particles")})
    to = _outs(topaz)["topaz_model"]
    assert to["meta"]["epoch"] == 7 and to["path"].endswith("model_epoch7.sav")  # the best test AUPRC
    tp = _run(manager, puid, "relion_autopick", {"method": "topaz", "mpi": 1},
              {"micrographs": mics, "topaz_model": _ref(topaz, "topaz_model")})
    log = _command(manager, puid, tp)
    assert "--particle_diameter 60 --topaz_extract --topaz_model" in log
    assert _outs(tp)["coordinates"]["meta"]["picker"].startswith("Topaz")
    general = _run(manager, puid, "relion_autopick", {"method": "topaz", "mpi": 1}, {"micrographs": mics}, ok=False)
    assert general["status"] == "failed" and "particle diameter" in general["error"]

    # multi-body refinement of two halves of the particle, then the analysis of their motions
    m = MapVolume.read(pdir / ro["map"]["path"])
    z = np.indices(m.data.shape)[0]
    for name, part in (("body1.mrc", z < 16), ("body2.mrc", z >= 16)):
        m.like(part.astype(np.float32)).write(pdir / name)
    b1 = _run(manager, puid, "import_maps", {"mask": str(pdir / "body1.mrc"), "compute_fsc": False})
    b2 = _run(manager, puid, "import_maps", {"mask": str(pdir / "body2.mrc"), "compute_fsc": False})
    mb = _run(manager, puid, "relion_multibody", {"threads": 1, "gpus": 0, "movies": 2},
              {"particles": _ref(ref, "particles"), "body_1": _ref(b1, "mask"), "body_2": _ref(b2, "mask")})
    log = _command(manager, puid, mb)
    assert "mpirun -n 3" in log and "--multibody_masks" in log and "--reconstruct_subtracted_bodies" in log
    # the last iteration of the refinement with the half sets apart (its final optimiser has them joined)
    assert f"--continue {ref['uid']}/run_it004_optimiser.star" in log
    mo = _outs(mb)
    assert {"body_1", "body_2", "particles", "motion_1", "motion_2"} <= set(mo)
    assert mo["motion_1"]["meta"]["frames"] == 10
    bodies = next(iter(rl.read_star(pdir / mb["uid"] / "bodies.star").values()))
    assert bodies.column("rlnBodyRotateRelativeTo") == ["2", "1"]

    # DynaMight: deformations, inverse deformations and deformed back-projection
    dm = _run(manager, puid, "relion_dynamight", {"threads": 2, "epochs": 10},
              {"particles": _ref(ref, "particles"), "map": _ref(ref, "map")})
    log = _command(manager, puid, dm)
    assert "optimize-deformations" in log and "optimize-inverse-deformations" in log and "deformable-backprojection" in log
    assert {"half_maps", "map"} <= set(_outs(dm))


def test_relion_spa_validation(manager, spa):
    puid = spa["puid"]
    mics = _ref(spa["ctf"], "micrographs")
    job = manager.create_job(puid, "relion_extract", {}, {"micrographs": mics})
    with pytest.raises(Exception, match="one of the two"):
        manager.queue_job(puid, job["uid"])
    problems = get_job_type("relion_extract").validate({"box": 63}, {"coordinates": "J1"})
    assert problems == ["Box sizes must be even"]
    assert get_job_type("relion_class2d").validate({"algorithm": "vdam", "mpi": 4}, {})
    assert get_job_type("relion_motioncorr").validate({"implementation": "motioncor2", "gpus": 0}, {})
    assert get_job_type("relion_autopick").validate({"method": "template"}, {"micrographs": "J1"})
    assert get_job_type("relion_select2d").validate({"mode": "list", "classes": ""}, {})
    assert get_job_type("relion_import").validate({"pixel_size": 0, "optics_group": "a b"}, {}) == [
        "Give the pixel size of the movies", "The optics group name may only hold letters, digits and '-'"]
    assert rl.parse_numbers("1, 4, 7-9") == [1, 4, 7, 8, 9]
    # MotionCor2 on GPUs: the executable of the MotionCor2 tool, one GPU per process
    mc = _run(manager, puid, "relion_motioncorr", {"implementation": "motioncor2", "gpus": 1, "patches_x": 1,
                                                   "patches_y": 1}, {"movies": _ref(spa["import"], "movies")})
    log = _command(manager, puid, mc)
    assert "--use_motioncor2 --motioncor2_exe" in log and "MotionCor2" in log and "--gpu 0" in log
    assert "--float16" not in log and "--grouping_for_ps" not in log


def test_relion_movies_to_map_workflow(manager, movies):
    """The workflow template: the chain runs to the selection of the 2D classes, waits for the clicks, then goes on."""
    p = manager.create_project("RELION workflow")
    puid = p["uid"]
    res = manager.instantiate_workflow(puid, "relion_spa", queue=True, overrides={
        "import": {"movies": str(movies["folder"] / "mic_*.mrc"), "pixel_size": 1.0, "dose_per_frame": 1.5,
                   "gain": str(movies["gain"])},
        "motion": {"patches_x": 1, "patches_y": 1, "threads": 2}, "ctf": {"mpi": 1},
        "pick": {"diameter_min": 50, "diameter_max": 70, "mpi": 1}, "extract": {"box": 64, "rescale": 32, "mpi": 1},
        "class2d": {"classes": 4, "vdam_batches": 20, "threads": 2, "gpus": 0},
        "inimodel": {"iterations": 20, "threads": 2, "gpus": 0}, "refine": {"threads": 1, "gpus": 0},
        "mask": {"lowpass": 10, "extend": 2, "soft_edge": 3, "threads": 1}})
    assert not res["problems"], res["problems"]
    by_type = {j["type"]: j for j in res["jobs"]}
    assert "relion_ctf_refine" not in by_type and "relion_locres" in by_type  # optional steps: off / on by default
    uids = [j["uid"] for j in res["jobs"]]
    curate = by_type["relion_curate"]
    run_until_done(manager, puid, uids[:uids.index(curate["uid"]) + 1], timeout=300)
    assert manager.job(puid, curate["uid"])["status"] == "waiting"  # the curation waits too
    done = manager.finish_interactive(puid, curate["uid"])  # Done as it opened: the 8 Å limit of the workflow
    assert done["status"] == "completed" and _outs(done)["micrographs"]["meta"]["n_micrographs"] == 3
    select = by_type["relion_select2d"]
    upstream = uids[:uids.index(select["uid"]) + 1]
    run_until_done(manager, puid, upstream, timeout=300)
    assert manager.job(puid, select["uid"])["status"] == "waiting"
    later = manager.job(puid, by_type["relion_initial_model"]["uid"])
    assert later["status"] == "queued"  # waits for the selection
    done = manager.finish_interactive(puid, select["uid"], json.dumps({"classes": [1, 2, 3, 4]}))
    assert done["status"] == "completed"
    run_until_done(manager, puid, [j["uid"] for j in res["jobs"]], timeout=300)
    states = {j["type"] + j["uid"]: manager.job(puid, j["uid"])["status"] for j in res["jobs"]}
    assert all(s == "completed" for s in states.values()), states
    post = _outs(manager.job(puid, by_type["relion_postprocess"]["uid"]))
    assert post["map_sharp"]["meta"]["resolution"] == 3.6


def test_agents_read_the_2d_classes():
    """AI agents see the classes of a gallery (largest first), and whether a selection waits for a person."""
    from cryoplug.agent.tools import report_lines
    classes = [{"class": c, "image": f"J7/classes/class_{c:03d}.png", "particles": n, "fraction": n / 100,
                "resolution": r} for c, n, r in ((1, 10, 12.0), (2, 60, 8.5), (3, 30, 999.0))]
    waiting = report_lines({"sections": [{"kind": "classes2d", "title": "2D classes", "classes": classes,
                                          "selectable": True, "waiting": True}]})
    assert "WAITING" in waiting[0] and waiting[2] == "  2 | 60 | 60.0 % | 8.5" and waiting[3] == "  3 | 30 | 30.0 % | -"
    kept = report_lines({"sections": [{"kind": "classes2d", "title": "2D classes", "classes": classes, "selected": [2, 3],
                                       "how": "clicked"}]})
    assert "kept: classes 2, 3 (clicked)" in kept[0]
    lines = report_lines({"sections": [
        {"kind": "exposures", "title": "Curate exposures", "data": "J9/exposures.json", "waiting": True},
        {"kind": "exposures", "title": "Exposures", "data": "J9/exposures.json", "decided": "1101", "overrides": {"m": "reject"}},
        {"kind": "picks", "title": "Inspect picks", "data": "J9/picks.json", "waiting": True}]})
    assert "WAITING" in lines[0] and "mode=thresholds" in lines[0] and "kept 3 of 4 (1 decided by hand)" in lines[1]
    assert "WAITING" in lines[2] and "min_fom" in lines[2]


def test_inspect_picks_and_manual_picking(manager, spa):
    """The pick inspector waits, keeps the picks above the threshold minus those removed plus those added by hand;
    manual picking starts from nothing and also finishes from the work saved while it waited."""
    puid, ctf = spa["puid"], spa["ctf"]
    pdir = manager.project_dir(puid)
    mics = _ref(ctf, "micrographs")
    pick = _run(manager, puid, "relion_autopick", {"method": "log", "diameter_min": 50, "diameter_max": 70, "mpi": 1},
                {"micrographs": mics})
    browser = _sections(manager, puid, pick, "picks")[0]  # auto-picking shows its picks, read-only
    assert not browser.get("waiting") and browser["diameter_px"] == 70.0
    info = json.loads((pdir / browser["data"]).read_text())
    raw = np.frombuffer((pdir / info["binary"]).read_bytes(), dtype="<f4").reshape(-1, 3)
    assert len(raw) == info["n_picks"] == _outs(pick)["coordinates"]["meta"]["n_particles"]
    assert info["micrographs"][0]["shape"] == [384, 384] and info["has_fom"]

    insp = _run(manager, puid, "relion_pick_inspect", {}, {"micrographs": mics, "coordinates": _ref(pick, "coordinates")},
                status="waiting")
    tool = _sections(manager, puid, insp, "picks")[0]
    assert tool["waiting"] and tool["editable"] and not tool["manual"] and tool["prompt"]
    info = json.loads((pdir / tool["data"]).read_text())
    raw = np.frombuffer((pdir / info["binary"]).read_bytes(), dtype="<f4").reshape(-1, 3)
    first, second = info["micrographs"][0], info["micrographs"][1]
    threshold = float(np.median(raw[:, 2]))
    above = int((raw[:, 2] >= threshold).sum())
    removed = [i for i in range(first["count"]) if raw[first["offset"] + i, 2] >= threshold][:2]
    edits = {first["name"]: {"removed": removed, "added": []},
             second["name"]: {"removed": [], "added": [[100.0, 120.0], [200.5, 50.0], [9999.0, 5.0]]}}  # the last is outside
    done = manager.finish_interactive(puid, insp["uid"], json.dumps({"threshold": threshold, "edits": edits}))
    out = _outs(done)["coordinates"]
    assert done["status"] == "completed" and out["meta"]["n_particles"] == above - 2 + 2
    assert out["meta"]["picker"] == "inspected" and out["meta"]["relion"]["kind"] == "manualpick"
    pairs = dict(rl.coordinate_files(pdir / out["path"]))
    xy, fom = rl.read_coordinates(pdir / pairs[second["name"]])
    assert [100.0, 120.0] in xy.tolist() and fom.max() > raw[:, 2].max()  # picked by hand: above any automatic pick
    assert pairs[second["name"]].endswith("mic_002_manualpick.star")
    final = _sections(manager, puid, done, "picks")[-1]
    assert not final.get("waiting")
    ext = _run(manager, puid, "relion_extract", {"box": 64, "mpi": 1}, {"micrographs": mics, "coordinates": _ref(done, "coordinates")})
    assert _outs(ext)["particles"]["meta"]["n_particles"] > 0

    # manual picking: a diameter is needed, the work saved while waiting is used by Done
    bad = manager.create_job(puid, "relion_pick_inspect", {}, {"micrographs": mics})
    with pytest.raises(Exception, match="particle diameter"):
        manager.queue_job(puid, bad["uid"])
    man = _run(manager, puid, "relion_pick_inspect", {"diameter": 60, "max_micrographs": 2}, {"micrographs": mics},
               status="waiting")
    tool = _sections(manager, puid, man, "picks")[0]
    info = json.loads((pdir / tool["data"]).read_text())
    assert tool["manual"] and len(info["micrographs"]) == 2 and info["n_picks"] == 0 and tool["diameter_px"] == 60.0
    name = info["micrographs"][0]["name"]
    manager.write_session(puid, man["uid"], "picks", {"threshold": None, "edits": {name: {"removed": [], "added": [[150, 150], [60, 300]]}}})
    assert manager.read_session(puid, man["uid"], "picks")["edits"][name]["added"][1] == [60, 300]
    done = manager.finish_interactive(puid, man["uid"], "")
    assert _outs(done)["coordinates"]["meta"]["n_particles"] == 2 and _outs(done)["coordinates"]["meta"]["picker"] == "manual"
    with pytest.raises(Exception, match="waiting"):
        manager.write_session(puid, man["uid"], "picks", {})
    with pytest.raises(Exception, match="Session names"):
        manager.read_session(puid, man["uid"], "../x")


def test_micrograph_pictures_and_sessions_over_the_api(manager, spa):
    from fastapi.testclient import TestClient

    from cryoplug.server.app import create_app
    puid, mc, ctf = spa["puid"], spa["motioncorr"], spa["ctf"]
    pdir = manager.project_dir(puid)
    mic = rl.micrograph_table(rl.read_star(pdir / _outs(mc)["micrographs"]["path"])).column("rlnMicrographName")[0]
    ctf_img = rl.micrograph_table(rl.read_star(pdir / _outs(ctf)["micrographs"]["path"])).column("rlnCtfImage")[0]
    app = create_app(manager.config, start_scheduler=False, manager=manager)
    with TestClient(app, base_url="http://localhost:39500") as client:
        r = client.get(f"/api/projects/{puid}/micrograph", params={"path": mic, "size": 256, "lowpass": 20})
        assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content[:4] == b"\x89PNG"
        import struct
        w, hgt = struct.unpack(">II", r.content[16:24])
        assert (w, hgt) == (384 // 2, 384 // 2)  # binned by ceil(384 / 256) = 2, as the browser computes it
        assert list((pdir / ".cryoplug_cache" / "micrographs").glob("*.png"))
        r = client.get(f"/api/projects/{puid}/ctf-image", params={"path": ctf_img, "size": 128})
        assert r.status_code == 200 and r.content[:4] == b"\x89PNG"
        assert client.get(f"/api/projects/{puid}/micrograph", params={"path": "../etc/passwd"}).status_code == 404
        r = client.put(f"/api/projects/{puid}/jobs/{mc['uid']}/session", params={"name": "picks"}, json={"a": 1})
        assert r.status_code >= 400  # only a waiting job keeps a session
        assert client.get(f"/api/projects/{puid}/jobs/{mc['uid']}/session", params={"name": "picks"}).json() == {}
        # the drift of a movie, from its motion file
        motion = rl.micrograph_table(rl.read_star(pdir / _outs(mc)["micrographs"]["path"])).column("rlnMicrographMetadata")[0]
        d = client.get(f"/api/projects/{puid}/drift", params={"path": motion}).json()
        assert len(d["x"]) == len(d["y"]) == 6 and d["x"][0] == 0
        assert client.get(f"/api/projects/{puid}/drift", params={"path": mic}).status_code == 400
