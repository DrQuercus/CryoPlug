"""Real-space refinement jobs (Phenix, Servalcat)."""
from __future__ import annotations

import time
from pathlib import Path

from cryoplug.jobs import register
from cryoplug.jobs.base import JobContext, JobType, OutputDef, Param, Slot, extra_args_param, resolution_param
from cryoplug.jobs.building import pick_new_model, quick_cc, report_model
from cryoplug.parsers import highlights, metrics_table, parse_metrics


def _restraint_files(value: str) -> list[str]:
    return [p.strip() for p in str(value or "").replace(",", ";").split(";") if p.strip()]


@register
class PhenixRealSpaceRefine(JobType):
    name = "phenix_real_space_refine"
    title = "Real-space refinement (Phenix)"
    category = "Refinement"
    tool = "phenix"
    software = ["Phenix real_space_refine"]
    cpus = 4
    description = ("phenix.real_space_refine: global minimisation with secondary-structure, Ramachandran and rotamer "
                   "restraints, morphing, simulated annealing and ADP refinement against the map.")
    inputs = [Slot("model", ("model",), "Model"), Slot("map", ("map",), "Map")]
    params = [
        resolution_param(),
        Param("macro_cycles", "int", 5, label="Macro cycles", min=1),
        Param("run", "str", "", label="Strategy (run=)", placeholder="minimization_global+local_grid_search+morphing+simulated_annealing+adp",
              help="Empty = Phenix default strategy."),
        Param("secondary_structure", "bool", True, label="Secondary-structure restraints"),
        Param("rama_restraints", "bool", False, label="Ramachandran restraints",
              help="Useful at low resolution; avoid if you report Ramachandran statistics as validation."),
        Param("nqh_flips", "bool", True, label="N/Q/H flips"),
        Param("restraints", "str", "", label="Ligand restraint CIFs", path_kind="file",
              help="Restraint dictionaries for ligands (paths separated by ';')."),
        Param("nproc", "int", 4, label="Processors", min=1),
        extra_args_param(),
    ]
    outputs = [OutputDef("model", "model", "Refined model")]

    def run(self, ctx: JobContext) -> None:
        model, m = ctx.require("model"), ctx.require("map")
        res = ctx.resolution(slots=["map", "model"])
        args = [ctx.program("phenix", "phenix.real_space_refine"), model.path, m.path, *_restraint_files(ctx.params["restraints"]),
                f"resolution={res}", f"macro_cycles={ctx.params['macro_cycles']}", f"nproc={ctx.params['nproc']}",
                f"secondary_structure.enabled={ctx.params['secondary_structure']}",
                f"ramachandran_plot_restraints.enabled={ctx.params['rama_restraints']}",
                f"nqh_flips={ctx.params['nqh_flips']}"]
        if ctx.params.get("run"):
            args.append(f"run={ctx.params['run']}")
        args += ctx.split_extra()
        start = time.time()
        ctx.run(args, tool="phenix", stdout_path=ctx.path("real_space_refine.out"))
        out = pick_new_model(ctx, start, ["*_real_space_refined_*.cif", "*_real_space_refined_*.pdb", "*real_space_refined*"])
        report_model(ctx, out)
        values = parse_metrics(Path(ctx.path("real_space_refine.out")).read_text(errors="replace"))
        ctx.add_output("model", "model", out, "Refined model", inherit=["map"], meta={"metrics": values})
        if values:
            ctx.add_metrics("Final statistics (Phenix)", metrics_table(values))
            highlights(ctx, values)
        else:
            quick_cc(ctx, m.path, out, res)


@register
class ServalcatRefine(JobType):
    name = "servalcat_refine"
    title = "Refinement (Servalcat)"
    category = "Refinement"
    tool = "servalcat"
    software = ["Servalcat"]
    cpus = 4
    description = ("servalcat refine_spa_norefmac: likelihood-free refinement against unsharpened half maps with "
                   "atomic B-factors, sharpened Fo and Fo-Fc difference maps (Yamashita et al. 2021).")
    inputs = [Slot("model", ("model",), "Model"), Slot("half_maps", ("half_maps",), "Half maps", required=False),
              Slot("map", ("map",), "Map", required=False, help="Only if half maps are not available."),
              Slot("mask", ("mask",), "Mask for Fo-Fc", required=False)]
    params = [
        resolution_param(),
        Param("ncycle", "int", 10, label="Cycles", min=1),
        Param("pg", "str", "", label="Point group", placeholder="C1",
              help="Symmetry for NCS constraints (model must be the asymmetric unit). Empty = none."),
        Param("hydrogen", "choice", "all", choices=["all", "yes", "no"], label="Hydrogens"),
        Param("jellybody", "bool", False, label="Jelly-body restraints"),
        Param("weight", "float", 0.0, label="Weight", min=0.0, help="0 = automatic."),
        Param("ligand", "str", "", label="Ligand restraint CIFs", help="Paths separated by ';'."),
        extra_args_param(),
    ]
    outputs = [OutputDef("model", "model", "Refined model"), OutputDef("fofc", "map", "Fo-Fc map")]

    @classmethod
    def validate(cls, params, connected):
        if "half_maps" not in connected and "map" not in connected:
            return ["Connect half maps (preferred) or a map"]
        return []

    def run(self, ctx: JobContext) -> None:
        model = ctx.require("model")
        res = ctx.resolution(slots=["half_maps", "map", "model"])
        args = [ctx.executable("servalcat"), "refine_spa_norefmac", "--model", model.path,
                "--resolution", f"{res:.3f}", "--ncycle", str(ctx.params["ncycle"]),
                "--hydrogen", ctx.params["hydrogen"], "-o", "refined"]
        hm = ctx.input("half_maps")
        if hm:
            args += ["--halfmaps", hm.files[0], hm.files[1]]
        else:
            args += ["--map", ctx.require("map").path]
        mask = ctx.input("mask")
        if mask:
            args += ["--mask_for_fofc", mask.path]
        if ctx.params.get("pg"):
            args += ["--pg", ctx.params["pg"]]
        if ctx.params["jellybody"]:
            args.append("--jellybody")
        if ctx.params["weight"] > 0:
            args += ["--weight", str(ctx.params["weight"])]
        ligands = _restraint_files(ctx.params["ligand"])
        if ligands:
            args += ["--ligand", *ligands]
        args += ctx.split_extra()
        start = time.time()
        ctx.run(args, tool="servalcat", stdout_path=ctx.path("servalcat.out"))
        out = pick_new_model(ctx, start, ["refined.mmcif", "refined.pdb", "refined*.mmcif"], exclude=("expanded", "traj"))
        report_model(ctx, out)
        values = parse_metrics(ctx.path("servalcat.out").read_text(errors="replace"))
        ctx.add_output("model", "model", out, "Servalcat refined model", inherit=["half_maps", "map"], meta={"metrics": values})
        fofc = ctx.find_new_files(["*normalized_fofc.mrc"], start)
        if fofc:
            ctx.add_output("fofc", "map", fofc[0], "Normalised Fo-Fc map", inherit=["half_maps", "map"])
        fo = ctx.find_new_files(["*normalized_fo.mrc"], start)
        if fo:
            ctx.add_output("fo", "map", fo[0], "Sharpened Fo map", inherit=["half_maps", "map"])
        if values:
            ctx.add_metrics("Statistics", metrics_table(values))
        if not hm:
            quick_cc(ctx, ctx.require("map").path, out, res)
