"""RELION particle picking and extraction: relion_autopick (Laplacian-of-Gaussian, templates, Topaz), Topaz training,
relion_preprocess --extract (and re-extraction of refined particles, re-centred or re-scaled)."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np

from cryoplug import relion as rl
from cryoplug.jobs import register
from cryoplug.jobs.base import JobContext, JobError, OutputDef, Param, Slot, extra_args_param
from cryoplug.jobs.relion_common import (
    SAMPLINGS, FileProgress, RelionJob, histogram, micrograph_gallery, mpi, particles_out_meta, relion_command,
    relion_particles, run_relion, sample, star_of, thumbnail_of,
)


def topaz_exe(ctx: JobContext) -> str:
    """Topaz as RELION runs it: the given executable, else relion_python_topaz next to RELION."""
    given = str(ctx.params.get("topaz_exe") or "").strip()
    return given or ctx.program("relion", "relion_python_topaz")


def read_picks(ctx: JobContext, listing: Path) -> tuple[dict[str, np.ndarray], list[float]]:
    """Positions of the particles of every micrograph of a coordinate list, and all their figures of merit."""
    picks: dict[str, np.ndarray] = {}
    foms: list[float] = []
    for mic, coords in rl.coordinate_files(listing):
        path = ctx.abs(coords)
        if not path.exists():
            continue
        xy, fom = rl.read_coordinates(path)
        picks[mic] = xy
        if fom is not None:
            foms.extend(float(v) for v in fom)
    return picks, foms


def pick_report(ctx: JobContext, listing: Path, mics_star: Path, diameter_a: float, method: str) -> dict[str, Any]:
    """Plots and pictures of the picked particles; returns the metadata of the coordinates output."""
    picks, foms = read_picks(ctx, listing)
    tables = rl.read_star(mics_star)
    names = rl.micrograph_table(tables).column("rlnMicrographName")
    apix = rl.micrograph_pixel_size(tables)
    counts = [len(picks.get(m, ())) for m in names]
    total = int(sum(counts))
    if not total:
        raise JobError("No particle was picked (see the log; lower the threshold?)")
    x = list(range(1, len(names) + 1))
    ctx.add_plot("Particles per micrograph", [{"name": "Particles", "x": x, "y": counts}], x_label="Micrograph",
                 y_label="Particles")
    if foms:
        ctx.add_plot("Figure of merit of the picks", [{"name": "Particles", **histogram(np.array(foms))}],
                     x_label="Autopick figure of merit", y_label="Particles", style="bars",
                     note="Extraction can keep only the particles above a figure of merit (Topaz: e.g. 0 or -1).")
    ctx.add_table("Picking", ["", "Value"], [["Micrographs", f"{len(names):,}"], ["Particles", f"{total:,}"],
                                             ["Per micrograph", f"{total / max(1, len(names)):.0f} on average"],
                                             ["Method", method]])
    diameter_px = diameter_a / apix if apix and diameter_a else 0.0
    shown = sample([m for m in names if len(picks.get(m, ()))], 4)
    gallery = micrograph_gallery(ctx, shown, "picks", "Picked particles", picks=picks, diameter_px=diameter_px)
    thumb = None
    if gallery is not None and shown:
        from cryoplug.micrographs import picks_image
        try:
            thumb = thumbnail_of(ctx, picks_image(ctx.abs(shown[0]), picks[shown[0]], diameter_px, 256), "coordinates")
        except Exception as exc:  # cosmetic
            ctx.warn(f"No thumbnail: {exc}")
    ctx.add_highlight("Particles", f"{total:,}")
    return {"n_particles": total, "n_micrographs": len(names), "picker": method,
            "diameter": diameter_a or None, "pixel_size": round(apix, 4) if apix else None,
            "micrographs": ctx.rel(mics_star), "relion": {"star": ctx.rel(listing), "kind": "autopick"},
            "_thumbnail": thumb}


# ------------------------------------------------------------------ auto-picking
@register
class RelionAutopick(RelionJob):
    name = "relion_autopick"
    title = "Auto-picking (RELION)"
    category = "Picking"
    description = ("Picks particles on the micrographs with relion_autopick: Laplacian-of-Gaussian blob detection "
                   "(no template: the way to start a new data set), template matching against 2D class averages or "
                   "projections of a 3D map, or Topaz (a neural network, general or trained on this data set).")
    gpu_param = "gpus"
    cpu_param = "mpi"
    inputs = [
        Slot("micrographs", ("micrographs",), "Micrographs", help="With CTF estimation (needed by templates)."),
        Slot("references", ("class_averages",), "2D templates", required=False,
             help="Selected 2D class averages (Select 2D classes), for template matching."),
        Slot("map", ("map",), "3D template", required=False,
             help="A map made by RELION from this data set, projected into templates (instead of 2D classes)."),
        Slot("topaz_model", ("topaz_model",), "Topaz model", required=False,
             help="Trained by 'Topaz training'. Without it, Topaz uses its general model."),
    ]
    params = [
        Param("method", "choice", "log", choices=["log", "template", "topaz"], label="Method",
              labels={"log": "Laplacian-of-Gaussian (no template)", "template": "Templates (2D classes or a 3D map)",
                      "topaz": "Topaz (neural network)"}),
        Param("diameter_min", "float", 150.0, label="Minimum diameter", unit="Å", min=10,
              help="LoG: the smallest size of the particles."),
        Param("diameter_max", "float", 200.0, label="Maximum diameter", unit="Å", min=10,
              help="LoG: the largest size of the particles."),
        Param("log_threshold", "float", 0.0, label="LoG threshold adjustment", unit="σ", min=-5, max=5,
              help="LoG: lower picks more particles (and more junk), higher fewer."),
        Param("log_upper", "float", 0.0, label="LoG upper threshold", unit="σ", min=0, advanced=True,
              help="LoG: discards blobs this many σ above the average (ice, ethane drops; ~1.5 for low contrast). "
                   "0 = none."),
        Param("log_maxres", "float", 20.0, label="LoG maximum resolution", unit="Å", min=0, advanced=True,
              help="The micrographs are down-scaled to this resolution for the LoG filter."),
        Param("white_particles", "bool", False, label="White particles", advanced=True,
              help="LoG: particles brighter than the background (usual micrographs: no)."),
        Param("threshold", "float", 0.05, label="Picking threshold", min=0, max=1,
              help="Templates: lower picks more particles (and more junk)."),
        Param("min_distance", "float", 100.0, label="Minimum inter-particle distance", unit="Å", min=0,
              help="Templates: picks closer than this are one particle."),
        Param("max_stddev_noise", "float", 1.1, label="Maximum stddev noise", advanced=True,
              help="Templates: no picks where the background is noisier than this (carbon, contamination). "
                   "-1 = off."),
        Param("min_avg_noise", "float", -999.0, label="Minimum average noise", advanced=True,
              help="Templates: no picks where the background is darker than this (-0.5 to 0). -999 = off."),
        Param("lowpass", "float", 20.0, label="Low-pass filter of the templates", unit="Å", min=0, advanced=True),
        Param("highpass", "float", 0.0, label="High-pass filter of the micrographs", unit="Å", min=0, advanced=True,
              help="Removes ice gradients (200-400 Å). 0 = none."),
        Param("psi_step", "float", 5.0, label="In-plane angular sampling", unit="°", min=1, max=30, advanced=True),
        Param("invert_templates", "bool", True, label="Templates have inverted contrast", advanced=True,
              help="Yes for RELION class averages of particles extracted with inverted contrast."),
        Param("ctf_templates", "bool", True, label="Templates are CTF-corrected", advanced=True),
        Param("ignore_first_peak", "bool", False, label="Ignore CTFs until the first peak", advanced=True),
        Param("symmetry", "str", "C1", label="Symmetry of the 3D template", advanced=True),
        Param("sampling_3d", "choice", "30°", choices=SAMPLINGS[:4], label="Projections of the 3D template",
              advanced=True, help="Angular step between the projections used as templates."),
        Param("shrink", "float", 0.0, label="Shrink factor", min=0, max=1, advanced=True,
              help="Templates: micrographs down-scaled for speed (0 = to the low-pass filter)."),
        Param("particle_diameter", "float", 0.0, label="Particle diameter", unit="Å", min=0,
              help="Topaz (down-scaling and particle radius); also used to draw the picks. 0 = the LoG maximum."),
        Param("topaz_particles", "int", 0, label="Particles per micrograph (Topaz)", min=0, advanced=True,
              help="Expected number; 0 = Topaz's default."),
        Param("topaz_exe", "str", "", label="Topaz executable", advanced=True, admin_only=True,
              placeholder="relion_python_topaz", help="Default: relion_python_topaz, installed with RELION."),
        Param("topaz_args", "str", "", label="Other Topaz arguments", advanced=True, admin_only=True),
        mpi(4, help="Micrographs processed at the same time."),
        Param("gpus", "int", 0, label="GPUs", min=0, max=16,
              help="Templates and Topaz can use GPUs (not the LoG picker)."),
        extra_args_param(),
    ]
    outputs = [OutputDef("coordinates", "coordinates", "Picked particles")]

    @classmethod
    def validate(cls, params: dict[str, Any], connected: dict[str, str]) -> list[str]:
        method = params.get("method")
        if method == "template" and "references" not in connected and "map" not in connected:
            return ["Template matching needs 2D templates or a 3D template"]
        if method == "log" and float(params.get("diameter_min") or 0) > float(params.get("diameter_max") or 0):
            return ["The minimum diameter is larger than the maximum"]
        return []

    def run(self, ctx: JobContext) -> None:
        p = ctx.params
        mics_in = ctx.require("micrographs")
        star, ref = star_of(ctx, mics_in)
        n = rl.star_rows(star)
        method = p["method"]
        args = [*relion_command(ctx, "relion_autopick", p["mpi"]), "--fn_topaz_exe", topaz_exe(ctx), "--i", ref,
                "--odir", ctx.rel(ctx.job_dir) + "/", "--pickname", "autopick"]
        gpu = int(p["gpus"]) > 0
        diameter = float(p["particle_diameter"] or 0)
        if method == "topaz":
            model = ctx.input("topaz_model")
            diameter = diameter or float((model.meta.get("diameter") if model else 0) or 0)
            if not diameter > 0:  # RELION derives Topaz's down-scaling and particle radius from it
                raise JobError("Give the particle diameter (Topaz needs it)")
            args += ["--particle_diameter", f"{diameter:g}", "--topaz_extract"]
            if model:
                args += ["--topaz_model", ctx.rel(model.path)]
            if str(p["topaz_args"]).strip():
                args += ["--topaz_args", f" {p['topaz_args'].strip()} "]
            if gpu:
                args += ["--gpu", ""]
            label = "Topaz" + (f" ({model.source})" if model else " (general model)")
        elif method == "log":
            if gpu:
                ctx.log("The Laplacian-of-Gaussian picker does not use GPUs")
            args += ["--LoG", "--LoG_diam_min", f"{p['diameter_min']:g}", "--LoG_diam_max", f"{p['diameter_max']:g}",
                     "--shrink", "0", "--lowpass", f"{p['log_maxres']:g}", "--LoG_adjust_threshold", f"{p['log_threshold']:g}"]
            if p["log_upper"] > 0:
                args += ["--LoG_upper_threshold", f"{p['log_upper']:g}"]
            if p["white_particles"]:
                args.append("--Log_invert")
            diameter = diameter or float(p["diameter_max"])
            label = f"Laplacian-of-Gaussian ({p['diameter_min']:g}-{p['diameter_max']:g} Å)"
        else:
            refs, map3d = ctx.input("references"), ctx.input("map")
            if refs:
                args += ["--ref", ctx.rel(refs.path)]
                ref_apix = float(refs.meta.get("pixel_size") or 0)
                label = f"templates ({refs.source})"
            elif map3d:
                args += ["--ref", ctx.rel(map3d.path), "--sym", str(p["symmetry"]).strip() or "C1",
                         "--healpix_order", str(SAMPLINGS.index(p["sampling_3d"]) + 1)]
                ref_apix = float(map3d.meta.get("pixel_size") or 0)
                label = f"3D template ({map3d.source})"
            else:
                raise JobError("Template matching needs 2D templates or a 3D template")
            if p["invert_templates"]:
                args.append("--invert")
            if p["ctf_templates"]:
                args.append("--ctf")
                if p["ignore_first_peak"]:
                    args.append("--ctf_intact_first_peak")
            args += ["--ang", f"{p['psi_step']:g}", "--shrink", f"{p['shrink']:g}"]
            if p["lowpass"] > 0:
                args += ["--lowpass", f"{p['lowpass']:g}"]
            if p["highpass"] > 0:
                args += ["--highpass", f"{p['highpass']:g}"]
            if ref_apix:
                args += ["--angpix_ref", f"{ref_apix:g}"]
            args += ["--threshold", f"{p['threshold']:g}", "--min_distance", f"{p['min_distance']:g}",
                     "--max_stddev_noise", f"{p['max_stddev_noise']:g}"]
            if p["min_avg_noise"] > -900:
                args += ["--min_avg_noise", f"{p['min_avg_noise']:g}"]
            if gpu:
                args += ["--gpu", ""]
            diameter = diameter or float(p["min_distance"])
        args += ctx.split_extra()
        ctx.progress(0.03, f"relion_autopick ({method}) on {n:,} micrographs")
        with FileProgress(ctx, ctx.job_dir, "*_autopick.star", n, "Micrographs picked"):
            run_relion(ctx, args)
        listing = ctx.path("autopick.star")
        if not listing.exists():
            raise JobError("relion_autopick wrote no autopick.star (see the log)")
        meta = pick_report(ctx, listing, star, diameter, label)
        thumb = meta.pop("_thumbnail")
        ctx.add_output("coordinates", "coordinates", listing, f"{meta['n_particles']:,} particles picked", meta=meta,
                       thumbnail=thumb)


# ------------------------------------------------------------------ Topaz training
@register
class RelionTopazTrain(RelionJob):
    name = "relion_topaz_train"
    title = "Topaz training (RELION)"
    category = "Picking"
    description = ("Trains a Topaz picking network on this data set (relion_autopick --topaz_train): from particles "
                   "picked and cleaned once (the particles of good 2D classes) or from coordinates. Pick the whole "
                   "data set with the trained model next (Auto-picking, Topaz).")
    gpu_param = "gpus"
    cpu_param = ""
    inputs = [
        Slot("micrographs", ("micrographs",), "Micrographs", help="Micrographs with CTF (a few hundred are enough)."),
        Slot("particles", ("particles",), "Training particles", required=False,
             help="Good particles (from selected 2D classes): their positions train the network."),
        Slot("coordinates", ("coordinates",), "Training coordinates", required=False,
             help="Or picked coordinates (manually checked)."),
    ]
    params = [
        Param("particle_diameter", "float", 0.0, label="Particle diameter", unit="Å", min=0, required=True),
        Param("topaz_particles", "int", 200, label="Particles per micrograph", min=1,
              help="Expected average number on a micrograph (Topaz's -n): estimate it generously."),
        Param("topaz_exe", "str", "", label="Topaz executable", advanced=True, admin_only=True,
              placeholder="relion_python_topaz"),
        Param("topaz_args", "str", "", label="Other Topaz arguments", advanced=True, admin_only=True),
        Param("gpus", "int", 1, label="GPUs", min=1, max=8, help="Topaz training needs a GPU."),
        extra_args_param(),
    ]
    outputs = [OutputDef("topaz_model", "topaz_model", "Topaz model")]

    @classmethod
    def validate(cls, params: dict[str, Any], connected: dict[str, str]) -> list[str]:
        problems = []
        if "particles" not in connected and "coordinates" not in connected:
            problems.append("Connect training particles or coordinates")
        if not float(params.get("particle_diameter") or 0) > 0:
            problems.append("Give the particle diameter")
        return problems

    def run(self, ctx: JobContext) -> None:
        p = ctx.params
        star, ref = star_of(ctx, ctx.require("micrographs"))
        args = [*relion_command(ctx, "relion_autopick", 1), "--fn_topaz_exe", topaz_exe(ctx), "--i", ref,
                "--odir", ctx.rel(ctx.job_dir) + "/", "--pickname", "autopick",
                "--particle_diameter", f"{p['particle_diameter']:g}", "--topaz_train",
                "--topaz_nr_particles", str(p["topaz_particles"])]
        parts, coords = ctx.input("particles"), ctx.input("coordinates")
        if parts:
            pstar = relion_particles(ctx, parts)
            args += ["--topaz_train_parts", pstar["ref"]]
            source = f"{pstar['n']:,} particles of {parts.source}"
        elif coords:
            args += ["--topaz_train_picks", ctx.rel(coords.path)]
            source = f"coordinates of {coords.source}"
        else:
            raise JobError("Connect training particles or coordinates")
        if str(p["topaz_args"]).strip():
            args += ["--topaz_args", f" {p['topaz_args'].strip()} "]
        args += ["--gpu", "", *ctx.split_extra()]
        ctx.progress(0.03, f"Topaz training on {source}")
        run_relion(ctx, args)
        models = sorted(ctx.job_dir.glob("model_epoch*.sav"), key=lambda q: int(re.sub(r"\D", "", q.stem) or 0))
        if not models:
            raise JobError("Topaz wrote no model (model_epochN.sav; see the log and run.out)")
        best, history = models[-1], self.training_curve(ctx)
        meta: dict[str, Any] = {"diameter": p["particle_diameter"], "epochs": len(models), "trained_on": source}
        if history:
            epoch, auprc = max(history, key=lambda h: h[1])
            match = next((m for m in models if m.stem == f"model_epoch{epoch}"), None)
            if match:
                best = match
            meta.update({"epoch": epoch, "auprc": round(auprc, 3)})
            ctx.add_highlight("Test AUPRC", f"{auprc:.3f} (epoch {epoch})")
        ctx.add_output("topaz_model", "topaz_model", [best, *[m for m in models if m != best]],
                       f"Topaz model ({best.stem})", meta=meta)

    @staticmethod
    def training_curve(ctx: JobContext) -> list[tuple[int, float]]:
        """Test precision (AUPRC) per epoch from Topaz's model_training.txt."""
        path = ctx.path("model_training.txt")
        if not path.exists():
            return []
        lines = [ln.split("\t") for ln in path.read_text().splitlines() if ln.strip()]
        if not lines or "auprc" not in lines[0]:
            return []
        head = lines[0]
        i_epoch, i_split, i_auprc = head.index("epoch"), head.index("split"), head.index("auprc")
        best: dict[int, float] = {}
        for row in lines[1:]:
            if len(row) <= i_auprc or row[i_split] != "test":
                continue
            try:
                best[int(row[i_epoch])] = float(row[i_auprc])
            except ValueError:
                continue
        history = sorted(best.items())
        if history:
            ctx.add_plot("Topaz training", [{"name": "Test AUPRC", "x": [h[0] for h in history], "y": [h[1] for h in history]}],
                         x_label="Epoch", y_label="AUPRC")
        return history


# ------------------------------------------------------------------ extraction
@register
class RelionExtract(RelionJob):
    name = "relion_extract"
    title = "Particle extraction (RELION)"
    category = "Picking"
    description = ("Cuts the particles out of the micrographs (relion_preprocess --extract), with their CTF: box, "
                   "down-scaling, contrast inversion and normalisation as RELION expects. Also re-extracts refined "
                   "particles (re-centred on their refined positions, or at another pixel size).")
    gpu_param = ""
    cpu_param = "mpi"
    inputs = [
        Slot("micrographs", ("micrographs",), "Micrographs", help="With CTF estimation."),
        Slot("coordinates", ("coordinates",), "Coordinates", required=False, help="Picked particles."),
        Slot("particles", ("particles",), "Particles to re-extract", required=False,
             help="Instead of coordinates: refined particles extracted again (re-centred, other box or pixel size)."),
    ]
    params = [
        Param("box", "int", 0, label="Box size", unit="px", min=0, max=2048,
              help="In micrograph pixels (even). 0 = about twice the particle diameter of the picking."),
        Param("rescale", "int", 0, label="Re-scaled box", unit="px", min=0, max=2048,
              help="Down-scale the particles to this box (even): faster classification. 0 = keep the pixel size."),
        Param("invert_contrast", "bool", True, label="Invert contrast", help="White particles, as RELION expects."),
        Param("normalize", "bool", True, label="Normalize"),
        Param("bg_diameter", "float", -1.0, label="Background circle diameter", unit="px", advanced=True,
              help="Normalisation outside this circle (before re-scaling). Negative: 75 % of the box."),
        Param("white_dust", "float", -1.0, label="White dust removal", unit="σ", advanced=True, help="Negative: off."),
        Param("black_dust", "float", -1.0, label="Black dust removal", unit="σ", advanced=True, help="Negative: off."),
        Param("float16", "bool", True, label="Write in float16"),
        Param("min_fom", "float", -999.0, label="Minimum figure of merit", advanced=True,
              help="Only particles picked with this autopick figure of merit or more (Topaz: e.g. -1). -999 = all."),
        Param("recentre", "choice", "recentre", choices=["recentre", "keep", "reset"], label="Refined offsets",
              labels={"recentre": "Re-centre on the refined positions", "keep": "Keep the offsets",
                      "reset": "Reset the offsets to zero"},
              help="Re-extraction: re-centre the boxes on the refined particle centres (RELION's --recenter)."),
        Param("recentre_x", "float", 0.0, label="Re-centre on X", unit="px", advanced=True,
              help="Re-extraction: a point of the reference (pixels from its centre) to centre the boxes on."),
        Param("recentre_y", "float", 0.0, label="Re-centre on Y", unit="px", advanced=True),
        Param("recentre_z", "float", 0.0, label="Re-centre on Z", unit="px", advanced=True),
        mpi(4, help="Micrographs processed at the same time."),
        extra_args_param(),
    ]
    outputs = [OutputDef("particles", "particles", "Particles")]

    @classmethod
    def validate(cls, params: dict[str, Any], connected: dict[str, str]) -> list[str]:
        if ("coordinates" in connected) == ("particles" in connected):
            return ["Connect coordinates (extraction) or particles (re-extraction), one of the two"]
        box, scale = int(params.get("box") or 0), int(params.get("rescale") or 0)
        if box % 2 or scale % 2:
            return ["Box sizes must be even"]
        if box and scale > box:
            return ["The re-scaled box cannot be larger than the box"]
        return []

    def run(self, ctx: JobContext) -> None:
        p = ctx.params
        mics_in = ctx.require("micrographs")
        star, ref = star_of(ctx, mics_in)
        mtables = rl.read_star(star)
        n_mics = len(rl.micrograph_table(mtables))
        apix = rl.micrograph_pixel_size(mtables)
        coords, parts = ctx.input("coordinates"), ctx.input("particles")
        box = int(p["box"])
        if not box:
            diameter = float((coords.meta.get("diameter") if coords else 0) or 0)
            if not diameter and parts and parts.meta.get("box") and parts.meta.get("pixel_size"):
                diameter = 0.5 * parts.meta["box"] * parts.meta["pixel_size"]
            if not diameter or not apix:
                raise JobError("Give the box size (the particle diameter of the picking is unknown)")
            box = rl.good_box(2.0 * diameter / apix)
            box += box % 2
            ctx.log(f"Box: {box} px (about twice the {diameter:g} Å particle diameter, {apix:g} Å/px)")
        args = [*relion_command(ctx, "relion_preprocess", p["mpi"]), "--i", ref]
        if parts:
            pin = relion_particles(ctx, parts)
            args += ["--reextract_data_star", pin["ref"]]
            if p["recentre"] == "reset":
                args.append("--reset_offsets")
            elif p["recentre"] == "recentre":
                args += ["--recenter", "--recenter_x", f"{p['recentre_x']:g}", "--recenter_y", f"{p['recentre_y']:g}",
                         "--recenter_z", f"{p['recentre_z']:g}"]
            source = f"{pin['n']:,} particles of {parts.source}"
        else:
            args += ["--coord_list", ctx.rel(coords.path)]
            source = f"{int(coords.meta.get('n_particles') or 0):,} picks of {coords.source}"
        out = ctx.path("particles.star")
        args += ["--part_star", ctx.rel(out)]
        if parts:
            args += ["--pick_star", ctx.rel(ctx.path("extractpick.star"))]
        args += ["--part_dir", ctx.rel(ctx.job_dir) + "/", "--extract", "--extract_size", str(box)]
        if p["min_fom"] > -900:
            args += ["--minimum_pick_fom", f"{p['min_fom']:g}"]
        if p["float16"]:
            args.append("--float16")
        bg_radius = (0.75 * box if p["bg_diameter"] < 0 else p["bg_diameter"]) / 2
        scale = int(p["rescale"])
        if scale:
            args += ["--scale", str(scale)]
            bg_radius = bg_radius * scale / box
        if p["normalize"]:
            args += ["--norm", "--bg_radius", f"{int(bg_radius):d}", "--white_dust", f"{p['white_dust']:g}",
                     "--black_dust", f"{p['black_dust']:g}"]
        if p["invert_contrast"]:
            args.append("--invert_contrast")
        args += ctx.split_extra()
        ctx.add_table("Extraction", ["Setting", "Value"], [
            ["Source", source], ["Micrographs", f"{n_mics:,}"],
            ["Box", f"{box} px ({box * apix:.0f} Å)" if apix else f"{box} px"],
            ["Re-scaled", f"{scale} px ({apix * box / scale:.3f} Å/px)" if scale and apix else "no"]])
        ctx.progress(0.03, f"relion_preprocess: extracting from {n_mics:,} micrographs")
        with FileProgress(ctx, ctx.job_dir, "*_extract.star", n_mics, "Micrographs extracted"):
            run_relion(ctx, args)
        if not out.exists():
            raise JobError("relion_preprocess wrote no particles.star (see the log)")
        meta = particles_out_meta(ctx, out, "extract", micrographs=ctx.rel(star))
        if not meta["n_particles"]:
            raise JobError("No particle was extracted (see the log)")
        thumb = self.montage(ctx, out)
        ctx.add_highlight("Particles", f"{meta['n_particles']:,}")
        ctx.add_highlight("Box", f"{meta['box']} px · {meta['pixel_size']:g} Å/px")
        ctx.add_output("particles", "particles", out, f"{meta['n_particles']:,} particles", meta=meta, thumbnail=thumb)

    @staticmethod
    def montage(ctx: JobContext, star: Path) -> Path | None:
        """Some extracted particles side by side (report picture and job card thumbnail)."""
        from cryoplug.imaging import write_png
        from cryoplug.particles import ParticleImages, montage, particle_tile
        try:
            images = ParticleImages(star, ctx.project_dir)
            ids = sample(list(range(images.n)), 24)
            tiles = [particle_tile(images.image(i), 80) for i in ids]
            images.close()
        except Exception as exc:  # cosmetic
            ctx.warn(f"No particle pictures: {exc}")
            return None
        png = write_png(ctx.path("particles.png"), montage(tiles, per_row=8))
        ctx.add_image("Extracted particles", png)
        return write_png(ctx.path("thumb_particles.png"), montage(tiles[:9], per_row=3))
