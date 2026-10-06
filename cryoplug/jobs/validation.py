"""Validation jobs: Phenix comprehensive validation, MolProbity, EMRinger and built-in map-model metrics."""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import numpy as np

from cryoplug.jobs import register
from cryoplug.jobs.base import JobContext, JobType, OutputDef, Param, Slot, extra_args_param, resolution_param
from cryoplug.parsers import highlights, metrics_table, parse_metrics


def _phenix_report(ctx: JobContext, out_file: Path, title: str, name: str) -> dict[str, float]:
    text = out_file.read_text(errors="replace") if out_file.exists() else ""
    values = parse_metrics(text)
    if values:
        ctx.add_metrics(title, metrics_table(values))
        highlights(ctx, values)
    else:
        ctx.warn("No metrics recognised in the program output (see log)")
    report = ctx.path(f"{name}.json")
    report.write_text(json.dumps({"program": name, "metrics": values}, indent=1))
    ctx.add_output("report", "report", [report, out_file], title, meta={"metrics": values})
    return values


@register
class PhenixValidationCryoEM(JobType):
    name = "phenix_validation_cryoem"
    title = "Comprehensive validation (Phenix)"
    category = "Validation"
    tool = "phenix"
    software = ["Phenix validation_cryoem", "MolProbity"]
    description = ("phenix.validation_cryoem: MolProbity geometry, model-map correlation (CC_mask, CC_box...), "
                   "map-model FSC and per-residue fit, as reported in wwPDB/EMDB validation.")
    inputs = [Slot("model", ("model",), "Model"), Slot("map", ("map",), "Map")]
    params = [resolution_param(), extra_args_param()]
    outputs = [OutputDef("report", "report", "Validation report")]

    def run(self, ctx: JobContext) -> None:
        model, m = ctx.require("model"), ctx.require("map")
        res = ctx.resolution(slots=["map", "model"])
        out = ctx.path("validation_cryoem.out")
        ctx.run([ctx.program("phenix", "phenix.validation_cryoem"), model.path, m.path, f"resolution={res}",
                 *ctx.split_extra()], tool="phenix", stdout_path=out)
        _phenix_report(ctx, out, "Phenix cryo-EM validation", "validation_cryoem")


@register
class PhenixMolProbity(JobType):
    name = "phenix_molprobity"
    title = "MolProbity geometry"
    category = "Validation"
    tool = "phenix"
    software = ["MolProbity"]
    description = "All-atom contacts, Ramachandran, rotamers and covalent geometry (phenix.molprobity)."
    inputs = [Slot("model", ("model",), "Model")]
    params = [extra_args_param()]
    outputs = [OutputDef("report", "report", "MolProbity report")]

    def run(self, ctx: JobContext) -> None:
        model = ctx.require("model")
        stdout = ctx.path("molprobity_stdout.out")
        ctx.run([ctx.program("phenix", "phenix.molprobity"), model.path, *ctx.split_extra()], tool="phenix",
                stdout_path=stdout)
        out = ctx.path("molprobity.out")
        _phenix_report(ctx, out if out.exists() else stdout, "MolProbity", "molprobity")
        coot_script = ctx.path("molprobity_coot.py")
        if coot_script.exists():
            ctx.log("Coot to-do list written: molprobity_coot.py (Calculate > Run Script in Coot)")


@register
class PhenixEMRinger(JobType):
    name = "phenix_emringer"
    title = "EMRinger"
    category = "Validation"
    tool = "phenix"
    software = ["EMRinger"]
    description = "Side-chain density-based validation of model-to-map fit (Barad et al. 2015). Meaningful below ~4.5 Å."
    inputs = [Slot("model", ("model",), "Model"), Slot("map", ("map",), "Map")]
    params = [extra_args_param()]
    outputs = [OutputDef("report", "report", "EMRinger report")]

    def run(self, ctx: JobContext) -> None:
        model, m = ctx.require("model"), ctx.require("map")
        out = ctx.path("emringer.out")
        ctx.run([ctx.program("phenix", "phenix.emringer"), model.path, m.path, *ctx.split_extra()], tool="phenix",
                stdout_path=out)
        _phenix_report(ctx, out, "EMRinger", "emringer")


# ------------------------------------------------------------- built-in
@register
class MapModelValidation(JobType):
    name = "mapmodel_validation"
    title = "Map-model validation (Q-score, FSC)"
    category = "Validation"
    software = ["CryoPlug"]
    cpus = 2
    description = ("Built-in map-model metrics, no external program needed: per-atom/residue Q-scores (after Pintilie "
                   "et al. 2020) compared with the value expected at this resolution, map-model FSC, CC_mask/CC_box "
                   "and atom inclusion at the suggested contour level.")
    inputs = [Slot("model", ("model",), "Model"), Slot("map", ("map",), "Map", help="Primary (deposited) map."),
              Slot("fsc", ("fsc",), "Half-map FSC", required=False, help="Overlay the half-map FSC on the map-model FSC.")]
    params = [
        resolution_param(),
        Param("contour", "float", 0.0, label="Contour level", help="Map threshold for atom inclusion. 0 = suggest from model mass."),
        Param("compute_q", "bool", True, label="Compute Q-scores"),
        Param("worst_residues", "int", 30, label="Worst residues listed", min=5, advanced=True),
    ]
    outputs = [OutputDef("report", "report", "Map-model validation")]

    def run(self, ctx: JobContext) -> None:
        from cryoplug.analysis import atom_inclusion, expected_q, map_model_fsc, model_envelope, per_residue, q_scores
        from cryoplug.modelio import atom_arrays, read_structure, structure_summary
        from cryoplug.mrc import MapVolume, correlation, model_map, suggest_contour

        model, m = ctx.require("model"), ctx.require("map")
        res = ctx.resolution(slots=["map", "model"])
        vol = MapVolume.read(m.path)
        st = read_structure(model.path)
        atoms = atom_arrays(st)
        xyz = atoms["xyz"]
        summary = structure_summary(st)
        report: dict = {"resolution": res, "map": m.source, "model": model.source}
        metrics = []

        inside = float(np.mean(vol.contains(xyz))) if len(xyz) else 0.0
        report["fraction_atoms_in_box"] = inside
        metrics.append({"label": "Atoms inside map box", "value": f"{100 * inside:.1f} %",
                        "status": "good" if inside > 0.999 else "warn" if inside > 0.95 else "bad", "target": "100 %"})

        ctx.progress(0.1, "Model map and correlations")
        env = model_envelope(vol, xyz)
        mm = model_map(xyz, atoms["z"], vol, res)
        cc_m = correlation(vol.data, mm, env)
        cc_b = correlation(vol.data, mm)
        report.update(cc_mask=cc_m, cc_box=cc_b)
        metrics.append({"label": "CC (mask)", "value": f"{cc_m:.3f}", "status": "good" if cc_m >= 0.7 else "warn" if cc_m >= 0.5 else "bad", "target": "≥ 0.7"})
        metrics.append({"label": "CC (box)", "value": f"{cc_b:.3f}"})
        ctx.add_highlight("CC (mask)", f"{cc_m:.3f}", metrics[-2]["status"])

        contour = float(ctx.params.get("contour") or 0)
        if contour == 0:
            contour = suggest_contour(vol, summary["mass_da"])
            report["contour_suggested"] = True
        incl = atom_inclusion(vol, xyz, contour)
        report.update(contour=contour, atom_inclusion=incl)
        metrics.append({"label": "Contour level" + (" (suggested from mass)" if report.get("contour_suggested") else ""),
                        "value": f"{contour:.4g}"})
        metrics.append({"label": "Atom inclusion at contour", "value": f"{100 * incl:.1f} %",
                        "status": "good" if incl >= 0.8 else "warn" if incl >= 0.6 else "bad", "target": "≥ 80 %"})

        ctx.progress(0.3, "Map-model FSC")
        mfsc = map_model_fsc(vol, xyz, atoms["z"], env)
        report["map_model_fsc"] = {k: mfsc[k] for k in ("resolution_0.5", "resolution_0.143")}
        report["map_model_fsc_0.5"] = mfsc["resolution_0.5"]
        metrics.append({"label": "Map-model FSC = 0.5", "value": f"{mfsc['resolution_0.5']:.2f} Å",
                        "status": "good" if mfsc["resolution_0.5"] <= res * 1.25 else "warn", "target": f"≈ {res:.2f} Å or better"})
        series = [{"name": "Map vs model", "x": mfsc["frequency"], "y": mfsc["fsc"]}]
        fsc_in = ctx.input("fsc")
        if fsc_in:
            try:
                data = json.loads(Path(fsc_in.files[0]).read_text())
                key = "corrected" if "corrected" in data["curves"] else "unmasked"
                series.append({"name": f"Half maps ({key})", "x": data["frequency"], "y": data["curves"][key]})
            except (OSError, ValueError, KeyError) as exc:
                ctx.warn(f"Could not read half-map FSC: {exc}")
        ctx.add_plot("Map-model FSC", series, x_label="Resolution (Å)", y_label="FSC", x_kind="resolution",
                     hlines=[{"y": 0.5, "label": "0.5"}, {"y": 0.143, "label": "0.143"}], y_range=[-0.1, 1.05])

        if ctx.params["compute_q"]:
            ctx.progress(0.5, f"Q-scores for {len(xyz)} atoms")
            q = q_scores(vol, xyz)
            q_mean = float(np.nanmean(q)) if np.any(~np.isnan(q)) else float("nan")
            q_exp = expected_q(res)
            residues = per_residue(atoms["keys"], q)
            report.update(q_mean=q_mean, q_expected=q_exp)
            status = "good" if q_mean >= q_exp - 0.05 else "warn" if q_mean >= q_exp - 0.15 else "bad"
            metrics.append({"label": "Mean Q-score", "value": f"{q_mean:.3f}", "status": status,
                            "target": f"expected ≈ {q_exp:.2f} at {res:.2f} Å"})
            ctx.add_highlight("Q-score", f"{q_mean:.3f}", status)
            chains: dict[str, list[float]] = {}
            for r in residues:
                if not math.isnan(r["value"]):
                    chains.setdefault(r["chain"], []).append(r["value"])
            ctx.add_table("Q-score per chain", ["Chain", "Residues", "Mean Q", "Q < 0.3 (%)"],
                          [[c, len(v), f"{np.mean(v):.3f}", f"{100 * np.mean(np.array(v) < 0.3):.1f}"] for c, v in chains.items()])
            worst = sorted((r for r in residues if not math.isnan(r["value"])), key=lambda r: r["value"])[: ctx.params["worst_residues"]]
            ctx.add_table("Lowest Q-score residues", ["Chain", "Residue", "Q"],
                          [[r["chain"], f"{r['resname']} {r['num']}{r['icode']}", f"{r['value']:.3f}"] for r in worst])
            plot_series = []
            for c in list(chains)[:12]:
                rs = [r for r in residues if r["chain"] == c and not math.isnan(r["value"])]
                plot_series.append({"name": f"Chain {c}", "x": [r["num"] for r in rs], "y": [round(r["value"], 3) for r in rs]})
            ctx.add_plot("Per-residue Q-score", plot_series, x_label="Residue number", y_label="Q-score",
                         hlines=[{"y": q_exp, "label": "expected"}], y_range=[-0.2, 1.0])
            qcsv = ctx.path("q_scores_per_residue.csv")
            with open(qcsv, "w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["chain", "resnum", "icode", "resname", "q"])
                for r in residues:
                    w.writerow([r["chain"], r["num"], r["icode"], r["resname"], f"{r['value']:.4f}"])

        ctx.add_metrics("Map-model fit", metrics)
        report["metrics"] = metrics
        out = ctx.path("mapmodel_validation.json")
        out.write_text(json.dumps(report, indent=1, default=float))
        files = [out] + ([ctx.path("q_scores_per_residue.csv")] if ctx.params["compute_q"] else [])
        ctx.add_output("report", "report", files, "Map-model validation",
                       meta={k: report[k] for k in ("cc_mask", "atom_inclusion", "contour", "q_mean", "q_expected", "map_model_fsc_0.5")
                             if k in report})
