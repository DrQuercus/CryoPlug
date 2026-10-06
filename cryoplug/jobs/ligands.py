"""Ligands and waters: restraint generation (Phenix eLBOW) and water placement (phenix.douse)."""
from __future__ import annotations

import re
import time

from cryoplug.jobs import register
from cryoplug.jobs.base import JobContext, JobError, JobType, OutputDef, Param, Slot, extra_args_param, resolution_param
from cryoplug.jobs.building import pick_new_model, report_model

LIGAND_CODE = re.compile(r"[A-Za-z0-9]{1,5}")


@register
class PhenixElbow(JobType):
    name = "phenix_elbow"
    title = "Ligand restraints (eLBOW)"
    category = "Model building"
    tool = "phenix"
    software = ["Phenix eLBOW"]
    description = ("Geometry restraints (CIF) and starting coordinates for a ligand from its CCD code or SMILES "
                   "(phenix.elbow). Connect the restraints to real-space refinement or Servalcat.")
    params = [
        Param("source", "choice", "ccd", choices=["ccd", "smiles"], label="Ligand definition"),
        Param("ccd_code", "str", "", label="CCD code", placeholder="ATP"),
        Param("smiles", "str", "", label="SMILES", placeholder="CC(=O)Oc1ccccc1C(=O)O"),
        Param("residue_name", "str", "LIG", label="Residue name", help="Code (1-5 characters) used for a SMILES ligand."),
        Param("optimize", "bool", True, label="Optimise geometry (--opt)"),
        extra_args_param(),
    ]
    outputs = [OutputDef("restraints", "restraints", "Restraints"), OutputDef("ligand", "model", "Ligand coordinates")]

    @classmethod
    def validate(cls, params, connected):
        if params.get("source") == "ccd" and not LIGAND_CODE.fullmatch(str(params.get("ccd_code") or "")):
            return ["Give a CCD code (1-5 letters/digits)"]
        if params.get("source") == "smiles" and not params.get("smiles"):
            return ["Give a SMILES string"]
        if not LIGAND_CODE.fullmatch(str(params.get("residue_name") or "")):
            return ["Residue name must be 1-5 letters/digits"]
        return []

    def run(self, ctx: JobContext) -> None:
        prog = ctx.program("phenix", "phenix.elbow")
        if ctx.params["source"] == "ccd":
            name = ctx.params["ccd_code"].upper()
            args = [prog, f"--chemical_component={name}", f"--output={name}"]
        else:
            name = ctx.params["residue_name"].upper()
            args = [prog, f"--smiles={ctx.params['smiles']}", f"--id={name}", f"--output={name}"]
        if ctx.params["optimize"]:
            args.append("--opt")
        ctx.run(args + ctx.split_extra(), tool="phenix")
        cif, pdb = ctx.path(f"{name}.cif"), ctx.path(f"{name}.pdb")
        if not cif.exists():
            raise JobError(f"phenix.elbow did not write {cif.name}")
        ctx.add_output("restraints", "restraints", cif, f"{name} restraints", meta={"residue": name})
        if pdb.exists():
            ctx.add_output("ligand", "model", pdb, f"{name} coordinates", meta={"residue": name})
        ctx.add_highlight("Ligand", name)


@register
class PhenixDouse(JobType):
    name = "phenix_douse"
    title = "Add waters (phenix.douse)"
    category = "Refinement"
    tool = "phenix"
    software = ["Phenix douse"]
    description = ("Place ordered water molecules in the map around the model (phenix.douse). Useful for maps better "
                   "than about 2.5-3 Å; refine and inspect the waters afterwards.")
    inputs = [Slot("model", ("model",), "Model"), Slot("map", ("map",), "Map")]
    params = [resolution_param(), extra_args_param()]
    outputs = [OutputDef("model", "model", "Model with waters")]

    def run(self, ctx: JobContext) -> None:
        from cryoplug.modelio import read_structure, structure_summary
        model, m = ctx.require("model"), ctx.require("map")
        res = ctx.resolution(slots=["map", "model"])
        before = structure_summary(read_structure(model.path))["waters"]
        start = time.time()
        ctx.run([ctx.program("phenix", "phenix.douse"), model.path, m.path, f"resolution={res}", *ctx.split_extra()],
                tool="phenix")
        out = pick_new_model(ctx, start, ["*douse*", "*water*"])
        summary = report_model(ctx, out)
        ctx.add_output("model", "model", out, "Model with waters", inherit=["map", "model"])
        ctx.add_highlight("Waters added", summary["waters"] - before)
