"""Fake pytom-match-pick 0.14 for the tests: the programs CryoPlug runs, dispatched on the program name, with the options
of the real entry points (pytom_tm/entry_points.py, argparse: an unknown option is an error), their checks (template
and mask of the same box, the RELION 5 tomogram set found as pytom finds it) and their output files. The matching is a
real normalised cross-correlation of the tomogram with the masked template, at a single rotation."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import mrcfile
import numpy as np


def read(path: str) -> tuple[np.ndarray, float]:
    with mrcfile.open(path, permissive=True) as m:
        return np.asarray(m.data, dtype=np.float32).copy(), float(m.voxel_size.x)


def write(path: str | Path, data: np.ndarray, voxel: float) -> None:
    with mrcfile.new(str(path), overwrite=True) as m:
        m.set_data(np.ascontiguousarray(data, dtype=np.float32))
        m.voxel_size = voxel


def die(msg: str) -> None:
    print(msg, file=sys.stderr)
    sys.exit(1)


def gaussian_lowpass(data: np.ndarray, voxel: float, resolution: float) -> np.ndarray:
    f = np.fft.fftn(data)
    k2 = sum(np.meshgrid(*[np.fft.fftfreq(n, voxel) ** 2 for n in data.shape], indexing="ij"))
    return np.fft.ifftn(f * np.exp(-k2 * resolution ** 2 * np.log(2))).real.astype(np.float32)


# ------------------------------------------------------------------ pytom_create_template.py
def create_template(argv: list[str]) -> None:
    from scipy import ndimage
    ap = argparse.ArgumentParser(prog="pytom_create_template.py")
    ap.add_argument("-i", "--input-map", required=True)
    ap.add_argument("-o", "--output-file")
    ap.add_argument("--input-voxel-size-angstrom", type=float)
    ap.add_argument("--output-voxel-size-angstrom", type=float, required=True)
    ap.add_argument("--center", action="store_true")
    ap.add_argument("--low-pass", type=float)
    ap.add_argument("-b", "--box-size", type=int)
    ap.add_argument("--invert", action="store_true")
    ap.add_argument("-m", "--mirror", action="store_true")
    ap.add_argument("--log", default="INFO")
    a = ap.parse_args(argv)
    data, vox = read(a.input_map)
    vin, vout = a.input_voxel_size_angstrom or vox, a.output_voxel_size_angstrom
    data = gaussian_lowpass(data, vin, a.low_pass or 2 * vout)
    data = ndimage.zoom(data, vin / vout, order=1)
    n = data.shape[0]
    if a.box_size and a.box_size > n:
        pad = a.box_size - n
        data = np.pad(data, [(pad // 2, pad - pad // 2)] * 3)
    if a.invert:
        data = -data
    if a.mirror:
        data = data[::-1]
    out = a.output_file or f"template_{Path(a.input_map).stem}_{vout:.2f}A.mrc"
    write(out, data, vout)
    print(f"Template written to {out}: box {data.shape[0]}, voxel {vout} A")


# ------------------------------------------------------------------ pytom_create_mask.py
def create_mask(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(prog="pytom_create_mask.py")
    ap.add_argument("-b", "--box-size", type=int, required=True)
    ap.add_argument("-o", "--output-file")
    ap.add_argument("--voxel-size", type=float, default=1.0)
    ap.add_argument("-r", "--radius", type=float, required=True)
    ap.add_argument("--radius-minor1", type=float)
    ap.add_argument("--radius-minor2", type=float)
    ap.add_argument("-s", "--sigma", type=float)
    a = ap.parse_args(argv)
    n = a.box_size
    z, y, x = np.indices((n, n, n)) - n // 2
    r = np.sqrt(x ** 2 + y ** 2 + z ** 2)
    mask = (r <= a.radius).astype(np.float32)
    if a.sigma:
        mask = np.where(r <= a.radius, 1.0, np.exp(-((r - a.radius) ** 2) / (2 * a.sigma ** 2))).astype(np.float32)
    out = a.output_file or f"mask_b{n}px_r{a.radius}px.mrc"
    write(out, mask, a.voxel_size)
    print(f"Mask written to {out}")


# ------------------------------------------------------------------ pytom_match_template.py
def relion5_metadata(star: str, tomogram: str) -> dict:
    """As pytom's parse_relion5_star_data: the row whose rlnTomoName ends the tomogram file name, the tilt-series file
    in tilt_series/ next to tomograms.star (its folder in the set is ignored)."""
    sys.path.insert(0, os.environ.get("CRYOPLUG_ROOT", "."))
    from cryoplug import relion as rl
    tables = rl.read_star(star)
    g = tables.get("global")
    if g is None:
        die(f"ValueError: {star} has no data_global table")
    stem = Path(tomogram).stem
    rows = [i for i, name in enumerate(g.column("rlnTomoName")) if stem.endswith(name)]
    if len(rows) != 1:
        die(f"ValueError: Could not find a unique match for {stem} in {star} ({len(rows)} matches)")
    i = rows[0]

    def val(label: str) -> str:
        if not g.has(label):
            die(f"KeyError: '{label}'")
        return g.rows[i][g.labels.index(label)]
    ts_file = Path(star).parent / "tilt_series" / Path(val("rlnTomoTiltSeriesStarFile")).name
    if not ts_file.exists():
        die(f"FileNotFoundError: {ts_file}")
    ts = next(iter(rl.read_star(ts_file).values()))
    for label in ("rlnTomoNominalStageTiltAngle", "rlnMicrographPreExposure", "rlnDefocusU", "rlnDefocusV"):
        if not ts.has(label):
            die(f"KeyError: '{label}'")
    meta = {"tilt_angles": [float(v) for v in ts.column("rlnTomoNominalStageTiltAngle")],
            "dose": [float(v) for v in ts.column("rlnMicrographPreExposure")],
            "defocus_um": [(float(u) + float(v)) / 2e4 for u, v in zip(ts.column("rlnDefocusU"), ts.column("rlnDefocusV"))],
            "binning": float(val("rlnTomoTomogramBinning")), "tilt_series_pixel_size": float(val("rlnTomoTiltSeriesPixelSize")),
            "defocus_handedness": int(float(val("rlnTomoHand"))), "voltage_kv": float(val("rlnVoltage")),
            "cs_mm": float(val("rlnSphericalAberration")), "amplitude_contrast": float(val("rlnAmplitudeContrast"))}
    meta["voxel_size"] = meta["binning"] * meta["tilt_series_pixel_size"]
    return meta


def lcc(volume: np.ndarray, template: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Local normalised cross-correlation of the volume with the template inside the mask (FFT, 'same' size)."""
    from scipy.signal import fftconvolve
    w = mask / mask.sum()
    t = template * mask
    mt = (t * w).sum() / w.sum()
    t = (template - mt) * mask
    t /= np.sqrt((t ** 2).sum()) or 1.0
    flip = (slice(None, None, -1),) * 3
    num = fftconvolve(volume, t[flip], mode="same")
    mean = fftconvolve(volume, w[flip], mode="same")
    mean2 = fftconvolve(volume ** 2, w[flip], mode="same")
    std = np.sqrt(np.maximum(mean2 - mean ** 2, 1e-12) * mask.sum())
    return (num / std).astype(np.float32)


def match_template(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(prog="pytom_match_template.py")
    ap.add_argument("-t", "--template", required=True)
    ap.add_argument("-v", "--tomogram", required=True)
    ap.add_argument("-d", "--destination", default="./")
    ap.add_argument("-m", "--mask", required=True)
    ap.add_argument("--non-spherical-mask", action="store_true")
    ap.add_argument("--mirror-template", action="store_true")
    ap.add_argument("--invert-template-contrast", action="store_true")
    ap.add_argument("--particle-diameter", type=float)
    ap.add_argument("--angular-search", type=str)
    ap.add_argument("--z-axis-rotational-symmetry", type=int, default=1)
    ap.add_argument("-s", "--volume-split", type=int, nargs=3, default=[1, 1, 1])
    ap.add_argument("--search-x", type=int, nargs=2)
    ap.add_argument("--search-y", type=int, nargs=2)
    ap.add_argument("--search-z", type=int, nargs=2)
    ap.add_argument("--tomogram-mask")
    ap.add_argument("-a", "--tilt-angles", nargs="+")
    ap.add_argument("--tilt-angles-first-column", nargs=1)
    ap.add_argument("--per-tilt-weighting", action="store_true")
    ap.add_argument("--voxel-size-angstrom", type=float)
    ap.add_argument("--low-pass", type=float)
    ap.add_argument("--high-pass", type=float)
    ap.add_argument("--dose-accumulation")
    ap.add_argument("--defocus")
    ap.add_argument("--amplitude-contrast", type=float)
    ap.add_argument("--spherical-aberration", type=float)
    ap.add_argument("--voltage", type=float)
    ap.add_argument("--phase-shift", type=float, default=0.0)
    ap.add_argument("--tomogram-ctf-model", choices=["phase-flip"])
    ap.add_argument("--defocus-handedness", type=int, choices=[-1, 0, 1], default=0)
    ap.add_argument("--spectral-whitening", action="store_true")
    ap.add_argument("-r", "--random-phase-correction", action="store_true")
    ap.add_argument("--half-precision", action="store_true")
    ap.add_argument("--rng-seed", type=int)
    ap.add_argument("--relion5-tomograms-star")
    ap.add_argument("--warp-xml-file")
    ap.add_argument("-g", "--gpu-ids", type=int, nargs="+", required=True)
    ap.add_argument("--log", default="INFO")
    a = ap.parse_args(argv)
    if not Path(a.destination).is_dir():
        die(f"error: destination {a.destination} is not a directory")
    if a.particle_diameter is None and a.angular_search is None:
        die("ValueError: give --particle-diameter or --angular-search")
    if a.defocus and None in (a.amplitude_contrast, a.spherical_aberration, a.voltage):
        die("ValueError: --defocus needs --amplitude-contrast, --spherical-aberration and --voltage")
    template, tvox = read(a.template)
    mask, _ = read(a.mask)
    if template.shape != mask.shape:
        die("ValueError: Template and mask should have the same box size")
    volume, vox = read(a.tomogram)
    meta = relion5_metadata(a.relion5_tomograms_star, a.tomogram) if a.relion5_tomograms_star else None
    if meta:
        vox = meta["voxel_size"]
    vox = a.voxel_size_angstrom or vox
    if abs(tvox - vox) > 0.01 * vox:
        die(f"ValueError: template voxel size {tvox} differs from the tomogram voxel size {vox}")
    if a.invert_template_contrast:
        template = -template
    if a.low_pass:
        volume = gaussian_lowpass(volume, vox, a.low_pass)
    scores = lcc(volume.astype(np.float64), template.astype(np.float64), mask.astype(np.float64))
    tomo_id = Path(a.tomogram).stem
    dest = Path(a.destination)
    write(dest / f"{tomo_id}_scores.mrc", scores, vox)
    write(dest / f"{tomo_id}_angles.mrc", np.zeros_like(scores), vox)
    job = {"tomo_id": tomo_id, "tomogram": str(Path(a.tomogram).absolute()), "template": a.template, "mask": a.mask,
           "output_dir": str(dest.absolute()), "voxel_size": vox, "particle_diameter": a.particle_diameter,
           "search_space": int(scores.size), "std": float(scores.std()), "gpu_ids": a.gpu_ids,
           "ctf_model": a.tomogram_ctf_model, "random_phase": a.random_phase_correction, "ts_metadata": meta,
           "shape_xyz": list(scores.shape[::-1])}
    (dest / f"{tomo_id}_job.json").write_text(json.dumps(job, indent=1))
    print(f"Progress job_0 on device {a.gpu_ids[0]}: 100%")
    print(f"Max LCC {float(scores.max()):.3f}; written {tomo_id}_scores.mrc, {tomo_id}_angles.mrc, {tomo_id}_job.json")


# ------------------------------------------------------------------ pytom_extract_candidates.py
def extract_candidates(argv: list[str]) -> None:
    from scipy.special import erfcinv
    ap = argparse.ArgumentParser(prog="pytom_extract_candidates.py")
    ap.add_argument("-j", "--job-file", required=True)
    ap.add_argument("--tomogram-mask")
    ap.add_argument("--ignore_tomogram_mask", action="store_true")
    ap.add_argument("-n", "--number-of-particles", type=int, required=True)
    ap.add_argument("--number-of-false-positives", type=float, default=1.0)
    ap.add_argument("--particle-diameter", type=float)
    ap.add_argument("-c", "--cut-off", type=float)
    ap.add_argument("--tophat-filter", action="store_true")
    ap.add_argument("--tophat-connectivity", type=int, default=1)
    ap.add_argument("--tophat-bins", type=int, default=50)
    ap.add_argument("--plot-bins", type=int, default=20)
    ap.add_argument("--relion5-compat", action="store_true")
    ap.add_argument("--log", default="INFO")
    a = ap.parse_args(argv)
    job = json.loads(Path(a.job_file).read_text())
    diameter = a.particle_diameter or job.get("particle_diameter")
    if not diameter:
        die("ValueError: the particle diameter is needed (not given to pytom_match_template.py)")
    out_dir, tomo_id, vox = Path(job["output_dir"]), job["tomo_id"], float(job["voxel_size"])
    scores, _ = read(str(out_dir / f"{tomo_id}_scores.mrc"))
    radius = max(1, int(diameter / 2 / vox))
    sigma = float(scores.std())
    cut = a.cut_off if a.cut_off is not None else float(
        erfcinv(2 * a.number_of_false_positives / scores.size) * np.sqrt(2) * sigma)
    work = scores.copy()
    z, y, x = np.indices(scores.shape)
    peaks = []
    while len(peaks) < a.number_of_particles:
        k = int(np.argmax(work))
        pz, py, px = np.unravel_index(k, work.shape)
        value = float(work[pz, py, px])
        if value < cut or value == -np.inf:
            break
        peaks.append((px, py, pz, value))
        work[(x - px) ** 2 + (y - py) ** 2 + (z - pz) ** 2 <= radius ** 2] = -np.inf
    if a.relion5_compat:
        meta = job.get("ts_metadata") or {}
        binning, pixel = meta.get("binning", 1.0), meta.get("tilt_series_pixel_size", vox)
        shape = np.array(job["shape_xyz"], dtype=float)
        centre = shape * binning / 2 - 1 if meta else shape / 2
        labels = ["rlnCenteredCoordinateXAngst", "rlnCenteredCoordinateYAngst", "rlnCenteredCoordinateZAngst",
                  "rlnAngleRot", "rlnAngleTilt", "rlnAnglePsi", "rlnLCCmax", "rlnCutOff", "rlnSearchStd",
                  "rlnTomoTiltSeriesPixelSize", "rlnTomoName"]
        name = tomo_id[4:] if tomo_id.startswith("rec_") else tomo_id
        rows = [[*[f"{(c * binning - centre[i]) * pixel:.4f}" for i, c in enumerate(pk[:3])], "0", "0", "0",
                 f"{pk[3]:.6f}", f"{cut:.6f}", f"{sigma:.6f}", f"{pixel:g}", name] for pk in peaks]
    else:
        labels = ["rlnCoordinateX", "rlnCoordinateY", "rlnCoordinateZ", "rlnAngleRot", "rlnAngleTilt", "rlnAnglePsi",
                  "rlnLCCmax", "rlnCutOff", "rlnSearchStd", "rlnDetectorPixelSize", "rlnMicrographName"]
        rows = [[str(pk[0]), str(pk[1]), str(pk[2]), "0", "0", "0", f"{pk[3]:.6f}", f"{cut:.6f}", f"{sigma:.6f}",
                 f"{vox:g}", tomo_id] for pk in peaks]
    if peaks:  # as the starfile package writes a single table: an unnamed data block
        text = "# Created by the starfile Python package (fake)\n\ndata_\n\nloop_\n"
        text += "".join(f"_{lab} #{i + 1}\n" for i, lab in enumerate(labels))
        text += "".join("\t".join(r) + "\n" for r in rows)
        (out_dir / f"{tomo_id}_particles.star").write_text(text)
    print(f"Extracted {len(peaks)} particles (cut-off {cut:.4f}){' with the tophat filter' if a.tophat_filter else ''}")


PROGRAMS = {"pytom_create_template.py": create_template, "pytom_create_mask.py": create_mask,
            "pytom_match_template.py": match_template, "pytom_extract_candidates.py": extract_candidates}

if __name__ == "__main__":
    prog = os.path.basename(sys.argv[0])
    if prog not in PROGRAMS:
        die(f"fake pytom: unknown program {prog}")
    print(f"fake {prog} {' '.join(sys.argv[1:])}")
    PROGRAMS[prog](sys.argv[1:])
