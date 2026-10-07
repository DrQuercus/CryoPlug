"""Heterogeneity analysis with cryoDRGN: training, latent space analysis, trajectories, particle selection.

cryoDRGN (Zhong et al. 2021) learns a latent space of the conformations and compositions present in the
particles. The jobs wrap its command-line programs (version 4.x) and turn their outputs into CryoPlug data:
a ``latent`` output (per-particle coordinates, clusters) shown in an interactive explorer, and
``volume_series`` outputs (cluster volumes, trajectories) played in the 3D viewer.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import numpy as np

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
)
from cryoplug.jobs.series import (
    add_series,
    correlations,
    load_frames,
    molecule_region,
    normalised,
    similarity_order,
)

LOSS_LINE = re.compile(r"Epoch: (\d+) Average gen loss = ([-\d.eE+]+), KLD = ([-\d.eE+]+), total loss = ([-\d.eE+]+)")
EXPLORER_POINTS = 12_000


# ------------------------------------------------------------- latent explorer
def _round(a: np.ndarray, digits: int = 3) -> list:
    return np.round(np.asarray(a, dtype=np.float64), digits).tolist()


def latent_report(ctx: JobContext, embeddings: dict[str, np.ndarray], labels: np.ndarray | None, title: str,
                  axis_labels: dict[str, tuple[str, str]] | None = None, volumes: dict[int, str] | None = None,
                  centres_ind: np.ndarray | None = None, paths: list[dict] | None = None,
                  selected: list[int] | None = None, name: str = "latent_view.json") -> Path:
    """Write the data of the interactive latent space explorer (a sample of the particles, cluster centres with
    their volumes, trajectories) and add it to the report."""
    embeddings = {k: np.asarray(v, dtype=np.float32) for k, v in embeddings.items() if v is not None and len(v)}
    if not embeddings:
        raise JobError("No latent coordinates to show")
    n = len(next(iter(embeddings.values())))
    rng = np.random.default_rng(0)
    shown = np.sort(rng.choice(n, size=min(n, EXPLORER_POINTS), replace=False))
    axis_labels = axis_labels or {}
    data: dict[str, Any] = {"n_total": int(n), "n_shown": int(len(shown)), "embeddings": {}, "clusters": [],
                            "paths": paths or [], "selected": selected or []}
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
            data["clusters"].append({"id": i, "name": f"Cluster {i + 1}", "count": int(count),
                                     "percent": round(100.0 * count / n, 2), "centre": centre,
                                     "volume": (volumes or {}).get(i)})
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


def parse_losses(log_path: Path) -> list[tuple[int, float, float, float]]:
    """(epoch, reconstruction loss, KL divergence, total loss) for every epoch logged by train_vae."""
    out = []
    if log_path.exists():
        for line in log_path.read_text(errors="replace").splitlines():
            m = LOSS_LINE.search(line)
            if m:
                out.append((int(m.group(1)), float(m.group(2)), float(m.group(3)), float(m.group(4))))
    return out


def latent_input(ctx: JobContext, slot: str = "latent", method: str | None = "cryoDRGN") -> tuple[InputData, dict]:
    lat = ctx.require(slot)
    if method and lat.meta.get("method") != method:
        raise JobError(f"This job needs a {method} latent space (got: {lat.meta.get('method', 'unknown')})")
    with np.load(lat.files[0]) as data:
        arrays = {k: data[k] for k in data.files}
    return lat, arrays


def workdir_of(ctx: JobContext, lat: InputData) -> tuple[Path, int]:
    workdir = ctx.abs(lat.meta["workdir"])
    epoch = int(lat.meta["epoch"])
    if not (workdir / f"weights.{epoch}.pkl").exists() and not (workdir / "weights.pkl").exists():
        raise JobError(f"cryoDRGN weights not found in {workdir}")
    return workdir, epoch


def weights_and_z(workdir: Path, epoch: int) -> tuple[Path, Path]:
    w, z = workdir / f"weights.{epoch}.pkl", workdir / f"z.{epoch}.pkl"
    if not w.exists():
        w, z = workdir / "weights.pkl", workdir / "z.pkl"
    return w, z


def series_similarity(ctx: JobContext, files: list[Path], labels: list[str], title: str) -> None:
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
                    note="Clusters ordered by similarity: blocks of similar volumes are one state; isolated volumes are "
                         "distinct states, or junk particles.")


# ------------------------------------------------------- particle preparation
def _prepared(ctx: JobContext, prep: dict, box: int) -> dict | None:
    if not prep or int(prep.get("box") or 0) != box:
        return None
    paths = {k: ctx.abs(prep[k]) for k in ("stack", "poses", "ctf")}
    if not all(p.exists() for p in paths.values()):
        return None
    return {**{k: str(v) for k, v in paths.items()}, "datadir": prep.get("datadir"), "box": box}


def prepare_particles(ctx: JobContext, particles: InputData, box: int, pose_source: str) -> dict[str, Any]:
    """Poses, CTF and image stack in cryoDRGN's formats. Reuses an earlier preparation at the same image size
    (including the one a particle selection points to, with its indices)."""
    meta = particles.meta
    orig = int(meta.get("box") or 0)
    box = box or orig
    if orig and box > orig:  # images are only cropped in Fourier space, never enlarged
        ctx.log(f"The particles are {orig} px: trained at that size (not {box} px)")
        box = orig
    if box and box % 2:
        raise JobError(f"The image size must be even (got {box})")
    found = _prepared(ctx, meta.get("cryodrgn") or {}, box)
    if found:
        ctx.log(f"Reusing particles prepared at {box} px: {found['stack']}")
        return {**found, "ind": None, "reused": True}
    parent = _prepared(ctx, meta.get("parent_cryodrgn") or {}, box)
    if parent and meta.get("parent_indices"):
        ctx.log(f"Reusing the {box} px particles of the parent dataset with the selected indices")
        return {**parent, "ind": str(ctx.abs(meta["parent_indices"])), "reused": True}

    exe = ctx.executable("cryodrgn")
    fmt = meta.get("format", "cs")
    src = particles.files[0]
    datadir = meta.get("datadir")
    out = ctx.path("prepared")
    out.mkdir(exist_ok=True)
    poses, ctf = out / "pose.pkl", out / "ctf.pkl"
    ctx.progress(0.02, "Reading poses and CTF")
    if fmt == "cs":
        flags = {"heterogeneous refinement": ["--hetrefine"], "ab initio": ["--abinit"]}.get(pose_source, [])
        ctx.run([exe, "parse_pose_csparc", src, "-o", poses, *(["-D", str(orig)] if orig else []), *flags], tool="cryodrgn")
        ctx.run([exe, "parse_ctf_csparc", src, "-o", ctf], tool="cryodrgn")
    else:
        size = ["-D", str(orig)] if orig else []
        apix = ["--Apix", str(meta["pixel_size"])] if meta.get("pixel_size") else []
        ctx.run([exe, "parse_pose_star", src, "-o", poses, *size], tool="cryodrgn")
        ctx.run([exe, "parse_ctf_star", src, "-o", ctf, *size, *apix], tool="cryodrgn")
    if box and (not orig or box < orig):
        n = int(meta.get("n_particles") or 0)
        stack = out / f"particles.{box}.mrcs"
        args = [exe, "downsample", src, "-D", str(box), "-o", stack]
        if datadir:
            args += ["--datadir", datadir]
        if n > 50_000:  # one file per 50k images keeps the memory moderate
            args += ["--chunk", "50000"]
        ctx.progress(0.05, f"Downsampling {n:,} images to {box} px" if n else f"Downsampling the images to {box} px")
        ctx.run(args, tool="cryodrgn")
        if n > 50_000:
            stack = out / f"particles.{box}.txt"
        if not stack.exists():
            raise JobError(f"cryodrgn downsample did not write {stack.name} (see the log)")
        stack_datadir = None
    else:
        stack, stack_datadir = Path(src), datadir
    prep = {"box": box, "stack": ctx.rel(stack), "poses": ctx.rel(poses), "ctf": ctx.rel(ctf), "datadir": stack_datadir}
    ctx.add_output("particles_prepared", "particles", [stack, poses, ctf], f"Particles prepared for cryoDRGN ({box} px)",
                   meta={**meta, "cryodrgn": prep, "particles_file": meta.get("particles_file") or ctx.rel(src)})
    return {"stack": str(stack), "poses": str(poses), "ctf": str(ctf), "datadir": stack_datadir, "box": box, "ind": None,
            "reused": False}


# ---------------------------------------------------------------- analysis
def run_analysis(ctx: JobContext, workdir: Path, epoch: int, outdir: Path, params: dict[str, Any]) -> None:
    args = [ctx.executable("cryodrgn"), "analyze", workdir, str(epoch), "-o", outdir,
            "--ksample", str(params["ksample"]), "--pc", str(params.get("pcs", 2)),
            "--n-per-pc", str(params.get("n_per_pc", 10))]
    if params.get("apix"):
        args += ["--Apix", f"{params['apix']:g}"]
    if params.get("flip"):
        args.append("--flip")
    if params.get("invert"):
        args.append("--invert")
    if params.get("downsample"):
        args += ["-d", str(params["downsample"])]
    if params.get("lowpass"):
        args += ["--low-pass", f"{params['lowpass']:g}"]
    ctx.run(args, tool="cryodrgn")


def finish_analysis(ctx: JobContext, workdir: Path, epoch: int, analyze_dir: Path, k: int,
                    lineage: dict[str, Any]) -> None:
    """Latent output, explorer, cluster volumes and PC trajectories from a cryodrgn analyze folder."""
    from cryoplug import particles as pt
    _w, zfile = weights_and_z(workdir, epoch)
    if not zfile.exists():
        raise JobError(f"Latent vectors not found: {zfile}")
    z = pt.load_array_pkl(zfile).astype(np.float32)
    if z.ndim == 1:
        z = z[:, None]
    kdir = analyze_dir / f"kmeans{k}"
    labels = pt.load_array_pkl(kdir / "labels.pkl").astype(np.int32) if (kdir / "labels.pkl").exists() else None
    centres_ind = np.loadtxt(kdir / "centers_ind.txt", dtype=int, ndmin=1) if (kdir / "centers_ind.txt").exists() else None
    umap = pt.load_array_pkl(analyze_dir / "umap.pkl").astype(np.float32) if (analyze_dir / "umap.pkl").exists() else None
    mean, axes, var = pt.pca_basis(z)
    pca = ((z - mean) @ axes.T).astype(np.float32)
    n = len(z)

    kfiles = sorted(kdir.glob("vol_*.mrc"))
    counts = np.bincount(labels, minlength=k) if labels is not None else np.zeros(k, int)
    klabels = [f"Cluster {i + 1} · {100.0 * counts[i] / n:.1f} %" for i in range(len(kfiles))]
    apix = None
    if kfiles:
        from cryoplug.mrc import MapVolume
        apix = round(MapVolume.read(kfiles[0], header_only=True).pixel_size, 4)
        add_series(ctx, "kmeans", kfiles, f"Volumes of the {len(kfiles)} latent clusters", klabels,
                   {"kind": "kmeans", "particles": counts.tolist()[: len(kfiles)]})
    for pc in range(1, 10):
        pfiles = sorted((analyze_dir / f"pc{pc}").glob("vol_*.mrc"))
        if not pfiles:
            break
        add_series(ctx, f"pc{pc}", pfiles, f"Trajectory along PC{pc} (5th → 95th percentile)",
                   [f"PC{pc} · {j + 1}/{len(pfiles)}" for j in range(len(pfiles))], {"kind": f"pc{pc}"})

    arrays = {"z": z, "pca": pca, "pca_mean": mean.astype(np.float32), "pca_axes": axes.astype(np.float32)}
    if umap is not None and len(umap) == n:
        arrays["umap"] = umap
    if labels is not None:
        arrays["labels"] = labels
    if centres_ind is not None:
        arrays["centers_ind"] = centres_ind
    npz = ctx.path("latent.npz")
    np.savez(npz, **arrays)

    embeddings = {}
    if "umap" in arrays:
        embeddings["UMAP"] = arrays["umap"]
    embeddings["PCA"] = pca
    volumes = {i: ctx.rel(f) for i, f in enumerate(kfiles)}
    latent_report(ctx, embeddings, labels, f"Latent space of {n:,} particles (epoch {epoch})",
                  {"UMAP": ("UMAP 1", "UMAP 2"), "PCA": (f"PC1 ({var[0]} %)", f"PC2 ({var[1]} %)")}, volumes, centres_ind)
    if labels is not None:
        ctx.add_table("Latent clusters (k-means)", ["Cluster", "Particles", "%"],
                      [[i + 1, int(c), f"{100.0 * c / n:.1f}"] for i, c in enumerate(counts)])
    if len(kfiles) >= 3:
        series_similarity(ctx, kfiles, [f"Cluster {i + 1}" for i in range(len(kfiles))],
                          "Similarity of the cluster volumes (correlation)")
    for png, title in (("umap_hexbin.png", "cryoDRGN: particle density in UMAP"),
                       (f"kmeans{k}/umap.png", "cryoDRGN: k-means clusters in UMAP"),
                       ("z_pca_hexbin.png", "cryoDRGN: particle density along PC1 and PC2")):
        if (analyze_dir / png).exists():
            ctx.add_image(title, analyze_dir / png)
    ctx.add_text("Explore further with cryoDRGN",
                 f"Interactive dashboard (particle selection, latent panels), on the server:\n"
                 f"  cryodrgn dashboard {workdir} --epoch {epoch}\n"
                 f"(then open http://localhost:5050 through an SSH tunnel: ssh -L 5050:localhost:5050 <server>)\n\n"
                 f"Jupyter notebooks: {analyze_dir}/cryoDRGN_viz.ipynb, cryoDRGN_filtering.ipynb")
    w, zf = weights_and_z(workdir, epoch)
    files = [npz, zf, w, workdir / "config.yaml"]
    meta = {"method": "cryoDRGN", "workdir": ctx.rel(workdir), "epoch": epoch, "zdim": int(z.shape[1]),
            "n_particles": n, "k": len(kfiles) or k, "apix": apix, **lineage}
    ctx.add_output("latent", "latent", [f for f in files if Path(f).exists()],
                   f"Latent space (cryoDRGN, {z.shape[1]}-D, epoch {epoch})", meta=meta)
    ctx.add_highlight("Particles", f"{n:,}")
    ctx.add_highlight("Latent", f"{z.shape[1]}-D · {len(kfiles)} clusters")


def analysis_params() -> list[Param]:
    return [
        Param("ksample", "int", 20, label="Volumes sampled (k-means)", min=2, max=200,
              help="The latent space is split into this many k-means clusters; one volume is generated per cluster."),
        Param("apix", "float", 0.0, label="Pixel size of the volumes", unit="Å", min=0.0, advanced=True,
              help="0 = from the CTF parameters (corrected for the image size used in training)."),
        Param("flip", "bool", False, label="Flip handedness of the volumes", advanced=True),
        Param("lowpass", "float", 0.0, label="Low-pass the volumes", unit="Å", min=0.0, advanced=True, help="0 = none."),
    ]


# ------------------------------------------------------------------ jobs
@register
class CryodrgnTrain(JobType):
    name = "cryodrgn_train"
    title = "cryoDRGN training"
    category = "Heterogeneity"
    tool = "cryodrgn"
    software = ["cryoDRGN"]
    gpu = 1
    cpus = 8
    description = ("Heterogeneous reconstruction with cryoDRGN: a neural network learns a latent space of the "
                   "conformations and compositions in the particles (poses from the consensus refinement), then "
                   "volumes are generated across it. Results: interactive latent space, cluster volumes and trajectories "
                   "along the main components.")
    inputs = [Slot("particles", ("particles",), "Particles",
                   help="With the poses of a consensus refinement (Import from CryoSPARC, Import particles, or a selection).")]
    params = [
        Param("box", "int", 128, label="Image size for training", unit="px", min=0,
              help="Images are Fourier-cropped to this size (even): 128 for a first pass, 256 for the final run; 0 keeps "
                   "the original size."),
        Param("zdim", "int", 8, label="Latent dimensions", min=1, max=128),
        Param("epochs", "int", 25, label="Epochs", min=1,
              help="Full passes over the particles; check convergence by continuing to 50 (results should not change)."),
        *analysis_params(),
        Param("gpus", "int", 1, label="GPUs", min=1, max=16, help="More than one enables --multigpu (useful for 256 px images)."),
        Param("lazy", "bool", False, label="Lazy loading", help="For datasets larger than the memory (keep the images on a fast disk)."),
        Param("pose_source", "choice", "refinement", choices=["refinement", "heterogeneous refinement", "ab initio"],
              label="Poses from", advanced=True, help="Type of CryoSPARC job the particles come from."),
        Param("enc_dim", "int", 1024, label="Encoder width", min=16, advanced=True),
        Param("enc_layers", "int", 3, label="Encoder layers", min=1, advanced=True),
        Param("dec_dim", "int", 1024, label="Decoder width", min=16, advanced=True),
        Param("dec_layers", "int", 3, label="Decoder layers", min=1, advanced=True),
        Param("batch_size", "int", 8, label="Batch size", min=1, advanced=True),
        Param("uninvert", "bool", False, label="Do not invert the images", advanced=True,
              help="For negative stain (dark particles on a light background)."),
        extra_args_param(),
    ]
    outputs = [OutputDef("latent", "latent", "Latent space"),
               OutputDef("kmeans", "volume_series", "Volumes of the latent clusters"),
               OutputDef("pc1", "volume_series", "Trajectory along PC1"),
               OutputDef("pc2", "volume_series", "Trajectory along PC2"),
               OutputDef("particles_prepared", "particles", "Particles prepared for cryoDRGN")]

    @classmethod
    def resources(cls, params):
        res = super().resources(params)
        res["num_gpus"] = max(1, int(params.get("gpus") or 1))
        return res

    @classmethod
    def validate(cls, params, connected):
        box = int(params.get("box") or 0)
        return [f"The image size must be even (got {box})"] if box % 2 else []

    def run(self, ctx: JobContext) -> None:
        p = ctx.params
        particles = ctx.require("particles")
        if not particles.meta.get("has_poses", True):
            raise JobError("These particles have no poses: import the particles of a consensus refinement.")
        prep = prepare_particles(ctx, particles, p["box"], p["pose_source"])
        workdir = ctx.path("train")
        epochs = p["epochs"]
        args = [ctx.executable("cryodrgn"), "train_vae", prep["stack"], "--poses", prep["poses"], "--ctf", prep["ctf"],
                "--zdim", str(p["zdim"]), "-n", str(epochs), "-o", workdir, "--no-analysis",
                "--enc-dim", str(p["enc_dim"]), "--enc-layers", str(p["enc_layers"]),
                "--dec-dim", str(p["dec_dim"]), "--dec-layers", str(p["dec_layers"]), "-b", str(p["batch_size"])]
        if prep.get("ind"):
            args += ["--ind", prep["ind"]]
        if prep.get("datadir"):
            args += ["--datadir", prep["datadir"]]
        if p["gpus"] > 1:
            args.append("--multigpu")
        if p["lazy"]:
            args.append("--lazy")
        if p["uninvert"]:
            args.append("--uninvert-data")
        args += ctx.split_extra()

        def progress(line: str) -> None:
            m = re.search(r"=====> Epoch: (\d+)", line)
            if m:
                done = int(m.group(1))
                ctx.progress(0.1 + 0.75 * done / epochs, f"Epoch {done}/{epochs}")

        ctx.progress(0.1, f"Training ({epochs} epochs)")
        ctx.run(args, tool="cryodrgn", on_line=progress)
        if not (workdir / f"weights.{epochs}.pkl").exists() and not (workdir / "weights.pkl").exists():
            raise JobError("cryoDRGN finished without writing its weights (see the log)")
        losses = parse_losses(workdir / "run.log")
        if losses:
            e = [x[0] for x in losses]
            ctx.add_plot("Training losses", [
                {"name": "Total loss", "x": e, "y": [x[3] for x in losses]},
                {"name": "Reconstruction (gen) loss", "x": e, "y": [x[1] for x in losses]},
                {"name": "KL divergence", "x": e, "y": [x[2] for x in losses]},
            ], x_label="Epoch", y_label="Loss")
        ctx.progress(0.88, "Analysing the latent space")
        analyze_dir = workdir / f"analyze.{epochs}"
        run_analysis(ctx, workdir, epochs, analyze_dir, p)
        prepared = {"box": prep["box"], "datadir": prep.get("datadir")}
        for key in ("stack", "poses", "ctf", "ind"):
            if prep.get(key):
                prepared[key] = ctx.rel(prep[key])
        source = particles.meta.get("particles_file") or ctx.rel(particles.files[0])
        lineage = {"particles": source, "box": prep["box"], "prepared": prepared}
        finish_analysis(ctx, workdir, epochs, analyze_dir, p["ksample"], lineage)


@register
class CryodrgnAnalyze(JobType):
    name = "cryodrgn_analyze"
    title = "cryoDRGN analysis"
    category = "Heterogeneity"
    tool = "cryodrgn"
    software = ["cryoDRGN"]
    gpu = 1
    cpus = 4
    description = ("Analyses a trained cryoDRGN model again (another epoch, more clusters, longer trajectories, "
                   "flipped hand...): latent space, cluster volumes and principal-component trajectories.")
    inputs = [Slot("latent", ("latent",), "cryoDRGN latent space", help="From a cryoDRGN training.")]
    params = [
        Param("epoch", "int", 0, label="Epoch", min=0, help="0 = the epoch of the input (the last one of the training)."),
        *analysis_params(),
        Param("pcs", "int", 2, label="Principal components", min=1, max=8, help="Trajectories generated along the first components."),
        Param("n_per_pc", "int", 10, label="Volumes per trajectory", min=3, max=50),
        Param("downsample", "int", 0, label="Volume box", unit="px", min=0, advanced=True, help="0 = training size."),
    ]
    outputs = [OutputDef("latent", "latent", "Latent space"), OutputDef("kmeans", "volume_series", "Volumes of the latent clusters"),
               OutputDef("pc1", "volume_series", "Trajectory along PC1"), OutputDef("pc2", "volume_series", "Trajectory along PC2")]

    def run(self, ctx: JobContext) -> None:
        lat, _arrays = latent_input(ctx)
        workdir, epoch = workdir_of(ctx, lat)
        if ctx.params["epoch"]:
            epoch = ctx.params["epoch"]
            if not (workdir / f"weights.{epoch}.pkl").exists():
                raise JobError(f"No checkpoint for epoch {epoch} in {workdir}")
        outdir = ctx.path("analyze")
        run_analysis(ctx, workdir, epoch, outdir, ctx.params)
        lineage = {k: lat.meta[k] for k in ("particles", "box", "prepared") if k in lat.meta}
        finish_analysis(ctx, workdir, epoch, outdir, ctx.params["ksample"], lineage)


@register
class CryodrgnTrajectory(JobType):
    name = "cryodrgn_trajectory"
    title = "cryoDRGN trajectory"
    category = "Heterogeneity"
    tool = "cryodrgn"
    software = ["cryoDRGN"]
    gpu = 1
    description = ("Volumes along a path in the cryoDRGN latent space between chosen clusters (through the "
                   "particles, or in a straight line): a movie of the transition, to play in the 3D viewer.")
    inputs = [Slot("latent", ("latent",), "cryoDRGN latent space")]
    params = [
        Param("clusters", "str", "", label="Clusters to visit", required=True, placeholder="3, 12",
              help="Cluster numbers of the analysis, in order (two or more)."),
        Param("frames", "int", 20, label="Volumes", min=3, max=100),
        Param("path", "choice", "through the particles", choices=["through the particles", "straight line"],
              label="Path", help="Through the particles: shortest path on the neighbour graph (stays in populated regions)."),
        Param("apix", "float", 0.0, label="Pixel size", unit="Å", min=0.0, advanced=True, help="0 = as the cluster volumes."),
        Param("flip", "bool", False, label="Flip handedness", advanced=True),
    ]
    outputs = [OutputDef("trajectory", "volume_series", "Trajectory")]

    @classmethod
    def validate(cls, params, connected):
        try:
            if len(parse_clusters(params.get("clusters", ""), 10_000)) < 2:
                return ["Give at least two clusters"]
        except ValueError as exc:
            return [str(exc)]
        return []

    def run(self, ctx: JobContext) -> None:
        lat, arrays = latent_input(ctx)
        workdir, epoch = workdir_of(ctx, lat)
        if "centers_ind" not in arrays:
            raise JobError("The latent space has no clusters (run a cryoDRGN analysis)")
        try:
            chosen = parse_clusters(ctx.params["clusters"], len(arrays["centers_ind"]))
        except ValueError as exc:
            raise JobError(str(exc)) from None
        anchors = [int(arrays["centers_ind"][c]) for c in chosen]
        weights, zfile = weights_and_z(workdir, epoch)
        z = arrays["z"]
        n_frames = ctx.params["frames"]
        if ctx.params["path"] == "through the particles":
            path_txt, path_ind = ctx.path("z_path.txt"), ctx.path("z_path_ind.txt")
            ctx.run([ctx.executable("cryodrgn"), "graph_traversal", zfile, "--anchors", *map(str, anchors),
                     "-o", path_txt, "--outind", path_ind], tool="cryodrgn")
            if not path_ind.exists():
                raise JobError("cryodrgn graph_traversal found no path (see the log)")
            ind = np.loadtxt(path_ind, dtype=int, ndmin=1)
            ind = ind[np.unique(np.round(np.linspace(0, len(ind) - 1, min(n_frames, len(ind)))).astype(int))]
            zsel = z[ind]
        else:
            pts = z[anchors].astype(np.float64)
            t = np.linspace(0, len(pts) - 1, n_frames)
            i0 = np.minimum(np.floor(t).astype(int), len(pts) - 2)
            zsel = pts[i0] + (t - i0)[:, None] * (pts[i0 + 1] - pts[i0])
            ind = np.array([int(np.argmin(((z - q) ** 2).sum(1))) for q in zsel])  # nearest particles, for UMAP
        zvals = ctx.path("z_values.txt")
        np.savetxt(zvals, np.atleast_2d(zsel))
        apix = ctx.params["apix"] or lat.meta.get("apix")
        args = [ctx.executable("cryodrgn"), "eval_vol", weights, "--config", workdir / "config.yaml", "--zfile", zvals,
                "-o", ctx.path("trajectory")]
        if apix:
            args += ["--Apix", f"{float(apix):g}"]
        if ctx.params["flip"]:
            args.append("--flip")
        ctx.run(args, tool="cryodrgn")
        files = sorted(ctx.path("trajectory").glob("vol_*.mrc"))
        if not files:
            raise JobError("cryodrgn eval_vol wrote no volume (see the log)")
        labels = arrays.get("labels")
        frame_labels = [f"{j + 1}/{len(files)}" + (f" · cluster {int(labels[ind[j]]) + 1}" if labels is not None else "")
                        for j in range(len(files))]
        names = " → ".join(str(c + 1) for c in chosen)
        add_series(ctx, "trajectory", files, f"Trajectory clusters {names} ({len(files)} volumes)", frame_labels,
                   {"kind": "trajectory", "clusters": [c + 1 for c in chosen]})
        embeddings = {"PCA": arrays["pca"]}
        if "umap" in arrays:
            embeddings = {"UMAP": arrays["umap"], **embeddings}
        path_pts = {"PCA": _round((np.atleast_2d(zsel) - arrays["pca_mean"]) @ arrays["pca_axes"].T)}
        if "umap" in arrays:
            path_pts["UMAP"] = _round(arrays["umap"][ind])
        latent_report(ctx, embeddings, labels, f"Path through clusters {names}",
                      {"UMAP": ("UMAP 1", "UMAP 2"), "PCA": ("PC1", "PC2")},
                      centres_ind=arrays["centers_ind"], paths=[{"name": f"Clusters {names}", "points": path_pts}])
        ctx.add_highlight("Volumes", len(files))


@register
class SelectParticles(JobType):
    name = "select_particles"
    title = "Select particles (latent clusters)"
    category = "Heterogeneity"
    software = ["CryoPlug"]
    description = ("Keeps or removes the particles of chosen latent clusters (cryoDRGN or 3D variability): to discard "
                   "junk, or to isolate a state. Writes a CryoSPARC .cs file and cryoDRGN indices; the selection can be "
                   "trained again with cryoDRGN or refined in CryoSPARC.")
    inputs = [Slot("latent", ("latent",), "Latent space", help="From a cryoDRGN training/analysis or a CryoSPARC 3D variability import.")]
    params = [
        Param("clusters", "str", "", label="Clusters", required=True, placeholder="1, 4-6",
              help="Cluster numbers as shown in the latent explorer."),
        Param("action", "choice", "keep", choices=["keep", "remove"], label="Action"),
    ]
    outputs = [OutputDef("particles", "particles", "Selected particles")]

    @classmethod
    def validate(cls, params, connected):
        try:
            parse_clusters(params.get("clusters", ""), 10_000)
        except ValueError as exc:
            return [str(exc)]
        return [] if str(params.get("clusters", "")).strip() else ["Give the clusters to keep or remove"]

    def run(self, ctx: JobContext) -> None:
        from cryoplug import particles as pt
        lat, arrays = latent_input(ctx, method=None)
        if "labels" not in arrays:
            raise JobError("This latent space has no clusters")
        labels = arrays["labels"]
        k = int(labels.max()) + 1
        try:
            chosen = parse_clusters(ctx.params["clusters"], k)
        except ValueError as exc:
            raise JobError(str(exc)) from None
        keep = np.isin(labels, chosen)
        if ctx.params["action"] == "remove":
            keep = ~keep
        ind = np.nonzero(keep)[0]
        if not len(ind):
            raise JobError("No particle left")
        src = lat.meta.get("particles")
        if not src or not ctx.abs(src).exists():
            raise JobError("The particles of this latent space are not available any more")
        data = pt.read_cs(ctx.abs(src)) if str(src).endswith(".cs") else None
        if data is None:
            raise JobError("Selections are written for CryoSPARC .cs particles only")
        if len(data) != len(labels):
            raise JobError(f"The latent space has {len(labels):,} particles, the particle file {len(data):,}")
        out = pt.write_cs(data[ind], ctx.path("particles.cs"))
        ind_pkl = pt.save_array_pkl(ind, ctx.path("indices.pkl"))
        np.savetxt(ctx.path("indices.txt"), ind, fmt="%d")
        info = pt.summary(data[ind])
        parent_src = ctx.abs(src)
        meta: dict[str, Any] = {**info, "format": "cs", "selection": {"clusters": [c + 1 for c in chosen],
                                                                       "action": ctx.params["action"]}}
        # keep the image folder of the parent particles, and let cryoDRGN reuse the parent's prepared stack
        parent_meta = self._parent_meta(ctx, parent_src)
        for key in ("datadir", "symmetry", "resolution"):
            if key in parent_meta:
                meta[key] = parent_meta[key]
        prepared = lat.meta.get("prepared") or {}
        if prepared.get("stack"):
            base = pt.load_array_pkl(ctx.abs(prepared["ind"])) if prepared.get("ind") else None
            absolute = base[ind] if base is not None else ind
            parent_ind = pt.save_array_pkl(absolute, ctx.path("parent_indices.pkl"))
            meta["parent_cryodrgn"] = {k: v for k, v in prepared.items() if k != "ind"}
            meta["parent_indices"] = ctx.rel(parent_ind)
        counts = np.bincount(labels, minlength=k)
        rows = [[c + 1, int(counts[c]), "kept" if (c in chosen) == (ctx.params["action"] == "keep") else "removed"] for c in range(k)]
        ctx.add_table("Clusters", ["Cluster", "Particles", "Selection"], rows)
        embeddings = {key[len("embedding_"):].capitalize(): arrays[key] for key in arrays if key.startswith("embedding_")}
        if "umap" in arrays:
            embeddings["UMAP"] = arrays["umap"]
        if "pca" in arrays:
            embeddings["PCA"] = arrays["pca"]
        if embeddings:
            kept = [c for c in range(k) if (c in chosen) == (ctx.params["action"] == "keep")]
            latent_report(ctx, embeddings, labels, "Selected clusters (highlighted)", centres_ind=arrays.get("centers_ind"),
                          selected=kept)
        ctx.add_text("Using the selection",
                     f"CryoSPARC: Import Particle Stack with the metadata file {out.resolve()} (the images are those of the "
                     f"original particles; data path: {meta.get('datadir', 'the CryoSPARC project folder')}).\n"
                     f"cryoDRGN: train again on this output (the downsampled images of the first training are reused), or "
                     f"use --ind {ind_pkl.resolve()} by hand.")
        label = f"{'Kept' if ctx.params['action'] == 'keep' else 'Without'} clusters {', '.join(str(c + 1) for c in chosen)} ({len(ind):,})"
        ctx.add_output("particles", "particles", [out, ind_pkl, ctx.path("indices.txt")], label, meta=meta)
        ctx.add_highlight("Particles", f"{len(ind):,} ({100.0 * len(ind) / len(labels):.0f} %)")

    @staticmethod
    def _parent_meta(ctx: JobContext, particles_path: Path) -> dict[str, Any]:
        """Metadata of the job output the particles file belongs to (state.json of its job folder)."""
        try:
            state = json.loads((particles_path.parent / "state.json").read_text())
        except (OSError, ValueError):
            return {}
        for out in state.get("outputs") or []:
            if out.get("type") == "particles" and ctx.abs(out.get("path", "")).resolve() == particles_path.resolve():
                return out.get("meta") or {}
        return {}
