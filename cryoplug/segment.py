"""Segmentation of a map into regions: the watershed of ChimeraX's Segger (Pintilie et al. 2010, J. Struct. Biol. 170,
427-438), where every voxel above the threshold climbs the (smoothed) density to a local maximum, the voxels reaching
the same maximum forming a region; the regions are then grouped by the depth of the valley between them, the
shallowest first (the persistence of each maximum, instead of Segger's progressive smoothing), until the number of
regions asked remains. Each region gives a soft mask: rigid bodies of a multi-body refinement, focused refinement or
classification, signal subtraction.
"""
from __future__ import annotations

import heapq
import math

import numpy as np

MAX_SIDE = 160  # the climb runs on a binned copy of larger maps (the regions are mapped back to the full grid)


def basins(data: np.ndarray, inside: np.ndarray) -> np.ndarray:
    """The local maximum (1..K, 0 outside) every voxel inside climbs to by steepest ascent over its 26 neighbours."""
    shape = data.shape
    work = np.where(inside, data, -np.inf).astype(np.float64)
    idx = np.arange(work.size, dtype=np.int64).reshape(shape)
    best, bestv = idx.copy(), work.copy()
    pad = np.pad(work, 1, constant_values=-np.inf)
    padi = np.pad(idx, 1, constant_values=-1)
    for dz in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dz == dy == dx == 0:
                    continue
                sl = (slice(1 + dz, 1 + dz + shape[0]), slice(1 + dy, 1 + dy + shape[1]), slice(1 + dx, 1 + dx + shape[2]))
                v = pad[sl]
                higher = v > bestv
                bestv = np.where(higher, v, bestv)
                best = np.where(higher, padi[sl], best)
    parent = best.ravel()
    while True:  # pointer jumping: every voxel to the maximum at the end of its climb
        nxt = parent[parent]
        if np.array_equal(nxt, parent):
            break
        parent = nxt
    flat_inside = inside.ravel()
    roots = np.unique(parent[flat_inside])
    lut = np.zeros(work.size, dtype=np.int32)
    lut[roots] = np.arange(1, len(roots) + 1, dtype=np.int32)
    labels = np.where(flat_inside, lut[parent], 0).astype(np.int32)
    return labels.reshape(shape)


def saddles(data: np.ndarray, labels: np.ndarray) -> dict[tuple[int, int], float]:
    """For every pair of touching regions, the highest density at which they meet (the lower of two face-adjacent
    voxels of the two regions, highest over their common border)."""
    k = int(labels.max())
    out: dict[tuple[int, int], float] = {}
    for axis in range(3):
        a = [slice(None)] * 3
        b = [slice(None)] * 3
        a[axis], b[axis] = slice(0, -1), slice(1, None)
        la, lb = labels[tuple(a)], labels[tuple(b)]
        sel = (la != lb) & (la > 0) & (lb > 0)
        if not sel.any():
            continue
        lo, hi = np.minimum(la[sel], lb[sel]).astype(np.int64), np.maximum(la[sel], lb[sel]).astype(np.int64)
        v = np.minimum(data[tuple(a)][sel], data[tuple(b)][sel])
        keys = lo * (k + 1) + hi
        order = np.lexsort((-v, keys))  # per key, the highest value first
        keys, v = keys[order], v[order]
        first = np.ones(len(keys), dtype=bool)
        first[1:] = keys[1:] != keys[:-1]
        for key, value in zip(keys[first], v[first]):
            pair = (int(key // (k + 1)), int(key % (k + 1)))
            if value > out.get(pair, -np.inf):
                out[pair] = float(value)
    return out


def merge(data: np.ndarray, labels: np.ndarray, n_regions: int, min_voxels: int = 0) -> np.ndarray:
    """Regions grouped until ``n_regions`` remain: regions smaller than ``min_voxels`` first, then the pair separated
    by the shallowest valley (the lower of their two maxima minus the saddle between them). Regions touching no other
    stay apart. Returns labels 1..n by decreasing size."""
    k = int(labels.max())
    if k == 0:
        return labels
    from scipy import ndimage
    index = np.arange(1, k + 1)
    peak = np.full(k + 1, -np.inf)
    peak[1:] = ndimage.maximum(data, labels, index)
    size = np.zeros(k + 1, dtype=np.int64)
    size[1:] = np.bincount(labels.ravel(), minlength=k + 1)[1:]
    neigh: dict[int, dict[int, float]] = {i: {} for i in range(1, k + 1)}
    for (a, b), s in saddles(data, labels).items():
        neigh[a][b] = s
        neigh[b][a] = s
    parent = np.arange(k + 1)
    alive = set(range(1, k + 1))

    def depth(a: int, b: int) -> float:
        return min(peak[a], peak[b]) - neigh[a][b]

    def unite(a: int, b: int) -> int:
        keep, gone = (a, b) if size[a] >= size[b] else (b, a)
        parent[gone] = keep
        size[keep] += size[gone]
        peak[keep] = max(peak[keep], peak[gone])
        for c, s in neigh.pop(gone).items():
            neigh[c].pop(gone, None)
            if c != keep:
                neigh[keep][c] = max(neigh[keep].get(c, -np.inf), s)
                neigh[c][keep] = neigh[keep][c]
        neigh[keep].pop(gone, None)
        alive.discard(gone)
        return keep

    if min_voxels > 0:  # small regions into the neighbour they meet highest
        for r in sorted(alive, key=lambda r: size[r]):
            if r in alive and size[r] < min_voxels and neigh[r] and len(alive) > n_regions:
                unite(r, max(neigh[r], key=lambda c: neigh[r][c]))
    heap = [(depth(a, b), a, b) for a in alive for b in neigh[a] if a < b]
    heapq.heapify(heap)
    while len(alive) > n_regions and heap:
        d, a, b = heapq.heappop(heap)
        if a not in alive or b not in alive or b not in neigh[a] or abs(depth(a, b) - d) > 1e-12:
            continue  # stale entry
        keep = unite(a, b)
        for c in neigh[keep]:
            heapq.heappush(heap, (depth(keep, c), min(keep, c), max(keep, c)))

    def root(i: int) -> int:
        while parent[i] != i:
            i = parent[i]
        return i
    roots = np.array([0] + [root(i) for i in range(1, k + 1)])
    merged = roots[labels]
    final = sorted(alive, key=lambda r: -size[r])
    lut = np.zeros(k + 1, dtype=np.int32)
    lut[final] = np.arange(1, len(final) + 1, dtype=np.int32)
    return lut[merged]


def segment(data: np.ndarray, inside: np.ndarray, n_regions: int, min_voxels: int = 0) -> tuple[np.ndarray, int]:
    """Labels 1..n (by decreasing size, 0 outside) of ``data`` above ``inside``, and the number of watershed regions
    before grouping. Maps larger than MAX_SIDE are segmented on a binned copy, the regions mapped back."""
    factor = max(1, math.ceil(max(data.shape) / MAX_SIDE))
    if factor > 1:
        from scipy import ndimage
        pad = [(0, (-s) % factor) for s in data.shape]
        d = np.pad(data, pad, mode="edge")
        m = np.pad(inside, pad)
        nz, ny, nx = (s // factor for s in d.shape)
        coarse = d.reshape(nz, factor, ny, factor, nx, factor).mean(axis=(1, 3, 5))
        cin = m.reshape(nz, factor, ny, factor, nx, factor).any(axis=(1, 3, 5))
        raw = basins(coarse, cin)
        labels = merge(coarse, raw, n_regions, max(0, min_voxels // factor ** 3))
        full = np.repeat(np.repeat(np.repeat(labels, factor, 0), factor, 1), factor, 2)[:data.shape[0], :data.shape[1], :data.shape[2]]
        full = np.where(inside, full, 0)
        hole = inside & (full == 0)
        if hole.any():  # voxels inside whose binned voxel fell outside: the nearest region
            idx = ndimage.distance_transform_edt(full == 0, return_distances=False, return_indices=True)
            full = np.where(hole, full[tuple(idx)], full)
        return full.astype(np.int32), int(raw.max())
    raw = basins(data, inside)
    return merge(data, raw, n_regions, min_voxels), int(raw.max())


def colour_projections(data: np.ndarray, labels: np.ndarray, colours: list[tuple[int, int, int]],
                       size: int = 240) -> list[np.ndarray]:
    """Projections along z, y and x (RGB, uint8): each pixel coloured by the region of the densest voxel on its ray,
    shaded by that density; grey where the ray meets no region."""
    out = []
    lo = float(np.percentile(data, 50))
    hi = float(data.max()) or 1.0
    for axis in (0, 1, 2):
        arg = np.argmax(data, axis=axis)
        dens = np.take_along_axis(data, np.expand_dims(arg, axis), axis).squeeze(axis)
        lab = np.take_along_axis(labels, np.expand_dims(arg, axis), axis).squeeze(axis)
        shade = np.clip((dens - lo) / max(hi - lo, 1e-9), 0, 1)
        img = np.zeros(lab.shape + (3,), dtype=np.float32)
        img[...] = (shade * 200)[..., None]
        for r in range(1, int(labels.max()) + 1):
            sel = lab == r
            if sel.any():
                c = np.array(colours[(r - 1) % len(colours)], dtype=np.float32)
                img[sel] = (0.35 + 0.65 * shade[sel])[:, None] * c
        img = img[::-1]  # first array axis up
        out.append(_resize_rgb(np.clip(img, 0, 255).astype(np.uint8), size))
    return out


def _resize_rgb(img: np.ndarray, size: int) -> np.ndarray:
    h, w = img.shape[:2]
    scale = size / max(h, w)
    ys = np.clip((np.arange(int(round(h * scale))) / scale).astype(int), 0, h - 1)
    xs = np.clip((np.arange(int(round(w * scale))) / scale).astype(int), 0, w - 1)
    return img[ys][:, xs]
