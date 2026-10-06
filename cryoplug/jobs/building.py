"""Model building jobs: ModelAngelo, structure prediction, predicted-model processing and docking."""
from __future__ import annotations

import json
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

MODEL_PATTERNS = ["*.pdb", "*.cif", "*.mmcif"]


def pick_new_model(ctx: JobContext, since: float, preferred: list[str], root: Path | None = None,
                   exclude: tuple[str, ...] = ()) -> Path:
    candidates = [p for p in ctx.find_new_files(MODEL_PATTERNS, since, root)
                  if not p.is_symlink() and not any(x in p.name for x in exclude)]
    for pattern in preferred:
        for p in candidates:
            if p.match(pattern):
                return p
    if candidates:
        return candidates[0]
    raise JobError("The program finished but no output model was found")


def report_model(ctx: JobContext, path: Path, title: str = "Model content") -> dict:
    from cryoplug.jobs.imports import describe_model
    summary = describe_model(ctx, path)
    ctx.add_highlight("Residues", summary["n_residues"])
    return summary


def quick_cc(ctx: JobContext, map_path: str, model_path: Path, resolution: float | None) -> float | None:
    """Model-map correlation inside the model envelope (cheap built-in check)."""
    try:
        from cryoplug.analysis import cc_mask
        from cryoplug.modelio import read_structure
        from cryoplug.mrc import MapVolume
        if not resolution:
            return None
        cc = cc_mask(MapVolume.read(map_path), read_structure(model_path), resolution)
        ctx.add_highlight("CC (mask)", f"{cc:.3f}", "good" if cc >= 0.7 else "warn" if cc >= 0.5 else "bad")
        return cc
    except Exception as exc:  # pragma: no cover - informative only
        ctx.warn(f"Model-map correlation not computed: {exc}")
        return None


# ---------------------------------------------------------------- ModelAngelo
@register
class ModelAngeloBuild(JobType):
    name = "modelangelo_build"
    title = "ModelAngelo build"
    category = "Model building"
    tool = "modelangelo"
    software = ["ModelAngelo"]
    gpu = 1
    cpus = 4
    description = ("Automated model building (Jamali et al. 2024). With a sequence: 'build' (protein, RNA and DNA "
                   "FASTA are passed separately). Without: 'build_no_seq', which also writes HMM profiles to search "
                   "sequence databases for unknown proteins. Use the best sharpened / post-processed map.")
    inputs = [Slot("map", ("map",), "Map"), Slot("sequence", ("sequence",), "Sequence", required=False)]
    params = [
        Param("device", "str", "", label="CUDA device(s)", advanced=True,
              help="Value for --device (index inside the GPUs given to the job). Empty = automatic."),
        Param("legacy_fasta_flag", "bool", False, label="Old ModelAngelo (-f)", advanced=True,
              help="ModelAngelo < 1.0 used -f instead of -pf for the protein FASTA."),
        extra_args_param(),
    ]
    outputs = [OutputDef("model", "model", "ModelAngelo model")]

    def run(self, ctx: JobContext) -> None:
        m = ctx.require("map")
        seq = ctx.input("sequence")
        out_name = "modelangelo"
        exe = ctx.executable("modelangelo")
        if seq:
            args = [exe, "build", "-v", m.path, "-o", out_name]
            flags = {"protein": "-f" if ctx.params["legacy_fasta_flag"] else "-pf", "rna": "-rf", "dna": "-df"}
            used = False
            for kind, flag in flags.items():
                rel = seq.meta.get(f"{kind}_file")
                if rel:
                    args += [flag, str(ctx.abs(rel))]
                    used = True
            if not used:
                args += [flags["protein"], seq.files[0]]
        else:
            args = [exe, "build_no_seq", "-v", m.path, "-o", out_name]
        args += ["--device", ",".join(ctx.relative_gpus(ctx.params.get("device", "")))]
        args += ctx.split_extra()
        start = time.time()
        ctx.run(args, tool="modelangelo")
        out = first_existing([ctx.path(out_name, f"{out_name}.cif"), ctx.path(f"{out_name}.cif")])
        if out is None:
            out = pick_new_model(ctx, start, ["*.cif"], root=ctx.path(out_name), exclude=("raw",))
        report_model(ctx, out)
        ctx.add_output("model", "model", out, "ModelAngelo model", inherit=["map"])
        raw = first_existing([ctx.path(out_name, f"{out_name}_raw.cif")])
        if raw:
            ctx.add_output("model_raw", "model", raw, "ModelAngelo raw model (no pruning)", inherit=["map"])
        hmm = ctx.path(out_name, "hmm_profiles")
        if hmm.is_dir():
            ctx.log(f"HMM profiles for sequence search (e.g. hhblits/hmmsearch): {hmm}")
        quick_cc(ctx, m.path, out, m.meta.get("resolution"))


# ------------------------------------------------------------------ ColabFold
@register
class ColabFoldPredict(JobType):
    name = "colabfold_predict"
    title = "AlphaFold2 prediction (ColabFold)"
    category = "Model building"
    tool = "colabfold"
    software = ["ColabFold", "AlphaFold2"]
    gpu = 1
    description = ("Predict starting models with a local ColabFold installation. Records can be predicted separately "
                   "or as one complex (sequences joined with ':').")
    inputs = [Slot("sequence", ("sequence",), "Sequence")]
    params = [
        Param("as_complex", "bool", True, label="Predict as a complex"),
        Param("num_models", "int", 5, label="Models", min=1, max=5),
        Param("num_recycle", "int", 3, label="Recycles", min=0),
        Param("templates", "bool", False, label="Use templates"),
        Param("amber", "bool", False, label="Amber relaxation"),
        extra_args_param(),
    ]
    outputs = [OutputDef("model", "model", "Top-ranked model")]

    def run(self, ctx: JobContext) -> None:
        from cryoplug.modelio import classify_sequence, parse_fasta, write_fasta
        seq = ctx.require("sequence")
        records = [(n, s) for n, s in parse_fasta(Path(seq.files[0]).read_text()) if classify_sequence(s) == "protein"]
        if not records:
            raise JobError("No protein sequence to predict")
        if ctx.params["as_complex"] and len(records) > 1:
            records = [("complex", ":".join(s for _, s in records))]
        fasta = write_fasta([(n.split()[0].replace("|", "_"), s) for n, s in records], ctx.path("colabfold_input.fasta"))
        args = [ctx.executable("colabfold"), str(fasta), "colabfold_out", "--num-models", str(ctx.params["num_models"]),
                "--num-recycle", str(ctx.params["num_recycle"])]
        if ctx.params["templates"]:
            args.append("--templates")
        if ctx.params["amber"]:
            args += ["--amber", "--use-gpu-relax"]
        args += ctx.split_extra()
        ctx.run(args, tool="colabfold")
        out_dir = ctx.path("colabfold_out")
        n = 0
        for name, _ in records:
            safe = name.split()[0].replace("|", "_")
            hits = sorted(out_dir.glob(f"{safe}*rank_001*.pdb"))
            if ctx.params["amber"]:
                hits = [p for p in hits if "_relaxed_" in p.name] or hits
            if not hits:
                continue
            n += 1
            out_name = "model" if n == 1 else f"model_{n}"
            ctx.add_output(out_name, "model", hits[0], f"ColabFold {safe} (rank 1)", meta={"predicted": True})
        if n == 0:
            raise JobError("No ranked model found in colabfold_out")
        ctx.add_highlight("Models", n)


# ------------------------------------------------------------ Phenix helpers
@register
class PhenixProcessPredictedModel(JobType):
    name = "phenix_process_predicted_model"
    title = "Process predicted model (Phenix)"
    category = "Model building"
    tool = "phenix"
    software = ["Phenix process_predicted_model"]
    description = ("Trim low-confidence residues of an AlphaFold/ColabFold model, convert pLDDT to B-factors and "
                   "optionally split into compact domains for docking (phenix.process_predicted_model).")
    inputs = [Slot("model", ("model",), "Predicted model")]
    params = [
        Param("minimum_plddt", "float", 70.0, label="Minimum pLDDT", min=0, max=100),
        Param("b_value_field_is", "choice", "plddt", choices=["plddt", "rmsd"], label="B-factor column contains"),
        Param("split_by_domains", "bool", False, label="Split into compact domains"),
        Param("maximum_domains", "int", 3, label="Maximum domains", min=1),
        extra_args_param(),
    ]
    outputs = [OutputDef("model", "model", "Processed model")]

    def run(self, ctx: JobContext) -> None:
        model = ctx.require("model")
        plddt = float(ctx.params["minimum_plddt"])
        args = [ctx.program("phenix", "phenix.process_predicted_model"), model.path,
                f"b_value_field_is={ctx.params['b_value_field_is']}",
                f"minimum_plddt={plddt / 100.0 if plddt > 1 else plddt:.2f}",
                f"split_model_by_compact_regions={ctx.params['split_by_domains']}",
                f"maximum_domains={ctx.params['maximum_domains']}", *ctx.split_extra()]
        start = time.time()
        ctx.run(args, tool="phenix")
        out = pick_new_model(ctx, start, ["*_processed.pdb", "*_processed.cif", "*processed*"])
        report_model(ctx, out)
        ctx.add_output("model", "model", out, "Processed predicted model", inherit=["model"])
        for i, extra in enumerate(sorted(ctx.find_new_files(["*_processed_*.pdb"], start)), start=1):
            if extra != out:
                ctx.add_output(f"domain_{i}", "model", extra, f"Domain {i}", inherit=["model"])


@register
class PhenixDockInMap(JobType):
    name = "phenix_dock_in_map"
    title = "Dock in map (Phenix)"
    category = "Model building"
    tool = "phenix"
    software = ["Phenix dock_in_map"]
    cpus = 4
    description = "Global search for the position of a model (or domains) in the map (phenix.dock_in_map)."
    inputs = [Slot("model", ("model",), "Model"), Slot("map", ("map",), "Map")]
    params = [resolution_param(), Param("nproc", "int", 4, label="Processors", min=1), extra_args_param()]
    outputs = [OutputDef("model", "model", "Docked model")]

    def run(self, ctx: JobContext) -> None:
        model, m = ctx.require("model"), ctx.require("map")
        res = ctx.resolution(slots=["map", "model"])
        args = [ctx.program("phenix", "phenix.dock_in_map"), model.path, m.path, f"resolution={res}",
                f"nproc={ctx.params['nproc']}", *ctx.split_extra()]
        start = time.time()
        ctx.run(args, tool="phenix")
        out = pick_new_model(ctx, start, ["placed_model*", "*docked*", "*placed*"])
        ctx.add_output("model", "model", out, "Docked model", inherit=["map"])
        quick_cc(ctx, m.path, out, res)


# ------------------------------------------------------------------ ChimeraX
def fitmap_script(map_path: str, model_path: str, output: str, resolution: float, search: int,
                  placement: str, radius: float) -> str:
    return f"""
from chimerax.core.commands import run
MAP, MODEL, OUTPUT = {map_path!r}, {model_path!r}, {output!r}
mp = run(session, 'open "%s"' % MAP)[0]
mdl = run(session, 'open "%s"' % MODEL)[0]
cmd = 'fitmap #%s inMap #%s resolution {resolution} metric cor' % (mdl.id_string, mp.id_string)
if {search} > 0:
    cmd += ' search {search} placement {placement} radius {radius} clusterAngle 6 clusterShift 3 listFits false'
session.logger.info(cmd)
run(session, cmd)
run(session, 'save "%s" models #%s relModel #%s' % (OUTPUT, mdl.id_string, mp.id_string))
"""


@register
class ChimeraXFit(JobType):
    name = "chimerax_fitmap"
    title = "Rigid-body fit (ChimeraX)"
    category = "Model building"
    tool = "chimerax"
    software = ["UCSF ChimeraX"]
    description = ("Rigid-body fit of a model into the map with ChimeraX 'fitmap' using a simulated map at the map "
                   "resolution. Local optimisation from the current position, or a global search.")
    inputs = [Slot("model", ("model",), "Model"), Slot("map", ("map",), "Map")]
    params = [
        resolution_param(),
        Param("search", "int", 0, label="Global search placements", min=0,
              help="0 = local optimisation only; e.g. 200 for a global search."),
        Param("placement", "choice", "sr", choices=["sr", "s", "r"], label="Search moves",
              help="sr = shift and rotate, s = shift only, r = rotate only.", advanced=True),
        Param("radius", "float", 0.0, label="Search radius", unit="Å", min=0, advanced=True,
              help="Limit the global search to this distance from the starting position (0 = whole map)."),
    ]
    outputs = [OutputDef("model", "model", "Fitted model")]

    def run(self, ctx: JobContext) -> None:
        model, m = ctx.require("model"), ctx.require("map")
        res = ctx.resolution(slots=["map", "model"])
        out = ctx.path("fitted_model.cif")
        radius = ctx.params["radius"] or 1e6
        script = fitmap_script(m.path, model.path, str(out), res, int(ctx.params["search"]),
                               ctx.params["placement"], radius)
        ctx.path("fitmap.py").write_text(script)
        ctx.run([ctx.executable("chimerax"), "--nogui", "--exit", "--script", str(ctx.path("fitmap.py"))], tool="chimerax")
        if not out.exists():
            raise JobError("ChimeraX did not write the fitted model (see log)")
        ctx.add_output("model", "model", out, "Fitted model", inherit=["map"])
        cc = quick_cc(ctx, m.path, out, res)
        if cc is not None:
            ctx.add_metrics("Fit", [{"label": "Model-map CC (mask)", "value": f"{cc:.3f}"}])
        (ctx.path("fit_result.json")).write_text(json.dumps({"cc_mask": cc}))
