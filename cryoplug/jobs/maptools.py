"""Built-in map jobs (no external dependency): FSC and map operations."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from cryoplug.jobs import register
from cryoplug.jobs.base import JobContext, JobError, JobType, OutputDef, Param, Slot
from cryoplug.mrc import (
    MapVolume, crop_or_pad, directional_fsc, flip_hand, fourier_filter, fsc_to_emdb_xml, half_map_fsc,
)

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


def fourier_resample(vol: MapVolume, pixel_size: float) -> MapVolume:
    """The map at another pixel size by Fourier cropping or padding (its physical size and origin kept; the box
    becomes the nearest even number of voxels, so the pixel size reached can differ slightly from the one asked)."""
    data = np.asarray(vol.data, dtype=np.float32)
    old = np.array(data.shape)  # z, y, x
    voxel_zyx = np.asarray(vol.voxel, dtype=float)[::-1]
    new = np.maximum(2, np.round(old * voxel_zyx / pixel_size / 2).astype(int) * 2)
    spec = np.fft.fftshift(np.fft.fftn(data))
    out = np.zeros(tuple(new), dtype=complex)
    src, dst = [], []
    for o, n in zip(old, new):
        m = min(o, n)
        src.append(slice(o // 2 - m // 2, o // 2 - m // 2 + m))
        dst.append(slice(n // 2 - m // 2, n // 2 - m // 2 + m))
    out[tuple(dst)] = spec[tuple(src)]
    res = np.real(np.fft.ifftn(np.fft.ifftshift(out))) * (np.prod(new) / np.prod(old))
    return MapVolume(data=res.astype(np.float32), voxel=(voxel_zyx * old / new)[::-1].copy(), origin=np.array(vol.origin, dtype=float))


def onto_grid(vol: MapVolume, like: MapVolume) -> MapVolume:
    """The map sampled on the grid of another (its box, pixel size and origin), by trilinear interpolation; 0 outside."""
    nz, ny, nx = like.data.shape
    axes = [like.origin[a] + like.voxel[a] * np.arange(n) for a, n in enumerate((nx, ny, nz))]
    z, y, x = np.meshgrid(axes[2], axes[1], axes[0], indexing="ij")
    values = vol.interpolate(np.stack([x.ravel(), y.ravel(), z.ravel()], 1), outside=0.0)
    return MapVolume(data=values.reshape(like.data.shape).astype(np.float32), voxel=np.array(like.voxel, dtype=float),
                     origin=np.array(like.origin, dtype=float))


@register
class MapTools(JobType):
    name = "map_tools"
    title = "Map operations"
    category = "Map processing"
    description = ("Built-in map manipulations applied in order, as CryoSPARC's Volume Tools: set pixel size, resample "
                   "(to another pixel size, by Fourier cropping or padding, or onto the grid of another map), crop/pad, "
                   "flip handedness, global B-factor sharpening/blurring, low-pass filter, mask, normalisation.")
    software = ["CryoPlug"]
    inputs = [Slot("map", ("map",), "Map"), Slot("mask", ("mask",), "Mask", required=False),
              Slot("reference", ("map", "mask"), "Grid of", required=False,
                   help="Resample the map onto the grid (box, pixel size, origin) of this map: to compare or combine them.")]
    params = [
        Param("set_pixel_size", "float", 0.0, label="Set pixel size", unit="Å", min=0.0,
              help="Changes the header only (a wrong calibration). 0 = keep."),
        Param("resample_pixel_size", "float", 0.0, label="Resample to pixel size", unit="Å", min=0.0,
              help="Fourier cropping (larger pixels) or padding (smaller), the physical size kept. 0 = keep."),
        Param("box", "int", 0, label="Crop/pad to box", unit="px", min=0, help="Centered cubic box. 0 = keep."),
        Param("flip_hand", "bool", False, label="Flip handedness"),
        Param("bfactor", "float", 0.0, label="Global B-factor", unit="Å²",
              help="Negative values sharpen, positive values blur. 0 = none."),
        Param("lowpass", "float", 0.0, label="Low-pass filter", unit="Å", min=0.0, help="0 = none."),
        Param("apply_mask", "bool", False, label="Multiply by mask"),
        Param("normalize", "bool", False, label="Normalise (mean 0, sd 1)"),
        Param("output_name", "str", "map_processed", label="Output file name", advanced=True, path_kind="name"),
    ]
    outputs = [OutputDef("map", "map", "Processed map")]

    @classmethod
    def validate(cls, params, connected):
        if params.get("apply_mask") and "mask" not in connected:
            return ["'Multiply by mask' needs a mask input"]
        if params.get("resample_pixel_size") and "reference" in connected:
            return ["Resample either to a pixel size or onto the grid of another map, not both"]
        return []

    def run(self, ctx: JobContext) -> None:
        inp = ctx.require("map")
        vol = MapVolume.read(inp.path)
        steps = []
        p = ctx.params
        if p["set_pixel_size"] > 0:
            vol.voxel = np.array([p["set_pixel_size"]] * 3, dtype=float)
            steps.append(f"pixel size {p['set_pixel_size']} Å")
        if p["resample_pixel_size"] > 0:
            before = vol.shape_xyz
            vol = fourier_resample(vol, float(p["resample_pixel_size"]))
            steps.append(f"resampled from {before[0]} to {vol.shape_xyz[0]} px ({vol.voxel[0]:.4f} Å/px)")
        reference = ctx.input("reference")
        if reference is not None:
            like = MapVolume.read(reference.path)
            vol = onto_grid(vol, like)
            steps.append(f"on the grid of {reference.source} ({like.shape_xyz[0]} px, {like.voxel[0]:.4f} Å/px)")
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
        if p["resample_pixel_size"] > 0 and meta.get("resolution") and meta["resolution"] < 2 * float(vol.voxel[0]):
            meta["resolution"] = round(2 * float(vol.voxel[0]), 3)  # no detail beyond the new Nyquist limit
        if p["flip_hand"]:
            meta["hand_flipped"] = True
        ctx.add_output("map", "map", out, "Processed map", meta=meta)
        ctx.add_text("Operations", "\n".join(f"{i + 1}. {s}" for i, s in enumerate(steps)))


@register
class DirectionalFSC(JobType):
    name = "directional_fsc"
    title = "Directional resolution (3D FSC)"
    category = "Map processing"
    software = ["CryoPlug"]
    cpus = 2
    description = ("Conical FSC in every direction (3D FSC, Tan et al. 2017): reveals the resolution anisotropy caused by "
                   "preferred orientation, with the best / worst directions and a direction map. Strong anisotropy: "
                   "consider spIsoNet, tilted data collection or orientation rebalancing.")
    inputs = [Slot("half_maps", ("half_maps",), "Half maps"), Slot("mask", ("mask",), "Mask", required=False)]
    params = [
        Param("cone_angle", "float", 20.0, label="Cone half-angle", unit="°", min=5.0, max=45.0),
        Param("sampling", "float", 15.0, label="Direction sampling", unit="°", min=5.0, max=30.0),
        Param("threshold", "float", 0.143, label="FSC threshold", min=0.01, max=0.99),
        Param("max_box", "int", 256, label="Maximum box", unit="px", min=64, advanced=True,
              help="Larger maps are cropped around the centre to limit memory use."),
    ]
    outputs = [OutputDef("report", "report", "Directional FSC")]

    def run(self, ctx: JobContext) -> None:
        hm = ctx.require("half_maps")
        a, b = MapVolume.read(hm.files[0]), MapVolume.read(hm.files[1])
        if a.data.shape != b.data.shape:
            raise JobError("Half maps have different dimensions")
        mask_in = ctx.input("mask")
        m = MapVolume.read(mask_in.path) if mask_in else None
        if m is not None and m.data.shape != a.data.shape:
            ctx.warn("Mask dimensions differ from the half maps: ignoring the mask")
            m = None
        box = int(ctx.params["max_box"])
        if max(a.shape_xyz) > box:
            ctx.warn(f"Box {a.shape_xyz} cropped to {box}^3 around the centre")
            a, b = crop_or_pad(a, box), crop_or_pad(b, box)
            m = crop_or_pad(m, box) if m is not None else None
        ctx.progress(0.1, "Computing conical FSCs")
        r = directional_fsc(a.data, b.data, a.voxel, m.data if m is not None else None,
                            half_angle=ctx.params["cone_angle"], az_step=ctx.params["sampling"],
                            el_step=ctx.params["sampling"], threshold=ctx.params["threshold"])
        dirs = r["directions"]
        res = np.array([d["resolution"] for d in dirs])
        best, worst = dirs[int(np.argmin(res))], dirs[int(np.argmax(res))]
        ratio = float(res.max() / res.min())
        freqs = r["frequency"].tolist()
        ctx.add_plot("Global and extreme directional FSC", [
            {"name": "Global", "x": freqs, "y": r["global"].tolist()},
            {"name": f"Best direction (az {best['azimuth']:.0f}°, el {best['elevation']:.0f}°)", "x": freqs,
             "y": best["fsc"].tolist()},
            {"name": f"Worst direction (az {worst['azimuth']:.0f}°, el {worst['elevation']:.0f}°)", "x": freqs,
             "y": worst["fsc"].tolist()},
        ], x_label="Resolution (Å)", y_label="FSC", x_kind="resolution",
            hlines=[{"y": ctx.params["threshold"], "label": f"{ctx.params['threshold']:g}"}], y_range=[-0.1, 1.05])
        lookup = {(d["azimuth"], d["elevation"]): d["resolution"] for d in dirs}
        elevations = sorted(r["elevations"], reverse=True)
        rows = [[round(lookup.get((az, el), lookup.get((0.0, el), float("nan"))), 2) for az in r["azimuths"]]
                for el in elevations]
        ctx.add_heatmap("Resolution by direction", [f"{az:.0f}°" for az in r["azimuths"]], [f"{el:.0f}°" for el in elevations],
                        rows, unit="Å", x_label="Azimuth", y_label="Elevation",
                        note="Lower is better. Elevation 90° is the map z axis; opposite directions are equivalent.")
        status = "good" if ratio <= 1.25 else "warn" if ratio <= 1.6 else "bad"
        ctx.add_metrics("Directional resolution", [
            {"label": "Global resolution", "value": f"{r['global_resolution']:.2f} Å"},
            {"label": "Best direction",
             "value": f"{best['resolution']:.2f} Å (az {best['azimuth']:.0f}°, el {best['elevation']:.0f}°)"},
            {"label": "Worst direction",
             "value": f"{worst['resolution']:.2f} Å (az {worst['azimuth']:.0f}°, el {worst['elevation']:.0f}°)"},
            {"label": "Mean directional resolution", "value": f"{float(res.mean()):.2f} Å"},
            {"label": "Anisotropy (worst / best)", "value": f"{ratio:.2f}", "status": status, "target": "≤ 1.25"},
            {"label": "Cone half-angle", "value": f"{ctx.params['cone_angle']:g}°"},
        ])
        ctx.add_highlight("Directional", f"{best['resolution']:.2f}–{worst['resolution']:.2f} Å")
        ctx.add_highlight("Anisotropy", f"{ratio:.2f}", status)
        out = ctx.path("directional_fsc.json")
        out.write_text(json.dumps({
            "global_resolution": r["global_resolution"], "anisotropy": ratio, "cone_angle": r["half_angle"],
            "directions": [{k: d[k] for k in ("azimuth", "elevation", "resolution")} for d in dirs]}, indent=1))
        ctx.add_output("report", "report", out, "Directional FSC",
                       meta={"anisotropy": round(ratio, 3), "best": round(best["resolution"], 3),
                             "worst": round(worst["resolution"], 3)})
