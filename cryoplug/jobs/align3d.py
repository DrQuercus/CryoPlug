"""Align 3D maps (CryoSPARC's "Align 3D Maps"): a map brought into the frame of a reference map, with its half maps,
mask and particle poses, so that particles refined against different references can be compared or combined.

The rotation and shift are found on the maps (:func:`cryoplug.poses.align_maps`, or a local refinement), then applied
to the volumes (:func:`cryoplug.poses.frame_volume`) and to the poses of the particles (:func:`cryoplug.poses.apply_frame`,
RELION Euler angles and origins converted to cryoDRGN's convention and back).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from cryoplug import poses as ps
from cryoplug import relion as rl
from cryoplug.jobs import register
from cryoplug.jobs.base import JobContext, JobError, JobType, OutputDef, Param, Slot
from cryoplug.jobs.maptools import onto_grid
from cryoplug.mrc import MapVolume


def on_reference_grid(data: np.ndarray, apix: float, reference: MapVolume) -> MapVolume:
    """A volume aligned on the reference (box centres matched, index D/2) written on the grid of the reference."""
    if data.shape == reference.data.shape and abs(apix - reference.pixel_size) < 1e-4:
        return reference.like(data)
    d_m, d_r = data.shape[0], reference.data.shape[0]
    origin = np.asarray(reference.origin, dtype=float) + (d_r // 2) * np.asarray(reference.voxel, dtype=float) - (d_m // 2) * apix
    return onto_grid(MapVolume(data=np.asarray(data, dtype=np.float32), voxel=np.full(3, apix), origin=origin), reference)


def realigned_poses(star: Path, frame: dict[str, Any], map_box: int, map_apix: float, out: Path) -> tuple[Path, int]:
    """A copy of a RELION particle file with the poses expressed for the aligned map. The shift of the frame (a
    fraction of the map box) is converted to the box and pixel of the particles."""
    from cryoplug import particles as pt
    tables = rl.read_star(star)
    ptab = rl.particle_table(tables)
    if not (ptab.has("rlnAngleRot") and ptab.has("rlnAngleTilt") and ptab.has("rlnAnglePsi")):
        raise JobError("The particles have no orientations (rlnAngleRot/Tilt/Psi): refine them first")
    box, apix = rl.grid_from_star(tables)
    if not box or not apix:
        raise JobError("The box or pixel size of the particles is unknown (no optics table)")
    euler = np.stack([ptab.floats("rlnAngleRot"), ptab.floats("rlnAngleTilt"), ptab.floats("rlnAnglePsi")], 1)
    if ptab.has("rlnOriginXAngst"):
        origin = np.stack([ptab.floats("rlnOriginXAngst"), ptab.floats("rlnOriginYAngst")], 1) / apix
    elif ptab.has("rlnOriginX"):
        origin = np.stack([ptab.floats("rlnOriginX"), ptab.floats("rlnOriginY")], 1)
    else:
        origin = np.zeros((len(ptab), 2))
    scaled = dict(frame)
    scaled["shift"] = (np.asarray(frame["shift"], dtype=float) * map_box * map_apix / (apix * box)).tolist()
    rot, trans = ps.apply_frame(ps.relion_to_matrix(euler), origin / box, scaled)
    pt.star_with_poses(star, out, ps.matrix_to_relion(rot), trans * box, apix=apix)
    return out, len(ptab)


def slices_image(ctx: JobContext, reference: MapVolume, before: MapVolume, after: MapVolume) -> Path | None:
    """Central slices (z, y, x) of the reference, of the map before and after the alignment, in rows."""
    try:
        from cryoplug.imaging import write_png
        from cryoplug.micrographs import tile_sheet

        def norm(img: np.ndarray) -> np.ndarray:
            lo, hi = np.percentile(img, [1, 99.5])
            return (np.clip((img - lo) / ((hi - lo) or 1.0), 0, 1) * 255).astype(np.uint8)

        tiles = []
        for vol in (reference, before, after):
            d = vol.data
            c = [s // 2 for s in d.shape]
            tiles += [norm(d[c[0]]), norm(d[:, c[1]]), norm(d[:, :, c[2]])]
        size = max(t.shape[0] for t in tiles)
        tiles = [np.pad(t, ((0, size - t.shape[0]), (0, size - t.shape[1]))) for t in tiles]
        return write_png(ctx.path("alignment_slices.png"), tile_sheet(tiles, per_row=3))
    except Exception as exc:  # cosmetic
        ctx.warn(f"No picture of the slices: {exc}")
        return None


@register
class Align3DMaps(JobType):
    name = "align_3d_maps"
    title = "Align 3D maps"
    category = "Map processing"
    software = ["CryoPlug"]
    description = ("A map brought into the frame of a reference map, as CryoSPARC's Align 3D maps: the rotation and shift "
                   "are found by a global search over all orientations (or refined locally for maps already close), then "
                   "applied to the map, its half maps and mask, and to the poses of its particles. Particles refined "
                   "against different references (two data sets, two classes, an ab initio model) can then be compared, "
                   "joined or refined together.")
    inputs = [
        Slot("map", ("map",), "Map to align", help="Its half maps, mask and particles (optional) follow it.", prefer=("map",)),
        Slot("reference", ("map",), "Reference map", help="The frame to bring the map into (box and pixel size of the outputs)."),
        Slot("half_maps", ("half_maps",), "Half maps of the map", required=False),
        Slot("mask", ("mask",), "Mask of the map", required=False),
        Slot("particles", ("particles",), "Particles of the map", required=False,
             help="Particles whose poses refer to the map (those of its refinement): their poses are changed to refer to "
                  "the reference."),
    ]
    params = [
        Param("search", "choice", "global", choices=["global", "local"], label="Search",
              labels={"global": "Global (any orientation)", "local": "Local (maps already close)"},
              help="Global: evenly spread orientations, the best ones refined. Local: small rotations and shifts from the "
                   "maps as they are."),
        Param("try_mirror", "bool", False, label="Also try the mirror image", show_if="search=global",
              help="For a map of unknown hand (ab initio): the hand that fits better is kept, and the poses of the "
                   "particles are mirrored with it."),
        Param("search_box", "int", 32, label="Search box", unit="px", min=16, max=96, advanced=True,
              help="Size of the low-passed copies compared (the coarse search); 32 is about 1/32 of the box per voxel."),
        Param("rotations", "int", 4000, label="Orientations tried", min=200, max=50000, advanced=True, show_if="search=global",
              help="4,000: about 15° apart; the best are then refined."),
    ]
    outputs = [OutputDef("map", "map", "Aligned map"), OutputDef("half_maps", "half_maps", "Aligned half maps"),
               OutputDef("mask", "mask", "Aligned mask"), OutputDef("particles", "particles", "Particles with aligned poses")]

    def run(self, ctx: JobContext) -> None:
        p = ctx.params
        mov_in, ref_in = ctx.require("map"), ctx.require("reference")
        moving, reference = MapVolume.read(mov_in.path), MapVolume.read(ref_in.path)
        for vol, what in ((moving, "map"), (reference, "reference")):
            if len(set(vol.data.shape)) != 1:
                raise JobError(f"The {what} is not in a cubic box {vol.shape_xyz}: crop or pad it first (Map operations)")
        apix_m = moving.pixel_size
        ctx.progress(0.05, "Searching the orientation" if p["search"] == "global" else "Refining the alignment")
        if p["search"] == "global":
            result = ps.align_maps(moving.data, apix_m, reference.data, reference.pixel_size, size=int(p["search_box"]),
                                   n_rotations=int(p["rotations"]), hands=(False, True) if p["try_mirror"] else (False,))
        else:
            result = ps.refine_alignment(moving.data, apix_m, reference.data, reference.pixel_size, size=int(p["search_box"]))
        frame = ps.frame_dict(result["rotation"], result["mirror"], result["shift"], score=round(float(result["score"]), 4))
        angle = float(ps.rotation_angle(result["rotation"]))
        shift_a = float(np.linalg.norm(result["shift"]) * moving.data.shape[0] * apix_m)
        axis = ps.matrix_to_rotvec(result["rotation"])[0]
        axis = axis / (np.linalg.norm(axis) or 1.0)
        ctx.progress(0.6, "Moving the maps")
        aligned = on_reference_grid(ps.frame_volume(moving.data, frame), apix_m, reference)
        corr = float(np.corrcoef(aligned.data.ravel(), reference.data.ravel())[0, 1])
        meta = {**ctx.inherited_meta("map"), "aligned_to": ref_in.source, "frame": frame}
        for key in ("symmetry",):
            if ref_in.meta.get(key) and key not in meta:
                meta[key] = ref_in.meta[key]
        out = aligned.write(ctx.path("aligned_map.mrc"))
        ctx.add_output("map", "map", out, f"{mov_in.source} on {ref_in.source}", meta=meta)
        halves = ctx.input("half_maps")
        if halves is not None:
            files = []
            for i, path in enumerate(halves.files[:2], 1):
                half = MapVolume.read(path)
                files.append(on_reference_grid(ps.frame_volume(half.data, frame), half.pixel_size, reference)
                             .write(ctx.path(f"aligned_half_map_{i}.mrc")))
            ctx.add_output("half_maps", "half_maps", files, "Aligned half maps", meta={**ctx.inherited_meta("half_maps"), **meta})
        mask_in = ctx.input("mask")
        if mask_in is not None:
            mask = MapVolume.read(mask_in.path)
            moved = np.clip(ps.frame_volume(mask.data, frame), 0, 1)
            placed = on_reference_grid(moved, mask.pixel_size, reference)
            placed.data = np.clip(placed.data, 0, 1)
            ctx.add_output("mask", "mask", placed.write(ctx.path("aligned_mask.mrc")), "Aligned mask",
                           meta={**ctx.inherited_meta("mask"), "frame": frame})
        parts_in = ctx.input("particles")
        n_parts = 0
        if parts_in is not None:
            ctx.progress(0.8, "Changing the poses of the particles")
            from cryoplug.jobs.relion_common import particles_out_meta, relion_particles
            parts = relion_particles(ctx, parts_in, need_poses=True)
            star, n_parts = realigned_poses(Path(parts["star"]), frame, moving.data.shape[0], apix_m, ctx.path("particles_aligned.star"))
            if rl.particle_table(parts["tables"]).has("rlnAnglePsiPrior"):
                ctx.warn("The particles have angle priors (helical processing): they still refer to the old frame")
            ctx.add_output("particles", "particles", star, f"Particles with aligned poses ({n_parts:,})",
                           meta=particles_out_meta(ctx, star, "aligned", frame=frame,
                                                   **({"symmetry": meta["symmetry"]} if meta.get("symmetry") else {})))
        hand = {"clear": "certain", "weak": "probable", "none": "uncertain", "not tested": "not tested"}[result["hand_confidence"]]
        rows = [["Search", "global" if p["search"] == "global" else "local"],
                ["Rotation", f"{angle:.1f}° about ({axis[0]:.2f}, {axis[1]:.2f}, {axis[2]:.2f})"],
                ["Shift", f"{shift_a:.1f} Å"],
                ["Correlation after alignment", f"{corr:.3f}"],
                ["Mirror image", ("yes" if result["mirror"] else "no") + (f" (hand {hand})" if p["try_mirror"] and p["search"] == "global" else "")]]
        if result.get("other_hand_score") is not None:
            rows.append(["Score of the other hand", f"{result['other_hand_score']:.3f} (this one {result['score']:.3f})"])
        if n_parts:
            rows.append(["Particles", f"{n_parts:,} poses changed"])
        ctx.add_table("Alignment", ["", "Value"], rows)
        png = slices_image(ctx, reference, on_reference_grid(moving.data, apix_m, reference), aligned)
        if png:
            ctx.add_image("Central slices: reference, map before, map after (rows; z, y, x)", png)
        ctx.add_highlight("Rotation", f"{angle:.1f}°")
        ctx.add_highlight("Shift", f"{shift_a:.1f} Å")
        ctx.add_highlight("Correlation", f"{corr:.3f}", "good" if corr > 0.8 else ("warn" if corr > 0.5 else "bad"))
        if result["mirror"]:
            ctx.add_highlight("Mirror", "yes", "warn")
        ctx.save_report()
