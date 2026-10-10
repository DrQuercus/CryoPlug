"""Fake cryoDRGN 4.3 command line for the tests: same sub-commands, arguments and output files, with tiny
synthetic data (latent space with three groups that settles over the epochs, volumes in which one blob moves
with the latent value). FAKE_CRYODRGN_VERSION sets the version it reports (e.g. 3.3.0, with checkpoints
numbered from 0 as in versions before 3.5)."""
import argparse
import os
import pickle
import sys

import numpy as np

sys.path.insert(0, os.environ.get("CRYOPLUG_ROOT", ""))
from cryoplug.mrc import MapVolume  # noqa: E402

BOX = 32
VERSION = os.environ.get("FAKE_CRYODRGN_VERSION", "4.3.1")
ONE_BASED = tuple(int(x) for x in VERSION.split(".")[:2]) >= (3, 5)


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
    with open(f"{sdir}/state_labels.pkl", "wb") as fh:
        pickle.dump(states + 1, fh)
    for s in range(m):
        volume(f"{sdir}/state_{s + 1:03d}_mean.mrc", s - m / 2, a.Apix, box=box)
        volume(f"{sdir}/state_{s + 1:03d}_std.mrc", 0, a.Apix, box=box)


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
        imgs = np.random.default_rng(0).normal(0, 1, (len(data), a.D, a.D)).astype(np.float32)
        MapVolume(data=imgs, voxel=np.ones(3)).write(a.o)
    elif cmd == "train_vae":
        train(rest)
    elif cmd == "analyze":
        analyze(rest)
    elif cmd == "backproject_voxel":
        backproject(rest)
    elif cmd == "analyze_landscape":
        landscape(rest)
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
        zs = np.atleast_2d(np.loadtxt(a.zfile))
        os.makedirs(a.o, exist_ok=True)
        for i, q in enumerate(zs, start=1):  # the decoder sharpens over the epochs
            volume(f"{a.o}/vol_{i:03d}.mrc", float(q[0]), a.Apix, box=a.d or BOX, noise=0.3 / max(epoch, 1), seed=i)
    else:
        sys.exit(f"fake cryodrgn: unknown command {cmd}")


main()
