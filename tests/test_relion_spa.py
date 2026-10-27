"""The RELION single-particle pipeline, movies to map, with fake RELION 5.1 programs (tests/fakes/relion_spa.py) that
accept only RELION's options, run from the project folder and write RELION's files: import, motion correction, CTF,
curation, picking (LoG, Topaz), extraction, 2D classification and selection, initial model, 3D classification,
auto-refine, mask, CTF refinement, polishing, local resolution, subsets, multi-body refinement and DynaMight."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

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

    # selection by reference (CryoSPARC's Reference Based Auto Select 2D): the classes against projections of a map
    nomap = manager.create_job(puid, "relion_select2d", {"mode": "reference"}, {"classes": _ref(c2d, "classes")})
    with pytest.raises(Exception, match="Connect the reference map"):
        manager.queue_job(puid, nomap["uid"])
    byref = _run(manager, puid, "relion_select2d", {"mode": "reference", "min_correlation": 0.5, "angular_step": 15},
                 {"classes": _ref(c2d, "classes"), "reference": _ref(ini, "map")})
    gallery = _sections(manager, puid, byref, "classes2d")[-1]
    scores = {r["class"]: r["score"] for r in gallery["classes"]}
    assert len(scores) == 4 and all(0 <= v <= 1 for v in scores.values())
    kept = sorted(c for c, v in scores.items() if v >= 0.5)
    assert kept and gallery["selected"] == kept
    assert _outs(byref)["particles"]["meta"]["n_particles"] == sum(r["particles"] for r in records if r["class"] in kept)
    assert "Correlation of the classes with the reference" in {s["title"] for s in _sections(manager, puid, byref, "plot")}
    assert (pdir / byref["uid"] / "reference_matches.png").exists()
    strict = _run(manager, puid, "relion_select2d", {"mode": "reference", "min_correlation": 0.9999, "angular_step": 15},
                  {"classes": _ref(c2d, "classes"), "reference": _ref(ini, "map")}, ok=False)
    assert strict["status"] == "failed" and "No class correlates at least 0.9999" in strict["error"]

    # 3D classification with global searches
    c3d = _run(manager, puid, "relion_class3d_global", {"classes": 2, "iterations": 3, "threads": 2, "gpus": 0},
               {"particles": _ref(sel, "particles"), "map": _ref(ini, "map")})
    log = _command(manager, puid, c3d)
    assert "--K 2" in log and "--healpix_order 2" in log and "--ini_high 60" in log
    assert "--firstiter_cc" not in log  # the initial model of RELION is on its absolute greyscale
    o3 = _outs(c3d)
    assert {"maps", "particles", "map_1", "map_2"} <= set(o3)
    assert sum(o3[f"class_{c}"]["meta"]["n_particles"] for c in (1, 2) if f"class_{c}" in o3) == n_sel
    # diagnostics as in CryoSPARC: share of the classes over the iterations, viewing directions of each class
    plots = {s["title"]: s for s in _sections(manager, puid, c3d, "plot")}
    shares = plots["Share of each class over the iterations"]["series"]
    assert [s["name"] for s in shares] == ["Class 1", "Class 2"] and shares[0]["x"] == [1, 2, 3]
    assert shares[0]["y"][0] < shares[0]["y"][-1] and shares[0]["y"][-1] > shares[1]["y"][-1]
    assert "Estimated resolution of each class" in plots and "Posterior precision of the poses" in plots
    assert "Particle shifts" in plots
    heat = {s["title"]: s for s in _sections(manager, puid, c3d, "heatmap")}
    assert {"Viewing directions (all particles)", "Viewing directions of class 1", "Viewing directions of class 2"} <= set(heat)
    assert sum(map(sum, heat["Viewing directions (all particles)"]["values"])) == pytest.approx(100, abs=0.5)

    # heterogeneous refinement: a class map and a decoy of another box and pixel size (resampled onto the particles)
    MapVolume(data=np.random.default_rng(3).normal(0, 1, (40, 40, 40)).astype(np.float32),
              voxel=np.full(3, 1.6)).write(pdir / "decoy.mrc")
    decoy = _run(manager, puid, "import_maps", {"map": str(pdir / "decoy.mrc"), "compute_fsc": False})
    het = _run(manager, puid, "relion_hetero_refine", {"iterations": 4, "threads": 2, "gpus": 0},
               {"particles": _ref(sel, "particles"), "ref_1": _ref(c3d, "map_1"), "ref_2": _ref(decoy, "map")})
    log = _command(manager, puid, het)
    assert f"--ref {het['uid']}/references.star" in log and "--K 2" in log and "--ini_high 40" in log
    assert "--firstiter_cc" in log  # the imported decoy is not on RELION's absolute greyscale
    refs = rl.read_star(pdir / het["uid"] / "references.star")["model_classes"].column("rlnReferenceImage")
    assert refs == [f"{het['uid']}/references/ref_1.mrc", f"{het['uid']}/references/ref_2.mrc"]
    resampled = MapVolume.read(pdir / refs[1])
    assert resampled.shape_xyz == (32, 32, 32) and resampled.pixel_size == pytest.approx(2.0)
    oh = _outs(het)
    assert {"maps", "particles", "map_1", "map_2", "class_1", "class_2"} <= set(oh)
    assert oh["class_1"]["meta"]["n_particles"] > oh["class_2"]["meta"]["n_particles"]
    assert [r[1] for r in next(s for s in _sections(manager, puid, het, "table") if s["title"] == "References")["rows"]] \
        == ["ref_1.mrc", "ref_2.mrc"]
    # the gallery of the 3D classes, and the selection of classes (clicked, or listed)
    (gal,) = [s for s in _sections(manager, puid, c3d, "classes2d")]
    assert gal["three_d"] and gal["actions"] and gal["select_job"] == "relion_select3d" and len(gal["classes"]) == 2
    assert (pdir / gal["classes"][0]["image"]).exists() and gal["classes"][0]["map"].endswith("_class001.mrc")
    s3 = _run(manager, puid, "relion_select3d", {}, {"particles": _ref(c3d, "particles")}, status="waiting")
    (waiting,) = _sections(manager, puid, s3, "classes2d")
    assert waiting["selectable"] and waiting["waiting"] and waiting["three_d"]
    done = manager.finish_interactive(puid, s3["uid"], json.dumps({"classes": [1]}))
    o = _outs(done)
    assert o["particles"]["meta"]["n_particles"] == o3["class_1"]["meta"]["n_particles"]
    assert o["rejected"]["meta"]["n_particles"] == o3["class_2"]["meta"]["n_particles"]
    listed = _run(manager, puid, "relion_select3d", {"mode": "list", "classes": "1, 2"}, {"particles": _ref(c3d, "particles")})
    assert _outs(listed)["particles"]["meta"]["n_particles"] == n_sel and "rejected" not in _outs(listed)
    bad = _run(manager, puid, "relion_select3d", {}, {"particles": _ref(sel, "particles")}, ok=False)
    assert bad["status"] == "failed" and "Classified particles" in bad["error"]

    one = manager.create_job(puid, "relion_hetero_refine", {}, {"particles": _ref(sel, "particles"), "ref_1": _ref(c3d, "map_1")})
    with pytest.raises(Exception, match="Reference 2"):  # a class per reference: two at least
        manager.queue_job(puid, one["uid"])

    # 3D auto-refine: gold standard with 3 MPI processes
    ref = _run(manager, puid, "relion_refine3d", {"threads": 2, "gpus": 0},
               {"particles": _ref(sel, "particles"), "map": _ref(c3d, "map_1")})
    log = _command(manager, puid, ref)
    assert "mpirun -n 3" in log and "--auto_refine --split_random_halves" in log and "--firstiter_cc" not in log
    assert "--healpix_order 2 --auto_local_healpix_order 4 --offset_range 5 --offset_step 2 --sym C1" in log
    ro = _outs(ref)
    assert {"map", "half_maps", "fsc", "particles"} <= set(ro)
    assert ro["particles"]["meta"]["relion"]["optimiser"].endswith("run_optimiser.star")
    assert [s["title"] for s in _sections(manager, puid, ref, "heatmap")] == ["Viewing directions"]
    assert "Posterior precision of the poses" in {s["title"] for s in _sections(manager, puid, ref, "plot")}

    # particle utilities: orientations rebalanced, sets compared, particles down-sampled, symmetry expanded
    rb = _run(manager, puid, "relion_rebalance", {"bin_size": 30, "percentile": 50}, {"particles": _ref(ref, "particles")})
    ob = _outs(rb)
    n_kept, n_removed = ob["particles"]["meta"]["n_particles"], ob["removed"]["meta"]["n_particles"]
    assert n_kept + n_removed == n_sel and n_removed > 0
    assert [s["title"] for s in _sections(manager, puid, rb, "heatmap")] == ["Viewing directions before", "Viewing directions after"]
    for match, extra in (("image", {}), ("position", {"distance": 5.0})):
        sets = _run(manager, puid, "relion_particle_sets", {"match": match, **extra},
                    {"set_a": _ref(ref, "particles"), "set_b": _ref(rb, "particles")})
        os_ = _outs(sets)
        assert os_["both"]["meta"]["n_particles"] == n_kept and os_["a_only"]["meta"]["n_particles"] == n_removed
        assert "b_only" not in os_
    ds = _run(manager, puid, "relion_downsample", {"box": 16}, {"particles": _ref(sel, "particles")})
    od = _outs(ds)["particles"]
    assert od["meta"]["box"] == 16 and od["meta"]["pixel_size"] == pytest.approx(4.0) and od["meta"]["n_particles"] == n_sel
    first = rl.particle_table(rl.read_star(pdir / od["path"])).column("rlnImageName")[0]
    import mrcfile
    with mrcfile.open(str(pdir / first.split("@")[1])) as m:
        assert m.data.shape[1:] == (16, 16) and float(m.voxel_size.x) == pytest.approx(4.0)
    src = rl.particle_table(rl.read_star(pdir / _outs(sel)["particles"]["path"])).column("rlnImageName")[0]
    with mrcfile.open(str(pdir / src.split("@")[1])) as m:
        before = float(np.asarray(m.data[int(src.split("@")[0]) - 1]).mean())
    with mrcfile.open(str(pdir / first.split("@")[1])) as m:
        assert float(np.asarray(m.data[0]).mean()) == pytest.approx(before, abs=1e-3)  # pixel values kept
    se = _run(manager, puid, "relion_symmetry_expand", {"symmetry": "C2"}, {"particles": _ref(ref, "particles")})
    assert "relion_particle_symmetry_expand" in _command(manager, puid, se) and "--sym C2" in _command(manager, puid, se)
    assert _outs(se)["particles"]["meta"]["n_particles"] == 2 * n_sel
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

    # what a deposition writes from this history: a methods sentence per step, with its citations, and Table 1
    from cryoplug.jobs.deposition import methods_text, table1
    from cryoplug.methods import Processing
    hist = manager.ancestors(puid, ref2["uid"]) + [manager.job(puid, ref2["uid"])]
    proc = Processing(hist)
    text, cites = methods_text(hist, proc)
    assert text.startswith("4 movies (pixel size 1 Å, 300 kV, total exposure 9.0 e⁻/Å² in 6 frames) were processed in "
                           "RELION-5. Beam-induced motion was corrected with RELION's implementation of the MotionCor2 "
                           "algorithm (1 × 1 patches, dose weighting).")
    for phrase in ("estimated with CTFFIND (fitted between 30 and 5 Å", "Micrographs were curated",
                   "with RELION's Laplacian-of-Gaussian picker (particle diameter 50-70 Å)",
                   f"{n_parts:,} particles were extracted in boxes of 64 pixels, downscaled to 32 pixels (2 Å/pixel)",
                   "Reference-free 2D classification into 4 classes used the VDAM algorithm (20 mini-batches)",
                   f"{n_sel:,} particles in 3 selected 2D classes were kept", "generated de novo with the VDAM algorithm",
                   "sorted by 3D classification into 2 classes (3 iterations, with global angular searches)",
                   f"3D auto-refinement of {n_sel:,} particles gave a map at", "A soft mask was made from the map low-pass "
                   "filtered to 10 Å", "post-processing with a soft mask sharpened it with a B-factor of",
                   "Data processing was orchestrated with CryoPlug"):
        assert phrase in text, phrase
    assert text.index("Beam-induced motion") < text.index("CTFFIND") < text.index("picked") < text.index("auto-refinement")
    assert cites[0].startswith("RELION: Scheres S.H.W. (2012)") and len(cites) == len(set(cites))
    for ref_ in ("Zivanov J. et al. (2018) eLife 7, e42166", "MotionCor2", "CTFFIND", "Scheres S.H.W., Chen S. (2012)",
                 "Chen S. et al. (2013) Ultramicroscopy", "Rosenthal P.B., Henderson R. (2003)"):
        assert any(ref_ in c for c in cites), ref_
    st = proc.stats
    assert st["voltage"] == 300 and st["exposure"] == 9.0 and st["pixel_size"] == 1.0 and st["symmetry"] == "C1"
    assert st["initial_particles"] == n_parts and st["final_particles"] == n_sel and st["n_movies"] == 4
    assert st["bfactor"] == _outs(ref2)["map_sharp"]["meta"]["bfactor"] and 0 < st["defocus_range"][0] <= st["defocus_range"][1]
    summary = {"ligands": {}, "n_atoms": 0, "n_hydrogens": 0, "polymers": [], "b_iso": {"mean": 0}}
    rows = dict(table1(summary, {}, st["resolution"], {"pixel_size": 2.0}, "C1", st))
    assert rows["Voltage (kV)"] == "300" and rows["Electron exposure (e–/Å²)"] == "9.0" and rows["Pixel size (Å)"] == "1"
    assert rows["Initial particle images (no.)"] == f"{n_parts:,}" and rows["Final particle images (no.)"] == f"{n_sel:,}"
    assert rows["Defocus range (μm)"].endswith("(estimated)") and rows["Map sharpening B factor (Å²)"]
    assert rows["Magnification"] == ""  # not known to CryoPlug

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


def test_helical_options(manager, spa):
    """The helical options of RELION's GUI in picking, extraction, 2D and 3D classification, auto-refine and masks."""
    puid, ctf = spa["puid"], spa["ctf"]
    pdir = manager.project_dir(puid)
    mics = _ref(ctf, "micrographs")
    pick = _run(manager, puid, "relion_autopick", {"method": "log", "diameter_min": 50, "diameter_max": 70, "mpi": 1},
                {"micrographs": mics})
    ext = _run(manager, puid, "relion_extract", {"box": 64, "rescale": 32, "mpi": 1, "helical": True, "helical_rise": 4.75,
                                                 "helical_outer_diameter": 60}, {"micrographs": mics, "coordinates": _ref(pick, "coordinates")})
    assert ("--invert_contrast --helix --helical_outer_diameter 60 --helical_bimodal_angular_priors "
            "--helical_cut_into_segments --helical_nr_asu 1 --helical_rise 4.75") in _command(manager, puid, ext)
    parts = _ref(ext, "particles")
    c2 = _run(manager, puid, "relion_class2d", {"classes": 2, "vdam_batches": 10, "threads": 1, "gpus": 0, "helical": True,
                                                "helical_outer_diameter": 60}, {"particles": parts})
    assert ("--helical_outer_diameter 60 --no_init_blobs --bimodal_psi --sigma_psi 2 --helix --helical_rise_initial 4.75 "
            "--norm --scale") in _command(manager, puid, c2)
    MapVolume(data=np.random.default_rng(5).normal(0, 1, (32, 32, 32)).astype(np.float32), voxel=np.full(3, 2.0)).write(pdir / "tube.mrc")
    ref = _ref(_run(manager, puid, "import_maps", {"map": str(pdir / "tube.mrc"), "compute_fsc": False}), "map")
    helix = {"helical": True, "helical_outer_diameter": 60, "helical_twist": -1.2, "helical_rise_initial": 4.75}
    c3 = _run(manager, puid, "relion_class3d_global", {"classes": 2, "iterations": 2, "threads": 1, "gpus": 0, **helix},
              {"particles": parts, "map": ref})
    assert ("--sym C1 --norm --scale --helix --helical_outer_diameter 60 --helical_nr_asu 1 --helical_twist_initial -1.2 "
            "--helical_rise_initial 4.75 --helical_z_percentage 0.3 --helical_keep_tilt_prior_fixed --sigma_tilt 5 "
            "--sigma_psi 3.33333 --sigma_rot 0 --j 1") in _command(manager, puid, c3)
    r3 = _run(manager, puid, "relion_refine3d", {"threads": 1, "gpus": 0, **helix, "helical_search": True, "helical_twist_min": -1.5,
                                                 "helical_twist_max": -1.0, "helical_rise_min": 4.6, "helical_rise_max": 4.9},
              {"particles": parts, "map": ref})
    assert ("--helix --helical_outer_diameter 60 --helical_nr_asu 1 --helical_twist_initial -1.2 --helical_rise_initial 4.75 "
            "--helical_z_percentage 0.3 --helical_symmetry_search --helical_twist_min -1.5 --helical_twist_max -1 "
            "--helical_rise_min 4.6 --helical_rise_max 4.9 --sigma_tilt 5 --sigma_psi 3.33333 --sigma_rot 0 "
            "--helical_keep_tilt_prior_fixed") in _command(manager, puid, r3)
    mask = _run(manager, puid, "relion_mask_create", {"lowpass": 10, "threads": 1, "helical": True}, {"map": ref})
    assert "--width_soft_edge 6 --helix --z_percentage 0.3 --j 1" in _command(manager, puid, mask)
    # before queueing
    for jtype, params in (("relion_refine3d", {"helical": True}),
                          ("relion_class3d_global", {**helix, "helical_search": True}),
                          ("relion_autopick", {"method": "log", "helical": True, "helical_rise": 4.75})):
        job = manager.create_job(puid, jtype, params, {"particles": parts, "map": ref} if jtype != "relion_autopick" else {"micrographs": mics})
        with pytest.raises(Exception, match="elical"):
            manager.queue_job(puid, job["uid"])


def test_helical_symmetry_toolbox_and_ctf_options(manager, spa):
    """relion_helix_toolbox: a cylinder reference, the local search of twist and rise followed by their imposition, a
    given symmetry imposed; CTFFIND's sample tilt search and extra ice statistics."""
    puid, mc = spa["puid"], spa["motioncorr"]
    pdir = manager.project_dir(puid)
    cyl = _run(manager, puid, "relion_helix_toolbox", {"mode": "cylinder", "box": 48, "pixel_size": 2.0, "outer_diameter": 60})
    assert (f"relion_helix_toolbox --cylinder --o {cyl['uid']}/cylinder.mrc --boxdim 48 --cyl_inner_diameter -1 "
            "--cyl_outer_diameter 60 --angpix 2 --sphere_percentage 0.9 --width 5") in _command(manager, puid, cyl)
    ref = _ref(cyl, "map")
    assert MapVolume.read(pdir / _outs(cyl)["map"]["path"]).data.shape == (48, 48, 48)
    search = _run(manager, puid, "relion_helix_toolbox", {"mode": "search", "outer_diameter": 60, "twist_min": -2,
                                                          "twist_max": -1, "rise_min": 4.6, "rise_max": 4.9}, {"map": ref})
    log = _command(manager, puid, search)
    assert (f"--search --i {_outs(cyl)['map']['path']} --cyl_inner_diameter -1 --cyl_outer_diameter 60 --angpix 2 "
            "--z_percentage 0.3 --rise_min 4.6 --rise_max 4.9 --twist_min -2 --twist_max -1") in log
    assert "--impose" in log and "--rise 4.78 --twist -1.6" in log
    meta = _outs(search)["map"]["meta"]
    assert meta["helical_rise"] == 4.78 and meta["helical_twist"] == -1.6
    rows = dict(next(s for s in _sections(manager, puid, search, "table") if s["title"] == "Helical symmetry found")["rows"])
    assert rows["Handedness"] == "left-handed"
    imp = _run(manager, puid, "relion_helix_toolbox", {"mode": "impose", "outer_diameter": 60, "twist": 179.4, "rise": 2.4},
               {"map": ref})
    assert "--rise 2.4 --twist 179.4" in _command(manager, puid, imp)
    job = manager.create_job(puid, "relion_helix_toolbox", {"mode": "search"}, {})
    with pytest.raises(Exception, match="map of the filament"):
        manager.queue_job(puid, job["uid"])
    # symmetry axes (relion_align_symmetry on its own)
    al = _run(manager, puid, "relion_align_symmetry", {"symmetry": "d2", "apply_sym": True}, {"map": ref})
    assert (f"relion_align_symmetry --i {_outs(cyl)['map']['path']} --o {al['uid']}/aligned.mrc --sym D2 --angpix 2 "
            "--box_size 64 --nr_uniform 400 --apply_sym") in _command(manager, puid, al)
    assert _outs(al)["map"]["meta"]["symmetry"] == "D2"
    job = manager.create_job(puid, "relion_align_symmetry", {"symmetry": "C1"}, {"map": ref})
    with pytest.raises(Exception, match="no axis"):
        manager.queue_job(puid, job["uid"])
    # CTFFIND: tilt search and extra ice statistics (with the power spectra of motion correction)
    ctf = _run(manager, puid, "relion_ctffind", {"mpi": 1, "tilt_search": True, "extra_ice": True},
               {"micrographs": _ref(mc, "micrographs")})
    log = _command(manager, puid, ctf)
    assert "--use_given_ps --extra_ice_stats --tilt_search" in log
    mics = rl.micrograph_table(rl.read_star(pdir / _outs(ctf)["micrographs"]["path"]))
    assert mics.has("rlnCtfIceRingKurtosis") and mics.has("rlnCtfIceRingPowerFraction")
    data = json.loads((pdir / _sections(manager, puid, ctf, "exposures")[0]["data"]).read_text())
    assert {"ice_fraction", "ice_kurtosis"} <= {m["key"] for m in data["metrics"]}


def test_gain_estimation(manager, spa):
    """relion_estimate_gain on the movies; the movies come out with the estimated gain, which motion correction uses."""
    puid, imp = spa["puid"], spa["import"]
    pdir = manager.project_dir(puid)
    est = _run(manager, puid, "relion_estimate_gain", {"max_frames": 12, "threads": 2}, {"movies": _ref(imp, "movies")})
    log = _command(manager, puid, est)
    assert (f"relion_estimate_gain --i {_outs(imp)['movies']['path']} --o {est['uid']}/gain_estimate.mrc --j 2 "
            "--max_frames 12 --random") in log and "already have a gain reference" in log
    out = _outs(est)
    assert out["movies"]["meta"]["gain"] == f"{est['uid']}/gain_estimate.mrc" and out["gain"]["type"] == "files"
    assert (pdir / out["movies"]["path"]).read_text() == (pdir / _outs(imp)["movies"]["path"]).read_text()
    assert any(s["title"] == "Gain reference" for s in _sections(manager, puid, est, "image"))
    mc = _run(manager, puid, "relion_motioncorr", {"threads": 2, "patches_x": 1, "patches_y": 1}, {"movies": _ref(est, "movies")})
    assert f"--gainref {est['uid']}/gain_estimate.mrc --gain_rot 0 --gain_flip 0" in _command(manager, puid, mc)


def test_amyloid_initial_model(manager, spa):
    """relion_helix_inimodel2d from the class averages of a 2D selection (or of the classification itself)."""
    puid, ctf = spa["puid"], spa["ctf"]
    pdir = manager.project_dir(puid)
    mics = _ref(ctf, "micrographs")
    pick = _run(manager, puid, "relion_autopick", {"method": "log", "diameter_min": 50, "diameter_max": 70, "mpi": 1},
                {"micrographs": mics})
    ext = _run(manager, puid, "relion_extract", {"box": 64, "rescale": 32, "mpi": 1},
               {"micrographs": mics, "coordinates": _ref(pick, "coordinates")})
    c2d = _run(manager, puid, "relion_class2d", {"classes": 3, "vdam_batches": 10, "threads": 1, "gpus": 0},
               {"particles": _ref(ext, "particles")})
    sel = _run(manager, puid, "relion_select2d", {"mode": "list", "classes": "1, 2"}, {"classes": _ref(c2d, "classes")})
    job = manager.create_job(puid, "relion_helix_inimodel2d", {}, {"classes": _ref(sel, "class_averages")})
    with pytest.raises(Exception, match="cross-over distance"):
        manager.queue_job(puid, job["uid"])
    ini = _run(manager, puid, "relion_helix_inimodel2d", {"crossover_distance": 600, "iterations": 3, "symmetry": 2,
                                                          "threads": 2}, {"classes": _ref(sel, "class_averages")})
    log = _command(manager, puid, ini)
    assert (f"relion_helix_inimodel2d --i {ini['uid']}/class_averages.star --o {ini['uid']}/inimodel --crossover_distance 600 "
            "--angpix 2 --iter 3 --K 1 --search_shift 10 --search_angle 15 --step_angle 1 --sym 2 --j 2") in log
    refs = rl.read_star(pdir / ini["uid"] / "class_averages.star")
    assert len(next(iter(refs.values())).rows) == 2
    vol = MapVolume.read(pdir / _outs(ini)["map"]["path"])
    assert vol.data.shape == (32, 32, 32) and abs(vol.voxel[0] - 2.0) < 1e-6
    both = _run(manager, puid, "relion_helix_inimodel2d", {"crossover_distance": 600, "iterations": 2, "classes": 2},
                {"classes": _ref(c2d, "classes")})
    assert {"map", "map_1", "map_2"} <= set(_outs(both))


def test_continue_relion_runs(manager, spa):
    """A failed or killed auto-refinement or 3D classification goes on from its last iteration (relion_refine
    --continue, under run_ct<iteration> as RELION's GUI names it)."""
    import os
    puid, ctf = spa["puid"], spa["ctf"]
    pdir = manager.project_dir(puid)
    mics = _ref(ctf, "micrographs")
    pick = _run(manager, puid, "relion_autopick", {"method": "log", "diameter_min": 50, "diameter_max": 70, "mpi": 1},
                {"micrographs": mics})
    ext = _run(manager, puid, "relion_extract", {"box": 64, "rescale": 32, "mpi": 1},
               {"micrographs": mics, "coordinates": _ref(pick, "coordinates")})
    MapVolume(data=np.random.default_rng(5).normal(0, 1, (32, 32, 32)).astype(np.float32), voxel=np.full(3, 2.0)).write(pdir / "ref.mrc")
    ref = _ref(_run(manager, puid, "import_maps", {"map": str(pdir / "ref.mrc"), "compute_fsc": False}), "map")
    parts = _ref(ext, "particles")

    def crash(job, keep):  # as if the run had stopped after iteration ``keep``: later files gone, the job failed
        jdir = pdir / job["uid"]
        for f in jdir.iterdir():
            m = re.match(r"run_it(\d+)_", f.name)
            if (m and int(m.group(1)) > keep) or re.match(r"run_(class001|half\d_class001_unfil|data|model|optimiser)\.", f.name):
                os.unlink(f)
        manager.db.update_job(puid, job["uid"], status="failed", error="relion_refine_mpi exited with code 1")

    r3 = _run(manager, puid, "relion_refine3d", {"threads": 1, "gpus": 0}, {"particles": parts, "map": ref})
    with pytest.raises(Exception, match="failed or killed"):
        manager.continue_job(puid, r3["uid"])
    crash(r3, 2)
    manager.continue_job(puid, r3["uid"])
    run_until_done(manager, puid, [r3["uid"]], timeout=180)
    done = manager.job(puid, r3["uid"])
    assert done["status"] == "completed", done["error"]
    log = _command(manager, puid, done)
    assert f"--continue {r3['uid']}/run_it002_optimiser.star --o {r3['uid']}/run_ct2 --dont_combine_weights_via_disc" in log
    assert "Continuing from iteration 2" in log and not (pdir / r3["uid"] / "continue.json").exists()
    out = _outs(done)
    assert out["particles"]["path"].endswith("run_ct2_data.star") and {"map", "half_maps"} <= set(out)
    assert out["particles"]["meta"]["relion"]["optimiser"].endswith("run_ct2_optimiser.star")

    c3 = _run(manager, puid, "relion_class3d_global", {"classes": 2, "iterations": 3, "threads": 1, "gpus": 0},
              {"particles": parts, "map": ref})
    crash(c3, 2)
    manager.continue_job(puid, c3["uid"])
    run_until_done(manager, puid, [c3["uid"]], timeout=180)
    done = manager.job(puid, c3["uid"])
    assert done["status"] == "completed", done["error"]
    assert f"--continue {c3['uid']}/run_it002_optimiser.star --o {c3['uid']}/run_ct2" in _command(manager, puid, done)
    out = _outs(done)
    assert out["map_1"]["path"].endswith("run_ct2_it003_class001.mrc") and out["particles"]["path"].endswith("run_ct2_it003_data.star")
    plots = {s["title"]: s for s in _sections(manager, puid, done, "plot")}
    assert plots["Share of each class over the iterations"]["series"][0]["x"] == [1, 2, 3]  # both runs

    sel = _run(manager, puid, "relion_select3d", {"mode": "list", "classes": "1"}, {"particles": _ref(done, "particles")})
    manager.db.update_job(puid, sel["uid"], status="failed")
    with pytest.raises(Exception, match="cannot be continued"):
        manager.continue_job(puid, sel["uid"])
    empty = manager.create_job(puid, "relion_refine3d", {"threads": 1, "gpus": 0}, {"particles": parts, "map": ref})
    manager.db.update_job(puid, empty["uid"], status="killed")
    with pytest.raises(Exception, match="Nothing to continue"):
        manager.continue_job(puid, empty["uid"])


EPU_XML = """<?xml version="1.0" encoding="utf-8"?>
<MicroscopeImage xmlns="http://schemas.datacontract.org/2004/07/Fei.SharedObjects" xmlns:i="http://www.w3.org/2001/XMLSchema-instance">
<microscopeData><optics><BeamShift xmlns:a="http://schemas.datacontract.org/2004/07/Fei.Types"><a:_x>{x}</a:_x><a:_y>{y}</a:_y></BeamShift>
</optics></microscopeData></MicroscopeImage>"""


def test_optics_groups(manager, spa, tmp_path):
    """Optics groups from the beam shift of EPU's XML files (clustered), from the file names, or from the order."""
    puid, mc, ctf = spa["puid"], spa["motioncorr"], spa["ctf"]
    pdir = manager.project_dir(puid)
    xml = tmp_path / "Images-Disc1" / "GridSquare_7" / "Data"
    xml.mkdir(parents=True)
    for name, (x, y) in {"mic_001": (-0.21, -0.20), "mic_002": (-0.20, -0.21), "mic_003": (0.19, 0.20), "mic_004": (0.20, 0.21)}.items():
        (xml / f"{name}.xml").write_text(EPU_XML.format(x=x, y=y))
    mics = _ref(ctf, "micrographs")
    bs = _run(manager, puid, "relion_optics_groups", {"method": "beam_shift", "xml_folder": str(tmp_path), "groups": 2},
              {"data": mics})
    out = _outs(bs)["micrographs"]
    tables = rl.read_star(pdir / out["path"])
    assert tables["optics"].column("rlnOpticsGroupName") == ["opticsGroup1", "opticsGroup2"]
    groups = dict(zip([n.split("/")[-1] for n in tables["micrographs"].column("rlnMicrographName")],
                      tables["micrographs"].column("rlnOpticsGroup")))
    assert groups["mic_001.mrc"] == groups["mic_002.mrc"] != groups["mic_003.mrc"] == groups["mic_004.mrc"]
    assert out["meta"]["optics_groups"] == 2 and out["meta"]["motion_star"]  # still known for polishing
    plot = next(s for s in _sections(manager, puid, bs, "plot") if s["title"] == "Beam shifts")
    assert plot["style"] == "points" and len(plot["series"]) == 2
    order = _run(manager, puid, "relion_optics_groups", {"method": "order", "groups": 2}, {"data": _ref(mc, "micrographs")})
    t2 = rl.read_star(pdir / _outs(order)["micrographs"]["path"])
    assert t2["micrographs"].column("rlnOpticsGroup") == ["1", "1", "2", "2"]
    names = _run(manager, puid, "relion_optics_groups", {"method": "filename", "pattern": r"mic_00([12])"},
                 {"data": mics}, ok=False)
    assert names["status"] == "completed"  # mic_003 and mic_004 without match: one group of their own
    assert len(rl.read_star(pdir / _outs(names)["micrographs"]["path"])["optics"].rows) == 3
    bad = _run(manager, puid, "relion_optics_groups", {"method": "beam_shift", "xml_folder": str(tmp_path / "nothing")},
               {"data": mics}, ok=False)
    assert bad["status"] == "failed" and "EPU" in bad["error"]


def test_topaz_denoising_and_picks_on_denoised(manager, spa):
    """topaz denoise on the micrographs; picks made on the denoised micrographs are extracted from the originals."""
    puid, ctf = spa["puid"], spa["ctf"]
    pdir = manager.project_dir(puid)
    den = _run(manager, puid, "relion_topaz_denoise", {"gpus": 0}, {"micrographs": _ref(ctf, "micrographs")})
    log = _command(manager, puid, den)
    assert f"relion_python_topaz denoise -o {den['uid']}/denoised -m unet -d -1 -s 1024 -p 500 " in log
    out = _outs(den)["micrographs"]
    names = rl.micrograph_table(rl.read_star(pdir / out["path"])).column("rlnMicrographName")
    assert names[0] == f"{den['uid']}/denoised/mic_001.mrc" and (pdir / names[0]).exists()
    assert out["meta"]["denoised"] and out["meta"]["has_ctf"] and out["meta"]["n_micrographs"] == 4
    assert any(s["title"] == "Originals and denoised" for s in _sections(manager, puid, den, "image"))
    pick = _run(manager, puid, "relion_autopick", {"method": "log", "diameter_min": 50, "diameter_max": 70, "mpi": 1},
                {"micrographs": _ref(den, "micrographs")})
    assert _outs(pick)["coordinates"]["meta"]["name_map"] == out["meta"]["name_map"]
    ext = _run(manager, puid, "relion_extract", {"box": 64, "rescale": 32, "mpi": 1},
               {"micrographs": _ref(ctf, "micrographs"), "coordinates": _ref(pick, "coordinates")})
    assert f"--coord_list {ext['uid']}/coordinates_originals.star" in _command(manager, puid, ext)
    parts = rl.particle_table(rl.read_star(pdir / _outs(ext)["particles"]["path"]))
    n_picks = _outs(pick)["coordinates"]["meta"]["n_particles"]
    assert n_picks - 5 <= len(parts) <= n_picks  # those too close to the edges are left out
    assert all("denoised" not in m for m in parts.column("rlnMicrographName"))
    assert len(set(parts.column("rlnMicrographName"))) == 4
    wrong = _run(manager, puid, "relion_extract", {"box": 64, "mpi": 1},
                 {"micrographs": _ref(den, "micrographs"), "coordinates": _ref(pick, "coordinates")}, ok=False)
    assert wrong["status"] == "failed" and "original micrographs" in wrong["error"]


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


def _live(manager, puid, juid):
    path = manager.project_dir(puid) / juid / "report.json"
    try:
        sections = json.loads(path.read_text()).get("sections", [])
    except (OSError, ValueError):  # not written yet (or being written)
        return {}
    return next((s for s in sections if s["kind"] == "live"), {})


def _wait_for(manager, puid, juid, cond, timeout=180):
    from cryoplug.scheduler import Scheduler
    sched = Scheduler(manager)
    end = time.time() + timeout
    while time.time() < end:
        sched.tick()
        job = manager.job(puid, juid)
        if job["status"] in ("failed", "killed"):
            raise AssertionError(f"{job['error']}\n{log_of(manager, puid, juid)}")
        if cond(job):
            return job
        time.sleep(0.3)
    raise TimeoutError(f"{manager.job(puid, juid)['status']}: {_live(manager, puid, juid)}")


def test_live_preprocessing(manager, movies, tmp_path):
    """Live processing: the movies arriving in a folder are imported, motion-corrected, CTF-estimated, sorted, picked
    and extracted at every cycle, only the new ones (--only_do_unfinished); the acceptance limits change from the panel,
    the session pauses and resumes, a streaming 2D classification follows the particles; Stop ends the job with its
    outputs."""
    import shutil
    folder = tmp_path / "session" / "Movies"
    folder.mkdir(parents=True)

    def arrive(*names):
        for name in names:
            tmp = folder / f".part_{name}"
            shutil.copy(movies["folder"] / name, tmp)
            tmp.replace(folder / name)  # complete at once, as a movie written by the microscope software

    def counts():
        return _live(manager, puid, uid).get("counts") or {}
    puid = manager.create_project("Live")["uid"]
    job = manager.create_job(puid, "relion_live", {
        "movies": str(folder / "*.mrc"), "pixel_size": 1.0, "dose_per_frame": 1.5, "gain": str(movies["gain"]),
        "patches_x": 1, "patches_y": 1, "threads": 2, "mpi": 1, "interval": 2, "settle": 0, "max_ctf_resolution": 8.0,
        "picking": "log", "diameter_min": 50, "diameter_max": 70, "extract": True, "box": 64, "rescale": 32,
        "class2d": True, "class2d_min": 10, "class2d_every": 100000, "class2d_classes": 2, "class2d_batches": 10}, {})
    manager.queue_job(puid, job["uid"])
    uid = job["uid"]
    live = _wait_for(manager, puid, uid, lambda j: _live(manager, puid, uid).get("status") == "waiting for movies")
    assert live["status"] == "running"
    arrive("mic_001.mrc", "mic_002.mrc")
    _wait_for(manager, puid, uid, lambda j: counts().get("particles", 0) > 0)
    assert counts()["movies"] == 2 and counts()["ctf"] == 2 and counts()["accepted"] == 2
    assert _live(manager, puid, uid)["limits"] == ["CTF fit resolution ≤ 8 Å"]
    # acceptance limits changed from the panel: sorted again at the next cycle
    manager.control_job(puid, uid, "thresholds", {"ctf_res": [None, 4.0], "nonsense": [1, 2]})
    _wait_for(manager, puid, uid, lambda j: counts().get("accepted") == 0)
    assert counts()["particles"] == 0 and _live(manager, puid, uid)["limits"] == ["CTF fit resolution ≤ 4 Å"]
    manager.control_job(puid, uid, "thresholds", {"ctf_res": [None, 8.0]})
    _wait_for(manager, puid, uid, lambda j: counts().get("accepted") == 2 and counts().get("particles", 0) > 0)
    with pytest.raises(Exception, match="limits"):
        manager.control_job(puid, uid, "thresholds", "8")
    # paused: the movies wait
    manager.control_job(puid, uid, "pause")
    _wait_for(manager, puid, uid, lambda j: _live(manager, puid, uid).get("status") == "paused")
    arrive("mic_003.mrc", "mic_004.mrc")
    time.sleep(4)
    assert counts()["movies"] == 2
    manager.control_job(puid, uid, "resume")
    _wait_for(manager, puid, uid, lambda j: counts().get("ctf") == 4)
    # the streaming 2D classification ran on the particles of the first cycle
    _wait_for(manager, puid, uid, lambda j: ((_live(manager, puid, uid).get("class2d") or {}).get("done_particles") or 0) > 0)
    with pytest.raises(Exception, match="does not take"):
        manager.control_job(puid, uid, "rewind")
    asked = manager.control_job(puid, uid, "stop")
    assert asked["message"].startswith("Stopping")
    done = _wait_for(manager, puid, uid, lambda j: j["status"] == "completed")
    o = _outs(done)
    assert o["all_micrographs"]["meta"]["n_micrographs"] == 4 and o["micrographs"]["meta"]["n_micrographs"] == 3
    assert o["rejected"]["meta"]["n_micrographs"] == 1  # the worst CTF fit (12 Å)
    assert o["movies"]["meta"]["n_movies"] == 4 and o["coordinates"]["meta"]["n_particles"] > 0
    assert o["particles"]["meta"]["n_particles"] > 0 and o["particles"]["meta"]["box"] == 32
    assert o["classes"]["type"] == "class_averages" and o["classified_particles"]["meta"]["n_particles"] > 0
    final = _live(manager, puid, uid)
    assert final["status"] == "stopped" and final["counts"]["movies"] == 4 and final["cycles"] >= 3
    log = log_of(manager, puid, uid)
    for prog in ("relion_run_motioncorr", "relion_run_ctffind", "relion_autopick", "relion_preprocess"):
        assert any(prog in line and "--only_do_unfinished" in line for line in log.splitlines()), prog
    assert "New acceptance limits: CTF fit resolution ≤ 4 Å" in log and "Resumed" in log
    report = {s.get("key"): s for s in json.loads((manager.project_dir(puid) / uid / "report.json").read_text())["sections"]}
    assert report["CTF:exposures"]["kind"] == "exposures" and report["CTF:exposures"]["thresholds"] == {"ctf_res": [None, 8.0]}
    assert "Picking:picks" in report and "live_counts" in report
    gallery = report["class2d:classes"]
    assert gallery["kind"] == "classes2d" and gallery["actions"] and not gallery["running"] and len(gallery["classes"]) == 2
    with pytest.raises(Exception, match="not running"):
        manager.control_job(puid, uid, "stop")
    # the classes of the streaming 2D classification go to a selection job
    sel = _run(manager, puid, "relion_select2d", {"mode": "list", "classes": "1"}, {"classes": _ref(done, "classes")})
    assert _outs(sel)["particles"]["meta"]["n_particles"] > 0


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


def test_clean_in_3d_workflow(manager, spa):
    """The 3D cleaning workflow: particles imported → initial model with 3 classes → heterogeneous refinement against
    them → the selection of the good classes waits for the clicks → auto-refine of the particles kept."""
    puid, ctf = spa["puid"], spa["ctf"]
    pdir = manager.project_dir(puid)
    mics = _ref(ctf, "micrographs")
    pick = _run(manager, puid, "relion_autopick", {"method": "log", "diameter_min": 50, "diameter_max": 70, "mpi": 1},
                {"micrographs": mics})
    ext = _run(manager, puid, "relion_extract", {"box": 64, "rescale": 32, "mpi": 1},
               {"micrographs": mics, "coordinates": _ref(pick, "coordinates")})
    res = manager.instantiate_workflow(puid, "relion_clean_3d", queue=True, overrides={
        "import": {"path": str(pdir / _outs(ext)["particles"]["path"])},
        "inimodel": {"iterations": 20, "threads": 1, "gpus": 0}, "hetero": {"iterations": 2, "threads": 1, "gpus": 0},
        "refine": {"threads": 1, "gpus": 0}})
    assert not res["problems"], res["problems"]
    by_type = {j["type"]: j for j in res["jobs"]}
    hetero = by_type["relion_hetero_refine"]
    assert {k: v["output"] for k, v in hetero["inputs"].items() if k.startswith("ref_")} == {
        "ref_1": "map_1", "ref_2": "map_2", "ref_3": "map_3", "ref_4": "map_4"}
    uids = [j["uid"] for j in res["jobs"]]
    select = by_type["relion_select3d"]
    run_until_done(manager, puid, uids[:uids.index(select["uid"]) + 1], timeout=300)
    assert manager.job(puid, select["uid"])["status"] == "waiting", log_of(manager, puid, hetero["uid"])
    assert "--ref" in _command(manager, puid, hetero)
    done = manager.finish_interactive(puid, select["uid"], json.dumps({"classes": [1, 2]}))
    assert done["status"] == "completed"
    run_until_done(manager, puid, uids, timeout=300)
    states = {j["type"]: manager.job(puid, j["uid"])["status"] for j in res["jobs"]}
    assert all(s == "completed" for s in states.values()), states


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
    assert info["stride"] == 3 and not info.get("has_power") and info["pick_type"] == "particles"
    raw = np.frombuffer((pdir / info["binary"]).read_bytes(), dtype="<f4").reshape(-1, 3)
    assert len(raw) == info["n_picks"] == _outs(pick)["coordinates"]["meta"]["n_particles"]
    assert info["micrographs"][0]["shape"] == [384, 384] and info["has_fom"]

    insp = _run(manager, puid, "relion_pick_inspect", {}, {"micrographs": mics, "coordinates": _ref(pick, "coordinates")},
                status="waiting")
    tool = _sections(manager, puid, insp, "picks")[0]
    assert tool["waiting"] and tool["editable"] and not tool["manual"] and "power range" in tool["prompt"]
    info = json.loads((pdir / tool["data"]).read_text())
    assert info["stride"] == 4 and info["has_power"] and info["power_range"][0] < info["power_range"][1]
    raw = np.frombuffer((pdir / info["binary"]).read_bytes(), dtype="<f4").reshape(-1, 4)
    assert np.isfinite(raw[:, 3]).all() and (raw[:, 3] >= 0).all()
    first, second = info["micrographs"][0], info["micrographs"][1]
    threshold = float(np.median(raw[:, 2]))
    top_power = float(np.quantile(raw[:, 3], 0.8))  # the 20 % of highest power left out
    kept = (raw[:, 2] >= threshold) & (raw[:, 3] <= top_power)
    assert 0 < kept.sum() < (raw[:, 2] >= threshold).sum()
    removed = [i for i in range(first["count"]) if kept[first["offset"] + i]][:2]
    edits = {first["name"]: {"removed": removed, "added": []},
             second["name"]: {"removed": [], "added": [[100.0, 120.0], [200.5, 50.0], [9999.0, 5.0]]}}  # the last is outside
    done = manager.finish_interactive(puid, insp["uid"], json.dumps({"threshold": threshold, "power_range": [None, top_power],
                                                                     "edits": edits}))
    out = _outs(done)["coordinates"]
    assert done["status"] == "completed" and out["meta"]["n_particles"] == int(kept.sum()) - len(removed) + 2
    rows = dict(next(s for s in _sections(manager, puid, done, "table") if s["title"] == "Picks kept")["rows"])
    assert rows["Power range"].startswith("– to ")
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


def test_local_power_of_the_picks(tmp_path):
    """The power score is high on contamination (dark blob), low on empty ice."""
    import mrcfile

    from cryoplug.jobs.relion_picking import local_power
    img = np.random.default_rng(3).normal(0, 1, (512, 512)).astype(np.float32)
    yy, xx = np.indices(img.shape)
    img[np.hypot(xx - 400, yy - 120) < 30] -= 12.0  # ice crystal / contamination
    with mrcfile.new(tmp_path / "m.mrc") as m:
        m.set_data(img)
        m.voxel_size = 1.0
    power = local_power(tmp_path / "m.mrc", np.array([[400.0, 120.0], [100.0, 400.0], [-50.0, 9999.0]]), 60, 1.0)
    assert power[0] > 10 * power[1] and np.isfinite(power).all()


def test_filament_picking_and_helical_extraction(manager, spa):
    """Filaments clicked by their start and end are written as RELION's manual picking of helices writes them
    ("startend" coordinates); the extraction refuses them without helical processing and cuts them into segments."""
    puid, ctf = spa["puid"], spa["ctf"]
    pdir = manager.project_dir(puid)
    mics = _ref(ctf, "micrographs")
    bad = manager.create_job(puid, "relion_pick_inspect", {"mode": "filaments"}, {"micrographs": mics})
    with pytest.raises(Exception, match="tube diameter"):
        manager.queue_job(puid, bad["uid"])
    fil = _run(manager, puid, "relion_pick_inspect", {"mode": "filaments", "diameter": 60}, {"micrographs": mics},
               status="waiting")
    tool = _sections(manager, puid, fil, "picks")[0]
    assert tool["mode"] == "filaments" and tool["title"] == "Filament picking" and "end of each filament" in tool["prompt"]
    info = json.loads((pdir / tool["data"]).read_text())
    a, b = info["micrographs"][0]["name"], info["micrographs"][1]["name"]
    filaments = {a: [[50, 50, 330, 90], [60, 120, 70, 330], [10, 10, 10, 10]],  # the last has no length
                 b: [[100, 100, 260, 300], [5, 5, 9999, 5]], "nowhere.mrc": [[1, 2, 3, 4]]}  # outside, unknown
    manager.write_session(puid, fil["uid"], "picks", {"filaments": filaments})
    done = manager.finish_interactive(puid, fil["uid"], "")
    out = _outs(done)["coordinates"]
    assert done["status"] == "completed" and out["label"] == "3 filaments"
    assert out["meta"]["n_filaments"] == 3 and out["meta"]["pick_type"] == "startend" and out["meta"]["filament_length"] > 0
    listing = pdir / out["path"]
    assert rl.coordinate_pick_type(listing) == "startend" and "_rlnMicrographPickType" in listing.read_text()
    pairs = dict(rl.coordinate_files(listing))
    xy, _ = rl.read_coordinates(pdir / pairs[a])
    assert xy.tolist() == [[50, 50], [330, 90], [60, 120], [70, 330]]
    browser = _sections(manager, puid, done, "picks")[-1]
    assert browser["mode"] == "filaments" and not browser.get("waiting")
    assert any(s["title"] == "Length of the filaments" for s in _sections(manager, puid, done, "plot"))
    coords = {"micrographs": mics, "coordinates": _ref(done, "coordinates")}
    no = _run(manager, puid, "relion_extract", {"box": 64, "mpi": 1}, coords, ok=False)
    assert no["status"] == "failed" and "Helical processing" in no["error"]
    ext = _run(manager, puid, "relion_extract", {"box": 64, "mpi": 1, "helical": True, "helical_rise": 4.75,
                                                 "helical_outer_diameter": 60}, coords)
    log = _command(manager, puid, ext)
    assert "--helix" in log and "--helical_cut_into_segments" in log
    table = dict(next(s for s in _sections(manager, puid, ext, "table") if s["title"] == "Extraction")["rows"])
    assert table["Source"].startswith("3 filaments of")
    parts = rl.particle_table(rl.read_star(pdir / _outs(ext)["particles"]["path"]))
    assert len(parts) > 3 * 10 and set(parts.column("rlnHelicalTubeID")) == {"1", "2"}
    assert float(parts.column("rlnAngleTiltPrior")[0]) == 90.0


def test_curation_counts_the_particles_of_each_micrograph(manager, spa):
    """With picks or particles connected, the curation of micrographs limits their number per micrograph and passes
    on those of the micrographs kept."""
    puid, ctf = spa["puid"], spa["ctf"]
    pdir = manager.project_dir(puid)
    mics = _ref(ctf, "micrographs")
    pick = _run(manager, puid, "relion_autopick", {"method": "log", "diameter_min": 50, "diameter_max": 70, "mpi": 1},
                {"micrographs": mics})
    counts = {m: rl.star_rows(pdir / c) for m, c in rl.coordinate_files(pdir / _outs(pick)["coordinates"]["path"])}
    limit = sorted(counts.values())[len(counts) // 2]
    wanted = {m for m, c in counts.items() if c >= limit}
    assert 0 < len(wanted) < len(counts)
    cur = _run(manager, puid, "relion_curate", {"mode": "thresholds", "min_particles": limit},
               {"micrographs": mics, "particles": _ref(pick, "coordinates")})
    out = _outs(cur)
    kept = rl.micrograph_table(rl.read_star(pdir / out["micrographs"]["path"])).column("rlnMicrographName")
    assert set(kept) == wanted
    picks = dict(rl.coordinate_files(pdir / out["coordinates"]["path"]))
    assert set(picks) == wanted and out["coordinates"]["meta"]["n_particles"] == sum(counts[m] for m in wanted)
    rows = dict(next(s for s in _sections(manager, puid, cur, "table") if s["title"] == "Rejected by")["rows"])
    assert rows[f"Particles ≥ {limit}"] == str(len(counts) - len(wanted))
    ext = _run(manager, puid, "relion_extract", {"box": 64, "rescale": 32, "mpi": 1},
               {"micrographs": mics, "coordinates": _ref(pick, "coordinates")})
    cur2 = _run(manager, puid, "relion_curate", {"mode": "thresholds", "max_particles": limit - 1},
                {"micrographs": mics, "particles": _ref(ext, "particles")})
    parts = rl.particle_table(rl.read_star(pdir / _outs(cur2)["particles"]["path"]))
    assert parts and set(parts.column("rlnMicrographName")) <= set(counts) - wanted
    bad = _run(manager, puid, "relion_curate", {"mode": "thresholds", "min_particles": 5}, {"micrographs": mics}, ok=False)
    assert bad["status"] == "failed" and "connect the particles" in bad["error"]


def test_restack_and_micrograph_sets(manager, spa):
    """Restack particles (relion_stack_create, one stack per micrograph, re-centred) and two micrograph sets compared."""
    puid, ctf, mc = spa["puid"], spa["ctf"], spa["motioncorr"]
    pdir = manager.project_dir(puid)
    mics = _ref(ctf, "micrographs")
    pick = _run(manager, puid, "relion_autopick", {"method": "log", "diameter_min": 50, "diameter_max": 70, "mpi": 1},
                {"micrographs": mics})
    ext = _run(manager, puid, "relion_extract", {"box": 64, "rescale": 32, "mpi": 1},
               {"micrographs": mics, "coordinates": _ref(pick, "coordinates")})
    c2d = _run(manager, puid, "relion_class2d", {"classes": 2, "vdam_batches": 10, "threads": 1, "gpus": 0},
               {"particles": _ref(ext, "particles")})
    no = _run(manager, puid, "relion_restack", {"recentre": True}, {"particles": _ref(ext, "particles")}, ok=False)
    assert no["status"] == "failed" and "shifts" in no["error"]
    rs = _run(manager, puid, "relion_restack", {"recentre": True}, {"particles": _ref(c2d, "particles")})
    log = _command(manager, puid, rs)
    assert (f"relion_stack_create --i {_outs(c2d)['particles']['path']} --o {rs['uid']}/particles --split_per_micrograph "
            "--apply_rounded_offsets_only --float16") in log
    before = rl.particle_table(rl.read_star(pdir / _outs(c2d)["particles"]["path"]))
    after = rl.particle_table(rl.read_star(pdir / _outs(rs)["particles"]["path"]))
    assert len(after) == len(before) and all(n.split("@")[1].startswith(f"{rs['uid']}/particles_") for n in after.column("rlnImageName"))
    assert np.abs(after.floats("rlnOriginXAngst")).max() <= 1.0 + 1e-6  # whole pixels applied, less than one left
    from cryoplug.particles import ParticleImages
    images = ParticleImages(pdir / _outs(rs)["particles"]["path"], pdir)
    assert images.n == len(after) and images.image(0).shape == (32, 32)
    # micrograph sets: the CTF micrographs against a curation of them
    cur = _run(manager, puid, "relion_curate", {"mode": "thresholds", "max_defocus": 15000}, {"micrographs": mics})
    sets = _run(manager, puid, "relion_particle_sets", {}, {"set_a": mics, "set_b": _ref(cur, "micrographs")})
    out = _outs(sets)
    kept = rl.star_rows(pdir / _outs(cur)["micrographs"]["path"])
    assert out["both"]["type"] == "micrographs" and out["both"]["meta"]["n_micrographs"] == kept
    assert out.get("a_only", {}).get("meta", {}).get("n_micrographs", 0) == 4 - kept and "b_only" not in out
    other = _run(manager, puid, "relion_particle_sets", {}, {"set_a": _ref(mc, "micrographs"), "set_b": _ref(cur, "micrographs")})
    assert _outs(other)["both"]["meta"]["n_micrographs"] == kept  # motion-corrected and CTF micrographs: the same names
    mixed = _run(manager, puid, "relion_particle_sets", {}, {"set_a": mics, "set_b": _ref(ext, "particles")}, ok=False)
    assert mixed["status"] == "failed" and "not one of each" in mixed["error"]


def test_particle_curation_by_statistics(manager, spa):
    """Particles kept on their statistics: interactive (limits on the distributions, images over the API), at once
    with written limits, or split into clusters of one statistic (Gaussian mixture)."""
    from fastapi.testclient import TestClient

    from cryoplug.server.app import create_app
    puid, ctf = spa["puid"], spa["ctf"]
    pdir = manager.project_dir(puid)
    mics = _ref(ctf, "micrographs")
    pick = _run(manager, puid, "relion_autopick", {"method": "log", "diameter_min": 50, "diameter_max": 70, "mpi": 1},
                {"micrographs": mics})
    ext = _run(manager, puid, "relion_extract", {"box": 64, "rescale": 32, "mpi": 1},
               {"micrographs": mics, "coordinates": _ref(pick, "coordinates")})
    c2d = _run(manager, puid, "relion_class2d", {"classes": 2, "vdam_batches": 10, "threads": 1, "gpus": 0},
               {"particles": _ref(ext, "particles")})
    parts = _ref(c2d, "particles")
    ptab = rl.particle_table(rl.read_star(pdir / _outs(c2d)["particles"]["path"]))
    n = len(ptab)
    prob = ptab.floats("rlnMaxValueProbDistribution")

    cur = _run(manager, puid, "relion_particle_curate", {"limits": "max_prob 0.3 -"}, {"particles": parts}, status="waiting")
    tool = _sections(manager, puid, cur, "particle_stats")[0]
    assert tool["waiting"] and tool["limits"] == {"max_prob": [0.3, None]}
    info = json.loads((pdir / tool["data"]).read_text())
    keys = [m["key"] for m in info["metrics"]]
    assert {"max_prob", "n_significant", "loglike", "norm", "fom", "defocus", "per_micrograph"} <= set(keys)
    assert "shift" not in keys  # the same for every particle: nothing to curate
    assert info["n"] == info["n_display"] == n and info["source"] == {"job": c2d["uid"], "output": "particles"}
    codes = np.frombuffer((pdir / info["binary"]).read_bytes(), dtype="<u2").reshape(len(keys), n)
    m = info["metrics"][keys.index("max_prob")]
    decoded = m["lo"] + codes[keys.index("max_prob")] / 65534 * (m["hi"] - m["lo"])
    assert np.allclose(decoded, prob, atol=(m["hi"] - m["lo"]) / 60000 + 1e-6)
    manager.write_session(puid, cur["uid"], "pcurate", {"limits": {"max_prob": [0.5, None], "norm": [None, 1.1]}})
    done = manager.finish_interactive(puid, cur["uid"], "")
    norm = ptab.floats("rlnNormCorrection")
    expect = int(((prob >= 0.5) & (norm <= 1.1)).sum())
    out = _outs(done)
    assert done["status"] == "completed" and out["particles"]["meta"]["n_particles"] == expect
    assert out["rejected"]["meta"]["n_particles"] == n - expect
    final = _sections(manager, puid, done, "particle_stats")[0]
    assert not final["waiting"] and final["kept"] == expect and final["limits"]["norm"] == [None, 1.1]
    rows = dict(next(s for s in _sections(manager, puid, done, "table") if s["title"] == "Rejected by")["rows"])
    assert rows["Probability of the best pose: ≥ 0.5"] == f"{int((prob < 0.5).sum()):,}"

    thr = _run(manager, puid, "relion_particle_curate", {"mode": "thresholds", "limits": "rlnMaxValueProbDistribution 0.5 -\nnorm - 1.1"},
               {"particles": parts})
    assert _outs(thr)["particles"]["meta"]["n_particles"] == expect
    bad = _run(manager, puid, "relion_particle_curate", {"mode": "thresholds", "limits": "nothing 1 2"}, {"particles": parts}, ok=False)
    assert bad["status"] == "failed" and "available" in bad["error"] and "max_prob" in bad["error"]
    job = manager.create_job(puid, "relion_particle_curate", {"mode": "thresholds"}, {"particles": parts})
    with pytest.raises(Exception, match="at least one limit"):
        manager.queue_job(puid, job["uid"])

    clu = _run(manager, puid, "relion_particle_curate", {"mode": "cluster", "statistic": "max_prob", "clusters": 2},
               {"particles": parts})
    outs = _outs(clu)
    sizes = [outs[k]["meta"]["n_particles"] for k in ("cluster_1", "cluster_2")]
    assert sum(sizes) == n and min(sizes) > 0
    low = rl.particle_table(rl.read_star(pdir / outs["cluster_1"]["path"])).floats("rlnMaxValueProbDistribution")
    high = rl.particle_table(rl.read_star(pdir / outs["cluster_2"]["path"])).floats("rlnMaxValueProbDistribution")
    assert low.mean() < high.mean()
    assert any(s["title"] == "Probability of the best pose: clusters" for s in _sections(manager, puid, clu, "plot"))

    app = create_app(manager.config, start_scheduler=False, manager=manager)
    with TestClient(app, base_url="http://localhost:39500") as client:
        r = client.get(f"/api/projects/{puid}/jobs/{c2d['uid']}/particle-images", params={"output": "particles", "ids": "0,3,5", "size": 48})
        assert r.status_code == 200 and r.content[:4] == b"\x89PNG", r.text
        r = client.get(f"/api/projects/{puid}/jobs/{c2d['uid']}/particle-images", params={"output": "classes", "ids": "0"})
        assert r.status_code == 404


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


def test_mark_complete_and_resume_2d(manager, spa):
    """A stopped iterative run gives the outputs of its last iteration (CryoSPARC's Mark as complete); a 2D
    classification goes on from its last iteration too."""
    import os
    puid, ctf = spa["puid"], spa["ctf"]
    pdir = manager.project_dir(puid)
    mics = _ref(ctf, "micrographs")
    pick = _run(manager, puid, "relion_autopick", {"method": "log", "diameter_min": 50, "diameter_max": 70, "mpi": 1},
                {"micrographs": mics})
    ext = _run(manager, puid, "relion_extract", {"box": 64, "rescale": 32, "mpi": 1},
               {"micrographs": mics, "coordinates": _ref(pick, "coordinates")})
    parts = _ref(ext, "particles")
    MapVolume(data=np.random.default_rng(5).normal(0, 1, (32, 32, 32)).astype(np.float32), voxel=np.full(3, 2.0)).write(pdir / "ref.mrc")
    ref = _ref(_run(manager, puid, "import_maps", {"map": str(pdir / "ref.mrc"), "compute_fsc": False}), "map")

    def crash(job, keep, status="failed"):  # as if the run had stopped after iteration ``keep``
        for f in (pdir / job["uid"]).iterdir():
            m = re.match(r"run_(?:ct\d+_)?it(\d+)_", f.name)
            if (m and int(m.group(1)) > keep) or re.match(r"run_(class001|half\d_class001_unfil|data|model|optimiser)\.", f.name):
                os.unlink(f)
        manager.db.update_job(puid, job["uid"], status=status, outputs=[], highlights=[], error="relion_refine exited with code 1")

    # 2D classification: resumed (EM, 5 iterations, stopped after 2), then marked complete after a second stop
    c2 = _run(manager, puid, "relion_class2d", {"classes": 3, "algorithm": "em", "em_iterations": 5, "threads": 1, "gpus": 0},
              {"particles": parts})
    with pytest.raises(Exception, match="failed or killed"):
        manager.mark_complete(puid, c2["uid"])
    crash(c2, 2)
    manager.continue_job(puid, c2["uid"])
    run_until_done(manager, puid, [c2["uid"]], timeout=180)
    done = manager.job(puid, c2["uid"])
    assert done["status"] == "completed", done["error"]
    assert f"--continue {c2['uid']}/run_it002_optimiser.star --o {c2['uid']}/run_ct2" in _command(manager, puid, done)
    assert "--iter 5" in _command(manager, puid, done)
    assert _outs(done)["particles"]["path"].endswith("run_ct2_it005_data.star")

    crash(c2, 3, status="killed")
    marked = manager.mark_complete(puid, c2["uid"])
    assert marked["status"] == "completed" and marked["error"] == "" and marked["message"] == "Marked as complete (iteration 3)"
    out = _outs(marked)
    assert out["particles"]["path"].endswith("run_ct2_it003_data.star") and out["classes"]["meta"]["n_classes"] == 3
    assert "Job marked as complete (iteration 3)" in (pdir / c2["uid"] / "job.log").read_text()
    with pytest.raises(Exception, match="failed or killed"):
        manager.mark_complete(puid, c2["uid"])

    # 3D auto-refine stopped at iteration 2: the map is the average of that iteration's half maps
    r3 = _run(manager, puid, "relion_refine3d", {"threads": 1, "gpus": 0}, {"particles": parts, "map": ref})
    crash(r3, 2)
    marked = manager.mark_complete(puid, r3["uid"])
    assert marked["status"] == "completed", marked
    out = _outs(marked)
    assert {"map", "half_maps", "particles"} <= set(out) and out["particles"]["path"].endswith("run_it002_data.star")
    assert (pdir / r3["uid"] / "run_it002_halves_average.mrc").exists()
    assert {h["label"]: h["value"] for h in marked["highlights"]}["Stopped at"] == "iteration 2"
    assert any("FSC" in s["title"] for s in _sections(manager, puid, marked, "plot"))

    # 3D classification and initial model
    c3 = _run(manager, puid, "relion_class3d_global", {"classes": 2, "iterations": 4, "threads": 1, "gpus": 0},
              {"particles": parts, "map": ref})
    crash(c3, 2)
    out = _outs(manager.mark_complete(puid, c3["uid"]))
    assert out["map_1"]["path"].endswith("run_it002_class001.mrc") and out["particles"]["path"].endswith("run_it002_data.star")
    ini = _run(manager, puid, "relion_initial_model", {"iterations": 20, "classes": 1, "symmetry": "C2", "threads": 1, "gpus": 0},
               {"particles": parts})
    manager.db.update_job(puid, ini["uid"], status="killed", outputs=[])
    out = _outs(manager.mark_complete(puid, ini["uid"]))
    assert out["map"]["meta"]["symmetry"] == "C2" and out["particles"]["path"].endswith("run_it020_data.star")

    # jobs that cannot be marked complete, or with nothing written
    sel = _run(manager, puid, "relion_select3d", {"mode": "list", "classes": "1"}, {"particles": _ref(c3, "particles")})
    manager.db.update_job(puid, sel["uid"], status="failed")
    with pytest.raises(Exception, match="cannot be marked as complete"):
        manager.mark_complete(puid, sel["uid"])
    empty = manager.create_job(puid, "relion_refine3d", {"threads": 1, "gpus": 0}, {"particles": parts, "map": ref})
    manager.db.update_job(puid, empty["uid"], status="killed")
    with pytest.raises(Exception, match="never ran"):
        manager.mark_complete(puid, empty["uid"])
    assert manager.job(puid, empty["uid"])["status"] == "killed"


def test_clear_intermediate_results(manager, spa):
    """Sizes of the job folders, and the files of every iteration but the last cleared (CryoSPARC's Clear intermediate
    results), the outputs kept."""
    puid, ctf = spa["puid"], spa["ctf"]
    pdir = manager.project_dir(puid)
    mics = _ref(ctf, "micrographs")
    pick = _run(manager, puid, "relion_autopick", {"method": "log", "diameter_min": 50, "diameter_max": 70, "mpi": 1},
                {"micrographs": mics})
    ext = _run(manager, puid, "relion_extract", {"box": 64, "rescale": 32, "mpi": 1},
               {"micrographs": mics, "coordinates": _ref(pick, "coordinates")})
    c2 = _run(manager, puid, "relion_class2d", {"classes": 3, "algorithm": "em", "em_iterations": 6, "threads": 1, "gpus": 0},
              {"particles": _ref(ext, "particles")})
    assert c2["disk_bytes"] and c2["disk_bytes"] > 1000  # measured by the worker when the job ended
    jdir = pdir / c2["uid"]
    before = sorted(f.name for f in jdir.glob("run_it*"))
    assert any(n.startswith("run_it001_") for n in before) and any(n.startswith("run_it006_") for n in before)

    res = manager.clear_intermediate(puid, c2["uid"])
    after = sorted(f.name for f in jdir.glob("run_it*"))
    assert res["last_iteration"] == 6 and res["files"] == len(before) - len(after) and res["freed_bytes"] > 0
    assert after and all(n.startswith("run_it006_") for n in after)
    job = manager.job(puid, c2["uid"])
    assert job["disk_bytes"] == res["disk_bytes"] < c2["disk_bytes"]
    for out in job["outputs"]:
        assert all((pdir / f).exists() for f in out["files"])
    assert "Intermediate results cleared" in (jdir / "job.log").read_text()
    again = manager.clear_intermediate(puid, c2["uid"])
    assert again["files"] == 0 and again["freed_bytes"] == 0
    assert any(p["uid"] == puid and p["disk_bytes"] > 0 for p in manager.db.list_projects())

    with pytest.raises(Exception, match="keeps no intermediate results"):
        manager.clear_intermediate(puid, ext["uid"])
    pending = manager.create_job(puid, "relion_class2d", {"threads": 1, "gpus": 0}, {"particles": _ref(ext, "particles")})
    with pytest.raises(Exception, match="Only a job that is over"):
        manager.clear_intermediate(puid, pending["uid"])
    # a job that ended before the sizes were kept is measured when its details are read
    manager.db.update_job(puid, ext["uid"], disk_bytes=None)
    assert manager.job_detail(puid, ext["uid"])["disk_bytes"] > 0 and manager.job(puid, ext["uid"])["disk_bytes"] > 0


def test_import_coordinates_and_templates(manager, spa, tmp_path):
    """Picks of other programs (STAR, BOX, Topaz, CryoSPARC) matched to the micrographs by name; 2D templates for
    template picking."""
    import mrcfile
    puid, ctf = spa["puid"], spa["ctf"]
    mics = _ref(ctf, "micrographs")
    picks = tmp_path / "picks"
    picks.mkdir()
    (picks / "mic_001_autopick.star").write_text("\ndata_\n\nloop_\n_rlnCoordinateX #1\n_rlnCoordinateY #2\n"
                                                 "_rlnAutopickFigureOfMerit #3\n100.0 120.0 0.9\n200.0 220.0 0.5\n")
    (picks / "mic_002.box").write_text("90 110 20 20\n190 210 20 20\n290 310 20 20\n")
    (picks / "topaz.txt").write_text("image_name\tx_coord\ty_coord\tscore\nmic_003\t50\t60\t1.5\nmic_003\t150\t160\t-0.5\n")
    cs = np.zeros(2, dtype=[("uid", "<u8"), ("location/micrograph_path", "S64"), ("location/center_x_frac", "<f4"),
                            ("location/center_y_frac", "<f4"), ("location/micrograph_shape", "<u4", (2,))])
    cs["location/micrograph_path"] = b"J9/motioncorrected/mic_004_patch_aligned_doseweighted.mrc"
    cs["location/center_x_frac"], cs["location/center_y_frac"] = [0.25, 0.5], [0.5, 0.75]
    cs["location/micrograph_shape"] = [[400, 400], [400, 400]]
    np.save(picks / "picked.npy", cs)
    (picks / "picked.npy").rename(picks / "picked_particles.cs")
    (picks / "other_movie.box").write_text("1 1 10 10\n")  # matches no micrograph
    imp = _run(manager, puid, "import_coordinates", {"path": f"{picks}/*.star; {picks}/*.box; {picks}/topaz.txt; {picks}/*.cs",
                                                     "scale": 1.0, "diameter": 60}, {"micrographs": mics})
    out = _outs(imp)["coordinates"]
    assert out["meta"]["n_particles"] == 2 + 3 + 2 + 2 and out["meta"]["n_micrographs"] == 4
    pdir = manager.project_dir(puid)
    listing = rl.read_star(pdir / out["path"])["coordinate_files"]
    files = dict(zip([Path(m).name for m in listing.column("rlnMicrographName")], listing.column("rlnMicrographCoordinates")))
    xy, fom = rl.read_coordinates(pdir / files["mic_002.mrc"])
    assert xy.tolist() == [[100.0, 120.0], [200.0, 220.0], [300.0, 320.0]]  # box corners → centres
    xy, _ = rl.read_coordinates(pdir / files["mic_004.mrc"])
    assert xy.tolist() == [[100.0, 200.0], [200.0, 300.0]]  # CryoSPARC fractions × micrograph shape
    xy, fom = rl.read_coordinates(pdir / files["mic_003.mrc"])
    assert xy.tolist() == [[50.0, 60.0], [150.0, 160.0]] and fom.tolist() == [1.5, -0.5]
    assert "1 coordinate set(s) match no micrograph: other_movie.box" in log_of(manager, puid, imp["uid"])
    ext = _run(manager, puid, "relion_extract", {"box": 64, "rescale": 32, "mpi": 1}, {"micrographs": mics, "coordinates": _ref(imp, "coordinates")})
    assert _outs(ext)["particles"]["meta"]["n_particles"] > 0
    scaled = _run(manager, puid, "import_coordinates", {"path": f"{picks}/mic_002.box", "scale": 0.5}, {"micrographs": mics})
    listing = rl.read_star(pdir / _outs(scaled)["coordinates"]["path"])["coordinate_files"]
    assert rl.read_coordinates(pdir / listing.column("rlnMicrographCoordinates")[0])[0].tolist()[0] == [50.0, 60.0]
    bad = _run(manager, puid, "import_coordinates", {"path": f"{picks}/other_movie.box"}, {"micrographs": mics}, ok=False)
    assert "matches a micrograph" in bad["error"]

    # 2D templates: a stack and a single image, inverted, for template picking
    with mrcfile.new(str(tmp_path / "refs.mrcs"), overwrite=True) as mrc:
        mrc.set_data(np.random.default_rng(1).normal(0, 1, (2, 32, 32)).astype(np.float32))
        mrc.voxel_size = 2.0
    with mrcfile.new(str(tmp_path / "one.mrc"), overwrite=True) as mrc:
        mrc.set_data(np.ones((32, 32), np.float32))
    tpl = _run(manager, puid, "import_templates", {"path": f"{tmp_path}/refs.mrcs; {tmp_path}/one.mrc", "invert": True})
    meta = _outs(tpl)["classes"]["meta"]
    assert (meta["n_classes"], meta["box"], meta["pixel_size"]) == (3, 32, 2.0)
    with mrcfile.open(str(pdir / _outs(tpl)["classes"]["path"])) as mrc:
        assert float(mrc.data[2].mean()) == -1.0
    pick = _run(manager, puid, "relion_autopick", {"method": "template", "mpi": 1, "gpus": 0},
                {"micrographs": mics, "references": _ref(tpl, "classes")})
    assert f"--ref {tpl['uid']}/templates.mrcs" in _command(manager, puid, pick)


def test_simulate_particles(manager, spa):
    """relion_project: random orientations with noise, or the orientations and CTFs of real particles."""
    puid, ctf = spa["puid"], spa["ctf"]
    pdir = manager.project_dir(puid)
    mics = _ref(ctf, "micrographs")
    pick = _run(manager, puid, "relion_autopick", {"method": "log", "diameter_min": 50, "diameter_max": 70, "mpi": 1},
                {"micrographs": mics})
    ext = _run(manager, puid, "relion_extract", {"box": 64, "rescale": 32, "mpi": 1},
               {"micrographs": mics, "coordinates": _ref(pick, "coordinates")})
    MapVolume(data=np.random.default_rng(5).normal(0, 1, (32, 32, 32)).astype(np.float32), voxel=np.full(3, 2.0)).write(pdir / "ref.mrc")
    ref = _ref(_run(manager, puid, "import_maps", {"map": str(pdir / "ref.mrc"), "compute_fsc": False}), "map")
    uni = _run(manager, puid, "relion_simulate", {"n_particles": 25, "noise": 2.0, "sigma_offset": 3}, {"map": ref})
    assert (f"relion_project --i {ref['job']}/" in _command(manager, puid, uni)
            and f"--o {uni['uid']}/simulated --angpix 2 --nr_uniform 25 --sigma_offset 3 --add_noise --white_noise 2" in _command(manager, puid, uni))
    out = _outs(uni)["particles"]
    assert out["meta"]["n_particles"] == 25 and out["meta"]["has_poses"] and out["meta"]["box"] == 32
    # the orientations and CTFs of particles refined against the map (extracted ones have no orientations)
    bad = _run(manager, puid, "relion_simulate", {"mode": "particles"}, {"map": ref, "particles": _ref(ext, "particles")}, ok=False)
    assert bad["status"] == "failed" and "no orientations" in bad["error"]
    r3 = _run(manager, puid, "relion_refine3d", {"threads": 1, "gpus": 0}, {"particles": _ref(ext, "particles"), "map": ref})
    real = _run(manager, puid, "relion_simulate", {"mode": "particles"}, {"map": ref, "particles": _ref(r3, "particles")})
    assert f"--ang {_outs(r3)['particles']['path']} --ctf" in _command(manager, puid, real)
    assert _outs(real)["particles"]["meta"]["n_particles"] == _outs(ext)["particles"]["meta"]["n_particles"]
    gt = _run(manager, puid, "relion_simulate", {"mode": "realistic", "snr_factor": 0.5}, {"map": ref, "particles": _ref(r3, "particles")})
    assert "--simulate --adjust_simulation_SNR 0.5" in _command(manager, puid, gt) and "--add_noise" not in _command(manager, puid, gt)
    job = manager.create_job(puid, "relion_simulate", {"mode": "particles"}, {"map": ref})
    with pytest.raises(Exception, match="Connect the particles"):
        manager.queue_job(puid, job["uid"])


def test_symmetry_search(manager, tmp_path):
    """Symmetry search (CryoSPARC's Symmetry Search Utility): relion_align_symmetry for every group tried, then the map
    against its symmetrised copy; the highest group obeyed is proposed with the map on its axes. A map without symmetry
    gives C1 and no map."""
    p = manager.create_project("Symmetry search")
    puid = p["uid"]
    n = 48
    z, y, x = np.indices((n, n, n)) - n // 2

    def blob(cx, cy, cz, s=2.5):
        return np.exp(-((x - cx) ** 2 + (y - cy) ** 2 + (z - cz) ** 2) / (2 * s * s))
    c4 = sum(blob(9 * np.cos(a), 9 * np.sin(a), 5) for a in np.radians([20, 110, 200, 290])) + 1.2 * blob(0, 0, -7)
    MapVolume(data=c4.astype(np.float32), voxel=np.full(3, 2.0)).write(tmp_path / "c4.mrc")
    imp = _run(manager, puid, "import_maps", {"map": str(tmp_path / "c4.mrc"), "compute_fsc": False})
    bad = manager.create_job(puid, "relion_symmetry_search", {"groups": "C2, Q7"}, {"map": _ref(imp, "map")})
    with pytest.raises(Exception, match="not a point group"):
        manager.queue_job(puid, bad["uid"])
    job = _run(manager, puid, "relion_symmetry_search", {"groups": "C2-C6, D2-D4, T, O, I"}, {"map": _ref(imp, "map")})
    log = _command(manager, puid, job)
    assert log.count("relion_align_symmetry") >= 11 and "--sym C4" in log and "--sym I" in log
    out = _outs(job)["map"]
    assert out["meta"]["symmetry"] == "C4" and out["meta"]["symmetry_cc"] > 0.95
    rows = {r[0]: r for r in _sections(manager, puid, job, "table")[0]["rows"]}
    assert rows["C4"][4] == "proposed" and rows["C2"][3] == "yes" and rows["C3"][3] == "–" and rows["D4"][3] == "–"
    assert rows["T"][1] == 12 and rows["O"][1] == 24 and rows["I"][1] == 60
    lone = blob(6, 0, 0) + 0.8 * blob(-3, 7, 2) + 0.5 * blob(-2, -6, -5)
    MapVolume(data=lone.astype(np.float32), voxel=np.full(3, 2.0)).write(tmp_path / "c1.mrc")
    imp1 = _run(manager, puid, "import_maps", {"map": str(tmp_path / "c1.mrc"), "compute_fsc": False})
    none = _run(manager, puid, "relion_symmetry_search", {"groups": "C2-C4, D2"}, {"map": _ref(imp1, "map")})
    assert "map" not in _outs(none) and "No group tried reaches" in log_of(manager, puid, none["uid"])


def test_pick_tuner(manager, spa):
    """Tune LoG picking (CryoSPARC's Blob Picker Tuner): relion_autopick for every diameter range × threshold on a few
    micrographs, their picks side by side; the settings chosen go to an auto-picking of all the micrographs."""
    puid, ctf = spa["puid"], spa["ctf"]
    pdir = manager.project_dir(puid)
    mics = {"micrographs": _ref(ctf, "micrographs")}
    for params, error in (({"diameters": "70-50"}, "minimum is larger"), ({"thresholds": "low, high"}, "numbers separated"),
                          ({"diameters": "1-2; 2-3; 3-4; 4-5; 5-6", "thresholds": "-2, -1, 0, 1, 2"}, "at most 24")):
        bad = manager.create_job(puid, "relion_pick_tuner", params, mics)
        with pytest.raises(Exception, match=error):
            manager.queue_job(puid, bad["uid"])
    job = _run(manager, puid, "relion_pick_tuner", {"diameters": "40-60; 50-70", "thresholds": "-0.5, 0.5",
                                                    "n_micrographs": 2}, mics)
    log = _command(manager, puid, job)
    assert log.count("relion_autopick --i") == 4 and f"--i {job['uid']}/micrographs_subset.star" in log
    assert "--LoG --LoG_diam_min 50 --LoG_diam_max 70 --shrink 0 --lowpass 20 --LoG_adjust_threshold -0.5" in log
    assert len(rl.micrograph_table(rl.read_star(pdir / job["uid"] / "micrographs_subset.star"))) == 2
    sec = _sections(manager, puid, job, "picktuning")[0]
    cards = sec["settings"]
    assert [c["label"] for c in cards] == ["Ø 40-60 Å · threshold -0.5", "Ø 40-60 Å · threshold 0.5",
                                           "Ø 50-70 Å · threshold -0.5", "Ø 50-70 Å · threshold 0.5"]
    assert all(c["image"] and (pdir / c["image"]).exists() and len(c["counts"]) == 2 for c in cards)
    assert sum(cards[0]["counts"]) >= sum(cards[1]["counts"]) and sum(cards[0]["counts"]) > 0  # lower threshold, more picks
    assert _outs(job)["tuning"]["meta"] == {"settings": 4, "micrographs": 2}
    # a setting chosen: the auto-picking of all the micrographs with it
    pick = _run(manager, puid, "relion_autopick", {**cards[2]["params"], "mpi": 1}, mics)
    assert "--LoG_diam_min 50 --LoG_diam_max 70" in _command(manager, puid, pick)
    assert _outs(pick)["coordinates"]["meta"]["n_micrographs"] == 4


def test_junk_detector_finds_contamination_and_film_edges():
    from cryoplug import junk as jk
    rng = np.random.default_rng(1)
    n, apix, diameter = 1024, 2.0, 100.0
    y, x = np.mgrid[0:n, 0:n].astype(np.float32)
    img = rng.normal(0, 1.0, (n, n)).astype(np.float32) + 0.6 * x / n  # noise and an ice thickness gradient
    for cx, cy in rng.uniform(60, n - 60, (120, 2)):
        img -= 1.2 * np.exp(-((x - cx) ** 2 + (y - cy) ** 2) / (2 * 12.5 ** 2))
    clean_junk, _ = jk.detect(img, apix, diameter)
    assert clean_junk.mean() < 0.05  # particles and the gradient are not junk (a dense cluster may be)
    img -= 6.0 * np.exp(-((x - 700) ** 2 + (y - 300) ** 2) / (2 * 60.0 ** 2))  # a drop of contamination
    img[:, :200] -= 1.5  # carbon film on the left
    junk, f = jk.detect(img, apix, diameter)
    assert list(jk.on_junk(junk, f, np.array([[700, 300], [200, 512], [500, 800]]))) == [True, True, False]
    assert not jk.detect(img, apix, diameter, dark=0, edges=0, bright=0)[0].any()


def test_micrograph_junk_job(manager, spa):
    import mrcfile
    puid, ctf = spa["puid"], spa["ctf"]
    pdir = manager.project_dir(puid)
    mics = {"micrographs": _ref(ctf, "micrographs")}
    names = rl.micrograph_table(rl.read_star(pdir / _outs(ctf)["micrographs"]["path"])).column("rlnMicrographName")
    with mrcfile.open(str(pdir / names[1]), mode="r+") as m:  # contamination painted on the second micrograph
        d = np.asarray(m.data, dtype=np.float32)
        yy, xx = np.mgrid[0:d.shape[0], 0:d.shape[1]]
        d = d - 10.0 * float(d.std()) * np.exp(-((xx - 250) ** 2 + (yy - 250) ** 2) / (2 * 25.0 ** 2))
        m.set_data(d.astype(m.data.dtype))
    pick = _run(manager, puid, "relion_autopick", {"method": "log", "diameter_min": 50, "diameter_max": 70, "mpi": 1}, mics)
    bad = manager.create_job(puid, "micrograph_junk", {"dark": 0, "edges": 0}, mics)
    with pytest.raises(Exception, match="at least one kind of junk"):
        manager.queue_job(puid, bad["uid"])
    job = _run(manager, puid, "micrograph_junk", {"max_junk": 5, "particle_diameter": 60},
               {**mics, "coordinates": _ref(pick, "coordinates")})
    outs = _outs(job)
    assert {"coordinates", "rejected"} <= set(outs)
    n_in = _outs(pick)["coordinates"]["meta"]["n_particles"]
    assert outs["coordinates"]["meta"]["n_particles"] + outs["rejected"]["meta"]["n_particles"] == n_in
    table = next(s for s in _sections(manager, puid, job, "table") if s["title"].startswith("Junk per micrograph"))
    assert table["rows"][0][0] == names[1] and float(table["rows"][0][1]) > 5, table["rows"]  # the painted one is the worst
    for mic, cf in rl.coordinate_files(pdir / outs["rejected"]["path"]):  # rejected picks only around the paint
        xy, _ = rl.read_coordinates(pdir / cf)
        if len(xy):
            assert mic == names[1] and np.all(np.hypot(xy[:, 0] - 250, xy[:, 1] - 250) < 120)
    clean = rl.micrograph_table(rl.read_star(pdir / outs["micrographs"]["path"])).column("rlnMicrographName")
    assert names[1] not in clean and len(clean) == len(names) - 1
    assert (pdir / job["uid"] / "junk.png").exists()
