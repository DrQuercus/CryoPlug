"""RELION classification: 2D classification (VDAM or EM), selection of 2D classes (clicked in a gallery as in
CryoSPARC, listed, or automatic with RELION's class ranker), 3D initial model (VDAM) and 3D classification."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from cryoplug import relion as rl
from cryoplug.jobs import register
from cryoplug.jobs.base import JobContext, JobError, OutputDef, Param, Slot, extra_args_param
from cryoplug.jobs.relion import Progress, absolute_greyscale
from cryoplug.jobs.relion_common import (
    SAMPLINGS, RelionJob, compute_params, gpus, healpix, mask_diameter, mpi, particles_out_meta, refine_compute_params,
    relion_command, relion_particles, run_relion, threads,
)
from cryoplug.mrc import MapVolume

BLUSH_MODELS = ["v1.0", "amy-v1.0"]


# ------------------------------------------------------------------ class galleries
def class_counts(data_star: Path) -> tuple[dict[int, int], int]:
    """Particles of each class (rlnClassNumber) of a data.star, and their total."""
    ptab = rl.particle_table(rl.read_star(data_star))
    if not ptab.has("rlnClassNumber"):
        return {}, len(ptab)
    values, counts = np.unique(np.array([int(float(c)) for c in ptab.column("rlnClassNumber")]), return_counts=True)
    return {int(v): int(c) for v, c in zip(values, counts)}, len(ptab)


def render_classes(ctx: JobContext, files: dict[str, Path], folder: str = "classes", size: int = 128) -> list[dict[str, Any]]:
    """A picture of every class average (PNG) and its record for the gallery: particles, share, resolution."""
    import mrcfile

    from cryoplug.imaging import write_png
    from cryoplug.micrographs import class_tile
    infos = rl.model_classes(files["model"])
    counts, total = class_counts(files["data"]) if files["data"].exists() else ({}, 0)
    with mrcfile.open(str(files["classes"]), permissive=True) as m:
        stack = np.asarray(m.data, dtype=np.float32)
    if stack.ndim == 2:
        stack = stack[np.newaxis]
    out_dir = ctx.path(folder)
    out_dir.mkdir(exist_ok=True)
    records = []
    for info in infos:
        k = info["class"]
        if k > len(stack):
            continue
        png = write_png(out_dir / f"class_{k:03d}.png", class_tile(stack[k - 1], min(size, stack.shape[-1]) if size else stack.shape[-1]))
        n = counts.get(k, int(round(info["distribution"] * total)))
        records.append({"class": k, "image": ctx.rel(png), "particles": n,
                        "fraction": round(n / total, 4) if total else round(info["distribution"], 4),
                        "resolution": round(info["resolution"], 2) if info["resolution"] else None,
                        "accuracy_rotations": round(info["accuracy_rotations"], 2) if info["accuracy_rotations"] else None,
                        "accuracy_translations": round(info["accuracy_translations"], 2) if info["accuracy_translations"] else None})
    return records


def gallery_thumbnail(ctx: JobContext, files: dict[str, Path], records: list[dict[str, Any]], name: str) -> Path | None:
    """The nine largest classes side by side (job card thumbnail)."""
    import mrcfile

    from cryoplug.imaging import write_png
    from cryoplug.micrographs import class_tile, tile_sheet
    best = sorted(records, key=lambda r: -r["particles"])[:9]
    if not best:
        return None
    with mrcfile.open(str(files["classes"]), permissive=True) as m:
        stack = np.asarray(m.data, dtype=np.float32)
    if stack.ndim == 2:
        stack = stack[np.newaxis]
    tiles = [class_tile(stack[r["class"] - 1], 80) for r in best]
    return write_png(ctx.path(f"thumb_{name}.png"), tile_sheet(tiles, per_row=3, gap=3))


def class_section(ctx: JobContext, records: list[dict[str, Any]], iteration: int, files: dict[str, Path], **extra: Any) -> None:
    pixel_size, box = 0.0, 0
    try:
        tables = rl.read_star(files["data"])
        box, pixel_size = rl.grid_from_star(tables)
    except (OSError, ValueError):
        pass
    total = sum(r["particles"] for r in records)
    ctx.add_classes(f"2D classes (iteration {iteration})" if iteration else "2D classes", records, key="classes",
                    iteration=iteration, particles=total, pixel_size=pixel_size, box=box,
                    source=ctx.rel(files["data"]), **extra)


class LiveClasses(Progress):
    """Progress of relion_refine and, at each iteration, the class averages written so far (a gallery redrawn while
    the classification runs, as in CryoSPARC)."""

    def __init__(self, ctx: JobContext, total: int):
        super().__init__(ctx, total=total)
        self.shown = 0

    def __call__(self, line: str) -> None:
        before = self.iteration
        super().__call__(line)
        if self.iteration == before:
            return
        it, files = rl.iteration_files(self.ctx.job_dir)
        if not it or it == self.shown or not files["classes"].exists() or not files["model"].exists():
            return
        try:
            records = render_classes(self.ctx, files)
            class_section(self.ctx, records, it, files, running=True)
            self.shown = it
        except Exception as exc:  # pictures are cosmetic; the final gallery is drawn at the end
            self.ctx.log(f"(class averages of iteration {it} not shown: {exc})")


def classes_output_meta(ctx: JobContext, files: dict[str, Path], records: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    tables = rl.read_star(files["data"])
    box, apix = rl.grid_from_star(tables)
    return {"n_classes": len(records), "box": box, "pixel_size": round(apix, 4),
            "particles": sum(r["particles"] for r in records),
            "relion": {"star": ctx.rel(files["classes"]), "kind": kind, "model": ctx.rel(files["model"]),
                       "data": ctx.rel(files["data"]), "optimiser": ctx.rel(files["optimiser"])}}


# ------------------------------------------------------------------ 2D classification
@register
class RelionClass2D(RelionJob):
    name = "relion_class2d"
    title = "2D classification (RELION)"
    category = "Classification"
    description = ("Sorts the particles into 2D class averages (relion_refine): VDAM, the fast gradient algorithm of "
                   "RELION 4/5, or the classic EM algorithm. The class averages show up while it runs; the particles "
                   "keep their class for the selection of the good classes (Select 2D classes).")
    inputs = [Slot("particles", ("particles",), "Particles", help="Extracted particles (RELION or CryoSPARC).")]
    params = [
        Param("classes", "int", 50, label="Number of classes", min=1, max=500),
        Param("algorithm", "choice", "vdam", choices=["vdam", "em"], label="Algorithm",
              labels={"vdam": "VDAM (gradient, fast)", "em": "EM (classic)"},
              help="VDAM: mini-batches, much faster, no MPI. EM: all particles every iteration, MPI possible."),
        Param("vdam_batches", "int", 200, label="VDAM mini-batches", min=10, max=2000,
              help="200 gives good results for most data sets; 100 is faster."),
        Param("em_iterations", "int", 25, label="EM iterations", min=1, max=500),
        Param("particle_diameter", "float", 0.0, label="Mask diameter", unit="Å", min=0,
              help="Circular mask on the particles, a bit larger than them. 0 = 90 % of the box."),
        Param("tau2_fudge", "float", 2.0, label="Regularisation parameter T", min=0.1, max=100,
              help="1-2 for 2D classification; higher values trust the data more."),
        Param("ctf", "bool", True, label="CTF correction"),
        Param("ignore_first_peak", "bool", False, label="Ignore CTFs until the first peak", advanced=True),
        Param("zero_mask", "bool", True, label="Mask particles with zeros", advanced=True),
        Param("highres_limit", "float", 0.0, label="Limit E-step resolution", unit="Å", min=0,
              help="Alignment only up to this resolution: 7-12 Å against over-fitting of small or featureless "
                   "particles. 0 = none."),
        Param("center_classes", "bool", True, label="Centre class averages"),
        Param("align", "bool", True, label="Align the images", advanced=True,
              help="No: classification only, with the poses already in the particle file."),
        Param("psi_step", "float", 6.0, label="In-plane angular sampling", unit="°", min=0.5, max=30),
        Param("offset_range", "float", 5.0, label="Offset search range", unit="px", min=0, max=100),
        Param("offset_step", "float", 1.0, label="Offset search step", unit="px", min=0.1, max=10),
        Param("allow_coarser", "bool", False, label="Allow coarser sampling", advanced=True),
        *refine_compute_params(3),
        mpi(1, help="EM only (VDAM runs without MPI)."),
        threads(8),
        gpus(1),
        extra_args_param(),
    ]
    outputs = [OutputDef("classes", "class_averages", "2D class averages"),
               OutputDef("particles", "particles", "Classified particles")]

    @classmethod
    def validate(cls, params: dict[str, Any], connected: dict[str, str]) -> list[str]:
        if params.get("algorithm") == "vdam" and int(params.get("mpi") or 1) > 1:
            return ["VDAM does not run with MPI: set 1 MPI process (more threads), or use EM"]
        return []

    def run(self, ctx: JobContext) -> None:
        p = ctx.params
        parts = relion_particles(ctx, ctx.require("particles"))
        diameter = mask_diameter(ctx, parts)
        vdam = p["algorithm"] == "vdam"
        iters = int(p["vdam_batches"] if vdam else p["em_iterations"])
        args = [*relion_command(ctx, "relion_refine", 1 if vdam else p["mpi"]), "--o", ctx.rel(ctx.path("run"))]
        if vdam:
            args += ["--grad", "--class_inactivity_threshold", "0.1", "--grad_write_iter", "10"]
        args += ["--iter", str(iters), "--i", parts["ref"]]
        compute_params(p, args)
        args += ["--pad", "2"]
        if p["ctf"]:
            args.append("--ctf")
            if p["ignore_first_peak"]:
                args.append("--ctf_intact_first_peak")
        args += ["--tau2_fudge", f"{p['tau2_fudge']:g}", "--particle_diameter", f"{diameter:g}", "--K", str(p["classes"]),
                 "--flatten_solvent"]
        if p["zero_mask"]:
            args.append("--zero_mask")
        if p["highres_limit"] > 0:
            args += ["--strict_highres_exp", f"{p['highres_limit']:g}"]
        if p["center_classes"]:
            args.append("--center_classes")
        args += ["--oversampling", "1"]
        if not p["align"]:
            args.append("--skip_align")
        else:  # the GUI's samplings are the oversampled ones
            args += ["--psi_step", f"{2 * p['psi_step']:g}", "--offset_range", f"{p['offset_range']:g}",
                     "--offset_step", f"{2 * p['offset_step']:g}"]
            if p["allow_coarser"]:
                args.append("--allow_coarser_sampling")
        args += ["--norm", "--scale", "--j", str(p["threads"])]
        if p["gpus"] > 0:
            args += ["--gpu", ""]
        args += ctx.split_extra()
        ctx.add_table("2D classification", ["Setting", "Value"], [
            ["Particles", f"{parts['n']:,} · box {parts['box']} px · {parts['pixel_size']:g} Å/px"],
            ["Classes", str(p["classes"])], ["Algorithm", f"VDAM, {iters} mini-batches" if vdam else f"EM, {iters} iterations"],
            ["Mask diameter", f"{diameter:g} Å"], ["T", f"{p['tau2_fudge']:g}"]])
        ctx.progress(0.03, f"relion_refine: {p['classes']} classes")
        run_relion(ctx, args, on_line=LiveClasses(ctx, iters))
        it, files = rl.iteration_files(ctx.job_dir)
        missing = [f.name for f in files.values() if not f.exists()] if files else ["run_itNNN_optimiser.star"]
        if missing:
            raise JobError(f"relion_refine did not write {', '.join(missing)} (see the log)")
        records = render_classes(ctx, files)
        class_section(ctx, records, it, files, actions=True)
        thumb = gallery_thumbnail(ctx, files, records, "classes")
        empty = sum(1 for r in records if r["particles"] == 0)
        ctx.add_highlight("Classes", f"{len(records) - empty} of {len(records)}" if empty else str(len(records)))
        ctx.add_highlight("Particles", f"{sum(r['particles'] for r in records):,}")
        ctx.add_output("classes", "class_averages", [files["classes"], files["model"], files["data"], files["optimiser"]],
                       f"{len(records)} class averages", meta=classes_output_meta(ctx, files, records, "class2d"),
                       thumbnail=thumb)
        ctx.add_output("particles", "particles", files["data"], f"Classified particles ({parts['n']:,})",
                       meta=particles_out_meta(ctx, files["data"], "class2d", optimiser=ctx.rel(files["optimiser"]),
                                               model=ctx.rel(files["model"])))


# ------------------------------------------------------------------ class selection
@register
class RelionSelect2D(RelionJob):
    name = "relion_select2d"
    title = "Select 2D classes"
    category = "Classification"
    interactive = True
    description = ("Keeps the particles of the good 2D classes. As in CryoSPARC, the job shows the class averages and "
                   "waits: click the good classes (sorted by size or resolution), then Done. The classes can also be "
                   "listed, or chosen automatically by RELION's class ranker. Gives the selected particles, the "
                   "rejected ones, and the selected class averages (templates for picking).")
    gpu_param = ""
    cpu_param = ""
    inputs = [Slot("classes", ("class_averages",), "2D classes", help="The class averages of a 2D classification.")]
    params = [
        Param("mode", "choice", "interactive", choices=["interactive", "list", "auto"], label="Selection",
              labels={"interactive": "Click the classes (the job waits for you)", "list": "The classes listed below",
                      "auto": "Automatic (RELION class ranker)"}),
        Param("classes", "str", "", label="Classes", placeholder="1, 4, 7-9",
              help="Class numbers for 'listed' (ranges allowed)."),
        Param("min_score", "float", 0.5, label="Minimum score", min=0, max=1,
              help="Class ranker: classes predicted at least this good are selected."),
        Param("min_particles", "int", 0, label="Select at least this many particles", min=0, advanced=True,
              help="Class ranker: the best classes are added until this many particles (0 = no minimum)."),
        Param("min_classes", "int", 0, label="Or at least this many classes", min=0, advanced=True),
        Param("max_per_class", "int", 0, label="At most this many particles per class", min=0, advanced=True,
              help="Evens out the views for an initial model (RELION's relion_display option). 0 = all."),
        Param("recentre", "bool", False, label="Re-centre the class averages",
              help="On their centre of mass, for templates of auto-picking."),
    ]
    outputs = [OutputDef("particles", "particles", "Selected particles"),
               OutputDef("rejected", "particles", "Rejected particles"),
               OutputDef("class_averages", "class_averages", "Selected class averages")]

    @classmethod
    def validate(cls, params: dict[str, Any], connected: dict[str, str]) -> list[str]:
        if params.get("mode") == "list":
            try:
                if not rl.parse_numbers(params.get("classes") or ""):
                    return ["List the classes to keep (e.g. 1, 4, 7-9)"]
            except ValueError as exc:
                return [str(exc)]
        return []

    @staticmethod
    def source_files(ctx: JobContext) -> dict[str, Path]:
        inp = ctx.require("classes")
        rel = inp.meta.get("relion") or {}
        files = {k: ctx.abs(rel[k]) for k in ("model", "data", "optimiser") if rel.get(k)}
        files["classes"] = Path(inp.path)
        missing = [k for k in ("model", "data", "classes") if k not in files or not files[k].exists()]
        if missing:
            raise JobError(f"{inp.source} is not the output of a RELION 2D classification ({', '.join(missing)} missing)")
        return files

    def prepare(self, ctx: JobContext) -> bool:
        p = ctx.params
        files = self.source_files(ctx)
        ctx.progress(0.1, "Drawing the class averages")
        records = render_classes(ctx, files)
        state = {"files": {k: ctx.rel(v) for k, v in files.items()}, "classes": records}
        ctx.path("selection.json").write_text(json.dumps(state, indent=1))
        if p["mode"] == "list":
            self.select(ctx, rl.parse_numbers(p["classes"]), "listed")
            return True
        if p["mode"] == "auto":
            self.rank(ctx, files, records)
            return True
        total = sum(r["particles"] for r in records)
        class_section(ctx, records, 0, files, selectable=True, waiting=True)
        ctx.add_highlight("Classes", str(len(records)))
        ctx.add_highlight("Particles", f"{total:,}")
        ctx.log("Waiting: click the good classes in the job panel, then Done")
        return False

    def finalize(self, ctx: JobContext, choice: str | None) -> None:
        try:
            chosen = json.loads(choice or "{}").get("classes") if (choice or "").strip().startswith("{") else rl.parse_numbers(choice or "")
        except (ValueError, AttributeError) as exc:
            raise JobError(f"Selection not understood: {exc}") from None
        if not chosen:
            raise JobError("Select at least one class")
        self.select(ctx, sorted({int(c) for c in chosen}), "clicked")

    def select(self, ctx: JobContext, chosen: list[int], how: str, scores: dict[int, float] | None = None) -> None:
        p = ctx.params
        state = json.loads(ctx.path("selection.json").read_text())
        files = {k: ctx.abs(v) for k, v in state["files"].items()}
        records = state["classes"]
        known = {r["class"] for r in records}
        wrong = [c for c in chosen if c not in known]
        if wrong:
            raise JobError(f"No class {', '.join(map(str, wrong))} (the classes go from 1 to {max(known)})")
        tables = rl.read_star(files["data"])
        ptab = rl.particle_table(tables)
        classes = np.array([int(float(c)) for c in ptab.column("rlnClassNumber")])
        keep = np.isin(classes, chosen)
        if int(p["max_per_class"]) > 0:
            rng = np.random.default_rng(0)
            for c in chosen:
                idx = np.flatnonzero(classes == c)
                if len(idx) > p["max_per_class"]:
                    keep[rng.choice(idx, len(idx) - int(p["max_per_class"]), replace=False)] = False
        n_keep = int(keep.sum())
        if not n_keep:
            raise JobError("The selected classes hold no particle")
        sel = rl.write_star(ctx.path("particles.star"), [t if t is not ptab else ptab.select(np.flatnonzero(keep))
                                                         for t in tables.values()],
                            comment=f"Particles of classes {', '.join(map(str, chosen))} of {ctx.rel(files['data'])}")
        meta = particles_out_meta(ctx, sel, "select2d", classes=chosen)
        ctx.add_output("particles", "particles", sel, f"{n_keep:,} selected particles", meta=meta)
        rejected = ~np.isin(classes, chosen)
        if rejected.any():
            rej = rl.write_star(ctx.path("particles_rejected.star"),
                                [t if t is not ptab else ptab.select(np.flatnonzero(rejected)) for t in tables.values()],
                                comment="Particles of the classes not selected")
            ctx.add_output("rejected", "particles", rej, f"{int(rejected.sum()):,} rejected particles",
                           meta=particles_out_meta(ctx, rej, "select2d"))
        avg = self.class_averages(ctx, files, records, chosen)
        for r in records:
            r["selected"] = r["class"] in chosen
            if scores and r["class"] in scores:
                r["score"] = round(scores[r["class"]], 3)
        class_section(ctx, records, 0, files, selected=chosen, how=how)
        ctx.highlights = [hl for hl in ctx.highlights if hl["label"] != "Classes"]  # that of the waiting gallery
        ctx.add_table("Selection", ["", "Value"], [
            ["Classes kept", f"{len(chosen)} of {len(records)} ({how})"], ["Particles kept", f"{n_keep:,} of {len(ptab):,}"],
            ["Class numbers", ", ".join(map(str, chosen))]])
        ctx.add_highlight("Classes kept", f"{len(chosen)} / {len(records)}")
        ctx.add_highlight("Particles", f"{n_keep:,}")
        sel_records = [r for r in records if r["class"] in chosen]
        thumb = gallery_thumbnail(ctx, files, sel_records, "selected")
        box, apix = rl.grid_from_star(tables)
        ctx.add_output("class_averages", "class_averages", avg, f"{len(chosen)} class averages",
                       meta={"n_classes": len(chosen), "box": box, "pixel_size": round(apix, 4),
                             "relion": {"star": ctx.rel(avg), "kind": "select2d", "data": ctx.rel(sel)}},
                       thumbnail=thumb)
        ctx.save_report()

    @staticmethod
    def class_averages(ctx: JobContext, files: dict[str, Path], records: list[dict[str, Any]], chosen: list[int]) -> Path:
        """The selected class averages as RELION's class_averages.star (references for template picking),
        re-centred on their centre of mass when asked."""
        model = rl.read_star(files["model"])["model_classes"]
        rows = [model.rows[c - 1] for c in chosen]
        table = rl.StarTable("model_classes", list(model.labels), [list(r) for r in rows])
        if ctx.params.get("recentre"):
            import mrcfile
            from scipy import ndimage
            with mrcfile.open(str(files["classes"]), permissive=True) as m:
                stack = np.asarray(m.data, dtype=np.float32)
                voxel = float(m.voxel_size.x)
            out = []
            for c in chosen:
                img = stack[c - 1]
                pos = np.clip(img, 0, None)
                com = np.array(ndimage.center_of_mass(pos)) if pos.sum() > 0 else np.array(img.shape) / 2
                out.append(ndimage.shift(img, np.array(img.shape) // 2 - com, order=1, mode="constant"))
            stack_path = ctx.path("class_averages.mrcs")
            with mrcfile.new(str(stack_path), overwrite=True) as m:
                m.set_data(np.stack(out).astype(np.float32))
                m.voxel_size = voxel
            table.set_column("rlnReferenceImage", [f"{i + 1:06d}@{ctx.rel(stack_path)}" for i in range(len(chosen))])
            ctx.log(f"{len(chosen)} class averages re-centred on their centre of mass")
        return rl.write_star(ctx.path("class_averages.star"), [table], comment="Selected class averages")

    def rank(self, ctx: JobContext, files: dict[str, Path], records: list[dict[str, Any]]) -> None:
        """Automatic selection with relion_class_ranker (as RELION's Subset selection job does)."""
        p = ctx.params
        if not files.get("optimiser") or not files["optimiser"].exists():
            raise JobError("The class ranker needs the optimiser.star of the 2D classification")
        args = [ctx.program("relion", "relion_class_ranker"), "--opt", ctx.rel(files["optimiser"]),
                "--o", ctx.rel(ctx.job_dir) + "/", "--fn_sel_parts", "ranked_particles.star",
                "--fn_sel_classavgs", "ranked_class_averages.star"]
        if p["min_particles"] > 0:
            args += ["--select_min_nr_particles", str(p["min_particles"])]
        elif p["min_classes"] > 0:
            args += ["--select_min_nr_classes", str(p["min_classes"])]
        args += ["--fn_root", "rank", "--do_granularity_features", "--auto_select", "--min_score", f"{p['min_score']:g}"]
        ctx.progress(0.3, "relion_class_ranker")
        run_relion(ctx, args)
        model = ctx.path("rank_model.star")
        if not model.exists():
            raise JobError("relion_class_ranker wrote no rank_model.star (is RELION's Python environment installed? "
                           "see the log)")
        classes = rl.read_star(model).get("model_classes")
        if classes is None or not classes.has("rlnClassScore"):
            raise JobError("rank_model.star has no class scores (rlnClassScore)")
        scores = {i + 1: float(s) for i, s in enumerate(classes.floats("rlnClassScore"))}
        ranked = ctx.path("ranked_class_averages.star")
        chosen: list[int] = []
        if ranked.exists():  # the classes the ranker selected (it also honours the minimum particles / classes)
            images = rl.read_star(ranked)
            table = next(iter(images.values()))
            names = set(table.column("rlnReferenceImage")) if table.has("rlnReferenceImage") else set()
            ref_of = {i + 1: r for i, r in enumerate(rl.read_star(files["model"])["model_classes"].column("rlnReferenceImage"))}
            chosen = sorted(k for k, ref in ref_of.items() if ref in names)
        if not chosen:
            chosen = sorted(k for k, s in scores.items() if s >= p["min_score"])
        if not chosen:
            raise JobError(f"No class scores {p['min_score']:g} or more (best {max(scores.values()):.2f}): lower the minimum score")
        self.select(ctx, chosen, f"class ranker, score ≥ {p['min_score']:g}", scores=scores)


# ------------------------------------------------------------------ initial model
@register
class RelionInitialModel(RelionJob):
    name = "relion_initial_model"
    title = "3D initial model (RELION)"
    category = "Reconstruction"
    description = ("Makes a first 3D map from the particles without any reference (relion_refine --grad "
                   "--denovo_3dref, VDAM), then aligns it on the symmetry axes and applies the symmetry "
                   "(relion_align_symmetry): RELION's ab initio. Several classes separate different structures.")
    inputs = [Slot("particles", ("particles",), "Particles",
                   help="A few thousand to tens of thousands of good particles, from many views (Select 2D classes; "
                        "'at most N particles per class' evens out the views).")]
    params = [
        Param("iterations", "int", 200, label="VDAM mini-batches", min=10, max=2000),
        Param("classes", "int", 1, label="Number of classes", min=1, max=20),
        Param("symmetry", "str", "C1", label="Symmetry",
              help="Point group of the particle (C1, C2, D7, O, I...). The model is made in C1 and symmetrised after."),
        Param("run_c1", "bool", True, label="Run in C1, apply the symmetry after",
              help="Off: the whole optimisation in the point group (RELION's GUI option)."),
        Param("particle_diameter", "float", 0.0, label="Mask diameter", unit="Å", min=0, help="0 = 90 % of the box."),
        Param("tau2_fudge", "float", 4.0, label="Regularisation parameter T", min=0.1, max=100),
        Param("flatten_solvent", "bool", True, label="Flatten and enforce non-negative solvent"),
        Param("ctf", "bool", True, label="CTF correction"),
        Param("ignore_first_peak", "bool", False, label="Ignore CTFs until the first peak", advanced=True),
        *refine_compute_params(3),
        threads(8),
        gpus(1),
        extra_args_param(),
    ]
    outputs = [OutputDef("map", "map", "Initial model"), OutputDef("maps", "volume_series", "Class maps"),
               OutputDef("particles", "particles", "Particles")]
    cpu_param = "threads"

    def run(self, ctx: JobContext) -> None:
        p = ctx.params
        parts = relion_particles(ctx, ctx.require("particles"))
        diameter = mask_diameter(ctx, parts)
        sym = (str(p["symmetry"]).strip() or "C1").upper()
        iters, k = int(p["iterations"]), int(p["classes"])
        args = [*relion_command(ctx, "relion_refine", 1), "--o", ctx.rel(ctx.path("run")), "--iter", str(iters),
                "--grad", "--denovo_3dref", "--i", parts["ref"]]
        if p["ctf"]:
            args.append("--ctf")
            if p["ignore_first_peak"]:
                args.append("--ctf_intact_first_peak")
        args += ["--K", str(k), "--sym", "C1" if p["run_c1"] else sym]
        if p["flatten_solvent"]:
            args.append("--flatten_solvent")
        args.append("--zero_mask")
        compute_params(p, args)
        args += ["--pad", "1", "--particle_diameter", f"{diameter:g}", "--oversampling", "1", "--healpix_order", "1",
                 "--offset_range", "6", "--offset_step", "2", "--auto_sampling", "--tau2_fudge", f"{p['tau2_fudge']:g}",
                 "--j", str(p["threads"])]
        if p["gpus"] > 0:
            args += ["--gpu", ""]
        args += ctx.split_extra()
        ctx.progress(0.03, f"relion_refine: initial model, {k} class(es)")
        run_relion(ctx, args, on_line=Progress(ctx, total=iters, end=0.85))
        it, files = rl.iteration_files(ctx.job_dir)
        if not it or not files["model"].exists():
            raise JobError("relion_refine wrote no run_itNNN_model.star (see the log)")
        ctx.progress(0.88, f"relion_align_symmetry ({sym})")
        out = ctx.path("initial_model.mrc")
        run_relion(ctx, [ctx.program("relion", "relion_align_symmetry"), "--i", ctx.rel(files["model"]),
                         "--o", ctx.rel(out), "--sym", sym if p["run_c1"] and sym != "C1" else "C1",
                         "--apply_sym", "--select_largest_class"])
        if not out.exists():
            raise JobError("relion_align_symmetry wrote no initial_model.mrc (see the log)")
        infos = rl.model_classes(files["model"])
        counts, total = class_counts(files["data"]) if files["data"].exists() else ({}, 0)
        rows, maps = [], []
        for info in infos:
            c = info["class"]
            path = ctx.path(f"run_it{it:03d}_class{c:03d}.mrc")
            if not path.exists():
                continue
            maps.append(path)
            n = counts.get(c, 0)
            rows.append([c, f"{n:,}", f"{100 * n / max(total, 1):.1f} %",
                         f"{info['resolution']:.1f} Å" if info["resolution"] else "-"])
            if k > 1:
                ctx.add_output(f"map_{c}", "map", path, f"Class {c} ({100 * n / max(total, 1):.0f} %)",
                               meta={"symmetry": "C1", "relion_map": True, "class_number": c, "particles": n})
        ctx.add_table("Classes", ["Class", "Particles", "Share", "Estimated resolution"], rows)
        ctx.add_highlight("Symmetry", sym)
        ctx.add_output("map", "map", out, f"Initial model ({sym})", meta={"symmetry": sym, "relion_map": True})
        if k > 1 and maps:
            ctx.add_output("maps", "volume_series", maps, f"{len(maps)} class maps", meta={"frames": len(maps), "relion_map": True})
        if files["data"].exists():
            ctx.add_output("particles", "particles", files["data"], f"Particles ({total:,})",
                           meta=particles_out_meta(ctx, files["data"], "inimodel", optimiser=ctx.rel(files["optimiser"])))


# ------------------------------------------------------------------ 3D classification
@register
class RelionClass3DGlobal(RelionJob):
    name = "relion_class3d_global"
    title = "3D classification (RELION)"
    category = "Classification"
    description = ("Sorts the particles into 3D classes against a reference (relion_refine --K): global angular "
                   "searches, local ones, or none (classification with the poses kept). Every class gives a map and "
                   "its particles; Blush regularisation helps small or flexible particles.")
    inputs = [
        Slot("particles", ("particles",), "Particles"),
        Slot("map", ("map",), "Reference map", help="An initial model or a refined map: low-pass filtered by RELION.",
             prefer=("map",)),
        Slot("mask", ("mask",), "Mask", required=False, help="Soft mask around the particle (or a region of it)."),
    ]
    params = [
        Param("classes", "int", 4, label="Number of classes", min=1, max=100),
        Param("iterations", "int", 25, label="Iterations", min=1, max=500),
        Param("tau2_fudge", "float", 4.0, label="Regularisation parameter T", min=0.1, max=1000),
        Param("symmetry", "str", "C1", label="Symmetry"),
        Param("ini_high", "float", 60.0, label="Initial low-pass filter", unit="Å", min=0,
              help="RELION low-pass filters the reference strongly at first (40-60 Å for an initial model)."),
        Param("absolute_greyscale", "choice", "auto", choices=["auto", "yes", "no"], label="Reference on absolute greyscale",
              labels={"auto": "Auto (yes for a map made by RELION)", "yes": "Yes", "no": "No"},
              help="No: the first iteration uses cross-correlation (--firstiter_cc), right for a map from another "
                   "program. A map made by RELION from these particles is on RELION's absolute greyscale."),
        Param("alignment", "choice", "global", choices=["global", "local", "none"], label="Alignment",
              labels={"global": "Global angular searches", "local": "Local searches around the poses",
                      "none": "None (classification only, poses kept)"}),
        Param("angular_step", "choice", "7.5°", choices=SAMPLINGS[:7], label="Angular sampling"),
        Param("local_range", "float", 5.0, label="Local angular search range", unit="°", min=0.5, max=30),
        Param("relax_sym", "str", "", label="Relax symmetry", advanced=True, placeholder="e.g. C2"),
        Param("offset_range", "float", 5.0, label="Offset search range", unit="px", min=0, max=100),
        Param("offset_step", "float", 1.0, label="Offset search step", unit="px", min=0.1, max=10),
        Param("particle_diameter", "float", 0.0, label="Mask diameter", unit="Å", min=0, help="0 = 90 % of the box."),
        Param("zero_mask", "bool", True, label="Mask particles with zeros", advanced=True),
        Param("highres_limit", "float", 0.0, label="Limit E-step resolution", unit="Å", min=0, advanced=True),
        Param("fast_subsets", "bool", False, label="Fast subsets", advanced=True,
              help="Large data sets: the first iterations on random subsets."),
        Param("blush", "bool", False, label="Blush regularisation",
              help="RELION 5 neural-network prior (needs RELION's Python environment)."),
        Param("blush_model", "choice", "v1.0", choices=BLUSH_MODELS, label="Blush network", advanced=True,
              help="v1.0 (Kimanius et al. 2024); amy-v1.0 for amyloid filaments."),
        Param("allow_coarser", "bool", False, label="Allow coarser sampling", advanced=True),
        Param("pad1", "bool", False, label="Skip padding", advanced=True),
        *refine_compute_params(3),
        mpi(1),
        threads(8),
        gpus(1),
        extra_args_param(),
    ]
    outputs = [OutputDef("maps", "volume_series", "Class maps"), OutputDef("particles", "particles", "Classified particles"),
               OutputDef("map_1", "map", "Map of class 1"), OutputDef("class_1", "particles", "Particles of class 1")]

    def run(self, ctx: JobContext) -> None:
        p = ctx.params
        parts = relion_particles(ctx, ctx.require("particles"))
        ref = ctx.require("map")
        mask = ctx.input("mask")
        diameter = mask_diameter(ctx, parts)
        sym = (str(p["symmetry"]).strip() or "C1").upper()
        k, iters = int(p["classes"]), int(p["iterations"])
        args = [*relion_command(ctx, "relion_refine", p["mpi"]), "--o", ctx.rel(ctx.path("run")), "--i", parts["ref"],
                "--ref", ctx.rel(ref.path)]
        if not absolute_greyscale(ctx, ref):
            args.append("--firstiter_cc")
        args.append("--trust_ref_size")
        if p["ini_high"] > 0:
            args += ["--ini_high", f"{p['ini_high']:g}"]
        compute_params(p, args)
        args += ["--pad", "1" if p["pad1"] else "2", "--ctf", "--iter", str(iters), "--tau2_fudge", f"{p['tau2_fudge']:g}",
                 "--particle_diameter", f"{diameter:g}"]
        if p["fast_subsets"]:
            args.append("--fast_subsets")
        args += ["--K", str(k), "--flatten_solvent"]
        if p["zero_mask"]:
            args.append("--zero_mask")
        if p["highres_limit"] > 0:
            args += ["--strict_highres_exp", f"{p['highres_limit']:g}"]
        if p["blush"]:
            args += ["--blush", "--blush_model", p["blush_model"]]
        if mask:
            args += ["--solvent_mask", ctx.rel(mask.path)]
        if p["alignment"] == "none":
            args.append("--skip_align")
        else:
            args += ["--oversampling", "1", "--healpix_order", str(healpix(p["angular_step"]))]
            if p["alignment"] == "local":
                args += ["--sigma_ang", f"{p['local_range'] / 3:.4g}"]
                if str(p["relax_sym"]).strip():
                    args += ["--relax_sym", str(p["relax_sym"]).strip().upper()]
            args += ["--offset_range", f"{p['offset_range']:g}", "--offset_step", f"{2 * p['offset_step']:g}"]
            if p["allow_coarser"]:
                args.append("--allow_coarser_sampling")
        args += ["--sym", sym, "--norm", "--scale", "--j", str(p["threads"])]
        if p["gpus"] > 0:
            if p["alignment"] == "none":
                ctx.log("No GPU without alignment (RELION cannot use GPUs when skipping alignments)")
            else:
                args += ["--gpu", ""]
        args += ctx.split_extra()
        ctx.progress(0.03, f"relion_refine: {k} classes, {iters} iterations")
        run_relion(ctx, args, on_line=Progress(ctx, total=iters))
        it, files = rl.iteration_files(ctx.job_dir)
        if not it or not files["data"].exists():
            raise JobError("relion_refine wrote no run_itNNN_data.star (see the log)")
        class3d_outputs(ctx, files, it, k, sym)


def class3d_outputs(ctx: JobContext, files: dict[str, Path], it: int, k: int, sym: str) -> None:
    """Maps and particles of every 3D class, a table and the projections of the class maps side by side."""
    from cryoplug.imaging import write_png
    from cryoplug.micrographs import tile_sheet
    from cryoplug.mrc import projection_image
    tables = rl.read_star(files["data"])
    ptab = rl.particle_table(tables)
    classes = np.array([int(float(c)) for c in ptab.column("rlnClassNumber")])
    n = len(classes)
    infos = {i["class"]: i for i in rl.model_classes(files["model"])} if files["model"].exists() else {}
    maps, rows, tiles = [], [], []
    for c in range(1, k + 1):
        path = ctx.path(f"run_it{it:03d}_class{c:03d}.mrc")
        if not path.exists():
            continue
        maps.append(path)
        count = int((classes == c).sum())
        res = (infos.get(c) or {}).get("resolution")
        rows.append([c, f"{count:,}", f"{100 * count / max(n, 1):.1f} %", f"{res:.1f} Å" if res else "-"])
        try:
            tiles.append(projection_image(MapVolume.read(path), 120))
        except Exception:  # cosmetic
            pass
        if count:
            sel = rl.write_star(ctx.path(f"class_{c}.star"),
                                [t if t is not ptab else ptab.select(np.flatnonzero(classes == c)) for t in tables.values()],
                                comment=f"Particles of class {c} ({files['data'].name})")
            ctx.add_output(f"class_{c}", "particles", sel, f"Particles of class {c} ({count:,})",
                           meta=particles_out_meta(ctx, sel, "class3d", symmetry=sym, class_number=c))
        ctx.add_output(f"map_{c}", "map", path, f"Class {c} ({100 * count / max(n, 1):.0f} %)",
                       meta={"symmetry": sym, "relion_map": True, "class_number": c, "particles": count,
                             **({"resolution": round(res, 2)} if res else {})})
    if not maps:
        raise JobError("RELION wrote no class map (see the log)")
    ctx.add_table("Classes", ["Class", "Particles", "Share", "Estimated resolution"], rows)
    if tiles:
        png = write_png(ctx.path("classes.png"), tile_sheet([t if t.ndim == 2 else t[..., :3] for t in tiles], per_row=min(6, len(tiles))))
        ctx.add_image("Class maps (projections)", png)
    ctx.add_output("maps", "volume_series", maps, f"{len(maps)} class maps", meta={"symmetry": sym, "frames": len(maps)})
    ctx.add_output("particles", "particles", files["data"], f"Classified particles ({n:,})",
                   meta=particles_out_meta(ctx, files["data"], "class3d", symmetry=sym, optimiser=ctx.rel(files["optimiser"]),
                                           model=ctx.rel(files["model"])))
    ctx.add_highlight("Classes", str(len(maps)))

