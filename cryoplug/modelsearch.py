"""Compare models built automatically into the same density, and combine them (CryoAtom2 enhanced).

Every model is scored against one reference map: the experimental map, even when the model was built into another
map (re-sharpened, LocScale, EMReady...), so that a map processed into "cleaner" density earns nothing by itself.

* Fitted residues: residues whose atoms have a mean Q-score (Pintilie et al. 2020) at least ``fitted_cutoff``,
  about 0.2 below the value expected at the map's resolution. They measure completeness and correctness together:
  a residue built in noise, or with the wrong side chain, has a low Q-score.
* Duplicated residues: two Cα (or P) atoms of residues that are not neighbours closer than 2.5 Å, i.e. the same
  density traced twice. They are counted once in the score: ``score = fitted - duplicated fitted / 2``.
* Fragments: stretches of residues connected by a peptide (C-N) or phosphodiester (O3'-P) bond. Alpha helices built
  as many short disconnected pieces are the sign of a map in the wrong hand (CryoAtom2 manual).

``combine`` adds to the best model the fragments of other models that fit where it has nothing, the way
phenix.combine_models works by segment: each fragment must fit (Q-score) and keep clear of the residues already there.
"""
from __future__ import annotations

import math
from typing import Any

import gemmi
import numpy as np
from scipy.spatial import cKDTree

from cryoplug.analysis import cc_mask, expected_q, q_scores
from cryoplug.modelio import WATER_NAMES, atom_arrays
from cryoplug.mrc import MapVolume

CONNECT = 2.1        # Å: peptide C-N or O3'-P bond between consecutive residues
DUPLICATE = 2.5      # Å between the Cα / P atoms of residues that are not neighbours: the same density traced twice
SHORT = 10           # residues: a fragment shorter than this is "short"
CLEARANCE = 3.0      # Å: a fragment added by combine keeps its Cα / P atoms this far from those already there
CHAIN_NAMES = [*"ABCDEFGHIJKLMNOPQRSTUVWXYZ", *"abcdefghijklmnopqrstuvwxyz", *"0123456789"]


def fitted_cutoff(resolution: float) -> float:
    """Mean Q-score of a residue's atoms from which it counts as fitted to the density."""
    return max(0.15, expected_q(resolution) - 0.2)


def _anchor(res: gemmi.Residue) -> gemmi.Atom | None:
    return res.find_atom("CA", "*") or res.find_atom("P", "*")


def _anchor_xyz(res: gemmi.Residue) -> list[float]:
    pos = _anchor(res).pos
    return [pos.x, pos.y, pos.z]


def _bonded(a: gemmi.Residue, b: gemmi.Residue) -> bool:
    """Whether two consecutive residues of a chain are covalently joined (or close enough, when atoms are missing)."""
    for first, second, limit in (("C", "N", CONNECT), ("O3'", "P", CONNECT), ("O3*", "P", CONNECT)):
        x, y = a.find_atom(first, "*"), b.find_atom(second, "*")
        if x and y:
            return x.pos.dist(y.pos) <= limit
    x, y = _anchor(a), _anchor(b)
    if x and y:
        return x.pos.dist(y.pos) <= (4.3 if x.name == "CA" else 8.0)
    return False


def polymer_residues(st: gemmi.Structure) -> list[tuple[str, int, gemmi.Residue]]:
    """(chain name, index in the chain, residue) of the residues with a Cα or P atom (waters and ligands left out)."""
    out = []
    for chain in st[0]:
        for i, res in enumerate(chain):
            if res.name in WATER_NAMES or _anchor(res) is None:
                continue
            out.append((chain.name, i, res))
    return out


def fragments(st: gemmi.Structure) -> list[list[tuple[str, int]]]:
    """Continuous stretches of bonded residues, as (chain name, residue index) lists."""
    out: list[list[tuple[str, int]]] = []
    for chain in st[0]:
        current: list[tuple[str, int]] = []
        previous = None
        for i, res in enumerate(chain):
            if res.name in WATER_NAMES or _anchor(res) is None:
                continue
            if previous is not None and current and _bonded(previous, res):
                current.append((chain.name, i))
            else:
                if current:
                    out.append(current)
                current = [(chain.name, i)]
            previous = res
        if current:
            out.append(current)
    return out


def residue_q(st: gemmi.Structure, vol: MapVolume) -> dict[tuple[str, int], float]:
    """Mean Q-score of the heavy atoms of each residue, by (chain name, residue index)."""
    atoms = atom_arrays(st)
    if not len(atoms["xyz"]):
        return {}
    q = q_scores(vol, atoms["xyz"])
    index = {}
    for chain in st[0]:
        for i, res in enumerate(chain):
            index[(chain.name, res.seqid.num, res.seqid.icode.strip(), res.name)] = i
    sums: dict[tuple[str, int], list[float]] = {}
    for key, value in zip(atoms["keys"], q, strict=True):
        if math.isnan(value):
            continue
        i = index.get(key[:4])
        if i is not None:
            sums.setdefault((key[0], i), []).append(float(value))
    return {k: float(np.mean(v)) for k, v in sums.items()}


def duplicated(st: gemmi.Structure) -> set[tuple[str, int]]:
    """Residues whose Cα / P atom is within 2.5 Å of the Cα / P atom of a residue that is not its neighbour."""
    residues = polymer_residues(st)
    if len(residues) < 2:
        return set()
    xyz = np.array([_anchor_xyz(r) for _, _, r in residues])
    out: set[tuple[str, int]] = set()
    for i, j in cKDTree(xyz).query_pairs(DUPLICATE):
        (ci, ii, _), (cj, ij, _) = residues[i], residues[j]
        if ci == cj and abs(ii - ij) <= 1:
            continue
        out.add((ci, ii))
        out.add((cj, ij))
    return out


def score_model(st: gemmi.Structure, vol: MapVolume, resolution: float) -> dict[str, Any]:
    """Fit of a model to the reference map: fitted and duplicated residues, Q-score, CC (mask), fragments and the
    score used to rank models. ``per_residue`` (Q by residue) is kept for ``combine``."""
    residues = polymer_residues(st)
    n = len(residues)
    cut = fitted_cutoff(resolution)
    q = residue_q(st, vol)
    fitted = {(c, i) for c, i, _ in residues if q.get((c, i), -1.0) >= cut}
    dup = duplicated(st)
    lengths = sorted((len(f) for f in fragments(st)), reverse=True)
    in_short = sum(n_ for n_ in lengths if n_ < SHORT)
    try:
        cc = cc_mask(vol, st, resolution) if n else 0.0
    except ValueError:
        cc = 0.0
    values = [q[(c, i)] for c, i, _ in residues if (c, i) in q]
    confidence = [_anchor(r).b_iso for _, _, r in residues]
    return {
        "residues": n,
        "fitted": len(fitted),
        "fitted_fraction": len(fitted) / n if n else 0.0,
        "duplicated": len(dup),
        "score": len(fitted) - 0.5 * len(fitted & dup),
        "q_mean": float(np.mean(values)) if values else 0.0,
        "cc_mask": float(cc),
        "fragments": len(lengths),
        "longest": lengths[0] if lengths else 0,
        "median_fragment": float(np.median(lengths)) if lengths else 0.0,
        "short_fraction": in_short / n if n else 0.0,
        "confidence": float(np.mean(confidence)) if confidence else 0.0,
        "cutoff": cut,
        "per_residue": q,
    }


def wrong_hand_suspected(score: dict[str, Any]) -> bool:
    """Most residues in short disconnected pieces: what CryoAtom2 builds in a map of the wrong hand."""
    return score["residues"] >= 20 and score["short_fraction"] > 0.5


def _free_chain_names(st: gemmi.Structure):
    used = {ch.name for ch in st[0]}
    for name in CHAIN_NAMES + [a + b for a in CHAIN_NAMES for b in CHAIN_NAMES]:
        if name not in used:
            used.add(name)
            yield name


def _clear_runs(clear: np.ndarray) -> list[tuple[int, int]]:
    """(start, end) of the stretches of True values."""
    runs, start = [], None
    for i, ok in enumerate([*clear.tolist(), False]):
        if ok and start is None:
            start = i
        elif not ok and start is not None:
            runs.append((start, i))
            start = None
    return runs


def combine(base: gemmi.Structure, others: list[tuple[str, gemmi.Structure, dict[tuple[str, int], float]]],
            cutoff: float, min_length: int = 5) -> tuple[gemmi.Structure, list[dict[str, Any]]]:
    """The best model with, added as new chains, the pieces of other models that fit the density where it has
    nothing: the stretches of their fragments whose Cα / P atoms are 3 Å or more from those already there (a fragment
    overlapping the model at its ends is trimmed to the part that fills the gap), at least ``min_length`` residues
    long, with a mean Q-score and most residues at ``cutoff`` or above. ``others``: (label, structure, per-residue Q)
    in the order to try them. Returns the model and what was added."""
    combined = base.clone()
    anchors = [_anchor_xyz(r) for _, _, r in polymer_residues(combined)]
    tree = cKDTree(np.array(anchors)) if anchors else None
    names = _free_chain_names(combined)
    added: list[dict[str, Any]] = []
    for label, st, q in others:
        for frag in fragments(st):
            if len(frag) < min_length:
                continue
            chain = st[0][frag[0][0]]
            xyz = np.array([_anchor_xyz(chain[i]) for _, i in frag])
            clear = tree.query(xyz)[0] >= CLEARANCE if tree is not None else np.ones(len(frag), bool)
            for start, end in _clear_runs(clear):
                if end - start < min_length:
                    continue
                piece = frag[start:end]
                known = [v for v in (q.get(k) for k in piece) if v is not None]
                if not known or np.mean(known) < cutoff or sum(v >= cutoff for v in known) < 0.6 * len(piece):
                    continue
                residues = [chain[i] for _, i in piece]
                new = gemmi.Chain(next(names))
                for res in residues:
                    new.add_residue(res)
                combined[0].add_chain(new)
                anchors.extend(xyz[start:end].tolist())
                tree = cKDTree(np.array(anchors))
                added.append({"source": label, "chain": chain.name, "first": residues[0].seqid.num,
                              "last": residues[-1].seqid.num, "length": len(residues), "q_mean": float(np.mean(known)),
                              "new_chain": new.name})
    combined.setup_entities()
    return combined, added
