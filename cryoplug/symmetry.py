"""Point groups in RELION's conventions (src/symmetries.cpp): the rotations of Cn, Dn, T, O and I (I1 to I4 settings),
the symmetrisation of a map about its centre and the correlation of a map with its symmetrised copy.

RELION defines each group by generators (``rot_axis n x y z``: a rotation of 360°/n about that axis): Cn by an n-fold
axis along z, Dn adds a two-fold along x, T, O and I (I2, the default: 2-fold along z, 5-fold in the xz plane) by the
axes below. The group is the closure of its generators.
"""
from __future__ import annotations

import re

import numpy as np

GENERATORS: dict[str, list[tuple[tuple[float, float, float], int]]] = {
    "T": [((0.0, 0.0, 1.0), 3), ((0.0, 0.816496, 0.577350), 2)],
    "O": [((0.5773502, 0.5773502, 0.5773502), 3), ((0.0, 0.0, 1.0), 4)],
    "I2": [((0.0, 0.0, 1.0), 2), ((0.525731114, 0.0, 0.850650807), 5), ((0.0, 0.356822076, 0.934172364), 3)],
    "I1": [((1.0, 0.0, 0.0), 2), ((0.85065080702670, 0.0, -0.5257311142635), 5), ((0.9341723640, 0.3568220765, 0.0), 3)],
    "I3": [((-0.5257311143, 0.0, 0.8506508070), 2), ((0.0, 0.0, 1.0), 5),
           ((-0.4911234778630044, 0.3568220764705179, 0.7946544753759428), 3)],
    "I4": [((0.5257311143, 0.0, 0.8506508070), 2), ((0.8944271932547096, 0.0, 0.4472135909903704), 5),
           ((0.4911234778630044, 0.3568220764705179, 0.7946544753759428), 3)],
}
GENERATORS["I"] = GENERATORS["I2"]
GROUP = re.compile(r"^(C|D)(\d+)$|^(T|O|I[1-4]?)$")


def normalise(sym: str) -> str:
    """'c4' -> 'C4'; ValueError for anything that is not a rotation point group."""
    s = str(sym).strip().upper()
    m = GROUP.match(s)
    if not m or (m.group(2) is not None and int(m.group(2)) < 1):
        raise ValueError(f"{sym}: not a point group (Cn, Dn, T, O, I, I1-I4)")
    return s


def rotation(axis: tuple[float, float, float], order: int) -> np.ndarray:
    """The rotation of 360°/order about ``axis`` (x, y, z), as a matrix acting on (x, y, z) column vectors."""
    a = np.asarray(axis, dtype=float)
    a = a / np.linalg.norm(a)
    t = 2 * np.pi / order
    k = np.array([[0, -a[2], a[1]], [a[2], 0, -a[0]], [-a[1], a[0], 0]])
    return np.eye(3) + np.sin(t) * k + (1 - np.cos(t)) * k @ k


def generators(sym: str) -> list[np.ndarray]:
    s = normalise(sym)
    if s[0] in "CD" and s[1:].isdigit():
        n = int(s[1:])
        gens = [rotation((0, 0, 1), n)] if n > 1 else []
        if s[0] == "D":
            gens.append(rotation((1, 0, 0), 2))
        return gens
    return [rotation(axis, order) for axis, order in GENERATORS[s]]


def operators(sym: str) -> np.ndarray:
    """All the rotations of the group (n, 3, 3), the identity first: the closure of the generators."""
    ops = [np.eye(3)]
    gens = generators(sym)
    frontier = list(ops)
    while frontier:
        new = []
        for a in frontier:
            for g in gens:
                m = g @ a
                if not any(np.allclose(m, b, atol=1e-4) for b in ops):
                    ops.append(m)
                    new.append(m)
        frontier = new
        if len(ops) > 200:
            raise ValueError(f"{sym}: the generators do not close into a finite group")
    return np.stack(ops)


def order(sym: str) -> int:
    s = normalise(sym)
    if s[0] in "CD" and s[1:].isdigit():
        return int(s[1:]) * (2 if s[0] == "D" else 1)
    return {"T": 12, "O": 24}.get(s, 60)


def parse_groups(text: str) -> list[str]:
    """'C2-C12, D2-D8, T, O, I' -> ['C2', ..., 'C12', 'D2', ..., 'D8', 'T', 'O', 'I']."""
    out: list[str] = []
    for part in re.split(r"[,;\s]+", str(text).strip()):
        if not part:
            continue
        m = re.fullmatch(r"([CDcd])(\d+)\s*-\s*(?:[CDcd])?(\d+)", part)
        names = [f"{m.group(1).upper()}{n}" for n in range(int(m.group(2)), int(m.group(3)) + 1)] if m else [part]
        for name in names:
            s = normalise(name)
            if s not in out and s != "C1":
                out.append(s)
    if not out:
        raise ValueError("No point group to try")
    return out


def _rotate(data: np.ndarray, rot: np.ndarray) -> np.ndarray:
    """``data`` (z, y, x) sampled at R·r about the centre voxel (box // 2, RELION's origin), trilinear."""
    from scipy import ndimage
    p = np.eye(3)[::-1]  # (x, y, z) <-> (z, y, x)
    m = p @ rot @ p
    c = np.array(data.shape, dtype=float) // 2
    return ndimage.affine_transform(data, m, offset=c - m @ c, order=1, mode="constant", cval=0.0)


def symmetrise(data: np.ndarray, ops: np.ndarray) -> np.ndarray:
    """The average of the map over the rotations of the group."""
    out = np.zeros(data.shape, dtype=np.float64)
    for rot in ops:
        out += data if np.allclose(rot, np.eye(3)) else _rotate(data, rot)
    return (out / len(ops)).astype(np.float32)


def prepare(data: np.ndarray, apix: float, resolution: float, max_box: int = 96) -> tuple[np.ndarray, float]:
    """The map Fourier-cropped to ``max_box`` voxels (or the box keeping 2.5 samples per ``resolution``, if larger)
    and low-pass filtered to ``resolution`` Å (a Gaussian falling to half at that resolution)."""
    data = np.asarray(data, dtype=np.float32)
    box = data.shape[0]
    needed = (int(np.ceil(2.5 * box * apix / max(resolution, 2 * apix))) + 1) // 2 * 2
    target = min(box, max(max_box, needed))
    f = np.fft.fftshift(np.fft.fftn(data))
    if target < box:
        s = (box - target) // 2
        f = f[s:s + target, s:s + target, s:s + target]
        apix = apix * box / target
    n = f.shape[0]
    k = (np.arange(n) - n // 2) / (n * apix)
    k2 = k[:, None, None] ** 2 + k[None, :, None] ** 2 + k[None, None, :] ** 2
    sigma2 = 1.0 / resolution ** 2 / np.log(2) / 2  # exp(-k² / (2 σ²)) = 1/2 at k = 1/resolution
    f = f * np.exp(-k2 / (2 * sigma2))
    return np.fft.ifftn(np.fft.ifftshift(f)).real.astype(np.float32) * (n / box) ** 3, apix


def symmetry_cc(data: np.ndarray, ops: np.ndarray, radius: float | None = None) -> float:
    """Correlation between the map and its symmetrised copy inside a sphere about the centre (RELION's origin)."""
    sym = symmetrise(data, ops)
    n = data.shape[0]
    z, y, x = np.indices(data.shape) - n // 2
    inside = x ** 2 + y ** 2 + z ** 2 <= (radius if radius else 0.45 * n) ** 2
    a, b = data[inside].astype(np.float64), sym[inside].astype(np.float64)
    a -= a.mean()
    b -= b.mean()
    den = np.sqrt((a * a).sum() * (b * b).sum())
    return float((a * b).sum() / den) if den > 0 else 0.0
