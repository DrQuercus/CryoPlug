"""Fake cryoDRGN 4.3 command line for the tests: same sub-commands, arguments and output files, with tiny
synthetic data (latent space with three groups that settles over the epochs, volumes in which one blob moves
with the latent value). FAKE_CRYODRGN_VERSION sets the version it reports (e.g. 3.3.0, with checkpoints
numbered from 0 as in versions before 3.5).

cryodrgn abinit (cryoDRGN-AI, from 4.2) learns poses that are the consensus poses (read next to the CTF file, when
the particles have some) seen from another frame: a hidden rotation, a shift and, with FAKE_CRYODRGN_MIRROR, the
other hand; a tenth of the particles (junk) get random poses. As the real program, it saves its shifts in pixels,
and analyze / eval_vol / analyze_landscape treat its models as cryoDRGN 4.3 does (analysis written in the training
folder, no downsampling, eval_vol from 4.3 only, landscape volumes inverted with a pixel size of 1 Å)."""
import argparse
import os
import pickle
import sys

import numpy as np

sys.path.insert(0, os.environ.get("CRYOPLUG_ROOT", ""))
from cryoplug.mrc import MapVolume  # noqa: E402

BOX = 32
VERSION = os.environ.get("FAKE_CRYODRGN_VERSION", "4.3.1")
V = tuple(int(x) for x in VERSION.split(".")[:2])
ONE_BASED = V >= (3, 5)
HIDDEN = {"rotation": [[0.36, -0.48, 0.8], [0.8, 0.6, 0.0], [-0.48, 0.64, 0.6]], "mirror": False,
          "shift": [0.02, -0.03, 0.01]}  # frame of the fake cryoDRGN-AI models (a rotation of ~67°)


def volume(path, shift, apix=4.0, extra=1.0, box=BOX, noise=0.0, seed=0):
    z, y, x = np.indices((box, box, box)) * (BOX / box)
    c = BOX / 2
    a = np.exp(-((x - c + 5) ** 2 + (y - c) ** 2 + (z - c) ** 2) / 12.0)
    b = extra * np.exp(-((x - c - 4 - shift) ** 2 + (y - c) ** 2 + (z - c) ** 2) / 10.0)
    data = a + b
    if noise:
        data = data + np.random.default_rng(seed).normal(0, noise, data.shape)
    MapVolume(data=data.astype(np.float32), voxel=np.array([apix] * 3)).write(path)


def n_images(stack, ind=None):
    if ind:
        with open(ind, "rb") as fh:
            return len(pickle.load(fh))
    if stack.endswith(".cs"):
        return len(np.load(stack))
    if stack.endswith(".txt"):
        folder = os.path.dirname(stack)
        return sum(MapVolume.read(os.path.join(folder, line.strip()), header_only=True).shape_xyz[2]
                   for line in open(stack) if line.strip())
    return MapVolume.read(stack, header_only=True).shape_xyz[2]


def image_box(stack):
    if stack.endswith(".mrcs"):
        return MapVolume.read(stack, header_only=True).shape_xyz[0]
    if stack.endswith(".cs"):
        return int(np.load(stack)["blob/shape"][0][0])
    return BOX


def expmap(v):
    """Rotation matrices from axis-angle vectors (Rodrigues), as cryoDRGN's lie_tools.expmap."""
    theta = np.linalg.norm(v, axis=1, keepdims=True)
    k = v / np.maximum(theta, 1e-12)
    kx = np.zeros((len(v), 3, 3))
    kx[:, 0, 1], kx[:, 0, 2], kx[:, 1, 2] = -k[:, 2], k[:, 1], -k[:, 0]
    kx = kx - kx.transpose(0, 2, 1)
    t = theta[:, :, None]
    return np.eye(3)[None] + np.sin(t) * kx + (1 - np.cos(t)) * kx @ kx


def load_ctf(path):
    with open(path, "rb") as fh:
        return pickle.load(fh)


def check_pose_file(path):
    """As cryoDRGN's PoseTracker.load: translations must be fractions of the box."""
    with open(path, "rb") as fh:
        obj = pickle.load(fh)
    if isinstance(obj, tuple) and len(obj) == 2 and not np.all(np.asarray(obj[1]) <= 1):
        print("ValueError: Old pose format detected; translations must be in units of fraction of box!", flush=True)
        sys.exit(1)


def is_abinit_model(workdir):
    path = os.path.join(workdir, "config.yaml")
    return os.path.exists(path) and "data_norm_mean" in open(path).read()


def model_box(workdir):
    for line in open(os.path.join(workdir, "config.yaml")):
        if line.strip().startswith("D:"):
            return int(line.split(":")[1]) - 1
    return BOX


def molecule(box):
    """A chiral test object (four blobs of different sizes), the 'consensus' volume of the fake models."""
    z, y, x = (np.indices((box, box, box)) - box // 2) * (BOX / box)
    blobs = [((6, 0, 0), 3.0, 1.0), ((-4, 7, 1), 2.2, 0.8), ((0, -3, 8), 1.8, 0.6), ((-2, -6, -5), 2.6, 0.9)]
    return sum(a * np.exp(-((x - bx) ** 2 + (y - by) ** 2 + (z - bz) ** 2) / (2 * sd * sd)) for (bx, by, bz), sd, a in blobs)


def latent(n, zdim, epoch, total):
    """Three groups; each particle drifts less and less as the epochs go by (convergence)."""
    rng = np.random.default_rng(1)
    groups = rng.integers(0, 3, n)
    z = rng.normal(0, 0.3, (n, zdim)) + groups[:, None] * 2.0
    drift = np.random.default_rng(100 + epoch).normal(0, 1.0, (n, zdim)) * 0.6 / epoch
    return (z + drift).astype(np.float32)


def train(rest):
    p = argparse.ArgumentParser()
    p.add_argument("particles")
    for opt in ("--poses", "--ctf", "-o", "--ind", "--datadir", "--enc-dim", "--enc-layers", "--dec-dim", "--dec-layers",
                "-b", "--beta", "--seed", "--load"):
        p.add_argument(opt)
    p.add_argument("--domain", default="fourier", choices=["fourier", "hartley"])
    p.add_argument("--zdim", type=int)
    p.add_argument("-n", type=int)
    p.add_argument("--checkpoint", type=int, default=1)
    p.add_argument("--log-interval", type=int, default=1000)
    for flag in ("--no-analysis", "--multigpu", "--lazy", "--uninvert-data", "--no-amp", "--amp", "--do-pose-sgd"):
        p.add_argument(flag, action="store_true")
    a = p.parse_args(rest)
    if a.particles.endswith(".cs") and not a.datadir:
        sys.exit("Cannot find the images: give --datadir")
    check_pose_file(a.poses)
    if a.do_pose_sgd and a.domain != "hartley":  # as train_vae.py of cryoDRGN 4.3.1
        print('Traceback (most recent call last):\n    assert args.domain == "hartley", "Need to use --domain hartley if doing pose SGD"\nAssertionError: Need to use --domain hartley if doing pose SGD', flush=True)
        sys.exit(1)
    if a.no_analysis and not ONE_BASED:
        sys.exit("train_vae: error: unrecognized arguments: --no-analysis")
    n = n_images(a.particles, a.ind)
    os.makedirs(a.o, exist_ok=True)
    start = 1
    if a.load:
        with open(a.load, "rb") as fh:
            start = int(pickle.load(fh)[0]) + 1
        if start > a.n:
            sys.exit(f"ValueError: If starting from a saved checkpoint at epoch {start - 1}, the number of epochs to "
                     f"train must be greater than {a.n}!")
    first, last = (start, a.n) if ONE_BASED else (start - 1, a.n - 1)
    with open(f"{a.o}/run.log", "a") as log:
        for e in range(first, last + 1):
            for done in (n // 2, n):
                line = (f"# [Train Epoch: {e}/{a.n}] [{done}/{n} particles] gen loss=0.9, kld=2.0, beta=0.125, loss=1.0")
                print(line, flush=True)
            gen = 0.8 + 0.2 / (e + 1)
            line = (f"# =====> Epoch: {e} Average gen loss = {gen:.6}, KLD = {2 + 1 / (e + 1):.6f}, total loss = "
                    f"{gen + 0.25:.6f}; Finished in 0:00:01.500000")
            print(line, flush=True)
            log.write(line + "\n")
            z = latent(n, a.zdim, e + (0 if ONE_BASED else 1), a.n)
            if e % a.checkpoint == 0 or e == last:
                with open(f"{a.o}/weights.{e}.pkl", "wb") as fh:
                    pickle.dump(np.array([e]), fh)
                with open(f"{a.o}/z.{e}.pkl", "wb") as fh:
                    pickle.dump(z, fh)
                    pickle.dump(np.zeros_like(z), fh)
                if a.do_pose_sgd:
                    with open(a.poses, "rb") as src, open(f"{a.o}/pose.{e}.pkl", "wb") as fh:
                        fh.write(src.read())
    for name in ("weights.pkl", "z.pkl"):
        with open(f"{a.o}/{name}", "wb") as fh:
            pickle.dump(z if name == "z.pkl" else np.array([last]), fh)
    with open(f"{a.o}/config.yaml", "w") as fh:
        fh.write(f"dataset_args:\n  particles: {a.particles}\n  ctf: {a.ctf}\nlattice_args:\n  D: 33\n")


def analyze(rest):
    p = argparse.ArgumentParser()
    p.add_argument("workdir")
    p.add_argument("epoch", type=int)
    p.add_argument("-o")
    p.add_argument("--ksample", type=int, default=20)
    p.add_argument("--pc", type=int, default=2)
    if tuple(int(x) for x in VERSION.split(".")[:2]) >= (4, 2):
        p.add_argument("--n-per-pc", type=int, default=10)
    p.add_argument("--Apix", type=float, default=1.0)
    p.add_argument("-d", type=int)
    p.add_argument("--low-pass")
    for flag in ("--flip", "--invert"):
        p.add_argument(flag, action="store_true")
    a = p.parse_args(rest)
    n_per_pc = getattr(a, "n_per_pc", 10)
    abinit = is_abinit_model(a.workdir)
    if abinit:  # cryoDRGN-AI: results in the training folder, -o, -d, --flip and --low-pass ignored (ModelAnalyzer)
        if "zdim: 0" in open(f"{a.workdir}/config.yaml").read():
            print("No analyses available for homogeneous reconstruction!", flush=True)
            return
        a.o, a.d = None, model_box(a.workdir)
    with open(f"{a.workdir}/z.{a.epoch}.pkl", "rb") as fh:
        z = pickle.load(fh)
    k = a.ksample
    out = a.o or f"{a.workdir}/analyze.{a.epoch}"
    kdir = f"{out}/kmeans{k}"
    os.makedirs(kdir, exist_ok=True)
    order = np.argsort(z[:, 0])
    labels = np.empty(len(z), dtype=np.int64)
    for i, chunk in enumerate(np.array_split(order, k)):
        labels[chunk] = i
    centers_ind = [int(chunk[len(chunk) // 2]) for chunk in np.array_split(order, k)]
    with open(f"{kdir}/labels.pkl", "wb") as fh:
        pickle.dump(labels, fh)
    np.savetxt(f"{kdir}/centers.txt", z[centers_ind])
    np.savetxt(f"{kdir}/centers_ind.txt", centers_ind, fmt="%d")
    box = a.d or BOX
    for i, ci in enumerate(centers_ind):  # cluster 1 is junk-like: weak density
        volume(f"{kdir}/vol_{i + 1:03d}.mrc", float(z[ci, 0]), a.Apix, extra=0.2 if i == 0 else 1.0, box=box)
    for pc in range(1, a.pc + 1):
        os.makedirs(f"{out}/pc{pc}", exist_ok=True)
        for j in range(n_per_pc):
            volume(f"{out}/pc{pc}/vol_{j + 1:03d}.mrc", (j - n_per_pc / 2) / 2, a.Apix, box=box)
    with open(f"{out}/umap.pkl", "wb") as fh:
        pickle.dump(z[:, :2] * np.array([1.0, -1.0], np.float32), fh)
    png = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                        "1f15c4890000000d49444154789c63000100000500010d0a2db40000000049454e44ae426082")
    for name in ("umap_hexbin.png", "z_pca_hexbin.png", f"kmeans{k}/umap.png"):
        with open(f"{out}/{name}", "wb") as fh:
            fh.write(png)


def backproject(rest):
    p = argparse.ArgumentParser()
    p.add_argument("particles")
    for opt in ("--poses", "--ctf", "-o", "--ind", "--datadir"):
        p.add_argument(opt)
    p.add_argument("--first", type=int)
    for flag in ("--uninvert-data", "--lazy"):
        p.add_argument(flag, action="store_true")
    a = p.parse_args(rest)
    if os.environ.get("FAKE_CRYODRGN_BACKPROJECT_FAIL"):
        sys.exit("RuntimeError: cannot read the images")
    check_pose_file(a.poses)
    ctf = load_ctf(a.ctf)
    box = image_box(a.particles)
    apix = float(ctf[0, 1]) * float(ctf[0, 0]) / box
    os.makedirs(a.o, exist_ok=True)
    sign = -1.0 if (a.uninvert_data != bool(os.environ.get("FAKE_CRYODRGN_INVERTED"))) else 1.0
    zz, yy, xx = np.indices((box, box, box))
    blob = np.exp(-((xx - box / 2) ** 2 + (yy - box / 2) ** 2 + (zz - box / 2) ** 2) / (2 * (box / 8) ** 2))
    noise = 0.02 if not os.environ.get("FAKE_CRYODRGN_BAD_POSES") else 5.0
    for name, seed in (("backproject.mrc", 0), ("half_map_a.mrc", 1), ("half_map_b.mrc", 2)):
        data = sign * blob + np.random.default_rng(seed).normal(0, noise * (1 if seed else 0.5), blob.shape)
        MapVolume(data=data.astype(np.float32), voxel=np.array([apix] * 3)).write(f"{a.o}/{name}")
    print(f"Backprojected {min(a.first or 10**9, n_images(a.particles, a.ind)):,d} images in 1.00s", flush=True)


def landscape(rest):
    p = argparse.ArgumentParser()
    p.add_argument("workdir")
    p.add_argument("epoch", type=int)
    p.add_argument("-o")
    p.add_argument("-N", type=int, default=1000)
    p.add_argument("-d", type=int, default=128)
    p.add_argument("--Apix", type=float, default=1.0)
    p.add_argument("-M", type=int, default=10)
    p.add_argument("--linkage", default="average")
    p.add_argument("--pc-dim", type=int, default=20)
    p.add_argument("--mask")
    for flag in ("--skip-umap", "--flip", "--multigpu"):
        p.add_argument(flag, action="store_true")
    a = p.parse_args(rest)
    assert a.skip_umap and os.path.exists(f"{a.o}/umap.pkl"), "UMAP file not found"
    config = open(os.path.join(a.workdir, "config.yaml")).read() if is_abinit_model(a.workdir) else ""
    inverted = bool(config) and any(f"invert_data: {v}" in config for v in ("True", "true"))
    if inverted:  # cryoDRGN 4.3 inverts these volumes (invert_data), then zeroes negative density: the molecule
        global volume
        plain = volume

        def volume(path, shift, apix=4.0, extra=1.0, box=BOX, noise=0.0, seed=0):
            plain(path, shift, 1.0, extra, box, noise, seed)
            v = MapVolume.read(path)
            MapVolume(data=-v.data, voxel=v.voxel).write(path)
    with open(f"{a.workdir}/z.{a.epoch}.pkl", "rb") as fh:
        z = pickle.load(fh)
    with open(f"{a.workdir}/weights.{a.epoch}.pkl", "rb"):
        pass
    k, m = a.N, a.M
    kdir = f"{a.o}/kmeans{k}"
    os.makedirs(kdir, exist_ok=True)
    order = np.argsort(z[:, 0])
    labels = np.empty(len(z), dtype=np.int64)
    chunks = np.array_split(order, k)
    for i, chunk in enumerate(chunks):
        labels[chunk] = i
    with open(f"{kdir}/labels.pkl", "wb") as fh:
        pickle.dump(labels + 1, fh)
    np.savetxt(f"{kdir}/centers_ind.txt", [int(c[len(c) // 2]) for c in chunks], fmt="%d")
    box = min(a.d, BOX)
    for i, chunk in enumerate(chunks):
        volume(f"{kdir}/vol_{i + 1:03d}.mrc", float(z[chunk[len(chunk) // 2], 0]), a.Apix, box=box)
    MapVolume(data=np.ones((box, box, box), np.float32), voxel=np.array([a.Apix] * 3)).write(f"{a.o}/mask.mrc")
    with open(f"{a.o}/mask_slices.png", "wb") as fh:
        fh.write(bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                               "1f15c4890000000d49444154789c63000100000500010d0a2db40000000049454e44ae426082"))
    pcs = np.stack([np.linspace(-1, 1, k), np.sin(np.linspace(0, 3, k))], 1)
    with open(f"{a.o}/vol_pca_{k}.pkl", "wb") as fh:
        pickle.dump(np.c_[pcs, np.zeros((k, min(a.pc_dim, k - 1) - 2))], fh)
    for i in range(1, 6):
        os.makedirs(f"{a.o}/vol_pcs/pc{i}", exist_ok=True)
        for j in range(1, 11):
            volume(f"{a.o}/vol_pcs/pc{i}/{j:03d}.mrc", (j - 5) / 2, a.Apix, box=box)
    sdir = f"{a.o}/sketch_clustering_{a.linkage}_{m}"
    os.makedirs(sdir, exist_ok=True)
    states = np.minimum(np.arange(k) * m // k, m - 1)
    if inverted:  # clustered on noise: the states no longer follow the structure
        states = np.random.default_rng(0).permutation(states)
    with open(f"{sdir}/state_labels.pkl", "wb") as fh:
        pickle.dump(states + 1, fh)
    for s in range(m):
        volume(f"{sdir}/state_{s + 1:03d}_mean.mrc", s - m / 2, a.Apix, box=box)
        volume(f"{sdir}/state_{s + 1:03d}_std.mrc", 0, a.Apix, box=box)


def abinit(rest):
    from cryoplug import poses as ps
    p = argparse.ArgumentParser()
    p.add_argument("particles")
    for opt in ("-o", "--ctf", "--ind", "--datadir", "--load", "--load-poses", "--seed"):
        p.add_argument(opt)
    for opt, default in (("--zdim", None), ("-n", 30), ("--epochs-pose-search", None), ("--n-imgs-pretrain", 10000),
                         ("--layers", 3), ("--dim", 256), ("--batch-size-hps", 16), ("--batch-size-sgd", 128),
                         ("--checkpoint", 5), ("--log-interval", 10000)):
        p.add_argument(opt, type=int, default=default)
    p.add_argument("--t-extent", type=float, default=20.0)
    for flag in ("--uninvert-data", "--no-amp", "--use-conf-encoder", "--lazy", "--multigpu", "--no-analysis"):
        p.add_argument(flag, action="store_true")
    a = p.parse_args(rest)
    if a.particles.endswith(".cs") and not a.datadir:
        sys.exit("Cannot find the images: give --datadir")
    n = n_images(a.particles, a.ind)
    box = image_box(a.particles)
    hps = a.epochs_pose_search or 3
    os.makedirs(a.o, exist_ok=True)
    rng = np.random.default_rng(5)
    mirror = bool(os.environ.get("FAKE_CRYODRGN_MIRROR"))
    if a.load:  # a continued model keeps its frame and hand
        mirror = "fake_mirror: True" in open(os.path.join(os.path.dirname(a.load), "config.yaml")).read()
    frame = dict(HIDDEN, mirror=mirror)
    cons = os.path.join(os.path.dirname(a.ctf), "pose.pkl")  # consensus poses prepared next to the CTF, if any
    if os.path.exists(cons):
        with open(cons, "rb") as fh:
            rot_c, trans_c = pickle.load(fh)
        if a.ind:
            with open(a.ind, "rb") as fh:
                ind = pickle.load(fh)
            rot_c, trans_c = rot_c[ind], trans_c[ind]
    else:
        rot_c, trans_c = ps.uniform_rotations(n + 5)[rng.permutation(n + 5)[:n]], rng.normal(0, 0.01, (n, 2))
    g, w = np.array(frame["rotation"]), np.array(frame["shift"])
    rot_a = (ps.MIRROR @ rot_c @ g @ ps.MIRROR) if frame["mirror"] else rot_c @ g  # learned = consensus in another frame
    aligned = (ps.MIRROR @ rot_a @ ps.MIRROR if frame["mirror"] else rot_a) @ g.T
    trans_a = trans_c + np.einsum("nij,j->ni", aligned[:, :2, :], w)
    junk = np.arange(n) % 10 == 3
    start = 0
    if a.load:
        with open(a.load, "rb") as fh:
            start = int(pickle.load(fh)[0]) + 1
    # the hidden object as the model sees it: the consensus volume taken back to the frame of the model
    ref = molecule(box)
    model = ps.frame_volume(ref.astype(np.float32), frame, inverse=True)
    with open(f"{a.o}/config.yaml", "w") as fh:
        fh.write(f"dataset_args:\n  particles: {a.particles}\n  ctf: {a.ctf}\n  invert_data: {not a.uninvert_data}\n"
                 f"lattice_args:\n  D: {box + 1}\nmodel_args:\n  zdim: {a.zdim}\ndata_norm_mean: 0.0\ndata_norm_std: 1.0\n"
                 f"fake_mirror: {mirror}\n")
    with open(f"{a.o}/run.log", "a") as log:
        for e in range(start, a.n + 1):
            phase = "Pretrain" if e == 0 else ("HPS" if e <= hps else "SGD")
            images = min(a.n_imgs_pretrain, n) if e == 0 else n
            for done in (images // 2, images):
                print(f"# [Train Epoch: {e}/{a.n}] [{done}/{n} particles]", flush=True)
            loss = 0.9 + 0.5 / (e + 1) - (0.05 if phase == "SGD" else 0.0)
            seconds = {"Pretrain": "0:00:00.800000", "HPS": "0:00:04.000000", "SGD": "0:00:00.250000"}[phase]
            line = f"# =====> {phase} Epoch: {e} finished in {seconds}; total loss = {loss:.6f}"
            print(line, flush=True)
            log.write(line + "\n")
            if not (e == 0 or e <= hps or e % a.checkpoint == 0 or e == a.n):
                continue
            noise = np.radians(40.0 / (e + 1) if e else 120.0)  # poses settle over the epochs
            rot = rot_a @ ps.rotvec_to_matrix(np.random.default_rng(e).normal(0, noise / 2, (n, 3)))
            rot[junk] = ps.uniform_rotations(int(junk.sum()) + 3)[: int(junk.sum())]
            with open(f"{a.o}/pose.{e}.pkl", "wb") as fh:
                pickle.dump((rot.astype(np.float32), (trans_a * box).astype(np.float32)), fh)  # shifts in pixels
            if a.zdim:
                with open(f"{a.o}/z.{e}.pkl", "wb") as fh:
                    pickle.dump(latent(n, a.zdim, max(e, 1), a.n), fh)
            MapVolume(data=(model * min(1.0, 0.3 + e / a.n)).astype(np.float32),
                      voxel=np.array([4.0 * BOX / box] * 3)).write(f"{a.o}/reconstruct.{e}.mrc")
            with open(f"{a.o}/weights.{e}.pkl", "wb") as fh:
                pickle.dump(np.array([e]), fh)
    print(f"Finished in 0:00:10 ({a.n} epochs)", flush=True)


def main():
    cmd, rest = sys.argv[1], sys.argv[2:]
    print(f"fake cryodrgn {cmd} {' '.join(rest)}", flush=True)
    p = argparse.ArgumentParser()
    if cmd == "--version":
        print(f"cryoDRGN {VERSION}")
    elif cmd in ("parse_pose_csparc", "parse_ctf_csparc"):
        p.add_argument("cs")
        p.add_argument("-o")
        p.add_argument("-D")
        p.add_argument("--hetrefine", action="store_true")
        p.add_argument("--abinit", action="store_true")
        a = p.parse_args(rest)
        data = np.load(a.cs)
        key = "alignments3D/pose" if cmd == "parse_pose_csparc" else "ctf/df1_A"
        if key not in data.dtype.names:
            sys.exit(f"ValueError: no field of name {key}")
        if cmd == "parse_pose_csparc":
            rot = expmap(data["alignments3D/pose"].astype(np.float64)).transpose(0, 2, 1)
            obj = (rot.astype(np.float32), data["alignments3D/shift"] / float(data["blob/shape"][0][0]))
        else:
            obj = np.zeros((len(data), 9), np.float32)
            obj[:, 0] = data["blob/shape"][:, 0]
            obj[:, 1] = data["blob/psize_A"]
            for i, f in enumerate(("ctf/df1_A", "ctf/df2_A", "ctf/df_angle_rad", "ctf/accel_kv", "ctf/cs_mm",
                                   "ctf/amp_contrast", "ctf/phase_shift_rad")):
                obj[:, i + 2] = data[f] * (180 / np.pi if f.endswith("_rad") else 1)
        with open(a.o, "wb") as fh:
            pickle.dump(obj, fh)
    elif cmd == "downsample":
        p.add_argument("input")
        p.add_argument("-D", type=int)
        p.add_argument("-o")
        p.add_argument("--datadir")
        p.add_argument("--chunk", type=int)
        a = p.parse_args(rest)
        data = np.load(a.input)
        for path in set(data["blob/path"].astype(str)):
            path = path.lstrip(">")
            full = os.path.join(a.datadir or os.path.dirname(a.input), path)
            if not os.path.exists(full):
                sys.exit(f"Cannot find file `{path}` under datadir={a.datadir}")
        yy, xx = np.indices((a.D, a.D)) - a.D / 2
        blob = np.exp(-(xx ** 2 + yy ** 2) / (2 * (a.D / 6) ** 2))
        sign = 1.0 if os.environ.get("FAKE_CRYODRGN_INVERTED") else -1.0  # raw particles are darker than the solvent
        imgs = np.random.default_rng(0).normal(0, 1, (len(data), a.D, a.D)) + sign * 0.8 * blob
        MapVolume(data=imgs.astype(np.float32), voxel=np.ones(3)).write(a.o)
    elif cmd == "train_vae":
        train(rest)
    elif cmd == "analyze":
        analyze(rest)
    elif cmd == "backproject_voxel":
        backproject(rest)
    elif cmd == "analyze_landscape":
        landscape(rest)
    elif cmd == "abinit":
        if V < (4, 2):
            sys.exit("cryodrgn: error: argument command: invalid choice: 'abinit'")
        abinit(rest)
    elif cmd == "graph_traversal":
        p.add_argument("zfile")
        p.add_argument("--anchors", nargs="+", type=int)
        p.add_argument("-o")
        p.add_argument("--outind")
        a = p.parse_args(rest)
        with open(a.zfile, "rb") as fh:
            z = pickle.load(fh)
        path = []
        for s, e in zip(a.anchors[:-1], a.anchors[1:], strict=True):
            for t in np.linspace(0, 1, 15):
                q = z[s] * (1 - t) + z[e] * t
                i = int(np.argmin(((z - q) ** 2).sum(1)))
                if not path or path[-1] != i:
                    path.append(i)
        np.savetxt(a.o, z[path])
        np.savetxt(a.outind, path, fmt="%d")
    elif cmd == "eval_vol":
        p.add_argument("weights")
        p.add_argument("--config")
        p.add_argument("--zfile")
        p.add_argument("-o")
        p.add_argument("--Apix", type=float, default=1.0)
        p.add_argument("-d", type=int)
        p.add_argument("--flip", action="store_true")
        a = p.parse_args(rest)
        with open(a.weights, "rb") as fh:
            epoch = int(pickle.load(fh)[0])
        box = a.d or BOX
        if "data_norm_mean" in open(a.config).read():  # a cryoDRGN-AI checkpoint
            if V < (4, 3):
                sys.exit("KeyError: \"config must include dataset_args['norm']\"")
            if a.d:
                sys.exit("ValueError: cryodrgn eval_vol does not support --downsample for abinit (DrgnAI / HyperVolume) checkpoints")
            box = model_box(os.path.dirname(a.config))
        zs = np.atleast_2d(np.loadtxt(a.zfile))
        os.makedirs(a.o, exist_ok=True)
        for i, q in enumerate(zs, start=1):  # the decoder sharpens over the epochs
            volume(f"{a.o}/vol_{i:03d}.mrc", float(q[0]), a.Apix, box=box, noise=0.3 / max(epoch, 1), seed=i)
    else:
        sys.exit(f"fake cryodrgn: unknown command {cmd}")


main()
