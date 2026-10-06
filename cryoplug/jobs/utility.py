"""Utility jobs: model editing, merging, rendering and arbitrary commands."""
from __future__ import annotations

import glob
import shlex
import string
from pathlib import Path

from cryoplug.jobs import register
from cryoplug.jobs.base import JobContext, JobError, JobType, OutputDef, Param, Slot, resolution_param
from cryoplug.tools import TOOLS


@register
class ModelTools(JobType):
    name = "model_tools"
    title = "Model operations"
    category = "Utilities"
    software = ["gemmi"]
    description = ("Built-in model editing with gemmi: convert PDB/mmCIF, remove hydrogens/waters/ligands/alternative "
                   "conformations, select or rename chains, reset B-factors and occupancies.")
    inputs = [Slot("model", ("model",), "Model")]
    params = [
        Param("output_format", "choice", "cif", choices=["cif", "pdb"], label="Output format"),
        Param("keep_chains", "str", "", label="Keep chains", placeholder="A,B", help="Empty = all chains."),
        Param("rename_chains", "str", "", label="Rename chains", placeholder="A:H,B:L"),
        Param("remove_hydrogens", "bool", False, label="Remove hydrogens"),
        Param("remove_waters", "bool", False, label="Remove waters"),
        Param("remove_ligands", "bool", False, label="Remove ligands (non-polymer)"),
        Param("remove_altlocs", "bool", False, label="Keep only the first alternative conformation"),
        Param("set_bfactor", "float", 0.0, label="Set all B-factors", unit="Å²", min=0.0, help="0 = keep."),
        Param("set_occupancy_one", "bool", False, label="Set occupancies to 1"),
        Param("output_name", "str", "model_edited", label="Output name", advanced=True),
    ]
    outputs = [OutputDef("model", "model", "Edited model")]

    def run(self, ctx: JobContext) -> None:
        from cryoplug.jobs.building import report_model
        from cryoplug.modelio import WATER_NAMES, read_structure, write_structure
        p = ctx.params
        st = read_structure(ctx.require("model").path)
        done = []
        if p["keep_chains"]:
            keep = {c.strip() for c in p["keep_chains"].split(",") if c.strip()}
            for model in st:
                for name in [c.name for c in model if c.name not in keep]:
                    model.remove_chain(name)
            done.append(f"kept chains {', '.join(sorted(keep))}")
        if p["rename_chains"]:
            for pair in p["rename_chains"].split(","):
                if ":" not in pair:
                    raise JobError(f"Bad rename specification '{pair}' (use OLD:NEW)")
                old, new = (x.strip() for x in pair.split(":", 1))
                st.rename_chain(old, new)
            done.append(f"renamed {p['rename_chains']}")
        if p["remove_hydrogens"]:
            st.remove_hydrogens()
            done.append("removed hydrogens")
        if p["remove_waters"]:
            st.remove_waters()
            done.append("removed waters")
        if p["remove_ligands"]:
            for model in st:
                for chain in model:
                    for i in reversed(range(len(chain))):
                        res = chain[i]
                        if res.het_flag == "H" and res.name not in WATER_NAMES:
                            del chain[i]
            done.append("removed ligands")
        if p["remove_altlocs"]:
            st.remove_alternative_conformations()
            done.append("removed alternative conformations")
        if p["set_bfactor"] > 0 or p["set_occupancy_one"]:
            for model in st:
                for chain in model:
                    for res in chain:
                        for atom in res:
                            if p["set_bfactor"] > 0:
                                atom.b_iso = p["set_bfactor"]
                            if p["set_occupancy_one"]:
                                atom.occ = 1.0
            done.append("reset B-factors/occupancies")
        st.remove_empty_chains()
        st.setup_entities()
        out = write_structure(st, ctx.path(f"{p['output_name'] or 'model_edited'}.{p['output_format']}"))
        ctx.log("Operations: " + (", ".join(done) or "format conversion only"))
        report_model(ctx, out)
        ctx.add_output("model", "model", out, "Edited model", inherit=["model"])


def _free_chain_id(used: set[str]) -> str:
    for c in string.ascii_uppercase + string.ascii_lowercase + string.digits:
        if c not in used:
            return c
    for a in string.ascii_uppercase:
        for b in string.ascii_uppercase:
            if a + b not in used:
                return a + b
    raise JobError("No free chain identifier")


@register
class MergeModels(JobType):
    name = "merge_models"
    title = "Merge models"
    category = "Utilities"
    software = ["gemmi"]
    description = "Combine chains from several models (e.g. docked domains or subunits) into one; clashing chain IDs are renamed."
    inputs = [Slot("model_a", ("model",), "Model A"), Slot("model_b", ("model",), "Model B"),
              Slot("model_c", ("model",), "Model C", required=False), Slot("model_d", ("model",), "Model D", required=False)]
    outputs = [OutputDef("model", "model", "Merged model")]

    def run(self, ctx: JobContext) -> None:
        from cryoplug.jobs.building import report_model
        from cryoplug.modelio import read_structure, write_structure
        base = read_structure(ctx.require("model_a").path)
        target = base[0]
        used = {c.name for c in target}
        for slot in ("model_b", "model_c", "model_d"):
            inp = ctx.input(slot)
            if not inp:
                continue
            other = read_structure(inp.path)
            for chain in other[0]:
                new = chain.clone()
                if new.name in used:
                    old = new.name
                    new.name = _free_chain_id(used)
                    ctx.log(f"{inp.source}: chain {old} renamed to {new.name}")
                used.add(new.name)
                target.add_chain(new)
        while len(base) > 1:
            del base[1]
        base.setup_entities()
        out = write_structure(base, ctx.path("merged_model.cif"))
        report_model(ctx, out)
        ctx.add_output("model", "model", out, "Merged model", inherit=["model_a"])


def render_script(map_path: str | None, model_path: str | None, level: float, width: int, height: int,
                  out_prefix: str, transparency: int) -> str:
    lines = ["from chimerax.core.commands import run", "run(session, 'set bgColor white')"]
    n = 0
    if model_path:
        n += 1
        lines += [f"run(session, 'open \"%s\"' % {model_path!r})",
                  f"run(session, 'hide #{n} atoms; show #{n} cartoons; color #{n} bypolymer')"]
    if map_path:
        n += 1
        lines.append(f"run(session, 'open \"%s\"' % {map_path!r})")
        lv = f"level {level}" if level else "sdLevel 4"
        lines.append(f"run(session, 'volume #{n} style surface {lv} color #b4c8e0')")
        if model_path:
            lines.append(f"run(session, 'transparency #{n} {transparency} target s')")
    lines += ["run(session, 'view; lighting soft; graphics silhouettes true')"]
    for name, turn in (("front", None), ("side", "turn y 90"), ("top", "turn x 90")):
        if turn:
            lines.append(f"run(session, '{turn}')")
        lines.append(f"run(session, 'save \"%s\" width {width} height {height} supersample 3' % {out_prefix + '_' + name + '.png'!r})")
    return "\n".join(lines) + "\n"


@register
class ChimeraXRender(JobType):
    name = "chimerax_render"
    title = "Render images (ChimeraX)"
    category = "Utilities"
    tool = "chimerax"
    software = ["UCSF ChimeraX"]
    description = "Offscreen ChimeraX rendering of the map and/or model from three orthogonal views (figures, reports)."
    inputs = [Slot("map", ("map",), "Map", required=False), Slot("model", ("model",), "Model", required=False)]
    params = [
        Param("level", "float", 0.0, label="Contour level", help="0 = 4 standard deviations above the mean."),
        Param("transparency", "int", 60, label="Map transparency with model", unit="%", min=0, max=100),
        Param("width", "int", 1200, label="Width", unit="px", min=100),
        Param("height", "int", 900, label="Height", unit="px", min=100),
    ]
    outputs = [OutputDef("images", "files", "Images")]

    @classmethod
    def validate(cls, params, connected):
        return [] if connected else ["Connect a map and/or a model"]

    def run(self, ctx: JobContext) -> None:
        m, model = ctx.input("map"), ctx.input("model")
        prefix = str(ctx.path("render"))
        script = render_script(m.path if m else None, model.path if model else None, ctx.params["level"],
                               ctx.params["width"], ctx.params["height"], prefix, ctx.params["transparency"])
        ctx.path("render.py").write_text(script)
        ctx.run([ctx.executable("chimerax"), "--nogui", "--offscreen", "--exit", "--script", str(ctx.path("render.py"))], tool="chimerax")
        images = [Path(f"{prefix}_{v}.png") for v in ("front", "side", "top")]
        images = [p for p in images if p.exists()]
        if not images:
            raise JobError("No image produced (does this ChimeraX build support --offscreen?)")
        for p in images:
            ctx.add_image(p.stem.replace("render_", "").capitalize() + " view", p)
        ctx.add_output("images", "files", images, "Rendered images")


@register
class CustomCommand(JobType):
    name = "custom_command"
    title = "Custom command"
    category = "Utilities"
    tool = "shell"
    software = []
    description = ("Run any program on CryoPlug data (bash script template). Placeholders: {map} {map2} {half_map_a} "
                   "{half_map_b} {model} {mask} {sequence} {job_dir} {resolution} {pixel_size} {gpus}. Files matching "
                   "the output patterns are registered as outputs.")
    inputs = [Slot("map", ("map",), "Map", required=False), Slot("map2", ("map",), "Second map", required=False),
              Slot("half_maps", ("half_maps",), "Half maps", required=False), Slot("model", ("model",), "Model", required=False),
              Slot("mask", ("mask",), "Mask", required=False), Slot("sequence", ("sequence",), "Sequence", required=False)]
    params = [
        Param("command", "text", "", label="Command", required=True,
              placeholder="my_program --in {map} --out result.mrc --res {resolution}"),
        Param("environment", "choice", "shell", choices=list(TOOLS), label="Environment",
              help="Tool whose setup (source/conda/module) is applied before the command."),
        Param("output_maps", "str", "", label="Output map pattern", placeholder="result*.mrc"),
        Param("output_models", "str", "", label="Output model pattern", placeholder="*.cif"),
        resolution_param(),
        Param("use_gpu", "bool", False, label="Needs a GPU", advanced=True),
    ]
    outputs = [OutputDef("map", "map"), OutputDef("model", "model")]

    @classmethod
    def resources(cls, params):
        res = super().resources(params)
        res["num_gpus"] = 1 if params.get("use_gpu") else 0
        return res

    def run(self, ctx: JobContext) -> None:
        values: dict[str, str] = {"job_dir": str(ctx.job_dir), "gpus": ",".join(ctx.physical_gpus())}
        for slot in ("map", "map2", "model", "mask", "sequence"):
            inp = ctx.input(slot)
            values[slot] = inp.path if inp else ""
        hm = ctx.input("half_maps")
        values["half_map_a"], values["half_map_b"] = (hm.files[0], hm.files[1]) if hm else ("", "")
        try:
            values["resolution"] = f"{ctx.resolution():.3f}"
        except JobError:
            values["resolution"] = ""
        for slot in ("map", "half_maps", "map2"):
            inp = ctx.input(slot)
            if inp and inp.meta.get("pixel_size"):
                values["pixel_size"] = str(inp.meta["pixel_size"])
                break
        values.setdefault("pixel_size", "")
        template = ctx.params["command"]
        try:
            command = template.format_map({k: shlex.quote(v) if v else "''" for k, v in values.items()})
        except KeyError as exc:
            raise JobError(f"Unknown placeholder {exc} in command") from None
        ctx.path("custom_command.sh").write_text("#!/bin/bash\nset -euo pipefail\n" + command + "\n")
        env_tool = ctx.params["environment"]
        ctx.run(["bash", str(ctx.path("custom_command.sh"))], tool=env_tool)
        n = 0
        for kind, pattern in (("map", ctx.params["output_maps"]), ("model", ctx.params["output_models"])):
            if not pattern:
                continue
            for i, f in enumerate(sorted(glob.glob(str(ctx.path(pattern))))):
                name = kind if i == 0 else f"{kind}_{i + 1}"
                ctx.add_output(name, kind, f, Path(f).name, inherit=["map", "half_maps"])
                n += 1
        if (ctx.params["output_maps"] or ctx.params["output_models"]) and n == 0:
            raise JobError("No file matched the output patterns")
