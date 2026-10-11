"""cryoDRGN-AI (cryodrgn abinit) with a fake cryodrgn: pose maths (conversions, comparison, frames, map alignment),
the ab initio job (input check, live phases, comparison with the consensus, hand, learned poses, validation) and
the jobs that use its models (analysis, trajectories, continuation, convergence, landscape, selection, training)."""
from __future__ import annotations

import json
import pickle

import numpy as np
import pytest
from conftest import log_of, run_until_done

from cryoplug import particles as pt
from cryoplug import poses as ps
from cryoplug.jobs.cryodrgn_ai import ABINIT_EPOCH_RE, AbinitMonitor, abinit_row
from cryoplug.mrc import MapVolume


def _run(manager, puid, jtype, params, inputs=None, ok=True):
    job = manager.create_job(puid, jtype, params, inputs or {})
    manager.queue_job(puid, job["uid"])
    run_until_done(manager, puid, [job["uid"]], timeout=300)
    done = manager.job(puid, job["uid"])
    if ok:
        assert done["status"] == "completed", f"{done['error']}\n{log_of(manager, puid, job['uid'])}"
    return done


def _outs(job):
    return {o["name"]: o for o in job["outputs"]}


def _sections(manager, puid, job):
    return manager.job_detail(puid, job["uid"])["report"]["sections"]


def _section(manager, puid, job, key):
    return next(s for s in _sections(manager, puid, job) if s.get("key") == key)


def _view(manager, puid, job, index=0):
    secs = [s for s in _sections(manager, puid, job) if s["kind"] == "latent"]
    return json.loads((manager.project_dir(puid) / secs[index]["path"]).read_text())


def _highlights(job):
    return {h["label"]: h for h in job["highlights"]}


ABINIT = {"box": 16, "zdim": 4, "epochs": 8, "pose_epochs": 2, "checkpoint": 2, "ksample": 6, "validate_images": 0}


@pytest.fixture()
def drgn_project(manager, cs_particles):
    p = manager.create_project("cryoDRGN-AI")
    puid = p["uid"]
    imp = _run(manager, puid, "import_cryosparc", {"job_dir": str(cs_particles["refine"])})
    return puid, imp


@pytest.fixture()
def abinit_mirror(manager, drgn_project, monkeypatch):
    """An ab initio run whose model came out as the mirror image of the consensus."""
    monkeypatch.setenv("FAKE_CRYODRGN_MIRROR", "1")
    puid, imp = drgn_project
    job = _run(manager, puid, "cryodrgn_abinit", ABINIT, {"particles": {"job": imp["uid"], "output": "particles"}})
    monkeypatch.delenv("FAKE_CRYODRGN_MIRROR")
    return puid, imp, job


# --------------------------------------------------------------- pose maths
def test_pose_conversions_round_trip():
    rng = np.random.default_rng(0)
    rot = ps.uniform_rotations(200)[rng.permutation(200)]
    assert np.abs(ps.relion_to_matrix(ps.matrix_to_relion(rot)) - rot).max() < 1e-9
    trans = rng.normal(0, 0.02, (200, 2))
    pose, shift = ps.cryodrgn_to_cryosparc(rot, trans, 128)
    rot2, trans2 = ps.cryosparc_to_cryodrgn(pose, shift, 128)
    assert np.abs(rot2 - rot).max() < 1e-9 and np.abs(trans2 - trans).max() < 1e-12
    assert np.allclose(ps.rotation_angle(ps.axis_rotation("z", 30)), 30)
    ops, used = ps.symmetry_operators("D3")
    assert used == "D3" and len(ops) == 6 and np.allclose(np.linalg.det(ops), 1)
    assert ps.symmetry_operators("O")[1] == "C1"


@pytest.mark.parametrize("mirror,symmetry", [(False, "C1"), (True, "C1"), (True, "C4")])
def test_compare_poses_finds_frame_hand_and_outliers(mirror, symmetry):
    rng = np.random.default_rng(1)
    n = 2000
    rot_c = ps.uniform_rotations(n)[rng.permutation(n)]
    trans_c = rng.normal(0, 0.02, (n, 2))
    g = ps.rotvec_to_matrix(np.array([[0.7, -0.4, 1.1]]))[0]
    w = np.array([0.01, -0.02, 0.03])
    ops, _ = ps.symmetry_operators(symmetry)
    copies = np.einsum("nij,nkj->nik", rot_c, ops[rng.integers(0, len(ops), n)])  # any symmetry-equivalent pose
    rot_a = (ps.MIRROR @ copies @ g @ ps.MIRROR) if mirror else copies @ g
    rot_a = rot_a @ ps.rotvec_to_matrix(rng.normal(0, np.radians(2), (n, 3)))
    junk = rng.random(n) < 0.15
    rot_a[junk] = ps.uniform_rotations(int(junk.sum()) + 3)[: int(junk.sum())]
    aligned = (ps.MIRROR @ rot_a @ ps.MIRROR if mirror else rot_a) @ g.T
    trans_a = trans_c + np.einsum("nij,j->ni", aligned[:, :2, :], w)
    cmp = ps.compare_poses(rot_a, trans_a, rot_c, trans_c, symmetry=symmetry)
    assert cmp["mirror"] == mirror and cmp["hand_confidence"] == "clear" and cmp["symmetry"] == symmetry
    assert cmp["median"] < 6 and cmp["within"][10] > 0.75 and cmp["other_hand_median"] > 30
    assert np.median(cmp["angle"][junk]) > 30 and np.median(cmp["angle"][~junk]) < 5  # the junk stands out
    if symmetry == "C1":
        assert ps.rotation_angle(cmp["rotation"].T @ g) < 1 and np.allclose(cmp["shift"], w, atol=1e-3)
    # the poses brought into the consensus frame are the consensus poses
    rot_f, trans_f = ps.apply_frame(rot_a, trans_a, ps.frame_dict(cmp["rotation"], cmp["mirror"], cmp["shift"]))
    good = ~junk
    assert np.median(ps.compare_poses(rot_f[good], trans_f[good], rot_c[good], trans_c[good], symmetry)["angle"]) < 5
    if symmetry == "C1":
        assert np.median(np.abs(trans_f - trans_c)[good]) < 1e-3


def _chiral(d=24):
    zz, yy, xx = (np.indices((d, d, d)) - d // 2).astype(float) * (32 / d)
    blobs = [((6, 0, 0), 3.0, 1.0), ((-4, 7, 1), 2.2, 0.8), ((0, -3, 8), 1.8, 0.6), ((-2, -6, -5), 2.6, 0.9)]
    return sum(a * np.exp(-((xx - x) ** 2 + (yy - y) ** 2 + (zz - z) ** 2) / (2 * s * s))
               for (x, y, z), s, a in blobs).astype(np.float32)


def _project(v, rot, t=(0.0, 0.0)):
    """image(x) = Σ_z v(Rᵀ (x + t, z)): cryoDRGN's projection convention, in pixels about the centre."""
    from scipy import ndimage
    n = v.shape[0]
    g = (np.indices((n, n, n)) - n // 2).astype(float)
    xyz = np.stack([g[2] + t[0], g[1] + t[1], g[0]]).reshape(3, -1)
    src = rot.T @ xyz
    vals = ndimage.map_coordinates(v, np.stack([src[2], src[1], src[0]]) + n // 2, order=1).reshape(n, n, n)
    return vals.sum(axis=0)


@pytest.mark.parametrize("mirror", [False, True])
def test_frame_keeps_the_images(mirror):
    """A volume brought into another frame, with its poses changed the same way, gives the same projections."""
    vol = _chiral(28)
    frame = ps.frame_dict(ps.rotvec_to_matrix(np.array([[0.4, -0.9, 0.3]]))[0], mirror, np.array([0.05, -0.03, 0.04]))
    moved = ps.frame_volume(vol, frame)
    back = ps.frame_volume(moved, frame, inverse=True)
    r = np.sqrt(((np.indices(vol.shape) - 14) ** 2).sum(0))
    assert np.corrcoef(back[r < 9], vol[r < 9])[0, 1] > 0.995  # two linear interpolations
    rng = np.random.default_rng(2)
    for _ in range(3):
        rot = ps.rotvec_to_matrix(rng.normal(0, 1.2, (1, 3)))
        trans = rng.normal(0, 0.02, (1, 2))
        rot2, trans2 = ps.apply_frame(rot, trans, frame)
        a, b = _project(vol, rot[0], trans[0] * 28), _project(moved, rot2[0], trans2[0] * 28)
        assert np.corrcoef(a.ravel(), b.ravel())[0, 1] > 0.999
    flipped = ps.flipped_frame(frame)
    assert flipped["mirror"] != mirror
    assert np.corrcoef(ps.frame_volume(vol, flipped).ravel(), ps.frame_volume(moved, ps.flipped_frame(None)).ravel())[0, 1] > 0.98


@pytest.mark.parametrize("mirror", [False, True])
def test_align_maps_recovers_frame_and_hand(mirror):
    vol = _chiral(24)
    g = ps.rotvec_to_matrix(np.array([[-0.5, 0.8, 0.2]]))[0]
    frame = ps.frame_dict(g, mirror, np.array([0.03, 0.0, -0.02]))
    ref = ps.frame_volume(vol, frame) + np.random.default_rng(0).normal(0, 0.02, vol.shape).astype(np.float32)
    found = ps.align_maps(vol, 2.0, ref, 2.0, size=20, n_rotations=900, fine=24)
    assert found["mirror"] == mirror and found["hand_confidence"] in ("clear", "weak")
    assert ps.rotation_angle(found["rotation"].T @ g) < 4
    aligned = ps.frame_volume(vol, ps.frame_dict(found["rotation"], found["mirror"], found["shift"]))
    assert np.corrcoef(aligned.ravel(), ref.ravel())[0, 1] > 0.9


def test_particle_files_with_poses(tmp_path):
    rng = np.random.default_rng(3)
    rot = ps.uniform_rotations(6)
    trans = rng.normal(0, 0.02, (6, 2))
    data = np.zeros(6, dtype=[("uid", "<u8"), ("blob/shape", "<u4", (2,)), ("blob/psize_A", "<f4")])
    data["blob/shape"], data["blob/psize_A"] = 64, 1.5
    pose, shift = ps.cryodrgn_to_cryosparc(rot, trans, 64)
    out = pt.with_poses(data, pose, shift)
    assert set(out.dtype.names) >= {"alignments3D/pose", "alignments3D/shift", "alignments3D/psize_A", "alignments3D/split"}
    back, tback = ps.cryosparc_to_cryodrgn(out["alignments3D/pose"], out["alignments3D/shift"], 64)
    assert np.abs(back - rot).max() < 1e-5 and np.abs(tback - trans).max() < 1e-6 and pt.summary(out)["has_poses"]
    star = tmp_path / "p.star"
    star.write_text("data_optics\n\nloop_\n_rlnOpticsGroup #1\n_rlnImageSize #2\n_rlnImagePixelSize #3\n1 64 1.5\n\n"
                    "data_particles\n\nloop_\n_rlnImageName #1\n_rlnOpticsGroup #2\n"
                    + "".join(f"{i + 1:06d}@a.mrcs 1\n" for i in range(6)))
    written = pt.star_with_poses(star, tmp_path / "q.star", ps.matrix_to_relion(rot), trans * 64, apix=1.5)
    euler = np.array([[float(v) for v in pt.star_column(written, c)] for c in ("_rlnAngleRot", "_rlnAngleTilt", "_rlnAnglePsi")]).T
    origin = np.array([[float(v) for v in pt.star_column(written, c)] for c in ("_rlnOriginXAngst", "_rlnOriginYAngst")]).T
    assert np.abs(ps.relion_to_matrix(euler) - rot).max() < 1e-4 and np.abs(origin / 1.5 / 64 - trans).max() < 1e-6
    assert pt.star_column(written, "_rlnImageName")[2] == "000003@a.mrcs" and "data_optics" in written.read_text()


def test_abinit_monitor_phases_and_time_left():
    class Ctx:
        def __init__(self):
            self.messages, self.plots = [], {}

        def progress(self, fraction, message=None, log=True):
            self.messages.append((fraction, message))

        def add_plot(self, title, series, **kw):
            self.plots[kw.get("key")] = series

    line = "(INFO) (abinit.py) # =====> HPS Epoch: 2 finished in 1 day, 2:03:04.500000; total loss = 0.912345"
    row = abinit_row(ABINIT_EPOCH_RE.search(line))
    assert row == {"epoch": 2, "phase": "HPS", "seconds": 86400 + 7384.5, "loss": 0.912345}
    ctx = Ctx()
    mon = AbinitMonitor(ctx, n=1000, last_epoch=10, pose_epochs=3, pretrain=500)
    mon.on_line("# =====> Pretrain Epoch: 0 finished in 0:00:10.000000; total loss = 2.0")
    mon.on_line("# =====> HPS Epoch: 1 finished in 0:10:00.000000; total loss = 1.5")
    msg = ctx.messages[-1][1]
    # 2 pose search epochs of 10 min left, then 7 SGD epochs estimated at 1/15 of that speed
    assert msg.startswith("Pose search · epoch 1/10 done") and "10 min per epoch" in msg and "roughly 25 min left" in msg
    mon.on_line("# =====> HPS Epoch: 2 finished in 0:10:00.000000; total loss = 1.2")
    mon.on_line("# =====> HPS Epoch: 3 finished in 0:10:00.000000; total loss = 1.1")
    mon.on_line("# =====> SGD Epoch: 4 finished in 0:01:00.000000; total loss = 0.9")
    assert "about 6 min left" in ctx.messages[-1][1] and ctx.messages[-1][0] > ctx.messages[-3][0]
    names = [s["name"] for s in ctx.plots["losses"]]
    assert names == ["Pose search epochs (HPS)", "Gradient descent epochs (SGD)"] and ctx.plots["losses"][0]["x"] == [1, 2, 3]


# ------------------------------------------------------------------- the job
def test_abinit_job_with_consensus_poses(manager, abinit_mirror):
    puid, imp, job = abinit_mirror
    outs = _outs(job)
    log = log_of(manager, puid, job["uid"])
    cmd = next(line for line in log.splitlines() if " abinit " in line and "--epochs-pose-search 2" in line)
    assert "--zdim 4" in cmd and "-n 8" in cmd and "--checkpoint 2" in cmd and "--no-analysis" in cmd and "--uninvert-data" not in cmd
    # input check: backprojection with the consensus poses, and the average image (dark particles: inverted, the default)
    assert _section(manager, puid, job, "input_check")["kind"] == "metrics"
    contrast = _section(manager, puid, job, "contrast")["metrics"]
    assert contrast[0]["status"] == "good" and "darker" in contrast[0]["value"]
    # training report: losses per phase, pose stability, reconstructions across epochs
    assert [s["name"] for s in _section(manager, puid, job, "losses")["series"]] == ["Pose search epochs (HPS)",
                                                                                     "Gradient descent epochs (SGD)"]
    change = _section(manager, puid, job, "pose_change")["series"][0]
    assert change["x"] == [2, 4, 6, 8] and change["y"][0] > change["y"][-1]
    assert outs["reconstructions"]["type"] == "volume_series" and len(outs["reconstructions"]["files"]) == 5
    # comparison with the consensus: mirror found and corrected, junk particles stand out
    metrics = {m["label"]: m for m in _section(manager, puid, job, "comparison")["metrics"]}
    assert metrics["Hand of the cryoDRGN-AI model"]["value"].startswith("mirror image") and \
        metrics["Hand of the cryoDRGN-AI model"]["status"] == "good"
    assert _highlights(job)["Hand"]["value"] == "mirrored, corrected"
    # the learned poses, in the consensus frame and hand, written back into the particles
    learned = outs["particles"]
    assert learned["meta"]["poses_from"] == "cryoDRGN-AI" and learned["meta"]["has_poses"] and learned["path"].endswith(".cs")
    data = pt.read_cs(manager.project_dir(puid) / learned["path"])
    orig = pt.read_cs(manager.project_dir(puid) / _outs(imp)["particles"]["path"])
    rot_l, _ = ps.cryosparc_to_cryodrgn(data["alignments3D/pose"], data["alignments3D/shift"], 32)
    rot_o, _ = ps.cryosparc_to_cryodrgn(orig["alignments3D/pose"], orig["alignments3D/shift"], 32)
    angles = ps.pose_change(rot_l, rot_o)
    junk = np.arange(len(angles)) % 10 == 3
    assert np.median(angles[~junk]) < 8 and np.median(angles[junk]) > 30
    lat = outs["latent"]["meta"]
    assert lat["abinit"]["pose_epochs"] == 2 and lat["frame"]["mirror"] and lat["frame"]["source"] == "consensus poses"
    assert lat["pose_comparison"]["mirror"] and lat["particles"] == learned["path"]
    with open(manager.project_dir(puid) / lat["prepared"]["poses"], "rb") as fh:
        rot_p, trans_p = pickle.load(fh)
    assert np.all(np.abs(trans_p) <= 1) and len(rot_p) == 300  # cryoDRGN units: fractions of the box
    # validation: backprojection with the learned poses
    assert outs["validation"]["type"] == "map" and outs["validation_half_maps"]["type"] == "half_maps"
    assert "Learned poses" in _highlights(job)
    # latent space: same explorer, coloured by the pose difference, volumes in the consensus frame
    view = _view(manager, puid, job)
    assert {"pose_diff", "view_diff", "shift_diff", "tilt", "defocus"} <= set(view["colorings"])
    assert len(view["clusters"]) == 6 and outs["kmeans"]["type"] == "volume_series"
    advice = next(s for s in _sections(manager, puid, job) if s["title"] == "What to do next")["text"]
    assert "cryoDRGN-AI again" in advice or "Junk" in advice
    vol = MapVolume.read(manager.project_dir(puid) / outs["reconstruction"]["path"])
    assert vol.data.shape == (16, 16, 16) and abs(vol.pixel_size - 4.0) < 1e-3  # training pixel size (2 Å × 32 / 16)


def test_jobs_on_abinit_models(manager, abinit_mirror):
    puid, imp, job = abinit_mirror
    latent = {"latent": {"job": job["uid"], "output": "latent"}}
    # analysis: cryoDRGN writes into the training folder, so the model is linked into the analysis job
    ana = _run(manager, puid, "cryodrgn_analyze", {"ksample": 4, "n_per_pc": 4, "downsample": 8, "flip": True}, latent)
    ana_dir = manager.project_dir(puid) / ana["uid"]
    assert (ana_dir / "model" / "analyze.8" / "kmeans4").is_dir() and _outs(ana)["kmeans"]["files"][0].startswith(ana["uid"])
    log = log_of(manager, puid, ana["uid"])
    assert "-d 8" not in log and "--flip" not in log and "generated at the training size" in log
    assert _outs(ana)["latent"]["meta"]["frame"]["mirror"] is False  # the flip went into the frame
    # trajectory and volumes: eval_vol at the training size, then the frame of the consensus
    traj = _run(manager, puid, "cryodrgn_trajectory", {"clusters": "1, 4", "frames": 4}, latent)
    tlog = log_of(manager, puid, traj["uid"])
    assert "eval_vol" in tlog and " -d " not in tlog
    assert MapVolume.read(manager.project_dir(puid) / _outs(traj)["trajectory"]["files"][0]).data.shape == (16, 16, 16)
    vols = _run(manager, puid, "cryodrgn_volumes", {"particles": "5"}, latent)
    assert _outs(vols)["volume"]["type"] == "map"
    # continuation: cryodrgn abinit --load, then the same results
    cont = _run(manager, puid, "cryodrgn_continue", {"epochs": 12, "ksample": 4}, latent)
    clog = log_of(manager, puid, cont["uid"])
    assert "--load" in clog and "weights.8.pkl" in clog and "-n 12" in clog
    cmeta = _outs(cont)["latent"]["meta"]
    assert cmeta["epoch"] == 12 and len(cmeta["workdirs"]) == 2 and cmeta["frame"]["mirror"]
    assert _outs(cont)["particles"]["meta"]["poses_from"] == "cryoDRGN-AI"
    # convergence: epochs of both trainings (pretraining left out), with the poses
    conv = _run(manager, puid, "cryodrgn_convergence", {"particles": 2, "epochs": "4, 8, 12"},
                {"latent": {"job": cont["uid"], "output": "latent"}})
    verdict = {m["label"]: m for m in _section(manager, puid, conv, "verdict")["metrics"]}
    assert "Poses (cryoDRGN-AI)" in verdict and "epoch_000" not in str(_outs(conv))
    # landscape: own mask (cryoDRGN's would come from inverted volumes), volumes put back to positive density
    land = _run(manager, puid, "cryodrgn_landscape", {"sketch": 20, "states": 3, "box": 32}, latent)
    assert "--mask" in log_of(manager, puid, land["uid"])
    state = MapVolume.read(manager.project_dir(puid) / _outs(land)["states"]["files"][0])
    assert state.data.max() > -state.data.min() and abs(state.pixel_size - 4.0) < 1e-3


def test_selections_and_training_with_learned_poses(manager, abinit_mirror):
    puid, imp, job = abinit_mirror
    latent = {"latent": {"job": job["uid"], "output": "latent"}}
    sel = _run(manager, puid, "select_particles", {"clusters": "1-3"}, latent)
    smeta = _outs(sel)["particles"]["meta"]
    assert smeta["poses_from"] == "cryoDRGN-AI" and smeta["parent_cryodrgn"]["box"] == 16
    # a training on the selection reuses the images with the learned poses (fractions of the box)
    train = _run(manager, puid, "cryodrgn_train", {"box": 16, "zdim": 2, "epochs": 2, "ksample": 3, "check_inputs": "no check"},
                 {"particles": {"job": sel["uid"], "output": "particles"}})
    tlog = log_of(manager, puid, train["uid"])
    assert "Reusing the 16 px particles of the parent dataset" in tlog and "poses_learned.pkl" in tlog
    # at another size the poses are read from the particle file, where they were written as a refinement's
    again = _run(manager, puid, "cryodrgn_train", {"box": 32, "zdim": 2, "epochs": 2, "ksample": 3, "check_inputs": "no check",
                                                   "pose_source": "heterogeneous refinement"},
                 {"particles": {"job": job["uid"], "output": "particles"}})
    alog = log_of(manager, puid, again["uid"])
    assert "parse_pose_csparc" in alog and "--hetrefine" not in alog and "read as such" in alog
    # the consensus poses can be kept instead
    cons = _run(manager, puid, "select_particles", {"clusters": "1-3", "poses": "consensus (original particles)"}, latent)
    cmeta = _outs(cons)["particles"]["meta"]
    assert "poses_from" not in cmeta and cmeta["parent_cryodrgn"]["poses"].endswith("prepared/pose.pkl")
    ab2 = _run(manager, puid, "cryodrgn_abinit", {**ABINIT, "epochs": 4, "validate": False},
               {"particles": {"job": sel["uid"], "output": "particles"}})
    assert "--ind" in log_of(manager, puid, ab2["uid"]) and _outs(ab2)["latent"]["meta"]["n_particles"] == smeta["n_particles"]


def test_abinit_without_consensus_poses(manager, cs_particles, tmp_path, monkeypatch):
    """Particles of a 2D selection (no 3D alignment): no comparison, the contrast from the images, a reference map
    for the frame and the hand."""
    data = pt.merged_particles(cs_particles["refine"] / "J5_005_particles.cs", cs_particles["refine"] / "J5_passthrough_particles.cs")
    keep = [n for n in data.dtype.names if not n.startswith("alignments3D")]
    flat = np.zeros(len(data), dtype=[(n, data.dtype.fields[n][0]) for n in keep])
    for name in keep:
        flat[name] = data[name]
    job_dir = cs_particles["root"] / "J20"
    job_dir.mkdir(exist_ok=True)
    pt.write_cs(flat, job_dir / "J20_particles_selected.cs")
    ref = tmp_path / "reference.mrc"  # the molecule of the fake models, in the consensus frame
    z, y, x = (np.indices((16, 16, 16)) - 8) * 2.0
    blobs = [((6, 0, 0), 3.0, 1.0), ((-4, 7, 1), 2.2, 0.8), ((0, -3, 8), 1.8, 0.6), ((-2, -6, -5), 2.6, 0.9)]
    mol = sum(a * np.exp(-((x - bx) ** 2 + (y - by) ** 2 + (z - bz) ** 2) / (2 * s * s)) for (bx, by, bz), s, a in blobs)
    MapVolume(data=mol.astype(np.float32), voxel=np.array([4.0] * 3)).write(ref)
    p = manager.create_project("2D")
    puid = p["uid"]
    imp = _run(manager, puid, "import_particles", {"path": str(job_dir)})
    assert not _outs(imp)["particles"]["meta"]["has_poses"]
    refimp = _run(manager, puid, "import_maps", {"map": str(ref), "compute_fsc": False})
    monkeypatch.setenv("FAKE_CRYODRGN_INVERTED", "1")  # images already inverted: bright particles
    monkeypatch.setenv("FAKE_CRYODRGN_MIRROR", "1")
    job = _run(manager, puid, "cryodrgn_abinit", {**ABINIT, "epochs": 5},
               {"particles": {"job": imp["uid"], "output": "particles"},
                "reference": {"job": refimp["uid"], "output": "map"}})
    log = log_of(manager, puid, job["uid"])
    assert "--uninvert-data" in log and "parse_pose_csparc" not in log and "brighter than the solvent" in log
    secs = {s.get("key") for s in _sections(manager, puid, job)}
    assert "comparison" not in secs and "map_alignment" in secs and "input_check" not in secs
    align = {m["label"]: m for m in _section(manager, puid, job, "map_alignment")["metrics"]}
    assert align["Hand of the cryoDRGN-AI model"]["value"].startswith("mirror image")
    lat = _outs(job)["latent"]["meta"]
    assert lat["frame"]["source"] == "reference map" and lat["frame"]["mirror"] and "consensus_particles" not in lat
    assert "pose_diff" not in _view(manager, puid, job)["colorings"]
    learned = pt.read_cs(manager.project_dir(puid) / _outs(job)["particles"]["path"])
    assert "alignments3D/pose" in learned.dtype.names and _outs(job)["particles"]["meta"]["has_poses"]
    # these particles can now be trained with train_vae
    sel = _run(manager, puid, "cryodrgn_train", {"box": 16, "zdim": 2, "epochs": 2, "ksample": 3, "check_inputs": "no check"},
               {"particles": {"job": job["uid"], "output": "particles"}})
    assert sel["status"] == "completed"


def test_homogeneous_abinit_and_versions(manager, drgn_project, monkeypatch):
    puid, imp = drgn_project
    particles = {"particles": {"job": imp["uid"], "output": "particles"}}
    homo = _run(manager, puid, "cryodrgn_abinit", {**ABINIT, "zdim": 0, "epochs": 4, "validate": False}, particles)
    outs = _outs(homo)
    assert "latent" not in outs and outs["reconstruction"]["type"] == "map" and outs["particles"]["meta"]["has_poses"]
    monkeypatch.setenv("FAKE_CRYODRGN_VERSION", "4.1.0")
    old = _run(manager, puid, "cryodrgn_abinit", ABINIT, particles, ok=False)
    assert old["status"] == "failed" and "needs cryoDRGN 4.2" in old["error"]
    monkeypatch.setenv("FAKE_CRYODRGN_VERSION", "4.2.1")
    v42 = _run(manager, puid, "cryodrgn_abinit", {**ABINIT, "epochs": 4, "validate": False}, particles)
    assert "eval_vol cannot read them" in log_of(manager, puid, v42["uid"])
    traj = _run(manager, puid, "cryodrgn_trajectory", {"clusters": "1, 3", "frames": 3},
                {"latent": {"job": v42["uid"], "output": "latent"}}, ok=False)
    assert traj["status"] == "failed" and "eval_vol reads these models from 4.3" in traj["error"]
    conv = _run(manager, puid, "cryodrgn_convergence", {"particles": 2}, {"latent": {"job": v42["uid"], "output": "latent"}})
    assert "the volumes are not compared" in log_of(manager, puid, conv["uid"])


def test_cryodrgn_ai_workflow(manager, cs_particles):
    p = manager.create_project("Workflow AI")
    res = manager.instantiate_workflow(p["uid"], "heterogeneity_cryodrgn_ai",
                                       overrides={"import": {"job_dir": str(cs_particles["refine"])}}, queue=False)
    by_type = {j["type"]: j for j in res["jobs"]}
    assert set(by_type) == {"import_cryosparc", "cryodrgn_abinit", "cryodrgn_convergence", "series_analysis"}
    ab = by_type["cryodrgn_abinit"]
    assert ab["params"]["box"] == 128 and ab["params"]["epochs"] == 30 and set(ab["inputs"]) == {"particles"}
    assert by_type["series_analysis"]["inputs"] == {"series": {"job": ab["uid"], "output": "kmeans"}}
    res = manager.instantiate_workflow(p["uid"], "heterogeneity_cryodrgn_ai", include=["reference"],
                                       overrides={"import": {"job_dir": str(cs_particles["refine"])},
                                                  "reference": {"map": "/tmp/ref.mrc"}}, queue=False)
    ab = next(j for j in res["jobs"] if j["type"] == "cryodrgn_abinit")
    assert set(ab["inputs"]) == {"particles", "reference"}
