"""Built-in map jobs (no external dependency): FSC and map operations."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from cryoplug.jobs import register
from cryoplug.jobs.base import JobContext, JobError, JobType, OutputDef, Param, Slot
from cryoplug.mrc import MapVolume, crop_or_pad, flip_hand, fourier_filter, fsc_to_emdb_xml, half_map_fsc

CURVE_LABELS = {
    "unmasked": "No mask",
    "masked": "Masked",
    "phase_randomized": "Phase randomised",
    "corrected": "Corrected (gold standard)",
}


def compute_and_report_fsc(ctx: JobContext, half_a: str | Path, half_b: str | Path, mask: str | Path | None,
                           output_name: str = "fsc") -> float:
    """Compute the half-map FSC, write JSON/EMDB-XML files, add plot + output. Returns the best resolution."""
    a, b = MapVolume.read(half_a), MapVolume.read(half_b)
    if a.data.shape != b.data.shape:
        raise JobError(f"Half maps have different dimensions: {a.shape_xyz} vs {b.shape_xyz}")
    if not np.allclose(a.voxel, b.voxel, rtol=1e-3):
        ctx.warn(f"Half maps have different voxel sizes: {a.voxel} vs {b.voxel}")
    m = MapVolume.read(mask) if mask else None
    if m is not None and m.data.shape != a.data.shape:
        ctx.warn("Mask dimensions differ from the half maps: computing unmasked FSC only")
        m = None
    result = half_map_fsc(a, b, m)
    res = result["resolution"]
    ctx.log("FSC=0.143 resolution: " + ", ".join(f"{k} {v:.2f} Å" for k, v in res.items()))
    (ctx.path(f"{output_name}.json")).write_text(json.dumps(result))
    best_key = "corrected" if "corrected" in result["curves"] else "unmasked"
    xml = ctx.path(f"{output_name}_emdb.xml")
    xml.write_text(fsc_to_emdb_xml(result["frequency"], result["curves"][best_key], title=f"Half-map FSC ({best_key})"))
    series = [{"name": CURVE_LABELS.get(k, k), "x": result["frequency"], "y": v} for k, v in result["curves"].items()]
    ctx.add_plot("Half-map Fourier shell correlation", series, x_label="Resolution (Å)", y_label="FSC",
                 x_kind="resolution", hlines=[{"y": 0.143, "label": "0.143"}, {"y": 0.5, "label": "0.5"}],
                 y_range=[-0.1, 1.05])
    metrics = [{"label": f"Resolution ({CURVE_LABELS.get(k, k)})", "value": f"{v:.2f} Å"}
               for k, v in res.items() if k != "best"]
    if result.get("randomized_from"):
        metrics.append({"label": "Phases randomised beyond", "value": f"{result['randomized_from']:.2f} Å"})
    ctx.add_metrics("FSC summary", metrics)
    ctx.add_output(output_name, "fsc", [ctx.path(f"{output_name}.json"), xml], "Half-map FSC",
                   meta={"resolution": round(res["best"], 3), "curve": best_key})
    return float(res["best"])


@register
class HalfMapFSC(JobType):
    name = "map_fsc"
    title = "Half-map FSC"
    category = "Map processing"
    description = ("Gold-standard Fourier shell correlation between half maps: unmasked, masked and corrected by "
                   "high-resolution phase randomisation. Writes an EMDB-compatible FSC XML for deposition.")
    software = ["CryoPlug"]
    inputs = [Slot("half_maps", ("half_maps",), "Half maps"), Slot("mask", ("mask",), "Mask", required=False)]
    outputs = [OutputDef("fsc", "fsc", "FSC curve")]

    def run(self, ctx: JobContext) -> None:
        hm = ctx.require("half_maps")
        mask = ctx.input("mask")
        res = compute_and_report_fsc(ctx, hm.files[0], hm.files[1], mask.path if mask else None)
        ctx.add_highlight("Resolution", f"{res:.2f} Å")


@register
class MapTools(JobType):
    name = "map_tools"
    title = "Map operations"
    category = "Map processing"
    description = ("Built-in map manipulations applied in order: set pixel size, crop/pad, flip handedness, global "
                   "B-factor sharpening/blurring, low-pass filter, mask, normalisation.")
    software = ["CryoPlug"]
    inputs = [Slot("map", ("map",), "Map"), Slot("mask", ("mask",), "Mask", required=False)]
    params = [
        Param("set_pixel_size", "float", 0.0, label="Set pixel size", unit="Å", min=0.0, help="0 = keep."),
        Param("box", "int", 0, label="Crop/pad to box", unit="px", min=0, help="Centered cubic box. 0 = keep."),
        Param("flip_hand", "bool", False, label="Flip handedness"),
        Param("bfactor", "float", 0.0, label="Global B-factor", unit="Å²",
              help="Negative values sharpen, positive values blur. 0 = none."),
        Param("lowpass", "float", 0.0, label="Low-pass filter", unit="Å", min=0.0, help="0 = none."),
        Param("apply_mask", "bool", False, label="Multiply by mask"),
        Param("normalize", "bool", False, label="Normalise (mean 0, sd 1)"),
        Param("output_name", "str", "map_processed", label="Output file name", advanced=True),
    ]
    outputs = [OutputDef("map", "map", "Processed map")]

    @classmethod
    def validate(cls, params, connected):
        if params.get("apply_mask") and "mask" not in connected:
            return ["'Multiply by mask' needs a mask input"]
        return []

    def run(self, ctx: JobContext) -> None:
        inp = ctx.require("map")
        vol = MapVolume.read(inp.path)
        steps = []
        p = ctx.params
        if p["set_pixel_size"] > 0:
            vol.voxel = np.array([p["set_pixel_size"]] * 3, dtype=float)
            steps.append(f"pixel size {p['set_pixel_size']} Å")
        if p["box"] > 0:
            vol = crop_or_pad(vol, int(p["box"]))
            steps.append(f"box {p['box']}")
        if p["flip_hand"]:
            vol = vol.like(flip_hand(vol))
            steps.append("flipped hand")
        if p["bfactor"] or p["lowpass"]:
            vol = vol.like(fourier_filter(vol, bfactor=p["bfactor"], lowpass=p["lowpass"]))
            if p["bfactor"]:
                steps.append(f"B-factor {p['bfactor']} Å²")
            if p["lowpass"]:
                steps.append(f"low-pass {p['lowpass']} Å")
        if p["apply_mask"]:
            mask = MapVolume.read(ctx.require("mask").path)
            if mask.data.shape != vol.data.shape:
                raise JobError("Mask and map dimensions differ (apply the mask before cropping)")
            vol = vol.like(vol.data * np.clip(mask.data, 0, 1))
            steps.append("masked")
        if p["normalize"]:
            sd = float(vol.data.std()) or 1.0
            vol = vol.like((vol.data - vol.data.mean()) / sd)
            steps.append("normalised")
        if not steps:
            raise JobError("No operation selected")
        out = vol.write(ctx.path(f"{p['output_name'] or 'map_processed'}.mrc"))
        ctx.log("Applied: " + ", ".join(steps))
        meta = ctx.inherited_meta("map")
        if p["set_pixel_size"] > 0:
            meta.pop("resolution", None)
        if p["flip_hand"]:
            meta["hand_flipped"] = True
        ctx.add_output("map", "map", out, "Processed map", meta=meta)
        ctx.add_text("Operations", "\n".join(f"{i + 1}. {s}" for i, s in enumerate(steps)))
