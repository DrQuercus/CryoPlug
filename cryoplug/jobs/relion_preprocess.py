"""RELION preprocessing: import of movies or micrographs, motion correction, CTF estimation, micrograph curation.

The import writes the ``movies.star`` (or ``micrographs.star``) that relion_import writes, with the optics group of
the data set. The movie folders are linked into the job folder so that RELION reads them with project-relative paths
(RELION refuses absolute ones). Motion correction and CTF estimation run relion_run_motioncorr and relion_run_ctffind
with the options of RELION 5.1's GUI.
"""
from __future__ import annotations

import glob
import os
import re
import struct
from pathlib import Path
from typing import Any

import numpy as np

from cryoplug import relion as rl
from cryoplug.jobs import register
from cryoplug.jobs.base import JobContext, JobError, JobType, OutputDef, Param, Slot, extra_args_param
from cryoplug.jobs.relion_common import (
    FileProgress, RelionJob, histogram, micrograph_gallery, micrograph_meta, mpi, relion_command, run_relion,
    sample, star_of, thumbnail_of, threads,
)

MOVIE_SUFFIXES = (".tif", ".tiff", ".eer", ".mrc", ".mrcs", ".mrc.bz2", ".mrcs.bz2", ".mrc.zst", ".mrcs.zst", ".mrc.xz",
                  ".mrcs.xz")
GAIN_ROTATIONS = ["0", "1", "2", "3"]
GAIN_FLIPS = ["0", "1", "2"]
SAFE_GROUP = re.compile(r"[A-Za-z0-9-]+")


# ------------------------------------------------------------------ movie headers
def tiff_info(path: Path) -> dict[str, int]:
    """Frames, width and height of a TIFF or EER movie from its directory of images (no image data read)."""
    with open(path, "rb") as fh:
        head = fh.read(16)
        order = {b"II": "<", b"MM": ">"}.get(head[:2])
        if order is None:
            raise ValueError("not a TIFF file")
        version = struct.unpack(order + "H", head[2:4])[0]
        big = version == 43
        offset = struct.unpack(order + "Q", head[8:16])[0] if big else struct.unpack(order + "I", head[4:8])[0]
        frames, width, height = 0, 0, 0
        while offset and frames < 100000:
            fh.seek(offset)
            count = struct.unpack(order + ("Q" if big else "H"), fh.read(8 if big else 2))[0]
            entry = 20 if big else 12
            raw = fh.read(count * entry)
            if frames == 0:
                for i in range(count):
                    e = raw[i * entry:(i + 1) * entry]
                    tag, typ = struct.unpack(order + "HH", e[:4])
                    value = e[12:] if big else e[8:]
                    v = struct.unpack(order + ("H" if typ == 3 else "I"), value[:2] if typ == 3 else value[:4])[0]
                    if tag == 256:
                        width = v
                    elif tag == 257:
                        height = v
            frames += 1
            nxt = fh.read(8 if big else 4)
            offset = struct.unpack(order + ("Q" if big else "I"), nxt)[0] if len(nxt) == (8 if big else 4) else 0
    return {"frames": frames, "width": width, "height": height}


def movie_info(path: Path) -> dict[str, int] | None:
    """Frames and size of a movie (MRC, TIFF or EER); None when the format cannot be read here (compressed)."""
    name = path.name.lower()
    try:
        if name.endswith((".mrc", ".mrcs")):
            import mrcfile
            with mrcfile.open(str(path), permissive=True, header_only=True) as m:
                h = m.header
                return {"frames": int(h.nz), "width": int(h.nx), "height": int(h.ny)}
        if name.endswith((".tif", ".tiff", ".eer")):
            return tiff_info(path)
    except (OSError, ValueError, struct.error):
        return None
    return None


def find_movies(pattern: str) -> list[Path]:
    """Movies matching a wildcard, or all the movies of a folder."""
    raw = os.path.expanduser(pattern.strip())
    if os.path.isdir(raw):
        files = [p for p in Path(raw).iterdir() if p.is_file() and p.name.lower().endswith(MOVIE_SUFFIXES)]
    else:
        files = [Path(p) for p in glob.glob(raw, recursive=True) if Path(p).is_file()]
    return sorted(files, key=lambda p: str(p))


def link_folders(ctx: JobContext, files: list[Path], subdir: str = "movies") -> list[str]:
    """Links the folders of the files into the job folder; returns their project-relative names (as RELION reads
    them). Folders of the same name are numbered."""
    base = ctx.path(subdir)
    base.mkdir(exist_ok=True)
    links: dict[Path, Path] = {}
    used: set[str] = set()
    out = []
    for f in files:
        folder = f.resolve().parent
        if folder not in links:
            stem = re.sub(r"[^A-Za-z0-9._-]+", "_", folder.name) or "data"
            name, k = stem, 1
            while name in used:
                k += 1
                name = f"{stem}_{k}"
            used.add(name)
            link = base / name
            if link.is_symlink() or link.exists():
                link.unlink()
            link.symlink_to(folder)
            links[folder] = link
        out.append(link_rel(ctx, links[folder] / f.name))
    return out


def link_rel(ctx: JobContext, link: Path) -> str:
    """The project-relative name of a link in the job folder (ctx.rel would follow the link out of the project)."""
    return os.path.relpath(link, ctx.project_dir)


def link_file(ctx: JobContext, path: str, name: str) -> str:
    """A file linked into the job folder under ``name`` (its extension kept); project-relative."""
    src = Path(os.path.expanduser(path)).resolve()
    if not src.exists():
        raise JobError(f"{path} does not exist")
    suffix = "".join(src.suffixes[-2:]) if src.name.endswith((".bz2", ".zst", ".xz")) else src.suffix
    link = ctx.path(name + suffix)
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(src)
    return link_rel(ctx, link)


# ------------------------------------------------------------------ import
@register
class RelionImport(JobType):
    name = "relion_import"
    title = "Import movies (RELION)"
    category = "Import"
    software = ["RELION"]
    description = ("Imports raw movies (or single-frame micrographs) with their optics group: pixel size, voltage, "
                   "spherical aberration, amplitude contrast, MTF and gain reference. Writes the movies.star of "
                   "relion_import; the movie folders are linked into the job folder, nothing is copied. The start of "
                   "the RELION pipeline: motion correction, CTF estimation, picking...")
    inputs = []
    params = [
        Param("movies", "path", "", label="Movies", required=True, placeholder="/data/session/Movies/*.tiff",
              help="A wildcard selecting the movies (TIFF, EER, MRC, compressed MRC), or their folder. '**' searches "
                   "sub-folders (e.g. /data/session/**/*_fractions.tiff)."),
        Param("kind", "choice", "movies", choices=["movies", "micrographs"], label="Data",
              labels={"movies": "Multi-frame movies", "micrographs": "Single-frame micrographs (already aligned)"}),
        Param("pixel_size", "float", 0.0, label="Pixel size", unit="Å", min=0.0, max=20, required=True,
              help="Of the movies as recorded (super-resolution pixel size for super-resolution movies: bin them in "
                   "motion correction)."),
        Param("voltage", "float", 300.0, label="Voltage", unit="kV", min=50, max=500),
        Param("cs", "float", 2.7, label="Spherical aberration", unit="mm", min=0, max=10,
              help="2.7 (Titan Krios, Talos, most CRYO ARMs), 1.4 (some CRYO ARMs), 0.01 (Cs corrector)."),
        Param("amplitude_contrast", "float", 0.1, label="Amplitude contrast", min=0, max=0.5,
              help="RELION's default of 0.1 works better than the theoretical 0.07 in practice."),
        Param("dose_per_frame", "float", 0.0, label="Dose per frame", unit="e⁻/Å²", min=0,
              help="Electrons per Å² in each frame (fraction for EER): used by motion correction for dose weighting. "
                   "0 = give it in motion correction."),
        Param("gain", "path", "", label="Gain reference", placeholder="optional",
              help="Gain reference (MRC, or .gain for EER) when the movies are not gain-corrected."),
        Param("gain_rotation", "choice", "0", choices=GAIN_ROTATIONS, label="Gain rotation",
              labels={"0": "No rotation", "1": "90° (1)", "2": "180° (2)", "3": "270° (3)"},
              help="Rotation of the gain reference, clockwise as shown by relion_display (MotionCor2's -RotGain)."),
        Param("gain_flip", "choice", "0", choices=GAIN_FLIPS, label="Gain flip",
              labels={"0": "No flipping", "1": "Upside down (1)", "2": "Left to right (2)"},
              help="Flip of the gain reference after rotation (MotionCor2's -FlipGain)."),
        Param("defect_file", "path", "", label="Defect file", advanced=True,
              help="UCSF MotionCor2-style defect list, or a defect map (1 = bad pixel), used in motion correction."),
        Param("mtf", "path", "", label="Detector MTF", advanced=True, placeholder="mtf_k3_300kv.star",
              help="MTF curve of the detector (STAR file from the RELION wiki): used by post-processing."),
        Param("optics_group", "str", "opticsGroup1", label="Optics group name", advanced=True,
              help="Letters, digits and '-' only. Data sets imported separately keep their own optics groups."),
        Param("beamtilt_x", "float", 0.0, label="Beam tilt X", unit="mrad", advanced=True),
        Param("beamtilt_y", "float", 0.0, label="Beam tilt Y", unit="mrad", advanced=True),
    ]
    outputs = [OutputDef("movies", "movies", "Movies"), OutputDef("micrographs", "micrographs", "Micrographs")]

    @classmethod
    def validate(cls, params: dict[str, Any], connected: dict[str, str]) -> list[str]:
        problems = []
        if not float(params.get("pixel_size") or 0) > 0:
            problems.append("Give the pixel size of the movies")
        if not SAFE_GROUP.fullmatch(str(params.get("optics_group") or "")):
            problems.append("The optics group name may only hold letters, digits and '-'")
        return problems

    def run(self, ctx: JobContext) -> None:
        p = ctx.params
        files = find_movies(p["movies"])
        if not files:
            raise JobError(f"No file matches {p['movies']} (give a wildcard such as /data/Movies/*.tiff, or the folder)")
        movies = p["kind"] == "movies"
        ctx.progress(0.1, f"Linking the folders of {len(files):,} {'movies' if movies else 'micrographs'}")
        names = link_folders(ctx, files, "movies" if movies else "micrographs")
        optics = rl.StarTable("optics")
        cols: list[tuple[str, Any]] = [("rlnOpticsGroupName", p["optics_group"]), ("rlnOpticsGroup", 1)]
        mtf = link_file(ctx, p["mtf"], "mtf") if str(p["mtf"]).strip() else ""
        if mtf:
            cols.append(("rlnMtfFileName", mtf))
        if not movies:
            cols.append(("rlnMicrographPixelSize", p["pixel_size"]))
        cols += [("rlnMicrographOriginalPixelSize", p["pixel_size"]), ("rlnVoltage", p["voltage"]),
                 ("rlnSphericalAberration", p["cs"]), ("rlnAmplitudeContrast", p["amplitude_contrast"])]
        if p["beamtilt_x"] or p["beamtilt_y"]:
            cols += [("rlnBeamTiltX", p["beamtilt_x"]), ("rlnBeamTiltY", p["beamtilt_y"])]
        optics.labels = [c[0] for c in cols]
        optics.rows = [[rl._fmt(c[1]) for c in cols]]
        table = rl.StarTable("movies" if movies else "micrographs")
        table.set_column("rlnMicrographMovieName" if movies else "rlnMicrographName", names)
        table.set_column("rlnOpticsGroup", [1] * len(names))
        star = rl.write_star(ctx.path("movies.star" if movies else "micrographs.star"), [optics, table],
                             comment=f"Written by CryoPlug as relion_import does: {len(names)} files from {p['movies']}")
        meta: dict[str, Any] = {"n_movies" if movies else "n_micrographs": len(names), "pixel_size": p["pixel_size"],
                                "voltage": p["voltage"], "cs": p["cs"], "amplitude_contrast": p["amplitude_contrast"],
                                "optics_groups": 1, "relion": {"star": ctx.rel(star), "kind": "import"}}
        if p["dose_per_frame"]:
            meta["dose_per_frame"] = p["dose_per_frame"]
        if str(p["gain"]).strip():
            meta["gain"] = link_file(ctx, p["gain"], "gain_reference")
            meta["gain_rotation"], meta["gain_flip"] = int(p["gain_rotation"]), int(p["gain_flip"])
        if str(p["defect_file"]).strip():
            meta["defect_file"] = link_file(ctx, p["defect_file"], "defects")
        if mtf:
            meta["mtf"] = mtf
        info = movie_info(files[0])
        if info:
            meta.update({"frames": info["frames"], "dims": [info["width"], info["height"]]})
        rows = [["Files", f"{len(names):,}"], ["Pixel size", f"{p['pixel_size']:g} Å"],
                ["Voltage", f"{p['voltage']:g} kV"], ["Spherical aberration", f"{p['cs']:g} mm"],
                ["Amplitude contrast", f"{p['amplitude_contrast']:g}"]]
        if info:
            rows.append(["First file", f"{info['width']} × {info['height']} px, {info['frames']} frame(s)"])
        if p["dose_per_frame"]:
            rows.append(["Dose per frame", f"{p['dose_per_frame']:g} e⁻/Å²"
                         + (f" (total {p['dose_per_frame'] * info['frames']:.1f})" if info and info["frames"] > 1 else "")])
        if meta.get("gain"):
            rows.append(["Gain reference", f"{p['gain']} (rotation {p['gain_rotation']}, flip {p['gain_flip']})"])
        ctx.add_table("Import", ["Setting", "Value"], rows)
        ctx.add_table("Files (first 10)", ["File"], [[n] for n in names[:10]])
        ctx.add_highlight("Movies" if movies else "Micrographs", f"{len(names):,}")
        thumb = None
        if not movies:
            from cryoplug.micrographs import preview, read_image
            try:
                thumb = thumbnail_of(ctx, preview(read_image(files[0]), 256)[0], "micrographs")
            except Exception as exc:  # cosmetic
                ctx.warn(f"No preview of {files[0].name}: {exc}")
        if movies:
            ctx.add_output("movies", "movies", star, f"{len(names):,} movies", meta=meta)
        else:
            meta["has_ctf"] = False
            ctx.add_output("micrographs", "micrographs", star, f"{len(names):,} micrographs", meta=meta, thumbnail=thumb)


# ------------------------------------------------------------------ motion correction
@register
class RelionMotionCorr(RelionJob):
    name = "relion_motioncorr"
    title = "Motion correction (RELION)"
    category = "Preprocessing"
    description = ("Aligns the frames of each movie (beam-induced motion, in patches) and sums them with dose "
                   "weighting: RELION's own CPU implementation of the MotionCor2 algorithm, or UCSF MotionCor2 on GPUs. "
                   "Also writes the summed power spectra that CTFFIND uses. Gives the micrographs and the motion of "
                   "every movie (for Bayesian polishing).")
    gpu_param = "gpus"
    inputs = [Slot("movies", ("movies",), "Movies")]
    params = [
        Param("implementation", "choice", "relion", choices=["relion", "motioncor2"], label="Program",
              labels={"relion": "RELION's own (CPU)", "motioncor2": "UCSF MotionCor2 / MotionCor3 (GPU)"},
              help="RELION's implementation runs on CPUs with threads (fastest when the number of frames divides by "
                   "the threads); MotionCor2 needs a GPU per MPI process. The results are almost identical."),
        Param("dose_per_frame", "float", 0.0, label="Dose per frame", unit="e⁻/Å²", min=0,
              help="0 = the dose given at import."),
        Param("pre_exposure", "float", 0.0, label="Pre-exposure", unit="e⁻/Å²", min=0),
        Param("dose_weighting", "bool", True, label="Dose weighting"),
        Param("save_nodw", "bool", False, label="Also save non-dose-weighted sums",
              help="Sometimes better for CTF estimation; CTF refinement always uses the dose-weighted particles."),
        Param("patches_x", "int", 5, label="Patches in X", min=1, max=20,
              help="Local motion in patches (RELION's GUI default is 1: whole frames only; 5 × 5 suits K2/K3/Falcon)."),
        Param("patches_y", "int", 5, label="Patches in Y", min=1, max=20),
        Param("bfactor", "float", 150.0, label="B-factor", unit="Å²", min=0, max=1500,
              help="Applied to the frames for the alignment only."),
        Param("binning", "float", 1.0, label="Binning", min=1, max=8,
              help="Fourier binning of the sums: 2 for super-resolution movies. The micrograph size must stay even."),
        Param("first_frame", "int", 1, label="First frame", min=1),
        Param("last_frame", "int", -1, label="Last frame", help="0 or less: all the frames."),
        Param("group_frames", "int", 1, label="Group frames", min=1, max=10,
              help="Frames averaged before the alignment (low dose per frame)."),
        Param("eer_grouping", "int", 32, label="EER fractionation", min=1, max=1000,
              help="Hardware frames per fraction for Falcon 4 EER movies (the dose per frame is then per fraction)."),
        Param("float16", "bool", True, label="Write in float16",
              help="Half the disc space (RELION reads it; Gctf does not). Needs the power spectra for CTFFIND."),
        Param("save_power_spectra", "bool", True, label="Save summed power spectra",
              help="Non-dose-weighted power spectra summed every few e⁻/Å²: a better signal for CTFFIND (RELION only)."),
        Param("ps_dose", "float", 4.0, label="Sum power spectra every", unit="e⁻/Å²", min=0.5, max=20, advanced=True,
              help="McMullan et al. (2015): 4 e⁻/Å² gives the best Thon rings."),
        Param("gain", "path", "", label="Gain reference", advanced=True, placeholder="from the import",
              help="Overrides the gain reference of the import."),
        Param("gain_rotation", "choice", "import", choices=["import", *GAIN_ROTATIONS], label="Gain rotation",
              advanced=True, labels={"import": "As imported", "0": "No rotation", "1": "90° (1)", "2": "180° (2)",
                                     "3": "270° (3)"}),
        Param("gain_flip", "choice", "import", choices=["import", *GAIN_FLIPS], label="Gain flip", advanced=True,
              labels={"import": "As imported", "0": "No flipping", "1": "Upside down (1)", "2": "Left to right (2)"}),
        Param("defect_file", "path", "", label="Defect file", advanced=True, placeholder="from the import"),
        Param("motioncor2_args", "str", "", label="Other MotionCor2 arguments", advanced=True, admin_only=True),
        mpi(1),
        threads(8, help="Threads per MPI process for RELION's implementation (--j)."),
        Param("gpus", "int", 0, label="GPUs", min=0, max=16, help="For MotionCor2: one per MPI process."),
        extra_args_param(),
    ]
    outputs = [OutputDef("micrographs", "micrographs", "Motion-corrected micrographs")]

    @classmethod
    def validate(cls, params: dict[str, Any], connected: dict[str, str]) -> list[str]:
        if params.get("implementation") == "motioncor2" and not int(params.get("gpus") or 0):
            return ["MotionCor2 needs at least one GPU"]
        return []

    def run(self, ctx: JobContext) -> None:
        p = ctx.params
        movies = ctx.require("movies")
        star, ref = star_of(ctx, movies)
        n = rl.star_rows(star)
        meta = movies.meta
        dose = float(p["dose_per_frame"] or meta.get("dose_per_frame") or 0)
        own = p["implementation"] == "relion"
        if not dose and (p["dose_weighting"] or (own and p["save_power_spectra"])):
            raise JobError("Give the dose per frame (here or at import): dose weighting and the power spectra need it")
        float16, save_ps = bool(p["float16"]), bool(p["save_power_spectra"])
        if not own and (float16 or save_ps):
            ctx.log("MotionCor2 writes neither float16 files nor power spectra: both switched off")
            float16 = save_ps = False
        if own and float16 and not save_ps:
            ctx.log("float16 micrographs need the power spectra for CTFFIND: saving them")
            save_ps = True
        out_dir = ctx.rel(ctx.job_dir) + "/"
        args = [*relion_command(ctx, "relion_run_motioncorr", p["mpi"]), "--i", ref, "--o", out_dir,
                "--first_frame_sum", str(p["first_frame"]), "--last_frame_sum", str(p["last_frame"])]
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
        defect = str(p["defect_file"]).strip() or meta.get("defect_file", "")
        if defect:
            args += ["--defect_file", ctx.rel(ctx.abs(defect)) if not os.path.isabs(defect) else defect]
        args += ["--bin_factor", f"{p['binning']:g}", "--bfactor", f"{p['bfactor']:g}", "--dose_per_frame", f"{dose:g}",
                 "--preexposure", f"{p['pre_exposure']:g}", "--patch_x", str(p["patches_x"]),
                 "--patch_y", str(p["patches_y"]), "--eer_grouping", str(p["eer_grouping"])]
        if p["group_frames"] > 1:
            args += ["--group_frames", str(p["group_frames"])]
        gain = str(p["gain"]).strip() or meta.get("gain", "")
        if gain:
            rot = meta.get("gain_rotation", 0) if p["gain_rotation"] == "import" else int(p["gain_rotation"])
            flip = meta.get("gain_flip", 0) if p["gain_flip"] == "import" else int(p["gain_flip"])
            gain_ref = ctx.rel(Path(os.path.expanduser(gain))) if os.path.isabs(os.path.expanduser(gain)) else gain
            args += ["--gainref", gain_ref, "--gain_rot", str(rot), "--gain_flip", str(flip)]
        if p["dose_weighting"]:
            args.append("--dose_weighting")
            if p["save_nodw"]:
                args.append("--save_noDW")
        if save_ps:
            args += ["--grouping_for_ps", str(max(1, round(p["ps_dose"] / dose)))]
        args += ctx.split_extra()
        ctx.add_table("Motion correction", ["Setting", "Value"], [
            ["Movies", f"{n:,}"], ["Program", "RELION (CPU)" if own else "MotionCor2 (GPU)"],
            ["Patches", f"{p['patches_x']} × {p['patches_y']}"], ["Dose per frame", f"{dose:g} e⁻/Å²"],
            ["Binning", f"{p['binning']:g}"], ["Gain reference", gain or "none"],
            ["Dose weighting", "yes" if p["dose_weighting"] else "no"]])
        ctx.progress(0.03, f"relion_run_motioncorr on {n:,} movies")
        with FileProgress(ctx, ctx.job_dir, "*.star", n + 1, "Movies aligned"):
            run_relion(ctx, args)
        out = ctx.path("corrected_micrographs.star")
        if not out.exists():
            raise JobError("relion_run_motioncorr wrote no corrected_micrographs.star (see the log)")
        self.report(ctx, out, n)

    def report(self, ctx: JobContext, out: Path, n_movies: int) -> None:
        tables = rl.read_star(out)
        mics = rl.micrograph_table(tables)
        names = mics.column("rlnMicrographName")
        if not names:
            raise JobError("No movie was corrected (see the log)")
        if len(names) < n_movies:
            ctx.warn(f"{n_movies - len(names)} movie(s) failed (see the log): {len(names)} micrographs")
        rows = []
        if mics.has("rlnAccumMotionTotal"):
            total = mics.floats("rlnAccumMotionTotal")
            x = list(range(1, len(names) + 1))
            series = [{"name": "Total", "x": x, "y": [round(float(v), 2) for v in total]}]
            for lab, nm in (("rlnAccumMotionEarly", "Early"), ("rlnAccumMotionLate", "Late")):
                if mics.has(lab):
                    series.append({"name": nm, "x": x, "y": [round(float(v), 2) for v in mics.floats(lab)]})
            ctx.add_plot("Accumulated motion per micrograph", series, x_label="Micrograph", y_label="Motion (Å)")
            ctx.add_plot("Total motion", [{"name": "Micrographs", **histogram(total)}], x_label="Total motion (Å)",
                         y_label="Micrographs", style="bars")
            rows += [["Median total motion", f"{np.median(total):.1f} Å"], ["Largest", f"{total.max():.1f} Å"]]
            ctx.add_highlight("Motion", f"{np.median(total):.1f} Å")
        if mics.has("rlnMicrographMetadata"):
            self.trajectory(ctx, ctx.abs(mics.column("rlnMicrographMetadata")[0]), names[0])
        gallery = micrograph_gallery(ctx, sample(names, 4), "micrographs", "Micrographs")
        thumb = None
        if gallery is not None:
            from cryoplug.micrographs import preview, read_image
            try:
                thumb = thumbnail_of(ctx, preview(read_image(ctx.abs(names[0])), 256)[0], "micrographs")
            except Exception as exc:  # cosmetic
                ctx.warn(f"No thumbnail: {exc}")
        ctx.add_table("Motion", ["", "Value"], [["Micrographs", f"{len(names):,}"], *rows])
        ctx.add_highlight("Micrographs", f"{len(names):,}")
        meta = micrograph_meta(ctx, out, "motioncorr", has_motion=True,
                               power_spectra=mics.has("rlnCtfPowerSpectrum"), nodw=mics.has("rlnMicrographNameNoDW"))
        ctx.add_output("micrographs", "micrographs", out, f"{len(names):,} micrographs", meta=meta, thumbnail=thumb)

    @staticmethod
    def trajectory(ctx: JobContext, path: Path, name: str) -> None:
        if not path.exists():
            return
        try:
            shifts = rl.read_star(path).get("global_shift")
            if shifts is None or not shifts.rows:
                return
            x, y = shifts.floats("rlnMicrographShiftX"), shifts.floats("rlnMicrographShiftY")
        except (OSError, ValueError, KeyError):
            return
        ctx.add_plot(f"Drift of {Path(name).name}", [{"name": "Frames", "x": [round(float(v), 2) for v in x],
                                                      "y": [round(float(v), 2) for v in y]}],
                     x_label="Shift X (px)", y_label="Shift Y (px)")


# ------------------------------------------------------------------ CTF estimation
@register
class RelionCtffind(RelionJob):
    name = "relion_ctffind"
    title = "CTF estimation (CTFFIND)"
    category = "Preprocessing"
    description = ("Fits the contrast transfer function of every micrograph with CTFFIND 4.1 or 5 (run by "
                   "relion_run_ctffind): defocus, astigmatism, phase shift, and the resolution to which the fit holds "
                   "(the best criterion to curate the micrographs).")
    gpu_param = ""
    cpu_param = "mpi"
    inputs = [Slot("micrographs", ("micrographs",), "Micrographs", help="Motion-corrected micrographs.")]
    params = [
        Param("use_power_spectra", "bool", True, label="Use the power spectra of motion correction",
              help="Needed for float16 micrographs; switched off when the micrographs have none."),
        Param("use_nodw", "bool", False, label="Use the non-dose-weighted sums",
              help="When motion correction saved them (rlnMicrographNameNoDW)."),
        Param("res_min", "float", 30.0, label="Minimum resolution", unit="Å", min=5, max=200),
        Param("res_max", "float", 5.0, label="Maximum resolution", unit="Å", min=1, max=30,
              help="Highest resolution used in the fit (RELION's default 5 Å; 3-4 Å for good data)."),
        Param("defocus_min", "float", 5000.0, label="Minimum defocus", unit="Å", min=0),
        Param("defocus_max", "float", 50000.0, label="Maximum defocus", unit="Å", min=1000),
        Param("defocus_step", "float", 500.0, label="Defocus step", unit="Å", min=50),
        Param("astigmatism", "float", 100.0, label="Expected astigmatism", unit="Å", min=0,
              help="CTFFIND's dAst restraint."),
        Param("box", "int", 512, label="FFT box", unit="px", min=64, max=2048),
        Param("phase_shift", "bool", False, label="Estimate phase shifts", help="Volta phase plate data."),
        Param("phase_min", "float", 0.0, label="Phase shift min", unit="°", advanced=True),
        Param("phase_max", "float", 180.0, label="Phase shift max", unit="°", advanced=True),
        Param("phase_step", "float", 10.0, label="Phase shift step", unit="°", advanced=True),
        Param("exhaustive", "bool", False, label="Exhaustive search", advanced=True,
              help="Slower; for difficult astigmatism or phase shifts (useful with CTFFIND 4.1.8 and older)."),
        Param("window", "int", -1, label="Window", unit="px", advanced=True,
              help="Fit only a centred square of this size (labels at the edge of film); negative: whole micrograph."),
        mpi(4, help="Micrographs processed at the same time (CTFFIND is single-threaded)."),
        extra_args_param(),
    ]
    outputs = [OutputDef("micrographs", "micrographs", "Micrographs with CTF")]

    def run(self, ctx: JobContext) -> None:
        p = ctx.params
        inp = ctx.require("micrographs")
        star, ref = star_of(ctx, inp)
        tables = rl.read_star(star)
        mics = rl.micrograph_table(tables)
        n = len(mics)
        use_ps = bool(p["use_power_spectra"])
        if use_ps and not mics.has("rlnCtfPowerSpectrum"):
            ctx.log("The micrographs have no power spectra (rlnCtfPowerSpectrum): CTFFIND reads the micrographs")
            use_ps = False
        args = [*relion_command(ctx, "relion_run_ctffind", p["mpi"]), "--i", ref, "--o", ctx.rel(ctx.job_dir) + "/",
                "--Box", str(p["box"]), "--ResMin", f"{p['res_min']:g}", "--ResMax", f"{p['res_max']:g}",
                "--dFMin", f"{p['defocus_min']:g}", "--dFMax", f"{p['defocus_max']:g}", "--FStep", f"{p['defocus_step']:g}",
                "--dAst", f"{p['astigmatism']:g}"]
        if p["use_nodw"]:
            if not mics.has("rlnMicrographNameNoDW"):
                raise JobError("The micrographs have no non-dose-weighted sums (save them in motion correction)")
            args.append("--use_noDW")
        if p["phase_shift"]:
            args += ["--do_phaseshift", "--phase_min", f"{p['phase_min']:g}", "--phase_max", f"{p['phase_max']:g}",
                     "--phase_step", f"{p['phase_step']:g}"]
        args += ["--ctffind_exe", ctx.executable("ctffind"), "--ctfWin", str(p["window"]), "--is_ctffind4"]
        if not p["exhaustive"]:
            args.append("--fast_search")
        if use_ps:
            args.append("--use_given_ps")
        args += ctx.split_extra()
        ctx.progress(0.03, f"CTFFIND on {n:,} micrographs")
        with FileProgress(ctx, ctx.job_dir, "*.ctf", n, "Micrographs fitted"):
            run_relion(ctx, args)
        out = ctx.path("micrographs_ctf.star")
        if not out.exists():
            raise JobError("relion_run_ctffind wrote no micrographs_ctf.star (is CTFFIND configured in Tools? see the log)")
        # relion_run_ctffind drops the motion of the movies (rlnMicrographMetadata, rlnAccumMotion*): the micrographs
        # of motion correction stay known for the motion criterion of curation and for Bayesian polishing
        motion = {"motion_star": ref} if mics.has("rlnMicrographMetadata") else \
            {k: inp.meta[k] for k in ("motion_star",) if inp.meta.get(k)}
        self.report(ctx, out, n, motion)

    def report(self, ctx: JobContext, out: Path, n_in: int, extra: dict[str, Any] | None = None) -> None:
        tables = rl.read_star(out)
        mics = rl.micrograph_table(tables)
        n = len(mics)
        if not n:
            raise JobError("CTFFIND fitted no micrograph (see the log)")
        if n < n_in:
            ctx.warn(f"{n_in - n} micrograph(s) without CTF fit were left out")
        x = list(range(1, n + 1))
        du, dv = mics.floats("rlnDefocusU") / 1e4, mics.floats("rlnDefocusV") / 1e4
        ctx.add_plot("Defocus per micrograph", [{"name": "Defocus U", "x": x, "y": [round(float(v), 4) for v in du]},
                                                {"name": "Defocus V", "x": x, "y": [round(float(v), 4) for v in dv]}],
                     x_label="Micrograph", y_label="Defocus (µm)")
        rows = [["Micrographs", f"{n:,}"], ["Defocus", f"{du.min():.2f} – {du.max():.2f} µm (median {np.median(du):.2f})"]]
        best = None
        if mics.has("rlnCtfMaxResolution"):
            res = mics.floats("rlnCtfMaxResolution")
            ctx.add_plot("CTF fit resolution", [{"name": "Micrographs", **histogram(res, lo=0, hi=min(25.0, float(res.max()) + 0.5))}],
                         x_label="CTF fit resolution (Å)", y_label="Micrographs", style="bars")
            ctx.add_plot("CTF fit resolution per micrograph", [{"name": "Fit", "x": x, "y": [round(float(v), 2) for v in res]}],
                         x_label="Micrograph", y_label="Resolution (Å)")
            rows.append(["CTF fit resolution", f"median {np.median(res):.2f} Å (best {res.min():.2f}, worst {res.max():.2f})"])
            ctx.add_highlight("CTF fit", f"{np.median(res):.1f} Å")
            best = res
        if mics.has("rlnCtfAstigmatism"):
            ast = mics.floats("rlnCtfAstigmatism")
            rows.append(["Astigmatism", f"median {np.median(ast):.0f} Å"])
        if mics.has("rlnPhaseShift"):
            rows.append(["Phase shift", f"median {np.median(mics.floats('rlnPhaseShift')):.0f}°"])
        if mics.has("rlnCtfIceRingDensity"):
            rows.append(["Ice ring density", f"median {np.median(mics.floats('rlnCtfIceRingDensity')):.2f}"])
        ctx.add_table("CTF estimation", ["", "Value"], rows)
        ctx.add_highlight("Defocus", f"{du.min():.1f}–{du.max():.1f} µm")
        thumb = None
        if mics.has("rlnCtfImage"):
            order = np.argsort(best) if best is not None else np.arange(n)
            picks = [int(order[0]), int(order[len(order) // 2]), int(order[-1])] if n >= 3 else list(range(n))
            from cryoplug.imaging import write_png
            from cryoplug.micrographs import ctf_fit_image, tile_sheet
            tiles, labels = [], []
            for i in picks:
                try:
                    tiles.append(ctf_fit_image(ctx.abs(mics.column("rlnCtfImage")[i]), 256))
                    labels.append(f"{Path(mics.column('rlnMicrographName')[i]).name}"
                                  + (f" ({best[i]:.1f} Å)" if best is not None else ""))
                except Exception as exc:  # cosmetic
                    ctx.warn(f"No CTF picture: {exc}")
            if tiles:
                png = write_png(ctx.path("ctf_fits.png"), tile_sheet(tiles, per_row=3, gap=4))
                ctx.add_image("CTF fits: best, median, worst (" + "; ".join(labels) + ")", png)
                thumb = thumbnail_of(ctx, tiles[0], "micrographs")
        meta = micrograph_meta(ctx, out, "ctffind", **(extra or {}))
        ctx.add_output("micrographs", "micrographs", out, f"{n:,} micrographs with CTF", meta=meta, thumbnail=thumb)


# ------------------------------------------------------------------ curation
CURATION = [  # (parameter, STAR label, kept when, unit, label)
    ("max_ctf_resolution", "rlnCtfMaxResolution", "max", "Å", "CTF fit resolution"),
    ("min_defocus", "rlnDefocusU", "min", "Å", "Defocus"),
    ("max_defocus", "rlnDefocusU", "max", "Å", "Defocus"),
    ("max_astigmatism", "rlnCtfAstigmatism", "max", "Å", "Astigmatism"),
    ("min_fom", "rlnCtfFigureOfMerit", "min", "", "CTF figure of merit"),
    ("max_motion", "rlnAccumMotionTotal", "max", "Å", "Total motion"),
    ("max_ice", "rlnCtfIceRingDensity", "max", "", "Ice ring density"),
]


@register
class RelionCurate(JobType):
    name = "relion_curate"
    title = "Curate micrographs"
    category = "Preprocessing"
    software = ["RELION"]
    description = ("Keeps the micrographs whose CTF fit, defocus, astigmatism, motion or ice meet thresholds (as "
                   "CryoSPARC's Curate Exposures; relion_star_handler --select one criterion at a time). Shows the "
                   "distributions with the thresholds; the rejected micrographs are an output too.")
    inputs = [Slot("micrographs", ("micrographs",), "Micrographs", help="With CTF estimation for the CTF criteria.")]
    params = [
        Param("max_ctf_resolution", "float", 0.0, label="Worst CTF fit resolution", unit="Å", min=0,
              help="Keep micrographs whose CTF fits to this resolution or better (e.g. 6 Å). 0 = no limit."),
        Param("min_defocus", "float", 0.0, label="Minimum defocus", unit="Å", min=0, help="0 = no limit."),
        Param("max_defocus", "float", 0.0, label="Maximum defocus", unit="Å", min=0, help="0 = no limit."),
        Param("max_astigmatism", "float", 0.0, label="Maximum astigmatism", unit="Å", min=0, help="0 = no limit."),
        Param("min_fom", "float", 0.0, label="Minimum CTF figure of merit", min=0, advanced=True, help="0 = no limit."),
        Param("max_motion", "float", 0.0, label="Maximum total motion", unit="Å", min=0, help="0 = no limit."),
        Param("max_ice", "float", 0.0, label="Maximum ice ring density", min=0, advanced=True,
              help="CTFFIND 5 measure of crystalline ice. 0 = no limit."),
    ]
    outputs = [OutputDef("micrographs", "micrographs", "Kept micrographs"),
               OutputDef("rejected", "micrographs", "Rejected micrographs")]

    def run(self, ctx: JobContext) -> None:
        p = ctx.params
        inp = ctx.require("micrographs")
        star, _ = star_of(ctx, inp)
        tables = rl.read_star(star)
        mics = rl.micrograph_table(tables)
        n = len(mics)
        keep = np.ones(n, dtype=bool)
        rows, plotted = [], set()
        motion = self.motion_of(ctx, inp, mics)
        for name, label, kind, unit, title in CURATION:
            limit = float(p[name] or 0)
            if mics.has(label):
                values = mics.floats(label)
            elif label in motion:
                values = motion[label]
            else:
                if limit:
                    raise JobError(f"{title}: the micrographs have no {label} column (run "
                                   + ("motion correction" if label.startswith("rlnAccumMotion") else "CTF estimation")
                                   + " first?)")
                continue
            if limit:
                ok = (values <= limit if kind == "max" else values >= limit) | np.isnan(values)  # unknown: kept
                rows.append([f"{title} {'≤' if kind == 'max' else '≥'} {limit:g} {unit}".strip(), f"{int((~ok).sum()):,}"])
                keep &= ok
            if label not in plotted:
                plotted.add(label)
                lines = [{"x": float(p[q]), "label": f"{'≤' if k == 'max' else '≥'} {float(p[q]):g}"}
                         for q, lab, k, _, _ in CURATION if lab == label and float(p[q] or 0)]
                ctx.add_plot(title, [{"name": "Micrographs", **histogram(values)}], x_label=f"{title} ({unit})" if unit else title,
                             y_label="Micrographs", style="bars", vlines=lines)
        if not rows:
            raise JobError("Set at least one threshold")
        kept = int(keep.sum())
        ctx.add_table("Rejected by", ["Criterion", "Micrographs rejected"], rows + [["Kept", f"{kept:,} of {n:,}"]])
        if not kept:
            raise JobError("No micrograph meets all the thresholds")
        ctx.add_highlight("Kept", f"{kept:,} / {n:,}")
        out = rl.subset_star(star, ctx.path("micrographs.star"), keep, comment=f"Curated from {inp.source}")
        passed = {k: v for k, v in inp.meta.items() if k in ("has_motion", "power_spectra", "motion_star")}
        names = mics.column("rlnMicrographName")
        thumb = None
        try:  # the first micrograph kept, for the job card
            from cryoplug.micrographs import preview, read_image
            thumb = thumbnail_of(ctx, preview(read_image(ctx.abs(names[int(np.flatnonzero(keep)[0])])), 256)[0], "micrographs")
        except Exception as exc:  # cosmetic
            ctx.warn(f"No thumbnail: {exc}")
        ctx.add_output("micrographs", "micrographs", out, f"{kept:,} micrographs",
                       meta=micrograph_meta(ctx, out, "curate", **passed), thumbnail=thumb)
        if kept < n:
            rej = rl.subset_star(star, ctx.path("micrographs_rejected.star"), ~keep, comment=f"Rejected from {inp.source}")
            ctx.add_output("rejected", "micrographs", rej, f"{n - kept:,} rejected micrographs",
                           meta=micrograph_meta(ctx, rej, "curate", **passed))
        if kept < n:
            micrograph_gallery(ctx, sample([names[i] for i in np.flatnonzero(~keep)], 2), "rejected_examples",
                               "Rejected examples")

    @staticmethod
    def motion_of(ctx: JobContext, inp: Any, mics: rl.StarTable) -> dict[str, np.ndarray]:
        """The motion of the micrographs (rlnAccumMotion*) from the motion correction they came from, when the CTF
        estimation in between dropped it."""
        ref = inp.meta.get("motion_star")
        if mics.has("rlnAccumMotionTotal") or not ref or not ctx.abs(ref).exists():
            return {}
        source = rl.micrograph_table(rl.read_star(ctx.abs(ref)))
        out = {}
        names = mics.column("rlnMicrographName")
        for label in ("rlnAccumMotionTotal", "rlnAccumMotionEarly", "rlnAccumMotionLate"):
            if source.has(label):
                by_name = dict(zip(source.column("rlnMicrographName"), source.floats(label)))
                out[label] = np.array([by_name.get(m, np.nan) for m in names])
        return out
