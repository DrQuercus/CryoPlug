"""cryoDRGN: heterogeneous reconstruction with a neural network (Zhong et al. 2021, Nat. Methods 18, 176).

The jobs follow the protocol of Kinman et al. (2023, Nat. Protoc. 18, 319):

1. the particles of a consensus refinement are converted to cryoDRGN's formats (poses, CTF, downsampled
   images) and checked by backprojecting the first 10,000 images (``cryodrgn_backproject``, or the input
   check that starts a training);
2. a first training at 128 px with a small network (``cryodrgn_train``) shows the main states and the junk,
   which is removed in the latent space explorer (``select_particles``);
3. a final training at 256 px with a large network on the cleaned particles, checked for convergence
   (``cryodrgn_convergence``) and continued when needed (``cryodrgn_continue``);
4. analysis: cluster volumes and trajectories (``cryodrgn_analyze``, ``cryodrgn_trajectory``), volumes at
   chosen particles (``cryodrgn_volumes``) and the landscape of the volumes (``cryodrgn_landscape``).

Every job turns cryoDRGN's outputs into CryoPlug data: a ``latent`` output (latent coordinates, clusters and
per-particle values such as the defocus or the viewing direction) shown in the interactive explorer, and
``volume_series`` outputs played in the 3D viewer. cryoDRGN 3.x and 4.x are supported: the version is read
when a job starts (checkpoints are numbered from 1 since 3.5, some options appeared in 4.x).
"""
from __future__ import annotations

import math
import re
import shlex
import time
from pathlib import Path
from typing import Any

import numpy as np

from cryoplug.jobs import register
from cryoplug.jobs.base import InputData, JobContext, JobError, JobType, OutputDef, Param, Slot, extra_args_param
from cryoplug.jobs.heterogeneity import (
    _round,
    coloring,
    embedding_of,
    latent_input,
    latent_report,
    parse_clusters,
    parse_indices,
    parse_region,
    series_similarity,
)
from cryoplug.jobs.series import add_series, load_frames, molecule_region

EPOCH_RE = re.compile(r"=====> Epoch: (\d+) Average gen loss = ([^,\s]+), KLD = ([^,\s]+), total loss = ([^;\s]+)"
                      r"(?:; Finished in (?:(\d+) days?, )?(\d+):(\d+):(\d+(?:\.\d+)?))?")
BATCH_RE = re.compile(r"\[Train Epoch: (\d+)/(\d+)\] \[(\d+)/(\d+) (?:particles|images)\]")
VERSION_RE = re.compile(r"cryo\s*DRGN\s+v?(\d+)\.(\d+)(?:\.(\d+))?", re.I)
DEFAULT_VERSION = (4, 3, 0)
MODEL_CHOICES = ["auto (protocol)", "small (256 × 3)", "large (1024 × 3)", "custom (widths below)"]
CHECK_CHOICES = ["check, then train", "check, stop if it fails", "no check"]
POSE_SOURCES = ["refinement", "heterogeneous refinement", "ab initio"]
INPUT_CHECK_IMAGES = 10_000


# ------------------------------------------------------------------ versions
_VERSIONS: dict[str, tuple[int, int, int]] = {}


def cryodrgn_version(ctx: JobContext) -> tuple[int, int, int]:
    """Version of the cryoDRGN this job runs (read once per job with ``cryodrgn --version``)."""
    key = str(ctx.job_dir)
    if key in _VERSIONS:
        return _VERSIONS[key]
    lines: list[str] = []
    ctx.run([ctx.executable("cryodrgn"), "--version"], tool="cryodrgn", check=False, on_line=lines.append)
    found = next((m for m in (VERSION_RE.search(line) for line in lines) if m), None)
    if found:
        version = (int(found.group(1)), int(found.group(2)), int(found.group(3) or 0))
    else:
        version = DEFAULT_VERSION
        ctx.warn("The cryoDRGN version could not be read: options of version 4.x are used")
    if version < (3, 0):
        ctx.warn(f"cryoDRGN {version_text(version)} is old: CryoPlug is made for versions 3.4 to 4.x "
                 "(pip install -U cryodrgn)")
    _VERSIONS[key] = version
    return version


def version_text(version: tuple[int, ...]) -> str:
    return ".".join(str(v) for v in version)


# -------------------------------------------------------------------- memory
def memory_limit() -> int | None:
    """Memory (bytes) the job may use: the machine's, or the limit of its control group (SLURM jobs)."""
    limits: list[int] = []
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                limits.append(int(line.split()[1]) * 1024)
                break
    except (OSError, ValueError, IndexError):
        pass
    candidates = ["/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"]
    try:
        for line in Path("/proc/self/cgroup").read_text().splitlines():
            parts = line.split(":", 2)
            if len(parts) == 3 and parts[0] == "0" and parts[2] not in ("", "/"):
                candidates.insert(0, f"/sys/fs/cgroup{parts[2]}/memory.max")
            elif len(parts) == 3 and "memory" in parts[1].split(",") and parts[2] not in ("", "/"):
                candidates.insert(0, f"/sys/fs/cgroup/memory{parts[2]}/memory.limit_in_bytes")
    except OSError:
        pass
    for path in candidates:
        try:
            text = Path(path).read_text().strip()
        except OSError:
            continue
        if text.isdigit() and 0 < int(text) < (1 << 50):
            limits.append(int(text))
    return min(limits) if limits else None


def decide_lazy(ctx: JobContext, requested: bool, n: int, box: int) -> bool:
    """Lazy loading when asked, or when the images would not fit comfortably in memory."""
    if requested:
        return True
    limit = memory_limit()
    need = 2 * max(n, 0) * box * box * 4  # float32 images and cryoDRGN's working copies
    if limit and n and need > 0.6 * limit:
        ctx.log(f"The {n:,} images of {box} px need about {need / 1e9:.0f} GB of memory and {limit / 1e9:.0f} GB are "
                "available: images are read from disk as needed (--lazy)")
        return True
    return False


def fmt_duration(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    if seconds < 90:
        return f"{seconds:.0f} s"
    minutes = seconds / 60
    if minutes < 90:
        return f"{minutes:.0f} min"
    hours, minutes = divmod(int(round(minutes)), 60)
    if hours < 48:
        return f"{hours} h {minutes:02d}"
    days, hours = divmod(hours, 24)
    return f"{days} d {hours} h"


# ---------------------------------------------------------- training folders
def checkpoint_epochs(workdir: Path) -> list[int]:
    """Epochs with saved weights in a cryoDRGN training folder (weights.N.pkl)."""
    out = []
    for f in Path(workdir).glob("weights.*.pkl"):
        m = re.fullmatch(r"weights\.(\d+)\.pkl", f.name)
        if m and (Path(workdir) / f"z.{m.group(1)}.pkl").exists():
            out.append(int(m.group(1)))
    return sorted(out)


def weights_and_z(workdir: Path, epoch: int) -> tuple[Path, Path]:
    w, z = workdir / f"weights.{epoch}.pkl", workdir / f"z.{epoch}.pkl"
    if not w.exists():
        w, z = workdir / "weights.pkl", workdir / "z.pkl"
    return w, z


def train_chain(ctx: JobContext, lat: InputData) -> list[Path]:
    """Training folders of a latent space, oldest first (a continued training adds a folder)."""
    dirs = lat.meta.get("workdirs") or [lat.meta.get("workdir")]
    return [ctx.abs(d) for d in dirs if d]


def epoch_folders(chain: list[Path]) -> dict[int, Path]:
    """Epoch -> training folder holding its checkpoint (the latest folder wins)."""
    found: dict[int, Path] = {}
    for workdir in chain:
        for e in checkpoint_epochs(workdir):
            found[e] = workdir
    return found


def workdir_of(ctx: JobContext, lat: InputData, epoch: int = 0) -> tuple[Path, int]:
    """Training folder and epoch of a latent space (or of another epoch of the same training)."""
    if not lat.meta.get("workdir"):
        raise JobError("This latent space does not come from a cryoDRGN training")
    workdir, current = ctx.abs(lat.meta["workdir"]), int(lat.meta["epoch"])
    if epoch and epoch != current:
        folders = epoch_folders(train_chain(ctx, lat))
        if epoch not in folders:
            raise JobError(f"No checkpoint for epoch {epoch} (saved: {', '.join(map(str, sorted(folders))) or 'none'})")
        return folders[epoch], epoch
    if not (workdir / f"weights.{current}.pkl").exists() and not (workdir / "weights.pkl").exists():
        raise JobError(f"cryoDRGN weights not found in {workdir}")
    return workdir, current


# -------------------------------------------------------------------- losses
def loss_row(m: re.Match) -> dict[str, Any]:
    def num(text: str) -> float:
        try:
            return float(text)
        except ValueError:
            return float("nan")
    seconds = None
    if m.group(6) is not None:
        seconds = int(m.group(5) or 0) * 86400 + int(m.group(6)) * 3600 + int(m.group(7)) * 60 + float(m.group(8))
    return {"epoch": int(m.group(1)), "gen": num(m.group(2)), "kld": num(m.group(3)), "total": num(m.group(4)),
            "seconds": seconds}


def parse_losses(log_path: Path) -> list[dict[str, Any]]:
    """Losses of every epoch logged by train_vae (run.log of the training folder)."""
    rows: list[dict[str, Any]] = []
    if Path(log_path).exists():
        for line in Path(log_path).read_text(errors="replace").splitlines():
            m = EPOCH_RE.search(line)
            if m:
                rows.append(loss_row(m))
    return rows


def chain_losses(chain: list[Path]) -> list[dict[str, Any]]:
    by_epoch: dict[int, dict[str, Any]] = {}
    for workdir in chain:
        for row in parse_losses(workdir / "run.log"):
            by_epoch[row["epoch"]] = row
    return [by_epoch[e] for e in sorted(by_epoch)]


def loss_plots(ctx: JobContext, rows: list[dict[str, Any]]) -> None:
    """Loss curves of a training (redrawn in place while it runs)."""
    if not rows:
        return
    e = [r["epoch"] for r in rows]
    clean = lambda v: None if not np.isfinite(v) else round(float(v), 6)  # noqa: E731
    ctx.add_plot("Training loss", [
        {"name": "Reconstruction error (gen loss)", "x": e, "y": [clean(r["gen"]) for r in rows]},
        {"name": "Total loss", "x": e, "y": [clean(r["total"]) for r in rows]},
    ], x_label="Epoch", y_label="Loss", key="losses",
        note="The reconstruction error falls quickly, then levels off: a plateau means the network has learnt what it "
             "can from these images. Still falling at the end: train longer (cryoDRGN continue training). The total "
             "loss adds the KL term (β × KLD).")
    ctx.add_plot("KL divergence of the latent encoding", [
        {"name": "KLD", "x": e, "y": [clean(r["kld"]) for r in rows]},
    ], x_label="Epoch", y_label="KLD", key="kld",
        note="How much information the latent coordinates carry about each image. It usually rises over the first "
             "epochs, then stabilises.")


class TrainingMonitor:
    """Follows train_vae's output: progress and time left on the job card, loss curves after each epoch."""

    def __init__(self, ctx: JobContext, last_epoch: int, n_epochs: int, span: tuple[float, float] = (0.1, 0.85),
                 history: list[dict[str, Any]] | None = None):
        self.ctx, self.last_epoch, self.total, self.span = ctx, last_epoch, max(1, n_epochs), span
        self.history = list(history or [])
        self.rows: list[dict[str, Any]] = []
        self.epoch_started = time.time()
        self.updated = 0.0

    def on_line(self, line: str) -> None:
        m = EPOCH_RE.search(line)
        if m:
            row = loss_row(m)
            if row["seconds"] is None:
                row["seconds"] = time.time() - self.epoch_started
            self.epoch_started = time.time()
            self.rows.append(row)
            self.report(row["epoch"], 0.0, finished=True)
            loss_plots(self.ctx, self.history + self.rows)
            return
        m = BATCH_RE.search(line)
        if m and time.time() - self.updated > 3:
            self.report(int(m.group(1)), int(m.group(3)) / max(1, int(m.group(4))))

    def per_epoch(self) -> float | None:
        recent = [r["seconds"] for r in self.rows[-5:] if r.get("seconds")]
        return float(np.median(recent)) if recent else None

    def report(self, epoch: int, fraction: float, finished: bool = False) -> None:
        self.updated = time.time()
        done = len(self.rows) + (0.0 if finished else fraction)
        per = self.per_epoch()
        if per is None and fraction > 0.02:
            per = (time.time() - self.epoch_started) / fraction
        msg = f"Epoch {epoch}/{self.last_epoch} done" if finished else f"Epoch {epoch}/{self.last_epoch} · {fraction:.0%}"
        if per:
            msg += f" · {fmt_duration(per)} per epoch"
            if done < self.total:
                msg += f" · about {fmt_duration((self.total - done) * per)} left"
        lo, hi = self.span
        self.ctx.progress(lo + (hi - lo) * min(1.0, done / self.total), msg, log=False)


# --------------------------------------------------------- particle preparation
def _prepared(ctx: JobContext, prep: dict, box: int) -> dict | None:
    if not prep or int(prep.get("box") or 0) != box:
        return None
    paths = {k: ctx.abs(prep[k]) for k in ("stack", "poses", "ctf") if prep.get(k)}
    if len(paths) < 3 or not all(p.exists() for p in paths.values()):
        return None
    return {**{k: str(v) for k, v in paths.items()}, "datadir": prep.get("datadir"), "box": box}


def prepare_particles(ctx: JobContext, particles: InputData, box: int, pose_source: str) -> dict[str, Any]:
    """Poses, CTF and image stack in cryoDRGN's formats. Reuses an earlier preparation at the same image size
    (including the one a particle selection points to, with its indices)."""
    from cryoplug import particles as pt
    meta = particles.meta
    if not meta.get("has_poses", True):
        raise JobError("These particles have no poses: import the particles of a consensus refinement.")
    orig = int(meta.get("box") or 0)
    box = box or orig
    if orig and box > orig:  # images are only cropped in Fourier space, never enlarged
        ctx.log(f"The particles are {orig} px: they are used at that size (not {box} px)")
        box = orig
    if box and box % 2:
        raise JobError(f"The image size must be even (got {box})")
    n_all = int(meta.get("n_particles") or 0)
    found = _prepared(ctx, meta.get("cryodrgn") or {}, box)
    if found:
        ctx.log(f"Reusing the particles prepared at {box} px: {found['stack']}")
        return {**found, "ind": None, "reused": True, "n": n_all}
    parent = _prepared(ctx, meta.get("parent_cryodrgn") or {}, box)
    if parent and meta.get("parent_indices"):
        ind_path = ctx.abs(meta["parent_indices"])
        try:
            n = len(pt.load_array_pkl(ind_path))
        except Exception:  # noqa: BLE001 - count only
            n = n_all
        ctx.log(f"Reusing the {box} px particles of the parent dataset with the selected indices ({n:,})")
        return {**parent, "ind": str(ind_path), "reused": True, "n": n}

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
        stack = out / f"particles.{box}.mrcs"
        args = [exe, "downsample", src, "-D", str(box), "-o", stack]
        if datadir:
            args += ["--datadir", datadir]
        if n_all > 50_000:  # one file per 50k images keeps the memory moderate
            args += ["--chunk", "50000"]
        ctx.progress(0.04, f"Downsampling {n_all:,} images to {box} px" if n_all else f"Downsampling the images to {box} px")
        ctx.run(args, tool="cryodrgn")
        if n_all > 50_000:
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
            "reused": False, "n": n_all}


def prepared_lineage(ctx: JobContext, particles: InputData, prep: dict[str, Any]) -> dict[str, Any]:
    """What a latent output records about its particles (to select them, reuse the images, show them)."""
    prepared: dict[str, Any] = {"box": prep["box"], "datadir": prep.get("datadir")}
    for key in ("stack", "poses", "ctf", "ind"):
        if prep.get(key):
            prepared[key] = ctx.rel(prep[key])
    source = particles.meta.get("particles_file") or ctx.rel(particles.files[0])
    return {"particles": source, "box": prep["box"], "prepared": prepared}


def training_apix(ctx: JobContext, prepared: dict[str, Any], box: int) -> float | None:
    """Pixel size of the images used for training: original pixel size × original size / training size."""
    from cryoplug import particles as pt
    if not prepared.get("ctf") or not box:
        return None
    try:
        c = pt.load_array_pkl(ctx.abs(prepared["ctf"]))
        size, apix = float(c[0, 0]), float(c[0, 1])
    except Exception:  # noqa: BLE001 - unreadable or empty CTF file
        return None
    if size <= 0 or apix <= 0:
        return None
    if len(c) > 1 and np.ptp(c[:, 1]) > 1e-3:
        ctx.warn("The particles have several pixel sizes (optics groups): the first one is used for the volumes")
    return round(apix * size / box, 4)


# ------------------------------------------------------------ input check
def soft_sphere(shape: tuple[int, int, int], radius: float, edge: float) -> np.ndarray:
    d = shape[0]
    zz, yy, xx = np.indices(shape, dtype=np.float32)
    r = np.sqrt((xx - d / 2) ** 2 + (yy - d / 2) ** 2 + (zz - d / 2) ** 2)
    return np.clip(0.5 + 0.5 * np.cos(np.pi * np.clip((r - radius) / max(edge, 1e-6), 0, 1)), 0, 1).astype(np.float32)


def assess_backprojection(full: Path, half_a: Path | None = None, half_b: Path | None = None) -> dict[str, Any]:
    """Signal and contrast of a backprojection: half-set FSC inside a soft sphere, and whether the particle
    comes out as positive density (with the wrong contrast convention it is negative: hollow volumes)."""
    from cryoplug.mrc import MapVolume, fsc, resolution_at
    vol = MapVolume.read(full)
    d = vol.data.astype(np.float64)
    box = d.shape[0]
    apix = vol.pixel_size
    zz, yy, xx = np.indices(d.shape)
    r = np.sqrt((xx - box / 2) ** 2 + (yy - box / 2) ** 2 + (zz - box / 2) ** 2) / box
    inner, shell = r < 0.2, (r > 0.38) & (r < 0.48)
    contrast = float((d[inner].mean() - d[shell].mean()) / (d[shell].std() or 1.0))
    result: dict[str, Any] = {"apix": apix, "box": box, "contrast": contrast, "nyquist": 2 * apix}
    result["contrast_status"] = "good" if contrast > 1 else ("bad" if contrast < -1 else "warn")
    if half_a is not None and half_b is not None and half_a.exists() and half_b.exists():
        a, b = MapVolume.read(half_a), MapVolume.read(half_b)
        if a.data.shape == b.data.shape == vol.data.shape:
            mask = soft_sphere(a.data.shape, 0.4 * box, 0.06 * box)
            freqs, curve = fsc(a.data * mask, b.data * mask, a.voxel)
            res143, res05 = resolution_at(freqs, curve, 0.143, persist=2), resolution_at(freqs, curve, 0.5, persist=2)
            good, bad = max(4 * result["nyquist"], 12.0), max(10 * result["nyquist"], 30.0)
            result.update(frequency=freqs.tolist(), fsc=curve.tolist(), res143=res143, res05=res05, good_below=good,
                          fsc_status="good" if res143 <= good else ("bad" if res143 > bad else "warn"))
    statuses = [result["contrast_status"], result.get("fsc_status", "good")]
    result["verdict"] = "bad" if "bad" in statuses else ("warn" if "warn" in statuses else "good")
    return result


def backprojection_check(ctx: JobContext, prep: dict[str, Any], uninvert: bool, lazy: bool,
                         first: int = INPUT_CHECK_IMAGES, span: tuple[float, float] = (0.06, 0.1)) -> dict[str, Any] | None:
    """Backprojects the first images with their poses and CTF (Kinman et al. 2023, step 5): a map that looks
    like the consensus refinement shows that the inputs are read correctly."""
    outdir = ctx.path("input_check")
    n = int(prep.get("n") or 0)
    used = min(first, n) if first and n else (first or n)
    args = [ctx.executable("cryodrgn"), "backproject_voxel", prep["stack"], "--poses", prep["poses"], "--ctf",
            prep["ctf"], "-o", outdir]
    if first:
        args += ["--first", str(first)]
    if prep.get("ind"):
        args += ["--ind", prep["ind"]]
    if prep.get("datadir"):
        args += ["--datadir", prep["datadir"]]
    if uninvert:
        args.append("--uninvert-data")
    if lazy:
        args.append("--lazy")
    ctx.progress(span[0], f"Input check: backprojecting {used:,} particles" if used else "Input check: backprojection")
    code = ctx.run(args, tool="cryodrgn", check=False)
    full = outdir / "backproject.mrc"
    if outdir.is_file():  # versions before 3.4 write the map at the -o path
        full = outdir.rename(ctx.path("input_check.mrc"))
    if code != 0 or not full.exists():
        ctx.warn("The input check (backprojection) did not complete: see the log above")
        return None
    half_a, half_b = outdir / "half_map_a.mrc", outdir / "half_map_b.mrc"
    try:
        result = assess_backprojection(full, half_a, half_b)
    except Exception as exc:  # noqa: BLE001 - the check is informative
        ctx.warn(f"The backprojection could not be assessed: {exc}")
        return None
    result["n_images"] = used
    report_backprojection(ctx, result, full, used)
    ctx.progress(span[1], f"Input check: {'passed' if result['verdict'] == 'good' else 'see the report'}")
    return result


def report_backprojection(ctx: JobContext, result: dict[str, Any], full: Path, used: int) -> None:
    images = f"{used:,} particles" if used else "the particles"
    metrics = []
    if "res143" in result:
        metrics.append({"label": "Resolution of the backprojection (half-set FSC = 0.143)",
                        "value": f"{result['res143']:.1f} Å", "status": result["fsc_status"],
                        "target": f"≤ {result['good_below']:.0f} Å with {images}"})
        metrics.append({"label": "Half-set FSC = 0.5", "value": f"{result['res05']:.1f} Å", "status": None, "target": ""})
    c = result["contrast"]
    word = {"good": "positive", "bad": "negative", "warn": "unclear"}[result["contrast_status"]]
    metrics.append({"label": "Density of the particle", "value": f"{word} ({c:+.1f} SD)", "status": result["contrast_status"],
                    "target": "positive (molecule denser than the solvent)"})
    verdict = {"good": "inputs read correctly", "warn": "to check in 3D", "bad": "inputs probably wrong"}[result["verdict"]]
    metrics.append({"label": "Verdict", "value": verdict, "status": result["verdict"], "target": ""})
    ctx.add_metrics(f"Input check: backprojection of {images}", metrics, key="input_check")
    if "fsc" in result:
        ctx.add_plot("Input check: half-set FSC of the backprojection", [
            {"name": "FSC (soft spherical mask)", "x": result["frequency"], "y": result["fsc"]}],
            x_label="Resolution (Å)", y_label="FSC", x_kind="resolution", key="input_check_fsc",
            hlines=[{"y": 0.143, "label": "0.143"}, {"y": 0.5, "label": "0.5"}], y_range=[-0.1, 1.05],
            note="A plain backprojection of a few thousand images: it only has to show signal well beyond the first "
                 "shells. Open the 'backprojection' map in the 3D viewer with your consensus map: same shape, same hand.")
    advice = []
    if result.get("fsc_status") == "bad":
        advice.append("The backprojection does not resolve the particle: the poses or the CTF are probably not read "
                      "correctly. Check 'Poses from' (refinement, heterogeneous refinement or ab initio), that the particles "
                      "are those of the consensus refinement (not of an extraction or a 2D classification), and the image size.")
    if result["contrast_status"] == "bad":
        advice.append("The particle comes out as negative density: the images have the opposite contrast to what cryoDRGN "
                      "expects. Train with 'Do not invert the images' (advanced parameter), otherwise the volumes come out "
                      "hollow or inside out.")
    if result["verdict"] == "warn" and not advice:
        advice.append("The backprojection is weak or ambiguous. That can be normal for a small particle or few images: "
                      "compare it with the consensus map in the 3D viewer before going on.")
    if advice:
        ctx.add_text("Input check: what to do", "\n\n".join(advice), key="input_check_advice")
    ctx.add_output("backprojection", "map", full, f"Backprojection of {images} (input check)")
    ctx.add_highlight("Input check", {"good": "passed", "warn": "to check", "bad": "failed"}[result["verdict"]],
                      result["verdict"])


# -------------------------------------------------------------------- training
def model_preset(params: dict[str, Any], box: int, second_round: bool = False) -> dict[str, Any]:
    """Network size: small (256 × 3) for a first pass at 128 px, large (1024 × 3) for the final training (above
    128 px, or on particles selected in an earlier cryoDRGN latent space)."""
    choice = str(params.get("model") or MODEL_CHOICES[0])
    if choice.startswith("custom"):
        return {"name": "custom", "enc_dim": params["enc_dim"], "enc_layers": params["enc_layers"],
                "dec_dim": params["dec_dim"], "dec_layers": params["dec_layers"]}
    small = choice.startswith("small") or (choice.startswith("auto") and box <= 128 and not second_round)
    width = 256 if small else 1024
    return {"name": "small" if small else "large", "enc_dim": width, "enc_layers": 3, "dec_dim": width, "dec_layers": 3}


def train_settings(params: dict[str, Any], model: dict[str, Any]) -> dict[str, Any]:
    """The training options a latent output keeps, to continue the training later with the same network."""
    return {"zdim": params["zdim"], "model": model["name"], "enc_dim": model["enc_dim"], "enc_layers": model["enc_layers"],
            "dec_dim": model["dec_dim"], "dec_layers": model["dec_layers"], "batch_size": params["batch_size"],
            "beta": str(params.get("beta") or "").strip(), "amp": bool(params.get("amp", True)),
            "pose_sgd": bool(params.get("pose_sgd", False)), "checkpoint": int(params.get("checkpoint") or 1),
            "uninvert": bool(params.get("uninvert", False)), "extra": str(params.get("extra_args") or "")}


def train_args(settings: dict[str, Any], prep: dict[str, Any], version: tuple[int, int, int], lazy: bool, gpus: int,
               poses: str | Path | None = None, seed: int = 0) -> list[str]:
    """train_vae options (input files, network, training) for this cryoDRGN version."""
    args = [str(prep["stack"]), "--poses", str(poses or prep["poses"]), "--ctf", str(prep["ctf"]),
            "--zdim", str(settings["zdim"]), "--enc-dim", str(settings["enc_dim"]), "--enc-layers", str(settings["enc_layers"]),
            "--dec-dim", str(settings["dec_dim"]), "--dec-layers", str(settings["dec_layers"]),
            "-b", str(settings["batch_size"])]
    if prep.get("ind"):
        args += ["--ind", str(prep["ind"])]
    if prep.get("datadir"):
        args += ["--datadir", str(prep["datadir"])]
    if settings.get("beta"):
        args += ["--beta", settings["beta"]]
    if not settings.get("amp", True):
        if version >= (1, 0):
            args.append("--no-amp")
    elif version < (1, 0):
        args.append("--amp")
    if settings.get("pose_sgd"):
        args.append("--do-pose-sgd")
    if seed:
        args += ["--seed", str(seed)]
    if int(settings.get("checkpoint") or 1) != 1:
        args += ["--checkpoint", str(settings["checkpoint"])]
    if settings.get("uninvert"):
        args.append("--uninvert-data")
    if lazy:
        args.append("--lazy")
    if gpus > 1:
        args.append("--multigpu")
    args += shlex.split(settings.get("extra") or "")
    return args


def run_training(ctx: JobContext, args: list[str], workdir: Path, epochs: int, first_epoch: int, version: tuple[int, int, int],
                 load: Path | None = None, history: list[dict[str, Any]] | None = None,
                 span: tuple[float, float] = (0.1, 0.85)) -> int:
    """Runs train_vae and returns the last epoch with a checkpoint."""
    cmd = [ctx.executable("cryodrgn"), "train_vae", *args, "-n", str(epochs), "-o", workdir]
    if load is not None:
        cmd += ["--load", load]
    if version >= (3, 5):
        cmd.append("--no-analysis")  # analysed below, with CryoPlug's options
    last = epochs if version >= (3, 5) else epochs - 1  # checkpoints are numbered from 0 before 3.5
    monitor = TrainingMonitor(ctx, last, epochs - first_epoch + 1, span, history)
    ctx.progress(span[0], f"Training: epochs {first_epoch} to {epochs}" if first_epoch > 1 else f"Training ({epochs} epochs)")
    try:
        ctx.run(cmd, tool="cryodrgn", on_line=monitor.on_line)
    except JobError as exc:
        hint = training_failure_hint(ctx)
        raise JobError(f"{exc}{': ' + hint if hint else ''}") from None
    saved = checkpoint_epochs(workdir)
    if not saved:
        raise JobError("cryoDRGN finished without saving its weights (see the log)")
    if not monitor.rows:  # no epoch line seen (other output format): read the log of the training folder
        loss_plots(ctx, (history or []) + parse_losses(workdir / "run.log"))
    per = monitor.per_epoch()
    if per:
        ctx.add_highlight("Time per epoch", fmt_duration(per))
    return saved[-1]


def training_failure_hint(ctx: JobContext) -> str:
    """A likely cause for a failed training, from the end of the log (troubleshooting table of the protocol)."""
    try:
        tail = ctx.log_path.read_text(errors="replace")[-20_000:]
    except OSError:
        return ""
    if "CUDA out of memory" in tail or "OutOfMemoryError" in tail:
        return ("the GPU ran out of memory: lower the batch size, use a smaller image size, or the small network")
    if "MemoryError" in tail or "Killed" in tail or "Cannot allocate memory" in tail:
        return "the machine ran out of memory: tick 'Lazy loading' (images read from disk as needed)"
    if "AssertionError" in tail and ("amp" in tail.lower() or "scaler" in tail.lower() or "nan" in tail.lower()):
        return "numerical instability with mixed precision: train again with 'Mixed precision' off"
    if "must be greater than" in tail and "epoch" in tail:
        return "the number of epochs must be larger than the epoch of the checkpoint the training continues from"
    return ""


# -------------------------------------------------------------------- analysis
def analysis_params() -> list[Param]:
    return [
        Param("ksample", "int", 20, label="Volumes sampled (k-means)", min=2, max=200,
              help="The latent space is split into this many k-means clusters; the volume of the particle nearest to each "
                   "cluster centre is generated (20 for an overview, 50-100 to look for rare states)."),
        Param("apix", "float", 0.0, label="Pixel size of the volumes", unit="Å", min=0.0, advanced=True,
              help="0 = from the CTF parameters (original pixel size × original size / training size)."),
        Param("flip", "bool", False, label="Flip handedness of the volumes", advanced=True,
              help="When the consensus refinement had the wrong hand."),
        Param("lowpass", "float", 0.0, label="Low-pass the volumes", unit="Å", min=0.0, advanced=True, help="0 = none."),
    ]


def run_analysis(ctx: JobContext, workdir: Path, epoch: int, outdir: Path, params: dict[str, Any],
                 version: tuple[int, int, int], apix: float | None, box: int) -> None:
    """cryodrgn analyze: PCA and UMAP of the latent space, k-means cluster volumes, PC trajectories."""
    args = [ctx.executable("cryodrgn"), "analyze", workdir, str(epoch), "-o", outdir,
            "--ksample", str(params["ksample"]), "--pc", str(params.get("pcs", 2))]
    n_per_pc = int(params.get("n_per_pc", 10))
    if n_per_pc != 10:
        if version >= (4, 2):
            args += ["--n-per-pc", str(n_per_pc)]
        else:
            ctx.warn(f"cryoDRGN {version_text(version)} generates 10 volumes per trajectory (--n-per-pc needs 4.2)")
    down = int(params.get("downsample") or 0)
    apix = float(params.get("apix") or 0) or apix
    if apix:  # cryoDRGN writes downsampled volumes with the pixel size it is given: scale it here
        args += ["--Apix", f"{apix * (box / down if down and box else 1):.4f}".rstrip("0").rstrip(".")]
    if params.get("flip"):
        args.append("--flip")
    if params.get("invert"):
        args.append("--invert")
    if down:
        args += ["-d", str(down)]
    if params.get("lowpass"):
        args += ["--low-pass", f"{params['lowpass']:g}"]
    ctx.run(args, tool="cryodrgn")


def find_kmeans_dir(analyze_dir: Path, k: int) -> Path | None:
    kdir = analyze_dir / f"kmeans{k}"
    if kdir.is_dir():
        return kdir
    return next((d for d in sorted(analyze_dir.glob("kmeans*")) if d.is_dir()), None)


def particle_covariates(ctx: JobContext, lineage: dict[str, Any], workdir: Path, epoch: int,
                        n: int) -> tuple[dict[str, dict], dict[str, np.ndarray]]:
    """Values per particle that are not structural (defocus, viewing direction, shift), to colour the latent
    space and to test whether it encodes them (Kinman et al. 2023: non-structural heterogeneity)."""
    from cryoplug import particles as pt
    prepared = lineage.get("prepared") or {}
    colorings: dict[str, dict] = {}
    aux: dict[str, np.ndarray] = {}
    ind = None
    try:
        if prepared.get("ind"):
            ind = pt.load_array_pkl(ctx.abs(prepared["ind"])).astype(np.int64)
    except Exception as exc:  # noqa: BLE001 - informative only
        ctx.warn(f"Particle indices not read ({exc}): no defocus or pose colouring")
        return colorings, aux

    def subset(a: np.ndarray, filtered: bool = True) -> np.ndarray | None:
        a = np.asarray(a)
        if filtered and ind is not None and len(a) > int(ind.max(initial=0)) and len(a) != n:
            a = a[ind]
        return a if len(a) == n else None

    box_orig = apix_orig = 0.0
    if prepared.get("ctf") and ctx.abs(prepared["ctf"]).exists():
        try:
            c = subset(pt.load_array_pkl(ctx.abs(prepared["ctf"])))
        except Exception:  # noqa: BLE001
            c = None
        if c is not None and c.ndim == 2 and c.shape[1] >= 4:
            box_orig, apix_orig = float(c[0, 0]), float(c[0, 1])
            defocus = (c[:, 2] + c[:, 3]) / 2e4
            if np.ptp(defocus) > 1e-4:
                colorings["defocus"] = coloring(defocus, "Defocus", "µm",
                                                note="A latent space ordered by defocus reflects the images (CTF, ice "
                                                     "thickness), not the structure.")
    sgd = workdir / f"pose.{epoch}.pkl"
    source = sgd if sgd.exists() else (ctx.abs(prepared["poses"]) if prepared.get("poses") else None)
    if source is not None and source.exists():
        try:
            obj = pt.load_pkl(source)
            rot, trans = (obj[0], obj[1]) if isinstance(obj, (tuple, list)) else (obj, None)
            rot = subset(rot, filtered=source != sgd)
            trans = subset(trans, filtered=source != sgd) if trans is not None else None
        except Exception:  # noqa: BLE001
            rot = trans = None
        if rot is not None and rot.ndim == 3 and rot.shape[1:] == (3, 3):
            direction = np.asarray(rot[:, 2, :], dtype=np.float64)  # projection axis in the frame of the map
            direction /= np.maximum(np.linalg.norm(direction, axis=1, keepdims=True), 1e-9)
            aux["direction"] = direction
            colorings["tilt"] = coloring(np.degrees(np.arccos(np.clip(direction[:, 2], -1, 1))), "Viewing tilt", "°",
                                         lo=0, hi=180, note="Angle between the viewing direction and the z axis of the map.")
            colorings["azimuth"] = coloring(np.degrees(np.arctan2(direction[:, 1], direction[:, 0])), "Viewing azimuth", "°",
                                            cyclic=True, lo=-180, hi=180, note="Direction of view around the z axis.")
        if trans is not None and np.asarray(trans).ndim == 2:
            shift = np.linalg.norm(np.asarray(trans, dtype=np.float64), axis=1)
            unit = "Å" if box_orig and apix_orig else "px"
            shift *= box_orig * apix_orig if unit == "Å" else float(lineage.get("box") or 1)
            if np.ptp(shift) > 0:
                colorings["shift"] = coloring(shift, "In-plane shift", unit,
                                              note="Large shifts can mean off-centre or mis-picked particles.")
                aux["shift"] = shift
    if "defocus" in colorings:
        aux["defocus"] = colorings["defocus"]["values"]
    return colorings, aux


def covariate_r2(z: np.ndarray, targets: dict[str, np.ndarray], k: int = 15, sample: int = 3000,
                 seed: int = 0) -> dict[str, tuple[float, float]]:
    """How well the latent coordinates predict each covariate: R² of a linear fit and of the average of the
    k nearest neighbours in the latent space (catches non-linear dependence), on a random subset."""
    n = len(z)
    rng = np.random.default_rng(seed)
    idx = np.sort(rng.choice(n, size=min(n, sample), replace=False))
    zs = np.asarray(z, dtype=np.float64).reshape(n, -1)[idx]
    zs = (zs - zs.mean(0)) / (zs.std(0) + 1e-9)
    k = max(1, min(k, len(zs) - 1))
    sq = (zs ** 2).sum(1)
    d2 = sq[:, None] + sq[None, :] - 2 * zs @ zs.T
    np.fill_diagonal(d2, np.inf)
    nn = np.argpartition(d2, k, axis=1)[:, :k]
    design = np.c_[zs, np.ones(len(zs))]
    out = {}
    for name, values in targets.items():
        y = np.asarray(values, dtype=np.float64).reshape(n, -1)[idx]
        ss_tot = float(((y - y.mean(0)) ** 2).sum())
        if ss_tot <= 1e-12:
            continue
        r2_knn = 1 - float(((y - y[nn].mean(axis=1)) ** 2).sum()) / ss_tot
        coef, *_ = np.linalg.lstsq(design, y, rcond=None)
        r2_lin = 1 - float(((y - design @ coef) ** 2).sum()) / ss_tot
        out[name] = (max(0.0, r2_lin), max(0.0, r2_knn))
    return out


def resize(img: np.ndarray, size: int) -> np.ndarray:
    from scipy import ndimage
    return ndimage.zoom(np.asarray(img, dtype=np.float32), size / max(img.shape), order=1)[:size, :size]


def crop_box(region: np.ndarray, margin: int = 4) -> tuple[slice, slice, slice]:
    """A cube around the molecule (same for every volume), so that thumbnails show it large."""
    idx = np.argwhere(region)
    if not len(idx):
        return (slice(None),) * 3
    lo, hi = idx.min(0) - margin, idx.max(0) + margin + 1
    centre, half = (lo + hi) / 2, max(8, int(np.ceil((hi - lo).max() / 2)))
    start = np.clip(np.round(centre - half).astype(int), 0, None)
    return tuple(slice(int(a), int(a) + 2 * half) for a in start)


def projection_tiles(stack: np.ndarray, size: int = 72, axes: tuple[int, ...] = (0, 1, 2),
                     crop: tuple[slice, slice, slice] | None = None) -> list[list[np.ndarray]]:
    """Projections of volumes (n, z, y, x) along the given axes, grey levels shared by all (uint8, y up)."""
    proj = [[(v[crop] if crop else v).mean(axis=a) for a in axes] for v in stack]
    flat = np.concatenate([p.ravel() for views in proj for p in views])
    lo, hi = np.percentile(flat, [1, 99.7])
    out = []
    for views in proj:
        row = []
        for p in views:
            img = np.clip((resize(p, size) - lo) / ((hi - lo) or 1.0), 0, 1)
            row.append((img[::-1] * 255).astype(np.uint8))
        out.append(row)
    return out


def strip_png(path: Path, tiles: list[np.ndarray], gap: int = 3) -> Path:
    from cryoplug.imaging import write_png
    h = max(t.shape[0] for t in tiles)
    w = sum(t.shape[1] for t in tiles) + gap * (len(tiles) - 1)
    img = np.zeros((h, w, 4), dtype=np.uint8)
    x = 0
    for t in tiles:
        img[: t.shape[0], x: x + t.shape[1], :3] = t[..., None]
        img[: t.shape[0], x: x + t.shape[1], 3] = 255
        x += t.shape[1] + gap
    return write_png(path, img)


def high_frequency_share(v: np.ndarray) -> float:
    """Share of a volume's power at high spatial frequency: low for featureless blobs (aggregates, junk)."""
    power = np.abs(np.fft.rfftn(v - v.mean())) ** 2
    freq = [np.fft.fftfreq(s) for s in v.shape[:-1]] + [np.fft.rfftfreq(v.shape[-1])]
    k = np.sqrt(sum(f ** 2 for f in np.meshgrid(*freq, indexing="ij")))
    band = (k > 0.02) & (k < 0.5)
    return float(power[band & (k > 0.12)].sum() / (power[band].sum() or 1.0))


def cluster_gallery(ctx: JobContext, files: list[Path], counts: np.ndarray, n: int, folder: str = "gallery",
                    prefix: str = "cluster") -> list[dict[str, Any]]:
    """Three projections of each cluster volume (common grey scale) and warning flags for junk-like volumes:
    weak (little density above the common threshold), noisy (density scattered outside the molecule) or blurry
    (no fine detail: aggregates, junk)."""
    from scipy import ndimage

    from cryoplug import masks as mk
    try:
        vols, _factor = load_frames(files, max_box=64)
    except Exception as exc:  # noqa: BLE001 - the gallery is informative
        ctx.warn(f"Cluster gallery not made: {exc}")
        return []
    stack = np.stack([v.data for v in vols])
    mean = stack.mean(axis=0)
    region = molecule_region(mean)
    level = mk.otsu_threshold(mk.sample_values(mean))
    occupied = (stack > level).reshape(len(stack), -1).sum(axis=1).astype(np.float64)
    outside = ~ndimage.binary_dilation(region, iterations=3)
    noise = np.array([float(v[outside].std() / (v[region].std() or 1.0)) if outside.any() else 0.0 for v in stack])
    detail = np.array([high_frequency_share(v) for v in stack])
    med_occ, med_noise, med_detail = float(np.median(occupied)) or 1.0, float(np.median(noise)), float(np.median(detail)) or 1.0
    tiles = projection_tiles(stack, crop=crop_box(region))
    ctx.path(folder).mkdir(exist_ok=True)
    out = []
    for i, views in enumerate(tiles):
        png = strip_png(ctx.path(folder, f"{prefix}_{i + 1:03d}.png"), views)
        flags = []
        if occupied[i] < 0.6 * med_occ:
            flags.append("weak")
        if noise[i] > max(2.0 * med_noise, med_noise + 0.04):
            flags.append("noisy")
        if detail[i] < 0.5 * med_detail:
            flags.append("blurry")
        if i < len(counts) and n and counts[i] < 0.01 * n:
            flags.append("rare")
        out.append({"image": ctx.rel(png), "flags": flags, "occupancy": round(float(occupied[i] / med_occ), 2),
                    "noise": round(float(noise[i]), 3), "detail": round(float(detail[i] / med_detail), 2)})
    return out


def viewing_heatmap(ctx: JobContext, direction: np.ndarray, key: str | None = None) -> None:
    """Distribution of the viewing directions (azimuth × elevation, % of the particles)."""
    el = 90 - np.degrees(np.arccos(np.clip(direction[:, 2], -1, 1)))
    az = np.degrees(np.arctan2(direction[:, 1], direction[:, 0]))
    hist, _ye, _xe = np.histogram2d(el, az, bins=[12, 24], range=[[-90, 90], [-180, 180]])
    hist = hist[::-1] * 100.0 / max(1, len(direction))  # top row = +90°
    ylab = [f"{82.5 - 15 * i:+.0f}°" for i in range(12)]  # bin centres
    xlab = [f"{-180 + 15 * j}°" for j in range(24)]
    ctx.add_heatmap("Viewing directions of the particles", xlab, ylab, hist.round(3).tolist(), unit="%",
                    x_label="Azimuth", y_label="Elevation", key=key,
                    note="From the consensus poses. Even coverage is best; empty bands are missing views, which limit "
                         "what any reconstruction can show. Colour the latent space by viewing tilt or azimuth to check "
                         "that its clusters are not just views.")


R2_MEANING = {
    "Defocus": "the network encodes image properties (CTF, ice thickness) along with the structure",
    "Viewing direction": "part of the latent space follows the views: pose errors, preferred orientation, or views of "
                         "junk particles",
    "In-plane shift": "off-centre particles group together: mis-picked or partial particles",
}


def diagnostics(ctx: JobContext, z: np.ndarray, aux: dict[str, np.ndarray]) -> dict[str, tuple[float, float]]:
    targets = {}
    if "defocus" in aux:
        targets["Defocus"] = aux["defocus"]
    if "direction" in aux:
        targets["Viewing direction"] = aux["direction"]
    if "shift" in aux:
        targets["In-plane shift"] = aux["shift"]
    if not targets or len(z) < 50:
        return {}
    try:
        r2 = covariate_r2(z, targets)
    except Exception as exc:  # noqa: BLE001 - informative only
        ctx.warn(f"Latent space diagnostics not computed: {exc}")
        return {}
    rows = []
    for name, (lin, knn) in r2.items():
        best = max(lin, knn)
        status = "good" if best < 0.1 else ("warn" if best >= 0.3 else "")
        meaning = "not encoded: the latent space reflects the structure" if best < 0.1 else (
            ("strongly encoded: " if best >= 0.3 else "weakly encoded: ") + R2_MEANING.get(name, ""))
        rows.append([name, f"{lin:.2f}", f"{knn:.2f}", status, meaning])
    ctx.add_table("Is the latent space structural? (non-structural heterogeneity)",
                  ["Particle property", "Linear R²", "Neighbour R²", "Status", "Meaning"], rows,
                  note="R² = share of the property's variation predicted from the latent coordinates (by a linear fit, and "
                       "by the average of the 15 nearest particles in the latent space). Near 0: the latent space does not "
                       "depend on it, as it should. Colour the explorer by the property to see where it varies.")
    return r2


def next_steps(box: int, model: str, flagged: dict[str, list[int]], counts: np.ndarray, n: int, outliers: int,
               r2: dict[str, tuple[float, float]], landscape: bool = False) -> str:
    """What to do with these results, following the protocol and what the analysis found."""
    lines = []
    first_pass = box <= 128 or model == "small"
    junk = sorted(set(flagged.get("weak", []) + flagged.get("noisy", []) + flagged.get("blurry", [])))
    if junk:
        share = 100.0 * sum(int(counts[i]) for i in junk if i < len(counts)) / max(1, n)
        parts = []
        if flagged.get("weak"):
            parts.append(f"weak density: {', '.join(str(i + 1) for i in flagged['weak'])}")
        if flagged.get("noisy"):
            parts.append(f"noisy: {', '.join(str(i + 1) for i in flagged['noisy'])}")
        if flagged.get("blurry"):
            parts.append(f"no fine detail: {', '.join(str(i + 1) for i in flagged['blurry'])}")
        lines.append(f"• Junk? Clusters flagged in the gallery ({'; '.join(parts)}) hold {share:.1f} % of the particles. "
                     "Look at them in 3D (double-click), then select them in the explorer and use “Remove…”.")
    elif first_pass:
        lines.append("• Junk: no cluster volume looks weak or noisy at the common threshold. Still look at every volume "
                     "in 3D (Play in 3D): junk often shows as broken or blobby density.")
    if outliers:
        lines.append(f"• Outliers: {outliers:,} particles ({100.0 * outliers / max(1, n):.1f} %) lie far from the others "
                     "(‖z‖ > mean + 2 SD; colour the explorer by ‖z‖). Remove them with the “Outliers” button of the "
                     "explorer when they look like junk.")
    worst = [(name, max(v)) for name, v in r2.items() if max(v) >= 0.3]
    for name, value in worst:
        lines.append(f"• {name} is encoded in the latent space (R² {value:.2f}): colour the explorer by it. Clusters "
                     "that follow it are rarely real states.")
    if landscape:
        lines.append("• States: play the state volumes in 3D; the particles of each state can be selected (cluster "
                     "chips of the explorer) and refined in CryoSPARC to confirm them.")
    elif first_pass:
        lines.append("• Then: this first pass (small network, ≤ 128 px) is for cleaning. Train again on the cleaned "
                     "particles at 256 px with the large network (cryoDRGN training on the selection).")
    else:
        lines.append("• Convergence: run “cryoDRGN convergence check” before trusting fine details.")
        lines.append("• States and motions: compare the cluster volumes (Play in 3D), follow a path between clusters "
                     "(Trajectory…), or group the volumes into states with the landscape analysis.")
        lines.append("• Validation: refine the particles of each state in CryoSPARC (homogeneous refinement). A feature "
                     "seen only in cryoDRGN volumes needs this confirmation.")
    return "\n".join(lines)


def finish_analysis(ctx: JobContext, workdir: Path, epoch: int, analyze_dir: Path, k: int, lineage: dict[str, Any],
                    title: str | None = None) -> dict[str, Any]:
    """Latent output, explorer, cluster volumes, PC trajectories and diagnostics from a cryodrgn analyze folder."""
    from cryoplug import particles as pt
    from cryoplug.mrc import MapVolume
    _w, zfile = weights_and_z(workdir, epoch)
    if not zfile.exists():
        raise JobError(f"Latent vectors not found: {zfile}")
    z = pt.load_array_pkl(zfile).astype(np.float32)
    if z.ndim == 1:
        z = z[:, None]
    n, zdim = z.shape
    if not find_kmeans_dir(analyze_dir, k) and find_kmeans_dir(workdir / f"analyze.{epoch}", k):
        analyze_dir = workdir / f"analyze.{epoch}"  # cryoDRGN-AI models are analysed in the training folder
    kdir = find_kmeans_dir(analyze_dir, k)
    labels = centres_ind = None
    if kdir and (kdir / "labels.pkl").exists():
        labels = pt.load_array_pkl(kdir / "labels.pkl").astype(np.int32)
        if len(labels) != n:
            ctx.warn(f"The k-means labels have {len(labels):,} entries for {n:,} particles: not used")
            labels = None
        elif labels.min() == 1:
            labels -= 1
    if kdir and (kdir / "centers_ind.txt").exists():
        centres_ind = np.loadtxt(kdir / "centers_ind.txt", dtype=int, ndmin=1)
    umap = None
    if (analyze_dir / "umap.pkl").exists():
        umap = pt.load_array_pkl(analyze_dir / "umap.pkl").astype(np.float32)
        if umap.ndim != 2 or len(umap) != n:
            umap = None
    mean, axes, var = pt.pca_components(z, 10)
    proj = ((z - mean) @ axes.T).astype(np.float32)
    pca2 = proj[:, :2] if proj.shape[1] >= 2 else np.c_[proj, np.zeros(n, np.float32)]
    axes2 = axes[:2] if len(axes) >= 2 else np.r_[axes, np.zeros_like(axes)]

    kfiles = sorted(kdir.glob("vol_*.mrc")) if kdir else []
    nk = len(kfiles) or (int(labels.max()) + 1 if labels is not None else 0)
    counts = np.bincount(labels, minlength=nk) if labels is not None else np.zeros(nk, int)
    apix = round(MapVolume.read(kfiles[0], header_only=True).pixel_size, 4) if kfiles else None
    gallery = cluster_gallery(ctx, kfiles, counts, n) if len(kfiles) >= 2 else []
    flagged: dict[str, list[int]] = {}
    for i, g in enumerate(gallery):
        for f in g["flags"]:
            flagged.setdefault(f, []).append(i)
    if kfiles:
        klabels = [f"Cluster {i + 1} · {100.0 * counts[i] / n:.1f} %" if i < len(counts) else f"Cluster {i + 1}"
                   for i in range(len(kfiles))]
        add_series(ctx, "kmeans", kfiles, f"Volumes of the {len(kfiles)} latent clusters", klabels,
                   {"kind": "kmeans", "particles": counts.tolist()[: len(kfiles)]})
    for pc in range(1, 10):
        pfiles = sorted((analyze_dir / f"pc{pc}").glob("vol_*.mrc"))
        if not pfiles:
            break
        add_series(ctx, f"pc{pc}", pfiles, f"Trajectory along PC{pc} (5th → 95th percentile)",
                   [f"PC{pc} · {j + 1}/{len(pfiles)}" for j in range(len(pfiles))], {"kind": f"pc{pc}"})

    covs, aux = particle_covariates(ctx, lineage, workdir, epoch, n)
    znorm = np.linalg.norm(z, axis=1)
    threshold = float(znorm.mean() + 2 * znorm.std())
    outliers = int((znorm > threshold).sum())
    colorings = {"znorm": coloring(znorm, "‖z‖ (distance from the centre)",
                                   note="Particles far from the centre of the latent space: often junk or rare views.")}
    for i in range(min(3, proj.shape[1])):
        colorings[f"pc{i + 1}"] = coloring(proj[:, i], f"PC{i + 1}", note=f"{var[i]:.1f} % of the variance of the latent space")
    colorings.update(covs)

    arrays: dict[str, np.ndarray] = {"z": z, "pca": pca2.astype(np.float32), "pca_mean": mean.astype(np.float32),
                                     "pca_axes": axes2.astype(np.float32), "pca_var": np.asarray(var, np.float32),
                                     "znorm": znorm.astype(np.float32)}
    if umap is not None:
        arrays["umap"] = umap
    if labels is not None:
        arrays["labels"] = labels
    if centres_ind is not None:
        arrays["centers_ind"] = centres_ind
    for key, c in covs.items():
        arrays[f"cov_{key}"] = np.asarray(c["values"], dtype=np.float32)
    if "direction" in aux:
        arrays["direction"] = aux["direction"].astype(np.float32)
    npz = ctx.path("latent.npz")
    np.savez(npz, **arrays)

    embeddings = {"UMAP": umap} if umap is not None else {}
    embeddings["PCA"] = pca2
    volumes = {i: ctx.rel(f) for i, f in enumerate(kfiles)}
    pc_label = (lambda i: f"PC{i + 1} ({var[i]:.1f} %)" if i < len(var) else f"PC{i + 1}")
    latent_report(ctx, embeddings, labels, title or f"Latent space of {n:,} particles (epoch {epoch})",
                  {"UMAP": ("UMAP 1", "UMAP 2"), "PCA": (pc_label(0), pc_label(1))}, volumes, centres_ind,
                  colorings=colorings, clusters_info=gallery,
                  extra={"outliers": {"threshold": round(threshold, 4), "count": outliers, "zscore": 2.0},
                         "zdim": int(zdim), "epoch": int(epoch)})
    r2 = diagnostics(ctx, z, aux)
    if len(var) >= 2:
        pcs = list(range(1, len(var) + 1))
        ctx.add_plot("Variance of the latent space along its principal components", [
            {"name": "Explained variance (%)", "x": pcs, "y": var},
            {"name": "Cumulative (%)", "x": pcs, "y": np.round(np.cumsum(var), 2).tolist()},
        ], x_label="PC", y_label="%", y_range=[0, 105],
            note="One dominant component: the heterogeneity is mostly one motion or one composition change. Spread over "
                 "many components: several independent changes (or noise). The UMAP map uses all of them.")
    if "direction" in aux:
        viewing_heatmap(ctx, aux["direction"])
    if labels is not None:
        rows = []
        for i, c in enumerate(counts):
            flags = ", ".join(gallery[i]["flags"]) if i < len(gallery) and gallery[i]["flags"] else ""
            rows.append([i + 1, int(c), f"{100.0 * c / n:.1f}", flags or "—"])
        ctx.add_table("Latent clusters (k-means)", ["Cluster", "Particles", "%", "Flags"], rows,
                      note="weak: little density at the threshold shared by all the volumes (missing parts, or junk); "
                           "noisy: density scattered outside the molecule; blurry: no fine detail (aggregates, junk); "
                           "rare: under 1 % of the particles. Flags are hints: look at the volumes in 3D before removing.")
    if len(kfiles) >= 3:
        series_similarity(ctx, kfiles, [f"Cluster {i + 1}" for i in range(len(kfiles))],
                          "Similarity of the cluster volumes (correlation)")
    model = (lineage.get("train") or {}).get("model", "")
    ctx.add_text("What to do next", next_steps(int(lineage.get("box") or 0), model, flagged, counts, n, outliers, r2))
    ctx.add_text("Explore further with cryoDRGN",
                 f"Interactive dashboard of cryoDRGN (particle selection, latent panels), on the server:\n"
                 f"  cryodrgn dashboard {workdir} --epoch {epoch}\n"
                 f"(then open http://localhost:5050 through an SSH tunnel: ssh -L 5050:localhost:5050 <server>)\n\n"
                 f"Jupyter notebooks and cryoDRGN's own figures: {analyze_dir}")
    w, zf = weights_and_z(workdir, epoch)
    files = [npz, zf, w, workdir / "config.yaml"]
    meta = {**lineage, "method": "cryoDRGN", "workdir": ctx.rel(workdir), "epoch": int(epoch), "zdim": int(zdim),
            "n_particles": int(n), "k": len(kfiles) or k, "apix": apix}
    meta.setdefault("workdirs", [ctx.rel(workdir)])
    ctx.add_output("latent", "latent", [f for f in files if Path(f).exists()],
                   f"Latent space (cryoDRGN, {zdim}-D, epoch {epoch})", meta=meta)
    ctx.add_highlight("Particles", f"{n:,}")
    ctx.add_highlight("Latent", f"{zdim}-D · {len(kfiles)} clusters")
    junk = set(flagged.get("weak", []) + flagged.get("noisy", []) + flagged.get("blurry", []))
    if junk:
        ctx.add_highlight("Flagged clusters", len(junk), "warn")
    return {"n": n, "zdim": zdim, "flagged": flagged, "outliers": outliers, "r2": r2}


def analysis_lineage(lat: InputData) -> dict[str, Any]:
    """What a new analysis of a latent space keeps from it."""
    return {k: lat.meta[k] for k in ("particles", "box", "prepared", "train", "workdirs") if k in lat.meta}


def analysis_apix(ctx: JobContext, lineage: dict[str, Any], lat_meta: dict[str, Any] | None = None) -> float | None:
    apix = training_apix(ctx, lineage.get("prepared") or {}, int(lineage.get("box") or 0))
    return apix or (lat_meta or {}).get("apix")


# ======================================================================= jobs
def gpus_param() -> Param:
    return Param("gpus", "int", 1, label="GPUs", min=1, max=16,
                 help="More than one enables --multigpu (worth it at 256 px with the large network).")


def lazy_param() -> Param:
    return Param("lazy", "bool", False, label="Lazy loading",
                 help="Reads the images from disk as needed instead of loading them all in memory (needs a fast disk). "
                      "Turned on automatically when they would not fit in memory.")


def latent_slot(help_text: str = "From a cryoDRGN training or analysis.") -> Slot:
    return Slot("latent", ("latent",), "cryoDRGN latent space", help=help_text)


ANALYSIS_OUTPUTS = [OutputDef("latent", "latent", "Latent space"),
                    OutputDef("kmeans", "volume_series", "Volumes of the latent clusters"),
                    OutputDef("pc1", "volume_series", "Trajectory along PC1"),
                    OutputDef("pc2", "volume_series", "Trajectory along PC2")]


@register
class CryodrgnBackproject(JobType):
    name = "cryodrgn_backproject"
    title = "cryoDRGN input check (backprojection)"
    category = "Heterogeneity"
    tool = "cryodrgn"
    software = ["cryoDRGN"]
    gpu = 1
    cpus = 4
    description = ("Checks that cryoDRGN reads the particles correctly before a long training (Kinman et al. 2023): the "
                   "poses and CTF are converted, the images downsampled, and the first particles backprojected into a map "
                   "to compare with the consensus refinement (same shape, positive density, signal in the half-set FSC). "
                   "The prepared particles are reused by the trainings that follow.")
    inputs = [Slot("particles", ("particles",), "Particles",
                   help="With the poses of a consensus refinement (Import from CryoSPARC, Import particles, or a selection).")]
    params = [
        Param("box", "int", 128, label="Image size", unit="px", min=0,
              help="Size the images are downsampled to (the size of the training that follows); 0 keeps the original size."),
        Param("images", "int", INPUT_CHECK_IMAGES, label="Images backprojected", min=0,
              help="The first N particles (0 = all of them: a quick homogeneous reconstruction)."),
        Param("pose_source", "choice", "refinement", choices=POSE_SOURCES, label="Poses from",
              help="Type of CryoSPARC job the particles come from."),
        Param("uninvert", "bool", False, label="Do not invert the images", advanced=True,
              help="Test the other contrast convention when the default one gives negative density."),
        lazy_param(),
    ]
    outputs = [OutputDef("backprojection", "map", "Backprojection"),
               OutputDef("particles_prepared", "particles", "Particles prepared for cryoDRGN")]

    @classmethod
    def validate(cls, params, connected):
        box = int(params.get("box") or 0)
        return [f"The image size must be even (got {box})"] if box % 2 else []

    def run(self, ctx: JobContext) -> None:
        p = ctx.params
        particles = ctx.require("particles")
        cryodrgn_version(ctx)
        prep = prepare_particles(ctx, particles, p["box"], p["pose_source"])
        lazy = decide_lazy(ctx, p["lazy"], prep["n"], prep["box"])
        result = backprojection_check(ctx, prep, p["uninvert"], lazy, first=p["images"], span=(0.3, 0.95))
        if result is None:
            raise JobError("The backprojection failed (see the log): cryoDRGN cannot read these particles as they are")
        if prep.get("reused"):  # make the prepared particles available from this job too
            ctx.add_output("particles_prepared", "particles", particles.files, particles.label or "Particles",
                           meta=particles.meta)


@register
class CryodrgnTrain(JobType):
    name = "cryodrgn_train"
    title = "cryoDRGN training"
    category = "Heterogeneity"
    tool = "cryodrgn"
    software = ["cryoDRGN"]
    gpu = 1
    gpu_param = "gpus"
    cpus = 8
    description = ("Heterogeneous reconstruction with cryoDRGN: a neural network learns a latent space of the "
                   "conformations and compositions in the particles (poses of the consensus refinement). The inputs are "
                   "checked first, the losses followed live, then the latent space is analysed: interactive explorer "
                   "(colour by defocus, views...), cluster volumes with junk warnings, trajectories along the main "
                   "components, and advice on what to do next.")
    inputs = [Slot("particles", ("particles",), "Particles",
                   help="With the poses of a consensus refinement (Import from CryoSPARC, Import particles, or a selection).")]
    params = [
        Param("box", "int", 128, label="Image size for training", unit="px", min=0,
              help="Images are Fourier-cropped to this size (even). Protocol: 128 px for the first pass (finding the "
                   "junk), 256 px for the final training; 0 keeps the original size."),
        Param("zdim", "int", 8, label="Latent dimensions", min=1, max=128,
              help="Size of the latent space. 8 suits most datasets (the protocol's choice); 1 or 2 only for a single "
                   "simple motion; more than 10 rarely helps."),
        Param("epochs", "int", 50, label="Epochs", min=1,
              help="Full passes over the particles. The protocol trains 50 epochs, checks convergence (cryoDRGN "
                   "convergence check) and continues to 100 when needed."),
        Param("model", "choice", MODEL_CHOICES[0], choices=MODEL_CHOICES, label="Network size",
              help="small (256 × 3 layers): fast, for the first pass at 128 px. large (1024 × 3): the final training at "
                   "256 px, resolves finer differences. auto, as in the protocol: small for a first pass up to 128 px; large "
                   "above 128 px or for particles selected in an earlier cryoDRGN latent space (the second round)."),
        Param("check_inputs", "choice", CHECK_CHOICES[0], choices=CHECK_CHOICES, label="Input check",
              help="Backprojects the first 10,000 particles before training (a few minutes): a map like the consensus "
                   "refinement shows that poses, CTF and contrast are read correctly. 'stop if it fails' saves the GPU "
                   "time of a training on wrong inputs."),
        *analysis_params(),
        gpus_param(),
        lazy_param(),
        Param("pose_source", "choice", "refinement", choices=POSE_SOURCES, label="Poses from", advanced=True,
              help="Type of CryoSPARC job the particles come from (shifts of heterogeneous refinements are rescaled; ab "
                   "initio uses its class 0 poses)."),
        Param("batch_size", "int", 16, label="Batch size", min=1, advanced=True,
              help="Images per optimisation step (cryoDRGN's default: 16). Lower it if the GPU runs out of memory."),
        Param("beta", "str", "", label="β (weight of the KL term)", advanced=True, placeholder="1/zdim",
              help="Empty = 1/zdim (cryoDRGN's default). Larger values give a smoother, less detailed latent space."),
        Param("amp", "bool", True, label="Mixed precision", advanced=True,
              help="Faster training on recent GPUs. Turn it off if the training stops on an assertion error "
                   "(numerical instability)."),
        Param("pose_sgd", "bool", False, label="Refine the poses", advanced=True,
              help="Optimises the poses during training (--do-pose-sgd), for poses of moderate accuracy. The protocol "
                   "keeps the consensus poses."),
        Param("seed", "int", 0, label="Random seed", min=0, advanced=True, help="0 = random. Fix it to repeat a training."),
        Param("checkpoint", "int", 1, label="Checkpoint every", unit="epochs", min=1, advanced=True,
              help="Weights and latent coordinates are saved every N epochs (the convergence check compares them). The "
                   "number of epochs must be a multiple of it."),
        Param("enc_dim", "int", 1024, label="Encoder width (custom network)", min=16, advanced=True),
        Param("enc_layers", "int", 3, label="Encoder layers (custom network)", min=1, advanced=True),
        Param("dec_dim", "int", 1024, label="Decoder width (custom network)", min=16, advanced=True),
        Param("dec_layers", "int", 3, label="Decoder layers (custom network)", min=1, advanced=True),
        Param("uninvert", "bool", False, label="Do not invert the images", advanced=True,
              help="cryoDRGN inverts the image contrast by default. Tick it when the input check finds negative density "
                   "(hollow volumes), e.g. for negative stain."),
        extra_args_param(),
    ]
    outputs = [*ANALYSIS_OUTPUTS,
               OutputDef("particles_prepared", "particles", "Particles prepared for cryoDRGN"),
               OutputDef("backprojection", "map", "Backprojection (input check)")]

    @classmethod
    def validate(cls, params, connected):
        problems = []
        box = int(params.get("box") or 0)
        if box % 2:
            problems.append(f"The image size must be even (got {box})")
        every, epochs = int(params.get("checkpoint") or 1), int(params.get("epochs") or 0)
        if every > 1 and epochs % every:
            problems.append(f"The number of epochs ({epochs}) must be a multiple of the checkpoint interval ({every})")
        beta = str(params.get("beta") or "").strip()
        if beta:
            try:
                float(beta)
            except ValueError:
                problems.append("β must be a number (for example 0.125)")
        return problems

    def run(self, ctx: JobContext) -> None:
        p = ctx.params
        particles = ctx.require("particles")
        version = cryodrgn_version(ctx)
        prep = prepare_particles(ctx, particles, p["box"], p["pose_source"])
        lazy = decide_lazy(ctx, p["lazy"], prep["n"], prep["box"])
        if not p["check_inputs"].startswith("no"):
            check = backprojection_check(ctx, prep, p["uninvert"], lazy)
            if check and check["verdict"] == "bad" and "stop" in p["check_inputs"]:
                raise JobError("The input check failed (see the report), so the training was not started. Fix the inputs, "
                               "or set 'Input check' to 'check, then train' to train anyway.")
        second = bool(particles.meta.get("parent_cryodrgn") or (particles.meta.get("selection") or {}).get("mode"))
        model = model_preset(p, prep["box"], second_round=second)
        settings = train_settings(p, model)
        ctx.log(f"Network: {model['name']} (encoder {model['enc_dim']} × {model['enc_layers']}, decoder "
                f"{model['dec_dim']} × {model['dec_layers']}); latent space {p['zdim']}-D; {p['epochs']} epochs; "
                f"{prep['box']} px images")
        workdir = ctx.path("train")
        args = train_args(settings, prep, version, lazy, p["gpus"], seed=p["seed"])
        epoch = run_training(ctx, args, workdir, p["epochs"], 1, version)
        lineage = prepared_lineage(ctx, particles, prep)
        lineage["train"] = {**settings, "epochs": p["epochs"], "version": version_text(version), "lazy": lazy}
        lineage["workdirs"] = [ctx.rel(workdir)]
        ctx.progress(0.87, "Analysing the latent space")
        analyze_dir = workdir / f"analyze.{epoch}"
        run_analysis(ctx, workdir, epoch, analyze_dir, p, version, analysis_apix(ctx, lineage), prep["box"])
        finish_analysis(ctx, workdir, epoch, analyze_dir, p["ksample"], lineage)


@register
class CryodrgnContinue(JobType):
    name = "cryodrgn_continue"
    title = "cryoDRGN continue training"
    category = "Heterogeneity"
    tool = "cryodrgn"
    software = ["cryoDRGN"]
    gpu = 1
    gpu_param = "gpus"
    cpus = 8
    description = ("Trains a cryoDRGN model for more epochs from its last checkpoint (same network, same particles), "
                   "then analyses it again: for a training that has not converged yet (see the convergence check).")
    inputs = [latent_slot("From a cryoDRGN training (or an earlier continuation).")]
    params = [
        Param("epochs", "int", 0, label="Train until epoch", min=0,
              help="Total number of epochs at the end (0 = twice as many as now, e.g. 50 → 100, as in the protocol)."),
        *analysis_params(),
        gpus_param(),
        lazy_param(),
    ]
    outputs = list(ANALYSIS_OUTPUTS)

    def run(self, ctx: JobContext) -> None:
        p = ctx.params
        lat, _arrays = latent_input(ctx)
        train, prepared = lat.meta.get("train"), lat.meta.get("prepared")
        if not train or not prepared or not prepared.get("stack"):
            raise JobError("This latent space does not record how it was trained (made by an older CryoPlug): start a new "
                           "cryoDRGN training instead")
        version = cryodrgn_version(ctx)
        chain = train_chain(ctx, lat)
        last_dir = chain[-1]
        done = int(train.get("epochs") or 0)
        weights = last_dir / "weights.pkl"
        if not weights.exists() or not done:
            saved = checkpoint_epochs(last_dir)
            if not saved:
                raise JobError(f"No checkpoint in {last_dir}")
            weights, done = last_dir / f"weights.{saved[-1]}.pkl", saved[-1] + (0 if version >= (3, 5) else 1)
        target = p["epochs"] or 2 * done
        every = int(train.get("checkpoint") or 1)
        if every > 1 and target % every:
            target = int(math.ceil(target / every) * every)
            ctx.log(f"Training until epoch {target} (a multiple of the checkpoint interval, {every})")
        if target <= done:
            raise JobError(f"The training already has {done} epochs: give more in 'Train until epoch'")
        prep = {"stack": str(ctx.abs(prepared["stack"])), "poses": str(ctx.abs(prepared["poses"])),
                "ctf": str(ctx.abs(prepared["ctf"])), "datadir": prepared.get("datadir"), "box": int(prepared["box"]),
                "ind": str(ctx.abs(prepared["ind"])) if prepared.get("ind") else None}
        missing = [k for k in ("stack", "poses", "ctf") if not Path(prep[k]).exists()]
        if missing:
            raise JobError(f"The prepared particles are not available any more ({', '.join(missing)} missing)")
        n = int(lat.meta.get("n_particles") or 0)
        lazy = decide_lazy(ctx, p["lazy"] or bool(train.get("lazy")), n, prep["box"])
        poses = None
        if train.get("pose_sgd"):
            refined = sorted(last_dir.glob("pose*.pkl"), key=lambda f: f.stat().st_mtime)
            poses = last_dir / "pose.pkl" if (last_dir / "pose.pkl").exists() else (refined[-1] if refined else None)
        ctx.log(f"Continuing from {weights} (epoch {done}) until epoch {target}")
        workdir = ctx.path("train")
        args = train_args(train, prep, version, lazy, p["gpus"], poses=poses)
        epoch = run_training(ctx, args, workdir, target, done + 1, version, load=weights, history=chain_losses(chain))
        lineage = analysis_lineage(lat)
        lineage["train"] = {**train, "epochs": target, "version": version_text(version)}
        lineage["workdirs"] = [*(lat.meta.get("workdirs") or [lat.meta["workdir"]]), ctx.rel(workdir)]
        ctx.progress(0.87, "Analysing the latent space")
        analyze_dir = workdir / f"analyze.{epoch}"
        run_analysis(ctx, workdir, epoch, analyze_dir, p, version, analysis_apix(ctx, lineage, lat.meta), prep["box"])
        finish_analysis(ctx, workdir, epoch, analyze_dir, p["ksample"], lineage)


@register
class CryodrgnAnalyze(JobType):
    name = "cryodrgn_analyze"
    title = "cryoDRGN analysis"
    category = "Heterogeneity"
    tool = "cryodrgn"
    software = ["cryoDRGN"]
    gpu = 1
    cpus = 4
    description = ("Analyses a trained cryoDRGN model again: another epoch (an earlier one when the last looks "
                   "overtrained), more clusters (to find rare states), longer trajectories, flipped hand... Gives the "
                   "same explorer, volumes and diagnostics as the training.")
    inputs = [latent_slot()]
    params = [
        Param("epoch", "int", 0, label="Epoch", min=0,
              help="0 = the epoch of the input. An earlier epoch when the last one gives noisy volumes (overtraining)."),
        *analysis_params(),
        Param("pcs", "int", 2, label="Principal components", min=1, max=8,
              help="Trajectories are generated along the first components."),
        Param("n_per_pc", "int", 10, label="Volumes per trajectory", min=3, max=50),
        Param("downsample", "int", 0, label="Volume box", unit="px", min=0, advanced=True,
              help="0 = training size. Smaller volumes are faster to generate and to view."),
    ]
    outputs = list(ANALYSIS_OUTPUTS)

    def run(self, ctx: JobContext) -> None:
        p = ctx.params
        lat, _arrays = latent_input(ctx)
        version = cryodrgn_version(ctx)
        workdir, epoch = workdir_of(ctx, lat, p["epoch"])
        lineage = analysis_lineage(lat)
        outdir = ctx.path("analyze")
        run_analysis(ctx, workdir, epoch, outdir, p, version, analysis_apix(ctx, lineage, lat.meta),
                     int(lineage.get("box") or 0))
        finish_analysis(ctx, workdir, epoch, outdir, p["ksample"], lineage)


def eval_volumes(ctx: JobContext, weights: Path, config: Path, zvalues: np.ndarray, outdir: Path, apix: float | None,
                 flip: bool = False, box: int = 0, train_box: int = 0) -> list[Path]:
    """Volumes of the decoder at the given latent coordinates (cryodrgn eval_vol)."""
    outdir.mkdir(parents=True, exist_ok=True)
    zfile = outdir.with_name(outdir.name + "_z.txt")
    np.savetxt(zfile, np.atleast_2d(zvalues))
    args = [ctx.executable("cryodrgn"), "eval_vol", weights, "--config", config, "--zfile", zfile, "-o", outdir]
    if box and train_box and box < train_box:
        args += ["-d", str(box)]
        apix = apix * train_box / box if apix else None
    if apix:
        args += ["--Apix", f"{apix:.4f}".rstrip("0").rstrip(".")]
    if flip:
        args.append("--flip")
    ctx.run(args, tool="cryodrgn")
    files = sorted(outdir.glob("vol_*.mrc"))
    if not files:
        raise JobError("cryodrgn eval_vol wrote no volume (see the log)")
    return files


def latent_embeddings(arrays: dict[str, np.ndarray]) -> tuple[dict[str, np.ndarray], dict[str, tuple[str, str]]]:
    emb, labels = {}, {"UMAP": ("UMAP 1", "UMAP 2"), "PCA": ("PC1", "PC2")}
    if "umap" in arrays:
        emb["UMAP"] = arrays["umap"]
    if "pca" in arrays:
        emb["PCA"] = arrays["pca"]
    for key in arrays:
        if key.startswith("embedding_"):
            name = key[len("embedding_"):].replace("_", " ").title().replace("Pca", "PCA")
            emb[name] = arrays[key]
    return emb, labels


def stored_colorings(arrays: dict[str, np.ndarray]) -> dict[str, dict]:
    """Colourings of the explorer kept in a latent output (cov_* arrays, ‖z‖)."""
    names = {"defocus": ("Defocus", "µm", False), "tilt": ("Viewing tilt", "°", False),
             "azimuth": ("Viewing azimuth", "°", True), "shift": ("In-plane shift", "Å", False)}
    out = {}
    if "znorm" in arrays:
        out["znorm"] = coloring(arrays["znorm"], "‖z‖ (distance from the centre)")
    for key, (label, unit, cyclic) in names.items():
        if f"cov_{key}" in arrays:
            lo, hi = {"tilt": (0, 180), "azimuth": (-180, 180)}.get(key, (None, None))
            out[key] = coloring(arrays[f"cov_{key}"], label, unit, cyclic=cyclic, lo=lo, hi=hi)
    return out


def path_points(arrays: dict[str, np.ndarray], zsel: np.ndarray, ind: np.ndarray) -> dict[str, list]:
    pts = {}
    if "pca_mean" in arrays and "pca_axes" in arrays:
        pts["PCA"] = _round((np.atleast_2d(zsel) - arrays["pca_mean"]) @ arrays["pca_axes"].T)
    if "umap" in arrays:
        pts["UMAP"] = _round(arrays["umap"][ind])
    return pts


@register
class CryodrgnTrajectory(JobType):
    name = "cryodrgn_trajectory"
    title = "cryoDRGN trajectory"
    category = "Heterogeneity"
    tool = "cryodrgn"
    software = ["cryoDRGN"]
    gpu = 1
    description = ("Volumes along a path in the cryoDRGN latent space between chosen clusters or particles: through "
                   "the particles (a path the data support, cryodrgn graph_traversal) or in a straight line. A movie of "
                   "the transition, to play in the 3D viewer.")
    inputs = [latent_slot()]
    params = [
        Param("clusters", "str", "", label="Clusters to visit", placeholder="3, 12",
              help="Cluster numbers of the analysis, in order (two or more)."),
        Param("particles", "str", "", label="Particles to visit", placeholder="1043, 22071", advanced=True,
              help="Particle numbers instead of clusters (the explorer shows them when pointing at a particle)."),
        Param("frames", "int", 20, label="Volumes", min=3, max=100),
        Param("path", "choice", "through the particles", choices=["through the particles", "straight line"],
              label="Path",
              help="Through the particles: shortest path on the neighbour graph of the latent space, which stays in "
                   "populated regions (states the data contain). Straight line: may cross empty regions."),
        Param("loop", "bool", False, label="Back to the start", advanced=True,
              help="Returns to the first cluster at the end (a closed loop, for a cyclic motion)."),
        Param("apix", "float", 0.0, label="Pixel size", unit="Å", min=0.0, advanced=True, help="0 = as the cluster volumes."),
        Param("flip", "bool", False, label="Flip handedness", advanced=True),
    ]
    outputs = [OutputDef("trajectory", "volume_series", "Trajectory")]

    @classmethod
    def validate(cls, params, connected):
        try:
            if str(params.get("particles") or "").strip():
                if len(parse_indices(params["particles"])) < 2:
                    return ["Give at least two particles"]
            elif len(parse_clusters(params.get("clusters", ""), 10_000)) < 2:
                return ["Give at least two clusters"]
        except ValueError as exc:
            return [str(exc)]
        return []

    def run(self, ctx: JobContext) -> None:
        p = ctx.params
        lat, arrays = latent_input(ctx)
        cryodrgn_version(ctx)
        workdir, epoch = workdir_of(ctx, lat)
        z = arrays["z"]
        try:
            if str(p["particles"]).strip():
                anchors = parse_indices(p["particles"], len(z))
                names = "particles " + " → ".join(str(a) for a in anchors)
                chosen = []
            else:
                if "centers_ind" not in arrays:
                    raise JobError("The latent space has no clusters (run a cryoDRGN analysis), or give particles")
                chosen = parse_clusters(p["clusters"], len(arrays["centers_ind"]))
                anchors = [int(arrays["centers_ind"][c]) for c in chosen]
                names = "clusters " + " → ".join(str(c + 1) for c in chosen)
        except ValueError as exc:
            raise JobError(str(exc)) from None
        if p["loop"]:
            anchors = anchors + [anchors[0]]
        weights, zfile = weights_and_z(workdir, epoch)
        n_frames = p["frames"]
        ind = None
        if p["path"] == "through the particles":
            path_txt, path_ind = ctx.path("z_path.txt"), ctx.path("z_path_ind.txt")
            ctx.run([ctx.executable("cryodrgn"), "graph_traversal", zfile, "--anchors", *map(str, anchors),
                     "-o", path_txt, "--outind", path_ind], tool="cryodrgn", check=False)
            if path_ind.exists():
                full = np.loadtxt(path_ind, dtype=int, ndmin=1)
                ind = full[np.unique(np.round(np.linspace(0, len(full) - 1, min(n_frames, len(full)))).astype(int))]
                zsel = z[ind]
            else:
                ctx.warn("No path through the particles links these points (the neighbour graph is disconnected): "
                         "a straight line is used instead")
        if ind is None:
            pts = z[anchors].astype(np.float64)
            t = np.linspace(0, len(pts) - 1, n_frames)
            i0 = np.minimum(np.floor(t).astype(int), len(pts) - 2)
            zsel = pts[i0] + (t - i0)[:, None] * (pts[i0 + 1] - pts[i0])
            ind = np.array([int(np.argmin(((z - q) ** 2).sum(1))) for q in zsel])  # nearest particles, for the maps
        apix = p["apix"] or lat.meta.get("apix")
        files = eval_volumes(ctx, weights, workdir / "config.yaml", zsel, ctx.path("trajectory"), apix, p["flip"])
        labels = arrays.get("labels")
        frame_labels = [f"{j + 1}/{len(files)}" + (f" · cluster {int(labels[ind[j]]) + 1}" if labels is not None else "")
                        for j in range(len(files))]
        add_series(ctx, "trajectory", files, f"Trajectory {names} ({len(files)} volumes)", frame_labels,
                   {"kind": "trajectory", "clusters": [c + 1 for c in chosen], "particles": [int(i) for i in ind]})
        embeddings, axis = latent_embeddings(arrays)
        latent_report(ctx, embeddings, labels, f"Path through {names}", axis, centres_ind=arrays.get("centers_ind"),
                      paths=[{"name": names.capitalize(), "points": path_points(arrays, zsel, ind)}],
                      colorings=stored_colorings(arrays))
        ctx.add_highlight("Volumes", len(files))


@register
class CryodrgnVolumes(JobType):
    name = "cryodrgn_volumes"
    title = "cryoDRGN volumes at particles"
    category = "Heterogeneity"
    tool = "cryodrgn"
    software = ["cryoDRGN"]
    gpu = 1
    description = ("Generates the volumes of chosen particles (from their latent coordinates), or of a region drawn with "
                   "the lasso of the latent explorer: the volume at its centre, or several volumes spread over it. To look "
                   "at a part of the latent space that the clusters do not show.")
    inputs = [latent_slot()]
    params = [
        Param("selection", "choice", "particles", choices=["particles", "region"], label="Volumes of"),
        Param("particles", "str", "", label="Particles", placeholder="1043, 22071",
              help="Particle numbers (the explorer shows them when pointing at a particle)."),
        Param("region", "text", "", label="Region (lasso)", advanced=True,
              help="Filled in by the lasso of the latent explorer: the embedding and the polygon drawn in it."),
        Param("volumes", "int", 1, label="Volumes in the region", min=1, max=50,
              help="1 = the volume of the particle at the centre of the region; more = k-means centres of the particles "
                   "inside it (on-data points, as cryoDRGN's own analysis)."),
        Param("apix", "float", 0.0, label="Pixel size", unit="Å", min=0.0, advanced=True, help="0 = as the cluster volumes."),
        Param("flip", "bool", False, label="Flip handedness", advanced=True),
    ]
    outputs = [OutputDef("volume", "map", "Volume"), OutputDef("volumes", "volume_series", "Volumes")]

    @classmethod
    def validate(cls, params, connected):
        try:
            if (params.get("selection") or "particles") == "particles":
                if not parse_indices(params.get("particles", "")):
                    return ["Give the particle numbers"]
            else:
                parse_region(params.get("region") or "")
        except ValueError as exc:
            return [str(exc)]
        return []

    def run(self, ctx: JobContext) -> None:
        from cryoplug import particles as pt
        p = ctx.params
        lat, arrays = latent_input(ctx)
        cryodrgn_version(ctx)
        workdir, epoch = workdir_of(ctx, lat)
        z = arrays["z"]
        try:
            if p["selection"] == "particles":
                ind = np.array(parse_indices(p["particles"], len(z)), dtype=int)
                names = [f"particle {i}" for i in ind]
                what = (f"particle {ind[0]}" if len(ind) == 1 else
                        "particles " + ", ".join(str(i) for i in ind[:8]) + ("…" if len(ind) > 8 else ""))
            else:
                region = parse_region(p["region"])
                inside = np.nonzero(pt.points_in_polygon(embedding_of(arrays, region["embedding"]), region["polygon"]))[0]
                if not len(inside):
                    raise JobError("No particle inside the region")
                zr = z[inside]
                if p["volumes"] <= 1 or len(inside) < 2:
                    centres = np.median(zr, axis=0, keepdims=True)
                else:
                    _lab, centres = pt.kmeans(zr, min(p["volumes"], len(inside)))
                ind = inside[pt.nearest_indices(zr, centres)]
                names = [f"region · {j + 1}/{len(ind)} (particle {i})" for j, i in enumerate(ind)]
                what = f"a region of {len(inside):,} particles"
        except ValueError as exc:
            raise JobError(str(exc)) from None
        weights, _zfile = weights_and_z(workdir, epoch)
        apix = p["apix"] or lat.meta.get("apix")
        files = eval_volumes(ctx, weights, workdir / "config.yaml", z[ind], ctx.path("volumes"), apix, p["flip"])
        labels = arrays.get("labels")
        if labels is not None:
            names = [f"{nm} · cluster {int(labels[i]) + 1}" for nm, i in zip(names, ind, strict=True)]
        if len(files) == 1:
            ctx.add_output("volume", "map", files[0], f"Volume of {names[0]}")
        else:
            add_series(ctx, "volumes", files, f"Volumes of {what} ({len(files)})", names,
                       {"kind": "particles", "particles": [int(i) for i in ind]})
        embeddings, axis = latent_embeddings(arrays)
        latent_report(ctx, embeddings, labels, f"Volume{'s' if len(ind) > 1 else ''} of {what}", axis,
                      centres_ind=arrays.get("centers_ind"),
                      colorings=stored_colorings(arrays),
                      extra={"marks": [{"name": nm, "particle": int(i), "points": {k: _round(e[int(i)]) for k, e in embeddings.items()}}
                                       for nm, i in zip(names, ind, strict=True)]})
        ctx.add_highlight("Volumes", len(files))


# ---------------------------------------------------------------- convergence
def knn_sets(z: np.ndarray, k: int = 15) -> np.ndarray:
    """Indices of the k nearest neighbours of each point (excluding itself)."""
    zs = np.asarray(z, dtype=np.float64)
    sq = (zs ** 2).sum(1)
    d2 = sq[:, None] + sq[None, :] - 2 * zs @ zs.T
    np.fill_diagonal(d2, np.inf)
    k = max(1, min(k, len(zs) - 1))
    return np.argpartition(d2, k, axis=1)[:, :k]


def neighbour_overlap(a: np.ndarray, b: np.ndarray) -> float:
    """Mean fraction of neighbours shared by two neighbour lists of the same points."""
    k = a.shape[1]
    return float(np.mean([len(np.intersect1d(x, y, assume_unique=True)) / k for x, y in zip(a, b, strict=True)]))


def loss_trend(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Is the reconstruction error still falling? Slope over the last quarter of the epochs, compared with the
    total drop since the first epoch."""
    pts = [(r["epoch"], r["gen"]) for r in rows if np.isfinite(r["gen"])]
    if len(pts) < 6:
        return {"state": "unknown", "text": "too few epochs logged"}
    e, g = np.array(pts, dtype=np.float64).T
    q = max(3, len(g) // 4)
    slope = float(np.polyfit(e[-q:], g[-q:], 1)[0])
    drop = float(g[0] - g.min())
    rel = (-slope * 10) / drop if drop > 0 else 0.0
    state = "plateau" if rel < 0.02 else ("slow" if rel < 0.06 else "falling")
    text = {"plateau": "levelled off", "slow": "still decreasing slowly", "falling": "still decreasing"}[state]
    return {"state": state, "relative": rel, "text": f"{text} ({100 * max(rel, 0):.1f} % of the total drop per 10 epochs "
                                                      "over the last quarter of the training)"}


def plateaued(values: list[float], higher_is_better: bool) -> bool:
    """The last quarter of a curve stays close to its best value (within 10 % for a score that rises to a
    maximum, 25 % for a motion that falls to its noise floor)."""
    v = np.asarray([x for x in values if x is not None and np.isfinite(x)], dtype=np.float64)
    if len(v) < 4:
        return False
    q = max(2, len(v) // 4)
    if higher_is_better:
        return float(v[-q:].mean()) >= 0.9 * float(v.max())
    return float(v[-q:].mean()) <= 1.25 * float(v.min()) + 1e-6


def choose_epochs(text: str, available: list[int], count: int = 6) -> list[int]:
    if str(text or "").strip():
        wanted = parse_indices(text)
        missing = [e for e in wanted if e not in available]
        if missing:
            raise JobError(f"No checkpoint for epoch(s) {', '.join(map(str, missing))} (saved: {available[0]} to "
                           f"{available[-1]})")
        return sorted(wanted)
    if len(available) <= count:
        return list(available)
    picks = np.round(np.linspace(0, len(available) - 1, count)).astype(int)
    return sorted({available[i] for i in picks})


@register
class CryodrgnConvergence(JobType):
    name = "cryodrgn_convergence"
    title = "cryoDRGN convergence check"
    category = "Heterogeneity"
    tool = "cryodrgn"
    software = ["cryoDRGN"]
    gpu = 1
    cpus = 4
    description = ("Tells whether a cryoDRGN training has converged (Kinman et al. 2023): the losses, how far the particles "
                   "still move in the latent space from one epoch to the next, whether their neighbours stay the same, "
                   "and how the volumes of representative particles change across epochs. Gives the epoch from which the "
                   "results are stable, or advises to train longer.")
    inputs = [latent_slot("From a cryoDRGN training: the checkpoints of its epochs are compared.")]
    params = [
        Param("epochs", "str", "", label="Epochs compared", placeholder="auto",
              help="Epochs whose volumes are generated, e.g. '10, 20, 30, 40, 50'. Empty = 6 epochs spread over the training."),
        Param("particles", "int", 6, label="Representative particles", min=2, max=16,
              help="Particles spread over the latent space (k-means centres of the last epoch) whose volumes are followed "
                   "across the epochs."),
        Param("volumes", "bool", True, label="Compare volumes",
              help="Generates the volumes of the representative particles at each compared epoch (GPU, a few minutes). "
                   "Off: only the losses and the latent space are compared."),
        Param("box", "int", 64, label="Volume box", unit="px", min=0, advanced=True,
              help="Volumes are generated at this size to save time (0 = training size)."),
    ]
    outputs = [OutputDef("rep_1", "volume_series", "Particle 1 across epochs")]

    @classmethod
    def validate(cls, params, connected):
        try:
            parse_indices(params.get("epochs", ""))
        except ValueError as exc:
            return [str(exc)]
        return []

    def run(self, ctx: JobContext) -> None:
        from cryoplug import particles as pt
        p = ctx.params
        lat, arrays = latent_input(ctx)
        chain = train_chain(ctx, lat)
        folders = epoch_folders(chain)
        available = sorted(folders)
        if len(available) < 3:
            raise JobError("The convergence check needs the checkpoints of at least 3 epochs (cryoDRGN saves one per epoch "
                           "unless 'Checkpoint every' was raised)")
        final = available[-1]
        ctx.progress(0.03, f"{len(available)} checkpoints (epochs {available[0]} to {final})")
        # the verdict comes first in the report: a placeholder, replaced once everything is compared
        ctx.add_metrics("Convergence", [{"label": "Verdict", "value": "being computed…", "status": None, "target": ""}],
                        key="verdict")
        ctx.add_text("What to do", "The comparison of the epochs is running.", key="advice")
        losses = chain_losses(chain)
        loss_plots(ctx, losses)
        trend = loss_trend(losses)

        # latent space: motion between epochs and stability of the neighbourhoods (on a fixed subset)
        z_final = pt.load_array_pkl(folders[final] / f"z.{final}.pkl").astype(np.float32)
        n = len(z_final)
        rng = np.random.default_rng(0)
        sample = np.sort(rng.choice(n, size=min(n, 2000), replace=False))
        nn_final = knn_sets(z_final[sample])
        scanned = available if len(available) <= 120 else sorted({available[i] for i in np.round(np.linspace(0, len(available) - 1, 120)).astype(int)})
        motion, prev_z, prev_nn, z_cache = [], None, None, {}
        vol_epochs = choose_epochs(p["epochs"], available)
        for j, e in enumerate(scanned):
            z = z_final if e == final else pt.load_array_pkl(folders[e] / f"z.{e}.pkl").astype(np.float32)
            if len(z) != n:
                ctx.warn(f"Epoch {e} has {len(z):,} particles (not {n:,}): skipped")
                continue
            if e in vol_epochs:
                z_cache[e] = z
            zs = z[sample]
            nn = nn_final if e == final else knn_sets(zs)
            row = {"epoch": e, "final": neighbour_overlap(nn, nn_final), "shift": None, "previous": None}
            if prev_z is not None:
                spread = float(np.median(np.linalg.norm(zs - zs.mean(0), axis=1))) or 1.0
                row["shift"] = 100.0 * float(np.median(np.linalg.norm(zs - prev_z, axis=1))) / spread
                row["previous"] = neighbour_overlap(nn, prev_nn)
            motion.append(row)
            prev_z, prev_nn = zs, nn
            ctx.progress(0.03 + 0.3 * (j + 1) / len(scanned), f"Latent space of epoch {e}", log=False)
        moved = [r for r in motion if r["shift"] is not None]
        ctx.add_plot("Motion of the particles in the latent space", [
            {"name": "Median shift since the previous checkpoint (% of the spread)", "x": [r["epoch"] for r in moved],
             "y": [round(r["shift"], 3) for r in moved]}],
            x_label="Epoch", y_label="%",
            note="How far a typical particle moves between checkpoints, relative to the size of the latent space. It "
                 "drops, then levels off when the encoder has converged (it never reaches zero).")
        ctx.add_plot("Neighbourhoods of the particles", [
            {"name": "Neighbours shared with the previous checkpoint", "x": [r["epoch"] for r in moved],
             "y": [round(r["previous"], 4) for r in moved]},
            {"name": "Neighbours shared with the last epoch", "x": [r["epoch"] for r in motion],
             "y": [round(r["final"], 4) for r in motion]}],
            x_label="Epoch", y_label="Fraction of the 15 nearest particles", y_range=[0, 1.02],
            note="Whether the particles keep the same neighbours in the latent space (2,000 particles, 15 neighbours "
                 "each). Stable neighbourhoods mean a stable organisation of the latent space, even if it rotates.")
        latent_stable = plateaued([r["shift"] for r in moved], higher_is_better=False) and plateaued(
            [r["previous"] for r in moved], higher_is_better=True)

        # the latent space at the compared epochs, aligned on the last one, in the explorer
        mean_f, axes_f, _var = pt.pca_components(z_final, 2)
        axes_f = axes_f if len(axes_f) >= 2 else np.r_[axes_f, np.zeros_like(axes_f)]
        embeddings: dict[str, np.ndarray] = {}
        for e in vol_epochs:
            z = z_cache.get(e)
            if z is None:
                continue
            a, b = z[sample] - z[sample].mean(0), z_final[sample] - z_final[sample].mean(0)
            u, _s, vt = np.linalg.svd(a.T.astype(np.float64) @ b.astype(np.float64))
            embeddings[f"Epoch {e}"] = (((z - z.mean(0)) @ (u @ vt)) @ axes_f.T).astype(np.float32)
        labels = arrays.get("labels") if arrays.get("labels") is not None and len(arrays["labels"]) == n else None
        if embeddings:
            latent_report(ctx, embeddings, labels, "Latent space at the compared epochs (aligned on the last epoch, PCA)",
                          {k: ("PC1 of the last epoch", "PC2 of the last epoch") for k in embeddings},
                          centres_ind=arrays.get("centers_ind") if labels is not None else None,
                          colorings=stored_colorings(arrays) if len(arrays.get("z", [])) == n else None)

        volume_state, stable_from, overtrained = "not compared", None, None
        if p["volumes"] and len(vol_epochs) >= 2:
            volume_state, stable_from, overtrained = self._volumes(ctx, lat, folders, vol_epochs, z_cache, z_final)
        self._verdict(ctx, trend, latent_stable, volume_state, stable_from, overtrained, final, motion)

    def _volumes(self, ctx: JobContext, lat: InputData, folders: dict[int, Path], epochs: list[int],
                 z_cache: dict[int, np.ndarray], z_final: np.ndarray) -> tuple[str, int | None, int | None]:
        from scipy import ndimage

        from cryoplug import particles as pt
        from cryoplug.imaging import write_png
        p = ctx.params
        cryodrgn_version(ctx)
        _lab, centres = pt.kmeans(z_final, p["particles"])
        reps = pt.nearest_indices(z_final, centres)
        lineage = analysis_lineage(lat)
        train_box = int(lineage.get("box") or 0)
        apix = analysis_apix(ctx, lineage, lat.meta)
        box = p["box"] if p["box"] and train_box and p["box"] < train_box else 0
        per_epoch: dict[int, list[Path]] = {}
        for j, e in enumerate(epochs):
            ctx.progress(0.35 + 0.45 * j / len(epochs), f"Volumes of the representative particles at epoch {e}")
            w, cfg = folders[e] / f"weights.{e}.pkl", folders[e] / "config.yaml"
            per_epoch[e] = eval_volumes(ctx, w, cfg, z_cache[e][reps], ctx.path("convergence", f"epoch_{e:03d}"), apix,
                                        box=box, train_box=train_box)
        frames = {e: load_frames(per_epoch[e], max_box=64)[0] for e in epochs}
        stack = np.stack([[frames[e][r].data for e in epochs] for r in range(len(reps))])  # (reps, epochs, z, y, x)
        region = molecule_region(stack[:, -1].mean(axis=0))
        grown = ndimage.binary_dilation(region, iterations=2)
        outside = ~ndimage.binary_dilation(region, iterations=4)
        cc = np.zeros((len(reps), len(epochs) - 1))
        noise = np.zeros((len(reps), len(epochs)))
        for r in range(len(reps)):
            for j in range(len(epochs)):
                v = stack[r, j]
                noise[r, j] = float(v[outside].std() / (v[region].std() or 1.0)) if outside.any() else 0.0
                if j:
                    a, b = stack[r, j - 1][grown].astype(np.float64), v[grown].astype(np.float64)
                    a, b = a - a.mean(), b - b.mean()
                    cc[r, j - 1] = float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b) or 1.0))
        pairs = [f"{epochs[j - 1]}→{epochs[j]}" for j in range(1, len(epochs))]
        ctx.add_heatmap("Change of the volumes between compared epochs (correlation)", pairs,
                        [f"Particle {r + 1}" for r in range(len(reps))], cc.round(4).tolist(),
                        x_label="Epochs", y_label="Representative particle",
                        note="Correlation between the volumes of the same particle at two epochs (inside the molecule). "
                             "Above 0.98: the decoder no longer changes this volume.")
        med = np.median(cc, axis=0)
        ctx.add_plot("Volume stability across epochs", [
            {"name": "Median correlation with the previous compared epoch", "x": epochs[1:], "y": np.round(med, 4).tolist()},
            {"name": "Lowest correlation", "x": epochs[1:], "y": np.round(cc.min(axis=0), 4).tolist()}],
            x_label="Epoch", y_label="Correlation", y_range=[min(0.8, float(cc.min()) - 0.02), 1.005])
        tiles = projection_tiles(stack.reshape(-1, *stack.shape[2:]), size=64, axes=(0,), crop=crop_box(region))
        gap, size = 3, 64
        sheet = np.zeros((len(reps) * (size + gap) - gap, len(epochs) * (size + gap) - gap, 4), dtype=np.uint8)
        for idx, views in enumerate(tiles):
            r, j = divmod(idx, len(epochs))
            sheet[r * (size + gap): r * (size + gap) + size, j * (size + gap): j * (size + gap) + size, :3] = views[0][..., None]
            sheet[r * (size + gap): r * (size + gap) + size, j * (size + gap): j * (size + gap) + size, 3] = 255
        ctx.add_image(f"Representative particles (rows) at epochs {', '.join(map(str, epochs))} (columns)",
                      write_png(ctx.path("convergence_volumes.png"), sheet))
        for r, i in enumerate(reps):
            add_series(ctx, f"rep_{r + 1}", [per_epoch[e][r] for e in epochs],
                       f"Particle {int(i)} at epochs {epochs[0]} to {epochs[-1]}", [f"Epoch {e}" for e in epochs],
                       {"kind": "convergence", "particle": int(i)})
        stable_from = None
        for j in range(len(med)):
            if np.all(med[j:] >= 0.98):
                stable_from = epochs[j]
                break
        last_med, last_min = float(med[-1]), float(cc[:, -1].min())
        state = "stable" if last_med >= 0.98 and last_min >= 0.95 else ("nearly" if last_med >= 0.95 else "changing")
        overtrained = None
        if stable_from is not None:
            j0 = epochs.index(stable_from)
            if float(np.median(noise[:, -1])) > 1.25 * float(np.median(noise[:, j0])) + 0.02:
                overtrained = stable_from
        return state, stable_from, overtrained

    @staticmethod
    def _verdict(ctx: JobContext, trend: dict[str, Any], latent_stable: bool, volume_state: str, stable_from: int | None,
                 overtrained: int | None, final: int, motion: list[dict[str, Any]]) -> None:
        checks = [
            {"label": "Training loss", "value": trend["text"],
             "status": {"plateau": "good", "slow": "warn", "falling": "bad"}.get(trend["state"])},
            {"label": "Latent space", "value": "stable over the last epochs" if latent_stable else "still changing",
             "status": "good" if latent_stable else "warn"},
        ]
        if volume_state != "not compared":
            checks.append({"label": "Volumes of representative particles",
                           "value": {"stable": "stable", "nearly": "almost stable", "changing": "still changing"}[volume_state]
                           + (f" from epoch {stable_from}" if stable_from is not None and volume_state != "changing" else ""),
                           "status": {"stable": "good", "nearly": "warn", "changing": "bad"}[volume_state]})
        # the volumes and the organisation of the latent space decide (Kinman et al. 2023); a loss that still falls
        # slowly mostly means the network keeps fitting the training images better
        if volume_state == "not compared":
            good = latent_stable and trend["state"] != "falling"
            bad = not latent_stable and trend["state"] == "falling"
        else:
            good = volume_state == "stable" and latent_stable
            bad = volume_state == "changing" or (not latent_stable and trend["state"] == "falling")
        verdict = "converged" if good else ("not converged" if bad else "nearly converged")
        checks.append({"label": "Verdict", "value": verdict, "status": "good" if good else ("bad" if bad else "warn")})
        if trend["state"] == "falling":
            checks[0]["status"] = "warn"
        ctx.add_metrics("Convergence", checks, key="verdict")
        advice = []
        if good:
            advice.append(f"The training has converged{f' from about epoch {stable_from}' if stable_from else ''}: the "
                          f"results of epoch {final} can be used.")
        elif bad:
            advice.append(f"The training has not converged: continue it to epoch {2 * final} (cryoDRGN continue training "
                          "on the latent space), then run this check again.")
        else:
            advice.append(f"Almost converged: the overall picture is reliable, fine details may still change. Continuing to "
                          f"epoch {2 * final} makes sure.")
        if overtrained is not None:
            advice.append(f"The volumes get noisier over the last epochs (overtraining?): compare with the analysis of epoch "
                          f"{overtrained} (cryoDRGN analysis with Epoch = {overtrained}) and keep the cleaner one.")
        if good and trend["state"] == "falling":
            advice.append("The loss still decreases a little: the network keeps fitting the training images better, which "
                          "does not change the volumes. Fine details are reliable once the volumes are stable.")
        advice.append("Play the 'Particle … across epochs' outputs in 3D: a converged training shows the same volume at "
                      "every epoch.")
        ctx.add_text("What to do", "\n\n".join(advice), key="advice")
        ctx.add_highlight("Convergence", verdict, "good" if good else ("bad" if bad else "warn"))
        if stable_from is not None:
            ctx.add_highlight("Stable from", f"epoch {stable_from}")


# ------------------------------------------------------------------ landscape
@register
class CryodrgnLandscape(JobType):
    name = "cryodrgn_landscape"
    title = "cryoDRGN landscape analysis"
    category = "Heterogeneity"
    tool = "cryodrgn"
    software = ["cryoDRGN"]
    gpu = 1
    gpu_param = "gpus"
    cpus = 8
    description = ("Maps the conformational landscape from the volumes rather than from the latent space (cryodrgn "
                   "analyze_landscape): many volumes are generated across the latent space, compared by PCA inside a "
                   "mask, and grouped into states by hierarchical clustering. Results: the state volumes, trajectories "
                   "along the main volume components, and the particles of each state, ready to select.")
    inputs = [latent_slot(),
              Slot("mask", ("mask",), "Mask", required=False,
                   help="Region where the volumes are compared, e.g. a moving domain (default: made from the volumes).")]
    params = [
        Param("sketch", "int", 1000, label="Volumes generated", min=20, max=5000,
              help="Volumes sampled across the latent space (k-means centres of the particles)."),
        Param("states", "int", 10, label="States", min=2, max=50, help="Number of groups of similar volumes."),
        Param("box", "int", 128, label="Volume box", unit="px", min=32,
              help="Volumes are generated at this size (smaller is faster; 128 as cryoDRGN's default)."),
        Param("linkage", "choice", "average", choices=["average", "ward", "complete", "single"], label="Linkage",
              advanced=True, help="How the hierarchical clustering merges groups of volumes."),
        Param("pc_dim", "int", 20, label="Volume PCA dimensions", min=2, max=100, advanced=True),
        Param("flip", "bool", False, label="Flip handedness", advanced=True),
        gpus_param(),
    ]
    outputs = [OutputDef("latent", "latent", "Latent space with the states"),
               OutputDef("states", "volume_series", "State volumes"),
               OutputDef("vol_pc1", "volume_series", "Volume PC1 trajectory"),
               OutputDef("vol_pc2", "volume_series", "Volume PC2 trajectory")]

    def run(self, ctx: JobContext) -> None:
        from cryoplug import particles as pt
        p = ctx.params
        lat, arrays = latent_input(ctx)
        cryodrgn_version(ctx)
        workdir, epoch = workdir_of(ctx, lat)
        if not (workdir / f"weights.{epoch}.pkl").exists():
            raise JobError(f"The landscape analysis needs the checkpoint of epoch {epoch} (weights.{epoch}.pkl)")
        z = arrays["z"]
        n = len(z)
        sketch = min(p["sketch"], n)
        outdir = ctx.path("landscape")
        outdir.mkdir(exist_ok=True)
        umap = arrays.get("umap") if arrays.get("umap") is not None else arrays.get("pca")
        pt.save_array_pkl(np.asarray(umap, dtype=np.float32), outdir / "umap.pkl")
        lineage = analysis_lineage(lat)
        train_box = int(lineage.get("box") or 0)
        apix = analysis_apix(ctx, lineage, lat.meta) or 1.0
        box = min(p["box"] - p["box"] % 2, train_box) if train_box else p["box"] - p["box"] % 2
        apix_vol = apix * train_box / box if train_box else apix
        args = [ctx.executable("cryodrgn"), "analyze_landscape", workdir, str(epoch), "-o", outdir, "--skip-umap",
                "-N", str(sketch), "-d", str(box), "--Apix", f"{apix_vol:.4f}".rstrip("0").rstrip("."),
                "-M", str(p["states"]), "--linkage", p["linkage"], "--pc-dim", str(min(p["pc_dim"], sketch - 1))]
        mask_in = ctx.input("mask")
        if mask_in is not None:
            args += ["--mask", self._mask(ctx, Path(ctx.abs(mask_in.files[0])), box, apix_vol)]
        if p["flip"]:
            args.append("--flip")
        if p["gpus"] > 1:
            args.append("--multigpu")
        ctx.progress(0.05, f"Generating {sketch:,} volumes and comparing them")
        ctx.run(args, tool="cryodrgn")
        self._results(ctx, lat, arrays, outdir, sketch, p, n)

    @staticmethod
    def _mask(ctx: JobContext, path: Path, box: int, apix: float) -> Path:
        """The mask resampled to the box of the generated volumes."""
        from scipy import ndimage

        from cryoplug.mrc import MapVolume
        vol = MapVolume.read(path)
        data = vol.data
        if data.shape != (box, box, box):
            data = ndimage.zoom(data, [box / s for s in data.shape], order=1)[:box, :box, :box]
        out = ctx.path("landscape_mask.mrc")
        MapVolume(data=(data > 0.5).astype(np.float32), voxel=np.array([apix] * 3)).write(out)
        return out

    def _results(self, ctx: JobContext, lat: InputData, arrays: dict[str, np.ndarray], outdir: Path, k: int,
                 p: dict[str, Any], n: int) -> None:
        from cryoplug import particles as pt
        kdir = outdir / f"kmeans{k}"
        sdir = outdir / f"sketch_clustering_{p['linkage']}_{p['states']}"
        if not (kdir / "labels.pkl").exists() or not (sdir / "state_labels.pkl").exists():
            raise JobError("cryodrgn analyze_landscape did not write its clustering (see the log)")
        klabels = pt.load_array_pkl(kdir / "labels.pkl").astype(np.int64)
        klabels -= int(klabels.min()) if klabels.min() in (0, 1) else 0
        state_of_volume = pt.load_array_pkl(sdir / "state_labels.pkl").astype(np.int64) - 1
        if len(klabels) != n or klabels.max() >= len(state_of_volume):
            raise JobError("The landscape clustering does not match the particles of the latent space")
        states = state_of_volume[klabels]
        m = int(state_of_volume.max()) + 1
        counts = np.bincount(states, minlength=m)
        vol_counts = np.bincount(state_of_volume, minlength=m)
        z = arrays["z"]
        centres = np.array([int(np.argmin(((z - np.median(z[states == s], axis=0)) ** 2).sum(1))) if counts[s] else 0
                            for s in range(m)])
        means = [sdir / f"state_{s + 1:03d}_mean.mrc" for s in range(m)]
        means = [f for f in means if f.exists()]
        if means:
            add_series(ctx, "states", means, f"Volumes of the {len(means)} states (mean of their volumes)",
                       [f"State {s + 1} · {100.0 * counts[s] / n:.1f} %" for s in range(len(means))],
                       {"kind": "states", "particles": counts.tolist()})
        for i in range(1, 6):
            files = sorted((outdir / "vol_pcs" / f"pc{i}").glob("*.mrc"))
            if files:
                add_series(ctx, f"vol_pc{i}", files, f"Trajectory along volume PC{i}",
                           [f"Volume PC{i} · {j + 1}/{len(files)}" for j in range(len(files))], {"kind": f"vol_pc{i}"})
        new = {key: v for key, v in arrays.items() if key not in ("labels", "centers_ind")}
        new["labels"], new["centers_ind"] = states.astype(np.int32), centres
        vol_pca_file = outdir / f"vol_pca_{k}.pkl"
        if vol_pca_file.exists():
            vol_pca = pt.load_array_pkl(vol_pca_file)
            if vol_pca.ndim == 2 and vol_pca.shape[1] >= 2 and len(vol_pca) == len(state_of_volume):
                new["embedding_volume_pca"] = vol_pca[klabels, :2].astype(np.float32)
        npz = ctx.path("latent.npz")
        np.savez(npz, **new)
        embeddings, axis = latent_embeddings(new)
        axis["Volume PCA"] = ("Volume PC1", "Volume PC2")
        gallery = cluster_gallery(ctx, means, counts, n, prefix="state") if len(means) >= 2 else []
        latent_report(ctx, embeddings, states, f"States of the landscape ({m} states, {k:,} volumes)", axis,
                      {s: ctx.rel(f) for s, f in enumerate(means)}, centres, colorings=stored_colorings(new),
                      clusters_info=gallery, cluster_name="State")
        ctx.add_table("States", ["State", "Volumes", "Particles", "%"],
                      [[s + 1, int(vol_counts[s]), int(counts[s]), f"{100.0 * counts[s] / n:.1f}"] for s in range(m)],
                      note="Each state groups similar generated volumes; its particles are those of its volumes' k-means "
                           "clusters. The state volume is the particle-weighted mean of its volumes.")
        if len(means) >= 3:
            series_similarity(ctx, means, [f"State {s + 1}" for s in range(len(means))],
                              "Similarity of the state volumes (correlation)",
                              note="States ordered by similarity: neighbours differ least.")
        for png, title in (("mask_slices.png", "Mask used to compare the volumes (central slices)"),):
            if (outdir / png).exists():
                ctx.add_image(title, outdir / png)
        ctx.add_text("Reading the landscape",
                     "Unlike the k-means clusters of the latent space, the states group volumes that look alike, so "
                     "particles far apart in the latent space but with the same structure end up together. Play the "
                     "state volumes in 3D, then select the particles of a state (cluster chips of the explorer, "
                     "“Keep…”) to refine it in CryoSPARC. The volume PC trajectories show the main ways the volumes "
                     "differ inside the mask.")
        w, zf = weights_and_z(ctx.abs(lat.meta["workdir"]), int(lat.meta["epoch"]))
        meta = {**{k_: v for k_, v in lat.meta.items() if k_ not in ("k",)}, "k": m, "landscape": True}
        ctx.add_output("latent", "latent", [npz, *[f for f in (zf, w) if f.exists()]],
                       f"Latent space with {m} landscape states", meta=meta)
        ctx.add_highlight("States", m)
        ctx.add_highlight("Volumes", f"{k:,}")
