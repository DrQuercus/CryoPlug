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


def _rotation_to(axis: np.ndarray) -> np.ndarray:
    """Rotation matrix taking the z axis onto ``axis``."""
    z = np.array([0.0, 0.0, 1.0])
    a = np.asarray(axis, dtype=float) / np.linalg.norm(axis)
    v = np.cross(z, a)
    c = float(np.dot(z, a))
    if np.linalg.norm(v) < 1e-8:
        return np.eye(3) if c > 0 else np.diag([1.0, -1.0, -1.0])
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + vx + vx @ vx * (1.0 / (1.0 + c))


def build_helix(sequence: str, center=(0.0, 0.0, 0.0), axis=(0.0, 0.0, 1.0), chain_name: str = "A") -> gemmi.Chain:
    """Idealised alpha-helix (approximate backbone + CB) for a sequence, along ``axis`` through ``center``."""
    chain = gemmi.Chain(chain_name)
    names3 = gemmi.expand_one_letter_sequence(sequence, gemmi.ResidueKind.AA)
    n = len(sequence)
    z0 = -n * 1.5 / 2
    rot = _rotation_to(np.array(axis))
    ctr = np.array(center, dtype=float)
    for i in range(n):
        res = gemmi.Residue()
        res.name = names3[i]
        res.seqid = gemmi.SeqId(i + 1, " ")
        res.entity_type = gemmi.EntityType.Polymer
        ang = math.radians(100 * i)
        for name, el, r, dang, dz in (("N", "N", 1.55, -28, -0.45), ("CA", "C", 2.3, 0, 0.0), ("C", "C", 1.65, 28, 0.55),
                                      ("O", "O", 1.9, 40, 1.6), ("CB", "C", 3.3, 10, -0.4)):
            local = np.array([r * math.cos(ang + math.radians(dang)), r * math.sin(ang + math.radians(dang)), z0 + 1.5 * i + dz])
            x, y, z = rot @ local + ctr
            a = gemmi.Atom()
            a.name = name
            a.element = gemmi.Element(el)
            a.pos = gemmi.Position(x, y, z)
            a.b_iso = 25.0 + 20 * abs(i - n / 2) / n
            a.occ = 1.0
            res.add_atom(a)
        chain.add_residue(res)
    return chain


# A small "globular" assembly: six helices with different orientations around the centre.
HELIX_LAYOUT = [
    ((-6.0, 0.0, 0.0), (0.0, 0.2, 1.0)), ((6.0, 0.0, 0.0), (0.0, -0.3, 1.0)),
    ((0.0, -6.5, 0.0), (1.0, 0.0, 0.25)), ((0.0, 6.5, 0.0), (1.0, 0.2, -0.3)),
    ((0.0, 0.0, -7.0), (0.3, 1.0, 0.0)), ((0.0, 0.0, 7.0), (-0.25, 1.0, 0.2)),
]


def _preferred_orientation(data: np.ndarray, voxel: float, bfactor: float) -> np.ndarray:
    """Attenuate the signal along z (B-factor scaled by cos^2 of the angle to z), mimicking preferred orientation."""
    n = data.shape[0]
    f = np.fft.rfftn(data)
    kz = (np.fft.fftfreq(n) / voxel)[:, None, None]
    ky = (np.fft.fftfreq(n) / voxel)[None, :, None]
    kx = (np.fft.rfftfreq(n) / voxel)[None, None, :]
    s2 = kx ** 2 + ky ** 2 + kz ** 2
    with np.errstate(invalid="ignore", divide="ignore"):
        cos2 = np.nan_to_num(kz ** 2 / s2)
    f *= np.exp(-bfactor * cos2 * s2 / 4.0)
    return np.fft.irfftn(f, s=data.shape, axes=(0, 1, 2)).astype(np.float32)


def make_demo_dataset(root: str | Path, box: int = 64, pixel_size: float = 1.1, noise: float = 0.15, seed: int = 0,
                      anisotropy_bfactor: float = 120.0) -> dict[str, Path]:
    """Write a fake CryoSPARC job folder (J42) plus model and FASTA under ``root``.

    The half maps carry a mild resolution anisotropy along z (``anisotropy_bfactor``, 0 = isotropic) so that the
    directional-resolution analysis has something to show.
    """
    root = Path(root).expanduser()
    cs = root / "CS-demo" / "J42"
    cs.mkdir(parents=True, exist_ok=True)
    c = box * pixel_size / 2
    st = gemmi.Structure()
    st.name = "demo"
    model = gemmi.Model(1)
    seg = 16
    for i, (offset, axis) in enumerate(HELIX_LAYOUT):
        center = (c + offset[0], c + offset[1], c + offset[2])
        model.add_chain(build_helix(DEMO_SEQUENCE[i * seg:(i + 1) * seg], center, axis, "ABCDEF"[i]))
    st.add_model(model)
    st.setup_entities()
    model_path = root / "demo_model.pdb"
    st.write_pdb(str(model_path))
    xyz = np.array([[a.pos.x, a.pos.y, a.pos.z] for ch in st[0] for r in ch for a in r])
    z = np.array([a.element.atomic_number for ch in st[0] for r in ch for a in r], dtype=float)
    like = MapVolume(data=np.zeros((box, box, box), np.float32), voxel=np.array([pixel_size] * 3))
    clean = model_map(xyz, z, like, 2.8)
    if anisotropy_bfactor > 0:
        clean = _preferred_orientation(clean, pixel_size, anisotropy_bfactor)
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
    fasta.write_text("".join(f">demo_{'ABCDEF'[i]}\n{DEMO_SEQUENCE[i * seg:(i + 1) * seg]}\n" for i in range(len(HELIX_LAYOUT))))
    return {"cryosparc_job": cs, "model": model_path, "fasta": fasta}
