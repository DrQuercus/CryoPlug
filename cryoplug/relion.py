"""RELION interoperability.

* STAR files: the tables of a RELION STAR file (optics, particles, model, post-processing...), read and written.
* CryoSPARC to RELION: the particles of a CryoSPARC job as a RELION 5 particle STAR file, with the conventions of
  pyem's csparc2star: Euler angles of the transposed CryoSPARC rotation, origins in Å (shift × pixel size, no sign
  change), defocus and phase-shift angles in degrees, half sets (``rlnRandomSubset`` = split + 1) and optics groups
  (exposure groups) kept. RELION reads image stacks only under the ``.mrcs`` extension: each stack is linked under that
  name into the job folder.
* Results: FSC curves of ``run_model.star`` and ``postprocess.star``.
* Grids: maps and masks sampled on the grid of a particle set. A set re-centred and re-boxed by signal subtraction has
  a *frame*: the new box and its centre, in voxels of the original box relative to its centre (RELION's logical
  coordinates), always a whole number of voxels so that maps go from one grid to the other without interpolation.
  Maps are centred as in RELION and CryoSPARC: the box centre is voxel ``box // 2``.
"""
from __future__ import annotations

import math
import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import numpy as np

# Angular sampling of RELION's auto-refinement: label -> HEALPix order (as the RELION GUI maps them)
HEALPIX = {"30°": 0, "15°": 1, "7.5°": 2, "3.7°": 3, "1.8°": 4, "0.9°": 5, "0.5°": 6, "0.2°": 7}


# ------------------------------------------------------------------ STAR files
@dataclass
class StarTable:
    """One block of a STAR file: a loop (``rows``) or a list of single values (``loop=False``, one row)."""
    name: str  # block name without "data_" (optics, particles, general, model_class_1...)
    labels: list[str] = field(default_factory=list)  # without the leading "_" (rlnImageName...)
    rows: list[list[str]] = field(default_factory=list)
    loop: bool = True

    def __len__(self) -> int:
        return len(self.rows)

    def has(self, label: str) -> bool:
        return label in self.labels

    def column(self, label: str) -> list[str]:
        if label not in self.labels:
            raise KeyError(f"No {label} column in table {self.name}")
        i = self.labels.index(label)
        return [row[i] for row in self.rows]

    def floats(self, label: str) -> np.ndarray:
        return np.array([float(v) for v in self.column(label)], dtype=np.float64)

    def value(self, label: str, default: Any = None) -> Any:
        """Value of a list table (or of the first row of a loop)."""
        if label not in self.labels or not self.rows:
            return default
        return self.rows[0][self.labels.index(label)]

    def set_column(self, label: str, values: Iterable[Any]) -> None:
        """Adds the column, or replaces its values."""
        text = [_fmt(v) for v in values]
        if not self.rows and not self.labels:
            self.rows = [[] for _ in text]
        if len(text) != len(self.rows):
            raise ValueError(f"{label}: {len(text)} values for {len(self.rows)} rows")
        if label in self.labels:
            i = self.labels.index(label)
            for row, v in zip(self.rows, text):
                row[i] = v
        else:
            self.labels.append(label)
            for row, v in zip(self.rows, text):
                row.append(v)

    def drop(self, *labels: str) -> None:
        keep = [i for i, lab in enumerate(self.labels) if lab not in labels]
        self.labels = [self.labels[i] for i in keep]
        self.rows = [[row[i] for i in keep] for row in self.rows]

    def select(self, rows: Iterable[int]) -> "StarTable":
        return StarTable(self.name, list(self.labels), [list(self.rows[int(i)]) for i in rows], self.loop)


def _fmt(v: Any) -> str:
    if isinstance(v, (bool, np.bool_)):
        return "1" if v else "0"
    if isinstance(v, (int, np.integer)):
        return str(int(v))
    if isinstance(v, (float, np.floating)):
        return f"{float(v):.6f}"
    s = str(v)
    return s if s and not any(c.isspace() for c in s) else f'"{s}"'


def _split(line: str) -> list[str]:
    if '"' in line or "'" in line:
        try:
            return shlex.split(line)
        except ValueError:
            pass
    return line.split()


def read_star(path: str | Path) -> dict[str, StarTable]:
    """All tables of a STAR file, by block name (the first block of a name wins)."""
    tables: dict[str, StarTable] = {}
    current: StarTable | None = None
    in_labels = False
    with open(path, errors="replace") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("data_"):
                current = StarTable(line[5:], loop=False)
                tables.setdefault(current.name, current)
                in_labels = False
                continue
            if current is None:
                continue
            if line.startswith("loop_"):
                current.loop, current.labels, current.rows, in_labels = True, [], [], True
                continue
            if line.startswith("_"):
                parts = _split(line)
                label = parts[0][1:]
                if current.loop and in_labels:
                    current.labels.append(label)
                elif not current.loop:  # list table: "_label value"
                    current.labels.append(label)
                    if not current.rows:
                        current.rows.append([])
                    current.rows[0].append(parts[1] if len(parts) > 1 else "")
                continue
            if current.loop:
                in_labels = False
                current.rows.append(_split(line))
    return tables


def _token(v: str) -> str:
    """A value as written in a STAR file: quoted when it holds spaces (or is empty)."""
    if v and not any(c.isspace() for c in v):
        return v
    return v if len(v) > 1 and v[0] == v[-1] and v[0] in "\"'" else f'"{v}"'


def write_star(path: str | Path, tables: Iterable[StarTable], comment: str = "") -> Path:
    path = Path(path)
    out: list[str] = []
    if comment:
        out += [f"# {c}" for c in comment.splitlines()]
        out.append("")
    for t in tables:
        out += ["", f"data_{t.name}", ""]
        if t.loop:
            out.append("loop_")
            out += [f"_{lab} #{i + 1}" for i, lab in enumerate(t.labels)]
            out += [" ".join(_token(v) for v in row) for row in t.rows]
        else:
            row = t.rows[0] if t.rows else [""] * len(t.labels)
            width = max((len(lab) for lab in t.labels), default=0) + 2
            out += [f"_{lab:<{width}}{_token(v)}" for lab, v in zip(t.labels, row)]
        out.append("")
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text("\n".join(out).lstrip("\n") + "\n")
    os.replace(tmp, path)
    return path


def particle_table(tables: dict[str, StarTable]) -> StarTable:
    """The particle table: ``data_particles`` (RELION 3.1+), or the table holding ``rlnImageName`` (older files)."""
    if "particles" in tables:
        return tables["particles"]
    for t in tables.values():
        if t.loop and t.has("rlnImageName"):
            return t
    raise ValueError("No particle table (no data_particles block nor rlnImageName column)")


def grid_from_star(tables: dict[str, StarTable]) -> tuple[int, float]:
    """Box and pixel size of the particles (first optics group)."""
    optics = tables.get("optics")
    if optics is not None and optics.rows:
        box = optics.value("rlnImageSize")
        apix = optics.value("rlnImagePixelSize")
        if box is not None and apix is not None:
            return int(float(box)), float(apix)
    parts = particle_table(tables)
    for lab in ("rlnImagePixelSize", "rlnDetectorPixelSize"):
        if parts.has(lab):
            apix = float(parts.rows[0][parts.labels.index(lab)])
            if lab == "rlnDetectorPixelSize" and parts.has("rlnMagnification"):
                apix = apix * 1e4 / float(parts.rows[0][parts.labels.index("rlnMagnification")])
            return 0, apix
    return 0, 0.0


def micrograph_table(tables: dict[str, StarTable]) -> StarTable:
    """The movies or micrographs table of a RELION file (``data_micrographs``, ``data_movies``, or the table holding
    their names)."""
    for name in ("micrographs", "movies"):
        if name in tables:
            return tables[name]
    for t in tables.values():
        if t.loop and (t.has("rlnMicrographName") or t.has("rlnMicrographMovieName")):
            return t
    raise ValueError("No micrograph table (no data_micrographs / data_movies block)")


def optics_value(tables: dict[str, StarTable], label: str, default: Any = None) -> Any:
    """A column of the first optics group."""
    optics = tables.get("optics")
    if optics is None or not optics.rows or not optics.has(label):
        return default
    return optics.value(label, default)


def micrograph_pixel_size(tables: dict[str, StarTable]) -> float:
    """Pixel size of the micrographs (binned by motion correction), else of the movies."""
    for label in ("rlnMicrographPixelSize", "rlnMicrographOriginalPixelSize"):
        value = optics_value(tables, label)
        if value is not None:
            return float(value)
    return 0.0


def coordinate_files(path: str | Path) -> list[tuple[str, str]]:
    """(micrograph, coordinate file) of a list written by RELION's picking jobs (``data_coordinate_files``)."""
    tables = read_star(path)
    table = tables.get("coordinate_files")
    if table is None:
        table = next((t for t in tables.values() if t.loop and t.has("rlnMicrographCoordinates")), None)
    if table is None:
        raise ValueError(f"{Path(path).name} is not a list of coordinate files (no rlnMicrographCoordinates)")
    return list(zip(table.column("rlnMicrographName"), table.column("rlnMicrographCoordinates")))


def pipeline_post(name: str) -> str:
    """What RELION keeps of an input name to name its outputs (decomposePipelineFileName): the part after a
    ``/jobNNN/`` folder, else the whole name."""
    m = re.search(r"/job\d{3}/(.+)$", name)
    return m.group(1) if m else name


def write_coordinates(path: str | Path, xy: np.ndarray, fom: np.ndarray | None = None) -> Path:
    """A coordinate file as RELION's picking writes it (x, y in micrograph pixels, figure of merit)."""
    xy = np.asarray(xy, dtype=np.float64).reshape(-1, 2)
    n = len(xy)
    t = StarTable("")
    t.set_column("rlnCoordinateX", xy[:, 0])
    t.set_column("rlnCoordinateY", xy[:, 1])
    t.set_column("rlnClassNumber", [0] * n)
    t.set_column("rlnAutopickFigureOfMerit", np.zeros(n) if fom is None else np.asarray(fom, dtype=np.float64))
    t.set_column("rlnAnglePsi", [-999.0] * n)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return write_star(path, [t])


def write_coordinate_list(path: str | Path, pairs: list[tuple[str, str]], pick_type: str | None = None) -> Path:
    """The list of coordinate files of a picking job (``data_coordinate_files``): micrograph, its coordinates.
    ``pick_type`` "startend" (filaments: each coordinate file holds the start and end of every tube, one after the
    other) or "lines" goes in a ``data_general`` block first, as RELION 5's manual picker writes it."""
    t = StarTable("coordinate_files")
    t.set_column("rlnMicrographName", [m for m, _ in pairs])
    t.set_column("rlnMicrographCoordinates", [c for _, c in pairs])
    tables = [t]
    if pick_type:
        tables.insert(0, StarTable("general", ["rlnMicrographPickType"], [[pick_type]], loop=False))
    return write_star(path, tables)


def coordinate_pick_type(path: str | Path) -> str:
    """What a list of coordinate files holds: "particles" (the default), "startend" or "lines" (filaments)."""
    try:
        general = read_star(path).get("general")
    except (OSError, ValueError):
        return "particles"
    value = general.value("rlnMicrographPickType") if general is not None else None
    return str(value) if value else "particles"


def read_coordinates(path: str | Path) -> tuple[np.ndarray, np.ndarray | None]:
    """Particle positions (x, y in pixels of the micrograph) of a coordinate file, and their figure of merit."""
    tables = read_star(path)
    table = next((t for t in tables.values() if t.loop and t.has("rlnCoordinateX")), None)
    if table is None or not table.rows:
        return np.zeros((0, 2)), None
    xy = np.stack([table.floats("rlnCoordinateX"), table.floats("rlnCoordinateY")], axis=1)
    fom = table.floats("rlnAutopickFigureOfMerit") if table.has("rlnAutopickFigureOfMerit") else None
    return xy, fom


def model_classes(path: str | Path) -> list[dict[str, Any]]:
    """Classes of a ``run_itNNN_model.star``: number, reference image (``N@stack``), share of the particles,
    accuracies and estimated resolution."""
    tables = read_star(path)
    table = tables.get("model_classes")
    if table is None:
        raise ValueError(f"{Path(path).name} has no data_model_classes block")
    out = []
    for i, row in enumerate(table.rows):
        rec = dict(zip(table.labels, row))

        def num(label: str) -> float | None:
            try:
                v = float(rec[label])
            except (KeyError, ValueError):
                return None
            return v if math.isfinite(v) else None
        out.append({"class": i + 1, "image": rec.get("rlnReferenceImage", ""),
                    "distribution": num("rlnClassDistribution") or 0.0,
                    "accuracy_rotations": num("rlnAccuracyRotations"),
                    "accuracy_translations": num("rlnAccuracyTranslationsAngst") or num("rlnAccuracyTranslations"),
                    "resolution": num("rlnEstimatedResolution"),
                    "completeness": num("rlnOverallFourierCompleteness")})
    return out


def iteration_files(folder: Path, root: str = "run") -> tuple[int, dict[str, Path]]:
    """Latest iteration written by relion_refine (classification, initial model) and its files."""
    found = sorted(int(m.group(1)) for p in folder.glob(f"{root}_it*_optimiser.star")
                   if (m := re.fullmatch(rf"{re.escape(root)}_it(\d+)_optimiser\.star", p.name)))
    if not found:
        return 0, {}
    it = found[-1]
    stem = f"{root}_it{it:03d}"
    return it, {"data": folder / f"{stem}_data.star", "model": folder / f"{stem}_model.star",
                "optimiser": folder / f"{stem}_optimiser.star", "classes": folder / f"{stem}_classes.mrcs"}


def subset_star(src: str | Path, dst: str | Path, keep: Iterable[int] | np.ndarray, comment: str = "") -> Path:
    """A copy of a RELION file (particles, micrographs or movies) with only some rows of its main table."""
    tables = read_star(src)
    try:
        main = particle_table(tables)
    except ValueError:
        main = micrograph_table(tables)
    rows = np.flatnonzero(keep) if isinstance(keep, np.ndarray) and keep.dtype == bool else list(keep)
    return write_star(dst, [t if t is not main else main.select(rows) for t in tables.values()], comment=comment)


def star_rows(path: str | Path) -> int:
    """Number of rows of the main table (particles, micrographs, movies or coordinate files)."""
    tables = read_star(path)
    for getter in (particle_table, micrograph_table):
        try:
            return len(getter(tables))
        except ValueError:
            continue
    table = next((t for t in tables.values() if t.loop), None)
    return len(table) if table is not None else 0


def parse_numbers(text: str) -> list[int]:
    """Class numbers written as "1, 4, 7-9" (ranges included)."""
    out: list[int] = []
    for part in re.split(r"[,;\s]+", str(text or "").strip()):
        if not part:
            continue
        m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            out.extend(range(min(a, b), max(a, b) + 1))
        elif part.isdigit():
            out.append(int(part))
        else:
            raise ValueError(f"'{part}' is not a class number or a range (e.g. 1, 4, 7-9)")
    return sorted(set(out))


# ---------------------------------------------------------- CryoSPARC -> RELION
CS_NEEDED =("blob/path", "blob/idx", "blob/shape", "blob/psize_A", "ctf/df1_A", "ctf/df2_A", "ctf/df_angle_rad",
             "ctf/accel_kv", "ctf/cs_mm", "ctf/amp_contrast")
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _text(v: Any) -> str:
    return v.decode() if isinstance(v, bytes) else str(v)


class StackLinker:
    """Image stacks linked into a folder under the ``.mrcs`` extension, with names unique and safe for STAR files.
    ``refs`` are the names written in the STAR file: relative to ``base`` (the folder RELION runs from)."""

    def __init__(self, folder: Path, base: Path):
        self.folder, self.base = folder, base
        self.folder.mkdir(parents=True, exist_ok=True)
        self.links: dict[Path, str] = {}  # resolved target -> reference
        self.used: set[str] = set()
        self.missing: list[str] = []

    def ref(self, target: Path, label: str) -> str:
        target = target.resolve() if target.exists() else target
        if target in self.links:
            return self.links[target]
        if not target.exists():
            self.missing.append(str(target))
        stem = _SAFE.sub("_", label.strip("/").replace("/", "_"))
        for ext in (".mrcs", ".mrc", ".st"):
            if stem.lower().endswith(ext):
                stem = stem[: -len(ext)]
                break
        name, k = f"{stem or 'stack'}.mrcs", 1
        while name in self.used:
            k += 1
            name = f"{stem}_{k}.mrcs"
        self.used.add(name)
        link = self.folder / name
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(target)
        ref = os.path.relpath(link, self.base)
        self.links[target] = ref
        return ref


def resolve_image(rel: str, roots: Iterable[Path | None]) -> Path:
    p = Path(rel)
    if p.is_absolute():
        return p
    candidates = [r / p for r in roots if r is not None]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0] if candidates else p


def cs_to_star(data: np.ndarray, datadir: Path, linker: StackLinker) -> tuple[list[StarTable], dict[str, Any]]:
    """CryoSPARC particles (merged with their passthrough fields) as the optics and particle tables of a RELION 5 STAR
    file. Image stacks are linked by ``linker``; ``datadir`` is the folder their paths are relative to (the CryoSPARC
    project folder)."""
    names = set(data.dtype.names or ())
    missing = [n for n in CS_NEEDED if n not in names]
    if missing:
        raise ValueError("The particles have no " + ", ".join(missing) + " (import them from the job that holds the "
                         "images and the CTF, with its passthrough file)")
    n = len(data)
    if n == 0:
        raise ValueError("No particle")
    shape = np.asarray(data["blob/shape"]).reshape(n, -1)[:, 0].astype(int)
    psize = np.asarray(data["blob/psize_A"], dtype=np.float64)
    if len(np.unique(shape)) > 1 or float(np.ptp(psize)) > 1e-3:
        raise ValueError("The particles have different box or pixel sizes (several extractions): re-extract them at one "
                         "size in CryoSPARC")
    box, apix = int(shape[0]), float(psize[0])

    # image names: N@stack, the stacks linked as .mrcs
    paths = [_text(p) for p in data["blob/path"]]
    refs: dict[str, str] = {}
    for p in dict.fromkeys(paths):
        rel = p[1:] if p.startswith(">") else p
        refs[p] = linker.ref(resolve_image(rel, [datadir]), rel)
    if linker.missing:
        more = f" (and {len(linker.missing) - 1} more)" if len(linker.missing) > 1 else ""
        raise ValueError(f"Particle images not found: {linker.missing[0]}{more}. Set the image folder of the import (the "
                         "CryoSPARC project folder)")
    idx = np.asarray(data["blob/idx"], dtype=np.int64)
    parts = StarTable("particles")
    parts.set_column("rlnImageName", [f"{i + 1:06d}@{refs[p]}" for i, p in zip(idx, paths)])
    if "location/micrograph_path" in names:
        mics = [_text(m) for m in data["location/micrograph_path"]]
        parts.set_column("rlnMicrographName", [m[1:] if m.startswith(">") else (m or "mic") for m in mics])
    else:  # one group per image stack (≈ per micrograph after extraction) for RELION's per-group scale
        parts.set_column("rlnMicrographName", [refs[p] for p in paths])
    if {"location/center_x_frac", "location/center_y_frac", "location/micrograph_shape"} <= names:
        mshape = np.asarray(data["location/micrograph_shape"]).reshape(n, -1)  # (ny, nx)
        if mshape.shape[1] >= 2:
            parts.set_column("rlnCoordinateX", np.round(np.asarray(data["location/center_x_frac"]) * mshape[:, 1]).astype(int))
            parts.set_column("rlnCoordinateY", np.round((1 - np.asarray(data["location/center_y_frac"])) * mshape[:, 0]).astype(int))
    parts.set_column("rlnDefocusU", np.asarray(data["ctf/df1_A"], dtype=np.float64))
    parts.set_column("rlnDefocusV", np.asarray(data["ctf/df2_A"], dtype=np.float64))
    parts.set_column("rlnDefocusAngle", np.degrees(np.asarray(data["ctf/df_angle_rad"], dtype=np.float64)))
    if "ctf/phase_shift_rad" in names:
        parts.set_column("rlnPhaseShift", np.degrees(np.asarray(data["ctf/phase_shift_rad"], dtype=np.float64)))
    if "ctf/bfactor" in names:
        parts.set_column("rlnCtfBfactor", np.asarray(data["ctf/bfactor"], dtype=np.float64))

    # optics groups: CryoSPARC exposure groups
    group_ids = np.asarray(data["ctf/exp_group_id"]).astype(np.int64) if "ctf/exp_group_id" in names else np.zeros(n, np.int64)
    uniq, group = np.unique(group_ids, return_inverse=True)
    parts.set_column("rlnOpticsGroup", group + 1)
    optics = StarTable("optics")
    rows = []
    for g in range(len(uniq)):
        first = int(np.flatnonzero(group == g)[0])
        row: dict[str, Any] = {
            "rlnOpticsGroupName": f"opticsGroup{g + 1}", "rlnOpticsGroup": g + 1,
            "rlnVoltage": float(data["ctf/accel_kv"][first]), "rlnSphericalAberration": float(data["ctf/cs_mm"][first]),
            "rlnAmplitudeContrast": float(data["ctf/amp_contrast"][first]), "rlnImagePixelSize": apix,
            "rlnImageSize": box, "rlnImageDimensionality": 2, "rlnCtfDataAreCtfPremultiplied": 0,
        }
        if "ctf/tilt_A" in names:  # beam tilt in mrad (pyem)
            tilt = np.asarray(data["ctf/tilt_A"][first], dtype=np.float64).ravel()
            cs = float(data["ctf/cs_mm"][first]) or 1.0
            row["rlnBeamTiltX"] = float(np.arcsin(np.clip(tilt[0] / cs * 1e-7, -1, 1)) * 1e3)
            row["rlnBeamTiltY"] = float(np.arcsin(np.clip(tilt[1] / cs * 1e-7, -1, 1)) * 1e3)
        if "ctf/anisomag" in names:
            mag = np.asarray(data["ctf/anisomag"][first], dtype=np.float64).ravel()
            if len(mag) == 4 and np.any(mag):
                row.update(rlnMagMat00=mag[0], rlnMagMat01=mag[1], rlnMagMat10=mag[2], rlnMagMat11=mag[3])
        rows.append(row)
    labels = list(dict.fromkeys(label for r in rows for label in r))
    optics.labels = labels
    optics.rows = [[_fmt(r.get(label, 0.0)) for label in labels] for r in rows]

    info: dict[str, Any] = {"n_particles": n, "box": box, "pixel_size": round(apix, 4), "optics_groups": len(uniq),
                            "stacks": len(refs), "has_poses": False, "half_sets": False}
    if "alignments3D/pose" in names and "alignments3D/shift" in names:
        from cryoplug import poses as ps
        pose = np.asarray(data["alignments3D/pose"], dtype=np.float64).reshape(n, 3)
        euler = ps.matrix_to_relion(ps.rotvec_to_matrix(pose).transpose(0, 2, 1))
        shift_apix = (np.asarray(data["alignments3D/psize_A"], dtype=np.float64) if "alignments3D/psize_A" in names
                      else psize)
        shift = np.asarray(data["alignments3D/shift"], dtype=np.float64).reshape(n, 2) * shift_apix[:, None]
        parts.set_column("rlnAngleRot", euler[:, 0])
        parts.set_column("rlnAngleTilt", euler[:, 1])
        parts.set_column("rlnAnglePsi", euler[:, 2])
        parts.set_column("rlnOriginXAngst", shift[:, 0])
        parts.set_column("rlnOriginYAngst", shift[:, 1])
        info["has_poses"] = True
        if "alignments3D/split" in names:
            split = np.asarray(data["alignments3D/split"]).astype(np.int64)
            if set(np.unique(split).tolist()) == {0, 1}:
                parts.set_column("rlnRandomSubset", split + 1)
                info["half_sets"] = True
    return [optics, parts], info


def relink_star(tables: dict[str, StarTable], datadir: Path | None, star_dir: Path, linker: StackLinker) -> dict[str, Any]:
    """A RELION particle STAR file made readable from another folder: every image stack linked by ``linker`` (paths
    relative to ``datadir``, else to the folder of the file). Modifies the particle table in place."""
    parts = particle_table(tables)
    names = parts.column("rlnImageName")
    refs: dict[str, str] = {}
    out = []
    for value in names:
        number, at, rel = value.partition("@")
        if not at:
            number, rel = "", value
        if rel not in refs:
            refs[rel] = linker.ref(resolve_image(rel, [datadir, star_dir]), rel)
        out.append(f"{number}@{refs[rel]}" if number else refs[rel])
    if linker.missing:
        more = f" (and {len(linker.missing) - 1} more)" if len(linker.missing) > 1 else ""
        raise ValueError(f"Particle images not found: {linker.missing[0]}{more}. Set the image folder of the import (the "
                         "RELION project folder the image paths are relative to)")
    parts.set_column("rlnImageName", out)
    box, apix = grid_from_star(tables)
    has_poses = parts.has("rlnAngleRot") and parts.has("rlnAngleTilt") and parts.has("rlnAnglePsi")
    subsets = parts.has("rlnRandomSubset") and set(parts.column("rlnRandomSubset")) >= {"1", "2"}
    return {"n_particles": len(parts), "box": box, "pixel_size": round(apix, 4), "stacks": len(refs),
            "optics_groups": len(tables["optics"]) if "optics" in tables else 1, "has_poses": has_poses,
            "half_sets": bool(subsets)}


# --------------------------------------------------------------------- results
def model_fsc(path: str | Path) -> dict[str, Any]:
    """Gold-standard FSC of the last iteration of a refinement (``run_model.star``) and its accuracy estimates."""
    tables = read_star(path)
    out: dict[str, Any] = {}
    curve = tables.get("model_class_1")
    if curve is not None and curve.has("rlnGoldStandardFsc") and curve.has("rlnResolution"):
        freq, fsc = curve.floats("rlnResolution"), curve.floats("rlnGoldStandardFsc")
        keep = freq > 0
        out["frequency"], out["fsc"] = freq[keep].tolist(), fsc[keep].tolist()
    classes = tables.get("model_classes")
    if classes is not None and classes.rows:
        for label, key in (("rlnAccuracyRotations", "accuracy_rotations"),
                           ("rlnAccuracyTranslationsAngst", "accuracy_translations"),
                           ("rlnEstimatedResolution", "estimated_resolution")):
            if classes.has(label):
                try:
                    out[key] = float(classes.column(label)[0])
                except ValueError:
                    pass
        if classes.has("rlnClassDistribution"):
            out["class_distribution"] = classes.floats("rlnClassDistribution").tolist()
    general = tables.get("model_general")
    if general is not None:
        for label, key in (("rlnCurrentResolution", "current_resolution"), ("rlnCurrentIteration", "iteration")):
            v = general.value(label)
            if v is not None:
                try:
                    out[key] = float(v)
                except ValueError:
                    pass
    return out


PP_CURVES = {"rlnFourierShellCorrelationCorrected": "corrected", "rlnFourierShellCorrelationUnmaskedMaps": "unmasked",
             "rlnFourierShellCorrelationMaskedMaps": "masked",
             "rlnCorrectedFourierShellCorrelationPhaseRandomizedMaskedMaps": "phase_randomized"}


def postprocess_results(path: str | Path) -> dict[str, Any]:
    """Resolution, B-factor and FSC curves written by relion_postprocess."""
    tables = read_star(path)
    out: dict[str, Any] = {"curves": {}}
    general = tables.get("general")
    if general is not None:
        for label, key in (("rlnFinalResolution", "resolution"), ("rlnBfactorUsedForSharpening", "bfactor"),
                           ("rlnRandomiseFrom", "randomized_from")):
            v = general.value(label)
            if v is not None:
                try:
                    out[key] = float(v)
                except ValueError:
                    pass
    fsc = tables.get("fsc")
    if fsc is not None and fsc.has("rlnResolution"):
        freq = fsc.floats("rlnResolution")
        keep = freq > 0
        out["frequency"] = freq[keep].tolist()
        for label, key in PP_CURVES.items():
            if fsc.has(label):
                out["curves"][key] = fsc.floats(label)[keep].tolist()
    return out


def resolution_at(freq: Iterable[float], fsc: Iterable[float], threshold: float = 0.143) -> float | None:
    """Resolution (Å) where an FSC curve first falls below the threshold (linear interpolation)."""
    f, c = np.asarray(list(freq), dtype=np.float64), np.asarray(list(fsc), dtype=np.float64)
    if len(f) < 2:
        return None
    below = np.flatnonzero(c < threshold)
    below = below[below > 0]
    if not len(below):
        return float(1.0 / f[-1]) if f[-1] > 0 else None
    i = int(below[0])
    t = (c[i - 1] - threshold) / ((c[i - 1] - c[i]) or 1.0)
    freq = f[i - 1] + t * (f[i] - f[i - 1])
    return float(1.0 / freq) if freq > 0 else None


# ----------------------------------------------------------------------- grids
def good_box(size: float, minimum: int = 64) -> int:
    """Smallest even box size ≥ size made of the factors 2, 3, 5, 7 only (fast Fourier transforms)."""
    n = max(minimum, int(math.ceil(size)))
    n += n % 2
    while True:
        m = n
        for p in (2, 3, 5, 7):
            while m % p == 0:
                m //= p
        if m == 1:
            return n
        n += 2


def center_of_mass(mask: np.ndarray) -> np.ndarray:
    """Centre of mass (x, y, z) of a mask (z, y, x), in voxels relative to the box centre (voxel box // 2), the
    logical coordinates in which RELION re-centres subtracted particles."""
    w = np.clip(np.asarray(mask, dtype=np.float64), 0, None)
    total = w.sum()
    if total <= 0:
        raise ValueError("The mask is empty")
    out = []
    for axis in (2, 1, 0):  # x, y, z
        n = w.shape[axis]
        profile = w.sum(axis=tuple(a for a in range(3) if a != axis))
        out.append(float((profile * (np.arange(n) - n // 2)).sum() / total))
    return np.array(out)


def mask_extent(mask: np.ndarray, center: np.ndarray, threshold: float = 0.01, radial: bool = False) -> float:
    """Largest distance (voxels) from ``center`` (logical xyz) to a voxel of the mask: along x, y or z (the half side of
    the cube holding the mask), or ``radial`` (the radius of the sphere holding it)."""
    idx = np.argwhere(np.asarray(mask) > threshold)  # (z, y, x)
    if not len(idx):
        raise ValueError("The mask is empty")
    n = np.array(mask.shape)
    d = np.abs(idx - n // 2 - np.asarray(center, dtype=np.float64)[::-1])
    return float(np.sqrt((d ** 2).sum(axis=1)).max() if radial else d.max())


def suggest_box(mask: np.ndarray, center: np.ndarray, original: int, factor: float = 1.5) -> int:
    """Box of re-boxed subtracted particles: ``factor`` × the extent of the mask around the new centre (room for the
    signal the CTF spreads around the region), at most the original box."""
    extent = 2.0 * (mask_extent(mask, center) + 1.0)
    box = good_box(factor * extent)
    return int(min(box, original - original % 2))


def frame_of(meta: dict[str, Any]) -> dict[str, Any] | None:
    """The frame of re-centred, re-boxed particles (``relion_frame`` in their metadata), or None."""
    fr = meta.get("relion_frame")
    if not fr:
        return None
    return {"box": int(fr["box"]), "center": [int(round(float(c))) for c in fr["center"]],
            "parent_box": int(fr["parent_box"]), "pixel_size": float(fr["pixel_size"])}


def _sample(data: np.ndarray, box: int, scale: float, offset: np.ndarray, order: int) -> np.ndarray:
    """out[i] = data[scale * i + offset] (zyx), zero outside; exact slicing when the scale is 1 and the offset whole."""
    data = np.asarray(data, dtype=np.float32)
    if abs(scale - 1.0) < 1e-6 and np.allclose(offset, np.round(offset), atol=1e-6):
        off = np.round(offset).astype(int)
        out = np.zeros((box,) * 3, dtype=np.float32)
        src, dst = [], []
        for axis in range(3):
            lo = max(0, -off[axis])
            hi = min(box, data.shape[axis] - off[axis])
            if hi <= lo:
                return out
            dst.append(slice(lo, hi))
            src.append(slice(lo + off[axis], hi + off[axis]))
        out[tuple(dst)] = data[tuple(src)]
        return out
    from scipy import ndimage
    if scale > 1.0 + 1e-6:  # coarser output: low-pass first against aliasing
        data = ndimage.gaussian_filter(data, sigma=0.5 * scale)
    return ndimage.affine_transform(data, np.full(3, scale), offset=offset, output_shape=(box,) * 3, order=order,
                                    mode="constant", cval=0.0).astype(np.float32)


def to_grid(data: np.ndarray, pixel_size: float, box: int, grid_pixel: float, center: Iterable[float] = (0, 0, 0),
            order: int = 1) -> np.ndarray:
    """A centred map (z, y, x) of ``pixel_size`` Å resampled on a box³ grid of ``grid_pixel`` Å whose centre lies at
    ``center`` (x, y, z, in voxels of that grid size relative to the centre of the map)."""
    c = np.asarray(list(center), dtype=np.float64)[::-1]  # zyx
    s = grid_pixel / pixel_size
    n_in = np.array(np.asarray(data).shape)
    offset = (c - box // 2) * s + n_in // 2
    return _sample(data, box, s, offset, order)


def to_parent(data: np.ndarray, frame: dict[str, Any]) -> np.ndarray:
    """A map on the grid of re-centred particles put back on the grid of the original particles (whole voxels)."""
    parent, c = int(frame["parent_box"]), np.asarray(frame["center"], dtype=np.float64)[::-1]  # zyx
    offset = np.array(np.asarray(data).shape) // 2 - c - parent // 2  # parent voxel j <- local voxel j + offset
    return _sample(data, parent, 1.0, offset, 0)


def particle_diameter(mask: np.ndarray | None, pixel_size: float, box: int) -> float:
    """Diameter (Å) of RELION's circular mask on the images: the extent of the mask plus a margin, or 90 % of the box."""
    limit = 0.98 * box * pixel_size
    if mask is None:
        return round(0.9 * box * pixel_size, 1)
    radius = mask_extent(mask, np.zeros(3), radial=True)
    return round(min(limit, 2.0 * radius * pixel_size + 10.0), 1)
