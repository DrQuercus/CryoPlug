"""Latent spaces of heterogeneity analyses (cryoDRGN, CryoSPARC 3D variability): the interactive explorer and
particle selection.

The cryoDRGN jobs themselves are in :mod:`cryoplug.jobs.cryodrgn`. A ``latent`` output holds the latent
coordinates of every particle (``latent.npz``: ``z``, 2-D embeddings, cluster labels, per-particle values used
to colour the explorer) and points to the particles they come from, so that a selection made in the explorer
becomes a new particle set.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

import numpy as np

from cryoplug.jobs import register
from cryoplug.jobs.base import InputData, JobContext, JobError, JobType, OutputDef, Param, Slot
from cryoplug.jobs.series import correlations, load_frames, molecule_region, normalised, similarity_order

EXPLORER_POINTS = 12_000


# ------------------------------------------------------------- latent explorer
def _round(a: np.ndarray, digits: int = 3) -> list:
    return np.round(np.asarray(a, dtype=np.float64), digits).tolist()


def _compact(values: np.ndarray, lo: float, hi: float) -> list:
    """Values rounded to about a thousandth of their display range (keeps the explorer data small)."""
    span = float(hi - lo) or 1.0
    digits = int(min(6, max(0, 3 - math.floor(math.log10(span)))))
    out = np.round(np.asarray(values, dtype=np.float64), digits)
    return [None if not np.isfinite(v) else (int(v) if digits == 0 else float(v)) for v in out]


def coloring(values: np.ndarray, label: str, unit: str = "", cyclic: bool = False, note: str = "",
             lo: float | None = None, hi: float | None = None) -> dict[str, Any]:
    """A per-particle value the explorer can colour the points by (defocus, viewing angle, ...)."""
    return {"values": np.asarray(values, dtype=np.float64), "label": label, "unit": unit, "cyclic": cyclic,
            "note": note, "lo": lo, "hi": hi}


def latent_report(ctx: JobContext, embeddings: dict[str, np.ndarray], labels: np.ndarray | None, title: str,
                  axis_labels: dict[str, tuple[str, str]] | None = None, volumes: dict[int, str] | None = None,
                  centres_ind: np.ndarray | None = None, paths: list[dict] | None = None,
                  selected: list[int] | None = None, name: str = "latent_view.json",
                  colorings: dict[str, dict[str, Any]] | None = None, clusters_info: list[dict] | None = None,
                  extra: dict[str, Any] | None = None, cluster_name: str = "Cluster",
                  cluster_names: list[str] | None = None) -> Path:
    """Write the data of the interactive latent space explorer and add it to the report.

    The explorer shows a sample of the particles (``index``: their position in the latent output) in each 2-D
    embedding, coloured by cluster or by any of the ``colorings``; cluster centres carry their volume and
    the optional ``clusters_info`` (thumbnail ``image``, warning ``flags``)."""
    embeddings = {k: np.asarray(v, dtype=np.float32) for k, v in embeddings.items() if v is not None and len(v)}
    if not embeddings:
        raise JobError("No latent coordinates to show")
    n = len(next(iter(embeddings.values())))
    rng = np.random.default_rng(0)
    shown = np.sort(rng.choice(n, size=min(n, EXPLORER_POINTS), replace=False))
    axis_labels = axis_labels or {}
    data: dict[str, Any] = {"n_total": int(n), "n_shown": int(len(shown)), "index": shown.tolist(), "embeddings": {},
                            "clusters": [], "paths": paths or [], "selected": selected or [], "cluster_name": cluster_name}
    for key, emb in embeddings.items():
        xl, yl = axis_labels.get(key, (f"{key} 1", f"{key} 2"))
        data["embeddings"][key] = {"x": _round(emb[shown, 0]), "y": _round(emb[shown, 1]), "xlabel": xl, "ylabel": yl}
    if labels is not None:
        labels = np.asarray(labels).astype(int)
        data["labels"] = labels[shown].tolist()
        counts = np.bincount(labels, minlength=int(labels.max()) + 1)
        for i, count in enumerate(counts):
            members = labels == i
            if centres_ind is not None and i < len(centres_ind):
                centre = {k: _round(e[int(centres_ind[i])]) for k, e in embeddings.items()}
            else:
                centre = {k: _round(np.median(e[members], axis=0)) if members.any() else [0, 0] for k, e in embeddings.items()}
            entry = {"id": i, "name": (cluster_names[i] if cluster_names and i < len(cluster_names) else f"{cluster_name} {i + 1}"),
                     "count": int(count), "percent": round(100.0 * count / n, 2), "centre": centre,
                     "volume": (volumes or {}).get(i)}
            if centres_ind is not None and i < len(centres_ind):
                entry["particle"] = int(centres_ind[i])
            if clusters_info and i < len(clusters_info) and clusters_info[i]:
                entry.update(clusters_info[i])
            data["clusters"].append(entry)
    if colorings:
        data["colorings"] = {}
        for key, c in colorings.items():
            values = np.asarray(c["values"], dtype=np.float64)
            if len(values) != n:
                continue
            finite = values[np.isfinite(values)]
            if not len(finite):
                continue
            lo = c.get("lo") if c.get("lo") is not None else float(np.percentile(finite, 2))
            hi = c.get("hi") if c.get("hi") is not None else float(np.percentile(finite, 98))
            if hi <= lo:
                lo, hi = float(finite.min()), float(finite.max()) or lo + 1.0
            data["colorings"][key] = {"label": c["label"], "unit": c.get("unit", ""), "cyclic": bool(c.get("cyclic")),
                                      "note": c.get("note", ""), "lo": round(lo, 6), "hi": round(hi, 6),
                                      "values": _compact(values[shown], lo, hi)}
    if extra:
        data.update(extra)
    path = ctx.path(name)
    path.write_text(json.dumps(data, separators=(",", ":")))
    ctx.add_latent(title, path)
    return path


# ------------------------------------------------------------------- helpers
def parse_clusters(text: str, k: int) -> list[int]:
    """'1, 4-6' -> [0, 3, 4, 5] (clusters are numbered from 1 in the reports)."""
    out: list[int] = []
    for token in re.split(r"[,;\s]+", str(text or "").strip()):
        if not token:
            continue
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", token)
        if not m:
            raise ValueError(f"Cannot read '{token}': give cluster numbers like 1, 4-6")
        first, last = int(m.group(1)), int(m.group(2) or m.group(1))
        for c in range(min(first, last), max(first, last) + 1):
            if not 1 <= c <= k:
                raise ValueError(f"Cluster {c} does not exist (1 to {k})")
            if c - 1 not in out:
                out.append(c - 1)
    return out


def parse_indices(text: str, n: int | None = None) -> list[int]:
    """'12, 40-42' -> [12, 40, 41, 42] (particle numbers as shown by the explorer, from 0)."""
    out: list[int] = []
    for token in re.split(r"[,;\s]+", str(text or "").strip()):
        if not token:
            continue
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", token)
        if not m:
            raise ValueError(f"Cannot read '{token}': give particle numbers like 12, 40-42")
        first, last = int(m.group(1)), int(m.group(2) or m.group(1))
        if abs(last - first) > 10_000:
            raise ValueError(f"Range {token} is too long")
        for i in range(min(first, last), max(first, last) + 1):
            if n is not None and not 0 <= i < n:
                raise ValueError(f"Particle {i} does not exist (0 to {n - 1})")
            if i not in out:
                out.append(i)
    return out


def parse_region(text: str) -> dict[str, Any]:
    """Region drawn with the lasso of the explorer: {"embedding": "UMAP", "polygon": [[x, y], ...]}."""
    try:
        region = json.loads(text) if isinstance(text, str) else dict(text or {})
    except ValueError:
        raise ValueError("The region is not valid (draw it with the lasso of the latent explorer)") from None
    poly = region.get("polygon") if isinstance(region, dict) else None
    if not isinstance(poly, list) or len(poly) < 3:
        raise ValueError("The region needs a polygon of at least 3 points (draw it with the lasso of the latent explorer)")
    try:
        pts = np.asarray(poly, dtype=np.float64)
    except (TypeError, ValueError):
        raise ValueError("The region polygon must be a list of [x, y] points") from None
    if pts.ndim != 2 or pts.shape[1] != 2 or not np.all(np.isfinite(pts)) or len(pts) > 5000:
        raise ValueError("The region polygon must be a list of [x, y] points")
    return {"embedding": str(region.get("embedding") or "UMAP"), "polygon": pts}


def embedding_of(arrays: dict[str, np.ndarray], name: str) -> np.ndarray:
    """A 2-D embedding of a latent output by its explorer name (UMAP, PCA, Components...)."""
    key = {"umap": "umap", "pca": "pca"}.get(name.lower(), f"embedding_{name.lower().replace(' ', '_')}")
    if key in arrays:
        return np.asarray(arrays[key], dtype=np.float64)[:, :2]
    raise JobError(f"This latent space has no '{name}' coordinates")


def latent_size(arrays: dict[str, np.ndarray]) -> int:
    for key in ("z", "labels", "umap", "pca"):
        if key in arrays:
            return len(arrays[key])
    return len(next(iter(arrays.values())))


def latent_input(ctx: JobContext, slot: str = "latent", method: str | None = "cryoDRGN") -> tuple[InputData, dict]:
    lat = ctx.require(slot)
    if method and lat.meta.get("method") != method:
        raise JobError(f"This job needs a {method} latent space (got: {lat.meta.get('method', 'unknown')})")
    with np.load(lat.files[0]) as data:
        arrays = {k: data[k] for k in data.files}
    return lat, arrays


def series_similarity(ctx: JobContext, files: list[Path], labels: list[str], title: str, note: str = "") -> None:
    """Correlation of the cluster volumes, ordered so that similar volumes are neighbours."""
    try:
        vols, _f = load_frames(files)
        region = molecule_region(np.mean([v.data for v in vols], axis=0))
        stack = np.stack([normalised(v.data, region) for v in vols])
        corr = correlations(stack, region)
    except Exception as exc:  # informative only
        ctx.warn(f"Volume similarity not computed: {exc}")
        return
    order = similarity_order(corr)
    names = [labels[i] for i in order]
    ctx.add_heatmap(title, names, names, corr[np.ix_(order, order)].round(3).tolist(),
                    note=note or "Clusters ordered by similarity: blocks of similar volumes are one state; isolated volumes "
                                 "are distinct states, or junk particles.")


def particles_source(ctx: JobContext, lat: InputData) -> tuple[Path, dict[str, Any]]:
    """The particle file a latent space was computed from (row i = particle i of the latent space) and the
    metadata of the job output it belongs to (image folder, symmetry...)."""
    src = lat.meta.get("particles")
    if not src or not ctx.abs(src).exists():
        raise JobError("The particles of this latent space are not available any more")
    path = ctx.abs(src)
    return path, output_meta_of(ctx, path)


def output_meta_of(ctx: JobContext, particles_path: Path) -> dict[str, Any]:
    """Metadata of the job output a particle file belongs to (state.json of its job folder)."""
    try:
        state = json.loads((particles_path.parent / "state.json").read_text())
    except (OSError, ValueError):
        return {}
    for out in state.get("outputs") or []:
        if out.get("type") == "particles" and ctx.abs(out.get("path", "")).resolve() == particles_path.resolve():
            return out.get("meta") or {}
    return {}


# ------------------------------------------------------------ particle selection
SELECTION_MODES = ["clusters", "region", "outliers"]


@register
class SelectParticles(JobType):
    name = "select_particles"
    title = "Select particles (latent space)"
    category = "Heterogeneity"
    software = ["CryoPlug"]
    description = ("Keeps or removes particles chosen in a latent space (cryoDRGN or 3D variability): whole clusters, a "
                   "region drawn with the lasso of the explorer, or the outliers far from the other particles. To discard "
                   "junk or to isolate a state. Writes the selection and the particles left out (CryoSPARC .cs or RELION "
                   ".star, plus cryoDRGN indices): train again with cryoDRGN, or classify / refine in CryoSPARC.")
    inputs = [Slot("latent", ("latent",), "Latent space", help="From a cryoDRGN training/analysis or a CryoSPARC 3D variability import.")]
    params = [
        Param("selection", "choice", "clusters", choices=SELECTION_MODES, label="Select by",
              help="clusters: cluster numbers of the explorer; region: a region drawn with the lasso of the explorer; "
                   "outliers: particles far from the centre of the latent space (large ‖z‖), often junk."),
        Param("clusters", "str", "", label="Clusters", placeholder="1, 4-6",
              help="Cluster numbers as shown in the latent explorer (selection by clusters)."),
        Param("region", "text", "", label="Region (lasso)", advanced=True,
              help="Filled in by the lasso of the latent explorer: the embedding and the polygon drawn in it."),
        Param("zscore", "float", 2.0, label="Outlier threshold", unit="SD", min=0.5, max=10.0,
              help="Selection by outliers: particles whose ‖z‖ exceeds the mean by this many standard deviations."),
        Param("action", "choice", "keep", choices=["keep", "remove"], label="Action",
              help="keep: the output holds the selected particles; remove: it holds all the others."),
    ]
    outputs = [OutputDef("particles", "particles", "Selected particles"),
               OutputDef("excluded", "particles", "Particles left out")]

    @classmethod
    def validate(cls, params, connected):
        mode = params.get("selection") or "clusters"
        if mode == "clusters":
            try:
                parse_clusters(params.get("clusters", ""), 10_000)
            except ValueError as exc:
                return [str(exc)]
            return [] if str(params.get("clusters", "")).strip() else ["Give the clusters to keep or remove"]
        if mode == "region":
            try:
                parse_region(params.get("region") or "")
            except ValueError as exc:
                return [str(exc)]
        return []

    def run(self, ctx: JobContext) -> None:
        p = ctx.params
        lat, arrays = latent_input(ctx, method=None)
        n = latent_size(arrays)
        mode = p["selection"]
        labels = arrays.get("labels")
        chosen: list[int] = []
        if mode == "clusters":
            if labels is None:
                raise JobError("This latent space has no clusters: select a region or the outliers instead")
            k = int(labels.max()) + 1
            try:
                chosen = parse_clusters(p["clusters"], k)
            except ValueError as exc:
                raise JobError(str(exc)) from None
            member = np.isin(labels, chosen)
            what = f"clusters {', '.join(str(c + 1) for c in chosen)}"
        elif mode == "region":
            try:
                region = parse_region(p["region"])
            except ValueError as exc:
                raise JobError(str(exc)) from None
            from cryoplug import particles as pt
            member = pt.points_in_polygon(embedding_of(arrays, region["embedding"]), region["polygon"])
            what = f"a region of the {region['embedding']} map"
        else:
            z = arrays.get("z")
            if z is None:
                raise JobError("This latent space has no latent vectors")
            norm = np.linalg.norm(np.asarray(z, dtype=np.float64).reshape(n, -1), axis=1)
            threshold = float(norm.mean() + p["zscore"] * norm.std())
            member = norm > threshold
            what = f"outliers (‖z‖ > {threshold:.2f}, mean + {p['zscore']:g} SD)"
        keep = member if p["action"] == "keep" else ~member
        ind, left = np.nonzero(keep)[0], np.nonzero(~keep)[0]
        if not len(ind):
            raise JobError(f"No particle left: {what} hold{'s' if mode != 'clusters' else ''} "
                           f"{'none' if p['action'] == 'keep' else 'all'} of the particles")
        ctx.log(f"Selection: {what} ({int(member.sum()):,} particles); action {p['action']}: {len(ind):,} kept, {len(left):,} left out")
        src, parent_meta = particles_source(ctx, lat)
        out = self._write(ctx, src, ind, "particles", n)
        excluded = self._write(ctx, src, left, "excluded", n) if len(left) else None
        base_meta: dict[str, Any] = {"format": parent_meta.get("format") or ("star" if src.suffix == ".star" else "cs")}
        for key in ("datadir", "symmetry", "resolution", "pixel_size", "box"):
            if key in parent_meta:
                base_meta[key] = parent_meta[key]
        selection = {"mode": mode, "action": p["action"], "what": what}
        if chosen:
            selection["clusters"] = [c + 1 for c in chosen]
        prepared = lat.meta.get("prepared") or {}

        def meta_for(rows: np.ndarray, file: Path, name: str) -> dict[str, Any]:
            from cryoplug import particles as pt
            meta = {**base_meta, **self._summary(file), "selection": selection}
            ind_pkl = pt.save_array_pkl(rows, ctx.path(f"{name}_indices.pkl"))
            meta["indices"] = ctx.rel(ind_pkl)
            if prepared.get("stack"):  # cryoDRGN reuses the prepared (downsampled) images with these indices
                base = pt.load_array_pkl(ctx.abs(prepared["ind"])) if prepared.get("ind") else None
                absolute = base[rows] if base is not None else rows
                parent_ind = pt.save_array_pkl(absolute, ctx.path(f"{name}_parent_indices.pkl"))
                meta["parent_cryodrgn"] = {k: v for k, v in prepared.items() if k != "ind"}
                meta["parent_indices"] = ctx.rel(parent_ind)
            return meta

        kept_meta = meta_for(ind, out, "particles")
        np.savetxt(ctx.path("indices.txt"), ind, fmt="%d")
        label = f"{'Kept' if p['action'] == 'keep' else 'Without'} {what} ({len(ind):,})"
        ctx.add_output("particles", "particles", [out, ctx.abs(kept_meta["indices"]), ctx.path("indices.txt")], label,
                       meta=kept_meta)
        if excluded is not None:
            ex_meta = meta_for(left, excluded, "excluded")
            ctx.add_output("excluded", "particles", [excluded, ctx.abs(ex_meta["indices"])],
                           f"Left out ({len(left):,})", meta=ex_meta)
        self._report(ctx, arrays, labels, chosen, keep, mode, what, out, excluded, kept_meta)
        ctx.add_highlight("Particles", f"{len(ind):,} ({100.0 * len(ind) / n:.0f} %)")
        if len(left):
            ctx.add_highlight("Left out", f"{len(left):,}")

    @staticmethod
    def _write(ctx: JobContext, src: Path, rows: np.ndarray, name: str, n: int) -> Path:
        from cryoplug import particles as pt
        if src.suffix.lower() == ".star":
            total = pt.star_count(src)
            if total != n:
                raise JobError(f"The latent space has {n:,} particles, the particle file {total:,}")
            return pt.filter_star(src, ctx.path(f"{name}.star"), rows)
        try:
            data = pt.read_cs(src)
        except ValueError as exc:
            raise JobError(str(exc)) from None
        if len(data) != n:
            raise JobError(f"The latent space has {n:,} particles, the particle file {len(data):,}")
        return pt.write_cs(data[rows], ctx.path(f"{name}.cs"))

    @staticmethod
    def _summary(path: Path) -> dict[str, Any]:
        from cryoplug import particles as pt
        if path.suffix == ".star":
            from cryoplug.jobs.imports import star_summary
            return {**star_summary(path), "has_images": True, "has_poses": True, "has_ctf": True}
        return pt.summary(pt.read_cs(path))

    @staticmethod
    def _report(ctx: JobContext, arrays: dict, labels, chosen: list[int], keep: np.ndarray, mode: str, what: str,
                out: Path, excluded: Path | None, meta: dict[str, Any]) -> None:
        n = len(keep)
        if labels is not None and mode == "clusters":
            k = int(labels.max()) + 1
            counts = np.bincount(labels, minlength=k)
            kept_c = np.bincount(labels[keep], minlength=k)
            rows = [[c + 1, int(counts[c]), int(kept_c[c]), "kept" if kept_c[c] == counts[c] else ("removed" if kept_c[c] == 0 else "partly")]
                    for c in range(k)]
            ctx.add_table("Clusters", ["Cluster", "Particles", "Kept", "Selection"], rows)
        embeddings = {key[len("embedding_"):].replace("_", " ").capitalize(): arrays[key]
                      for key in arrays if key.startswith("embedding_")}
        if "umap" in arrays:
            embeddings["UMAP"] = arrays["umap"]
        if "pca" in arrays:
            embeddings["PCA"] = arrays["pca"]
        if embeddings and labels is not None and mode == "clusters":
            kept = [c for c in range(int(labels.max()) + 1) if keep[labels == c].all() and (labels == c).any()]
            latent_report(ctx, embeddings, labels, "Selected clusters (highlighted)", centres_ind=arrays.get("centers_ind"),
                          selected=kept)
        elif embeddings:
            flags = keep.astype(int)
            latent_report(ctx, embeddings, 1 - flags, f"Selection: {what}", cluster_name="Group",
                          cluster_names=[f"Kept ({int(flags.sum()):,})", f"Left out ({n - int(flags.sum()):,})"],
                          selected=[0])
        datadir = meta.get("datadir", "the CryoSPARC project folder")
        lines = [f"CryoSPARC: Import Particle Stack with the metadata file {out.resolve()} (the images are those of the "
                 f"original particles; data path: {datadir})."]
        if excluded is not None:
            lines.append(f"The particles left out are in {excluded.resolve()}: a 2D classification of them in CryoSPARC "
                         "shows whether they really are junk (or a state worth keeping).")
        lines.append("cryoDRGN: train again on this output (the downsampled images of the first training are reused).")
        ctx.add_text("Using the selection", "\n".join(lines))


# --------------------------------------------------------------- particle images
_READERS: dict[tuple[str, float, str], Any] = {}


def _output_datadir(project_dir: Path, source: Path, outputs: list[dict[str, Any]]) -> str | None:
    """Image folder of a particle file, from the output it belongs to (this job's, or its own job's)."""
    def absolute(rel: str) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else project_dir / p
    candidates = list(outputs)
    try:
        candidates += json.loads((source.parent / "state.json").read_text()).get("outputs") or []
    except (OSError, ValueError):
        pass
    for out in candidates:
        try:
            same = out.get("type") == "particles" and absolute(out.get("path", "")).resolve() == source.resolve()
        except OSError:
            continue
        if same:
            return (out.get("meta") or {}).get("datadir")
    return None


def latent_image_reader(project_dir: Path, meta: dict[str, Any], outputs: list[dict[str, Any]]):
    """Reader of the particle images of a latent output, and the row of each latent particle in it: the images
    prepared for cryoDRGN when there are some (downsampled, quick to read), else the original particles."""
    from cryoplug import particles as pt

    def absolute(rel: str) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else project_dir / p
    prepared = meta.get("prepared") or {}
    rows = None
    if prepared.get("stack") and absolute(prepared["stack"]).exists():
        source, datadir = absolute(prepared["stack"]), prepared.get("datadir")
        if prepared.get("ind"):
            rows = pt.load_array_pkl(absolute(prepared["ind"])).astype(np.int64)
    elif meta.get("particles") and absolute(meta["particles"]).exists():
        source = absolute(meta["particles"])
        datadir = _output_datadir(project_dir, source, outputs)
    else:
        raise ValueError("the particles of this latent space are not available")
    key = (str(source.resolve()), source.stat().st_mtime, str(datadir or ""))
    if key not in _READERS:
        while len(_READERS) >= 4:
            _READERS.pop(next(iter(_READERS))).close()
        _READERS[key] = pt.ParticleImages(source, datadir)
    return _READERS[key], rows


def particle_montage_png(project_dir: Path, output: dict[str, Any], ids: list[int], size: int = 96,
                         outputs: list[dict[str, Any]] | None = None, per_row: int = 8) -> bytes:
    """Contact sheet of particle images (low-pass filtered so the particles are visible) as PNG bytes."""
    from cryoplug import particles as pt
    from cryoplug.imaging import png_bytes
    reader, rows = latent_image_reader(project_dir, output.get("meta") or {}, outputs or [])
    n = int((output.get("meta") or {}).get("n_particles") or (len(rows) if rows is not None else reader.n))
    tiles = []
    for i in ids:
        if not 0 <= i < n:
            raise IndexError(f"particle {i} does not exist (0 to {n - 1})")
        row = int(rows[i]) if rows is not None else i
        tiles.append(pt.particle_tile(reader.image(row), size))
    return png_bytes(pt.montage(tiles, per_row=per_row))
