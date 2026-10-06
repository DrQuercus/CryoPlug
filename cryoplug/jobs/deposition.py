"""Pre-deposition checks and wwPDB/EMDB deposition package."""
from __future__ import annotations

import json
import math
import shutil
import zipfile
from pathlib import Path
from typing import Any

import numpy as np

from cryoplug import __version__
from cryoplug.jobs import register
from cryoplug.jobs.base import JobContext, JobError, JobType, OutputDef, Param, Slot, resolution_param

STATUS_ORDER = {"fail": 0, "warn": 1, "pass": 2, "info": 3}


class Checklist:
    def __init__(self) -> None:
        self.items: list[dict[str, str]] = []

    def add(self, category: str, check: str, status: str, detail: str = "") -> None:
        self.items.append({"category": category, "check": check, "status": status, "detail": detail})

    def count(self, status: str) -> int:
        return sum(1 for i in self.items if i["status"] == status)


def _map_checks(cl: Checklist, ctx: JobContext, name: str, path: str, ref=None):
    from cryoplug.mrc import MapVolume
    vol = MapVolume.read(path)
    finite = bool(np.isfinite(vol.data).all())
    cl.add("Maps", f"{name}: values finite", "pass" if finite else "fail", "" if finite else "NaN/Inf values present")
    if not np.allclose(vol.voxel, vol.voxel[0], rtol=1e-3):
        cl.add("Maps", f"{name}: isotropic voxels", "warn", f"voxel size {vol.voxel.round(4).tolist()}")
    if ref is not None:
        same_box = vol.data.shape == ref.data.shape
        same_vox = np.allclose(vol.voxel, ref.voxel, rtol=1e-3)
        same_org = np.allclose(vol.origin, ref.origin, atol=0.05)
        ok = same_box and same_vox and same_org
        detail = [] if ok else [s for s, c in (("box", same_box), ("voxel size", same_vox), ("origin", same_org)) if not c]
        cl.add("Maps", f"{name}: same grid as primary map", "pass" if ok else "warn",
               "differs in " + ", ".join(detail) if detail else "")
    return vol


def _chain_breaks(st) -> list[str]:
    breaks = []
    for chain in st[0]:
        polymer = chain.get_polymer()
        for i in range(polymer.length() - 1):
            r1, r2 = polymer[i], polymer[i + 1]
            if r2.seqid.num != r1.seqid.num + 1:
                continue
            c = r1.find_atom("C", "*") or r1.find_atom("O3'", "*")
            n = r2.find_atom("N", "*") or r2.find_atom("P", "*")
            if c and n and c.pos.dist(n.pos) > 2.5:
                breaks.append(f"{chain.name}/{r1.name}{r1.seqid.num}-{r2.name}{r2.seqid.num} ({c.pos.dist(n.pos):.1f} Å)")
    return breaks


@register
class PreDepositionCheck(JobType):
    name = "predeposition_check"
    title = "Pre-deposition checks"
    category = "Deposition"
    software = ["CryoPlug"]
    description = ("Automated checklist before submitting to the wwPDB/EMDB: map/half-map/mask consistency, model "
                   "inside the map, fit at the suggested contour, chain IDs, unknown residues, occupancies, B-factors, "
                   "severe clashes, chain breaks and agreement with the deposited sequence.")
    inputs = [Slot("model", ("model",), "Model"), Slot("map", ("map",), "Primary map"),
              Slot("half_maps", ("half_maps",), "Half maps", required=False),
              Slot("mask", ("mask",), "Mask", required=False),
              Slot("sequence", ("sequence",), "Sequence", required=False)]
    params = [resolution_param(), Param("contour", "float", 0.0, label="Contour level", help="0 = suggest from model mass.")]
    outputs = [OutputDef("report", "report", "Checklist")]

    def run(self, ctx: JobContext) -> None:
        from cryoplug.analysis import atom_inclusion, cc_mask
        from cryoplug.modelio import (
            align_chain_to_sequence, atom_arrays, classify_sequence, parse_fasta, read_structure,
            severe_overlaps, structure_summary,
        )
        from cryoplug.mrc import suggest_contour

        cl = Checklist()
        model, m = ctx.require("model"), ctx.require("map")
        ctx.progress(0.05, "Checking maps")
        vol = _map_checks(cl, ctx, "Primary map", m.path)
        zero_frac = float(np.mean(vol.data == 0))
        if zero_frac > 0.3:
            cl.add("Maps", "Primary map masking", "info",
                   f"{100 * zero_frac:.0f} % of voxels are exactly 0 (masked map). Deposit the unmasked half maps too.")
        if np.any(np.abs(vol.origin) > 1e-3):
            cl.add("Maps", "Map origin", "info", f"Non-zero origin {np.round(vol.origin, 2).tolist()} Å: check the model superimposes in ChimeraX.")
        hm = ctx.input("half_maps")
        if hm:
            for i, f in enumerate(hm.files[:2]):
                _map_checks(cl, ctx, f"Half map {i + 1}", f, ref=vol)
        else:
            cl.add("Maps", "Half maps provided", "fail", "EMDB requires both unfiltered, unmasked half maps.")
        mask = ctx.input("mask")
        if mask:
            mv = _map_checks(cl, ctx, "Mask", mask.path, ref=vol)
            lo, hi = float(mv.data.min()), float(mv.data.max())
            cl.add("Maps", "Mask values within [0, 1]", "pass" if lo >= -1e-3 and hi <= 1.001 else "warn", f"range {lo:.3g} .. {hi:.3g}")
        else:
            cl.add("Maps", "FSC mask provided", "info", "Recommended: deposit the mask used for the FSC calculation.")
        cl.add("Maps", "Handedness", "info", "Not testable automatically: confirm α-helices are right-handed.")

        ctx.progress(0.3, "Checking model")
        st = read_structure(model.path)
        summary = structure_summary(st)
        atoms = atom_arrays(st)
        xyz = atoms["xyz"]
        if len(st) > 1:
            cl.add("Model", "Single model", "warn", f"{len(st)} models in file: only the first is checked.")
        inside = float(np.mean(vol.contains(xyz))) if len(xyz) else 0
        cl.add("Model", "Atoms inside the map box", "pass" if inside > 0.999 else "warn" if inside > 0.95 else "fail",
               f"{100 * inside:.1f} % inside")
        try:
            res = ctx.resolution(slots=["map", "half_maps", "model"])
        except JobError:
            res = None
            cl.add("Model", "Resolution known", "warn", "Set the resolution parameter to evaluate the fit.")
        if res:
            cc = cc_mask(vol, st, res)
            cl.add("Fit", "Model-map correlation (CC mask)", "pass" if cc >= 0.7 else "warn" if cc >= 0.5 else "fail", f"{cc:.3f}")
        contour = float(ctx.params.get("contour") or 0) or suggest_contour(vol, summary["mass_da"])
        incl = atom_inclusion(vol, xyz, contour)
        cl.add("Fit", "Atom inclusion at contour", "pass" if incl >= 0.8 else "warn" if incl >= 0.6 else "fail",
               f"{100 * incl:.1f} % at level {contour:.4g}" + (" (suggested from model mass)" if not ctx.params.get("contour") else ""))

        long_ids = [c.name for c in st[0] if len(c.name) > 4]
        multi = [c.name for c in st[0] if len(c.name) > 1]
        cl.add("Model", "Chain identifiers", "fail" if long_ids else "info" if multi else "pass",
               f"IDs longer than 4 characters: {', '.join(long_ids)}" if long_ids else
               "Multi-character chain IDs: deposit as mmCIF (done by the Deposition package job)." if multi else "")
        unk = [f"{c.name}/{r.seqid.num}" for c in st[0] for r in c if r.name in ("UNK", "UNX", "UNL", "N")]
        cl.add("Model", "Unknown residues (UNK/UNX/UNL)", "warn" if unk else "pass",
               f"{len(unk)} residues, e.g. {', '.join(unk[:8])}" if unk else "")
        cl.add("Model", "Zero-occupancy atoms", "warn" if summary["zero_occupancy_atoms"] else "pass",
               f"{summary['zero_occupancy_atoms']} atoms" if summary["zero_occupancy_atoms"] else "")
        if summary["altloc_atoms"]:
            cl.add("Model", "Alternative conformations", "info", f"{summary['altloc_atoms']} atoms with altlocs")
        b = atoms["b"]
        if len(b) and float(np.ptp(b)) < 1e-3:
            cl.add("Model", "B-factors refined", "warn", f"All atoms have B = {b[0]:.2f}: refine ADPs (e.g. real_space_refine run=adp).")
        elif len(b) and float(b.min()) <= 0:
            cl.add("Model", "B-factors positive", "warn", f"min B = {b.min():.2f}")
        else:
            cl.add("Model", "B-factors refined", "pass", f"mean {summary['b_iso']['mean']} Å²")
        if summary["predicted_like"]:
            cl.add("Model", "Predicted-model B-factors", "warn", "B-factor column looks like pLDDT: refine B-factors against the map.")
        if summary["n_hydrogens"]:
            cl.add("Model", "Hydrogens", "info", f"{summary['n_hydrogens']} H atoms (riding hydrogens can be deposited).")
        if summary["ligands"]:
            cl.add("Model", "Ligands", "info", "Check CCD codes and restraints: " + ", ".join(f"{k}×{v}" for k, v in summary["ligands"].items()))

        ctx.progress(0.6, "Clash and geometry screening")
        overlaps = severe_overlaps(st)
        cl.add("Geometry", "Severe atomic overlaps (< 2.2 Å)", "warn" if overlaps else "pass",
               f"{len(overlaps)} pairs, e.g. " + "; ".join(f"{o['atom1']}–{o['atom2']} {o['distance']} Å" for o in overlaps[:5]) if overlaps else "")
        breaks = _chain_breaks(st)
        cl.add("Geometry", "Chain breaks between consecutively numbered residues", "warn" if breaks else "pass",
               f"{len(breaks)}: " + "; ".join(breaks[:6]) if breaks else "")

        seq = ctx.input("sequence")
        if seq:
            records = parse_fasta(Path(seq.files[0]).read_text())
            for poly in summary["polymers"]:
                best = None
                for name, s in records:
                    kind = classify_sequence(s)
                    if poly["type"] not in (kind, "other"):
                        continue
                    try:
                        al = align_chain_to_sequence(st, poly["chain"], s.replace(":", ""), kind)
                    except Exception:
                        continue
                    if best is None or al["identity"] > best[1]["identity"]:
                        best = (name, al)
                if best is None:
                    cl.add("Sequence", f"Chain {poly['chain']} matches a deposited sequence", "warn", "no matching record")
                else:
                    ident = best[1]["identity"]
                    cl.add("Sequence", f"Chain {poly['chain']} vs '{best[0][:30]}'", "pass" if ident >= 98 else "warn" if ident >= 90 else "fail",
                           f"{ident:.1f} % identity over {poly['length']} modelled residues")
        else:
            cl.add("Sequence", "Sequence provided", "info", "Connect the sample sequence to check model/sequence agreement.")

        cl.items.sort(key=lambda i: (STATUS_ORDER[i["status"]], i["category"]))
        nfail, nwarn = cl.count("fail"), cl.count("warn")
        ctx.add_table("Checklist", ["Status", "Category", "Check", "Details"],
                      [[i["status"].upper(), i["category"], i["check"], i["detail"]] for i in cl.items])
        ctx.add_highlight("Failed", nfail, "bad" if nfail else "good")
        ctx.add_highlight("Warnings", nwarn, "warn" if nwarn else "good")
        out = ctx.path("predeposition_checklist.json")
        out.write_text(json.dumps({"items": cl.items, "contour": contour, "resolution": res, "fail": nfail, "warn": nwarn}, indent=1))
        ctx.add_output("report", "report", out, "Pre-deposition checklist",
                       meta={"fail": nfail, "warn": nwarn, "contour": contour, "atom_inclusion": incl})


# ------------------------------------------------------------------ package
METHOD_TEMPLATES = {
    "import_cryosparc": "The final reconstruction was imported from CryoSPARC{res}.",
    "import_maps": "Half maps and the final map were imported{res}.",
    "map_fsc": "The gold-standard FSC was recomputed from the half maps with high-resolution noise substitution{res}.",
    "phenix_resolve_cryo_em": "Density modification was performed with phenix.resolve_cryo_em.",
    "phenix_auto_sharpen": "The map was sharpened with phenix.auto_sharpen.",
    "phenix_local_aniso_sharpen": "Local anisotropic sharpening was performed with phenix.local_aniso_sharpen.",
    "locscale": "Local amplitude scaling was performed with LocScale 2 ({mode} mode).",
    "emmernet": "A feature-enhanced map was computed with EMmerNet (LocScale 2).",
    "deepemhancer": "The map was post-processed with DeepEMhancer ({model} network).",
    "emready": "The map was enhanced with EMReady.",
    "modelangelo_build": "An initial model was built automatically with ModelAngelo.",
    "colabfold_predict": "Starting models were predicted with AlphaFold2 as implemented in ColabFold.",
    "import_model": "A starting model was obtained ({source}).",
    "phenix_process_predicted_model": "Low-confidence regions of the predicted model were removed with phenix.process_predicted_model.",
    "phenix_dock_in_map": "The model was docked into the map with phenix.dock_in_map.",
    "chimerax_fitmap": "The model was rigid-body fitted into the map with ChimeraX fitmap.",
    "isolde_session": "The model was rebuilt interactively in ISOLDE.",
    "coot_session": "The model was manually rebuilt in Coot.",
    "phenix_real_space_refine": "The model was refined with phenix.real_space_refine ({macro_cycles} macro-cycles).",
    "servalcat_refine": "The model was refined against the half maps with Servalcat ({ncycle} cycles).",
    "phenix_validation_cryoem": "The model was validated with phenix.validation_cryoem and MolProbity.",
    "phenix_molprobity": "Model geometry was assessed with MolProbity.",
    "phenix_emringer": "Side-chain fit was assessed with EMRinger.",
    "mapmodel_validation": "Map-model agreement was assessed with Q-scores and map-model FSC.",
}


def methods_text(ancestors: list[dict[str, Any]]) -> tuple[str, list[str]]:
    from cryoplug.jobs import REGISTRY
    from cryoplug.tools import TOOLS
    sentences, citations, seen = [], [], set()
    for job in ancestors:
        if job.get("status") != "completed":
            continue
        tpl = METHOD_TEMPLATES.get(job["type"])
        params = dict(job.get("params") or {})
        res = None
        for o in job.get("outputs") or []:
            res = res or (o.get("meta") or {}).get("resolution")
        fields = {**{k: str(v).replace("_", " ") for k, v in params.items()},
                  "res": f" (gold-standard FSC = 0.143 resolution {res:.2f} Å)" if isinstance(res, (int, float)) else ""}
        if tpl:
            try:
                s = tpl.format(**fields)
            except (KeyError, IndexError):
                s = tpl.split("(")[0].strip() + "."
            if s not in sentences:
                sentences.append(s)
        jt = REGISTRY.get(job["type"])
        if jt and jt.tool and jt.tool not in seen and jt.tool in TOOLS and TOOLS[jt.tool].citation:
            seen.add(jt.tool)
            citations.append(f"{TOOLS[jt.tool].label}: {TOOLS[jt.tool].citation}")
    sentences.append(f"Post-processing was orchestrated with CryoPlug v{__version__}.")
    if any(j["type"] == "mapmodel_validation" for j in ancestors):
        citations.append("Q-score: Pintilie G. et al. (2020) Nat. Methods 17, 328-334.")
    return " ".join(sentences), citations


def collect_metrics(ancestors: list[dict[str, Any]]) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    for job in ancestors:
        for out in job.get("outputs") or []:
            meta = out.get("meta") or {}
            for k, v in (meta.get("metrics") or {}).items():
                metrics[k] = v
            if out.get("type") == "report":
                for k in ("q_mean", "q_expected", "cc_mask", "atom_inclusion", "map_model_fsc_0.5"):
                    if k in meta:
                        metrics[k] = meta[k]
            if meta.get("resolution") and job["type"] in ("import_cryosparc", "import_maps", "map_fsc"):
                metrics["resolution"] = meta["resolution"]
    return metrics


def table1(summary: dict[str, Any], metrics: dict[str, Any], resolution: float | None, map_info: dict[str, Any],
           symmetry: str) -> list[tuple[str, str]]:
    def f(key: str, fmt: str = "{:g}", suffix: str = "") -> str:
        v = metrics.get(key)
        return (fmt.format(v) + suffix) if isinstance(v, (int, float)) and not (isinstance(v, float) and math.isnan(v)) else ""
    ligands = sum(summary["ligands"].values())
    rows = [
        ("**Data collection and processing**", ""),
        ("Magnification", ""), ("Voltage (kV)", ""), ("Electron exposure (e–/Å²)", ""), ("Defocus range (μm)", ""),
        ("Pixel size (Å)", f"{map_info.get('pixel_size', '')}"),
        ("Symmetry imposed", symmetry),
        ("Initial particle images (no.)", ""), ("Final particle images (no.)", ""),
        ("Map resolution (Å)", f"{resolution:.2f}" if resolution else ""),
        ("FSC threshold", "0.143"),
        ("**Refinement**", ""),
        ("Model resolution (Å), FSC 0.5", f("map_model_fsc_0.5", "{:.2f}")),
        ("Map sharpening B factor (Å²)", ""),
        ("Non-hydrogen atoms", str(summary["n_atoms"] - summary["n_hydrogens"])),
        ("Protein / nucleotide residues", str(sum(p["length"] for p in summary["polymers"]))),
        ("Ligands", str(ligands)),
        ("Mean B factor (Å²)", str(summary["b_iso"]["mean"])),
        ("R.m.s. deviations – bond lengths (Å)", f("rms_bonds", "{:.3f}")),
        ("R.m.s. deviations – bond angles (°)", f("rms_angles", "{:.2f}")),
        ("**Validation**", ""),
        ("MolProbity score", f("molprobity_score", "{:.2f}")),
        ("Clashscore", f("clashscore", "{:.2f}")),
        ("Poor rotamers (%)", f("rotamer_outliers", "{:.2f}")),
        ("Ramachandran favoured (%)", f("rama_favored", "{:.2f}")),
        ("Ramachandran outliers (%)", f("rama_outliers", "{:.2f}")),
        ("CC (mask)", f("cc_mask", "{:.3f}")),
        ("EMRinger score", f("emringer", "{:.2f}")),
        ("Mean Q-score", f("q_mean", "{:.3f}")),
    ]
    return rows


@register
class DepositionPackage(JobType):
    name = "deposition_package"
    title = "Deposition package"
    category = "Deposition"
    software = ["CryoPlug"]
    description = ("Assemble everything for wwPDB OneDep: model as mmCIF (entities set up), primary map, half maps, "
                   "mask, EMDB FSC XML, validation reports, a deposition checklist with the suggested contour level, "
                   "a draft methods paragraph with citations and a draft 'Table 1' built from the job history.")
    inputs = [Slot("model", ("model",), "Final model"), Slot("map", ("map",), "Primary map"),
              Slot("half_maps", ("half_maps",), "Half maps"), Slot("mask", ("mask",), "Mask", required=False),
              Slot("fsc", ("fsc",), "Half-map FSC", required=False),
              Slot("checklist", ("report",), "Pre-deposition checklist", required=False),
              Slot("sequence", ("sequence",), "Sequence", required=False)]
    params = [
        resolution_param(),
        Param("contour", "float", 0.0, label="Recommended contour level", help="0 = from the checklist or model mass."),
        Param("make_zip", "bool", False, label="Create a ZIP archive"),
    ]
    outputs = [OutputDef("package", "files", "Deposition package")]

    def run(self, ctx: JobContext) -> None:
        from cryoplug.modelio import read_structure, structure_summary, write_structure
        from cryoplug.mrc import MapVolume, suggest_contour

        dep = ctx.path("deposition")
        if dep.exists():
            shutil.rmtree(dep)
        (dep / "validation").mkdir(parents=True)
        model, m, hm = ctx.require("model"), ctx.require("map"), ctx.require("half_maps")
        files: list[Path] = []

        ctx.progress(0.1, "Writing mmCIF model")
        st = read_structure(model.path)
        st.setup_entities()
        st.assign_label_seq_id(False)
        cif = write_structure(st, dep / "model.cif")
        files.append(cif)
        summary = structure_summary(st)

        ctx.progress(0.2, "Copying maps")
        for src, name in ((m.path, "primary_map.mrc"), (hm.files[0], "half_map_1.mrc"), (hm.files[1], "half_map_2.mrc")):
            shutil.copyfile(src, dep / name)
            files.append(dep / name)
        mask = ctx.input("mask")
        if mask:
            shutil.copyfile(mask.path, dep / "mask.mrc")
            files.append(dep / "mask.mrc")

        fsc = ctx.input("fsc")
        xml = next((f for f in (fsc.files if fsc else []) if f.endswith(".xml")), None)
        if xml:
            shutil.copyfile(xml, dep / "fsc.xml")
        else:
            ctx.progress(0.3, "Computing half-map FSC for the FSC XML")
            from cryoplug.jobs.maptools import compute_and_report_fsc
            compute_and_report_fsc(ctx, hm.files[0], hm.files[1], mask.path if mask else None)
            shutil.copyfile(ctx.path("fsc_emdb.xml"), dep / "fsc.xml")
        files.append(dep / "fsc.xml")

        for job in ctx.ancestors:
            for out in job.get("outputs") or []:
                if out.get("type") == "report":
                    for f in out.get("files") or []:
                        if Path(f).is_file():
                            target = dep / "validation" / f"{job['uid']}_{Path(f).name}"
                            shutil.copyfile(f, target)

        primary = MapVolume.read(m.path)
        try:
            res: float | None = ctx.resolution(slots=["map", "half_maps", "fsc"])
        except JobError:
            res = None
        contour = float(ctx.params.get("contour") or 0)
        chk = ctx.input("checklist")
        if not contour and chk and chk.meta.get("contour"):
            contour = float(chk.meta["contour"])
        if not contour:
            contour = suggest_contour(primary, summary["mass_da"])

        metrics = collect_metrics(ctx.ancestors)
        symmetry = (m.meta.get("symmetry") or hm.meta.get("symmetry") or "C1")
        methods, citations = methods_text(ctx.ancestors)
        (dep / "methods_draft.md").write_text(
            "# Draft methods\n\n" + methods + "\n\n## Software citations\n\n" + "\n".join(f"- {c}" for c in citations) + "\n")
        rows = table1(summary, metrics, res, primary.info(), symmetry)
        (dep / "table1_draft.md").write_text("# Draft cryo-EM statistics table\n\nEmpty cells: fill in from data collection and processing records.\n\n"
                                             "| | |\n|---|---|\n" + "\n".join(f"| {a} | {b} |" for a, b in rows) + "\n")

        info = primary.info()
        chk_text = ""
        if chk:
            try:
                data = json.loads(Path(chk.files[0]).read_text())
                bad = [i for i in data["items"] if i["status"] in ("fail", "warn")]
                chk_text = "\n".join(f"- [{i['status'].upper()}] {i['category']}: {i['check']} — {i['detail']}" for i in bad) or "- No failures or warnings."
            except (OSError, ValueError, KeyError):
                chk_text = ""
        checklist_md = f"""# wwPDB / EMDB deposition checklist

Generated by CryoPlug v{__version__} for project {ctx.project_uid}, job {ctx.uid}.

## Files (upload in OneDep: https://deposit.wwpdb.org/deposition/)

| OneDep content type | File |
|---|---|
| Model coordinates (mmCIF) | `model.cif` |
| Primary map | `primary_map.mrc` |
| Half map 1 / 2 | `half_map_1.mrc`, `half_map_2.mrc` |
| Mask | {"`mask.mrc`" if mask else "(none)"} |
| FSC curve (XML) | `fsc.xml` |

## Values requested by OneDep

- Recommended contour level: **{contour:.4g}** (check visually; atom inclusion is reported by the checks)
- Map resolution: **{f"{res:.2f} Å" if res else "(fill in)"}** (FSC 0.143)
- Pixel size: **{info['pixel_size']} Å**, box **{' × '.join(map(str, info['box']))}**
- Symmetry: **{symmetry}**
- Model: {summary['n_chains']} chains, {summary['n_residues']} residues, {summary['mass_da'] / 1000:.1f} kDa

## Before submitting

1. Run the official wwPDB validation server on `model.cif` + `primary_map.mrc` + half maps:
   https://validate.wwpdb.org (stand-alone validation, no deposition needed).
2. Review the `validation/` reports and `methods_draft.md`, `table1_draft.md`.
3. Keep the same chain IDs and entity sequences as in your manuscript.

## Open issues from the pre-deposition checks

{chk_text or "- Run the 'Pre-deposition checks' job and connect it to include its findings here."}
"""
        (dep / "CHECKLIST.md").write_text(checklist_md)
        files += [dep / "CHECKLIST.md", dep / "methods_draft.md", dep / "table1_draft.md"]

        if ctx.params["make_zip"]:
            ctx.progress(0.8, "Creating ZIP archive")
            zpath = ctx.path("deposition_package.zip")
            with zipfile.ZipFile(zpath, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
                for p in sorted(dep.rglob("*")):
                    if p.is_file():
                        zf.write(p, p.relative_to(dep.parent), compress_type=zipfile.ZIP_STORED if p.suffix == ".mrc" else zipfile.ZIP_DEFLATED)
            files.append(zpath)
        ctx.add_text("Draft methods", methods + "\n\n" + "\n".join(citations))
        ctx.add_table("Draft Table 1", ["Item", "Value"], [[a.strip("*"), b] for a, b in rows])
        ctx.add_metrics("Deposition values", [
            {"label": "Recommended contour level", "value": f"{contour:.4g}"},
            {"label": "Resolution", "value": f"{res:.2f} Å" if res else "unknown"},
            {"label": "Pixel size", "value": f"{info['pixel_size']} Å"},
            {"label": "Symmetry", "value": symmetry},
        ])
        ctx.add_output("package", "files", files, "Deposition package", meta={"contour": contour, "resolution": res})
        ctx.add_highlight("Contour", f"{contour:.4g}")
