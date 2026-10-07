"""Fake cryoDRGN 4.3 command line for the tests: same sub-commands, arguments and output files, with tiny
synthetic data (latent space with three groups, volumes in which one blob moves with the latent value)."""
import argparse
import os
import pickle
import sys

import numpy as np

sys.path.insert(0, os.environ.get("CRYOPLUG_ROOT", ""))
from cryoplug.mrc import MapVolume  # noqa: E402

BOX = 32


def volume(path, shift, apix=4.0, extra=1.0):
    z, y, x = np.indices((BOX, BOX, BOX))
    c = BOX / 2
    a = np.exp(-((x - c + 5) ** 2 + (y - c) ** 2 + (z - c) ** 2) / 12.0)
    b = extra * np.exp(-((x - c - 4 - shift) ** 2 + (y - c) ** 2 + (z - c) ** 2) / 10.0)
    MapVolume(data=(a + b).astype(np.float32), voxel=np.array([apix] * 3)).write(path)


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


def main():
    cmd, rest = sys.argv[1], sys.argv[2:]
    print(f"fake cryodrgn {cmd} {' '.join(rest)}", flush=True)
    p = argparse.ArgumentParser()
    if cmd == "--version":
        print("cryoDRGN 4.3.1")
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
        obj = (data["alignments3D/pose"], data["alignments3D/shift"]) if cmd == "parse_pose_csparc" else np.zeros((len(data), 9), np.float32)
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
        MapVolume(data=np.zeros((len(data), a.D, a.D), np.float32), voxel=np.ones(3)).write(a.o)
    elif cmd == "train_vae":
        p.add_argument("particles")
        for opt in ("--poses", "--ctf", "-o", "--ind", "--datadir", "--enc-dim", "--enc-layers", "--dec-dim", "--dec-layers", "-b"):
            p.add_argument(opt)
        p.add_argument("--zdim", type=int)
        p.add_argument("-n", type=int)
        for flag in ("--no-analysis", "--multigpu", "--lazy", "--uninvert-data"):
            p.add_argument(flag, action="store_true")
        a = p.parse_args(rest)
        if a.particles.endswith(".cs") and not a.datadir:
            sys.exit("Cannot find the images: give --datadir")
        n = n_images(a.particles, a.ind)
        os.makedirs(a.o, exist_ok=True)
        rng = np.random.default_rng(1)
        groups = rng.integers(0, 3, n)
        z = (rng.normal(0, 0.3, (n, a.zdim)) + groups[:, None] * 2.0).astype(np.float32)
        with open(f"{a.o}/run.log", "w") as log:
            for e in range(1, a.n + 1):
                line = f"# =====> Epoch: {e} Average gen loss = {1 / e:.6}, KLD = {2 + 1 / e:.6f}, total loss = {1 / e + 0.01:.6f}; Finished in 0:00:01"
                print(line, flush=True)
                log.write(line + "\n")
                for name in (f"weights.{e}.pkl", f"z.{e}.pkl"):
                    with open(f"{a.o}/{name}", "wb") as fh:
                        pickle.dump(z if name.startswith("z") else np.zeros(1), fh)
                        if name.startswith("z"):
                            pickle.dump(np.zeros_like(z), fh)
        for name in ("weights.pkl", "z.pkl"):
            with open(f"{a.o}/{name}", "wb") as fh:
                pickle.dump(z if name == "z.pkl" else np.zeros(1), fh)
        with open(f"{a.o}/config.yaml", "w") as fh:
            fh.write(f"dataset_args:\n  particles: {a.particles}\n  ctf: {a.ctf}\nlattice_args:\n  D: 33\n")
    elif cmd == "analyze":
        p.add_argument("workdir")
        p.add_argument("epoch", type=int)
        p.add_argument("-o")
        p.add_argument("--ksample", type=int, default=20)
        p.add_argument("--pc", type=int, default=2)
        p.add_argument("--n-per-pc", type=int, default=10)
        p.add_argument("--Apix", type=float, default=4.0)
        p.add_argument("-d")
        p.add_argument("--low-pass")
        for flag in ("--flip", "--invert"):
            p.add_argument(flag, action="store_true")
        a = p.parse_args(rest)
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
        for i, ci in enumerate(centers_ind):
            volume(f"{kdir}/vol_{i + 1:03d}.mrc", float(z[ci, 0]), a.Apix, extra=0.2 if i == 0 else 1.0)
        for pc in range(1, a.pc + 1):
            os.makedirs(f"{out}/pc{pc}", exist_ok=True)
            for j in range(a.n_per_pc):
                volume(f"{out}/pc{pc}/vol_{j + 1:03d}.mrc", (j - a.n_per_pc / 2) / 2, a.Apix)
        with open(f"{out}/umap.pkl", "wb") as fh:
            pickle.dump(z[:, :2] * np.array([1.0, -1.0], np.float32), fh)
        png = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                            "1f15c4890000000d49444154789c63000100000500010d0a2db40000000049454e44ae426082")
        for name in ("umap_hexbin.png", "z_pca_hexbin.png", f"kmeans{k}/umap.png"):
            with open(f"{out}/{name}", "wb") as fh:
                fh.write(png)
    elif cmd == "graph_traversal":
        p.add_argument("zfile")
        p.add_argument("--anchors", nargs="+", type=int)
        p.add_argument("-o")
        p.add_argument("--outind")
        a = p.parse_args(rest)
        with open(a.zfile, "rb") as fh:
            z = pickle.load(fh)
        path = []
        for s, e in zip(a.anchors[:-1], a.anchors[1:]):
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
        p.add_argument("--flip", action="store_true")
        a = p.parse_args(rest)
        zs = np.atleast_2d(np.loadtxt(a.zfile))
        os.makedirs(a.o, exist_ok=True)
        for i, q in enumerate(zs, start=1):
            volume(f"{a.o}/vol_{i:03d}.mrc", float(q[0]), a.Apix)
    else:
        sys.exit(f"fake cryodrgn: unknown command {cmd}")


main()
