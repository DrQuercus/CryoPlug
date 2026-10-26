"""Particle utilities around RELION's particle files, as CryoSPARC's: particle sets compared (intersection and
differences, by image or by position on the micrograph), orientations rebalanced, particles down-sampled (Fourier
cropping), symmetry expansion (relion_particle_symmetry_expand).

The particle files written are RELION's (optics and particles tables), read by every RELION job of CryoPlug.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from cryoplug import relion as rl
from cryoplug.jobs import register
from cryoplug.jobs.base import JobContext, JobError, JobType, OutputDef, Param, Slot, extra_args_param
from cryoplug.jobs.relion_common import (
    particles_out_meta, relion_particles, run_relion, viewing_directions,
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
                   "position on the micrograph (particles picked or extracted twice). Join star files gives the union.")
    inputs = [Slot("set_a", ("particles",), "Set A"), Slot("set_b", ("particles",), "Set B")]
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
