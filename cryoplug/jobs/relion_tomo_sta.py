"""RELION 5 subtomogram averaging, from tomograms to the map: particle picking in the tomograms (the job panel instead of
RELION's napari picker, writing the same annotations and the particle set RELION derives from them), coordinate import,
subtomogram extraction, particle reconstruction, initial model, 3D classification and auto-refinement of subtomograms
(relion_refine --ios), CTF refinement and frame alignment (Bayesian polishing) of the tilt series.

Subtomogram particles flow from job to job as optimisation sets (cryoplug.tomo, cryoplug.jobs.tomo_common).
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import numpy as np

from cryoplug import relion as rl
from cryoplug import tomo as tm
from cryoplug.jobs import register
from cryoplug.jobs.base import JobContext, JobError, JobType, OutputDef, Param, Slot, extra_args_param
from cryoplug.jobs.relion import symmetry_param
from cryoplug.jobs.relion_classify import RelionClass3DGlobal, RelionInitialModel
from cryoplug.jobs.relion_common import TOOL, RelionJob, mpi, relion_command, run_relion, sample, threads
from cryoplug.jobs.relion_refinement import RelionRefine3D
from cryoplug.jobs.relion_tomo import CATEGORY, tilt_set_input, tomogram_index
from cryoplug.jobs.tomo_common import tomo_output, tomo_particles


# ------------------------------------------------------------------ particle poses from annotations
def _rotation_tools():
    from scipy.spatial.transform import Rotation
    basis = Rotation.from_euler("y", 90, degrees=True).as_matrix()
    prior = Rotation.from_matrix(basis).as_euler("ZYZ", degrees=True)
    return Rotation, basis, prior


def _frame(z: np.ndarray, x_hint: np.ndarray | None = None) -> np.ndarray:
    """A rotation matrix whose third column (the particle's Z axis) is ``z``."""
    z = z / np.linalg.norm(z)
    hint = x_hint if x_hint is not None else (np.array([1.0, 0, 0]) if abs(z[0]) < 0.9 else np.array([0, 1.0, 0]))
    x = hint - np.dot(hint, z) * z
    if np.linalg.norm(x) < 1e-6:
        x = np.cross(z, [0, 0, 1.0]) if abs(z[2]) < 0.9 else np.cross(z, [0, 1.0, 0])
    x /= np.linalg.norm(x)
    return np.stack([x, np.cross(z, x), z], axis=1)


def sphere_poses(centre: np.ndarray, radius: float, spacing: float) -> tuple[np.ndarray, np.ndarray]:
    """Points about ``spacing`` apart on a sphere (Fibonacci lattice) and their orientations (Z along the normal)."""
    n = max(1, int(round(4 * np.pi * radius ** 2 / max(spacing, 1e-6) ** 2)))
    i = np.arange(n) + 0.5
    phi = np.arccos(1 - 2 * i / n)
    theta = np.pi * (1 + 5 ** 0.5) * i
    normals = np.stack([np.cos(theta) * np.sin(phi), np.sin(theta) * np.sin(phi), np.cos(phi)], axis=1)
    return centre + radius * normals, np.stack([_frame(v) for v in normals])


def filament_poses(points: np.ndarray, spacing: float) -> tuple[np.ndarray, np.ndarray, float]:
    """Positions ``spacing`` apart along a smooth curve through the clicked points, orientations with Z along the curve
    (no twist: parallel transport of the X axis), and the length of the curve."""
    from scipy import interpolate
    pts = [points[0]]
    for p in points[1:]:  # identical consecutive points break the spline (as RELION removes them)
        if np.linalg.norm(p - pts[-1]) > 1e-6:
            pts.append(p)
    pts = np.asarray(pts, dtype=float)
    if len(pts) < 2:
        raise JobError("A filament needs two different points at least")
    k = min(3, len(pts) - 1)
    tck, _ = interpolate.splprep(pts.T, k=k, s=0)
    u = np.linspace(0, 1, 2000)
    dense = np.stack(interpolate.splev(u, tck), axis=1)
    seg = np.linalg.norm(np.diff(dense, axis=0), axis=1)
    arc = np.concatenate([[0.0], np.cumsum(seg)])
    length = float(arc[-1])
    at = np.arange(0.0, length + 1e-6, spacing) if spacing > 0 else np.array([0.0])
    uu = np.interp(at, arc, u)
    pos = np.stack(interpolate.splev(uu, tck), axis=1)
    tangents = np.stack(interpolate.splev(uu, tck, der=1), axis=1)
    frames, x_prev = [], None
    for t in tangents:
        f = _frame(t, x_prev)
        x_prev = f[:, 0]
        frames.append(f)
    return pos, np.stack(frames), length


def derive_poses(mode: str, picks: dict[str, Any], tset: tm.TiltSeriesSet, spacing: float = 0.0,
                 polarity_known: bool = False) -> rl.StarTable:
    """The particle set RELION's get_particle_poses derives from napari annotations (binned tomogram voxels): centred
    positions in Å, and for spheres and filaments the orientations relative to the surface or the axis with their
    priors (rlnTomoSubtomogram* and rlnAngle*Prior)."""
    rows: list[list[Any]] = []
    Rotation, basis, prior = _rotation_tools()

    def geometry(ts: str) -> tuple[list[int], float, float]:
        size = [int(float(tset.value(ts, lab) or 0)) for lab in ("rlnTomoSizeX", "rlnTomoSizeY", "rlnTomoSizeZ")]
        pixel = float(tset.value(ts, "rlnTomoTiltSeriesPixelSize") or tset.value(ts, "rlnMicrographOriginalPixelSize") or 1)
        return size, pixel, float(tset.value(ts, "rlnTomoTomogramBinning") or 1)

    def eulers(frames: np.ndarray) -> np.ndarray:
        return Rotation.from_matrix(frames @ basis).inv().as_euler("ZYZ", degrees=True)

    for ts in tset.names:
        items = picks.get(ts) or []
        if not items:
            continue
        size, pixel, binning = geometry(ts)
        scale = pixel * binning
        if mode == "particles":
            xyz = tm.from_binned_voxels(np.asarray(items, dtype=float)[:, :3], size, pixel, binning)
            rows += [[ts, *v] for v in xyz]
        elif mode == "spheres":
            for item in items:
                centre = tm.from_binned_voxels(np.asarray(item[:3], dtype=float)[None], size, pixel, binning)[0]
                pos, frames = sphere_poses(centre, float(item[3]) * scale, spacing)
                rows += [[ts, *p, *e] for p, e in zip(pos, eulers(frames))]
        else:  # filaments
            for tube, points in enumerate(items, start=1):
                xyz = tm.from_binned_voxels(np.asarray(points, dtype=float)[:, :3], size, pixel, binning)
                pos, frames, length = filament_poses(xyz, spacing)
                along = np.linspace(0, 1, len(pos)) * length / pixel  # as RELION writes rlnHelicalTrackLengthAngst
                rows += [[ts, tube, d, *p, *e] for d, p, e in zip(along, pos, eulers(frames))]
    if not rows:
        raise JobError("No particle was picked")
    if mode == "particles":
        labels = ["rlnTomoName", "rlnCenteredCoordinateXAngst", "rlnCenteredCoordinateYAngst", "rlnCenteredCoordinateZAngst"]
    elif mode == "spheres":
        labels = ["rlnTomoName", "rlnCenteredCoordinateXAngst", "rlnCenteredCoordinateYAngst", "rlnCenteredCoordinateZAngst",
                  "rlnTomoSubtomogramRot", "rlnTomoSubtomogramTilt", "rlnTomoSubtomogramPsi"]
    else:
        labels = ["rlnTomoName", "rlnHelicalTubeID", "rlnHelicalTrackLengthAngst", "rlnCenteredCoordinateXAngst",
                  "rlnCenteredCoordinateYAngst", "rlnCenteredCoordinateZAngst", "rlnTomoSubtomogramRot",
                  "rlnTomoSubtomogramTilt", "rlnTomoSubtomogramPsi"]
    table = rl.StarTable("particles", labels, [[r[0], *[rl._fmt(float(v)) if not isinstance(v, int) else str(v) for v in r[1:]]]
                                               for r in rows])
    if mode != "particles":
        n = len(table)
        for label, value in (("rlnAngleRot", prior[0]), ("rlnAngleTilt", prior[1]), ("rlnAnglePsi", prior[2]),
                             ("rlnAngleTiltPrior", prior[1]), ("rlnAnglePsiPrior", prior[2])):
            table.set_column(label, [float(value)] * n)
        if mode == "filaments" and not polarity_known:
            table.set_column("rlnAnglePsiFlipRatio", [0.5] * n)
    return table


def write_annotations(folder: Path, mode: str, picks: dict[str, Any]) -> list[Path]:
    """The annotation files of RELION's napari picker (one per tomogram, binned voxels), so that the picks can also be
    opened in RELION."""
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for ts, items in picks.items():
        if not items:
            continue
        f = folder / f"{ts}_{mode}.star"
        if mode == "filaments":
            rows = [[str(i), *[rl._fmt(float(v)) for v in pt[:3]]] for i, pts in enumerate(items) for pt in pts]
            table = rl.StarTable("", ["rlnTomoManifoldIndex", "rlnCoordinateX", "rlnCoordinateY", "rlnCoordinateZ"], rows)
        elif mode == "spheres":
            table = rl.StarTable("", ["rlnCoordinateX", "rlnCoordinateY", "rlnCoordinateZ", "rlnSphereRadius"],
                                 [[rl._fmt(float(v)) for v in it[:4]] for it in items])
        else:
            table = rl.StarTable("", ["rlnCoordinateX", "rlnCoordinateY", "rlnCoordinateZ"],
                                 [[rl._fmt(float(v)) for v in it[:3]] for it in items])
        out.append(rl.write_star(f, [table]))
    return out


# ------------------------------------------------------------------ picking
@register
class RelionTomoPick(JobType):
    name = "relion_tomo_pick"
    title = "Pick particles in tomograms"
    category = CATEGORY
    interactive = True
    tool = TOOL
    software = ["RELION"]
    description = ("Picks particles in the tomograms, slice by slice, as RELION's napari picker: single particles (a "
                   "click on each), spheres (vesicles, virus capsids: a centre and a radius, particles sampled on the "
                   "surface) or filaments (points along each tube, particles sampled along it), with their orientations "
                   "relative to the surface or the axis. Writes the annotations and the particle set RELION derives from "
                   "them (particles.star, optimisation_set.star).")
    inputs = [Slot("tomograms", ("tomograms",), "Tomograms", help="Reconstructed (best denoised) tomograms."),
              Slot("particles", ("tomo_particles",), "Particles to edit", required=False,
                   help="Picks of an earlier job, shown to add to or remove from (single particles).")]
    params = [
        Param("mode", "choice", "particles", choices=["particles", "spheres", "filaments"], label="Picking",
              labels={"particles": "Particles (a click on each)", "spheres": "Spheres (vesicles, capsids)",
                      "filaments": "Filaments (points along each)"}),
        Param("diameter", "float", 150.0, label="Particle diameter", unit="Å", min=1, help="Size of the circles shown."),
        Param("spacing", "float", 60.0, label="Particle spacing", unit="Å", min=1, show_if="mode=spheres|filaments",
              help="Distance between the particles sampled on the spheres or along the filaments."),
        Param("polarity_known", "bool", False, label="Filament polarity known", show_if="mode=filaments",
              help="The filaments were clicked from their start to their end (otherwise both directions are refined)."),
    ]
    outputs = [OutputDef("particles", "tomo_particles", "Picked particles")]

    @classmethod
    def validate(cls, params: dict[str, Any], connected: dict[str, str]) -> list[str]:
        if params.get("mode", "particles") != "particles" and "particles" in connected:
            return ["Earlier picks can be edited in the particle mode only"]
        return []

    def prepare(self, ctx: JobContext) -> bool:
        p = ctx.params
        star, ref, tset = tilt_set_input(ctx, "tomograms")
        if not tm.is_reconstructed(tset):
            raise JobError("These are not reconstructed tomograms (no rlnTomoReconstructedTomogram)")
        index_path = tomogram_index(ctx, tset, "pick_index.json")
        index = json.loads(index_path.read_text())
        if not index["tomograms"]:
            raise JobError("None of the tomograms can be read")
        initial: dict[str, list[list[float]]] = {}
        given = ctx.input("particles")
        if given is not None:
            parts = tomo_particles(ctx, given, extracted=False)
            ptab = parts["tables"].get("particles") or rl.particle_table(parts["tables"])
            if not all(ptab.has(f"rlnCenteredCoordinate{a}Angst") for a in "XYZ"):
                raise JobError("These particles have no centred coordinates (rlnCenteredCoordinateXAngst)")
            xyz = tm.centered_positions(ptab)
            names = ptab.column("rlnTomoName")
            for ts in tset.names:
                sel = [i for i, n in enumerate(names) if n == ts]
                if not sel:
                    continue
                size = [int(float(tset.value(ts, lab) or 0)) for lab in ("rlnTomoSizeX", "rlnTomoSizeY", "rlnTomoSizeZ")]
                pixel = float(tset.value(ts, "rlnTomoTiltSeriesPixelSize") or 1)
                binning = float(tset.value(ts, "rlnTomoTomogramBinning") or 1)
                vox = tm.to_binned_voxels(xyz[sel], size, pixel, binning)
                initial[ts] = [[round(float(v), 2) for v in row] for row in vox]
        binned_px = float(index["tomograms"][0]["pixel"]) or 1.0
        index.update({"mode": p["mode"], "diameter_px": round(p["diameter"] / binned_px, 2), "initial": initial,
                      "star": ref})
        index_path.write_text(json.dumps(index))
        ctx.path("pick.json").write_text(json.dumps({"star": ref, "mode": p["mode"]}))
        prompts = {"particles": "Click each particle on the slice where it is centred (click it again to remove it); the "
                                "wheel or ↑ ↓ change the slice, ← → the tomogram. Then Done.",
                   "spheres": "Click the centre of each sphere on its middle slice, then a point of its edge; click a "
                              "centre again to remove the sphere. Then Done.",
                   "filaments": "Click points along each filament (on the slices where it runs), Enter ends a filament; "
                                "click a point of a filament to remove it. Then Done."}
        ctx.add_tool("tomopick", "Pick particles", index_path, waiting=True, editable=True, mode=p["mode"],
                     prompt_title="Waiting for your picks", prompt=prompts[p["mode"]])
        ctx.add_highlight("Tomograms", f"{len(index['tomograms']):,}")
        if initial:
            ctx.add_highlight("Earlier picks", f"{sum(len(v) for v in initial.values()):,}")
        ctx.log("Waiting: pick the particles in the job panel, then Done")
        return False

    def finalize(self, ctx: JobContext, choice: str | None) -> None:
        p = ctx.params
        state = json.loads(ctx.path("pick.json").read_text())
        try:
            sent = json.loads(choice) if (choice or "").strip() else {}
        except ValueError as exc:
            raise JobError(f"Picks not understood: {exc}") from None
        if not sent:
            saved = ctx.path("session_picks.json")
            sent = json.loads(saved.read_text()) if saved.exists() else {}
        mode = state["mode"]
        picks = sent.get(mode) or {}
        if not any(picks.values()):
            raise JobError("No particle was picked")
        star = ctx.abs(state["star"])
        tset = tm.read_set(star, ctx.project_dir)
        write_annotations(ctx.path("annotations"), mode, picks)
        table = derive_poses(mode, picks, tset, float(p.get("spacing") or 0), bool(p.get("polarity_known")))
        pstar = rl.write_star(ctx.path("particles.star"), [table])
        tm.write_optimisation_set(ctx.path("optimisation_set.star"), particles=ctx.rel(pstar), tomograms=state["star"])
        names = table.column("rlnTomoName")
        rows = [[ts, len(picks.get(ts) or []), names.count(ts)] for ts in tset.names if picks.get(ts)]
        what = {"particles": "Picks", "spheres": "Spheres", "filaments": "Filaments"}[mode]
        ctx.add_table("Picked particles", ["Tomogram", what, "Particles"], rows)
        ctx.add_highlight("Particles", f"{len(table):,}")
        for sec in ctx.report.get("sections", []):  # done: the picker becomes read-only
            if sec.get("kind") == "tomopick":
                sec.update(waiting=False, editable=False)
        ctx.save_report()
        parts = {"tomograms": state["star"], "trajectories": None}
        tomo_output(ctx, "particles", pstar, f"{len(table):,} particles in {len(rows)} tomograms", parts, f"pick_{mode}",
                    opt=ctx.path("optimisation_set.star"))


# ------------------------------------------------------------------ coordinate import
@register
class RelionTomoImportCoordinates(RelionJob):
    name = "relion_tomo_import_coords"
    title = "Import tomogram coordinates"
    category = CATEGORY
    gpu_param = ""
    cpu_param = ""
    description = ("Imports particle positions picked elsewhere (crYOLO, PyTom, Dynamo, IMOD models converted, template "
                   "matching): STAR files or text files with three columns (X Y Z), or six with the subtomogram angles "
                   "(relion_tomo_import_coordinates), one file per tomogram named after it.")
    inputs = [Slot("tomograms", ("tomograms",), "Tomograms", help="Those the coordinates belong to.")]
    params = [
        Param("files", "path", "", label="Coordinate files", placeholder="/data/coords/*.coords",
              help="A wildcard over the files; each file name, without the substrings below, is the tomogram name."),
        Param("remove", "str", "", label="Remove from the file names", placeholder="_coords",
              help="Removed from the file names to get the tomogram names."),
        Param("remove2", "str", "", label="Also remove", advanced=True),
        Param("centred", "bool", False, label="Coordinates relative to the tomogram centre",
              help="In Å from the centre (rlnCenteredCoordinate). Otherwise pixels of the unbinned tilt series from the corner."),
        Param("scale", "float", 1.0, label="Multiply the coordinates by", min=0,
              help="E.g. the binning of the tomograms they were picked in, to get unbinned pixels."),
        Param("add", "float", 0.0, label="Then add", advanced=True),
    ]
    outputs = [OutputDef("particles", "tomo_particles", "Particles")]

    def run(self, ctx: JobContext) -> None:
        p = ctx.params
        star, ref, tset = tilt_set_input(ctx, "tomograms")
        pattern = str(p["files"]).strip()
        if not pattern:
            raise JobError("Give the coordinate files")
        args = [ctx.program(TOOL, "relion_tomo_import_coordinates"), "--i", pattern, "--o", ctx.rel(ctx.job_dir) + "/"]
        if str(p["remove"]).strip():
            args += ["--remove_substring", str(p["remove"]).strip()]
        if str(p["remove2"]).strip():
            args += ["--remove_substring2", str(p["remove2"]).strip()]
        if p["centred"]:
            args.append("--centered")
        args += ["--scale_factor", f"{p['scale']:g}", "--add_factor", f"{p['add']:g}"]
        ctx.progress(0.2, "relion_tomo_import_coordinates")
        run_relion(ctx, args)
        pstar = ctx.path("particles.star")
        if not pstar.exists():
            raise JobError("relion_tomo_import_coordinates wrote no particles.star (see the log)")
        tables = rl.read_star(pstar)
        ptab = tables.get("particles") or next(iter(tables.values()))
        names = ptab.column("rlnTomoName") if ptab.has("rlnTomoName") else []
        unknown = sorted(set(names) - set(tset.names))
        if unknown:
            ctx.warn(f"Coordinates of tomograms not in the input ({', '.join(unknown[:5])}): fix the names to remove")
        ctx.add_table("Imported coordinates", ["Tomogram", "Particles"],
                      [[ts, names.count(ts)] for ts in sorted(set(names))])
        ctx.add_highlight("Particles", f"{len(ptab):,}")
        tm.write_optimisation_set(ctx.path("optimisation_set.star"), particles=ctx.rel(pstar), tomograms=ref)
        tomo_output(ctx, "particles", pstar, f"{len(ptab):,} particles", {"tomograms": ref}, "import_coords",
                    opt=ctx.path("optimisation_set.star"))


# ------------------------------------------------------------------ subtomogram extraction
@register
class RelionTomoExtract(RelionJob):
    name = "relion_tomo_extract"
    title = "Extract subtomograms (RELION)"
    category = CATEGORY
    gpu_param = ""
    cpu_param = "mpi*threads"
    description = ("Extracts the particles from the tilt series (relion_tomo_subtomo): for each particle the CTF-"
                   "premultiplied images of the tilts that see it, as a stack of 2D images (RELION 5), a 3D pseudo-"
                   "subtomogram (RELION 4) or a real-space subtomogram, binned to a box for refinement.")
    inputs = [Slot("particles", ("tomo_particles",), "Particles", help="Picked (or refined) particles.")]
    params = [
        Param("box_A", "float", 128.0, label="Box size", unit="Å", min=10,
              help="Final box in Å (about 1.5-2 × the particle)."),
        Param("binned_px", "int", 64, label="Binned box", unit="px", min=8, max=1024,
              help="The box above binned to this many pixels: smaller is faster, Nyquist lower."),
        Param("precrop_A", "float", -1.0, label="Pre-crop box", unit="Å",
              help="A larger box cut from the tilt images before CTF premultiplication, then cropped (captures the "
                   "signal the CTF delocalises); −1: none."),
        Param("max_dose", "float", -1.0, label="Highest dose", unit="e⁻/Å²",
              help="Tilts that received more are left out (−1: all)."),
        Param("min_frames", "int", 1, label="Fewest tilts", min=1, max=100,
              help="Particles seen in fewer tilts (below the highest dose) are left out."),
        Param("format", "choice", "stack2d", choices=["stack2d", "pseudo3d", "real3d"], label="Subtomograms",
              labels={"stack2d": "2D stacks (RELION 5)", "pseudo3d": "3D pseudo-subtomograms (RELION 4)",
                      "real3d": "Real-space 3D subtomograms"},
              help="Real-space subtomograms are sometimes better for initial alignment."),
        Param("float16", "bool", True, label="Write in float16"),
        mpi(1),
        threads(8),
        extra_args_param(),
    ]
    outputs = [OutputDef("particles", "tomo_particles", "Subtomograms")]

    def run(self, ctx: JobContext) -> None:
        p = ctx.params
        parts = tomo_particles(ctx, ctx.require("particles"), extracted=False)
        args = [*relion_command(ctx, "relion_tomo_subtomo", p["mpi"]), "--i", parts["ios"], "--theme", "classic",
                "--o", ctx.rel(ctx.job_dir) + "/", "--box_size_A", f"{p['box_A']:g}", "--precrop_size_A", f"{p['precrop_A']:g}",
                "--binned_size_pix", str(p["binned_px"])]
        if p["max_dose"] > 0:
            args += ["--max_dose", f"{p['max_dose']:g}"]
        if p["min_frames"] > 0:
            args += ["--min_frames", str(p["min_frames"])]
        if p["format"] == "stack2d":
            args.append("--stack2d")
        elif p["format"] == "real3d":
            args.append("--real_subtomo")
        if p["float16"]:
            args.append("--float16")
        args += ["--j", str(p["threads"]), *ctx.split_extra()]
        ctx.add_table("Extraction", ["Setting", "Value"], [
            ["Particles", f"{parts['n']:,}"], ["Box", f"{p['box_A']:g} Å, binned to {p['binned_px']} px"],
            ["Subtomograms", {"stack2d": "2D stacks", "pseudo3d": "3D pseudo-subtomograms", "real3d": "real-space 3D"}[p["format"]]]])
        ctx.progress(0.05, f"relion_tomo_subtomo on {parts['n']:,} particles")
        run_relion(ctx, args)
        opt = ctx.path("optimisation_set.star")
        pstar = ctx.path("particles.star")
        if not (opt.exists() and pstar.exists()):
            raise JobError("relion_tomo_subtomo wrote no particles.star / optimisation_set.star (see the log)")
        tables = rl.read_star(pstar)
        ptab = tables.get("particles") or rl.particle_table(tables)
        if len(ptab) < parts["n"]:
            ctx.warn(f"{parts['n'] - len(ptab):,} particle(s) left out (outside the tomogram, or too few tilts)")
        ctx.add_highlight("Subtomograms", f"{len(ptab):,}")
        self.gallery(ctx, ptab)
        out = tomo_output(ctx, "particles", pstar, f"{len(ptab):,} subtomograms", parts, "subtomo", opt=opt)
        if out["meta"].get("box"):
            ctx.add_highlight("Box", f"{out['meta']['box']} px · {out['meta'].get('pixel_size', 0):g} Å/px")

    @staticmethod
    def gallery(ctx: JobContext, ptab: rl.StarTable) -> None:
        """A few subtomograms: the central (zero-tilt) image of a 2D stack, or the central slice of a 3D one."""
        from cryoplug.imaging import write_png
        from cryoplug.micrographs import preview, tile_sheet
        tiles = []
        for name in sample(ptab.column("rlnImageName"), 8):
            try:
                import mrcfile
                with mrcfile.open(str(ctx.abs(name.split("@")[-1])), permissive=True) as m:
                    data = np.asarray(m.data, dtype=np.float32)
                img = data[len(data) // 2] if data.ndim == 3 else data
                tiles.append(preview(img, 96, sigma=0.6)[0])
            except Exception:  # cosmetic
                continue
        if tiles:
            s = min(min(t.shape) for t in tiles)
            png = write_png(ctx.path("subtomograms.png"), tile_sheet([t[:s, :s] for t in tiles], per_row=8, gap=3))
            ctx.add_image("Subtomograms (central image or slice)", png)


# ------------------------------------------------------------------ particle reconstruction
@register
class RelionTomoReconstructParticle(RelionJob):
    name = "relion_tomo_reconstruct_particle"
    title = "Reconstruct particle (RELION)"
    category = CATEGORY
    gpu_param = ""
    cpu_param = "mpi*threads"
    description = ("Reconstructs the average of the particles from the tilt images, in their current orientations "
                   "(relion_tomo_reconstruct_particle): the two half maps and the full map, at any binning and box. The "
                   "first average of particles picked on spheres or filaments, a map after refinement, CTF refinement or "
                   "polishing at full size.")
    inputs = [Slot("particles", ("tomo_particles",), "Particles"),
              Slot("mask", ("mask",), "Mask", required=False, help="For the masked FSC.")]
    params = [
        Param("binning", "float", 1.0, label="Binning", min=1, max=16, help="Of the tilt images (real-valued)."),
        Param("box", "int", 128, label="Box", unit="px", min=16, max=1024, help="Binned pixels."),
        Param("crop", "int", -1, label="Cropped box", unit="px", help="Also maps cropped to this box (−1: none)."),
        Param("snr", "float", 0.0, label="Wiener SNR constant", min=0, advanced=True,
              help="0: a heuristic against divisions by small numbers."),
        symmetry_param(),
        Param("helical", "bool", False, label="Apply helical symmetry"),
        Param("helical_asu", "int", 1, label="Unique asymmetrical units", min=1, show_if="helical"),
        Param("helical_twist", "float", -1.0, label="Helical twist", unit="°", show_if="helical"),
        Param("helical_rise", "float", 4.75, label="Helical rise", unit="Å", show_if="helical"),
        Param("helical_diameter", "float", 200.0, label="Outer helical diameter", unit="Å", show_if="helical"),
        Param("helical_z", "float", 20.0, label="Central Z length", unit="%", show_if="helical"),
        mpi(1),
        threads(8),
        extra_args_param(),
    ]
    outputs = [OutputDef("map", "map", "Map"), OutputDef("half_maps", "half_maps", "Half maps"), OutputDef("fsc", "fsc", "FSC")]

    def run(self, ctx: JobContext) -> None:
        from cryoplug.jobs.maptools import compute_and_report_fsc
        p = ctx.params
        parts = tomo_particles(ctx, ctx.require("particles"), extracted=False)
        sym = (str(p["symmetry"]).strip() or "C1").upper()
        out_dir = ctx.rel(ctx.job_dir) + "/"
        args = [*relion_command(ctx, "relion_tomo_reconstruct_particle", p["mpi"]), "--i", parts["ios"],
                "--theme", "classic", "--o", out_dir, "--b", str(p["box"])]
        if p["crop"] > 0:
            args += ["--crop", str(p["crop"])]
        args += ["--bin", f"{p['binning']:g}"]
        if p["snr"] > 0:
            args += ["--SNR", f"{p['snr']:g}"]
        if p["helical"]:
            args += ["--nr_helical_asu", str(p["helical_asu"]), "--helical_twist", f"{p['helical_twist']:g}",
                     "--helical_rise", f"{p['helical_rise']:g}"]
        args += ["--j", str(p["threads"]), "--j_out", str(p["threads"]), "--j_in", "1", "--sym", sym, *ctx.split_extra()]
        ctx.progress(0.05, f"relion_tomo_reconstruct_particle ({parts['n']:,} particles)")
        run_relion(ctx, args)
        # the half maps only with half sets (rlnRandomSubset, given by a refinement): not for freshly picked particles
        maps = {k: ctx.path(f"{k}.mrc") for k in ("merged", "half1", "half2") if ctx.path(f"{k}.mrc").exists()}
        if "merged" not in maps:
            raise JobError("relion_tomo_reconstruct_particle wrote no merged.mrc (see the log)")
        halves = "half1" in maps and "half2" in maps
        if p["helical"]:
            for key in list(maps):
                out = ctx.path(f"helical_{key}.mrc")
                run_relion(ctx, [ctx.program(TOOL, "relion_helix_toolbox"), "--impose", "--i", ctx.rel(maps[key]),
                                 "--o", ctx.rel(out), "--twist", f"{p['helical_twist']:g}", "--rise", f"{p['helical_rise']:g}",
                                 "--z_percentage", f"{p['helical_z'] / 100:g}", "--cyl_outer_diameter", f"{p['helical_diameter']:g}"])
                if out.exists():
                    maps[key] = out
        meta: dict[str, Any] = {"symmetry": sym, "relion_map": True}
        if halves:
            mask = ctx.input("mask")
            res = compute_and_report_fsc(ctx, maps["half1"], maps["half2"], mask.path if mask else None)
            meta["resolution"] = round(res, 3)
            ctx.add_highlight("Resolution", f"{res:.2f} Å")
        else:
            ctx.warn("The particles have no half sets yet (rlnRandomSubset, given by a refinement): one map of all the "
                     "particles, no half maps nor FSC")
        ctx.add_output("map", "map", maps["merged"], "Average", meta=meta)
        if halves:
            ctx.add_output("half_maps", "half_maps", [maps["half1"], maps["half2"]], "Half maps", meta=meta)
        ctx.add_highlight("Particles", f"{parts['n']:,}")


# ------------------------------------------------------------------ initial model, classification, refinement
SIGMA_TILT = Param("sigma_tilt", "float", -1.0, label="Prior width on the tilt angle", unit="°", min=-1, max=30,
                   help="For particles picked on spheres, surfaces or filaments (with tilt priors): the searches stay "
                        "within ± 3 × this of the prior. −1: no prior.")


def _tomo_slots(slots: list[Slot]) -> list[Slot]:
    return [dataclasses.replace(s, types=("tomo_particles",), help="Subtomograms (Extract subtomograms).")
            if s.name == "particles" else s for s in slots]


def _tomo_outputs(outputs: list[OutputDef]) -> list[OutputDef]:
    return [dataclasses.replace(o, type="tomo_particles") if o.type == "particles" else o for o in outputs]


@register
class RelionTomoInitialModel(RelionInitialModel):
    name = "relion_tomo_initial_model"
    title = "3D initial model (subtomograms)"
    category = CATEGORY
    description = ("A first map from the subtomograms without any reference (relion_refine --grad --denovo_3dref --ios, "
                   "VDAM), aligned on the symmetry axes and symmetrised: RELION 5's ab initio for tomography.")
    inputs = _tomo_slots(RelionInitialModel.inputs)
    params = [*RelionInitialModel.params[:-1], SIGMA_TILT, RelionInitialModel.params[-1]]
    outputs = _tomo_outputs(RelionInitialModel.outputs)


@register
class RelionTomoClass3D(RelionClass3DGlobal):
    name = "relion_tomo_class3d"
    title = "3D classification (subtomograms)"
    category = CATEGORY
    description = ("Sorts the subtomograms into 3D classes against a reference (relion_refine --K --ios): global or "
                   "local searches, or none. Every class gives a map and its particles.")
    inputs = _tomo_slots(RelionClass3DGlobal.inputs)
    params = [dataclasses.replace(prm, default=1.0) if prm.name == "tau2_fudge" else prm
              for prm in RelionClass3DGlobal.params[:-1]] + [SIGMA_TILT, RelionClass3DGlobal.params[-1]]
    outputs = _tomo_outputs(RelionClass3DGlobal.outputs)


@register
class RelionTomoRefine3D(RelionRefine3D):
    name = "relion_tomo_refine3d"
    title = "3D auto-refine (subtomograms)"
    category = CATEGORY
    description = ("Gold-standard 3D refinement of the subtomograms against a reference (relion_refine --auto_refine "
                   "--ios): global then local angular searches, two independent half sets. Gives the map, the half maps, "
                   "the FSC and the refined particles (for CTF refinement and frame alignment).")
    inputs = _tomo_slots(RelionRefine3D.inputs)
    params = [*RelionRefine3D.params[:-1], SIGMA_TILT, RelionRefine3D.params[-1]]
    outputs = _tomo_outputs(RelionRefine3D.outputs)


# ------------------------------------------------------------------ CTF refinement and frame alignment
def reference_args(ctx: JobContext) -> list[str]:
    """--ref1/--ref2 (the half maps), --mask and --fsc (the postprocess.star next to a post-processed map)."""
    halves = ctx.require("half_maps")
    if len(halves.files) < 2:
        raise JobError(f"{halves.source}: two half maps are needed")
    args = ["--ref1", ctx.rel(halves.files[0]), "--ref2", ctx.rel(halves.files[1])]
    mask = ctx.input("mask")
    if mask:
        args += ["--mask", ctx.rel(mask.path)]
    pp = ctx.input("postprocess")
    if pp:
        star = Path(pp.path).with_suffix(".star")
        if star.exists():
            args += ["--fsc", ctx.rel(star)]
        else:
            ctx.log(f"{pp.source}: no postprocess.star next to it; the FSC of the half maps is used")
    return args


REFERENCE_SLOTS = [
    Slot("particles", ("tomo_particles",), "Particles", help="Refined subtomograms."),
    Slot("half_maps", ("half_maps",), "Half maps", help="Of the refinement of these particles."),
    Slot("mask", ("mask",), "Mask", required=False),
    Slot("postprocess", ("map",), "Post-processed map", required=False, prefer=("map_sharp",),
         help="Of RELION's post-processing: its FSC weights the frequencies (--fsc)."),
]


@register
class RelionTomoCtfRefine(RelionJob):
    name = "relion_tomo_ctf_refine"
    title = "CTF refinement (tomography)"
    category = CATEGORY
    gpu_param = ""
    cpu_param = "mpi*threads"
    description = ("Refines the defocus of every tilt image and the contrast scale (ice thickness) against the "
                   "reference (relion_tomo_refine_ctf). Then reconstruct or refine the particles again.")
    inputs = REFERENCE_SLOTS
    params = [
        Param("box", "int", 128, label="Box for the estimation", unit="px", min=32, max=1024,
              help="Can be larger than the reference: captures the signal the CTF delocalises."),
        Param("defocus", "bool", True, label="Refine the defocus"),
        Param("defocus_range", "float", 3000.0, label="Defocus search range", unit="Å", min=0, show_if="defocus"),
        Param("regularise", "bool", False, label="Regularise the defocus", show_if="defocus",
              help="Keeps the defoci of a tilt series similar (the high tilts have little signal)."),
        Param("lambda_", "float", 0.1, label="Regularisation λ", min=0, max=1, show_if="regularise"),
        Param("scale", "bool", True, label="Refine the contrast scale"),
        Param("per_frame_scale", "bool", True, label="Scale per tilt image", show_if="scale",
              help="Otherwise the ice thickness, beam luminance and surface normal imply the scale of each tilt."),
        Param("per_tomo_scale", "bool", False, label="Scale per tomogram", show_if="scale", advanced=True),
        mpi(1),
        threads(8),
        extra_args_param(),
    ]
    outputs = [OutputDef("particles", "tomo_particles", "Particles"), OutputDef("tomograms", "tomograms", "Tomograms")]

    @classmethod
    def validate(cls, params: dict[str, Any], connected: dict[str, str]) -> list[str]:
        if params.get("per_frame_scale", True) and params.get("per_tomo_scale"):
            return ["The scale is estimated per tilt image or per tomogram, not both"]
        return []

    def run(self, ctx: JobContext) -> None:
        p = ctx.params
        parts = tomo_particles(ctx, ctx.require("particles"))
        args = [*relion_command(ctx, "relion_tomo_refine_ctf", p["mpi"]), "--i", parts["ios"], *reference_args(ctx),
                "--theme", "classic", "--o", ctx.rel(ctx.job_dir) + "/", "--b", str(p["box"])]
        if p["defocus"]:
            args += ["--do_defocus", "--d0", f"-{p['defocus_range']:g}", "--d1", f"{p['defocus_range']:g}"]
            if p["regularise"]:
                args += ["--do_reg_defocus", "--lambda", f"{p['lambda_']:g}"]
        if p["scale"]:
            args.append("--do_scale")
            if p["per_frame_scale"]:
                args.append("--per_frame_scale")
            if p["per_tomo_scale"]:  # what the program reads (RELION 5.1's GUI passes --per_tomo_scale, ignored)
                args.append("--per_tomogram_scale")
        args += ["--j", str(p["threads"]), *ctx.split_extra()]
        ctx.progress(0.05, "relion_tomo_refine_ctf")
        run_relion(ctx, args)
        tomo_refinement_outputs(ctx, parts, "ctf_refine")


@register
class RelionTomoFrameAlign(RelionJob):
    name = "relion_tomo_frame_align"
    title = "Frame alignment / polishing (tomography)"
    category = CATEGORY
    gpu_param = ""
    cpu_param = "mpi*threads"
    description = ("Refines the alignment of every tilt image against the reference (relion_tomo_align): rigid shifts "
                   "and angles of the tilts, the 3D positions of the particles and optionally their motion during the "
                   "tilt series (the subtomogram version of Bayesian polishing). Then reconstruct or refine again.")
    inputs = REFERENCE_SLOTS
    params = [
        Param("box", "int", 128, label="Box for the estimation", unit="px", min=32, max=1024),
        Param("max_error", "int", 5, label="Largest position error", unit="px", min=0, max=64,
              help="Assumed error of the projected 2D positions of the particles."),
        Param("shift_only", "bool", False, label="Align by shift only",
              help="Cross-correlation shifts of very badly aligned tilts (no iterative optimisation)."),
        Param("shift_by_particles", "bool", False, label="Shifts from the particles only", show_if="shift_only",
              help="Aligns only the particles instead of predicting whole images (corrects less than half a box)."),
        Param("motion", "bool", False, label="Fit per-particle motion",
              help="3D motion tracks of the particles (Bayesian polishing), besides the rigid alignment."),
        Param("sigma_vel", "float", 0.2, label="Sigma for velocity", unit="Å/dose", min=0, show_if="motion"),
        Param("sigma_div", "float", 5000.0, label="Sigma for divergence", unit="Å", min=0, show_if="motion"),
        Param("gaussian", "bool", False, label="Gaussian decay", show_if="motion",
              help="Smoother motion, shorter run (correlation of velocities decaying as a Gaussian of the distance)."),
        mpi(1),
        threads(8),
        extra_args_param(),
    ]
    outputs = [OutputDef("particles", "tomo_particles", "Particles"), OutputDef("tomograms", "tomograms", "Tomograms")]

    @classmethod
    def validate(cls, params: dict[str, Any], connected: dict[str, str]) -> list[str]:
        if params.get("shift_only") and params.get("motion"):
            return ["Per-particle motion and shift-only alignment cannot be combined"]
        return []

    def run(self, ctx: JobContext) -> None:
        p = ctx.params
        parts = tomo_particles(ctx, ctx.require("particles"))
        args = [*relion_command(ctx, "relion_tomo_align", p["mpi"]), "--i", parts["ios"], *reference_args(ctx),
                "--theme", "classic", "--o", ctx.rel(ctx.job_dir) + "/", "--b", str(p["box"]), "--r", str(p["max_error"])]
        if p["shift_only"]:
            args.append("--shift_only")
            if p["shift_by_particles"]:
                args.append("--shift_only_by_particles")
        if p["motion"]:
            args += ["--motion", "--s_vel", f"{p['sigma_vel']:g}", "--s_div", f"{p['sigma_div']:g}"]
            if p["gaussian"]:
                args.append("--sq_exp_ker")
        args += ["--j", str(p["threads"]), *ctx.split_extra()]
        ctx.progress(0.05, "relion_tomo_align")
        run_relion(ctx, args)
        tomo_refinement_outputs(ctx, parts, "frame_align")


def tomo_refinement_outputs(ctx: JobContext, parts: dict[str, Any], kind: str) -> None:
    """The optimisation set and tomogram set written by CTF refinement or frame alignment."""
    opt = ctx.path("optimisation_set.star")
    if not opt.exists():
        raise JobError("No optimisation_set.star was written (see the log)")
    files = tm.read_optimisation_set(opt)
    tomo_output(ctx, "particles", ctx.abs(files.get("particles") or parts["ref"]),
                f"{parts['n']:,} particles ({'CTF refined' if kind == 'ctf_refine' else 'tilt series realigned'})",
                {**parts, "tomograms": files.get("tomograms"), "trajectories": files.get("trajectories")}, kind, opt=opt)
    star = ctx.path("tomograms.star")
    if star.exists():
        tset = tm.read_set(star, ctx.project_dir)
        meta = tm.set_meta(ctx.rel(star), tset, kind)
        ctx.add_output("tomograms", "tomograms", star, f"{len(tset.series)} tomograms (updated tilt series)", meta=meta)
        if kind == "ctf_refine":
            rows = []
            for ts, t in tset.series.items():
                if t.has("rlnDefocusU"):
                    du = t.floats("rlnDefocusU") / 1e4
                    rows.append([ts, len(t), f"{du.min():.2f}–{du.max():.2f} µm"])
            if rows:
                ctx.add_table("Refined defoci", ["Tilt series", "Tilt images", "Defocus"], rows)
        if files.get("trajectories"):
            ctx.add_highlight("Particle motion", "fitted")
    ctx.add_highlight("Particles", f"{parts['n']:,}")
