"""Local-resolution analysis shared by the jobs: values in the molecule, per chain and per residue, and the
colour range used by the 3D viewer."""
from __future__ import annotations

from typing import Any

import gemmi
import numpy as np

from cryoplug.mrc import MapVolume, zone_mask

# Values outside this range (in Å) are background fill written by the programs (0, Nyquist cap, box size).
VALID_RANGE = (0.0, 100.0)
BACKBONE_ATOMS = ("CA", "P", "C4'")


def valid(values: np.ndarray) -> np.ndarray:
    v = np.asarray(values, dtype=np.float64).ravel()
    return v[np.isfinite(v) & (v > VALID_RANGE[0]) & (v < VALID_RANGE[1])]


def region(locres: MapVolume, mask: MapVolume | None = None, xyz: np.ndarray | None = None,
           density: MapVolume | None = None) -> tuple[np.ndarray | None, str]:
    """Voxels where the statistics are taken, and how they were chosen: inside a mask, near the atoms, or in
    the molecule found in the density (15 Å low-pass, Otsu level)."""
    if mask is not None:
        if mask.data.shape == locres.data.shape:
            return mask.data >= 0.5, "inside the mask"
    if xyz is not None and len(xyz):
        return zone_mask(locres, xyz, 3.0), "within 3 Å of the model atoms"
    if density is not None and density.data.shape == locres.data.shape:
        from cryoplug import masks as mk
        smooth = mk.smooth_map(density, 15.0)
        level = mk.otsu_threshold(mk.sample_values(smooth))
        return smooth >= level, "in the molecule (density above the Otsu level after a 15 Å low-pass)"
    return None, "in the whole box"


def summary(values: np.ndarray) -> dict[str, float]:
    v = valid(values)
    if not v.size:
        return {"n": 0}
    p = np.percentile(v, [5, 25, 50, 75, 95])
    return {"n": int(v.size), "min": float(v.min()), "p5": float(p[0]), "p25": float(p[1]), "median": float(p[2]),
            "p75": float(p[3]), "p95": float(p[4]), "max": float(v.max())}


def display_range(stats: dict[str, float]) -> list[float] | None:
    """Colour scale of the viewer: 5th to 95th percentile in the molecule, rounded to 0.1 Å."""
    if not stats.get("n"):
        return None
    lo, hi = round(stats["p5"], 1), round(stats["p95"], 1)
    return [lo, hi if hi > lo else lo + 0.1]


def histogram(values: np.ndarray, bins: int = 60) -> dict[str, list[float]]:
    """Distribution as the percentage of voxels per resolution bin."""
    v = valid(values)
    if not v.size:
        return {"x": [], "y": []}
    lo, hi = np.percentile(v, [0.5, 99.5])
    if hi <= lo:
        hi = lo + 0.1
    counts, edges = np.histogram(v, bins=bins, range=(float(lo), float(hi)))
    return {"x": ((edges[:-1] + edges[1:]) / 2).round(3).tolist(), "y": (100.0 * counts / v.size).round(4).tolist()}


def per_residue(locres: MapVolume, st: gemmi.Structure) -> list[dict[str, Any]]:
    """Local resolution at the backbone atom (CA, P or C4') of every residue, or at its first atom."""
    keys, xyz = [], []
    for chain in st[0]:
        for res in chain:
            atom = next((a for name in BACKBONE_ATOMS for a in res if a.name == name), None) or (res[0] if len(res) else None)
            if atom is None:
                continue
            keys.append((chain.name, res.seqid.num, res.seqid.icode.strip(), res.name))
            xyz.append((atom.pos.x, atom.pos.y, atom.pos.z))
    if not xyz:
        return []
    values = locres.interpolate(np.array(xyz), outside=np.nan)
    rows = []
    for (chain, num, icode, name), value in zip(keys, values):
        ok = bool(np.isfinite(value) and VALID_RANGE[0] < value < VALID_RANGE[1])
        rows.append({"chain": chain, "num": num, "icode": icode, "name": name, "value": float(value) if ok else None})
    return rows


def per_chain(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Median and spread of the residue values of each chain, best resolved chains first."""
    chains: dict[str, list[float]] = {}
    order: list[str] = []
    for r in rows:
        if r["chain"] not in chains:
            chains[r["chain"]] = []
            order.append(r["chain"])
        if r["value"] is not None:
            chains[r["chain"]].append(r["value"])
    out = []
    for name in order:
        v = np.array(chains[name])
        if not v.size:
            continue
        p10, p50, p90 = np.percentile(v, [10, 50, 90])
        out.append({"chain": name, "n": int(v.size), "median": float(p50), "best": float(p10), "worst": float(p90)})
    return sorted(out, key=lambda c: c["median"])


def with_bfactors(st: gemmi.Structure, rows: list[dict[str, Any]]) -> gemmi.Structure:
    """Copy of the model whose B-factor column holds the local resolution of each residue (for ChimeraX,
    Coot or PyMOL colouring); residues outside the map get 0."""
    out = st.clone()
    values = {(r["chain"], r["num"], r["icode"]): r["value"] for r in rows}
    for chain in out[0]:
        for res in chain:
            value = values.get((chain.name, res.seqid.num, res.seqid.icode.strip()))
            for atom in res:
                atom.b_iso = float(value) if value is not None else 0.0
    return out
