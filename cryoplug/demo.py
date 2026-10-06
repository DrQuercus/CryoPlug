"""Synthetic demo dataset: a CryoSPARC-like refinement folder (half maps, maps, mask), a model and a FASTA file.

``cryoplug demo-data DIR`` lets you try the whole interface without real data
(only built-in jobs and properly configured external programs will run on it).
"""
from __future__ import annotations

import math
from pathlib import Path

import gemmi
import numpy as np

from cryoplug.mrc import MapVolume, model_map

DEMO_SEQUENCE = "MKTAYIAKQRQISFVKSHFSRQLEERLGLIEVQAPILSRVGDGTQDNLSGAEKAVQVKVKALPDAQFEVVHSLAKWKRQTLGQHDFSAGEGLYTHMKALRPDEDRLSPLHSVYVDQWDWERVMGDGERQFSTLKSTVEAIWAGIKATEAAVSEEFGLAPFLPDQIHFVHSQELLSRYPDLDAKGRERAIAKDLGAVFLVGIGGKLSDGHRHDVRAPDYDDWSTPSELGHAGLNGDILVWNPVLEDAFELSSMGIRVDADTLKHQLALTGDEDRLELEWHQALLRGEMPQTIGGGIGQSRLTMLLLQLPHIGQVQAGVWPAACRESFAG"


def build_helix(sequence: str, center=(0.0, 0.0, 0.0), axis_angle: float = 0.0, chain_name: str = "A") -> gemmi.Structure:
    """Idealised alpha-helix (approximate backbone + CB) for a sequence."""
    st = gemmi.Structure()
    st.name = "demo"
    model = gemmi.Model(1)
    chain = gemmi.Chain(chain_name)
    names3 = gemmi.expand_one_letter_sequence(sequence, gemmi.ResidueKind.AA)
    n = len(sequence)
    z0 = -n * 1.5 / 2
    ca, sa = math.cos(axis_angle), math.sin(axis_angle)
    for i in range(n):
        res = gemmi.Residue()
        res.name = names3[i]
        res.seqid = gemmi.SeqId(i + 1, " ")
        res.entity_type = gemmi.EntityType.Polymer
        ang = math.radians(100 * i)
        for name, el, r, dang, dz in (("N", "N", 1.55, -28, -0.45), ("CA", "C", 2.3, 0, 0.0), ("C", "C", 1.65, 28, 0.55),
                                      ("O", "O", 1.9, 40, 1.6), ("CB", "C", 3.3, 10, -0.4)):
            x = r * math.cos(ang + math.radians(dang))
            y = r * math.sin(ang + math.radians(dang))
            z = z0 + 1.5 * i + dz
            # tilt the helix axis in the xz plane
            xt, zt = x * ca + z * sa, -x * sa + z * ca
            a = gemmi.Atom()
            a.name = name
            a.element = gemmi.Element(el)
            a.pos = gemmi.Position(center[0] + xt, center[1] + y, center[2] + zt)
            a.b_iso = 25.0 + 20 * abs(i - n / 2) / n
            a.occ = 1.0
            res.add_atom(a)
        chain.add_residue(res)
    model.add_chain(chain)
    st.add_model(model)
    st.setup_entities()
    return st


def make_demo_dataset(root: str | Path, box: int = 64, pixel_size: float = 1.1, noise: float = 0.15, seed: int = 0) -> dict[str, Path]:
    """Write a fake CryoSPARC job folder (J42) plus model and FASTA under ``root``."""
    root = Path(root).expanduser()
    cs = root / "CS-demo" / "J42"
    cs.mkdir(parents=True, exist_ok=True)
    c = box * pixel_size / 2
    helices = [build_helix(DEMO_SEQUENCE[:24], (c - 6, c, c), 0.15, "A"), build_helix(DEMO_SEQUENCE[24:48], (c + 6, c, c), -0.2, "B")]
    st = helices[0]
    st[0].add_chain(helices[1][0][0])
    st.setup_entities()
    model_path = root / "demo_model.pdb"
    st.write_pdb(str(model_path))
    xyz = np.array([[a.pos.x, a.pos.y, a.pos.z] for ch in st[0] for r in ch for a in r])
    z = np.array([a.element.atomic_number for ch in st[0] for r in ch for a in r], dtype=float)
    like = MapVolume(data=np.zeros((box, box, box), np.float32), voxel=np.array([pixel_size] * 3))
    clean = model_map(xyz, z, like, 2.8)
    clean /= clean.max()
    rng = np.random.default_rng(seed)
    a = like.like(clean + rng.normal(0, noise, clean.shape).astype(np.float32))
    b = like.like(clean + rng.normal(0, noise, clean.shape).astype(np.float32))
    full = like.like((a.data + b.data) / 2)
    mask = like.like((model_map(xyz, np.ones(len(xyz)), like, 9.0) > 0.04).astype(np.float32))
    a.write(cs / "J42_008_volume_map_half_A.mrc")
    b.write(cs / "J42_008_volume_map_half_B.mrc")
    full.write(cs / "J42_008_volume_map.mrc")
    from cryoplug.mrc import fourier_filter
    full.like(fourier_filter(full, bfactor=-40, lowpass=3.0)).write(cs / "J42_008_volume_map_sharp.mrc")
    mask.write(cs / "J42_008_volume_mask_fsc_auto.mrc")
    fasta = root / "demo_sequence.fasta"
    fasta.write_text(f">demo_A\n{DEMO_SEQUENCE[:24]}\n>demo_B\n{DEMO_SEQUENCE[24:48]}\n")
    return {"cryosparc_job": cs, "model": model_path, "fasta": fasta}
