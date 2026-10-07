"""Validation jobs: Phenix comprehensive validation, MolProbity, EMRinger and built-in map-model metrics."""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import numpy as np

from cryoplug.jobs import register
from cryoplug.jobs.base import JobContext, JobError, JobType, OutputDef, Param, Slot, extra_args_param, resolution_param
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


# ------------------------------------------------------------ checkMySequence
def _seq_issues(data: dict, key: str) -> list[tuple[str, dict]]:
    block = data.get(key) or {}
    return [(kind, item) for kind in ("protein", "na") for item in (block.get(kind) or []) if isinstance(item, dict)]


def _span(item: dict, start: str = "resid_start_reference", end: str = "resid_end_reference") -> str:
    return f"{item.get(start, '?')}–{item.get(end, '?')}"


def report_sequence_check(ctx: JobContext, data: dict) -> int:
    """Tables and highlight from a checkMySequence JSON report; returns the number of issues."""
    shifts, tracing = _seq_issues(data, "register_shifts"), _seq_issues(data, "tracing_issues")
    mismatches, unknown = _seq_issues(data, "sequence_mismatches"), _seq_issues(data, "unidentified_chains")
    numbering = _seq_issues(data, "indexing_issues")
    n_issues = len(shifts) + len(tracing) + len(mismatches) + len(unknown) + len(numbering)
    ctx.add_metrics("Sequence assignment", [
        {"label": "Register shifts", "value": len(shifts), "status": "bad" if shifts else "good"},
        {"label": "Sequence mismatches", "value": len(mismatches), "status": "bad" if mismatches else "good"},
        {"label": "Tracing issues", "value": len(tracing), "status": "warn" if tracing else "good"},
        {"label": "Unidentified chains", "value": len(unknown), "status": "warn" if unknown else "good"},
        {"label": "Numbering issues", "value": len(numbering), "status": "warn" if numbering else "good"},
    ])
    if shifts:
        ctx.add_table("Possible register shifts (fix them in Coot or ISOLDE)",
                      ["Chain", "Model residues", "Shift", "Sequence fits residues", "−log10(p)"],
                      [[it.get("chain_id_reference", "?"), _span(it), f"{int(it.get('shift', 0)):+d}",
                        _span(it, "resid_start_new", "resid_end_new"), f"{float(it.get('mlogpv') or 0):.2f}"]
                       for _k, it in shifts])
        ctx.add_text("Shifted fragments (upper case: the fragment as modelled, then where its density fits best)",
                     "\n\n".join(f"Chain {it.get('chain_id_reference', '?')} {_span(it)}, shift {int(it.get('shift', 0)):+d}\n"
                                 f"  model     {it.get('model_seq', '')}\n  best fit  {it.get('new_seq', '')}" for _k, it in shifts),
                     mono=True)
    if tracing:
        ctx.add_table("Tracing issues (overlapping fragments disagree: check the chain path)",
                      ["Chain", "Model residues", "Shift", "−log10(p)"],
                      [[it.get("chain_id_reference", "?"), _span(it), f"{int(it.get('shift', 0)):+d}",
                        f"{float(it.get('mlogpv') or 0):.2f}"] for _k, it in tracing])
    if mismatches:
        ctx.add_table("Chains whose sequence differs from the reference", ["Chain", "Residues", "Identity", "E-value"],
                      [[it.get("chain_id_reference", "?"), _span(it), f"{float(it.get('seq2ref_si') or 0):.1f} %",
                        f"{float(it.get('evalue') or 0):.1e}"] for _k, it in mismatches])
        ctx.add_text("Alignments of the mismatching chains (model above, reference below)",
                     "\n\n".join(f"Chain {it.get('chain_id_reference', '?')}\n{it.get('alignment', '').rstrip()}" for _k, it in mismatches),
                     mono=True)
    if unknown:
        ctx.add_table("Chains matching no input sequence (missing sequence, or built outside the density)",
                      ["Chain", "Residues", "Type"],
                      [[it.get("chain_id_reference", "?"), _span(it), "protein" if k == "protein" else "nucleic acid"] for k, it in unknown])
    if numbering:
        ctx.add_table("Chain breaks without a gap in the residue numbering", ["Chain", "Residues", "Break at"],
                      [[it.get("chain_id_reference", "?"), _span(it), it.get("break_idx", "?")] for _k, it in numbering])
    raw = data.get("raw_results") or {}
    rows, series = [], []
    for kind in ("protein", "na"):
        for chain, frags in (raw.get(kind) or {}).items():
            frags = [f for f in frags if isinstance(f, dict)]
            errors = sum(1 for f in frags if f.get("error"))
            confirmed = sum(1 for f in frags if f.get("match"))
            rows.append([chain, "protein" if kind == "protein" else "nucleic acid", len(frags),
                         f"{100 * confirmed / len(frags):.0f} %" if frags else "–", errors])
            if errors and len(series) < 8:
                pts = sorted(((float(f.get("s", 0)) + float(f.get("e", 0))) / 2, float(f.get("mlogpv") or 0)) for f in frags)
                series.append({"name": f"Chain {chain}", "x": [p[0] for p in pts], "y": [round(p[1], 3) for p in pts]})
    if rows:
        ctx.add_table("Fragments tested per chain", ["Chain", "Type", "Fragments", "Sequence confirmed", "Possible errors"], rows)
    if series:
        ctx.add_plot("Sequence confidence along the chains with possible errors", series, x_label="Residue",
                     y_label="−log10(p)", hlines=[{"y": 0.55, "label": "threshold"}])
    status = "good" if not n_issues else "bad" if shifts or mismatches else "warn"
    ctx.add_highlight("Sequence check", "clean" if not n_issues else f"{n_issues} issue{'s' if n_issues > 1 else ''}", status)
    return n_issues


@register
class CheckMySequence(JobType):
    name = "checkmysequence"
    title = "Sequence register check (checkMySequence)"
    category = "Validation"
    tool = "checkmysequence"
    software = ["checkMySequence"]
    description = ("Finds sequence-register shifts, chains matching no input sequence, sequence mismatches and numbering "
                   "issues in a model built into a cryo-EM map (Chojnowski 2022): errors that geometry validation misses.")
    inputs = [Slot("model", ("model",), "Model"),
              Slot("map", ("map",), "Map", help="The map the model was built into."),
              Slot("sequence", ("sequence",), "Sequences", help="All the target sequences of the sample (FASTA).")]
    params = [
        Param("selection", "str", "", label="Part of the model", placeholder="chain A and resi 10:200", advanced=True,
              help="Fragments to check, in cctbx selection syntax (empty = whole model)."),
        Param("plot", "bool", True, label="PDF bar plot", advanced=True),
        extra_args_param(),
    ]
    outputs = [OutputDef("report", "report", "Sequence assignment report")]

    def run(self, ctx: JobContext) -> None:
        import time
        model, m, seq = ctx.require("model"), ctx.require("map"), ctx.require("sequence")
        out, log = ctx.path("checkmysequence.json"), ctx.path("checkmysequence.log")
        args = [ctx.executable("checkmysequence"), "--mapin", m.path, "--modelin", model.path, "--seqin", seq.files[0],
                "--jsonout", str(out)]
        if ctx.params["selection"].strip():
            args += ["--select", ctx.params["selection"].strip()]
        if ctx.params["plot"]:
            args.append("--plot")
        args += ctx.split_extra()
        start = time.time()
        try:
            ctx.run(args, tool="checkmysequence", stdout_path=log)
        except JobError:
            if log.exists() and "hmmer" in log.read_text(errors="replace").lower():
                raise JobError("checkMySequence needs HMMER (hmmsearch) in its environment: "
                               "conda install -c bioconda hmmer") from None
            raise
        if not out.exists():
            raise JobError("checkMySequence wrote no result (checkmysequence.json): see the log above.")
        try:
            data = json.loads(out.read_text())
        except ValueError as exc:
            raise JobError(f"Unreadable checkMySequence result: {exc}") from None
        n = report_sequence_check(ctx, data)
        files = [out, log, *ctx.find_new_files(["*.pdf"], start)]
        ctx.add_output("report", "report", files, "checkMySequence report", meta={"issues": n, "clean": n == 0})


# ----------------------------------------------------------- wwPDB validation
PERCENTILE_LABELS = {
    "clashscore": "Clashscore",
    "percent-rama-outliers": "Ramachandran outliers (%)",
    "percent-rota-outliers": "Sidechain outliers (%)",
    "RNAsuiteness": "RNA backbone suiteness",
    "percent-RSRZ-outliers": "RSRZ outliers (%)",
}
FAILED_STATUSES = {"failed", "error", "aborted", "cancelled", "canceled", "killed"}
OUTPUT_FILES = (("validation-report-log", "validation_log.txt"), ("validation-report-full", "validation_report.pdf"),
                ("validation-data", "validation_data.xml"), ("validation-data-cif", "validation_data.cif"),
                ("validation-report-slider", "validation_sliders.svg"))


def _num(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_wwpdb_xml(path: str | Path) -> dict:
    """Entry-level values and residues with outliers from a wwPDB validation XML file."""
    import xml.etree.ElementTree as ET
    root = ET.parse(str(path)).getroot()
    entry = root.find("Entry")
    residues = []
    for sub in root.iter("ModelledSubgroup"):
        issues = []
        if (sub.get("rama") or "").upper() == "OUTLIER":
            issues.append("Ramachandran")
        if (sub.get("rota") or "").upper() == "OUTLIER":
            issues.append("rotamer")
        for child in sub:
            if child.tag == "clash":
                issues.append("clash")
            elif child.tag.endswith("-outlier"):
                issues.append(child.tag[: -len("-outlier")])
        if issues:
            residues.append({"chain": sub.get("chain", "?"), "resnum": sub.get("resnum", "?"), "resname": sub.get("resname", "?"),
                             "issues": sorted(set(issues), key=issues.index)})
    return {"entry": dict(entry.attrib) if entry is not None else {}, "residues": residues}


def report_wwpdb(ctx: JobContext, summary: dict) -> dict[str, float]:
    entry = summary.get("entry") or {}
    rows, percentiles = [], {}
    for key, value in entry.items():
        if not key.startswith("absolute-percentile-"):
            continue
        metric = key[len("absolute-percentile-"):]
        pct = _num(value)
        if pct is None:
            continue
        percentiles[metric] = pct
        rel = _num(entry.get(f"relative-percentile-{metric}"))
        raw = entry.get(metric, "")
        text = f"{raw} · percentile {pct:.0f}" + (f" ({rel:.0f} among similar resolution)" if rel is not None else "")
        rows.append({"label": PERCENTILE_LABELS.get(metric, metric.replace("-", " ").replace("_", " ")), "value": text,
                     "status": "good" if pct >= 50 else "warn" if pct >= 20 else "bad"})
    if rows:
        ctx.add_metrics("Percentile ranks among PDB entries (higher is better)", rows)
    others = [[k, v] for k, v in entry.items() if "percentile" not in k
              and any(word in k.lower() for word in ("resolution", "inclusion", "contour", "q_score", "q-score", "fsc"))]
    if others:
        ctx.add_table("Map and fit values", ["Value", ""], others)
    residues = summary.get("residues") or []
    if residues:
        counts: dict[str, int] = {}
        for r in residues:
            for issue in r["issues"]:
                counts[issue] = counts.get(issue, 0) + 1
        ctx.add_metrics("Residues with outliers", [{"label": k, "value": v} for k, v in sorted(counts.items(), key=lambda kv: -kv[1])])
        ctx.add_table(f"Residues with outliers (first 60 of {len(residues)})", ["Chain", "Residue", "Outliers"],
                      [[r["chain"], f"{r['resname']} {r['resnum']}", ", ".join(r["issues"])] for r in residues[:60]])
    for metric, label in (("clashscore", "Clashscore"), ("percent-rama-outliers", "Rama outliers")):
        if metric in percentiles:
            p = percentiles[metric]
            ctx.add_highlight(label, f"{entry.get(metric, '?')} (P{p:.0f})", "good" if p >= 50 else "warn" if p >= 20 else "bad")
    return percentiles


class _OneDep:
    """Steps of onedep_validate_cli (one call per step, session kept in the job folder). The client prints
    "OneDep error: ..." but exits with code 0 on server errors, so its output is checked."""

    def __init__(self, ctx: JobContext):
        self.ctx = ctx
        self.exe = ctx.executable("onedep")
        self.session = ctx.path("onedep_session.txt")
        self.tool = ctx.tool_spec("onedep")

    def call(self, *args: str | Path, quiet: bool = False) -> str:
        import re
        import shlex
        import subprocess

        from cryoplug import tools as toolmod
        cmd = [self.exe, "--session_file", str(self.session), *map(str, args)]
        if not quiet:
            self.ctx.log("$ " + shlex.join(cmd))
            with open(self.ctx.path("commands.sh"), "a") as fh:
                fh.write(shlex.join(cmd) + "\n")
        try:
            proc = subprocess.run(toolmod.wrap_command(self.tool, cmd, "onedep"), capture_output=True, text=True,
                                  errors="replace", env=toolmod.tool_env(self.tool), cwd=str(self.ctx.job_dir), timeout=3600)
        except subprocess.TimeoutExpired:
            raise JobError("The wwPDB validation server did not answer within an hour") from None
        text = (proc.stdout or "") + (proc.stderr or "")
        if not quiet:
            for line in text.strip().splitlines():
                self.ctx.log(line)
        error = re.search(r"OneDep error:\s*(.*)", text)
        if error:
            raise JobError(f"wwPDB validation server: {error.group(1).strip() or 'unknown error'}")
        if proc.returncode != 0:
            raise JobError(f"{Path(self.exe).name} exited with code {proc.returncode}: {text.strip()[-600:]}")
        return text


@register
class WwpdbValidation(JobType):
    name = "wwpdb_validation"
    title = "wwPDB validation report (OneDep)"
    category = "Validation"
    tool = "onedep"
    software = ["wwPDB OneDep validation"]
    description = ("Official wwPDB validation report (PDF and XML) from the OneDep validation web service: the report "
                   "journals and reviewers receive. The model and the primary map are uploaded to the wwPDB server.")
    inputs = [Slot("model", ("model",), "Final model"),
              Slot("map", ("map",), "Primary map", help="The map that will be deposited as the primary map.")]
    params = [
        resolution_param(),
        Param("timeout", "int", 180, label="Maximum wait", unit="min", min=5,
              help="The validation of a large EM entry can take an hour or more."),
        Param("poll", "int", 30, label="Check every", unit="s", min=5, advanced=True),
    ]
    outputs = [OutputDef("report", "report", "wwPDB validation report")]

    def run(self, ctx: JobContext) -> None:
        import gzip
        import re
        import shutil
        import time
        model, m = ctx.require("model"), ctx.require("map")
        try:
            res = ctx.resolution(slots=["map", "model"])
        except JobError:
            res = None
        model_cif = self._model_cif(ctx, model.path, res)
        ctx.progress(0.05, "Compressing the map for the upload")
        map_gz = ctx.path("primary_map.map.gz")
        with open(m.path, "rb") as src, gzip.open(map_gz, "wb", compresslevel=4) as dst:
            shutil.copyfileobj(src, dst, 16 * 1024 * 1024)
        od = _OneDep(ctx)
        ctx.progress(0.1, "Opening a session on the wwPDB validation server")
        od.call("--new_session")
        ctx.progress(0.15, "Uploading the model")
        od.call("--input_file", model_cif, "--input_type", "model")
        ctx.progress(0.2, f"Uploading the map ({map_gz.stat().st_size / 1e6:.0f} MB)")
        od.call("--input_file", map_gz, "--input_type", "em-volume")
        od.call("--validate", "--exp_method", "EM")
        started, last, status = time.time(), None, "unknown"
        timeout, poll = ctx.params["timeout"], ctx.params["poll"]
        while True:
            found = re.search(r"OneDep status:\s*(\S+)", od.call("--status", quiet=True))
            status = found.group(1).lower() if found else "unknown"
            if status != last:
                ctx.log(f"wwPDB server status: {status}")
                last = status
            if status == "completed" or status in FAILED_STATUSES:
                break
            minutes = (time.time() - started) / 60
            if minutes > timeout:
                raise JobError(f"No report after {timeout} min (server status: {status}). Raise 'Maximum wait' and run the "
                               "job again; the session id is in onedep_session.txt.")
            ctx.progress(min(0.9, 0.25 + 0.65 * minutes / timeout), f"Validation running on the wwPDB server ({minutes:.0f} min)")
            time.sleep(poll)
        files: dict[str, Path] = {}
        for ctype, name in OUTPUT_FILES:
            target = ctx.path(name)
            try:
                od.call("--output_file", target, "--output_type", ctype)
            except JobError as exc:
                ctx.warn(f"{name} not retrieved: {exc}")
                continue
            if target.exists() and target.stat().st_size:
                files[ctype] = target
        if status in FAILED_STATUSES:
            log = files.get("validation-report-log")
            tail = "\n".join(log.read_text(errors="replace").splitlines()[-25:]) if log else ""
            raise JobError("The wwPDB server could not validate these files" + (f":\n{tail}" if tail else " (no log returned)."))
        if "validation-report-full" not in files:
            raise JobError("The validation finished but the PDF report could not be downloaded (see the log).")
        percentiles: dict[str, float] = {}
        if "validation-data" in files:
            try:
                percentiles = report_wwpdb(ctx, parse_wwpdb_xml(files["validation-data"]))
            except Exception as exc:  # the PDF is the reference; the summary is a convenience
                ctx.warn(f"Validation XML not summarised: {exc}")
        if "validation-report-slider" in files:
            ctx.add_image("wwPDB percentile sliders", files["validation-report-slider"])
        ctx.add_text("Report", f"Full report: {files['validation-report-full'].resolve()}\nRead it before depositing: the "
                               "annotators and reviewers see the same document.")
        ctx.add_output("report", "report", list(files.values()), "wwPDB validation report",
                       meta={"percentiles": percentiles})

    @staticmethod
    def _model_cif(ctx: JobContext, path: str, res: float | None) -> Path:
        """mmCIF with entities, the experimental method and, when known, the resolution (OneDep needs mmCIF)."""
        from cryoplug.modelio import read_structure
        st = read_structure(path)
        st.setup_entities()
        st.assign_label_seq_id(False)
        doc = st.make_mmcif_document()
        block = doc.sole_block()
        entry = block.name or "model"
        if block.find_value("_exptl.method") is None:
            block.set_mmcif_category("_exptl.", {"entry_id": [entry], "method": ["ELECTRON MICROSCOPY"]})
        if res and block.find_value("_em_3d_reconstruction.resolution") is None:
            block.set_mmcif_category("_em_3d_reconstruction.", {"entry_id": [entry], "id": ["1"], "resolution": [f"{res:.2f}"],
                                                                 "resolution_method": ["FSC 0.143 CUT-OFF"]})
        out = ctx.path("model_for_validation.cif")
        doc.write_file(str(out))
        return out
