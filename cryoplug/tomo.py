"""RELION 5 tomography files.

* Tilt-series sets: a ``global`` table, one row per tilt series (``rlnTomoName``, ``rlnTomoTiltSeriesStarFile``, the
  optics, and later the reconstructed tomograms), and one STAR file per tilt series whose single block is named after
  it (``data_TS_01``), one row per tilt image. Import, motion correction, CTF estimation, tilt-image exclusion and
  tilt-series alignment each write a new set; reconstruction, denoising, CTF refinement and frame alignment write the
  same kind of set as ``tomograms.star``. The file names in a set are relative to the project folder, where RELION runs.
* Optimisation sets: a small STAR file naming the particle set (``rlnTomoParticlesFile``), the tomogram set
  (``rlnTomoTomogramsFile``) and, after frame alignment, the particle trajectories (``rlnTomoTrajectoriesFile``):
  the input of subtomogram extraction, reconstruction, refinement and polishing.
* Particle sets: ``data_general`` (``rlnTomoSubTomosAre2DStacks``), ``data_optics`` and ``data_particles`` with the
  tomogram name and the position of each particle in Å from the tomogram centre (``rlnCenteredCoordinate[XYZ]Angst``).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from cryoplug import relion as rl


# ------------------------------------------------------------------ tilt-series and tomogram sets
@dataclass
class TiltSeriesSet:
    """A tilt-series (or tomogram) set: its global table and the tilt-image table of each tilt series."""
    global_table: rl.StarTable
    series: dict[str, rl.StarTable] = field(default_factory=dict)

    @property
    def names(self) -> list[str]:
        return self.global_table.column("rlnTomoName")

    def value(self, name: str, label: str, default: Any = None) -> Any:
        g = self.global_table
        if not g.has(label):
            return default
        return g.rows[self.names.index(name)][g.labels.index(label)]

    def n_tilts(self) -> int:
        return sum(len(t) for t in self.series.values())


def read_set(path: str | Path, root: str | Path) -> TiltSeriesSet:
    """A tilt-series or tomogram set; ``root``: the project folder its file names are relative to."""
    tables = rl.read_star(path)
    glob = tables.get("global")
    if glob is None or not glob.has("rlnTomoName"):
        raise ValueError(f"{Path(path).name} is not a RELION 5 tilt-series set (no data_global with rlnTomoName)")
    out = TiltSeriesSet(glob)
    if not glob.has("rlnTomoTiltSeriesStarFile"):
        raise ValueError(f"{Path(path).name}: no rlnTomoTiltSeriesStarFile column (a RELION 4 tomogram set?)")
    for name, rel in zip(glob.column("rlnTomoName"), glob.column("rlnTomoTiltSeriesStarFile")):
        f = Path(rel) if Path(rel).is_absolute() else Path(root) / rel
        if not f.exists():
            raise ValueError(f"The tilt series {name}: {rel} is missing")
        blocks = rl.read_star(f)
        table = blocks.get(name) or next(iter(blocks.values()), None)
        if table is None:
            raise ValueError(f"{rel}: no table for the tilt series {name}")
        out.series[name] = table
    return out


def write_set(path: str | Path, tset: TiltSeriesSet, root: str | Path, comment: str = "") -> Path:
    """A set as RELION's Python programs write it: the tilt-series files in ``tilt_series/`` next to ``path``, named
    after the tilt series; tilt series without images are left out."""
    path = Path(path)
    folder = path.parent / "tilt_series"
    folder.mkdir(parents=True, exist_ok=True)
    g = tset.global_table
    keep, files = [], []
    for i, name in enumerate(g.column("rlnTomoName")):
        table = tset.series.get(name)
        if table is None or not len(table):
            continue
        f = folder / f"{name}.star"
        rl.write_star(f, [rl.StarTable(name, list(table.labels), [list(r) for r in table.rows])])
        keep.append(i)
        files.append(str(f.relative_to(root)) if f.is_relative_to(root) else str(f))
    out = g.select(keep)
    out.name = "global"
    out.set_column("rlnTomoTiltSeriesStarFile", files)
    return rl.write_star(path, [out], comment=comment)


# the tomogram of a tilt series to look at: the denoised one, the reconstruction, or (a reconstruction for denoising, which
# RELION writes as the two halves only) the tomogram of the even frames
TOMOGRAM_LABELS = ("rlnTomoReconstructedTomogramDenoised", "rlnTomoReconstructedTomogram", "rlnTomoReconstructedTomogramHalf1")
PROJECTION_LABELS = ("rlnTomogramProjection", "rlnTomogramProjectionHalf1")


def is_reconstructed(tset: TiltSeriesSet) -> bool:
    return any(tset.global_table.has(label) for label in TOMOGRAM_LABELS)


def tomogram_file(tset: TiltSeriesSet, name: str, root: str | Path | None = None,
                  labels: tuple[str, ...] = TOMOGRAM_LABELS) -> str | None:
    """The first tomogram (or projection) of ``labels`` the set names for a tilt series (and that exists in ``root``)."""
    for label in labels:
        v = tset.value(name, label)
        if v and v != "N/A" and (root is None or (Path(root) / v).exists() or Path(v).is_absolute() and Path(v).exists()):
            return v
    return None


def tilt_order(table: rl.StarTable) -> np.ndarray:
    """Indices of the tilt images by stage tilt angle (the order RELION's tools show them in)."""
    if table.has("rlnTomoNominalStageTiltAngle"):
        return np.argsort(table.floats("rlnTomoNominalStageTiltAngle"), kind="stable")
    return np.arange(len(table))


def _floats(table: rl.StarTable, label: str) -> np.ndarray | None:
    try:
        return table.floats(label) if table.has(label) else None
    except ValueError:
        return None


def set_meta(rel_star: str, tset: TiltSeriesSet, kind: str, **extra: Any) -> dict[str, Any]:
    """Metadata of a tilt-series or tomogram set written by a job."""
    g = tset.global_table
    first = next(iter(tset.series.values()), rl.StarTable("none"))

    def gval(label: str) -> float | None:
        try:
            return float(g.value(label)) if g.has(label) else None
        except (TypeError, ValueError):
            return None
    pixel = gval("rlnTomoTiltSeriesPixelSize") or gval("rlnMicrographOriginalPixelSize")
    angles = [t.floats("rlnTomoNominalStageTiltAngle") for t in tset.series.values() if t.has("rlnTomoNominalStageTiltAngle") and len(t)]
    meta: dict[str, Any] = {
        "n_tomograms": len(tset.series), "n_tilts": tset.n_tilts(),
        "pixel_size": round(pixel, 4) if pixel else None, "voltage": gval("rlnVoltage"),
        "cs": gval("rlnSphericalAberration"), "amplitude_contrast": gval("rlnAmplitudeContrast"),
        "tilt_range": [round(float(min(a.min() for a in angles)), 1), round(float(max(a.max() for a in angles)), 1)] if angles else None,
        "has_motion": first.has("rlnMicrographName"), "has_ctf": first.has("rlnDefocusU"),
        "aligned": first.has("rlnTomoXTilt") or first.has("rlnTomoYTilt"),
        "even_odd": first.has("rlnMicrographNameEven"),
        "tomo_names": tset.names[:500],
        "relion": {"star": rel_star, "kind": kind},
    }
    if is_reconstructed(tset):
        binning = gval("rlnTomoTomogramBinning") or 1.0
        raw = ("rlnTomoReconstructedTomogram", "rlnTomoReconstructedTomogramHalf1")
        meta.update({
            "reconstructed": True, "binning": round(binning, 4),
            "binned_pixel_size": round((pixel or 1.0) * binning, 4) if pixel else None,
            "size": [int(float(g.value(lab) or 0)) for lab in ("rlnTomoSizeX", "rlnTomoSizeY", "rlnTomoSizeZ")]
            if g.has("rlnTomoSizeX") else None,
            "tomograms": {n: tomogram_file(tset, n, labels=raw) for n in tset.names[:500]},
            "halves": g.has("rlnTomoReconstructedTomogramHalf1"),
        })
    if g.has("rlnTomoReconstructedTomogramDenoised"):
        meta["denoised"] = dict(zip(tset.names[:500], g.column("rlnTomoReconstructedTomogramDenoised")[:500]))
    if any(g.has(label) for label in PROJECTION_LABELS):
        meta["projections"] = {n: tomogram_file(tset, n, labels=PROJECTION_LABELS) for n in tset.names[:500]}
    meta.update(extra)
    return meta


def series_rows(tset: TiltSeriesSet) -> list[list[Any]]:
    """One summary row per tilt series: name, images, tilt range, dose, and defocus, CTF fit and alignment error when
    known."""
    rows = []
    for name, t in tset.series.items():
        angles = _floats(t, "rlnTomoNominalStageTiltAngle")
        dose = _floats(t, "rlnMicrographPreExposure")
        defocus = _floats(t, "rlnDefocusU")
        res = _floats(t, "rlnCtfMaxResolution")
        rows.append([name, len(t),
                     f"{angles.min():g}° to {angles.max():g}°" if angles is not None and len(angles) else "—",
                     f"{dose.max():.0f} e⁻/Å²" if dose is not None and len(dose) else "—",
                     f"{np.median(defocus) / 1e4:.2f} µm" if defocus is not None and len(defocus) else "—",
                     f"{np.median(res):.1f} Å" if res is not None and len(res) else "—"])
    return rows


def parse_mdoc(path: str | Path) -> list[dict[str, str]]:
    """The tilt images of a SerialEM mdoc file: one dict per ``[ZValue = n]`` section (TiltAngle, SubFramePath,
    ExposureDose, NumSubFrames, DateTime, TargetDefocus...)."""
    sections: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for raw in Path(path).read_text(errors="replace").splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            current = {} if line[1:].split("=")[0].strip() == "ZValue" else None
            if current is not None:
                sections.append(current)
            continue
        if current is not None and "=" in line:
            key, value = line.split("=", 1)
            current[key.strip()] = value.strip()
    return sections


def mdoc_basename(name: str) -> str:
    """The base name RELION matches tilt images on: the file name without directories (Windows ones too) nor any
    suffix (``TS_01_000_0.0.mrc`` -> ``TS_01_000_0``, as RELION's ``basename``)."""
    path = Path(str(name).split("\\")[-1].split("/")[-1])
    while path.stem != str(path):
        path = Path(path.stem)
    return str(path)


# ------------------------------------------------------------------ optimisation and particle sets
def read_optimisation_set(path: str | Path) -> dict[str, str]:
    """The files an optimisation set names: particles, tomograms, trajectories, manifolds, reference maps and mask."""
    names = {"rlnTomoParticlesFile": "particles", "rlnTomoTomogramsFile": "tomograms",
             "rlnTomoTrajectoriesFile": "trajectories", "rlnTomoManifoldsFile": "manifolds",
             "rlnTomoReferenceMap1File": "ref1", "rlnTomoReferenceMap2File": "ref2",
             "rlnTomoReferenceMaskFile": "mask", "rlnTomoReferenceFscFile": "fsc"}
    out: dict[str, str] = {}
    for table in rl.read_star(path).values():
        for label, key in names.items():
            v = table.value(label)
            if v not in (None, "", "None") and key not in out:
                out[key] = v
    return out


def write_optimisation_set(path: str | Path, **files: str | None) -> Path:
    """An optimisation set as RELION's programs write it (a list table)."""
    labels = {"particles": "rlnTomoParticlesFile", "tomograms": "rlnTomoTomogramsFile",
              "trajectories": "rlnTomoTrajectoriesFile", "manifolds": "rlnTomoManifoldsFile"}
    table = rl.StarTable("", loop=False)
    for key, label in labels.items():
        if files.get(key):
            table.labels.append(label)
            if not table.rows:
                table.rows.append([])
            table.rows[0].append(str(files[key]))
    return rl.write_star(path, [table])


def particle_meta(ctx_rel: Any, opt_star: Path, root: Path, kind: str, **extra: Any) -> dict[str, Any]:
    """Metadata of the subtomogram particles of an optimisation set. ``ctx_rel``: path -> project-relative name."""
    files = read_optimisation_set(opt_star)
    if "particles" not in files:
        raise ValueError(f"{opt_star.name} names no particle set")
    pstar = root / files["particles"]
    tables = rl.read_star(pstar)
    parts = tables.get("particles") or rl.particle_table(tables)
    general = tables.get("general")
    optics = tables.get("optics")
    names = parts.column("rlnTomoName") if parts.has("rlnTomoName") else []
    meta: dict[str, Any] = {
        "n_particles": len(parts), "n_tomograms": len(set(names)),
        "per_tomogram": {n: names.count(n) for n in sorted(set(names))[:500]},
        "has_poses": parts.has("rlnAngleRot") and parts.has("rlnAngleTilt"),
        "extracted": parts.has("rlnImageName"),
        "stack2d": bool(int(float(general.value("rlnTomoSubTomosAre2DStacks", 0) or 0))) if general is not None else False,
        "half_sets": parts.has("rlnRandomSubset"),
        "optimisation_set": ctx_rel(opt_star), "particles_star": files["particles"],
        "tomograms_star": files.get("tomograms"), "trajectories": files.get("trajectories"),
        "relion": {"star": files["particles"], "optimisation_set": ctx_rel(opt_star), "kind": kind, "tomo": True},
    }
    if optics is not None and optics.rows:
        for label, key in (("rlnImageSize", "box"), ("rlnImagePixelSize", "pixel_size"),
                           ("rlnTomoTiltSeriesPixelSize", "tilt_series_pixel_size"),
                           ("rlnTomoSubtomogramBinning", "binning")):
            v = optics.value(label)
            if v not in (None, ""):
                meta[key] = int(float(v)) if key == "box" else round(float(v), 4)
    meta.update(extra)
    return meta


def centered_positions(parts: rl.StarTable) -> np.ndarray:
    """Particle positions (Å from the tomogram centre), with the refined offsets of RELION 5 applied
    (``rlnOriginX/Y/ZAngst`` are in the frame of the subtomogram: rotated by ``rlnTomoSubtomogram*`` first)."""
    xyz = np.stack([parts.floats(f"rlnCenteredCoordinate{a}Angst") for a in "XYZ"], axis=1)
    if all(parts.has(f"rlnOrigin{a}Angst") for a in "XYZ"):
        off = np.stack([parts.floats(f"rlnOrigin{a}Angst") for a in "XYZ"], axis=1)
        if all(parts.has(f"rlnTomoSubtomogram{e}") for e in ("Rot", "Tilt", "Psi")):
            from scipy.spatial.transform import Rotation
            ang = np.stack([parts.floats(f"rlnTomoSubtomogram{e}") for e in ("Rot", "Tilt", "Psi")], axis=1)
            mats = Rotation.from_euler("ZYZ", ang, degrees=True).as_matrix()
            off = np.einsum("nji,nj->ni", mats, off)  # A_subtomo^T @ offset
        xyz = xyz - off
    return xyz


def to_binned_voxels(centered_A: np.ndarray, size: list[int], pixel: float, binning: float) -> np.ndarray:
    """Å from the tomogram centre -> voxels of the binned tomogram (napari annotations; RELION's
    get_particle_poses: centred = voxel × pixel × binning − (size / 2 − 1) × pixel)."""
    centre = (np.asarray(size, dtype=float) / 2 - 1) * pixel
    return (np.asarray(centered_A, dtype=float) + centre) / (pixel * binning)


def from_binned_voxels(voxels: np.ndarray, size: list[int], pixel: float, binning: float) -> np.ndarray:
    centre = (np.asarray(size, dtype=float) / 2 - 1) * pixel
    return np.asarray(voxels, dtype=float) * pixel * binning - centre
