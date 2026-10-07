"""checkMySequence and the official wwPDB validation report (OneDep web service client)."""
from __future__ import annotations

import gzip
import stat

import pytest

from conftest import log_of, run_until_done
from cryoplug.jobs.base import JobContext, JobError
from cryoplug.jobs.validation import _OneDep


def _setup(manager, synthetic, title):
    p = manager.create_project(title)
    puid = p["uid"]
    imp = manager.create_job(puid, "import_cryosparc", {"job_dir": str(synthetic["cryosparc_job"])})
    mdl = manager.create_job(puid, "import_model", {"source": "file", "path": str(synthetic["model"])})
    seq = manager.create_job(puid, "import_sequence", {"path": str(synthetic["fasta"])})
    for j in (imp, mdl, seq):
        manager.queue_job(puid, j["uid"])
    run_until_done(manager, puid, [imp["uid"], mdl["uid"], seq["uid"]])
    return puid, imp["uid"], mdl["uid"], seq["uid"]


def _run(manager, puid, jtype, params, inputs):
    job = manager.create_job(puid, jtype, params, inputs)
    manager.queue_job(puid, job["uid"])
    run_until_done(manager, puid, [job["uid"]])
    done = manager.job(puid, job["uid"])
    assert done["status"] == "completed", f"{done['error']}\n{log_of(manager, puid, job['uid'])}"
    return done


def test_checkmysequence_job(manager, synthetic):
    puid, imp, mdl, seq = _setup(manager, synthetic, "Sequence check")
    done = _run(manager, puid, "checkmysequence", {}, {"model": {"job": mdl, "output": "model"},
                                                      "map": {"job": imp, "output": "map_sharp"},
                                                      "sequence": {"job": seq, "output": "sequence"}})
    log = log_of(manager, puid, done["uid"])
    assert "--jsonout" in log and "--plot" in log and "--seqin" in log
    hl = {h["label"]: h for h in done["highlights"]}
    assert hl["Sequence check"]["value"] == "1 issue" and hl["Sequence check"]["status"] == "bad"
    report = manager.job_detail(puid, done["uid"])["report"]["sections"]
    shifts = next(s for s in report if s["title"].startswith("Possible register shifts"))
    assert shifts["rows"][0][:3] == ["A", "5–18", "+2"]
    assert any(s["kind"] == "plot" for s in report)
    files = done["outputs"][0]["files"]
    assert any(f.endswith(".json") for f in files) and any(f.endswith(".pdf") for f in files)


def test_wwpdb_validation_job(manager, synthetic):
    puid, imp, mdl, _seq = _setup(manager, synthetic, "wwPDB report")
    done = _run(manager, puid, "wwpdb_validation", {"poll": 5}, {"model": {"job": mdl, "output": "model"},
                                                                 "map": {"job": imp, "output": "map_sharp"}})
    jdir = manager.job_dir(puid, done["uid"])
    cif = (jdir / "model_for_validation.cif").read_text()
    assert "ELECTRON MICROSCOPY" in cif and "_em_3d_reconstruction.resolution" in cif
    with gzip.open(jdir / "primary_map.map.gz", "rb") as fh:
        assert fh.read() == synthetic["map"].read_bytes()
    log = log_of(manager, puid, done["uid"])
    assert "--exp_method EM" in log and "wwPDB server status: completed" in log
    names = sorted(f.rsplit("/", 1)[-1] for f in done["outputs"][0]["files"])
    assert names == ["validation_data.cif", "validation_data.xml", "validation_log.txt", "validation_report.pdf", "validation_sliders.svg"]
    hl = {h["label"]: h for h in done["highlights"]}
    assert hl["Clashscore"]["value"] == "3.21 (P92)" and hl["Clashscore"]["status"] == "good"
    sections = manager.job_detail(puid, done["uid"])["report"]["sections"]
    pct = next(s for s in sections if s["title"].startswith("Percentile ranks"))
    rota = next(m for m in pct["metrics"] if m["label"].startswith("Sidechain"))
    assert rota["status"] == "bad" and "percentile 15" in rota["value"]
    outliers = next(s for s in sections if s["title"].startswith("Residues with outliers ("))
    assert outliers["rows"] == [["A", "LEU 12", "Ramachandran, rotamer, clash"]]
    assert done["outputs"][0]["meta"]["percentiles"]["clashscore"] == 92.0


def test_onedep_errors_are_not_silent(tmp_path):
    """The client prints 'OneDep error' and exits with 0 on server errors: the job must still fail."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "onedep_validate_cli"
    exe.write_text("#!/bin/bash\necho 'OneDep error: Service temporarily unavailable'\nexit 0\n")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    job_dir = tmp_path / "J1"
    job_dir.mkdir()
    ctx = JobContext({"uid": "J1", "project_dir": str(tmp_path), "tools": {"onedep": {"bin_dir": str(bin_dir)}}}, job_dir)
    with pytest.raises(JobError, match="Service temporarily unavailable"):
        _OneDep(ctx).call("--new_session")
