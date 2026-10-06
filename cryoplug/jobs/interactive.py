"""Interactive model building sessions (ISOLDE in ChimeraX, Coot).

Like CryoSPARC interactive jobs, these jobs prepare a session and then wait.
The user opens the program on the server display (one click in the browser)
or downloads a ready-to-run bundle for a local workstation, rebuilds, saves the
model in the job directory (or uploads it), and finishes the job by choosing
the model to register as output.
"""
from __future__ import annotations

import os
from pathlib import Path

from cryoplug.jobs import register
from cryoplug.jobs.base import JobContext, JobError, JobType, OutputDef, Param, Slot

EXCLUDED_DIRS = ("coot-backup", "coot-download", "processing_files", ".cryoplug_cache")


def model_suffix(path: str) -> str:
    name = path.lower()
    for suf in (".mmcif", ".cif", ".pdb", ".ent"):
        if name.endswith(suf):
            return ".cif" if suf == ".mmcif" else suf
    return ".pdb"


def list_candidate_models(job_dir: Path) -> list[dict]:
    """Models saved by the user in the job directory, newest first."""
    out = []
    for dirpath, dirs, files in os.walk(job_dir):
        dirs[:] = [d for d in dirs if not d.startswith(EXCLUDED_DIRS)]
        for fn in files:
            low = fn.lower()
            if not low.endswith((".pdb", ".cif", ".mmcif", ".ent")):
                continue
            if fn.startswith("input_"):
                continue
            p = Path(dirpath) / fn
            if p.is_symlink():
                continue
            st = p.stat()
            out.append({"path": str(p.relative_to(job_dir)), "size": st.st_size, "mtime": st.st_mtime})
    return sorted(out, key=lambda d: d["mtime"], reverse=True)


class InteractiveBase(JobType):
    category = "Interactive"
    interactive = True
    inputs = [
        Slot("model", ("model",), "Model"),
        Slot("map", ("map",), "Map", help="Map used for fitting (sharpened)."),
        Slot("map2", ("map",), "Second map", required=False, help="e.g. unsharpened or density-modified map."),
    ]
    outputs = [OutputDef("model", "model", "Rebuilt model")]
    session_script = ""

    def stage_inputs(self, ctx: JobContext) -> dict[str, str]:
        model = ctx.require("model")
        staged = {"model": ctx.link_input(model.path, "input_model" + model_suffix(model.path)).name,
                  "maps": []}
        staged["maps"].append(ctx.link_input(ctx.require("map").path, "input_map" + Path(ctx.require("map").path).suffix).name)
        m2 = ctx.input("map2")
        if m2:
            staged["maps"].append(ctx.link_input(m2.path, "input_map2" + Path(m2.path).suffix).name)
        return staged

    def bundle_files(self, ctx: JobContext) -> list[tuple[str, Path]]:
        files = []
        for p in sorted(ctx.job_dir.iterdir()):
            if p.name.startswith("input_") or p.name in (self.session_script, "README_session.txt"):
                files.append((p.name, p.resolve()))
        return files

    def finalize(self, ctx: JobContext, choice: str | None) -> None:
        from cryoplug.jobs.building import report_model, quick_cc
        candidates = list_candidate_models(ctx.job_dir)
        if choice:
            path = (ctx.job_dir / choice).resolve()
            if ctx.job_dir.resolve() not in path.parents:
                raise JobError("The selected model must be inside the job directory")
        elif candidates:
            path = ctx.job_dir / candidates[0]["path"]
        else:
            raise JobError("No saved model found in the job directory. Save or upload your model first.")
        if not path.is_file():
            raise JobError(f"File not found: {path}")
        ctx.log(f"Registering user model {path.name}")
        report_model(ctx, path)
        ctx.add_output("model", "model", path, f"Model from {self.title.split(' ')[0]}", inherit=["map", "model"])
        m = ctx.input("map")
        if m:
            quick_cc(ctx, m.path, path, m.meta.get("resolution"))


@register
class IsoldeSession(InteractiveBase):
    name = "isolde_session"
    title = "ISOLDE session"
    tool = "chimerax"
    software = ["ISOLDE", "UCSF ChimeraX"]
    session_script = "isolde_session.py"
    description = ("Interactive, physics-based rebuilding in ISOLDE (molecular dynamics flexible fitting in ChimeraX). "
                   "The model and maps are loaded and associated automatically; save the result in the job folder "
                   "(e.g. 'save isolde_model.cif models #1') then finish the job.")
    params = [
        Param("start_isolde", "bool", True, label="Start ISOLDE automatically"),
        Param("extra_commands", "text", "", label="Extra ChimeraX commands", advanced=True,
              help="One ChimeraX command per line, run after loading (e.g. 'isolde restrain ligands #1')."),
    ]

    def prepare(self, ctx: JobContext) -> None:
        staged = self.stage_inputs(ctx)
        maps = staged["maps"]
        map_ids = ",".join(str(i + 2) for i in range(len(maps)))
        extra = [c.strip() for c in str(ctx.params.get("extra_commands") or "").splitlines() if c.strip()]
        lines = [
            "# CryoPlug ISOLDE session. Open with:  chimerax isolde_session.py",
            "import os",
            "from chimerax.core.commands import run",
            "here = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else os.getcwd()",
            "run(session, 'cd \"%s\"' % here)",
            f"run(session, 'open \"{staged['model']}\"')",
        ]
        lines += [f"run(session, 'open \"{m}\"')" for m in maps]
        lines.append(f"run(session, 'clipper associate #{map_ids} toModel #1')")
        if ctx.params["start_isolde"]:
            lines.append("run(session, 'isolde start')")
        lines += [f"run(session, {c!r})" for c in extra]
        lines.append("session.logger.info('CryoPlug: when done, save with  save isolde_model.cif models #1  "
                     "(in this folder), then click Finish in CryoPlug.')")
        ctx.path(self.session_script).write_text("\n".join(lines) + "\n")
        ctx.path("README_session.txt").write_text(
            "CryoPlug ISOLDE session\n\n"
            "1. Run:  chimerax isolde_session.py   (from this folder)\n"
            "2. Rebuild the model in ISOLDE.\n"
            "3. Save:  save isolde_model.cif models #1\n"
            "4. In CryoPlug, upload the saved model (if you worked on another computer) and click Finish.\n")
        ctx.log("ISOLDE session prepared. Launch it from the browser or download the session bundle.")

    def launch_command(self, ctx: JobContext) -> list[str]:
        return [ctx.executable("chimerax"), str(ctx.path(self.session_script))]


@register
class CootSession(InteractiveBase):
    name = "coot_session"
    title = "Coot session"
    tool = "coot"
    software = ["Coot"]
    session_script = "run_coot.sh"
    description = ("Interactive model building and real-space refinement in Coot. Save coordinates in the job folder "
                   "(File > Save Coordinates) then finish the job.")
    params = []

    def prepare(self, ctx: JobContext) -> None:
        staged = self.stage_inputs(ctx)
        args = ["coot", "--pdb", staged["model"]]
        for m in staged["maps"]:
            args += ["--map", m]
        script = "#!/bin/bash\n# CryoPlug Coot session\ncd \"$(dirname \"$0\")\"\nexec " + " ".join(f'"{a}"' for a in args) + "\n"
        p = ctx.path(self.session_script)
        p.write_text(script)
        p.chmod(0o755)
        ctx.path("README_session.txt").write_text(
            "CryoPlug Coot session\n\n"
            "1. Run:  ./run_coot.sh   (or: coot --pdb input_model... --map input_map...)\n"
            "2. Build/refine, then File > Save Coordinates into this folder (e.g. coot_model.cif).\n"
            "3. In CryoPlug, upload the model if needed and click Finish.\n")
        ctx.log("Coot session prepared. Launch it from the browser or download the session bundle.")

    def launch_command(self, ctx: JobContext) -> list[str]:
        model = next(p.name for p in ctx.job_dir.iterdir() if p.name.startswith("input_model"))
        args = [ctx.executable("coot"), "--pdb", model]
        for p in sorted(ctx.job_dir.iterdir()):
            if p.name.startswith("input_map"):
                args += ["--map", p.name]
        return args
