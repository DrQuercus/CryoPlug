"""CryoAtom2 enhanced: several runs and settings, every model scored against the experimental map, the best kept and
its gaps filled from the other runs, chains identified until no new sequence appears, and the hand of the map checked.
The fake CryoAtom2 (conftest) builds more with lower Cα thresholds, another part of the chain in a sharpened copy of
the map, and short misplaced pieces when $CRYOPLUG_FAKE_WRONG_HAND says the map is mirrored."""
from __future__ import annotations

import json

import gemmi
import numpy as np
import pytest
from conftest import SEQUENCE, build_helix, log_of, run_until_done

from cryoplug import modelsearch as ms
from cryoplug.jobs.enhanced import auto_settings, consensus_hits, parse_bfactors
from cryoplug.manager import ManagerError
from cryoplug.mrc import MapVolume, model_map


def _project(manager, synthetic):
    p = manager.create_project("Enhanced building")
    imp = manager.create_job(p["uid"], "import_cryosparc", {"job_dir": str(synthetic["cryosparc_job"])})
    manager.queue_job(p["uid"], imp["uid"])
    run_until_done(manager, p["uid"], [imp["uid"]])
    return p["uid"], imp["uid"]


def _run(manager, puid, params, inputs, timeout=240):
    job = manager.create_job(puid, "cryoatom_enhanced", params, inputs)
    manager.queue_job(puid, job["uid"])
    run_until_done(manager, puid, [job["uid"]], timeout=timeout)
    job = manager.job(puid, job["uid"])
    assert job["status"] == "completed", f"{job['error']}\n{log_of(manager, puid, job['uid'])}"
    return job


def _section(job_dir, title):
    report = json.loads((job_dir / "report.json").read_text())
    return next(s for s in report["sections"] if s["title"] == title)


def test_scoring_and_combination():
    st = build_helix()
    xyz = np.array([[a.pos.x, a.pos.y, a.pos.z] for r in st[0][0] for a in r])
    weights = np.array([a.element.atomic_number for r in st[0][0] for a in r], float)
    like = MapVolume(data=np.zeros((48, 48, 48), np.float32), voxel=np.array([1.0, 1.0, 1.0]))
    clean = model_map(xyz, weights, like, 3.0)
    vol = like.like(clean / clean.max() + np.random.default_rng(0).normal(0, 0.15, clean.shape).astype(np.float32))
    n = len(st[0][0])
    full = ms.score_model(st, vol, 3.0)
    assert full["fitted"] == full["residues"] == n and full["fragments"] == 1 and full["duplicated"] == 0

    def part(a, b):
        out = st.clone()
        for i in range(n - 1, -1, -1):
            if not a <= i < b:
                del out[0][0][i]
        return out
    first, last = part(0, 13), part(9, n)
    s_last = ms.score_model(last, vol, 3.0)
    combined, added = ms.combine(first, [("last", last, s_last["per_residue"])], ms.fitted_cutoff(3.0))
    assert added == [{"source": "last", "chain": "A", "first": 14, "last": n, "length": n - 13,
                      "q_mean": pytest.approx(added[0]["q_mean"]), "new_chain": "B"}]  # trimmed to the gap
    assert ms.score_model(combined, vol, 3.0)["fitted"] == n
    # The same density traced twice counts once; residues in noise do not count
    twice = st.clone()
    copy = twice[0][0].clone()
    copy.name = "B"
    twice[0].add_chain(copy)
    s_twice = ms.score_model(twice, vol, 3.0)
    assert s_twice["duplicated"] == 2 * n and s_twice["score"] == n
    away = st.clone()
    for r in away[0][0]:
        for a in r:
            a.pos = gemmi.Position(a.pos.x + 9, a.pos.y + 9, a.pos.z)
    assert ms.score_model(away, vol, 3.0)["fitted"] == 0
    # Short disconnected pieces: what a mirrored map gives
    pieces = gemmi.Structure()
    model = gemmi.Model("1")
    residues = list(st[0][0])
    for j in range(0, n, 5):
        chain = gemmi.Chain(f"P{j // 5}")
        for r in residues[j:j + 5]:
            chain.add_residue(r)
        model.add_chain(chain)
    pieces.add_model(model)
    assert ms.wrong_hand_suspected(ms.score_model(pieces, vol, 3.0)) and not ms.wrong_hand_suspected(full)


def test_helpers():
    assert auto_settings(3.0) == ["standard", "more_residues"] and auto_settings(4.2) == ["standard", "low_resolution"]
    assert parse_bfactors("-30, 40;0 -30") == [-30.0, 40.0]
    with pytest.raises(ValueError):
        parse_bfactors("sharp")
    hits = consensus_hits([[("a", "MKV"), ("b", "GGG")], [("a2", "mkv")], [("c", "PPP")]])
    assert hits == [("a", "MKV")]  # found by 2 runs of 3; GGG and PPP by one only


def test_best_run_kept_and_gaps_filled(manager, synthetic):
    puid, imp = _project(manager, synthetic)
    job = _run(manager, puid, {"settings": "standard", "runs": 2, "bfactors": "-30"},
               {"map": {"job": imp, "output": "map_sharp"}, "mask": {"job": imp, "output": "mask"}})
    log = log_of(manager, puid, job["uid"])
    job_dir = manager.job_dir(puid, job["uid"])
    assert log.count("$ ") >= 4 and "-c " in log and "-m " in log and (job_dir / "map_B-30.mrc").exists()
    runs = _section(job_dir, "Runs compared")
    assert len(runs["rows"]) == 5  # 2 runs on the map, 2 on the sharpened copy (best setting), the combination
    outs = {o["name"]: o for o in job["outputs"]}
    assert {"model", "model_raw", "candidates"} <= set(outs) and "map_other_hand" not in outs
    best = gemmi.read_structure(str(manager.project_dir(puid) / outs["model"]["path"]))
    n = len(build_helix()[0][0])
    assert sum(len(ch) for ch in best[0]) == n  # the first 60% and the last 60% put together
    assert "Combination:" in log and "kept" in log and _section(job_dir, "Pieces added from other runs")["rows"]
    highlights = {h["label"]: h["value"] for h in job["highlights"]}
    assert highlights["Fitted"].startswith(str(n)) and highlights["Runs"] == "4"
    assert "Hand" not in highlights  # the model is in one piece: no reason to check


def test_identification_until_no_new_sequence(manager, synthetic, tmp_path):
    puid, imp = _project(manager, synthetic)
    db = tmp_path / "proteome.fasta"
    db.write_text(f">sp|P0TEST|HELIX_TEST Test helix protein\n{SEQUENCE}\n>sp|P0OTHER|OTHER\nMAAAA\n")
    job = _run(manager, puid, {"settings": "standard + more residues", "runs": 1, "protein_db": str(db),
                               "check_hand": "never"}, {"map": {"job": imp, "output": "map_sharp"}})
    log = log_of(manager, puid, job["uid"])
    assert "Round 2: rebuilding with 1 new protein sequence(s): sp|P0TEST" in log and "has converged" in log
    assert "-ps " in log.split("Round 2:")[1] and "-pf " in log
    outs = {o["name"]: o for o in job["outputs"]}
    fasta = (manager.project_dir(puid) / outs["sequence"]["path"]).read_text()
    assert "P0TEST" in fasta and "P0OTHER" not in fasta
    assert {h["label"]: h["value"] for h in job["highlights"]}["Best run"].startswith("More residues")


def test_wrong_hand_detected(manager, synthetic, monkeypatch):
    monkeypatch.setenv("CRYOPLUG_FAKE_WRONG_HAND", "1")
    puid, imp = _project(manager, synthetic)
    job = _run(manager, puid, {"settings": "standard", "runs": 1, "combine": False},
               {"map": {"job": imp, "output": "map_sharp"}})
    log = log_of(manager, puid, job["uid"])
    assert "is the map in the wrong hand?" in log and "probably in the WRONG HAND" in log
    outs = {o["name"]: o for o in job["outputs"]}
    assert {"map_other_hand", "model_other_hand"} <= set(outs)
    assert {h["label"]: h["value"] for h in job["highlights"]}["Hand"] == "probably wrong"
    # The mirrored map is the experimental map with one axis reversed
    mirrored = MapVolume.read(manager.project_dir(puid) / outs["map_other_hand"]["path"])
    sharp = next(o for o in manager.job(puid, imp)["outputs"] if o["name"] == "map_sharp")
    original = MapVolume.read(manager.project_dir(puid) / sharp["path"])
    assert np.allclose(mirrored.data, original.data[::-1])


def test_map_model_cycles(manager, synthetic):
    """LocScale hybrid with the part of the best model fitted to the density, CryoAtom2 again in the new map, while
    the score improves; Phenix model-based sharpening and DeepEMhancer give one map each; a missing program is left
    out. The fake CryoAtom2 builds 60% of the chain with the standard settings, everything in an improved map."""
    puid, imp = _project(manager, synthetic)
    job = _run(manager, puid, {"settings": "standard", "runs": 1, "combine": False, "check_hand": "never",
                               "improve_map": ["locscale_hybrid", "phenix_model_sharpening", "deepemhancer", "emready"],
                               "improve_rounds": 3},
               {"map": {"job": imp, "output": "map_sharp"}, "half_maps": {"job": imp, "output": "half_maps"}})
    log = log_of(manager, puid, job["uid"])
    job_dir = manager.job_dir(puid, job["uid"])
    n = len(build_helix()[0][0])
    first = int(n * 0.6)
    assert f"LocScale hybrid, cycle 1: sharpening with the {first} fitted residues" in log
    assert "-mc " in log and "--complete_model" in log and "-hm " in log
    fitted = gemmi.read_structure(str(job_dir / "improve" / "locscale_hybrid_1" / "model_fitted.cif"))
    assert sum(len(ch) for ch in fitted[0]) == first and {a.b_iso for ch in fitted[0] for r in ch for a in r} == {40.0}
    # Cycle 2 sharpens with the complete model and builds the same: no better, so no cycle 3
    assert f"LocScale hybrid, cycle 2: sharpening with the {n} fitted residues" in log
    assert "LocScale hybrid map, cycle 2: best run" in log and not (job_dir / "improve" / "locscale_hybrid_3").exists()
    assert (job_dir / "improve" / "phenix_model_sharpening_1" / "sharpened_map.mrc").is_symlink()  # .ccp4 read as .mrc
    assert (job_dir / "improve" / "phenix_model_sharpening_1" / "sharpened_map.ccp4").exists()
    assert (job_dir / "improve" / "deepemhancer" / "deepemhancer_tightTarget.mrc").exists()
    assert "EMReady2 left out" in log  # not installed: the job goes on
    outs = {o["name"]: o for o in job["outputs"]}
    assert outs["map_improved"]["path"].endswith("improve/locscale_hybrid_1/locscale_hybrid.mrc")
    assert {h["label"]: h["value"] for h in job["highlights"]}["Best run"] == "Standard · LocScale hybrid ⟳1"
    maps = [r[1] for r in _section(job_dir, "Runs compared")["rows"]]
    assert maps == ["experimental", "LocScale hybrid ⟳1", "LocScale hybrid ⟳2", "Phenix model-based sharpening ⟳1",
                    "DeepEMhancer"]
    assert "Map improvement tried: LocScale hybrid" in _section(job_dir, "Summary")["text"]


def test_map_improvement_choices(manager, synthetic):
    puid, imp = _project(manager, synthetic)
    with pytest.raises(ManagerError, match="not among"):
        manager.create_job(puid, "cryoatom_enhanced", {"improve_map": ["sharpen_it"]}, {"map": {"job": imp, "output": "map_sharp"}})
    job = manager.create_job(puid, "cryoatom_enhanced", {"improve_map": "emmernet, phenix_density_modification"},
                             {"map": {"job": imp, "output": "map_sharp"}})
    assert job["params"]["improve_map"] == ["emmernet", "phenix_density_modification"]
    with pytest.raises(ManagerError, match="needs the half maps"):
        manager.queue_job(puid, job["uid"])
