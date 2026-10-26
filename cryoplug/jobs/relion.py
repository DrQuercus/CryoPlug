"""RELION jobs: local (focused) refinement, signal subtraction (with symmetry expansion), focused 3D classification,
post-processing and reconstruction.

Particles from CryoSPARC are converted on the fly with pyem's conventions (:mod:`cryoplug.relion`): their image stacks
are linked into the job folder. RELION programs run from the project folder with project-relative paths, as in a RELION
project, so that the RELION files of one job (data, model and optimiser STAR files) are read by the next.

Maps and masks are given and returned on the grid of the original particles (that of the consensus refinement): the
jobs resample them on the grid of re-centred, re-boxed particles (signal subtraction) and put the results back, by
whole voxels. A mask drawn on the consensus map therefore serves every job of the chain.
"""
from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import Any

import numpy as np

from cryoplug import relion as rl
from cryoplug.jobs import register
from cryoplug.jobs.base import InputData, JobContext, JobError, JobType, OutputDef, Param, Slot, extra_args_param
from cryoplug.mrc import MapVolume, fsc, fsc_to_emdb_xml

TOOL = "relion"
LOCAL_STEPS = ["1.8°", "0.9°", "3.7°", "7.5°"]
CURVE_LABELS = {"corrected": "Corrected (gold standard)", "unmasked": "No mask", "masked": "Masked",
                "phase_randomized": "Phase randomised", "gold_standard": "Gold-standard FSC (RELION)"}


# ------------------------------------------------------------------ parameters
def mpi_param(default: int = 3, minimum: int = 1, help: str = "") -> Param:
    return Param("mpi", "int", default, label="MPI processes", min=minimum, max=256,
                 help=help or "RELION processes started with mpirun (the launcher is set by the 'MPIRUN' variable of the "
                              "RELION tool, e.g. srun on a cluster). With GPUs, 2 processes per GPU plus one.")


def threads_param(default: int = 4) -> Param:
    return Param("threads", "int", default, label="Threads per process", min=1, max=128,
                 help="--j: the job asks for MPI processes × threads CPUs.")


def gpus_param(default: int = 1) -> Param:
    return Param("gpus", "int", default, label="GPUs", min=0, max=16,
                 help="GPUs given to the job (all of them used with --gpu). 0 = CPU only, much slower.")


def symmetry_param(help: str = "") -> Param:
    return Param("symmetry", "str", "", label="Symmetry", placeholder="from the inputs",
                 help=help or "Point group (C1, C2, D7...). Empty: that of the inputs, C1 for re-centred particles "
                              "(a focused region is rarely symmetric).")


class RelionJob(JobType):
    """Shared parts of the RELION jobs (not registered)."""
    tool = TOOL
    category = "Reconstruction"
    software = ["RELION"]
    gpu_param = "gpus"
    cpu_param = "mpi*threads"


def relion_command(ctx: JobContext, name: str, mpi: int) -> list[str]:
    """Command prefix of a RELION program: its MPI version under the launcher when ``mpi`` > 1."""
    if mpi > 1:
        launcher = (ctx.tool_spec(TOOL).get("env") or {}).get("MPIRUN") or "mpirun"
        return [*shlex.split(launcher), "-n", str(mpi), ctx.program(TOOL, f"{name}_mpi")]
    return [ctx.program(TOOL, name)]


def run_relion(ctx: JobContext, args: list[str], on_line=None) -> None:
    """Runs a RELION program from the project folder. MPI start-up failures get an explanation."""
    tail: list[str] = []

    def watch(line: str) -> None:
        if len(tail) < 2000 and ("slots" in line or "mpirun" in line.lower() or "ERROR" in line):
            tail.append(line)
        if on_line:
            on_line(line)

    try:
        ctx.run(args, tool=TOOL, cwd=ctx.project_dir, on_line=watch)
    except JobError as exc:
        if any("not enough slots" in t for t in tail):
            raise JobError(f"{exc}\nmpirun found fewer CPU slots than MPI processes: lower 'MPI processes', or set the "
                           "MPIRUN variable of the RELION tool to 'mpirun --oversubscribe' (Open MPI)") from None
        raise


# -------------------------------------------------------------- particle input
def prepare_particles(ctx: JobContext, particles: InputData, need_poses: bool = True, subsets: bool = False) -> dict[str, Any]:
    """The particles as a STAR file RELION reads from the project folder: a RELION file of an earlier job as it is, a
    CryoSPARC .cs file converted, any other STAR file with its image stacks linked into the job folder. Returns the
    STAR path (project-relative ``ref``), the particle grid (box, pixel size) and the frame of re-centred particles.
    ``subsets``: particles without half-set assignment get random half sets (in a copy)."""
    meta = particles.meta
    native = meta.get("relion") or {}
    src = Path(ctx.abs(meta["particles_file"])) if meta.get("particles_file") else Path(particles.files[0])
    if not src.exists():
        raise JobError(f"The particle file {src} is not available any more")
    frame = rl.frame_of(meta)
    if native.get("star") and ctx.abs(native["star"]).exists() and src.suffix == ".star" \
            and ctx.abs(native["star"]).resolve() == src.resolve():
        tables = rl.read_star(src)
        box, apix = rl.grid_from_star(tables)
        parts = rl.particle_table(tables)
        info = {"n_particles": len(parts), "box": box, "pixel_size": apix,
                "has_poses": parts.has("rlnAngleRot"), "half_sets": parts.has("rlnRandomSubset")}
        star, how = src, "RELION file of an earlier job, used as it is"
    else:
        linker = rl.StackLinker(ctx.path("Particles"), ctx.project_dir)
        datadir = Path(meta["datadir"]).expanduser() if meta.get("datadir") else None
        try:
            if src.suffix.lower() == ".cs":
                from cryoplug import particles as pt
                data = pt.read_cs(src)
                tables_list, info = rl.cs_to_star(data, datadir or pt.project_dir_of(src.parent), linker)
                how = "CryoSPARC particles converted (pyem conventions)"
            elif src.suffix.lower() == ".star":
                tables = rl.read_star(src)
                info = rl.relink_star(tables, datadir, src.parent, linker)
                tables_list = list(tables.values())
                how = "STAR file with its image stacks linked into the job folder"
            else:
                raise JobError(f"RELION needs a CryoSPARC .cs or a RELION .star particle file, not {src.name}")
        except ValueError as exc:
            raise JobError(str(exc)) from None
        star = rl.write_star(ctx.path("particles.star"), tables_list, comment=f"Particles for RELION, from {src}")
    if subsets and not info.get("half_sets"):
        tables = rl.read_star(star)
        ptab = rl.particle_table(tables)
        ptab.set_column("rlnRandomSubset", np.random.default_rng(0).permutation(np.arange(len(ptab)) % 2) + 1)
        star = rl.write_star(ctx.path("particles.star"), list(tables.values()), comment="Particles with random half sets")
        info = {**info, "half_sets": True, "random_halves": True}
        ctx.log("The particles carry no half-set assignment: random half sets given")
    if need_poses and not info.get("has_poses"):
        raise JobError("These particles have no 3D poses: RELION local refinement starts from the poses of a consensus "
                       "refinement (import the particles of a CryoSPARC refinement job)")
    box, apix = int(info.get("box") or meta.get("box") or 0), float(info.get("pixel_size") or meta.get("pixel_size") or 0)
    if not box or not apix:
        raise JobError("The box or the pixel size of the particles is unknown (no optics table in the STAR file?)")
    ctx.log(f"Particles: {info['n_particles']:,} ({how}); box {box} px, {apix:g} Å/px"
            + (f"; re-centred on ({', '.join(str(c) for c in frame['center'])}) voxels of the {frame['parent_box']} px box"
               if frame else ""))
    if not info.get("half_sets") and need_poses:
        ctx.log("The particles carry no half-set assignment: RELION splits them at random")
    return {"star": star, "ref": ctx.rel(star), "box": box, "pixel_size": apix, "frame": frame, "info": info,
            "n": int(info["n_particles"]), "meta": meta}


def grid_center(parts: dict[str, Any]) -> list[int]:
    return list(parts["frame"]["center"]) if parts["frame"] else [0, 0, 0]


def map_on_grid(ctx: JobContext, inp: InputData, parts: dict[str, Any], name: str, mask: bool = False) -> tuple[Path, np.ndarray]:
    """A map or mask of the consensus grid (or any grid centred the same way) resampled on the particle grid and
    written into the job folder (origin 0, as RELION expects)."""
    vol = MapVolume.read(inp.path)
    box, apix = parts["box"], parts["pixel_size"]
    if abs(vol.pixel_size - apix) > 1e-3 * apix:
        ctx.log(f"{inp.label or inp.source}: {vol.pixel_size:g} Å/px resampled to the {apix:g} Å/px of the particles")
    data = rl.to_grid(vol.data, vol.pixel_size, box, apix, grid_center(parts), order=1)
    if mask:
        data = np.clip(data, 0.0, 1.0)
        if float(data.max()) < 0.5:
            raise JobError(f"The mask {inp.source} is empty on the grid of the particles (is it the mask of this map?)")
    out = MapVolume(data=data, voxel=np.full(3, apix)).write(ctx.path(name))
    return out, data


def to_consensus(ctx: JobContext, path: Path, parts: dict[str, Any], name: str) -> Path:
    """A map on the particle grid put back on the consensus grid (a copy only for re-centred particles)."""
    frame = parts["frame"]
    if not frame:
        return path
    vol = MapVolume.read(path)
    return MapVolume(data=rl.to_parent(vol.data, frame), voxel=np.full(3, frame["pixel_size"])).write(ctx.path(name))


def inherited_symmetry(ctx: JobContext, parts: dict[str, Any], *slots: str) -> str:
    value = str(ctx.params.get("symmetry") or "").strip().upper()
    if value:
        return value
    if parts["frame"]:
        sym = str(parts["meta"].get("symmetry") or "C1").upper()
        if sym not in ("", "C1"):
            ctx.log(f"Symmetry C1 for the re-centred particles (the consensus was {sym}; set it to keep it)")
        return "C1"
    for name in slots:
        inp = ctx.input(name)
        if inp and inp.meta.get("symmetry"):
            return str(inp.meta["symmetry"]).upper()
    return str(parts["meta"].get("symmetry") or "C1").upper()


def known_resolution(ctx: JobContext, *slots: str) -> float | None:
    for name in slots:
        inp = ctx.input(name)
        if inp and inp.meta.get("resolution"):
            return float(inp.meta["resolution"])
    return None


def auto_ini_high(ctx: JobContext, *slots: str) -> float:
    value = float(ctx.params.get("ini_high") or 0)
    if value > 0:
        return value
    res = known_resolution(ctx, *slots)
    out = float(np.clip(3.0 * res, 8.0, 15.0)) if res else 10.0
    ctx.log(f"Initial low-pass filter of the reference: {out:g} Å" + (f" (3 × the {res:g} Å of the inputs)" if res else ""))
    return out


def absolute_greyscale(ctx: JobContext, ref: InputData) -> bool:
    choice = ctx.params.get("absolute_greyscale", "auto")
    if choice == "auto":
        return bool(ref.meta.get("relion_map"))
    return choice == "yes"


# ------------------------------------------------------------------ results
def report_fsc(ctx: JobContext, frequency: list[float], curves: dict[str, list[float]], best: str,
               resolution: float | None, title: str) -> Path:
    """FSC curves in CryoPlug's format (JSON + EMDB XML), plotted; returns the JSON file."""
    res = {k: r for k, c in curves.items() if (r := rl.resolution_at(frequency, c)) is not None}
    if resolution:
        res["best"] = resolution
    elif best in res:
        res["best"] = res[best]
    path = ctx.path("fsc.json")
    path.write_text(json.dumps({"frequency": frequency, "curves": curves, "resolution": res, "source": "RELION"}))
    xml = ctx.path("fsc_emdb.xml")
    xml.write_text(fsc_to_emdb_xml(frequency, curves[best], title=f"{title} ({best})"))
    ctx.add_plot(title, [{"name": CURVE_LABELS.get(k, k), "x": frequency, "y": v} for k, v in curves.items()],
                 x_label="Resolution (Å)", y_label="FSC", x_kind="resolution",
                 hlines=[{"y": 0.143, "label": "0.143"}, {"y": 0.5, "label": "0.5"}], y_range=[-0.1, 1.05])
    ctx.add_output("fsc", "fsc", [path, xml], title,
                   meta={"resolution": round(res.get("best", 0.0), 3), "curve": best} if res.get("best") else {"curve": best})
    return path


def postprocess(ctx: JobContext, half1: Path, half2: Path, mask: Path, apix: float, bfactor: float,
                resolution: float | None, name: str = "postprocess", locres: bool = False, mtf: str = "",
                extra: list[str] | None = None) -> dict[str, Any]:
    """relion_postprocess of two half maps with a mask; returns its results (resolution, B-factor, curves).
    ``locres``: then the local resolution, in a second run (with --locres relion_postprocess does only that, and
    refuses --auto_bfac: the locally filtered map is sharpened with the B-factor of the first run)."""
    root = ctx.rel(ctx.path(name))
    common = ["--mask", ctx.rel(mask), "--i", ctx.rel(half1), "--i2", ctx.rel(half2), "--o", root, "--angpix", f"{apix:g}"]
    args = [ctx.program(TOOL, "relion_postprocess"), *common]
    if bfactor:
        args += ["--adhoc_bfac", f"{bfactor:g}"]
    elif resolution and resolution > 8.0:
        ctx.log(f"Resolution {resolution:.1f} Å: too low to fit a B-factor (fit from 10 Å); the map is only FSC-weighted")
    else:
        args += ["--auto_bfac", "--autob_lowres", "10"]
    if mtf:
        args += ["--mtf", mtf]
    run_relion(ctx, [*args, *(extra or [])])
    star = ctx.path(f"{name}.star")
    if not star.exists():
        raise JobError(f"relion_postprocess did not write {star.name} (see the log)")
    results = {**rl.postprocess_results(star), "map": ctx.path(f"{name}.mrc"), "masked": ctx.path(f"{name}_masked.mrc"),
               "locres": ctx.path(f"{name}_locres.mrc")}
    if locres:
        sharpen = bfactor or results.get("bfactor") or 0.0
        ctx.progress(0.6, "relion_postprocess --locres (local resolution)")
        run_relion(ctx, [ctx.program(TOOL, "relion_postprocess"), "--locres", *common, "--adhoc_bfac", f"{sharpen:g}",
                         *(["--mtf", mtf] if mtf else [])])
    return results


def particles_meta(ctx: JobContext, star: Path, parts: dict[str, Any], kind: str, symmetry: str, **extra: Any) -> dict[str, Any]:
    """Metadata of a particle STAR file written by RELION in this job (read from the project folder)."""
    tables = rl.read_star(star)
    box, apix = rl.grid_from_star(tables)
    n = len(rl.particle_table(tables))
    meta: dict[str, Any] = {"format": "star", "datadir": str(ctx.project_dir), "n_particles": n,
                            "box": box or parts["box"], "pixel_size": round(apix or parts["pixel_size"], 4),
                            "has_images": True, "has_poses": True, "has_ctf": True, "symmetry": symmetry,
                            "relion": {"star": ctx.rel(star), "kind": kind, "symmetry": symmetry, **extra}}
    if parts["frame"]:
        meta["relion_frame"] = parts["frame"]
    return meta


class Progress:
    """Live progress of relion_refine from its output."""
    ITER = re.compile(r"Auto-refine: Iteration=\s*(\d+)")
    RES = re.compile(r"Auto-refine: Resolution=\s*([\d.]+)")
    EXP = re.compile(r"(?:Expectation|Gradient optimisation) iteration\s+(\d+)(?:\s+of\s+(\d+))?")

    def __init__(self, ctx: JobContext, total: int = 0, start: float = 0.05, end: float = 0.9):
        self.ctx, self.total, self.start, self.end = ctx, total, start, end
        self.iteration, self.history = 0, []

    def __call__(self, line: str) -> None:
        m = self.ITER.search(line) or self.EXP.search(line)
        if m:
            self.iteration = int(m.group(1))
            if m.re is self.EXP and m.group(2):
                self.total = int(m.group(2))
            frac = self.iteration / self.total if self.total else 1 - 0.85 ** self.iteration
            self.ctx.progress(self.start + (self.end - self.start) * min(1.0, frac),
                              f"Iteration {self.iteration}" + (f" of {self.total}" if self.total else ""), log=False)
            return
        m = self.RES.search(line)
        if m:
            self.history.append((self.iteration, float(m.group(1))))
            self.ctx.progress(self.start + (self.end - self.start) * min(1.0, 1 - 0.85 ** self.iteration),
                              f"Iteration {self.iteration}: {float(m.group(1)):.2f} Å", log=False)
        elif "Refinement has converged" in line:
            self.ctx.progress(self.end, "Converged: last iteration with the two halves combined")


def outputs_of_refine(ctx: JobContext, root: str = "run") -> dict[str, Path]:
    found = {"map": ctx.path(f"{root}_class001.mrc"), "half1": ctx.path(f"{root}_half1_class001_unfil.mrc"),
             "half2": ctx.path(f"{root}_half2_class001_unfil.mrc"), "data": ctx.path(f"{root}_data.star"),
             "model": ctx.path(f"{root}_model.star"), "optimiser": ctx.path(f"{root}_optimiser.star")}
    missing = [p.name for p in found.values() if not p.exists()]
    if missing:
        raise JobError(f"RELION did not write {', '.join(missing)} (see the log)")
    return found


def partial_refine_outputs(ctx: JobContext, root: str = "run") -> tuple[int, dict[str, Path]]:
    """An auto-refinement that stopped (Mark as complete): the half maps of the last iteration written, their average
    as the map, and its particles and model (the halves were never joined in a last iteration)."""
    it, stem = rl.iteration_stem(ctx.job_dir, root, suffix="_half1_model.star")
    if not it:
        raise JobError("No iteration was written: nothing to use (clear the job and run it again)")
    out = {"half1": ctx.path(f"{stem}_half1_class001.mrc"), "half2": ctx.path(f"{stem}_half2_class001.mrc"),
           "data": ctx.path(f"{stem}_data.star"), "model": ctx.path(f"{stem}_half1_model.star"),
           "optimiser": ctx.path(f"{stem}_optimiser.star")}
    missing = [f.name for f in out.values() if not f.exists()]
    if missing:
        raise JobError(f"Iteration {it} is incomplete ({', '.join(missing)} missing)")
    a, b = MapVolume.read(out["half1"]), MapVolume.read(out["half2"])
    out["map"] = ctx.path(f"{stem}_halves_average.mrc")
    a.like(((a.data + b.data) / 2).astype(np.float32)).write(out["map"])
    ctx.log(f"Iteration {it}: map = average of the two half maps ({out['half1'].name}, {out['half2'].name})")
    ctx.add_highlight("Stopped at", f"iteration {it}")
    return it, out


# ------------------------------------------------------------- local refinement
@register
class RelionLocalRefine(RelionJob):
    name = "relion_local_refine"
    title = "RELION local refinement"
    description = ("Gold-standard 3D auto-refinement in RELION with local angular searches around the poses of the "
                   "particles (a CryoSPARC consensus refinement or an earlier RELION job), focused by a mask: the RELION "
                   "counterpart of CryoSPARC's local refinement, with Blush regularisation for small or flexible regions. "
                   "CryoSPARC half sets are kept. Writes the map, half maps, FSC, refined particles and, with a mask, the "
                   "post-processed map, all on the grid of the consensus map.")
    inputs = [
        Slot("particles", ("particles",), "Particles",
             help="With poses: a CryoSPARC refinement, a selection, or the subtracted particles of 'RELION signal "
                  "subtraction'.", prefer=("particles",)),
        Slot("map", ("map",), "Reference map", help="The consensus map (unsharpened), low-pass filtered by RELION.",
             prefer=("map",)),
        Slot("mask", ("mask",), "Mask", required=False,
             help="Soft mask around the region to refine (focused refinement), drawn on the consensus map. Without a "
                  "mask, RELION uses a sphere of the particle diameter."),
    ]
    params = [
        Param("angular_step", "choice", LOCAL_STEPS[0], choices=LOCAL_STEPS, label="Initial angular sampling",
              help="Local searches from the start: about ± 6 steps around the input poses (1.8° = ± 11°)."),
        Param("local_searches", "bool", True, label="Local searches from the start",
              help="Off: global angular search first (a full refinement from scratch), local from 1.8° on."),
        Param("offset_range", "float", 3.0, label="Offset search range", unit="px", min=0.5, max=50),
        Param("offset_step", "float", 0.75, label="Offset search step", unit="px", min=0.1, max=10),
        symmetry_param(),
        Param("blush", "bool", False, label="Blush regularisation",
              help="RELION 5 neural-network prior: better maps of small or flexible regions (needs RELION's Blush "
                   "model installed with RELION's Python environment)."),
        Param("ini_high", "float", 0.0, label="Initial low-pass filter", unit="Å", min=0.0,
              help="0 = 3 × the resolution of the inputs, between 8 and 15 Å."),
        Param("postprocess", "bool", True, label="Post-process with the mask",
              help="relion_postprocess at the end: masked, phase-randomisation corrected FSC and B-factor sharpening."),
        Param("bfactor", "float", 0.0, label="Sharpening B-factor", unit="Å²",
              help="0 = automatic (Rosenthal & Henderson fit); a negative value sharpens by that much."),
        Param("solvent_correct_fsc", "bool", True, label="Solvent-flattened FSC",
              help="--solvent_correct_fsc: the FSC driving the refinement is corrected for the mask (needs a mask)."),
        Param("particle_diameter", "float", 0.0, label="Mask diameter", unit="Å", min=0.0, advanced=True,
              help="Circular mask on the images. 0 = the extent of the mask + 10 Å (90 % of the box without mask)."),
        Param("absolute_greyscale", "choice", "auto", choices=["auto", "yes", "no"], label="Reference on absolute greyscale",
              advanced=True, help="No: the first iteration uses cross-correlation (--firstiter_cc), right for a CryoSPARC "
                                  "map. Auto: yes for a map made by RELION."),
        Param("relax_sym", "str", "", label="Relax symmetry", advanced=True, placeholder="e.g. C2",
              help="--relax_sym: let each particle take any symmetry-related pose of this point group (pseudo-symmetry)."),
        Param("pool", "int", 10, label="Pool of particles", min=1, max=200, advanced=True),
        Param("preread_images", "bool", False, label="Read all images into memory", advanced=True),
        mpi_param(3, 3, help="At least 3 (a leader and one process per half set). With GPUs, 2 per GPU plus one."),
        threads_param(),
        gpus_param(),
        extra_args_param(),
    ]
    outputs = [
        OutputDef("map", "map", "Refined map"),
        OutputDef("half_maps", "half_maps", "Half maps"),
        OutputDef("map_sharp", "map", "Post-processed map"),
        OutputDef("mask", "mask", "Mask"),
        OutputDef("fsc", "fsc", "FSC"),
        OutputDef("particles", "particles", "Refined particles"),
    ]
    completable = True

    def run(self, ctx: JobContext) -> None:
        p = ctx.params
        ctx.progress(0.01, "Preparing the particles")
        parts = prepare_particles(ctx, ctx.require("particles"))
        ref_in = ctx.require("map")
        ref, _ = map_on_grid(ctx, ref_in, parts, "reference.mrc")
        mask_in = ctx.input("mask")
        mask, mask_data = map_on_grid(ctx, mask_in, parts, "mask.mrc", mask=True) if mask_in else (None, None)
        sym = inherited_symmetry(ctx, parts, "map", "mask")
        diameter = float(p["particle_diameter"]) or rl.particle_diameter(mask_data, parts["pixel_size"], parts["box"])
        healpix = rl.HEALPIX[p["angular_step"]]
        args = [*relion_command(ctx, "relion_refine", p["mpi"]), "--o", ctx.rel(ctx.path("run")), "--auto_refine",
                "--split_random_halves", "--i", parts["ref"], "--ref", ctx.rel(ref)]
        if not absolute_greyscale(ctx, ref_in):
            args.append("--firstiter_cc")
        args += ["--trust_ref_size", "--ini_high", f"{auto_ini_high(ctx, 'map', 'particles'):g}",
                 "--dont_combine_weights_via_disc", "--pool", str(p["pool"]), "--pad", "2", "--ctf",
                 "--particle_diameter", f"{diameter:g}", "--flatten_solvent", "--zero_mask"]
        if mask:
            args += ["--solvent_mask", ctx.rel(mask)]
            if p["solvent_correct_fsc"]:
                args.append("--solvent_correct_fsc")
        args += ["--oversampling", "1", "--healpix_order", str(healpix),
                 "--auto_local_healpix_order", str(healpix if p["local_searches"] else 4),
                 "--offset_range", f"{p['offset_range']:g}", "--offset_step", f"{2 * p['offset_step']:g}",
                 "--sym", sym, "--low_resol_join_halves", "40", "--norm", "--scale", "--j", str(p["threads"])]
        if p["relax_sym"].strip():
            args += ["--relax_sym", p["relax_sym"].strip().upper()]
        if p["blush"]:
            args.append("--blush")
        if p["preread_images"]:
            args.append("--preread_images")
        if p["gpus"] > 0:
            args += ["--gpu", ""]
        args += ctx.split_extra()
        ctx.add_table("RELION refinement", ["Setting", "Value"], [
            ["Particles", f"{parts['n']:,} · box {parts['box']} px · {parts['pixel_size']:g} Å/px"],
            ["Angular sampling", f"{p['angular_step']} · {'local' if p['local_searches'] else 'global'} searches from the start"],
            ["Offsets", f"± {p['offset_range']:g} px, step {p['offset_step']:g} px"],
            ["Symmetry", sym], ["Mask", mask_in.source if mask_in else "sphere"], ["Mask diameter", f"{diameter:g} Å"],
            ["Blush", "yes" if p["blush"] else "no"]])
        progress = Progress(ctx)
        ctx.progress(0.05, "relion_refine")
        run_relion(ctx, args, on_line=progress)
        out = outputs_of_refine(ctx)
        finish_refinement(ctx, out, parts, sym, mask, mask_data, progress, kind="refine3d")

    def finish_partial(self, ctx: JobContext) -> str:
        parts = prepare_particles(ctx, ctx.require("particles"))
        mask_in = ctx.input("mask")
        mask, mask_data = map_on_grid(ctx, mask_in, parts, "mask.mrc", mask=True) if mask_in else (None, None)
        it, out = partial_refine_outputs(ctx)
        finish_refinement(ctx, out, parts, inherited_symmetry(ctx, parts, "map", "mask"), mask, mask_data, Progress(ctx),
                          kind="refine3d")
        return f"iteration {it}"


def finish_refinement(ctx: JobContext, out: dict[str, Path], parts: dict[str, Any], sym: str, mask: Path | None,
                      mask_data: np.ndarray | None, progress: Progress, kind: str) -> None:
    """Outputs of an auto-refinement: FSC, optional post-processing, maps on the consensus grid, particles."""
    p = ctx.params
    model = rl.model_fsc(out["model"])
    if not model.get("fsc") and out["half1"].exists() and out["half2"].exists():  # e.g. the halves of an iteration
        a, b = MapVolume.read(out["half1"]), MapVolume.read(out["half2"])
        freq, curve = fsc(a.data, b.data, a.voxel)
        model["frequency"], model["fsc"] = freq[1:].tolist(), curve[1:].tolist()
    resolution = None
    if model.get("fsc"):
        resolution = rl.resolution_at(model["frequency"], model["fsc"])
    if progress.history:
        ctx.add_plot("Resolution during the refinement", [{"name": "Resolution", "x": [h[0] for h in progress.history],
                                                           "y": [h[1] for h in progress.history]}],
                     x_label="Iteration", y_label="Resolution (Å)")
    pp = None
    if mask is not None and p.get("postprocess", True):
        ctx.progress(0.92, "relion_postprocess")
        pp = postprocess(ctx, out["half1"], out["half2"], mask, parts["pixel_size"], float(p.get("bfactor") or 0),
                         resolution)
        resolution = pp.get("resolution") or resolution
        if pp.get("curves"):
            report_fsc(ctx, pp["frequency"], pp["curves"], "corrected" if "corrected" in pp["curves"] else "unmasked",
                       resolution, "Fourier shell correlation (relion_postprocess)")
    elif mask is None and p.get("postprocess", True):
        ctx.log("No mask: the map is not post-processed (connect a mask to get the masked FSC and the sharpened map)")
    if pp is None and model.get("fsc"):
        report_fsc(ctx, model["frequency"], {"gold_standard": model["fsc"]}, "gold_standard", resolution,
                   "Gold-standard FSC (RELION)")
    meta: dict[str, Any] = {"symmetry": sym, "relion_map": True}
    if resolution:
        meta["resolution"] = round(float(resolution), 3)
        ctx.add_highlight("Resolution", f"{resolution:.2f} Å")
    metrics = []
    if resolution:
        metrics.append({"label": "Resolution (FSC = 0.143)", "value": f"{resolution:.2f} Å"})
    if pp and pp.get("bfactor") is not None:
        metrics.append({"label": "Sharpening B-factor", "value": f"{pp['bfactor']:.0f} Å²"})
    for key, label, unit in (("accuracy_rotations", "Angular accuracy", "°"), ("accuracy_translations", "Offset accuracy", " Å")):
        if model.get(key) is not None:
            metrics.append({"label": label, "value": f"{model[key]:.2f}{unit}"})
    metrics.append({"label": "Particles", "value": f"{parts['n']:,}"})
    ctx.add_metrics("RELION refinement", metrics)
    if parts["frame"]:
        ctx.log("The maps are put back on the grid of the consensus map (whole-voxel shift of the re-centred box)")
    full = to_consensus(ctx, out["map"], parts, "map.mrc")
    h1 = to_consensus(ctx, out["half1"], parts, "half_map_1.mrc")
    h2 = to_consensus(ctx, out["half2"], parts, "half_map_2.mrc")
    ctx.add_output("map", "map", full, "Refined map (unsharpened)", meta=meta)
    ctx.add_output("half_maps", "half_maps", [h1, h2], "Half maps (unfiltered)", meta=meta)
    if pp and pp["map"].exists():
        sharp = to_consensus(ctx, pp["map"], parts, "map_sharp.mrc")
        ctx.add_output("map_sharp", "map", sharp, "Post-processed map",
                       meta={**meta, **({"bfactor": round(pp["bfactor"], 1)} if pp.get("bfactor") is not None else {})})
    if mask is not None:
        ctx.add_output("mask", "mask", to_consensus(ctx, mask, parts, "mask_consensus.mrc"), "Mask", meta=meta)
    pmeta = particles_meta(ctx, out["data"], parts, kind, sym, optimiser=ctx.rel(out["optimiser"]),
                           model=ctx.rel(out["model"]))
    if resolution:
        pmeta["resolution"] = round(float(resolution), 3)
    ctx.add_output("particles", "particles", out["data"], f"Refined particles ({pmeta['n_particles']:,})", meta=pmeta)
    from cryoplug.jobs.relion_common import refinement_diagnostics
    refinement_diagnostics(ctx, out["data"], ctx.job_dir)


# ------------------------------------------------------------- signal subtraction
@register
class RelionSubtract(RelionJob):
    name = "relion_subtract"
    title = "RELION signal subtraction"
    description = ("Removes from each particle image the projection of everything outside a mask (relion_particle_"
                   "subtract), re-centres the particles on the masked region and puts them in a smaller box: the "
                   "particles of a focused refinement or classification. Optionally expands the symmetry first, so that "
                   "every copy of a symmetric complex becomes a particle. Needs particles refined in RELION (it uses that "
                   "refinement's model).")
    gpu_param = ""
    inputs = [
        Slot("particles", ("particles",), "Particles refined in RELION",
             help="The particles of 'RELION local refinement' (or 3D classification) of the whole complex.",
             prefer=("particles",)),
        Slot("mask", ("mask",), "Mask of the region to keep",
             help="Soft mask (values 0 to 1) around the region to keep, on the consensus map. Everything outside it is "
                  "subtracted."),
    ]
    params = [
        Param("recenter", "bool", True, label="Re-centre on the mask",
              help="The particles are re-centred on the centre of mass of the mask (whole voxels)."),
        Param("new_box", "int", 0, label="New box size", unit="px", min=0,
              help="0 = automatic: 1.5 × the extent of the mask (room for the signal the CTF spreads), at most the "
                   "original box. The original box is kept without re-centring."),
        Param("expand_symmetry", "bool", False, label="Expand the symmetry first",
              help="Each particle is copied once per symmetry-related pose (relion_particle_symmetry_expand) before "
                   "the subtraction: focused refinement or classification of one copy of a symmetric complex, in C1."),
        Param("float16", "bool", False, label="Write half-precision images", advanced=True,
              help="--float16: subtracted stacks take half the disk space (RELION 4+ reads them)."),
        mpi_param(4, 1, help="Refinements are subtracted with at least 2 processes (one per half set)."),
        threads_param(1),
        extra_args_param(),
    ]
    outputs = [
        OutputDef("particles", "particles", "Subtracted particles"),
        OutputDef("map", "map", "Map of the refinement"),
        OutputDef("mask", "mask", "Mask of the kept region"),
    ]

    def run(self, ctx: JobContext) -> None:
        p = ctx.params
        inp = ctx.require("particles")
        native = inp.meta.get("relion") or {}
        if not native.get("optimiser") or not ctx.abs(native["optimiser"]).exists():
            raise JobError("Signal subtraction uses the RELION model of the refinement of these particles: connect the "
                           "particles of 'RELION local refinement' (a short refinement with local searches from the "
                           "CryoSPARC poses is enough)")
        parts = prepare_particles(ctx, inp)
        mask_in = ctx.require("mask")
        mask, mask_data = map_on_grid(ctx, mask_in, parts, "mask_keep.mrc", mask=True)
        box = parts["box"]
        center = np.round(rl.center_of_mass(mask_data)).astype(int) if p["recenter"] else np.zeros(3, int)
        new_box = int(p["new_box"]) or (rl.suggest_box(mask_data, center, box) if p["recenter"] else 0)
        if new_box and new_box % 2:
            raise JobError(f"The box size must be even (got {new_box})")
        if new_box > box:
            raise JobError(f"The new box ({new_box} px) is larger than the particles ({box} px)")
        kind = native.get("kind", "refine3d")
        mpi = int(p["mpi"])
        if kind == "refine3d" and mpi < 2:
            ctx.log("Subtraction of a gold-standard refinement: 2 MPI processes (one per half set)")
            mpi = 2
        data_star = None
        sym = str(native.get("symmetry") or inp.meta.get("symmetry") or "C1").upper()
        copies = 1
        if p["expand_symmetry"]:
            if sym in ("", "C1"):
                ctx.warn("The refinement was done in C1: there is no symmetry to expand")
            else:
                data_star = ctx.path("expanded.star")
                run_relion(ctx, [ctx.program(TOOL, "relion_particle_symmetry_expand"), "--i", native["star"],
                                 "--o", ctx.rel(data_star), "--sym", sym])
                if not data_star.exists():
                    raise JobError("relion_particle_symmetry_expand did not write expanded.star (see the log)")
                from cryoplug import particles as pt
                copies = max(1, round(pt.star_count(data_star) / max(parts["n"], 1)))
                ctx.log(f"Symmetry {sym} expanded: {copies} copies of every particle")
        args = [*relion_command(ctx, "relion_particle_subtract", mpi), "--i", native["optimiser"],
                "--mask", ctx.rel(mask), "--o", ctx.rel(ctx.job_dir) + "/"]
        if data_star is not None:
            args += ["--data", ctx.rel(data_star)]
        if np.any(center != 0):
            args += ["--center_x", str(center[0]), "--center_y", str(center[1]), "--center_z", str(center[2])]
        if new_box and new_box != box:
            args += ["--new_box", str(new_box)]
        if p["float16"]:
            args.append("--float16")
        args += ctx.split_extra()
        apix = parts["pixel_size"]
        ctx.add_table("Signal subtraction", ["Setting", "Value"], [
            ["Particles", f"{parts['n']:,}" + (f" × {copies} ({sym} expanded)" if copies > 1 else "")],
            ["Kept region", mask_in.source],
            ["New centre", f"({', '.join(str(c) for c in center)}) px = ({', '.join(f'{c * apix:.1f}' for c in center)}) Å "
                           "from the box centre" if p["recenter"] else "unchanged"],
            ["Box", f"{box} → {new_box or box} px ({(new_box or box) * apix:.0f} Å)"]])
        try:  # picture of the kept region on the consensus map
            from cryoplug import masks as mk
            from cryoplug.imaging import write_png
            ref = inp.sibling("map", type="map")
            if ref is not None:
                ref_vol = MapVolume.read(ref.path)
                vol = rl.to_grid(ref_vol.data, ref_vol.pixel_size, box, apix, grid_center(parts))
                cz, cy, cx = (np.array([center[2], center[1], center[0]]) + box // 2).tolist()
                write_png(ctx.path("kept_region.png"), mk.overlay_slices(vol, mask_data, (cz, cy, cx)))
                ctx.add_image("Kept region (XY, XZ, YZ through the new centre)", ctx.path("kept_region.png"))
        except Exception as exc:  # noqa: BLE001 - picture only
            ctx.warn(f"Picture of the kept region not made: {exc}")
        ctx.progress(0.1, "relion_particle_subtract")
        run_relion(ctx, args)
        star = ctx.path("particles_subtracted.star")
        if not star.exists():
            raise JobError("relion_particle_subtract did not write particles_subtracted.star (see the log)")
        prev = parts["frame"]
        frame = None
        if np.any(center != 0) or (new_box and new_box != box):
            base = np.array(prev["center"]) if prev else np.zeros(3, int)
            frame = {"box": new_box or box, "center": [int(c) for c in base + center],
                     "parent_box": prev["parent_box"] if prev else box, "pixel_size": apix}
        sparts = {**parts, "frame": frame or prev, "box": new_box or box}
        meta = particles_meta(ctx, star, sparts, "subtracted", "C1", subtracted_from=inp.source,
                              copies=copies, parent_symmetry=sym)
        if copies > 1:
            meta["symmetry_expanded"] = sym
        ctx.add_output("particles", "particles", star, f"Subtracted particles ({meta['n_particles']:,})", meta=meta)
        ref = inp.sibling("map", type="map")
        if ref is not None:
            ctx.add_output("map", "map", ctx.link_input(ref.path, "refinement_map.mrc"), "Map of the refinement",
                           meta={**ref.meta})
        ctx.add_output("mask", "mask", ctx.link_input(mask_in.path, "mask_kept_region.mrc"), "Mask of the kept region",
                       meta={**mask_in.meta})
        ctx.add_highlight("Particles", f"{meta['n_particles']:,}")
        ctx.add_highlight("Box", f"{new_box or box} px")


# ---------------------------------------------------------- 3D classification
@register
class RelionClass3D(RelionJob):
    name = "relion_class3d"
    title = "RELION 3D classification (focused)"
    description = ("3D classification in RELION of particles with poses, focused by a mask, without alignment (or with "
                   "local searches): separates the states of a region, typically on subtracted particles. Every class "
                   "gives a map and its particles.")
    inputs = [
        Slot("particles", ("particles",), "Particles", help="With poses (subtracted particles for a focused "
                                                             "classification).", prefer=("particles",)),
        Slot("map", ("map",), "Reference map", help="The consensus (or refined) map, low-pass filtered by RELION.",
             prefer=("map",)),
        Slot("mask", ("mask",), "Mask", required=False, help="Region to classify, drawn on the consensus map."),
    ]
    params = [
        Param("classes", "int", 4, label="Number of classes", min=1, max=50),
        Param("iterations", "int", 25, label="Iterations", min=1, max=200),
        Param("tau2_fudge", "float", 4.0, label="Regularisation parameter T", min=0.1, max=1000,
              help="Higher values trust the data more (finer details, risk of noise): 4 by default, 10-40 for small "
                   "regions of subtracted particles."),
        Param("alignment", "choice", "none", choices=["none", "local"], label="Alignment",
              help="none: classification only, poses kept (--skip_align). local: local angular searches as well."),
        Param("angular_step", "choice", LOCAL_STEPS[0], choices=LOCAL_STEPS, label="Angular sampling (local)"),
        Param("sigma_ang", "float", 3.0, label="Angular search range (local)", unit="°", min=0.5, max=30,
              help="Standard deviation of the local searches (--sigma_ang)."),
        symmetry_param(),
        Param("blush", "bool", False, label="Blush regularisation"),
        Param("ini_high", "float", 0.0, label="Initial low-pass filter", unit="Å", min=0.0,
              help="0 = 3 × the resolution of the inputs, between 8 and 15 Å."),
        Param("particle_diameter", "float", 0.0, label="Mask diameter", unit="Å", min=0.0, advanced=True),
        Param("absolute_greyscale", "choice", "auto", choices=["auto", "yes", "no"], label="Reference on absolute greyscale",
              advanced=True),
        Param("pool", "int", 30, label="Pool of particles", min=1, max=200, advanced=True),
        mpi_param(3, 1),
        threads_param(),
        gpus_param(),
        extra_args_param(),
    ]
    outputs = [
        OutputDef("maps", "volume_series", "Class maps"),
        OutputDef("particles", "particles", "Classified particles"),
        OutputDef("class_1", "particles", "Particles of class 1"),
        OutputDef("map_1", "map", "Map of class 1"),
    ]
    completable = True

    def run(self, ctx: JobContext) -> None:
        p = ctx.params
        ctx.progress(0.01, "Preparing the particles")
        parts = prepare_particles(ctx, ctx.require("particles"))
        ref_in = ctx.require("map")
        ref, _ = map_on_grid(ctx, ref_in, parts, "reference.mrc")
        mask_in = ctx.input("mask")
        mask, mask_data = map_on_grid(ctx, mask_in, parts, "mask.mrc", mask=True) if mask_in else (None, None)
        sym = inherited_symmetry(ctx, parts, "map")
        diameter = float(p["particle_diameter"]) or rl.particle_diameter(mask_data, parts["pixel_size"], parts["box"])
        k, iters = int(p["classes"]), int(p["iterations"])
        args = [*relion_command(ctx, "relion_refine", p["mpi"]), "--o", ctx.rel(ctx.path("run")), "--i", parts["ref"],
                "--ref", ctx.rel(ref)]
        if not absolute_greyscale(ctx, ref_in):
            args.append("--firstiter_cc")
        args += ["--trust_ref_size", "--ini_high", f"{auto_ini_high(ctx, 'map', 'particles'):g}",
                 "--dont_combine_weights_via_disc", "--pool", str(p["pool"]), "--pad", "2", "--ctf",
                 "--iter", str(iters), "--tau2_fudge", f"{p['tau2_fudge']:g}", "--particle_diameter", f"{diameter:g}",
                 "--K", str(k), "--flatten_solvent", "--zero_mask"]
        if mask:
            args += ["--solvent_mask", ctx.rel(mask)]
        if p["alignment"] == "none":
            args.append("--skip_align")
        else:
            args += ["--oversampling", "1", "--healpix_order", str(rl.HEALPIX[p["angular_step"]]),
                     "--sigma_ang", f"{p['sigma_ang']:g}", "--offset_range", "3", "--offset_step", "1.5"]
        args += ["--sym", sym, "--norm", "--scale", "--j", str(p["threads"])]
        if p["blush"]:
            args.append("--blush")
        if p["gpus"] > 0:
            args += ["--gpu", ""]
        args += ctx.split_extra()
        progress = Progress(ctx, total=iters)
        ctx.progress(0.05, "relion_refine (classification)")
        run_relion(ctx, args, on_line=progress)
        self.finish(ctx, parts, sym, k)

    def finish_partial(self, ctx: JobContext) -> str:
        parts = prepare_particles(ctx, ctx.require("particles"))
        return f"iteration {self.finish(ctx, parts, inherited_symmetry(ctx, parts, 'map'), int(ctx.params['classes']))}"

    def finish(self, ctx: JobContext, parts: dict[str, Any], sym: str, k: int) -> int:
        """Maps and particles of the classes of the last iteration written."""
        it, stem = rl.iteration_stem(ctx.job_dir, suffix="_data.star")
        if not stem:
            raise JobError("RELION did not write any run_it###_data.star (see the log)")
        data_star = ctx.path(f"{stem}_data.star")
        model = rl.model_fsc(ctx.path(f"{stem}_model.star")) if ctx.path(f"{stem}_model.star").exists() else {}
        tables = rl.read_star(data_star)
        ptab = rl.particle_table(tables)
        classes = np.array([int(float(c)) for c in ptab.column("rlnClassNumber")])
        n = len(classes)
        maps, rows = [], []
        base_meta = {"symmetry": sym, "relion_map": True}
        for c in range(1, k + 1):
            path = ctx.path(f"{stem}_class{c:03d}.mrc")
            if not path.exists():
                continue
            m = to_consensus(ctx, path, parts, f"class_{c}.mrc")
            maps.append(m)
            count = int((classes == c).sum())
            rows.append([c, f"{count:,}", f"{100.0 * count / max(n, 1):.1f} %"])
            if count:
                sel = rl.write_star(ctx.path(f"class_{c}.star"),
                                    [t if t is not ptab else ptab.select(np.flatnonzero(classes == c)) for t in tables.values()],
                                    comment=f"Particles of class {c} ({data_star.name})")
                meta = particles_meta(ctx, sel, parts, "class3d", sym, class_number=c)
                ctx.add_output(f"class_{c}", "particles", sel, f"Particles of class {c} ({count:,})", meta=meta)
            ctx.add_output(f"map_{c}", "map", m, f"Map of class {c} ({100.0 * count / max(n, 1):.0f} %)",
                           meta={**base_meta, "class_number": c, "particles": count})
        if not maps:
            raise JobError("RELION wrote no class map (see the log)")
        ctx.add_output("maps", "volume_series", maps, f"{len(maps)} class maps", meta={**base_meta, "frames": len(maps)})
        meta = particles_meta(ctx, data_star, parts, "class3d", sym, optimiser=ctx.rel(ctx.path(f"{stem}_optimiser.star")))
        ctx.add_output("particles", "particles", data_star, f"Classified particles ({n:,})", meta=meta)
        if model.get("estimated_resolution") is not None:
            ctx.log(f"Estimated resolution of class 1: {model['estimated_resolution']:.2f} Å")
        ctx.add_table("Classes", ["Class", "Particles", "Fraction"], rows)
        ctx.add_highlight("Classes", len(maps))
        return it


# ------------------------------------------------------------- post-processing
@register
class RelionPostprocess(RelionJob):
    name = "relion_postprocess"
    title = "RELION post-processing"
    description = ("relion_postprocess of two half maps: FSC with the mask corrected by phase randomisation, B-factor "
                   "sharpening (automatic Guinier fit or given), optional local resolution. Works on the half maps of "
                   "any job (CryoSPARC imports, RELION refinements, composite maps).")
    gpu_param = ""
    cpu_param = ""
    inputs = [Slot("half_maps", ("half_maps",), "Half maps"), Slot("mask", ("mask",), "Mask")]
    params = [
        Param("bfactor", "float", 0.0, label="Sharpening B-factor", unit="Å²",
              help="0 = automatic (Rosenthal & Henderson fit from 10 Å); a negative value sharpens by that much."),
        Param("locres", "bool", False, label="Local resolution", help="--locres: RELION's local resolution map."),
        Param("mtf", "path", "", label="Detector MTF (STAR)", path_kind="file", advanced=True,
              help="MTF curve of the detector, as RELION writes it (e.g. mtf_k3_standard_300kV.star)."),
        extra_args_param(),
    ]
    outputs = [OutputDef("map_sharp", "map", "Post-processed map"), OutputDef("map_masked", "map", "Masked map"),
               OutputDef("fsc", "fsc", "FSC"), OutputDef("map_locres", "locres", "Local resolution map")]

    def run(self, ctx: JobContext) -> None:
        hm, mask_in = ctx.require("half_maps"), ctx.require("mask")
        a = MapVolume.read(hm.files[0], header_only=True)
        m = MapVolume.read(mask_in.path, header_only=True)
        if tuple(a.shape_xyz) != tuple(m.shape_xyz):
            raise JobError(f"The mask ({m.shape_xyz}) and the half maps ({a.shape_xyz}) have different boxes")
        h1, h2 = ctx.link_input(hm.files[0], "half1.mrc"), ctx.link_input(hm.files[1], "half2.mrc")
        mask = ctx.link_input(mask_in.path, "mask.mrc")
        res_in = hm.meta.get("resolution")
        pp = postprocess(ctx, h1, h2, mask, a.pixel_size, float(ctx.params["bfactor"]), float(res_in) if res_in else None,
                         locres=ctx.params["locres"], mtf=ctx.params["mtf"], extra=ctx.split_extra())
        resolution = pp.get("resolution")
        meta = {**{k: v for k, v in hm.meta.items() if k in ("symmetry",)}}
        if resolution:
            meta["resolution"] = round(resolution, 3)
            ctx.add_highlight("Resolution", f"{resolution:.2f} Å")
        if pp.get("bfactor") is not None:
            meta["bfactor"] = round(pp["bfactor"], 1)
            ctx.add_highlight("B-factor", f"{pp['bfactor']:.0f} Å²")
        if pp.get("curves"):
            report_fsc(ctx, pp["frequency"], pp["curves"], "corrected" if "corrected" in pp["curves"] else "unmasked",
                       resolution, "Fourier shell correlation (relion_postprocess)")
        ctx.add_output("map_sharp", "map", pp["map"], "Post-processed map", meta=meta)
        if pp["masked"].exists():
            ctx.add_output("map_masked", "map", pp["masked"], "Post-processed map (masked)", meta=meta)
        if ctx.params["locres"] and pp["locres"].exists():
            from cryoplug.jobs.imports import locres_meta
            lmeta = {**meta, **locres_meta(ctx, pp["locres"], mask), "colour_map": ctx.rel(pp["map"])}
            ctx.add_output("map_locres", "locres", pp["locres"], "Local resolution map (RELION)", meta=lmeta)


# --------------------------------------------------------------- reconstruction
@register
class RelionReconstruct(RelionJob):
    name = "relion_reconstruct"
    title = "RELION reconstruction"
    description = ("Reconstructs the two half maps and the full map of particles from their poses, without alignment "
                   "(relion_reconstruct): a full-resolution map of a particle subset (a cryoDRGN selection, a class), "
                   "with its gold-standard FSC.")
    gpu_param = ""
    cpu_param = "mpi"
    inputs = [Slot("particles", ("particles",), "Particles", prefer=("particles",)),
              Slot("mask", ("mask",), "Mask", required=False, help="For the masked FSC.")]
    params = [
        symmetry_param(),
        Param("maxres", "float", 0.0, label="Maximum resolution", unit="Å", min=0.0, help="0 = Nyquist."),
        mpi_param(1, 1, help="relion_reconstruct_mpi with more than 1."),
        extra_args_param(),
    ]
    outputs = [OutputDef("map", "map", "Map"), OutputDef("half_maps", "half_maps", "Half maps"), OutputDef("fsc", "fsc", "FSC")]

    def run(self, ctx: JobContext) -> None:
        from cryoplug.jobs.maptools import compute_and_report_fsc
        parts = prepare_particles(ctx, ctx.require("particles"), subsets=True)
        sym = inherited_symmetry(ctx, parts)
        made = {}
        for i, (name, subset) in enumerate((("half1", ["--subset", "1"]), ("half2", ["--subset", "2"]), ("map", []))):
            out = ctx.path(f"reconstruct_{name}.mrc")
            args = [*relion_command(ctx, "relion_reconstruct", ctx.params["mpi"]), "--i", parts["ref"], "--o", ctx.rel(out),
                    "--ctf", "--sym", sym, *subset]
            if ctx.params["maxres"] > 0:
                args += ["--maxres", f"{ctx.params['maxres']:g}"]
            args += ctx.split_extra()
            ctx.progress(0.05 + 0.3 * i, f"relion_reconstruct {name}")
            run_relion(ctx, args)
            if not out.exists():
                raise JobError(f"relion_reconstruct did not write {out.name} (see the log)")
            made[name] = to_consensus(ctx, out, parts, f"{name}.mrc" if parts["frame"] else out.name)
        if parts["info"].get("random_halves"):
            ctx.warn("The half sets were drawn at random here, not those of the refinement that gave the poses: the FSC "
                     "is optimistic (the two halves were aligned together)")
        mask_in = ctx.input("mask")
        res = compute_and_report_fsc(ctx, made["half1"], made["half2"], mask_in.path if mask_in else None)
        meta = {"symmetry": sym, "relion_map": True, "resolution": round(res, 3)}
        ctx.add_output("map", "map", made["map"], "Reconstruction", meta=meta)
        ctx.add_output("half_maps", "half_maps", [made["half1"], made["half2"]], "Half maps", meta=meta)
        ctx.add_highlight("Resolution", f"{res:.2f} Å")
        ctx.add_highlight("Particles", f"{parts['n']:,}")
