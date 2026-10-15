"""Mask creation (built-in): soft-edged masks for FSC, local refinement, LocScale, spIsoNet, signal subtraction."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from cryoplug.jobs import register
from cryoplug.jobs.base import JobContext, JobError, JobType, OutputDef, Param, Slot
from cryoplug.mrc import MapVolume

SOURCE_LABELS = {
    "map_density": "map density",
    "model_atoms": "atoms of the model",
    "density_near_model": "map density near the model",
}


@register
class CreateMask(JobType):
    name = "create_mask"
    title = "Create mask"
    category = "Map processing"
    software = ["CryoPlug"]
    cpus = 2
    description = ("Soft-edged mask from the map density (low-pass + threshold), from the atoms of chosen chains, or "
                   "from the density near them, with dilation and soft edge in Å as in CryoSPARC. For FSC, local "
                   "refinement, LocScale, spIsoNet or signal subtraction; can be combined with another mask.")
    inputs = [
        Slot("map", ("map",), "Map", help="Sets the box and pixel size of the mask; thresholded for density-based masks."),
        Slot("model", ("model",), "Model", required=False,
             help="For a mask around chains, or to keep only the density near them (must be fitted in the map)."),
        Slot("mask", ("mask",), "Mask to combine", required=False, help="Used by 'Combine with mask'."),
    ]
    params = [
        Param("source", "choice", "map_density", choices=list(SOURCE_LABELS), label="Mask from",
              help="map density: low-pass + threshold · model atoms: everything near the selected atoms · density near "
                   "model: map density, only close to the selected atoms (includes unmodelled density)."),
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
    outputs = [OutputDef("mask", "mask", "Mask")]

    @classmethod
    def validate(cls, params, connected):
        from cryoplug.masks import parse_selection
        problems = []
        if params.get("source") in ("model_atoms", "density_near_model") and "model" not in connected:
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
        if source == "model_atoms":
            core = mk.atoms_core(vol, sel["xyz"], p["atom_radius"])
            rows.append({"label": "Core", "value": f"within {p['atom_radius']:g} Å of the atoms"})
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
        if sel:
            meta["chains"] = sel["chains"]
        ctx.add_output("mask", "mask", out, f"Mask ({SOURCE_LABELS[source]})", meta={**ctx.inherited_meta("map"), **meta})
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
                   "density above a level or an existing mask, added, erased or intersected; then dilated and given a "
                   "soft edge. Computed here on the full grid of the map, with its centre of mass and the box to "
                   "re-centre particles on it (signal subtraction).")
    inputs = [
        Slot("map", ("map",), "Map", help="The map the mask is drawn on: it sets the box and the pixel size."),
        Slot("model", ("model",), "Model", required=False, help="For zones around chains (drawn in the viewer)."),
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

        def masks(path: str) -> np.ndarray:
            return maskdraw.resample_mask(str(inside_project(path)), vol)

        ctx.progress(0.1, f"Drawing {len(recipe['ops'])} operation{'s' if len(recipe['ops']) > 1 else ''} on the full grid")
        try:
            core = maskdraw.draw(recipe, vol, models=models, masks=masks)
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
