"""Rigid-body fitting of an atomic model or a map into a map, as ChimeraX's ``fitmap`` does it (map_fit bundle).

The moving object is a set of points with optional weights: atom positions (no weights: the average map value at the
atoms is maximised), or the grid points of a map above its contour level with their values as weights (also for a
model at a given resolution: the points of its simulated map, see ``masks.molmap``). The points are moved by
gradient ascent, alternating translation and rotation steps whose size is set in grid units of the target map: half a
voxel at first, grown by 1.2 after each segment of 4 steps that moved the points far enough, halved otherwise, until
it falls below 0.01 voxel or 2000 steps (ChimeraX's ``locate_maximum``). The metric is the overlap (sum of the
products of the weights and the map values), the correlation about zero or the correlation about the mean (``cam``).
Map values and gradients are interpolated trilinearly (gradients from central differences), 0 outside the map.

A search (``fit_search``) starts from random positions and orientations, optimises each, gathers the results closer
than 6° and 3 Å, and keeps those with enough points inside the contour of the target map, best first.

Transforms are 4×4 matrices (rows, NumPy convention) acting on the points' own coordinates: ``q = R p + t``.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

from cryoplug.mrc import MapVolume

METRICS = {"overlap": "sum product", "correlation": "correlation", "cam": "correlation about mean"}
MAX_POINTS = 50_000  # grid points of a moving map used for the fit (a regular subsample beyond)
SEARCH_POINTS = 5_000  # points used for the random starts of a search (the best fits are then refined with all)
MAX_SEARCH = 200


# ------------------------------------------------------------------ transforms
def translation(v) -> np.ndarray:
    m = np.eye(4)
    m[:3, 3] = v
    return m


def rotation(axis, angle_deg: float, center=(0.0, 0.0, 0.0)) -> np.ndarray:
    """Rotation by ``angle_deg`` about ``axis`` through ``center`` (right-handed)."""
    a = np.asarray(axis, dtype=np.float64)
    n = np.linalg.norm(a)
    m = np.eye(4)
    if n == 0 or angle_deg == 0:
        return m
    x, y, z = a / n
    t = math.radians(angle_deg)
    c, s, C = math.cos(t), math.sin(t), 1 - math.cos(t)
    r = np.array([[c + x * x * C, x * y * C - z * s, x * z * C + y * s],
                  [y * x * C + z * s, c + y * y * C, y * z * C - x * s],
                  [z * x * C - y * s, z * y * C + x * s, c + z * z * C]])
    center = np.asarray(center, dtype=np.float64)
    m[:3, :3] = r
    m[:3, 3] = center - r @ center
    return m


def apply(m: np.ndarray, points: np.ndarray) -> np.ndarray:
    return np.asarray(points, dtype=np.float64) @ m[:3, :3].T + m[:3, 3]


def angle_of(m: np.ndarray) -> float:
    """Rotation angle (degrees) of a transform."""
    return math.degrees(math.acos(max(-1.0, min(1.0, (float(np.trace(m[:3, :3])) - 1) / 2))))


def shift_and_angle(m: np.ndarray, center) -> tuple[float, float]:
    """How far a transform moves ``center`` (Å) and its rotation angle (degrees), as ChimeraX reports a fit."""
    c = np.asarray(center, dtype=np.float64)
    return float(np.linalg.norm(apply(m, c[None])[0] - c)), angle_of(m)


def as_matrix(value: Any) -> np.ndarray:
    """A 4×4 rigid transform from JSON (4 rows of 4, 3 rows of 4, or 12 / 16 numbers row by row); ValueError when it
    is not a rotation and a translation."""
    try:
        a = np.asarray(value, dtype=np.float64)
    except (TypeError, ValueError):
        raise ValueError("The position must be a 4×4 or 3×4 matrix of numbers") from None
    if a.size == 16:
        a = a.reshape(4, 4)
    elif a.size == 12:
        a = np.vstack([a.reshape(3, 4), [0, 0, 0, 1]])
    else:
        raise ValueError("The position must be a 4×4 or 3×4 matrix of numbers")
    if not np.all(np.isfinite(a)) or not np.allclose(a[3], [0, 0, 0, 1], atol=1e-6):
        raise ValueError("The position must be a rigid transform (last row 0 0 0 1)")
    r = a[:3, :3]
    if not np.allclose(r @ r.T, np.eye(3), atol=2e-3) or np.linalg.det(r) < 0:
        raise ValueError("The position must be a rotation and a translation (no scaling, no mirror)")
    if np.abs(a[:3, 3]).max() > 1e5:
        raise ValueError("The translation of the position is out of range")
    u, _s, vt = np.linalg.svd(r)  # orthonormalised: what JavaScript sends is rounded
    a[:3, :3] = u @ vt
    return a


def matrix_rows(m: np.ndarray, digits: int = 6) -> list[list[float]]:
    return [[round(float(v), digits) for v in row] for row in m]


def is_identity(m: np.ndarray, tol: float = 1e-6) -> bool:
    return bool(np.allclose(m, np.eye(4), atol=tol))


def describe(m: np.ndarray) -> str:
    """The transform as ChimeraX prints it: the 3×4 matrix, then the axis, angle and shift along the axis."""
    lines = ["Matrix rotation and translation"]
    lines += ["  " + " ".join(f"{v:12.8f}" for v in m[i, :3]) + f" {m[i, 3]:12.8f}" for i in range(3)]
    angle = angle_of(m)
    if angle > 1e-6:
        w, v = np.linalg.eig(m[:3, :3])
        axis = np.real(v[:, int(np.argmin(np.abs(w - 1)))])
        r = m[:3, :3]
        if np.dot(axis, [r[2, 1] - r[1, 2], r[0, 2] - r[2, 0], r[1, 0] - r[0, 1]]) < 0:
            axis = -axis
        lines.append(f"  Axis {axis[0]:.8f} {axis[1]:.8f} {axis[2]:.8f}")
        lines.append(f"  Rotation angle (degrees) {angle:.8g}")
        lines.append(f"  Shift along axis {float(np.dot(axis, m[:3, 3])):.8g}")
    else:
        lines.append(f"  Shift {np.linalg.norm(m[:3, 3]):.8g} (no rotation)")
    return "\n".join(lines)


def random_rotation(rng: np.random.Generator) -> np.ndarray:
    """A uniformly random rotation (random unit quaternion)."""
    u1, u2, u3 = rng.random(3)
    q = (math.sqrt(1 - u1) * math.sin(2 * math.pi * u2), math.sqrt(1 - u1) * math.cos(2 * math.pi * u2),
         math.sqrt(u1) * math.sin(2 * math.pi * u3), math.sqrt(u1) * math.cos(2 * math.pi * u3))
    x, y, z, w = q
    m = np.eye(4)
    m[:3, :3] = [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                 [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                 [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]
    return m


# ---------------------------------------------------------------- target map
_NEIGHBOURS = [np.array(v, dtype=np.float64) for v in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))]


class Target:
    """The map fitted into: trilinear values and gradients at any point (0 outside the box)."""

    def __init__(self, vol: MapVolume):
        self.vol = vol
        self.data = np.ascontiguousarray(vol.data, dtype=np.float32)
        self.origin = np.asarray(vol.origin, dtype=np.float64)
        self.voxel = np.asarray(vol.voxel, dtype=np.float64)
        self.to_ijk = np.diag(1.0 / self.voxel)  # linear part of the Cartesian -> grid index transform
        self.n = np.array(vol.data.shape[::-1])

    def _at(self, ijk: np.ndarray) -> np.ndarray:
        from scipy.ndimage import map_coordinates
        return map_coordinates(self.data, [ijk[:, 2], ijk[:, 1], ijk[:, 0]], order=1, mode="constant", cval=0.0,
                               prefilter=False)

    def ijk(self, points: np.ndarray) -> np.ndarray:
        return (points - self.origin) / self.voxel

    def values(self, points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Values at the points and whether each one lies inside the map."""
        ijk = self.ijk(points)
        inside = np.all((ijk >= 0) & (ijk <= self.n - 1), axis=1)
        return self._at(ijk).astype(np.float64), inside

    def gradients(self, points: np.ndarray, with_values: bool = False):
        """Gradients (value per Å along x, y, z): central differences one voxel apart of the trilinear interpolation,
        the same as interpolating the central-difference gradients of the grid; with the values when asked. All the
        samples are interpolated in one call."""
        ijk = self.ijk(points)
        n = len(points)
        parts = [ijk + d for d in _NEIGHBOURS] + ([ijk] if with_values else [])
        s = self._at(np.concatenate(parts)).astype(np.float64)
        g = np.empty((n, 3))
        for a in range(3):
            g[:, a] = (s[2 * a * n:(2 * a + 1) * n] - s[(2 * a + 1) * n:(2 * a + 2) * n]) / (2.0 * self.voxel[a])
        return (g, s[6 * n:]) if with_values else g

    def level_bounds(self, level: float | None) -> tuple[np.ndarray, np.ndarray]:
        """Corners (Å) of the region above ``level`` (the whole box without a level or when nothing is above)."""
        lo, hi = self.origin, self.origin + (self.n - 1) * self.voxel
        if level is None:
            return lo, hi
        above = np.nonzero(self.data >= level)
        if not len(above[0]):
            return lo, hi
        ijk_lo = np.array([above[2].min(), above[1].min(), above[0].min()])
        ijk_hi = np.array([above[2].max(), above[1].max(), above[0].max()])
        return self.origin + ijk_lo * self.voxel, self.origin + ijk_hi * self.voxel


# ------------------------------------------------------------ moving points
def map_points(vol: MapVolume, level: float, max_points: int = MAX_POINTS) -> tuple[np.ndarray, np.ndarray]:
    """Grid points of a map at or above ``level`` (Å, the map's own coordinates) and their values, a regular subsample
    of at most ``max_points``."""
    idx = np.flatnonzero(vol.data.ravel() >= level)
    if not idx.size:
        raise ValueError(f"No point of the map is above the level {level:.4g}")
    if idx.size > max_points:
        idx = idx[np.linspace(0, idx.size - 1, max_points).astype(np.int64)]
    z, y, x = np.unravel_index(idx, vol.data.shape)
    points = np.asarray(vol.origin, dtype=np.float64) + np.stack([x, y, z], axis=1) * np.asarray(vol.voxel, dtype=np.float64)
    return points, vol.data.ravel()[idx].astype(np.float64)


def simulated_points(xyz: np.ndarray, weights: np.ndarray, resolution: float,
                     contour: float = 0.95) -> tuple[np.ndarray, np.ndarray]:
    """Points of the map simulated from atoms (ChimeraX's ``fitmap ... resolution r``): molmap on a grid of resolution
    / 3 padded by 3 × resolution, its points above the contour enclosing 95 % of it, with their values."""
    from cryoplug.masks import level_enclosing, molmap
    xyz = np.asarray(xyz, dtype=np.float64)
    step, pad = resolution / 3.0, 3.0 * resolution
    lo = xyz.min(axis=0) - pad
    shape = np.ceil((xyz.max(axis=0) + pad - lo) / step).astype(int) + 1
    grid = MapVolume(data=np.zeros(tuple(shape[::-1]), dtype=np.float32), voxel=np.full(3, step), origin=lo)
    sim = grid.like(molmap(grid, xyz, weights, resolution))
    level = level_enclosing(sim.data, contour)
    if level is None:
        raise ValueError("The simulated map is empty")
    return map_points(sim, level)


def default_level(vol: MapVolume) -> float:
    """Contour of a map when none is given: 0.5 for a mask (values 0-1), otherwise the level with 1 % of the voxels
    above it (ChimeraX's first contour)."""
    lo, hi = float(vol.data.min()), float(vol.data.max())
    if lo >= 0 and hi <= 1.0 and hi > 0.5:
        return 0.5
    sample = vol.data.ravel()[:: max(1, vol.data.size // 2_000_000)]
    return float(np.quantile(sample, 0.99))


# --------------------------------------------------------------- the fitting
def _coefficients(metric: str, w: np.ndarray | None, v: np.ndarray) -> np.ndarray | None:
    """d(metric)/d(value) per point, up to a positive factor (only directions matter)."""
    if metric == "sum product":
        return w
    if metric == "correlation about mean":
        v = v - v.mean()
    vv = float(v @ v)
    return w - (float(w @ v) / vv) * v if vv > 0 else w


def _gradient_and_torque(points, w, target: Target, cur: np.ndarray, metric: str, center, need_torque: bool):
    q = apply(cur, points)
    if metric == "sum product":
        g_scene, v = target.gradients(q), None
    else:
        g_scene, v = target.gradients(q, with_values=True)
    g = g_scene @ cur[:3, :3]  # gradient with respect to the points' own coordinates
    a = _coefficients(metric, w, v) if (metric != "sum product" or w is not None) else None
    if a is not None:
        g = g * a[:, None]
    grad = g.sum(axis=0)
    torque = _cross(points - center, g).sum(axis=0) if need_torque else None
    return grad, torque


def _cross(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Row-wise cross products (numpy.cross is slow on many small calls)."""
    return np.stack([a[:, 1] * b[:, 2] - a[:, 2] * b[:, 1], a[:, 2] * b[:, 0] - a[:, 0] * b[:, 2],
                     a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]], axis=1)


def _translation_step(points, w, target, cur, step, metric, center) -> np.ndarray:
    g, _ = _gradient_and_torque(points, w, target, cur, metric, center, False)
    gijk = target.to_ijk @ cur[:3, :3] @ g
    n = float(np.linalg.norm(gijk))
    return translation(g * (step / n)) if n > 0 else np.eye(4)


def _rotation_step(points, w, target, cur, step, metric, center) -> np.ndarray:
    _, tau = _gradient_and_torque(points, w, target, cur, metric, center, True)
    n = float(np.linalg.norm(tau))
    if len(points) == 1 or n == 0:
        return np.eye(4)
    axis = tau / n
    motion = _cross(np.broadcast_to(axis, points.shape), points - center) @ (target.to_ijk @ cur[:3, :3]).T
    largest = float(np.sqrt((motion ** 2).sum(axis=1)).max())
    return rotation(axis, math.degrees(step / largest), center) if largest > 0 else np.eye(4)


def _largest_ijk_motion(points, cur, seg, to_ijk) -> float:
    a = to_ijk @ (cur[:3, :3] @ seg[:3, :3] - cur[:3, :3])
    b = to_ijk @ cur[:3, :3] @ seg[:3, 3]
    d = points @ a.T + b
    return float(np.sqrt((d ** 2).sum(axis=1)).max())


def locate_maximum(points: np.ndarray, weights: np.ndarray | None, target: Target, metric: str = "overlap",
                   start: np.ndarray | None = None, max_steps: int = 2000, step_min: float = 0.01, step_max: float = 0.5,
                   shift: bool = True, rotate: bool = True, center=None) -> tuple[np.ndarray, dict[str, Any]]:
    """Gradient ascent of the metric from ``start``; returns the transform of the points' own coordinates at the
    maximum and the statistics of the fit."""
    me = METRICS[metric]
    pts = np.asarray(points, dtype=np.float64)
    w = None if weights is None else np.asarray(weights, dtype=np.float64)
    if w is None and me != "sum product":
        raise ValueError("Correlation needs weighted points (a map, or a model at a resolution)")
    if me == "correlation about mean":
        w = w - w.mean()
    start = np.eye(4) if start is None else np.asarray(start, dtype=np.float64)
    rc = pts.mean(axis=0) if center is None else np.asarray(center, dtype=np.float64)
    kinds = [k for k, on in ((_translation_step, shift), (_rotation_step, rotate)) if on]
    move, step, steps = np.eye(4), step_max, 0
    while kinds and steps < max_steps and step > step_min:
        seg = np.eye(4)
        for k in range(4):
            seg = seg @ kinds[k % len(kinds)](pts, w, target, start @ move @ seg, step, me, rc)
        steps += 4
        if _largest_ijk_motion(pts, start @ move, seg, target.to_ijk) < 0.25 * 4 * step:
            step *= 0.5
        else:
            step = min(step * 1.2, step_max)
        move = move @ seg
    total = start @ move
    stats = evaluate(pts, weights, target, total)
    stats["shift"], stats["angle"] = shift_and_angle(move, rc)
    stats["steps"] = steps
    return total, stats


def evaluate(points: np.ndarray, weights: np.ndarray | None, target: Target, m: np.ndarray,
             level: float | None = None) -> dict[str, Any]:
    """Scores of the points placed by ``m``: average map value (points inside the map), and with weights the overlap,
    correlation and correlation about the mean; the points below ``level`` (outside the contour)."""
    v, inside = target.values(apply(m, points))
    n_in = int(inside.sum())
    out: dict[str, Any] = {"points": int(len(points)), "points_in_map": n_in,
                           "average_map_value": float(v[inside].sum() / n_in) if n_in else 0.0}
    if weights is not None:
        w = np.asarray(weights, dtype=np.float64)
        olap, nw, nv = float(w @ v), float(w @ w), float(v @ v)
        n = len(w)
        mw, mv = w.sum() / n, v.sum() / n
        d2 = max((nw - n * mw * mw) * (nv - n * mv * mv), 0.0)
        out.update(overlap=olap, correlation=olap / math.sqrt(nw * nv) if nw * nv > 0 else 0.0,
                   cam=(olap - n * mw * mv) / math.sqrt(d2) if d2 > 0 else 0.0)
    if level is not None:
        out["contour_level"] = float(level)
        out["outside_contour"] = int(np.count_nonzero(v < level))
        out["inside_fraction"] = 1.0 - out["outside_contour"] / max(len(points), 1)
    return out


def score_of(stats: dict[str, Any]) -> float:
    return float(stats["correlation"]) if "correlation" in stats else float(stats["average_map_value"])


def _subsample(points, weights, n):
    if len(points) <= n:
        return points, weights
    idx = np.linspace(0, len(points) - 1, n).astype(np.int64)
    return points[idx], (None if weights is None else weights[idx])


def fit_search(points: np.ndarray, weights: np.ndarray | None, target: Target, n: int, metric: str = "overlap",
               start: np.ndarray | None = None, radius: float | None = None, level: float | None = None,
               seed: int = 0, angle_tolerance: float = 6.0, shift_tolerance: float = 3.0, min_inside: float = 0.1,
               refine: int = 5, rotate: bool = True, shift: bool = True, workers: int = 1) -> list[dict[str, Any]]:
    """Fits from ``n`` random placements (within ``radius`` Å of the starting position, otherwise anywhere in the
    region of the target above ``level``): unique fits (6°, 3 Å apart), best first, each {matrix, stats, hits}. The
    best ``refine`` are optimised again with all the points. The placements are drawn first and optimised on
    ``workers`` threads (the interpolation releases the GIL): the result does not depend on their number."""
    from concurrent.futures import ThreadPoolExecutor
    if not 1 <= n <= MAX_SEARCH:
        raise ValueError(f"A search takes 1 to {MAX_SEARCH} random placements")
    rng = np.random.default_rng(seed)
    start = np.eye(4) if start is None else np.asarray(start, dtype=np.float64)
    pts = np.asarray(points, dtype=np.float64)
    center = pts.mean(axis=0)
    placed_center = apply(start, center[None])[0]
    lo, hi = target.level_bounds(level)
    few, few_w = _subsample(pts, weights, SEARCH_POINTS)
    starts = []
    for _ in range(n):
        if not shift:
            where = placed_center
        elif radius:
            d = rng.normal(size=3)
            where = placed_center + radius * rng.random() * d / np.linalg.norm(d)
        else:
            where = lo + rng.random(3) * (hi - lo)
        rot = random_rotation(rng)[:3, :3] if rotate else start[:3, :3]
        tf = np.eye(4)
        tf[:3, :3] = rot
        starts.append(translation(where) @ tf @ translation(-center))

    def optimise(args):
        p, pw, m0 = args
        return locate_maximum(p, pw, target, metric, start=m0, rotate=rotate, shift=shift)

    # threads pay off with many points; with few, Python's own work between the interpolations dominates
    with ThreadPoolExecutor(max_workers=max(1, int(workers)) if len(few) >= 2000 else 1) as pool:
        results = list(pool.map(optimise, [(few, few_w, tf) for tf in starts]))
        found: list[dict[str, Any]] = []
        for m, stats in results:
            here = apply(m, center[None])[0]
            for f in found:
                if (np.linalg.norm(apply(f["matrix"], center[None])[0] - here) < shift_tolerance
                        and angle_of(m @ np.linalg.inv(f["matrix"])) < angle_tolerance):
                    f["hits"] += 1
                    break
            else:
                found.append({"matrix": m, "stats": stats, "hits": 1})
        found.sort(key=lambda f: score_of(f["stats"]), reverse=True)
        best = found[:refine]  # every point, from the position found
        for f, (m, stats) in zip(best, pool.map(optimise, [(pts, weights, f["matrix"]) for f in best])):
            f["matrix"], f["stats"] = m, stats
    for f in found:  # scored with every point, the motion counted from the starting position
        motion = f["matrix"] @ np.linalg.inv(start)
        f["stats"].update(evaluate(pts, weights, target, f["matrix"], level))
        f["stats"]["shift"], f["stats"]["angle"] = shift_and_angle(motion, placed_center)
    kept = [f for f in found if level is None or f["stats"].get("inside_fraction", 1.0) >= min_inside]
    kept.sort(key=lambda f: score_of(f["stats"]), reverse=True)
    return kept


def fit(points: np.ndarray, weights: np.ndarray | None, target: Target, metric: str = "overlap",
        start: np.ndarray | None = None, search: int = 0, radius: float | None = None, level: float | None = None,
        shift: bool = True, rotate: bool = True, seed: int = 0, workers: int = 1) -> dict[str, Any]:
    """Local fit from ``start`` (or a search): {matrix, stats, before, fits}. ``before`` scores the start."""
    start = np.eye(4) if start is None else np.asarray(start, dtype=np.float64)
    before = evaluate(points, weights, target, start, level)
    if search:
        fits = fit_search(points, weights, target, search, metric, start=start, radius=radius, level=level, seed=seed,
                          rotate=rotate, shift=shift, workers=workers)
        if not fits:
            raise ValueError(f"None of the {search} placements kept enough points inside the contour of the map")
        best = fits[0]
        return {"matrix": best["matrix"], "stats": best["stats"], "before": before, "fits": fits}
    m, stats = locate_maximum(points, weights, target, metric, start=start, shift=shift, rotate=rotate)
    stats.update({k: v for k, v in evaluate(points, weights, target, m, level).items() if k not in stats})
    return {"matrix": m, "stats": stats, "before": before, "fits": []}


def message(what: str, into: str, result: dict[str, Any]) -> str:
    """Report of a fit, in the words of ChimeraX's log."""
    s, b = result["stats"], result["before"]
    lines = [f"Fit {what} in map {into} using {s['points']:,} points"]
    if "correlation" in s:
        lines.append(f"  correlation = {s['correlation']:.4g}, correlation about mean = {s['cam']:.4g}, overlap = {s['overlap']:.4g} "
                     f"(before: {b['correlation']:.4g}, {b['cam']:.4g}, {b['overlap']:.4g})")
    else:
        lines.append(f"  average map value = {s['average_map_value']:.4g} (before: {b['average_map_value']:.4g})")
    lines.append(f"  steps = {s['steps']}, shift = {s['shift']:.3g} Å, angle = {s['angle']:.3g} degrees")
    if "outside_contour" in s and "correlation" not in s:  # atoms (ChimeraX counts them only)
        lines.append(f"  atoms outside contour = {s['outside_contour']:,} of {s['points']:,}, contour level = {s['contour_level']:.5g}")
    return "\n".join(lines)


# ------------------------------------------------------------- placed objects
def place_map(moving: MapVolume, m: np.ndarray, grid: MapVolume | None = None) -> MapVolume:
    """A map moved by ``m`` (its own coordinates -> placed), resampled (trilinear) on ``grid``, or on its own grid
    when there is none: a pure translation then only changes the origin (no interpolation)."""
    from scipy.ndimage import affine_transform
    m = np.asarray(m, dtype=np.float64)
    if grid is None:
        if angle_of(m) < 1e-6:
            return MapVolume(data=moving.data.copy(), voxel=np.array(moving.voxel, dtype=np.float64),
                             origin=np.asarray(moving.origin, dtype=np.float64) + m[:3, 3])
        grid = moving
    r_inv = m[:3, :3].T
    vin, vout = np.asarray(moving.voxel, dtype=np.float64), np.asarray(grid.voxel, dtype=np.float64)
    # output grid index (x, y, z) -> placed position -> the moving map's own position -> its grid index
    lin = np.diag(1.0 / vin) @ r_inv @ np.diag(vout)
    off = np.diag(1.0 / vin) @ (r_inv @ (np.asarray(grid.origin, dtype=np.float64) - m[:3, 3]) - np.asarray(moving.origin, dtype=np.float64))
    rev = np.eye(3)[::-1]  # arrays are z, y, x
    data = affine_transform(np.asarray(moving.data, dtype=np.float32), rev @ lin @ rev, offset=off[::-1],
                            output_shape=grid.data.shape, order=1, mode="constant", cval=0.0, prefilter=False)
    return MapVolume(data=data.astype(np.float32), voxel=vout.copy(), origin=np.asarray(grid.origin, dtype=np.float64).copy())


def place_structure(st, m: np.ndarray) -> None:
    """Move every model of a gemmi structure by ``m`` (positions and anisotropic B-factors)."""
    import gemmi
    tr = gemmi.Transform()
    tr.mat.fromlist(np.asarray(m, dtype=np.float64)[:3, :3].tolist())
    tr.vec.fromlist(np.asarray(m, dtype=np.float64)[:3, 3].tolist())
    for model in st:
        model.transform_pos_and_adp(tr)
