"""RELION 5 tomography, from tilt series to tomograms: import (SerialEM mdoc files), motion correction, CTF estimation,
tilt-image exclusion, tilt-series alignment (IMOD or AreTomo2), tomogram reconstruction and denoising (cryoCARE).

Each job runs the program RELION 5.1's GUI runs, with the options it passes (src/pipeline_jobs.cpp), from the project
folder. The sets of tilt series these programs write (cryoplug.tomo) are the data that flows from job to job: a global
STAR file and one STAR file per tilt series. The napari tools of RELION (tilt-image exclusion, particle picking) are
replaced by tools in the job panel that write the same files.
"""
from __future__ import annotations

import dataclasses
import glob
import json
import os
from pathlib import Path
from typing import Any

import numpy as np

from cryoplug import relion as rl
from cryoplug import tomo as tm
from cryoplug.jobs import register
from cryoplug.jobs.base import JobContext, JobError, JobType, OutputDef, Param, Slot, extra_args_param
from cryoplug.jobs.relion_common import (TOOL, FileProgress, RelionJob, micrograph_gallery, mpi, relion_command, run_relion,
                                         sample, star_of, thumbnail_of, threads)
from cryoplug.jobs.relion_preprocess import RelionCtffind, RelionMotionCorr, find_movies, link_folders

CATEGORY = "Tomography"


# ------------------------------------------------------------------ shared helpers
def tilt_set_input(ctx: JobContext, slot: str = "tiltseries") -> tuple[Path, str, tm.TiltSeriesSet]:
    """The tilt-series (or tomogram) set of an input: its STAR file, project-relative name and contents."""
    star, ref = star_of(ctx, ctx.require(slot))
    try:
        return star, ref, tm.read_set(star, ctx.project_dir)
    except ValueError as exc:
        raise JobError(str(exc)) from None


def tilt_index(ctx: JobContext, tset: tm.TiltSeriesSet, name: str = "tilt_series_index.json",
               excluded: dict[str, set[int]] | None = None) -> Path:
    """The tilt images of every tilt series by tilt angle, for the tilt-series browser and the exclusion tool: the row
    of each image in its table, its tilt, dose, image and CTF fit."""
    series = []
    for ts, table in tset.series.items():
        cols = {lab: (table.column(lab) if table.has(lab) else None) for lab in (
            "rlnTomoNominalStageTiltAngle", "rlnMicrographPreExposure", "rlnMicrographName", "rlnMicrographMovieName",
            "rlnCtfImage", "rlnDefocusU", "rlnDefocusV", "rlnCtfMaxResolution", "rlnCtfFigureOfMerit",
            "rlnAccumMotionTotal", "rlnTomoXShiftAngst", "rlnTomoYShiftAngst")}

        def num(label: str, i: int, scale: float = 1.0) -> float | None:
            col = cols[label]
            try:
                return round(float(col[i]) * scale, 4) if col is not None else None
            except ValueError:
                return None
        tilts = []
        for i in tm.tilt_order(table):
            i = int(i)
            image = (cols["rlnMicrographName"] or cols["rlnMicrographMovieName"] or [None] * len(table))[i]
            du, dv = num("rlnDefocusU", i), num("rlnDefocusV", i)
            tilts.append({"row": i, "tilt": num("rlnTomoNominalStageTiltAngle", i), "dose": num("rlnMicrographPreExposure", i),
                          "image": image, "movie": cols["rlnMicrographName"] is None,
                          "ctf_image": cols["rlnCtfImage"][i] if cols["rlnCtfImage"] else None,
                          "defocus": round((du + dv) / 2e4, 4) if du is not None and dv is not None else None,
                          "ctf_res": num("rlnCtfMaxResolution", i), "fom": num("rlnCtfFigureOfMerit", i),
                          "motion": num("rlnAccumMotionTotal", i),
                          "excluded": bool(excluded and i in excluded.get(ts, set()))})
        series.append({"name": ts, "tilts": tilts})
    first = next(iter(tset.series.values()), rl.StarTable("none"))
    data = {"series": series, "has_ctf": first.has("rlnDefocusU"), "has_motion": first.has("rlnMicrographName"),
            "pixel_size": tm.set_meta("", tset, "").get("pixel_size")}
    path = ctx.path(name)
    path.write_text(json.dumps(data))
    return path


def tilt_series_report(ctx: JobContext, tset: tm.TiltSeriesSet, title: str = "Tilt series", browser: bool = True) -> Path | None:
    """Summary table, plots of what the job measured against the tilt angle, a few tilt images; returns a thumbnail."""
    ctx.add_table(title, ["Tilt series", "Images", "Tilt range", "Dose", "Defocus", "CTF fit"], tm.series_rows(tset))
    ctx.add_highlight("Tilt series", f"{len(tset.series):,}")
    ctx.add_highlight("Tilt images", f"{tset.n_tilts():,}")
    shown = list(tset.series.items())[:8]
    for label, title_, scale, unit in (("rlnDefocusU", "Defocus against tilt angle", 1e-4, "Defocus (µm)"),
                                       ("rlnCtfMaxResolution", "CTF fit resolution against tilt angle", 1.0, "Resolution (Å)"),
                                       ("rlnAccumMotionTotal", "Motion against tilt angle", 1.0, "Total motion (Å)"),
                                       ("rlnTomoXShiftAngst", "Alignment shift X against tilt angle", 1.0, "Shift X (Å)")):
        series = []
        for ts, t in shown:
            if not (t.has(label) and t.has("rlnTomoNominalStageTiltAngle")):
                continue
            order = tm.tilt_order(t)
            x, y = t.floats("rlnTomoNominalStageTiltAngle")[order], t.floats(label)[order] * scale
            series.append({"name": ts, "x": [round(float(v), 2) for v in x], "y": [round(float(v), 4) for v in y]})
        if series:
            ctx.add_plot(title_, series, x_label="Tilt angle (°)", y_label=unit)
    if browser:
        ctx.add_tool("tiltseries", "Tilt images", tilt_index(ctx, tset), waiting=False, editable=False)
    first = next(iter(tset.series.values()), None)
    if first is None or not first.has("rlnMicrographName"):
        return None
    order = tm.tilt_order(first)
    names = [first.column("rlnMicrographName")[int(i)] for i in sample(list(order), 4)]
    png = micrograph_gallery(ctx, names, "tilt_images", f"Tilt images of {tset.names[0]} (lowest to highest tilt)", size=300, per_row=4)
    if png is None:
        return None
    from cryoplug.micrographs import preview, read_image
    zero = int(order[int(np.argmin(np.abs(first.floats("rlnTomoNominalStageTiltAngle")[order])))]) \
        if first.has("rlnTomoNominalStageTiltAngle") else 0
    try:
        return thumbnail_of(ctx, preview(read_image(ctx.abs(first.column("rlnMicrographName")[zero])), 256)[0], "tilt")
    except Exception as exc:  # cosmetic
        ctx.warn(f"No thumbnail: {exc}")
        return None


def set_output(ctx: JobContext, name: str, type_: str, star: Path, kind: str, label: str,
               thumbnail: Path | None = None, **extra: Any) -> tm.TiltSeriesSet:
    tset = tm.read_set(star, ctx.project_dir)
    if not tset.series:
        raise JobError(f"{star.name} lists no tilt series (see the log)")
    meta = tm.set_meta(ctx.rel(star), tset, kind, **extra)
    ctx.add_output(name, type_, star, label.format(n=len(tset.series), tilts=tset.n_tilts()), meta=meta, thumbnail=thumbnail)
    return tset


def tomogram_index(ctx: JobContext, tset: tm.TiltSeriesSet, name: str = "tomograms_index.json") -> Path:
    """The tomograms of a set for the slice viewer: file (the denoised one when there is one), binned size and pixel."""
    from cryoplug.mrc import MapVolume
    g = tset.global_table
    meta = tm.set_meta("", tset, "")
    items = []
    for ts in tset.names:
        show = tm.tomogram_file(tset, ts, ctx.project_dir)
        if not show:
            continue
        raw = tm.tomogram_file(tset, ts, ctx.project_dir, ("rlnTomoReconstructedTomogram", "rlnTomoReconstructedTomogramHalf1"))
        head = MapVolume.read(ctx.abs(show), header_only=True)
        items.append({"name": ts, "path": show, "raw": raw,
                      "denoised": show if show == tset.value(ts, "rlnTomoReconstructedTomogramDenoised") else None,
                      "shape": [int(v) for v in head.shape_xyz], "pixel": round(float(head.voxel[0]), 4),
                      "projection": tm.tomogram_file(tset, ts, ctx.project_dir, tm.PROJECTION_LABELS)})
    data = {"tomograms": items, "binning": meta.get("binning"), "pixel_size": meta.get("pixel_size"),
            "size": meta.get("size"), "has_size": g.has("rlnTomoSizeX")}
    path = ctx.path(name)
    path.write_text(json.dumps(data))
    return path


def tool_executable(ctx: JobContext, key: str, label: str) -> str:
    """The executable of a program RELION runs (AreTomo2, batchruntomo, cryoCARE): that of the Tools page, or its name
    (looked for in the PATH of the RELION tool)."""
    exe = ctx.executable(key)
    if not exe:
        raise JobError(f"{label} is not configured (page Tools)")
    if not os.path.isabs(exe):
        ctx.log(f"{label} is not configured in Tools: RELION looks for {exe} in its PATH")
    return exe


# ------------------------------------------------------------------ import
@register
class RelionTomoImport(JobType):
    name = "relion_tomo_import"
    title = "Import tilt series (RELION)"
    category = CATEGORY
    tool = TOOL
    software = ["RELION"]
    description = ("Imports tilt series collected with SerialEM (relion_python_tomo_import SerialEM): the movies (or "
                   "motion-corrected images) of every tilt and the mdoc file of each tilt series, which gives the tilt "
                   "angles, the order of acquisition (hence the dose before each tilt) and the nominal defocus. Writes "
                   "RELION 5's tilt-series set: the start of subtomogram averaging.")
    inputs: list[Slot] = []
    params = [
        Param("movies", "path", "", label="Tilt image files", placeholder="/data/frames/*.tif",
              help="A wildcard matching the movie (or image) of every tilt of every tilt series."),
        Param("mdocs", "path", "", label="mdoc files", placeholder="/data/mdoc/*.mdoc",
              help="SerialEM's mdoc file of each tilt series: the tilt series takes its name (the file name without "
                   "extensions); its SubFramePath entries name the tilt images."),
        Param("pixel_size", "float", 0.0, label="Pixel size", unit="Å", min=0, help="Of the movies (unbinned)."),
        Param("voltage", "float", 300.0, label="Voltage", unit="kV", min=60, max=400),
        Param("cs", "float", 2.7, label="Spherical aberration", unit="mm", min=0, max=10),
        Param("amplitude_contrast", "float", 0.1, label="Amplitude contrast", min=0, max=1),
        Param("dose", "float", 3.0, label="Dose per tilt image", unit="e⁻/Å²", min=0,
              help="Overrides the doses of the mdoc files (ExposureDose). 0: those of the mdoc files."),
        Param("dose_per_frame", "bool", False, label="The dose above is per movie frame",
              help="The dose before each tilt is then counted frame by frame (NumSubFrames of the mdoc file)."),
        Param("tilt_axis", "float", 85.0, label="Tilt axis angle", unit="°", min=-180, max=180,
              help="Nominal angle of the tilt axis in the images (counter-clockwise from Y), refined by the alignment."),
        Param("flip_hand", "bool", True, label="Invert defocus handedness",
              help="The sign of the defocus slope with depth (rlnTomoHand −1): yes for the RELION tutorial data "
                   "(EMPIAR-10164); found by experience for a microscope (a wrong hand mirrors the map)."),
        Param("motion_corrected", "bool", False, label="Images already motion-corrected",
              help="The tilt images are single images (or were corrected elsewhere): no motion correction job."),
        Param("prefix", "str", "", label="Prefix", help="Added to the tilt-series names (several sessions)."),
        Param("optics_group", "str", "", label="Optics group", placeholder="one per tilt series",
              help="One optics group for all the tilt series (empty: each tilt series is its own group)."),
        Param("mtf", "path", "", label="MTF of the detector", advanced=True),
        extra_args_param(),
    ]
    outputs = [OutputDef("tiltseries", "tiltseries", "Tilt series")]

    @classmethod
    def validate(cls, params: dict[str, Any], connected: dict[str, str]) -> list[str]:
        problems = []
        if not str(params.get("movies") or "").strip():
            problems.append("Give the tilt image files")
        if not str(params.get("mdocs") or "").strip():
            problems.append("Give the mdoc files")
        if not float(params.get("pixel_size") or 0) > 0:
            problems.append("Give the pixel size")
        return problems

    @staticmethod
    def pattern(ctx: JobContext, user: str, files: list[Path], subdir: str) -> str:
        """A project-relative wildcard over the folders linked into the job folder (RELION's import globs relative
        patterns only)."""
        links = link_folders(ctx, files, subdir)
        folders = sorted({os.path.dirname(link) for link in links})
        base = os.path.basename(os.path.expanduser(user.strip()).rstrip("/"))
        if not any(c in base for c in "*?[") or os.path.isdir(os.path.expanduser(user.strip())):
            base = f"*{files[0].suffix}"
        if len(folders) == 1:
            return f"{folders[0]}/{base}"
        return f"{os.path.dirname(folders[0])}/*/{base}"

    @staticmethod
    def add_prefix(ctx: JobContext, star: Path, tset: tm.TiltSeriesSet, prefix: str) -> tm.TiltSeriesSet:
        """Tilt series renamed <prefix>_<name>, as --prefix would name them: RELION 5.1's import then looks for the
        prefixed names in the names of the tilt images and finds no tilt series at all."""
        renamed = tm.TiltSeriesSet(tset.global_table)
        old = list(tset.names)
        new = [f"{prefix}_{n}" for n in old]
        renamed.global_table.set_column("rlnTomoName", new)
        for a, b in zip(old, new):
            renamed.series[b] = tset.series[a]
        for f in ctx.path("tilt_series").glob("*.star"):
            f.unlink()
        tm.write_set(star, renamed, ctx.project_dir, comment=f"Tilt series imported with the prefix {prefix}")
        ctx.log(f"Tilt series named {new[0]}..., with the prefix (added after relion_python_tomo_import)")
        return tm.read_set(star, ctx.project_dir)

    def run(self, ctx: JobContext) -> None:
        p = ctx.params
        movies = find_movies(p["movies"]) if not os.path.isdir(os.path.expanduser(p["movies"])) else \
            sorted(f for f in Path(os.path.expanduser(p["movies"])).iterdir() if f.is_file())
        if not movies:
            raise JobError(f"No tilt image matches {p['movies']}")
        raw = os.path.expanduser(p["mdocs"].strip())
        mdocs = sorted(Path(raw).glob("*.mdoc")) if os.path.isdir(raw) else \
            sorted(Path(f) for f in glob.glob(raw, recursive=True) if f.endswith(".mdoc"))
        if not mdocs:
            raise JobError(f"No mdoc file matches {p['mdocs']}")
        names = {tm.mdoc_basename(f.name) for f in movies}
        missing, empty = 0, []
        for f in mdocs:
            try:
                tilts = tm.parse_mdoc(f)
            except OSError as exc:
                raise JobError(f"Cannot read {f}: {exc}") from None
            absent = sum(1 for t in tilts if tm.mdoc_basename(t.get("SubFramePath", "")) not in names)
            missing += absent
            if absent == len(tilts) or not any(tm.mdoc_basename(f.name) in n for n in names):
                empty.append(f.name)
        if empty:  # RELION 5.1 drops their tilt series, then pairs the other names with the wrong mdoc files
            raise JobError(f"No tilt image among the files for {', '.join(empty[:5])}"
                           f"{' ...' if len(empty) > 5 else ''}: leave these mdoc files out of the wildcard")
        if missing:
            ctx.warn(f"{missing} tilt image(s) named in the mdoc files are not among the {len(movies):,} files: "
                     "RELION leaves them out")
        ctx.log(f"{len(movies):,} tilt images, {len(mdocs)} mdoc files")
        mov = self.pattern(ctx, p["movies"], movies, "frames")
        mdc = self.pattern(ctx, p["mdocs"], mdocs, "mdoc")
        args = [ctx.program(TOOL, "relion_python_tomo_import"), "SerialEM", "--tilt-image-movie-pattern", mov,
                "--mdoc-file-pattern", mdc, "--nominal-tilt-axis-angle", f"{p['tilt_axis']:g}",
                "--nominal-pixel-size", f"{p['pixel_size']:g}", "--voltage", f"{p['voltage']:g}",
                "--spherical-aberration", f"{p['cs']:g}", "--amplitude-contrast", f"{p['amplitude_contrast']:g}",
                "--optics-group-name", str(p["optics_group"]).strip()]
        if p["dose"] > 0:
            args += ["--dose-per-movie-frame" if p["dose_per_frame"] else "--dose-per-tilt-image", f"{p['dose']:g}"]
        if str(p["mtf"]).strip():
            args += ["--mtf-file", str(p["mtf"]).strip()]
        if p["flip_hand"]:
            args.append("--invert-defocus-handedness")
        if p["motion_corrected"]:
            args.append("--images-are-motion-corrected")
        args += ["--output-directory", ctx.rel(ctx.job_dir) + "/", *ctx.split_extra()]
        ctx.progress(0.1, "relion_python_tomo_import")
        run_relion(ctx, args)
        star = ctx.path("tilt_series.star")
        if not star.exists():
            raise JobError("The import wrote no tilt_series.star (see the log)")
        tset = tm.read_set(star, ctx.project_dir)
        if str(p["prefix"]).strip():
            tset = self.add_prefix(ctx, star, tset, str(p["prefix"]).strip())
        thumb = tilt_series_report(ctx, tset, browser=bool(p["motion_corrected"]))
        set_output(ctx, "tiltseries", "tiltseries", star, "import", "{n} tilt series ({tilts:,} images)", thumb,
                   motion_corrected=bool(p["motion_corrected"]))


# ------------------------------------------------------------------ motion correction
_SPA_ONLY = {"dose_per_frame", "pre_exposure", "dose_weighting", "save_nodw", "first_frame", "last_frame"}


def _tomo_params(params: list[Param], drop: set[str], defaults: dict[str, Any] | None = None) -> list[Param]:
    out = []
    for prm in params:
        if prm.name in drop:
            continue
        out.append(dataclasses.replace(prm, default=defaults[prm.name]) if defaults and prm.name in defaults else prm)
    return out


@register
class RelionTomoMotionCorr(RelionMotionCorr):
    name = "relion_tomo_motioncorr"
    title = "Motion correction (tilt series)"
    category = CATEGORY
    description = ("Aligns the frames of every tilt movie and sums them (relion_run_motioncorr on a tilt-series set, "
                   "RELION's own CPU implementation or MotionCor2): the tilt images, their motion and the power spectra "
                   "CTFFIND reads. Optionally also the sums of the even and of the odd frames, for denoising.")
    inputs = [Slot("tiltseries", ("tiltseries",), "Tilt series")]
    params = _tomo_params(RelionMotionCorr.params, _SPA_ONLY, {"patches_x": 1, "patches_y": 1}) + [
        Param("even_odd", "bool", False, label="Save images for denoising",
              help="Sums of the even and of the odd frames of each movie (--even_odd_split): the two half tomograms "
                   "cryoCARE learns to denoise from."),
    ]
    outputs = [OutputDef("tiltseries", "tiltseries", "Motion-corrected tilt series")]

    def run(self, ctx: JobContext) -> None:
        p = ctx.params
        star, ref, tset = tilt_set_input(ctx)
        first = next(iter(tset.series.values()))
        if not first.has("rlnMicrographMovieName"):
            raise JobError("These tilt series are already motion-corrected (no movies): go on to the CTF estimation")
        n = tset.n_tilts()
        own = p["implementation"] == "relion"
        float16, save_ps = bool(p["float16"]), bool(p["save_power_spectra"])
        if not own and (float16 or save_ps):
            ctx.log("MotionCor2 writes neither float16 files nor power spectra: both switched off")
            float16 = save_ps = False
        if own and float16 and not save_ps:
            ctx.log("float16 images need the power spectra for CTFFIND: saving them")
            save_ps = True
        args = [*relion_command(ctx, "relion_run_motioncorr", p["mpi"]), "--i", ref, "--o", ctx.rel(ctx.job_dir) + "/"]
        if p["even_odd"]:
            args.append("--even_odd_split")
        if own:
            args += ["--use_own", "--j", str(p["threads"])]
            if float16:
                args.append("--float16")
        else:
            args += ["--use_motioncor2", "--motioncor2_exe", ctx.executable("motioncor")]
            if str(p["motioncor2_args"]).strip():
                args += ["--other_motioncor2_args", f" {p['motioncor2_args'].strip()} "]
            ngpu = max(1, len(ctx.resources.get("gpus") or []) or int(p["gpus"]))
            args += ["--gpu", ":".join(str(i % ngpu) for i in range(max(1, int(p["mpi"]))))]
        if str(p["defect_file"]).strip():
            args += ["--defect_file", str(p["defect_file"]).strip()]
        args += ["--bin_factor", f"{p['binning']:g}", "--bfactor", f"{p['bfactor']:g}", "--patch_x", str(p["patches_x"]),
                 "--patch_y", str(p["patches_y"]), "--eer_grouping", str(p["eer_grouping"])]
        if p["group_frames"] > 1:
            args += ["--group_frames", str(p["group_frames"])]
        gain = str(p["gain"]).strip()
        if gain:
            rot = 0 if p["gain_rotation"] == "import" else int(p["gain_rotation"])
            flip = 0 if p["gain_flip"] == "import" else int(p["gain_flip"])
            args += ["--gainref", gain, "--gain_rot", str(rot), "--gain_flip", str(flip)]
        if save_ps:  # the GUI groups power spectra over that many frames for tilt series (a dose of 1 per frame)
            args += ["--grouping_for_ps", str(max(1, round(p["ps_dose"])))]
        args += ctx.split_extra()
        ctx.add_table("Motion correction", ["Setting", "Value"], [
            ["Tilt series", f"{len(tset.series):,}"], ["Tilt movies", f"{n:,}"],
            ["Program", "RELION (CPU)" if own else "MotionCor2 (GPU)"], ["Patches", f"{p['patches_x']} × {p['patches_y']}"],
            ["Binning", f"{p['binning']:g}"], ["Even/odd sums", "yes" if p["even_odd"] else "no"]])
        ctx.progress(0.03, f"relion_run_motioncorr on {n:,} tilt movies")
        with FileProgress(ctx, ctx.job_dir, "*.star", n + 1, "Tilt movies aligned"):
            run_relion(ctx, args)
        out = ctx.path("corrected_tilt_series.star")
        if not out.exists():
            raise JobError("relion_run_motioncorr wrote no corrected_tilt_series.star (see the log)")
        tset = tm.read_set(out, ctx.project_dir)
        thumb = tilt_series_report(ctx, tset)
        set_output(ctx, "tiltseries", "tiltseries", out, "motioncorr", "{n} tilt series, motion-corrected", thumb)


# ------------------------------------------------------------------ CTF estimation
@register
class RelionTomoCtffind(RelionCtffind):
    name = "relion_tomo_ctffind"
    title = "CTF estimation (tilt series)"
    category = CATEGORY
    description = ("Fits the CTF of every tilt image with CTFFIND (relion_run_ctffind on a tilt-series set): the defocus "
                   "is searched around the nominal defocus of the mdoc files, and the highest resolution of the fit "
                   "lowered with the dose the image received before (its Thon rings fade).")
    inputs = [Slot("tiltseries", ("tiltseries",), "Tilt series", help="Motion-corrected tilt series.")]
    params = _tomo_params(RelionCtffind.params, {"use_nodw"}) + [
        Param("nominal_range", "float", 10000.0, label="Search around the nominal defocus", unit="Å", min=0,
              help="± this around rlnTomoNominalDefocus (0: the minimum and maximum defocus above)."),
        Param("dose_fading", "float", 100.0, label="Dose-dependent Thon ring fading", unit="e⁻/Å²", min=0,
              help="The highest resolution of the fit is multiplied by exp(dose / this value) (0: the same for all)."),
    ]
    outputs = [OutputDef("tiltseries", "tiltseries", "Tilt series with CTF")]

    def run(self, ctx: JobContext) -> None:
        p = ctx.params
        star, ref, tset = tilt_set_input(ctx)
        first = next(iter(tset.series.values()))
        if not first.has("rlnMicrographName"):
            raise JobError("These tilt series are not motion-corrected yet (no rlnMicrographName)")
        n = tset.n_tilts()
        use_ps = bool(p["use_power_spectra"]) and first.has("rlnCtfPowerSpectrum")
        if p["use_power_spectra"] and not use_ps:
            ctx.log("The tilt images have no power spectra (rlnCtfPowerSpectrum): CTFFIND reads the images")
        args = [*relion_command(ctx, "relion_run_ctffind", p["mpi"]), "--i", ref, "--o", ctx.rel(ctx.job_dir) + "/",
                "--Box", str(p["box"]), "--ResMin", f"{p['res_min']:g}", "--ResMax", f"{p['res_max']:g}",
                "--dFMin", f"{p['defocus_min']:g}", "--dFMax", f"{p['defocus_max']:g}", "--FStep", f"{p['defocus_step']:g}",
                "--dAst", f"{p['astigmatism']:g}", "--localsearch_nominal_defocus", f"{p['nominal_range']:g}",
                "--exp_factor_dose", f"{p['dose_fading']:g}"]
        if p["phase_shift"]:
            args += ["--do_phaseshift", "--phase_min", f"{p['phase_min']:g}", "--phase_max", f"{p['phase_max']:g}",
                     "--phase_step", f"{p['phase_step']:g}"]
        args += ["--ctffind_exe", ctx.executable("ctffind"), "--ctfWin", str(p["window"]), "--is_ctffind4"]
        if not p["exhaustive"]:
            args.append("--fast_search")
        if use_ps:
            args.append("--use_given_ps")
            if p["extra_ice"]:
                args.append("--extra_ice_stats")
        if p["tilt_search"]:
            args.append("--tilt_search")
        args += ctx.split_extra()
        ctx.progress(0.03, f"CTFFIND on {n:,} tilt images")
        with FileProgress(ctx, ctx.job_dir, "*.ctf", n, "Tilt images fitted"):
            run_relion(ctx, args)
        out = ctx.path("tilt_series_ctf.star")
        if not out.exists():
            raise JobError("relion_run_ctffind wrote no tilt_series_ctf.star (is CTFFIND configured in Tools? see the log)")
        tset = tm.read_set(out, ctx.project_dir)
        res = np.concatenate([t.floats("rlnCtfMaxResolution") for t in tset.series.values() if t.has("rlnCtfMaxResolution")] or [np.zeros(0)])
        du = np.concatenate([t.floats("rlnDefocusU") for t in tset.series.values() if t.has("rlnDefocusU")] or [np.zeros(0)])
        if len(du):
            ctx.add_highlight("Defocus", f"{du.min() / 1e4:.1f}–{du.max() / 1e4:.1f} µm")
        if len(res):
            ctx.add_highlight("CTF fit", f"{np.median(res):.1f} Å")
        thumb = tilt_series_report(ctx, tset)
        set_output(ctx, "tiltseries", "tiltseries", out, "ctffind", "{n} tilt series with CTF", thumb)


# ------------------------------------------------------------------ tilt-image exclusion
EXCLUSION = {"max_tilt": "tilt", "max_dose": "dose", "max_ctf_res": "ctf_res"}


def auto_excluded(tset: tm.TiltSeriesSet, p: dict[str, Any]) -> dict[str, set[int]]:
    """The tilt images outside the limits: |tilt| above, dose before the image above, CTF fit worse than."""
    out: dict[str, set[int]] = {}
    for ts, t in tset.series.items():
        bad = np.zeros(len(t), dtype=bool)
        for param, label in (("max_tilt", "rlnTomoNominalStageTiltAngle"), ("max_dose", "rlnMicrographPreExposure"),
                             ("max_ctf_res", "rlnCtfMaxResolution")):
            limit = float(p.get(param) or 0)
            if limit > 0 and t.has(label):
                values = t.floats(label)
                bad |= (np.abs(values) if param == "max_tilt" else values) > limit
        out[ts] = set(np.flatnonzero(bad).tolist())
    return out


@register
class RelionTomoExclude(JobType):
    name = "relion_tomo_exclude"
    title = "Exclude tilt images"
    category = CATEGORY
    interactive = True
    tool = TOOL
    software = ["RELION"]
    description = ("Leaves out the bad images of each tilt series (an edge of the grid or a lamella in the beam, ice "
                   "crystals, drift, a failed CTF fit), as RELION's tilt-image excluder: the images by tilt angle with "
                   "their CTF fit, a click to exclude or keep one, and limits on the tilt, the dose and the CTF fit. "
                   "Writes the selected tilt series as RELION does (selected_tilt_series.star).")
    inputs = [Slot("tiltseries", ("tiltseries",), "Tilt series", help="Motion-corrected (and best CTF-estimated) tilt series.")]
    params = [
        Param("mode", "choice", "interactive", choices=["interactive", "limits"], label="Exclusion",
              labels={"interactive": "Image by image (the job waits for you)", "limits": "By the limits below only"},
              help="Interactive: the limits below pre-select the images to exclude; you check and change them."),
        Param("max_tilt", "float", 0.0, label="Highest tilt", unit="°", min=0, max=90, help="0: no limit."),
        Param("max_dose", "float", 0.0, label="Highest dose before the image", unit="e⁻/Å²", min=0, help="0: no limit."),
        Param("max_ctf_res", "float", 0.0, label="Worst CTF fit resolution", unit="Å", min=0,
              help="Images whose CTF fit holds only to a coarser resolution are excluded (0: no limit)."),
    ]
    outputs = [OutputDef("tiltseries", "tiltseries", "Selected tilt series")]

    def prepare(self, ctx: JobContext) -> bool:
        p = ctx.params
        star, ref, tset = tilt_set_input(ctx)
        excluded = auto_excluded(tset, p)
        if p["mode"] == "limits":
            self.write(ctx, tset, excluded, "limits")
            return True
        index = tilt_index(ctx, tset, excluded=excluded)
        ctx.path("exclude.json").write_text(json.dumps({"star": ref}))
        n_ex = sum(len(v) for v in excluded.values())
        ctx.add_tool("tiltseries", "Exclude tilt images", index, waiting=True, editable=True,
                     prompt_title="Waiting for your selection",
                     prompt="Click an image to exclude it (click again to keep it); ← → move through the tilt series. "
                            + (f"{n_ex} image(s) start excluded by the limits. " if n_ex else "") + "Then Done.")
        ctx.add_highlight("Tilt series", f"{len(tset.series):,}")
        ctx.add_highlight("Tilt images", f"{tset.n_tilts():,}")
        ctx.log("Waiting: exclude the bad tilt images in the job panel, then Done")
        return False

    def finalize(self, ctx: JobContext, choice: str | None) -> None:
        state = json.loads(ctx.path("exclude.json").read_text())
        try:
            sent = json.loads(choice) if (choice or "").strip() else {}
        except ValueError as exc:
            raise JobError(f"Selection not understood: {exc}") from None
        if not sent:
            saved = ctx.path("session_exclude.json")
            sent = json.loads(saved.read_text()) if saved.exists() else {}
        tset = tm.read_set(ctx.abs(state["star"]), ctx.project_dir)
        if "excluded" in sent:
            excluded = {ts: {int(i) for i in rows} for ts, rows in (sent.get("excluded") or {}).items()}
        else:
            excluded = auto_excluded(tset, ctx.params)
        for sec in ctx.report.get("sections", []):  # done: the tool becomes a read-only browser
            if sec.get("kind") == "tiltseries":
                sec.update(waiting=False, editable=False)
        ctx.save_report()
        self.write(ctx, tset, excluded, "interactive")

    def write(self, ctx: JobContext, tset: tm.TiltSeriesSet, excluded: dict[str, set[int]], how: str) -> None:
        kept = tm.TiltSeriesSet(tset.global_table)
        rows = []
        for ts, table in tset.series.items():
            out = excluded.get(ts, set())
            keep = [i for i in range(len(table)) if i not in out]
            kept.series[ts] = table.select(keep)
            rows.append([ts, len(table), len(table) - len(keep), len(keep)])
        if not any(len(t) for t in kept.series.values()):
            raise JobError("Every tilt image is excluded")
        star = tm.write_set(ctx.path("selected_tilt_series.star"), kept, ctx.project_dir,
                            comment="Tilt series with the selected tilt images (CryoPlug tilt-image exclusion)")
        n_out = sum(r[2] for r in rows)
        ctx.add_table("Tilt images kept", ["Tilt series", "Images", "Excluded", "Kept"], rows)
        ctx.add_highlight("Excluded", f"{n_out:,} of {sum(r[1] for r in rows):,}")
        dropped = [r[0] for r in rows if r[3] == 0]
        if dropped:
            ctx.warn(f"Tilt series without any image left (left out): {', '.join(dropped)}")
        tset2 = tm.read_set(star, ctx.project_dir)
        thumb = tilt_series_report(ctx, tset2, "Selected tilt series", browser=how == "limits")
        set_output(ctx, "tiltseries", "tiltseries", star, "exclude", "{n} tilt series ({tilts:,} images kept)", thumb,
                   excluded=n_out)


# ------------------------------------------------------------------ tilt-series alignment
@register
class RelionTomoAlign(RelionJob):
    name = "relion_tomo_align"
    title = "Align tilt series (IMOD, AreTomo2)"
    category = CATEGORY
    description = ("Aligns the images of every tilt series (relion_align_tiltseries): IMOD's fiducial-based alignment "
                   "(gold beads), IMOD's patch tracking (no fiducials), or AreTomo2's projection matching on GPUs, which "
                   "can also estimate the CTF and reconstruct the tomograms. Gives the tilt angles, tilt axis and shifts "
                   "of every image, the geometry of the tomograms.")
    gpu_param = ""
    cpu_param = "mpi"
    inputs = [Slot("tiltseries", ("tiltseries",), "Tilt series", help="With their CTF, best after excluding bad images.")]
    params = [
        Param("method", "choice", "aretomo", choices=["aretomo", "imod_patch", "imod_fiducials"], label="Method",
              labels={"aretomo": "AreTomo2 (projection matching, GPU)", "imod_patch": "IMOD patch tracking",
                      "imod_fiducials": "IMOD fiducials (gold beads)"}),
        Param("thickness", "float", 300.0, label="Tomogram thickness", unit="nm", min=10, max=2000,
              help="Estimated thickness of the sample: projection matching in AreTomo2, patch tracking in IMOD."),
        Param("fiducial_diameter", "float", 10.0, label="Fiducial diameter", unit="nm", min=1, max=50,
              show_if="method=imod_fiducials"),
        Param("patch_size", "float", 100.0, label="Patch size", unit="nm", min=1, max=1000, show_if="method=imod_patch"),
        Param("patch_overlap", "float", 50.0, label="Patch overlap", unit="%", min=0, max=99, show_if="method=imod_patch"),
        Param("tilt_correct", "bool", False, label="Correct the tilt angle offset", show_if="method=aretomo",
              help="AreTomo's -TiltCor: for lamellae milled at an angle or tilted samples (in situ data)."),
        Param("tilt_offset", "float", 999.0, label="Tilt angle offset", unit="°", show_if="method=aretomo", advanced=True,
              help="Above 180: AreTomo2 searches the offset itself."),
        Param("aretomo_ctf", "bool", False, label="Also estimate the CTF", show_if="method=aretomo",
              help="Overwrites the CTF of the input (no CTF estimation job needed)."),
        Param("phase_shift", "bool", False, label="Also estimate the phase shift", show_if="method=aretomo", advanced=True),
        Param("reconstruct", "bool", False, label="Reconstruct the tomograms", show_if="method=aretomo",
              help="AreTomo2 reconstructs them (weighted back-projection): the output is then a tomogram set."),
        Param("only_reconstruct", "bool", False, label="Skip the alignment (reconstruct only)", show_if="method=aretomo",
              advanced=True, help="With alignments already in the input (e.g. from IMOD)."),
        Param("vol_z", "int", 1000, label="Thickness of the reconstruction", unit="px", min=50, max=10000,
              show_if="method=aretomo", help="Unbinned voxels (AreTomo's -VolZ)."),
        Param("out_bin", "int", 4, label="Binning of the reconstruction", min=1, max=20, show_if="method=aretomo"),
        Param("aretomo_args", "str", "", label="Other AreTomo2 arguments", advanced=True, admin_only=True,
              show_if="method=aretomo"),
        mpi(1, help="Tilt series aligned at the same time (with AreTomo2: one GPU each)."),
        Param("gpus", "int", 1, label="GPUs", min=0, max=16, show_if="method=aretomo"),
        extra_args_param(),
    ]
    outputs = [OutputDef("tiltseries", "tiltseries", "Aligned tilt series"),
               OutputDef("tomograms", "tomograms", "Tomograms (AreTomo2)")]

    @classmethod
    def validate(cls, params: dict[str, Any], connected: dict[str, str]) -> list[str]:
        if params.get("method") == "aretomo" and not int(params.get("gpus") or 0):
            return ["AreTomo2 needs a GPU"]
        return []

    @classmethod
    def resources(cls, params: dict[str, Any]) -> dict[str, int]:
        """GPUs for AreTomo2 only (IMOD runs on CPUs)."""
        gpus = int(params.get("gpus") or 0) if params.get("method", "aretomo") == "aretomo" else 0
        return {"num_gpus": gpus, "num_cpus": max(1, int(params.get("mpi") or 1))}

    def run(self, ctx: JobContext) -> None:
        p = ctx.params
        star, ref, tset = tilt_set_input(ctx)
        args = [*relion_command(ctx, "relion_align_tiltseries", p["mpi"]), "--i", ref, "--o", ctx.rel(ctx.job_dir) + "/",
                "--tomogram_thickness", f"{p['thickness']:g}"]
        aretomo_rec = p["method"] == "aretomo" and p["reconstruct"]
        if p["method"] == "imod_fiducials":
            args += ["--imod_fiducials", "--fiducial_diameter", f"{p['fiducial_diameter']:g}",
                     "--batchtomo_exe", tool_executable(ctx, "imod", "IMOD (batchruntomo)")]
        elif p["method"] == "imod_patch":
            args += ["--imod_patchtrack", "--patch_size", f"{p['patch_size']:g}", "--patch_overlap", f"{p['patch_overlap']:g}",
                     "--batchtomo_exe", tool_executable(ctx, "imod", "IMOD (batchruntomo)")]
        else:
            args += ["--aretomo2", "--aretomo_exe", tool_executable(ctx, "aretomo", "AreTomo2")]
            if p["tilt_correct"]:
                args += ["--aretomo_tiltcorrect", "--aretomo_tiltcorrect_angle", f"{p['tilt_offset']:g}"]
            if p["aretomo_ctf"]:
                args.append("--aretomo_ctf")
                if p["phase_shift"]:
                    args.append("--aretomo_phaseshift")
            if aretomo_rec:
                args.append("--aretomo_reconstruct")
                if p["only_reconstruct"]:
                    args.append("--aretomo_only_reconstruct")
                args += ["--aretomo_VolZ", str(p["vol_z"]), "--aretomo_OutBin", str(p["out_bin"])]
            args += ["--other_wrapper_args", f" {str(p['aretomo_args']).strip()} "]
            ngpu = max(1, len(ctx.resources.get("gpus") or []) or int(p["gpus"]))
            args += ["--gpu", ":".join(str(i % ngpu) for i in range(max(1, int(p["mpi"]))))]
        args += ctx.split_extra()
        ctx.add_table("Tilt-series alignment", ["Setting", "Value"], [
            ["Tilt series", f"{len(tset.series):,}"],
            ["Method", {"aretomo": "AreTomo2", "imod_patch": "IMOD patch tracking", "imod_fiducials": "IMOD fiducials"}[p["method"]]],
            ["Thickness", f"{p['thickness']:g} nm"]])
        ctx.progress(0.05, f"relion_align_tiltseries on {len(tset.series)} tilt series")
        run_relion(ctx, args)
        out = ctx.path("tomograms.star" if aretomo_rec else "aligned_tilt_series.star")
        if not out.exists():
            raise JobError(f"relion_align_tiltseries wrote no {out.name} (see the log)")
        aligned = tm.read_set(out, ctx.project_dir)
        g = aligned.global_table
        rows = []
        for ts in aligned.names:
            row = [ts]
            for label in ("rlnIMODResidualErrorMean", "rlnIMODLeaveOutError", "rlnAreTomoTiltAxisScore", "rlnTomoZRot"):
                v = aligned.value(ts, label)
                row.append(f"{float(v):.2f}" if v not in (None, "") else "—")
            rows.append(row)
        ctx.add_table("Alignment", ["Tilt series", "IMOD residual error (nm)", "IMOD leave-out error (nm)",
                                    "AreTomo2 tilt-axis score", "Tilt axis (°)"], rows)
        if g.has("rlnIMODResidualErrorMean"):
            err = g.floats("rlnIMODResidualErrorMean")
            ctx.add_highlight("Residual error", f"{np.median(err):.2f} nm")
        thumb = tilt_series_report(ctx, aligned)
        if aretomo_rec:
            ctx.add_tool("tomograms", "Tomograms", tomogram_index(ctx, aligned), waiting=False, editable=False)
            set_output(ctx, "tomograms", "tomograms", out, "aretomo_reconstruct", "{n} tomograms (AreTomo2)", thumb)
        else:
            set_output(ctx, "tiltseries", "tiltseries", out, "align", "{n} aligned tilt series", thumb)


# ------------------------------------------------------------------ tomogram reconstruction
@register
class RelionTomoReconstruct(RelionJob):
    name = "relion_tomo_reconstruct"
    title = "Reconstruct tomograms (RELION)"
    category = CATEGORY
    description = ("Reconstructs a binned tomogram of every aligned tilt series (relion_tomo_reconstruct_tomogram), "
                   "CTF-corrected by Fourier inversion with a Wiener filter, for looking at the data and picking "
                   "particles (subtomogram averaging goes back to the tilt images). Optionally the tomograms of the "
                   "even and odd frames, for denoising, and a 2D projection of a slab of each tomogram.")
    gpu_param = ""
    cpu_param = "mpi*threads"
    inputs = [Slot("tiltseries", ("tiltseries",), "Tilt series", help="Aligned tilt series (with their CTF).")]
    params = [
        Param("binned_pixel", "float", 10.0, label="Pixel size of the tomograms", unit="Å", min=0.5, max=100,
              help="Binned for picking: 5-10 Å is enough to see particles."),
        Param("width", "int", 0, label="Width", unit="px", min=0, max=20000,
              help="Unbinned pixels; 0: that of the tilt images."),
        Param("height", "int", 0, label="Height", unit="px", min=0, max=20000, help="Unbinned pixels; 0: that of the tilt images."),
        Param("thickness", "int", 2000, label="Thickness", unit="px", min=1, max=20000,
              help="Unbinned pixels: a little more than the sample (keep the empty space small for denoising)."),
        Param("fourier", "bool", True, label="Fourier inversion",
              help="Instead of real-space back-projection: CTF correction with a Wiener filter."),
        Param("ctf_first_peak", "bool", True, label="Ignore CTFs until the first peak",
              help="Less low-resolution contrast boost, better high-resolution detail."),
        Param("skip_wiener", "bool", False, label="Skip the Wiener filter", advanced=True,
              help="Multiply by the CTF only (no division by CTF², no even/odd frames needed)."),
        Param("split", "bool", False, label="Tomograms for denoising",
              help="Also the tomograms of the even and of the odd frames (needs 'Save images for denoising' in motion "
                   "correction; not with Fourier inversion)."),
        Param("projections", "bool", True, label="2D projections of a central slab",
              help="A sum of Z slices of each tomogram, to screen the tomograms at a glance."),
        Param("proj_centre", "int", 0, label="Central slice of the slab", unit="px", advanced=True,
              help="In binned pixels from the middle of the tomogram."),
        Param("proj_thickness", "int", 10, label="Slices in the slab", unit="px", min=1, max=500, advanced=True),
        Param("tilt_offset", "float", 0.0, label="Tilt angle offset", unit="°", min=-25, max=25, advanced=True,
              help="Added to all tilt angles (lamellae milled at an angle)."),
        Param("tomo_name", "str", "", label="Only this tomogram", advanced=True, placeholder="rlnTomoName"),
        mpi(1, help="Tomograms reconstructed at the same time."),
        threads(8),
        extra_args_param(),
    ]
    outputs = [OutputDef("tomograms", "tomograms", "Tomograms")]

    @classmethod
    def validate(cls, params: dict[str, Any], connected: dict[str, str]) -> list[str]:
        if params.get("split") and params.get("fourier", True):
            return ["Tomograms for denoising are reconstructed without Fourier inversion: switch one of them off"]
        return []

    def run(self, ctx: JobContext) -> None:
        p = ctx.params
        star, ref, tset = tilt_set_input(ctx)
        first = next(iter(tset.series.values()))
        if not (first.has("rlnTomoXTilt") or first.has("rlnTomoYTilt")):
            raise JobError("These tilt series are not aligned yet (align them first)")
        if p["split"] and not first.has("rlnMicrographNameEven"):
            raise JobError("Tomograms for denoising need the even/odd sums of motion correction ('Save images for denoising')")
        width, height = int(p["width"]), int(p["height"])
        if not (width and height) and first.has("rlnMicrographName"):
            from cryoplug.micrographs import micrograph_shape
            try:
                h, w = micrograph_shape(ctx.abs(first.column("rlnMicrographName")[0]))
            except (OSError, ValueError) as exc:
                raise JobError(f"Give the width and height of the tomograms (the tilt images cannot be read: {exc})") from None
            width, height = width or w, height or h
            ctx.log(f"Tomogram size from the tilt images: {width} × {height} px")
        args = [*relion_command(ctx, "relion_tomo_reconstruct_tomogram", p["mpi"]), "--t", ref, "--o", ctx.rel(ctx.job_dir) + "/"]
        if str(p["tomo_name"]).strip():
            args += ["--tn", str(p["tomo_name"]).strip()]
        if p["split"]:
            args.append("--generate_split_tomograms")
        args += ["--w", str(width), "--h", str(height), "--d", str(p["thickness"]), "--binned_angpix", f"{p['binned_pixel']:g}"]
        if p["fourier"]:
            args.append("--fourier")
            if p["ctf_first_peak"]:
                args.append("--ctf_intact_first_peak")
            if p["skip_wiener"]:
                args.append("--skip_fourier_wiener")
        if p["projections"]:
            args += ["--do_proj", "--centre_proj", str(p["proj_centre"]), "--thickness_proj", str(p["proj_thickness"])]
        if abs(p["tilt_offset"]) > 0:
            args += ["--tiltangle_offset", f"{p['tilt_offset']:g}"]
        args += ["--j", str(p["threads"]), *ctx.split_extra()]
        n = 1 if str(p["tomo_name"]).strip() else len(tset.series)
        ctx.add_table("Reconstruction", ["Setting", "Value"], [
            ["Tomograms", f"{n:,}"], ["Size (unbinned)", f"{width} × {height} × {p['thickness']} px"],
            ["Pixel size", f"{p['binned_pixel']:g} Å"], ["Method", "Fourier inversion" if p["fourier"] else "back-projection"],
            ["Even/odd tomograms", "yes" if p["split"] else "no"]])
        ctx.progress(0.05, f"relion_tomo_reconstruct_tomogram ({n} tomograms)")
        with FileProgress(ctx, ctx.path("tomograms"), "*.mrc", n * (2 if p["split"] else 1), "Tomograms"):
            run_relion(ctx, args)
        out = ctx.path("tomograms.star")
        if not out.exists() and str(p["tomo_name"]).strip():
            self.single_tomogram_set(ctx, tset, str(p["tomo_name"]).strip(), width, height)
        if not out.exists():
            raise JobError("relion_tomo_reconstruct_tomogram wrote no tomograms.star (see the log)")
        rec = tm.read_set(out, ctx.project_dir)
        tomogram_report(ctx, rec)

    @staticmethod
    def single_tomogram_set(ctx: JobContext, tset: tm.TiltSeriesSet, name: str, width: int, height: int) -> None:
        """The tomogram set of a single reconstruction (--tn), which RELION does not write: the input row of that tilt
        series with the files and geometry of its tomogram."""
        p = ctx.params
        if name not in tset.names:
            raise JobError(f"No tilt series {name} in the input")
        if p["split"]:
            files = {"rlnTomoReconstructedTomogramHalf1": f"tomograms/rec_{name}_half1.mrc",
                     "rlnTomoReconstructedTomogramHalf2": f"tomograms/rec_{name}_half2.mrc"}
            if p["projections"]:
                files.update({"rlnTomogramProjectionHalf1": f"projections/rec_{name}_half1.mrc",
                              "rlnTomogramProjectionHalf2": f"projections/rec_{name}_half2.mrc"})
        else:
            files = {"rlnTomoReconstructedTomogram": f"tomograms/rec_{name}.mrc"}
            if p["projections"]:
                files["rlnTomogramProjection"] = f"projections/rec_{name}.mrc"
        if not all(ctx.path(f).exists() for f in files.values()):
            return
        one = tm.TiltSeriesSet(tset.global_table.select([tset.names.index(name)]), {name: tset.series[name]})
        g = one.global_table
        pixel = float(tset.value(name, "rlnTomoTiltSeriesPixelSize") or tset.value(name, "rlnMicrographOriginalPixelSize") or 0)
        if pixel and abs(p["binned_pixel"] / pixel - 1) > 1e-2:
            g.set_column("rlnTomoTomogramBinning", [f"{p['binned_pixel'] / pixel:.6g}"])
        for label, value in (("rlnTomoSizeX", width), ("rlnTomoSizeY", height), ("rlnTomoSizeZ", int(p["thickness"]))):
            g.set_column(label, [str(value)])
        for label, f in files.items():
            g.set_column(label, [ctx.rel(ctx.path(f))])
        tm.write_set(ctx.path("tomograms.star"), one, ctx.project_dir,
                     comment=f"Tomogram {name} (written by CryoPlug: RELION writes no tomogram set for a single tomogram)")
        ctx.log(f"Single tomogram: tomograms.star written for {name}")


def tomogram_report(ctx: JobContext, rec: tm.TiltSeriesSet, kind: str = "reconstruct", **extra: Any) -> None:
    """The projections (or central slices) of the tomograms, the slice viewer and the output."""
    thumb = None
    names = [v for v in (tm.tomogram_file(rec, ts, ctx.project_dir, tm.PROJECTION_LABELS) for ts in rec.names) if v]
    if names:
        png = micrograph_gallery(ctx, sample(names, 6), "projections", "Tomograms (2D projections of a central slab)",
                                 size=300, per_row=3)
        if png is not None and names:
            from cryoplug.micrographs import preview, read_image
            try:
                thumb = thumbnail_of(ctx, preview(read_image(ctx.abs(names[0])), 256, sigma=0.8)[0], "tomogram")
            except Exception as exc:  # cosmetic
                ctx.warn(f"No thumbnail: {exc}")
    if thumb is None:
        thumb = central_slice_thumbnail(ctx, rec)
    index = tomogram_index(ctx, rec)
    ctx.add_tool("tomograms", "Tomograms", index, waiting=False, editable=False)
    meta = tm.set_meta(ctx.rel(ctx.path("tomograms.star")), rec, kind, **extra)
    if meta.get("binned_pixel_size"):
        ctx.add_highlight("Pixel size", f"{meta['binned_pixel_size']:g} Å")
    ctx.add_highlight("Tomograms", f"{len(rec.series):,}")
    ctx.add_output("tomograms", "tomograms", ctx.path("tomograms.star"), f"{len(rec.series)} tomograms", meta=meta,
                   thumbnail=thumb)


def central_slice_thumbnail(ctx: JobContext, rec: tm.TiltSeriesSet) -> Path | None:
    from cryoplug.micrographs import preview
    from cryoplug.mrc import MapVolume
    for ts in rec.names[:1]:
        path = tm.tomogram_file(rec, ts, ctx.project_dir)
        if not path:
            continue
        try:
            vol = MapVolume.read(ctx.abs(path))
            z = vol.data.shape[0] // 2
            slab = vol.data[max(0, z - 2):z + 3].mean(axis=0)
            return thumbnail_of(ctx, preview(slab, 256, sigma=0.8)[0], "tomogram")
        except Exception as exc:  # cosmetic
            ctx.warn(f"No thumbnail: {exc}")
    return None


# ------------------------------------------------------------------ denoising
@register
class RelionTomoDenoise(RelionJob):
    name = "relion_tomo_denoise"
    title = "Denoise tomograms (cryoCARE)"
    category = CATEGORY
    description = ("Denoises the tomograms with cryoCARE (relion_python_tomo_denoise): a network learns, on the "
                   "tomograms of the even and of the odd frames of a few tilt series (Noise2Noise), to remove the noise, "
                   "then denoises every tomogram. Much easier picking; the averaging still uses the raw tilt images.")
    gpu_param = "gpus"
    cpu_param = ""
    inputs = [Slot("tomograms", ("tomograms",), "Tomograms",
                   help="Reconstructed with 'Tomograms for denoising' (the even/odd half tomograms). A training job "
                        "connected here gives its model to a denoising job.")]
    params = [
        Param("mode", "choice", "both", choices=["both", "train", "predict"], label="Steps",
              labels={"both": "Train a model, then denoise", "train": "Train a model only",
                      "predict": "Denoise with a trained model"}),
        Param("training", "str", "", label="Tomograms to train on", placeholder="TS_01:TS_03",
              show_if="mode=both|train",
              help="Names separated by ':' (their defocus should span that of the data). Empty: three spread over the set."),
        Param("subvolumes", "int", 1200, label="Sub-volumes per tomogram", min=100, max=10000, show_if="mode=both|train"),
        Param("subvolume_size", "int", 72, label="Sub-volume size", unit="px", min=32, max=256, show_if="mode=both|train"),
        Param("model", "path", "", label="Trained model", show_if="mode=predict", placeholder="from the input",
              help="denoising_model.tar.gz of a training job (empty: that of the job that made the input)."),
        Param("tiles", "str", "2,2,2", label="Tiles (X,Y,Z)", show_if="mode=both|predict",
              help="Raise them on a GPU out of memory (e.g. 4,4,2)."),
        Param("tomo_name", "str", "", label="Only this tomogram", advanced=True, show_if="mode=both|predict"),
        Param("gpus", "int", 1, label="GPUs", min=1, max=1, help="cryoCARE uses a single GPU."),
        extra_args_param(),
    ]
    outputs = [OutputDef("tomograms", "tomograms", "Denoised tomograms")]

    def run(self, ctx: JobContext) -> None:
        p = ctx.params
        inp = ctx.require("tomograms")
        star, ref, tset = tilt_set_input(ctx, "tomograms")
        g = tset.global_table
        if not g.has("rlnTomoReconstructedTomogramHalf1"):
            raise JobError("These tomograms have no even/odd halves: reconstruct them with 'Tomograms for denoising'")
        exe = tool_executable(ctx, "cryocare", "cryoCARE")
        cryocare_dir = os.path.dirname(exe) if os.path.isabs(exe) else ""
        prog = ctx.program(TOOL, "relion_python_tomo_denoise")
        out_dir = ctx.rel(ctx.job_dir) + "/"
        model = ""
        if p["mode"] in ("both", "train"):
            names = [n for n in str(p["training"]).replace(",", ":").split(":") if n.strip()]
            unknown = [n for n in names if n not in tset.names]
            if unknown:
                raise JobError(f"Unknown tomograms: {', '.join(unknown)}")
            if not names:
                names = sample(tset.names, 3)
                ctx.log(f"Training on {', '.join(names)}")
            args = [prog, "cryoCARE:train", "--tomogram-star-file", ref, *(["--cryocare-path", cryocare_dir] if cryocare_dir else []),
                    "--output-directory", out_dir, "--gpu", "0", "--training-tomograms", ":".join(names),
                    "--number-training-subvolumes", str(p["subvolumes"]), "--subvolume-sidelength", str(p["subvolume_size"]),
                    *ctx.split_extra()]
            ctx.progress(0.05, f"cryoCARE: training on {len(names)} tomograms")
            run_relion(ctx, args)
            model = ctx.rel(ctx.path("denoising_model.tar.gz"))
            if not ctx.path("denoising_model.tar.gz").exists():
                raise JobError("cryoCARE wrote no denoising_model.tar.gz: the training failed (see the log)")
        if p["mode"] in ("both", "predict"):
            if not model:
                given = str(p["model"]).strip()
                model = given or str(inp.meta.get("denoising_model") or "")
                if not model:
                    raise JobError("Give the trained model (denoising_model.tar.gz), or connect the tomograms of a training job")
                if not ctx.abs(model).exists() and not Path(model).exists():
                    raise JobError(f"The model {model} is missing")
            tiles = [t.strip() for t in str(p["tiles"]).replace(" ", ",").split(",") if t.strip()]
            if len(tiles) != 3 or not all(t.isdigit() for t in tiles):
                raise JobError("Tiles: three whole numbers (X,Y,Z)")
            args = [prog, "cryoCARE:predict", "--tomogram-star-file", ref,
                    *(["--cryocare-path", cryocare_dir] if cryocare_dir else []), "--output-directory", out_dir, "--gpu", "0"]
            if str(p["tomo_name"]).strip():
                args += ["--tomogram-name", str(p["tomo_name"]).strip()]
            args += ["--model-file", model, "--n-tiles", *tiles, *ctx.split_extra()]
            ctx.progress(0.5, "cryoCARE: denoising the tomograms")
            run_relion(ctx, args)
        out = ctx.path("tomograms.star")
        if not out.exists():
            raise JobError("relion_python_tomo_denoise wrote no tomograms.star (see the log)")
        rec = tm.read_set(out, ctx.project_dir)
        extra = {"denoising_model": model} if model else {}
        if p["mode"] == "train":
            ctx.add_highlight("Model", "trained")
        tomogram_report(ctx, rec, "denoise", **extra)
