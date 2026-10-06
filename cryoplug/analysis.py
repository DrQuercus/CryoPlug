"""Built-in map-model validation metrics.

* Q-score (after Pintilie et al. 2020, Nat. Methods 17, 328-334)
* map-model FSC with a soft model envelope
* real-space correlation inside the model envelope (CC_mask) and over the box
* atom inclusion at a contour level
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

import gemmi
import numpy as np

from cryoplug.modelio import atom_arrays
from cryoplug.mrc import MapVolume, correlation, fsc, model_map, resolution_at


def expected_q(resolution: float) -> float:
    """Expected average Q-score of a well-fitted protein model at a given resolution (Pintilie 2020)."""
    return -0.1775 * resolution + 1.1192


def model_envelope(vol: MapVolume, xyz: np.ndarray, width: float = 6.0) -> np.ndarray:
    """Soft (0..1) envelope around the atoms."""
    env = model_map(xyz, np.ones(len(xyz)), vol, width)
    ref = float(np.percentile(env[env > 0], 50)) if np.any(env > 0) else 1.0
    return np.clip(env / (0.5 * ref), 0.0, 1.0).astype(np.float32)


def cc_mask(vol: MapVolume, st: gemmi.Structure, resolution: float) -> float:
    atoms = atom_arrays(st)
    if not len(atoms["xyz"]):
        raise ValueError("Model has no atoms")
    mm = model_map(atoms["xyz"], atoms["z"], vol, resolution)
    env = model_envelope(vol, atoms["xyz"])
    return correlation(vol.data, mm, env)


def map_model_fsc(vol: MapVolume, xyz: np.ndarray, weights: np.ndarray, envelope: np.ndarray | None = None) -> dict[str, Any]:
    sharp = model_map(xyz, weights, vol, 2.0 * float(np.min(vol.voxel)))
    env = envelope if envelope is not None else model_envelope(vol, xyz)
    freqs, curve = fsc(vol.data * env, sharp * env, vol.voxel)
    return {
        "frequency": freqs.tolist(),
        "fsc": curve.tolist(),
        "resolution_0.5": resolution_at(freqs, curve, 0.5),
        "resolution_0.143": resolution_at(freqs, curve, 0.143),
    }


def atom_inclusion(vol: MapVolume, xyz: np.ndarray, contour: float) -> float:
    vals = vol.interpolate(xyz)
    ok = ~np.isnan(vals)
    if not ok.any():
        return 0.0
    return float(np.mean(vals[ok] >= contour))


def _fibonacci_sphere(n: int) -> np.ndarray:
    i = np.arange(n) + 0.5
    phi = np.arccos(1 - 2 * i / n)
    theta = np.pi * (1 + 5 ** 0.5) * i
    return np.stack([np.cos(theta) * np.sin(phi), np.sin(theta) * np.sin(phi), np.cos(phi)], axis=1)


def q_scores(vol: MapVolume, xyz: np.ndarray, sigma: float = 0.6, r_max: float = 2.0, dr: float = 0.1,
             n_points: int = 8, n_directions: int = 48) -> np.ndarray:
    """Per-atom Q-scores. Map values are sampled on spherical shells around each atom (only at points closer to
    that atom than to any other) and correlated with a Gaussian reference profile."""
    n = len(xyz)
    if n == 0:
        return np.zeros(0)
    dirs = _fibonacci_sphere(n_directions)
    radii = np.arange(dr, r_max + 1e-9, dr)
    cell = 2.0 * r_max
    keys = np.floor(xyz / cell).astype(np.int64)
    buckets: dict[tuple[int, int, int], list[int]] = defaultdict(list)
    for i, k in enumerate(map(tuple, keys)):
        buckets[k].append(i)
    grid = {k: np.array(v, dtype=np.int64) for k, v in buckets.items()}
    empty = np.zeros(0, dtype=np.int64)
    neighbor_offsets = [(dx, dy, dz) for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)]
    shell_r = np.broadcast_to(radii[:, None], (len(radii), len(dirs)))

    points, prof, owner = [], [], []
    for i in range(n):
        kx, ky, kz = keys[i]
        neigh = np.concatenate([grid.get((kx + dx, ky + dy, kz + dz), empty) for dx, dy, dz in neighbor_offsets])
        neigh = neigh[neigh != i]
        center = xyz[i]
        pts = center + radii[:, None, None] * dirs[None, :, :]  # (R, D, 3)
        if len(neigh):
            nxyz = xyz[neigh]
            close = np.linalg.norm(nxyz - center, axis=1) < 2 * r_max + 0.01
            nxyz = nxyz[close]
        else:
            nxyz = np.zeros((0, 3))
        if len(nxyz):
            d_other = np.min(np.linalg.norm(pts[:, :, None, :] - nxyz[None, None, :, :], axis=3), axis=2)
            ok = d_other >= shell_r
        else:
            ok = np.ones(shell_r.shape, dtype=bool)
        sel = ok & (np.cumsum(ok, axis=1) <= n_points)
        p = pts[sel]
        r = shell_r[sel]
        points.append(center[None, :])
        prof.append(np.zeros(1))
        points.append(p)
        prof.append(r)
        owner.append(np.full(1 + len(p), i, dtype=np.int64))
    allp = np.concatenate(points)
    allr = np.concatenate(prof)
    own = np.concatenate(owner)
    u = vol.interpolate(allp)
    v = np.exp(-(allr ** 2) / (2 * sigma ** 2))
    ok = ~np.isnan(u)
    u, v, own = u[ok], v[ok], own[ok]
    cnt = np.bincount(own, minlength=n).astype(np.float64)
    su = np.bincount(own, u, n)
    sv = np.bincount(own, v, n)
    suu = np.bincount(own, u * u, n)
    svv = np.bincount(own, v * v, n)
    suv = np.bincount(own, u * v, n)
    num = cnt * suv - su * sv
    den = np.sqrt(np.clip(cnt * suu - su ** 2, 0, None) * np.clip(cnt * svv - sv ** 2, 0, None))
    with np.errstate(invalid="ignore", divide="ignore"):
        q = np.where(den > 0, num / den, np.nan)
    q[cnt < 3] = np.nan
    return q


def per_residue(keys: list[tuple], values: np.ndarray) -> list[dict[str, Any]]:
    groups: dict[tuple, list[float]] = {}
    order: list[tuple] = []
    for key, val in zip(keys, values):
        rk = key[:4]  # chain, seqnum, icode, resname
        if rk not in groups:
            groups[rk] = []
            order.append(rk)
        if not math.isnan(val):
            groups[rk].append(float(val))
    out = []
    for rk in order:
        vals = groups[rk]
        out.append({"chain": rk[0], "num": rk[1], "icode": rk[2], "resname": rk[3],
                    "value": float(np.mean(vals)) if vals else float("nan")})
    return out
