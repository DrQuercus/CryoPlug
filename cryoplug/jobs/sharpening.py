"""Map enhancement jobs: density modification, global/local sharpening and deep-learning post-processing."""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

from cryoplug.jobs import register
from cryoplug.jobs.base import (
    JobContext,
    JobError,
    JobType,
    OutputDef,
    Param,
    Slot,
    extra_args_param,
    first_existing,
    resolution_param,
)

MAP_PATTERNS = ["*.ccp4", "*.mrc", "*.map"]
MAP_SUFFIXES = (".ccp4", ".mrc", ".map")


def pick_new_map(ctx: JobContext, since: float, preferred: list[str], exclude: tuple[str, ...] = ()) -> Path:
    """Locate the map written by an external program in the job directory (or next to its inputs)."""
    def candidates() -> list[Path]:  # ``exclude`` applies to the path inside the job folder (e.g. processing_files/)
        return [p for p in ctx.find_new_files(MAP_PATTERNS, since)
                if not p.is_symlink() and not p.name.startswith("thumb_") and not any(x in ctx.rel(p) for x in exclude)]
    found = candidates()
    if not found and ctx.rescue_stray_files(preferred, since, suffixes=MAP_SUFFIXES):
        found = candidates()
    for name in preferred:
        for p in found:
            if p.name == name or p.match(name):
                return p
    if found:
        return found[0]
    raise JobError(f"The program finished but wrote no map in {ctx.job_dir.name}/ (expected {', '.join(preferred)}). "
                   "It may have stopped on an error without returning a failure code: see the log.")


def replaced_in_phenix2(ctx: JobContext) -> list[str]:
    """Phenix 2 replaced auto_sharpen and local_aniso_sharpen with map_sharpening: they still run, with --force.
    (Without phenix.map_sharpening, an older Phenix that does not know --force.)"""
    return ["--force"] if ctx.has_program("phenix", "phenix.map_sharpening") else []


LOCSCALE_SYMMETRY = re.compile(r"[CD]\d+|T|O|I")  # the point groups LocScale's symmetry operators accept


def locscale_symmetry(ctx: JobContext) -> str:
    """Symmetry for LocScale / EMmerNet (C1 = none), checked: LocScale crashes on RELION-style names such as I2."""
    sym = (ctx.params.get("symmetry") or ctx.inherited_meta("half_maps", "map").get("symmetry") or "C1").strip().upper()
    if not LOCSCALE_SYMMETRY.fullmatch(sym):
        raise JobError(f"LocScale does not know the point group {sym}: it accepts Cn, Dn, T, O and I (not I1-I5)")
    return sym


def stage_maps(ctx: JobContext, hm, full, copy_first: bool = False) -> list[str]:
    """Input maps linked into the job folder, so that what LocScale writes next to its inputs lands here.
    ``copy_first``: the first map is copied instead (model-based LocScale rewrites it in place to reset its origin,
    which would change the original file through a link)."""
    import shutil
    sources = [(hm.files[0], "half_map_1.mrc"), (hm.files[1], "half_map_2.mrc")] if hm else [(full.path, "input_map.mrc")]
    out = []
    for i, (src, name) in enumerate(sources):
        if copy_first and i == 0:
            dst = ctx.path(name)
            if dst.exists() or dst.is_symlink():
                dst.unlink()
            shutil.copy2(src, dst)
            out.append(str(dst))
        else:
            out.append(str(ctx.link_input(src, name)))
    return out


def restore_origin(ctx: JobContext, out: Path, source: str) -> None:
    """Programs that write their map with origin 0 (LocScale, DeepEMhancer, spIsoNet): the origin of the input map put
    back, so that a re-boxed map stays on its models."""
    from cryoplug.mrc import MapVolume
    try:
        src = MapVolume.read(source, header_only=True)
        if not any(abs(o) > 1e-3 for o in src.origin):
            return
        vol = MapVolume.read(out)
        if vol.shape_xyz != src.shape_xyz or any(abs(o) > 1e-3 for o in vol.origin):
            return
        vol.origin = src.origin.copy()
        vol.write(out)
        ctx.log(f"Origin of the input map put back on {out.name} ({', '.join(f'{o:.2f}' for o in src.origin)} Å)")
    except Exception as exc:  # noqa: BLE001 - cosmetic
        ctx.warn(f"Origin of {out.name} not checked: {exc}")


def _halves_or_map(params, connected, need_any=("half_maps", "map")) -> list[str]:
    if not any(s in connected for s in need_any):
        return ["Connect half maps or a full map"]
    return []


# ------------------------------------------------------------------- Phenix
@register
class PhenixResolveCryoEM(JobType):
    name = "phenix_resolve_cryo_em"
    title = "Density modification (Phenix)"
    category = "Map processing"
    tool = "phenix"
    software = ["Phenix resolve_cryo_em"]
    cpus = 8
    cpu_param = "nproc"
    description = ("Maximum-likelihood density modification from half maps (phenix.resolve_cryo_em, Terwilliger et al. "
                   "2020). Typically improves interpretability and the half-map FSC. A sequence helps.")
    inputs = [Slot("half_maps", ("half_maps",), "Half maps"),
              Slot("sequence", ("sequence",), "Sequence", required=False)]
    params = [
        resolution_param(),
        Param("solvent_content", "float", 0.0, label="Solvent content", min=0.0, max=0.99,
              help="Fraction of the box that is solvent. Used when no sequence is given (0 = let Phenix estimate)."),
        Param("nproc", "int", 8, label="Processors", min=1),
        extra_args_param(),
    ]
    outputs = [OutputDef("map", "map", "Density-modified map")]

    def run(self, ctx: JobContext) -> None:
        hm = ctx.require("half_maps")
        res = ctx.resolution()
        args = [ctx.program("phenix", "phenix.resolve_cryo_em"), hm.files[0], hm.files[1],
                f"resolution={res}", f"nproc={ctx.params['nproc']}"]
        seq = ctx.input("sequence")
        if seq:
            args.append(f"seq_file={seq.files[0]}")
        elif ctx.params["solvent_content"] > 0:
            args.append(f"solvent_content={ctx.params['solvent_content']}")
        args += ctx.split_extra()
        start = time.time()
        ctx.run(args, tool="phenix")
        out = pick_new_map(ctx, start, ["denmod_map.ccp4", "*denmod*"])
        ctx.add_output("map", "map", out, "Density-modified map", inherit=["half_maps"])


@register
class PhenixAutoSharpen(JobType):
    name = "phenix_auto_sharpen"
    title = "Auto-sharpen (Phenix)"
    category = "Map processing"
    tool = "phenix"
    software = ["Phenix auto_sharpen"]
    description = ("Global sharpening maximising map detail and connectivity (phenix.auto_sharpen). With Phenix 2, "
                   "'Map sharpening (Phenix)' is the current tool (auto_sharpen still runs, with --force).")
    inputs = [Slot("map", ("map",), "Map", help="Unsharpened map.", prefer=("map",)),
              Slot("half_maps", ("half_maps",), "Half maps", required=False)]
    params = [resolution_param(), extra_args_param()]
    outputs = [OutputDef("map", "map", "Sharpened map")]

    def run(self, ctx: JobContext) -> None:
        m = ctx.require("map")
        args = [ctx.program("phenix", "phenix.auto_sharpen"), m.path, f"resolution={ctx.resolution()}"]
        hm = ctx.input("half_maps")
        if hm:
            args += [f"half_map_file={hm.files[0]}", f"half_map_file={hm.files[1]}"]
        args += replaced_in_phenix2(ctx) + ctx.split_extra()
        start = time.time()
        ctx.run(args, tool="phenix")
        out = pick_new_map(ctx, start, ["sharpened_map.ccp4", "*sharpened*"])
        ctx.add_output("map", "map", out, "Auto-sharpened map", inherit=["map"])


@register
class PhenixMapSharpening(JobType):
    name = "phenix_map_sharpening"
    title = "Map sharpening (Phenix)"
    category = "Map processing"
    tool = "phenix"
    software = ["Phenix map_sharpening"]
    description = ("Phenix's sharpening tool (phenix.map_sharpening, which replaces auto_sharpen and local_aniso_sharpen "
                   "in Phenix 2): shell scaling from two half maps (or a map and a fitted model), anisotropic and "
                   "optionally local; or B-factor sharpening of a single map.")
    inputs = [Slot("half_maps", ("half_maps",), "Half maps", required=False, help="Shell scaling (recommended)."),
              Slot("map", ("map",), "Map", required=False, prefer=("map",),
                   help="Unsharpened map: B-factor sharpening, or shell scaling with a model."),
              Slot("model", ("model",), "Model", required=False, help="Fitted in the map: model-based shell scaling."),
              Slot("sequence", ("sequence",), "Sequence", required=False, help="Lets Phenix estimate the molecular volume.")]
    params = [
        Param("method", "choice", "shell", choices=["shell", "b-factor"], label="Method",
              help="shell: resolution-dependent scaling from the half maps (or map + model); b-factor: one B-factor for a "
                   "single map."),
        resolution_param(help="Map resolution. 0 = from the inputs; needed for B-factor sharpening."),
        Param("anisotropic", "bool", True, label="Anisotropic correction", help="anisotropic_sharpen (shell scaling)."),
        Param("local", "bool", False, label="Local sharpening",
              help="local_sharpen: optimised in small boxes (slow on full-size maps)."),
        extra_args_param(),
    ]
    outputs = [OutputDef("map", "map", "Sharpened map")]

    @classmethod
    def validate(cls, params, connected):
        if params.get("method") == "b-factor":
            return [] if ("map" in connected or "half_maps" in connected) else ["B-factor sharpening needs a map"]
        if "half_maps" in connected or ("map" in connected and "model" in connected):
            return []
        return ["Shell scaling needs half maps, or a map and a fitted model"]

    def run(self, ctx: JobContext) -> None:
        if not ctx.has_program("phenix", "phenix.map_sharpening") and os.path.isabs(ctx.executable("phenix")):
            raise JobError("phenix.map_sharpening was not found next to the other Phenix programs (Phenix 1.20 or later "
                           "needed): use 'Auto-sharpen (Phenix)' with an older Phenix")
        p = ctx.params
        hm, m, model, seq = ctx.input("half_maps"), ctx.input("map"), ctx.input("model"), ctx.input("sequence")
        args = [ctx.program("phenix", "phenix.map_sharpening")]
        if p["method"] == "b-factor":
            args += [m.path if m else hm.files[0], "sharpening_method=b-factor"]  # type: ignore[union-attr]
            if not m:
                ctx.warn("B-factor sharpening of half map A: connect the full map to sharpen it instead")
        elif hm:
            args += [hm.files[0], hm.files[1]]
        else:
            args += [m.path, model.path]  # type: ignore[union-attr]
        try:
            args.append(f"resolution={ctx.resolution(slots=['half_maps', 'map', 'model']):.3f}")
        except JobError:
            if p["method"] == "b-factor":
                raise
        if seq:
            args.append(f"seq_file={seq.files[0]}")
        if p["method"] == "shell":
            if not p["anisotropic"]:
                args.append("anisotropic_sharpen=False")
            if p["local"]:
                args.append("local_sharpen=True")
        args += ctx.split_extra()
        start = time.time()
        ctx.run(args, tool="phenix")
        out = pick_new_map(ctx, start, ["sharpened_map.ccp4", "*sharpened*"])
        restore_origin(ctx, out, hm.files[0] if hm else m.path)  # type: ignore[union-attr]
        label = {"shell": "Shell-scaled map" + (" (local)" if p["local"] else ""), "b-factor": "B-factor sharpened map"}
        ctx.add_output("map", "map", out, label[p["method"]], inherit=["half_maps", "map"])


@register
class PhenixLocalAnisoSharpen(JobType):
    name = "phenix_local_aniso_sharpen"
    title = "Local anisotropic sharpening (Phenix)"
    category = "Map processing"
    tool = "phenix"
    software = ["Phenix local_aniso_sharpen"]
    cpus = 4
    cpu_param = "nproc"
    description = ("Local, anisotropic sharpening and resolution-dependent scaling from half maps "
                   "(phenix.local_aniso_sharpen), optionally guided by a model.")
    inputs = [Slot("half_maps", ("half_maps",), "Half maps"), Slot("model", ("model",), "Model", required=False)]
    params = [resolution_param(), Param("nproc", "int", 4, label="Processors", min=1), extra_args_param()]
    outputs = [OutputDef("map", "map", "Locally sharpened map")]

    def run(self, ctx: JobContext) -> None:
        hm = ctx.require("half_maps")
        args = [ctx.program("phenix", "phenix.local_aniso_sharpen"), hm.files[0], hm.files[1]]
        model = ctx.input("model")
        if model:
            args.append(model.path)
        args += [f"resolution={ctx.resolution()}", f"nproc={ctx.params['nproc']}", *replaced_in_phenix2(ctx),
                 *ctx.split_extra()]
        start = time.time()
        ctx.run(args, tool="phenix")
        out = pick_new_map(ctx, start, ["*aniso*sharpen*", "*sharpened*"])
        ctx.add_output("map", "map", out, "Local aniso-sharpened map", inherit=["half_maps"])


# ------------------------------------------------------------------ LocScale
@register
class LocScale(JobType):
    name = "locscale"
    title = "LocScale 2"
    category = "Map processing"
    tool = "locscale"
    software = ["LocScale 2"]
    gpu = 1
    cpus = 4
    cpu_param = "nproc"
    description = ("Local amplitude scaling. Model-free (EMmerNet reference, default), pseudo-atomic model, "
                   "model-based (requires a model; REFMAC/Servalcat used for B-factor refinement) or hybrid "
                   "(partial model completed with pseudo-atoms). Use unfiltered, unsharpened half maps.")
    inputs = [
        Slot("half_maps", ("half_maps",), "Half maps", required=False, help="Unfiltered half maps (preferred)."),
        Slot("map", ("map",), "Full map", required=False, help="Unsharpened full map, if no half maps.", prefer=("map",)),
        Slot("model", ("model",), "Model", required=False, help="Required for model-based and hybrid modes."),
        Slot("mask", ("mask",), "Mask", required=False),
    ]
    params = [
        Param("mode", "choice", "model_free", choices=["model_free", "model_free_pseudomodel", "model_based", "hybrid"],
              label="Mode"),
        resolution_param(),
        Param("symmetry", "str", "", label="Symmetry", placeholder="C1",
              help="Point group used to symmetrise the reference. Empty = from the imported maps."),
        Param("nproc", "int", 4, label="Processes", min=1),
        Param("use_mpi", "bool", False, label="Use MPI (mpirun)", advanced=True),
        Param("gpu_ids", "str", "", label="GPU ids", advanced=True,
              help="Physical GPU ids passed to -gpus. Empty = the GPU(s) assigned by the lane."),
        extra_args_param(),
    ]
    outputs = [OutputDef("map", "map", "LocScale map")]

    @classmethod
    def validate(cls, params, connected):
        problems = _halves_or_map(params, connected)
        if params.get("mode") in ("model_based", "hybrid") and "model" not in connected:
            problems.append(f"Mode '{params.get('mode')}' needs a model input")
        return problems

    @classmethod
    def resources(cls, params):
        out = super().resources(params)
        if params.get("mode", "model_free") != "model_free":  # only the model-free mode runs EMmerNet on a GPU
            out["num_gpus"] = 0
        return out

    def run(self, ctx: JobContext) -> None:
        mode = ctx.params["mode"]
        args: list[str] = [ctx.executable("locscale")]
        hm, full = ctx.input("half_maps"), ctx.input("map")
        if not hm and not full:
            raise JobError("No input map")
        maps = stage_maps(ctx, hm, full, copy_first=mode == "model_based")
        args += ["-hm", *maps] if hm else ["-em", maps[0]]
        if mode in ("model_based", "hybrid"):
            args += ["-mc", ctx.require("model").path]
            if mode == "hybrid":
                args.append("--complete_model")
        elif mode == "model_free_pseudomodel":
            args.append("--build_using_pseudomodel")
        mask = ctx.input("mask")
        if mask:
            args += ["-ma", mask.path]
        try:
            args += ["-res", f"{ctx.resolution():.3f}"]
        except JobError:
            if mode in ("model_based", "hybrid"):
                raise
        sym = locscale_symmetry(ctx)
        if sym != "C1" and mode == "model_free":
            # LocScale 2.4 symmetrises the EMmerNet variance map, which the model-free mode does not compute: it stops
            ctx.warn(f"Symmetry {sym} not applied: model-free LocScale 2.4 fails when symmetrising (no Monte-Carlo "
                     "variance map). Use the EMmerNet job (Monte-Carlo on) for a symmetrised map.")
        elif sym != "C1":
            args += ["-sym", sym]
        # Absolute paths: LocScale resolves relative ones against the folder of the input maps, which is
        # another job's folder (the map and the processing files would land there).
        out = ctx.path(f"locscale_{mode}.mrc")
        args += ["-o", str(out), "-v", "-op", str(ctx.path("processing_files"))]
        if mode == "model_free":
            args += ["-gpus", *ctx.physical_gpus(ctx.params.get("gpu_ids", ""))]
        nproc = int(ctx.params["nproc"])
        if ctx.params["use_mpi"] and nproc > 1:
            args = ["mpirun", "-np", str(nproc), *args, "-mpi"]
        elif nproc > 1:
            args += ["-np", str(nproc)]
        args += ctx.split_extra()
        start = time.time()
        ctx.run(args, tool="locscale")
        if not out.exists():
            out = pick_new_map(ctx, start, [out.name, "locscale*.mrc"], exclude=("processing_files", "half_map", "input_map"))
        restore_origin(ctx, out, (hm.files[0] if hm else full.path))
        ctx.add_output("map", "map", out, f"LocScale ({mode.replace('_', ' ')})", inherit=["half_maps", "map"])


@register
class EMmerNet(JobType):
    name = "emmernet"
    title = "EMmerNet feature enhancement"
    category = "Map processing"
    tool = "locscale"
    software = ["LocScale 2 (EMmerNet)"]
    gpu = 1
    description = ("Confidence-weighted, feature-enhanced map from EMmerNet (locscale feature_enhance) with Monte-Carlo "
                   "uncertainty estimation. Treat high-uncertainty regions with caution.")
    inputs = [Slot("half_maps", ("half_maps",), "Half maps", required=False),
              Slot("map", ("map",), "Full map", required=False, prefer=("map",)),
              Slot("mask", ("mask",), "Mask", required=False)]
    params = [
        Param("symmetry", "str", "", label="Symmetry", placeholder="C1"),
        Param("batch_size", "int", 8, label="Batch size", min=1, advanced=True),
        Param("monte_carlo", "bool", True, label="Monte-Carlo uncertainty"),
        Param("gpu_ids", "str", "", label="GPU ids", advanced=True, help="Empty = the GPU(s) assigned by the lane."),
        extra_args_param(),
    ]
    outputs = [OutputDef("map", "map", "Feature-enhanced map"), OutputDef("confidence", "map", "pVDDT confidence map"),
               OutputDef("map_locscale", "map", "LocScale map with the EMmerNet reference")]

    @classmethod
    def validate(cls, params, connected):
        return _halves_or_map(params, connected)

    def run(self, ctx: JobContext) -> None:
        args: list[str] = [ctx.executable("locscale"), "feature_enhance"]
        hm, full = ctx.input("half_maps"), ctx.input("map")
        # linked here: EMmerNet writes its LocScale map and the pVDDT map next to its input maps
        maps = stage_maps(ctx, hm, full)
        args += ["-hm", *maps] if hm else ["-em", maps[0]]
        mask = ctx.input("mask")
        if mask:
            args += ["-ma", mask.path]
        sym = locscale_symmetry(ctx)
        if sym != "C1":
            args += ["-sym", sym]
        if not ctx.params["monte_carlo"] and sym != "C1":
            ctx.warn(f"Monte-Carlo kept on: LocScale 2.4 cannot symmetrise ({sym}) without the Monte-Carlo variance map")
        elif not ctx.params["monte_carlo"]:
            args.append("-no_mc")
        # absolute paths, as for LocScale (relative ones are resolved against the input map's folder)
        out = ctx.path("feature_enhanced.mrc")
        args += ["-bs", str(ctx.params["batch_size"]), "-gpus", *ctx.physical_gpus(ctx.params.get("gpu_ids", "")),
                 "-o", str(out), "-v", "-op", str(ctx.path("processing_files")), *ctx.split_extra()]
        start = time.time()
        ctx.run(args, tool="locscale")
        if not out.exists():
            out = pick_new_map(ctx, start, [out.name, "feature_enhanced*.mrc"],
                               exclude=("_variance", "_baseline", "pVDDT", "processing_files", "half_map", "input_map"))
        source = hm.files[0] if hm else full.path
        restore_origin(ctx, out, source)
        ctx.add_output("map", "map", out, "EMmerNet feature-enhanced map", inherit=["half_maps", "map"])
        new = [p for p in ctx.find_new_files(["*.mrc"], start) if "processing_files" not in ctx.rel(p) and p != out and not p.is_symlink()]
        pvddt = next((p for p in new if "pvddt" in p.name.lower()), None)
        variance = next((p for p in new if "_variance" in p.name.lower()), None)
        if pvddt:  # -100 (unreliable) to 100: the authors ask to read the enhanced map with it
            ctx.add_output("confidence", "map", pvddt, "pVDDT confidence map (-100 to 100)", inherit=["half_maps", "map"],
                           meta={"display_range": [-100, 100], "kind": "pVDDT"})
        elif variance:
            ctx.add_output("confidence", "map", variance, "EMmerNet uncertainty (variance) map", inherit=["half_maps", "map"])
        final = next((p for p in new if p not in (pvddt, variance) and ("baseline" in p.name.lower() or "locscale" in p.name.lower())), None)
        if final:
            restore_origin(ctx, final, source)
            ctx.add_output("map_locscale", "map", final, "LocScale map with the EMmerNet reference", inherit=["half_maps", "map"])


# -------------------------------------------------------------- DeepEMhancer
@register
class DeepEMhancer(JobType):
    name = "deepemhancer"
    title = "DeepEMhancer"
    category = "Map processing"
    tool = "deepemhancer"
    software = ["DeepEMhancer"]
    gpu = 1
    description = "Deep-learning post-processing producing a masked, locally sharpened-like map (Sanchez-Garcia et al. 2021)."
    inputs = [Slot("half_maps", ("half_maps",), "Half maps", required=False),
              Slot("map", ("map",), "Full map", required=False, prefer=("map",))]
    params = [
        Param("model", "choice", "tightTarget", choices=["tightTarget", "wideTarget", "highRes"], label="Network",
              help="tightTarget (default), wideTarget (keeps more surrounding density), highRes (maps better than 4 Å)."),
        Param("batch_size", "int", 8, label="Batch size", min=1, advanced=True),
        Param("gpu_ids", "str", "", label="GPU ids", advanced=True, help="Empty = lane GPU(s); -1 = CPU."),
        Param("models_dir", "path", "", label="Models directory", advanced=True, path_kind="dir",
              help="--deepLearningModelPath, if the networks are not in the default location."),
        extra_args_param(),
    ]
    outputs = [OutputDef("map", "map", "DeepEMhancer map")]

    @classmethod
    def validate(cls, params, connected):
        return _halves_or_map(params, connected)

    def run(self, ctx: JobContext) -> None:
        hm, full = ctx.input("half_maps"), ctx.input("map")
        out = ctx.path(f"deepemhancer_{ctx.params['model']}.mrc")
        args = [ctx.executable("deepemhancer")]
        args += ["-i", hm.files[0], "-i2", hm.files[1]] if hm else ["-i", full.path]  # type: ignore[union-attr]
        args += ["-o", str(out), "-p", ctx.params["model"], "-b", str(ctx.params["batch_size"]),
                 "-g", ",".join(ctx.physical_gpus(ctx.params.get("gpu_ids", "")))]
        if ctx.params.get("models_dir"):
            args += ["--deepLearningModelPath", ctx.params["models_dir"]]
        args += ctx.split_extra()
        ctx.run(args, tool="deepemhancer")
        restore_origin(ctx, out, hm.files[0] if hm else full.path)  # type: ignore[union-attr]
        ctx.add_output("map", "map", out, f"DeepEMhancer ({ctx.params['model']})", inherit=["half_maps", "map"])


# ------------------------------------------------------------------ EMReady
@register
class EMReady(JobType):
    name = "emready"
    title = "EMReady2"
    category = "Map processing"
    tool = "emready"
    software = ["EMReady2"]
    gpu = 1
    description = ("Deep-learning map enhancement aware of the local quality of the map (EMReady2, Cao et al. 2026; "
                   "EMReady 1 is run the same way). A mask restricts the result to the region of interest.")
    inputs = [Slot("map", ("map",), "Map"), Slot("mask", ("mask",), "Mask", required=False)]
    params = [
        Param("stride", "int", 16, label="Stride", min=6, max=64),
        Param("batch_size", "int", 16, label="Batch size", min=1, advanced=True),
        Param("gpu_ids", "str", "", label="GPU ids", advanced=True, help="Empty = lane GPU(s)."),
        Param("mask_contour", "float", 0.5, label="Mask contour", min=0.0, max=1.0, advanced=True,
              help="Voxels of the mask above this value are kept (-c); EMReady's own default, 0, keeps the whole box."),
        extra_args_param(),
    ]
    outputs = [OutputDef("map", "map", "EMReady map")]

    def run(self, ctx: JobContext) -> None:
        m = ctx.require("map")
        out = ctx.path("emready.mrc")
        args = [ctx.executable("emready"), m.path, str(out), "-g", ",".join(ctx.physical_gpus(ctx.params.get("gpu_ids", ""))),
                "-s", str(ctx.params["stride"]), "-b", str(ctx.params["batch_size"])]
        mask = ctx.input("mask")
        if mask:
            args += ["-m", mask.path, "-c", f"{ctx.params['mask_contour']:g}"]
        args += ctx.split_extra()
        ctx.run(args, tool="emready")
        ctx.add_output("map", "map", out, "EMReady map", inherit=["map"])


# ------------------------------------------------------------------ spIsoNet
@register
class SpIsoNet(JobType):
    name = "spisonet"
    title = "Anisotropy correction (spIsoNet)"
    category = "Map processing"
    tool = "spisonet"
    software = ["spIsoNet"]
    gpu = 1
    cpus = 8
    description = ("Self-supervised correction of the resolution anisotropy caused by preferred orientation (spIsoNet, "
                   "Liu et al. 2025): computes the 3D FSC, then trains a network on the half maps to restore the poorly "
                   "sampled directions. The corrected half maps are no longer independent: never use them for the "
                   "deposited FSC.")
    inputs = [Slot("half_maps", ("half_maps",), "Half maps", help="Unfiltered, unmasked half maps."),
              Slot("mask", ("mask",), "Mask", help="Soft mask around the particle (required by spIsoNet).")]
    params = [
        resolution_param(),
        Param("epochs", "int", 30, label="Training epochs", min=1),
        Param("alpha", "float", 1.0, label="Alpha (missing-direction loss weight)", advanced=True),
        Param("beta", "float", 0.5, label="Beta (denoising loss weight)", advanced=True),
        Param("cone_sampling_angle", "float", 10.0, label="3D FSC cone sampling", unit="°", advanced=True),
        Param("acc_batches", "int", 2, label="Accumulated batches", min=1, advanced=True),
        Param("ncpus", "int", 8, label="CPUs", min=1),
        Param("gpu_ids", "str", "", label="GPU ids", advanced=True, help="Physical GPU ids (--gpuID). Empty = lane GPU(s)."),
        extra_args_param(),
    ]
    outputs = [OutputDef("half_maps", "half_maps", "Corrected half maps"), OutputDef("map", "map", "Corrected map"),
               OutputDef("fsc3d", "map", "3D FSC volume")]

    def run(self, ctx: JobContext) -> None:
        from cryoplug.mrc import MapVolume
        hm, mask = ctx.require("half_maps"), ctx.require("mask")
        res = ctx.resolution()
        h1 = ctx.link_input(hm.files[0], "half_map_1.mrc")
        h2 = ctx.link_input(hm.files[1], "half_map_2.mrc")
        exe = ctx.executable("spisonet")
        ctx.progress(0.05, "Computing the 3D FSC")
        ctx.run([exe, "fsc3d", h1, h2, mask.path, "--ncpus", str(ctx.params["ncpus"]), "--limit_res", f"{res:.3f}",
                 "--cone_sampling_angle", str(ctx.params["cone_sampling_angle"])], tool="spisonet")
        fsc3d = ctx.path("FSC3D.mrc")
        if not fsc3d.exists():
            raise JobError("spIsoNet did not write FSC3D.mrc (an option it does not know is only reported as 'not "
                           "recognized' in the log)")
        try:  # spIsoNet writes the 3D FSC without a pixel size: that of the half maps
            vol, ref = MapVolume.read(fsc3d), MapVolume.read(h1, header_only=True)
            if vol.shape_xyz == ref.shape_xyz:
                vol.voxel, vol.origin = ref.voxel.copy(), ref.origin.copy()
                vol.write(fsc3d)
        except Exception as exc:  # noqa: BLE001 - informative volume
            ctx.warn(f"Pixel size of FSC3D.mrc not set: {exc}")
        ctx.progress(0.2, "Training the anisotropy-correction network")
        ctx.run([exe, "reconstruct", h1, h2, "--aniso_file", fsc3d, "--mask", mask.path, "--limit_res", f"{res:.3f}",
                 "--epochs", str(ctx.params["epochs"]), "--alpha", str(ctx.params["alpha"]), "--beta", str(ctx.params["beta"]),
                 "--output_dir", str(ctx.path("isonet_maps")), "--gpuID", ",".join(ctx.physical_gpus(ctx.params.get("gpu_ids", ""))),
                 "--acc_batches", str(ctx.params["acc_batches"]), "--ncpus", str(ctx.params["ncpus"]), *ctx.split_extra()],
                tool="spisonet")
        c1 = first_existing([ctx.path("isonet_maps", "corrected_half_map_1.mrc")])
        c2 = first_existing([ctx.path("isonet_maps", "corrected_half_map_2.mrc")])
        if not (c1 and c2):
            raise JobError("Corrected half maps not found in isonet_maps/ (spIsoNet exits normally on an option it does "
                           "not know: look for 'not recognized' in the log)")
        for c in (c1, c2):
            restore_origin(ctx, c, hm.files[0])
        a, b = MapVolume.read(c1), MapVolume.read(c2)
        avg = a.write(ctx.path("corrected_map.mrc"), (a.data + b.data) / 2)
        meta = {"anisotropy_corrected": True}
        ctx.add_output("half_maps", "half_maps", [c1, c2], "spIsoNet corrected half maps", inherit=["half_maps"], meta=meta)
        ctx.add_output("map", "map", avg, "spIsoNet corrected map", inherit=["half_maps"], meta=meta)
        ctx.add_output("fsc3d", "map", fsc3d, "3D FSC volume (before correction)", inherit=["half_maps"])
