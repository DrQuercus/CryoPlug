"""RELION refinement and its companions: 3D auto-refine, mask creation, CTF refinement, Bayesian polishing, local
resolution, joining and splitting particle sets, multi-body refinement and DynaMight.

CTF refinement and polishing read a ``postprocess.star`` (FSC, half maps and mask): RELION's GUI asks for that of a
post-processing job; here the job runs relion_postprocess itself on the half maps and mask it is given.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np

from cryoplug import relion as rl
from cryoplug.jobs import register
from cryoplug.jobs.base import InputData, JobContext, JobError, JobType, OutputDef, Param, Slot, extra_args_param
from cryoplug.jobs.relion import (
    Progress, absolute_greyscale, finish_refinement, map_on_grid, outputs_of_refine, partial_refine_outputs, postprocess,
)
from cryoplug.jobs.relion_common import (
    SAMPLINGS, RelionJob, compute_params, continuation, continue_args, gpus, healpix, helical_check, helical_params,
    helical_refine_args, histogram,
    mask_diameter, mpi, particles_out_meta, refine_compute_params, relion_command, relion_particles, run_relion, star_of,
    threads,
)
from cryoplug.mrc import MapVolume

FIT_CHOICES = ["no", "micrograph", "particle"]
FIT_LABELS = {"no": "No", "micrograph": "Per micrograph", "particle": "Per particle"}
FIT_CODE = {"no": "f", "micrograph": "m", "particle": "p"}


# ------------------------------------------------------------------ 3D auto-refine
@register
class RelionRefine3D(RelionJob):
    name = "relion_refine3d"
    title = "3D auto-refine (RELION)"
    category = "Reconstruction"
    description = ("Gold-standard 3D refinement of the particles against a reference (relion_refine --auto_refine): "
                   "global angular searches first, local ones as it converges, two independent half sets. Gives the "
                   "map, the half maps, the FSC, the refined particles and, with a mask, the post-processed map.")
    inputs = [
        Slot("particles", ("particles",), "Particles"),
        Slot("map", ("map",), "Reference map", help="An initial model, a class of a 3D classification or a refined map.",
             prefer=("map",)),
        Slot("mask", ("mask",), "Mask", required=False,
             help="Soft mask around the particle: solvent-corrected FSC and post-processing."),
    ]
    params = [
        Param("ini_high", "float", 60.0, label="Initial low-pass filter", unit="Å", min=0,
              help="40-60 Å for an initial model; 10-15 Å for a map already refined from these particles."),
        Param("absolute_greyscale", "choice", "auto", choices=["auto", "yes", "no"], label="Reference on absolute greyscale",
              labels={"auto": "Auto (yes for a map made by RELION)", "yes": "Yes", "no": "No"},
              help="No: the first iteration uses cross-correlation (--firstiter_cc), right for a map from another "
                   "program. A map made by RELION from these particles is on RELION's absolute greyscale."),
        Param("symmetry", "str", "C1", label="Symmetry"),
        Param("particle_diameter", "float", 0.0, label="Mask diameter", unit="Å", min=0, help="0 = 90 % of the box."),
        Param("angular_step", "choice", "7.5°", choices=SAMPLINGS[:7], label="Initial angular sampling"),
        Param("local_from", "choice", "1.8°", choices=SAMPLINGS[:8], label="Local searches from",
              help="Angular sampling from which the searches are local."),
        Param("offset_range", "float", 5.0, label="Initial offset range", unit="px", min=0, max=100),
        Param("offset_step", "float", 1.0, label="Initial offset step", unit="px", min=0.1, max=10),
        Param("solvent_fsc", "bool", False, label="Solvent-flattened FSC",
              help="FSC of the masked maps drives the refinement (needs a mask): for small particles in a big box."),
        Param("blush", "bool", False, label="Blush regularisation"),
        Param("blush_model", "choice", "v1.0", choices=["v1.0", "amy-v1.0"], label="Blush network", advanced=True),
        Param("relax_sym", "str", "", label="Relax symmetry", advanced=True, placeholder="e.g. C2"),
        Param("auto_faster", "bool", False, label="Finer angular sampling faster", advanced=True,
              help="--auto_ignore_angles --auto_resol_angles."),
        Param("zero_mask", "bool", True, label="Mask particles with zeros", advanced=True),
        Param("pad1", "bool", False, label="Skip padding", advanced=True),
        Param("postprocess", "bool", True, label="Post-process with the mask"),
        Param("bfactor", "float", 0.0, label="Sharpening B-factor", unit="Å²", help="0 = automatic."),
        *helical_params("3d"),
        *refine_compute_params(3),
        mpi(3, 3, help="At least 3 (a leader and one process per half set: RELION's requirement); with GPUs, 2 per "
                       "GPU plus one."),
        threads(4),
        gpus(1),
        extra_args_param(),
    ]
    outputs = [OutputDef("map", "map", "Refined map"), OutputDef("half_maps", "half_maps", "Half maps"),
               OutputDef("map_sharp", "map", "Post-processed map"), OutputDef("mask", "mask", "Mask"),
               OutputDef("fsc", "fsc", "FSC"), OutputDef("particles", "particles", "Refined particles")]
    continuable = True
    completable = True
    intermediate = rl.ITERATION_FILES

    @classmethod
    def validate(cls, params: dict[str, Any], connected: dict[str, str]) -> list[str]:
        return helical_check(params)

    def finish_partial(self, ctx: JobContext) -> str:
        parts = relion_particles(ctx, ctx.require("particles"))
        mask_in = ctx.input("mask")
        mask, mask_data = map_on_grid(ctx, mask_in, parts, "mask.mrc", mask=True) if mask_in else (None, None)
        it, out = partial_refine_outputs(ctx)
        finish_refinement(ctx, out, parts, (str(ctx.params["symmetry"]).strip() or "C1").upper(), mask, mask_data,
                          Progress(ctx), kind="refine3d")
        return f"iteration {it}"

    def run(self, ctx: JobContext) -> None:
        p = ctx.params
        parts = relion_particles(ctx, ctx.require("particles"))
        ref_in = ctx.require("map")
        ref, _ = map_on_grid(ctx, ref_in, parts, "reference.mrc")
        mask_in = ctx.input("mask")
        mask, mask_data = map_on_grid(ctx, mask_in, parts, "mask.mrc", mask=True) if mask_in else (None, None)
        sym = (str(p["symmetry"]).strip() or "C1").upper()
        cont = continuation(ctx)
        if cont:  # a failed or killed run goes on from its last iteration
            opt, it, root = cont
            args = continue_args(ctx, p, opt, root)
            if p["auto_faster"]:
                args += ["--auto_ignore_angles", "--auto_resol_angles"]
            ctx.add_table("3D auto-refine", ["Setting", "Value"], [
                ["Continued", f"from iteration {it} ({Path(opt).name})"],
                ["Particles", f"{parts['n']:,} · box {parts['box']} px · {parts['pixel_size']:g} Å/px"], ["Symmetry", sym]])
            progress = Progress(ctx)
            ctx.progress(0.03, f"relion_refine (auto-refine, continued from iteration {it})")
            run_relion(ctx, args, on_line=progress)
            finish_refinement(ctx, outputs_of_refine(ctx, root), parts, sym, mask, mask_data, progress, kind="refine3d")
            return
        diameter = mask_diameter(ctx, parts)
        args = [*relion_command(ctx, "relion_refine", p["mpi"]), "--o", ctx.rel(ctx.path("run")), "--auto_refine",
                "--split_random_halves", "--i", parts["ref"], "--ref", ctx.rel(ref)]
        if not absolute_greyscale(ctx, ref_in):
            args.append("--firstiter_cc")
        args.append("--trust_ref_size")
        if p["ini_high"] > 0:
            args += ["--ini_high", f"{p['ini_high']:g}"]
        if p["blush"]:
            args += ["--blush", "--blush_model", p["blush_model"]]
        compute_params(p, args)
        args += ["--pad", "1" if p["pad1"] else "2"]
        if p["auto_faster"]:
            args += ["--auto_ignore_angles", "--auto_resol_angles"]
        args += ["--ctf", "--particle_diameter", f"{diameter:g}", "--flatten_solvent"]
        if p["zero_mask"]:
            args.append("--zero_mask")
        if mask:
            args += ["--solvent_mask", ctx.rel(mask)]
            if p["solvent_fsc"]:
                args.append("--solvent_correct_fsc")
        args += ["--oversampling", "1", "--healpix_order", str(healpix(p["angular_step"])),
                 "--auto_local_healpix_order", str(healpix(p["local_from"])), "--offset_range", f"{p['offset_range']:g}",
                 "--offset_step", f"{2 * p['offset_step']:g}", "--sym", sym, "--low_resol_join_halves", "40",
                 "--norm", "--scale"]
        args += helical_refine_args(p, local_differs=healpix(p["angular_step"]) != healpix(p["local_from"]))
        if str(p["relax_sym"]).strip():
            args += ["--relax_sym", str(p["relax_sym"]).strip().upper()]
        args += ["--j", str(p["threads"])]
        if p["gpus"] > 0:
            args += ["--gpu", ""]
        args += ctx.split_extra()
        ctx.add_table("3D auto-refine", ["Setting", "Value"], [
            ["Particles", f"{parts['n']:,} · box {parts['box']} px · {parts['pixel_size']:g} Å/px"],
            ["Symmetry", sym], ["Initial low-pass", f"{p['ini_high']:g} Å"],
            ["Angular sampling", f"{p['angular_step']}, local from {p['local_from']}"],
            ["Mask diameter", f"{diameter:g} Å"], ["Mask", mask_in.source if mask_in else "none"],
            ["Blush", "yes" if p["blush"] else "no"]])
        progress = Progress(ctx)
        ctx.progress(0.03, "relion_refine (auto-refine)")
        run_relion(ctx, args, on_line=progress)
        finish_refinement(ctx, outputs_of_refine(ctx), parts, sym, mask, mask_data, progress, kind="refine3d")


# ------------------------------------------------------------------ mask creation
@register
class RelionMaskCreate(RelionJob):
    name = "relion_mask_create"
    title = "Mask creation (RELION)"
    category = "Map processing"
    description = ("relion_mask_create: the map low-pass filtered, thresholded, extended by a few pixels and given a "
                   "soft raised-cosine edge. The solvent mask of post-processing, refinement and CTF refinement.")
    gpu_param = ""
    cpu_param = "threads"
    inputs = [Slot("map", ("map",), "Map", help="A refined (unsharpened) map, or a class of it.", prefer=("map",))]
    params = [
        Param("lowpass", "float", 15.0, label="Low-pass filter", unit="Å", min=0, help="15-20 Å for a solvent mask."),
        Param("threshold", "float", 0.0, label="Initial binarisation threshold",
              help="The lowest level without noise outside the particle. 0 = automatic (Otsu threshold of the "
                   "low-pass filtered map)."),
        Param("extend", "int", 3, label="Extend the binary mask", unit="px", min=0, max=50),
        Param("soft_edge", "int", 6, label="Soft edge", unit="px", min=0, max=50,
              help="Raised-cosine edge (RELION's default 3; 5-10 pixels are usual)."),
        *helical_params("mask"),
        threads(4),
        extra_args_param(),
    ]
    outputs = [OutputDef("mask", "mask", "Mask")]

    def run(self, ctx: JobContext) -> None:
        p = ctx.params
        inp = ctx.require("map")
        threshold = float(p["threshold"])
        if not threshold:
            from cryoplug.masks import otsu_threshold, sample_values, smooth_map
            vol = MapVolume.read(inp.path)
            threshold = float(otsu_threshold(sample_values(smooth_map(vol, p["lowpass"] or 15.0))))
            ctx.log(f"Threshold: {threshold:.4g} (Otsu threshold of the map low-pass filtered at {p['lowpass']:g} Å)")
        out = ctx.path("mask.mrc")
        args = [ctx.program("relion", "relion_mask_create"), "--i", ctx.rel(inp.path), "--o", ctx.rel(out)]
        if p["lowpass"] > 0:
            args += ["--lowpass", f"{p['lowpass']:g}"]
        args += ["--ini_threshold", f"{threshold:.6g}", "--extend_inimask", str(p["extend"]),
                 "--width_soft_edge", str(p["soft_edge"])]
        if p["helical"]:  # a cylinder of the central part along z
            args += ["--helix", "--z_percentage", f"{p['helical_z_percentage'] / 100:g}"]
        args += ["--j", str(p["threads"]), *ctx.split_extra()]
        ctx.progress(0.1, "relion_mask_create")
        run_relion(ctx, args)
        if not out.exists():
            raise JobError("relion_mask_create wrote no mask.mrc (see the log)")
        data = MapVolume.read(out).data
        if float(data.max()) < 0.5:
            raise JobError(f"The mask is empty at threshold {threshold:.4g}: lower the threshold")
        from cryoplug.masks import statistics
        voxel = MapVolume.read(out).voxel
        stats = statistics(data, voxel)
        ctx.add_table("Mask", ["", "Value"], [["Threshold", f"{threshold:.4g}"],
                                             ["Low-pass", f"{p['lowpass']:g} Å"],
                                             ["Extension / soft edge", f"{p['extend']} / {p['soft_edge']} px"],
                                             *[[k.replace("_", " ").capitalize(), f"{v:.3g}"] for k, v in stats.items()]])
        ctx.add_output("mask", "mask", out, "Mask", inherit=("map",), meta={"threshold": round(threshold, 6)})


# ------------------------------------------------------------------ symmetry axes
@register
class RelionAlignSymmetry(JobType):
    name = "relion_align_symmetry"
    title = "Align to symmetry axes (RELION)"
    category = "Map processing"
    tool = "relion"
    software = ["RELION"]
    description = ("relion_align_symmetry: a map refined in C1 turned (and re-centred) so that its symmetry axes are "
                   "RELION's standard ones, as CryoSPARC's Volume Alignment Tools: the reference of a symmetric "
                   "refinement, or of a symmetry expansion. Optionally with the symmetry applied.")
    inputs = [Slot("map", ("map",), "Map", help="A map refined without symmetry (C1).")]
    params = [
        Param("symmetry", "str", "C2", label="Symmetry", required=True, help="Point group: Cn, Dn, T, O, I (I1 to I4 conventions)."),
        Param("apply_sym", "bool", False, label="Apply the symmetry", help="The aligned map symmetrised (--apply_sym)."),
        Param("keep_centre", "bool", False, label="Keep the centre", help="Do not re-centre the map first (--keep_centre)."),
        Param("only_rot", "bool", False, label="Rotation about z only", advanced=True,
              help="The axis is already along z: search only the rotation around it (--only_rot)."),
        Param("box_size", "int", 64, label="Working box", unit="px", min=32, max=512, advanced=True,
              help="Small box (Nyquist about 20 Å) is usually enough."),
        Param("nr_uniform", "int", 400, label="Orientations tried", min=10, max=100000, advanced=True),
        Param("max_resolution", "float", -1.0, label="Highest resolution used", unit="Å", advanced=True, help="-1 = Nyquist of the working box."),
        extra_args_param(),
    ]
    outputs = [OutputDef("map", "map", "Aligned map")]

    @classmethod
    def validate(cls, params: dict[str, Any], connected: dict[str, str]) -> list[str]:
        sym = str(params.get("symmetry") or "").strip().upper()
        if not re.fullmatch(r"C\d+|D\d+|T|O|I\d?", sym):
            return ["Give a point group: Cn, Dn, T, O or I"]
        if sym == "C1":
            return ["C1 has no axis to align"]
        return []

    def run(self, ctx: JobContext) -> None:
        p = ctx.params
        inp = ctx.require("map")
        sym = str(p["symmetry"]).strip().upper()
        apix = float(MapVolume.read(inp.path, header_only=True).voxel[0])
        out = ctx.path("aligned.mrc")
        args = [ctx.program("relion", "relion_align_symmetry"), "--i", ctx.rel(inp.path), "--o", ctx.rel(out), "--sym", sym,
                "--angpix", f"{apix:g}", "--box_size", str(p["box_size"]), "--nr_uniform", str(p["nr_uniform"])]
        if p["max_resolution"] > 0:
            args += ["--maxres", f"{p['max_resolution']:g}"]
        for flag in ("keep_centre", "only_rot", "apply_sym"):
            if p[flag]:
                args.append(f"--{flag}")
        ctx.progress(0.1, f"relion_align_symmetry ({sym})")
        run_relion(ctx, args + ctx.split_extra())
        if not out.exists():
            raise JobError("relion_align_symmetry wrote no aligned map (see the log)")
        meta = {**ctx.inherited_meta("map"), "symmetry": sym if p["apply_sym"] else "C1", "aligned_to": sym}
        ctx.add_output("map", "map", out, f"Aligned to the {sym} axes" + (" (symmetrised)" if p["apply_sym"] else ""), meta=meta)
        ctx.add_highlight("Symmetry axes", sym)


# ------------------------------------------------------------------ helical symmetry tools
@register
class RelionHelixToolbox(JobType):
    name = "relion_helix_toolbox"
    title = "Helical symmetry (RELION)"
    category = "Map processing"
    tool = "relion"
    software = ["RELION"]
    description = ("relion_helix_toolbox on the map of a filament: local search of the helical twist and rise (--search), "
                   "then the symmetry found imposed in real space; a given symmetry imposed (--impose); or a cylinder as "
                   "the initial reference of a helical refinement (--cylinder).")
    inputs = [Slot("map", ("map",), "Map", required=False, help="A helical reconstruction (search, impose).")]
    params = [
        Param("mode", "choice", "search", choices=["search", "impose", "cylinder"], label="Tool",
              labels={"search": "Search the twist and rise, then impose them", "impose": "Impose a helical symmetry",
                      "cylinder": "Cylinder (initial reference)"}),
        Param("outer_diameter", "float", 200.0, label="Tube diameter", unit="Å", min=1,
              help="Outer diameter of the filament: the symmetry is searched and imposed within it."),
        Param("inner_diameter", "float", -1.0, label="Inner tube diameter", unit="Å", help="Hollow filaments; -1 = solid."),
        Param("twist_min", "float", -2.0, label="Twist from", unit="°", show_if="mode=search", help="+ for right-handed."),
        Param("twist_max", "float", -0.5, label="Twist to", unit="°", show_if="mode=search"),
        Param("rise_min", "float", 4.6, label="Rise from", unit="Å", min=0, show_if="mode=search"),
        Param("rise_max", "float", 4.9, label="Rise to", unit="Å", min=0, show_if="mode=search"),
        Param("twist", "float", 0.0, label="Twist", unit="°", show_if="mode=impose", help="+ for right-handed."),
        Param("rise", "float", 4.75, label="Rise", unit="Å", min=0, show_if="mode=impose"),
        Param("z_percentage", "float", 30.0, label="Central part along the axis", unit="%", min=10, max=90,
              show_if="mode=search|impose", help="Part of the box height used (the ends of a reconstruction are worse)."),
        Param("box", "int", 256, label="Box", unit="px", min=20, max=2048, show_if="mode=cylinder"),
        Param("pixel_size", "float", 1.0, label="Pixel size", unit="Å", min=0.01, show_if="mode=cylinder",
              help="That of the particles the reference is for."),
        Param("polar", "bool", False, label="Polar cylinder", show_if="mode=cylinder", advanced=True,
              help="Narrower at the bottom, for a polar reference (--polar)."),
        extra_args_param(),
    ]
    outputs = [OutputDef("map", "map", "Map")]

    @classmethod
    def validate(cls, params: dict[str, Any], connected: dict[str, str]) -> list[str]:
        mode = params.get("mode", "search")
        if mode in ("search", "impose") and "map" not in connected:
            return ["Connect the map of the filament"]
        if mode == "search" and not (float(params.get("twist_min", -2)) < float(params.get("twist_max", -0.5))
                                     and 0 < float(params.get("rise_min", 4.6)) < float(params.get("rise_max", 4.9))):
            return ["The search ranges need a lower and a higher value (rise above 0)"]
        if mode == "impose" and not float(params.get("rise") or 0) > 0:
            return ["Give the helical rise"]
        return []

    def run(self, ctx: JobContext) -> None:
        p = ctx.params
        prog = ctx.program("relion", "relion_helix_toolbox")
        diam = ["--cyl_inner_diameter", f"{p['inner_diameter']:g}", "--cyl_outer_diameter", f"{p['outer_diameter']:g}"]
        if p["mode"] == "cylinder":
            out = ctx.path("cylinder.mrc")
            args = [prog, "--cylinder", "--o", ctx.rel(out), "--boxdim", str(p["box"]), *diam,
                    "--angpix", f"{p['pixel_size']:g}", "--sphere_percentage", "0.9", "--width", "5"]
            if p["polar"]:
                args.append("--polar")
            run_relion(ctx, args + ctx.split_extra())
            if not out.exists():
                raise JobError("relion_helix_toolbox wrote no cylinder (see the log)")
            ctx.add_output("map", "map", out, f"Cylinder ({p['outer_diameter']:g} Å, {p['box']} px)",
                           meta={"helical_reference": True})
            return
        inp = ctx.require("map")
        apix = float(MapVolume.read(inp.path).voxel[0])
        common = ["--i", ctx.rel(inp.path), *diam, "--angpix", f"{apix:g}", "--z_percentage", f"{p['z_percentage'] / 100:g}"]
        if p["mode"] == "search":
            found: dict[str, float] = {}

            def read(line: str) -> None:
                m = re.search(r"Refined helical rise = ([-\d.eE+]+) Angstroms, twist = ([-\d.eE+]+) degrees", line)
                if m:
                    found.update(rise=float(m.group(1)), twist=float(m.group(2)))
            ctx.progress(0.1, "Local search of the helical twist and rise")
            run_relion(ctx, [prog, "--search", *common, "--rise_min", f"{p['rise_min']:g}", "--rise_max", f"{p['rise_max']:g}",
                             "--twist_min", f"{p['twist_min']:g}", "--twist_max", f"{p['twist_max']:g}", *ctx.split_extra()],
                       on_line=read)
            if not found:
                raise JobError("relion_helix_toolbox did not report the refined symmetry (see the log)")
            twist, rise = found["twist"], found["rise"]
            ctx.add_table("Helical symmetry found", ["", "Value"], [
                ["Twist", f"{twist:.4f}° (searched {p['twist_min']:g} to {p['twist_max']:g}°)"],
                ["Rise", f"{rise:.4f} Å (searched {p['rise_min']:g} to {p['rise_max']:g} Å)"],
                ["Handedness", "right-handed" if twist > 0 else "left-handed"]])
            ctx.add_highlight("Twist", f"{twist:.3f}°")
            ctx.add_highlight("Rise", f"{rise:.3f} Å")
            at_edge = [n for n, v, lo, hi in (("twist", twist, p["twist_min"], p["twist_max"]), ("rise", rise, p["rise_min"], p["rise_max"]))
                       if min(abs(v - lo), abs(v - hi)) < 1e-3 * max(1.0, abs(hi - lo))]
            if at_edge:
                ctx.warn(f"The {' and '.join(at_edge)} found are at the end of the range searched: widen it")
        else:
            twist, rise = float(p["twist"]), float(p["rise"])
        out = ctx.path("helical.mrc")
        ctx.progress(0.7, "Helical symmetry imposed in real space")
        run_relion(ctx, [prog, "--impose", *common, "--o", ctx.rel(out), "--rise", f"{rise:g}", "--twist", f"{twist:g}"])
        if not out.exists():
            raise JobError("relion_helix_toolbox wrote no map (see the log)")
        ctx.add_output("map", "map", out, f"Helical symmetry imposed ({twist:.3f}°, {rise:.3f} Å)",
                       meta={"helical_twist": round(twist, 5), "helical_rise": round(rise, 5)})


# ------------------------------------------------------------------ helpers of CTF refinement and polishing
def postprocess_star(ctx: JobContext, parts: dict[str, Any]) -> tuple[Path, float | None]:
    """relion_postprocess of the connected half maps with the mask, on the grid of the particles (the
    postprocess.star that relion_ctf_refine and relion_motion_refine read)."""
    halves = ctx.require("half_maps")
    if len(halves.files) < 2:
        raise JobError(f"{halves.source} is not a pair of half maps")
    h1, d1 = map_on_grid(ctx, InputData({**halves.data, "path": halves.files[0]}), parts, "half1_unfil.mrc")
    h2, _ = map_on_grid(ctx, InputData({**halves.data, "path": halves.files[1]}), parts, "half2_unfil.mrc")
    mask, _ = map_on_grid(ctx, ctx.require("mask"), parts, "mask.mrc", mask=True)
    ctx.progress(0.05, "relion_postprocess (FSC of the half maps, for the weighting)")
    pp = postprocess(ctx, h1, h2, mask, parts["pixel_size"], 0.0, halves.meta.get("resolution"))
    if pp.get("resolution"):
        ctx.log(f"Post-processing: {pp['resolution']:.2f} Å")
    return ctx.path("postprocess.star"), pp.get("resolution")


def optics_rows(star: Path) -> list[list[str]]:
    tables = rl.read_star(star)
    optics = tables.get("optics")
    if optics is None:
        return []
    wanted = [lab for lab in ("rlnOpticsGroupName", "rlnBeamTiltX", "rlnBeamTiltY", "rlnOddZernike", "rlnEvenZernike",
                              "rlnMagMat00", "rlnMagMat01", "rlnMagMat10", "rlnMagMat11") if optics.has(lab)]
    return [wanted] + [[row[optics.labels.index(lab)] for lab in wanted] for row in optics.rows]


# ------------------------------------------------------------------ CTF refinement
@register
class RelionCtfRefine(RelionJob):
    name = "relion_ctf_refine"
    title = "CTF refinement (RELION)"
    category = "Refinement"
    description = ("Refines the optics of the data set and the CTF of every particle against the refined map "
                   "(relion_ctf_refine): anisotropic magnification, beam tilt, trefoil and 4th-order aberrations per "
                   "optics group, then defocus per particle and astigmatism per micrograph. 'Complete' runs the three "
                   "passes of the RELION tutorial in a row; refine the particles again afterwards.")
    gpu_param = ""
    inputs = [
        Slot("particles", ("particles",), "Refined particles", help="From a RELION 3D auto-refine."),
        Slot("half_maps", ("half_maps",), "Half maps", help="Of the same refinement."),
        Slot("mask", ("mask",), "Mask", help="Soft solvent mask (encompassing the whole particle)."),
    ]
    params = [
        Param("mode", "choice", "complete", choices=["complete", "custom"], label="Refinement",
              labels={"complete": "Complete: magnification, aberrations, then defocus and astigmatism",
                      "custom": "The fits chosen below (one pass)"}),
        Param("fit_defocus", "choice", "particle", choices=FIT_CHOICES, label="Fit defocus", labels=FIT_LABELS),
        Param("fit_astigmatism", "choice", "micrograph", choices=FIT_CHOICES, label="Fit astigmatism", labels=FIT_LABELS),
        Param("fit_bfactor", "choice", "no", choices=FIT_CHOICES, label="Fit B-factor", labels=FIT_LABELS),
        Param("fit_phase", "choice", "no", choices=FIT_CHOICES, label="Fit phase shift", labels=FIT_LABELS,
              help="Phase plate data."),
        Param("aniso_mag", "bool", False, label="Anisotropic magnification", help="Custom: alone in its pass."),
        Param("beamtilt", "bool", False, label="Beam tilt", help="Data beyond about 4.5 Å."),
        Param("trefoil", "bool", False, label="Trefoil", help="Data beyond about 3.5 Å."),
        Param("fourth_order", "bool", False, label="4th-order aberrations", help="Data beyond about 3 Å."),
        Param("min_resolution", "float", 30.0, label="Minimum resolution for the fits", unit="Å", min=5, max=100),
        mpi(1),
        threads(8),
        extra_args_param(),
    ]
    outputs = [OutputDef("particles", "particles", "Particles with refined CTF")]

    def passes(self) -> list[tuple[str, list[str]]]:
        p = self.p
        kmin = f"{p['min_resolution']:g}"
        ctf = ["--fit_defocus", "--kmin_defocus", kmin,
               "--fit_mode", FIT_CODE[p["fit_phase"]] + FIT_CODE[p["fit_defocus"]] + FIT_CODE[p["fit_astigmatism"]]
               + "f" + FIT_CODE[p["fit_bfactor"]]]
        if p["mode"] == "complete":
            return [("anisotropic magnification", ["--fit_aniso", "--kmin_mag", kmin]),
                    ("beam tilt, trefoil and 4th-order aberrations", ["--fit_beamtilt", "--kmin_tilt", kmin,
                                                                       "--odd_aberr_max_n", "3", "--fit_aberr"]),
                    ("defocus per particle, astigmatism per micrograph",
                     ["--fit_defocus", "--kmin_defocus", kmin, "--fit_mode", "fpmff"])]
        if p["aniso_mag"]:
            return [("anisotropic magnification", ["--fit_aniso", "--kmin_mag", kmin])]
        args: list[str] = []
        if any(p[k] != "no" for k in ("fit_defocus", "fit_astigmatism", "fit_bfactor", "fit_phase")):
            args += ctf
        if p["beamtilt"]:
            args += ["--fit_beamtilt", "--kmin_tilt", kmin]
            if p["trefoil"]:
                args += ["--odd_aberr_max_n", "3"]
        if p["fourth_order"]:
            args.append("--fit_aberr")
        if not args:
            raise JobError("Nothing to fit: choose at least one parameter")
        return [("custom fits", args)]

    def run(self, ctx: JobContext) -> None:
        self.p = p = ctx.params
        parts = relion_particles(ctx, ctx.require("particles"), need_poses=True)
        post, _ = postprocess_star(ctx, parts)
        current = parts["ref"]
        steps = self.passes()
        before = rl.read_star(parts["star"])
        for i, (label, fit) in enumerate(steps, 1):
            out_dir = ctx.path(f"pass{i}") if len(steps) > 1 else ctx.job_dir
            out_dir.mkdir(exist_ok=True)
            ctx.progress(0.1 + 0.8 * (i - 1) / len(steps), f"relion_ctf_refine {i}/{len(steps)}: {label}")
            args = [*relion_command(ctx, "relion_ctf_refine", p["mpi"]), "--i", current, "--f", ctx.rel(post),
                    "--o", ctx.rel(out_dir) + "/", *fit, "--j", str(p["threads"]), *ctx.split_extra()]
            run_relion(ctx, args)
            star = out_dir / "particles_ctf_refine.star"
            if not star.exists():
                raise JobError(f"relion_ctf_refine ({label}) wrote no particles_ctf_refine.star (see the log)")
            current = ctx.rel(star)
        final = ctx.abs(current)
        rows = optics_rows(final)
        if rows:
            ctx.add_table("Optics groups after refinement", rows[0], rows[1:])
        self.defocus_change(ctx, before, rl.read_star(final))
        meta = particles_out_meta(ctx, final, "ctfrefine")
        ctx.add_highlight("Passes", str(len(steps)))
        ctx.add_output("particles", "particles", final, f"Particles with refined CTF ({meta['n_particles']:,})", meta=meta)

    @staticmethod
    def defocus_change(ctx: JobContext, before: dict[str, rl.StarTable], after: dict[str, rl.StarTable]) -> None:
        a, b = rl.particle_table(before), rl.particle_table(after)
        if not (a.has("rlnImageName") and b.has("rlnImageName") and a.has("rlnDefocusU") and b.has("rlnDefocusU")):
            return
        old = dict(zip(a.column("rlnImageName"), a.floats("rlnDefocusU")))
        delta = np.array([new - old[name] for name, new in zip(b.column("rlnImageName"), b.floats("rlnDefocusU"))
                          if name in old])
        if len(delta):
            ctx.add_plot("Defocus change per particle", [{"name": "Particles", **histogram(delta, 40)}],
                         x_label="Change of defocus U (Å)", y_label="Particles", style="bars")
            ctx.add_highlight("Defocus change", f"{np.median(np.abs(delta)):.0f} Å (median)")


# ------------------------------------------------------------------ Bayesian polishing
@register
class RelionPolish(RelionJob):
    name = "relion_polish"
    title = "Bayesian polishing (RELION)"
    category = "Refinement"
    description = ("Tracks the motion of every particle through the movie frames and re-extracts them with frame "
                   "weights fitted to the data (relion_motion_refine). Train the motion parameters once on a subset "
                   "(optional), then polish; refine the polished ('shiny') particles again.")
    gpu_param = ""
    inputs = [
        Slot("particles", ("particles",), "Refined particles", help="From a RELION 3D auto-refine (or CTF refinement)."),
        Slot("micrographs", ("micrographs",), "Micrographs from motion correction",
             help="The output of RELION motion correction (it holds the motion of every movie)."),
        Slot("half_maps", ("half_maps",), "Half maps"),
        Slot("mask", ("mask",), "Mask"),
        Slot("params", ("polish_params",), "Trained parameters", required=False,
             help="The parameters of a training run (otherwise the three sigmas below)."),
    ]
    params = [
        Param("mode", "choice", "polish", choices=["polish", "train"], label="Task",
              labels={"polish": "Polish the particles", "train": "Train the motion parameters"}),
        Param("sigma_vel", "float", 0.2, label="Sigma for velocity", unit="Å/dose", min=0,
              help="Smaller values: shorter tracks."),
        Param("sigma_div", "float", 5000.0, label="Sigma for divergence", unit="Å", min=0,
              help="Smaller values: tracks more alike across the micrograph."),
        Param("sigma_acc", "float", 2.0, label="Sigma for acceleration", unit="Å/dose",
              help="Smaller values: straighter tracks. Training: negative to fit two parameters only."),
        Param("eval_fraction", "float", 0.5, label="Fourier pixels for testing", min=0.1, max=0.9, advanced=True),
        Param("min_particles", "int", 10000, label="Particles for training", min=1000),
        Param("first_frame", "int", 1, label="First frame", min=1),
        Param("last_frame", "int", -1, label="Last frame", help="0 or less: all."),
        Param("box", "int", -1, label="Extraction box", unit="px", advanced=True,
              help="In unbinned movie pixels (even); -1 = that of the extraction (with the re-scaled box below)."),
        Param("rescale", "int", -1, label="Re-scaled box", unit="px", advanced=True, help="-1 = as extracted."),
        Param("bfac_minres", "float", 20.0, label="Minimum resolution for B-factor fit", unit="Å", advanced=True),
        Param("bfac_maxres", "float", -1.0, label="Maximum resolution for B-factor fit", unit="Å", advanced=True,
              help="-1 = from the FSC."),
        Param("float16", "bool", True, label="Write in float16"),
        mpi(1),
        threads(8),
        extra_args_param(),
    ]
    outputs = [OutputDef("particles", "particles", "Polished particles"),
               OutputDef("polish_params", "polish_params", "Trained parameters")]

    @classmethod
    def validate(cls, params: dict[str, Any], connected: dict[str, str]) -> list[str]:
        box, scale = int(params.get("box") or -1), int(params.get("rescale") or -1)
        if (box > 0) != (scale > 0):
            return ["Give both the extraction box and the re-scaled box, or neither"]
        if box > 0 and (box % 2 or scale % 2 or scale > box):
            return ["Boxes must be even, the re-scaled one not larger"]
        return []

    def run(self, ctx: JobContext) -> None:
        p = ctx.params
        parts = relion_particles(ctx, ctx.require("particles"), need_poses=True)
        mics_in = ctx.require("micrographs")
        mstar, mref = star_of(ctx, mics_in)
        if not rl.micrograph_table(rl.read_star(mstar)).has("rlnMicrographMetadata"):
            # micrographs after CTF estimation: those of the motion correction before it hold the motion
            motion = mics_in.meta.get("motion_star")
            if not motion or not ctx.abs(motion).exists():
                raise JobError(f"{mics_in.source} has no movie motion (rlnMicrographMetadata): connect the micrographs "
                               "of RELION motion correction")
            mref = motion
            ctx.log(f"Movie motion from {motion} (the motion correction of {mics_in.source})")
        post, _ = postprocess_star(ctx, parts)
        args = [*relion_command(ctx, "relion_motion_refine", p["mpi"]), "--i", parts["ref"], "--f", ctx.rel(post),
                "--corr_mic", mref, "--first_frame", str(p["first_frame"]), "--last_frame", str(p["last_frame"]),
                "--o", ctx.rel(ctx.job_dir) + "/"]
        if p["float16"]:
            args.append("--float16")
        if p["mode"] == "train":
            args += ["--min_p", str(p["min_particles"]), "--eval_frac", f"{p['eval_fraction']:g}",
                     "--align_frac", f"{1 - p['eval_fraction']:g}", "--params2" if p["sigma_acc"] < 0 else "--params3"]
        else:
            trained = ctx.input("params")
            if trained:
                args += ["--params_file", ctx.rel(trained.path)]
            else:
                args += ["--s_vel", f"{p['sigma_vel']:g}", "--s_div", f"{p['sigma_div']:g}", "--s_acc", f"{p['sigma_acc']:g}"]
            args += ["--combine_frames", "--bfac_minfreq", f"{p['bfac_minres']:g}", "--bfac_maxfreq", f"{p['bfac_maxres']:g}"]
            if p["box"] > 0:
                args += ["--window", str(p["box"]), "--scale", str(p["rescale"])]
        args += ["--j", str(p["threads"]), *ctx.split_extra()]
        ctx.progress(0.1, "relion_motion_refine (" + ("training" if p["mode"] == "train" else "polishing") + ")")
        run_relion(ctx, args, on_line=self.watch(ctx))
        if p["mode"] == "train":
            self.trained(ctx)
        else:
            self.polished(ctx)

    @staticmethod
    def watch(ctx: JobContext):
        def on_line(line: str) -> None:
            if "Estimating motion" in line or "Combining frames" in line or "Fitting B-factors" in line:
                ctx.progress(0.5 if "Estimating" in line else 0.8, line.strip(" +"), log=False)
        return on_line

    @staticmethod
    def trained(ctx: JobContext) -> None:
        path = ctx.path("opt_params_all_groups.txt")
        if not path.exists():
            raise JobError("relion_motion_refine wrote no opt_params_all_groups.txt (see the log)")
        values = [float(v) for v in re.findall(r"[-+0-9.eE]+", path.read_text())[:3]]
        meta: dict[str, Any] = {}
        if len(values) == 3:
            meta = {"sigma_vel": values[0], "sigma_div": values[1], "sigma_acc": values[2]}
            ctx.add_table("Trained parameters", ["Parameter", "Value"],
                          [["Sigma for velocity", f"{values[0]:.4g} Å/dose"], ["Sigma for divergence", f"{values[1]:.4g} Å"],
                           ["Sigma for acceleration", f"{values[2]:.4g} Å/dose"]])
        ctx.add_output("polish_params", "polish_params", path, "Trained polishing parameters", meta=meta)

    @staticmethod
    def polished(ctx: JobContext) -> None:
        star = ctx.path("shiny.star")
        if not star.exists():
            raise JobError("relion_motion_refine wrote no shiny.star (see the log)")
        bf = ctx.path("bfactors.star")
        if bf.exists():
            try:
                table = next(t for t in rl.read_star(bf).values() if t.loop)
                frames = table.floats("rlnMovieFrameNumber")
                series = [{"name": "B-factor", "x": [int(f) for f in frames],
                           "y": [round(float(v), 1) for v in table.floats("rlnBfactorUsedForSharpening")]}]
                ctx.add_plot("Frame weighting: B-factor per frame", series, x_label="Frame", y_label="B-factor (Å²)")
                if table.has("rlnFittedInterceptGuinierPlot"):
                    ctx.add_plot("Frame weighting: scale per frame",
                                 [{"name": "Intercept", "x": [int(f) for f in frames],
                                   "y": [round(float(v), 3) for v in table.floats("rlnFittedInterceptGuinierPlot")]}],
                                 x_label="Frame", y_label="Guinier intercept")
            except (StopIteration, KeyError, ValueError):
                pass
        meta = particles_out_meta(ctx, star, "polish")
        ctx.add_highlight("Particles", f"{meta['n_particles']:,}")
        ctx.add_output("particles", "particles", star, f"Polished particles ({meta['n_particles']:,})", meta=meta)


# ------------------------------------------------------------------ local resolution
@register
class RelionLocalRes(RelionJob):
    name = "relion_locres"
    title = "Local resolution (RELION)"
    category = "Validation"
    description = ("Local resolution with relion_postprocess --locres: FSC in a soft sphere moved over the map. Gives "
                   "the local resolution map (to colour the map in the 3D viewer) and the map locally filtered to it.")
    gpu_param = ""
    cpu_param = "mpi"
    inputs = [Slot("half_maps", ("half_maps",), "Half maps"),
              Slot("mask", ("mask",), "Mask", required=False, help="Statistics within the mask.")]
    params = [
        Param("bfactor", "float", -100.0, label="B-factor", unit="Å²",
              help="Sharpening of the locally filtered map (the post-processing B-factor is a good value)."),
        Param("mtf", "path", "", label="Detector MTF", advanced=True),
        mpi(1),
        extra_args_param(),
    ]
    outputs = [OutputDef("locres", "locres", "Local resolution map"), OutputDef("map", "map", "Locally filtered map")]

    def run(self, ctx: JobContext) -> None:
        p = ctx.params
        halves = ctx.require("half_maps")
        if len(halves.files) < 2:
            raise JobError(f"{halves.source} is not a pair of half maps")
        apix = MapVolume.read(halves.files[0]).pixel_size
        args = [*relion_command(ctx, "relion_postprocess", p["mpi"]), "--locres", "--i", ctx.rel(halves.files[0]),
                "--i2", ctx.rel(halves.files[1]), "--o", ctx.rel(ctx.path("relion")), "--angpix", f"{apix:g}",
                "--adhoc_bfac", f"{p['bfactor']:g}"]
        if str(p["mtf"]).strip():
            args += ["--mtf", str(p["mtf"]).strip()]
        mask = ctx.input("mask")
        if mask:
            args += ["--mask", ctx.rel(mask.path)]
        args += ctx.split_extra()
        ctx.progress(0.1, "relion_postprocess --locres")
        run_relion(ctx, args)
        locres, filt = ctx.path("relion_locres.mrc"), ctx.path("relion_locres_filtered.mrc")
        if not locres.exists():
            raise JobError("relion_postprocess wrote no relion_locres.mrc (see the log)")
        data = MapVolume.read(locres).data
        inside = data > 0
        if mask:
            mvol = MapVolume.read(mask.path)
            if mvol.data.shape == data.shape:
                inside &= mvol.data > 0.5
        values = data[inside]
        values = values[(values > 0) & (values < 100)]
        meta = {"resolution": round(float(np.median(values)), 2)} if len(values) else {}
        if len(values):
            ctx.add_plot("Local resolution", [{"name": "Voxels", **histogram(values, 40)}], x_label="Resolution (Å)",
                         y_label="Voxels", style="bars")
            ctx.add_highlight("Local resolution", f"{np.percentile(values, 5):.1f}–{np.percentile(values, 95):.1f} Å")
        ctx.add_output("locres", "locres", locres, "Local resolution (RELION)", meta=meta)
        if filt.exists():
            ctx.add_output("map", "map", filt, "Locally filtered map", inherit=("half_maps",))


# ------------------------------------------------------------------ join and subsets
@register
class RelionJoin(JobType):
    name = "relion_join"
    title = "Join star files (RELION)"
    category = "Utilities"
    tool = "relion"
    software = ["RELION"]
    description = ("Merges two to four particle, micrograph or movie sets into one (relion_star_handler --combine), "
                   "removing particles present twice.")
    inputs = [Slot(f"set_{i}", ("particles", "micrographs", "movies"), f"Set {i}", required=i <= 2, group="sets")
              for i in range(1, 5)]
    params = [extra_args_param()]
    outputs = [OutputDef("particles", "particles", "Joined particles"), OutputDef("micrographs", "micrographs", "Joined micrographs"),
               OutputDef("movies", "movies", "Joined movies")]

    def run(self, ctx: JobContext) -> None:
        sets = [ctx.input(f"set_{i}") for i in range(1, 5)]
        sets = [s for s in sets if s]
        kinds = {s.type for s in sets}
        if len(kinds) != 1:
            raise JobError(f"Join sets of one kind (here {', '.join(sorted(kinds))})")
        kind = kinds.pop()
        refs = []
        for s in sets:
            if kind == "particles":
                refs.append(relion_particles(ctx, s)["ref"])
            else:
                refs.append(star_of(ctx, s)[1])
        label, name = {"particles": ("rlnImageName", "join_particles.star"), "micrographs": ("rlnMicrographName", "join_mics.star"),
                       "movies": ("rlnMicrographMovieName", "join_movies.star")}[kind]
        out = ctx.path(name)
        run_relion(ctx, [ctx.program("relion", "relion_star_handler"), "--combine", "--i", " ".join(refs),
                         "--check_duplicates", label, "--o", ctx.rel(out), *ctx.split_extra()])
        if not out.exists():
            raise JobError(f"relion_star_handler wrote no {name} (see the log)")
        n = rl.star_rows(out)
        ctx.add_table("Joined", ["Set", "Rows"], [[s.source, f"{rl.star_rows(Path(s.path)):,}"] for s in sets] + [["Result", f"{n:,}"]])
        ctx.add_highlight(kind.capitalize(), f"{n:,}")
        if kind == "particles":
            ctx.add_output("particles", "particles", out, f"{n:,} particles", meta=particles_out_meta(ctx, out, "join"))
        else:
            from cryoplug.jobs.relion_common import micrograph_meta
            ctx.add_output(kind, kind, out, f"{n:,} {kind}", meta=micrograph_meta(ctx, out, "join"))


@register
class RelionSubset(JobType):
    name = "relion_subset"
    title = "Subset selection (RELION)"
    category = "Utilities"
    tool = "relion"
    software = ["RELION"]
    description = ("relion_star_handler on a particle or micrograph set: remove duplicated particles (closer than a "
                   "distance after alignment: they inflate the FSC), split into subsets, keep the rows of a metadata "
                   "range, or discard images with outlying statistics.")
    inputs = [Slot("data", ("particles", "micrographs"), "Particles or micrographs")]
    params = [
        Param("operation", "choice", "duplicates", choices=["duplicates", "split", "value", "statistics"], label="Operation",
              labels={"duplicates": "Remove duplicates", "split": "Split into subsets", "value": "Select on a metadata value",
                      "statistics": "Discard on image statistics"}),
        Param("min_distance", "float", 30.0, label="Minimum inter-particle distance", unit="Å", min=0,
              help="Duplicates: particles closer than this after alignment are one."),
        Param("image_angpix", "float", -1.0, label="Micrograph pixel size", unit="Å", advanced=True,
              help="Duplicates: pixel size of the micrographs the particles were extracted from, when the particles "
                   "were re-scaled. -1 = read from the file."),
        Param("random_order", "bool", True, label="Random order", help="Split: shuffle first."),
        Param("subset_size", "int", 0, label="Subset size", min=0, help="Split: particles per subset (0 = from the number)."),
        Param("subsets", "int", 2, label="Number of subsets", min=0, max=100, help="Split: 0 = as many as the size needs."),
        Param("label", "str", "rlnCtfMaxResolution", label="Metadata label", help="Value: the column to select on."),
        Param("min_value", "float", -9999.0, label="Minimum value"),
        Param("max_value", "float", 9999.0, label="Maximum value"),
        Param("discard_label", "str", "rlnImageName", label="Image label", advanced=True),
        Param("discard_sigma", "float", 4.0, label="Sigma", min=1, max=20, advanced=True,
              help="Statistics: images whose mean or stddev is this many σ from the ensemble are discarded."),
    ]
    outputs = [OutputDef("particles", "particles", "Selected particles"),
               OutputDef("micrographs", "micrographs", "Selected micrographs"),
               OutputDef("subset_1", "particles", "Subset 1"), OutputDef("subset_2", "particles", "Subset 2")]

    @classmethod
    def validate(cls, params: dict[str, Any], connected: dict[str, str]) -> list[str]:
        if params.get("operation") == "split" and not int(params.get("subset_size") or 0) and not int(params.get("subsets") or 0):
            return ["Split: give the subset size or the number of subsets"]
        return []

    def run(self, ctx: JobContext) -> None:
        p = ctx.params
        inp = ctx.require("data")
        kind = inp.type
        if kind == "particles":
            src = relion_particles(ctx, inp)["ref"]
        else:
            src = star_of(ctx, inp)[1]
        out = ctx.path("particles.star" if kind == "particles" else "micrographs.star")
        args = [ctx.program("relion", "relion_star_handler"), "--i", src, "--o", ctx.rel(out)]
        op = p["operation"]
        if op == "duplicates":
            if kind != "particles":
                raise JobError("Duplicates can only be removed from particles")
            args += ["--remove_duplicates", f"{p['min_distance']:g}"]
            if p["image_angpix"] > 0:
                args += ["--image_angpix", f"{p['image_angpix']:g}"]
        elif op == "value":
            args += ["--select", str(p["label"]).strip(), "--minval", f"{p['min_value']:g}", "--maxval", f"{p['max_value']:g}"]
        elif op == "statistics":
            args += ["--discard_on_stats", "--discard_label", str(p["discard_label"]).strip(),
                     "--discard_sigma", f"{p['discard_sigma']:g}"]
        else:
            args.append("--split")
            if p["random_order"]:
                args.append("--random_order")
            if p["subsets"] > 0:
                args += ["--nr_split", str(p["subsets"])]
            if p["subset_size"] > 0:
                args += ["--size_split", str(p["subset_size"])]
        ctx.progress(0.2, f"relion_star_handler ({op})")
        run_relion(ctx, args)
        n_in = rl.star_rows(ctx.abs(src))
        if op == "split":
            parts = sorted(ctx.job_dir.glob(f"{out.stem}_split*.star"))
            if not parts:
                raise JobError("relion_star_handler wrote no split file (see the log)")
            rows = []
            for i, path in enumerate(parts, 1):
                n = rl.star_rows(path)
                rows.append([f"Subset {i}", f"{n:,}"])
                if kind == "particles":
                    ctx.add_output(f"subset_{i}", "particles", path, f"Subset {i} ({n:,})", meta=particles_out_meta(ctx, path, "split"))
                else:
                    from cryoplug.jobs.relion_common import micrograph_meta
                    ctx.add_output(f"subset_{i}", "micrographs", path, f"Subset {i} ({n:,})", meta=micrograph_meta(ctx, path, "split"))
            ctx.add_table("Subsets", ["Subset", "Rows"], rows)
            ctx.add_highlight("Subsets", str(len(parts)))
            return
        if not out.exists():
            raise JobError(f"relion_star_handler wrote no {out.name} (see the log)")
        n = rl.star_rows(out)
        ctx.add_table("Selection", ["", "Rows"], [["Input", f"{n_in:,}"], ["Kept", f"{n:,}"], ["Removed", f"{n_in - n:,}"]])
        ctx.add_highlight("Kept", f"{n:,} / {n_in:,}")
        if kind == "particles":
            ctx.add_output("particles", "particles", out, f"{n:,} particles", meta=particles_out_meta(ctx, out, op))
        else:
            from cryoplug.jobs.relion_common import micrograph_meta
            ctx.add_output("micrographs", "micrographs", out, f"{n:,} micrographs", meta=micrograph_meta(ctx, out, op))


# ------------------------------------------------------------------ multi-body refinement
@register
class RelionMultiBody(RelionJob):
    name = "relion_multibody"
    title = "3D multi-body refinement (RELION)"
    category = "Heterogeneity"
    description = ("Refines rigid bodies of a flexible complex separately, each with its own mask, starting from a "
                   "consensus auto-refinement (relion_refine --multibody_masks), then analyses their motions "
                   "(relion_flex_analyse): the main movements as series of maps to play in the 3D viewer.")
    inputs = [Slot("particles", ("particles",), "Refined particles", help="The particles of a RELION 3D auto-refine.")] + [
        Slot(f"body_{i}", ("mask",), f"Body {i} mask", required=i <= 2, group="bodies",
             help="Soft mask of one rigid body, drawn on the consensus map (larger bodies first).")
        for i in range(1, 7)]
    params = [
        Param("bodies", "text", "", label="Bodies", placeholder="2 10 2\n1 10 2",
              help="One line per body: the body it rotates relative to, the width of the angular prior (°) and of "
                   "the offset prior (Å). Empty: body 1 relative to 2, the others relative to 1, 10° and 2 Å."),
        Param("angular_step", "choice", "1.8°", choices=SAMPLINGS[:7], label="Initial angular sampling"),
        Param("offset_range", "float", 3.0, label="Initial offset range", unit="px", min=0, max=50),
        Param("offset_step", "float", 0.75, label="Initial offset step", unit="px", min=0.1, max=10),
        Param("subtracted_bodies", "bool", True, label="Reconstruct subtracted bodies"),
        Param("blush", "bool", False, label="Blush regularisation"),
        Param("analyse", "bool", True, label="Flexibility analysis"),
        Param("movies", "int", 3, label="Eigenvector movies", min=0, max=16,
              help="Series of 10 maps along this many principal motions (volume series in the viewer)."),
        Param("select_eigenvalue", "int", 0, label="Select on eigenvalue", min=0, max=20, advanced=True,
              help="Particles whose value along this motion is in the range below (0 = no selection)."),
        Param("eigen_min", "float", -999.0, label="Minimum eigenvalue", advanced=True),
        Param("eigen_max", "float", 999.0, label="Maximum eigenvalue", advanced=True),
        Param("pad1", "bool", False, label="Skip padding", advanced=True),
        *refine_compute_params(3),
        mpi(3, 3, help="At least 3 (the two half sets of the consensus refinement are refined separately)."),
        threads(4),
        gpus(1),
        extra_args_param(),
    ]
    outputs = [OutputDef("particles", "particles", "Particles"), OutputDef("body_1", "map", "Body 1"),
               OutputDef("motion_1", "volume_series", "Motion 1"), OutputDef("selected", "particles", "Selected particles")]
    intermediate = rl.ITERATION_FILES

    def run(self, ctx: JobContext) -> None:
        p = ctx.params
        pin = ctx.require("particles")
        parts = relion_particles(ctx, pin, need_poses=True)
        opt = (pin.meta.get("relion") or {}).get("optimiser")
        if not opt or not ctx.abs(opt).exists():
            raise JobError("Multi-body refinement continues a RELION 3D auto-refine: connect its refined particles "
                           "(their optimiser.star is needed)")
        opt = split_halves_optimiser(ctx, opt)
        masks = []
        for i in range(1, 7):
            inp = ctx.input(f"body_{i}")
            if inp:
                masks.append(map_on_grid(ctx, inp, parts, f"body_{i}_mask.mrc", mask=True)[0])
        if len(masks) < 2:
            raise JobError("Give at least two body masks")
        specs = [ln.split() for ln in str(p["bodies"]).splitlines() if ln.strip()]
        if specs and len(specs) != len(masks):
            raise JobError(f"{len(specs)} lines in 'Bodies' for {len(masks)} masks")
        if not specs:
            specs = [["2" if i == 0 else "1", "10", "2"] for i in range(len(masks))]
        table = rl.StarTable("")
        table.set_column("rlnBodyMaskName", [ctx.rel(m) for m in masks])
        table.set_column("rlnBodyRotateRelativeTo", [int(s[0]) for s in specs])
        table.set_column("rlnBodySigmaAngles", [float(s[1]) for s in specs])
        table.set_column("rlnBodySigmaOffset", [float(s[2]) for s in specs])
        bodies = rl.write_star(ctx.path("bodies.star"), [table], comment="Bodies of the multi-body refinement")
        args = [*relion_command(ctx, "relion_refine", p["mpi"]), "--continue", opt, "--o", ctx.rel(ctx.path("run")),
                "--solvent_correct_fsc", "--multibody_masks", ctx.rel(bodies), "--oversampling", "1",
                "--healpix_order", str(healpix(p["angular_step"])), "--auto_local_healpix_order", str(healpix(p["angular_step"])),
                "--offset_range", f"{p['offset_range']:g}", "--offset_step", f"{2 * p['offset_step']:g}"]
        if p["blush"]:
            args += ["--blush", "--blush_model", "v1.0"]
        if p["subtracted_bodies"]:
            args.append("--reconstruct_subtracted_bodies")
        compute_params(p, args)
        args += ["--pad", "1" if p["pad1"] else "2", "--j", str(p["threads"])]
        if p["gpus"] > 0:
            args += ["--gpu", ""]
        args += ctx.split_extra()
        progress = Progress(ctx, end=0.8)
        ctx.progress(0.03, f"relion_refine: {len(masks)} bodies")
        run_relion(ctx, args, on_line=progress)
        rows = []
        for i in range(1, len(masks) + 1):
            body = ctx.path(f"run_body{i:03d}.mrc")
            if body.exists():
                ctx.add_output(f"body_{i}", "map", body, f"Body {i}", meta={"relion_map": True, "body": i})
                rows.append([i, specs[i - 1][0], f"{float(specs[i - 1][1]):g}°", f"{float(specs[i - 1][2]):g} Å"])
        if not rows:
            raise JobError("relion_refine wrote no body map (run_body001.mrc; see the log)")
        ctx.add_table("Bodies", ["Body", "Rotates relative to", "Angular prior", "Offset prior"], rows)
        data = ctx.path("run_data.star")
        if data.exists():
            ctx.add_output("particles", "particles", data, "Particles (body poses)", meta=particles_out_meta(ctx, data, "multibody"))
        if p["analyse"] and data.exists():
            self.analyse(ctx, bodies, len(masks))

    @staticmethod
    def analyse(ctx: JobContext, bodies: Path, n_bodies: int) -> None:
        p = ctx.params
        args = [ctx.program("relion", "relion_flex_analyse"), "--PCA_orient", "--model", ctx.rel(ctx.path("run_model.star")),
                "--data", ctx.rel(ctx.path("run_data.star")), "--bodies", ctx.rel(bodies), "--o", ctx.rel(ctx.path("analyse"))]
        if p["movies"] > 0:
            args += ["--do_maps", "--k", str(p["movies"])]
        if p["select_eigenvalue"] > 0:
            args += ["--select_eigenvalue", str(p["select_eigenvalue"]), "--select_eigenvalue_min", f"{p['eigen_min']:g}",
                     "--select_eigenvalue_max", f"{p['eigen_max']:g}"]
        variance: list[tuple[int, float]] = []

        def on_line(line: str) -> None:
            m = re.search(r"Component\s+(\d+)\s+explains\s+([\d.]+)\s*%", line)
            if m:
                variance.append((int(m.group(1)), float(m.group(2))))
        ctx.progress(0.85, "relion_flex_analyse")
        run_relion(ctx, args, on_line=on_line)
        if variance:
            ctx.add_plot("Variance explained by each motion", [{"name": "Variance", "x": [v[0] for v in variance],
                                                                 "y": [v[1] for v in variance]}],
                         x_label="Principal component", y_label="Variance (%)", style="bars")
        for k in range(1, int(p["movies"]) + 1):
            frames = sorted(ctx.job_dir.glob(f"analyse_component{k:03d}_bin*.mrc"))
            if frames:
                share = next((v for c, v in variance if c == k), None)
                ctx.add_output(f"motion_{k}", "volume_series", frames,
                               f"Motion {k}" + (f" ({share:.0f} % of the variance)" if share else ""),
                               meta={"frames": len(frames), "relion_map": True})
        sel = sorted(ctx.job_dir.glob("analyse_eval*_select*.star"))
        if sel:
            n = rl.star_rows(sel[0])
            ctx.add_output("selected", "particles", sel[0], f"Selected particles ({n:,})", meta=particles_out_meta(ctx, sel[0], "multibody"))


def split_halves_optimiser(ctx: JobContext, opt: str) -> str:
    """The optimiser of the last iteration that kept the half sets apart: relion_refine continues it for multi-body
    refinement (the final run_optimiser.star of an auto-refine has them joined, which it refuses)."""
    path = ctx.abs(opt)
    general = rl.read_star(path).get("optimiser_general")
    if general is not None and general.has("rlnDoSplitRandomHalves") and general.value("rlnDoSplitRandomHalves") == "1":
        return opt
    root = re.sub(r"_optimiser\.star$", "", path.name)
    iterations = sorted(path.parent.glob(f"{root}_it[0-9][0-9][0-9]_optimiser.star"))
    if not iterations:
        raise JobError(f"No optimiser.star of an iteration with separate half sets next to {opt} (multi-body "
                       "refinement continues a gold-standard auto-refine)")
    ctx.log(f"Continuing {ctx.rel(iterations[-1])}: the last iteration with the two half sets apart")
    return ctx.rel(iterations[-1])


# ------------------------------------------------------------------ DynaMight
@register
class RelionDynamight(RelionJob):
    name = "relion_dynamight"
    title = "DynaMight flexibility (RELION)"
    category = "Heterogeneity"
    description = ("Learns the continuous motions of the particles as deformations of a Gaussian model of the "
                   "consensus map (DynaMight, RELION 5), then optionally the inverse deformations and a deformed "
                   "back-projection: a consensus map improved where the particle moves. Explore the latent space "
                   "afterwards with relion_python_dynamight explore-latent-space (an interactive window).")
    gpu_param = ""
    cpu_param = "threads"
    gpu = 1
    inputs = [Slot("particles", ("particles",), "Refined particles"),
              Slot("map", ("map",), "Consensus map", help="Same box and pixel size as the particles.", prefer=("map",))]
    params = [
        Param("gaussians", "int", 10000, label="Number of Gaussians", min=1000, max=100000,
              help="1-2 per residue of the structure."),
        Param("threshold", "float", 0.0, label="Initial map threshold",
              help="Where the Gaussians are placed in the map. 0 = estimated by DynaMight."),
        Param("regularization", "float", 1.0, label="Regularisation factor", min=0.1, max=10,
              help="Above 1: more weight on the restraints."),
        Param("steps", "choice", "all", choices=["deformations", "all"], label="Tasks",
              labels={"deformations": "Forward deformations only", "all": "Deformations, inverse deformations and deformed back-projection"}),
        Param("epochs", "int", 200, label="Inverse deformation epochs", min=10, max=2000),
        Param("batch", "int", 10, label="Back-projection batch size", min=1, max=500, advanced=True),
        Param("preload", "bool", False, label="Preload images in RAM"),
        Param("dynamight_exe", "str", "", label="DynaMight executable", advanced=True, admin_only=True,
              placeholder="relion_python_dynamight"),
        threads(8),
        extra_args_param(),
    ]
    outputs = [OutputDef("half_maps", "half_maps", "Deformed back-projection half maps"),
               OutputDef("map", "map", "Deformed back-projection")]

    def run(self, ctx: JobContext) -> None:
        p = ctx.params
        parts = relion_particles(ctx, ctx.require("particles"), need_poses=True)
        ref, _ = map_on_grid(ctx, ctx.require("map"), parts, "consensus.mrc")
        exe = str(p["dynamight_exe"]).strip() or ctx.program("relion", "relion_python_dynamight")
        out_dir = ctx.rel(ctx.job_dir) + "/"
        gpu = ["--gpu-id", "0"]
        pre = ["--preload-images"] if p["preload"] else []
        args = [exe, "optimize-deformations", "--refinement-star-file", parts["ref"], "--output-directory", out_dir,
                "--initial-model", ctx.rel(ref), "--n-gaussians", str(p["gaussians"])]
        if p["threshold"]:
            args += ["--initial-threshold", f"{p['threshold']:g}"]
        args += ["--regularization-factor", f"{p['regularization']:g}", "--n-threads", str(p["threads"]), *pre, *gpu,
                 *ctx.split_extra()]
        ctx.progress(0.05, "DynaMight: forward deformations")
        run_relion(ctx, args)
        if p["steps"] == "all":
            ctx.progress(0.5, "DynaMight: inverse deformations")
            run_relion(ctx, [exe, "optimize-inverse-deformations", out_dir, "--n-epochs", str(p["epochs"]), *pre, *gpu])
            ctx.progress(0.8, "DynaMight: deformed back-projection")
            run_relion(ctx, [exe, "deformable-backprojection", out_dir, "--batch-size", str(p["batch"]), *pre, *gpu])
            h1, h2 = ctx.path("backprojection", "map_half1.mrc"), ctx.path("backprojection", "map_half2.mrc")
            if not (h1.exists() and h2.exists()):
                raise JobError("DynaMight wrote no backprojection/map_half[12].mrc (see the log)")
            ctx.add_output("half_maps", "half_maps", [h1, h2], "Deformed back-projection (half maps)", inherit=("map",))
            avg = MapVolume.read(h1)
            avg = MapVolume(data=(avg.data + MapVolume.read(h2).data) / 2, voxel=avg.voxel, origin=avg.origin)
            ctx.add_output("map", "map", avg.write(ctx.path("backprojection_average.mrc")), "Deformed back-projection",
                           inherit=("map",))
        checkpoints = sorted(ctx.job_dir.glob("forward_deformations/checkpoints/*.pth"))
        ctx.add_text("Explore the latent space", "DynaMight's latent space explorer is an interactive window: on the "
                     "server, from the project folder, run\n" f"{Path(exe).name} explore-latent-space {out_dir} --half-set 1"
                     + (f"\n({len(checkpoints)} checkpoints in forward_deformations/checkpoints)" if checkpoints else ""),
                     mono=True)
