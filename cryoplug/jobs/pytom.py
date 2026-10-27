"""Template matching in RELION 5 tomograms with pytom-match-pick (GPU): the particles of a known structure located and
oriented in the tomograms by their correlation with a template, the automatic picking the RELION 5 pipeline lacks.

For each tomogram, pytom_match_template.py correlates the template (the map of the particle at the voxel size of the
tomograms, made by pytom_create_template.py) with the tomogram over the rotations of the angular search, inside a mask
(by default pytom_create_mask.py's sphere, 15 % wider than the particle); given the tomogram set of RELION
(--relion5-tomograms-star) the template is weighted per tilt image by the tilt angle, defocus and dose of each image.
pytom_extract_candidates.py then takes the peaks of the score map above a cut-off (estimated from the number of false
positives allowed, or given), at least a particle radius apart. CryoPlug turns the peaks into a RELION 5 particle set:
positions in Å from the tomogram centre (RELION's convention, as the picker), the angles of the best rotation and the
score (rlnLCCmax).

pytom reads the tilt-series files of a RELION set from ``tilt_series/`` next to its ``tomograms.star`` and finds the
row of a tomogram by its file name ending with the tomogram name: the job writes such a copy of the set and links the
tomograms as ``rec_<name>.mrc``.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

import numpy as np

from cryoplug import relion as rl
from cryoplug import tomo as tm
from cryoplug.jobs import register
from cryoplug.jobs.base import JobContext, JobError, JobType, OutputDef, Param, Slot, extra_args_param
from cryoplug.jobs.relion_common import histogram
from cryoplug.jobs.relion_tomo import CATEGORY, central_slice_thumbnail, tilt_set_input
from cryoplug.jobs.tomo_common import tomo_output

TOOL = "pytom"
RAW = ("rlnTomoReconstructedTomogram",)
DENOISED = ("rlnTomoReconstructedTomogramDenoised",)


@register
class PytomTemplateMatch(JobType):
    name = "pytom_template_match"
    title = "Template matching (pytom-match-pick)"
    category = CATEGORY
    tool = TOOL
    software = ["pytom-match-pick"]
    gpu_param = "gpus"
    description = ("Finds the particles of a known structure in the tomograms by template matching (pytom-match-pick, "
                   "GPU): the template, a map of the particle, is correlated with every tomogram over all rotations, "
                   "weighted per tilt image with the tilt angles, defoci and doses of the RELION tilt series; the "
                   "highest peaks become a RELION 5 particle set with positions, angles and scores.")
    inputs = [Slot("tomograms", ("tomograms",), "Tomograms", help="Reconstructed tomograms (binned 6-8×: 10-20 Å/voxel)."),
              Slot("template", ("map",), "Template", help="A map of the particle: EMDB entry, earlier average, model map."),
              Slot("mask", ("mask",), "Mask", required=False,
                   help="Around the template (default: a sphere 15 % wider than the particle diameter).")]
    params = [
        Param("particle_diameter", "float", 0.0, label="Particle diameter", unit="Å", min=0, required=True,
              help="The longest dimension: the mask, the angular sampling (Crowther) and the distance between picks."),
        Param("invert", "bool", True, label="Invert the template contrast",
              help="Protein is dark in tomograms reconstructed from the tilt series (RELION, IMOD, AreTomo) and white "
                   "in maps: untick only for tomograms with white protein."),
        Param("mirror", "bool", False, label="Mirror the template", help="The other hand (a map of unknown hand)."),
        Param("angular_step", "float", 0.0, label="Angular step", unit="°", min=0, max=90,
              help="0: from the particle diameter and the resolution (Crowther criterion, pytom's default)."),
        Param("symmetry", "int", 1, label="Rotational symmetry about z", min=1, max=60,
              help="Cn symmetry of a template whose axis is z (relion_align_symmetry's convention): n times fewer rotations."),
        Param("low_pass", "float", 0.0, label="Low-pass filter", unit="Å", min=0,
              help="Resolution limit of the matching (also coarsens the angular sampling); 0: none."),
        Param("high_pass", "float", 0.0, label="High-pass filter", unit="Å", min=0, advanced=True, help="0: none."),
        Param("per_tilt", "bool", True, label="Weight by the tilt series (CTF, dose, tilts)",
              help="The template weighted per tilt image with the tilt angles, defoci and doses of the RELION tilt "
                   "series (--relion5-tomograms-star): the most sensitive matching. Needs CTF estimates."),
        Param("ctf_corrected", "choice", "auto", choices=["auto", "yes", "no"], label="Tomograms CTF-corrected",
              labels={"auto": "Auto (yes for RELION's Fourier inversion)", "yes": "Yes (phase-flip model)", "no": "No"},
              help="CTF-corrected tomograms are matched with a phase-flipped template CTF (--tomogram-ctf-model phase-flip)."),
        Param("random_phase", "bool", True, label="Random-phase correction",
              help="Subtracts the scores of a phase-randomised template: fewer false positives (pytom's advice)."),
        Param("spectral_whitening", "bool", False, label="Spectral whitening", advanced=True),
        Param("non_spherical_mask", "bool", False, label="Non-spherical mask", advanced=True,
              help="Needed when the mask given is not a sphere (about twice the time)."),
        Param("denoised", "bool", False, label="Match the denoised tomograms", advanced=True,
              help="Raw tomograms are better: denoising changes the noise the scores are judged against."),
        Param("n_particles", "int", 500, label="Particles per tomogram (at most)", min=1, max=1000000),
        Param("false_positives", "float", 1.0, label="False positives allowed per tomogram", min=0.1,
              help="Sets the score cut-off from the noise of the score map (pytom: about the false positives kept)."),
        Param("cut_off", "float", 0.0, label="Score cut-off", min=0, max=1,
              help="A fixed cut-off on the score (LCCmax) instead; 0: estimated from the false positives allowed."),
        Param("tophat", "bool", True, label="Tophat filter",
              help="Keeps sharp peaks only (tophat transform of the score map): fewer false positives on membranes "
                   "and edges."),
        Param("volume_split", "str", "1 1 1", label="Split each tomogram", placeholder="2 2 1", advanced=True,
              help="Pieces along x, y, z when a tomogram does not fit in the GPU memory."),
        Param("tomo_name", "str", "", label="Only this tomogram", advanced=True, placeholder="rlnTomoName"),
        Param("gpus", "int", 1, label="GPUs", min=1, max=16, help="The angular search is shared between them."),
        extra_args_param(),
    ]
    outputs = [OutputDef("particles", "tomo_particles", "Particles")]

    @classmethod
    def validate(cls, params: dict[str, Any], connected: dict[str, str]) -> list[str]:
        if not float(params.get("particle_diameter") or 0) > 0:
            return ["Give the particle diameter"]
        split = str(params.get("volume_split") or "1 1 1").split()
        if len(split) != 3 or not all(s.isdigit() and int(s) > 0 for s in split):
            return ["Split each tomogram: three whole numbers (x y z), e.g. 2 2 1"]
        if params.get("non_spherical_mask") and "mask" not in connected:
            return ["A non-spherical mask needs the mask input"]
        return []

    def run(self, ctx: JobContext) -> None:
        from cryoplug.mrc import MapVolume
        p = ctx.params
        star, ref, tset = tilt_set_input(ctx, "tomograms")
        if not tm.is_reconstructed(tset):
            raise JobError("These are not reconstructed tomograms (no rlnTomoReconstructedTomogram)")
        only = str(p["tomo_name"]).strip()
        if only and only not in tset.names:
            raise JobError(f"No tomogram {only} in the set ({', '.join(tset.names[:6])}…)")
        labels = DENOISED if p["denoised"] else RAW
        tomos = {n: tm.tomogram_file(tset, n, ctx.project_dir, labels) for n in ([only] if only else tset.names)}
        missing = [n for n, f in tomos.items() if not f]
        if p["denoised"] and len(missing) == len(tomos):
            raise JobError("These tomograms are not denoised: untick 'Match the denoised tomograms'")
        tomos = {n: f for n, f in tomos.items() if f}
        if not tomos:
            raise JobError("None of the tomograms can be found")
        if missing:
            ctx.warn(f"No tomogram file for {', '.join(missing[:5])}: left out")
        first = tset.series[next(iter(tomos))]
        if p["per_tilt"] and not first.has("rlnDefocusU"):
            raise JobError("The tilt series have no CTF estimates (rlnDefocusU): estimate the CTF first, or untick "
                           "'Weight by the tilt series'")
        geometry = {n: self.geometry(ctx, tset, n, f) for n, f in tomos.items()}
        voxels = sorted({round(g["voxel"], 3) for g in geometry.values()})
        if voxels[-1] > voxels[0] * 1.01:
            raise JobError(f"The tomograms have different voxel sizes ({', '.join(f'{v:g}' for v in voxels)} Å): match "
                           "them separately ('Only this tomogram')")
        vox = float(voxels[0])
        diameter = float(p["particle_diameter"])
        ctf_corrected = self.ctf_corrected(ctx)

        work = ctx.path("pytom")
        (work / "tomograms").mkdir(parents=True, exist_ok=True)
        pytom_set = self.pytom_set(ctx, tset, work) if p["per_tilt"] else None
        links = {}
        for n, f in tomos.items():  # pytom finds the row of a tomogram by its file name ending with the tomogram name
            link = work / "tomograms" / f"rec_{n}.mrc"
            if link.is_symlink() or link.exists():
                link.unlink()
            os.symlink(ctx.abs(f), link)
            links[n] = link

        ctx.progress(0.03, "pytom_create_template.py")
        template = self.template(ctx, work, vox, diameter)
        tbox = MapVolume.read(template, header_only=True).shape_xyz[0]
        mask = self.mask(ctx, work, vox, diameter, int(tbox))
        ctx.add_table("Template matching", ["Setting", "Value"], [
            ["Tomograms", f"{len(tomos)} at {vox:g} Å/voxel" + (" (denoised)" if p["denoised"] else "")],
            ["Template", f"{tbox} px box, {'inverted' if p['invert'] else 'not inverted'}{', mirrored' if p['mirror'] else ''}"],
            ["Mask", "the mask given" if ctx.input("mask") else f"sphere of {1.15 * diameter:.0f} Å"],
            ["Angular search", f"{p['angular_step']:g}°" if p["angular_step"] else f"Crowther criterion for {diameter:g} Å"],
            ["Weighting", "per tilt image (tilt angles, defoci, doses)" if pytom_set else "none (no tilt-series metadata)"],
            ["CTF of the tomograms", "corrected (phase-flip model)" if ctf_corrected else "not corrected"],
            ["Extraction", f"≤ {p['n_particles']:,} per tomogram, " + (f"cut-off {p['cut_off']:g}" if p["cut_off"] else
                                                                        f"{p['false_positives']:g} false positive(s)")]])

        rows: list[list[Any]] = []
        summary: list[list[Any]] = []
        scores_all: list[float] = []
        for k, (n, link) in enumerate(links.items()):
            frac = 0.08 + 0.85 * k / len(links)
            ctx.progress(frac, f"pytom_match_template.py: {n} ({k + 1}/{len(links)})")
            self.match(ctx, work, link, template, mask, pytom_set, ctf_corrected, diameter)
            job_json = work / f"{link.stem}_job.json"
            if not job_json.exists():
                raise JobError(f"pytom_match_template.py wrote no {job_json.name} for {n} (see the log)")
            ctx.progress(frac + 0.8 / len(links), f"pytom_extract_candidates.py: {n}")
            found = self.extract(ctx, work, job_json, link.stem, diameter)
            g = geometry[n]
            if len(found):
                xyz = tm.from_binned_voxels(found[:, :3], g["size"], g["pixel"], g["binning"])
                rows += [[n, *pos, *f[3:7]] for pos, f in zip(xyz, found)]
                scores_all += [float(v) for v in found[:, 6]]
            summary.append([n, len(found), f"{found[:, 6].max():.3f}" if len(found) else "–",
                            f"{float(found[0, 7]):.3f}" if len(found) and found.shape[1] > 7 else "–"])
            ctx.log(f"{n}: {len(found)} particles")
        ctx.add_table("Particles found", ["Tomogram", "Particles", "Best score", "Cut-off"], summary)
        if not rows:
            raise JobError("No particle above the cut-off in any tomogram: allow more false positives, lower the cut-off, "
                           "or check the template (contrast, hand, voxel size)")
        values = np.array(scores_all)
        ctx.add_plot("Scores of the particles (LCCmax)", [{"name": "Particles", **histogram(values, 30)}],
                     x_label="LCCmax", y_label="Particles", style="bars")
        self.score_slice(ctx, work, next(iter(links.values())).stem, next(iter(links)))
        labels_out = ["rlnTomoName", "rlnCenteredCoordinateXAngst", "rlnCenteredCoordinateYAngst",
                      "rlnCenteredCoordinateZAngst", "rlnAngleRot", "rlnAngleTilt", "rlnAnglePsi", "rlnLCCmax"]
        table = rl.StarTable("particles", labels_out, [[r[0], *[rl._fmt(float(v)) for v in r[1:8]]] for r in rows])
        pstar = rl.write_star(ctx.path("particles.star"), [table], comment="Template matching with pytom-match-pick")
        tm.write_optimisation_set(ctx.path("optimisation_set.star"), particles=ctx.rel(pstar), tomograms=ref)
        ctx.add_highlight("Particles", f"{len(rows):,}")
        ctx.add_highlight("Median score", f"{float(np.median(values)):.3f}")
        tomo_output(ctx, "particles", pstar, f"{len(rows):,} particles in {sum(1 for s in summary if s[1])} tomograms",
                    {"tomograms": ref}, "template_matching", opt=ctx.path("optimisation_set.star"),
                    thumbnail=central_slice_thumbnail(ctx, tset))

    # -------------------------------------------------------------- steps
    @staticmethod
    def geometry(ctx: JobContext, tset: tm.TiltSeriesSet, name: str, tomogram: str) -> dict[str, Any]:
        """Unbinned size, tilt-series pixel, binning and voxel size of a tomogram (the header checked against the set)."""
        from cryoplug.mrc import MapVolume
        head = MapVolume.read(ctx.abs(tomogram), header_only=True)
        pixel = float(tset.value(name, "rlnTomoTiltSeriesPixelSize") or tset.value(name, "rlnMicrographOriginalPixelSize")
                      or 0)
        binning = float(tset.value(name, "rlnTomoTomogramBinning") or 0)
        shape = [int(v) for v in head.shape_xyz]
        header = float(head.voxel[0])
        if not pixel:
            raise JobError(f"{name}: no pixel size in the tomogram set (rlnTomoTiltSeriesPixelSize)")
        if not binning:
            binning = header / pixel if header > 0 else 1.0
        size = [int(float(tset.value(name, lab) or 0)) for lab in ("rlnTomoSizeX", "rlnTomoSizeY", "rlnTomoSizeZ")]
        if not all(size):
            size = [int(round(s * binning)) for s in shape]
        vox = pixel * binning
        if header > 0 and abs(header - vox) > 0.01 * vox:
            ctx.warn(f"{name}: voxel size {header:g} Å in the header, {vox:g} Å from the tomogram set (used)")
        return {"size": size, "pixel": pixel, "binning": binning, "voxel": vox, "shape": shape}

    @staticmethod
    def ctf_corrected(ctx: JobContext) -> bool:
        choice = ctx.params["ctf_corrected"]
        if choice != "auto":
            return choice == "yes"
        rec = next((j for j in reversed(ctx.ancestors) if j.get("type") == "relion_tomo_reconstruct"), None)
        corrected = bool(rec and (rec.get("params") or {}).get("fourier", True))
        ctx.log(f"Tomograms {'CTF-corrected (Fourier inversion)' if corrected else 'not CTF-corrected'}"
                + ("" if rec else " (no RELION reconstruction upstream)"))
        return corrected

    @staticmethod
    def pytom_set(ctx: JobContext, tset: tm.TiltSeriesSet, work: Path) -> Path:
        """The tomogram set as pytom reads it: tilt-series files in tilt_series/ next to tomograms.star, and the
        columns it needs (binning 1 and no defocus handedness when RELION left them out)."""
        g = tset.global_table
        n = len(g)
        if not g.has("rlnTomoTomogramBinning"):
            g.set_column("rlnTomoTomogramBinning", ["1"] * n)
        if not g.has("rlnTomoHand"):
            g.set_column("rlnTomoHand", ["0"] * n)
        for label in ("rlnVoltage", "rlnSphericalAberration", "rlnAmplitudeContrast", "rlnTomoTiltSeriesPixelSize"):
            if not g.has(label):
                raise JobError(f"The tomogram set has no {label} (needed to weight the template by the tilt series)")
        return tm.write_set(work / "tomograms.star", tset, ctx.project_dir,
                            comment="The tomogram set as pytom-match-pick reads it")

    def template(self, ctx: JobContext, work: Path, vox: float, diameter: float) -> Path:
        p = ctx.params
        src = ctx.require("template")
        box = max(16, int(math.ceil(2.5 * diameter / vox / 2)) * 2)  # 2-3 times the particle (pytom's advice)
        out = work / "template.mrc"
        args = [ctx.program(TOOL, "pytom_create_template.py"), "-i", src.path, "-o", out,
                "--output-voxel-size-angstrom", f"{vox:g}", "-b", str(box)]
        if p["invert"]:
            args.append("--invert")
        if p["mirror"]:
            args.append("--mirror")
        ctx.run(args, tool=TOOL)
        if not out.exists():
            raise JobError("pytom_create_template.py wrote no template (see the log)")
        return out

    def mask(self, ctx: JobContext, work: Path, vox: float, diameter: float, box: int) -> Path:
        from cryoplug.mrc import MapVolume
        out = work / "mask.mrc"
        given = ctx.input("mask")
        if given is not None:  # on the grid of the template
            m = MapVolume.read(given.path)
            data = np.clip(rl.to_grid(m.data, m.pixel_size, box, vox), 0.0, 1.0).astype(np.float32)
            MapVolume(data=data, voxel=np.full(3, vox)).write(out)
            return out
        radius = 1.15 * diameter / 2 / vox  # 10-20 % wider than the particle (pytom's advice)
        args = [ctx.program(TOOL, "pytom_create_mask.py"), "-b", str(box), "-r", f"{radius:.2f}", "-s", "1",
                "--voxel-size", f"{vox:g}", "-o", out]
        ctx.run(args, tool=TOOL)
        if not out.exists():
            raise JobError("pytom_create_mask.py wrote no mask (see the log)")
        return out

    def match(self, ctx: JobContext, work: Path, tomogram: Path, template: Path, mask: Path, pytom_set: Path | None,
              ctf_corrected: bool, diameter: float) -> None:
        p = ctx.params
        args = [ctx.program(TOOL, "pytom_match_template.py"), "-t", template, "-v", tomogram, "-m", mask, "-d", work,
                "-g", *ctx.relative_gpus()]
        if p["angular_step"]:
            args += ["--angular-search", f"{p['angular_step']:g}"]
        else:
            args += ["--particle-diameter", f"{diameter:g}"]
        if int(p["symmetry"]) > 1:
            args += ["--z-axis-rotational-symmetry", str(int(p["symmetry"]))]
        if p["low_pass"]:
            args += ["--low-pass", f"{p['low_pass']:g}"]
        if p["high_pass"]:
            args += ["--high-pass", f"{p['high_pass']:g}"]
        if pytom_set is not None:
            args += ["--relion5-tomograms-star", pytom_set, "--per-tilt-weighting"]
        if ctf_corrected:
            args += ["--tomogram-ctf-model", "phase-flip"]
        if p["random_phase"]:
            args.append("--random-phase-correction")
        if p["spectral_whitening"]:
            args.append("--spectral-whitening")
        if p["non_spherical_mask"]:
            args.append("--non-spherical-mask")
        split = str(p["volume_split"] or "1 1 1").split()
        if split != ["1", "1", "1"]:
            args += ["--volume-split", *split]
        ctx.run(args + ctx.split_extra(), tool=TOOL)

    def extract(self, ctx: JobContext, work: Path, job_json: Path, stem: str, diameter: float) -> np.ndarray:
        """The peaks of one tomogram: (n, 8) binned voxels x y z, rot, tilt, psi, LCCmax, cut-off."""
        p = ctx.params
        args = [ctx.program(TOOL, "pytom_extract_candidates.py"), "-j", job_json, "-n", str(int(p["n_particles"])),
                "--number-of-false-positives", f"{p['false_positives']:g}", "--particle-diameter", f"{diameter:g}"]
        if p["cut_off"]:
            args += ["--cut-off", f"{p['cut_off']:g}"]
        if p["tophat"]:
            args.append("--tophat-filter")
        ctx.run(args, tool=TOOL)
        star = work / f"{stem}_particles.star"
        if not star.exists():
            return np.zeros((0, 8))
        tables = rl.read_star(star)
        t = next((tab for tab in tables.values() if tab.has("rlnCoordinateX")), None)
        if t is None or not len(t):
            return np.zeros((0, 8))
        cols = ["rlnCoordinateX", "rlnCoordinateY", "rlnCoordinateZ", "rlnAngleRot", "rlnAngleTilt", "rlnAnglePsi",
                "rlnLCCmax", "rlnCutOff"]
        return np.stack([t.floats(c) if t.has(c) else np.zeros(len(t)) for c in cols], axis=1)

    @staticmethod
    def score_slice(ctx: JobContext, work: Path, stem: str, name: str) -> None:
        """The central slice of the score map of the first tomogram, next to the tomogram slice (report image)."""
        from cryoplug.imaging import write_png
        from cryoplug.micrographs import preview, tile_sheet
        from cryoplug.mrc import MapVolume
        scores, tomo = work / f"{stem}_scores.mrc", work / "tomograms" / f"{stem}.mrc"
        try:
            s, t = MapVolume.read(scores).data, MapVolume.read(tomo).data
            z = s.shape[0] // 2
            tiles = [preview(t[max(0, z - 2):z + 3].mean(axis=0), 360, sigma=0.8)[0],
                     preview(s[max(0, z - 2):z + 3].max(axis=0), 360, sigma=0.0)[0]]
            png = write_png(ctx.path("scores.png"), tile_sheet(tiles, per_row=2, gap=4))
            ctx.add_image(f"{name}: central slices of the tomogram (left) and of the score map (right)", png)
            ctx.path("scores.json").write_text(json.dumps({"tomogram": name, "slice": z}))
        except Exception as exc:  # cosmetic
            ctx.warn(f"No picture of the score map: {exc}")
