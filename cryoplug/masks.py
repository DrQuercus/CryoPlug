"""Mask creation: a binary core (map density above a threshold, or atoms of selected chains) grown by a
dilation radius and given a raised-cosine soft edge, both in Å, as in CryoSPARC Volume Tools and
relion_mask_create. Distances are Euclidean. Big boxes are handled on a coarser grid where precision is
not needed (low-pass filtering, soft edge), so memory stays moderate.
"""
from __future__ import annotations

import math
import re
from typing import Any

import gemmi
import numpy as np

from cryoplug.mrc import MapVolume, bin_map, fourier_filter, zone_mask

A3_PER_DA = 1.21  # volume of protein per dalton (density 1.37 g/cm3), as for suggested contour levels
HYDROGEN_FACTOR = 1.075  # mass of a protein including the hydrogens missing from most models
MAX_EDT_SIDE = 320  # larger boxes get their distance map on a binned grid


def ndimage():
    try:
        from scipy import ndimage as nd
    except ImportError as exc:  # pragma: no cover - depends on the installation
        raise ImportError("Masks need SciPy: run `pip install scipy` in CryoPlug's environment") from exc
    return nd


# --------------------------------------------------------------- selections
_TERM = re.compile(r"^/?([A-Za-z0-9]+)(?::(-?\d+)(?:-(-?\d+))?)?$")


def parse_selection(text: str) -> list[tuple[str, int | None, int | None]]:
    """``"A, B, C:10-250"`` -> [(chain, first residue, last residue)] (residue numbers inclusive)."""
    terms: list[tuple[str, int | None, int | None]] = []
    for token in re.split(r"[,;\s]+", str(text or "").strip()):
        if not token:
            continue
        m = _TERM.match(token)
        if not m:
            raise ValueError(f"Cannot read '{token}': write chains and residue ranges like A, B, C:10-250")
        first = int(m.group(2)) if m.group(2) is not None else None
        last = int(m.group(3)) if m.group(3) is not None else first
        if first is not None and last is not None and last < first:
            first, last = last, first
        terms.append((m.group(1), first, last))
    return terms


def _matches(terms, chain: str, num: int) -> bool:
    return any(c == chain and (a is None or a <= num <= b) for c, a, b in terms)


def select_atoms(st: gemmi.Structure, include: str = "", exclude: str = "") -> dict[str, Any]:
    """Heavy atoms of the first model in the selection: positions, mass (Da, hydrogens included) and the
    chains found. An empty ``include`` means the whole model."""
    inc, exc = parse_selection(include), parse_selection(exclude)
    xyz: list[tuple[float, float, float]] = []
    mass, has_h = 0.0, False
    chains: list[str] = []
    for chain in st[0]:
        for res in chain:
            num = res.seqid.num
            if (inc and not _matches(inc, chain.name, num)) or _matches(exc, chain.name, num):
                continue
            for atom in res:
                mass += atom.element.weight * max(atom.occ, 0.0)
                if atom.element.name in ("H", "D"):
                    has_h = True
                    continue
                xyz.append((atom.pos.x, atom.pos.y, atom.pos.z))
            if chain.name not in chains:
                chains.append(chain.name)
    missing = sorted({c for c, _a, _b in inc} - {ch.name for ch in st[0]})
    return {"xyz": np.array(xyz, dtype=np.float64).reshape(-1, 3), "mass_da": mass * (1.0 if has_h else HYDROGEN_FACTOR),
            "chains": chains, "missing_chains": missing}


# ---------------------------------------------------------------- map cores
def _upsample(coarse: np.ndarray, factor: int, shape: tuple[int, ...]) -> np.ndarray:
    """Trilinear interpolation of a block-binned array back onto the original grid (inverse of bin_map: the
    centre of coarse voxel i is fine voxel i * factor + (factor - 1) / 2), one axis at a time; the slowest axis
    last, where whole planes are copied."""
    out = np.asarray(coarse, dtype=np.float32)
    for axis in reversed(range(len(shape))):
        n, m = shape[axis], out.shape[axis]
        c = np.clip((np.arange(n) - (factor - 1) / 2.0) / factor, 0, m - 1)
        i0 = np.floor(c).astype(np.intp)
        w = (c - i0).astype(np.float32).reshape([n if a == axis else 1 for a in range(out.ndim)])
        lo = np.take(out, i0, axis=axis)
        hi = np.take(out, np.minimum(i0 + 1, m - 1), axis=axis)
        hi -= lo
        hi *= w
        lo += hi
        out = lo
    return out


def smooth_map(vol: MapVolume, lowpass: float) -> np.ndarray:
    """The map low-pass filtered to ``lowpass`` Å, on its own grid. A filter much coarser than the voxel is
    applied to a binned copy (at least 4 voxels per filter length), then interpolated back."""
    if lowpass <= 0:
        return vol.data
    factor = max(1, min(int(lowpass / (4.0 * vol.pixel_size)), max(vol.data.shape) // 32))
    if factor <= 1:
        return fourier_filter(vol, lowpass=lowpass)
    coarse = bin_map(vol, factor)
    return _upsample(fourier_filter(coarse, lowpass=lowpass), factor, vol.data.shape).astype(np.float32)


def sample_values(data: np.ndarray, region: np.ndarray | None = None, limit: int = 4_000_000) -> np.ndarray:
    values = data[region] if region is not None else data.ravel()
    step = max(1, values.size // limit)
    return np.asarray(values[::step], dtype=np.float64)


def otsu_threshold(values: np.ndarray, bins: int = 512) -> float:
    """Level separating the two classes of values with the largest between-class variance (Otsu 1979)."""
    lo, hi = np.percentile(values, [0.1, 99.99])
    if hi <= lo:
        return float(hi)
    hist, edges = np.histogram(values, bins=bins, range=(lo, hi))
    p = hist / max(hist.sum(), 1)
    centres = (edges[:-1] + edges[1:]) / 2
    omega = np.cumsum(p)
    mu = np.cumsum(p * centres)
    with np.errstate(divide="ignore", invalid="ignore"):
        between = (mu[-1] * omega - mu) ** 2 / (omega * (1.0 - omega))
    between[~np.isfinite(between)] = 0.0
    return float(edges[int(np.argmax(between)) + 1])


def level_for_mass(values: np.ndarray, n_total: int, mass_da: float, voxel_volume: float) -> float:
    """Level above which the voxels (``values`` being a sample of ``n_total`` voxels) enclose ``mass_da``."""
    n_wanted = mass_da * A3_PER_DA / voxel_volume
    fraction = min(max(n_wanted / max(n_total, 1), 1.0 / values.size), 1.0)
    return float(np.quantile(values, 1.0 - fraction))


def remove_islands(core: np.ndarray, voxel: np.ndarray, min_mass_kda: float = 0.0,
                   keep_largest: bool = False) -> tuple[np.ndarray, int]:
    """Drop the connected blobs of the core lighter than ``min_mass_kda`` (protein-equivalent mass), or all
    but the largest one. Returns the cleaned core and the number of blobs removed."""
    if not keep_largest and min_mass_kda <= 0:
        return core, 0
    nd = ndimage()
    labels, n = nd.label(core, structure=np.ones((3, 3, 3), dtype=bool))
    if n <= 1:
        return core, 0
    sizes = np.bincount(labels.ravel())
    sizes[0] = 0
    if keep_largest:
        keep = np.zeros(n + 1, dtype=bool)
        keep[int(np.argmax(sizes))] = True
    else:
        keep = sizes * float(np.prod(voxel)) / A3_PER_DA / 1000.0 >= min_mass_kda
        keep[0] = False
    return keep[labels], int(n - np.count_nonzero(keep[1:]))


def atoms_core(vol: MapVolume, xyz: np.ndarray, radius: float) -> np.ndarray:
    """Voxels within ``radius`` Å of an atom."""
    return zone_mask(vol, xyz, radius)


# ---------------------------------------------------------------- soft edge
def _block_any(core: np.ndarray, factor: int) -> np.ndarray:
    pad = [(0, (-s) % factor) for s in core.shape]
    c = np.pad(core, pad) if any(p[1] for p in pad) else core
    nz, ny, nx = (s // factor for s in c.shape)
    return c.reshape(nz, factor, ny, factor, nx, factor).any(axis=(1, 3, 5))


def distance_from(core: np.ndarray, voxel: np.ndarray, max_side: int = MAX_EDT_SIDE) -> np.ndarray:
    """Euclidean distance (Å) of every voxel to the core (0 inside). Boxes larger than ``max_side`` get the
    distance computed on a binned grid and interpolated back (error below one binned voxel)."""
    nd = ndimage()
    sampling = np.asarray(voxel, dtype=np.float64)[::-1]  # array axes are z, y, x
    factor = max(1, math.ceil(max(core.shape) / max_side))
    if factor == 1:
        return nd.distance_transform_edt(~core, sampling=sampling).astype(np.float32)
    coarse = nd.distance_transform_edt(~_block_any(core, factor), sampling=sampling * factor).astype(np.float32)
    return _upsample(coarse, factor, core.shape)


def soft_edge(core: np.ndarray, voxel: np.ndarray, dilate: float, soft: float) -> np.ndarray:
    """1 inside the core and within ``dilate`` Å of it, then a raised cosine down to 0 over ``soft`` Å."""
    out = core.astype(np.float32)
    if not core.any() or dilate + soft <= 0:
        return out
    dist = distance_from(core, voxel)
    out[dist <= dilate] = 1.0
    if soft > 0:
        band = (dist > dilate) & (dist < dilate + soft)
        out[band] = 0.5 + 0.5 * np.cos(np.pi * (dist[band] - dilate) / soft)
    out[core] = 1.0
    return out


def combine(mask: np.ndarray, other: np.ndarray, how: str) -> np.ndarray:
    other = np.clip(other, 0.0, 1.0).astype(np.float32)
    if how == "union":
        return np.maximum(mask, other)
    if how == "intersect":
        return np.minimum(mask, other)
    if how == "subtract":
        return np.clip(mask - other, 0.0, 1.0)
    return mask


# ---------------------------------------------------------------- reporting
def statistics(mask: np.ndarray, voxel: np.ndarray) -> dict[str, float]:
    """Volume enclosed at 0.5, its protein-equivalent mass and the fraction of the box."""
    n = int(np.count_nonzero(mask >= 0.5))
    volume = n * float(np.prod(voxel))
    return {"volume_nm3": volume / 1000.0, "mass_kda": volume / A3_PER_DA / 1000.0, "fraction": n / max(mask.size, 1)}


def format_mass(kda: float) -> str:
    return f"{kda / 1000:.2f} MDa" if kda >= 1000 else f"{kda:.0f} kDa"


def centroid(mask: np.ndarray) -> tuple[int, int, int]:
    """(z, y, x) index of the centre of the voxels at 0.5 or more (centre of the box if none)."""
    sel = mask >= 0.5
    total = int(np.count_nonzero(sel))
    if not total:
        return tuple(s // 2 for s in mask.shape)  # type: ignore[return-value]
    out = []
    for axis in range(3):
        others = tuple(a for a in range(3) if a != axis)
        profile = sel.sum(axis=others, dtype=np.int64)
        out.append(int(round(float((profile * np.arange(len(profile))).sum()) / total)))
    return tuple(out)  # type: ignore[return-value]


def _resize(img: np.ndarray, size: int) -> np.ndarray:
    h, w = img.shape[:2]
    scale = size / max(h, w)
    rows = np.clip((np.arange(max(1, round(h * scale))) / scale).astype(int), 0, h - 1)
    cols = np.clip((np.arange(max(1, round(w * scale))) / scale).astype(int), 0, w - 1)
    return img[rows][:, cols]


def overlay_slices(data: np.ndarray, mask: np.ndarray, centre: tuple[int, int, int], size: int = 260) -> np.ndarray:
    """Three orthogonal sections (XY, XZ, YZ) through ``centre`` of the map, the mask tinted on top and its
    0.5 contour drawn, side by side as one RGB image (y and z point up)."""
    cz, cy, cx = centre
    planes = [(data[cz], mask[cz]), (data[:, cy, :], mask[:, cy, :]), (data[:, :, cx], mask[:, :, cx])]
    panels = []
    for d, m in planes:
        d, m = _resize(d[::-1], size), _resize(m[::-1], size)
        lo, hi = np.percentile(d, [1, 99.5])
        grey = np.clip((d - lo) / ((hi - lo) or 1.0), 0, 1)
        rgb = np.repeat(grey[..., None], 3, axis=2)
        tint = np.array([1.0, 0.55, 0.1])
        weight = (np.clip(m, 0, 1) * 0.35)[..., None]
        rgb = rgb * (1 - weight) + tint * weight
        inside = m >= 0.5
        edge = inside & ~(np.roll(inside, 1, 0) & np.roll(inside, -1, 0) & np.roll(inside, 1, 1) & np.roll(inside, -1, 1))
        rgb[edge] = (1.0, 0.45, 0.0)
        panels.append((rgb * 255).astype(np.uint8))
    height = max(p.shape[0] for p in panels)
    gap = np.full((height, 6, 3), 255, dtype=np.uint8)
    row = []
    for i, p in enumerate(panels):
        if p.shape[0] < height:
            p = np.pad(p, ((0, height - p.shape[0]), (0, 0), (0, 0)), constant_values=255)
        row += [p] if i == 0 else [gap, p]
    return np.concatenate(row, axis=1)
