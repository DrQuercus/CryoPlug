"""Rigid-body placement of a model, a map or a mask in a map (built-in, as ChimeraX's fitmap), from a position set by
hand in the 3D viewer and / or by local optimisation or a search."""
from __future__ import annotations

import json
import time

import numpy as np

from cryoplug.jobs import register
from cryoplug.jobs.base import JobContext, JobError, JobType, OutputDef, Param, Slot
from cryoplug.mrc import MapVolume


def parse_matrix(text: str) -> np.ndarray:
    """The position from the viewer (JSON 4×4 / 3×4 rows, or 12 / 16 numbers); identity when empty."""
    from cryoplug import fitting as ft
    text = str(text or "").strip()
    if not text:
        return np.eye(4)
    try:
        value = json.loads(text)
    except ValueError:
        value = [float(v) for v in text.replace(",", " ").split()]
    return ft.as_matrix(value)


@register
class FitInMap(JobType):
    name = "fit_in_map"
    title = "Fit in map (rigid body)"
    category = "Model building"
    software = ["CryoPlug"]
    cpus = 4
    description = ("Rigid-body placement of a model, a map or a mask in a map, as ChimeraX's fitmap (no ChimeraX needed): "
                   "from the position set by hand in the 3D viewer (Move), a local optimisation of the average map value "
                   "at the atoms, or of the overlap / correlation with the model's simulated map at a resolution, or a "
                   "search from random placements. Writes the placed model, or the map resampled on the target's grid.")
    inputs = [
        Slot("model", ("model",), "Model to place", required=False),
        Slot("moving_map", ("map", "mask"), "Map or mask to place", required=False,
             help="Instead of a model: fitted with its grid points above the contour, written on the target map's grid."),
        Slot("map", ("map",), "Target map", required=False, prefer=("map_sharp", "map"),
             help="The map to fit into. Without it, only the position from the viewer is applied."),
    ]
    params = [
        Param("matrix", "text", "", label="Position from the 3D viewer", advanced=True,
              help="Rotation and translation set with Move in the 3D viewer (4×4 matrix by rows). Empty: as it is."),
        Param("fit", "bool", True, label="Optimise the fit",
              help="Local rigid-body optimisation from the position (ChimeraX fitmap). Off: the position is applied as "
                   "it is, and scored."),
        Param("resolution", "float", 0.0, label="Resolution", unit="Å", min=0.0, max=60.0,
              help="Model: 0 fits the atoms (average map value at the atoms, ChimeraX's default); a resolution fits the "
                   "model's simulated map (molmap) with the metric below, better at low resolution."),
        Param("polish", "bool", True, label="Then fit the atoms",
              help="Model with a resolution: after the simulated map (which reaches further), a fit of the atoms for the "
                   "exact position."),
        Param("metric", "choice", "overlap", choices=["overlap", "correlation", "cam"], label="Metric",
              help="Maps and simulated maps: overlap (sum of products, ChimeraX's default), correlation about zero, or "
                   "correlation about the mean (cam)."),
        Param("search", "int", 0, label="Random placements", min=0, max=200,
              help="0: local fit from the position. N: global search from N random placements (rotations and shifts); "
                   "the best fit is written, the others listed."),
        Param("radius", "float", 0.0, label="Search radius", unit="Å", min=0.0,
              help="Random shifts within this distance of the position (0: anywhere in the density of the target)."),
        Param("chains", "str", "", label="Chains fitted", placeholder="A, B:10-250",
              help="Atoms used to fit (the whole model moves): one domain or one subunit. Empty: all the atoms."),
        Param("level", "float", 0.0, label="Contour of the map to place",
              help="Map or mask placed: its grid points above this level are fitted. 0: 0.5 for a mask, otherwise the "
                   "level with 1 % of the voxels above (ChimeraX's first contour)."),
        Param("target_level", "float", 0.0, label="Contour of the target map",
              help="Counts the atoms outside the density and bounds the search. 0: the level with 1 % of the voxels above."),
        Param("shift", "bool", True, label="Allow shifts", advanced=True),
        Param("rotate", "bool", True, label="Allow rotations", advanced=True),
        Param("seed", "int", 0, label="Random seed (search)", min=0, advanced=True),
        Param("output_name", "str", "", label="Output file name", advanced=True, path_kind="name",
              help="Empty: the input's name with _fitted (or _placed without a fit)."),
    ]
    outputs = [OutputDef("model", "model", "Placed model"), OutputDef("map", "map", "Placed map"),
               OutputDef("mask", "mask", "Placed mask")]

    @classmethod
    def validate(cls, params, connected):
        from cryoplug.masks import parse_selection
        problems = []
        if ("model" in connected) == ("moving_map" in connected):
            problems.append("Connect the model to place, or a map or mask to place (one of them)")
        fitting = params.get("fit", True) or int(params.get("search") or 0) > 0
        if fitting and "map" not in connected:
            problems.append("Connect the target map to fit into (or turn 'Optimise the fit' off to apply the position only)")
        if not fitting and not str(params.get("matrix") or "").strip():
            problems.append("Nothing to do: no position from the 3D viewer and no fit")
        if "model" in connected and not float(params.get("resolution") or 0) and params.get("metric", "overlap") != "overlap":
            problems.append("Correlation needs a resolution for a model (its simulated map); without, the atoms are fitted "
                            "by the average map value (overlap)")
        try:
            parse_matrix(params.get("matrix", ""))
        except ValueError as exc:
            problems.append(f"Position: {exc}")
        try:
            parse_selection(params.get("chains", ""))
        except ValueError as exc:
            problems.append(str(exc))
        return problems

    def run(self, ctx: JobContext) -> None:
        from cryoplug import fitting as ft
        from cryoplug.masks import select_atoms
        from cryoplug.modelio import read_structure, write_structure
        p = ctx.params
        start = parse_matrix(p["matrix"])
        model, moving, target_in = ctx.input("model"), ctx.input("moving_map"), ctx.input("map")
        fitting = bool(p["fit"]) or p["search"] > 0
        target = ft.Target(MapVolume.read(target_in.path)) if target_in else None
        level_t = None
        if target is not None:
            level_t = p["target_level"] or ft.default_level(target.vol)
        st = vol = None
        if model:
            st = read_structure(model.path)
            try:
                sel = select_atoms(st, p["chains"])
            except ValueError as exc:
                raise JobError(str(exc)) from None
            if not len(sel["xyz"]):
                raise JobError("No atom selected: check 'Chains fitted'")
            if sel["missing_chains"]:
                ctx.warn(f"Chain(s) not found in the model: {', '.join(sel['missing_chains'])}")
            if p["resolution"] > 0:
                points, weights = ft.simulated_points(sel["xyz"], sel["weights"], p["resolution"])
                what = f"simulated map of {len(sel['xyz']):,} atoms at {p['resolution']:g} Å"
            else:
                points, weights = sel["xyz"], None
                what = f"{len(sel['xyz']):,} atoms"
            metric = p["metric"] if p["resolution"] > 0 else "overlap"
        else:
            vol = MapVolume.read(moving.path)
            level = p["level"] or ft.default_level(vol)
            try:
                points, weights = ft.map_points(vol, level)
            except ValueError as exc:
                raise JobError(str(exc)) from None
            what = f"{len(points):,} grid points above {level:.4g}"
            metric = p["metric"]

        rows: list[dict] = []
        result = None
        if fitting:
            ctx.progress(0.1, f"Fitting {what} {'from ' + str(p['search']) + ' random placements' if p['search'] else 'from the position'}")
            t0 = time.time()
            try:
                result = ft.fit(points, weights, target, metric, start=start, search=p["search"], radius=p["radius"] or None,
                                level=level_t, shift=p["shift"], rotate=p["rotate"], seed=p["seed"],
                                workers=int(ctx.resources.get("num_cpus") or self.cpus))
            except ValueError as exc:
                raise JobError(str(exc)) from None
            final = result["matrix"]
            ctx.log(ft.message(model.source if model else moving.source, target_in.source, result))
            if model and p["resolution"] > 0 and p["polish"]:  # the exact position, from the atoms
                atoms = ft.fit(sel["xyz"], None, target, "overlap", start=final, level=level_t, shift=p["shift"], rotate=p["rotate"])
                ctx.log("Then on the atoms:\n" + ft.message(model.source, target_in.source, atoms))
                result["polish"] = atoms
                final = atoms["matrix"]
            ctx.log(f"Fit computed in {time.time() - t0:.1f} s")
        else:
            final = start
            if target is not None:
                result = {"matrix": final, "before": ft.evaluate(points, weights, target, np.eye(4), level_t),
                          "stats": ft.evaluate(points, weights, target, final, level_t)}

        # the placed object
        stem = (p["output_name"] or "").strip()
        suffix = "_fitted" if fitting else "_placed"
        if st is not None:
            ft.place_structure(st, final)
            src = model.path
            ext = ".pdb" if src.lower().endswith((".pdb", ".ent")) else ".cif"
            out = write_structure(st, ctx.path(f"{stem or _stem(src) + suffix}{ext}"))
            ctx.add_output("model", "model", out, f"Model {'fitted' if fitting else 'placed'} in {target_in.source if target_in else 'the viewer'}",
                           inherit=["map"] if target_in else ["model"], meta=self._meta(final, result, target_in))
        else:
            placed = ft.place_map(vol, final, target.vol if target is not None else None)
            kind = "mask" if moving.type == "mask" else "map"
            if kind == "mask":
                placed.data = np.clip(placed.data, 0.0, 1.0)
            out = placed.write(ctx.path(f"{stem or _stem(moving.path) + suffix}.mrc"))
            kept = float(placed.data.sum() * np.prod(placed.voxel)) / max(float(vol.data.sum() * np.prod(vol.voxel)), 1e-30)
            if vol.data.min() >= 0 and kept < 0.95:
                ctx.warn(f"Only {100 * kept:.0f} % of the {kind} lies inside the {'target map' if target is not None else 'box'} after the move")
            ctx.add_output(kind, kind, out, f"{kind.capitalize()} {'fitted' if fitting else 'placed'}"
                           f"{' on the grid of ' + target_in.source if target_in else ''}",
                           inherit=["map"] if target_in else ["moving_map"], meta=self._meta(final, result, target_in))
            if target is not None:
                grid = f"of {target_in.source} (resampled)"
            else:
                grid = "its own" + (" (origin shifted, no interpolation)" if ft.angle_of(final) < 1e-6 else " (resampled)")
            rows.append({"label": "Grid", "value": grid})
        self._report(ctx, start, final, result, rows, what, metric, target_in, level_t, np.asarray(points).mean(axis=0))

    @staticmethod
    def _meta(final: np.ndarray, result: dict | None, target_in) -> dict:
        from cryoplug import fitting as ft
        meta: dict = {"placement": ft.matrix_rows(final)}
        if result is not None:
            s = {**result["stats"], **({k: v for k, v in result["polish"]["stats"].items()
                                        if k in ("average_map_value", "outside_contour", "inside_fraction")} if result.get("polish") else {})}
            meta["fit"] = {k: (round(v, 5) if isinstance(v, float) else v) for k, v in s.items()
                           if k in ("average_map_value", "correlation", "cam", "overlap", "shift", "angle", "steps",
                                    "outside_contour", "inside_fraction", "points")}
        if target_in is not None:
            meta["fitted_in"] = target_in.path
        return meta

    @staticmethod
    def _report(ctx: JobContext, start, final, result, rows, what, metric, target_in, level_t, center) -> None:
        from cryoplug import fitting as ft
        total_shift, total_angle = ft.shift_and_angle(final, center)  # the centre of what was fitted
        rows = [{"label": "Fitted with", "value": what}, *rows]
        if result is not None:
            s, b = result["stats"], result["before"]
            if "correlation" in s:
                rows += [{"label": "Metric", "value": metric},
                         {"label": "Correlation", "value": f"{b['correlation']:.4f} → {s['correlation']:.4f}",
                          "status": "good" if s["correlation"] >= 0.7 else "warn"},
                         {"label": "Correlation about mean", "value": f"{b['cam']:.4f} → {s['cam']:.4f}"},
                         {"label": "Overlap", "value": f"{b['overlap']:.4g} → {s['overlap']:.4g}"}]
            rows.append({"label": "Average map value", "value": f"{b['average_map_value']:.4g} → {s['average_map_value']:.4g}"})
            if "outside_contour" in s and "correlation" not in s:  # atoms: those outside the density
                frac = s["outside_contour"] / max(s["points"], 1)
                rows.append({"label": "Atoms outside the contour", "value": f"{s['outside_contour']:,} of {s['points']:,} "
                             f"({100 * frac:.1f} %) at {s['contour_level']:.4g}", "status": "good" if frac < 0.1 else "warn"})
            if "steps" in s:
                rows.append({"label": "Optimisation", "value": f"{s['steps']} steps, moved {s['shift']:.2f} Å and "
                                                               f"{s['angle']:.2f}° from the position given"})
            if result.get("polish"):
                a, ab = result["polish"]["stats"], result["polish"]["before"]
                rows.append({"label": "Then on the atoms", "value": f"average map value {ab['average_map_value']:.4g} → "
                             f"{a['average_map_value']:.4g}, {a['steps']} steps, moved {a['shift']:.2f} Å and {a['angle']:.2f}°"})
                if "outside_contour" in a:
                    frac = a["outside_contour"] / max(a["points"], 1)
                    rows.append({"label": "Atoms outside the contour", "value": f"{a['outside_contour']:,} of {a['points']:,} ({100 * frac:.1f} %)",
                                 "status": "good" if frac < 0.1 else "warn"})
        if not ft.is_identity(start):
            rows.append({"label": "Position from the viewer",
                         "value": f"rotation {ft.angle_of(start):.2f}°, translation {np.linalg.norm(start[:3, 3]):.2f} Å"})
        rows.append({"label": "Placed", "value": f"centre moved {total_shift:.2f} Å, rotated {total_angle:.2f}° from the input file"})
        ctx.add_metrics("Fit", rows)
        if result is not None and result.get("fits"):
            ctx.add_table("Fits found by the search (best first)", ["#", "Score", "Times found", "Shift (Å)", "Angle (°)", "Inside contour"],
                          [[i + 1, f"{ft.score_of(f['stats']):.4f}", f["hits"], f"{f['stats']['shift']:.1f}", f"{f['stats']['angle']:.1f}",
                            f"{100 * f['stats'].get('inside_fraction', 1):.0f} %"] for i, f in enumerate(result["fits"][:20])])
        ctx.add_text("Transform (ChimeraX convention)", ft.describe(final), mono=True)
        if result is not None:
            s = result["stats"]
            if "correlation" in s:
                ctx.add_highlight("Correlation", f"{s['correlation']:.3f}", "good" if s["correlation"] >= 0.7 else "warn")
            else:
                ctx.add_highlight("Avg map value", f"{s['average_map_value']:.3g}")
            if result.get("polish"):
                s = result["polish"]["stats"]
            if "outside_contour" in s and "correlation" not in s:
                ctx.add_highlight("Inside", f"{100 * s.get('inside_fraction', 1):.0f} %",
                                  "good" if s.get("inside_fraction", 1) >= 0.9 else "warn")
        ctx.add_highlight("Moved", f"{total_shift:.1f} Å · {total_angle:.1f}°")


def _stem(path: str) -> str:
    from pathlib import Path
    name = Path(path).name
    for suf in (".mmcif", ".cif", ".pdb", ".ent", ".mrc", ".map", ".ccp4"):
        if name.lower().endswith(suf):
            return name[: -len(suf)]
    return Path(path).stem
