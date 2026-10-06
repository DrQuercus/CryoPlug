"""CryoAtom2, ModelAngelo HMM search, Boltz-2, spIsoNet, directional FSC, eLBOW and douse."""
from __future__ import annotations

import numpy as np
import pytest

from conftest import SEQUENCE, log_of, run_until_done
from cryoplug.jobs.building import parse_ligand_lines
from cryoplug.mrc import directional_fsc


def _setup(manager, synthetic):
    p = manager.create_project("New tools")
    imp = manager.create_job(p["uid"], "import_cryosparc", {"job_dir": str(synthetic["cryosparc_job"])})
    seq = manager.create_job(p["uid"], "import_sequence", {"fasta": f">helix\n{SEQUENCE}\n>rna\nACGUACGUAGGCU\n"})
    for j in (imp, seq):
        manager.queue_job(p["uid"], j["uid"])
    run_until_done(manager, p["uid"], [imp["uid"], seq["uid"]])
    return p["uid"], imp["uid"], seq["uid"]


def _ok(manager, puid, uid):
    job = manager.job(puid, uid)
    assert job["status"] == "completed", f"{uid} {job['type']}: {job['error']}\n{log_of(manager, puid, uid)}"
    return job


def test_model_building_additions(manager, synthetic, tmp_path):
    puid, imp, seq = _setup(manager, synthetic)
    db = tmp_path / "proteome.fasta"
    db.write_text(f">sp|P0TEST|HELIX_TEST Test helix protein\n{SEQUENCE}\n>tr|Q0WEAK|WEAK Weak\nMKKKKKKK\n>sp|P0OTHER|OTHER\nMAAAA\n")
    ca = manager.create_job(puid, "cryoatom_build", {}, {"map": {"job": imp, "output": "map_sharp"},
                                                          "sequence": {"job": seq, "output": "sequence"},
                                                          "mask": {"job": imp, "output": "mask"}})
    ident = manager.create_job(puid, "cryoatom_build", {"protein_db": str(db)}, {"map": {"job": imp, "output": "map_sharp"}})
    noseq = manager.create_job(puid, "modelangelo_build", {}, {"map": {"job": imp, "output": "map_sharp"}})
    hmm = manager.create_job(puid, "modelangelo_hmm_search", {"database": str(db)}, {"model": {"job": noseq["uid"], "output": "model"}})
    boltz = manager.create_job(puid, "boltz_predict", {"copies": "2", "ligands": "CCD:ATP\n2xCCD:MG\nSMILES:CC(=O)O",
                                                       "use_msa_server": False}, {"sequence": {"job": seq, "output": "sequence"}})
    uids = [ca["uid"], ident["uid"], noseq["uid"], hmm["uid"], boltz["uid"]]
    for u in uids:
        manager.queue_job(puid, u)
    run_until_done(manager, puid, uids)
    for u in uids:
        _ok(manager, puid, u)

    log = log_of(manager, puid, ca["uid"])
    assert "-ps" in log and "-rs" in log and "-m " in log and "-d 0" in log  # protein and RNA passed separately
    outs = {o["name"] for o in manager.job(puid, ca["uid"])["outputs"]}
    assert {"model", "model_raw"} <= outs
    ident_out = {o["name"]: o for o in manager.job(puid, ident["uid"])["outputs"]}
    assert "-pf" in log_of(manager, puid, ident["uid"]) and "sequence" in ident_out

    hjob = manager.job(puid, hmm["uid"])
    hout = {o["name"]: o for o in hjob["outputs"]}
    fasta = (manager.project_dir(puid) / hout["sequence"]["path"]).read_text()
    assert "P0TEST" in fasta and SEQUENCE in fasta and "Q0WEAK" not in fasta  # E-value cutoff applied

    bjob = manager.job(puid, boltz["uid"])
    yaml = (manager.job_dir(puid, boltz["uid"]) / "boltz_input.yaml").read_text()
    assert "id: [A, B]" in yaml and "rna:" in yaml and "ccd: 'ATP'" in yaml and "id: [E, F]" in yaml
    assert "smiles: 'CC(=O)O'" in yaml and "msa: empty" in yaml
    assert {h["label"] for h in bjob["highlights"]} >= {"ipTM", "pLDDT"}


def test_hmm_search_requires_profiles(manager, synthetic, tmp_path):
    puid, imp, seq = _setup(manager, synthetic)
    db = tmp_path / "db.fasta"
    db.write_text(">x\nMKV\n")
    withseq = manager.create_job(puid, "modelangelo_build", {}, {"map": {"job": imp, "output": "map_sharp"},
                                                                  "sequence": {"job": seq, "output": "sequence"}})
    hmm = manager.create_job(puid, "modelangelo_hmm_search", {"database": str(db)}, {"model": {"job": withseq["uid"], "output": "model"}})
    for j in (withseq, hmm):
        manager.queue_job(puid, j["uid"])
    run_until_done(manager, puid, [withseq["uid"], hmm["uid"]])
    job = manager.job(puid, hmm["uid"])
    assert job["status"] == "failed" and "hmm_profiles" in job["error"]


def test_map_and_refinement_additions(manager, synthetic):
    puid, imp, seq = _setup(manager, synthetic)
    iso = manager.create_job(puid, "spisonet", {"epochs": 2}, {"half_maps": {"job": imp, "output": "half_maps"},
                                                               "mask": {"job": imp, "output": "mask"}})
    dfsc = manager.create_job(puid, "directional_fsc", {"sampling": 30}, {"half_maps": {"job": imp, "output": "half_maps"},
                                                                          "mask": {"job": imp, "output": "mask"}})
    elbow = manager.create_job(puid, "phenix_elbow", {"ccd_code": "atp"})
    mdl = manager.create_job(puid, "import_model", {"source": "file", "path": str(synthetic["model"])})
    uids = [iso["uid"], dfsc["uid"], elbow["uid"], mdl["uid"]]
    for u in uids:
        manager.queue_job(puid, u)
    run_until_done(manager, puid, uids)
    for u in uids:
        _ok(manager, puid, u)
    douse = manager.create_job(puid, "phenix_douse", {}, {"model": {"job": mdl["uid"], "output": "model"},
                                                          "map": {"job": imp, "output": "map_sharp"}})
    rsr = manager.create_job(puid, "phenix_real_space_refine", {},
                             {"model": {"job": mdl["uid"], "output": "model"}, "map": {"job": imp, "output": "map_sharp"},
                              "restraints": {"job": elbow["uid"], "output": "restraints"}})
    for j in (douse, rsr):
        manager.queue_job(puid, j["uid"])
    run_until_done(manager, puid, [douse["uid"], rsr["uid"]])
    _ok(manager, puid, douse["uid"])
    _ok(manager, puid, rsr["uid"])

    ijob = manager.job(puid, iso["uid"])
    assert {o["name"] for o in ijob["outputs"]} == {"half_maps", "map", "fsc3d"}
    ilog = log_of(manager, puid, iso["uid"])
    assert "fsc3d" in ilog and "--aniso_file" in ilog and "--gpuID 0" in ilog

    djob = manager.job(puid, dfsc["uid"])
    assert djob["outputs"][0]["meta"]["anisotropy"] >= 1.0
    detail = manager.job_detail(puid, dfsc["uid"])
    heat = [s for s in detail["report"]["sections"] if s["kind"] == "heatmap"][0]
    assert len(heat["values"]) == len(heat["y_labels"]) == 4 and len(heat["x_labels"]) == 12

    assert "--chemical_component=ATP" in log_of(manager, puid, elbow["uid"])
    assert "ATP.cif" in log_of(manager, puid, rsr["uid"])  # restraints passed to real_space_refine
    hl = {h["label"]: h["value"] for h in manager.job(puid, douse["uid"])["highlights"]}
    assert hl["Waters added"] == 3


def test_parse_ligand_lines():
    assert parse_ligand_lines("CCD:ATP\n2xCCD:MG\n\n# comment\nSMILES:CCO") == [(1, "ccd", "ATP"), (2, "ccd", "MG"), (1, "smiles", "CCO")]
    with pytest.raises(ValueError):
        parse_ligand_lines("ATP")


def test_directional_fsc_detects_anisotropy():
    rng = np.random.default_rng(0)
    n = 48
    sig = rng.normal(size=(n, n, n)).astype(np.float32)
    f = np.fft.rfftn(sig)
    kz = np.fft.fftfreq(n)[:, None, None]
    f *= np.exp(-(kz ** 2) / (2 * 0.05 ** 2))  # signal only at low frequency along z
    sig = np.fft.irfftn(f, s=sig.shape, axes=(0, 1, 2)).astype(np.float32)
    sig /= sig.std()
    a = sig + rng.normal(0, 0.5, sig.shape).astype(np.float32)
    b = sig + rng.normal(0, 0.5, sig.shape).astype(np.float32)
    r = directional_fsc(a, b, np.array([1.0, 1.0, 1.0]), az_step=30, el_step=30)
    res = {(d["azimuth"], d["elevation"]): d["resolution"] for d in r["directions"]}
    assert res[(0.0, 0.0)] < 2.5 and res[(0.0, 90.0)] > 5.0
