"""Atomic model and sequence helpers built on gemmi."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import gemmi
import numpy as np

WATER_NAMES = {"HOH", "WAT", "DOD", "H2O"}
METALS = {"NA", "MG", "K", "CA", "MN", "FE", "CO", "NI", "CU", "ZN", "CD", "HG", "SR", "BA", "CS", "LI", "RB", "PT", "AU", "W", "MO"}
MODEL_SUFFIXES = (".pdb", ".ent", ".cif", ".mmcif", ".pdb.gz", ".cif.gz", ".mmcif.gz")


def is_model_file(path: str | Path) -> bool:
    name = str(path).lower()
    return name.endswith(MODEL_SUFFIXES)


def read_structure(path: str | Path) -> gemmi.Structure:
    st = gemmi.read_structure(str(path))
    st.remove_empty_chains()
    if len(st) == 0:
        raise ValueError(f"No models found in {path}")
    st.setup_entities()
    return st


def write_structure(st: gemmi.Structure, path: str | Path) -> Path:
    path = Path(path)
    name = path.name.lower()
    if name.endswith(".pdb") or name.endswith(".ent"):
        st.write_pdb(str(path))
    else:
        st.setup_entities()
        doc = st.make_mmcif_document()
        doc.write_file(str(path))
    return path


def polymer_type_name(ptype: gemmi.PolymerType) -> str:
    if ptype in (gemmi.PolymerType.PeptideL, gemmi.PolymerType.PeptideD):
        return "protein"
    if ptype == gemmi.PolymerType.Rna:
        return "rna"
    if ptype == gemmi.PolymerType.Dna:
        return "dna"
    if ptype == gemmi.PolymerType.DnaRnaHybrid:
        return "na-hybrid"
    return "other"


def polymer_sequences(st: gemmi.Structure) -> list[dict[str, Any]]:
    out = []
    for chain in st[0]:
        polymer = chain.get_polymer()
        if polymer.length() == 0:
            continue
        ptype = polymer.check_polymer_type()
        seq = gemmi.one_letter_code([r.name for r in polymer])
        out.append({"chain": chain.name, "type": polymer_type_name(ptype), "sequence": seq, "length": len(seq),
                    "first": polymer[0].seqid.num, "last": polymer[polymer.length() - 1].seqid.num})
    return out


def iter_atoms(st: gemmi.Structure, heavy_only: bool = True):
    for chain in st[0]:
        for res in chain:
            for atom in res:
                if heavy_only and atom.element.name in ("H", "D"):
                    continue
                yield chain, res, atom


def atom_arrays(st: gemmi.Structure, heavy_only: bool = True) -> dict[str, Any]:
    xyz, z, b, occ, keys = [], [], [], [], []
    for chain, res, atom in iter_atoms(st, heavy_only):
        xyz.append((atom.pos.x, atom.pos.y, atom.pos.z))
        z.append(atom.element.atomic_number)
        b.append(atom.b_iso)
        occ.append(atom.occ)
        keys.append((chain.name, res.seqid.num, res.seqid.icode.strip(), res.name, atom.name))
    return {
        "xyz": np.array(xyz, dtype=np.float64).reshape(-1, 3),
        "z": np.array(z, dtype=np.float64),
        "b": np.array(b, dtype=np.float64),
        "occ": np.array(occ, dtype=np.float64),
        "keys": keys,
    }


def structure_summary(st: gemmi.Structure) -> dict[str, Any]:
    chains = polymer_sequences(st)
    n_atoms = 0
    n_h = 0
    mass = 0.0
    bvals = []
    waters = 0
    ligands: dict[str, int] = {}
    n_res = 0
    altlocs = 0
    zero_occ = 0
    for chain in st[0]:
        for res in chain:
            n_res += 1
            if res.name in WATER_NAMES:
                waters += 1
            elif res.het_flag == "H":
                ligands[res.name] = ligands.get(res.name, 0) + 1
            for atom in res:
                n_atoms += 1
                mass += atom.element.weight * atom.occ
                if atom.element.name in ("H", "D"):
                    n_h += 1
                else:
                    bvals.append(atom.b_iso)
                if atom.altloc != "\0":
                    altlocs += 1
                if atom.occ <= 0:
                    zero_occ += 1
    b = np.array(bvals) if bvals else np.zeros(1)
    # AlphaFold-like models store pLDDT (0-100) in the B-factor column
    ca_b = [r[0].b_iso for ch in st[0] for r in ch if r.find_atom("CA", "*")] if len(st[0]) else []
    predicted_like = bool(ca_b) and 0 <= min(ca_b) and max(ca_b) <= 100 and float(np.mean(ca_b)) > 50 and float(np.std(ca_b)) > 1
    return {
        "n_models": len(st),
        "n_chains": len(st[0]),
        "n_residues": n_res,
        "n_atoms": n_atoms,
        "n_hydrogens": n_h,
        "mass_da": round(mass, 1),
        "waters": waters,
        "ligands": ligands,
        "altloc_atoms": altlocs,
        "zero_occupancy_atoms": zero_occ,
        "b_iso": {"min": round(float(b.min()), 2), "mean": round(float(b.mean()), 2), "max": round(float(b.max()), 2)},
        "polymers": chains,
        "predicted_like": predicted_like,
        "cell": [round(v, 3) for v in (st.cell.a, st.cell.b, st.cell.c)],
    }


def ca_traces(st: gemmi.Structure) -> list[np.ndarray]:
    traces = []
    for chain in st[0]:
        pts = []
        for res in chain:
            atom = res.find_atom("CA", "*") or res.find_atom("P", "*")
            if atom:
                pts.append((atom.pos.x, atom.pos.y, atom.pos.z))
        if pts:
            traces.append(np.array(pts))
    return traces


def severe_overlaps(st: gemmi.Structure, cutoff: float = 2.2, limit: int = 200) -> list[dict[str, Any]]:
    """Heavy-atom pairs closer than ``cutoff`` A that are not obviously bonded (rough clash check)."""
    work = st.clone()
    work.remove_hydrogens()
    work.setup_entities()
    model = work[0]
    ns = gemmi.NeighborSearch(model, work.cell, 5).populate()
    cs = gemmi.ContactSearch(cutoff)
    cs.ignore = gemmi.ContactSearch.Ignore.AdjacentResidues
    out = []
    for r in cs.find_contacts(ns):
        a1, a2 = r.partner1, r.partner2
        e1, e2 = a1.atom.element.name.upper(), a2.atom.element.name.upper()
        if e1 in METALS or e2 in METALS:
            continue
        if e1 == "S" and e2 == "S":  # disulfides
            continue
        if a1.residue.het_flag == "H" or a2.residue.het_flag == "H":
            # covalently attached ligands/glycans are common; flag only very short contacts
            if r.dist > 1.6:
                continue
        if work.find_connection_by_cra(a1, a2) is not None:
            continue
        out.append({
            "atom1": f"{a1.chain.name}/{a1.residue.name}{a1.residue.seqid.num}/{a1.atom.name}",
            "atom2": f"{a2.chain.name}/{a2.residue.name}{a2.residue.seqid.num}/{a2.atom.name}",
            "distance": round(r.dist, 2),
        })
        if len(out) >= limit:
            break
    return out


# ------------------------------------------------------------------ sequences
def parse_fasta(text: str) -> list[tuple[str, str]]:
    records: list[tuple[str, str]] = []
    name, seq = None, []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(";"):
            continue
        if line.startswith(">"):
            if name is not None:
                records.append((name, "".join(seq)))
            name, seq = line[1:].strip() or f"seq{len(records) + 1}", []
        else:
            seq.append(re.sub(r"[^A-Za-z:]", "", line).upper())
    if name is not None:
        records.append((name, "".join(seq)))
    elif seq:
        records.append(("seq1", "".join(seq)))
    return [(n, s) for n, s in records if s]


def classify_sequence(seq: str) -> str:
    s = seq.upper().replace(":", "")
    if not s:
        return "protein"
    nuc = sum(s.count(c) for c in "ACGTUN")
    if nuc / len(s) > 0.95:
        if "U" in s and "T" not in s:
            return "rna"
        if "T" in s:
            return "dna"
        return "rna"
    return "protein"


def write_fasta(records: list[tuple[str, str]], path: str | Path, width: int = 80) -> Path:
    path = Path(path)
    with open(path, "w") as fh:
        for name, seq in records:
            fh.write(f">{name}\n")
            for i in range(0, len(seq), width):
                fh.write(seq[i:i + width] + "\n")
    return path


def align_chain_to_sequence(st: gemmi.Structure, chain_name: str, sequence: str, kind: str = "protein") -> dict[str, Any]:
    chain = st[0].find_chain(chain_name)
    if chain is None:
        raise KeyError(chain_name)
    polymer = chain.get_polymer()
    rkind = {"protein": gemmi.ResidueKind.AA, "rna": gemmi.ResidueKind.RNA, "dna": gemmi.ResidueKind.DNA}.get(kind, gemmi.ResidueKind.AA)
    full = gemmi.expand_one_letter_sequence(sequence, rkind)
    ptype = polymer.check_polymer_type()
    result = gemmi.align_sequence_to_polymer(full, polymer, ptype, gemmi.AlignmentScoring())
    model_seq = gemmi.one_letter_code([r.name for r in polymer])
    return {
        "identity": round(result.calculate_identity(), 2),  # % identical over the shorter sequence
        "matches": result.match_count,
        "model_length": len(model_seq),
        "sequence_length": len(sequence),
        "cigar": result.cigar_str(),
    }
