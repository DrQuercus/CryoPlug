"""Masks drawn by hand in the 3D viewer, the way Chimera users make them: spheres, boxes and cylinders placed on the
map, a region of connected density picked with the mouse, a zone around atoms, the density simulated from a model
(ChimeraX's molmap) cut at a contour, the density above a level or an existing mask, each added, erased or intersected
in turn; then grown by a dilation and given a soft edge (in Å).

A *recipe* holds the operations in physical coordinates (Å, those of the map, origin included), so that the same
recipe gives the quick preview of the viewer, on a binned copy of the map, and the mask of the job, on the full grid.
"""
from __future__ import annotations

import math
from typing import Any, Callable

import numpy as np

from cryoplug.masks import MOLMAP_CONTOUR, level_enclosing, molmap, ndimage, parse_selection, smooth_map, soft_edge
from cryoplug.mrc import MapVolume, zone_mask

SHAPES = ("sphere", "box", "cylinder", "region", "zone", "molmap", "map", "mask")
MODES = ("add", "erase", "keep")
MAX_OPS = 500
MAX_SIZE = 5000.0  # Å: any radius or side above this is a mistake


def _vec(value: Any, what: str) -> list[float]:
    try:
        out = [float(v) for v in value]
    except (TypeError, ValueError):
        raise ValueError(f"{what}: three numbers expected") from None
    if len(out) != 3 or not all(math.isfinite(v) for v in out):
        raise ValueError(f"{what}: three numbers expected")
    return out


def _num(value: Any, what: str, lo: float = 0.0, hi: float = MAX_SIZE) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{what}: a number expected") from None
    if not math.isfinite(out) or not lo <= out <= hi:
        raise ValueError(f"{what}: between {lo:g} and {hi:g}")
    return out


def validate(recipe: Any) -> dict[str, Any]:
    """A clean copy of a recipe (raises ValueError with a readable message)."""
    if not isinstance(recipe, dict):
        raise ValueError("The recipe must be a JSON object")
    ops_in = recipe.get("ops") or []
    if not isinstance(ops_in, list):
        raise ValueError("ops must be a list")
    if len(ops_in) > MAX_OPS:
        raise ValueError(f"At most {MAX_OPS} operations")
    ops = []
    for n, op in enumerate(ops_in, 1):
        if not isinstance(op, dict):
            raise ValueError(f"Operation {n}: an object expected")
        shape, mode = op.get("shape"), op.get("mode", "add")
        if shape not in SHAPES:
            raise ValueError(f"Operation {n}: unknown shape {shape!r} ({', '.join(SHAPES)})")
        if mode not in MODES:
            raise ValueError(f"Operation {n}: unknown mode {mode!r} ({', '.join(MODES)})")
        clean: dict[str, Any] = {"shape": shape, "mode": mode}
        where = f"Operation {n} ({shape})"
        if shape == "sphere":
            clean.update(center=_vec(op.get("center"), f"{where} centre"), radius=_num(op.get("radius"), f"{where} radius", 0.1))
        elif shape == "box":
            clean.update(center=_vec(op.get("center"), f"{where} centre"),
                         size=[_num(s, f"{where} size", 0.1) for s in _vec(op.get("size"), f"{where} size")])
        elif shape == "cylinder":
            clean.update(start=_vec(op.get("start"), f"{where} start"), end=_vec(op.get("end"), f"{where} end"),
                         radius=_num(op.get("radius"), f"{where} radius", 0.1))
            if np.allclose(clean["start"], clean["end"]):
                raise ValueError(f"{where}: the two ends are the same point")
        elif shape == "region":
            clean.update(seed=_vec(op.get("seed"), f"{where} point"), level=_num(op.get("level"), f"{where} level", -1e30, 1e30),
                         lowpass=_num(op.get("lowpass", 0), f"{where} low-pass", 0, 100))
        elif shape == "map":
            clean.update(level=_num(op.get("level"), f"{where} level", -1e30, 1e30),
                         lowpass=_num(op.get("lowpass", 0), f"{where} low-pass", 0, 100))
        elif shape in ("zone", "molmap"):
            model = str(op.get("model") or "")
            if not model:
                raise ValueError(f"{where}: no model")
            selection = str(op.get("selection") or "")
            parse_selection(selection)  # readable error now rather than in the job
            clean.update(model=model, selection=selection)
            if op.get("matrix") is not None:  # the model moved in the viewer: where it is seen on the map
                from cryoplug import fitting
                try:
                    clean["matrix"] = fitting.matrix_rows(fitting.as_matrix(op["matrix"]))
                except ValueError as exc:
                    raise ValueError(f"{where}: {exc}") from None
            if shape == "zone":
                clean["radius"] = _num(op.get("radius", 3), f"{where} radius", 0.1, 100)
            else:
                clean.update(resolution=_num(op.get("resolution", 10), f"{where} resolution", 1, 60),
                             enclose=_num(op.get("enclose", 100 * MOLMAP_CONTOUR), f"{where} contour (% of the density)", 10, 99.99))
        elif shape == "mask":
            path = str(op.get("path") or "")
            if not path:
                raise ValueError(f"{where}: no mask file")
            clean.update(path=path)
        if op.get("label"):
            clean["label"] = str(op["label"])[:80]
        ops.append(clean)
    out = {"version": 1, "ops": ops,
           "dilate": _num(recipe.get("dilate", 0), "Dilation", 0, 100),
           "soft": _num(recipe.get("soft", 6), "Soft edge", 0, 100)}
    return out


def describe(op: dict[str, Any]) -> str:
    """One line for the job report."""
    verb = {"add": "Add", "erase": "Erase", "keep": "Keep only"}[op["mode"]]
    s = op["shape"]
    if s == "sphere":
        return f"{verb} sphere r = {op['radius']:.1f} Å at ({', '.join(f'{c:.1f}' for c in op['center'])})"
    if s == "box":
        return f"{verb} box {' × '.join(f'{c:.0f}' for c in op['size'])} Å at ({', '.join(f'{c:.1f}' for c in op['center'])})"
    if s == "cylinder":
        length = float(np.linalg.norm(np.subtract(op["end"], op["start"])))
        return f"{verb} cylinder r = {op['radius']:.1f} Å, {length:.0f} Å long"
    if s == "region":
        return f"{verb} connected region at level {op['level']:.4g}" + (f" (low-pass {op['lowpass']:g} Å)" if op["lowpass"] else "")
    if s == "map":
        return f"{verb} density above {op['level']:.4g}" + (f" (low-pass {op['lowpass']:g} Å)" if op["lowpass"] else "")
    moved = " (moved in the viewer)" if op.get("matrix") else ""
    if s == "zone":
        return f"{verb} {op['radius']:g} Å around {op['selection'] or 'all atoms'} of {op['model'].split('/')[-1]}{moved}"
    if s == "molmap":
        return (f"{verb} model map of {op['selection'] or 'all atoms'} of {op['model'].split('/')[-1]}{moved} at {op['resolution']:g} Å "
                f"(molmap), contour enclosing {op['enclose']:g} % of its density")
    return f"{verb} mask {op['path'].split('/')[-1]}"


# ------------------------------------------------------------------ drawing
class Grid:
    """Voxel coordinates of a map: x[i], y[j], z[k] in Å (axis-aligned maps)."""

    def __init__(self, vol: MapVolume):
        self.vol = vol
        nz, ny, nx = vol.data.shape
        self.shape = (nz, ny, nx)
        self.axes = [vol.origin[a] + vol.voxel[a] * np.arange(n) for a, n in enumerate((nx, ny, nz))]  # x, y, z

    def window(self, lo: np.ndarray, hi: np.ndarray) -> tuple[slice, slice, slice] | None:
        """Index slices (z, y, x) of the voxels between two corners (Å); None when outside the box."""
        out = []
        for a in (2, 1, 0):
            ax = self.axes[a]
            i0 = int(np.searchsorted(ax, lo[a], side="left"))
            i1 = int(np.searchsorted(ax, hi[a], side="right"))
            if i1 <= i0:
                return None
            out.append(slice(i0, i1))
        return tuple(out)  # type: ignore[return-value]

    def coords(self, win: tuple[slice, slice, slice]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Broadcastable z, y, x coordinates of a window."""
        z = self.axes[2][win[0]][:, None, None]
        y = self.axes[1][win[1]][None, :, None]
        x = self.axes[0][win[2]][None, None, :]
        return z, y, x


def _shape_mask(grid: Grid, op: dict[str, Any]) -> tuple[tuple[slice, slice, slice] | None, np.ndarray | None]:
    """Voxels of a geometric shape: (window, boolean array of the window)."""
    s = op["shape"]
    if s == "sphere":
        c, r = np.array(op["center"]), op["radius"]
        win = grid.window(c - r, c + r)
        if win is None:
            return None, None
        z, y, x = grid.coords(win)
        return win, (x - c[0]) ** 2 + (y - c[1]) ** 2 + (z - c[2]) ** 2 <= r * r
    if s == "box":
        c, half = np.array(op["center"]), np.array(op["size"]) / 2
        win = grid.window(c - half, c + half)
        if win is None:
            return None, None
        z, y, x = grid.coords(win)
        return win, (np.abs(x - c[0]) <= half[0]) & (np.abs(y - c[1]) <= half[1]) & (np.abs(z - c[2]) <= half[2])
    if s == "cylinder":
        p0, p1, r = np.array(op["start"]), np.array(op["end"]), op["radius"]
        win = grid.window(np.minimum(p0, p1) - r, np.maximum(p0, p1) + r)
        if win is None:
            return None, None
        z, y, x = grid.coords(win)
        d = p1 - p0
        length2 = float(d @ d)
        rx, ry, rz = x - p0[0], y - p0[1], z - p0[2]
        t = (rx * d[0] + ry * d[1] + rz * d[2]) / length2
        perp2 = rx ** 2 + ry ** 2 + rz ** 2 - t ** 2 * length2
        return win, (t >= 0) & (t <= 1) & (perp2 <= r * r)
    raise ValueError(s)


def _nearest_voxel(grid: Grid, point: list[float]) -> tuple[int, int, int]:
    idx = []
    for a in (2, 1, 0):
        ax = grid.axes[a]
        idx.append(int(np.clip(np.rint((point[a] - ax[0]) / (ax[1] - ax[0]) if len(ax) > 1 else 0), 0, len(ax) - 1)))
    return idx[0], idx[1], idx[2]


def region(smooth: np.ndarray, level: float, grid: Grid, seed: list[float], search: float = 4.0) -> np.ndarray:
    """The connected region of density above ``level`` holding the picked point (or the strongest voxel above the level
    within ``search`` Å of it: a point picked on the isosurface lies at the level, just inside or outside)."""
    nd = ndimage()
    above = smooth >= level
    k, j, i = _nearest_voxel(grid, seed)
    reach = max(1, int(math.ceil(search / float(np.min(grid.vol.voxel)))))
    sl = tuple(slice(max(0, v - reach), v + reach + 1) for v in (k, j, i))
    local = np.where(above[sl], smooth[sl], -np.inf)
    if not np.isfinite(local).any():
        return np.zeros_like(above)
    best = np.unravel_index(int(np.argmax(local)), local.shape)
    start = tuple(int(s.start + b) for s, b in zip(sl, best))
    labels, _n = nd.label(above)
    return labels == labels[start]


def draw(recipe: dict[str, Any], base: MapVolume, models: Callable[[str, str], np.ndarray] | None = None,
         masks: Callable[[str], np.ndarray] | None = None,
         atoms: Callable[[str, str], tuple[np.ndarray, np.ndarray]] | None = None,
         molmaps: Callable[[str, str, float], np.ndarray] | None = None) -> np.ndarray:
    """The binary core of a validated recipe on the grid of ``base`` (z, y, x). ``models(path, selection)`` gives atom
    positions (N, 3), ``atoms(path, selection)`` positions and molmap weights, ``molmaps(path, selection, resolution)``
    the simulated map of the atoms on the grid of ``base`` (computed from ``atoms`` without it: a cache for the
    preview), ``masks(path)`` an existing mask resampled on the grid of ``base``."""
    grid = Grid(base)
    core = np.zeros(base.data.shape, dtype=bool)
    smoothed: dict[float, np.ndarray] = {}

    def smooth(lowpass: float) -> np.ndarray:
        if lowpass not in smoothed:
            smoothed[lowpass] = smooth_map(base, lowpass) if lowpass > 0 else base.data
        return smoothed[lowpass]

    for op in recipe["ops"]:
        s, mode = op["shape"], op["mode"]
        win, part = None, None
        if s in ("sphere", "box", "cylinder"):
            win, part = _shape_mask(grid, op)
            if win is None:
                if mode == "keep":
                    core[:] = False
                continue
        elif s == "region":
            part = region(smooth(op["lowpass"]), op["level"], grid, op["seed"])
        elif s == "map":
            part = smooth(op["lowpass"]) >= op["level"]
        elif s == "zone":
            if models is None:
                raise ValueError("Zones need the model")
            xyz = _placed(op, models(op["model"], op["selection"]))
            part = zone_mask(base, xyz, op["radius"]) if len(xyz) else np.zeros_like(core)
        elif s == "molmap":
            if molmaps is not None and not op.get("matrix"):
                density = molmaps(op["model"], op["selection"], op["resolution"])
            elif atoms is not None:
                xyz, weights = atoms(op["model"], op["selection"])
                density = molmap(base, _placed(op, xyz), weights, op["resolution"])
            else:
                raise ValueError("Model maps need the model")
            level = level_enclosing(density, op["enclose"] / 100.0)
            part = density >= level if level is not None else np.zeros_like(core)
        elif s == "mask":
            if masks is None:
                raise ValueError("Masks need their file")
            part = masks(op["path"]) >= 0.5
        if win is not None:
            if mode == "add":
                core[win] |= part
            elif mode == "erase":
                core[win] &= ~part
            else:
                keep = np.zeros_like(core)
                keep[win] = part
                core &= keep
        elif mode == "add":
            core |= part
        elif mode == "erase":
            core &= ~part
        else:
            core &= part
    return core


def _placed(op: dict[str, Any], xyz: np.ndarray) -> np.ndarray:
    """Atom positions where the viewer showed the model (its transform relative to the map), as they are otherwise."""
    if not op.get("matrix") or not len(xyz):
        return xyz
    from cryoplug import fitting
    return fitting.apply(np.asarray(op["matrix"], dtype=np.float64), xyz)


def finish(core: np.ndarray, voxel: np.ndarray, recipe: dict[str, Any]) -> np.ndarray:
    """The soft mask: the core grown by the dilation, then a raised-cosine edge."""
    return soft_edge(core, voxel, recipe["dilate"], recipe["soft"])


def preview(core: np.ndarray, voxel: np.ndarray, recipe: dict[str, Any]) -> np.ndarray:
    """What the viewer shows while drawing: the core grown by the dilation, as 0/1 (its surface at 0.5)."""
    if recipe["dilate"] <= 0 or not core.any():
        return core.astype(np.float32)
    return (soft_edge(core, voxel, recipe["dilate"], 0.0) >= 0.5).astype(np.float32)


def atom_positions(path: str, selection: str = "") -> np.ndarray:
    """Heavy-atom positions of the selected chains / residues of a model."""
    return atom_weights(path, selection)[0]


def atom_weights(path: str, selection: str = "") -> tuple[np.ndarray, np.ndarray]:
    """Heavy-atom positions of the selected chains / residues of a model and their molmap weights (atomic numbers)."""
    import gemmi

    from cryoplug.masks import select_atoms
    st = gemmi.read_structure(str(path))
    st.setup_entities()
    sel = select_atoms(st, selection)
    return np.asarray(sel["xyz"], dtype=np.float64).reshape(-1, 3), np.asarray(sel["weights"], dtype=np.float64)


def resample_mask(path: str, like: MapVolume) -> np.ndarray:
    """An existing mask on the grid of ``like`` (same box: as it is; otherwise sampled at the voxel positions)."""
    m = MapVolume.read(path)
    if m.data.shape == like.data.shape and np.allclose(m.voxel, like.voxel, rtol=1e-3):
        return m.data
    grid = Grid(like)
    z, y, x = np.meshgrid(grid.axes[2], grid.axes[1], grid.axes[0], indexing="ij")
    values = m.interpolate(np.stack([x.ravel(), y.ravel(), z.ravel()], 1), outside=0.0)
    return values.reshape(like.data.shape).astype(np.float32)
