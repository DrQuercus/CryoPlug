"""Fake RELION 5.1 single-particle programs for the tests: motion correction, CTF estimation, picking, extraction,
2D classification, initial model, 3D classification (with relion_refine of relion.py), multi-body refinement, symmetry
alignment, class ranking, masks, CTF refinement, Bayesian polishing, STAR file handling, flexibility analysis and
DynaMight.

They accept only the options RELION 5.1 knows (relion_options.json, read from the parser calls of its sources; RELION
itself warns about an unknown option and ignores it, which would hide a misspelt one), run from the project folder
with project-relative paths, name their outputs as RELION does (the part of an input name after its ``jobNNN/``
folder, or the whole name, under the output folder) and write the same files and STAR columns.
The images are computed for real (sums of frames, boxes cut out of the micrographs, class averages), so that the
pictures of the reports show something sensible.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, os.environ.get("CRYOPLUG_ROOT", ""))
from cryoplug import relion as rl  # noqa: E402
from cryoplug.mrc import MapVolume  # noqa: E402

OPTIONS = json.loads((Path(__file__).resolve().parent / "relion_options.json").read_text())


def die(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def parse(argv: list[str], prog: str) -> dict[str, object]:
    spec = OPTIONS[prog]
    out, i = {}, 0
    while i < len(argv):
        opt = argv[i]
        if opt not in spec:
            die(f"Option {opt} is not a valid RELION argument")
        n = spec[opt]
        if n and i + 1 >= len(argv):
            die(f"Option {opt} needs a value")
        out[opt] = argv[i + 1] if n else True
        i += 1 + n
    return out


def need(a: dict, *names: str) -> None:
    for name in names:
        if name not in a:
            die(f"missing {name}")


def post(name: str) -> str:
    """What RELION keeps of an input name for its outputs (decomposePipelineFileName): the part after jobNNN/."""
    m = re.search(r"/job\d{3}[^/]*/(.+)$", name)
    return m.group(1) if m else name


def stem(name: str) -> str:
    return re.sub(r"\.(mrcs?|tiff?|eer|star|mrc\.bz2)$", "", name)


def read_image(path: str) -> np.ndarray:
    import mrcfile
    if not os.path.exists(path.split(":")[0]):
        die(f"Cannot read file {path} (from {os.getcwd()})")
    with mrcfile.open(path.split(":")[0], permissive=True) as m:
        return np.asarray(m.data, dtype=np.float32)


def write_image(path: str, data: np.ndarray, apix: float, float16: bool = False) -> None:
    import mrcfile
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with mrcfile.new(path, overwrite=True) as m:
        m.set_data(np.asarray(data, dtype=np.float16 if float16 else np.float32))
        m.voxel_size = apix


def pdf(path: str) -> None:
    Path(path).write_bytes(b"%PDF-1.4\n% fake RELION logfile\n%%EOF\n")


def outdir(a: dict, key: str = "--o") -> str:
    d = str(a[key])
    if not d.endswith("/"):
        d += "/"
    os.makedirs(d, exist_ok=True)
    return d


def progress(i: int, n: int, label: str) -> None:
    print(f" {label} {i}/{n} ...~~(,_,\">")


# ------------------------------------------------------------------ motion correction
def motioncorr(argv: list[str]) -> None:
    a = parse(argv, "relion_run_motioncorr")
    need(a, "--i", "--o")
    out = outdir(a)
    if "--use_motioncor2" in a:
        exe = str(a.get("--motioncor2_exe", ""))
        if not os.access(exe, os.X_OK):
            die(f"Cannot find the MotionCor2 executable {exe}")
        if "--float16" in a:
            die("MotionCor2 cannot write float16 files")
    elif "--use_own" not in a:
        die("Specify --use_own or --use_motioncor2")
    tables = rl.read_star(str(a["--i"]))
    if "movies" not in tables:
        die(f"{a['--i']} has no data_movies table")
    movies = tables["movies"]
    dose = float(a.get("--dose_per_frame", 1))
    if "--dose_weighting" in a and dose <= 0:
        die("Please specify the dose rate to calculate the grouping for power spectra / dose weighting")
    gain = None
    if "--gainref" in a:
        if not os.path.exists(str(a["--gainref"])):
            die(f"Cannot find the gain reference {a['--gainref']}")
        gain = read_image(str(a["--gainref"]))
    if "--defect_file" in a and not os.path.exists(str(a["--defect_file"])):
        die(f"Cannot find the defect file {a['--defect_file']}")
    apix0 = float(tables["optics"].value("rlnMicrographOriginalPixelSize"))
    binning = float(a.get("--bin_factor", 1))
    first, last = int(a.get("--first_frame_sum", 1)), int(a.get("--last_frame_sum", -1))
    f16 = "--float16" in a
    rows = []
    names = movies.column("rlnMicrographMovieName")
    for i, movie in enumerate(names, 1):
        frames = read_image(movie)
        if frames.ndim != 3:
            die(f"{movie} is not a movie")
        if gain is not None:
            frames = frames * gain
        total = frames[first - 1:(last if last > 0 else len(frames))].sum(axis=0)
        if binning > 1:
            b = int(binning)
            h, w = (total.shape[0] // b) * b, (total.shape[1] // b) * b
            total = total[:h, :w].reshape(h // b, b, w // b, b).mean(axis=(1, 3))
        root = out + stem(movie).replace(".", "_")
        apix = apix0 * binning
        write_image(root + ".mrc", total, apix, f16)
        if "--save_noDW" in a:
            write_image(root + "_noDW.mrc", total, apix, f16)
        if "--grouping_for_ps" in a:
            spec = np.log1p(np.abs(np.fft.fftshift(np.fft.fft2(total - total.mean()))))
            write_image(root + "_PS.mrc", spec, apix, f16)
        n = len(frames)
        shifts = np.array([[0.4 * f, 0.6 * f] for f in range(n)]) * (1 + 0.2 * i)
        general = rl.StarTable("general", ["rlnImageSizeX", "rlnImageSizeY", "rlnImageSizeZ", "rlnMicrographMovieName",
                                           "rlnMicrographBinning", "rlnMicrographOriginalPixelSize",
                                           "rlnMicrographDoseRate", "rlnMicrographPreExposure", "rlnVoltage",
                                           "rlnMicrographStartFrame", "rlnMotionModelVersion"],
                               [[str(frames.shape[2]), str(frames.shape[1]), str(n), movie, f"{binning:g}", f"{apix0:g}",
                                 f"{dose:g}", str(a.get("--preexposure", 0)), "300", "1", "1"]], loop=False)
        gs = rl.StarTable("global_shift")
        gs.set_column("rlnMicrographFrameNumber", list(range(1, n + 1)))
        gs.set_column("rlnMicrographShiftX", shifts[:, 0])
        gs.set_column("rlnMicrographShiftY", shifts[:, 1])
        rl.write_star(root + ".star", [general, gs])
        Path(root + ".log").write_text("fake motion correction\n")
        steps = np.linalg.norm(np.diff(shifts, axis=0), axis=1) * apix0
        rows.append((root, steps.sum(), steps[:2].sum(), steps[2:].sum()))
        progress(i, len(names), "Motion correction")
    optics = tables["optics"]
    optics.set_column("rlnMicrographPixelSize", [apix0 * binning] * len(optics))
    mics = rl.StarTable("micrographs")
    if "--save_noDW" in a and "--dose_weighting" in a:
        mics.set_column("rlnMicrographNameNoDW", [r[0] + "_noDW.mrc" for r in rows])
    if "--grouping_for_ps" in a:
        mics.set_column("rlnCtfPowerSpectrum", [r[0] + "_PS.mrc" for r in rows])
    mics.set_column("rlnMicrographName", [r[0] + ".mrc" for r in rows])
    mics.set_column("rlnMicrographMetadata", [r[0] + ".star" for r in rows])
    mics.set_column("rlnOpticsGroup", [1] * len(rows))
    mics.set_column("rlnAccumMotionTotal", [r[1] for r in rows])
    mics.set_column("rlnAccumMotionEarly", [r[2] for r in rows])
    mics.set_column("rlnAccumMotionLate", [r[3] for r in rows])
    rl.write_star(out + "corrected_micrographs.star", [optics, mics])
    pdf(out + "logfile.pdf")
    print(f" Written: {out}corrected_micrographs.star")


# ------------------------------------------------------------------ CTF estimation
def ctffind(argv: list[str]) -> None:
    a = parse(argv, "relion_run_ctffind")
    need(a, "--i", "--o", "--ctffind_exe")
    out = outdir(a)
    if not os.access(str(a["--ctffind_exe"]), os.X_OK):
        die(f"Cannot find the CTFFIND executable {a['--ctffind_exe']}")
    tables = rl.read_star(str(a["--i"]))
    mics = rl.micrograph_table(tables)
    if "--use_given_ps" in a and not mics.has("rlnCtfPowerSpectrum"):
        die("input micrographs STAR file does not contain rlnCtfPowerSpectrum")
    if "--use_noDW" in a and not mics.has("rlnMicrographNameNoDW"):
        die("input micrographs STAR file does not contain rlnMicrographNameNoDW")
    names = mics.column("rlnMicrographName")
    src = mics.column("rlnCtfPowerSpectrum") if "--use_given_ps" in a else \
        (mics.column("rlnMicrographNameNoDW") if "--use_noDW" in a else names)
    res_max = float(a.get("--ResMax", 5))
    out_rows = []
    for i, (name, given) in enumerate(zip(names, src)):
        if not os.path.exists(given):
            die(f"Cannot find {given}")
        root = out + stem(post(given))
        os.makedirs(os.path.dirname(root), exist_ok=True)
        y, x = np.indices((128, 128)) - 64
        r = np.hypot(x, y)
        rings = np.cos(r ** 2 / (40.0 + 6 * i)) * np.exp(-r / 80)
        write_image(root + ".ctf", rings.astype(np.float32), 1.0)
        defu = 14000.0 + 2500.0 * i
        maxres = 12.0 if i == len(names) - 1 and len(names) > 2 else max(res_max, 3.2 + 0.6 * i)
        Path(root + ".txt").write_text(f"# fake CTFFIND output\n1.0 {defu:.2f} {defu - 250:.2f} 35.0 0.0 0.21 {maxres:.2f}\n")
        Path(root + ".log").write_text("fake ctffind\n")
        out_rows.append((name, root + ".ctf:mrc", defu, defu - 250.0, 0.21, maxres))
        progress(i + 1, len(names), "CTFFIND")
    t = rl.StarTable("micrographs")
    if "--use_noDW" in a:
        t.set_column("rlnMicrographNameNoDW", mics.column("rlnMicrographNameNoDW"))
    t.set_column("rlnMicrographName", [r[0] for r in out_rows])
    t.set_column("rlnOpticsGroup", [1] * len(out_rows))
    t.set_column("rlnCtfImage", [r[1] for r in out_rows])
    t.set_column("rlnDefocusU", [r[2] for r in out_rows])
    t.set_column("rlnDefocusV", [r[3] for r in out_rows])
    t.set_column("rlnCtfAstigmatism", [r[2] - r[3] for r in out_rows])
    t.set_column("rlnDefocusAngle", [35.0] * len(out_rows))
    t.set_column("rlnCtfFigureOfMerit", [r[4] for r in out_rows])
    t.set_column("rlnCtfMaxResolution", [r[5] for r in out_rows])
    if "--do_phaseshift" in a:
        t.set_column("rlnPhaseShift", [90.0] * len(out_rows))
    t.set_column("rlnCtfIceRingDensity", [0.01 * (k + 1) for k in range(len(out_rows))])
    rl.write_star(out + "micrographs_ctf.star", [tables["optics"], t])
    pdf(out + "logfile.pdf")
    print(f" Done! Written out: {out}micrographs_ctf.star")


# ------------------------------------------------------------------ picking
def find_blobs(img: np.ndarray, diameter_px: float, dark: bool = True, adjust: float = 0.0):
    from scipy import ndimage
    sm = ndimage.gaussian_filter(img, max(1.0, diameter_px / 6))
    sig = -sm if dark else sm
    z = (sig - sig.mean()) / (sig.std() or 1.0)
    peaks = z == ndimage.maximum_filter(z, size=max(3, int(diameter_px * 0.8)))
    margin = diameter_px / 2
    ys, xs = np.nonzero(peaks & (z > 1.2 + adjust))
    ok = (xs > margin) & (ys > margin) & (xs < img.shape[1] - margin) & (ys < img.shape[0] - margin)
    return np.stack([xs[ok], ys[ok]], axis=1).astype(float), z[ys[ok], xs[ok]]


def autopick(argv: list[str]) -> None:
    a = parse(argv, "relion_autopick")
    need(a, "--i", "--odir")
    out = outdir(a, "--odir")
    pickname = str(a.get("--pickname", "autopick"))
    modes = [m for m in ("--ref", "--topaz_train", "--topaz_extract", "--LoG") if m in a]
    if len(modes) != 1:
        die("Provide either --ref, --topaz_train, --topaz_extract, or --LoG.")
    tables = rl.read_star(str(a["--i"]))
    names = rl.micrograph_table(tables).column("rlnMicrographName")
    apix = rl.micrograph_pixel_size(tables)
    if modes[0].startswith("--topaz"):
        exe = str(a.get("--fn_topaz_exe", "relion_python_topaz"))
        if not os.access(exe, os.X_OK):
            die(f"Cannot run the Topaz executable {exe}")
        if float(a.get("--particle_diameter", -1)) <= 0:  # RELION would run Topaz with a radius of 0 pixels
            die("fake: Topaz without --particle_diameter (down-scaling 4, radius 0) picks nothing useful")
    if "--topaz_train" in a:
        src = a.get("--topaz_train_parts") or a.get("--topaz_train_picks")
        if not src or not os.path.exists(str(src)):
            die("provide either --topaz_train_picks OR --topaz_train_parts with picked coordinates for training!")
        with open(out + "model_training.txt", "w") as fh:
            fh.write("epoch\titer\tsplit\tloss\tge_penalty\tprecision\tadj_precision\ttpr\tfpr\tauprc\n")
            for e in range(1, 11):
                fh.write(f"{e}\t{e * 100}\ttrain\t{0.5 / e:.4f}\t0\t0.5\t0.5\t0.5\t0.1\t-\n")
                fh.write(f"{e}\t{e * 100}\ttest\t{0.6 / e:.4f}\t0\t0.6\t0.6\t0.6\t0.1\t{0.4 + 0.04 * e - 0.003 * e * e:.4f}\n")
        for e in range(1, 11):
            Path(out + f"model_epoch{e}.sav").write_bytes(b"fake topaz model")
        print(" Done with training! Launch another Auto-picking job to use the model for picking coordinates. ")
        return
    if "--ref" in a:
        ref = str(a["--ref"])
        if not os.path.exists(ref):
            die(f"Cannot read references {ref}")
        if ref.endswith(".star"):
            refs = next(iter(rl.read_star(ref).values()))
            for img in refs.column("rlnReferenceImage"):
                if not os.path.exists(img.split("@", 1)[1]):
                    die(f"Cannot read reference image {img}")
        diameter = float(a.get("--min_distance", 100))
    elif "--LoG" in a:
        diameter = (float(a.get("--LoG_diam_min", 150)) + float(a.get("--LoG_diam_max", 200))) / 2
    else:
        if "--topaz_model" in a and not os.path.exists(str(a["--topaz_model"])):
            die(f"Cannot read the Topaz model {a['--topaz_model']}")
        diameter = float(a["--particle_diameter"])
    adjust = float(a.get("--LoG_adjust_threshold", 0))
    rows, total = [], 0
    for i, mic in enumerate(names, 1):
        img = read_image(mic)
        xy, fom = find_blobs(img, diameter / apix, dark="--Log_invert" not in a, adjust=adjust)
        if "--topaz_extract" in a:
            fom = fom - 3.0
        coords = out + stem(post(mic)) + f"_{pickname}.star"
        os.makedirs(os.path.dirname(coords), exist_ok=True)
        t = rl.StarTable("")
        t.set_column("rlnCoordinateX", xy[:, 0])
        t.set_column("rlnCoordinateY", xy[:, 1])
        t.set_column("rlnClassNumber", [0] * len(xy))
        t.set_column("rlnAutopickFigureOfMerit", fom)
        t.set_column("rlnAnglePsi", [-999.0] * len(xy))
        rl.write_star(coords, [t])
        rows.append((mic, coords))
        total += len(xy)
        progress(i, len(names), "Autopicking")
    general = rl.StarTable("general", ["rlnMicrographPickType"], [["particles"]], loop=False)
    listing = rl.StarTable("coordinate_files")
    listing.set_column("rlnMicrographName", [r[0] for r in rows])
    listing.set_column("rlnMicrographCoordinates", [r[1] for r in rows])
    rl.write_star(out + f"{pickname}.star", [general, listing])
    pdf(out + "logfile.pdf")
    print(f" Total number of particles from {len(names)} micrographs is {total}")


# ------------------------------------------------------------------ extraction
def preprocess(argv: list[str]) -> None:
    from scipy import ndimage
    a = parse(argv, "relion_preprocess")
    need(a, "--i", "--part_star", "--part_dir", "--extract", "--extract_size")
    part_dir = outdir(a, "--part_dir")
    box = int(a["--extract_size"])
    if box % 2:
        die("--extract_size must be even")
    scale = int(a.get("--scale", box))
    mtables = rl.read_star(str(a["--i"]))
    mics = rl.micrograph_table(mtables)
    if not mics.has("rlnDefocusU"):
        die("The micrograph STAR file has no CTF information")
    mic_rows = {name: dict(zip(mics.labels, row)) for name, row in zip(mics.column("rlnMicrographName"), mics.rows)}
    apix = rl.micrograph_pixel_size(mtables)
    picks: dict[str, list[dict]] = {}
    if "--coord_list" in a:
        startend = rl.coordinate_pick_type(str(a["--coord_list"])) == "startend"
        if startend and "--helix" not in a:
            die("Start-end coordinates of filaments need --helix")
        step = float(a.get("--helical_nr_asu", 1)) * float(a.get("--helical_rise", 0)) / apix
        for mic, coords in rl.coordinate_files(str(a["--coord_list"])):
            if mic not in mic_rows:
                continue
            xy, fom = rl.read_coordinates(coords)
            if startend and "--helical_cut_into_segments" in a:  # segments every nr_asu x rise along each tube
                if step <= 0:
                    die("--helical_rise must be positive to cut the tubes into segments")
                for tube, k in enumerate(range(0, len(xy) - 1, 2), 1):
                    (x1, y1), (x2, y2) = xy[k], xy[k + 1]
                    length = float(np.hypot(x2 - x1, y2 - y1))
                    psi = -np.degrees(np.arctan2(y2 - y1, x2 - x1))
                    for t in np.arange(0.0, length + 1e-6, step):
                        f = t / length if length else 0.0
                        picks.setdefault(mic, []).append({
                            "x": x1 + f * (x2 - x1), "y": y1 + f * (y2 - y1), "fom": 0.0,
                            "helix": {"rlnHelicalTubeID": tube, "rlnAngleTiltPrior": 90.0, "rlnAnglePsiPrior": psi,
                                      "rlnHelicalTrackLengthAngst": t * apix, "rlnAnglePsiFlipRatio": 0.5}})
                continue
            for k, (x, y) in enumerate(xy):
                picks.setdefault(mic, []).append({"x": x, "y": y, "fom": float(fom[k]) if fom is not None else 0.0})
    elif "--reextract_data_star" in a:
        ptables = rl.read_star(str(a["--reextract_data_star"]))
        ptab = rl.particle_table(ptables)
        _box, papix = rl.grid_from_star(ptables)
        for row in ptab.rows:
            rec = dict(zip(ptab.labels, row))
            x, y = float(rec["rlnCoordinateX"]), float(rec["rlnCoordinateY"])
            if "--recenter" in a and "rlnOriginXAngst" in rec:
                x -= float(rec["rlnOriginXAngst"]) / apix
                y -= float(rec["rlnOriginYAngst"]) / apix
                rec["rlnOriginXAngst"] = rec["rlnOriginYAngst"] = "0.000000"
            if "--reset_offsets" in a:
                rec["rlnOriginXAngst"] = rec["rlnOriginYAngst"] = "0.000000"
            picks.setdefault(rec["rlnMicrographName"], []).append({"x": x, "y": y, "fom": 0.0, "rec": rec})
    else:
        die("Provide --coord_list or --reextract_data_star")
    min_fom = float(a.get("--minimum_pick_fom", -1e9))
    bg = float(a.get("--bg_radius", box * 0.375 * scale / box))
    out_rows: list[dict] = []
    for i, (mic, plist) in enumerate(picks.items(), 1):
        img = read_image(mic)
        if "--invert_contrast" in a:
            img = -img
        stack, recs = [], []
        for pk in plist:
            if pk["fom"] < min_fom:
                continue
            x0, y0 = int(round(pk["x"])) - box // 2, int(round(pk["y"])) - box // 2
            if x0 < 0 or y0 < 0 or x0 + box > img.shape[1] or y0 + box > img.shape[0]:
                continue
            p = img[y0:y0 + box, x0:x0 + box].astype(np.float32)
            if scale != box:
                p = ndimage.zoom(p, scale / box, order=1)[:scale, :scale]
            if "--norm" in a:
                yy, xx = np.indices(p.shape) - scale / 2
                outside = np.hypot(xx, yy) > bg
                p = (p - p[outside].mean()) / (p[outside].std() or 1.0)
            stack.append(p)
            recs.append(pk)
        if not stack:
            continue
        name = part_dir + stem(post(mic)) + ".mrcs"
        write_image(name, np.stack(stack), apix * box / scale, "--float16" in a)
        Path(part_dir + stem(post(mic)) + "_extract.star").write_text("# fake extraction\n")
        ctf = mic_rows[mic]
        for k, pk in enumerate(recs, 1):
            row = dict(pk.get("rec") or {})
            row.update({"rlnCoordinateX": pk["x"] if "rec" not in pk else row["rlnCoordinateX"],
                        "rlnCoordinateY": pk["y"] if "rec" not in pk else row["rlnCoordinateY"],
                        "rlnImageName": f"{k:06d}@{name}", "rlnMicrographName": mic, "rlnOpticsGroup": 1})
            if "rec" not in pk:
                row.update({"rlnClassNumber": 0, "rlnAutopickFigureOfMerit": pk["fom"], "rlnAnglePsi": -999.0})
            row.update(pk.get("helix") or {})
            for lab in ("rlnCtfMaxResolution", "rlnCtfFigureOfMerit", "rlnDefocusU", "rlnDefocusV", "rlnDefocusAngle"):
                if lab in ctf:
                    row[lab] = ctf[lab]
            row.update({"rlnCtfBfactor": 0.0, "rlnCtfScalefactor": 1.0, "rlnPhaseShift": 0.0})
            out_rows.append(row)
        progress(i, len(picks), "Extraction")
    if not out_rows:
        die("No particle was extracted")
    labels = list(dict.fromkeys(k for r in out_rows for k in r))
    parts = rl.StarTable("particles", labels, [[rl._fmt(r.get(lab, 0)) for lab in labels] for r in out_rows])
    optics = rl.StarTable("optics")
    src = mtables["optics"]
    for lab in ("rlnOpticsGroupName", "rlnOpticsGroup", "rlnMtfFileName", "rlnMicrographOriginalPixelSize", "rlnVoltage",
                "rlnSphericalAberration", "rlnAmplitudeContrast"):
        if src.has(lab):
            optics.set_column(lab, src.column(lab))
    optics.set_column("rlnImagePixelSize", [apix * box / scale] * len(src))
    optics.set_column("rlnImageSize", [scale] * len(src))
    optics.set_column("rlnImageDimensionality", [2] * len(src))
    optics.set_column("rlnCtfDataAreCtfPremultiplied", [0] * len(src))
    rl.write_star(str(a["--part_star"]), [optics, parts])
    if "--pick_star" in a:
        rl.write_star(str(a["--pick_star"]), [rl.StarTable("coordinate_files", ["rlnMicrographName"], [[m] for m in picks])])
    print(f" Written out STAR file with {len(out_rows)} particles in {a['--part_star']}")


# ------------------------------------------------------------------ relion_refine: 2D, initial model, multi-body
def particle_images(tables: dict) -> np.ndarray:
    import mrcfile
    ptab = rl.particle_table(tables)
    cache, out = {}, []
    for name in ptab.column("rlnImageName"):
        num, stack = name.split("@", 1)
        if stack not in cache:
            if not os.path.exists(stack):
                die(f"Cannot open image file {stack} (from {os.getcwd()})")
            with mrcfile.open(stack, permissive=True) as m:
                data = np.asarray(m.data, dtype=np.float32)
            cache[stack] = data if data.ndim == 3 else data[np.newaxis]
        out.append(cache[stack][int(num) - 1])
    return np.stack(out)


def kmeans_classes(images: np.ndarray, k: int) -> np.ndarray:
    small = images[:, ::4, ::4].reshape(len(images), -1)
    small = (small - small.mean(axis=1, keepdims=True)) / (small.std(axis=1, keepdims=True) + 1e-6)
    rng = np.random.default_rng(3)
    centres = small[rng.choice(len(small), min(k, len(small)), replace=False)]
    labels = np.zeros(len(small), int)
    for _ in range(6):
        labels = np.argmin(((small[:, None, :] - centres[None]) ** 2).sum(axis=2), axis=1)
        for c in range(len(centres)):
            if (labels == c).any():
                centres[c] = small[labels == c].mean(axis=0)
    return labels + 1


def model_general(dim: int, k: int, box: int, apix: float, res: float) -> rl.StarTable:
    return rl.StarTable("model_general", ["rlnReferenceDimensionality", "rlnDataDimensionality", "rlnOriginalImageSize",
                                          "rlnCurrentResolution", "rlnPixelSize", "rlnNrClasses"],
                        [[str(dim), "2", str(box), f"{res:.6f}", f"{apix:.6f}", str(k)]], loop=False)


def optimiser(path: str, data: str, model: str, root: str, halves: bool = False) -> None:
    t = rl.StarTable("optimiser_general", ["rlnOutputRootName", "rlnModelStarFile", "rlnExperimentalDataStarFile",
                                           "rlnDoSplitRandomHalves"], [[root, model, data, "1" if halves else "0"]],
                     loop=False)
    rl.write_star(path, [t])


def iterations_to_write(n: int, grad: bool, every: int) -> list[int]:
    return sorted({*range(every, n + 1, every), n}) if grad else list(range(1, n + 1))


def class2d(a: dict) -> None:
    root = str(a["--o"])
    tables = rl.read_star(str(a["--i"]))
    box, apix = rl.grid_from_star(tables)
    images = particle_images(tables)
    k, n_iter = int(a.get("--K", 1)), int(a.get("--iter", 25))
    grad = "--grad" in a
    labels = kmeans_classes(images, k)
    ptab = rl.particle_table(tables)
    written = iterations_to_write(n_iter, grad, int(a.get("--grad_write_iter", 10)))
    for it in range(1, n_iter + 1):
        print(f" {'Gradient optimisation' if grad else 'Expectation'} iteration {it} of {n_iter}")
        if it not in written:
            continue
        last = f"{root}_it{it:03d}"
        avg = np.zeros((k, box, box), np.float32)
        for c in range(1, k + 1):
            if (labels == c).any():
                avg[c - 1] = images[labels == c].mean(axis=0)
        write_image(f"{last}_classes.mrcs", avg, apix)
        counts = np.array([(labels == c).sum() for c in range(1, k + 1)])
        classes = rl.StarTable("model_classes")
        classes.set_column("rlnReferenceImage", [f"{c:06d}@{last}_classes.mrcs" for c in range(1, k + 1)])
        classes.set_column("rlnClassDistribution", counts / max(1, counts.sum()))
        classes.set_column("rlnAccuracyRotations", [2.5 + c for c in range(k)])
        classes.set_column("rlnAccuracyTranslationsAngst", [1.2] * k)
        classes.set_column("rlnEstimatedResolution", [9.0 + 0.5 * c if counts[c] else 999.0 for c in range(k)])
        classes.set_column("rlnOverallFourierCompleteness", [1.0] * k)
        rl.write_star(f"{last}_model.star", [model_general(2, k, box, apix, 9.0), classes])
        ptab.set_column("rlnClassNumber", labels)
        ptab.set_column("rlnAnglePsi", (np.arange(len(labels)) * 37.0) % 360)
        ptab.set_column("rlnOriginXAngst", [0.5] * len(labels))
        ptab.set_column("rlnOriginYAngst", [-0.5] * len(labels))
        stats = np.random.default_rng(7 + it)  # per-particle statistics of the alignment, as RELION writes them
        ptab.set_column("rlnNormCorrection", stats.normal(1.0, 0.08, len(labels)))
        ptab.set_column("rlnLogLikeliContribution", stats.normal(5.2e4, 300.0, len(labels)))
        ptab.set_column("rlnMaxValueProbDistribution", np.clip(stats.beta(4, 3, len(labels)), 0, 1))
        ptab.set_column("rlnNrOfSignificantSamples", stats.integers(1, 60, len(labels)))
        rl.write_star(f"{last}_data.star", list(tables.values()))
        optimiser(f"{last}_optimiser.star", f"{last}_data.star", f"{last}_model.star", root)


def blob_map(box: int, apix: float, shift: float = 0.0) -> np.ndarray:
    z, y, x = np.indices((box, box, box), dtype=np.float32) - box / 2
    out = np.zeros((box, box, box), np.float32)
    for bx, by, bz, sig, w in ((0, 0, 0, 12, 1.0), (16 + shift, 4, 0, 8, 0.8), (-6, 15, 5, 7, 0.7)):
        out += w * np.exp(-((x - bx / apix) ** 2 + (y - by / apix) ** 2 + (z - bz / apix) ** 2) / (2 * (sig / apix) ** 2))
    return out


def initial_model(a: dict) -> None:
    root = str(a["--o"])
    tables = rl.read_star(str(a["--i"]))
    box, apix = rl.grid_from_star(tables)
    particle_images(tables)
    k, n_iter = int(a.get("--K", 1)), int(a.get("--iter", 200))
    for it in range(1, n_iter + 1, max(1, n_iter // 10)):
        print(f" Gradient optimisation iteration {it} of {n_iter}")
    last = f"{root}_it{n_iter:03d}"
    for c in range(1, k + 1):
        write_image(f"{last}_class{c:03d}.mrc", blob_map(box, apix, 2.0 * (c - 1)), apix)
    ptab = rl.particle_table(tables)
    rng = np.random.default_rng(4)
    n = len(ptab)
    ptab.set_column("rlnClassNumber", np.arange(n) % k + 1)
    for lab, hi in (("rlnAngleRot", 360), ("rlnAngleTilt", 180), ("rlnAnglePsi", 360)):
        ptab.set_column(lab, rng.uniform(0, hi, n))
    rl.write_star(f"{last}_data.star", list(tables.values()))
    classes = rl.StarTable("model_classes")
    classes.set_column("rlnReferenceImage", [f"{last}_class{c:03d}.mrc" for c in range(1, k + 1)])
    classes.set_column("rlnClassDistribution", [(k - c + 1) / (k * (k + 1) / 2) for c in range(1, k + 1)])
    classes.set_column("rlnEstimatedResolution", [12.0] * k)
    rl.write_star(f"{last}_model.star", [model_general(3, k, box, apix, 12.0), classes])
    optimiser(f"{last}_optimiser.star", f"{last}_data.star", f"{last}_model.star", root)


def multibody(a: dict) -> None:
    root = str(a["--o"])
    if _mpi() < 3:
        die("MlOptimiserMpi::initialiseWorkLoad: at least 3 MPI processes are required when splitting data into random halves")
    opt = rl.read_star(str(a["--continue"]))["optimiser_general"]
    if opt.value("rlnDoSplitRandomHalves") != "1":  # the final optimiser of an auto-refine has the halves joined
        die("One has to use split random halves with multi-body refinement!")
    model = rl.read_star(opt.value("rlnModelStarFile"))
    consensus = MapVolume.read(model["model_classes"].column("rlnReferenceImage")[0])
    bodies = next(iter(rl.read_star(str(a["--multibody_masks"])).values()))
    for lab in ("rlnBodyMaskName", "rlnBodyRotateRelativeTo", "rlnBodySigmaAngles", "rlnBodySigmaOffset"):
        if not bodies.has(lab):
            die(f"the body STAR file has no {lab}")
    tables = rl.read_star(opt.value("rlnExperimentalDataStarFile"))
    for i, mask_name in enumerate(bodies.column("rlnBodyMaskName"), 1):
        mask = MapVolume.read(mask_name)
        if mask.shape_xyz != consensus.shape_xyz:
            die(f"body mask {mask_name} is {mask.shape_xyz}, the consensus {consensus.shape_xyz}")
        body = consensus.like(consensus.data * mask.data)
        body.write(f"{root}_body{i:03d}.mrc")
        for h in (1, 2):
            body.write(f"{root}_half{h}_body{i:03d}_unfil.mrc")
        print(f" Auto-refine: Iteration= {i}")
    rl.write_star(f"{root}_data.star", list(tables.values()))
    rl.write_star(f"{root}_model.star", [model_general(3, 1, consensus.shape_xyz[0], consensus.pixel_size, 6.0)])


def refine_spa(argv: list[str], legacy) -> None:
    """relion_refine: 2D classification, initial model and multi-body here; 3D refinement and classification in
    relion.py (``legacy``)."""
    a = parse(argv, "relion_refine")
    if "--continue" in a and "--multibody_masks" in a:
        return multibody(a)
    need(a, "--o")
    if "--grad" in a and _mpi() > 1:
        die("Gradient refinement is not supported together with MPI")
    if "--denovo_3dref" in a:
        need(a, "--i")
        return initial_model(a)
    if "--ref" not in a:
        need(a, "--i", "--K")
        return class2d(a)
    legacy(argv)


def _mpi() -> int:
    return int(os.environ.get("FAKE_MPI_SIZE", "1"))


# ------------------------------------------------------------------ symmetry, class ranker, masks
def align_symmetry(argv: list[str]) -> None:
    a = parse(argv, "relion_align_symmetry")
    need(a, "--i", "--o", "--sym")
    if not re.fullmatch(r"[CDcd]\d+|[TOIoti]\d*", str(a["--sym"])):
        die(f"Invalid symmetry {a['--sym']}")
    src = str(a["--i"])
    if src.endswith("_model.star"):
        classes = rl.read_star(src)["model_classes"]
        best = int(np.argmax(classes.floats("rlnClassDistribution")))
        src = classes.column("rlnReferenceImage")[best]
        print(f" The largest class is {best + 1}")
    MapVolume.read(src).write(str(a["--o"]))
    print(f" The aligned map has been written to {a['--o']}")


def class_ranker(argv: list[str]) -> None:
    a = parse(argv, "relion_class_ranker")
    need(a, "--opt", "--o")
    out = outdir(a)
    opt = rl.read_star(str(a["--opt"]))["optimiser_general"]
    model = rl.read_star(opt.value("rlnModelStarFile"))
    classes = model["model_classes"]
    dist = classes.floats("rlnClassDistribution")
    scores = np.clip(0.2 + 2.5 * dist / max(dist.max(), 1e-6) * 0.3, 0, 1)
    scores[np.argmax(dist)] = 0.9
    root = str(a.get("--fn_root", "rank"))
    classes.set_column("rlnClassScore", scores)
    rl.write_star(out + f"{root}_model.star", list(model.values()))
    chosen = np.flatnonzero(scores >= float(a.get("--min_score", 0.5))) + 1
    data = rl.read_star(opt.value("rlnExperimentalDataStarFile"))
    ptab = rl.particle_table(data)
    keep = np.isin(np.array([int(float(c)) for c in ptab.column("rlnClassNumber")]), chosen)
    if "--auto_select" in a:
        rl.write_star(out + str(a.get("--fn_sel_parts", "particles.star")),
                      [t if t is not ptab else ptab.select(np.flatnonzero(keep)) for t in data.values()])
        rl.write_star(out + str(a.get("--fn_sel_classavgs", "class_averages.star")), [classes.select(chosen - 1)])
    optimiser(out + f"{root}_optimiser.star", opt.value("rlnExperimentalDataStarFile"), out + f"{root}_model.star", root)
    print(f" Selected {len(chosen)} classes")


def mask_create(argv: list[str]) -> None:
    from scipy import ndimage
    a = parse(argv, "relion_mask_create")
    need(a, "--i", "--o")
    vol = MapVolume.read(str(a["--i"]))
    data = ndimage.gaussian_filter(vol.data, 2.0) if "--lowpass" in a else vol.data
    core = data > float(a.get("--ini_threshold", 0.01))
    if not core.any():
        die("the threshold leaves nothing")
    ext = int(a.get("--extend_inimask", 0))
    if ext:
        core = ndimage.binary_dilation(core, iterations=ext)
    soft = int(a.get("--width_soft_edge", 0))
    dist = ndimage.distance_transform_edt(~core)
    mask = np.where(core, 1.0, np.where(dist <= soft, 0.5 + 0.5 * np.cos(np.pi * dist / max(soft, 1)), 0.0))
    vol.like(mask.astype(np.float32)).write(str(a["--o"]))
    print(f" Done creating mask! Written out: {a['--o']}")


# ------------------------------------------------------------------ CTF refinement and polishing
def check_post(path: str) -> dict:
    if not os.path.exists(path):
        die(f"Cannot read the postprocess STAR file {path}")
    tables = rl.read_star(path)
    general = tables.get("general")
    for lab in ("rlnUnfilteredMapHalf1", "rlnUnfilteredMapHalf2", "rlnMaskName"):
        name = general.value(lab) if general is not None else None
        if not name or not os.path.exists(name):
            die(f"{path}: {lab} {name} not found (relative to {os.getcwd()})")
    return tables


def ctf_refine(argv: list[str]) -> None:
    a = parse(argv, "relion_ctf_refine")
    need(a, "--i", "--f", "--o")
    out = outdir(a)
    check_post(str(a["--f"]))
    tables = rl.read_star(str(a["--i"]))
    ptab = rl.particle_table(tables)
    if not ptab.has("rlnAngleRot"):
        die("The particles have no orientations (refine them first)")
    optics = tables["optics"]
    if "--fit_aniso" in a:
        for lab, v in (("rlnMagMat00", 1.002), ("rlnMagMat01", 0.001), ("rlnMagMat10", 0.001), ("rlnMagMat11", 0.998)):
            optics.set_column(lab, [v] * len(optics))
    if "--fit_beamtilt" in a:
        optics.set_column("rlnBeamTiltX", [0.12] * len(optics))
        optics.set_column("rlnBeamTiltY", [-0.08] * len(optics))
    if "--odd_aberr_max_n" in a:
        optics.set_column("rlnOddZernike", ["[0.1,0.2,0.0,0.0,0.0,0.0]"] * len(optics))
    if "--fit_aberr" in a:
        optics.set_column("rlnEvenZernike", ["[0.0,0.1,0.0,0.0,0.0,0.0]"] * len(optics))
    if "--fit_defocus" in a:
        mode = str(a.get("--fit_mode", "fpmfm"))
        if len(mode) != 5 or any(c not in "fmp" for c in mode):
            die(f"Illegal --fit_mode {mode}")
        rng = np.random.default_rng(5)
        ptab.set_column("rlnDefocusU", ptab.floats("rlnDefocusU") + rng.normal(0, 120, len(ptab)))
        ptab.set_column("rlnDefocusV", ptab.floats("rlnDefocusV") + rng.normal(0, 120, len(ptab)))
    rl.write_star(out + "particles_ctf_refine.star", list(tables.values()))
    pdf(out + "logfile.pdf")
    print(f" + Done! Written out : {out}particles_ctf_refine.star")


def motion_refine(argv: list[str]) -> None:
    a = parse(argv, "relion_motion_refine")
    need(a, "--i", "--f", "--corr_mic", "--o")
    out = outdir(a)
    check_post(str(a["--f"]))
    mics = rl.micrograph_table(rl.read_star(str(a["--corr_mic"])))
    if not mics.has("rlnMicrographMetadata"):
        die(f"{a['--corr_mic']} has no rlnMicrographMetadata: use the output of motion correction")
    for meta in mics.column("rlnMicrographMetadata"):
        if not os.path.exists(meta):
            die(f"Cannot read {meta}")
    tables = rl.read_star(str(a["--i"]))
    ptab = rl.particle_table(tables)
    if "--params2" in a or "--params3" in a:
        Path(out + "opt_params_all_groups.txt").write_text("0.445 1785 2.93\n")
        print(" Optimal parameters written to opt_params_all_groups.txt")
        return
    if "--combine_frames" not in a:
        die("nothing to do: --combine_frames or parameter estimation (--params2 / --params3)")
    if "--params_file" in a and not os.path.exists(str(a["--params_file"])):
        die(f"Cannot read {a['--params_file']}")
    images = particle_images(tables)
    _box, apix = rl.grid_from_star(tables)
    by_mic: dict[str, list[int]] = {}
    for i, mic in enumerate(ptab.column("rlnMicrographName")):
        by_mic.setdefault(mic, []).append(i)
    names = ptab.column("rlnImageName")
    for mic, idx in by_mic.items():
        stack = out + stem(post(mic)) + "_shiny.mrcs"
        write_image(stack, images[idx], apix, "--float16" in a)
        Path(out + stem(post(mic)) + "_shiny.star").write_text("# fake\n")
        for k, i in enumerate(idx, 1):
            names[i] = f"{k:06d}@{stack}"
    ptab.set_column("rlnImageName", names)
    rl.write_star(out + "shiny.star", list(tables.values()))
    bf = rl.StarTable("perframe_bfactors")
    frames = np.arange(1, 7)
    bf.set_column("rlnMovieFrameNumber", frames)
    bf.set_column("rlnBfactorUsedForSharpening", -10.0 * (frames - 1))
    bf.set_column("rlnFittedInterceptGuinierPlot", 0.1 - 0.02 * frames)
    rl.write_star(out + "bfactors.star", [bf])
    pdf(out + "logfile.pdf")
    print(f" Done! Written out: {out}shiny.star")


# ------------------------------------------------------------------ STAR handling, flexibility, DynaMight
def star_handler(argv: list[str]) -> None:
    a = parse(argv, "relion_star_handler")
    need(a, "--i", "--o")
    out = str(a["--o"])
    if "--combine" in a:
        merged, seen, label = None, set(), str(a.get("--check_duplicates", ""))
        for name in str(a["--i"]).split():
            tables = rl.read_star(name)
            try:
                main = rl.particle_table(tables)
            except ValueError:
                main = rl.micrograph_table(tables)
            if merged is None:
                merged, base = main.select([]), tables
            for row in main.rows:
                key = row[main.labels.index(label)] if label and label in main.labels else id(row)
                if key in seen:
                    continue
                seen.add(key)
                merged.rows.append([row[main.labels.index(lab)] if lab in main.labels else "0" for lab in merged.labels])
        rl.write_star(out, [t if t.name != merged.name else merged for t in base.values()])
        return
    tables = rl.read_star(str(a["--i"]))
    try:
        main = rl.particle_table(tables)
    except ValueError:
        main = rl.micrograph_table(tables)
    n = len(main)
    if "--split" in a:
        order = np.random.default_rng(6).permutation(n) if "--random_order" in a else np.arange(n)
        nr, size = int(a.get("--nr_split", -1)), int(a.get("--size_split", -1))
        if nr < 0 and size < 0:
            die("nr_split and size_split are both zero. Set at least one of them to be positive.")
        if nr < 0:
            nr = int(np.ceil(n / size))
        if size < 0:
            size = int(np.ceil(n / nr))
        for s in range(nr):
            idx = order[s * size:(s + 1) * size]
            name = re.sub(r"\.star$", "", out) + f"_split{s + 1:05d}.star"
            rl.write_star(name, [t if t is not main else main.select(idx) for t in tables.values()])
            print(f" Written: {name} with {len(idx)} objects.")
        return
    keep = np.ones(n, bool)
    if "--remove_duplicates" in a:
        xy = np.stack([main.floats("rlnCoordinateX"), main.floats("rlnCoordinateY")], axis=1)
        mics = main.column("rlnMicrographName")
        seen: dict[str, list] = {}
        dist = float(a["--remove_duplicates"])
        _box, apix = rl.grid_from_star(tables)
        for i in range(n):
            close = [j for j in seen.get(mics[i], []) if np.hypot(*(xy[i] - xy[j])) * apix < dist]
            if close:
                keep[i] = False
            else:
                seen.setdefault(mics[i], []).append(i)
        print(f" Removed {int((~keep).sum())} duplicated objects from in total {n}")
    elif "--select" in a:
        label = str(a["--select"])
        if not main.has(label):
            die(f"The input STAR file has no {label}")
        values = main.floats(label)
        keep = (values >= float(a.get("--minval", -1e30))) & (values <= float(a.get("--maxval", 1e30)))
    elif "--discard_on_stats" in a:
        keep[: max(1, n // 20)] = False
    rl.write_star(out, [t if t is not main else main.select(np.flatnonzero(keep)) for t in tables.values()])


def flex_analyse(argv: list[str]) -> None:
    a = parse(argv, "relion_flex_analyse")
    need(a, "--model", "--data", "--bodies", "--o")
    root = str(a["--o"])
    bodies = next(iter(rl.read_star(str(a["--bodies"])).values()))
    k = int(a.get("--k", 3))
    shares = [41.0, 23.0, 12.0, 8.0, 5.0, 3.0][:max(k, 3)]
    for c, share in enumerate(shares, 1):
        print(f"  + Component {c} explains {share}% of variance.")
    if "--do_maps" in a:
        base = MapVolume.read(re.sub(r"_model\.star$", "_body001.mrc", str(a["--model"])))
        for c in range(1, k + 1):
            for b in range(1, 11):
                base.like(np.roll(base.data, b - 5, axis=c % 3)).write(f"{root}_component{c:03d}_bin{b:03d}.mrc")
    if int(a.get("--select_eigenvalue", -1)) > 0:
        data = rl.read_star(str(a["--data"]))
        ptab = rl.particle_table(data)
        sel = f"{root}_eval{int(a['--select_eigenvalue']):03d}_select.star"
        rl.write_star(sel, [t if t is not ptab else ptab.select(range(0, len(ptab), 2)) for t in data.values()])
    print(f" {len(bodies)} bodies analysed")
    pdf(root + "_logfile.pdf")


def dynamight(argv: list[str]) -> None:
    if not argv:
        die("no command")
    cmd, rest = argv[0], argv[1:]
    known = {"optimize-deformations": {"--refinement-star-file": 1, "--output-directory": 1, "--initial-model": 1,
                                       "--n-gaussians": 1, "--initial-threshold": 1, "--regularization-factor": 1,
                                       "--n-threads": 1, "--preload-images": 0, "--gpu-id": 1},
             "optimize-inverse-deformations": {"--n-epochs": 1, "--checkpoint-file": 1, "--save-deformations": 0,
                                               "--preload-images": 0, "--gpu-id": 1},
             "deformable-backprojection": {"--batch-size": 1, "--checkpoint-file": 1, "--preload-images": 0, "--gpu-id": 1}}
    if cmd not in known:
        die(f"No such command '{cmd}'")
    spec = known[cmd]
    pos = []
    opts, i = {}, 0
    while i < len(rest):
        if rest[i].startswith("--"):
            if rest[i] not in spec:
                die(f"No such option: {rest[i]}")
            opts[rest[i]] = rest[i + 1] if spec[rest[i]] else True
            i += 1 + spec[rest[i]]
        else:
            pos.append(rest[i])
            i += 1
    if cmd == "optimize-deformations":
        for need_opt in ("--refinement-star-file", "--output-directory", "--initial-model"):
            if need_opt not in opts:
                die(f"Missing option '{need_opt}'")
        d = Path(opts["--output-directory"]) / "forward_deformations" / "checkpoints"
        d.mkdir(parents=True, exist_ok=True)
        (d / "checkpoint_final.pth").write_bytes(b"fake")
        Path(opts["--output-directory"], "initial_model.mrc").write_bytes(Path(opts["--initial-model"]).read_bytes())
        print(" Epoch 1/1 done")
        return
    if not pos or not os.path.isdir(pos[0]):
        die("Missing the output directory of the deformations")
    base = Path(pos[0])
    if not (base / "forward_deformations" / "checkpoints" / "checkpoint_final.pth").exists():
        die("No checkpoint: run optimize-deformations first")
    if cmd == "optimize-inverse-deformations":
        (base / "inverse_deformations").mkdir(exist_ok=True)
        (base / "inverse_deformations" / "inv_chkpt.pth").write_bytes(b"fake")
        return
    if not (base / "inverse_deformations" / "inv_chkpt.pth").exists():
        die("No inverse deformations: run optimize-inverse-deformations first")
    (base / "backprojection").mkdir(exist_ok=True)
    vol = MapVolume.read(base / "initial_model.mrc")
    for h in (1, 2):
        vol.write(base / "backprojection" / f"map_half{h}.mrc")


PROGRAMS = {
    "relion_run_motioncorr": motioncorr, "relion_run_motioncorr_mpi": motioncorr,
    "relion_run_ctffind": ctffind, "relion_run_ctffind_mpi": ctffind,
    "relion_autopick": autopick, "relion_autopick_mpi": autopick,
    "relion_preprocess": preprocess, "relion_preprocess_mpi": preprocess,
    "relion_align_symmetry": align_symmetry, "relion_class_ranker": class_ranker, "relion_mask_create": mask_create,
    "relion_ctf_refine": ctf_refine, "relion_ctf_refine_mpi": ctf_refine,
    "relion_motion_refine": motion_refine, "relion_motion_refine_mpi": motion_refine,
    "relion_star_handler": star_handler, "relion_flex_analyse": flex_analyse, "relion_python_dynamight": dynamight,
}
