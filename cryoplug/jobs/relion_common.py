"""Shared parts of the RELION single-particle pipeline jobs (movies to map, as in RELION's GUI).

RELION programs run from the project folder with project-relative paths, as in a RELION project: a job writes into its
CryoPlug folder (``J12/``) and the STAR files of one job are read by the next. Each command is the one RELION 5.1's GUI
builds for the same options (src/pipeline_jobs.cpp), so that a job behaves as it would in RELION.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import numpy as np

from cryoplug import relion as rl
from cryoplug.jobs.base import InputData, JobContext, JobError, Param
from cryoplug.jobs.relion import TOOL, RelionJob, relion_command, run_relion

__all__ = ["TOOL", "RelionJob", "relion_command", "run_relion"]  # with the helpers below, used by the job modules

# Angular samplings of RELION's GUI: the oversampled values shown to the user; ``--healpix_order`` is the index
# (RELION subtracts the oversampling order from the GUI's HEALPix order, which starts at 1 for 30 degrees)
SAMPLINGS = ["30°", "15°", "7.5°", "3.7°", "1.8°", "0.9°", "0.5°", "0.2°", "0.1°"]


def healpix(label: str) -> int:
    return SAMPLINGS.index(label)


# ------------------------------------------------------------------ parameters
def mpi(default: int = 1, minimum: int = 1, help: str = "") -> Param:
    return Param("mpi", "int", default, label="MPI processes", min=minimum, max=512,
                 help=help or "RELION processes started with mpirun (set the launcher with the 'MPIRUN' variable of "
                              "the RELION tool, e.g. 'mpirun --oversubscribe' or srun). 1 = the program without MPI.")


def threads(default: int = 4, help: str = "") -> Param:
    return Param("threads", "int", default, label="Threads per process", min=1, max=256,
                 help=help or "--j: the job asks for MPI processes × threads CPUs.")


def gpus(default: int = 1, help: str = "") -> Param:
    return Param("gpus", "int", default, label="GPUs", min=0, max=16,
                 help=help or "GPUs given to the job (RELION uses all of them: --gpu \"\"). 0 = CPU only.")


def compute_params(p: dict[str, Any], args: list[str], gpu: bool = True) -> list[str]:
    """Disc and memory options shared by relion_refine jobs (as the GUI adds them)."""
    if not p.get("combine_through_disc"):
        args.append("--dont_combine_weights_via_disc")
    if not p.get("parallel_disc_io", True):
        args.append("--no_parallel_disc_io")
    if p.get("preread_images"):
        args.append("--preread_images")
    elif str(p.get("scratch_dir") or "").strip():
        args += ["--scratch_dir", str(p["scratch_dir"]).strip()]
    args += ["--pool", str(p.get("pool", 3))]
    return args


def refine_compute_params(pool: int = 3) -> list[Param]:
    return [
        Param("pool", "int", pool, label="Pooled particles", min=1, max=100, advanced=True,
              help="Particles read together per thread (--pool)."),
        Param("preread_images", "bool", False, label="Read all particles into memory", advanced=True,
              help="Fast on slow discs: box² × 4 bytes per particle in RAM."),
        Param("scratch_dir", "str", "", label="Scratch folder", advanced=True, placeholder="/scratch",
              help="A fast local disc where RELION copies the particles first (--scratch_dir)."),
        Param("parallel_disc_io", "bool", True, label="Parallel disc I/O", advanced=True,
              help="All MPI followers read images (off: only the leader reads, for NFS that breaks)."),
        Param("combine_through_disc", "bool", False, label="Combine iterations through disc", advanced=True),
    ]


# ------------------------------------------------------------------ inputs
def star_of(ctx: JobContext, inp: InputData) -> tuple[Path, str]:
    """The STAR file of an input (absolute) and its project-relative name (RELION reads it from the project)."""
    star = Path(inp.files[0] if inp.files else inp.path)
    if not star.exists():
        raise JobError(f"{inp.source}: {star.name} is not available any more")
    if star.suffix != ".star":
        raise JobError(f"{inp.source} is not a RELION STAR file ({star.name})")
    return star, ctx.rel(star)


def relion_particles(ctx: JobContext, inp: InputData, need_poses: bool = False) -> dict[str, Any]:
    """Particles as RELION reads them: a STAR file of a RELION job as it is, anything else (CryoSPARC) converted."""
    from cryoplug.jobs.relion import prepare_particles
    meta = inp.meta.get("relion") or {}
    star = Path(inp.files[0] if inp.files else inp.path)
    if meta.get("star") and star.suffix == ".star" and star.exists():
        tables = rl.read_star(star)
        box, apix = rl.grid_from_star(tables)
        ptab = rl.particle_table(tables)
        return {"star": star, "ref": ctx.rel(star), "box": box or int(inp.meta.get("box") or 0),
                "pixel_size": apix or float(inp.meta.get("pixel_size") or 0), "n": len(ptab), "tables": tables,
                "has_poses": ptab.has("rlnAngleRot"), "meta": inp.meta, "frame": None}
    parts = prepare_particles(ctx, inp, need_poses=need_poses)
    parts["tables"] = rl.read_star(parts["star"])
    parts["has_poses"] = bool(parts["info"].get("has_poses"))
    return parts


def mask_diameter(ctx: JobContext, parts: dict[str, Any], param: str = "particle_diameter") -> float:
    """The mask diameter (Å): the parameter, else 90 % of the box (logged)."""
    value = float(ctx.params.get(param) or 0)
    if value > 0:
        return value
    if not parts.get("box") or not parts.get("pixel_size"):
        raise JobError("Set the mask diameter: the box or pixel size of the particles is unknown")
    value = round(0.9 * parts["box"] * parts["pixel_size"])
    ctx.log(f"Mask diameter: {value:g} Å (90 % of the {parts['box']} px box)")
    return float(value)


# ------------------------------------------------------------------ outputs
def micrograph_meta(ctx: JobContext, star: Path, kind: str, **extra: Any) -> dict[str, Any]:
    """Metadata of a movies or micrographs STAR file written by a job: count, optics, CTF statistics."""
    tables = rl.read_star(star)
    table = rl.micrograph_table(tables)
    meta: dict[str, Any] = {"n_micrographs": len(table), "pixel_size": round(rl.micrograph_pixel_size(tables), 4),
                            "voltage": _float(rl.optics_value(tables, "rlnVoltage")),
                            "cs": _float(rl.optics_value(tables, "rlnSphericalAberration")),
                            "amplitude_contrast": _float(rl.optics_value(tables, "rlnAmplitudeContrast")),
                            "optics_groups": len(tables["optics"]) if "optics" in tables else 1,
                            "has_ctf": table.has("rlnDefocusU"), "relion": {"star": ctx.rel(star), "kind": kind}}
    if table.has("rlnDefocusU") and len(table):
        df = table.floats("rlnDefocusU")
        meta["defocus_range"] = [round(float(df.min()), 0), round(float(df.max()), 0)]
        if table.has("rlnCtfMaxResolution"):
            meta["ctf_resolution"] = round(float(np.median(table.floats("rlnCtfMaxResolution"))), 2)
    if table.has("rlnMicrographMetadata"):
        meta["has_motion"] = True
    meta.update(extra)
    return meta


def _float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def particles_out_meta(ctx: JobContext, star: Path, kind: str, **extra: Any) -> dict[str, Any]:
    """Metadata of a particle STAR file written by a RELION job (read later by every particle job)."""
    tables = rl.read_star(star)
    ptab = rl.particle_table(tables)
    box, apix = rl.grid_from_star(tables)
    meta: dict[str, Any] = {"format": "star", "datadir": str(ctx.project_dir), "n_particles": len(ptab), "box": box,
                            "pixel_size": round(apix, 4), "has_images": True, "has_poses": ptab.has("rlnAngleRot"),
                            "has_ctf": ptab.has("rlnDefocusU"), "relion": {"star": ctx.rel(star), "kind": kind}}
    meta["relion"].update({k: v for k, v in extra.items() if k in ("optimiser", "model", "symmetry", "class_number")})
    meta.update({k: v for k, v in extra.items() if k not in ("optimiser", "model")})
    return meta


def micrograph_gallery(ctx: JobContext, names: list[str], name: str, title: str, picks: dict[str, np.ndarray] | None = None,
                       diameter_px: float = 0.0, size: int = 360, per_row: int = 2) -> Path | None:
    """Some micrographs side by side (with their picked particles circled) as a report image; returns the PNG."""
    from cryoplug.imaging import write_png
    from cryoplug.micrographs import picks_image, preview, read_image, tile_sheet
    tiles = []
    for mic in names:
        path = ctx.abs(mic)
        if not path.exists():
            continue
        try:
            if picks is not None:
                tiles.append(picks_image(path, picks.get(mic, np.zeros((0, 2))), diameter_px, size))
            else:
                tiles.append(preview(read_image(path), size)[0])
        except Exception as exc:  # pictures are cosmetic
            ctx.warn(f"Could not draw {mic}: {exc}")
    if not tiles:
        return None
    h = min(t.shape[0] for t in tiles)
    w = min(t.shape[1] for t in tiles)
    tiles = [t[:h, :w] for t in tiles]
    png = write_png(ctx.path(f"{name}.png"), tile_sheet(tiles, per_row=per_row, gap=4))
    ctx.add_image(title, png)
    return png


def thumbnail_of(ctx: JobContext, image: np.ndarray, name: str) -> Path:
    from cryoplug.imaging import write_png
    from cryoplug.micrographs import crop_to
    return write_png(ctx.path(f"thumb_{name}.png"), crop_to(image, 256))


def sample(items: list[Any], n: int) -> list[Any]:
    """``n`` items spread over the list (first, last and evenly between)."""
    if len(items) <= n:
        return list(items)
    return [items[int(round(i))] for i in np.linspace(0, len(items) - 1, n)]


# ------------------------------------------------------------------ progress
class FileProgress:
    """Progress of a program that writes one file per micrograph: the files matching ``pattern`` under ``folder``
    are counted every few seconds while it runs."""

    def __init__(self, ctx: JobContext, folder: Path, pattern: str, total: int, label: str,
                 start: float = 0.05, end: float = 0.9, every: float = 3.0):
        self.ctx, self.folder, self.pattern, self.total, self.label = ctx, folder, pattern, max(1, total), label
        self.start, self.end, self.every = start, end, every
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.done = 0

    def count(self) -> int:
        try:
            return sum(1 for _ in self.folder.rglob(self.pattern))
        except OSError:
            return self.done

    def _loop(self) -> None:
        while not self._stop.wait(self.every):
            n = self.count()
            if n != self.done:
                self.done = n
                frac = min(1.0, n / self.total)
                self.ctx.progress(self.start + (self.end - self.start) * frac,
                                  f"{self.label}: {min(n, self.total):,} of {self.total:,}", log=False)

    def __enter__(self) -> "FileProgress":
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)


def histogram(values: np.ndarray, bins: int = 30, lo: float | None = None, hi: float | None = None) -> dict[str, list[float]]:
    """Counts of ``values`` in ``bins`` (bar chart series: x = bin centres)."""
    values = np.asarray(values, dtype=np.float64)
    values = values[np.isfinite(values)]
    if not len(values):
        return {"x": [], "y": []}
    lo = float(values.min()) if lo is None else lo
    hi = float(values.max()) if hi is None else hi
    if hi <= lo:
        hi = lo + 1.0
    counts, edges = np.histogram(values, bins=bins, range=(lo, hi))
    centres = (edges[:-1] + edges[1:]) / 2
    return {"x": [round(float(c), 4) for c in centres], "y": [int(c) for c in counts]}
