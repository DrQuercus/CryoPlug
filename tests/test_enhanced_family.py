"""The 'enhanced' jobs: alternatives tried, each scored against an independent criterion, the best kept with the
decision documented. Refinement with the map weight chosen by half-map cross-validation (fake Phenix: a weight of 10
or more jitters the model and raises the clashscore), the mask validated by the corrected FSC, the sharpening compared
on the model's Q-score, and ModelAngelo next to CryoAtom2 in the building job."""
from __future__ import annotations

import json

import pytest
from conftest import log_of, run_until_done

from cryoplug.jobs.refinement import choose_trial, fsc_average, parse_weights
from cryoplug.manager import ManagerError


def _project(manager, synthetic):
    p = manager.create_project("Enhanced family")
    puid = p["uid"]
    imp = manager.create_job(puid, "import_cryosparc", {"job_dir": str(synthetic["cryosparc_job"])})
    mdl = manager.create_job(puid, "import_model", {"source": "file", "path": str(synthetic["model"])})
    for j in (imp, mdl):
        manager.queue_job(puid, j["uid"])
    run_until_done(manager, puid, [imp["uid"], mdl["uid"]])
    return puid, imp["uid"], mdl["uid"]


def _run(manager, puid, kind, params, inputs, timeout=300):
    job = manager.create_job(puid, kind, params, inputs)
    manager.queue_job(puid, job["uid"])
    run_until_done(manager, puid, [job["uid"]], timeout=timeout)
    job = manager.job(puid, job["uid"])
    assert job["status"] == "completed", f"{job['error']}\n{log_of(manager, puid, job['uid'])}"
    return job


def _sections(manager, puid, juid):
    return manager.job_detail(puid, juid)["report"]["sections"]


def _decisions(sections):
    flow = next(s for s in sections if s["kind"] == "flow")
    return {n["id"]: n for n in flow["nodes"]}


# ------------------------------------------------------------- refinement
def test_weight_helpers():
    assert parse_weights("auto, 1, 3,10 30; auto") == ["auto", 1.0, 3.0, 10.0, 30.0]
    with pytest.raises(ValueError, match="neither"):
        parse_weights("auto, big")
    with pytest.raises(ValueError, match="positive"):
        parse_weights("0")
    with pytest.raises(ValueError, match="at least one"):
        parse_weights(" ")
    freq = [0.0, 0.1, 0.2, 0.3, 0.4]
    assert fsc_average(freq, [1.0, 0.9, 0.7, 0.3, 0.1], 4.0) == pytest.approx(0.8)  # shells to 1/4 = 0.25
    assert fsc_average(freq, [1.0, 0.9, 0.7, 0.3, 0.1], 50.0) == pytest.approx(0.5)  # none below the resolution: all
    trials = [{"label": "auto", "fsc_free_avg": 0.80, "gap": 0.01, "clashscore": 4.0},
              {"label": "10", "fsc_free_avg": 0.82, "gap": 0.08, "clashscore": 8.0},
              {"label": "30", "fsc_free_avg": 0.83, "gap": 0.20, "clashscore": 14.0}]
    assert choose_trial(trials, 10.0)["label"] == "10"  # the best free FSC among the acceptable clashscores
    assert choose_trial(trials, 100.0)["label"] == "30"
    assert choose_trial(trials, 1.0)["label"] == "30"  # none acceptable: all compete
    tie = [{"label": "a", "fsc_free_avg": 0.8, "gap": 0.05, "clashscore": None}, {"label": "b", "fsc_free_avg": 0.8, "gap": 0.01, "clashscore": None}]
    assert choose_trial(tie, 10.0)["label"] == "b"  # the same free FSC: the smaller gap


def test_refinement_enhanced_picks_the_weight_that_does_not_overfit(manager, synthetic):
    puid, imp, mdl = _project(manager, synthetic)
    job = _run(manager, puid, "phenix_refine_enhanced", {"weights": "auto, 3, 30", "trial_cycles": 2},
               {"model": {"job": mdl, "output": "model"}, "half_maps": {"job": imp, "output": "half_maps"},
                "map": {"job": imp, "output": "map_sharp"}})
    log = log_of(manager, puid, job["uid"])
    job_dir = manager.job_dir(puid, job["uid"])
    commands = (job_dir / "commands.sh").read_text()
    # three trials against half map 1 (2 cycles), then the final refinement against the full map (5 cycles)
    assert commands.count("phenix.real_space_refine") == 4
    assert commands.count("J12_005_volume_map_half_A.mrc") == 3 and "weight=3" in commands and "weight=30" in commands
    assert commands.count("macro_cycles=2") == 3 and "macro_cycles=5" in commands
    final = [c for c in commands.splitlines() if "macro_cycles=5" in c][0]
    assert "J12_005_volume_map_sharp.mrc" in final and "weight=" not in final  # auto kept: no weight given
    assert "Weight auto kept" in log and "clashscore above 10" in log
    report = json.loads((job_dir / "cross_validation.json").read_text())
    assert report["chosen"] == "auto" and [t["label"] for t in report["trials"]] == ["auto", "3", "30"]
    by = {t["label"]: t for t in report["trials"]}
    assert by["30"]["clashscore"] == pytest.approx(13.56) and by["auto"]["clashscore"] == pytest.approx(4.56)
    assert by["30"]["fsc_free_avg"] < by["auto"]["fsc_free_avg"]  # the jittered model fits the half maps less
    assert by["auto"]["fsc_free_avg"] == pytest.approx(by["3"]["fsc_free_avg"])  # the same model
    sections = _sections(manager, puid, job["uid"])
    keys = {s.get("key") for s in sections}
    assert {"trials", "trial_fsc", "trials_plot", "final_fsc", "decision_map"} <= keys
    frames = next(s for s in sections if s.get("key") == "trial_fsc")["frames"]
    assert [f["label"] for f in frames] == ["weight auto", "weight 3", "weight 30"]
    steps = _decisions(sections)
    assert steps["trial:auto"]["status"] == "chosen" and steps["trial:30"]["status"] == "discarded"
    assert steps["best"]["parents"] == ["trial:auto"] and steps["final"]["parents"] == ["best"] and steps["final"]["status"] == "chosen"
    outs = {o["name"]: o for o in job["outputs"]}
    assert outs["model"]["meta"]["weight"] == "auto" and len(outs["trials"]["files"]) == 3
    highlights = {h["label"]: h["value"] for h in job["highlights"]}
    assert highlights["Weight"] == "auto" and highlights["Free FSC (0.5)"].endswith("Å") and highlights["Over-fitting"].startswith(("+", "-"))
    bad = manager.create_job(puid, "phenix_refine_enhanced", {"weights": "auto, heavy"},
                             {"model": {"job": mdl, "output": "model"}, "half_maps": {"job": imp, "output": "half_maps"},
                              "map": {"job": imp, "output": "map_sharp"}})
    with pytest.raises(ManagerError, match="neither"):  # the weights are checked when the job is queued
        manager.queue_job(puid, bad["uid"])


# ------------------------------------------------------------------- masks
def test_mask_enhanced_keeps_a_safe_mask(manager, synthetic):
    from cryoplug.mrc import MapVolume
    puid, imp, _mdl = _project(manager, synthetic)
    job = _run(manager, puid, "mask_enhanced", {"thresholds": "1, 2", "dilations": "2, 4", "softs": "3", "lowpass": 10.0, "keep_masks": True},
               {"half_maps": {"job": imp, "output": "half_maps"}, "map": {"job": imp, "output": "map"}})
    log = log_of(manager, puid, job["uid"])
    job_dir = manager.job_dir(puid, job["uid"])
    outs = {o["name"]: o for o in job["outputs"]}
    assert {"mask", "fsc", "masks"} <= set(outs) and len(outs["masks"]["files"]) == 4
    meta = outs["mask"]["meta"]
    assert meta["fsc_validated"] is True and meta["dilation"] in (2.0, 4.0) and meta["soft_edge"] == 3.0 and meta["resolution"] > 0
    mask = MapVolume.read(job_dir / "mask.mrc")
    assert mask.data.shape == MapVolume.read(synthetic["half_a"]).data.shape
    assert float(mask.data.min()) >= 0.0 and float(mask.data.max()) <= 1.0 and 0 < float((mask.data >= 0.5).mean()) < 0.9
    assert (job_dir / "fsc.json").exists() and (job_dir / "fsc_emdb.xml").exists() and (job_dir / "mask_sections.png").exists()
    sections = _sections(manager, puid, job["uid"])
    keys = {s.get("key") for s in sections}
    assert {"masks", "mask_fsc", "mask_slices", "masks_plot", "decision_map"} <= keys
    table = next(s for s in sections if s.get("key") == "masks")
    assert len(table["rows"]) == 4 and table["columns"][0] == "Mask"
    frames = next(s for s in sections if s.get("key") == "mask_fsc")["frames"]
    assert [f["label"] for f in frames] == ["×1 · 2+3 Å", "×1 · 4+3 Å", "×2 · 2+3 Å", "×2 · 4+3 Å"]
    slices = next(s for s in sections if s.get("key") == "mask_slices")
    assert len(slices["frames"]) == 4 and slices["group"] == "masks" and all(f.get("view") for f in slices["frames"])
    steps = _decisions(sections)
    chosen = [n for n in steps.values() if n["id"].startswith("mask:") and n["status"] == "chosen"]
    assert len(chosen) == 1 and steps["best"]["parents"] == [chosen[0]["id"]] and steps["result"]["parents"] == ["best"]
    assert {"group:1", "group:2"} <= set(steps) and steps["level"]["parents"] == ["input"]
    highlights = {h["label"]: h["value"] for h in job["highlights"]}
    assert {"Resolution", "Mask", "Noise", "Unmasked"} <= set(highlights) and highlights["Mask"].endswith("+3 Å")
    assert highlights["Resolution"].endswith("Å") and "kept" in log
    bad = manager.create_job(puid, "mask_enhanced", {"thresholds": "1, x"}, {"half_maps": {"job": imp, "output": "half_maps"}})
    with pytest.raises(ManagerError, match="not a number"):
        manager.queue_job(puid, bad["uid"])


# ------------------------------------------------------------- ModelAngelo
def test_modelangelo_builds_next_to_cryoatom(manager, synthetic):
    """Both builders: the fake ModelAngelo builds the whole chain, the fake CryoAtom2 60 % of it with the standard
    settings: the ModelAngelo run is the best, whichever program built it, and it ran once (one run per map)."""
    puid, imp, mdl = _project(manager, synthetic)
    job = _run(manager, puid, "cryoatom_enhanced", {"programs": "both", "settings": "standard", "runs": 2, "combine": False,
                                                    "check_hand": "never"},
               {"map": {"job": imp, "output": "map_sharp"}, "sequence": {"job": mdl, "output": "sequence"}})
    log = log_of(manager, puid, job["uid"])
    job_dir = manager.job_dir(puid, job["uid"])
    commands = (job_dir / "commands.sh").read_text()
    assert commands.count("cryoatom build") == 2 and commands.count("model_angelo build -v") == 1
    angelo = [c for c in commands.splitlines() if "model_angelo" in c][0]
    assert " -pf " in angelo and "--device 0" in angelo and "build_no_seq" not in angelo and " -c " not in angelo
    assert "CryoAtom2 and ModelAngelo" in log and "(ModelAngelo once)" in log
    assert (job_dir / "runs" / "r1_map_modelangelo_1" / "modelangelo" / "modelangelo.cif").exists()
    highlights = {h["label"]: h["value"] for h in job["highlights"]}
    assert highlights["Best run"].startswith("ModelAngelo") and highlights["Runs"] == "3"
    sections = _sections(manager, puid, job["uid"])
    steps = _decisions(sections)
    assert steps["run:r1_map_modelangelo_1"]["status"] == "chosen" and steps["run:r1_map_standard_1"]["status"] == "discarded"
    assert steps["best:1"]["parents"] == ["run:r1_map_modelangelo_1"] and steps["settings"]["lines"][0] == "Standard, ModelAngelo"
    table = next(s for s in sections if s["title"] == "Runs compared")
    assert table["columns"][2] == "Builder / settings" and [r[2] for r in table["rows"]] == ["Standard", "Standard", "ModelAngelo"]
    assert [r[5] for r in table["rows"]] == ["", "", "✓ best"]
    outs = {o["name"]: o for o in job["outputs"]}
    assert outs["model"]["label"].endswith("(r1_map_modelangelo_1)") and len(outs["candidates"]["files"]) == 3


def test_modelangelo_alone_without_sequence(manager, synthetic):
    puid, imp, _mdl = _project(manager, synthetic)
    job = _run(manager, puid, "cryoatom_enhanced", {"programs": "modelangelo", "runs": 3, "combine": False, "check_hand": "never"},
               {"map": {"job": imp, "output": "map_sharp"}, "mask": {"job": imp, "output": "mask"}})
    commands = (manager.job_dir(puid, job["uid"]) / "commands.sh").read_text()
    assert "cryoatom" not in commands and commands.count("model_angelo build_no_seq -v") == 1 and " -m " in commands
    highlights = {h["label"]: h["value"] for h in job["highlights"]}
    assert highlights["Runs"] == "1" and highlights["Best run"].startswith("ModelAngelo")
    summary = next(s for s in _sections(manager, puid, job["uid"]) if s["title"] == "Summary")["text"]
    assert "ModelAngelo (ModelAngelo's own network" in summary


# -------------------------------------------------------------- sharpening
def test_sharpen_enhanced_ranks_the_maps_with_the_model(manager, synthetic):
    """B-factor maps and two methods (fakes copying their input), the EMReady2 one skipped (not installed): every map
    scored with the model, the best by Q-score kept, the comparison reported."""
    puid, imp, mdl = _project(manager, synthetic)
    job = _run(manager, puid, "sharpen_enhanced", {"bfactors": "auto, -50, 50", "methods": ["locscale_model_free", "deepemhancer", "emready"]},
               {"map": {"job": imp, "output": "map"}, "half_maps": {"job": imp, "output": "half_maps"},
                "model": {"job": mdl, "output": "model"}, "mask": {"job": imp, "output": "mask"}})
    log = log_of(manager, puid, job["uid"])
    job_dir = manager.job_dir(puid, job["uid"])
    commands = (job_dir / "commands.sh").read_text()
    assert "locscale -hm" in commands and "deepemhancer -i" in commands and "emready" not in commands
    assert "EMReady2 skipped" in log and "Guinier plot: fall-off B =" in log and "Map kept:" in log
    report = json.loads((job_dir / "comparison.json").read_text())
    keys = [m["key"] for m in report["maps"]]
    assert keys == ["input", "auto", "B-50", "B+50", "locscale_model_free", "deepemhancer"]
    scores = {m["key"]: m["score"]["q_mean"] for m in report["maps"]}
    assert all(v == v for v in scores.values()) and report["chosen_by"] == "q_score"
    assert report["chosen"] == max(scores, key=scores.get)
    assert scores["B+50"] < scores["B-50"]  # blurred: the density spreads out, the Q-score falls
    sections = _sections(manager, puid, job["uid"])
    keys = {s.get("key") for s in sections}
    assert {"maps", "map_sections", "guinier", "scores_plot", "model_fsc", "decision_map"} <= keys
    table = next(s for s in sections if s.get("key") == "maps")
    assert len(table["rows"]) == 6 and [r[6] for r in table["rows"]].count("✓ best") == 1
    frames = next(s for s in sections if s.get("key") == "map_sections")["frames"]
    assert len(frames) == 6 and frames[0]["label"] == "as given" and all(f.get("view") for f in frames)
    assert len(next(s for s in sections if s.get("key") == "guinier")["series"]) == 6
    steps = _decisions(sections)
    chosen = f"map:{report['chosen']}"
    assert steps[chosen]["status"] == "chosen" and steps["best"]["parents"] == [chosen] and steps["result"]["parents"] == ["best"]
    assert steps["map:emready"]["status"] == "skipped" and steps["map:B-50"]["group"] == "group:b"
    assert steps["map:deepemhancer"]["group"] == "group:methods" and steps["estimate"]["parents"] == ["input"]
    outs = {o["name"]: o for o in job["outputs"]}
    assert {"map", "maps", "map_locscale", "map_deepemhancer", "report"} <= set(outs) and len(outs["maps"]["files"]) == 5
    assert outs["map"]["meta"]["chosen_by"] == "q_score" and outs["map"]["meta"]["q_score"] == pytest.approx(max(scores.values()), abs=1e-3)
    assert ("map_auto" in outs) == (report["chosen"] != "auto")
    highlights = {h["label"]: h["value"] for h in job["highlights"]}
    assert highlights["Maps"] == "6" and highlights["B estimate"].endswith("Å²") and highlights["Best map"] == steps[chosen]["title"]


def test_sharpen_enhanced_without_a_model_keeps_the_guinier_map(manager, synthetic):
    puid, imp, _mdl = _project(manager, synthetic)
    job = _run(manager, puid, "sharpen_enhanced", {"bfactors": "auto, -30", "lowpass": False},
               {"map": {"job": imp, "output": "map"}})
    log = log_of(manager, puid, job["uid"])
    assert "No model connected" in log and "no model to score the maps" in log
    report = json.loads((manager.job_dir(puid, job["uid"]) / "comparison.json").read_text())
    assert report["chosen"] == "auto" and report["chosen_by"] == "guinier" and all(m["score"] is None for m in report["maps"])
    highlights = {h["label"]: h["value"] for h in job["highlights"]}
    assert highlights["Best map"].startswith("B ") and highlights["Best map"].endswith("(auto)") and "Q-score" not in highlights
    outs = {o["name"]: o for o in job["outputs"]}
    assert outs["map"]["meta"]["chosen_by"] == "guinier" and outs["map"]["meta"]["bfactor"] == report["auto_bfactor"]
    assert len(outs["maps"]["files"]) == 2 and "map_auto" not in outs
    steps = _decisions(_sections(manager, puid, job["uid"]))
    assert steps["best"]["status"] == "warning" and steps["map:auto"]["status"] == "chosen"
    keys = {s.get("key") for s in _sections(manager, puid, job["uid"])}
    assert "guinier" in keys and "scores_plot" not in keys
    bad = manager.create_job(puid, "sharpen_enhanced", {"bfactors": "auto, big"}, {"map": {"job": imp, "output": "map"}})
    with pytest.raises(ManagerError, match="not a number"):
        manager.queue_job(puid, bad["uid"])
    bad = manager.create_job(puid, "sharpen_enhanced", {"methods": ["phenix_density_modification"]}, {"map": {"job": imp, "output": "map"}})
    with pytest.raises(ManagerError, match="needs the half maps"):
        manager.queue_job(puid, bad["uid"])
