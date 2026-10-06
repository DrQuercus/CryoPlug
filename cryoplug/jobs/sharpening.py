"""Map enhancement jobs: density modification, global/local sharpening and deep-learning post-processing."""
from __future__ import annotations

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


def pick_new_map(ctx: JobContext, since: float, preferred: list[str], exclude: tuple[str, ...] = ()) -> Path:
    """Locate the map written by an external program in the job directory."""
    candidates = [p for p in ctx.find_new_files(MAP_PATTERNS, since)
                  if not p.is_symlink() and not p.name.startswith("thumb_") and not any(x in p.name for x in exclude)]
    for name in preferred:
        for p in candidates:
            if p.name == name or p.match(name):
                return p
    if candidates:
        return candidates[0]
    raise JobError("The program finished but no output map was found")


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
    description = "Global sharpening maximising map detail and connectivity (phenix.auto_sharpen)."
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
        args += ctx.split_extra()
        start = time.time()
        ctx.run(args, tool="phenix")
        out = pick_new_map(ctx, start, ["sharpened_map.ccp4", "*sharpened*"])
        ctx.add_output("map", "map", out, "Auto-sharpened map", inherit=["map"])


@register
class PhenixLocalAnisoSharpen(JobType):
    name = "phenix_local_aniso_sharpen"
    title = "Local anisotropic sharpening (Phenix)"
    category = "Map processing"
    tool = "phenix"
    software = ["Phenix local_aniso_sharpen"]
    cpus = 4
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
        args += [f"resolution={ctx.resolution()}", f"nproc={ctx.params['nproc']}", *ctx.split_extra()]
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

    def run(self, ctx: JobContext) -> None:
        mode = ctx.params["mode"]
        args: list[str] = [ctx.executable("locscale")]
        hm, full = ctx.input("half_maps"), ctx.input("map")
        if hm:
            args += ["-hm", hm.files[0], hm.files[1]]
        elif full:
            args += ["-em", full.path]
        else:
            raise JobError("No input map")
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
        sym = (ctx.params.get("symmetry") or ctx.inherited_meta("half_maps", "map").get("symmetry") or "C1").strip()
        if sym.upper() != "C1":
            args += ["-sym", sym]
        out_name = f"locscale_{mode}.mrc"
        args += ["-o", out_name, "-v", "-op", "processing_files"]
        if mode in ("model_free", "hybrid"):
            args += ["-gpus", *ctx.physical_gpus(ctx.params.get("gpu_ids", ""))]
        nproc = int(ctx.params["nproc"])
        if ctx.params["use_mpi"] and nproc > 1:
            args = ["mpirun", "-np", str(nproc), *args, "-mpi"]
        elif nproc > 1:
            args += ["-np", str(nproc)]
        args += ctx.split_extra()
        start = time.time()
        ctx.run(args, tool="locscale")
        out = ctx.path(out_name)
        if not out.exists():
            out = pick_new_map(ctx, start, ["locscale*.mrc"])
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
    outputs = [OutputDef("map", "map", "Feature-enhanced map")]

    @classmethod
    def validate(cls, params, connected):
        return _halves_or_map(params, connected)

    def run(self, ctx: JobContext) -> None:
        args: list[str] = [ctx.executable("locscale"), "feature_enhance"]
        hm, full = ctx.input("half_maps"), ctx.input("map")
        args += ["-hm", hm.files[0], hm.files[1]] if hm else ["-em", full.path]  # type: ignore[union-attr]
        mask = ctx.input("mask")
        if mask:
            args += ["-ma", mask.path]
        sym = (ctx.params.get("symmetry") or ctx.inherited_meta("half_maps", "map").get("symmetry") or "C1").strip()
        if sym.upper() != "C1":
            args += ["-sym", sym]
        if not ctx.params["monte_carlo"]:
            args.append("-no_mc")
        args += ["-bs", str(ctx.params["batch_size"]), "-gpus", *ctx.physical_gpus(ctx.params.get("gpu_ids", "")),
                 "-o", "feature_enhanced.mrc", "-v", "-op", "processing_files", *ctx.split_extra()]
        start = time.time()
        ctx.run(args, tool="locscale")
        out = ctx.path("feature_enhanced.mrc")
        if not out.exists():
            out = pick_new_map(ctx, start, ["feature_enhanced*.mrc"])
        ctx.add_output("map", "map", out, "EMmerNet feature-enhanced map", inherit=["half_maps", "map"])
        for extra in ctx.find_new_files(["*uncertainty*.mrc", "*pVDDT*.mrc", "*pvddt*.mrc", "*confidence*.mrc"], start):
            ctx.add_output("confidence", "map", extra, "EMmerNet confidence / uncertainty map", inherit=["half_maps", "map"])
            break


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
        ctx.add_output("map", "map", out, f"DeepEMhancer ({ctx.params['model']})", inherit=["half_maps", "map"])


# ------------------------------------------------------------------ EMReady
@register
class EMReady(JobType):
    name = "emready"
    title = "EMReady"
    category = "Map processing"
    tool = "emready"
    software = ["EMReady"]
    gpu = 1
    description = "Deep-learning map enhancement combining local and non-local features (He, Li & Huang 2023)."
    inputs = [Slot("map", ("map",), "Map"), Slot("mask", ("mask",), "Mask", required=False)]
    params = [
        Param("stride", "int", 16, label="Stride", min=6, max=64),
        Param("batch_size", "int", 16, label="Batch size", min=1, advanced=True),
        Param("gpu_ids", "str", "", label="GPU ids", advanced=True, help="Empty = lane GPU(s)."),
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
            args += ["-m", mask.path, "-c", "0.5"]
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
            raise JobError("spIsoNet did not write FSC3D.mrc")
        ctx.progress(0.2, "Training the anisotropy-correction network")
        ctx.run([exe, "reconstruct", h1, h2, "--aniso_file", fsc3d, "--mask", mask.path, "--limit_res", f"{res:.3f}",
                 "--epochs", str(ctx.params["epochs"]), "--alpha", str(ctx.params["alpha"]), "--beta", str(ctx.params["beta"]),
                 "--output_dir", "isonet_maps", "--gpuID", ",".join(ctx.physical_gpus(ctx.params.get("gpu_ids", ""))),
                 "--acc_batches", str(ctx.params["acc_batches"]), "--ncpus", str(ctx.params["ncpus"]), *ctx.split_extra()],
                tool="spisonet")
        c1 = first_existing([ctx.path("isonet_maps", "corrected_half_map_1.mrc")])
        c2 = first_existing([ctx.path("isonet_maps", "corrected_half_map_2.mrc")])
        if not (c1 and c2):
            raise JobError("Corrected half maps not found in isonet_maps/")
        a, b = MapVolume.read(c1), MapVolume.read(c2)
        avg = a.write(ctx.path("corrected_map.mrc"), (a.data + b.data) / 2)
        meta = {"anisotropy_corrected": True}
        ctx.add_output("half_maps", "half_maps", [c1, c2], "spIsoNet corrected half maps", inherit=["half_maps"], meta=meta)
        ctx.add_output("map", "map", avg, "spIsoNet corrected map", inherit=["half_maps"], meta=meta)
        ctx.add_output("fsc3d", "map", fsc3d, "3D FSC volume (before correction)", inherit=["half_maps"])
