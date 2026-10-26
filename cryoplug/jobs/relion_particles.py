"""Particle utilities around RELION's particle files, as CryoSPARC's: particle sets compared (intersection and
differences, by image or by position on the micrograph), orientations rebalanced, particles down-sampled (Fourier
cropping), symmetry expansion (relion_particle_symmetry_expand), optics groups, particles curated on their statistics
(interactive, limits or clusters).

The particle files written are RELION's (optics and particles tables), read by every RELION job of CryoPlug.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from cryoplug import relion as rl
from cryoplug.jobs import register
from cryoplug.jobs.base import JobContext, JobError, JobType, OutputDef, Param, Slot, extra_args_param
from cryoplug.jobs.relion_common import (
    histogram, particles_out_meta, relion_particles, run_relion, viewing_directions,
)


def _main(tables: dict[str, rl.StarTable]) -> rl.StarTable:
    return rl.particle_table(tables)


def _write_subset(ctx: JobContext, tables: dict[str, rl.StarTable], keep: np.ndarray, name: str, comment: str) -> Path:
    ptab = _main(tables)
    return rl.write_star(ctx.path(name), [t if t is not ptab else ptab.select(np.flatnonzero(keep)) for t in tables.values()],
                         comment=comment)


def micrograph_positions(tables: dict[str, rl.StarTable]) -> tuple[list[str], np.ndarray, float]:
    """Micrograph and position (Å on the micrograph, corrected by the alignment shifts) of every particle."""
    ptab = _main(tables)
    if not (ptab.has("rlnMicrographName") and ptab.has("rlnCoordinateX") and ptab.has("rlnCoordinateY")):
        raise JobError("The particles have no micrograph positions (rlnMicrographName, rlnCoordinateX/Y): match them by image")
    apix_mic = rl.optics_value(tables, "rlnMicrographPixelSize") or rl.optics_value(tables, "rlnMicrographOriginalPixelSize")
    apix_mic = float(apix_mic) if apix_mic else rl.grid_from_star(tables)[1]
    xy = np.stack([ptab.floats("rlnCoordinateX"), ptab.floats("rlnCoordinateY")], axis=1) * apix_mic
    if ptab.has("rlnOriginXAngst") and ptab.has("rlnOriginYAngst"):
        xy -= np.stack([ptab.floats("rlnOriginXAngst"), ptab.floats("rlnOriginYAngst")], axis=1)
    return ptab.column("rlnMicrographName"), xy, apix_mic


# ------------------------------------------------------------------ particle sets
@register
class RelionParticleSets(JobType):
    name = "relion_particle_sets"
    title = "Particle sets (intersection, difference)"
    category = "Utilities"
    software = ["RELION"]
    description = ("Compares two particle sets, as CryoSPARC's Particle Sets Tool: the particles in both, those of A "
                   "only and those of B only. Particles are matched by image (the same extracted particle) or by "
                   "position on the micrograph (particles picked or extracted twice). Two micrograph sets are compared "
                   "by micrograph name, as CryoSPARC's Exposure Sets Tool. Join star files gives the union.")
    inputs = [Slot("set_a", ("particles", "micrographs"), "Set A"), Slot("set_b", ("particles", "micrographs"), "Set B")]
    params = [
        Param("match", "choice", "image", choices=["image", "position"], label="Match the particles by",
              labels={"image": "Image (rlnImageName)", "position": "Position on the micrograph"},
              help="Position: for particles re-extracted, re-picked or from another program."),
        Param("distance", "float", 20.0, label="Distance of a match", unit="Å", min=0.5,
              help="Position: particles of the same micrograph closer than this are one (shifts of the alignment "
                   "included)."),
    ]
    outputs = [OutputDef("both", "particles", "In both (A ∩ B)"), OutputDef("a_only", "particles", "In A only (A − B)"),
               OutputDef("b_only", "particles", "In B only (B − A)")]

    def run(self, ctx: JobContext) -> None:
        p = ctx.params
        if {ctx.require("set_a").type, ctx.require("set_b").type} != {"particles"}:
            self.micrograph_sets(ctx)
            return
        a, b = relion_particles(ctx, ctx.require("set_a")), relion_particles(ctx, ctx.require("set_b"))
        ta, tb = a["tables"], b["tables"]
        na, nb = len(_main(ta)), len(_main(tb))
        if p["match"] == "image":
            names_a, names_b = _main(ta).column("rlnImageName"), _main(tb).column("rlnImageName")
            in_b, in_a = set(names_b), set(names_a)
            a_hit = np.array([n in in_b for n in names_a], dtype=bool)
            b_hit = np.array([n in in_a for n in names_b], dtype=bool)
            how = "same image"
        else:
            from scipy.spatial import cKDTree
            mics_a, xy_a, _ = micrograph_positions(ta)
            mics_b, xy_b, _ = micrograph_positions(tb)
            a_hit, b_hit = np.zeros(na, dtype=bool), np.zeros(nb, dtype=bool)
            by_mic: dict[str, list[int]] = {}
            for j, m in enumerate(mics_b):
                by_mic.setdefault(m, []).append(j)
            groups: dict[str, list[int]] = {}
            for i, m in enumerate(mics_a):
                groups.setdefault(m, []).append(i)
            for m, ia in groups.items():
                jb = by_mic.get(m)
                if not jb:
                    continue
                ia_arr, jb_arr = np.array(ia), np.array(jb)
                tree = cKDTree(xy_b[jb_arr])
                d, k = tree.query(xy_a[ia_arr], distance_upper_bound=float(p["distance"]))
                ok = np.isfinite(d)
                a_hit[ia_arr[ok]] = True
                b_hit[jb_arr[k[ok]]] = True
            how = f"same micrograph, within {p['distance']:g} Å"
        rows = [["Set A", f"{na:,}"], ["Set B", f"{nb:,}"], ["In both (A ∩ B)", f"{int(a_hit.sum()):,}"],
                ["A only", f"{int((~a_hit).sum()):,}"], ["B only", f"{int((~b_hit).sum()):,}"]]
        ctx.add_table(f"Particle sets (matched by {how})", ["", "Particles"], rows)
        ctx.add_highlight("Both", f"{int(a_hit.sum()):,}")
        for name, tables, keep, label, src in (("both", ta, a_hit, "in both sets", "A"), ("a_only", ta, ~a_hit, "of A only", "A"),
                                               ("b_only", tb, ~b_hit, "of B only", "B")):
            n = int(keep.sum())
            if not n:
                ctx.log(f"No particle {label}")
                continue
            out = _write_subset(ctx, tables, keep, f"particles_{name}.star", f"Particles {label} (from set {src}; {how})")
            ctx.add_output(name, "particles", out, f"{n:,} particles {label}", meta=particles_out_meta(ctx, out, "sets"))
        if not (a_hit.any() or (~a_hit).any() or (~b_hit).any()):
            raise JobError("Both sets are empty")

    def micrograph_sets(self, ctx: JobContext) -> None:
        """Two micrograph sets compared by name (the file name when the folders differ)."""
        from cryoplug.jobs.relion_common import micrograph_meta, star_of
        a, b = ctx.require("set_a"), ctx.require("set_b")
        if a.type != b.type:
            raise JobError("Compare two particle sets or two micrograph sets, not one of each")
        star_a, star_b = star_of(ctx, a)[0], star_of(ctx, b)[0]
        ta, tb = rl.read_star(star_a), rl.read_star(star_b)
        names_a, names_b = rl.micrograph_table(ta).column("rlnMicrographName"), rl.micrograph_table(tb).column("rlnMicrographName")
        full = bool(set(names_a) & set(names_b))
        key = (lambda m: m) if full else (lambda m: Path(m).name)  # noqa: E731
        in_b, in_a = {key(m) for m in names_b}, {key(m) for m in names_a}
        a_hit = np.array([key(m) in in_b for m in names_a], dtype=bool)
        b_hit = np.array([key(m) in in_a for m in names_b], dtype=bool)
        how = "micrograph name" if full else "micrograph file name"
        ctx.add_table(f"Micrograph sets (matched by {how})", ["", "Micrographs"], [
            ["Set A", f"{len(names_a):,}"], ["Set B", f"{len(names_b):,}"], ["In both (A ∩ B)", f"{int(a_hit.sum()):,}"],
            ["A only", f"{int((~a_hit).sum()):,}"], ["B only", f"{int((~b_hit).sum()):,}"]])
        ctx.add_highlight("Both", f"{int(a_hit.sum()):,}")
        passed = {k: v for k, v in a.meta.items() if k in ("has_motion", "power_spectra", "motion_star")}
        for name, star, keep, label, src in (("both", star_a, a_hit, "in both sets", "A"), ("a_only", star_a, ~a_hit, "of A only", "A"),
                                             ("b_only", star_b, ~b_hit, "of B only", "B")):
            n = int(keep.sum())
            if not n:
                ctx.log(f"No micrograph {label}")
                continue
            out = rl.subset_star(star, ctx.path(f"micrographs_{name}.star"), keep, comment=f"Micrographs {label} (from set {src})")
            ctx.add_output(name, "micrographs", out, f"{n:,} micrographs {label}", meta=micrograph_meta(ctx, out, "sets", **passed))


# ------------------------------------------------------------------ restacking
@register
class RelionRestack(JobType):
    name = "relion_restack"
    title = "Restack particles"
    category = "Utilities"
    tool = "relion"
    software = ["RELION"]
    description = ("Writes the images of a particle set into new stacks (relion_stack_create), as CryoSPARC's Restack "
                   "Particles: a subset kept after cleaning then reads only its own images, from fewer and smaller files "
                   "(faster on network disks, and the original extraction can be removed). Optionally re-centred on the "
                   "alignment shifts, rounded to whole pixels (no interpolation).")
    inputs = [Slot("particles", ("particles",), "Particles")]
    params = [
        Param("split_per_micrograph", "bool", True, label="One stack per micrograph",
              help="As the extraction writes them. Otherwise a single stack, written one image at a time."),
        Param("recentre", "bool", False, label="Re-centre on the alignment shifts",
              help="The shifts rounded to whole pixels are applied to the images (no interpolation); what remains stays "
                   "in the file (--apply_rounded_offsets_only)."),
        Param("float16", "bool", True, label="Images in 16-bit floats", help="Half the disk space (read by RELION 4 and 5)."),
        extra_args_param(),
    ]
    outputs = [OutputDef("particles", "particles", "Restacked particles")]

    def run(self, ctx: JobContext) -> None:
        p = ctx.params
        pin = relion_particles(ctx, ctx.require("particles"))
        if p["recentre"] and not _main(pin["tables"]).has("rlnOriginXAngst"):
            raise JobError("Re-centring needs the alignment shifts (rlnOriginXAngst): refine or classify the particles first")
        root = ctx.path("particles")  # relion_stack_create writes <root>.star and <root>[_<micrograph>].mrcs
        args = [ctx.program("relion", "relion_stack_create"), "--i", pin["ref"], "--o", ctx.rel(root)]
        args.append("--split_per_micrograph" if p["split_per_micrograph"] else "--one_by_one")
        if p["recentre"]:
            args.append("--apply_rounded_offsets_only")
        if p["float16"]:
            args.append("--float16")
        args += ctx.split_extra()
        ctx.progress(0.05, f"relion_stack_create: {pin['n']:,} particles")
        run_relion(ctx, args)
        out = ctx.path("particles.star")
        if not out.exists():
            raise JobError("relion_stack_create wrote no particles.star (see the log)")
        n = rl.star_rows(out)
        stacks = [f for f in ctx.job_dir.rglob("*.mrcs")]
        size = sum(f.stat().st_size for f in stacks)
        ctx.add_table("Restacking", ["", "Value"], [
            ["Particles", f"{n:,} (of {pin['n']:,})"], ["Stacks", f"{len(stacks):,}"],
            ["Size", f"{size / 1e9:.2f} GB" if size >= 1e8 else f"{size / 1e6:.1f} MB"],
            ["Re-centred", "yes (rounded shifts)" if p["recentre"] else "no"]])
        ctx.add_highlight("Particles", f"{n:,}")
        ctx.add_output("particles", "particles", out, f"{n:,} restacked particles", meta=particles_out_meta(ctx, out, "restack"))


# ------------------------------------------------------------------ rebalance orientations
def direction_bins(rot: np.ndarray, tilt: np.ndarray, step: float) -> np.ndarray:
    """Bin of each viewing direction on a sphere cut in bins of about equal area (``step`` degrees across)."""
    el = 90.0 - np.clip(tilt, 0.0, 180.0)  # +90 (tilt 0) to -90
    az = (rot + 180.0) % 360.0
    n_bands = max(1, int(round(180.0 / step)))
    band = np.minimum(n_bands - 1, ((el + 90.0) / 180.0 * n_bands).astype(int))
    centres = -90.0 + (np.arange(n_bands) + 0.5) * 180.0 / n_bands
    per_band = np.maximum(1, np.round(360.0 * np.cos(np.radians(centres)) / step)).astype(int)
    offsets = np.concatenate([[0], np.cumsum(per_band)[:-1]])
    col = np.minimum(per_band[band] - 1, (az / 360.0 * per_band[band]).astype(int))
    return offsets[band] + col


@register
class RelionRebalance(JobType):
    name = "relion_rebalance"
    title = "Rebalance orientations"
    category = "Utilities"
    software = ["RELION"]
    description = ("Removes particles from over-represented views (preferred orientation), as CryoSPARC's Rebalance "
                   "Orientations: the viewing directions are cut in bins of about equal area and each bin keeps at most "
                   "as many particles as a chosen percentile of the bins. The anisotropy of the map drops, at the cost "
                   "of particles.")
    inputs = [Slot("particles", ("particles",), "Particles with poses", help="Refined particles (3D auto-refine...).",
                   prefer=("particles",))]
    params = [
        Param("bin_size", "float", 10.0, label="Size of the view bins", unit="°", min=3, max=45),
        Param("percentile", "float", 80.0, label="Cap at this percentile of the bins", unit="%", min=10, max=100,
              help="Each bin keeps at most as many particles as the occupied bin at this percentile (100: nothing removed)."),
        Param("max_per_bin", "int", 0, label="Particles per bin at most", min=0, help="0 = from the percentile."),
        Param("seed", "int", 0, label="Random seed", advanced=True),
    ]
    outputs = [OutputDef("particles", "particles", "Rebalanced particles"), OutputDef("removed", "particles", "Removed particles")]

    def run(self, ctx: JobContext) -> None:
        p = ctx.params
        parts = relion_particles(ctx, ctx.require("particles"), need_poses=True)
        tables = parts["tables"]
        ptab = _main(tables)
        if not (ptab.has("rlnAngleRot") and ptab.has("rlnAngleTilt")):
            raise JobError("The particles have no poses (rlnAngleRot, rlnAngleTilt): refine them first")
        n = len(ptab)
        bins = direction_bins(ptab.floats("rlnAngleRot"), ptab.floats("rlnAngleTilt"), float(p["bin_size"]))
        ids, counts = np.unique(bins, return_counts=True)
        cap = int(p["max_per_bin"]) or max(1, int(np.ceil(np.percentile(counts, float(p["percentile"])))))
        rng = np.random.default_rng(int(p["seed"]))
        keep = np.ones(n, dtype=bool)
        for b, c in zip(ids, counts):
            if c > cap:
                idx = np.flatnonzero(bins == b)
                keep[rng.choice(idx, c - cap, replace=False)] = False
        kept = int(keep.sum())
        ctx.add_table("Rebalancing", ["", "Value"], [
            ["Particles", f"{n:,}"], ["Occupied view bins", f"{len(ids):,} (about {p['bin_size']:g}° across)"],
            ["Particles per bin", f"median {np.median(counts):.0f}, largest {counts.max():,}"],
            ["Cap per bin", f"{cap:,}"], ["Kept", f"{kept:,} ({100 * kept / n:.1f} %)"]])
        ctx.add_highlight("Kept", f"{kept:,} / {n:,}")
        viewing_directions(ctx, ptab, "Viewing directions before", key="views_before")
        viewing_directions(ctx, ptab.select(np.flatnonzero(keep)), "Viewing directions after", key="views_after")
        out = _write_subset(ctx, tables, keep, "particles.star", f"Rebalanced: at most {cap} particles per view bin")
        ctx.add_output("particles", "particles", out, f"{kept:,} rebalanced particles",
                       meta=particles_out_meta(ctx, out, "rebalance"))
        if kept < n:
            rem = _write_subset(ctx, tables, ~keep, "particles_removed.star", "Particles removed from crowded views")
            ctx.add_output("removed", "particles", rem, f"{n - kept:,} removed particles", meta=particles_out_meta(ctx, rem, "rebalance"))


# ------------------------------------------------------------------ down-sampling
def fourier_crop(images: np.ndarray, box: int) -> np.ndarray:
    """Images (n, N, N) Fourier-cropped to (n, box, box), pixel values kept (the mean of each image is unchanged)."""
    n_old = images.shape[-1]
    spec = np.fft.rfft2(images.astype(np.float32))
    h = box // 2
    cropped = np.concatenate([spec[:, :h, :h + 1], spec[:, n_old - h:, :h + 1]], axis=1)
    out = np.fft.irfft2(cropped, s=(box, box))
    return (out * (box / n_old) ** 2).astype(np.float32)


@register
class RelionDownsample(JobType):
    name = "relion_downsample"
    title = "Downsample particles"
    category = "Utilities"
    software = ["RELION"]
    description = ("Smaller particle images by Fourier cropping, as CryoSPARC's Downsample Particles: faster 2D and 3D "
                   "at a coarser pixel (the resolution limit becomes twice the new pixel size). Writes new stacks and a "
                   "RELION particle file with the new box and pixel size; poses and shifts (in Å) are kept.")
    inputs = [Slot("particles", ("particles",), "Particles")]
    params = [
        Param("box", "int", 128, label="New box size", unit="px", min=16, max=1024,
              help="Even, smaller than the box of the particles: new pixel = old pixel × old box / new box."),
    ]
    outputs = [OutputDef("particles", "particles", "Downsampled particles")]

    @classmethod
    def validate(cls, params: dict[str, Any], connected: dict[str, str]) -> list[str]:
        return ["The new box size must be even"] if int(params.get("box") or 0) % 2 else []

    def run(self, ctx: JobContext) -> None:
        import mrcfile
        p = ctx.params
        parts = relion_particles(ctx, ctx.require("particles"))
        tables = parts["tables"]
        ptab = _main(tables)
        box_old, apix_old = rl.grid_from_star(tables)
        box = int(p["box"])
        if not box_old or box >= box_old:
            raise JobError(f"The new box ({box} px) must be smaller than that of the particles ({box_old} px)")
        apix = apix_old * box_old / box
        names = ptab.column("rlnImageName")
        stacks: dict[str, list[tuple[int, int]]] = {}  # stack -> [(row, index in the stack)]
        for row, name in enumerate(names):
            idx, _, path = name.partition("@")
            stacks.setdefault(path, []).append((row, int(idx) - 1))
        out_dir = ctx.path("Particles")
        out_dir.mkdir(exist_ok=True)
        new_names = list(names)
        done = 0
        for k, (path, items) in enumerate(stacks.items()):
            src = ctx.abs(path)
            if not src.exists():
                raise JobError(f"Particle stack not found: {path}")
            with mrcfile.mmap(str(src), mode="r", permissive=True) as m:
                data = m.data if m.data.ndim == 3 else m.data[np.newaxis]
                order = [i for _, i in items]
                out = np.empty((len(order), box, box), dtype=np.float32)
                for s in range(0, len(order), 256):
                    out[s:s + 256] = fourier_crop(np.asarray(data[order[s:s + 256]]), box)
            dst = out_dir / f"{k + 1:05d}_{Path(path).stem}.mrcs"
            with mrcfile.new(str(dst), overwrite=True) as m:
                m.set_data(out)
                m.voxel_size = apix
            rel = ctx.rel(dst)
            for j, (row, _) in enumerate(items):
                new_names[row] = f"{j + 1:06d}@{rel}"
            done += len(items)
            ctx.progress(0.05 + 0.9 * done / len(names), f"{done:,} of {len(names):,} particles")
        ptab.set_column("rlnImageName", new_names)
        optics = tables.get("optics")
        if optics is not None and optics.rows:
            optics.set_column("rlnImageSize", [str(box)] * len(optics))
            optics.set_column("rlnImagePixelSize", [f"{apix:.6f}"] * len(optics))
        out = rl.write_star(ctx.path("particles.star"), list(tables.values()),
                            comment=f"Fourier-cropped from {box_old} to {box} px ({apix_old:g} to {apix:.4g} Å/px)")
        ctx.add_table("Downsampling", ["", "Before", "After"], [
            ["Box", f"{box_old} px", f"{box} px"], ["Pixel size", f"{apix_old:.4g} Å", f"{apix:.4g} Å"],
            ["Best resolution possible (Nyquist)", f"{2 * apix_old:.2f} Å", f"{2 * apix:.2f} Å"],
            ["Particles", f"{len(names):,}", f"{len(names):,}"]])
        ctx.add_highlight("Pixel", f"{apix:.3g} Å")
        ctx.add_output("particles", "particles", out, f"{len(names):,} particles, {box} px",
                       meta=particles_out_meta(ctx, out, "downsample"))


# ------------------------------------------------------------------ symmetry expansion
@register
class RelionSymmetryExpand(JobType):
    name = "relion_symmetry_expand"
    title = "Symmetry expansion (RELION)"
    category = "Utilities"
    tool = "relion"
    software = ["RELION"]
    description = ("Copies every particle once per symmetry-related pose (relion_particle_symmetry_expand), as "
                   "CryoSPARC's Symmetry Expansion: each copy of a symmetric complex becomes a particle, for focused "
                   "classification or refinement of one subunit in C1. Helical expansion along the axis too.")
    inputs = [Slot("particles", ("particles",), "Refined particles", prefer=("particles",),
                   help="Particles refined with the symmetry to expand.")]
    params = [
        Param("symmetry", "str", "", label="Symmetry", placeholder="e.g. D7",
              help="Point group of the refinement (empty: that of the particles)."),
        Param("helical", "bool", False, label="Helical expansion", help="Copies along the helical axis instead."),
        Param("twist", "float", 0.0, label="Helical twist", unit="°"),
        Param("rise", "float", 0.0, label="Helical rise", unit="Å"),
        Param("asu", "int", 1, label="Asymmetric units to expand", min=1, max=100),
        extra_args_param(),
    ]
    outputs = [OutputDef("particles", "particles", "Expanded particles")]

    def run(self, ctx: JobContext) -> None:
        p = ctx.params
        inp = ctx.require("particles")
        parts = relion_particles(ctx, inp, need_poses=True)
        sym = (str(p["symmetry"]).strip() or str(inp.meta.get("symmetry") or "C1")).upper()
        out = ctx.path("expanded.star")
        args = [ctx.program("relion", "relion_particle_symmetry_expand"), "--i", parts["ref"], "--o", ctx.rel(out)]
        if p["helical"]:
            if not p["rise"]:
                raise JobError("Helical expansion: give the rise (and the twist)")
            args += ["--helix", "--twist", f"{p['twist']:g}", "--rise", f"{p['rise']:g}", "--angpix", f"{parts['pixel_size']:g}",
                     "--asu", str(p["asu"])]
        else:
            if sym == "C1":
                raise JobError("C1 has nothing to expand: give the symmetry of the refinement")
            args += ["--sym", sym]
        args += ctx.split_extra()
        ctx.progress(0.2, "relion_particle_symmetry_expand")
        run_relion(ctx, args)
        if not out.exists():
            raise JobError("relion_particle_symmetry_expand wrote no expanded.star (see the log)")
        n = rl.star_rows(out)
        ctx.add_table("Expansion", ["", "Particles"], [["Before", f"{parts['n']:,}"], ["After", f"{n:,}"],
                                                       ["Copies per particle", f"{n / max(1, parts['n']):g}"]])
        ctx.add_highlight("Particles", f"{n:,}")
        ctx.add_output("particles", "particles", out, f"{n:,} expanded particles",
                       meta=particles_out_meta(ctx, out, "symexpand", symmetry="C1", expanded_from=sym))


# ------------------------------------------------------------------ optics groups
NAME_LABEL = {"movies": "rlnMicrographMovieName", "micrographs": "rlnMicrographName", "particles": "rlnMicrographName"}
EPU_SUFFIXES = ("_fractions", "_Fractions", "_EER", "_eer")


def epu_base(name: str) -> str:
    """The EPU name of a movie or micrograph (that of its .xml): without folder, extensions and _fractions/_EER."""
    import re
    stem = re.sub(r"\.(mrcs?|tiff?|eer)(\.(bz2|zst|xz))?$", "", Path(name).name, flags=re.IGNORECASE)
    for suffix in EPU_SUFFIXES:
        if stem.endswith(suffix):
            return stem[: -len(suffix)]
    return stem


def epu_beam_shift(path: Path) -> tuple[float, float] | None:
    """The beam shift (image shift) of an EPU acquisition, from its XML (microscopeData/optics/BeamShift/_x, _y)."""
    import xml.etree.ElementTree as ET
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return None
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] == "BeamShift":
            vals = {c.tag.rsplit("}", 1)[-1]: c.text for c in el}
            try:
                return float(vals["_x"]), float(vals["_y"])
            except (KeyError, TypeError, ValueError):
                return None
    return None


@register
class RelionOpticsGroups(JobType):
    name = "relion_optics_groups"
    title = "Optics groups (beam shift, file names)"
    category = "Utilities"
    software = ["RELION"]
    description = ("Splits movies, micrographs or particles into optics groups, as CryoSPARC's Exposure Group "
                   "Utilities: by the beam (image) shift of EPU's acquisition files (aberration-free image shift, "
                   "clustered), by a part of the file names, or in groups of acquisition order. CTF refinement then "
                   "fits the beam tilt and higher-order aberrations of each group.")
    inputs = [Slot("data", ("movies", "micrographs", "particles"), "Movies, micrographs or particles")]
    params = [
        Param("method", "choice", "beam_shift", choices=["beam_shift", "filename", "order"], label="Groups from",
              labels={"beam_shift": "Beam shift of EPU's XML files (clustered)", "filename": "A part of the file names",
                      "order": "Acquisition order (equal groups)"}),
        Param("xml_folder", "path", "", label="EPU folder", path_kind="dir", show_if="method=beam_shift",
              help="Folder of EPU's XML files (searched with its sub-folders), e.g. the session's Images-Disc1."),
        Param("groups", "int", 9, label="Number of groups", min=1, max=500, show_if="method=beam_shift|order",
              help="Beam shift: the holes per stage position of the collection (9 for 3 × 3...)."),
        Param("pattern", "str", r"GridSquare_(\d+)", label="Pattern of the file names", show_if="method=filename",
              help="A regular expression with one group in brackets: names with the same group go together."),
    ]
    outputs = [OutputDef("particles", "particles", "Particles"), OutputDef("micrographs", "micrographs", "Micrographs"),
               OutputDef("movies", "movies", "Movies")]

    def run(self, ctx: JobContext) -> None:
        import re
        p = ctx.params
        inp = ctx.require("data")
        kind = inp.type
        if kind == "particles":
            src = relion_particles(ctx, inp)["star"]
        else:
            from cryoplug.jobs.relion_common import star_of
            src = star_of(ctx, inp)[0]
        tables = rl.read_star(src)
        main = _main(tables) if kind == "particles" else rl.micrograph_table(tables)
        label = NAME_LABEL[kind]
        if not main.has(label):
            raise JobError(f"The {kind} have no {label} column")
        names = main.column(label)
        n = len(names)
        method = p["method"]
        shifts = None
        if method == "beam_shift":
            folder = Path(str(p["xml_folder"]).strip() or ".").expanduser()
            if not str(p["xml_folder"]).strip() or not folder.is_dir():
                raise JobError("Give the folder of EPU's XML files (EPU folder)")
            index = {epu_base(x.stem): x for x in folder.rglob("*.xml")}
            shifts = np.full((n, 2), np.nan)
            bases = [epu_base(nm) for nm in names]
            cache: dict[str, tuple[float, float] | None] = {}
            for i, b in enumerate(bases):
                if b not in cache:
                    cache[b] = epu_beam_shift(index[b]) if b in index else None
                if cache[b] is not None:
                    shifts[i] = cache[b]
            found = np.isfinite(shifts[:, 0])
            if not found.any():
                raise JobError(f"No EPU XML file with a beam shift matches the {kind} in {folder} (e.g. "
                               f"{bases[0]}.xml)")
            if not found.all():
                ctx.warn(f"{int((~found).sum()):,} of {n:,} without XML file: they join the nearest group of the others "
                         "(no beam shift: group 1)")
            from scipy.cluster.vq import kmeans2
            k = max(1, min(int(p["groups"]), len(np.unique(shifts[found], axis=0))))
            pts = shifts[found]
            scale = pts.std(axis=0)
            scale[scale == 0] = 1.0
            centres, lab = kmeans2(pts / scale, k, seed=1, minit="++")
            order = np.lexsort((centres[:, 0], -centres[:, 1]))  # numbered row by row, top left first
            rank = np.empty(k, dtype=int)
            rank[order] = np.arange(k)
            group = np.zeros(n, dtype=int)
            group[found] = rank[lab]
            how = f"beam shift of EPU's XML files, {k} clusters"
        elif method == "filename":
            try:
                rx = re.compile(str(p["pattern"]))
            except re.error as exc:
                raise JobError(f"Pattern: {exc}") from None
            if rx.groups < 1:
                raise JobError("The pattern needs a group in brackets, e.g. GridSquare_(\\d+)")
            keys = []
            for nm in names:
                m = rx.search(nm)
                keys.append(m.group(1) if m else "")
            if not any(keys):
                raise JobError(f"The pattern matches none of the names (e.g. {names[0]})")
            uniq = sorted(set(keys), key=lambda s: (len(s), s))
            lookup = {key: i for i, key in enumerate(uniq)}
            group = np.array([lookup[key] for key in keys])
            how = f"file names ({p['pattern']}), {len(uniq)} values"
        else:
            k = max(1, min(int(p["groups"]), n))
            group = np.minimum(k - 1, (np.arange(n) * k) // max(1, n))
            how = f"acquisition order, {k} groups"
        # new groups: one per (former optics group, new group) present
        old = np.array([int(float(v)) for v in main.column("rlnOpticsGroup")]) if main.has("rlnOpticsGroup") else np.ones(n, int)
        optics = tables.get("optics")
        if optics is None or not optics.rows:
            raise JobError("The file has no optics table (RELION 3.1 or later)")
        old_rows = {int(float(r[optics.labels.index("rlnOpticsGroup")])): r for r in optics.rows}
        combos = sorted(set(zip(old.tolist(), group.tolist())))
        number = {c: i + 1 for i, c in enumerate(combos)}
        name_col = optics.labels.index("rlnOpticsGroupName") if "rlnOpticsGroupName" in optics.labels else None
        group_col = optics.labels.index("rlnOpticsGroup")
        new_rows = []
        for (g, c), num in number.items():
            row = list(old_rows.get(g) or optics.rows[0])
            row[group_col] = str(num)
            if name_col is not None:
                row[name_col] = f"opticsGroup{num}"
            new_rows.append(row)
        optics.rows = new_rows
        main.set_column("rlnOpticsGroup", [str(number[(g, c)]) for g, c in zip(old.tolist(), group.tolist())])
        out = rl.write_star(ctx.path(f"{kind}.star"), list(tables.values()), comment=f"Optics groups from the {how}")
        counts = np.bincount([number[(g, c)] for g, c in zip(old.tolist(), group.tolist())])[1:]
        ctx.add_table("Optics groups", ["Group", kind.capitalize()], [[f"opticsGroup{i + 1}", f"{int(c):,}"] for i, c in enumerate(counts)])
        ctx.add_highlight("Optics groups", str(len(combos)))
        if shifts is not None:
            series = []
            for i in range(len(combos)):
                sel = np.flatnonzero((np.array([number[(g, c)] for g, c in zip(old.tolist(), group.tolist())]) == i + 1)
                                     & np.isfinite(shifts[:, 0]))
                if len(sel):
                    series.append({"name": f"opticsGroup{i + 1}", "x": [round(float(v), 6) for v in shifts[sel, 0]],
                                   "y": [round(float(v), 6) for v in shifts[sel, 1]]})
            ctx.add_plot("Beam shifts", series, x_label="Beam shift X", y_label="Beam shift Y", style="points",
                         note="Each colour is an optics group; one cloud per hole of the image-shift pattern.")
        if kind == "particles":
            ctx.add_output("particles", "particles", out, f"{n:,} particles, {len(combos)} optics groups",
                           meta=particles_out_meta(ctx, out, "optics", optics_groups=len(combos)))
        elif kind == "micrographs":
            from cryoplug.jobs.relion_common import micrograph_meta
            passed = {k: v for k, v in inp.meta.items() if k in ("has_motion", "power_spectra", "motion_star")}
            ctx.add_output("micrographs", "micrographs", out, f"{n:,} micrographs, {len(combos)} optics groups",
                           meta=micrograph_meta(ctx, out, "optics", **passed))
        else:
            meta = {**inp.meta, "optics_groups": len(combos), "relion": {"star": ctx.rel(out), "kind": "optics"}}
            ctx.add_output("movies", "movies", out, f"{n:,} movies, {len(combos)} optics groups", meta=meta)


# ------------------------------------------------------------------ particle curation by statistics
# Statistics of each particle: key -> (title, unit, RELION label, or "" when computed from several columns)
PARTICLE_METRICS: dict[str, tuple[str, str, str]] = {
    "fom": ("Pick figure of merit", "", "rlnAutopickFigureOfMerit"),
    "max_prob": ("Probability of the best pose", "", "rlnMaxValueProbDistribution"),
    "n_significant": ("Significant samples", "", "rlnNrOfSignificantSamples"),
    "loglike": ("Log-likelihood contribution", "", "rlnLogLikeliContribution"),
    "norm": ("Normalisation correction", "", "rlnNormCorrection"),
    "defocus": ("Defocus", "Å", ""),
    "astigmatism": ("Astigmatism", "Å", ""),
    "ctf_res": ("CTF fit resolution", "Å", "rlnCtfMaxResolution"),
    "ctf_fom": ("CTF figure of merit", "", "rlnCtfFigureOfMerit"),
    "bfactor": ("CTF B-factor", "Å²", "rlnCtfBfactor"),
    "scale": ("CTF scale factor", "", "rlnCtfScalefactor"),
    "shift": ("Shift of the alignment", "Å", ""),
    "per_micrograph": ("Particles on its micrograph", "", ""),
}
# columns that are not statistics of a particle (positions, names, groups, angles...)
NOT_STATISTICS = {
    "rlnCoordinateX", "rlnCoordinateY", "rlnCoordinateZ", "rlnImageName", "rlnMicrographName", "rlnOpticsGroup",
    "rlnClassNumber", "rlnRandomSubset", "rlnGroupNumber", "rlnGroupName", "rlnAngleRot", "rlnAngleTilt", "rlnAnglePsi",
    "rlnAngleRotPrior", "rlnAngleTiltPrior", "rlnAnglePsiPrior", "rlnOriginXAngst", "rlnOriginYAngst", "rlnOriginZAngst",
    "rlnOriginX", "rlnOriginY", "rlnOriginZ", "rlnOriginXPriorAngst", "rlnOriginYPriorAngst", "rlnDefocusU",
    "rlnDefocusV", "rlnDefocusAngle", "rlnPhaseShift", "rlnHelicalTubeID", "rlnAnglePsiFlipRatio",
    "rlnImageOriginalName", "rlnMicrographOriginalName", "rlnCtfImage", "rlnTomoName", "rlnTomoParticleName",
    "rlnMicrographId", "rlnBodyMaskName", "rlnParticleName", "rlnOriginalParticleName", "rlnHelicalTrackLength",
    "rlnHelicalTrackLengthAngst", "rlnClassNumberPrior",
}


def particle_statistics(ptab: rl.StarTable) -> dict[str, tuple[str, str, np.ndarray]]:
    """The statistics of every particle that vary: {key: (title, unit, values)}; RELION columns not known here come
    after, under their label."""
    n = len(ptab)

    def floats(label: str) -> np.ndarray | None:
        if not ptab.has(label):
            return None
        try:
            return ptab.floats(label)
        except ValueError:
            return None
    out: dict[str, tuple[str, str, np.ndarray]] = {}
    used = set()
    for key, (title, unit, label) in PARTICLE_METRICS.items():
        v = None
        if label:
            v = floats(label)
            used.add(label)
        elif key in ("defocus", "astigmatism"):
            du, dv = floats("rlnDefocusU"), floats("rlnDefocusV")
            if du is not None and dv is not None:
                v = (du + dv) / 2 if key == "defocus" else np.abs(du - dv)
        elif key == "shift":
            ox, oy = floats("rlnOriginXAngst"), floats("rlnOriginYAngst")
            if ox is not None and oy is not None:
                v = np.hypot(ox, oy)
        elif key == "per_micrograph" and ptab.has("rlnMicrographName"):
            _, inverse, counts = np.unique(ptab.column("rlnMicrographName"), return_inverse=True, return_counts=True)
            v = counts[inverse].astype(np.float64)
        if v is not None:
            out[key] = (title, unit, v)
    for label in ptab.labels:
        if label in used or label in NOT_STATISTICS:
            continue
        v = floats(label)
        if v is not None:
            out[label] = (label[3:] if label.startswith("rln") else label, "", v)
    varying = {}
    for key, (title, unit, v) in out.items():
        finite = v[np.isfinite(v)]
        if len(finite) == n and n and float(finite.max() - finite.min()) > 1e-12 * max(1.0, float(np.abs(finite).max())):
            varying[key] = (title, unit, v)
    return varying


def statistic_key(name: str, stats: dict[str, tuple[str, str, np.ndarray]]) -> str:
    """The key of a statistic named by its key or its RELION label (with or without rln, any case)."""
    lower = name.strip().lstrip("_").lower()
    for key, (_, _, label) in PARTICLE_METRICS.items():
        names = {key} | ({label.lower(), label.lower()[3:]} if label else set())
        if lower in names and key in stats:
            return key
    for key in stats:
        if lower in (key.lower(), key.lower()[3:] if key.startswith("rln") else key.lower()):
            return key
    known = ", ".join(stats) or "none"
    raise JobError(f"No statistic '{name}' in these particles (available: {known})")


def parse_limits(text: str, stats: dict[str, tuple[str, str, np.ndarray]]) -> dict[str, list[float | None]]:
    """Limits written one per line (or separated by ;): statistic, lowest, highest; - for no limit."""
    import re
    limits: dict[str, list[float | None]] = {}
    for raw in re.split(r"[;\n]", text or ""):
        line = raw.split("#")[0].strip()
        if not line:
            continue
        parts = line.replace(",", " ").split()
        if len(parts) != 3:
            raise JobError(f"Limits: '{line}' is not 'statistic lowest highest' (- for no limit)")
        key = statistic_key(parts[0], stats)
        try:
            lo, hi = (None if v in ("-", "none", "_") else float(v) for v in parts[1:])
        except ValueError:
            raise JobError(f"Limits: '{line}': the limits must be numbers or -") from None
        if lo is not None and hi is not None and lo > hi:
            raise JobError(f"Limits: '{line}': the lowest is above the highest")
        if lo is not None or hi is not None:
            limits[key] = [lo, hi]
    return limits


def statistics_mask(stats: dict[str, tuple[str, str, np.ndarray]], limits: dict[str, list[float | None]], n: int
                    ) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """The particles within every [lowest, highest] range, and those out of each one."""
    keep = np.ones(n, dtype=bool)
    out = {}
    for key, (lo, hi) in limits.items():
        v = stats[key][2]
        bad = np.zeros(n, dtype=bool)
        with np.errstate(invalid="ignore"):
            if lo is not None:
                bad |= v < lo
            if hi is not None:
                bad |= v > hi
        out[key] = bad
        keep &= ~bad
    return keep, out


def statistics_data(ctx: JobContext, stats: dict[str, tuple[str, str, np.ndarray]], max_display: int,
                    source: dict[str, str] | None) -> Path:
    """The statistics for the browser: ``particle_stats.bin`` (each statistic in turn, its values coded on 16 bits
    from its range: 0 to 65534, 65535 unknown) and ``particle_stats.json`` (statistics, ranges; with a random sample of
    the particles when there are more than ``max_display``, their rows in ``particle_stats_rows.bin``)."""
    n = len(next(iter(stats.values()))[2])
    rows = None
    if max_display and n > max_display:
        rows = np.sort(np.random.default_rng(0).choice(n, int(max_display), replace=False)).astype("<u4")
        ctx.path("particle_stats_rows.bin").write_bytes(rows.tobytes())
    codes, metrics = [], []
    for key, (title, unit, v) in stats.items():
        finite = v[np.isfinite(v)]
        lo, hi = float(finite.min()), float(finite.max())
        p_lo, p_hi = (float(x) for x in np.percentile(finite, [0.1, 99.9]))
        clipped = False  # a few extreme values would squash the histogram: shown at its ends
        if (p_hi - p_lo) > 0 and (hi - p_hi > p_hi - p_lo or p_lo - lo > p_hi - p_lo):
            lo, hi, clipped = p_lo, p_hi, True
        span = (hi - lo) or 1.0
        shown = v if rows is None else v[rows]
        code = np.full(len(shown), 65535, dtype="<u2")
        ok = np.isfinite(shown)
        code[ok] = np.clip(np.round((shown[ok] - lo) / span * 65534), 0, 65534).astype("<u2")
        codes.append(code)
        metrics.append({"key": key, "title": title, "unit": unit, "lo": lo, "hi": hi, "clipped": clipped,
                        "median": round(float(np.median(finite)), 6)})
    binary = ctx.path("particle_stats.bin")
    binary.write_bytes(np.concatenate(codes).tobytes())
    info = {"n": n, "n_display": n if rows is None else len(rows), "metrics": metrics, "binary": ctx.rel(binary),
            "rows": ctx.rel(ctx.path("particle_stats_rows.bin")) if rows is not None else None, "source": source}
    index = ctx.path("particle_stats.json")
    index.write_text(json.dumps(info, separators=(",", ":")))
    return index


def gaussian_mixture_1d(x: np.ndarray, k: int, iterations: int = 300, seed: int = 0
                        ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Weights, means and standard deviations of a mixture of ``k`` Gaussians fitted to ``x`` (EM), by mean."""
    x = np.asarray(x, dtype=np.float64)
    if len(x) > 200_000:  # the fit on a sample, the assignment on all
        x = np.random.default_rng(seed).choice(x, 200_000, replace=False)
    scale = float(x.std()) or 1.0
    mu = np.quantile(x, (np.arange(k) + 0.5) / k)
    sd = np.full(k, scale / k)
    w = np.full(k, 1.0 / k)
    floor = 1e-3 * scale
    for _ in range(iterations):
        logp = -0.5 * ((x[:, None] - mu) / sd) ** 2 - np.log(sd) + np.log(np.maximum(w, 1e-12))
        logp -= logp.max(axis=1, keepdims=True)
        r = np.exp(logp)
        r /= r.sum(axis=1, keepdims=True)
        nk = r.sum(axis=0) + 1e-12
        new_mu = (r * x[:, None]).sum(axis=0) / nk
        sd = np.maximum(np.sqrt((r * (x[:, None] - new_mu) ** 2).sum(axis=0) / nk), floor)
        w = nk / len(x)
        done = np.abs(new_mu - mu).max() < 1e-6 * scale
        mu = new_mu
        if done:
            break
    order = np.argsort(mu)
    return w[order], mu[order], sd[order]


def mixture_assign(v: np.ndarray, w: np.ndarray, mu: np.ndarray, sd: np.ndarray) -> np.ndarray:
    """The most likely component of each value (-1 for an unknown value)."""
    out = np.full(len(v), -1, dtype=int)
    ok = np.isfinite(v)
    logp = -0.5 * ((v[ok, None] - mu) / sd) ** 2 - np.log(sd) + np.log(np.maximum(w, 1e-12))
    out[ok] = np.argmax(logp, axis=1)
    return out


@register
class RelionParticleCurate(JobType):
    name = "relion_particle_curate"
    title = "Curate particles (statistics)"
    category = "Utilities"
    interactive = True
    software = ["RELION"]
    description = ("Keeps the particles whose statistics are within limits, as CryoSPARC's Subset Particles by "
                   "Statistic: probability of the best pose, significant samples, log-likelihood, normalisation, pick "
                   "figure of merit, defocus, CTF fit, alignment shift, particles on the micrograph and any other RELION "
                   "column. Interactive, the job waits with the distribution of each statistic (limits dragged on the "
                   "histograms, each showing the particles the other limits keep), a density plot of two of them and "
                   "images of particles kept and left out. Or applies the limits written below at once, or splits the "
                   "particles into clusters of one statistic (Gaussian mixture).")
    inputs = [Slot("particles", ("particles",), "Particles", help="Classified or refined particles have the most statistics.")]
    params = [
        Param("mode", "choice", "interactive", choices=["interactive", "thresholds", "cluster"], label="Curation",
              labels={"interactive": "On the distributions (the job waits for you)", "thresholds": "With the limits below, at once",
                      "cluster": "Clusters of one statistic (Gaussian mixture)"},
              help="Interactive: the limits below are where the limits start."),
        Param("limits", "text", "", label="Limits", placeholder="max_prob 0.1 -\nnorm 0.6 1.4\nrlnCtfMaxResolution - 6",
              show_if="mode=interactive|thresholds",
              help="One per line: the statistic, its lowest and highest value (- for no limit). Statistics: fom, max_prob, "
                   "n_significant, loglike, norm, defocus, astigmatism, ctf_res, ctf_fom, bfactor, scale, shift, "
                   "per_micrograph, or a RELION label (rlnXxx)."),
        Param("statistic", "str", "max_prob", label="Statistic", show_if="mode=cluster",
              help="The statistic whose clusters split the particles (a key above or a RELION label)."),
        Param("clusters", "int", 2, label="Clusters", min=2, max=6, show_if="mode=cluster"),
        Param("max_display", "int", 1_000_000, label="Particles shown at most", min=10_000, advanced=True,
              help="Interactive: above this, the plots show a random sample (the limits apply to all)."),
    ]
    outputs = [OutputDef("particles", "particles", "Kept particles"), OutputDef("rejected", "particles", "Rejected particles"),
               OutputDef("cluster_1", "particles", "Cluster 1"), OutputDef("cluster_2", "particles", "Cluster 2")]

    @classmethod
    def validate(cls, params: dict[str, Any], connected: dict[str, str]) -> list[str]:
        if params.get("mode") == "thresholds" and not str(params.get("limits") or "").strip():
            return ["Write at least one limit (e.g. max_prob 0.1 -)"]
        if params.get("mode") == "cluster" and not str(params.get("statistic") or "").strip():
            return ["Choose the statistic to cluster"]
        return []

    def load(self, ctx: JobContext) -> tuple[Any, dict[str, Any], dict[str, tuple[str, str, np.ndarray]]]:
        inp = ctx.require("particles")
        parts = relion_particles(ctx, inp)
        stats = particle_statistics(_main(parts["tables"]))
        if not stats:
            raise JobError("These particles have no statistic that varies (classify or refine them first)")
        return inp, parts, stats

    def prepare(self, ctx: JobContext) -> bool:
        p = ctx.params
        inp, parts, stats = self.load(ctx)
        ctx.log(f"{parts['n']:,} particles; statistics: {', '.join(stats)}")
        if p["mode"] == "cluster":
            self.clusters(ctx, inp, parts, stats)
            return True
        limits = parse_limits(p["limits"], stats)
        if p["mode"] == "thresholds":
            if not limits:
                raise JobError("Write at least one limit (e.g. max_prob 0.1 -)")
            self.apply(ctx, inp, parts, stats, limits, how="thresholds")
            return True
        job, _, output = inp.source.partition(".")  # the particle images come from the input's own output
        source = {"job": job, "output": output} if output else None
        data = statistics_data(ctx, stats, int(p["max_display"]), source)
        ctx.add_tool("particle_stats", "Curate particles", data, waiting=True, editable=True, limits=limits,
                     prompt_title="Waiting for your curation",
                     prompt="Drag the limits on the histograms (each shows the particles the other limits keep), look at "
                            "the particles kept and left out, then Done.")
        ctx.add_highlight("Particles", f"{parts['n']:,}")
        ctx.log("Waiting: set the limits in the job panel, then Done")
        return False

    def finalize(self, ctx: JobContext, choice: str | None) -> None:
        inp, parts, stats = self.load(ctx)
        try:
            sent = json.loads(choice) if (choice or "").strip() else {}
        except ValueError as exc:
            raise JobError(f"Curation not understood: {exc}") from None
        if not sent:  # Done without data: the work saved while the job waited, or the limits of the parameters
            saved = ctx.path("session_pcurate.json")
            sent = json.loads(saved.read_text()) if saved.exists() else {"limits": parse_limits(ctx.params["limits"], stats)}
        limits: dict[str, list[float | None]] = {}
        for key, bounds in (sent.get("limits") or {}).items():
            if key not in stats or not isinstance(bounds, (list, tuple)) or len(bounds) != 2:
                continue
            lo, hi = (None if b is None else float(b) for b in bounds)
            if lo is not None or hi is not None:
                limits[key] = [lo, hi]
        keep = self.apply(ctx, inp, parts, stats, limits, how="interactive")
        for sec in ctx.report.get("sections", []):  # done: the browser shows the limits applied
            if sec.get("kind") == "particle_stats" and sec.get("waiting"):
                sec.update(waiting=False, editable=False, limits=limits, kept=int(keep.sum()), title="Particle statistics")
        ctx.save_report()

    def apply(self, ctx: JobContext, inp: Any, parts: dict[str, Any], stats: dict[str, tuple[str, str, np.ndarray]],
              limits: dict[str, list[float | None]], how: str) -> np.ndarray:
        n = parts["n"]
        keep, out = statistics_mask(stats, limits, n)
        kept = int(keep.sum())
        if not kept:  # before any section: a waiting curation stays as it was
            raise JobError("No particle kept: widen the limits")

        def bound(key: str, v: float) -> str:
            unit = stats[key][1]
            return f"{v:.4g}" + (f" {unit}" if unit else "")
        rows = []
        for key, (lo, hi) in limits.items():
            rng = (f"{bound(key, lo)} to {bound(key, hi)}" if lo is not None and hi is not None
                   else f"≥ {bound(key, lo)}" if lo is not None else f"≤ {bound(key, hi)}")
            rows.append([f"{stats[key][0]}: {rng}", f"{int(out[key].sum()):,}"])
        ctx.add_table("Rejected by", ["Limit", "Particles out"], rows + [["Kept", f"{kept:,} of {n:,} ({100 * kept / n:.1f} %)"]])
        for key, (title, unit, v) in stats.items():
            if key not in limits and key not in ("max_prob", "loglike", "norm", "fom", "ctf_res"):
                continue
            lo, hi = limits.get(key, [None, None])
            lines = [{"x": b, "label": f"{s} {b:.4g}"} for b, s in ((lo, "≥"), (hi, "≤")) if b is not None]
            a, z = (float(x) for x in np.percentile(v, [0.1, 99.9]))
            ctx.add_plot(title, [{"name": "All", **histogram(v, bins=40, lo=a, hi=z)},
                                 {"name": "Kept", **histogram(v[keep], bins=40, lo=a, hi=z)}],
                         x_label=f"{title} ({unit})" if unit else title, y_label="Particles", style="bars", vlines=lines)
        ctx.add_highlight("Kept", f"{kept:,} / {n:,}")
        tables = parts["tables"]
        desc = "; ".join(r[0] for r in rows) or "no limit"
        outp = _write_subset(ctx, tables, keep, "particles.star", f"Curated from {inp.source} ({how}): {desc}")
        ctx.add_output("particles", "particles", outp, f"{kept:,} particles", meta=particles_out_meta(ctx, outp, "curate"))
        if kept < n:
            rej = _write_subset(ctx, tables, ~keep, "particles_rejected.star", f"Rejected from {inp.source} ({how}): {desc}")
            ctx.add_output("rejected", "particles", rej, f"{n - kept:,} rejected particles", meta=particles_out_meta(ctx, rej, "curate"))
        return keep

    def clusters(self, ctx: JobContext, inp: Any, parts: dict[str, Any], stats: dict[str, tuple[str, str, np.ndarray]]) -> None:
        p = ctx.params
        key = statistic_key(str(p["statistic"]), stats)
        title, unit, v = stats[key]
        k = int(p["clusters"])
        finite = v[np.isfinite(v)]
        if len(np.unique(finite)) < k:
            raise JobError(f"{title}: fewer distinct values than clusters")
        ctx.progress(0.2, f"Gaussian mixture of {k} clusters on {title}")
        w, mu, sd = gaussian_mixture_1d(finite, k)
        label = mixture_assign(v, w, mu, sd)
        a, z = (float(x) for x in np.percentile(finite, [0.1, 99.9]))
        hist = histogram(finite, bins=50, lo=a, hi=z)
        width = (z - a) / 50 if z > a else 1.0
        series = [{"name": "Particles", **hist}]
        xs = np.linspace(a, z, 200)
        for c in range(k):  # each Gaussian scaled to particles per bin
            ys = len(finite) * width * w[c] * np.exp(-0.5 * ((xs - mu[c]) / sd[c]) ** 2) / (sd[c] * np.sqrt(2 * np.pi))
            series.append({"name": f"Cluster {c + 1}", "x": [round(float(x), 6) for x in xs], "y": [round(float(y), 3) for y in ys]})
        ctx.add_plot(f"{title}: clusters", series, x_label=f"{title} ({unit})" if unit else title, y_label="Particles")
        rows = []
        tables = parts["tables"]
        for c in range(k):
            sel = label == c
            nc = int(sel.sum())
            rows.append([f"Cluster {c + 1}", f"{mu[c]:.4g} ± {sd[c]:.3g}", f"{100 * w[c]:.1f} %", f"{nc:,}"])
            if not nc:
                continue
            out = _write_subset(ctx, tables, sel, f"particles_cluster{c + 1}.star",
                                f"Cluster {c + 1} of {k} of {title} (mean {mu[c]:.4g}) from {inp.source}")
            ctx.add_output(f"cluster_{c + 1}", "particles", out, f"Cluster {c + 1}: {nc:,} particles ({title} {mu[c]:.3g})",
                           meta=particles_out_meta(ctx, out, "curate", cluster=c + 1))
        unknown = int((label < 0).sum())
        if unknown:
            rows.append(["Unknown value (in no cluster)", "", "", f"{unknown:,}"])
        ctx.add_table(f"Clusters of {title}", ["Cluster", "Mean ± s.d.", "Weight", "Particles"], rows)
        ctx.add_highlight("Clusters", str(k))
