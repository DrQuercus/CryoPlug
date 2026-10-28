"""Real-space refinement jobs (Phenix, Servalcat)."""
from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path
from typing import Any

from cryoplug.jobs import register
from cryoplug.jobs.base import (
    JobContext,
    JobError,
    JobType,
    OutputDef,
    Param,
    Slot,
    extra_args_param,
    resolution_param,
    split_paths,
)
from cryoplug.jobs.building import pick_new_model, quick_cc, report_model
from cryoplug.parsers import highlights, metrics_table, parse_metrics

RSR_DEFAULT_RUN = ["minimization_global", "adp"]  # phenix.real_space_refine without run=


def rsr_strategy(run: str, nqh_flips: bool) -> str:
    """Value of run= for phenix.real_space_refine ("" keeps Phenix's default). N/Q/H flips are a step of the
    strategy, not a parameter of their own: 'nqh_flips=True' stops Phenix with an unknown-parameter error."""
    steps = [s for s in re.split(r"[+,\s]+", run.strip()) if s]
    if nqh_flips and "nqh_flips" not in steps:
        steps = (steps or RSR_DEFAULT_RUN) + ["nqh_flips"]
    return "+".join(steps)


@register
class PhenixRealSpaceRefine(JobType):
    name = "phenix_real_space_refine"
    title = "Real-space refinement (Phenix)"
    category = "Refinement"
    tool = "phenix"
    software = ["Phenix real_space_refine"]
    cpus = 4
    cpu_param = "nproc"
    description = ("phenix.real_space_refine: global minimisation with secondary-structure, Ramachandran and rotamer "
                   "restraints, morphing, simulated annealing and ADP refinement against the map.")
    inputs = [Slot("model", ("model",), "Model"), Slot("map", ("map",), "Map"),
              Slot("restraints", ("restraints",), "Ligand restraints", required=False,
                   help="CIF restraints from 'Ligand restraints (eLBOW)'.")]
    params = [
        resolution_param(),
        Param("macro_cycles", "int", 5, label="Macro cycles", min=1),
        Param("run", "str", "", label="Strategy (run=)", placeholder="minimization_global+local_grid_search+morphing+simulated_annealing+adp",
              help="Steps joined by '+'. Empty = Phenix default strategy (global minimisation and ADP refinement)."),
        Param("secondary_structure", "bool", True, label="Secondary-structure restraints"),
        Param("rama_restraints", "bool", False, label="Ramachandran restraints",
              help="Useful at low resolution; avoid if you report Ramachandran statistics as validation."),
        Param("nqh_flips", "bool", False, label="N/Q/H flips",
              help="Adds nqh_flips to the strategy: Asn, Gln and His side chains are flipped where that fits better "
                   "(with an empty strategy: minimization_global+adp+nqh_flips)."),
        Param("restraints", "str", "", label="Ligand restraint CIFs", path_kind="files",
              help="Restraint dictionaries for ligands (paths separated by ';')."),
        Param("nproc", "int", 4, label="Processors", min=1),
        extra_args_param(),
    ]
    outputs = [OutputDef("model", "model", "Refined model")]

    def run(self, ctx: JobContext) -> None:
        model, m = ctx.require("model"), ctx.require("map")
        res = ctx.resolution(slots=["map", "model"])
        restraints = split_paths(ctx.params["restraints"])
        if ctx.input("restraints"):
            restraints.append(ctx.input("restraints").path)
        args = [ctx.program("phenix", "phenix.real_space_refine"), model.path, m.path, *restraints,
                f"resolution={res}", f"macro_cycles={ctx.params['macro_cycles']}", f"nproc={ctx.params['nproc']}",
                f"secondary_structure.enabled={ctx.params['secondary_structure']}",
                f"ramachandran_plot_restraints.enabled={ctx.params['rama_restraints']}"]
        strategy = rsr_strategy(ctx.params.get("run") or "", ctx.params.get("nqh_flips"))
        if strategy:
            args.append(f"run={strategy}")
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
    description = ("servalcat refine_spa_norefmac: reciprocal-space refinement against unsharpened half maps with a "
                   "Gaussian likelihood target and Servalcat's own engine (no REFMAC needed), atomic B-factors, sharpened "
                   "Fo and Fo-Fc difference maps (Yamashita et al. 2021).")
    inputs = [Slot("model", ("model",), "Model"), Slot("half_maps", ("half_maps",), "Half maps", required=False),
              Slot("map", ("map",), "Map", required=False, help="Only if half maps are not available."),
              Slot("mask", ("mask",), "Mask for Fo-Fc", required=False),
              Slot("restraints", ("restraints",), "Ligand restraints", required=False)]
    params = [
        resolution_param(),
        Param("ncycle", "int", 10, label="Cycles", min=1),
        Param("pg", "str", "", label="Point group", placeholder="C1",
              help="Symmetry for NCS constraints (model must be the asymmetric unit). Empty = none."),
        Param("hydrogen", "choice", "all", choices=["all", "yes", "no"], label="Hydrogens"),
        Param("jellybody", "bool", False, label="Jelly-body restraints"),
        Param("weight", "float", 0.0, label="Weight", min=0.0, help="0 = automatic."),
        Param("ligand", "str", "", label="Ligand restraint CIFs", help="Paths separated by ';'.", path_kind="files"),
        extra_args_param(),
    ]
    outputs = [OutputDef("model", "model", "Refined model"), OutputDef("fofc", "map", "Fo-Fc map"),
               OutputDef("model_expanded", "model", "Refined model (symmetry expanded)")]

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
            from cryoplug.mrc import map_info
            ref_map = hm.files[0] if hm else ctx.require("map").path
            if map_info(mask.path)["box"] != map_info(ref_map)["box"]:  # Servalcat stops on it
                ctx.warn(f"The mask box {map_info(mask.path)['box']} differs from the map's {map_info(ref_map)['box']}: "
                         "the Fo-Fc map is computed without it")
                mask = None
        if mask:
            args += ["--mask_for_fofc", mask.path]
        if ctx.params.get("pg"):
            args += ["--pg", ctx.params["pg"]]
        if ctx.params["jellybody"]:
            args.append("--jellybody")
        if ctx.params["weight"] > 0:
            args += ["--weight", str(ctx.params["weight"])]
        ligands = split_paths(ctx.params["ligand"])
        if ctx.input("restraints"):
            ligands.append(ctx.input("restraints").path)
        if ligands:
            args += ["--ligand", *ligands]
        args += ctx.split_extra()
        start = time.time()
        ctx.run(args, tool="servalcat", stdout_path=ctx.path("servalcat.out"))
        out = pick_new_model(ctx, start, ["refined.mmcif", "refined.pdb", "refined*.mmcif"], exclude=("expanded", "traj"))
        report_model(ctx, out)
        values = parse_metrics(ctx.path("servalcat.out").read_text(errors="replace"))
        ctx.add_output("model", "model", out, "Servalcat refined model", inherit=["half_maps", "map"], meta={"metrics": values})
        expanded = next(iter(ctx.find_new_files(["refined_expanded.mmcif", "refined_expanded.pdb"], start)), None)
        if expanded is not None:  # with a point group: the whole complex, for validation and deposition checks
            ctx.add_output("model_expanded", "model", expanded, "Servalcat refined model (symmetry expanded)",
                           inherit=["half_maps", "map"])
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


# ---------------------------------------------------- enhanced: the map weight chosen by half-map cross-validation
def parse_weights(text: str) -> list[str | float]:
    """'auto, 1, 3, 10, 30' → ['auto', 1.0, 3.0, 10.0, 30.0] (each once, in that order)."""
    out: list[str | float] = []
    for item in re.split(r"[,;\s]+", str(text or "").strip()):
        if not item:
            continue
        if item.lower() == "auto":
            value: str | float = "auto"
        else:
            try:
                value = float(item)
            except ValueError:
                raise ValueError(f"Map weights: '{item}' is neither a number nor 'auto'") from None
            if value <= 0:
                raise ValueError("Map weights must be positive")
        if value not in out:
            out.append(value)
    if not out:
        raise ValueError("Give at least one map weight to try (e.g. auto, 1, 3, 10, 30)")
    return out


def fsc_average(frequency: list[float], curve: list[float], resolution: float) -> float:
    """Mean of an FSC curve from the first shell to the resolution (Phenix's FSC_average)."""
    kept = [c for f, c in zip(frequency, curve) if 0 < f <= 1.0 / resolution + 1e-9]
    if not kept:
        kept = list(curve[1:]) or list(curve)
    return float(sum(kept) / len(kept))


def choose_trial(trials: list[dict[str, Any]], max_clashscore: float) -> dict[str, Any]:
    """The trial with the best free FSC (then the smallest work − free gap) among those with an acceptable clashscore;
    all of them when none has."""
    usable = [t for t in trials if t.get("clashscore") is None or t["clashscore"] <= max_clashscore] or list(trials)
    return max(usable, key=lambda t: (round(t["fsc_free_avg"], 3), -round(t["gap"], 3)))


def weight_label(w: str | float) -> str:
    return w if w == "auto" else f"{w:g}"


@register
class PhenixRefineEnhanced(JobType):
    name = "phenix_refine_enhanced"
    title = "Refinement enhanced (Phenix, cross-validated weight)"
    category = "Refinement"
    tool = "phenix"
    software = ["Phenix real_space_refine"]
    cpus = 4
    cpu_param = "nproc"
    description = ("phenix.real_space_refine run against half map 1 with several map weights; every result is scored by "
                   "its FSC against half map 2 (free) and half map 1 (work): the weight giving the best free FSC without "
                   "over-fitting or broken geometry is kept, and the model is then refined against the full map with it "
                   "(the half-map cross-validation of Brown et al. 2015, Acta Cryst. D71, 136).")
    inputs = [Slot("model", ("model",), "Model"),
              Slot("half_maps", ("half_maps",), "Half maps",
                   help="Unfiltered half maps: the trials refine against the first one, the second one scores them."),
              Slot("map", ("map",), "Map", help="The map of the final refinement (sharpened or post-processed)."),
              Slot("restraints", ("restraints",), "Ligand restraints", required=False,
                   help="CIF restraints from 'Ligand restraints (eLBOW)'.")]
    params = [
        resolution_param(),
        Param("weights", "str", "auto, 1, 3, 10, 30", label="Map weights to try",
              help="Values of weight= for the trials, separated by commas; 'auto' is Phenix's own choice (no weight "
                   "given). A larger weight follows the map more closely and over-fits sooner."),
        Param("trial_cycles", "int", 3, label="Macro cycles per trial", min=1,
              help="Fewer than the final refinement: the trials only compare the weights."),
        Param("macro_cycles", "int", 5, label="Macro cycles (final refinement)", min=1),
        Param("max_clashscore", "float", 10.0, label="Clashscore allowed", min=0.0,
              help="A trial whose clashscore exceeds this is not chosen (unless every trial does)."),
        Param("max_gap", "float", 0.05, label="Over-fitting allowed", min=0.0, max=1.0,
              help="Work − free FSC average above which a trial is flagged as over-fitted (the model follows the noise "
                   "of half map 1)."),
        Param("run", "str", "", label="Strategy (run=)", placeholder="minimization_global+local_grid_search+morphing+simulated_annealing+adp",
              help="Steps joined by '+'. Empty = Phenix default strategy (global minimisation and ADP refinement)."),
        Param("secondary_structure", "bool", True, label="Secondary-structure restraints"),
        Param("rama_restraints", "bool", False, label="Ramachandran restraints"),
        Param("nqh_flips", "bool", False, label="N/Q/H flips"),
        Param("restraints", "str", "", label="Ligand restraint CIFs", path_kind="files",
              help="Restraint dictionaries for ligands (paths separated by ';')."),
        Param("nproc", "int", 4, label="Processors", min=1),
        Param("keep_trials", "bool", True, label="Keep the models of the trials", advanced=True),
        extra_args_param(),
    ]
    outputs = [OutputDef("model", "model", "Refined model"), OutputDef("report", "report", "Cross-validation report"),
               OutputDef("trials", "files", "Models of the trials")]

    @classmethod
    def validate(cls, params: dict[str, Any], connected: dict[str, str]) -> list[str]:
        try:
            parse_weights(params.get("weights", ""))
        except ValueError as exc:
            return [str(exc)]
        return []

    def run(self, ctx: JobContext) -> None:
        from cryoplug.jobs.decisions import DecisionMap
        from cryoplug.mrc import MapVolume

        model, hm, m = ctx.require("model"), ctx.require("half_maps"), ctx.require("map")
        res = ctx.resolution(slots=["map", "half_maps", "model"])
        weights = parse_weights(ctx.params["weights"])
        labels = [weight_label(w) for w in weights]
        half_a, half_b = MapVolume.read(hm.files[0]), MapVolume.read(hm.files[1])
        if half_a.data.shape != half_b.data.shape:
            raise JobError("The two half maps do not have the same box")
        restraints = split_paths(ctx.params["restraints"])
        if ctx.input("restraints"):
            restraints.append(ctx.input("restraints").path)
        exe = ctx.program("phenix", "phenix.real_space_refine")
        common = [f"resolution={res}", f"nproc={ctx.params['nproc']}",
                  f"secondary_structure.enabled={ctx.params['secondary_structure']}",
                  f"ramachandran_plot_restraints.enabled={ctx.params['rama_restraints']}"]
        strategy = rsr_strategy(ctx.params.get("run") or "", ctx.params.get("nqh_flips"))
        if strategy:
            common.append(f"run={strategy}")
        common += ctx.split_extra()
        max_gap, max_clash = float(ctx.params["max_gap"]), float(ctx.params["max_clashscore"])
        self.half = (half_a, half_b)
        self.res = res

        flow = DecisionMap(ctx, note="The weights tried against half map 1, each scored by the FSC of its model against half "
                                     "map 2 (free, never seen by the refinement) and half map 1 (work); the one kept, and "
                                     "the final refinement. Click a tile for the scores and the model in 3D.")
        flow.add("input", "input", "Model and half maps", icon="model",
                 lines=[Path(model.path).name, f"{res:g} Å · {Path(hm.files[0]).name}, {Path(hm.files[1]).name}"],
                 view=[{"kind": "map", "path": m.path, "label": "map"}, {"kind": "model", "path": model.path, "label": "starting model"}])
        flow.add("plan", "decision", "Weights to try", parents=["input"], icon="sliders",
                 lines=[", ".join(labels), f"{ctx.params['trial_cycles']} macro cycle(s) each, against half map 1"],
                 detail="A map weight is the data term of real-space refinement against the restraints. Refined against "
                        "half map 1 only, each trial is scored against half map 2: a weight that follows the noise of "
                        "half map 1 gives a work FSC far above the free FSC.")
        flow.add("group:trials", "group", f"{len(weights)} trial{'s' if len(weights) > 1 else ''} against half map 1",
                 parents=["plan"], icon="grid")
        trials: list[dict[str, Any]] = []
        steps = len(weights) + 1
        for i, (w, label) in enumerate(zip(weights, labels)):
            node = f"trial:{label}"
            flow.add(node, "run", f"weight {label}", group="group:trials", status="running", lines=["refining…"], icon="model",
                     detail=f"phenix.real_space_refine against half map 1, weight {label}, {ctx.params['trial_cycles']} macro cycle(s).")
            folder = ctx.path("trials", f"weight_{label}")
            folder.mkdir(parents=True, exist_ok=True)
            ctx.progress(i / steps, f"Trial {i + 1}/{len(weights)}: weight {label} against half map 1")
            args = [exe, model.path, hm.files[0], *restraints, *common, f"macro_cycles={ctx.params['trial_cycles']}"]
            if w != "auto":
                args.append(f"weight={w:g}")
            start = time.time()
            try:
                ctx.run(args, tool="phenix", cwd=folder, stdout_path=folder / "real_space_refine.out")
                out = pick_new_model(ctx, start, ["*_real_space_refined_*.cif", "*_real_space_refined_*.pdb", "*real_space_refined*"], root=folder)
            except JobError as exc:
                ctx.warn(f"Trial with weight {label} failed: {exc}")
                flow.update(node, status="failed", lines=["failed", str(exc).splitlines()[0][:80]])
                continue
            trial = self._score(ctx, label, w, out, folder)
            trials.append(trial)
            over = trial["gap"] > max_gap
            clash = trial.get("clashscore")
            flow.update(node, status="warning" if over else "done",
                        lines=[f"free FSC {trial['fsc_free_0.5']:.2f} Å · average {trial['fsc_free_avg']:.3f}",
                               f"gap {trial['gap']:+.3f}" + (" (over-fitted)" if over else "") + (f" · clashscore {clash:g}" if clash is not None else "")],
                        detail=self._summary(trial), view=[{"kind": "map", "path": hm.files[1], "label": "half map 2"},
                                                           {"kind": "model", "path": out, "label": f"weight {label}"}])
            ctx.log(f"Trial weight {label}: {self._summary(trial)}")
            self._live(ctx, trials, max_gap)
        if not trials:
            raise JobError("No trial produced a model (see the log)")

        best = choose_trial(trials, max_clash)
        flow.mark(f"trial:{best['label']}", [f"trial:{t['label']}" for t in trials])
        bad = [t for t in trials if t.get("clashscore") is not None and t["clashscore"] > max_clash]
        why = [f"best free FSC average ({best['fsc_free_avg']:.3f})"]
        if bad:
            why.append(f"{len(bad)} trial{'s' if len(bad) > 1 else ''} left out for a clashscore above {max_clash:g}")
        flow.add("best", "decision", f"Weight {best['label']} kept", parents=[f"trial:{best['label']}"], status="chosen", icon="target",
                 lines=[why[0], why[1] if len(why) > 1 else f"gap {best['gap']:+.3f}"],
                 detail="; ".join(why) + ".\n" + self._summary(best))
        ctx.log(f"Weight {best['label']} kept: " + "; ".join(why))

        ctx.progress((steps - 1) / steps, f"Final refinement against {Path(m.path).name} with weight {best['label']}")
        flow.add("final", "result", "Final refinement", parents=["best"], status="running", icon="target",
                 lines=[f"against {Path(m.path).name}", f"weight {best['label']} · {ctx.params['macro_cycles']} macro cycles"])
        args = [exe, model.path, m.path, *restraints, *common, f"macro_cycles={ctx.params['macro_cycles']}"]
        if best["weight"] != "auto":
            args.append(f"weight={best['weight']:g}")
        start = time.time()
        ctx.run(args, tool="phenix", stdout_path=ctx.path("real_space_refine.out"))
        out = pick_new_model(ctx, start, ["*_real_space_refined_*.cif", "*_real_space_refined_*.pdb", "*real_space_refined*"])
        report_model(ctx, out)
        values = parse_metrics(Path(ctx.path("real_space_refine.out")).read_text(errors="replace"))
        final = self._fsc_pair(out)
        ctx.add_output("model", "model", out, f"Refined model (weight {best['label']})", inherit=["map"],
                       meta={"metrics": values, "weight": best["label"], "fsc_free_0.5": final["free"]["resolution_0.5"],
                             "fsc_gap": final["gap"]})
        if values:
            ctx.add_metrics("Final statistics (Phenix)", metrics_table(values))
            highlights(ctx, values, limit=2)
        ctx.add_highlight("Weight", best["label"])
        over = final["gap"] > max_gap
        ctx.add_highlight("Free FSC (0.5)", f"{final['free']['resolution_0.5']:.2f} Å")
        ctx.add_highlight("Over-fitting", f"{final['gap']:+.3f}", "warn" if over else "good")
        ctx.add_plot("Map-model FSC of the final model against the half maps",
                     [{"name": "Work (half map 1)", "x": final["work"]["frequency"], "y": final["work"]["fsc"]},
                      {"name": "Free (half map 2)", "x": final["free"]["frequency"], "y": final["free"]["fsc"]}],
                     x_label="Resolution (Å)", y_label="FSC", x_kind="resolution", key="final_fsc",
                     hlines=[{"y": 0.5, "label": "0.5"}, {"y": 0.143, "label": "0.143"}], y_range=[-0.1, 1.05],
                     note="The final model was refined against the full map: both curves are then 'seen' maps, the gap "
                          "between them is informative only compared with the trials.")
        flow.update("final", status="chosen", lines=[f"free FSC {final['free']['resolution_0.5']:.2f} Å · gap {final['gap']:+.3f}",
                                                     f"weight {best['label']}" + (f" · MolProbity {values['molprobity_score']:g}" if "molprobity_score" in values else "")],
                    view=[{"kind": "map", "path": m.path, "label": "map"}, {"kind": "model", "path": out, "label": "refined model"}])
        report = {"resolution": res, "weights": labels, "chosen": best["label"], "max_clashscore": max_clash, "max_gap": max_gap,
                  "trials": [{k: v for k, v in t.items() if k not in ("work", "free")} for t in trials],
                  "final": {"fsc_work_0.5": final["work"]["resolution_0.5"], "fsc_free_0.5": final["free"]["resolution_0.5"],
                            "fsc_work_avg": final["work_avg"], "fsc_free_avg": final["free_avg"], "gap": final["gap"], "metrics": values}}
        path = ctx.path("cross_validation.json")
        path.write_text(json.dumps(report, indent=1, default=float))
        ctx.add_output("report", "report", path, "Cross-validation of the map weight",
                       meta={"weight": best["label"], "fsc_free_0.5": final["free"]["resolution_0.5"], "gap": final["gap"]})
        if ctx.params["keep_trials"]:
            ctx.add_output("trials", "files", [t["model"] for t in trials], "Models of the trials (against half map 1)")
        else:
            shutil.rmtree(ctx.path("trials"), ignore_errors=True)
        lines = [f"Weight {best['label']} kept: " + "; ".join(why) + ".",
                 f"Final refinement against {Path(m.path).name}: free FSC {final['free']['resolution_0.5']:.2f} Å at 0.5, "
                 f"work − free gap {final['gap']:+.3f}" + (" (over-fitting: lower the weights)" if over else "") + "."]
        ctx.add_text("Summary", "\n".join(lines))

    # ----------------------------------------------------------------- helpers
    def _fsc_pair(self, model_path: Path) -> dict[str, Any]:
        """Map-model FSC of a model against both half maps, with the averages to the resolution."""
        from cryoplug.analysis import map_model_fsc, model_envelope
        from cryoplug.modelio import atom_arrays, read_structure
        half_a, half_b = self.half
        atoms = atom_arrays(read_structure(model_path))
        if not len(atoms["xyz"]):
            raise JobError(f"{Path(model_path).name} has no atoms")
        env = model_envelope(half_a, atoms["xyz"])
        work = map_model_fsc(half_a, atoms["xyz"], atoms["z"], env)
        free = map_model_fsc(half_b, atoms["xyz"], atoms["z"], env)
        work_avg = fsc_average(work["frequency"], work["fsc"], self.res)
        free_avg = fsc_average(free["frequency"], free["fsc"], self.res)
        return {"work": work, "free": free, "work_avg": work_avg, "free_avg": free_avg, "gap": work_avg - free_avg}

    def _score(self, ctx: JobContext, label: str, weight: str | float, model_path: Path, folder: Path) -> dict[str, Any]:
        pair = self._fsc_pair(model_path)
        values = parse_metrics(Path(folder / "real_space_refine.out").read_text(errors="replace"))
        return {"label": label, "weight": weight, "model": str(model_path), "work": pair["work"], "free": pair["free"],
                "fsc_work_0.5": pair["work"]["resolution_0.5"], "fsc_free_0.5": pair["free"]["resolution_0.5"],
                "fsc_work_avg": pair["work_avg"], "fsc_free_avg": pair["free_avg"], "gap": pair["gap"],
                "clashscore": values.get("clashscore"), "molprobity": values.get("molprobity_score"),
                "rama_outliers": values.get("rama_outliers"), "cc_mask": values.get("cc_mask")}

    @staticmethod
    def _summary(t: dict[str, Any]) -> str:
        geo = ", ".join(f"{k} {v:g}" for k, v in (("clashscore", t.get("clashscore")), ("MolProbity", t.get("molprobity")),
                                                   ("Ramachandran outliers %", t.get("rama_outliers"))) if v is not None)
        return (f"free FSC {t['fsc_free_0.5']:.2f} Å at 0.5 (average {t['fsc_free_avg']:.3f}), work {t['fsc_work_0.5']:.2f} Å "
                f"(average {t['fsc_work_avg']:.3f}), gap {t['gap']:+.3f}" + (f"; {geo}" if geo else ""))

    def _live(self, ctx: JobContext, trials: list[dict[str, Any]], max_gap: float) -> None:
        """The trials so far: a table, the FSC curves of each (a slider), the averages by trial."""
        fmt = lambda v, spec="g": "—" if v is None else format(v, spec)  # noqa: E731
        rows = [[t["label"], f"{t['fsc_free_0.5']:.2f}", f"{t['fsc_free_avg']:.3f}", f"{t['fsc_work_avg']:.3f}",
                 f"{t['gap']:+.3f}" + (" ⚠" if t["gap"] > max_gap else ""), fmt(t.get("clashscore")), fmt(t.get("molprobity")),
                 fmt(t.get("rama_outliers")), fmt(t.get("cc_mask"), ".3f")] for t in trials]
        ctx.add_table("Trials compared", ["Weight", "Free FSC 0.5 (Å)", "Free average", "Work average", "Gap", "Clashscore",
                                          "MolProbity", "Rama outliers (%)", "CC (mask)"], rows, key="trials",
                      note="Each trial refined against half map 1. Free: FSC of its model against half map 2 (not seen); "
                           "work: against half map 1. Averages from the first shell to the resolution. Gap = work − free: "
                           f"above {max_gap:g}, the model follows the noise of half map 1.")
        t = trials[-1]
        ctx.add_plot_frame("Map-model FSC of each trial", [{"name": "Work (half map 1)", "x": t["work"]["frequency"], "y": t["work"]["fsc"]},
                                                           {"name": "Free (half map 2)", "x": t["free"]["frequency"], "y": t["free"]["fsc"]}],
                           f"weight {t['label']}", key="trial_fsc", x_label="Resolution (Å)", y_label="FSC", x_kind="resolution",
                           hlines=[{"y": 0.5, "label": "0.5"}, {"y": 0.143, "label": "0.143"}], y_range=[-0.1, 1.05], group="trials",
                           note="A work curve well above the free one: the weight over-fits half map 1.")
        if len(trials) > 1:
            x = list(range(1, len(trials) + 1))
            ctx.add_plot("Free and work FSC averages by trial",
                         [{"name": "Free (half map 2)", "x": x, "y": [round(t["fsc_free_avg"], 4) for t in trials]},
                          {"name": "Work (half map 1)", "x": x, "y": [round(t["fsc_work_avg"], 4) for t in trials]}],
                         x_label="Trial", y_label="FSC average", key="trials_plot",
                         note="Trials in the order tried: " + ", ".join(f"{i + 1} = weight {t['label']}" for i, t in enumerate(trials)) + ".")
