"""Local resolution and composite maps (Phenix), for large complexes solved by local refinements."""
from __future__ import annotations

import csv
import time
from pathlib import Path
from typing import Any

from cryoplug.jobs import register
from cryoplug.jobs.base import (
    InputData,
    JobContext,
    JobError,
    JobType,
    OutputDef,
    Param,
    Slot,
    extra_args_param,
    resolution_param,
)
from cryoplug.jobs.sharpening import pick_new_map
from cryoplug.mrc import MapVolume


def report_local_resolution(ctx: JobContext, path: Path, mask: InputData | None = None, model: InputData | None = None,
                            density: str | None = None) -> dict[str, Any]:
    """Statistics of a local resolution map in the molecule, per chain and per residue (with a model); returns
    the output metadata (median, colour range of the 3D viewer)."""
    from cryoplug import locres as lr
    from cryoplug.modelio import atom_arrays, read_structure, write_structure
    vol = MapVolume.read(path)
    st = read_structure(model.path) if model else None
    m = MapVolume.read(mask.path) if mask else None
    if m is not None and m.data.shape != vol.data.shape:
        ctx.warn(f"The mask box {m.shape_xyz} differs from the map box {vol.shape_xyz}: statistics taken without it")
        m = None
    xyz = atom_arrays(st)["xyz"] if st is not None and m is None else None
    dens = MapVolume.read(density) if density and m is None and xyz is None else None
    region, how = lr.region(vol, m, xyz, dens)
    values = vol.data[region] if region is not None else vol.data
    stats = lr.summary(values)
    if not stats.get("n"):
        raise JobError("The local resolution map has no value between 0 and 100 Å in the molecule (see the log).")
    if not 1.0 <= stats["median"] <= 30.0:
        ctx.warn(f"{path.name} does not look like a local resolution map (median {stats['median']:.3g} in the molecule): "
                 "check which file the program wrote.")
    ctx.add_metrics(f"Local resolution {how}", [
        {"label": "Median", "value": f"{stats['median']:.2f} Å"},
        {"label": "Best 5 %", "value": f"≤ {stats['p5']:.2f} Å"},
        {"label": "Worst 5 %", "value": f"≥ {stats['p95']:.2f} Å"},
        {"label": "Middle 50 %", "value": f"{stats['p25']:.2f} – {stats['p75']:.2f} Å"},
        {"label": "Voxels", "value": f"{stats['n']:,}"},
    ])
    hist = lr.histogram(values)
    ctx.add_plot("Distribution of the local resolution", [{"name": "Voxels (%)", "x": hist["x"], "y": hist["y"]}],
                 x_label="Local resolution (Å)", y_label="% of the voxels")
    meta: dict[str, Any] = {"median": round(stats["median"], 3), "display_range": lr.display_range(stats)}
    if st is not None:
        rows = lr.per_residue(vol, st)
        chains = lr.per_chain(rows)
        if chains:
            ctx.add_table("Local resolution per chain (at the CA / P atoms, best first)",
                          ["Chain", "Residues", "Median (Å)", "Best 10 % (Å)", "Worst 10 % (Å)"],
                          [[c["chain"], c["n"], f"{c['median']:.2f}", f"{c['best']:.2f}", f"{c['worst']:.2f}"] for c in chains])
            worst = sorted((r for r in rows if r["value"] is not None), key=lambda r: -r["value"])[:25]
            ctx.add_table("Least resolved residues", ["Chain", "Residue", "Local resolution (Å)"],
                          [[r["chain"], f"{r['name']} {r['num']}{r['icode']}", f"{r['value']:.2f}"] for r in worst])
            table = ctx.path("local_resolution_per_residue.csv")
            with open(table, "w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["chain", "residue", "insertion", "name", "local_resolution_A"])
                for r in rows:
                    w.writerow([r["chain"], r["num"], r["icode"], r["name"], "" if r["value"] is None else f"{r['value']:.3f}"])
            coloured = write_structure(lr.with_bfactors(st, rows), ctx.path("model_local_resolution.cif"))
            ctx.add_output("per_residue", "report", [table, coloured],
                           "Local resolution per residue (CSV, and the model with it in the B-factor column)")
            ctx.log("model_local_resolution.cif carries the local resolution in its B-factor column (colour by B-factor "
                    "in ChimeraX, Coot or PyMOL)")
    ctx.add_highlight("Local res.", f"{stats['median']:.2f} Å")
    ctx.add_highlight("5–95 %", f"{stats['p5']:.1f}–{stats['p95']:.1f} Å")
    return meta


@register
class LocalResolution(JobType):
    name = "local_resolution"
    title = "Local resolution"
    category = "Map processing"
    tool = "phenix"
    software = ["Phenix local_resolution"]
    cpus = 4
    cpu_param = "nproc"
    description = ("Local resolution map from the two half maps (phenix.local_resolution), its distribution in the "
                   "molecule and, with a model, per chain and per residue. The 3D viewer colours a map by it.")
    inputs = [
        Slot("half_maps", ("half_maps",), "Half maps", help="Unfiltered, unmasked half maps (independent)."),
        Slot("map", ("map",), "Map to colour", required=False,
             help="Map shown coloured by local resolution in the 3D viewer (default: the sharpened map of the half maps' job)."),
        Slot("mask", ("mask",), "Mask", required=False, help="Statistics inside this mask (e.g. the FSC mask)."),
        Slot("model", ("model",), "Model", required=False, help="Local resolution of every chain and residue."),
    ]
    params = [
        Param("method", "choice", "fft", choices=["fft", "boxes"], label="Method",
              help="fft: one-step FFT-based estimate (Phenix default) · boxes: FSC in small boxes, then smoothed."),
        Param("fsc_cutoff", "float", 0.143, label="FSC threshold", min=0.01, max=0.99, advanced=True),
        Param("nproc", "int", 4, label="Processors", min=1),
        extra_args_param(),
    ]
    outputs = [OutputDef("local_resolution", "locres", "Local resolution map"),
               OutputDef("per_residue", "report", "Local resolution per residue")]

    def run(self, ctx: JobContext) -> None:
        hm = ctx.require("half_maps")
        p = ctx.params
        args = [ctx.program("phenix", "phenix.local_resolution"), hm.files[0], hm.files[1], f"nproc={p['nproc']}"]
        if p["method"] != "fft":
            args.append(f"method={p['method']}")
        if abs(p["fsc_cutoff"] - 0.143) > 1e-6:
            args.append(f"fsc_cutoff={p['fsc_cutoff']:g}")
        args += ctx.split_extra()
        start = time.time()
        ctx.run(args, tool="phenix")
        out = pick_new_map(ctx, start, ["*local_resolution*", "*local_res*", "*locres*"])
        shown = ctx.input("map") or hm.sibling("map_sharp", "map", type="map")
        meta = report_local_resolution(ctx, out, ctx.input("mask"), ctx.input("model"),
                                       density=shown.path if shown else hm.files[0])
        meta = {**ctx.inherited_meta("half_maps"), **meta}
        meta.pop("resolution", None)  # a resolution map, not a map at this resolution
        if shown:
            meta["colour_map"] = ctx.rel(shown.path)
        ctx.add_output("local_resolution", "locres", out, "Local resolution map", meta=meta)


MAX_FOCUSED = 8


@register
class CompositeMap(JobType):
    name = "composite_map"
    title = "Composite map (focused maps)"
    category = "Map processing"
    tool = "phenix"
    software = ["Phenix combine_focused_maps"]
    cpus = 4
    description = ("Combines the maps of local (focused) refinements into one composite map with "
                   "phenix.combine_focused_maps: each part of the model takes the map that fits it best. The half maps "
                   "of every map are combined the same way when available, for the FSC and the local resolution of the "
                   "composite.")
    inputs = [
        Slot("model", ("model",), "Model",
             help="Fitted in the reference map; its chains decide which map is used where (rigid-body fitted to each map)."),
        Slot("reference", ("map",), "Reference map", help="Consensus map: all maps are superimposed on it."),
        Slot("focused_1", ("map",), "Focused map 1", help="Map of a local refinement (e.g. a CryoSPARC import).",
             group="focused"),
        *(Slot(f"focused_{i}", ("map",), f"Focused map {i}", required=False, group="focused") for i in range(2, MAX_FOCUSED + 1)),
    ]
    params = [
        resolution_param(),
        Param("use_half_maps", "bool", True, label="Combine the half maps too",
              help="Takes the half maps produced by the same jobs as the maps (CryoSPARC imports, refinements)."),
        extra_args_param(),
    ]
    outputs = [OutputDef("map", "map", "Composite map"), OutputDef("half_maps", "half_maps", "Composite half maps")]

    def run(self, ctx: JobContext) -> None:
        from cryoplug.jobs.sharpening import MAP_PATTERNS
        model, reference = ctx.require("model"), ctx.require("reference")
        maps = [reference] + [m for i in range(1, MAX_FOCUSED + 1) if (m := ctx.input(f"focused_{i}"))]
        sources = [m.source for m in maps]
        if len(set(sources)) < len(sources):
            raise JobError("The same map is connected twice")
        res = ctx.resolution(slots=["reference", "focused_1", "model"])
        args = [ctx.program("phenix", "phenix.combine_focused_maps")] + [f"map_file={m.path}" for m in maps]
        args.append(f"model_file={model.path}")
        halves: list[InputData | None] = []
        if ctx.params["use_half_maps"]:
            halves = [m.sibling("half_maps", type="half_maps") for m in maps]
            missing = [m.source for m, hm in zip(maps, halves) if hm is None]
            if missing:
                ctx.warn(f"No half maps found next to {', '.join(missing)}: the composite half maps are not computed.")
                halves = []
            for hm in halves:
                args += [f"half_map_1_file={hm.files[0]}", f"half_map_2_file={hm.files[1]}"]
        args += [f"resolution={res}", *ctx.split_extra()]
        ctx.add_table("Maps combined", ["", "Map", "Half maps"],
                      [["reference" if i == 0 else f"focused {i}", m.source,
                        halves[i].source if halves else "–"] for i, m in enumerate(maps)])
        start = time.time()
        ctx.run(args, tool="phenix")
        composite = pick_new_map(ctx, start, ["*composite*", "combined_map*", "*combined*"], exclude=("contribution", "half"))
        meta = {**ctx.inherited_meta("reference"), "composite_of": sources}
        ctx.add_output("map", "map", composite, f"Composite of {len(maps)} maps", meta=meta)
        if halves:
            found = sorted(p for p in ctx.find_new_files(MAP_PATTERNS, start)
                           if "half" in p.name.lower() and "contribution" not in p.name.lower() and not p.is_symlink())
            if len(found) == 2:
                ctx.add_output("half_maps", "half_maps", found, "Composite half maps", meta=meta)
            else:
                ctx.warn(f"Composite half maps not identified among: {', '.join(p.name for p in found) or 'no file'}")
        contributions = sorted(p.name for p in ctx.find_new_files(MAP_PATTERNS, start) if "contribution" in p.name.lower())
        if contributions:
            ctx.log(f"Contribution of each map (in the job folder): {', '.join(contributions)}")
        ctx.add_highlight("Maps", len(maps))
