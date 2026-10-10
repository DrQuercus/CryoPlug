"""Particle metadata (CryoSPARC .cs files) and latent spaces (cryoDRGN, 3D variability).

CryoSPARC writes a job's particles as two NumPy record arrays: the fields produced by the job
(``J12_005_particles.cs``: poses, ...) and the fields passed through from upstream jobs
(``J12_passthrough_particles.cs``: image location, CTF, ...). Programs such as cryoDRGN want them in
one file: :func:`merged_particles` joins them by particle uid. Image paths (``blob/path``) are
relative to the CryoSPARC project directory, which is passed to cryoDRGN as ``--datadir``.
"""
from __future__ import annotations

import io
import pickle
import re
from pathlib import Path
from typing import Any

import numpy as np

NUMPY_MAGIC = b"\x93NUMPY"
CSDAT_MAGIC = b"\x95CSDAT"  # compressed stream format of recent CryoSPARC versions


# ------------------------------------------------------------------ .cs files
def read_cs(path: str | Path) -> np.ndarray:
    """A CryoSPARC dataset as a NumPy structured array (NumPy or compressed CSDAT format)."""
    path = Path(path)
    with open(path, "rb") as fh:
        magic = fh.read(6)
    if magic == NUMPY_MAGIC:
        return np.load(path, allow_pickle=False)
    if magic == CSDAT_MAGIC:
        try:
            from cryosparc.dataset import Dataset  # cryosparc-tools
        except ImportError:
            raise ValueError(f"{path.name} uses CryoSPARC's compressed format: install cryosparc-tools "
                             "(pip install cryosparc-tools) in CryoPlug's environment to read it") from None
        return np.asarray(Dataset.load(str(path)).to_records(fixed=True))
    raise ValueError(f"{path.name} is not a CryoSPARC .cs file")


def write_cs(data: np.ndarray, path: str | Path) -> Path:
    """Write a structured array in the NumPy .cs format read by CryoSPARC and cryoDRGN."""
    path = Path(path)
    with open(path, "wb") as fh:  # np.save(name) would append ".npy"
        np.save(fh, data, allow_pickle=False)
    return path


def _iteration(name: str) -> int:
    m = re.search(r"_(\d{3})_particles", name)
    return int(m.group(1)) if m else -1


def find_particle_files(job_dir: str | Path) -> tuple[Path | None, Path | None]:
    """The particles of a CryoSPARC job: (main file of the latest iteration, passthrough file)."""
    job_dir = Path(job_dir)
    main, passthrough = [], []
    for p in job_dir.glob("*particles*.cs"):
        name = p.name.lower()
        if not p.is_file() or "_class_" in name or "excluded" in name or "rejected" in name:
            continue
        (passthrough if "passthrough" in name else main).append(p)
    best = max(main, key=lambda p: (_iteration(p.name), p.stat().st_mtime), default=None)
    through = max(passthrough, key=lambda p: p.stat().st_mtime, default=None)
    if best is None and through is not None:  # some jobs only pass particles through
        best, through = through, None
    return best, through


def merge(main: np.ndarray, extra: np.ndarray | None) -> np.ndarray:
    """Fields of ``main`` plus the fields of ``extra`` it lacks, rows matched by ``uid``."""
    if extra is None or not extra.dtype.names:
        return main
    new = [n for n in extra.dtype.names if n not in main.dtype.names]
    if not new:
        return main
    if "uid" in main.dtype.names and "uid" in extra.dtype.names:
        order = np.argsort(extra["uid"])
        pos = np.searchsorted(extra["uid"], main["uid"], sorter=order)
        pos = np.clip(pos, 0, len(order) - 1)
        rows = order[pos]
        if not np.array_equal(extra["uid"][rows], main["uid"]):
            raise ValueError("The passthrough particles do not match the job's particles (uids differ)")
    elif len(extra) == len(main):
        rows = np.arange(len(main))
    else:
        raise ValueError("Cannot match the passthrough particles to the job's particles")
    dtype = main.dtype.descr + [d for d in extra.dtype.descr if d[0] in new]
    out = np.empty(len(main), dtype=dtype)
    for name in main.dtype.names:
        out[name] = main[name]
    for name in new:
        out[name] = extra[name][rows]
    return out


def merged_particles(main: str | Path, passthrough: str | Path | None = None) -> np.ndarray:
    return merge(read_cs(main), read_cs(passthrough) if passthrough else None)


def project_dir_of(job_dir: str | Path) -> Path:
    """CryoSPARC project directory of a job directory (image paths are relative to it)."""
    return Path(job_dir).resolve().parent


def summary(data: np.ndarray) -> dict[str, Any]:
    """Number of particles, box, pixel size, and what the metadata contains."""
    names = set(data.dtype.names or ())
    info: dict[str, Any] = {"n_particles": int(len(data))}
    if "blob/shape" in names and len(data):
        info["box"] = int(np.asarray(data["blob/shape"][0]).ravel()[0])
    if "blob/psize_A" in names and len(data):
        info["pixel_size"] = round(float(data["blob/psize_A"][0]), 4)
    if "blob/path" in names:
        info["n_stacks"] = int(len(np.unique(data["blob/path"])))
    info["has_images"] = "blob/path" in names and "blob/idx" in names
    info["has_poses"] = "alignments3D/pose" in names and "alignments3D/shift" in names
    info["has_ctf"] = "ctf/df1_A" in names
    comps = sorted(int(m.group(1)) for n in names if (m := re.fullmatch(r"components_mode_(\d+)/value", n)))
    if comps:
        info["components"] = len(comps)
    return info


def components(data: np.ndarray) -> np.ndarray | None:
    """3D variability reaction coordinates of each particle (N, K), or None."""
    names = sorted((n for n in data.dtype.names or () if re.fullmatch(r"components_mode_\d+/value", n)),
                   key=lambda n: int(re.search(r"\d+", n).group()))
    if not names:
        return None
    return np.stack([np.asarray(data[n], dtype=np.float32) for n in names], axis=1)


# -------------------------------------------------------------- pickle files
_ALLOWED = {
    ("numpy", "ndarray"), ("numpy", "dtype"),
    ("numpy.core.multiarray", "_reconstruct"), ("numpy._core.multiarray", "_reconstruct"),
    ("numpy.core.multiarray", "scalar"), ("numpy._core.multiarray", "scalar"),
    ("numpy.core.numeric", "_frombuffer"), ("numpy._core.numeric", "_frombuffer"),
}


class _ArrayUnpickler(pickle.Unpickler):
    """Unpickler restricted to NumPy arrays: cryoDRGN's .pkl outputs (latent vectors, labels, indices)
    are read without allowing a pickle file to run code."""

    def find_class(self, module, name):
        if (module, name) in _ALLOWED:
            return super().find_class(module, name)
        raise pickle.UnpicklingError(f"{module}.{name} is not allowed in a data .pkl file")


def load_pkl(path: str | Path) -> Any:
    """First object of a .pkl file made of NumPy arrays and plain containers (pose.pkl: (rotations, shifts))."""
    with open(path, "rb") as fh:
        return _ArrayUnpickler(fh).load()


def load_array_pkl(path: str | Path) -> np.ndarray:
    """First object of a .pkl file, which must be a NumPy array (z.N.pkl holds z_mu then z_logvar)."""
    return np.asarray(load_pkl(path))


def save_array_pkl(array: np.ndarray, path: str | Path) -> Path:
    path = Path(path)
    with open(path, "wb") as fh:
        pickle.dump(np.asarray(array), fh)
    return path


def array_from_bytes(data: bytes) -> np.ndarray:
    return np.asarray(_ArrayUnpickler(io.BytesIO(data)).load())


# --------------------------------------------------------------- latent spaces
def pca_basis(z: np.ndarray) -> tuple[np.ndarray, np.ndarray, list[float]]:
    """Mean, first two principal axes (2, d) and their explained variance (%) of the latent vectors."""
    z = np.asarray(z, dtype=np.float64)
    if z.ndim == 1:
        z = z[:, None]
    mean = z.mean(axis=0)
    sample = (z - mean)[:: max(1, len(z) // 200_000)]
    _u, s, vt = np.linalg.svd(sample, full_matrices=False)
    axes = np.zeros((2, z.shape[1]))
    axes[: min(2, len(vt))] = vt[:2]
    var = (s ** 2) / max(float((s ** 2).sum()), 1e-12) * 100
    return mean, axes, [round(float(v), 1) for v in var[:2]] + [0.0] * (2 - len(var[:2]))


def pca2(z: np.ndarray) -> tuple[np.ndarray, list[float]]:
    """First two principal components of the latent vectors and their explained variance (%)."""
    z = np.asarray(z, dtype=np.float64)
    if z.ndim == 1:
        z = z[:, None]
    mean, axes, var = pca_basis(z)
    return ((z - mean) @ axes.T).astype(np.float32), var


def kmeans(z: np.ndarray, k: int, iterations: int = 30, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Plain k-means (k-means++ start) on latent vectors: labels (N,) and centres (k, d)."""
    z = np.asarray(z, dtype=np.float64)
    rng = np.random.default_rng(seed)
    k = max(1, min(k, len(z)))
    sample = z[rng.choice(len(z), size=min(len(z), 100_000), replace=False)]
    centres = [sample[rng.integers(len(sample))]]
    for _ in range(1, k):
        d2 = np.min(((sample[:, None, :] - np.array(centres)[None]) ** 2).sum(-1), axis=1)
        probs = d2 / d2.sum() if d2.sum() > 0 else None
        centres.append(sample[rng.choice(len(sample), p=probs)])
    centres = np.array(centres)
    for _ in range(iterations):
        labels = np.argmin(((sample[:, None, :] - centres[None]) ** 2).sum(-1), axis=1)
        new = np.array([sample[labels == i].mean(axis=0) if np.any(labels == i) else centres[i] for i in range(k)])
        if np.allclose(new, centres):
            break
        centres = new
    labels = np.empty(len(z), dtype=np.int32)
    for start in range(0, len(z), 200_000):
        block = z[start:start + 200_000]
        labels[start:start + len(block)] = np.argmin(((block[:, None, :] - centres[None]) ** 2).sum(-1), axis=1)
    return labels, centres


def nearest_indices(z: np.ndarray, centres: np.ndarray) -> np.ndarray:
    """Index of the particle closest to each centre."""
    z = np.asarray(z, dtype=np.float64)
    return np.array([int(np.argmin(((z - c) ** 2).sum(-1))) for c in np.asarray(centres, dtype=np.float64)])


def pca_components(z: np.ndarray, n: int = 10) -> tuple[np.ndarray, np.ndarray, list[float]]:
    """Mean, first ``n`` principal axes (n, d) and the variance (%) each one explains."""
    z = np.asarray(z, dtype=np.float64)
    if z.ndim == 1:
        z = z[:, None]
    mean = z.mean(axis=0)
    sample = (z - mean)[:: max(1, len(z) // 200_000)]
    _u, s, vt = np.linalg.svd(sample, full_matrices=False)
    n = max(1, min(n, len(vt)))
    var = (s ** 2) / max(float((s ** 2).sum()), 1e-12) * 100
    return mean, vt[:n], [round(float(v), 2) for v in var[:n]]


def points_in_polygon(points: np.ndarray, polygon: np.ndarray) -> np.ndarray:
    """Which of the points (N, 2) lie inside the polygon (M, 2) (even-odd rule, as a lasso)."""
    pts = np.asarray(points, dtype=np.float64)
    poly = np.asarray(polygon, dtype=np.float64)
    x, y = pts[:, 0], pts[:, 1]
    inside = np.zeros(len(pts), dtype=bool)
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if yi != yj:
            crosses = ((yi > y) != (yj > y)) & (x < (xj - xi) * (y - yi) / (yj - yi) + xi)
            inside ^= crosses
        j = i
    return inside


# ------------------------------------------------------------------- .star files
def _star_table(path: str | Path) -> tuple[list[str], list[str], list[str], list[str]]:
    """A RELION .star file split around its particle table: (lines before the rows, column labels, rows,
    lines after). The particle table is the loop of the ``data_particles`` block (RELION 3.1+), or the
    loop holding ``_rlnImageName`` in older files."""
    lines = Path(path).read_text(errors="replace").splitlines()
    loops = []  # (block, labels, first row line, end line)
    block, i = "", 0
    while i < len(lines):
        s = lines[i].strip()
        if s.startswith("data_"):
            block = s[5:]
        elif s.startswith("loop_"):
            j = i + 1
            labels = []
            while j < len(lines) and lines[j].strip().startswith("_"):
                labels.append(lines[j].split()[0])
                j += 1
            k = j
            while k < len(lines) and not lines[k].strip().startswith(("data_", "loop_", "_")):
                k += 1
            loops.append((block, labels, j, k))
            i = k
            continue
        i += 1
    chosen = next((lp for lp in loops if lp[0] == "particles"), None) or next(
        (lp for lp in loops if "_rlnImageName" in lp[1]), None)
    if chosen is None:
        raise ValueError(f"No particle table in {Path(path).name}")
    _block, labels, start, end = chosen
    rows = [ln for ln in lines[start:end] if ln.strip() and not ln.strip().startswith("#")]
    return lines[:start], labels, rows, lines[end:]


def star_column(path: str | Path, label: str) -> list[str]:
    """Values of one column of the particle table of a .star file."""
    _head, labels, rows, _tail = _star_table(path)
    if label not in labels:
        raise ValueError(f"{Path(path).name} has no {label} column")
    col = labels.index(label)
    return [row.split()[col] for row in rows]


def star_count(path: str | Path) -> int:
    return len(_star_table(path)[2])


def filter_star(src: str | Path, dst: str | Path, ind: np.ndarray) -> Path:
    """Copy of a .star file keeping only the particle rows ``ind`` (optics table and other blocks unchanged)."""
    head, _labels, rows, tail = _star_table(src)
    kept = [rows[int(i)] for i in np.asarray(ind, dtype=np.int64)]
    dst = Path(dst)
    dst.write_text("\n".join(head + kept + [""] + tail).rstrip("\n") + "\n")
    return dst


# --------------------------------------------------------------- particle images
class ParticleImages:
    """Particle images read by index: from an image stack (.mrcs), a list of stacks (.txt, written by
    ``cryodrgn downsample --chunk``), or the image references of a CryoSPARC .cs or RELION .star file
    (paths relative to ``datadir``)."""

    def __init__(self, source: str | Path, datadir: str | Path | None = None):
        import mrcfile  # noqa: F401 - fail early when missing
        self.source = Path(source)
        self.datadir = Path(datadir) if datadir else None
        self._maps: dict[Path, Any] = {}
        suffix = self.source.suffix.lower()
        self._refs: tuple[list[Path], np.ndarray] | None = None
        self._stacks: list[Path] = []
        self._offsets = np.zeros(1, dtype=np.int64)
        if suffix in (".mrcs", ".mrc", ".st"):
            self._stacks = [self.source]
        elif suffix == ".txt":
            self._stacks = [self._resolve(line.strip(), self.source.parent)
                            for line in self.source.read_text().splitlines() if line.strip()]
        elif suffix == ".cs":
            data = read_cs(self.source)
            if "blob/path" not in (data.dtype.names or ()):
                raise ValueError(f"{self.source.name} has no image locations (blob/path)")
            names = {}
            paths = []
            for raw in data["blob/path"]:
                key = raw.decode() if isinstance(raw, bytes) else str(raw)
                if key not in names:
                    names[key] = self._resolve(key.lstrip(">"), self.source.parent)
                paths.append(names[key])
            self._refs = (paths, np.asarray(data["blob/idx"], dtype=np.int64))
        elif suffix == ".star":
            paths, idx, cache = [], [], {}
            for value in star_column(self.source, "_rlnImageName"):
                number, _at, rel = value.partition("@")
                if rel not in cache:
                    cache[rel] = self._resolve(rel, self.source.parent)
                paths.append(cache[rel])
                idx.append(int(number) - 1)
            self._refs = (paths, np.asarray(idx, dtype=np.int64))
        else:
            raise ValueError(f"Cannot read particle images from {self.source.name}")
        if self._refs is None:
            counts = [self._stack(p).shape[0] for p in self._stacks]
            self._offsets = np.concatenate([[0], np.cumsum(counts)]).astype(np.int64)
            self.n = int(self._offsets[-1])
        else:
            self.n = len(self._refs[1])

    def _resolve(self, rel: str, base: Path) -> Path:
        p = Path(rel)
        if p.is_absolute():
            return p
        for root in (self.datadir, base):
            if root is not None and (root / p).exists():
                return root / p
        return (self.datadir or base) / p

    def _stack(self, path: Path) -> np.ndarray:
        if path not in self._maps:
            import mrcfile
            if len(self._maps) > 32:
                for mm in self._maps.values():
                    mm.close()
                self._maps.clear()
            self._maps[path] = mrcfile.mmap(str(path), mode="r", permissive=True)
        data = self._maps[path].data
        return data if data.ndim == 3 else data[np.newaxis]

    def image(self, i: int) -> np.ndarray:
        if not 0 <= i < self.n:
            raise IndexError(f"Particle {i} does not exist (0 to {self.n - 1})")
        if self._refs is not None:
            path, idx = self._refs[0][i], int(self._refs[1][i])
        else:
            k = int(np.searchsorted(self._offsets, i, side="right") - 1)
            path, idx = self._stacks[k], i - int(self._offsets[k])
        return np.asarray(self._stack(path)[idx], dtype=np.float32)

    def close(self) -> None:
        for mm in self._maps.values():
            mm.close()
        self._maps.clear()


def particle_tile(image: np.ndarray, size: int = 96) -> np.ndarray:
    """A noisy particle image made readable: low-pass filtered, contrast-stretched, resized (uint8)."""
    from scipy import ndimage
    img = np.asarray(image, dtype=np.float32)
    img = ndimage.gaussian_filter(img, sigma=max(0.8, img.shape[0] / 64))
    lo, hi = np.percentile(img, [1, 99])
    img = np.clip((img - lo) / ((hi - lo) or 1.0), 0, 1)
    img = ndimage.zoom(img, size / img.shape[0], order=1)[:size, :size]
    return (img[::-1] * 255).astype(np.uint8)  # y up, as the maps


def montage(tiles: list[np.ndarray], per_row: int = 6, gap: int = 3) -> np.ndarray:
    """Tiles side by side on a transparent background (RGBA)."""
    if not tiles:
        return np.zeros((1, 1, 4), dtype=np.uint8)
    size = tiles[0].shape[0]
    cols = min(per_row, len(tiles))
    rows = int(np.ceil(len(tiles) / cols))
    sheet = np.zeros((rows * (size + gap) - gap, cols * (size + gap) - gap, 4), dtype=np.uint8)
    for i, t in enumerate(tiles):
        r, c = divmod(i, cols)
        y, x = r * (size + gap), c * (size + gap)
        sheet[y:y + size, x:x + size, :3] = t[..., None]
        sheet[y:y + size, x:x + size, 3] = 255
    return sheet
