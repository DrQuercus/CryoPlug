"""Volume series (3D variability frames, cryoDRGN clusters and trajectories): import, analysis, extraction."""
from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path
from typing import Any

import numpy as np

from cryoplug.jobs import register
from cryoplug.jobs.base import JobContext, JobError, JobType, OutputDef, Param, Slot
from cryoplug.mrc import MapVolume, bin_map

MAP_SUFFIXES = (".mrc", ".map", ".ccp4")
ANALYSIS_BOX = 96  # frames are compared on grids of at most this many voxels per side


# ---------------------------------------------------------------- helpers
def natural_key(text: str) -> list:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", text)]


def add_series(ctx: JobContext, name: str, files: list[Path], label: str, frame_labels: list[str] | None = None,
               meta: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Register an ordered list of volumes as a volume_series output (frame labels in the metadata)."""
    files = [Path(f) for f in files]
    if not files:
        return None
    labels = frame_labels or [f"Frame {i + 1}" for i in range(len(files))]
    return ctx.add_output(name, "volume_series", files, label, meta={"frames": len(files), "frame_labels": labels, **(meta or {})})


def short_labels(labels: list[str]) -> list[str]:
    """The part of the frame labels that tells the frames apart: 'Cluster 4 · 1.4 %' -> 'Cluster 4',
    'PC1 · 3/10' -> '3/10', 'Component 0 · frame 3' -> 'frame 3'."""
    parts = [str(lab).split(" · ") for lab in labels]
    width = max((len(p) for p in parts), default=0)
    columns = [[p[k] if k < len(p) else "" for p in parts] for k in range(width)]
    best = next((c for c in columns if len(set(c)) == len(c)), None) or next((c for c in columns if len(set(c)) > 1), None)
    return [lab or f"Frame {i + 1}" for i, lab in enumerate(best)] if best else [f"Frame {i + 1}" for i in range(len(labels))]


def load_frames(files: list[str | Path], max_box: int = ANALYSIS_BOX) -> tuple[list[MapVolume], int]:
    """The frames of a series, binned to at most ``max_box`` voxels per side (same factor for all)."""
    first = MapVolume.read(files[0], header_only=True)
    factor = max(1, int(np.ceil(max(first.shape_xyz) / max_box)))
    vols = []
    for f in files:
        v = MapVolume.read(f)
        if v.shape_xyz != first.shape_xyz:
            raise JobError(f"{Path(f).name} has a {v.shape_xyz} box, the first frame {first.shape_xyz}: frames must match")
        vols.append(bin_map(v, factor) if factor > 1 else v)
    return vols, factor


def molecule_region(mean: np.ndarray) -> np.ndarray:
    """Voxels of the molecule in the mean of the frames (Otsu level of the low-pass mean)."""
    from cryoplug import masks as mk
    level = mk.otsu_threshold(mk.sample_values(mean))
    region = mean >= level
    return region if region.sum() >= 50 else mean >= np.percentile(mean, 90)


def normalised(data: np.ndarray, region: np.ndarray) -> np.ndarray:
    """Frame scaled to mean 0 and SD 1 inside the region (frames from different jobs become comparable)."""
    vals = data[region]
    sd = float(vals.std()) or 1.0
    return (data - float(vals.mean())) / sd


def correlations(stack: np.ndarray, region: np.ndarray) -> np.ndarray:
    """Pearson correlation between frames inside the region."""
    x = stack[:, region].astype(np.float64)
    x -= x.mean(axis=1, keepdims=True)
    x /= np.maximum(np.linalg.norm(x, axis=1, keepdims=True), 1e-12)
    return np.clip(x @ x.T, -1.0, 1.0)


def similarity_order(corr: np.ndarray) -> list[int]:
    """Frames reordered so that similar ones are neighbours (greedy nearest-neighbour chain)."""
    n = len(corr)
    if n < 3:
        return list(range(n))
    start = int(np.argmin(corr.sum(axis=1)))  # an end of the chain: the least typical frame
    order, left = [start], set(range(n)) - {start}
    while left:
        nxt = max(left, key=lambda j: corr[order[-1], j])
        order.append(nxt)
        left.remove(nxt)
    return order


def frame_strip(stack: np.ndarray, labels: list[str], size: int = 96, per_row: int = 10) -> np.ndarray:
    """Projections of the frames side by side (contact sheet), grey levels shared by all frames."""
    proj = stack.mean(axis=1)  # (n, y, x) projection along z
    lo, hi = np.percentile(proj, [1, 99.7])
    n = len(proj)
    rows = int(np.ceil(n / per_row))
    cols = min(n, per_row)
    tile = size + 4
    sheet = np.full((rows * tile, cols * tile), 255, dtype=np.uint8)
    for i, p in enumerate(proj):
        h, w = p.shape
        scale = size / max(h, w)
        yy = np.clip((np.arange(int(h * scale)) / scale).astype(int), 0, h - 1)
        xx = np.clip((np.arange(int(w * scale)) / scale).astype(int), 0, w - 1)
        img = np.clip((p[::-1][yy][:, xx] - lo) / ((hi - lo) or 1.0), 0, 1)
        r, c = divmod(i, per_row)
        sheet[r * tile + 2: r * tile + 2 + img.shape[0], c * tile + 2: c * tile + 2 + img.shape[1]] = (img * 255).astype(np.uint8)
    return sheet


# ---------------------------------------------------------------- import
def group_series(files: list[Path]) -> dict[str, list[Path]]:
    """Group volume files into series: by 'component N' / 'series N' in their name (CryoSPARC 3D variability
    display), otherwise by folder; frames ordered by their number."""
    groups: dict[str, list[Path]] = {}
    for f in files:
        m = re.search(r"(component|comp|series|traj(?:ectory)?|pc|body)[_-]?(\d+)", f.stem, re.I)
        key = f"{m.group(1).lower()} {int(m.group(2))}" if m else (f.parent.name or "series")
        groups.setdefault(key, []).append(f)
    for key in groups:
        groups[key].sort(key=lambda p: natural_key(re.sub(r"(component|comp|series|traj(?:ectory)?|pc|body)[_-]?\d+", "", p.name, flags=re.I)))
    return dict(sorted(groups.items(), key=lambda kv: natural_key(kv[0])))


@register
class ImportVolumeSeries(JobType):
    name = "import_volume_series"
    title = "Import volume series"
    category = "Import"
    software = ["CryoSPARC"]
    description = ("Series of volumes from CryoSPARC 3D Variability Display, 3D Flex Generate, cryoDRGN, RELION "
                   "multi-body or any program: a folder, a ZIP file or a file pattern. One series per variability "
                   "component; play them in the 3D viewer and analyse them with 'Volume series analysis'.")
    params = [
        Param("path", "path", "", label="Folder, ZIP file or pattern", required=True, path_kind="any",
              placeholder="/data/CS-proj/J88  or  .../J88_component_000.zip  or  .../frame_*.mrc",
              help="Volumes are grouped by 'component N' (or 'series N') in their names, otherwise by folder."),
        Param("pixel_size", "float", 0.0, label="Pixel size", unit="Å", min=0.0, advanced=True,
              help="0 = keep the pixel size of the files."),
        Param("link_mode", "choice", "copy", choices=["copy", "symlink"], label="Copy or link files", advanced=True),
    ]
    outputs = [OutputDef("series", "volume_series", "Volume series")]

    def run(self, ctx: JobContext) -> None:
        import glob
        raw = str(Path(ctx.params["path"]).expanduser())
        src = Path(raw)
        if src.is_dir():
            files = [p for p in src.rglob("*") if p.suffix.lower() in MAP_SUFFIXES and p.is_file()]
        elif src.suffix.lower() == ".zip" and src.is_file():
            out = ctx.path("unzipped")
            with zipfile.ZipFile(src) as zf:
                for member in zf.infolist():
                    target = (out / member.filename).resolve()
                    if out.resolve() not in target.parents or member.is_dir():
                        continue  # paths escaping the folder, folders
                    if target.suffix.lower() in MAP_SUFFIXES:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(member) as fin, open(target, "wb") as fout:
                            shutil.copyfileobj(fin, fout)
            files = [p for p in out.rglob("*") if p.suffix.lower() in MAP_SUFFIXES]
        else:
            files = [Path(p) for p in glob.glob(raw) if Path(p).suffix.lower() in MAP_SUFFIXES]
        files = [f for f in files if not re.search(r"half|mask", f.name, re.I)]
        if len(files) < 2:
            raise JobError(f"Fewer than two volumes found in {raw}")
        groups = group_series(files)
        rows = []
        for i, (key, members) in enumerate(groups.items(), start=1):
            if len(members) < 2:
                ctx.warn(f"'{key}': a single volume, not a series ({members[0].name})")
                continue
            local = self._stage(ctx, key, members)
            name = "series" if i == 1 else f"series_{i}"
            labels = [f"{key.capitalize()} · frame {j + 1}" for j in range(len(local))]
            add_series(ctx, name, local, f"{key.capitalize()} ({len(local)} frames)", labels, {"kind": key, "source": raw})
            rows.append([name, key, len(local), members[0].name, members[-1].name])
        if not rows:
            raise JobError("No series of at least two volumes found")
        ctx.add_table("Series imported", ["Output", "Group", "Frames", "First", "Last"], rows)
        ctx.add_highlight("Series", len(rows))

    @staticmethod
    def _stage(ctx: JobContext, key: str, members: list[Path]) -> list[Path]:
        folder = ctx.path(re.sub(r"\W+", "_", key).strip("_") or "series")
        folder.mkdir(exist_ok=True)
        apix = ctx.params["pixel_size"]
        out = []
        for j, src in enumerate(members):
            dst = folder / f"frame_{j + 1:03d}{src.suffix.lower()}"
            if apix > 0:
                vol = MapVolume.read(src)
                vol.voxel = np.array([apix] * 3)
                vol.write(dst)
            elif ctx.params["link_mode"] == "symlink":
                dst.symlink_to(src.resolve())
            else:
                shutil.copy2(src, dst)
            out.append(dst)
        return out


# ---------------------------------------------------------------- analysis
def chain_regions(grid: MapVolume, st) -> tuple[list[tuple[str, np.ndarray]], np.ndarray]:
    """Voxels near each chain of the model, and the solvent (voxels farther than 8 Å from every atom)."""
    from cryoplug.mrc import zone_mask
    chains, everything = [], []
    for chain in st[0]:
        xyz = np.array([(a.pos.x, a.pos.y, a.pos.z) for r in chain for a in r if a.element.name not in ("H", "D")])
        if not len(xyz):
            continue
        everything.append(xyz)
        region = zone_mask(grid, xyz, max(3.0, float(np.mean(grid.voxel))))
        if region.sum() >= 3:
            chains.append((chain.name, region))
    solvent = ~zone_mask(grid, np.concatenate(everything), 8.0) if everything else np.zeros(grid.data.shape, bool)
    return chains, solvent


def chain_densities(frames: np.ndarray, chains: list[tuple[str, np.ndarray]], solvent: np.ndarray) -> np.ndarray:
    """Density of each chain in each frame above the solvent level, relative to the whole model in that frame:
    about 1 for a chain as dense as the rest, about 0 for a chain absent from that frame."""
    whole = np.any([r for _n, r in chains], axis=0)
    out = np.full((len(chains), len(frames)), np.nan)
    for f, frame in enumerate(frames):
        background = float(np.median(frame[solvent])) if solvent.sum() >= 100 else 0.0
        reference = float(frame[whole].mean()) - background
        if reference <= 0:
            continue
        for c, (_name, region) in enumerate(chains):
            out[c, f] = (float(frame[region].mean()) - background) / reference
    return out


def per_chain_table(ctx: JobContext, frames: np.ndarray, grid: MapVolume, model_path: str, labels: list[str],
                    variability: np.ndarray) -> None:
    """Density of every chain in every frame (raw frames, relative to the whole model) and its variability: a
    chain fading in some frames is absent from part of the particles, or moves away from its place in the model."""
    from cryoplug.modelio import read_structure
    chains, solvent = chain_regions(grid, read_structure(model_path))
    if not chains:
        ctx.warn("No chain of the model lies in the box of the series: per-chain analysis skipped")
        return
    occ = chain_densities(frames, chains, solvent)
    if np.isnan(occ).all():
        ctx.warn("The frames have no density above the solvent level at the model: per-chain analysis skipped")
        return
    ctx.add_heatmap("Density of each chain in each frame (relative to the whole model)",
                    labels, [n for n, _r in chains], np.where(np.isnan(occ), None, occ.round(3)).tolist(), unit="",
                    x_label="Frame", y_label="Chain",
                    note="1 = as dense as the model on average, 0 = no density. A chain fading in some frames is absent from "
                         "part of the particles (compositional heterogeneity), or moves away from its place in the model.")
    rows = []
    for (name, region), row in zip(chains, occ):
        lo, hi = float(np.nanmin(row)), float(np.nanmax(row))
        rows.append([name, f"{float(variability[region].mean()):.3f}", f"{lo:.2f} – {hi:.2f}",
                     "fades in some frames" if hi > 0.3 and lo < 0.5 * hi else ""])
    rows.sort(key=lambda r: -float(r[1]))
    ctx.add_table("Chains, most variable first", ["Chain", "Mean variability (σ)", "Relative density (min – max)", "Note"], rows)


@register
class SeriesAnalysis(JobType):
    name = "series_analysis"
    title = "Volume series analysis"
    category = "Heterogeneity"
    software = ["CryoPlug"]
    cpus = 2
    description = ("Interprets a volume series (3D variability frames, cryoDRGN clusters or trajectories): variability "
                   "map (where the density changes), mean and difference maps, similarity between frames and, with a "
                   "model, the chains that move or come and go.")
    inputs = [
        Slot("series", ("volume_series",), "Volume series", prefer=("kmeans", "series", "trajectory")),
        Slot("model", ("model",), "Model", required=False, help="Fitted in the frames: density and variability of each chain."),
        Slot("mask", ("mask",), "Mask", required=False, help="Region analysed (default: the molecule found in the mean of the frames)."),
    ]
    params = [
        Param("full_size", "bool", False, label="Full-size output maps", advanced=True,
              help="Write the variability, mean and difference maps at the size of the frames (default: binned like the analysis "
                   "when the frames are larger than 96 voxels)."),
    ]
    outputs = [OutputDef("variability", "variability", "Variability map"), OutputDef("mean", "map", "Mean of the frames"),
               OutputDef("difference", "map", "Difference last − first frame")]

    def run(self, ctx: JobContext) -> None:
        from cryoplug.imaging import write_png
        series = ctx.require("series")
        files = series.files
        if len(files) < 2:
            raise JobError("A series needs at least two volumes")
        labels = list(series.meta.get("frame_labels") or [f"Frame {i + 1}" for i in range(len(files))])[: len(files)]
        labels += [f"Frame {i + 1}" for i in range(len(labels), len(files))]
        short = short_labels(labels)
        ctx.progress(0.1, f"Reading {len(files)} frames")
        vols, factor = load_frames(files, 10_000 if ctx.params["full_size"] else ANALYSIS_BOX)
        grid = vols[0]
        mask = ctx.input("mask")
        if mask:
            m = MapVolume.read(mask.path)
            m = bin_map(m, factor) if factor > 1 else m
            if m.data.shape != grid.data.shape:
                raise JobError(f"The mask box {m.shape_xyz} does not match the frames {vols[0].shape_xyz}")
            region = m.data >= 0.5
            how = "inside the mask"
        else:
            region = molecule_region(np.mean([v.data for v in vols], axis=0))
            how = "in the molecule (mean of the frames above its Otsu level)"
        raw = np.stack([v.data for v in vols]).astype(np.float32)
        stack = np.stack([normalised(frame, region) for frame in raw]).astype(np.float32)
        ctx.progress(0.4, "Variability and similarity")
        variability = stack.std(axis=0)  # in SD of the density of the molecule, so that frames of any scale compare
        corr = correlations(stack, region)
        order = similarity_order(corr)

        from scipy.ndimage import binary_dilation
        # shown where any frame has density (a domain in a rare position too), with a margin so that surfaces are
        # coloured to their edge
        shown = binary_dilation(region | molecule_region(raw.max(axis=0)), iterations=3)
        var_path = grid.like(np.where(shown, variability, 0.0).astype(np.float32)).write(ctx.path("variability.mrc"))
        mean_path = grid.like(raw.mean(axis=0)).write(ctx.path("mean.mrc"))
        diff_path = grid.like(raw[-1] - raw[0]).write(ctx.path("difference.mrc"))
        vals = variability[region]
        p5, p50, p95 = (float(v) for v in np.percentile(vals, [5, 50, 95]))
        ctx.add_metrics(f"Variability {how}", [
            {"label": "Frames", "value": len(files)},
            {"label": "Median variability", "value": f"{p50:.3f} σ"},
            {"label": "Most variable 5 %", "value": f"≥ {p95:.3f} σ"},
            {"label": "Lowest correlation between two frames", "value": f"{float(corr.min()):.3f}"},
            {"label": "Grid", "value": f"{'×'.join(map(str, grid.shape_xyz))} voxels · {grid.pixel_size:.2f} Å" +
                                      (f" (binned {factor}×)" if factor > 1 else "")},
        ])
        sheet = write_png(ctx.path("frames.png"), frame_strip(stack, labels))
        ctx.add_image("Frames (projections, same grey scale)", sheet)
        ctx.add_heatmap("Correlation between frames", short, short, corr.round(3).tolist(), unit="",
                        note="Blocks of high correlation are one state; a gradient along the diagonal is a continuous motion.")
        steps = [float(corr[i, i + 1]) for i in range(len(files) - 1)]
        ctx.add_plot("Similarity of consecutive frames", [{"name": "Correlation with the next frame",
                                                           "x": list(range(1, len(steps) + 1)), "y": [round(s, 4) for s in steps]}],
                     x_label="Frame", y_label="Correlation")
        if order != list(range(len(files))):
            ctx.add_text("Frames ordered by similarity", " → ".join(short[i] for i in order) +
                         "\n\nFor k-means cluster volumes this order follows the main motion or composition change.")
        model = ctx.input("model")
        if model:
            ctx.progress(0.7, "Per-chain analysis")
            per_chain_table(ctx, np.stack([v.data for v in vols]), grid, model.path, short, variability)
        meta = {"display_range": [round(p5, 3), round(p95, 3)], "unit": "σ", "colour_map": ctx.rel(mean_path),
                "frames": len(files), "source": series.source}
        ctx.add_output("variability", "variability", var_path, "Variability map (SD across frames)", meta=meta)
        ctx.add_output("mean", "map", mean_path, "Mean of the frames", meta={"source": series.source})
        ctx.add_output("difference", "map", diff_path, f"Difference {short[-1]} − {short[0]}", meta={"source": series.source})
        ctx.add_highlight("Frames", len(files))
        ctx.add_highlight("Min. correlation", f"{float(corr.min()):.2f}")


@register
class ExtractVolume(JobType):
    name = "extract_volume"
    title = "Extract volume from series"
    category = "Heterogeneity"
    software = ["CryoPlug"]
    description = ("Takes one volume of a series (a cryoDRGN cluster, a 3D variability frame) as a map, to build or "
                   "refine a model of that state.")
    inputs = [Slot("series", ("volume_series",), "Volume series", prefer=("kmeans", "series", "trajectory"))]
    params = [Param("frame", "int", 1, label="Frame number", min=1, help="As numbered in the viewer and the reports (1 = first).")]
    outputs = [OutputDef("map", "map", "Volume")]

    def run(self, ctx: JobContext) -> None:
        series = ctx.require("series")
        i = ctx.params["frame"]
        if i > len(series.files):
            raise JobError(f"The series has {len(series.files)} frames")
        src = Path(series.files[i - 1])
        labels = series.meta.get("frame_labels") or []
        label = labels[i - 1] if i - 1 < len(labels) else f"Frame {i}"
        dst = ctx.path(f"frame_{i:03d}{src.suffix}")
        shutil.copy2(src, dst)
        ctx.add_output("map", "map", dst, label, meta={k: v for k, v in series.meta.items() if k in ("symmetry",)})
        ctx.add_highlight("Volume", label)
