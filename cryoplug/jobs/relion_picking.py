"""RELION particle picking and extraction: relion_autopick (Laplacian-of-Gaussian, templates, Topaz), Topaz training,
inspection and manual editing of the picks in the browser (as CryoSPARC's Inspect Picks and Manual Picker),
relion_preprocess --extract (and re-extraction of refined particles, re-centred or re-scaled)."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import numpy as np

from cryoplug import relion as rl
from cryoplug.jobs import register
from cryoplug.jobs.base import JobContext, JobError, JobType, OutputDef, Param, Slot, extra_args_param
from cryoplug.jobs.relion_common import (
    SAMPLINGS, FileProgress, RelionJob, helical_extract_args, helical_params, histogram, micrograph_gallery,
    micrograph_meta, mpi, particles_out_meta, relion_command, relion_particles, run_relion, sample, star_of,
    thumbnail_of,
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


POWER_SIZE = 1024  # micrographs are binned to about this size to measure the local power of the picks


def local_power(path: Path, xy: np.ndarray, diameter_px: float, apix: float, lowpass: float = 20.0) -> np.ndarray:
    """Local power of each pick, as CryoSPARC's power score: mean squared intensity (the micrograph binned, low-pass
    filtered and normalised) in a box of the particle's size around the pick. Ice crystals, carbon and contamination
    are high, empty ice is low."""
    from scipy import ndimage

    from cryoplug.micrographs import read_image
    img = np.asarray(read_image(path), dtype=np.float32)
    factor = max(1, int(np.ceil(max(img.shape) / POWER_SIZE)))
    h, w = (img.shape[0] // factor) * factor, (img.shape[1] // factor) * factor
    if factor > 1:
        img = img[:h, :w].reshape(h // factor, factor, w // factor, factor).mean(axis=(1, 3))
    if apix > 0 and lowpass > 0:
        img = ndimage.gaussian_filter(img, max(0.5, np.sqrt(np.log(2) / 2) / np.pi * lowpass / (apix * factor)))
    z = (img - np.median(img)) / (float(img.std()) or 1.0)
    sat = np.pad((z * z).cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    r = max(1, int(round(diameter_px / factor / 2)))
    cx, cy = np.round(xy[:, 0] / factor).astype(int), np.round(xy[:, 1] / factor).astype(int)
    hh, ww = z.shape
    x0, x1 = np.clip(cx - r, 0, ww), np.clip(cx + r + 1, 0, ww)
    y0, y1 = np.clip(cy - r, 0, hh), np.clip(cy + r + 1, 0, hh)
    area = np.maximum(1, (x1 - x0) * (y1 - y0))
    return (sat[y1, x1] - sat[y0, x1] - sat[y1, x0] + sat[y0, x0]) / area


def picks_data(ctx: JobContext, mics_star: Path, listing: Path | None, name: str = "picks",
               subset: list[str] | None = None, power_diameter_px: float = 0.0) -> tuple[Path, dict[str, Any]]:
    """The picks of every micrograph for the browser: ``<name>.bin`` (float32 x, y, figure of merit and, with
    ``power_diameter_px``, local power of each pick, micrograph after micrograph) and ``<name>.json`` (micrographs:
    name, shape, offset and number of their picks; the values per pick, ``stride``)."""
    from cryoplug.micrographs import micrograph_shape
    tables = rl.read_star(mics_star)
    names = subset if subset is not None else rl.micrograph_table(tables).column("rlnMicrographName")
    picks: dict[str, tuple[np.ndarray, np.ndarray | None]] = {}
    if listing is not None:
        for mic, coords in rl.coordinate_files(listing):
            path = ctx.abs(coords)
            if path.exists():
                picks[mic] = rl.read_coordinates(path)
    stride = 4 if power_diameter_px > 0 else 3
    apix = rl.micrograph_pixel_size(tables)
    entries, chunks, offset, has_fom, missing = [], [], 0, False, 0
    for mic in names:
        try:
            shape = micrograph_shape(ctx.abs(mic))
        except (OSError, ValueError):
            missing += 1
            continue
        xy, fom = picks.get(mic, (np.zeros((0, 2)), None))
        has_fom = has_fom or (fom is not None and len(xy) > 0)
        block = np.zeros((len(xy), stride), dtype="<f4")
        block[:, :2] = xy
        block[:, 2] = fom if fom is not None else 0.0
        chunks.append(block)
        entries.append({"name": mic, "shape": list(shape), "offset": offset, "count": len(xy)})
        offset += len(xy)
    if missing:
        ctx.warn(f"{missing} micrograph(s) could not be read and are left out")
    if stride == 4 and offset:  # local power of the picks, micrographs read in parallel
        from concurrent.futures import ThreadPoolExecutor
        todo = [(e, chunks[i]) for i, e in enumerate(entries) if e["count"]]

        def measure(item: tuple[dict[str, Any], np.ndarray]) -> None:
            entry, block = item
            try:
                block[:, 3] = local_power(ctx.abs(entry["name"]), block[:, :2].astype(np.float64), power_diameter_px, apix)
            except Exception as exc:  # unreadable: no power score for its picks
                block[:, 3] = np.nan
                ctx.warn(f"No power score for {entry['name']}: {exc}")
        done = 0
        with ThreadPoolExecutor(max_workers=8) as pool:
            for _ in pool.map(measure, todo):
                done += 1
                if done % 50 == 0:
                    ctx.progress(0.2 + 0.7 * done / len(todo), f"Power scores: {done:,} of {len(todo):,} micrographs", log=False)
    binary = ctx.path(f"{name}.bin")
    binary.write_bytes((np.concatenate(chunks) if chunks else np.zeros((0, stride), "<f4")).astype("<f4").tobytes())
    allfom = np.concatenate([c[:, 2] for c in chunks]) if chunks and offset else np.zeros(0)
    info = {"micrographs": entries, "binary": ctx.rel(binary), "apix": round(apix, 4), "stride": stride,
            "has_fom": has_fom, "n_picks": int(offset),
            "fom_range": [round(float(allfom.min()), 4), round(float(allfom.max()), 4)] if has_fom and len(allfom) else None,
            "pick_type": rl.coordinate_pick_type(listing) if listing is not None else "particles"}
    if stride == 4 and offset:
        power = np.concatenate([c[:, 3] for c in chunks])
        power = power[np.isfinite(power)]
        info["has_power"] = bool(len(power))
        if len(power):
            info["power_range"] = [round(float(power.min()), 4), round(float(power.max()), 4)]
    index = ctx.path(f"{name}.json")
    index.write_text(json.dumps(info, separators=(",", ":")))
    return index, info


def filament_report(ctx: JobContext, listing: Path, mics_star: Path, diameter_a: float, method: str) -> dict[str, Any]:
    """Filaments picked by their start and end (RELION's "startend" coordinates): counts, lengths, browser."""
    picks, _ = read_picks(ctx, listing)
    tables = rl.read_star(mics_star)
    names = rl.micrograph_table(tables).column("rlnMicrographName")
    apix = rl.micrograph_pixel_size(tables) or 1.0
    counts, lengths = [], []
    for m in names:
        xy = picks.get(m, np.zeros((0, 2)))
        n = len(xy) // 2
        counts.append(n)
        if n:
            lengths.extend((np.hypot(*(xy[1:2 * n:2] - xy[0:2 * n:2]).T) * apix).tolist())
    total = int(sum(counts))
    if not total:
        raise JobError("No filament was picked")
    ctx.add_plot("Filaments per micrograph", [{"name": "Filaments", "x": list(range(1, len(names) + 1)), "y": counts}],
                 x_label="Micrograph", y_label="Filaments")
    ctx.add_plot("Length of the filaments", [{"name": "Filaments", **histogram(np.array(lengths), lo=0.0)}],
                 x_label="Length (Å)", y_label="Filaments", style="bars")
    ctx.add_table("Filaments", ["", "Value"], [["Micrographs", f"{len(names):,}"], ["Filaments", f"{total:,}"],
                                               ["Total length", f"{sum(lengths) / 1e4:.2f} µm"],
                                               ["Median length", f"{np.median(lengths):.0f} Å"], ["Method", method]])
    ctx.add_highlight("Filaments", f"{total:,}")
    try:
        index, _ = picks_data(ctx, mics_star, listing, subset=[m for m in names if m in picks])
        ctx.add_tool("picks", "Picked filaments", index, diameter_px=round(diameter_a / apix, 2), editable=False, mode="filaments")
    except (OSError, ValueError) as exc:  # cosmetic
        ctx.warn(f"No pick browser: {exc}")
    return {"n_filaments": total, "filament_length": round(float(sum(lengths)), 1), "pick_type": "startend",
            "n_micrographs": len(names), "picker": method, "diameter": diameter_a or None, "pixel_size": round(apix, 4),
            "micrographs": ctx.rel(mics_star), "relion": {"star": ctx.rel(listing), "kind": "manualpick"}, "_thumbnail": None}


def pick_report(ctx: JobContext, listing: Path, mics_star: Path, diameter_a: float, method: str) -> dict[str, Any]:
    """Plots and pictures of the picked particles; returns the metadata of the coordinates output."""
    if rl.coordinate_pick_type(listing) == "startend":
        return filament_report(ctx, listing, mics_star, diameter_a, method)
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
    try:  # browse the picks micrograph by micrograph in the job panel
        index, _ = picks_data(ctx, mics_star, listing, subset=[m for m in names if m in picks])
        ctx.add_tool("picks", "Picked particles", index, diameter_px=round(diameter_px, 2), editable=False)
    except (OSError, ValueError) as exc:  # cosmetic
        ctx.warn(f"No pick browser: {exc}")
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
        *helical_params("autopick"),
        Param("topaz_filaments", "bool", False, label="Topaz: pick filaments", advanced=True,
              help="Topaz picks along filaments (RELION's --helix for Topaz)."),
        Param("topaz_filament_threshold", "float", -5.0, label="Topaz filament threshold", advanced=True,
              show_if="topaz_filaments"),
        Param("topaz_hough_length", "float", -1.0, label="Topaz minimum filament length", unit="Å", advanced=True,
              show_if="topaz_filaments", help="-1 = none."),
        Param("gpus", "int", 0, label="GPUs", min=0, max=16,
              help="Templates and Topaz can use GPUs (not the LoG picker)."),
        extra_args_param(),
    ]
    outputs = [OutputDef("coordinates", "coordinates", "Picked particles")]

    @classmethod
    def validate(cls, params: dict[str, Any], connected: dict[str, str]) -> list[str]:
        method = params.get("method")
        if params.get("helical") and method != "template":
            return ["Helical segments are picked by template matching (Topaz: 'pick filaments')"]
        if params.get("helical") and not (float(params.get("helical_rise") or 0) > 0 and float(params.get("helical_outer_diameter") or 0) > 0):
            return ["Helical picking: give the tube diameter and the rise"]
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
            if p["topaz_filaments"]:
                args += ["--helix", "--topaz_threshold", f"{p['topaz_filament_threshold']:g}"]
                if p["topaz_hough_length"] > 0:
                    args += ["--helical_tube_length_min", f"{p['topaz_hough_length']:g}"]
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
            # segments of filaments: one every nr_asu × rise along them (RELION's GUI)
            min_distance = p["helical_nr_asu"] * p["helical_rise"] if p["helical"] else p["min_distance"]
            args += ["--threshold", f"{p['threshold']:g}", "--min_distance", f"{min_distance:g}",
                     "--max_stddev_noise", f"{p['max_stddev_noise']:g}"]
            if p["min_avg_noise"] > -900:
                args += ["--min_avg_noise", f"{p['min_avg_noise']:g}"]
            if p["helical"]:
                args.append("--helix")
                if p["helical_amyloid"]:
                    args.append("--amyloid")
                args += ["--helical_tube_outer_diameter", f"{p['helical_outer_diameter']:g}",
                         "--helical_tube_kappa_max", f"{p['helical_kappa_max']:g}",
                         "--helical_tube_length_min", f"{p['helical_length_min']:g}"]
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
        if mics_in.meta.get("name_map"):  # picked on denoised micrographs: extracted from the originals
            meta["name_map"] = mics_in.meta["name_map"]
        ctx.add_output("coordinates", "coordinates", listing, f"{meta['n_particles']:,} particles picked", meta=meta,
                       thumbnail=thumb)


# ------------------------------------------------------------------ imports of picks and templates
COORD_SUFFIXES = re.compile(r"(_(autopick|manualpick|coords?|picks?|particles|extract|cryolo|topaz))+$", re.I)


def _star_picks(path: Path) -> dict[str, list[tuple[float, float, float]]]:
    """Picks of a STAR file: a coordinate file of one micrograph (key: its file name), RELION's list of coordinate
    files (autopick.star), or particles with their micrograph (rlnMicrographName)."""
    tables = rl.read_star(path)
    listing = tables.get("coordinate_files")
    if listing is not None and listing.has("rlnMicrographCoordinates"):
        out: dict[str, list[tuple[float, float, float]]] = {}
        for mic, coords in zip(listing.column("rlnMicrographName"), listing.column("rlnMicrographCoordinates")):
            for base in (path.parent, path.parent.parent, path.parent.parent.parent):
                if (base / coords).is_file():
                    xy, fom = rl.read_coordinates(base / coords)
                    f = fom if fom is not None else np.zeros(len(xy))
                    out[Path(mic).name] = [(float(x), float(y), float(s)) for (x, y), s in zip(xy, f)]
                    break
        return out
    table = next((tb for tb in tables.values() if tb.loop and tb.has("rlnCoordinateX") and tb.has("rlnCoordinateY")), None)
    if table is None:
        return {}
    xs, ys = table.floats("rlnCoordinateX"), table.floats("rlnCoordinateY")
    fom = table.floats("rlnAutopickFigureOfMerit") if table.has("rlnAutopickFigureOfMerit") else np.zeros(len(xs))
    if table.has("rlnMicrographName"):
        out = {}
        for mic, x, y, s in zip(table.column("rlnMicrographName"), xs, ys, fom):
            out.setdefault(Path(mic).name, []).append((float(x), float(y), float(s)))
        return out
    return {path.name: [(float(x), float(y), float(s)) for x, y, s in zip(xs, ys, fom)]}


def _box_picks(path: Path) -> dict[str, list[tuple[float, float, float]]]:
    """EMAN / crYOLO .box: x y (corner of the box) width height [score] per line; the centre is kept."""
    picks = []
    for line in path.read_text().splitlines():
        parts = line.replace(",", " ").split()
        if len(parts) < 4:
            continue
        try:
            x, y, w, hgt = (float(v) for v in parts[:4])
        except ValueError:
            continue
        score = float(parts[4]) if len(parts) > 4 and parts[4].replace(".", "", 1).replace("-", "", 1).isdigit() else 0.0
        picks.append((x + w / 2, y + hgt / 2, score))
    return {path.name: picks}


def _topaz_picks(path: Path) -> dict[str, list[tuple[float, float, float]]]:
    """Topaz extract: image_name x_coord y_coord score (tab-separated, a header line)."""
    out: dict[str, list[tuple[float, float, float]]] = {}
    lines = [ln.split() for ln in path.read_text().splitlines() if ln.strip()]
    if not lines:
        return out
    head = [h.lower() for h in lines[0]]
    start = 1 if "x_coord" in head else 0
    ix, iy = (head.index("x_coord"), head.index("y_coord")) if start else (1, 2)
    iname = head.index("image_name") if start and "image_name" in head else 0
    iscore = head.index("score") if start and "score" in head else 3
    for parts in lines[start:]:
        try:
            score = float(parts[iscore]) if len(parts) > iscore else 0.0
            out.setdefault(parts[iname], []).append((float(parts[ix]), float(parts[iy]), score))
        except (ValueError, IndexError):
            continue
    return out


def _cs_picks(path: Path) -> dict[str, list[tuple[float, float, float]]]:
    """CryoSPARC picks (.cs): fractional centres on each micrograph, its shape (rows, columns)."""
    from cryoplug.particles import read_cs
    data = read_cs(path)
    names = data.dtype.names or ()
    need = ("location/micrograph_path", "location/center_x_frac", "location/center_y_frac", "location/micrograph_shape")
    if not all(n in names for n in need):
        raise JobError(f"{path.name} has no pick locations (location/center_x_frac, ...): export the picks of a picking job")
    score_field = next((f for f in ("pick_stats/ncc_score", "pick_stats/power", "location/score") if f in names), None)
    out: dict[str, list[tuple[float, float, float]]] = {}
    for i in range(len(data)):
        mic = data["location/micrograph_path"][i]
        mic = mic.decode() if isinstance(mic, bytes) else str(mic)
        rows, cols = (int(v) for v in data["location/micrograph_shape"][i])
        score = float(data[score_field][i]) if score_field else 0.0
        out.setdefault(Path(mic).name, []).append((float(data["location/center_x_frac"][i]) * cols,
                                                   float(data["location/center_y_frac"][i]) * rows, score))
    return out


def given_files(value: str) -> list[Path]:
    """The files of a parameter of paths or wildcards separated by ';', in the order given (each wildcard sorted)."""
    import glob as globmod
    from cryoplug.jobs.base import split_paths
    found = []
    for pattern in split_paths(value):
        pattern = str(Path(pattern).expanduser())
        found += sorted(globmod.glob(pattern)) or [pattern]
    return [Path(f) for f in dict.fromkeys(found)]


def read_pick_files(paths: list[Path], fmt: str = "auto") -> dict[str, list[tuple[float, float, float]]]:
    """Picks of coordinate files of other programs, by the name of the file or micrograph they belong to."""
    picks: dict[str, list[tuple[float, float, float]]] = {}
    for path in paths:
        kind = fmt if fmt != "auto" else {".star": "star", ".box": "box", ".cs": "cs"}.get(path.suffix.lower(), "topaz")
        reader = {"star": _star_picks, "box": _box_picks, "topaz": _topaz_picks, "cs": _cs_picks}[kind]
        for key, values in reader(path).items():
            picks.setdefault(key, []).extend(values)
    return picks


def match_micrographs(keys: list[str], mic_names: list[str]) -> dict[str, str]:
    """Coordinate files (or micrograph names) matched to the micrographs of the project by file name: the same stem,
    or the stem followed by a picking suffix (mic_001_autopick.star, mic_001.box)."""
    stems = {Path(m).name.rsplit(".", 1)[0]: m for m in mic_names}
    ordered = sorted(stems, key=len, reverse=True)
    found: dict[str, str] = {}
    for key in keys:
        stem = Path(key).name
        stem = stem.rsplit(".", 1)[0] if "." in stem else stem
        stem = COORD_SUFFIXES.sub("", stem)
        if stem in stems:
            found[key] = stems[stem]
            continue
        hit = next((s for s in ordered if stem.startswith(s) or (s.endswith(stem) and len(stem) >= 4)), None)
        if hit:
            found[key] = stems[hit]
    return found


@register
class ImportCoordinates(JobType):
    name = "import_coordinates"
    title = "Import particle coordinates"
    category = "Import"
    software = ["CryoPlug"]
    description = ("Particle positions picked by another program, matched to the micrographs of the project by file name, "
                   "as RELION's import of coordinates: RELION or crYOLO STAR files (one per micrograph, an autopick.star "
                   "list or a particle file), EMAN or crYOLO .box files, Topaz picks (.txt) or CryoSPARC picks (.cs). "
                   "They feed the extraction, the inspection of picks or Topaz training.")
    inputs = [Slot("micrographs", ("micrographs",), "Micrographs", help="Those the particles were picked on (matched by name).")]
    params = [
        Param("path", "path", "", label="Coordinate files", required=True, path_kind="files",
              placeholder="/data/picks/*.star  (or *.box, picks.txt, picked_particles.cs)",
              help="Files or wildcards, separated by ';'."),
        Param("format", "choice", "auto", choices=["auto", "star", "box", "topaz", "cs"], label="Format",
              labels={"auto": "From the extension", "star": "STAR (RELION, crYOLO)", "box": "BOX (EMAN, crYOLO)",
                      "topaz": "Topaz (.txt)", "cs": "CryoSPARC (.cs)"}),
        Param("scale", "float", 1.0, label="Coordinate scale", min=0.001, max=1000,
              help="Multiplies the coordinates: e.g. 4 for picks made on micrographs binned 4 times."),
        Param("diameter", "float", 0.0, label="Particle diameter", unit="Å", min=0,
              help="Only for the circles on the pictures (0: none)."),
        Param("flip_y", "bool", False, label="Count y from the other edge", advanced=True,
              help="For coordinates whose y axis starts at the opposite edge of the micrograph."),
    ]
    outputs = [OutputDef("coordinates", "coordinates", "Imported coordinates")]

    def run(self, ctx: JobContext) -> None:
        p = ctx.params
        mics_in = ctx.require("micrographs")
        star, _ = star_of(ctx, mics_in)
        mic_names = rl.micrograph_table(rl.read_star(star)).column("rlnMicrographName")
        files = [f for f in given_files(p["path"]) if f.is_file()]
        if not files:
            raise JobError(f"No coordinate file found for {p['path']}")
        ctx.progress(0.1, f"Reading {len(files):,} coordinate file{'s' if len(files) > 1 else ''}")
        picks = read_pick_files(files, p["format"])
        matched = match_micrographs(list(picks), mic_names)
        unmatched = [k for k in picks if k not in matched]
        if not matched:
            raise JobError(f"No coordinate file matches a micrograph by name (e.g. {', '.join(list(picks)[:3])} against "
                           f"{', '.join(Path(m).name for m in mic_names[:3])})")
        out_dir = ctx.path("Coordinates")
        out_dir.mkdir(exist_ok=True)
        merged: dict[str, list[tuple[float, float, float]]] = {}
        for key, mic in matched.items():
            merged.setdefault(mic, []).extend(picks[key])
        pairs, total = [], 0
        scale = float(p["scale"])
        for mic in mic_names:
            values = merged.get(mic)
            if not values:
                continue
            arr = np.array(values, dtype=np.float64)
            xy = arr[:, :2] * scale
            if p["flip_y"]:
                from cryoplug.mrc import MapVolume
                xy[:, 1] = MapVolume.read(ctx.abs(mic), header_only=True).shape_xyz[1] - xy[:, 1]
            name = out_dir / (re.sub(r"\.[A-Za-z0-9]+$", "", Path(rl.pipeline_post(mic)).name) + "_coords.star")
            rl.write_coordinates(name, xy, arr[:, 2])
            pairs.append((mic, ctx.rel(name)))
            total += len(xy)
        listing = rl.write_coordinate_list(ctx.path("coords.star"), pairs)
        if unmatched:
            ctx.warn(f"{len(unmatched)} coordinate set(s) match no micrograph: {', '.join(unmatched[:5])}"
                     + (" ..." if len(unmatched) > 5 else ""))
        ctx.add_table("Import", ["", "Value"], [["Files read", f"{len(files):,}"], ["Micrographs with picks", f"{len(pairs):,} of {len(mic_names):,}"],
                                               ["Not matched", f"{len(unmatched):,}"], ["Scale", f"{scale:g}"]])
        meta = pick_report(ctx, listing, star, float(p["diameter"] or 0), "imported")
        thumb = meta.pop("_thumbnail")
        ctx.add_output("coordinates", "coordinates", listing, f"{total:,} imported picks", meta=meta, thumbnail=thumb)


@register
class ImportTemplates(JobType):
    name = "import_templates"
    title = "Import 2D templates"
    category = "Import"
    software = ["CryoPlug"]
    description = ("2D images to pick particles by template matching (Auto-picking, Templates), as CryoSPARC's Import "
                   "Templates: class averages from another program or project, projections, drawn shapes; a stack "
                   "(.mrcs) or single images (.mrc).")
    inputs: list[Slot] = []
    params = [
        Param("path", "path", "", label="Templates", required=True, path_kind="files",
              placeholder="/data/templates.mrcs  or  /data/refs/*.mrc", help="Files or wildcards, separated by ';'."),
        Param("pixel_size", "float", 0.0, label="Pixel size", unit="Å", min=0.0, max=50,
              help="0 = from the header of the first file."),
        Param("invert", "bool", False, label="Invert the contrast",
              help="RELION's templates have white particles on a dark background (like class averages)."),
    ]
    outputs = [OutputDef("classes", "class_averages", "2D templates")]

    def run(self, ctx: JobContext) -> None:
        import mrcfile
        from cryoplug.imaging import write_png
        from cryoplug.micrographs import tile_sheet
        p = ctx.params
        files = [f for f in given_files(p["path"]) if f.is_file()]
        if not files:
            raise JobError(f"No template file found for {p['path']}")
        images, apix = [], float(p["pixel_size"] or 0)
        for f in files:
            with mrcfile.open(str(f), permissive=True) as mrc:
                data = np.asarray(mrc.data, dtype=np.float32)
                if not apix:
                    apix = float(mrc.voxel_size.x) or 0.0
            images.extend(data if data.ndim == 3 else [data])
        shapes = {im.shape for im in images}
        if len(shapes) != 1:
            raise JobError(f"The templates have different sizes: {', '.join('x'.join(map(str, s)) for s in sorted(shapes))}")
        box = images[0].shape
        if box[0] != box[1]:
            raise JobError(f"Templates must be square ({box[1]} x {box[0]})")
        if apix <= 0:
            raise JobError("The pixel size is unknown (header 0): set it")
        stack = np.stack(images)
        if p["invert"]:
            stack = -stack
        out = ctx.path("templates.mrcs")
        with mrcfile.new(str(out), overwrite=True) as mrc:
            mrc.set_data(stack.astype(np.float32))
            mrc.voxel_size = apix
        tiles = []
        for im in stack[:24]:
            lo, hi = np.percentile(im, [1, 99.5])
            tiles.append((np.clip((im - lo) / ((hi - lo) or 1.0), 0, 1) * 255).astype(np.uint8))
        png = write_png(ctx.path("templates.png"), tile_sheet(tiles, per_row=8))
        ctx.add_image(f"Templates ({len(stack)})", png)
        ctx.add_highlight("Templates", str(len(stack)))
        ctx.add_output("classes", "class_averages", out, f"{len(stack)} templates",
                       meta={"pixel_size": round(apix, 4), "box": int(box[0]), "n_classes": len(stack), "templates": True},
                       thumbnail=png)


# ------------------------------------------------------------------ inspection and manual picking
@register
class RelionPickInspect(JobType):
    name = "relion_pick_inspect"
    title = "Inspect picks / manual picking"
    category = "Picking"
    interactive = True
    software = ["RELION"]
    description = ("Shows every micrograph with its picked particles, as CryoSPARC's Inspect Picks and Manual Picker: "
                   "a figure-of-merit threshold set on its histogram, particles removed or added with a click, zoom, pan "
                   "and low-pass filter. Without picks, particles are picked by hand (a training set for Topaz). Writes "
                   "the coordinate files RELION's extraction reads.")
    inputs = [
        Slot("micrographs", ("micrographs",), "Micrographs", help="Those the picks were made on."),
        Slot("coordinates", ("coordinates",), "Picks", required=False,
             help="Picks of an auto-picking job. Not connected: manual picking from scratch."),
    ]
    params = [
        Param("mode", "choice", "particles", choices=["particles", "filaments"], label="Picking",
              labels={"particles": "Particles", "filaments": "Filaments (start and end of each, for helical extraction)"},
              help="Filaments: the start and the end of each filament are clicked, as RELION's manual picking of helices."),
        Param("diameter", "float", 0.0, label="Particle diameter", unit="Å", min=0,
              help="Size of the circles (filaments: the tube diameter). 0 = that of the picking (required for manual "
                   "picking)."),
        Param("min_fom", "float", -999.0, label="Starting threshold",
              help="Figure of merit below which picks start hidden (set it on the histogram afterwards). -999 = all."),
        Param("power_score", "bool", True, label="Power score of the picks", show_if="mode=particles",
              help="Local power around each pick (as CryoSPARC's Inspect Picks): its range, set on a second histogram, "
                   "leaves out ice crystals, carbon and contamination (high) and empty ice (low)."),
        Param("lowpass", "float", 20.0, label="Low-pass filter of the display", unit="Å", min=0, max=200,
              help="The micrographs are shown filtered to this resolution (changeable in the viewer)."),
        Param("max_micrographs", "int", 0, label="Micrographs shown", min=0,
              help="0 = all. Manual picking of a training set: e.g. 30-50 micrographs spread over the data set."),
    ]
    outputs = [OutputDef("coordinates", "coordinates", "Picks")]

    @classmethod
    def validate(cls, params: dict[str, Any], connected: dict[str, str]) -> list[str]:
        if params.get("mode") == "filaments":
            if "coordinates" in connected:
                return ["Filaments are picked by hand: leave the picks out"]
            if not float(params.get("diameter") or 0) > 0:
                return ["Give the tube diameter of the filaments"]
            return []
        if "coordinates" not in connected and not float(params.get("diameter") or 0) > 0:
            return ["Give the particle diameter (manual picking)"]
        return []

    def prepare(self, ctx: JobContext) -> bool:
        p = ctx.params
        mics_in = ctx.require("micrographs")
        star, _ = star_of(ctx, mics_in)
        coords = ctx.input("coordinates")
        names = rl.micrograph_table(rl.read_star(star)).column("rlnMicrographName")
        if coords:  # micrographs with picks only (those the picker left out stay out)
            picked = {m for m, _ in rl.coordinate_files(coords.path)}
            names = [m for m in names if m in picked] or names
        if p["max_micrographs"] > 0:
            names = sample(names, int(p["max_micrographs"]))
        diameter = float(p["diameter"] or (coords.meta.get("diameter") if coords else 0) or 0)
        if not diameter > 0:
            raise JobError("Give the particle diameter")
        filaments = p["mode"] == "filaments"
        apix = rl.micrograph_pixel_size(rl.read_star(star)) or 1.0
        power = coords is not None and not filaments and bool(p["power_score"])
        ctx.progress(0.2, f"Reading the picks of {len(names):,} micrographs" + (" and their power" if power else ""))
        index, info = picks_data(ctx, star, Path(coords.path) if coords else None, subset=names,
                                 power_diameter_px=diameter / apix if power else 0.0)
        if not info["micrographs"]:
            raise JobError("None of the micrographs could be read")
        state = {"star": ctx.rel(star), "coordinates": ctx.rel(coords.path) if coords else None, "diameter": diameter,
                 "index": ctx.rel(index), "mode": p["mode"]}
        ctx.path("inspect.json").write_text(json.dumps(state, indent=1))
        manual = coords is None
        if filaments:
            title, prompt_title = "Filament picking", "Waiting for your filaments"
            prompt = "Click the start then the end of each filament (click on a filament to remove it), then Done."
        elif manual:
            title, prompt_title = "Manual picking", "Waiting for your picks"
            prompt = "Click the particles on each micrograph (click again to remove), then Done."
        else:
            title, prompt_title = "Inspect picks", "Waiting for your inspection"
            prompt = ("Set the figure-of-merit threshold" + (" and the power range" if power else "") + " on the "
                      "histograms, remove or add particles with a click, then Done.")
        ctx.add_tool("picks", title, index, waiting=True, editable=True, mode=p["mode"],
                     diameter_px=round(diameter / apix, 2), threshold=float(p["min_fom"]), lowpass=float(p["lowpass"]),
                     manual=manual, prompt_title=prompt_title, prompt=prompt)
        ctx.add_highlight("Micrographs", f"{len(info['micrographs']):,}")
        if not manual:
            ctx.add_highlight("Picked", f"{info['n_picks']:,}")
        ctx.log("Waiting: inspect the picks in the job panel, then Done")
        return False

    def finalize(self, ctx: JobContext, choice: str | None) -> None:
        state = json.loads(ctx.path("inspect.json").read_text())
        try:
            sent = json.loads(choice) if (choice or "").strip() else {}
        except ValueError as exc:
            raise JobError(f"Picks not understood: {exc}") from None
        if not sent:  # Done without data: the work saved while the job waited
            saved = ctx.path("session_picks.json")
            sent = json.loads(saved.read_text()) if saved.exists() else {}
        info = json.loads(ctx.abs(state["index"]).read_text())
        if state.get("mode") == "filaments":
            self.finish_filaments(ctx, state, info, sent.get("filaments") or {})
            return
        threshold = float(sent["threshold"]) if sent.get("threshold") is not None else -1e30
        prange = sent.get("power_range") if info.get("has_power") else None
        edits = sent.get("edits") or {}
        allpicks = np.frombuffer(ctx.abs(info["binary"]).read_bytes(), dtype="<f4").reshape(-1, int(info.get("stride") or 3))
        out_dir = ctx.path("picks")
        # picked by hand: a figure of merit above any automatic one (a later threshold keeps them)
        hand_fom = max(1.0, float(info["fom_range"][1]) + 1.0) if info["has_fom"] and info.get("fom_range") else 1.0
        pairs, total, kept_fom = [], 0, []
        for mic in info["micrographs"]:
            block = allpicks[mic["offset"]:mic["offset"] + mic["count"]]
            edit = edits.get(mic["name"]) or {}
            keep = block[:, 2] >= threshold if info["has_fom"] else np.ones(len(block), dtype=bool)
            if prange:  # power range (an unknown power passes)
                with np.errstate(invalid="ignore"):
                    if prange[0] is not None:
                        keep &= ~(block[:, 3] < float(prange[0]))
                    if prange[1] is not None:
                        keep &= ~(block[:, 3] > float(prange[1]))
            removed = np.array([int(i) for i in edit.get("removed") or []], dtype=int)
            keep[removed[(removed >= 0) & (removed < len(block))]] = False
            xy, fom = block[keep, :2].astype(np.float64), block[keep, 2].astype(np.float64)
            added = np.array([[float(a[0]), float(a[1])] for a in edit.get("added") or []], dtype=np.float64).reshape(-1, 2)
            h, w = mic["shape"]
            added = added[(added[:, 0] >= 0) & (added[:, 0] < w) & (added[:, 1] >= 0) & (added[:, 1] < h)]
            if len(added):
                xy = np.vstack([xy, added])
                fom = np.concatenate([fom, np.full(len(added), hand_fom)])
            if not len(xy):
                continue
            name = out_dir / (re.sub(r"\.[A-Za-z0-9]+$", "", rl.pipeline_post(mic["name"])) + "_manualpick.star")
            rl.write_coordinates(name, xy, fom)
            pairs.append((mic["name"], ctx.rel(name)))
            total += len(xy)
            kept_fom.extend(fom.tolist())
        if not total:
            raise JobError("No particle left: lower the threshold or pick some particles")
        listing = rl.write_coordinate_list(ctx.path("manualpick.star"), pairs)
        n_edit = sum(len((e or {}).get("removed") or []) + len((e or {}).get("added") or []) for e in edits.values())
        rows = [["Micrographs with particles", f"{len(pairs):,} of {len(info['micrographs']):,}"],
                ["Particles", f"{total:,}"], ["Edited by hand", f"{n_edit:,}"]]
        if info["has_fom"]:
            rows.insert(2, ["Figure-of-merit threshold", f"{threshold:g}" if threshold > -1e29 else "none"])
        if prange and (prange[0] is not None or prange[1] is not None):
            lo = f"{float(prange[0]):.3g}" if prange[0] is not None else "–"
            hi = f"{float(prange[1]):.3g}" if prange[1] is not None else "–"
            rows.insert(3, ["Power range", f"{lo} to {hi}"])
        ctx.add_table("Picks kept", ["", "Value"], rows)
        meta = pick_report(ctx, listing, ctx.abs(state["star"]), state["diameter"],
                           "manual" if state["coordinates"] is None else "inspected")
        thumb = meta.pop("_thumbnail")
        meta["relion"]["kind"] = "manualpick"
        mics_meta = ctx.require("micrographs").meta
        if mics_meta.get("name_map"):  # picked on denoised micrographs: extracted from the originals
            meta["name_map"] = mics_meta["name_map"]
        for sec in ctx.report.get("sections", []):  # the inspector is done: its picks are now read-only
            if sec.get("kind") == "picks" and sec.get("waiting"):
                sec["waiting"], sec["editable"] = False, False
        ctx.add_output("coordinates", "coordinates", listing, f"{total:,} particles", meta=meta, thumbnail=thumb)
        ctx.save_report()

    def finish_filaments(self, ctx: JobContext, state: dict[str, Any], info: dict[str, Any],
                         filaments: dict[str, list[list[float]]]) -> None:
        """Filaments as RELION's manual picking of helices writes them: the start and the end of each, one after the
        other, in a coordinate file per micrograph, and "startend" in the list (helical extraction cuts the segments)."""
        out_dir = ctx.path("picks")
        pairs, total = [], 0
        for mic in info["micrographs"]:
            h, w = mic["shape"]
            rows = []
            for f in filaments.get(mic["name"]) or []:
                if len(f) != 4:
                    continue
                x1, y1, x2, y2 = (float(v) for v in f)
                if all(0 <= x < w for x in (x1, x2)) and all(0 <= y < h for y in (y1, y2)) and (x1, y1) != (x2, y2):
                    rows += [[x1, y1], [x2, y2]]
            if not rows:
                continue
            name = out_dir / (re.sub(r"\.[A-Za-z0-9]+$", "", rl.pipeline_post(mic["name"])) + "_manualpick.star")
            rl.write_coordinates(name, np.array(rows))
            pairs.append((mic["name"], ctx.rel(name)))
            total += len(rows) // 2
        if not total:
            raise JobError("No filament: click the start then the end of each filament")
        listing = rl.write_coordinate_list(ctx.path("manualpick.star"), pairs, pick_type="startend")
        meta = pick_report(ctx, listing, ctx.abs(state["star"]), state["diameter"], "filaments by hand")
        meta.pop("_thumbnail", None)
        mics_meta = ctx.require("micrographs").meta
        if mics_meta.get("name_map"):
            meta["name_map"] = mics_meta["name_map"]
        for sec in ctx.report.get("sections", []):
            if sec.get("kind") == "picks" and sec.get("waiting"):
                sec["waiting"], sec["editable"] = False, False
        ctx.add_output("coordinates", "coordinates", listing, f"{total:,} filaments", meta=meta)
        ctx.save_report()


# ------------------------------------------------------------------ Topaz denoising
DENOISE_CHUNK = 200  # micrographs per call of topaz denoise


def name_map_of(ctx: JobContext, meta: dict[str, Any]) -> dict[str, str]:
    """Denoised micrograph name -> original name, when the micrographs (or the picks made on them) were denoised."""
    path = meta.get("name_map")
    if not path or not ctx.abs(path).exists():
        return {}
    return json.loads(ctx.abs(path).read_text())


@register
class RelionTopazDenoise(RelionJob):
    name = "relion_topaz_denoise"
    title = "Denoise micrographs (Topaz)"
    category = "Preprocessing"
    gpu_param = "gpus"
    cpu_param = ""
    description = ("Denoises the micrographs with Topaz's pretrained network (topaz denoise), as CryoSPARC's Micrograph "
                   "Denoiser: the particles stand out for picking, by eye or by program, and for inspection. The "
                   "denoised micrographs keep the CTF of the originals; picks made on them are extracted from the "
                   "originals (the extraction translates the names).")
    inputs = [Slot("micrographs", ("micrographs",), "Micrographs", help="Motion-corrected micrographs (with CTF).")]
    params = [
        Param("model", "choice", "unet", choices=["unet", "unet-small", "fcnn", "affine"], label="Network",
              labels={"unet": "U-net (default)", "unet-small": "Small U-net", "fcnn": "Fully convolutional", "affine": "Affine"},
              help="Topaz's pretrained denoising models."),
        Param("max_micrographs", "int", 0, label="Micrographs", min=0,
              help="0 = all. A few dozen spread over the data set are enough to judge or train a picking."),
        Param("patch_size", "int", 1024, label="Patch size", unit="px", min=0,
              help="Denoised in patches of this size (less GPU memory). 0 = whole micrographs."),
        Param("patch_padding", "int", 500, label="Patch padding", unit="px", min=0, advanced=True),
        Param("topaz_exe", "str", "", label="Topaz executable", advanced=True, admin_only=True,
              help="Empty: relion_python_topaz next to RELION."),
        Param("gpus", "int", 1, label="GPUs", min=0, max=1, help="0 = on the CPU (slow)."),
    ]
    outputs = [OutputDef("micrographs", "micrographs", "Denoised micrographs")]

    def run(self, ctx: JobContext) -> None:
        p = ctx.params
        mics_in = ctx.require("micrographs")
        if mics_in.meta.get("denoised"):
            raise JobError("These micrographs are already denoised")
        star, _ = star_of(ctx, mics_in)
        tables = rl.read_star(star)
        table = rl.micrograph_table(tables)
        names = table.column("rlnMicrographName")
        rows = list(range(len(names)))
        if p["max_micrographs"] > 0 and len(rows) > p["max_micrographs"]:
            rows = sorted(names.index(m) for m in sample(names, int(p["max_micrographs"])))
            table.rows = [table.rows[i] for i in rows]
            names = [names[i] for i in rows]
        # micrographs of the same file name in different folders go to different folders (topaz names by file name)
        seen: dict[str, int] = {}
        buckets: dict[int, list[str]] = {}
        for nm in names:
            stem = Path(nm).stem
            k = seen.get(stem, 0)
            seen[stem] = k + 1
            buckets.setdefault(k, []).append(nm)
        device = "0" if p["gpus"] > 0 else "-1"
        new_names: dict[str, str] = {}
        done = 0
        for k, group in buckets.items():
            out_dir = ctx.path("denoised" if k == 0 else f"denoised_{k + 1}")
            for s in range(0, len(group), DENOISE_CHUNK):
                chunk = group[s:s + DENOISE_CHUNK]
                run_relion(ctx, [topaz_exe(ctx), "denoise", "-o", ctx.rel(out_dir), "-m", p["model"], "-d", device,
                                 "-s", str(p["patch_size"]), "-p", str(p["patch_padding"]), *chunk])
                for nm in chunk:
                    new_names[nm] = f"{ctx.rel(out_dir)}/{Path(nm).stem}.mrc"
                done += len(chunk)
                ctx.progress(0.05 + 0.85 * done / len(names), f"{done:,} of {len(names):,} micrographs denoised")
        missing = [d for d in new_names.values() if not ctx.abs(d).exists()]
        if missing:
            raise JobError(f"topaz denoise wrote no {missing[0]} ({len(missing)} missing; see the log)")
        table.set_column("rlnMicrographName", [new_names[nm] for nm in names])
        out = rl.write_star(ctx.path("micrographs_denoised.star"), list(tables.values()),
                            comment=f"Denoised by Topaz ({p['model']}) from {ctx.rel(star)}")
        name_map = ctx.path("denoised_names.json")
        name_map.write_text(json.dumps({v: k for k, v in new_names.items()}, indent=0))
        shown = sample(names, 2)
        micrograph_gallery(ctx, [x for nm in shown for x in (nm, new_names[nm])], "denoised", "Originals and denoised")
        thumb = None
        try:
            from cryoplug.micrographs import preview, read_image
            thumb = thumbnail_of(ctx, preview(read_image(ctx.abs(new_names[names[0]])), 256)[0], "micrographs")
        except Exception as exc:  # cosmetic
            ctx.warn(f"No thumbnail: {exc}")
        ctx.add_table("Denoising", ["", "Value"], [["Micrographs", f"{len(names):,}"], ["Network", p["model"]],
                                                   ["Device", "GPU" if p["gpus"] > 0 else "CPU"]])
        ctx.add_highlight("Denoised", f"{len(names):,}")
        passed = {k: v for k, v in mics_in.meta.items() if k in ("has_motion", "power_spectra", "motion_star")}
        meta = micrograph_meta(ctx, out, "denoise", denoised=True, name_map=ctx.rel(name_map), raw_micrographs=ctx.rel(star),
                               **passed)
        ctx.add_output("micrographs", "micrographs", out, f"{len(names):,} denoised micrographs", meta=meta, thumbnail=thumb)


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
        *helical_params("extract"),
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
        if params.get("helical") and not (float(params.get("helical_rise") or 0) > 0 and float(params.get("helical_outer_diameter") or 0) > 0):
            return ["Helical segments: give the tube diameter and the rise"]
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
            coord_list = Path(coords.path)
            pick_type = rl.coordinate_pick_type(coord_list)
            translate = name_map_of(ctx, coords.meta)
            if translate:  # picked on denoised micrographs
                if mics_in.meta.get("denoised"):
                    raise JobError("Extract from the original micrographs, not the denoised ones (the picks made on the "
                                   "denoised micrographs are translated to the originals)")
                pairs = [(translate.get(m, m), c) for m, c in rl.coordinate_files(coord_list)]
                coord_list = rl.write_coordinate_list(ctx.path("coordinates_originals.star"), pairs,
                                                      pick_type=None if pick_type == "particles" else pick_type)
                ctx.log(f"Picks made on denoised micrographs: {len(pairs):,} coordinate files assigned to the originals")
            if pick_type in ("startend", "lines") and not p["helical"]:
                raise JobError("These picks are filaments (start and end of each): tick 'Helical processing' (tube "
                               "diameter, rise) to cut them into segments")
            args += ["--coord_list", ctx.rel(coord_list)]
            source = (f"{int(coords.meta.get('n_filaments') or 0):,} filaments of {coords.source}" if coords.meta.get("n_filaments")
                      else f"{int(coords.meta.get('n_particles') or 0):,} picks of {coords.source}")
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
        args += helical_extract_args(p)
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
        meta = particles_out_meta(ctx, out, "extract", micrographs=ctx.rel(star), extract_box=box,
                                  reextracted=bool(parts))
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
