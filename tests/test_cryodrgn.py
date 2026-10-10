"""cryoDRGN jobs with a fake cryodrgn: input check, training (live losses, presets, versions), analysis with
covariates and diagnostics, selections, continuation, convergence, landscape, trajectories and volumes."""
from __future__ import annotations

import json

import numpy as np
import pytest
from conftest import log_of, run_until_done

from cryoplug import particles as pt
from cryoplug.jobs.cryodrgn import (
    EPOCH_RE,
    assess_backprojection,
    covariate_r2,
    fmt_duration,
    loss_trend,
    model_preset,
    parse_losses,
)
from cryoplug.mrc import MapVolume


def _run(manager, puid, jtype, params, inputs=None, ok=True):
    job = manager.create_job(puid, jtype, params, inputs or {})
    manager.queue_job(puid, job["uid"])
    run_until_done(manager, puid, [job["uid"]], timeout=240)
    done = manager.job(puid, job["uid"])
    if ok:
        assert done["status"] == "completed", f"{done['error']}\n{log_of(manager, puid, job['uid'])}"
    return done


def _outs(job):
    return {o["name"]: o for o in job["outputs"]}


def _sections(manager, puid, job):
    return manager.job_detail(puid, job["uid"])["report"]["sections"]


def _view(manager, puid, job, index=0):
    secs = [s for s in _sections(manager, puid, job) if s["kind"] == "latent"]
    return json.loads((manager.project_dir(puid) / secs[index]["path"]).read_text())


def _highlights(job):
    return {h["label"]: h for h in job["highlights"]}


def _npz(manager, puid, out):
    with np.load(manager.project_dir(puid) / out["path"]) as data:
        return {k: data[k] for k in data.files}


@pytest.fixture()
def drgn_project(manager, cs_particles):
    p = manager.create_project("cryoDRGN")
    puid = p["uid"]
    imp = _run(manager, puid, "import_cryosparc", {"job_dir": str(cs_particles["refine"])})
    return puid, imp


@pytest.fixture()
def trained(manager, drgn_project):
    puid, imp = drgn_project
    train = _run(manager, puid, "cryodrgn_train", {"box": 16, "zdim": 4, "epochs": 4, "ksample": 6},
                 {"particles": {"job": imp["uid"], "output": "particles"}})
    return puid, imp, train


# ------------------------------------------------------------------ helpers
def test_log_parsing_and_helpers(tmp_path):
    line = ("# =====> Epoch: 12 Average gen loss = 0.912345, KLD = 3.210000, total loss = 1.320000; "
            "Finished in 1 day, 2:03:04.500000")
    m = EPOCH_RE.search(line)
    assert m and m.group(1) == "12"
    log = tmp_path / "run.log"
    log.write_text(line + "\n# =====> Epoch: 13 Average gen loss = nan, KLD = 1.0, total loss = 2.0; Finished in 0:00:05\n")
    rows = parse_losses(log)
    assert rows[0]["seconds"] == pytest.approx(86400 + 2 * 3600 + 3 * 60 + 4.5) and rows[0]["kld"] == 3.21
    assert np.isnan(rows[1]["gen"]) and rows[1]["seconds"] == 5
    assert fmt_duration(42) == "42 s" and fmt_duration(600) == "10 min" and fmt_duration(3 * 3600 + 300) == "3 h 05"
    assert model_preset({"model": "auto (protocol)"}, 128)["enc_dim"] == 256
    assert model_preset({"model": "auto (protocol)"}, 256)["name"] == "large"
    assert model_preset({"model": "auto (protocol)"}, 128, second_round=True)["name"] == "large"
    custom = model_preset({"model": "custom (widths below)", "enc_dim": 512, "enc_layers": 2, "dec_dim": 128, "dec_layers": 4}, 64)
    assert (custom["enc_dim"], custom["dec_layers"]) == (512, 4)
    falling = [{"epoch": e, "gen": 1.0 - 0.02 * e} for e in range(1, 21)]
    flat = [{"epoch": e, "gen": 1.0 + 0.5 * np.exp(-e)} for e in range(1, 41)]
    assert loss_trend(falling)["state"] == "falling" and loss_trend(flat)["state"] == "plateau"


def test_covariate_r2_detects_encoded_properties():
    rng = np.random.default_rng(0)
    z = rng.normal(0, 1, (2000, 4))
    r2 = covariate_r2(z, {"tied": z[:, 0] ** 2 + 0.05 * rng.normal(size=2000), "free": rng.normal(size=2000)})
    assert r2["tied"][1] > 0.8 and r2["tied"][0] < 0.2  # non-linear: only the neighbour average sees it
    assert max(r2["free"]) < 0.1


def test_backprojection_assessment(tmp_path):
    box = 32
    zz, yy, xx = np.indices((box, box, box))
    blob = np.exp(-((xx - 16) ** 2 + (yy - 16) ** 2 + (zz - 16) ** 2) / 32.0)
    rng = np.random.default_rng(0)
    for name, data in (("full", blob), ("a", blob + rng.normal(0, 0.02, blob.shape)), ("b", blob + rng.normal(0, 0.02, blob.shape)),
                       ("neg", -blob), ("na", rng.normal(0, 1, blob.shape)), ("nb", rng.normal(0, 1, blob.shape))):
        MapVolume(data=data.astype(np.float32), voxel=np.array([4.0] * 3)).write(tmp_path / f"{name}.mrc")
    good = assess_backprojection(tmp_path / "full.mrc", tmp_path / "a.mrc", tmp_path / "b.mrc")
    assert good["verdict"] == "good" and good["contrast"] > 1 and good["res143"] < good["good_below"]
    inverted = assess_backprojection(tmp_path / "neg.mrc")
    assert inverted["contrast_status"] == "bad" and inverted["verdict"] == "bad"
    noise = assess_backprojection(tmp_path / "full.mrc", tmp_path / "na.mrc", tmp_path / "nb.mrc")
    assert noise["fsc_status"] == "bad"


def test_star_and_image_helpers(tmp_path):
    stack = tmp_path / "Extract" / "a.mrcs"
    stack.parent.mkdir()
    imgs = np.arange(5 * 8 * 8, dtype=np.float32).reshape(5, 8, 8)
    MapVolume(data=imgs, voxel=np.ones(3)).write(stack)
    star = tmp_path / "p.star"
    star.write_text("data_optics\n\nloop_\n_rlnOpticsGroup #1\n_rlnImageSize #2\n1 8\n\ndata_particles\n\nloop_\n"
                    "_rlnImageName #1\n_rlnOpticsGroup #2\n" + "".join(f"{i + 1:06d}@Extract/a.mrcs 1\n" for i in (4, 2, 0)))
    reader = pt.ParticleImages(star)
    assert reader.n == 3 and np.array_equal(reader.image(1), imgs[2])
    sub = pt.filter_star(star, tmp_path / "sub.star", np.array([2]))
    assert pt.star_count(sub) == 1 and "data_optics" in sub.read_text() and pt.star_column(sub, "_rlnImageName") == ["000001@Extract/a.mrcs"]
    txt = tmp_path / "Extract" / "list.txt"
    MapVolume(data=imgs[:2] + 1000, voxel=np.ones(3)).write(tmp_path / "Extract" / "b.mrcs")
    txt.write_text("a.mrcs\nb.mrcs\n")
    chunks = pt.ParticleImages(txt)
    assert chunks.n == 7 and chunks.image(6)[0, 0] == imgs[1, 0, 0] + 1000
    tile = pt.particle_tile(chunks.image(0), size=24)
    sheet = pt.montage([tile, tile, tile], per_row=2)
    assert tile.shape == (24, 24) and sheet.shape == (24 * 2 + 3, 24 * 2 + 3, 4) and sheet[-1, -1, 3] == 0
    square = np.array([[0, 0], [1, 0], [1, 1], [0, 1]])
    assert pt.points_in_polygon(np.array([[0.5, 0.5], [1.5, 0.5]]), square).tolist() == [True, False]


# ------------------------------------------------------------------ jobs
def test_training_report(manager, trained):
    puid, imp, train = trained
    log = log_of(manager, puid, train["uid"])
    assert "--version" in log and "parse_pose_csparc" in log and "-D 32" in log and "downsample" in log
    assert "backproject_voxel" in log and "--first 10000" in log
    assert "--enc-dim 256" in log and "--dec-dim 256" in log and "-b 16" in log and "--no-analysis" in log
    assert "--no-amp" not in log and "--ksample 6" in log and "--Apix 4" in log
    outs = _outs(train)
    assert {"latent", "kmeans", "pc1", "pc2", "particles_prepared", "backprojection"} <= set(outs)
    assert outs["kmeans"]["type"] == "volume_series" and len(outs["kmeans"]["files"]) == 6
    assert outs["kmeans"]["meta"]["frame_labels"][0].startswith("Cluster 1 · ")
    meta = outs["latent"]["meta"]
    assert meta["method"] == "cryoDRGN" and meta["zdim"] == 4 and meta["apix"] == 4.0 and meta["epoch"] == 4
    assert meta["train"]["model"] == "small" and meta["train"]["epochs"] == 4 and meta["train"]["version"] == "4.3.1"
    assert meta["workdirs"] == [meta["workdir"]] and meta["prepared"]["box"] == 16
    hl = _highlights(train)
    assert hl["Input check"]["value"] == "passed" and hl["Input check"]["status"] == "good"
    assert hl["Time per epoch"]["value"] == "2 s" and hl["Flagged clusters"]["status"] == "warn"
    secs = _sections(manager, puid, train)
    keys = {s.get("key"): s for s in secs if s.get("key")}
    assert keys["losses"]["series"][0]["x"] == [1, 2, 3, 4] and keys["kld"]["kind"] == "plot"
    assert keys["input_check"]["kind"] == "metrics" and keys["input_check_fsc"]["x_kind"] == "resolution"
    titles = [s["title"] for s in secs]
    assert sum(t == "Training loss" for t in titles) == 1  # redrawn in place, not appended per epoch
    assert "Viewing directions of the particles" in titles and "What to do next" in titles
    diag = next(s for s in secs if s["title"].startswith("Is the latent space structural"))
    assert {r[0] for r in diag["rows"]} == {"Defocus", "Viewing direction", "In-plane shift"}
    clusters = next(s for s in secs if s["title"] == "Latent clusters (k-means)")
    assert "weak" in clusters["rows"][0][3]
    advice = next(s for s in secs if s["title"] == "What to do next")["text"]
    assert "Junk?" in advice and "256 px" in advice
    view = _view(manager, puid, train)
    assert view["n_total"] == 300 and len(view["index"]) == view["n_shown"] == 300
    assert set(view["embeddings"]) == {"UMAP", "PCA"}
    assert {"znorm", "pc1", "pc2", "pc3", "defocus", "tilt", "azimuth", "shift"} <= set(view["colorings"])
    defocus = view["colorings"]["defocus"]
    assert defocus["unit"] == "µm" and len(defocus["values"]) == 300 and 0.5 <= defocus["lo"] < defocus["hi"] <= 0.52  # (df1 + df2) / 2, df2 = 0 here
    assert view["colorings"]["azimuth"]["cyclic"] and view["colorings"]["tilt"]["hi"] == 180
    c0 = view["clusters"][0]
    assert c0["volume"].endswith("vol_001.mrc") and c0["image"].endswith("cluster_001.png") and "weak" in c0["flags"]
    assert (manager.project_dir(puid) / c0["image"]).exists() and "particle" in c0
    assert sum(c["count"] for c in view["clusters"]) == 300 and view["outliers"]["zscore"] == 2.0
    arrays = _npz(manager, puid, outs["latent"])
    assert {"z", "pca", "umap", "labels", "centers_ind", "znorm", "cov_defocus", "cov_tilt", "direction"} <= set(arrays)
    assert arrays["direction"].shape == (300, 3) and np.allclose(np.linalg.norm(arrays["direction"], axis=1), 1, atol=1e-4)


def test_analysis_trajectory_and_volumes(manager, trained):
    puid, _imp, train = trained
    latent = {"latent": {"job": train["uid"], "output": "latent"}}
    ana = _run(manager, puid, "cryodrgn_analyze", {"ksample": 8, "n_per_pc": 5, "epoch": 2, "downsample": 8}, latent)
    log = log_of(manager, puid, ana["uid"])
    assert " 2 -o " in log and "--n-per-pc 5" in log and "-d 8" in log and "--Apix 8" in log
    outs = _outs(ana)
    assert len(outs["kmeans"]["files"]) == 8 and len(outs["pc1"]["files"]) == 5 and outs["latent"]["meta"]["epoch"] == 2
    assert outs["latent"]["meta"]["train"]["model"] == "small"  # the lineage is kept
    # volumes generated from that analysis are at the training size: training pixel size, not the downsampled one
    from_ana = _run(manager, puid, "cryodrgn_volumes", {"particles": "5"}, {"latent": {"job": ana["uid"], "output": "latent"}})
    assert "--Apix 4" in log_of(manager, puid, from_ana["uid"]) and "--Apix 8" not in log_of(manager, puid, from_ana["uid"])
    traj = _run(manager, puid, "cryodrgn_trajectory", {"clusters": "1, 6", "frames": 6, "loop": True}, latent)
    tout = _outs(traj)["trajectory"]
    assert tout["type"] == "volume_series" and 2 <= len(tout["files"]) <= 6 and tout["meta"]["particles"]
    tlog = log_of(manager, puid, traj["uid"])
    assert "graph_traversal" in tlog and "--Apix 4" in tlog
    view = _view(manager, puid, traj)
    assert view["paths"][0]["points"]["UMAP"] and "defocus" in view["colorings"]
    straight = _run(manager, puid, "cryodrgn_trajectory", {"particles": "3, 250", "path": "straight line", "frames": 4}, latent)
    assert len(_outs(straight)["trajectory"]["files"]) == 4
    one = _run(manager, puid, "cryodrgn_volumes", {"particles": "7"}, latent)
    assert _outs(one)["volume"]["type"] == "map" and _outs(one)["volume"]["label"].startswith("Volume of particle 7")
    assert _view(manager, puid, one)["marks"][0]["particle"] == 7
    arrays = _npz(manager, puid, _outs(train)["latent"])
    umap = arrays["umap"]
    lo, hi = np.percentile(umap, [5, 60], axis=0)
    region = json.dumps({"embedding": "UMAP", "polygon": [[lo[0], lo[1]], [hi[0], lo[1]], [hi[0], hi[1]], [lo[0], hi[1]]]})
    several = _run(manager, puid, "cryodrgn_volumes", {"selection": "region", "region": region, "volumes": 3}, latent)
    assert len(_outs(several)["volumes"]["files"]) == 3
    from cryoplug.jobs import get_job_type
    problems = get_job_type("cryodrgn_volumes").check({"selection": "region", "region": "{}"}, {"latent": "x"})
    assert any("polygon" in m for m in problems)


def test_selections_and_retraining(manager, trained):
    puid, imp, train = trained
    latent = {"latent": {"job": train["uid"], "output": "latent"}}
    sel = _run(manager, puid, "select_particles", {"clusters": "1-3"}, latent)
    outs = _outs(sel)
    kept, left = outs["particles"], outs["excluded"]
    assert kept["meta"]["n_particles"] == 150 and left["meta"]["n_particles"] == 150
    assert len(pt.read_cs(manager.project_dir(puid) / kept["path"])) == 150
    assert kept["meta"]["datadir"] == _outs(imp)["particles"]["meta"]["datadir"] and kept["meta"]["parent_cryodrgn"]["box"] == 16
    assert _view(manager, puid, sel)["selected"] == [0, 1, 2]
    # a region drawn with the lasso: the same particles as the polygon test on the stored map
    arrays = _npz(manager, puid, _outs(train)["latent"])
    lo, hi = np.percentile(arrays["umap"], [10, 70], axis=0)
    poly = [[lo[0], lo[1]], [hi[0], lo[1]], [hi[0], hi[1]], [lo[0], hi[1]]]
    expected = int(pt.points_in_polygon(arrays["umap"], np.array(poly)).sum())
    reg = _run(manager, puid, "select_particles",
               {"selection": "region", "region": json.dumps({"embedding": "UMAP", "polygon": poly}), "action": "remove"}, latent)
    assert _outs(reg)["particles"]["meta"]["n_particles"] == 300 - expected
    assert _view(manager, puid, reg)["clusters"][0]["name"].startswith("Kept")
    out = _run(manager, puid, "select_particles", {"selection": "outliers", "zscore": 1.0, "action": "remove"}, latent)
    norm = np.linalg.norm(arrays["z"], axis=1)
    assert _outs(out)["particles"]["meta"]["n_particles"] == int((norm <= norm.mean() + norm.std()).sum())
    # train again on the kept clusters: the downsampled images are reused with --ind, no new input check needed
    again = _run(manager, puid, "cryodrgn_train", {"box": 16, "zdim": 4, "epochs": 2, "ksample": 4, "check_inputs": "no check"},
                 {"particles": {"job": sel["uid"], "output": "particles"}})
    log2 = log_of(manager, puid, again["uid"])
    assert "Reusing the 16 px particles of the parent dataset" in log2 and "--ind" in log2 and "downsample" not in log2
    assert "backproject_voxel" not in log2 and _outs(again)["latent"]["meta"]["n_particles"] == 150
    sel2 = _run(manager, puid, "select_particles", {"clusters": "1", "action": "remove"},
                {"latent": {"job": again["uid"], "output": "latent"}})
    parent_ind = pt.load_array_pkl(manager.project_dir(puid) / _outs(sel2)["particles"]["meta"]["parent_indices"])
    first_ind = pt.load_array_pkl(manager.project_dir(puid) / kept["meta"]["parent_indices"])
    assert set(parent_ind.tolist()) <= set(first_ind.tolist())


def test_continue_convergence_and_landscape(manager, trained):
    puid, _imp, train = trained
    latent = {"latent": {"job": train["uid"], "output": "latent"}}
    cont = _run(manager, puid, "cryodrgn_continue", {"epochs": 8, "ksample": 5}, latent)
    log = log_of(manager, puid, cont["uid"])
    assert "--load" in log and "weights.pkl" in log and "-n 8" in log and "--enc-dim 256" in log
    meta = _outs(cont)["latent"]["meta"]
    assert meta["epoch"] == 8 and len(meta["workdirs"]) == 2 and meta["train"]["epochs"] == 8
    losses = next(s for s in _sections(manager, puid, cont) if s.get("key") == "losses")
    assert losses["series"][0]["x"] == list(range(1, 9))  # earlier epochs included
    too_few = _run(manager, puid, "cryodrgn_continue", {"epochs": 3}, latent, ok=False)
    assert too_few["status"] == "failed" and "already has 4 epochs" in too_few["error"]

    conv = _run(manager, puid, "cryodrgn_convergence", {"particles": 3, "box": 16},
                {"latent": {"job": cont["uid"], "output": "latent"}})
    outs = _outs(conv)
    assert {"rep_1", "rep_2", "rep_3"} <= set(outs) and outs["rep_1"]["meta"]["frames"] >= 5
    assert outs["rep_1"]["meta"]["frame_labels"][-1] == "Epoch 8"
    clog = log_of(manager, puid, conv["uid"])
    assert "eval_vol" in clog and "-d" not in clog.split("eval_vol")[1].split("\n")[0]  # 16 px is the training size
    secs = _sections(manager, puid, conv)
    titles = [s["title"] for s in secs]
    assert {"Motion of the particles in the latent space", "Neighbourhoods of the particles", "Convergence",
            "Volume stability across epochs", "What to do"} <= set(titles)
    verdict = next(s for s in secs if s["title"] == "Convergence")["metrics"][-1]
    assert verdict["label"] == "Verdict" and verdict["value"] in ("converged", "nearly converged", "not converged")
    view = _view(manager, puid, conv)
    assert all(k.startswith("Epoch ") for k in view["embeddings"]) and "Epoch 8" in view["embeddings"]
    assert "Convergence" in _highlights(conv)

    land = _run(manager, puid, "cryodrgn_landscape", {"sketch": 20, "states": 4, "box": 32}, latent)
    llog = log_of(manager, puid, land["uid"])
    assert "analyze_landscape" in llog and "--skip-umap" in llog and "-N 20" in llog and "-M 4" in llog
    assert "-d 16 --Apix 4" in llog  # never larger than the training images
    louts = _outs(land)
    assert len(louts["states"]["files"]) == 4 and len(louts["vol_pc1"]["files"]) == 10
    lmeta = louts["latent"]["meta"]
    assert lmeta["k"] == 4 and lmeta["landscape"] and lmeta["workdir"] == _outs(train)["latent"]["meta"]["workdir"]
    view = _view(manager, puid, land)
    assert view["cluster_name"] == "State" and len(view["clusters"]) == 4 and "Volume PCA" in view["embeddings"]
    assert sum(c["count"] for c in view["clusters"]) == 300
    # the states can be selected like clusters
    sel = _run(manager, puid, "select_particles", {"clusters": "1"}, {"latent": {"job": land["uid"], "output": "latent"}})
    assert _outs(sel)["particles"]["meta"]["n_particles"] == view["clusters"][0]["count"]


def test_input_check_job_and_failures(manager, drgn_project, monkeypatch):
    puid, imp = drgn_project
    particles = {"particles": {"job": imp["uid"], "output": "particles"}}
    bp = _run(manager, puid, "cryodrgn_backproject", {"box": 16, "images": 100}, particles)
    outs = _outs(bp)
    assert outs["backprojection"]["type"] == "map" and outs["particles_prepared"]["meta"]["cryodrgn"]["box"] == 16
    assert "--first 100" in log_of(manager, puid, bp["uid"]) and _highlights(bp)["Input check"]["value"] == "passed"
    # training on the prepared particles reuses them
    monkeypatch.setenv("FAKE_CRYODRGN_INVERTED", "1")
    bad = _run(manager, puid, "cryodrgn_train", {"box": 16, "zdim": 2, "epochs": 2, "check_inputs": "check, stop if it fails"},
               {"particles": {"job": bp["uid"], "output": "particles_prepared"}}, ok=False)
    assert bad["status"] == "failed" and "input check failed" in bad["error"]
    log = log_of(manager, puid, bad["uid"])
    assert "Reusing the particles prepared at 16 px" in log and "train_vae" not in log
    advice = next(s for s in _sections(manager, puid, bad) if s["title"] == "Input check: what to do")["text"]
    assert "Do not invert the images" in advice


def test_old_cryodrgn_versions(manager, drgn_project, monkeypatch):
    """cryoDRGN 3.3: no --no-analysis, checkpoints numbered from 0, no --n-per-pc."""
    monkeypatch.setenv("FAKE_CRYODRGN_VERSION", "3.3.0")
    puid, imp = drgn_project
    train = _run(manager, puid, "cryodrgn_train", {"box": 16, "zdim": 3, "epochs": 3, "ksample": 4, "check_inputs": "no check",
                                                   "amp": False}, {"particles": {"job": imp["uid"], "output": "particles"}})
    log = log_of(manager, puid, train["uid"])
    assert "--no-analysis" not in log and "--no-amp" in log and "cryoDRGN 3.3.0" in log
    assert _outs(train)["latent"]["meta"]["epoch"] == 2 and _outs(train)["latent"]["meta"]["train"]["version"] == "3.3.0"
    ana = _run(manager, puid, "cryodrgn_analyze", {"ksample": 4, "n_per_pc": 4},
               {"latent": {"job": train["uid"], "output": "latent"}})
    alog = log_of(manager, puid, ana["uid"])
    assert "--n-per-pc 4" not in alog and "needs 4.2" in alog and len(_outs(ana)["pc1"]["files"]) == 10


def test_training_parameters(manager, drgn_project):
    from cryoplug.jobs import get_job_type
    jt = get_job_type("cryodrgn_train")
    assert jt.check({"box": 128, "epochs": 50, "checkpoint": 5, "beta": ""}, {"particles": "x"}) == []
    assert any("multiple" in m for m in jt.check({"box": 128, "epochs": 48, "checkpoint": 5}, {"particles": "x"}))
    assert any("β" in m for m in jt.check({"box": 128, "epochs": 50, "beta": "high"}, {"particles": "x"}))
    puid, imp = drgn_project
    train = _run(manager, puid, "cryodrgn_train",
                 {"box": 16, "zdim": 2, "epochs": 4, "ksample": 3, "check_inputs": "no check", "model": "large (1024 × 3)",
                  "beta": "0.5", "pose_sgd": True, "seed": 7, "checkpoint": 2, "batch_size": 8},
                 {"particles": {"job": imp["uid"], "output": "particles"}})
    log = log_of(manager, puid, train["uid"])
    for flag in ("--enc-dim 1024", "--beta 0.5", "--do-pose-sgd", "--seed 7", "--checkpoint 2", "-b 8"):
        assert flag in log, flag
    workdir = manager.project_dir(puid) / _outs(train)["latent"]["meta"]["workdir"]
    assert sorted(p.name for p in workdir.glob("weights.*.pkl")) == ["weights.2.pkl", "weights.4.pkl"]
    # pose SGD: the refined poses of the last epoch colour the explorer
    assert (workdir / "pose.4.pkl").exists() and "tilt" in _view(manager, puid, train)["colorings"]


def test_particle_images_api(manager, trained, cs_particles):
    from fastapi.testclient import TestClient

    from cryoplug.server.app import create_app
    puid, imp, train = trained
    app = create_app(manager.config, start_scheduler=False, manager=manager)
    with TestClient(app, base_url="http://localhost:39500") as client:
        r = client.get(f"/api/projects/{puid}/jobs/{train['uid']}/particle-images", params={"ids": "0,5,299", "size": 48})
        assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content[:4] == b"\x89PNG"
        assert client.get(f"/api/projects/{puid}/jobs/{train['uid']}/particle-images", params={"ids": "300"}).status_code == 400
        assert client.get(f"/api/projects/{puid}/jobs/{train['uid']}/particle-images", params={"ids": "x"}).status_code == 400
        assert client.get(f"/api/projects/{puid}/jobs/{imp['uid']}/particle-images", params={"ids": "1"}).status_code == 404
        # a selection trained again: images come from the parent's prepared stack through the indices
        sel = _run(manager, puid, "select_particles", {"clusters": "2"}, {"latent": {"job": train["uid"], "output": "latent"}})
        again = _run(manager, puid, "cryodrgn_train", {"box": 16, "zdim": 2, "epochs": 2, "ksample": 3, "check_inputs": "no check"},
                     {"particles": {"job": sel["uid"], "output": "particles"}})
        assert client.get(f"/api/projects/{puid}/jobs/{again['uid']}/particle-images", params={"ids": "0,1"}).status_code == 200
    # 3D variability coordinates: images read through the .cs file and the CryoSPARC project folder
    p = manager.create_project("3DVA images")
    va = _run(manager, p["uid"], "import_cryosparc", {"job_dir": str(cs_particles["variability"])})
    with TestClient(app, base_url="http://localhost:39500") as client:
        r = client.get(f"/api/projects/{p['uid']}/jobs/{va['uid']}/particle-images", params={"ids": "0,160"})
        assert r.status_code == 200, r.text
