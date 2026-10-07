"""Finding the outputs of external programs: files written next to the inputs (LocScale 2 does that with a
relative -o), clock skew between the compute node and the file server, and the EMmerNet variance map."""
from __future__ import annotations

import os
import time

import numpy as np
import pytest

from conftest import log_of, run_until_done
from cryoplug.jobs.base import JobContext, JobError
from cryoplug.jobs.building import pick_new_model, report_model
from cryoplug.jobs.interactive import list_candidate_models
from cryoplug.jobs.sharpening import pick_new_map
from cryoplug.mrc import MapVolume


def _map(path):
    return MapVolume(data=np.random.default_rng(0).normal(size=(8, 8, 8)).astype(np.float32), voxel=np.ones(3)).write(path)


@pytest.fixture()
def ctx(tmp_path):
    proj = tmp_path / "CP-test"
    j1, j2 = proj / "J1", proj / "J2"
    j1.mkdir(parents=True)
    j2.mkdir()
    h1, h2 = _map(j1 / "half_A.mrc"), _map(j1 / "half_B.mrc")
    old = _map(j1 / "locscale_old.mrc")  # left over from an earlier run: must stay where it is
    os.utime(old, (time.time() - 3600, time.time() - 3600))
    spec = {"uid": "J2", "project_uid": "P1", "project_dir": str(proj), "params": {},
            "inputs": {"half_maps": {"type": "half_maps", "path": str(h1), "files": [str(h1), str(h2)], "source": "J1.half_maps"}}}
    c = JobContext(spec, j2)
    c.snapshot()
    return c


def test_outputs_written_next_to_the_inputs_are_brought_back(ctx):
    j1, j2 = ctx.project_dir / "J1", ctx.job_dir
    start = time.time()
    _map(j1 / "locscale_model_free.mrc")  # what LocScale did with "-o locscale_model_free.mrc"
    out = pick_new_map(ctx, start, ["locscale_model_free.mrc", "locscale*.mrc"])
    assert out == j2 / "locscale_model_free.mrc" and out.exists()
    assert not (j1 / "locscale_model_free.mrc").exists()
    assert (j1 / "locscale_old.mrc").exists() and (j1 / "half_A.mrc").exists()
    assert "was written next to an input" in ctx.log_path.read_text()

    # outputs with an explicit name are rescued too (DeepEMhancer, EMReady...)
    _map(j1 / "emready.mrc")
    entry = ctx.add_output("map", "map", j2 / "emready.mrc", "EMReady map")
    assert entry["path"] == "J2/emready.mrc" and not (j1 / "emready.mrc").exists()

    # models as well, into the folder the job expects
    (j1 / "model_real_space_refined_000.pdb").write_text("ATOM\n")
    model = pick_new_model(ctx, start, ["*_real_space_refined_*.pdb"], root=j2 / "rsr")
    assert model == j2 / "rsr" / "model_real_space_refined_000.pdb"


def test_missing_outputs_give_a_useful_error(ctx):
    with pytest.raises(JobError, match=r"wrote no map in J2/ \(expected locscale_model_free.mrc\).*see the log"):
        pick_new_map(ctx, time.time(), ["locscale_model_free.mrc"])
    with pytest.raises(JobError, match="Expected output file is missing.*see the log"):
        ctx.add_output("map", "map", ctx.job_dir / "nothing.mrc")
    assert (ctx.project_dir / "J1" / "locscale_old.mrc").exists()  # an old file is never taken for an output


def test_new_files_are_recognised_despite_clock_skew(ctx):
    j2 = ctx.job_dir
    before = _map(j2 / "existing.mrc")
    os.utime(before, (time.time() - 7200, time.time() - 7200))
    ctx.snapshot()
    skewed = _map(j2 / "result.mrc")  # file server clock one hour behind the compute node
    os.utime(skewed, (time.time() - 3600, time.time() - 3600))
    found = ctx.find_new_files(["*.mrc"], since=time.time())
    assert skewed in found and before not in found


def test_emmernet_keeps_its_outputs_and_registers_the_variance_map(manager, synthetic):
    p = manager.create_project("EMmerNet paths")
    puid = p["uid"]
    imp = manager.create_job(puid, "import_cryosparc", {"job_dir": str(synthetic["cryosparc_job"])})
    manager.queue_job(puid, imp["uid"])
    run_until_done(manager, puid, [imp["uid"]])
    emn = manager.create_job(puid, "emmernet", {}, {"half_maps": {"job": imp["uid"], "output": "half_maps"}})
    manager.queue_job(puid, emn["uid"])
    run_until_done(manager, puid, [emn["uid"]])
    job = manager.job(puid, emn["uid"])
    assert job["status"] == "completed", f"{job['error']}\n{log_of(manager, puid, emn['uid'])}"
    outs = {o["name"]: o for o in job["outputs"]}
    assert outs["map"]["path"] == f"{emn['uid']}/feature_enhanced.mrc"
    assert outs["confidence"]["path"] == f"{emn['uid']}/feature_enhanced_variance.mrc"
    proj = manager.project_dir(puid)
    assert not list((proj / imp["uid"]).glob("feature_enhanced*")) and not (proj / imp["uid"] / "processing_files").exists()


def test_an_unreadable_model_summary_does_not_fail_the_job(ctx):
    bad = ctx.job_dir / "odd_model.cif"
    bad.write_text("data_odd\n_this.is not a model\n")
    assert report_model(ctx, bad) == {}
    assert "Model content not summarised (odd_model.cif)" in ctx.log_path.read_text()
    entry = ctx.add_output("model", "model", bad, "Model")  # the output is still registered
    assert entry["path"] == "J2/odd_model.cif"


def test_coot_default_save_name_is_a_candidate(tmp_path):
    job = tmp_path / "J9"
    (job / "coot-backup").mkdir(parents=True)
    (tmp_path / "model.pdb").write_text("ATOM\n")
    (job / "input_model.pdb").symlink_to(tmp_path / "model.pdb")  # staged input
    (job / "input_model.cif").write_text("data_x\n")  # staged input (copy when symlinks are not possible)
    (job / "coot-backup" / "input_model_backup.pdb").write_text("ATOM\n")
    (job / "input_model-coot-0.pdb").write_text("ATOM\n")  # what Coot suggests when saving input_model.pdb
    assert [c["path"] for c in list_candidate_models(job)] == ["input_model-coot-0.pdb"]
