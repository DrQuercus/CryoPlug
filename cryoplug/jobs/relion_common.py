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


# ------------------------------------------------------------------ diagnostics of refinements and classifications
def viewing_directions(ctx: JobContext, ptab: rl.StarTable, title: str = "Viewing directions", key: str | None = None,
                       note: str = "") -> None:
    """Distribution of the viewing directions (azimuth = rlnAngleRot, elevation = 90° − rlnAngleTilt; % of the
    particles), as CryoSPARC's viewing direction plot."""
    if not (ptab.has("rlnAngleRot") and ptab.has("rlnAngleTilt")) or not len(ptab):
        return
    az = (ptab.floats("rlnAngleRot") + 180.0) % 360.0 - 180.0
    el = 90.0 - np.clip(ptab.floats("rlnAngleTilt"), 0.0, 180.0)
    hist, _y, _x = np.histogram2d(el, az, bins=[12, 24], range=[[-90, 90], [-180, 180]])
    hist = hist[::-1] * 100.0 / max(1, len(ptab))  # top row = +90°
    ylab = [f"{82.5 - 15 * i:+.0f}°" for i in range(12)]
    xlab = [f"{-180 + 15 * j}°" for j in range(24)]
    ctx.add_heatmap(title, xlab, ylab, hist.round(3).tolist(), unit="%", x_label="Azimuth (rot)", y_label="Elevation (90° − tilt)",
                    key=key, note=note or "Even coverage is best; empty regions are views missing from the data (preferred "
                                          "orientation), which stretch the map along those directions.")


def iteration_history(folder: Path, root: str = "run") -> list[dict[str, Any]]:
    """Per iteration of relion_refine: the share and estimated resolution of every class, and the current resolution
    (``run_itNNN_model.star``; the first half set of an auto-refinement)."""
    import re
    found = []
    for p in folder.glob(f"{root}_it*_model.star"):
        m = re.fullmatch(rf"{re.escape(root)}_it(\d+)(_half1)?_model\.star", p.name)
        if m:
            found.append((int(m.group(1)), p))
    out = []
    for it, path in sorted(found):
        try:
            tables = rl.read_star(path)
        except (OSError, ValueError):
            continue
        rec: dict[str, Any] = {"iteration": it}
        classes = tables.get("model_classes")
        if classes is not None and classes.has("rlnClassDistribution"):
            rec["distribution"] = [float(v) for v in classes.floats("rlnClassDistribution")]
            if classes.has("rlnEstimatedResolution"):
                rec["resolution"] = [float(v) for v in classes.floats("rlnEstimatedResolution")]
        general = tables.get("model_general")
        if general is not None and general.value("rlnCurrentResolution") is not None:
            try:
                rec["current_resolution"] = float(general.value("rlnCurrentResolution"))
            except ValueError:
                pass
        out.append(rec)
    return out


def refinement_diagnostics(ctx: JobContext, data_star: Path, folder: Path | None = None, root: str = "run",
                           per_class: bool = False) -> None:
    """CryoSPARC-like diagnostics of a refinement or classification: viewing directions (of each large class too),
    posterior precision of the poses, shifts, and the share of each class over the iterations."""
    try:
        ptab = rl.particle_table(rl.read_star(data_star))
    except (OSError, ValueError) as exc:
        ctx.warn(f"No diagnostics: {exc}")
        return
    n = len(ptab)
    if not n:
        return
    classes = np.array([int(float(c)) for c in ptab.column("rlnClassNumber")]) if ptab.has("rlnClassNumber") else np.ones(n, int)
    viewing_directions(ctx, ptab, "Viewing directions (all particles)" if per_class else "Viewing directions", key="views")
    if per_class:
        values, counts = np.unique(classes, return_counts=True)
        big = [int(c) for c, k in sorted(zip(values, counts), key=lambda t: -t[1]) if k >= 0.05 * n][:6]
        for c in sorted(big):
            viewing_directions(ctx, ptab.select(np.flatnonzero(classes == c)), f"Viewing directions of class {c}",
                               key=f"views_{c}", note=f"{int((classes == c).sum()):,} particles.")
    if ptab.has("rlnMaxValueProbDistribution"):
        prob = ptab.floats("rlnMaxValueProbDistribution")
        ctx.add_plot("Posterior precision of the poses", [{"name": "Particles", **histogram(prob, lo=0.0, hi=1.0)}],
                     x_label="Probability of the best pose (rlnMaxValueProbDistribution)", y_label="Particles", style="bars",
                     note="Near 1: the particle has one clear pose. Many particles near 0: poor or ambiguous alignment "
                          "(junk, heterogeneity, too small a particle for the resolution).")
    if ptab.has("rlnOriginXAngst") and ptab.has("rlnOriginYAngst"):
        shift = np.hypot(ptab.floats("rlnOriginXAngst"), ptab.floats("rlnOriginYAngst"))
        ctx.add_plot("Particle shifts", [{"name": "Particles", **histogram(shift, lo=0.0)}], x_label="Shift (Å)",
                     y_label="Particles", style="bars",
                     note="Shifts of the particles from their picked centres. A tail of large shifts: picks off-centre "
                          "(re-extract with re-centring) or a box too small.")
    history = iteration_history(folder or data_star.parent, root)
    shares = [h for h in history if h.get("distribution")]
    k = max((len(h["distribution"]) for h in shares), default=0)
    if per_class and k > 1 and len(shares) > 1:
        x = [h["iteration"] for h in shares]
        ctx.add_plot("Share of each class over the iterations",
                     [{"name": f"Class {c + 1}", "x": x, "y": [round(100 * h["distribution"][c], 2) if c < len(h["distribution"]) else None
                                                              for h in shares]} for c in range(k)],
                     x_label="Iteration", y_label="Particles (%)",
                     note="Classes still exchanging particles at the end have not converged: more iterations, or a "
                          "different regularisation T.")
        if any(h.get("resolution") for h in shares):
            ctx.add_plot("Estimated resolution of each class",
                         [{"name": f"Class {c + 1}", "x": x,
                           "y": [round(h["resolution"][c], 2) if h.get("resolution") and c < len(h["resolution"]) and h["resolution"][c] < 900 else None
                                 for h in shares]} for c in range(k)],
                         x_label="Iteration", y_label="Resolution (Å)")


# ------------------------------------------------------------------ helical processing (RELION's GUI options)
def _f(value: float) -> str:
    return f"{value:g}"


def helical_params(kind: str) -> list[Param]:
    """The helical options of RELION's GUI for one kind of job (src/pipeline_jobs.cpp), shown once "Helical" is ticked:
    'autopick' (template picking of segments), 'extract', 'class2d', '3d' (classification, refinement), 'mask'."""
    s = "helical"
    on = Param("helical", "bool", False, label="Helical processing",
               help="Filaments (amyloids, helical assemblies), with RELION's helical options.")
    outer = Param("helical_outer_diameter", "float", 200.0, label="Tube diameter", unit="Å", min=0, show_if=s,
                  help="Outer diameter of the filament (a bit larger than it).")
    asu = Param("helical_nr_asu", "int", 1, label="Asymmetric units per segment", min=1, max=100, show_if=s)
    rise = Param("helical_rise", "float", 0.0, label="Helical rise", unit="Å", min=0, show_if=s)
    if kind == "autopick":
        return [on, outer, asu, rise,
                Param("helical_kappa_max", "float", 0.1, label="Maximum curvature (kappa)", min=0, max=1, show_if=s,
                      help="0.05-0.5: rigid to curved filaments."),
                Param("helical_length_min", "float", -1.0, label="Minimum filament length", unit="Å", show_if=s,
                      help="Shorter filaments are left out. -1 = none."),
                Param("helical_amyloid", "bool", False, label="Amyloid picking", show_if=s)]
    if kind == "extract":
        return [on, outer, asu, rise,
                Param("helical_bimodal", "bool", True, label="Bimodal angular priors", show_if=s,
                      help="The polarity of the filament is unknown: in-plane angles ψ and ψ + 180° both possible.")]
    if kind == "class2d":
        return [on, outer,
                Param("helical_bimodal", "bool", True, label="Bimodal in-plane angle search", show_if=s),
                Param("helical_range_psi", "float", 6.0, label="In-plane angular search range", unit="°", min=0, max=90,
                      show_if=s),
                Param("helical_restrict_offsets", "bool", True, label="Restrict offsets along the filament", show_if=s,
                      help="Offsets along the axis at most half the rise."),
                Param("helical_rise", "float", 4.75, label="Helical rise", unit="Å", min=0, show_if=s,
                      help="4.75 Å for amyloids (the distance between β-strands).")]
    if kind == "mask":
        return [on, Param("helical_z_percentage", "float", 30.0, label="Central part along the axis", unit="%", min=5,
                          max=100, show_if=s, help="Part of the box height kept along the filament axis.")]
    # 3d: classification and refinement
    return [
        on, Param("helical_inner_diameter", "float", -1.0, label="Inner tube diameter", unit="Å", show_if=s,
                  help="Hollow filaments; -1 = solid."),
        outer, asu,
        Param("helical_twist", "float", 0.0, label="Initial twist", unit="°", show_if=s),
        Param("helical_rise_initial", "float", 0.0, label="Initial rise", unit="Å", min=0, show_if=s),
        Param("helical_z_percentage", "float", 30.0, label="Central part along the axis", unit="%", min=5, max=100,
              show_if=s, help="Part of the box height used to impose the helical symmetry."),
        Param("helical_keep_tilt", "bool", True, label="Keep the tilt prior fixed", show_if=s),
        Param("helical_range_tilt", "float", 15.0, label="Tilt search range", unit="°", min=0, max=90, show_if=s),
        Param("helical_range_psi", "float", 10.0, label="In-plane search range", unit="°", min=0, max=90, show_if=s),
        Param("helical_range_rot", "float", -1.0, label="Rotation search range", unit="°", show_if=s, help="-1 = all."),
        Param("helical_range_distance", "float", -1.0, label="Range of segment distances", unit="Å", show_if=s,
              help="Restrains neighbouring segments of a filament. -1 = off."),
        Param("helical_nstart", "int", 1, label="Start number (n-start)", min=1, max=20, show_if=s),
        Param("helical_search", "bool", False, label="Local search of the helical symmetry", show_if=s),
        Param("helical_twist_min", "float", 0.0, label="Twist search: min", unit="°", show_if="helical_search"),
        Param("helical_twist_max", "float", 0.0, label="Twist search: max", unit="°", show_if="helical_search"),
        Param("helical_twist_step", "float", 0.0, label="Twist search: step", unit="°", show_if="helical_search"),
        Param("helical_rise_min", "float", 0.0, label="Rise search: min", unit="Å", show_if="helical_search"),
        Param("helical_rise_max", "float", 0.0, label="Rise search: max", unit="Å", show_if="helical_search"),
        Param("helical_rise_step", "float", 0.0, label="Rise search: step", unit="Å", show_if="helical_search"),
    ]


def helical_check(p: dict[str, Any]) -> list[str]:
    """Problems of the helical options (before queueing)."""
    if not p.get("helical"):
        return []
    problems = []
    if not float(p.get("helical_outer_diameter") or 0) > 0:
        problems.append("Helical: give the tube diameter")
    if "helical_rise_initial" in p and not float(p.get("helical_rise_initial") or 0) > 0:
        problems.append("Helical: give the initial rise")
    if p.get("helical_search") and not (float(p.get("helical_twist_max") or 0) > float(p.get("helical_twist_min") or 0)
                                        and float(p.get("helical_rise_max") or 0) > float(p.get("helical_rise_min") or 0)):
        problems.append("Helical symmetry search: give twist and rise ranges (max > min)")
    return problems


def _sigma(value: float) -> str:
    return _f(min(90.0, max(0.0, float(value))) / 3.0)


def helical_symmetry_args(p: dict[str, Any]) -> list[str]:
    args = ["--helix"]
    if float(p["helical_inner_diameter"]) > 0:
        args += ["--helical_inner_diameter", _f(p["helical_inner_diameter"])]
    args += ["--helical_outer_diameter", _f(p["helical_outer_diameter"]), "--helical_nr_asu", str(p["helical_nr_asu"]),
             "--helical_twist_initial", _f(p["helical_twist"]), "--helical_rise_initial", _f(p["helical_rise_initial"]),
             "--helical_z_percentage", _f(float(p["helical_z_percentage"]) / 100.0)]
    if p["helical_search"]:
        args += ["--helical_symmetry_search", "--helical_twist_min", _f(p["helical_twist_min"]),
                 "--helical_twist_max", _f(p["helical_twist_max"])]
        if float(p["helical_twist_step"]) > 0:
            args += ["--helical_twist_inistep", _f(p["helical_twist_step"])]
        args += ["--helical_rise_min", _f(p["helical_rise_min"]), "--helical_rise_max", _f(p["helical_rise_max"])]
        if float(p["helical_rise_step"]) > 0:
            args += ["--helical_rise_inistep", _f(p["helical_rise_step"])]
    return args


def _helical_priors(p: dict[str, Any]) -> list[str]:
    return ["--sigma_tilt", _sigma(p["helical_range_tilt"]), "--sigma_psi", _sigma(p["helical_range_psi"]),
            "--sigma_rot", _sigma(p["helical_range_rot"])]


def _helical_distance(p: dict[str, Any]) -> list[str]:
    if float(p["helical_range_distance"]) > 0:
        return ["--helical_sigma_distance", _f(float(p["helical_range_distance"]) / 3.0), "--helical_nstart", str(p["helical_nstart"])]
    return []


def helical_class3d_args(p: dict[str, Any], aligned: bool, local: bool) -> list[str]:
    """3D classification (RELION's GUI order): symmetry, tilt prior, then the priors of global searches."""
    if not p.get("helical"):
        return []
    args = helical_symmetry_args(p)
    if p["helical_keep_tilt"]:
        args.append("--helical_keep_tilt_prior_fixed")
    if aligned and not local:
        args += _helical_priors(p) + _helical_distance(p)
    return args


def helical_refine_args(p: dict[str, Any], local_differs: bool) -> list[str]:
    """3D auto-refine (RELION's GUI order): symmetry, priors when the first sampling is not the local one, distance,
    tilt prior."""
    if not p.get("helical"):
        return []
    args = helical_symmetry_args(p)
    if local_differs:
        args += _helical_priors(p)
    args += _helical_distance(p)
    if p["helical_keep_tilt"]:
        args.append("--helical_keep_tilt_prior_fixed")
    return args


def helical_class2d_args(p: dict[str, Any], aligned: bool) -> list[str]:
    if not p.get("helical"):
        return []
    args = ["--helical_outer_diameter", _f(p["helical_outer_diameter"]), "--no_init_blobs"]
    if aligned:
        if p["helical_bimodal"]:
            args.append("--bimodal_psi")
        args += ["--sigma_psi", _sigma(p["helical_range_psi"])]
        if p["helical_restrict_offsets"]:
            args += ["--helix", "--helical_rise_initial", _f(p["helical_rise"])]
    return args


def helical_extract_args(p: dict[str, Any]) -> list[str]:
    if not p.get("helical"):
        return []
    args = ["--helix", "--helical_outer_diameter", _f(p["helical_outer_diameter"])]
    if p["helical_bimodal"]:
        args.append("--helical_bimodal_angular_priors")
    return args + ["--helical_cut_into_segments", "--helical_nr_asu", str(p["helical_nr_asu"]),
                   "--helical_rise", _f(p["helical_rise"])]
