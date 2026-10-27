"""Live dashboards, as CryoSPARC's: one frame per iteration or run (a slider in the panel), the charts of iterative
RELION runs drawn while they run, CryoAtom2 enhanced's runs so far and best model so far, and the warnings of a job."""
from __future__ import annotations

import json

import numpy as np
from test_enhanced import _project
from test_enhanced import _run as run_enhanced
from test_relion_spa import _ref, _run, spa  # noqa: F401 (the fixture)

from cryoplug import relion as rl
from cryoplug.imaging import write_png
from cryoplug.jobs.base import MAX_FRAMES, JobContext
from cryoplug.jobs.iterations import MASK_COLOUR, IterationDashboard, sections_picture
from cryoplug.mrc import MapVolume


def _ctx(tmp_path, uid="J9"):
    project = tmp_path / "project"
    job_dir = project / uid
    job_dir.mkdir(parents=True, exist_ok=True)
    return JobContext({"uid": uid, "project_uid": "P1", "project_dir": str(project)}, job_dir)


def _report(path):
    return json.loads(path.read_text())


def _by_key(report):
    return {s["key"]: s for s in report["sections"] if s.get("key")}


# ------------------------------------------------------------------ frames
def test_frames_of_a_section(tmp_path):
    ctx = _ctx(tmp_path)
    png = write_png(ctx.path("a.png"), np.zeros((4, 4), np.uint8))
    model = ctx.path("model.cif")
    model.write_text("data_x\n")
    for i in (1, 2, 3):
        ctx.add_image_frame("Slices", png, f"Iteration {i}", key="slices", group="iterations", caption=f"step {i}",
                            view=[{"kind": "model", "path": model, "label": "the model"}])
        ctx.add_plot_frame("FSC", [{"name": f"it {i}", "x": [0.1, 0.2], "y": [1.0, 0.1 * i]}], f"Iteration {i}", key="fsc",
                           x_kind="resolution", hlines=[{"y": 0.143, "label": "0.143"}], group="iterations")
    ctx.add_image_frame("Slices", png, "Iteration 2", key="slices", caption="redrawn")  # a step drawn again: replaced
    sections = _by_key(_report(ctx.report_path))
    slices, fsc = sections["slices"], sections["fsc"]
    assert [f["label"] for f in slices["frames"]] == ["Iteration 1", "Iteration 3", "Iteration 2"]
    assert slices["frames"][0]["view"] == [{"kind": "model", "path": "J9/model.cif", "label": "the model"}]
    assert slices["path"] == "J9/a.png" and slices["group"] == "iterations" and slices["frames"][-1]["caption"] == "redrawn"
    assert len(fsc["frames"]) == 3 and abs(fsc["series"][0]["y"][1] - 0.3) < 1e-9 and fsc["x_kind"] == "resolution"
    # the best so far: one frame, replaced
    for i in (1, 2):
        ctx.add_image_frame("Best so far", png, f"run {i}", key="best", keep=False)
    assert [f["label"] for f in _by_key(_report(ctx.report_path))["best"]["frames"]] == ["run 2"]
    ctx.remove_section("best")
    assert "best" not in _by_key(_report(ctx.report_path))
    for i in range(MAX_FRAMES + 5):
        ctx.add_plot_frame("Long", [{"name": "x", "x": [0], "y": [i]}], f"Step {i}", key="long")
    frames = _by_key(_report(ctx.report_path))["long"]["frames"]
    assert len(frames) == MAX_FRAMES and frames[-1]["label"] == f"Step {MAX_FRAMES + 4}"
    ctx.add_highlight("Iteration", "3")
    ctx.remove_highlight("Iteration")
    assert not ctx.highlights


def test_sections_picture_outlines_the_mask(tmp_path):
    grid = np.indices((32, 32, 32)) - 16
    r = np.sqrt((grid ** 2).sum(axis=0))
    blob = MapVolume(data=np.exp(-r ** 2 / 40).astype(np.float32), voxel=np.ones(3))
    mask = MapVolume(data=(r < 9).astype(np.float32), voxel=np.ones(3))
    blob.write(tmp_path / "a.mrc")
    mask.write(tmp_path / "m.mrc")
    pic = sections_picture([tmp_path / "a.mrc", tmp_path / "a.mrc"], tmp_path / "m.mrc", size=64)
    assert pic.shape == (64, 3 * 64 + 2 * 4, 3) and pic.dtype == np.uint8
    orange = np.all(pic == MASK_COLOUR, axis=2)
    assert orange[:, :64].sum() > 20 and orange[:, 68:132].sum() > 20  # an outline in each section
    assert pic[32, 32].max() > 200 and pic[2, 2].max() < 60  # density bright, solvent dark
    assert not np.all(sections_picture([tmp_path / "a.mrc"]) == MASK_COLOUR, axis=2).any()


def _iteration_files(job_dir, it, box=24, apix=2.0):
    """What relion_refine writes for an iteration of an auto-refinement (iteration 0: the starting reference, no FSC
    yet, 999 for the accuracies)."""
    vol = MapVolume(data=np.random.default_rng(it).normal(size=(box, box, box)).astype(np.float32), voxel=np.full(3, apix))
    stem = job_dir / f"run_it{it:03d}"
    freq = np.arange(1, box // 2 + 1) / (box * apix)
    curve = rl.StarTable("model_class_1")
    curve.set_column("rlnResolution", freq)
    curve.set_column("rlnGoldStandardFsc", 1.0 / (1.0 + np.exp((freq - 1.0 / (10.0 - it)) * box * apix / 2.0)) if it
                     else np.zeros_like(freq))
    classes = rl.StarTable("model_classes")
    classes.set_column("rlnReferenceImage", [f"{stem.name}_half1_class001.mrc"])
    classes.set_column("rlnClassDistribution", [1.0])
    classes.set_column("rlnAccuracyRotations", [3.0 / it if it else 999.0])
    classes.set_column("rlnAccuracyTranslationsAngst", [1.0 / it if it else 999.0])
    general = rl.StarTable("model_general", ["rlnCurrentResolution"], [[f"{10.0 - it if it else 60.0:.4f}"]], loop=False)
    for h in (1, 2):
        vol.write(f"{stem}_half{h}_class001.mrc")
        rl.write_star(f"{stem}_half{h}_model.star", [general, classes, curve])


def test_iteration_dashboard_draws_the_iterations_that_ended(tmp_path):
    ctx = _ctx(tmp_path)
    MapVolume(data=np.ones((24, 24, 24), np.float32), voxel=np.full(3, 2.0)).write(ctx.path("mask.mrc"))
    dash = IterationDashboard(ctx, "refine", mask=ctx.path("mask.mrc"))
    dash(" Auto-refine: Iteration= 1")
    _iteration_files(ctx.job_dir, 1)
    assert not ctx.report_path.exists()  # iteration 1 still running: nothing drawn
    dash(" Auto-refine: Iteration= 2")
    dash(" Auto-refine: Resolution= 8.0 (no gain for 0 iter) ")
    dash(" Auto-refine: Changes in angles= 12.5 degrees; and in offsets= 1.25 Angstroms (no gain for 0 iter) ")
    sections = _by_key(_report(ctx.report_path))
    assert [f["label"] for f in sections["it_sections"]["frames"]] == ["Iteration 1"]
    assert [f["label"] for f in sections["it_fsc"]["frames"]] == ["Iteration 1"]
    assert {h["label"]: h["value"] for h in ctx.highlights}["Iteration"] == "1"
    _iteration_files(ctx.job_dir, 2)
    dash(" Auto-refine: Iteration= 3")
    _iteration_files(ctx.job_dir, 3)  # the last iteration: drawn when the run ends
    dash.finish()
    sections = _by_key(_report(ctx.report_path))
    frames = sections["it_sections"]["frames"]
    assert [f["label"] for f in frames] == ["Iteration 1", "Iteration 2", "Iteration 3"]
    assert [v["path"] for v in frames[2]["view"]] == ["J9/run_it003_half1_class001.mrc", "J9/run_it003_half2_class001.mrc"]
    assert "accuracy 1.00° · 0.33 Å" in frames[2]["caption"] and "resolution" in frames[2]["caption"]
    assert all((ctx.project_dir / f["path"]).exists() for f in frames)
    assert sections["it_sections"]["group"] == sections["it_fsc"]["group"] == "iterations"
    history = sections["resolution_history"]["series"][0]
    assert history["x"] == [1, 2, 3] and history["y"][0] > history["y"][-1]
    assert sections["it_changes"]["series"][0]["y"] == [12.5]
    highlights = {h["label"]: h["value"] for h in ctx.highlights}
    assert highlights["Iterations"] == "3" and "Iteration" not in highlights and highlights["Resolution"].endswith("Å")
    dash.finish()  # nothing new: nothing drawn twice
    assert len(_by_key(_report(ctx.report_path))["it_sections"]["frames"]) == 3


def test_the_starting_reference_is_shown_without_a_resolution(tmp_path):
    ctx = _ctx(tmp_path)
    dash = IterationDashboard(ctx, "refine")
    _iteration_files(ctx.job_dir, 0)  # written before the first iteration
    dash(" Auto-refine: Iteration= 1")
    _iteration_files(ctx.job_dir, 1)
    dash(" Auto-refine: Iteration= 2")
    dash.finish()
    sections = _by_key(_report(ctx.report_path))
    frames = sections["it_sections"]["frames"]
    assert [f["label"] for f in frames] == ["Iteration 0", "Iteration 1"]
    assert frames[0]["caption"] == "starting reference" and "999" not in json.dumps(sections)
    assert [f["label"] for f in sections["it_fsc"]["frames"]] == ["Iteration 1"]  # no FSC yet at iteration 0
    assert sections["resolution_history"]["series"][0]["x"] == [1]
    assert {h["label"]: h["value"] for h in ctx.highlights}["Iterations"] == "1"


# ------------------------------------------------------------------ RELION jobs
def test_relion_runs_show_their_iterations(manager, spa):  # noqa: F811
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

    def sections(job):
        return _by_key(_report(pdir / job["uid"] / "report.json"))

    c2 = _run(manager, puid, "relion_class2d", {"classes": 3, "algorithm": "em", "em_iterations": 4, "threads": 1, "gpus": 0},
              {"particles": parts})
    sheets = sections(c2)["it_classes2d"]["frames"]
    assert len(sheets) >= 2 and sheets[-1]["label"] == "Iteration 4" and "largest of 3 classes" in sheets[-1]["caption"]

    r3 = _run(manager, puid, "relion_refine3d", {"threads": 1, "gpus": 0}, {"particles": parts, "map": ref})
    got = sections(r3)
    assert [f["label"] for f in got["it_sections"]["frames"]] == [f"Iteration {i}" for i in range(1, 5)]
    assert len(got["it_fsc"]["frames"]) == 4 and got["it_changes"]["series"][0]["x"] == [1, 2, 3]
    assert got["resolution_history"]["title"] == "Resolution during the refinement"
    titles = [s["title"] for s in _report(pdir / r3["uid"] / "report.json")["sections"]]
    assert titles.count("Resolution during the refinement") == 1  # the final plot took the place of the live one
    assert {h["label"]: h["value"] for h in manager.job(puid, r3["uid"])["highlights"]}["Iterations"] == "4"
    detail = manager.job_detail(puid, r3["uid"])
    assert detail["report_mtime"] > 0 and isinstance(detail["warnings"], list)
    assert detail["report"]["sections"][0]["kind"]
    frames = next(s for s in detail["report"]["sections"] if s.get("key") == "it_sections")["frames"]
    assert all(len(f["view"]) == 2 for f in frames)
    # Clear intermediate iterations: the maps of the iterations deleted are no longer offered in 3D, the pictures stay
    manager.clear_intermediate(puid, r3["uid"])
    frames = next(s for s in manager.job_detail(puid, r3["uid"])["report"]["sections"] if s.get("key") == "it_sections")["frames"]
    assert len(frames) == 4 and all((pdir / f["path"]).exists() for f in frames)
    assert not frames[0]["view"] and all((pdir / v["path"]).exists() for f in frames for v in f["view"])

    c3 = _run(manager, puid, "relion_class3d_global", {"classes": 2, "iterations": 4, "threads": 1, "gpus": 0},
              {"particles": parts, "map": ref})
    got = sections(c3)
    classes = got["it_classes"]["frames"]
    assert [f["label"] for f in classes] == [f"Iteration {i}" for i in range(1, 5)]
    assert classes[-1]["caption"].startswith("Classes 1, 2 (left to right): ") and len(classes[-1]["view"]) == 2
    report = _report(pdir / c3["uid"] / "report.json")
    assert [s["title"] for s in report["sections"]].count("Share of each class over the iterations") == 1

    ini = _run(manager, puid, "relion_initial_model", {"iterations": 20, "classes": 1, "symmetry": "C1", "threads": 1, "gpus": 0},
               {"particles": parts})
    frames = sections(ini)["it_classes"]["frames"]
    assert sections(ini)["it_classes"]["title"] == "Map (projection)" and len(frames) >= 5
    assert frames[-1]["label"] == "Iteration 20"


# ------------------------------------------------------------------ CryoAtom2 enhanced
def test_enhanced_runs_shown_as_they_end(manager, synthetic):
    puid, imp = _project(manager, synthetic)
    job = run_enhanced(manager, puid, {"settings": "standard", "runs": 2, "bfactors": "-30", "keep_runs": False},
                       {"map": {"job": imp, "output": "map_sharp"}})
    pdir = manager.project_dir(puid)
    report = _report(pdir / job["uid"] / "report.json")
    got = _by_key(report)
    assert "best_preview" not in got  # the preview while running; the best model is an output at the end
    frames = got["runs_models"]["frames"]
    assert len(frames) >= 4 and frames[0]["label"] == "Run 1" and "fitted residues" in frames[0]["caption"]
    assert all((pdir / f["path"]).exists() for f in frames)
    assert got["runs"]["title"] == "Runs compared" and [s["title"] for s in report["sections"]].count("Runs compared") == 1
    assert [s["title"] for s in report["sections"]].count("Fitted residues by run") == 1
    log = (pdir / job["uid"] / "job.log").read_text()
    assert "live view" not in log  # drawn without error
    # the runs were not kept: their models are no longer offered in 3D (a combination, kept in the job folder, is)
    detail = manager.job_detail(puid, job["uid"])
    frames = next(s for s in detail["report"]["sections"] if s.get("key") == "runs_models")["frames"]
    views = [v["path"] for f in frames for v in f["view"]]
    assert views and not any("/runs/" in v for v in views) and all((pdir / v).exists() for v in views)
    assert any(f["view"] and f["view"][0]["kind"] == "map" for f in frames)


def test_enhanced_shows_the_best_model_while_running(tmp_path):
    """While the runs go on: every run's model as a frame, the best model so far (one frame, replaced), its scores
    among the key results, the runs so far."""
    from conftest import build_helix

    from cryoplug import modelsearch as ms
    from cryoplug.jobs.enhanced import PRESETS, CryoAtomEnhanced
    from cryoplug.modelio import read_structure, write_structure

    ctx = _ctx(tmp_path)
    MapVolume(data=np.zeros((8, 8, 8), np.float32), voxel=np.ones(3)).write(ctx.path("map.mrc"))
    job = CryoAtomEnhanced()
    job.ctx, job.ms, job.read_structure, job.resolution = ctx, ms, read_structure, 3.0
    job.candidates, job.done, job.planned = [], 0, 4
    setting = next(iter(PRESETS))
    for n, fitted in ((1, 10), (2, 18), (3, 12)):
        folder = ctx.path("runs", f"r1_map_{n}")
        folder.mkdir(parents=True)
        model = write_structure(build_helix(), folder / "cryoatom.cif")
        cand = {"label": f"r1_map_{setting}_{n}", "map": "map", "map_short": "experimental", "map_note": "experimental map",
                "map_path": str(ctx.path("map.mrc")), "setting": setting, "run": n, "round": 1, "model": str(model),
                "score": {"fitted": fitted, "fitted_fraction": fitted / 22, "q_mean": 0.5 + n / 100, "cc_mask": 0.72,
                          "score": float(fitted), "residues": 22, "fragments": 1, "duplicated": 0}}
        job.candidates.append(cand)
        job.done += 1
        job._live(cand)
    got = _by_key(_report(ctx.report_path))
    assert [f["label"] for f in got["runs_models"]["frames"]] == ["Run 1", "Run 2", "Run 3"]
    preview = got["best_preview"]["frames"]
    assert [f["label"] for f in preview] == [f"r1_map_{setting}_2"]  # run 3 did not beat run 2
    assert [v["kind"] for v in preview[0]["view"]] == ["map", "model"] and preview[0]["view"][0]["path"] == "J9/map.mrc"
    assert got["runs"]["title"] == "Runs so far" and [r[5] for r in got["runs"]["rows"]] == ["", "✓ best", ""]
    assert len(got["runs_plot"]["series"][0]["y"]) == 3
    highlights = {h["label"]: h["value"] for h in ctx.highlights}
    assert highlights["Runs"] == "3 of 4" and highlights["Fitted"] == "18 (82%)" and highlights["Q-score"] == "0.520"
    assert "live view" not in ctx.log_path.read_text() if ctx.log_path.exists() else True


# ------------------------------------------------------------------ warnings
def test_warnings_of_a_job(manager, tmp_path):
    p = manager.create_project("Warnings")
    job = manager.create_job(p["uid"], "import_maps", {"map": str(tmp_path / "nothing.mrc")})
    jdir = manager.job_dir(p["uid"], job["uid"])
    jdir.mkdir(parents=True, exist_ok=True)
    ctx = JobContext({"uid": job["uid"], "project_uid": p["uid"], "project_dir": str(manager.project_dir(p["uid"]))}, jdir)
    ctx.log("an ordinary line")
    ctx.warn("The mask does not have the box of the map:\nnot used for this map")
    ctx.warn("Run 3 failed")
    ctx.warn("Run 3 failed")
    ctx._raw_log(" WARNING: a line of the program itself, not CryoPlug's\n")
    found = manager.job_warnings(p["uid"], job["uid"])
    assert [w["text"] for w in found] == ["The mask does not have the box of the map:\nnot used for this map", "Run 3 failed"]
    assert found[1]["count"] == 2 and found[0].get("count") is None and found[0]["time"]
    ctx.warn("Later warning")
    with open(ctx.log_path, "a") as fh:
        fh.write("[2026-10-27 10:00:00] WARNING: a line being writ")  # incomplete: read once it is whole
    found = manager.job_warnings(p["uid"], job["uid"])
    assert [w["text"] for w in found][-1] == "Later warning"
    with open(ctx.log_path, "a") as fh:
        fh.write("ten\n")
    assert manager.job_warnings(p["uid"], job["uid"])[-1]["text"] == "a line being written"
    detail = manager.job_detail(p["uid"], job["uid"])
    assert len(detail["warnings"]) == 4 and detail["report_mtime"] == 0.0
    ctx.log_path.write_text("")  # cleared: read again from the start
    assert manager.job_warnings(p["uid"], job["uid"]) == []
    ctx.add_text("Note", "x")
    assert manager.job_detail(p["uid"], job["uid"])["report_mtime"] > 0
