"""Fake RELION 5.1 tomography programs for the tests, dispatched from relion.py on the program name: tilt-series import
(relion_python_tomo_import SerialEM), motion correction and CTF estimation of tilt series (relion_run_motioncorr and
relion_run_ctffind given a tilt-series set, from relion_spa.py), tilt-series alignment (relion_align_tiltseries: IMOD or
AreTomo2), tomogram reconstruction (relion_tomo_reconstruct_tomogram), denoising (relion_python_tomo_denoise
cryoCARE:train / cryoCARE:predict), coordinate import (relion_tomo_import_coordinates), subtomogram extraction
(relion_tomo_subtomo), particle reconstruction (relion_tomo_reconstruct_particle), CTF refinement (relion_tomo_refine_ctf),
frame alignment (relion_tomo_align) and relion_refine --ios.

As the real programs (RELION 5.1.1 sources), they take only the options RELION knows (relion_options.json), run from the
project folder and read and write RELION 5's tilt-series sets: the C++ programs write the tilt-series file of each tilt
series where TomogramSet::write puts it (the output folder followed by the input name: decomposePipelineFileName finds
no jobNNN/ folder in CryoPlug's names), the Python programs in tilt_series/ of the output folder. The quirks of RELION
5.1.1 are kept: the import's pre-exposure from the mdoc doses is the cumulative dose including the image, --prefix makes
the import find no tilt series, a reconstruction for denoising writes only the two halves, a particle reconstruction
without half sets only the merged map. The tomograms show the particles of tomo_data.py as blobs at their positions."""
from __future__ import annotations

import glob
import json
import os
import shutil
import sys
import tarfile
import zlib
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, os.environ.get("CRYOPLUG_ROOT", ""))
sys.path.insert(0, os.path.join(os.environ.get("CRYOPLUG_ROOT", ""), "tests", "fakes"))
import relion_spa as spa  # noqa: E402
import tomo_data  # noqa: E402
from cryoplug import relion as rl  # noqa: E402
from cryoplug import tomo as tm  # noqa: E402
from cryoplug.mrc import MapVolume  # noqa: E402

die = spa.die


def parse(argv: list[str], prog: str) -> dict:
    """The options of a program (relion_options.json: how many values each takes)."""
    spec = spa.OPTIONS[prog]
    out, i = {}, 0
    while i < len(argv):
        opt = argv[i]
        if opt not in spec:
            die(f"Option {opt} is not a valid RELION argument")
        n = spec[opt]
        if n and i + n >= len(argv):
            die(f"Option {opt} needs {n} value(s)")
        out[opt] = (argv[i + 1] if n == 1 else argv[i + 1:i + 1 + n]) if n else True
        i += 1 + n
    return out


def outdir(a: dict, key: str = "--o") -> str:
    return spa.outdir(a, key)


def is_tilt_series_set(path: str) -> bool:
    try:
        g = rl.read_star(path).get("global")
    except OSError:
        return False
    return g is not None and g.has("rlnTomoTiltSeriesStarFile")


def read_set(path: str) -> tm.TiltSeriesSet:
    if not os.path.exists(path):
        die(f"Cannot open file {path} (from {os.getcwd()})")
    try:
        return tm.read_set(path, ".")
    except ValueError as exc:
        die(f"ERROR: the input file is not a valid tilt series star file: {exc}")


def write_set(path: str, tset: tm.TiltSeriesSet) -> None:
    """TomogramSet::write: each tilt-series file at getOutputFileWithNewUniqueDate(input name, output folder)."""
    out = os.path.dirname(path) + "/"
    g = tset.global_table
    files = []
    for name, rel in zip(g.column("rlnTomoName"), g.column("rlnTomoTiltSeriesStarFile")):
        new = out + spa.post(rel)
        os.makedirs(os.path.dirname(new), exist_ok=True)
        t = tset.series[name]
        rl.write_star(new, [rl.StarTable(name, list(t.labels), [list(r) for r in t.rows])])
        files.append(new)
    g.set_column("rlnTomoTiltSeriesStarFile", files)
    g.name = "global"
    rl.write_star(path, [g])


def by_dose(table: rl.StarTable) -> rl.StarTable:
    """The tilt images by pre-exposure (TomogramSet sorts them so)."""
    if not table.has("rlnMicrographPreExposure"):
        return table
    return table.select(np.argsort(table.floats("rlnMicrographPreExposure"), kind="stable"))


def by_tilt(table: rl.StarTable) -> rl.StarTable:
    return table.select(np.argsort(table.floats("rlnTomoNominalStageTiltAngle"), kind="stable"))


def pixel_of(tset: tm.TiltSeriesSet, name: str) -> float:
    v = tset.value(name, "rlnTomoTiltSeriesPixelSize")
    if v is None:
        die("ERROR: tomogramSet->globalTable does not contain rlnTomoTiltSeriesPixelSize label")
    return float(v)


def tomogram(name: str, size: list[int], pixel: float, binning: float, noise: float = 0.5, seed: int = 0) -> np.ndarray:
    """A tomogram (z, y, x) of ``size`` unbinned pixels binned by ``binning``: the particles of the tilt series as dark
    blobs in noise."""
    nx, ny, nz = (max(1, int(round(s / binning))) for s in size)
    rng = np.random.default_rng(zlib.crc32(name.encode()) + seed)
    vol = rng.normal(0, noise, (nz, ny, nx)).astype(np.float32)
    z, y, x = np.indices((nz, ny, nx), dtype=np.float32)
    sig = tomo_data.PARTICLE_SIGMA / (pixel * binning)
    for vx, vy, vz in tm.to_binned_voxels(tomo_data.particle_positions(name), size, pixel, binning):
        vol -= np.exp(-((x - vx) ** 2 + (y - vy) ** 2 + (z - vz) ** 2) / (2 * sig ** 2))
    return vol


def image_size(table: rl.StarTable) -> tuple[int, int]:
    """Width and height of the tilt images."""
    name = (table.column("rlnMicrographName") or table.column("rlnMicrographMovieName"))[0]
    data = spa.read_image(name)
    return data.shape[-1], data.shape[-2]


# ------------------------------------------------------------------ import (relion_python_tomo_import SerialEM)
def _when(text: str) -> datetime:
    for fmt in ("%d-%b-%y  %H:%M:%S", "%d-%b-%y %H:%M:%S", "%d-%b-%Y  %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text.strip(), fmt)
        except ValueError:
            continue
    return datetime.min


def tomo_import(argv: list[str]) -> None:
    if not argv or argv[0] != "SerialEM":
        die("Usage: relion_python_tomo_import [OPTIONS] COMMAND [ARGS]...\nError: No such command (SerialEM)")
    a = parse(argv[1:], "relion_python_tomo_import:SerialEM")
    for need in ("--tilt-image-movie-pattern", "--mdoc-file-pattern", "--output-directory", "--nominal-tilt-axis-angle",
                 "--nominal-pixel-size", "--voltage", "--spherical-aberration", "--amplitude-contrast", "--optics-group-name"):
        if need not in a:
            die(f"Error: Missing option '{need}'.")
    per_tilt, per_frame = a.get("--dose-per-tilt-image"), a.get("--dose-per-movie-frame")
    if per_tilt is not None and per_frame is not None:
        die("ValueError: only one of dose_per_tilt_image and dose_per_movie_frame can be set.")
    out = Path(a["--output-directory"])
    (out / "tilt_series").mkdir(parents=True, exist_ok=True)
    for key in ("--tilt-image-movie-pattern", "--mdoc-file-pattern"):
        if os.path.isabs(a[key]):  # pathlib's Path().glob
            die("NotImplementedError: Non-relative patterns are unsupported")
    images = list(Path().glob(a["--tilt-image-movie-pattern"]))
    mdocs = sorted(Path().glob(a["--mdoc-file-pattern"]))
    print(f"Found {len(mdocs)} mdoc files and {len(images)} image files.")
    if not images:
        die("RuntimeError: Could not find any image files")
    if not mdocs:
        die("RuntimeError: Could not find any mdoc files")
    prefix = a.get("--prefix", "")
    ids = [(f"{prefix}_" if prefix else "") + tm.mdoc_basename(m.name) for m in mdocs]
    image_ids = [Path(tm.mdoc_basename(f.name)).stem for f in images]
    ids = [i for i in ids if any(Path(i).stem in name for name in image_ids)]  # the prefix included (RELION 5.1.1)
    files = [str(out / "tilt_series" / f"{i}.star") for i in ids]
    motion_corrected = "--images-are-motion-corrected" in a
    g = rl.StarTable("global")
    n = len(ids)
    g.set_column("rlnTomoName", ids)
    g.set_column("rlnTomoTiltSeriesStarFile", files)
    g.set_column("rlnVoltage", [float(a["--voltage"])] * n)
    g.set_column("rlnSphericalAberration", [float(a["--spherical-aberration"])] * n)
    g.set_column("rlnAmplitudeContrast", [float(a["--amplitude-contrast"])] * n)
    g.set_column("rlnMicrographOriginalPixelSize", [float(a["--nominal-pixel-size"])] * n)
    if motion_corrected:
        g.set_column("rlnTomoTiltSeriesPixelSize", [float(a["--nominal-pixel-size"])] * n)
    g.set_column("rlnTomoHand", [-1 if "--invert-defocus-handedness" in a else 1] * n)
    if "--mtf-file" in a:
        g.set_column("rlnMtfFileName", [a["--mtf-file"]] * n)
    if a["--optics-group-name"] != "":
        g.set_column("rlnOpticsGroupName", [a["--optics-group-name"]] * n)
    rl.write_star(out / "tilt_series.star", [g])
    by_base = {tm.mdoc_basename(f.name): f for f in images}
    for name, mdoc, f in zip(ids, mdocs, files):  # as RELION: the remaining names paired with the mdoc files in order
        sections = sorted(tm.parse_mdoc(mdoc), key=lambda s: _when(s.get("DateTime", "")))
        k = len(sections)
        if per_tilt is None and per_frame is None and all("ExposureDose" in s for s in sections):
            pre = np.cumsum([float(s["ExposureDose"]) for s in sections])
        elif per_tilt is not None:
            pre = float(per_tilt) * np.arange(k)
        elif per_frame is not None:
            post = float(per_frame) * np.cumsum([float(s.get("NumSubFrames", 0)) for s in sections])
            pre = np.concatenate([[0.0], post[:-1]])
        else:
            print("UserWarning: no dose information found in mdoc or provided, defaulting to zero.")
            pre = np.zeros(k)
        rows = []
        for s, dose in zip(sections, pre):
            img = by_base.get(tm.mdoc_basename(s.get("SubFramePath", "")))
            if img is None:
                print(f"WARNING: Image {s.get('SubFramePath', '').split(chr(92))[-1]} from {mdoc} not found in image files.")
                continue
            rows.append((str(img), s, float(dose)))
        t = rl.StarTable(name)
        if not motion_corrected:
            t.set_column("rlnMicrographMovieName", [r[0] for r in rows])
            t.set_column("rlnTomoTiltMovieFrameCount", [int(float(r[1].get("NumSubFrames", 1))) for r in rows])
        t.set_column("rlnTomoNominalStageTiltAngle", [float(r[1]["TiltAngle"]) for r in rows])
        t.set_column("rlnTomoNominalTiltAxisAngle", [float(a["--nominal-tilt-axis-angle"])] * len(rows))
        t.set_column("rlnMicrographPreExposure", [r[2] for r in rows])
        if rows and all("TargetDefocus" in r[1] for r in rows):
            t.set_column("rlnTomoNominalDefocus", [float(r[1]["TargetDefocus"]) for r in rows])
        if motion_corrected:
            t.set_column("rlnMicrographName", [r[0] for r in rows])
        rl.write_star(f, [t])
    (out / "log.txt").write_text("fake relion_python_tomo_import\n")
    print(f"Wrote STAR files for {len(files)} tilt-series.")


# ------------------------------------------------------------------ motion correction of tilt series
def motioncorr(a: dict, out: str) -> None:
    """relion_run_motioncorr on a tilt-series set (MotioncorrRunner with is_tomo)."""
    tset = read_set(str(a["--i"]))
    for t in tset.series.values():
        if not t.has("rlnMicrographMovieName"):
            die("The input STAR file does not contain the rlnMicrographMovieName column. Are you sure you imported "
                "files as movies, not single frame images?")
    binning = float(a.get("--bin_factor", 1))
    f16 = "--float16" in a
    grouping = int(a.get("--grouping_for_ps", 0) or 0) > 0
    even_odd = "--even_odd_split" in a
    gain = spa.read_image(str(a["--gainref"])) if "--gainref" in a else None
    done, total = 0, tset.n_tilts()
    for name in tset.names:
        t = by_dose(tset.series[name])
        apix0 = float(tset.value(name, "rlnMicrographOriginalPixelSize"))
        apix = apix0 * binning
        cols: dict[str, list] = {k: [] for k in ("ps", "even", "odd", "mic", "meta", "total", "early", "late")}
        for movie in t.column("rlnMicrographMovieName"):
            frames = spa.read_image(movie)
            if frames.ndim != 3:
                die(f"{movie} is not a movie")
            if gain is not None:
                frames = frames * gain

            def binned(img: np.ndarray) -> np.ndarray:
                if binning <= 1:
                    return img
                b = int(binning)
                h, w = (img.shape[0] // b) * b, (img.shape[1] // b) * b
                return img[:h, :w].reshape(h // b, b, w // b, b).mean(axis=(1, 3))
            root = out + spa.stem(movie).replace(".", "_")
            spa.write_image(root + ".mrc", binned(frames.sum(axis=0)), apix, f16)
            if grouping:
                total_img = frames.sum(axis=0)
                spec = np.log1p(np.abs(np.fft.fftshift(np.fft.fft2(total_img - total_img.mean()))))
                spa.write_image(root + "_PS.mrc", spec, apix, f16)
                cols["ps"].append(root + "_PS.mrc")
            if even_odd:
                spa.write_image(root + "_EVN.mrc", binned(frames[0::2].sum(axis=0)), apix, f16)
                spa.write_image(root + "_ODD.mrc", binned(frames[1::2].sum(axis=0)), apix, f16)
                cols["even"].append(root + "_EVN.mrc")
                cols["odd"].append(root + "_ODD.mrc")
            nf = len(frames)
            shifts = np.array([[0.3 * f, 0.5 * f] for f in range(nf)]) * (1 + 0.1 * (done % 5))
            general = rl.StarTable("general", ["rlnImageSizeX", "rlnImageSizeY", "rlnImageSizeZ", "rlnMicrographMovieName",
                                               "rlnMicrographBinning", "rlnMicrographOriginalPixelSize",
                                               "rlnMicrographDoseRate", "rlnMicrographPreExposure", "rlnVoltage",
                                               "rlnMicrographStartFrame", "rlnMotionModelVersion"],
                                   [[str(frames.shape[2]), str(frames.shape[1]), str(nf), movie, f"{binning:g}", f"{apix0:g}",
                                     "1", "0", "300", "1", "1"]], loop=False)
            gs = rl.StarTable("global_shift")
            gs.set_column("rlnMicrographFrameNumber", list(range(1, nf + 1)))
            gs.set_column("rlnMicrographShiftX", shifts[:, 0])
            gs.set_column("rlnMicrographShiftY", shifts[:, 1])
            rl.write_star(root + ".star", [general, gs])
            steps = np.linalg.norm(np.diff(shifts, axis=0), axis=1) * apix0
            cols["mic"].append(root + ".mrc")
            cols["meta"].append(root + ".star")
            cols["total"].append(float(steps.sum()))
            cols["early"].append(float(steps[:1].sum()))
            cols["late"].append(float(steps[1:].sum()))
            done += 1
            spa.progress(done, total, "Motion correction")
        if grouping:
            t.set_column("rlnCtfPowerSpectrum", cols["ps"])
        if even_odd:
            t.set_column("rlnMicrographNameEven", cols["even"])
            t.set_column("rlnMicrographNameOdd", cols["odd"])
        t.set_column("rlnMicrographName", cols["mic"])
        t.set_column("rlnMicrographMetadata", cols["meta"])
        t.set_column("rlnAccumMotionTotal", cols["total"])
        t.set_column("rlnAccumMotionEarly", cols["early"])
        t.set_column("rlnAccumMotionLate", cols["late"])
        tset.series[name] = t
    g = tset.global_table
    g.set_column("rlnTomoTiltSeriesPixelSize", [float(v) * binning for v in g.column("rlnMicrographOriginalPixelSize")])
    g.drop("rlnMicrographPixelSize")
    write_set(out + "corrected_tilt_series.star", tset)
    spa.pdf(out + "logfile.pdf")
    print(f" Written: {out}corrected_tilt_series.star")


# ------------------------------------------------------------------ CTF estimation of tilt series
def ctffind(a: dict, out: str) -> None:
    """relion_run_ctffind on a tilt-series set: the search around the nominal defocus, the highest resolution of the fit
    raised with the dose (CtffindRunner::getMySearchParameters)."""
    tset = read_set(str(a["--i"]))
    first = next(iter(tset.series.values()))
    if not first.has("rlnMicrographName"):
        die("The input STAR file does not contain the rlnMicrographName column (motion-correct the tilt series first)")
    use_ps = "--use_given_ps" in a
    if use_ps and not first.has("rlnCtfPowerSpectrum"):
        die("input micrographs STAR file does not contain rlnCtfPowerSpectrum")
    if "--extra_ice_stats" in a and not use_ps:
        die("ERROR: You cannot use --extra_ice_stats without also using --use_given_ps")
    local = float(a.get("--localsearch_nominal_defocus", 10000))
    if local > 0 and not first.has("rlnTomoNominalDefocus"):
        print("WARNING: you specified --localsearch_nominal_defocus, but there is no rlnTomoNomicalDefocus in the STAR "
              "file; using min and max defocus instead...")
    res_max, res_min = float(a.get("--ResMax", 5)), float(a.get("--ResMin", 30))
    factor = float(a.get("--exp_factor_dose", 100))
    power = []
    done, total = 0, tset.n_tilts()
    for k, name in enumerate(tset.names):
        t = by_dose(tset.series[name])
        mics = t.column("rlnMicrographName")
        src = t.column("rlnCtfPowerSpectrum") if use_ps else mics
        tilts = t.floats("rlnTomoNominalStageTiltAngle")
        doses = t.floats("rlnMicrographPreExposure")
        nominal = t.floats("rlnTomoNominalDefocus") if t.has("rlnTomoNominalDefocus") else np.full(len(t), -3.0)
        rows = []
        for i, given in enumerate(src):
            if not os.path.exists(given):
                die(f"Cannot find {given}")
            root = out + spa.stem(spa.post(given))
            os.makedirs(os.path.dirname(root), exist_ok=True)
            y, x = np.indices((128, 128)) - 64
            r = np.hypot(x, y)
            spa.write_image(root + ".ctf", (np.cos(r ** 2 / (40.0 + 3 * i)) * np.exp(-r / 80)).astype(np.float32), 1.0)
            limit = res_max * np.exp(doses[i] / factor) if factor > 0 else res_max
            limit = min(limit, res_min * 0.9)
            maxres = 25.0 if i == len(src) - 1 and len(src) > 3 else round(min(limit + 0.4 + 0.05 * i, 25.0), 2)
            defu = abs(nominal[i]) * 1e4 + 150.0 * abs(np.sin(np.radians(tilts[i]))) * 10 + 200.0 * k
            Path(root + ".txt").write_text(f"# fake CTFFIND output\n1.0 {defu:.2f} {defu - 180:.2f} 41.0 0.0 0.18 {maxres:.2f}\n")
            Path(root + ".log").write_text("fake ctffind\n")
            rows.append((root + ".ctf:mrc", defu, defu - 180.0, 0.18 - 0.005 * i, maxres))
            done += 1
            spa.progress(done, total, "CTFFIND")
        t.set_column("rlnCtfImage", [r[0] for r in rows])
        t.set_column("rlnDefocusU", [r[1] for r in rows])
        t.set_column("rlnDefocusV", [r[2] for r in rows])
        t.set_column("rlnCtfAstigmatism", [r[1] - r[2] for r in rows])
        t.set_column("rlnDefocusAngle", [41.0] * len(rows))
        t.set_column("rlnCtfFigureOfMerit", [r[3] for r in rows])
        t.set_column("rlnCtfMaxResolution", [r[4] for r in rows])
        if "--do_phaseshift" in a:
            t.set_column("rlnPhaseShift", [90.0] * len(rows))
        t.set_column("rlnCtfIceRingDensity", [0.01 * (j + 1) for j in range(len(rows))])
        if "--extra_ice_stats" in a:
            t.set_column("rlnCtfIceRingPowerFraction", [0.002 * (j + 1) for j in range(len(rows))])
            t.set_column("rlnCtfIceRingKurtosis", [2.5 + 0.3 * j for j in range(len(rows))])
        tset.series[name] = t
        keep = [lab for lab in t.labels if lab not in ("rlnMicrographName", "rlnMicrographMovieName")]
        power += [[row[t.labels.index(lab)] for lab in keep] for row in t.rows]
    write_set(out + "tilt_series_ctf.star", tset)
    rl.write_star(out + "power_spectra_fits.star", [rl.StarTable("", keep, power)])
    spa.pdf(out + "logfile.pdf")
    print(f" Done! Written out: {out}tilt_series_ctf.star")


# ------------------------------------------------------------------ tilt-series alignment
def align_tiltseries(argv: list[str]) -> None:
    a = parse(argv, "relion_align_tiltseries")
    spa.need(a, "--i", "--o")
    methods = [m for m in ("--imod_fiducials", "--imod_patchtrack", "--aretomo2") if m in a]
    if len(methods) != 1:
        die("ERROR: you need to specify one of these options: --imod_fiducials or --imod_patchtrack or --aretomo")
    aretomo = "--aretomo2" in a
    exe = str(a.get("--aretomo_exe" if aretomo else "--batchtomo_exe", ""))
    if not exe or not os.access(exe, os.X_OK):
        die(f"ERROR: cannot execute {exe or ('AreTomo2' if aretomo else 'batchruntomo')}")
    out = outdir(a)
    tset = read_set(str(a["--i"]))
    recon = aretomo and "--aretomo_reconstruct" in a
    ctf = aretomo and "--aretomo_ctf" in a
    g = tset.global_table
    extra: dict[str, list] = {}
    power, keep = [], []
    for k, name in enumerate(tset.names):
        t = by_tilt(tset.series[name])
        folder = out + f"external/{name}/"
        os.makedirs(folder, exist_ok=True)
        Path(folder + ("align.log" if not aretomo else f"{name}.aln")).write_text(f"fake {'AreTomo2' if aretomo else 'IMOD'}\n")
        tilts = t.floats("rlnTomoNominalStageTiltAngle")
        axis = t.floats("rlnTomoNominalTiltAxisAngle") if t.has("rlnTomoNominalTiltAxisAngle") else np.full(len(t), 85.0)
        n = len(t)
        t.set_column("rlnTomoXTilt", [0.0] * n)
        t.set_column("rlnTomoYTilt", tilts + 0.3)
        t.set_column("rlnTomoZRot", axis + 0.4 + 0.1 * k)
        t.set_column("rlnTomoXShiftAngst", np.round(3.0 * np.sin(np.radians(tilts)) + 0.5 * k, 3))
        t.set_column("rlnTomoYShiftAngst", np.round(-2.0 * np.cos(np.radians(tilts)) + 1.0, 3))
        if ctf:
            du = 30000.0 + 200 * k + 15 * np.abs(tilts)
            t.set_column("rlnDefocusU", du)
            t.set_column("rlnDefocusV", du - 150)
            t.set_column("rlnDefocusAngle", [38.0] * n)
            if "--aretomo_phaseshift" in a:
                t.set_column("rlnPhaseShift", [80.0] * n)
            t.set_column("rlnCtfFigureOfMerit", [0.2] * n)
            t.set_column("rlnCtfMaxResolution", 6.0 + np.abs(tilts) / 10)
            t.set_column("rlnCtfImage", [f"{i + 1}@{folder}{name}_CTF.mrcs:mrcs" for i in range(n)])
            keep = [lab for lab in t.labels if lab not in ("rlnMicrographName", "rlnMicrographMovieName")]
            power += [[row[t.labels.index(lab)] for lab in keep] for row in t.rows]
        tset.series[name] = t
        if aretomo:
            extra.setdefault("rlnAreTomoTiltAxisScore", []).append(round(0.8 - 0.05 * k, 3))
            if "--aretomo_tiltcorrect" in a:
                extra.setdefault("rlnAreTomoTiltangleOffset", []).append(1.2)
        else:
            extra.setdefault("rlnEtomoDirectiveFile", []).append(f"{folder}{name}.edf")
            extra.setdefault("rlnIMODResidualErrorMean", []).append(round(0.9 + 0.1 * k, 3))
            extra.setdefault("rlnIMODResidualErrorStddev", []).append(round(0.3 + 0.02 * k, 3))
            extra.setdefault("rlnIMODLeaveOutError", []).append(round(1.1 + 0.1 * k, 3))
        if recon:
            pixel = pixel_of(tset, name)
            binning = float(a.get("--aretomo_OutBin", 4))
            w, h = image_size(t)
            d = int(a.get("--aretomo_VolZ", 1000))
            path = out + f"tomograms/rec_{name}.mrc"
            spa.write_image(path, tomogram(name, [w, h, d], pixel, binning), pixel * binning)
            for label, value in (("rlnTomoTomogramBinning", binning), ("rlnTomoSizeX", w), ("rlnTomoSizeY", h),
                                 ("rlnTomoSizeZ", d), ("rlnTomoReconstructedTomogram", path)):
                extra.setdefault(label, []).append(value)
    for label, values in extra.items():
        g.set_column(label, values)
    write_set(out + ("tomograms.star" if recon else "aligned_tilt_series.star"), tset)
    if ctf:
        rl.write_star(out + "power_spectra_fits.star", [rl.StarTable("", keep, power)])
    spa.pdf(out + "logfile.pdf")
    print(f" Written out: {out}aligned_tilt_series.star")


# ------------------------------------------------------------------ tomogram reconstruction
def reconstruct_tomogram(argv: list[str]) -> None:
    a = parse(argv, "relion_tomo_reconstruct_tomogram")
    spa.need(a, "--o")
    star = a.get("--t") or (tm.read_optimisation_set(a["--i"]).get("tomograms") if "--i" in a else None)
    if not star:
        die("ERROR: no tomogram set (--t or --i)")
    tset = read_set(str(star))
    out = outdir(a)
    for opt in ("--w", "--h", "--d"):
        if int(a.get(opt, -1)) <= 0:
            die(f"ERROR: please provide {opt}")
    w, h, d = int(a["--w"]), int(a["--h"]), int(a["--d"])
    only = str(a.get("--tn", "*"))
    if only != "*" and only not in tset.names:
        die(f"ERROR: cannot find tomogram {only} in {star}")
    names = tset.names if only == "*" else [only]
    split, fourier = "--generate_split_tomograms" in a, "--fourier" in a
    halves = split and not fourier  # Fourier inversion reconstructs the whole tilt series only
    offset = float(a.get("--tiltangle_offset", 0))
    g = tset.global_table
    cols: dict[str, dict[str, object]] = {}
    for i, name in enumerate(names):
        t = tset.series[name]
        if not (t.has("rlnTomoXTilt") and t.has("rlnTomoYTilt") and t.has("rlnTomoZRot")):
            die("ERROR: tomograms do not have tilt series alignment parameters to calculate projectionMatrices")
        if halves and not t.has("rlnMicrographNameEven"):
            die("ERROR: the tilt series have no rlnMicrographNameEven / rlnMicrographNameOdd (motion correction with "
                "--even_odd_split)")
        pixel = pixel_of(tset, name)
        spacing = float(a["--binned_angpix"]) / pixel if float(a.get("--binned_angpix", -1)) > 0 else float(a.get("--bin", 1))
        if abs(offset) > 0:
            t.set_column("rlnTomoYTilt", t.floats("rlnTomoYTilt") + offset)
        tags = ["_half1", "_half2"] if halves else [""]
        for tag, seed in zip(tags, (1, 2) if halves else (0,)):
            vol = tomogram(name, [w, h, d], pixel, spacing, noise=0.7 if halves else 0.5, seed=seed)
            path = out + f"tomograms/rec_{name}{tag}.mrc"
            spa.write_image(path, vol, pixel * spacing)
            label = {"": "rlnTomoReconstructedTomogram", "_half1": "rlnTomoReconstructedTomogramHalf1",
                     "_half2": "rlnTomoReconstructedTomogramHalf2"}[tag]
            cols.setdefault(label, {})[name] = path
            if "--do_proj" in a:
                c = vol.shape[0] // 2 + int(a.get("--centre_proj", 0))
                th = int(a.get("--thickness_proj", 10))
                proj = vol[max(0, c - th // 2):c + th // 2 + 1].sum(axis=0)
                ppath = out + f"projections/rec_{name}{tag}.mrc"
                spa.write_image(ppath, proj, pixel * spacing)
                plabel = {"": "rlnTomogramProjection", "_half1": "rlnTomogramProjectionHalf1",
                          "_half2": "rlnTomogramHProjectionalf2"}[tag]  # sic: RELION 5.1's label for the second half
                cols.setdefault(plabel, {})[name] = ppath
        if abs(spacing - 1.0) > 1e-2:
            cols.setdefault("rlnTomoTomogramBinning", {})[name] = spacing
        for label, value in (("rlnTomoSizeX", w), ("rlnTomoSizeY", h), ("rlnTomoSizeZ", d)):
            cols.setdefault(label, {})[name] = value
        print(f" + Reconstructing tomogram {name} ({i + 1}/{len(names)})")
    if only == "*":
        for label, values in cols.items():
            g.set_column(label, [values.get(n, "N/A") for n in tset.names])
        write_set(out + "tomograms.star", tset)
        print(f" Written out: {out}tomograms.star")
    elif abs(offset) > 0:  # only the tilt-series file of that tilt series
        rel = tset.value(only, "rlnTomoTiltSeriesStarFile")
        new = out + spa.post(rel)
        os.makedirs(os.path.dirname(new), exist_ok=True)
        t = tset.series[only]
        rl.write_star(new, [rl.StarTable(only, list(t.labels), [list(r) for r in t.rows])])


# ------------------------------------------------------------------ denoising (cryoCARE)
def denoise(argv: list[str]) -> None:
    if not argv or argv[0] not in ("cryoCARE:train", "cryoCARE:predict"):
        die("Usage: relion_python_tomo_denoise [OPTIONS] COMMAND [ARGS]...\nError: No such command")
    cmd = argv[0]
    a = parse(argv[1:], f"relion_python_tomo_denoise:{cmd}")
    spa.need(a, "--tomogram-star-file", "--output-directory")
    star = Path(str(a["--tomogram-star-file"]))
    if not star.exists():
        die("RuntimeError: Could not find tomogram star file")
    path = str(a.get("--cryocare-path", ""))
    needed = ["cryoCARE_train.py", "cryoCARE_extract_train_data.py"] if cmd == "cryoCARE:train" else ["cryoCARE_predict.py"]
    for exe in needed:
        if path and not os.path.isfile(os.path.join(path, exe)):
            die(f"RuntimeError: Could not find {exe} executable in the input path: {path}")
        if not path and shutil.which(exe) is None:
            die(f"/bin/sh: {exe}: command not found")
    g = rl.read_star(star)["global"]
    if not g.has("rlnTomoReconstructedTomogramHalf1"):
        die("RuntimeError: Could not find rlnTomoReconstructedTomogramHalf1 in tomogram star file.")
    out = Path(str(a["--output-directory"]))
    (out / "external" / "training").mkdir(parents=True, exist_ok=True)
    (out / "tilt_series").mkdir(parents=True, exist_ok=True)
    names = g.column("rlnTomoName")
    if cmd == "cryoCARE:train":
        chosen = [n for n in str(a.get("--training-tomograms", "")).split(":") if n]
        for n in chosen:
            if n not in names:
                die(f"RuntimeError: Could not user specified tomogram rlnTomoName {n} in tomogram star file")
        (out / "external" / "training" / "train_config.json").write_text(json.dumps({"model_name": "denoising_model"}))
        model = out / "denoising_model.tar.gz"
        info = out / "external" / "training" / "model.json"
        info.write_text(json.dumps({"trained_on": chosen or names, "subvolumes": a.get("--number-training-subvolumes")}))
        with tarfile.open(model, "w:gz") as tar:
            tar.add(info, arcname="denoising_model/model.json")
    else:
        spa.need(a, "--model-file")
        if not Path(str(a["--model-file"])).exists():
            die(f"RuntimeError: Could not find denoising model {a['--model-file']}")
        only = a.get("--tomogram-name")
        if only is not None and only not in names:
            die(f"RuntimeError: Could not user specified tomogram rlnTomoName {only} in tomogram star file")
        (out / "tomograms").mkdir(parents=True, exist_ok=True)
        den = list(g.column("rlnTomoReconstructedTomogramDenoised")) if g.has("rlnTomoReconstructedTomogramDenoised") \
            else ["N/A"] * len(names)
        for i, (name, h1, h2) in enumerate(zip(names, g.column("rlnTomoReconstructedTomogramHalf1"),
                                                g.column("rlnTomoReconstructedTomogramHalf2"))):
            if only is not None and name != only:
                continue
            v1, v2 = MapVolume.read(h1), MapVolume.read(h2)
            target = out / "tomograms" / f"rec_{name}.mrc"
            v1.like(((v1.data + v2.data) / 2 * 0.5).astype(np.float32)).write(target)
            den[i] = str(target)
        g.set_column("rlnTomoReconstructedTomogramDenoised", den)
    files = []
    for name, rel in zip(names, g.column("rlnTomoTiltSeriesStarFile")):
        copy = out / "tilt_series" / f"{name}.star"
        shutil.copyfile(rel, copy)
        files.append(str(copy))
    g.set_column("rlnTomoTiltSeriesStarFile", files)
    rl.write_star(out / "tomograms.star", [g])
    (out / "log.txt").write_text(f"fake {cmd}\n")
    print(f"fake {cmd}: written {out}/tomograms.star")


# ------------------------------------------------------------------ coordinate import
def _coordinate_file(path: str, centred: bool, scale: float, add: float, name: str) -> tuple[rl.StarTable, bool]:
    """readCoordinateFile: a STAR file, or a text file of 3 (or 6, with the subtomogram angles) columns."""
    if path.endswith(".star"):
        table = next(iter(rl.read_star(path).values()))
        if not table.has("rlnTomoName"):
            if name == "None":
                die(f"readCoordinateFile ERROR: File {path} does not contain rlnTomoName, but no tomoname was provided...")
            table.set_column("rlnTomoName", [name] * len(table))
        table.name = "particles"
    else:
        rows = []
        for line in Path(path).read_text().splitlines():
            words = line.split()
            try:
                values = [float(w) for w in words[:6]]
            except ValueError:
                continue
            if len(values) >= 3:
                rows.append(values)
        labels = ["rlnTomoName"] + ([f"rlnCenteredCoordinate{c}Angst" for c in "XYZ"] if centred
                                    else [f"rlnCoordinate{c}" for c in "XYZ"])
        angles = bool(rows) and all(len(r) >= 6 for r in rows)
        if angles:
            labels += ["rlnTomoSubtomogramRot", "rlnTomoSubtomogramTilt", "rlnTomoSubtomogramPsi", "rlnAngleRot",
                       "rlnAngleTilt", "rlnAnglePsi", "rlnAngleTiltPrior", "rlnAnglePsiPrior"]
        table = rl.StarTable("particles", labels, [
            [name] + [rl._fmt(v * scale + add) for v in r[:3]] + ([rl._fmt(v) for v in r[3:6]] + ["0", "90", "0", "90", "0"]
                                                                  if angles else []) for r in rows])
    return table, all(table.has(f"rlnTomoSubtomogram{e}") for e in ("Rot", "Tilt", "Psi"))


def import_coordinates(argv: list[str]) -> None:
    a = parse(argv, "relion_tomo_import_coordinates")
    spa.need(a, "--i", "--o")
    out = outdir(a)
    pattern = str(a["--i"])
    files = sorted(glob.glob(pattern))
    if len(files) == 1:  # a single file is read as the STAR file listing the coordinate files
        listing = next(iter(rl.read_star(files[0]).values()), None) if files[0].endswith(".star") else None
        if listing is None or not listing.has("rlnTomoName"):
            die(f"Input star file {pattern} does not contain tomogram names (rlnTomoName)")
        if not listing.has("rlnTomoImportParticleFile"):
            die(f"Input star file {pattern} does not contain coordinate file names (rlnTomoImportParticleFile)")
        entries = list(zip(listing.column("rlnTomoName"), listing.column("rlnTomoImportParticleFile")))
    else:
        entries = []
        for f in files:
            name = "None"
            tables = rl.read_star(f) if f.endswith(".star") else {}
            if not any(t.has("rlnTomoName") for t in tables.values()):
                name = os.path.basename(f).rsplit(".", 1)[0]
                for sub in (a.get("--remove_substring", ""), a.get("--remove_substring2", "")):
                    if sub:
                        name = name.replace(sub, "")
            entries.append((name, f))
    tables, with_angles = [], []
    for name, f in entries:
        table, angles = _coordinate_file(f, "--centered" in a, float(a.get("--scale_factor", 1)),
                                         float(a.get("--add_factor", 0)), name)
        tables.append(table)
        with_angles.append(angles)
    if any(with_angles) and not all(with_angles):
        die("ERROR: some, but not all coordinate files contain prior angle information...")
    labels = tables[0].labels if tables else ["rlnTomoName"]
    if any(set(t.labels) != set(labels) for t in tables):
        die("ERROR: the coordinate files do not all have the same columns")
    rows = [[row[t.labels.index(lab)] for lab in labels] for t in tables for row in t.rows]
    rl.write_star(out + "particles.star", [rl.StarTable("particles", list(labels), rows)])
    print(f" Done! Imported coordinates as output {out}particles.star")


# ------------------------------------------------------------------ optimisation sets
def optimisation(a: dict, references: bool = False) -> dict[str, str]:
    """The files of the optimisation set (--i), overridden by --p, --t, --mot (and --ref1, --ref2, --mask, --fsc)."""
    files = tm.read_optimisation_set(a["--i"]) if "--i" in a else {}
    if "--i" in a and not os.path.exists(a["--i"]):
        die(f"Cannot read optimisation set {a['--i']}")
    for opt, key in (("--p", "particles"), ("--t", "tomograms"), ("--mot", "trajectories"), ("--ref1", "ref1"),
                     ("--ref2", "ref2"), ("--mask", "mask"), ("--fsc", "fsc")):
        if opt in a:
            files[key] = a[opt]
    for key in ("particles", "tomograms") + (("ref1", "ref2") if references else ()):
        if key not in files:
            die(f"ERROR: no {key} file given (optimisation set or command line)")
        if not os.path.exists(files[key]):
            die(f"Cannot read file {files[key]} (from {os.getcwd()})")
    return files


def write_optimisation_set(path: str, **files: str | None) -> None:
    tm.write_optimisation_set(path, **files)


def particle_set(path: str, tset: tm.TiltSeriesSet) -> dict[str, rl.StarTable]:
    """ParticleSet: the particles (named TS/n when they have no names), their optics (from the tomogram set when the file
    has none) and their centred positions (from pixels of the tilt series for RELION 4 coordinates)."""
    tables = rl.read_star(path)
    parts = tables.get("particles") or next((t for t in tables.values() if t.loop and t.has("rlnTomoName")), None)
    if parts is None or not len(parts):
        die(f"ParticleSet: no particles in {path}")
    names = parts.column("rlnTomoName")
    unknown = sorted(set(names) - set(tset.names))
    if unknown:
        die(f"ERROR: the particles of tomogram {unknown[0]} are not in the tomogram set")
    optics = tables.get("optics")
    if optics is None:
        group_of = {n: (tset.value(n, "rlnOpticsGroupName") or n) for n in tset.names}
        groups: list[str] = []
        for n in names:
            if group_of[n] not in groups:
                groups.append(group_of[n])
        optics = rl.StarTable("optics")
        firsts = [next(n for n in names if group_of[n] == grp) for grp in groups]
        optics.set_column("rlnVoltage", [float(tset.value(n, "rlnVoltage")) for n in firsts])
        optics.set_column("rlnSphericalAberration", [float(tset.value(n, "rlnSphericalAberration")) for n in firsts])
        optics.set_column("rlnAmplitudeContrast", [float(tset.value(n, "rlnAmplitudeContrast")) for n in firsts])
        optics.set_column("rlnTomoTiltSeriesPixelSize", [pixel_of(tset, n) for n in firsts])
        optics.set_column("rlnOpticsGroup", list(range(1, len(groups) + 1)))
        optics.set_column("rlnOpticsGroupName", groups)
        parts.set_column("rlnOpticsGroup", [groups.index(group_of[n]) + 1 for n in names])
    if not parts.has("rlnTomoParticleName"):
        count: dict[str, int] = {}
        out = []
        for n in names:
            count[n] = count.get(n, 0) + 1
            out.append(f"{n}/{count[n]}")
        parts.set_column("rlnTomoParticleName", out)
    if not parts.has("rlnCenteredCoordinateXAngst"):
        if not parts.has("rlnCoordinateX"):
            die(f"ERROR: the particles in {path} have no coordinates")
        for c, lab in zip("XYZ", ("rlnTomoSizeX", "rlnTomoSizeY", "rlnTomoSizeZ")):
            values = []
            for n, v in zip(names, parts.floats(f"rlnCoordinate{c}")):
                size = float(tset.value(n, lab) or 0)
                values.append((v - (size / 2 - 1)) * pixel_of(tset, n))
            parts.set_column(f"rlnCenteredCoordinate{c}Angst", values)
        parts.drop("rlnCoordinateX", "rlnCoordinateY", "rlnCoordinateZ")
    general = tables.get("general") or rl.StarTable("general", ["rlnTomoSubTomosAre2DStacks"], [["0"]], loop=False)
    return {"general": general, "optics": optics, "particles": parts}


# ------------------------------------------------------------------ subtomogram extraction
def subtomo(argv: list[str]) -> None:
    a = parse(argv, "relion_tomo_subtomo")
    spa.need(a, "--o")
    files = optimisation(a)
    out = outdir(a)
    tset = read_set(files["tomograms"])
    ps = particle_set(files["particles"], tset)
    parts, optics = ps["particles"], ps["optics"]
    stack2d, real3d = "--stack2d" in a, "--real_subtomo" in a
    f16 = "--float16" in a
    names = parts.column("rlnTomoName")
    pixel = pixel_of(tset, names[0])
    if float(a.get("--box_size_A", -1)) > 0:
        if int(a.get("--binned_size_pix", -1)) <= 0:
            die("ERROR: --binned_size_pix is needed with --box_size_A")
        binning = float(a["--box_size_A"]) / (int(a["--binned_size_pix"]) * pixel)
        crop = int(a["--binned_size_pix"])
        pre = float(a.get("--precrop_size_A", -1))
        box = crop if pre <= 0 else int(round(pre / (binning * pixel)))
    else:
        binning, box = float(a.get("--bin", 1)), int(a.get("--b", -1))
        crop = int(a.get("--crop", -1))
        if box <= 0:
            die("ERROR: give the box size (--b or --box_size_A)")
        crop = crop if crop > 0 else box
    max_dose, min_frames = float(a.get("--max_dose", -1)), int(a.get("--min_frames", 1))
    xyz = tm.centered_positions(parts)
    keep, images, ctfs, visible_col = [], [], [], []
    images2d: dict[str, list[np.ndarray]] = {}
    cache: dict[str, np.ndarray] = {}
    for i, (ts, pname) in enumerate(zip(names, parts.column("rlnTomoParticleName"))):
        t = tset.series[ts]
        if not t.has("rlnMicrographName"):
            die(f"ERROR: the tilt series {ts} have no rlnMicrographName")
        tilts = t.floats("rlnTomoNominalStageTiltAngle")
        doses = t.floats("rlnMicrographPreExposure")
        patches, visible = [], []
        for name, tilt, dose in zip(t.column("rlnMicrographName"), tilts, doses):
            if name not in cache:
                cache[name] = spa.read_image(name)
            img = cache[name]
            hh, ww = img.shape
            u = (xyz[i, 0] * np.cos(np.radians(tilt)) + xyz[i, 2] * np.sin(np.radians(tilt))) / pixel + ww / 2
            v = xyz[i, 1] / pixel + hh / 2
            seen = 0 <= u < ww and 0 <= v < hh and (max_dose <= 0 or dose <= max_dose)
            visible.append(1 if seen else 0)
            if not seen:
                continue
            size = int(round(crop * binning))
            pad = np.pad(img, size, mode="edge")
            y0, x0 = int(round(v)) + size - size // 2, int(round(u)) + size - size // 2
            patch = pad[y0:y0 + size, x0:x0 + size]
            if size != crop:
                from scipy import ndimage
                patch = ndimage.zoom(patch, crop / size, order=1)[:crop, :crop]
            patches.append(patch.astype(np.float32))
        if sum(visible) < min_frames:
            continue
        keep.append(i)
        root = out + "Subtomograms/" + pname
        os.makedirs(os.path.dirname(root), exist_ok=True)
        stack = np.stack(patches)
        if stack2d:
            spa.write_image(root + "_stack2d.mrcs", stack, pixel * binning, f16)
            images.append(root + "_stack2d.mrcs")
            visible_col.append("[" + ",".join(str(v) for v in visible) + "]")
        else:
            vol = np.repeat(stack.mean(axis=0)[None], crop, axis=0)
            data, weight = (root + "_subtomo.mrc", root + "_ctf.mrc") if real3d else (root + "_data.mrc", root + "_weights.mrc")
            spa.write_image(data, vol, pixel * binning, f16)
            spa.write_image(weight, np.ones_like(vol), pixel * binning, f16)
            images.append(data)
            ctfs.append(weight)
            if real3d:
                images2d.setdefault(ts, []).append(stack[len(stack) // 2])
        spa.progress(len(keep), len(names), "Subtomograms")
    if not keep:
        die("ERROR: no particle is visible in enough tilt images")
    sel = parts.select(keep)
    sel.set_column("rlnImageName", images)
    if not stack2d:
        sel.set_column("rlnCtfImage", ctfs)
    else:
        sel.set_column("rlnTomoVisibleFrames", visible_col)
        sel.drop("rlnCtfImage")
    sel.name = "particles"
    n_groups = len(optics)
    optics.set_column("rlnCtfDataAreCtfPremultiplied", [1 if stack2d else 0] * n_groups)
    optics.set_column("rlnImageDimensionality", [2 if stack2d else 3] * n_groups)
    optics.set_column("rlnTomoSubtomogramBinning", [binning] * n_groups)
    optics.set_column("rlnImagePixelSize", [binning * pixel] * n_groups)
    optics.set_column("rlnImageSize", [crop] * n_groups)
    general = rl.StarTable("general", ["rlnTomoSubTomosAre2DStacks"], [["1" if stack2d else "0"]], loop=False)
    rl.write_star(out + "particles.star", [general, optics, sel])
    if real3d:
        rows = []
        for ts, imgs in images2d.items():
            proj = out + f"Subtomograms/{ts}/proj2d.mrcs"
            spa.write_image(proj, np.stack(imgs), pixel * binning, f16)
            rows += [f"{k + 1}@{proj}" for k in range(len(imgs))]
        sel2 = sel.select(range(len(sel)))
        sel2.set_column("rlnImageName", rows)
        sel2.drop("rlnCtfImage")
        opt2 = optics.select(range(len(optics)))
        opt2.set_column("rlnImageDimensionality", [2] * n_groups)
        rl.write_star(out + "particles_for_class2d.star", [general, opt2, sel2])
    write_optimisation_set(out + "optimisation_set.star", particles=out + "particles.star", tomograms=files["tomograms"],
                           trajectories=files.get("trajectories"))
    print(f" Written out {len(keep)} subtomograms: {out}particles.star")


# ------------------------------------------------------------------ particle reconstruction
def reconstruct_particle(argv: list[str]) -> None:
    a = parse(argv, "relion_tomo_reconstruct_particle")
    spa.need(a, "--o", "--b")
    files = optimisation(a)
    out = outdir(a)
    os.makedirs(out + "temp", exist_ok=True)
    tset = read_set(files["tomograms"])
    parts = particle_set(files["particles"], tset)["particles"]
    box, crop = int(a["--b"]), int(a.get("--crop", -1))
    apix = pixel_of(tset, parts.column("rlnTomoName")[0]) * float(a.get("--bin", 1))
    halves = parts.has("rlnRandomSubset")
    if not halves:
        print(f"WARNING: The input particles  in {files['particles']} have no rlnRandomSubset to specify halfsets, "
              "joining all particles in half1")
    subsets = parts.column("rlnRandomSubset") if halves else ["1"] * len(parts)

    def write(tag: str, n: int, seed: int) -> None:
        rng = np.random.default_rng(seed)
        data = spa.blob_map(box, apix) + rng.normal(0, 2.0 / np.sqrt(max(n, 1)), (box, box, box)).astype(np.float32)
        spa.write_image(out + f"data_{tag}.mrc", data, apix)
        spa.write_image(out + f"weight_{tag}.mrc", np.ones_like(data), apix)
        if 0 < crop < box:
            spa.write_image(out + f"{tag}_full.mrc", data, apix)
            m = (box - crop) // 2
            data = data[m:m + crop, m:m + crop, m:m + crop]
        spa.write_image(out + f"{tag}.mrc", data, apix)
    if halves:
        for h in (1, 2):
            write(f"half{h}", subsets.count(str(h)), 10 + h)
    write("merged", len(parts), 13)
    print(f" Reconstructed {len(parts)} particles: {out}merged.mrc")


# ------------------------------------------------------------------ CTF refinement and frame alignment
def _check_references(files: dict[str, str]) -> None:
    for key in ("ref1", "ref2", "mask"):
        if key in files:
            MapVolume.read(files[key], header_only=True)
    if "fsc" in files and "fsc" not in rl.read_star(files["fsc"]):
        die(f"ERROR: {files['fsc']} has no data_fsc table (a postprocess.star)")


def refine_ctf(argv: list[str]) -> None:
    a = parse(argv, "relion_tomo_refine_ctf")
    spa.need(a, "--o", "--b")
    files = optimisation(a, references=True)
    _check_references(files)
    if "--per_frame_scale" in a and "--per_tomogram_scale" in a:
        die("ERROR: The options --per_frame_scale and --per_tomogram_scale are mutually exclusive")
    out = outdir(a)
    tset = read_set(files["tomograms"])
    particle_set(files["particles"], tset)
    defocus, scale = "--do_defocus" in a, "--do_scale" in a
    for k, name in enumerate(tset.names):
        t = tset.series[name]
        if defocus:
            if not t.has("rlnDefocusU"):
                die(f"ERROR: the tilt series {name} have no defocus (rlnDefocusU)")
            delta = 120.0 - 40.0 * k
            t.set_column("rlnDefocusU", t.floats("rlnDefocusU") + delta)
            t.set_column("rlnDefocusV", t.floats("rlnDefocusV") + delta)
        if scale:
            t.set_column("rlnCtfScalefactor", np.round(np.cos(np.radians(t.floats("rlnTomoNominalStageTiltAngle"))) ** 0.5, 4))
    tomograms = files["tomograms"]
    if defocus or scale:
        write_set(out + "tomograms.star", tset)
        tomograms = out + "tomograms.star"
    write_optimisation_set(out + "optimisation_set.star", particles=files["particles"], tomograms=tomograms,
                           trajectories=files.get("trajectories"))
    print(f" Written out: {out}optimisation_set.star")


def tomo_align(argv: list[str]) -> None:
    a = parse(argv, "relion_tomo_align")
    spa.need(a, "--o", "--b")
    files = optimisation(a, references=True)
    _check_references(files)
    out = outdir(a)
    tset = read_set(files["tomograms"])
    ps = particle_set(files["particles"], tset)
    shift_only, motion = "--shift_only" in a, "--motion" in a
    for k, name in enumerate(tset.names):
        t = tset.series[name]
        t.set_column("rlnTomoXShiftAngst", t.floats("rlnTomoXShiftAngst") + 0.8)
        t.set_column("rlnTomoYShiftAngst", t.floats("rlnTomoYShiftAngst") - 0.6)
        if not shift_only:
            t.set_column("rlnTomoZRot", t.floats("rlnTomoZRot") + 0.05)
    particles = files["particles"]
    if not shift_only:
        parts = ps["particles"]
        for c in "XYZ":
            parts.set_column(f"rlnCenteredCoordinate{c}Angst", parts.floats(f"rlnCenteredCoordinate{c}Angst") + 0.4)
        rl.write_star(out + "particles.star", [ps["general"], ps["optics"], parts])
        particles = out + "particles.star"
    trajectories = files.get("trajectories")
    if motion:
        parts = ps["particles"]
        tables = [rl.StarTable("general", ["rlnParticleNumber"], [[str(len(parts))]], loop=False)]
        for pname, ts in zip(parts.column("rlnTomoParticleName"), parts.column("rlnTomoName")):
            n = len(tset.series[ts])
            traj = rl.StarTable(pname)
            for c, scale in zip("XYZ", (0.3, -0.2, 0.05)):
                traj.set_column(f"rlnOrigin{c}Angst", np.round(scale * np.arange(n), 3))
            tables.append(traj)
        rl.write_star(out + "motion.star", tables)
        trajectories = out + "motion.star"
    write_set(out + "tomograms.star", tset)
    write_optimisation_set(out + "optimisation_set.star", particles=particles, tomograms=out + "tomograms.star",
                           trajectories=trajectories)
    print(f" Written out: {out}optimisation_set.star")


# ------------------------------------------------------------------ relion_refine --ios
def refine_ios(argv: list[str], run) -> None:
    """relion_refine --ios: the particles and tomograms of the optimisation set (MlOptimiser::parseInitial), and an
    optimisation set written with every data file (MlOptimiser::write)."""
    i = argv.index("--ios")
    if i + 1 >= len(argv):
        die("Option --ios needs a value")
    opt = argv[i + 1]
    if not os.path.exists(opt):
        die(f"Cannot read file {opt}")
    files = tm.read_optimisation_set(opt)
    rest = argv[:i] + argv[i + 2:]
    if "--i" not in rest:
        if "particles" not in files:
            die(f"No particles filename was found from command line or in optimisation_set {opt}")
        rest += ["--i", files["particles"]]
    if "--tomograms" not in rest and "tomograms" not in files:
        die(f"No tomograms filename was found from command line or  in optimisation_set {opt}")
    tomograms = rest[rest.index("--tomograms") + 1] if "--tomograms" in rest else files["tomograms"]
    os.environ["FAKE_TOMO"] = "1"
    root = rest[rest.index("--o") + 1] if "--o" in rest else ""
    before = {f: os.path.getmtime(f) for f in glob.glob(root + "*_data.star")}
    run(rest)
    for data in sorted(glob.glob(root + "*_data.star")):
        if before.get(data) == os.path.getmtime(data):
            continue
        write_optimisation_set(data[:-len("_data.star")] + "_optimisation_set.star", particles=data, tomograms=tomograms,
                               trajectories=files.get("trajectories"))


PROGRAMS = {
    "relion_python_tomo_import": tomo_import,
    "relion_align_tiltseries": align_tiltseries, "relion_align_tiltseries_mpi": align_tiltseries,
    "relion_tomo_reconstruct_tomogram": reconstruct_tomogram, "relion_tomo_reconstruct_tomogram_mpi": reconstruct_tomogram,
    "relion_python_tomo_denoise": denoise,
    "relion_tomo_import_coordinates": import_coordinates,
    "relion_tomo_subtomo": subtomo, "relion_tomo_subtomo_mpi": subtomo,
    "relion_tomo_reconstruct_particle": reconstruct_particle, "relion_tomo_reconstruct_particle_mpi": reconstruct_particle,
    "relion_tomo_refine_ctf": refine_ctf, "relion_tomo_refine_ctf_mpi": refine_ctf,
    "relion_tomo_align": tomo_align, "relion_tomo_align_mpi": tomo_align,
}
