"""Mask creation (built-in): soft-edged masks for FSC, local refinement, LocScale, spIsoNet, signal subtraction."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np

from cryoplug.jobs import register
from cryoplug.jobs.base import JobContext, JobError, JobType, OutputDef, Param, Slot
from cryoplug.mrc import MapVolume

SOURCE_LABELS = {
    "map_density": "map density",
    "model_atoms": "atoms of the model",
    "model_map": "simulated map of the model (molmap)",
    "density_near_model": "map density near the model",
}
MODEL_SOURCES = ("model_atoms", "model_map", "density_near_model")


@register
class CreateMask(JobType):
    name = "create_mask"
    title = "Create mask"
    category = "Map processing"
    software = ["CryoPlug"]
    cpus = 2
    description = ("Soft-edged mask from the map density (low-pass + threshold), from the atoms of chosen chains, from the "
                   "density simulated from them as ChimeraX's molmap, or from the map density near them, with dilation "
                   "and soft edge in Å as in CryoSPARC. For FSC, local refinement, LocScale, spIsoNet or signal "
                   "subtraction; can be combined with another mask.")
    inputs = [
        Slot("map", ("map",), "Map", help="Sets the box and pixel size of the mask; thresholded for density-based masks."),
        Slot("model", ("model",), "Model", required=False,
             help="For a mask around chains or from their simulated density, or to keep only the map density near them "
                  "(must be fitted in the map)."),
        Slot("mask", ("mask",), "Mask to combine", required=False, help="Used by 'Combine with mask'."),
    ]
    params = [
        Param("source", "choice", "map_density", choices=list(SOURCE_LABELS), label="Mask from",
              help="map density: low-pass + threshold · model atoms: everything near the selected atoms · model map: "
                   "density simulated from the selected atoms at a resolution, cut at a contour (ChimeraX molmap) · "
                   "density near model: map density, only close to the selected atoms (includes unmodelled density)."),
        Param("threshold", "float", 0.0, label="Threshold",
              help="0 = automatic: level enclosing the expected mass (or the mass of the selected atoms), otherwise "
                   "Otsu's method. Level of the low-pass-filtered map defining the core."),
        Param("lowpass", "float", 15.0, label="Low-pass before threshold", unit="Å", min=0.0,
              help="Smooths the density so that the mask follows the molecule rather than the noise (0 for none)."),
        Param("mass_kda", "float", 0.0, label="Expected mass", unit="kDa", min=0.0,
              help="0 = mass of the selected atoms when a model is connected. Used by the automatic threshold: the "
                   "mask core then encloses the volume of this mass."),
        Param("chains", "str", "", label="Chains / residues", placeholder="A, B, C:10-250",
              help="Atoms used with a model (empty = whole model)."),
        Param("exclude", "str", "", label="Leave out", placeholder="D, A:300-420",
              help="Chains or residue ranges removed from the selection."),
        Param("atom_radius", "float", 3.0, label="Radius around atoms", unit="Å", min=0.5,
              help="'model atoms': voxels closer than this to a selected atom form the core."),
        Param("near_distance", "float", 6.0, label="Distance from the atoms", unit="Å", min=0.5,
              help="'density near model': density farther than this from the selected atoms is left out."),
        Param("molmap_resolution", "float", 10.0, label="Model map resolution", unit="Å", min=1.0, max=60.0,
              help="'model map': resolution of the density simulated from the atoms (a Gaussian per atom, σ = resolution "
                   "/ (π√2), height its atomic number, as ChimeraX's molmap). 8–15 Å for a refinement or subtraction "
                   "mask (smooth, follows the domains); the map's resolution for a tight envelope."),
        Param("molmap_contour", "float", 95.0, label="Model map contour", unit="%", min=10.0, max=99.99,
              help="'model map': the core is the simulated density above the contour enclosing this share of it (95 % is "
                   "ChimeraX's first contour of a molmap). Higher values give a larger mask."),
        Param("dust", "float", 10.0, label="Remove blobs lighter than", unit="kDa", min=0.0,
              help="Density-based masks: isolated blobs below this protein-equivalent mass are removed (0 keeps them)."),
        Param("keep_largest", "bool", False, label="Keep only the largest blob", advanced=True),
        Param("dilate", "float", 3.0, label="Dilation radius", unit="Å", min=0.0,
              help="The core is extended by this distance (mask value 1)."),
        Param("soft", "float", 6.0, label="Soft edge width", unit="Å", min=0.0,
              help="Raised-cosine fall-off from 1 to 0 beyond the dilation."),
        Param("combine", "choice", "none", choices=["none", "union", "subtract", "intersect"], label="Combine with mask",
              help="Operation with the 'Mask to combine' input, applied to the soft masks."),
        Param("invert", "bool", False, label="Invert (1 − mask)", advanced=True),
        Param("output_name", "str", "mask", label="Output file name", advanced=True, path_kind="name"),
    ]
    outputs = [OutputDef("mask", "mask", "Mask"), OutputDef("model_map", "map", "Simulated map of the model (molmap)")]

    @classmethod
    def validate(cls, params, connected):
        from cryoplug.masks import parse_selection
        problems = []
        if params.get("source") in MODEL_SOURCES and "model" not in connected:
            problems.append("Connect a model for a mask built from atoms")
        if params.get("combine", "none") != "none" and "mask" not in connected:
            problems.append("Connect the mask to combine")
        for key in ("chains", "exclude"):
            try:
                parse_selection(params.get(key, ""))
            except ValueError as exc:
                problems.append(str(exc))
        return problems

    def run(self, ctx: JobContext) -> None:
        from cryoplug import masks as mk
        from cryoplug.imaging import write_png
        try:
            mk.ndimage()
        except ImportError as exc:
            raise JobError(str(exc)) from None
        p = ctx.params
        source = p["source"]
        vol = MapVolume.read(ctx.require("map").path)
        voxel = vol.voxel
        sel = self._selection(ctx, vol)
        rows: list[dict] = [{"label": "Mask from", "value": SOURCE_LABELS[source]}]
        if sel:
            rows.append({"label": "Selected atoms", "value": f"{len(sel['xyz']):,} (chains {', '.join(sel['chains'])})"})

        smooth = threshold = values = None
        model_map = molmap_level = None
        if source == "model_atoms":
            core = mk.atoms_core(vol, sel["xyz"], p["atom_radius"])
            rows.append({"label": "Core", "value": f"within {p['atom_radius']:g} Å of the atoms"})
        elif source == "model_map":
            res = p["molmap_resolution"]
            ctx.progress(0.1, f"Simulating the density of the atoms at {res:g} Å (molmap)")
            density = mk.molmap(vol, sel["xyz"], sel["weights"], res)
            molmap_level = mk.level_enclosing(density, p["molmap_contour"] / 100.0)
            if molmap_level is None:
                raise JobError("The simulated map is empty: the selected atoms lie outside the map box.")
            core = density >= molmap_level
            rows.append({"label": "Core", "value": f"model map at {res:g} Å (Gaussians of σ = {res * mk.SIGMA_FACTOR:.2f} Å), "
                                                   f"contour {molmap_level:.4g} enclosing {p['molmap_contour']:g} % of its density"})
            model_map = vol.like(density).write(ctx.path("model_map.mrc"))
            del density
        else:
            ctx.progress(0.1, "Low-pass filtering")
            smooth = mk.smooth_map(vol, p["lowpass"])
            near = mk.atoms_core(vol, sel["xyz"], p["near_distance"]) if source == "density_near_model" else None
            threshold, how, values = self._threshold(ctx, smooth, near, sel, voxel)
            sd = float(values.std()) or 1.0
            rows.append({"label": "Threshold", "value": f"{threshold:.4g} ({(threshold - float(values.mean())) / sd:.1f} σ) · {how}"})
            rows.append({"label": "Low-pass", "value": f"{p['lowpass']:g} Å" if p["lowpass"] > 0 else "none"})
            core = smooth >= threshold
            if near is not None:
                core &= near
                rows.append({"label": "Region", "value": f"within {p['near_distance']:g} Å of the atoms"})
            del near
            ctx.progress(0.4, "Removing isolated blobs")
            core, removed = mk.remove_islands(core, voxel, p["dust"], p["keep_largest"])
            if p["keep_largest"] or p["dust"] > 0:
                rows.append({"label": "Blobs removed", "value": removed})
        if not core.any():
            raise JobError("The mask is empty: lower the threshold, check the chain selection, or check that the model "
                           "lies inside the map box.")
        ctx.progress(0.6, "Dilation and soft edge")
        mask = mk.soft_edge(core, voxel, p["dilate"], p["soft"])
        del core
        rows.append({"label": "Dilation · soft edge", "value": f"{p['dilate']:g} Å · {p['soft']:g} Å"})
        if p["combine"] != "none":
            other = MapVolume.read(ctx.require("mask").path)
            if other.data.shape != mask.shape:
                raise JobError(f"The mask to combine has a {other.shape_xyz} box, the map {vol.shape_xyz}: they must match.")
            mask = mk.combine(mask, other.data, p["combine"])
            rows.append({"label": "Combined", "value": f"{p['combine']} {ctx.require('mask').source}"})
        if p["invert"]:
            mask = 1.0 - mask
            rows.append({"label": "Inverted", "value": "yes"})
        if not np.any(mask >= 0.5):
            raise JobError("The mask is empty after combining: check the operation and the masks.")

        out = vol.like(mask).write(ctx.path(f"{p['output_name'] or 'mask'}.mrc"))
        stats = mk.statistics(mask, voxel)
        rows += [
            {"label": "Volume (at 0.5)", "value": f"{stats['volume_nm3']:,.0f} nm³"},
            {"label": "Protein-equivalent mass", "value": mk.format_mass(stats["mass_kda"])},
            {"label": "Fraction of the box", "value": f"{100 * stats['fraction']:.1f} %"},
            {"label": "Box", "value": f"{'×'.join(map(str, vol.shape_xyz))} px · {vol.pixel_size:.3f} Å/px"},
        ]
        if sel:
            inside = vol.like(mask).interpolate(sel["xyz"], outside=0.0)
            frac = float(np.mean(inside >= 0.5))
            rows.append({"label": "Selected atoms inside the mask", "value": f"{100 * frac:.1f} %",
                         "status": "good" if frac >= 0.99 else "warn"})
        ctx.add_metrics("Mask", rows)
        image = write_png(ctx.path("mask_sections.png"), mk.overlay_slices(vol.data, mask, mk.centroid(mask)))
        ctx.add_image("Sections through the mask centre (XY, XZ, YZ): map in grey, mask in orange", image)
        if values is not None:
            self._histogram(ctx, values, threshold)
        ctx.add_text("Using the mask elsewhere",
                     f"File: {out.resolve()}\n\nCryoSPARC: Import 3D Volumes, type 'mask', with this path (same box and "
                     "pixel size as the map it was made from).\nRELION: give it to --solvent_mask / --mask.")
        meta = {"source": source, "dilation": p["dilate"], "soft_edge": p["soft"],
                "volume_nm3": round(stats["volume_nm3"], 1), "mass_kda": round(stats["mass_kda"], 1)}
        if threshold is not None:
            meta["threshold"] = round(float(threshold), 6)
        if source == "model_map":
            meta.update(molmap_resolution=p["molmap_resolution"], molmap_contour=p["molmap_contour"])
        if sel:
            meta["chains"] = sel["chains"]
        ctx.add_output("mask", "mask", out, f"Mask ({SOURCE_LABELS[source]})", meta={**ctx.inherited_meta("map"), **meta})
        if model_map is not None:  # as molmap's own result: to look at in the 3D viewer, or to compare with the map
            ctx.add_output("model_map", "map", model_map, f"Model map at {p['molmap_resolution']:g} Å (molmap)",
                           meta={"resolution": p["molmap_resolution"], "simulated": True, "chains": sel["chains"],
                                 "level": round(float(molmap_level), 8)})
        ctx.add_highlight("Mask", mk.format_mass(stats["mass_kda"]))
        ctx.add_highlight("Edge", f"{p['dilate']:g} + {p['soft']:g} Å")

    @staticmethod
    def _selection(ctx: JobContext, vol: MapVolume) -> dict | None:
        from cryoplug.masks import select_atoms
        from cryoplug.modelio import read_structure
        model = ctx.input("model")
        if not model:
            return None
        try:
            sel = select_atoms(read_structure(model.path), ctx.params["chains"], ctx.params["exclude"])
        except ValueError as exc:
            raise JobError(str(exc)) from None
        if sel["missing_chains"]:
            ctx.warn(f"Chain(s) not found in the model: {', '.join(sel['missing_chains'])}")
        if not len(sel["xyz"]):
            raise JobError("No atom selected: check 'Chains / residues' and 'Leave out'.")
        if float(np.mean(vol.contains(sel["xyz"]))) < 0.5:
            ctx.warn("Most selected atoms lie outside the map box: is the model fitted in this map?")
        return sel

    @staticmethod
    def _threshold(ctx: JobContext, smooth: np.ndarray, near: np.ndarray | None, sel: dict | None,
                   voxel: np.ndarray) -> tuple[float, str, np.ndarray]:
        from cryoplug import masks as mk
        p = ctx.params
        values = mk.sample_values(smooth, near)
        if not values.size:
            raise JobError("No map voxel near the selected atoms: is the model inside the map box?")
        if p["threshold"] > 0:
            return float(p["threshold"]), "set by hand", values
        mass = p["mass_kda"] * 1000.0 or (sel["mass_da"] if sel else 0.0)
        if mass > 0:
            n_total = int(np.count_nonzero(near)) if near is not None else smooth.size
            level = mk.level_for_mass(values, n_total, mass, float(np.prod(voxel)))
            origin = "expected mass" if p["mass_kda"] > 0 else "mass of the selected atoms"
            return level, f"automatic, encloses {mk.format_mass(mass / 1000)} ({origin})", values
        return mk.otsu_threshold(values), "automatic (Otsu's method; give the expected mass for a better level)", values

    @staticmethod
    def _histogram(ctx: JobContext, values: np.ndarray, threshold: float) -> None:
        lo, hi = np.percentile(values, [0.05, 99.95])
        counts, edges = np.histogram(values, bins=120, range=(float(lo), float(hi)))
        centres = ((edges[:-1] + edges[1:]) / 2).round(6).tolist()
        logc = np.log10(1 + counts).round(3).tolist()
        top = max(logc) if logc else 1.0
        ctx.add_plot("Values of the low-pass map", [
            {"name": "Voxels (log10)", "x": centres, "y": logc},
            {"name": "Threshold", "x": [threshold, threshold], "y": [0, top]},
        ], x_label="Map value", y_label="log10(voxels + 1)")


@register
class DrawMask(JobType):
    name = "draw_mask"
    title = "Mask drawn in the 3D viewer"
    category = "Map processing"
    software = ["CryoPlug"]
    cpus = 2
    description = ("Mask drawn by hand on a map in the 3D viewer, as in Chimera: spheres, boxes and cylinders placed with "
                   "the mouse, connected regions of density picked at the contour level, zones around chains, the "
                   "density simulated from chains of a model (molmap), the density above a level or an existing mask, "
                   "added, erased or intersected; then dilated and given a soft edge. Computed here on the full grid of "
                   "the map, with its centre of mass and the box to re-centre particles on it (signal subtraction).")
    inputs = [
        Slot("map", ("map",), "Map", help="The map the mask is drawn on: it sets the box and the pixel size."),
        Slot("model", ("model",), "Model", required=False,
             help="For zones around chains and model maps (molmap) drawn in the viewer."),
    ]
    params = [
        Param("recipe", "text", "", label="Drawing (from the 3D viewer)", advanced=True,
              help="Written by the mask editor of the 3D viewer (Mask button): the shapes and regions, in Å."),
        Param("dilate", "float", 2.0, label="Dilation", unit="Å", min=0.0, max=100.0,
              help="The drawn region is grown by this distance."),
        Param("soft", "float", 6.0, label="Soft edge", unit="Å", min=0.0, max=100.0,
              help="Raised-cosine fall-off from 1 to 0 beyond the dilation (≥ 6 Å for a refinement mask)."),
        Param("invert", "bool", False, label="Invert", advanced=True, help="1 outside the drawn region, 0 inside."),
        Param("output_name", "str", "mask_drawn", label="Output file name", advanced=True, path_kind="name"),
    ]
    outputs = [OutputDef("mask", "mask", "Drawn mask")]

    @classmethod
    def validate(cls, params, connected):
        import json

        from cryoplug import maskdraw
        text = str(params.get("recipe") or "").strip()
        if not text:
            return ["Draw the mask first: open the map in the 3D viewer and use the Mask tool (or 'Open mask editor' on "
                    "this job)"]
        try:
            recipe = maskdraw.validate(json.loads(text))
        except (ValueError, TypeError) as exc:
            return [f"Drawing: {exc}"]
        if not recipe["ops"]:
            return ["The drawing is empty: add at least one shape or region in the mask editor"]
        if not any(op["mode"] == "add" for op in recipe["ops"]):
            return ["The drawing only erases or intersects: add a shape, a region or the map density first"]
        return []

    def run(self, ctx: JobContext) -> None:
        import json

        from cryoplug import maskdraw
        from cryoplug import masks as mk
        from cryoplug import relion as rl
        from cryoplug.imaging import write_png
        p = ctx.params
        recipe = maskdraw.validate({**json.loads(p["recipe"]), "dilate": p["dilate"], "soft": p["soft"]})
        vol = MapVolume.read(ctx.require("map").path)
        model = ctx.input("model")

        def inside_project(path: str) -> Path:
            target = ctx.abs(path)
            try:
                target.resolve().relative_to(ctx.project_dir.resolve())
            except ValueError:
                if not (model and Path(model.path).resolve() == target.resolve()):
                    raise JobError(f"{path} is not a file of this project") from None
            if not target.exists():
                raise JobError(f"{path} does not exist any more")
            return target

        def models(path: str, selection: str) -> np.ndarray:
            try:
                return maskdraw.atom_positions(str(inside_project(path)), selection)
            except ValueError as exc:
                raise JobError(f"Zone around {selection or 'the model'}: {exc}") from None

        def atoms(path: str, selection: str) -> tuple[np.ndarray, np.ndarray]:
            try:
                return maskdraw.atom_weights(str(inside_project(path)), selection)
            except ValueError as exc:
                raise JobError(f"Model map of {selection or 'the model'}: {exc}") from None

        def masks(path: str) -> np.ndarray:
            return maskdraw.resample_mask(str(inside_project(path)), vol)

        ctx.progress(0.1, f"Drawing {len(recipe['ops'])} operation{'s' if len(recipe['ops']) > 1 else ''} on the full grid")
        try:
            core = maskdraw.draw(recipe, vol, models=models, masks=masks, atoms=atoms)
        except ValueError as exc:
            raise JobError(str(exc)) from None
        if not core.any():
            raise JobError("The drawn region is empty on the map's grid (shapes outside the box?)")
        ctx.progress(0.6, "Dilation and soft edge")
        mask = maskdraw.finish(core, vol.voxel, recipe)
        if p["invert"]:
            mask = 1.0 - mask
        out = vol.like(mask).write(ctx.path(f"{p['output_name'] or 'mask_drawn'}.mrc"))
        stats = mk.statistics(mask, vol.voxel)
        com = rl.center_of_mass(mask)  # voxels from the box centre (x, y, z)
        box = vol.shape_xyz[0]
        com_a = vol.origin + (np.array(vol.shape_xyz) // 2 + com) * vol.voxel
        suggested = rl.suggest_box(mask, np.round(com), box) if vol.shape_xyz[0] == vol.shape_xyz[1] == vol.shape_xyz[2] else None
        rows = [
            {"label": "Volume (at 0.5)", "value": f"{stats['volume_nm3']:,.0f} nm³"},
            {"label": "Protein-equivalent mass", "value": mk.format_mass(stats["mass_kda"])},
            {"label": "Fraction of the box", "value": f"{100 * stats['fraction']:.1f} %"},
            {"label": "Centre of mass", "value": f"({', '.join(f'{c:.1f}' for c in com_a)}) Å · "
                                                 f"({', '.join(f'{c:+.1f}' for c in com)}) voxels from the box centre"},
            {"label": "Edge", "value": f"dilation {p['dilate']:g} Å + soft edge {p['soft']:g} Å"},
        ]
        if suggested:
            rows.append({"label": "Box for re-centred particles", "value": f"{suggested} px ({suggested * vol.pixel_size:.0f} Å)"})
        ctx.add_metrics("Mask", rows)
        ctx.add_table("Drawing", ["#", "Operation"], [[i + 1, maskdraw.describe(op)] for i, op in enumerate(recipe["ops"])])
        image = write_png(ctx.path("mask_sections.png"), mk.overlay_slices(vol.data, mask, mk.centroid(mask)))
        ctx.add_image("Sections through the mask centre (XY, XZ, YZ): map in grey, mask in orange", image)
        meta = {**ctx.inherited_meta("map"), "source": "drawn", "dilation": p["dilate"], "soft_edge": p["soft"],
                "volume_nm3": round(stats["volume_nm3"], 1), "mass_kda": round(stats["mass_kda"], 1),
                "center_of_mass": [round(float(c), 2) for c in com_a], "operations": len(recipe["ops"])}
        if suggested:
            meta["suggested_box"] = int(suggested)
        ctx.add_output("mask", "mask", out, f"Drawn mask ({mk.format_mass(stats['mass_kda'])})", meta=meta)
        ctx.add_highlight("Mask", mk.format_mass(stats["mass_kda"]))
        ctx.add_highlight("Edge", f"{p['dilate']:g} + {p['soft']:g} Å")


# distinct colours for the regions (blue, orange, green, red, purple, brown, pink, olive, cyan, grey)
REGION_COLOURS = [(42, 120, 214), (235, 104, 52), (27, 175, 122), (208, 59, 59), (128, 90, 213), (140, 86, 75),
                  (227, 119, 194), (188, 189, 34), (23, 190, 207), (127, 127, 127)]
COLOUR_NAMES = ["blue", "orange", "green", "red", "purple", "brown", "pink", "olive", "cyan", "grey"]


@register
class SegmentMap(JobType):
    name = "segment_map"
    title = "Segment map into regions"
    category = "Map processing"
    software = ["CryoPlug"]
    cpus = 2
    description = ("Splits a map into regions (domains, subunits) as ChimeraX's Segger: watershed of the smoothed density "
                   "above a threshold, regions grouped by the depth of the valleys between them until the number asked "
                   "remains; a soft mask per region, the largest first: the bodies of a multi-body refinement, the mask "
                   "of a focused refinement or classification, or of a signal subtraction.")
    inputs = [Slot("map", ("map",), "Map", help="A consensus map (post-processed or sharpened: domains stand out)."),
              Slot("mask", ("mask",), "Restrict to a mask", required=False,
                   help="Only the density inside this mask is segmented (one part of a large complex).")]
    params = [
        Param("regions", "int", 2, label="Regions", min=1, max=30,
              help="How many regions to keep (largest first): 2-4 rigid bodies for a multi-body refinement."),
        Param("threshold", "float", 0.0, label="Threshold",
              help="Density of the smoothed map above which voxels are segmented. 0 = automatic: enclosing the "
                   "expected mass if given, otherwise Otsu's level."),
        Param("mass_kda", "float", 0.0, label="Expected mass", unit="kDa", min=0,
              help="For the automatic threshold (the mass of the complex, or of the part inside the mask)."),
        Param("lowpass", "float", 12.0, label="Smoothing (low-pass)", unit="Å", min=0,
              help="10-15 Å separates domains; finer smoothing splits them into many small regions."),
        Param("min_region_kda", "float", 10.0, label="Smallest region", unit="kDa", min=0, advanced=True,
              help="Watershed regions smaller than this are grouped first, and blobs smaller than this are dropped."),
        Param("dilate", "float", 3.0, label="Dilation", unit="Å", min=0, help="Each region grown by this much."),
        Param("soft", "float", 6.0, label="Soft edge", unit="Å", min=0,
              help="Raised-cosine edge: masks of neighbouring regions overlap there (RELION's multi-body advice)."),
    ]
    outputs = [OutputDef("region_1", "mask", "Region 1"), OutputDef("region_2", "mask", "Region 2")]

    def run(self, ctx: JobContext) -> None:
        from cryoplug import masks as mk
        from cryoplug import segment as sg
        from cryoplug.imaging import write_png
        from cryoplug.micrographs import tile_sheet
        p = ctx.params
        vol = MapVolume.read(ctx.require("map").path)
        voxel = np.asarray(vol.voxel, dtype=np.float64)
        ctx.progress(0.1, f"Smoothing to {p['lowpass']:g} Å")
        smooth = mk.smooth_map(vol, float(p["lowpass"]))
        region = None
        restrict = ctx.input("mask")
        if restrict is not None:
            m = MapVolume.read(restrict.path)
            if m.data.shape != smooth.shape:
                raise JobError(f"The mask has a {m.shape_xyz} box, the map {vol.shape_xyz}: they must match")
            region = m.data >= 0.5
        values = mk.sample_values(smooth, region)
        if p["threshold"] > 0:
            threshold, how = float(p["threshold"]), "set by hand"
        elif p["mass_kda"] > 0:
            n_total = int(np.count_nonzero(region)) if region is not None else smooth.size
            threshold = mk.level_for_mass(values, n_total, p["mass_kda"] * 1000.0, float(np.prod(voxel)))
            how = f"encloses {mk.format_mass(p['mass_kda'])}"
        else:
            threshold, how = mk.otsu_threshold(values), "Otsu's method (give the expected mass for a better level)"
        inside = smooth >= threshold
        if region is not None:
            inside &= region
        inside, removed = mk.remove_islands(inside, voxel, float(p["min_region_kda"]))
        if not inside.any():
            raise JobError("Nothing is left above the threshold: lower it, or give the expected mass")
        min_voxels = int(p["min_region_kda"] * 1000.0 * mk.A3_PER_DA / float(np.prod(voxel)))
        ctx.progress(0.3, "Watershed and grouping of the regions")
        labels, raw = sg.segment(smooth, inside, int(p["regions"]), min_voxels)
        n = int(labels.max())
        ctx.log(f"{raw} watershed regions above {threshold:.4g} ({how}), grouped into {n}")
        if n < int(p["regions"]):
            ctx.warn(f"Only {n} region(s): the density splits no further at this threshold and smoothing (lower the "
                     "smoothing or the threshold)")
        rows = []
        for r in range(1, n + 1):
            ctx.progress(0.4 + 0.5 * (r - 1) / n, f"Mask of region {r}")
            core = labels == r
            mask = mk.soft_edge(core, voxel, float(p["dilate"]), float(p["soft"]))
            out = vol.like(mask).write(ctx.path(f"region_{r}.mrc"))
            stats = mk.statistics(core.astype(np.float32), voxel)
            cz, cy, cx = mk.centroid(core)
            centre = [round(float(c * v + o), 1) for c, v, o in zip((cx, cy, cz), voxel, vol.origin)]
            colour = COLOUR_NAMES[(r - 1) % len(COLOUR_NAMES)]
            rows.append([r, colour, f"{stats['volume_nm3']:,.0f}", mk.format_mass(stats["mass_kda"]),
                         " ".join(f"{c:g}" for c in centre)])
            ctx.add_output(f"region_{r}", "mask", out, f"Region {r} ({colour}, {mk.format_mass(stats['mass_kda'])})",
                           meta={**ctx.inherited_meta("map"), "region": r, "colour": colour,
                                 "mass_kda": round(stats["mass_kda"], 1), "threshold": round(float(threshold), 6),
                                 "dilation": p["dilate"], "soft_edge": p["soft"]})
        ctx.add_table("Regions", ["Region", "Colour", "Volume (nm³)", "Protein-equivalent mass", "Centre (x y z, Å)"], rows)
        tiles = sg.colour_projections(smooth, labels, REGION_COLOURS, size=260)
        tiles = [np.pad(t, ((0, 260 - t.shape[0]), (0, 260 - t.shape[1]), (0, 0))) for t in tiles]
        png = write_png(ctx.path("regions.png"), tile_sheet(tiles, per_row=3, gap=6))
        ctx.add_image("Regions seen along z, y and x (the densest voxel of each ray, coloured by its region)", png)
        ctx.add_text("Using the regions",
                     "Multi-body refinement: connect the masks as Body 1, Body 2… (the largest first, as RELION advises). "
                     "Focused refinement, classification or signal subtraction: the mask of the region kept. The 3D "
                     "viewer shows the masks over the map.")
        ctx.add_highlight("Regions", str(n))
        ctx.add_highlight("Threshold", f"{threshold:.4g}")
        if removed:
            ctx.log(f"{removed} small blob(s) below {p['min_region_kda']:g} kDa left out")


# ------------------------------------------------------ enhanced: the mask validated by the corrected FSC
def parse_numbers(text: str, what: str) -> list[float]:
    """'2, 4, 8' → [2.0, 4.0, 8.0] (each once, in that order); ValueError with ``what`` in the message otherwise."""
    out: list[float] = []
    for item in re.split(r"[,;\s]+", str(text or "").strip()):
        if not item:
            continue
        try:
            value = float(item)
        except ValueError:
            raise ValueError(f"{what}: '{item}' is not a number") from None
        if value < 0:
            raise ValueError(f"{what}: {item} is negative")
        if value not in out:
            out.append(value)
    if not out:
        raise ValueError(f"{what}: give at least one value")
    return out


def judge_mask(result: dict[str, Any], max_noise: float, max_inflation: float) -> dict[str, Any]:
    """What the FSC of the half maps says about a mask (``result`` from :func:`cryoplug.mrc.half_map_fsc`): the
    resolutions, the correlation of the noise the mask induces (the phase-randomised FSC beyond the randomisation
    point) and how much the uncorrected masked FSC overstates the corrected one."""
    freqs = result["frequency"]
    curves, res = result["curves"], result["resolution"]
    rand = curves.get("phase_randomized") or []
    start = len(freqs)
    if result.get("randomized_from") and rand:
        start = next((i for i, f in enumerate(freqs) if f >= 1.0 / result["randomized_from"]), len(freqs)) + 2
    noise = float(max(rand[start:], default=0.0))
    masked, corrected = res.get("masked", res["unmasked"]), res.get("corrected", res["unmasked"])
    inflation = 100.0 * (corrected - masked) / corrected if corrected else 0.0
    too_tight, inflates = noise > max_noise, inflation > max_inflation
    verdict = "too tight (noise correlation)" if too_tight else "inflates the FSC" if inflates else "ok"
    return {"res_unmasked": res["unmasked"], "res_masked": masked, "res_corrected": corrected, "noise": noise,
            "inflation": inflation, "safe": not (too_tight or inflates), "verdict": verdict}


def choose_mask(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """The mask kept: among the safe ones, the best corrected resolution, the largest edge (dilation + soft edge)
    within 1 % of it; when none is safe, the one correlating the least noise."""
    safe = [c for c in candidates if c["safe"]]
    if not safe:
        return min(candidates, key=lambda c: (c["noise"], c["inflation"]))
    best = min(c["res_corrected"] for c in safe)
    pool = [c for c in safe if c["res_corrected"] <= best * 1.01]
    return max(pool, key=lambda c: (c["dilate"] + c["soft"], -c["res_corrected"]))


@register
class MaskEnhanced(JobType):
    name = "mask_enhanced"
    title = "Mask enhanced (validated by the FSC)"
    category = "Map processing"
    software = ["CryoPlug"]
    cpus = 2
    description = ("Masks made from the map at several thresholds, dilations and soft edges, each scored by the "
                   "gold-standard FSC of the half maps it gives (masked, corrected by phase randomisation): the tightest "
                   "mask that does not correlate the noise nor inflate the FSC is kept, with its FSC curves. The mask for "
                   "post-processing, local refinement and the deposition.")
    inputs = [Slot("half_maps", ("half_maps",), "Half maps", help="Unfiltered half maps: their FSC scores every mask."),
              Slot("map", ("map",), "Map", required=False,
                   help="Thresholded for the masks (default: the average of the half maps).")]
    params = [
        Param("lowpass", "float", 15.0, label="Low-pass before threshold", unit="Å", min=0.0,
              help="Smooths the density so that the masks follow the molecule rather than the noise."),
        Param("mass_kda", "float", 0.0, label="Expected mass", unit="kDa", min=0.0,
              help="0 = Otsu's threshold; otherwise the level enclosing this mass is the automatic level (×1)."),
        Param("thresholds", "str", "0.5, 1, 2", label="Threshold multipliers",
              help="Multiples of the automatic level, each giving a mask core (lower = larger core)."),
        Param("dilations", "str", "2, 4, 8", label="Dilations", unit="Å", help="Each core is extended by these distances."),
        Param("softs", "str", "3, 6, 10", label="Soft edges", unit="Å", help="Raised-cosine fall-off widths beyond the dilation."),
        Param("dust", "float", 10.0, label="Remove blobs lighter than", unit="kDa", min=0.0),
        Param("max_noise", "float", 0.2, label="Noise correlation allowed", min=0.0, max=1.0,
              help="Largest value of the phase-randomised FSC beyond the randomisation point: above it the mask "
                   "correlates the noise of the two half maps (too tight) and is not chosen."),
        Param("max_inflation", "float", 10.0, label="Inflation allowed", unit="%", min=0.0,
              help="How much better (in %) the uncorrected masked resolution may be than the corrected one: beyond, the "
                   "mask is flagged as inflating the FSC."),
        Param("keep_masks", "bool", False, label="Keep every mask", advanced=True,
              help="Write all the masks tried (one file each) and offer them in 3D."),
    ]
    outputs = [OutputDef("mask", "mask", "Mask"), OutputDef("fsc", "fsc", "Half-map FSC (mask kept)"),
               OutputDef("masks", "files", "All the masks tried")]

    @classmethod
    def validate(cls, params: dict[str, Any], connected: dict[str, str]) -> list[str]:
        problems = []
        for key, what in (("thresholds", "Threshold multipliers"), ("dilations", "Dilations"), ("softs", "Soft edges")):
            try:
                parse_numbers(params.get(key, ""), what)
            except ValueError as exc:
                problems.append(str(exc))
        return problems

    def run(self, ctx: JobContext) -> None:
        from cryoplug import masks as mk
        from cryoplug.imaging import write_png
        from cryoplug.jobs.decisions import DecisionMap
        from cryoplug.jobs.relion import report_fsc
        from cryoplug.mrc import half_map_fsc
        try:
            mk.ndimage()
        except ImportError as exc:
            raise JobError(str(exc)) from None
        p = ctx.params
        hm = ctx.require("half_maps")
        half_a, half_b = MapVolume.read(hm.files[0]), MapVolume.read(hm.files[1])
        if half_a.data.shape != half_b.data.shape:
            raise JobError("The two half maps do not have the same box")
        m = ctx.input("map")
        vol = MapVolume.read(m.path) if m else half_a.like((half_a.data + half_b.data) / 2)
        if vol.data.shape != half_a.data.shape:
            raise JobError(f"The map box {vol.shape_xyz} differs from the half maps' {half_a.shape_xyz}")
        thresholds = parse_numbers(p["thresholds"], "Threshold multipliers")
        dilations, softs = parse_numbers(p["dilations"], "Dilations"), parse_numbers(p["softs"], "Soft edges")
        voxel = vol.voxel
        max_noise, max_inflation = float(p["max_noise"]), float(p["max_inflation"])

        flow = DecisionMap(ctx, note="The masks tried (one box per threshold, dilation + soft edge inside), each scored by "
                                     "the FSC of the half maps it gives; the one kept. Click a tile for its resolutions.")
        flow.add("input", "input", "Half maps", icon="map", lines=[Path(hm.files[0]).name, Path(hm.files[1]).name],
                 view=[{"kind": "map", "path": hm.files[0], "label": "half map 1"}, {"kind": "map", "path": hm.files[1], "label": "half map 2"}])
        ctx.progress(0.02, "Low-pass filtering and automatic level")
        smooth = mk.smooth_map(vol, p["lowpass"])
        values = mk.sample_values(smooth)
        if p["mass_kda"] > 0:
            level0 = mk.level_for_mass(values, smooth.size, p["mass_kda"] * 1000.0, float(np.prod(voxel)))
            how = f"encloses {mk.format_mass(p['mass_kda'])}"
        else:
            level0 = mk.otsu_threshold(values)
            how = "Otsu's method"
        flow.add("level", "decision", "Automatic level", parents=["input"], icon="sliders",
                 lines=[f"{level0:.4g} ({how})", f"low-pass {p['lowpass']:g} Å · multipliers {', '.join(f'{t:g}' for t in thresholds)}"])
        base = half_map_fsc(half_a, half_b)
        ctx.add_highlight("Unmasked", f"{base['resolution']['unmasked']:.2f} Å")
        live = ctx.path("live")
        live.mkdir(exist_ok=True)
        if p["keep_masks"]:
            ctx.path("masks").mkdir(exist_ok=True)
        candidates: list[dict[str, Any]] = []
        total = len(thresholds) * len(dilations) * len(softs)
        for t in thresholds:
            level = t * level0
            gid = f"group:{t:g}"
            flow.add(gid, "group", f"Threshold ×{t:g} ({level:.4g}) · {len(dilations) * len(softs)} masks", parents=["level"], icon="grid")
            core = smooth >= level
            core, _removed = mk.remove_islands(core, voxel, p["dust"])
            if not core.any():
                ctx.warn(f"Threshold ×{t:g} ({level:.4g}) gives an empty core: skipped")
                flow.update(gid, status="failed")
                continue
            dist = mk.distance_from(core, voxel)
            for d in dilations:
                for s in softs:
                    i = len(candidates)
                    label = f"×{t:g} · {d:g}+{s:g} Å"
                    node = f"mask:{t:g}:{d:g}:{s:g}"
                    flow.add(node, "run", f"{d:g} + {s:g} Å", group=gid, status="running", lines=["FSC…"], icon="cube")
                    ctx.progress(0.05 + 0.85 * i / total, f"Mask {i + 1}/{total}: threshold ×{t:g}, dilation {d:g} Å, soft edge {s:g} Å")
                    mask = np.where(dist <= d, 1.0, 0.0).astype(np.float32)
                    if s > 0:
                        band = (dist > d) & (dist < d + s)
                        mask[band] = 0.5 + 0.5 * np.cos(np.pi * (dist[band] - d) / s)
                    result = half_map_fsc(half_a, half_b, half_a.like(mask))
                    stats = mk.statistics(mask, voxel)
                    cand = {"label": label, "threshold": t, "level": level, "dilate": d, "soft": s, "node": node,
                            "mass_kda": stats["mass_kda"], "fraction": stats["fraction"], "result": result,
                            **judge_mask(result, max_noise, max_inflation)}
                    if p["keep_masks"]:
                        cand["path"] = str(vol.like(mask).write(ctx.path("masks", f"mask_{i + 1:02d}.mrc")))
                    cand["png"] = str(write_png(live / f"mask_{i + 1:02d}.png", mk.overlay_slices(vol.data, mask, mk.centroid(mask), size=200)))
                    candidates.append(cand)
                    flow.update(node, status="done" if cand["safe"] else "discarded",
                                lines=[f"{cand['res_corrected']:.2f} Å corrected", cand["verdict"] if not cand["safe"]
                                       else f"noise {cand['noise']:.2f} · {mk.format_mass(cand['mass_kda'])}"],
                                detail=self._summary(cand), view=[{"kind": "mask", "path": cand["path"], "label": label}] if p["keep_masks"] else [])
                    ctx.log(f"Mask {label}: {self._summary(cand)}")
                    self._live(ctx, candidates, cand)
        if not candidates:
            raise JobError("No mask could be made: lower the threshold multipliers or the expected mass")

        best = choose_mask(candidates)
        flow.mark(best["node"], [c["node"] for c in candidates])
        safe = [c for c in candidates if c["safe"]]
        if not safe:
            ctx.warn("Every mask correlates the noise of the half maps or inflates the FSC: the least harmful one is kept. "
                     "Raise the dilations and soft edges, or lower the threshold multipliers.")
        why = (f"best corrected resolution among the {len(safe)} safe masks, the largest edge within 1 % of it" if safe
               else "no safe mask: the smallest noise correlation")
        flow.add("best", "decision", f"Mask {best['label']} kept", parents=[best["node"]], status="chosen" if safe else "warning",
                 icon="target", lines=[f"{best['res_corrected']:.2f} Å corrected · {mk.format_mass(best['mass_kda'])}", why],
                 detail=self._summary(best))
        ctx.log(f"Mask {best['label']} kept: {why}")

        # the mask kept, its FSC in CryoPlug's format, the pictures
        ctx.progress(0.95, "Writing the mask kept")
        core = smooth >= best["level"]
        core, _removed = mk.remove_islands(core, voxel, p["dust"])
        mask = mk.soft_edge(core, voxel, best["dilate"], best["soft"])
        out = vol.like(mask).write(ctx.path("mask.mrc"))
        image = write_png(ctx.path("mask_sections.png"), mk.overlay_slices(vol.data, mask, mk.centroid(mask)))
        ctx.add_image("Sections through the centre of the mask kept (XY, XZ, YZ): map in grey, mask in orange", image)
        res = best["result"]
        report_fsc(ctx, res["frequency"], res["curves"], "corrected", None, "Half-map FSC with the mask kept")
        meta = {**ctx.inherited_meta("half_maps"), "source": "map_density", "threshold": round(float(best["level"]), 6),
                "dilation": best["dilate"], "soft_edge": best["soft"], "mass_kda": round(best["mass_kda"], 1),
                "resolution": round(best["res_corrected"], 3), "fsc_validated": True}
        ctx.add_output("mask", "mask", out, f"Mask ({best['label']}, FSC-validated)", meta=meta, thumbnail=image)
        if p["keep_masks"]:
            ctx.add_output("masks", "files", [c["path"] for c in candidates], "All the masks tried")
        ctx.add_highlight("Resolution", f"{best['res_corrected']:.2f} Å")
        ctx.add_highlight("Mask", best["label"])
        ctx.add_highlight("Noise", f"{best['noise']:.2f}", "good" if best["safe"] else "warn")
        flow.add("result", "result", "Mask kept", parents=["best"], status="chosen", icon="target",
                 lines=[best["label"], f"{best['res_corrected']:.2f} Å (unmasked {best['res_unmasked']:.2f} Å)"],
                 view=[{"kind": "mask", "path": out, "label": "mask"}, {"kind": "map", "path": hm.files[0], "label": "half map 1"}])
        ctx.add_text("Summary", f"Mask {best['label']} kept ({why}): {self._summary(best)}.\n"
                                f"{len(safe)} of {len(candidates)} masks safe; unmasked resolution {best['res_unmasked']:.2f} Å.\n"
                                "A mask too tight correlates the noise of the two half maps: the phase-randomised FSC stays "
                                "high beyond the randomisation point, and the corrected curve is not reliable. The corrected "
                                "resolution is the one to report.")

    # ----------------------------------------------------------------- helpers
    @staticmethod
    def _summary(c: dict[str, Any]) -> str:
        from cryoplug.masks import format_mass
        return (f"corrected {c['res_corrected']:.2f} Å, masked {c['res_masked']:.2f} Å, unmasked {c['res_unmasked']:.2f} Å; "
                f"noise correlation {c['noise']:.2f}, inflation {c['inflation']:.1f} %; {format_mass(c['mass_kda'])}, "
                f"{100 * c['fraction']:.1f} % of the box; {c['verdict']}")

    def _live(self, ctx: JobContext, candidates: list[dict[str, Any]], cand: dict[str, Any]) -> None:
        """The masks so far: the table, the FSC curves and the sections of each (sliders), the resolutions by mask."""
        from cryoplug.masks import format_mass
        rows = [[c["label"], f"{c['level']:.4g}", format_mass(c["mass_kda"]), f"{100 * c['fraction']:.1f}", f"{c['res_masked']:.2f}",
                 f"{c['res_corrected']:.2f}", f"{c['noise']:.2f}", f"{c['inflation']:.1f}", "✓" if c["safe"] else c["verdict"]]
                for c in candidates]
        ctx.add_table("Masks compared", ["Mask", "Level", "Mass", "Box (%)", "Masked (Å)", "Corrected (Å)", "Noise", "Inflation (%)", "Verdict"],
                      rows, key="masks",
                      note=f"Unmasked resolution {candidates[0]['res_unmasked']:.2f} Å. Noise: the largest phase-randomised FSC "
                           "beyond the randomisation point (a tight mask correlates the noise of the two half maps). Inflation: "
                           "how much better the uncorrected masked resolution is than the corrected one.")
        res = cand["result"]
        curves = res["curves"]
        names = {"unmasked": "Unmasked", "masked": "Masked", "corrected": "Corrected", "phase_randomized": "Phase randomised"}
        ctx.add_plot_frame("FSC of each mask", [{"name": names[k], "x": res["frequency"], "y": curves[k]} for k in names if k in curves],
                           cand["label"], key="mask_fsc", x_label="Resolution (Å)", y_label="FSC", x_kind="resolution",
                           hlines=[{"y": 0.143, "label": "0.143"}, {"y": 0.5, "label": "0.5"}], y_range=[-0.1, 1.05], group="masks",
                           note="The corrected curve is the gold-standard FSC; the phase-randomised one should fall to zero "
                                "soon after the randomisation point.")
        ctx.add_image_frame("Sections through each mask", cand["png"], cand["label"], key="mask_slices", group="masks",
                            caption=f"{cand['res_corrected']:.2f} Å corrected · {format_mass(cand['mass_kda'])} · {cand['verdict']}",
                            view=[{"kind": "mask", "path": cand["path"], "label": cand["label"]}] if cand.get("path") else [],
                            note="Map in grey, mask in orange (XY, XZ, YZ through the centre of the mask).")
        if len(candidates) > 1:
            x = list(range(1, len(candidates) + 1))
            ctx.add_plot("Resolution by mask", [{"name": "Corrected", "x": x, "y": [round(c["res_corrected"], 3) for c in candidates]},
                                                {"name": "Masked (uncorrected)", "x": x, "y": [round(c["res_masked"], 3) for c in candidates]},
                                                {"name": "Noise correlation", "x": x, "y": [round(c["noise"], 3) for c in candidates]}],
                         x_label="Mask", y_label="Å (resolution) / FSC (noise)", key="masks_plot",
                         note="Masks in the order tried: " + ", ".join(f"{i + 1} = {c['label']}" for i, c in enumerate(candidates)) + ".")
