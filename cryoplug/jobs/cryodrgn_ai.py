"""cryoDRGN-AI: ab initio heterogeneous reconstruction (Levy et al. 2025, Nat. Methods 22, 1486; ``cryodrgn abinit``).

Unlike ``train_vae``, cryoDRGN-AI needs no consensus poses: it learns the pose and the latent coordinates of every
particle together, from the images and their CTF. A training goes through three phases: a pretraining on 10,000
images with random poses, a few epochs of hierarchical pose search (HPS: each image against projections over a grid
of orientations refined step by step, slow) and stochastic gradient descent (SGD), in which the poses and latent
coordinates of each particle are parameters refined by gradient (an auto-decoder: no encoder).

CryoPlug adds what makes the results usable and trustworthy:

* an input check without poses (contrast of the average image), or with the consensus poses when there are some
  (backprojection, as for a training), which also chooses the contrast convention;
* live progress per phase with a time estimate, loss curves, pose stability between checkpoints, the reconstruction
  across epochs;
* the comparison with the consensus poses: common frame, hand (mirror), pose difference of every particle (a colour
  of the explorer); or the alignment of the reconstruction on a reference map;
* results in the frame and hand of the consensus (or reference): volumes overlay the consensus map, and the
  particles are written back with their learned poses (CryoSPARC .cs or RELION .star) for a cryoDRGN training, a
  backprojection or CryoSPARC;
* a validation: backprojection of the particles with their learned poses (half-set FSC);
* the analysis tools of the cryoDRGN trainings (explorer, cluster volumes, trajectories, landscape, selection).

cryoDRGN's own tools need care with these models (handled here and in :mod:`cryoplug.jobs.cryodrgn`): shifts are
saved in pixels (not fractions of the box), ``analyze`` writes into the training folder and ignores flip, low-pass
and downsampling, ``eval_vol`` reads these models from version 4.3 only, ``analyze_landscape`` inverts the sign of
their volumes and writes them with a pixel size of 1 Å.
"""
from __future__ import annotations

import os
import pickle
import re
import shlex
import time
from pathlib import Path
from typing import Any

import numpy as np

from cryoplug import poses as ps
from cryoplug.jobs import register
from cryoplug.jobs.base import InputData, JobContext, JobError, JobType, OutputDef, Param, Slot, extra_args_param
from cryoplug.jobs.cryodrgn import (
    ANALYSIS_OUTPUTS,
    BATCH_RE,
    CHECK_CHOICES,
    POSE_SOURCES,
    analysis_params,
    backprojection_check,
    cryodrgn_version,
    decide_lazy,
    finish_analysis,
    fmt_duration,
    gpus_param,
    lazy_param,
    prepare_particles,
    projection_tiles,
    strip_png,
    training_apix,
    training_failure_hint,
    version_text,
)
from cryoplug.jobs.series import add_series

ABINIT_EPOCH_RE = re.compile(r"=====> (Pretrain|HPS|SGD) Epoch: (\d+) finished in (?:(\d+) days?, )?(\d+):(\d+):"
                             r"(\d+(?:\.\d+)?); total loss = ([^\s;]+)")
ABINIT_MIN = (4, 2)  # first release with ``cryodrgn abinit`` (cryoDRGN-AI)
EVAL_MIN = (4, 3)  # first release whose eval_vol reads cryoDRGN-AI checkpoints
CONTRAST_CHOICES = ["auto (from the images)", "invert (cryoDRGN default)", "do not invert"]
SGD_SPEEDUP = 15.0  # rough time per image of a pose-search epoch over an SGD epoch (20 in Levy et al. at 128 px)
PHASES = {"Pretrain": "Pretraining", "HPS": "Pose search", "SGD": "SGD"}


def is_abinit(meta: dict[str, Any] | None) -> bool:
    """Whether a latent output comes from a cryoDRGN-AI model."""
    return bool((meta or {}).get("abinit"))


# ----------------------------------------------------------------- versions
def require_abinit(ctx: JobContext, version: tuple[int, int, int]) -> None:
    if version < ABINIT_MIN:
        raise JobError(f"cryoDRGN-AI (cryodrgn abinit) needs cryoDRGN {version_text(ABINIT_MIN)} or later; this one is "
                       f"{version_text(version)}: update it (pip install -U cryodrgn)")


def require_eval(version: tuple[int, int, int]) -> None:
    if version < EVAL_MIN:
        raise JobError(f"cryoDRGN {version_text(version)} cannot generate the volumes of a cryoDRGN-AI model at chosen "
                       f"points (eval_vol reads these models from {version_text(EVAL_MIN)}): update cryoDRGN "
                       "(pip install -U cryodrgn)")


# -------------------------------------------------------------- checkpoints
def abinit_checkpoints(workdir: Path) -> list[int]:
    """Epochs saved by cryodrgn abinit (weights.N.pkl with pose.N.pkl; epoch 0 is the pretraining)."""
    out = []
    for f in Path(workdir).glob("weights.*.pkl"):
        m = re.fullmatch(r"weights\.(\d+)\.pkl", f.name)
        if m and (Path(workdir) / f"pose.{m.group(1)}.pkl").exists():
            out.append(int(m.group(1)))
    return sorted(out)


def learned_poses(workdir: Path, epoch: int, box: int) -> tuple[np.ndarray, np.ndarray | None]:
    """Poses of a checkpoint in cryoDRGN's usual units: cryodrgn abinit saves its shifts in pixels of the training
    images, train_vae, backproject_voxel and the pose parsers use fractions of the box."""
    from cryoplug import particles as pt
    obj = pt.load_pkl(Path(workdir) / f"pose.{epoch}.pkl")
    rot, trans = (obj[0], obj[1]) if isinstance(obj, (tuple, list)) else (obj, None)
    rot = np.asarray(rot, dtype=np.float64).reshape(-1, 3, 3)
    if trans is not None:
        trans = np.asarray(trans, dtype=np.float64).reshape(-1, 2) / float(box)
    return rot, trans


def save_poses(path: Path, rot: np.ndarray, trans: np.ndarray | None) -> Path:
    with open(path, "wb") as fh:
        pickle.dump((np.asarray(rot, dtype=np.float32), np.asarray(trans, dtype=np.float32)) if trans is not None
                    else np.asarray(rot, dtype=np.float32), fh)
    return path


def consensus_poses(ctx: JobContext, path: str | Path | None, ind: np.ndarray | None,
                    n: int) -> tuple[np.ndarray, np.ndarray | None] | None:
    """Consensus poses (cryoDRGN format) of the particles of a model, or None."""
    from cryoplug import particles as pt
    if not path or not ctx.abs(str(path)).exists():
        return None
    try:
        obj = pt.load_pkl(ctx.abs(str(path)))
    except Exception as exc:  # noqa: BLE001 - optional comparison
        ctx.warn(f"Consensus poses not read ({exc})")
        return None
    rot, trans = (obj[0], obj[1]) if isinstance(obj, (tuple, list)) else (obj, None)
    rot = np.asarray(rot, dtype=np.float64)
    trans = None if trans is None else np.asarray(trans, dtype=np.float64)
    if ind is not None and len(rot) != n and len(rot) > int(ind.max(initial=0)):
        rot, trans = rot[ind], (trans[ind] if trans is not None else None)
    if len(rot) != n:
        ctx.warn(f"The consensus poses ({len(rot):,}) do not match the particles ({n:,}): no comparison")
        return None
    return rot, trans


# ---------------------------------------------------------------------- log
def abinit_row(m: re.Match) -> dict[str, Any]:
    seconds = int(m.group(3) or 0) * 86400 + int(m.group(4)) * 3600 + int(m.group(5)) * 60 + float(m.group(6))
    try:
        loss = float(m.group(7))
    except ValueError:
        loss = float("nan")
    return {"epoch": int(m.group(2)), "phase": m.group(1), "seconds": seconds, "loss": loss}


def parse_abinit_log(path: Path) -> list[dict[str, Any]]:
    rows = []
    if Path(path).exists():
        for line in Path(path).read_text(errors="replace").splitlines():
            m = ABINIT_EPOCH_RE.search(line)
            if m:
                rows.append(abinit_row(m))
    return rows


def chain_rows(chain: list[Path]) -> list[dict[str, Any]]:
    by_epoch: dict[int, dict[str, Any]] = {}
    for workdir in chain:
        for row in parse_abinit_log(workdir / "run.log"):
            by_epoch[row["epoch"]] = row
    return [by_epoch[e] for e in sorted(by_epoch)]


def abinit_loss_plot(ctx: JobContext, rows: list[dict[str, Any]]) -> None:
    series = []
    for phase, name in (("HPS", "Pose search epochs (HPS)"), ("SGD", "Gradient descent epochs (SGD)")):
        pts = [r for r in rows if r["phase"] == phase and np.isfinite(r["loss"])]
        if pts:
            series.append({"name": name, "x": [r["epoch"] for r in pts], "y": [round(float(r["loss"]), 6) for r in pts]})
    if series:
        ctx.add_plot("Training loss", series, x_label="Epoch", y_label="Data error", key="losses",
                     note="Error between the images and the projections of the model. It falls while the pose search finds "
                          "the poses, drops again when SGD starts refining the poses and latent coordinates of each particle, "
                          "then levels off. The pretraining epoch (random poses) is not shown.")


def phase_times(rows: list[dict[str, Any]]) -> dict[str, float]:
    out = {}
    for phase in ("HPS", "SGD"):
        values = [r["seconds"] for r in rows if r["phase"] == phase and r.get("seconds")]
        if values:
            out[phase] = float(np.median(values))
    return out


class AbinitMonitor:
    """Follows cryodrgn abinit: phase, progress and time left on the job card (the pose search is much slower than
    the SGD epochs, so the estimate weighs each phase by its own speed), loss curves after each epoch."""

    def __init__(self, ctx: JobContext, n: int, last_epoch: int, pose_epochs: int, pretrain: int, first_epoch: int = 0,
                 span: tuple[float, float] = (0.1, 0.85), history: list[dict[str, Any]] | None = None, on_epoch=None):
        self.ctx, self.n, self.last, self.pose_epochs = ctx, max(1, n), last_epoch, pose_epochs
        self.pretrain_images = min(max(pretrain, 0), self.n) if first_epoch == 0 else 0
        self.first, self.span = first_epoch, span
        self.history = list(history or [])
        self.rows: list[dict[str, Any]] = []
        self.on_epoch = on_epoch
        self.started = self.epoch_started = time.time()
        self.updated = 0.0

    def phase(self, epoch: int) -> str:
        return "Pretrain" if epoch == 0 else ("HPS" if epoch <= self.pose_epochs else "SGD")

    def images(self, epoch: int) -> int:
        return self.pretrain_images if epoch == 0 else self.n

    def rate(self, phase: str) -> float | None:
        values = [r["seconds"] / max(1, self.images(r["epoch"])) for r in self.rows[-6:] if r["phase"] == phase and r["seconds"]]
        return float(np.median(values)) if values else None

    def remaining(self, epoch: int, fraction: float) -> tuple[float | None, bool]:
        """Seconds left (None when unknown) and whether the estimate is rough (SGD speed not measured yet)."""
        current = self.phase(epoch)
        rates = {ph: self.rate(ph) for ph in ("Pretrain", "HPS", "SGD")}
        if rates[current] is None and fraction > 0.02:
            rates[current] = (time.time() - self.epoch_started) / (fraction * max(1, self.images(epoch)))
        rough = False
        if rates["SGD"] is None and rates["HPS"] is not None:
            rates["SGD"], rough = rates["HPS"] / SGD_SPEEDUP, True
        total = 0.0
        if rates[current] is not None:
            total += (1 - fraction) * self.images(epoch) * rates[current]
        elif fraction < 1:
            return None, True
        for e in range(epoch + 1, self.last + 1):
            r = rates[self.phase(e)]
            if r is None:
                return None, True
            total += self.images(e) * r
        return total, rough

    def on_line(self, line: str) -> None:
        m = ABINIT_EPOCH_RE.search(line)
        if m:
            row = abinit_row(m)
            self.rows.append(row)
            self.epoch_started = time.time()
            self.report(row["epoch"], 1.0, finished=True)
            abinit_loss_plot(self.ctx, self.history + self.rows)
            if self.on_epoch:
                self.on_epoch(row)
            return
        m = BATCH_RE.search(line)
        if m and time.time() - self.updated > 3:
            epoch = int(m.group(1))
            self.report(epoch, min(1.0, int(m.group(3)) / max(1, self.images(epoch) or int(m.group(4)))))

    def report(self, epoch: int, fraction: float, finished: bool = False) -> None:
        self.updated = time.time()
        phase = self.phase(epoch)
        left, rough = self.remaining(epoch, fraction)
        msg = f"{PHASES[phase]} · epoch {epoch}/{self.last}" + (" done" if finished else f" · {fraction:.0%}")
        rate = self.rate(phase)
        if rate and phase != "Pretrain":
            msg += f" · {fmt_duration(rate * self.images(epoch))} per epoch"
        if left is not None and left > 0:
            msg += f" · {'roughly' if rough else 'about'} {fmt_duration(left)} left"
        spent = time.time() - self.started
        if left is not None and spent + left > 0:
            done = spent / (spent + left)
        else:
            done = (epoch - self.first + fraction) / max(1, self.last - self.first + 1)
        lo, hi = self.span
        self.ctx.progress(lo + (hi - lo) * min(1.0, max(0.0, done)), msg, log=False)


# ------------------------------------------------------------ pose stability
class PoseStability:
    """Angle between the poses of consecutive checkpoints: large while the pose search finds the poses, small and
    steady once they have converged. Updated while the training runs."""

    def __init__(self, ctx: JobContext, chain: list[Path], box: int):
        self.ctx, self.chain, self.box = ctx, chain, box
        self.rows: list[dict[str, Any]] = []
        self.last_epoch: int | None = None
        self.last_rot: np.ndarray | None = None

    def update(self, _row: dict[str, Any] | None = None) -> None:
        found: dict[int, Path] = {}
        for folder in self.chain:
            for e in abinit_checkpoints(folder):
                found[e] = folder
        for e in sorted(found):
            if e < 1 or (self.last_epoch is not None and e <= self.last_epoch):
                continue
            try:
                rot, _t = learned_poses(found[e], e, self.box)
            except Exception:  # noqa: BLE001 - written later, or unreadable
                continue
            if self.last_rot is not None and len(self.last_rot) == len(rot):
                ang = ps.pose_change(self.last_rot, rot)
                self.rows.append({"epoch": e, "previous": self.last_epoch, "median": float(np.median(ang)),
                                  "p90": float(np.percentile(ang, 90)), "moved": float(100.0 * (ang > 10).mean())})
            self.last_epoch, self.last_rot = e, rot
        if self.rows:
            self.report()

    def report(self) -> None:
        e = [r["epoch"] for r in self.rows]
        self.ctx.add_plot("Pose changes between checkpoints", [
            {"name": "Median change (°)", "x": e, "y": [round(r["median"], 3) for r in self.rows]},
            {"name": "90th percentile (°)", "x": e, "y": [round(r["p90"], 3) for r in self.rows]}],
            x_label="Epoch", y_label="Angle since the previous checkpoint (°)", key="pose_change",
            note="During the pose search the poses jump as they are found; once SGD refines them they only move by a "
                 "few degrees. Small, steady values over the last checkpoints: the poses have converged.")
        self.ctx.add_plot("Particles whose pose still changes", [
            {"name": "Pose changed by more than 10° (%)", "x": e, "y": [round(r["moved"], 3) for r in self.rows]}],
            x_label="Epoch", y_label="% of the particles", y_range=[0, 100], key="pose_moved",
            note="A few per cent can remain: junk particles have no stable pose.")

    def converged(self) -> bool | None:
        """Poses stable over the last checkpoints (median change ≤ 2°, under 5 % of the particles moving > 10°)."""
        sgd = [r for r in self.rows[-3:]]
        if len(sgd) < 2:
            return None
        return all(r["median"] <= 2.0 and r["moved"] <= 5.0 for r in sgd[-2:])


# ------------------------------------------------------------- input checks
def image_contrast(ctx: JobContext, prep: dict[str, Any], sample: int = 3000, seed: int = 0) -> dict[str, Any] | None:
    """Contrast of the average image: raw particles are darker than the solvent (cryoDRGN inverts the images by
    default for that reason); brighter particles mean images already inverted (train without inverting them)."""
    from scipy import ndimage

    from cryoplug import particles as pt
    from cryoplug.imaging import write_png
    try:
        reader = pt.ParticleImages(prep["stack"], prep.get("datadir"))
    except Exception as exc:  # noqa: BLE001 - reported
        ctx.warn(f"The particle images cannot be read ({exc})")
        return None
    try:
        rows = pt.load_array_pkl(prep["ind"]).astype(np.int64) if prep.get("ind") else None
        n = len(rows) if rows is not None else reader.n
        pick = np.sort(np.random.default_rng(seed).choice(n, size=min(n, sample), replace=False))
        acc = None
        for i in pick:
            img = reader.image(int(rows[i]) if rows is not None else int(i)).astype(np.float64)
            acc = img if acc is None else acc + img
    except Exception as exc:  # noqa: BLE001 - reported
        ctx.warn(f"The particle images cannot be read ({exc})")
        return None
    finally:
        reader.close()
    mean = ndimage.gaussian_filter(acc / len(pick), sigma=max(1.0, acc.shape[0] / 64))
    d = mean.shape[0]
    yy, xx = np.indices(mean.shape)
    r = np.sqrt((xx - d / 2) ** 2 + (yy - d / 2) ** 2) / d
    ring = mean[(r > 0.36) & (r < 0.46)]
    score = float((mean[r < 0.12].mean() - ring.mean()) / (ring.std() or 1.0))
    status = "dark" if score < -3 else ("bright" if score > 3 else "unclear")
    png = write_png(ctx.path("average_image.png"), pt.particle_tile(mean, size=160))
    return {"score": score, "status": status, "n": len(pick), "image": png}


def report_contrast(ctx: JobContext, result: dict[str, Any], uninvert: bool) -> None:
    word = {"dark": "darker than the solvent", "bright": "brighter than the solvent", "unclear": "unclear"}[result["status"]]
    expected = "bright" if uninvert else "dark"
    status = "good" if result["status"] == expected else ("warn" if result["status"] == "unclear" else "bad")
    ctx.add_metrics(f"Input check: average of {result['n']:,} particle images", [
        {"label": "Particles in the raw images", "value": f"{word} ({result['score']:+.1f} SD)", "status": status,
         "target": "darker (cryoDRGN inverts the images)" if not uninvert else "brighter (images used as they are)"},
        {"label": "Images", "value": "used as they are (--uninvert-data)" if uninvert else "inverted (cryoDRGN default)",
         "status": None, "target": ""}], key="contrast")
    ctx.add_image("Average of the particle images (low-passed)", result["image"], key="contrast_image")


def report_validation(ctx: JobContext, result: dict[str, Any], full: Path, used: int) -> None:
    """Report of the backprojection of the particles with their learned poses."""
    images = f"{used:,} particles" if used else "all the particles"
    metrics = []
    if "res143" in result:
        metrics.append({"label": "Resolution with the learned poses (half-set FSC = 0.143)",
                        "value": f"{result['res143']:.1f} Å", "status": result["fsc_status"],
                        "target": f"near Nyquist ({result['nyquist']:.1f} Å) for good poses"})
        metrics.append({"label": "Half-set FSC = 0.5", "value": f"{result['res05']:.1f} Å", "status": None, "target": ""})
    word = {"good": "positive", "bad": "negative", "warn": "unclear"}[result["contrast_status"]]
    metrics.append({"label": "Density of the particle", "value": f"{word} ({result['contrast']:+.1f} SD)",
                    "status": result["contrast_status"], "target": "positive"})
    ctx.add_metrics(f"Validation: backprojection of {images} with their learned poses", metrics, key="validation")
    if "fsc" in result:
        ctx.add_plot("Validation: half-set FSC with the learned poses", [
            {"name": "FSC (soft spherical mask)", "x": result["frequency"], "y": result["fsc"]}],
            x_label="Resolution (Å)", y_label="FSC", x_kind="resolution", key="validation_fsc",
            hlines=[{"y": 0.143, "label": "0.143"}, {"y": 0.5, "label": "0.5"}], y_range=[-0.1, 1.05],
            note="A plain reconstruction of the particles with the poses cryoDRGN-AI found (heterogeneity averaged, at the "
                 "training pixel size). Signal up to high resolution means accurate poses; a curve that drops at low "
                 "resolution means the pose search did not succeed (or the particles are very heterogeneous).")
    ctx.add_output("validation", "map", full, f"Backprojection of {images} with the learned poses")
    half_a, half_b = full.parent / "half_map_a.mrc", full.parent / "half_map_b.mrc"
    if half_a.exists() and half_b.exists():
        ctx.add_output("validation_half_maps", "half_maps", [half_a, half_b], "Half maps (learned poses)")
    if "res143" in result:
        ctx.add_highlight("Learned poses", f"{result['res143']:.1f} Å", result["fsc_status"])


# --------------------------------------------------------------- comparison
def report_comparison(ctx: JobContext, cmp: dict[str, Any], extent: float | None) -> dict[str, Any]:
    """Pose comparison with the consensus: metrics, distribution, what it means. Returns the summary kept in the
    metadata of the outputs."""
    n = len(cmp["angle"])
    hand = {"clear": "certain", "weak": "probable", "none": "uncertain"}[cmp["hand_confidence"]]
    med_view = float(np.median(cmp["view_angle"]))
    shift_a = cmp["shift_error"] * extent if cmp["shift_error"] is not None and extent else None
    within10, within30 = cmp["within"][10], cmp["within"][30]
    agree = "good" if within10 >= 0.7 else ("warn" if within30 >= 0.5 else "bad")
    metrics = [
        {"label": "Hand of the cryoDRGN-AI model", "value": ("mirror image of the consensus" if cmp["mirror"] else
                                                             "same as the consensus") + f" ({hand})",
         "status": {"clear": "good", "weak": "warn", "none": "bad"}[cmp["hand_confidence"]],
         "target": f"median {cmp['median']:.1f}° with this hand, {cmp['other_hand_median']:.0f}° with the other"},
        {"label": "Median pose difference", "value": f"{cmp['median']:.1f}°", "status": agree,
         "target": "a few degrees when both are right"},
        {"label": "Particles within 10° / 30°", "value": f"{100 * within10:.0f} % / {100 * within30:.0f} %",
         "status": agree, "target": ""},
        {"label": "Median viewing direction difference", "value": f"{med_view:.1f}°", "status": None, "target": ""},
    ]
    if shift_a is not None:
        metrics.append({"label": "Median shift difference", "value": f"{float(np.median(shift_a)):.1f} Å", "status": None,
                        "target": ""})
    if cmp["symmetry"] != "C1":
        metrics.append({"label": "Symmetry of the consensus", "value": f"{cmp['symmetry']} (equivalent poses not counted "
                        "as differences)", "status": None, "target": ""})
    ctx.add_metrics(f"Comparison with the consensus poses ({n:,} particles)", metrics, key="comparison")
    edges = np.arange(0, 185, 5)
    hist = np.histogram(np.clip(cmp["angle"], 0, 180), bins=edges)[0] * 100.0 / max(1, n)
    ctx.add_plot("Pose difference from the consensus", [
        {"name": "% of the particles", "x": (edges[:-1] + 2.5).tolist(), "y": np.round(hist, 3).tolist()}],
        x_label="Angle between the two poses of a particle (°)", y_label="% of the particles", key="comparison_hist",
        note="After bringing the cryoDRGN-AI model into the frame of the consensus. A peak near 0° holds the particles on "
             "which both agree; a flat tail holds particles whose pose differs (junk, other species, or views one of the "
             "two got wrong). Colour the explorer by 'Pose difference from the consensus' to see where they are. Peaks "
             "at 180° or 360°/n suggest a (pseudo-)symmetry: give it in 'Symmetry of the consensus'.")
    advice = []
    if cmp["hand_confidence"] == "none":
        advice.append("The poses differ too much from the consensus to tell the hand or the common frame: the ab initio run "
                      "may not have converged (pose stability, validation), or the consensus poses are poor. The results "
                      "are left in the frame of the cryoDRGN-AI model.")
    elif within10 >= 0.7:
        advice.append(f"Both reconstructions agree on {100 * within10:.0f} % of the particles (within 10°): the consensus "
                      "poses are reliable for most particles, so the heterogeneity seen here is not a consequence of pose "
                      "errors. A cryoDRGN training with the consensus poses (at 256 px, large network) is a good next "
                      "step after cleaning.")
    else:
        advice.append(f"Only {100 * within10:.0f} % of the particles agree within 10° and {100 * (1 - within30):.0f} % differ "
                      "by more than 30°. Colour the explorer by the pose difference: particles that differ and gather in "
                      "clusters are junk or another species (remove them); spread over the whole latent space, they point "
                      "to poses that one of the two reconstructions got wrong, e.g. on a flexible particle where the "
                      "consensus aligned the largest part.")
    if cmp["mirror"] and cmp["hand_confidence"] != "none":
        advice.append("The cryoDRGN-AI model came out as the mirror image of the consensus (an ab initio reconstruction cannot "
                      "tell the hands apart). Its volumes and poses are flipped back here: nothing to do.")
    ctx.add_text("Comparison with the consensus: what it means", "\n\n".join(advice), key="comparison_advice")
    ctx.add_highlight("Hand", "mirrored, corrected" if cmp["mirror"] else "as consensus",
                      {"clear": "good", "weak": "warn", "none": "bad"}[cmp["hand_confidence"]])
    ctx.add_highlight("Poses within 10°", f"{100 * within10:.0f} %", agree)
    return {"median": round(cmp["median"], 2), "within10": round(within10, 4), "within30": round(within30, 4),
            "mirror": cmp["mirror"], "hand_confidence": cmp["hand_confidence"], "symmetry": cmp["symmetry"]}


def report_map_alignment(ctx: JobContext, result: dict[str, Any], name: str) -> None:
    hand = {"clear": "certain", "weak": "probable", "none": "uncertain"}[result["hand_confidence"]]
    ctx.add_metrics(f"Alignment on the reference map ({name})", [
        {"label": "Hand of the cryoDRGN-AI model", "value": ("mirror image of the reference" if result["mirror"] else
                                                             "same as the reference") + f" ({hand})",
         "status": {"clear": "good", "weak": "warn", "none": "bad"}[result["hand_confidence"]],
         "target": f"correlation {result['score']:.3f} with this hand, {result['other_hand_score']:.3f} with the other"},
        {"label": "Correlation after alignment (low-passed)", "value": f"{result['score']:.3f}",
         "status": "good" if result["score"] > 0.8 else ("warn" if result["score"] > 0.5 else "bad"),
         "target": f"at {2 * result['pixel']:.0f} Å"}], key="map_alignment")
    ctx.add_highlight("Hand", "mirrored, corrected" if result["mirror"] else "as reference",
                      {"clear": "good", "weak": "warn", "none": "bad"}[result["hand_confidence"]])


# ----------------------------------------------------------------- outputs
def write_learned_particles(ctx: JobContext, source: Path, rot: np.ndarray, trans: np.ndarray | None,
                            meta: dict[str, Any], name: str = "particles_abinit") -> Path | None:
    """The particle file with the learned poses in its own format (CryoSPARC .cs or RELION .star), row for row."""
    from cryoplug import particles as pt
    trans = np.zeros((len(rot), 2)) if trans is None else trans
    try:
        if source.suffix.lower() == ".star":
            box = float(meta.get("box") or 0)
            if not box:
                raise ValueError("the image size is unknown")
            return pt.star_with_poses(source, ctx.path(f"{name}.star"), ps.matrix_to_relion(rot), trans * box,
                                      apix=meta.get("pixel_size"))
        data = pt.read_cs(source)
        if len(data) != len(rot):
            raise ValueError(f"{source.name} has {len(data):,} particles, the model {len(rot):,}")
        box = float(np.asarray(data["blob/shape"][0]).ravel()[0]) if "blob/shape" in (data.dtype.names or ()) else float(meta.get("box") or 0)
        if not box:
            raise ValueError("the image size is unknown")
        pose, shift = ps.cryodrgn_to_cryosparc(rot, trans, box)
        return pt.write_cs(pt.with_poses(data, pose, shift), ctx.path(f"{name}.cs"))
    except (OSError, ValueError, KeyError) as exc:
        ctx.warn(f"The particles with their learned poses could not be written: {exc}")
        return None


def finish_volumes(files: list[Path], frame: dict[str, Any] | None, lowpass: float = 0.0, apix: float | None = None,
                   mask: np.ndarray | None = None, frame_box: int | None = None) -> None:
    """Volumes of a cryoDRGN-AI model brought into the reference frame (and hand), low-passed if asked, with the
    pixel size of the training images in their header. Rewritten in place. With a ``mask`` of the molecule (frame of
    the model), a volume denser outside it than inside has the wrong sign and is inverted (analyze_landscape of
    cryoDRGN 4.3 inverts the volumes of these models)."""
    from cryoplug.mrc import MapVolume, fourier_filter
    for f in files:
        vol = MapVolume.read(f)
        data = vol.data.astype(np.float32)
        if mask is not None and mask.shape == data.shape and (mask > 0.5).any() and (mask <= 0.5).any():
            if float(data[mask > 0.5].mean()) < float(data[mask <= 0.5].mean()):
                data = -data
        if frame:
            data = ps.frame_volume(data, frame, frame_box=frame_box)
        voxel = np.array([apix] * 3) if apix else vol.voxel
        out = MapVolume(data=data, voxel=voxel)
        if lowpass:
            out = out.like(fourier_filter(out, lowpass=lowpass).astype(np.float32))
        out.write(f)


def volume_frame(lat_meta: dict[str, Any], flip: bool = False) -> dict[str, Any] | None:
    """The frame applied to the volumes generated from a cryoDRGN-AI model (with a change of hand if asked)."""
    frame = lat_meta.get("frame")
    if flip:
        return ps.flipped_frame(frame)
    return frame


def abinit_advice(summary: dict[str, Any] | None, frame: dict[str, Any] | None, stable: bool | None):
    """What to do next after a cryoDRGN-AI run (used by finish_analysis with what the analysis found)."""
    def advice(flagged: dict[str, list[int]], counts: np.ndarray, n: int, outliers: int,
               r2: dict[str, tuple[float, float]]) -> str:
        lines = []
        junk = sorted(set(flagged.get("weak", []) + flagged.get("noisy", []) + flagged.get("blurry", [])))
        if junk:
            share = 100.0 * sum(int(counts[i]) for i in junk if i < len(counts)) / max(1, n)
            lines.append(f"• Junk? Clusters {', '.join(str(i + 1) for i in junk)} (flagged in the gallery) hold {share:.1f} % of "
                         "the particles. Look at them in 3D, remove them in the explorer (“Remove…”), then run cryoDRGN-AI "
                         "again on the cleaned particles: the second round resolves the heterogeneity better (Levy et al. "
                         "2025 did so for the DSL1/SNARE complex and the V-ATPase).")
        else:
            lines.append("• Junk: no cluster volume looks weak, noisy or featureless. Still play every volume in 3D: broken "
                         "particles show as partial density.")
        if outliers:
            lines.append(f"• Outliers: {outliers:,} particles ({100.0 * outliers / max(1, n):.1f} %) lie far from the others "
                         "(colour by ‖z‖); remove them with the “Outliers” button when they look like junk.")
        if summary:
            if summary["within30"] < 0.7:
                lines.append(f"• Poses: {100 * (1 - summary['within30']):.0f} % of the particles differ from the consensus by "
                             "more than 30°: colour the explorer by 'Pose difference from the consensus'. Clustered, they are "
                             "junk or another species; spread out, the consensus poses are doubtful for them.")
            else:
                lines.append("• Poses: they agree with the consensus for most particles. After cleaning, a cryoDRGN training "
                             "with the consensus poses at 256 px (large network) gives finer detail.")
        if frame is None:
            lines.append("• Hand: unknown (no consensus poses nor reference map). An ab initio model may be the mirror image "
                         "of the molecule: check against a known structure, or run again with a reference map connected.")
        view = r2.get("Viewing direction")
        if view and max(view) >= 0.3:
            lines.append("• The latent space follows the viewing directions (R² ≥ 0.3): cryoDRGN-AI does not separate pose "
                         "from conformation explicitly, so a 'motion' may be a rigid rotation of the map. Check that the "
                         "trajectories change the shape, not only the orientation.")
        if stable is False:
            lines.append("• The poses still change between the last checkpoints: continue the training (cryoDRGN continue "
                         "training), then run the convergence check.")
        else:
            lines.append("• Convergence: run the cryoDRGN convergence check before trusting fine details.")
        lines.append("• Validation: refine the particles of each state in CryoSPARC — the 'Particles with their learned "
                     "poses' output imports as is (Import Particle Stack), and a reconstruction with these poses needs no "
                     "new alignment.")
        return "\n".join(lines)
    return advice


# ---------------------------------------------------------------- training
def abinit_settings(p: dict[str, Any], uninvert: bool) -> dict[str, Any]:
    """The options a cryoDRGN-AI model keeps, to continue its training with the same settings."""
    return {"zdim": int(p["zdim"]), "pose_epochs": int(p["pose_epochs"]), "pretrain": int(p["pretrain"]),
            "t_extent": float(p["t_extent"]), "layers": int(p["layers"]), "dim": int(p["dim"]),
            "batch_hps": int(p["batch_hps"]), "batch_sgd": int(p["batch_sgd"]), "checkpoint": int(p["checkpoint"]),
            "amp": bool(p["amp"]), "encoder": bool(p["encoder"]), "uninvert": bool(uninvert), "seed": int(p["seed"]),
            "extra": str(p.get("extra_args") or "")}


def abinit_args(settings: dict[str, Any], prep: dict[str, Any], lazy: bool, gpus: int) -> list[str]:
    args = [str(prep["stack"]), "--ctf", str(prep["ctf"]), "--zdim", str(settings["zdim"]),
            "--epochs-pose-search", str(settings["pose_epochs"]), "--n-imgs-pretrain", str(settings["pretrain"]),
            "--layers", str(settings["layers"]), "--dim", str(settings["dim"]), "--t-extent", f"{settings['t_extent']:g}",
            "--batch-size-hps", str(settings["batch_hps"]), "--batch-size-sgd", str(settings["batch_sgd"]),
            "--checkpoint", str(settings["checkpoint"])]
    if prep.get("ind"):
        args += ["--ind", str(prep["ind"])]
    if prep.get("datadir"):
        args += ["--datadir", str(prep["datadir"])]
    if settings.get("uninvert"):
        args.append("--uninvert-data")
    if not settings.get("amp", True):
        args.append("--no-amp")
    if settings.get("encoder"):
        args.append("--use-conf-encoder")
    if settings.get("seed"):
        args += ["--seed", str(settings["seed"])]
    if lazy:
        args.append("--lazy")
    if gpus > 1:
        args.append("--multigpu")
    return args + shlex.split(settings.get("extra") or "")


def run_abinit(ctx: JobContext, args: list[str], workdir: Path, epochs: int, settings: dict[str, Any], n: int,
               load: Path | None = None, history: list[dict[str, Any]] | None = None, first_epoch: int = 0,
               span: tuple[float, float] = (0.12, 0.8), on_epoch=None) -> tuple[int, list[dict[str, Any]]]:
    """Runs cryodrgn abinit; returns the last saved epoch and the epoch rows of this run."""
    cmd = [ctx.executable("cryodrgn"), "abinit", *args, "-n", str(epochs), "-o", workdir, "--no-analysis"]
    if load is not None:
        cmd += ["--load", load]
    monitor = AbinitMonitor(ctx, n, epochs, settings["pose_epochs"], settings["pretrain"], first_epoch, span, history,
                            on_epoch)
    ctx.progress(span[0], f"cryoDRGN-AI: epochs {first_epoch} to {epochs}" if first_epoch else
                 f"cryoDRGN-AI: pretraining, {settings['pose_epochs']} pose search epochs, then SGD up to epoch {epochs}")
    try:
        ctx.run(cmd, tool="cryodrgn", on_line=monitor.on_line)
    except JobError as exc:
        hint = training_failure_hint(ctx, amp=settings.get("amp", True), command="abinit")
        raise JobError(f"{exc}{': ' + hint if hint else ''}") from None
    saved = abinit_checkpoints(workdir)
    if not saved:
        raise JobError("cryoDRGN-AI finished without saving its model (see the log)")
    rows = monitor.rows or parse_abinit_log(workdir / "run.log")
    if not monitor.rows:
        abinit_loss_plot(ctx, (history or []) + rows)
    times = phase_times(rows)
    if times:
        ctx.add_highlight("Time per epoch", " · ".join(f"{'pose search' if k == 'HPS' else 'SGD'} {fmt_duration(v)}"
                                                       for k, v in times.items()))
    return saved[-1], rows


# --------------------------------------------------------- after a training
def finish_abinit(ctx: JobContext, *, source: Path, source_meta: dict[str, Any], prep: dict[str, Any],
                  settings: dict[str, Any], chain: list[Path], epoch: int, params: dict[str, Any],
                  version: tuple[int, int, int], lazy: bool, consensus: str | None, consensus_source: str | None,
                  pose_source: str, stability: PoseStability, validate: bool = False, reference: InputData | None = None,
                  validate_images: int = 0, previous_frame: dict[str, Any] | None = None) -> None:
    """Poses, frame and hand, particles with their learned poses, reconstructions, validation and analysis of a
    cryoDRGN-AI model (after a training or a continuation)."""
    from cryoplug import particles as pt
    from cryoplug.mrc import MapVolume
    workdir, box = chain[-1], int(prep["box"])
    ind = pt.load_array_pkl(prep["ind"]).astype(np.int64) if prep.get("ind") else None
    apix = float(params.get("apix") or 0) or training_apix(ctx, {"ctf": prep["ctf"]}, box) or source_meta.get("pixel_size")
    rot, trans = learned_poses(workdir, epoch, box)
    n = len(rot)
    stability.update()

    # frame and hand: from the consensus poses, else from a reference map
    ctx.progress(0.81, "Comparing the learned poses with the consensus" if consensus else "Learned poses")
    frame, summary = None, None
    cons = consensus_poses(ctx, consensus, ind, n) if consensus else None
    extent = apix * box if apix else None
    if cons is not None:
        symmetry = str(params.get("symmetry") or "").strip() or str(source_meta.get("symmetry") or "C1")
        cmp = ps.compare_poses(rot, trans, cons[0], cons[1], symmetry=symmetry)
        if cmp["symmetry"] != symmetry.upper() and symmetry.upper() != "C1":
            ctx.warn(f"Symmetry {symmetry} is not handled by the pose comparison (C and D groups are): compared as C1")
        summary = report_comparison(ctx, cmp, extent)
        arrays = {"angle": cmp["angle"].astype(np.float32), "view_angle": cmp["view_angle"].astype(np.float32)}
        if cmp["shift_error"] is not None and extent:
            arrays["shift_error"] = (cmp["shift_error"] * extent).astype(np.float32)
        np.savez(ctx.path("pose_comparison.npz"), **arrays)
        if cmp["hand_confidence"] != "none":
            frame = ps.frame_dict(cmp["rotation"], cmp["mirror"], cmp["shift"], source="consensus poses",
                                  confidence=cmp["hand_confidence"])
    elif reference is not None:
        last_map = workdir / f"reconstruct.{epoch}.mrc"
        try:
            ref = MapVolume.read(ctx.abs(reference.files[0]))
            mine = MapVolume.read(last_map)
            ctx.progress(0.82, "Aligning the reconstruction on the reference map (both hands)")
            size = min(32, max(16, mine.data.shape[0]))
            result = ps.align_maps(mine.data, apix or mine.pixel_size, ref.data, ref.pixel_size, size=size,
                                   n_rotations=3000 if size >= 32 else 1200)
            report_map_alignment(ctx, result, reference.label or Path(reference.files[0]).name)
            if result["hand_confidence"] != "none" and result["score"] > 0.5:
                frame = ps.frame_dict(result["rotation"], result["mirror"], result["shift"], source="reference map",
                                      confidence=result["hand_confidence"])
            else:
                ctx.warn("The reconstruction does not match the reference map well enough to set the frame and the hand")
        except Exception as exc:  # noqa: BLE001 - optional step
            ctx.warn(f"Alignment on the reference map failed: {exc}")
    elif previous_frame is not None:  # a continuation keeps the frame found for the earlier epochs
        frame = previous_frame
        ctx.log(f"Frame of the earlier training kept ({previous_frame.get('source', 'reference')})")
    if frame is not None and not params.get("align", True):
        ctx.log("The results stay in the frame of the cryoDRGN-AI model ('Put the results in the consensus frame' is off)")
        frame = None
    if params.get("flip"):
        frame = ps.flipped_frame(frame)
        ctx.log("Volumes and poses flipped (change of hand)")
    if frame is not None:
        rot, trans = ps.apply_frame(rot, trans, frame)

    # learned poses: cryoDRGN format (rows of the prepared images) and the particle file with them
    if ind is not None:  # rows of the prepared image stack, as cryoDRGN reads them with --ind
        full_rot = np.repeat(np.eye(3)[None], int(ind.max()) + 1, axis=0)
        full_trans = np.zeros((len(full_rot), 2))
        full_rot[ind] = rot
        if trans is not None:
            full_trans[ind] = trans
        poses_pkl = save_poses(ctx.path("poses_learned.pkl"), full_rot, full_trans)
    else:
        poses_pkl = save_poses(ctx.path("poses_learned.pkl"), rot, trans)
    particles_file = write_learned_particles(ctx, source, rot, trans, source_meta)
    prepared = {"box": box, "stack": ctx.rel(prep["stack"]), "ctf": ctx.rel(prep["ctf"]), "datadir": prep.get("datadir"),
                "pose_source": "refinement", "poses": ctx.rel(poses_pkl)}
    if prep.get("ind"):
        prepared["ind"] = ctx.rel(prep["ind"])
    if consensus:  # already relative to the project
        prepared["consensus_poses"] = consensus
        prepared["consensus_pose_source"] = pose_source
    lineage: dict[str, Any] = {"particles": ctx.rel(particles_file or source), "box": box, "prepared": prepared,
                               "abinit": {**settings, "epochs": int(epoch), "version": version_text(version),
                                          "lazy": bool(lazy)},
                               "frame": frame, "workdirs": [ctx.rel(d) for d in chain]}
    if consensus_source:
        lineage["consensus_particles"] = consensus_source
    if summary:
        lineage["pose_comparison"] = summary
    if particles_file is not None:
        pmeta: dict[str, Any] = {k: source_meta[k] for k in ("format", "datadir", "symmetry", "resolution", "pixel_size",
                                                             "box") if k in source_meta}
        if particles_file.suffix == ".cs":
            pmeta.update(pt.summary(pt.read_cs(particles_file)))
        else:
            pmeta.update({"n_particles": n, "has_images": True, "has_poses": True, "has_ctf": True})
        pmeta.update({"has_poses": True, "poses_from": "cryoDRGN-AI",
                      "pose_frame": (frame or {}).get("source", "cryoDRGN-AI model"), "abinit_job": ctx.uid})
        if ind is None:
            pmeta["cryodrgn"] = {k: v for k, v in prepared.items() if k not in ("consensus_poses", "consensus_pose_source")}
        else:
            pmeta["parent_cryodrgn"] = {k: v for k, v in prepared.items()
                                        if k not in ("ind", "consensus_poses", "consensus_pose_source")}
            pmeta["parent_indices"] = prepared["ind"]
        ctx.add_output("particles", "particles", particles_file, f"Particles with their learned poses ({n:,})", meta=pmeta)

    # reconstructions across epochs (volume at the centre of the latent space), in the reference frame
    found: dict[int, Path] = {}
    for folder in chain:
        for e in abinit_checkpoints(folder):
            if (folder / f"reconstruct.{e}.mrc").exists():
                found[e] = folder / f"reconstruct.{e}.mrc"
    shown = [e for e in sorted(found) if e >= 1]
    if shown:
        out_dir = ctx.path("reconstructions")
        out_dir.mkdir(exist_ok=True)
        files = []
        for e in shown:
            dst = out_dir / f"reconstruct_{e:03d}.mrc"
            dst.write_bytes(found[e].read_bytes())
            files.append(dst)
        finish_volumes(files, frame, float(params.get("lowpass") or 0), apix)
        what = "volume at the centre of the latent space" if settings["zdim"] else "reconstruction"
        ctx.add_output("reconstruction", "map", files[-1], f"cryoDRGN-AI {what} (epoch {shown[-1]})")
        if len(files) > 1:
            add_series(ctx, "reconstructions", files, f"cryoDRGN-AI {what} across epochs",
                       [f"Epoch {e}" for e in shown], {"kind": "abinit_epochs"})
            from cryoplug.jobs.series import load_frames
            vols, _f = load_frames(files, max_box=64)
            tiles = projection_tiles(np.stack([v.data for v in vols]), size=64, axes=(0,))
            ctx.add_image(f"Reconstruction at epochs {', '.join(map(str, shown))} (projections)",
                          strip_png(ctx.path("reconstructions.png"), [t[0] for t in tiles]))

    if validate:
        ctx.progress(0.84, "Validation: backprojection with the learned poses")
        vprep = {**prep, "poses": str(poses_pkl), "n": n if ind is None else len(ind)}
        backprojection_check(ctx, vprep, bool(settings.get("uninvert")), lazy, first=int(validate_images or 0),
                             span=(0.84, 0.87), folder="validation", report=report_validation)

    stable = stability.converged()
    if settings["zdim"] <= 0:
        ctx.add_highlight("Particles", f"{n:,}")
        ctx.add_text("What to do next",
                     "• This homogeneous ab initio reconstruction gives a map and the pose of every particle. Use them as an "
                     "initial model, or train a heterogeneous model (latent dimensions > 0) on the same particles.\n"
                     "• The particles with their learned poses import into CryoSPARC as is (Import Particle Stack).")
        return
    ctx.progress(0.88, "Analysing the latent space")
    # the frame already includes the change of hand asked for: the volumes are not flipped a second time
    analyze_dir = abinit_analysis(ctx, workdir, epoch, {**params, "flip": False}, version, apix,
                                  {"abinit": settings, "frame": frame})
    finish_analysis(ctx, workdir, epoch, analyze_dir, params["ksample"], lineage,
                    title=f"Latent space of {n:,} particles (cryoDRGN-AI, epoch {epoch})",
                    advice=abinit_advice(summary, frame, stable))


# ======================================================================= jobs
@register
class CryodrgnAbinit(JobType):
    name = "cryodrgn_abinit"
    title = "cryoDRGN-AI ab initio reconstruction"
    category = "Heterogeneity"
    tool = "cryodrgn"
    software = ["cryoDRGN"]
    gpu = 1
    gpu_param = "gpus"
    cpus = 8
    description = ("Heterogeneous reconstruction without consensus poses (cryoDRGN-AI, cryodrgn abinit): the pose and the "
                   "latent coordinates of every particle are learnt together, from the images and their CTF. Free from "
                   "the bias of a consensus refinement and robust to junk, it can start right after a 2D classification. "
                   "Live progress per phase, pose stability, comparison with the consensus poses (hand, frame, pose "
                   "difference per particle), validation backprojection, particles written back with their learned poses, "
                   "and the same latent space tools as the cryoDRGN trainings.")
    inputs = [Slot("particles", ("particles",), "Particles",
                   help="Images with their CTF (e.g. Import from CryoSPARC of a Select 2D or refinement job). Consensus poses "
                        "are optional: when present they are compared with the learned ones."),
              Slot("reference", ("map",), "Reference map", required=False,
                   help="Optional, for particles without consensus poses: a map of the same molecule (consensus, ab initio "
                        "or database map) that sets the frame and the hand of the results.")]
    params = [
        Param("box", "int", 128, label="Image size for training", unit="px", min=0,
              help="Images are Fourier-cropped to this size (even). 128 px suits cryoDRGN-AI (used for nearly all the "
                   "datasets of its paper): the pose search is costly at larger sizes. 0 keeps the original size."),
        Param("zdim", "int", 8, label="Latent dimensions", min=0, max=128,
              help="Size of the latent space (8 suits most datasets). 0 gives a homogeneous ab initio reconstruction (one "
                   "map and the pose of every particle)."),
        Param("epochs", "int", 30, label="Epochs", min=2,
              help="Total passes over the particles, pose search included (cryoDRGN's default: 30). Large or very "
                   "heterogeneous datasets benefit from more (50-100), checked with the convergence check."),
        Param("pose_epochs", "int", 3, label="Pose search epochs", min=1,
              help="Epochs of hierarchical pose search before switching to gradient descent (cryoDRGN's default: 3, warm-up, "
                   "search and refinement). These are the slow epochs."),
        Param("check_inputs", "choice", CHECK_CHOICES[0], choices=CHECK_CHOICES, label="Input check",
              help="Average image of the particles (contrast), plus a backprojection with the consensus poses when the "
                   "particles have some: shows that images and CTF are read correctly before hours of GPU time."),
        Param("contrast", "choice", CONTRAST_CHOICES[0], choices=CONTRAST_CHOICES, label="Image contrast",
              help="cryoDRGN expects dark particles in the raw images and inverts them. auto: decided from the input check "
                   "(average image, backprojection). A wrong choice gives hollow, inside-out volumes."),
        Param("align", "bool", True, label="Put the results in the consensus frame",
              help="Volumes and poses are brought into the frame and hand of the consensus (or of the reference map), so "
                   "that they overlay the consensus map in the 3D viewer and the particles keep comparable poses."),
        Param("validate", "bool", True, label="Validation backprojection",
              help="Backprojects the particles with their learned poses (a few minutes): the half-set FSC tells how "
                   "accurate the poses are."),
        *analysis_params(),
        gpus_param(),
        lazy_param(),
        Param("symmetry", "str", "", label="Symmetry of the consensus", advanced=True, placeholder="from the particles",
              help="Point group of the consensus refinement (C or D groups), so that symmetry-equivalent poses are not "
                   "counted as differences. cryoDRGN-AI itself reconstructs without symmetry."),
        Param("pose_source", "choice", "refinement", choices=POSE_SOURCES, label="Consensus poses from", advanced=True,
              help="Type of CryoSPARC job the particles come from (to read their consensus poses, if any)."),
        Param("pretrain", "int", 10_000, label="Pretraining images", min=0, advanced=True,
              help="Images of the first epoch, with random poses (cryoDRGN's default: 10,000)."),
        Param("t_extent", "float", 20.0, label="Translation search extent", unit="px", min=0.0, advanced=True,
              help="Range of the in-plane shift search at the training size (cryoDRGN's default: 20). 0 = no shift search, "
                   "for particles already centred."),
        Param("layers", "int", 3, label="Network layers", min=1, advanced=True),
        Param("dim", "int", 256, label="Network width", min=16, advanced=True,
              help="Hidden layer width of the volume network (cryoDRGN-AI: 3 × 256)."),
        Param("batch_hps", "int", 16, label="Batch size (pose search)", min=1, advanced=True,
              help="Lower it (8, as in the paper) if the GPU runs out of memory during the pose search."),
        Param("batch_sgd", "int", 128, label="Batch size (SGD)", min=1, advanced=True),
        Param("checkpoint", "int", 5, label="Checkpoint every", unit="epochs", min=1, advanced=True,
              help="Model, poses and latent coordinates are saved every N epochs (and at each pose search epoch): the pose "
                   "stability and the convergence check compare them."),
        Param("amp", "bool", True, label="Mixed precision", advanced=True,
              help="Faster on recent GPUs (not used during the pose search)."),
        Param("encoder", "bool", False, label="Latent encoder", advanced=True,
              help="Predicts the latent coordinates with an image encoder instead of optimising them per particle (the "
                   "default auto-decoder suits noisy images better)."),
        Param("validate_images", "int", 0, label="Images backprojected (validation)", min=0, advanced=True,
              help="0 = all the particles."),
        Param("seed", "int", 0, label="Random seed", min=0, advanced=True, help="0 = random."),
        extra_args_param(),
    ]
    outputs = [*ANALYSIS_OUTPUTS,
               OutputDef("reconstruction", "map", "Reconstruction (centre of the latent space)"),
               OutputDef("particles", "particles", "Particles with their learned poses"),
               OutputDef("validation", "map", "Backprojection with the learned poses"),
               OutputDef("validation_half_maps", "half_maps", "Half maps with the learned poses"),
               OutputDef("reconstructions", "volume_series", "Reconstruction across epochs"),
               OutputDef("particles_prepared", "particles", "Particles prepared for cryoDRGN"),
               OutputDef("backprojection", "map", "Backprojection (input check)")]

    @classmethod
    def validate(cls, params, connected):
        problems = []
        box = int(params.get("box") or 0)
        if box % 2:
            problems.append(f"The image size must be even (got {box})")
        epochs, pose = int(params.get("epochs") or 0), int(params.get("pose_epochs") or 0)
        if epochs <= pose:
            problems.append(f"The number of epochs ({epochs}) must be larger than the pose search epochs ({pose})")
        return problems

    def run(self, ctx: JobContext) -> None:
        p = ctx.params
        particles = ctx.require("particles")
        version = cryodrgn_version(ctx)
        require_abinit(ctx, version)
        if version < EVAL_MIN:
            ctx.warn(f"cryoDRGN {version_text(version)} trains cryoDRGN-AI models, but its eval_vol cannot read them: "
                     f"trajectories, volumes at particles and the volume comparison of the convergence check need "
                     f"{version_text(EVAL_MIN)} (pip install -U cryodrgn)")
        prep = prepare_particles(ctx, particles, p["box"], p["pose_source"], need_poses=False)
        lazy = decide_lazy(ctx, p["lazy"], prep["n"], prep["box"])
        uninvert = self._contrast(ctx, prep, lazy)
        settings = abinit_settings(p, uninvert)
        n = int(prep["n"] or 0)
        ctx.log(f"cryoDRGN-AI: {n:,} particles at {prep['box']} px; latent space {p['zdim']}-D; {p['epochs']} epochs "
                f"({p['pose_epochs']} of pose search); network {p['layers']} × {p['dim']}")
        workdir = ctx.path("train")
        stability = PoseStability(ctx, [workdir], int(prep["box"]))
        epoch, _rows = run_abinit(ctx, abinit_args(settings, prep, lazy, p["gpus"]), workdir, p["epochs"], settings, n,
                                  on_epoch=stability.update)
        meta = particles.meta
        source = ctx.abs(meta["particles_file"]) if meta.get("particles_file") else Path(particles.files[0])
        consensus = ctx.rel(prep["poses"]) if prep.get("poses") else None
        finish_abinit(ctx, source=source, source_meta=meta, prep=prep, settings=settings, chain=[workdir], epoch=epoch,
                      params=p, version=version, lazy=lazy, consensus=consensus,
                      consensus_source=ctx.rel(source) if consensus else None, pose_source=p["pose_source"],
                      stability=stability, validate=p["validate"], reference=ctx.input("reference"),
                      validate_images=p["validate_images"])

    @staticmethod
    def _contrast(ctx: JobContext, prep: dict[str, Any], lazy: bool) -> bool:
        """Input check and contrast convention: True = use the images as they are (--uninvert-data)."""
        p = ctx.params
        choice = p["contrast"]
        uninvert = choice.startswith("do not")
        checking = not p["check_inputs"].startswith("no")
        stop = "stop" in p["check_inputs"]
        check = None
        if checking and prep.get("poses"):
            check = backprojection_check(ctx, prep, uninvert, lazy)
            if check is None and stop:
                raise JobError("The input check could not run (see the log), so the training was not started. Set 'Input "
                               "check' to 'check, then train' to try anyway.")
            if check and choice.startswith("auto") and check["contrast_status"] == "bad":
                uninvert = not uninvert
                ctx.warn("The backprojection gives negative density with the default contrast: the images are used "
                         f"{'as they are (--uninvert-data)' if uninvert else 'inverted'}")
            elif check and check.get("fsc_status") == "bad" and stop:
                raise JobError("The input check failed: the consensus poses or the CTF do not give a map (see the report). "
                               "The particles may not match their metadata; set 'Input check' to 'check, then train' to "
                               "train anyway (cryoDRGN-AI does not use these poses).")
        if checking or choice.startswith("auto"):
            image = image_contrast(ctx, prep)
            if image is None:
                if stop:
                    raise JobError("The particle images cannot be read (see the log), so the training was not started")
            else:
                if choice.startswith("auto") and (check is None or check["contrast_status"] == "warn"):
                    if image["status"] == "bright":
                        uninvert = True
                        ctx.log("The particles are brighter than the solvent in the images: they are used as they are "
                                "(--uninvert-data)")
                    elif image["status"] == "dark":
                        uninvert = False
                    else:
                        ctx.warn("The contrast of the particles is unclear in the average image: cryoDRGN's default "
                                 "(inversion) is used. Check the first reconstructions: hollow volumes mean the opposite "
                                 "contrast.")
                report_contrast(ctx, image, uninvert)
        return uninvert


def continue_abinit(ctx: JobContext, lat: InputData, p: dict[str, Any], version: tuple[int, int, int]) -> None:
    """More epochs of SGD for a cryoDRGN-AI model (same settings), then the same results as the training."""
    from cryoplug.jobs.cryodrgn import train_chain
    require_abinit(ctx, version)
    settings, prepared = dict(lat.meta["abinit"]), dict(lat.meta.get("prepared") or {})
    chain = train_chain(ctx, lat)
    saved = abinit_checkpoints(chain[-1])
    if not saved:
        raise JobError(f"No cryoDRGN-AI checkpoint in {chain[-1]}")
    done = saved[-1]
    if done <= int(settings["pose_epochs"]):
        raise JobError(f"The training stopped during the pose search (epoch {done}): start a new cryoDRGN-AI run")
    target = int(p["epochs"] or 2 * done)
    if target <= done:
        raise JobError(f"The training already has {done} epochs: give more in 'Train until epoch'")
    prep = {"stack": str(ctx.abs(prepared["stack"])), "ctf": str(ctx.abs(prepared["ctf"])), "datadir": prepared.get("datadir"),
            "box": int(prepared["box"]), "ind": str(ctx.abs(prepared["ind"])) if prepared.get("ind") else None}
    missing = [k for k in ("stack", "ctf") if not Path(prep[k]).exists()]
    if missing:
        raise JobError(f"The prepared particles are not available any more ({', '.join(missing)} missing)")
    n = int(lat.meta.get("n_particles") or 0)
    lazy = decide_lazy(ctx, p["lazy"] or bool(settings.get("lazy")), n, prep["box"])
    workdir = ctx.path("train")
    ctx.log(f"Continuing the cryoDRGN-AI model from epoch {done} until epoch {target}")
    chain = [*chain, workdir]
    stability = PoseStability(ctx, chain, prep["box"])
    epoch, _rows = run_abinit(ctx, abinit_args(settings, prep, lazy, p["gpus"]), workdir, target, settings, n,
                              load=chain[-2] / f"weights.{done}.pkl", history=chain_rows(chain[:-1]), first_epoch=done + 1,
                              on_epoch=stability.update)
    source = ctx.abs(lat.meta["particles"])
    source_meta = {"format": "star" if source.suffix == ".star" else "cs", "box": None}
    from cryoplug.jobs.heterogeneity import output_meta_of
    source_meta.update(output_meta_of(ctx, source))
    consensus = prepared.get("consensus_poses")
    finish_abinit(ctx, source=source, source_meta=source_meta, prep=prep, settings=settings, chain=chain, epoch=epoch,
                  params={**p, "align": True}, version=version, lazy=lazy, consensus=consensus,
                  consensus_source=lat.meta.get("consensus_particles"),
                  pose_source=prepared.get("consensus_pose_source") or "refinement", stability=stability,
                  previous_frame=lat.meta.get("frame"))


# ------------------------------------------------- analysis of these models
def abinit_analysis(ctx: JobContext, workdir: Path, epoch: int, params: dict[str, Any], version: tuple[int, int, int],
                    apix: float | None, lat_meta: dict[str, Any]) -> Path:
    """cryodrgn analyze for a cryoDRGN-AI model. cryoDRGN writes its results into ``analyze.N`` of the folder it is
    given (whatever -o says) and ignores flip, low-pass and downsampling: the model files are linked into this job's
    folder when the training belongs to another job, and the volumes are finished here (frame, hand, low-pass)."""
    target = Path(workdir)
    if not target.resolve().is_relative_to(ctx.job_dir.resolve()):
        target = ctx.path("model")
        target.mkdir(exist_ok=True)
        for name in ("config.yaml", f"weights.{epoch}.pkl", f"z.{epoch}.pkl", f"pose.{epoch}.pkl"):
            src, dst = Path(workdir) / name, target / name
            if src.exists() and not dst.exists():
                try:
                    os.symlink(src.resolve(), dst)
                except OSError:
                    dst.write_bytes(src.read_bytes())
    args = [ctx.executable("cryodrgn"), "analyze", target, str(epoch), "--ksample", str(params["ksample"]),
            "--pc", str(params.get("pcs", 2))]
    n_per_pc = int(params.get("n_per_pc", 10))
    if n_per_pc != 10:
        args += ["--n-per-pc", str(n_per_pc)]
    if apix:
        args += ["--Apix", f"{apix:.4f}".rstrip("0").rstrip(".")]
    if params.get("invert"):
        args.append("--invert")
    if int(params.get("downsample") or 0):
        ctx.warn("Volumes of cryoDRGN-AI models are generated at the training size (cryoDRGN does not downsample them)")
    ctx.run(args, tool="cryodrgn")
    analyze_dir = target / f"analyze.{epoch}"
    files = sorted(analyze_dir.glob("kmeans*/vol_*.mrc")) + sorted(analyze_dir.glob("pc*/vol_*.mrc"))
    frame = volume_frame(lat_meta, bool(params.get("flip")))
    if files and (frame or params.get("lowpass") or apix):
        ctx.progress(0.9, f"Volumes in the {'consensus frame' if frame else 'frame of the model'} ({len(files)})", log=False)
        finish_volumes(files, frame, float(params.get("lowpass") or 0), apix)
    return analyze_dir


def abinit_pose_covariates(ctx: JobContext, lineage: dict[str, Any], workdir: Path, epoch: int, n: int,
                           ind: np.ndarray | None, extent: float) -> tuple[dict[str, dict], dict[str, np.ndarray]]:
    """Explorer colourings from the learned poses (viewing direction, shift) and, with consensus poses, how far each
    particle's learned pose is from its consensus pose."""
    from cryoplug import particles as pt
    from cryoplug.jobs.heterogeneity import coloring
    prepared = lineage.get("prepared") or {}
    box = int(lineage.get("box") or prepared.get("box") or 0)
    colorings: dict[str, dict] = {}
    aux: dict[str, np.ndarray] = {}
    rot = trans = None
    try:
        if box and (Path(workdir) / f"pose.{epoch}.pkl").exists():
            rot, trans = learned_poses(workdir, epoch, box)
            if lineage.get("frame"):
                rot, trans = ps.apply_frame(rot, trans, lineage["frame"])
        elif prepared.get("poses"):
            obj = pt.load_pkl(ctx.abs(prepared["poses"]))
            rot, trans = (np.asarray(obj[0]), np.asarray(obj[1])) if isinstance(obj, (tuple, list)) else (np.asarray(obj), None)
            if ind is not None and len(rot) != n:
                rot, trans = rot[ind], (trans[ind] if trans is not None else None)
    except Exception as exc:  # noqa: BLE001 - informative only
        ctx.warn(f"Learned poses not read ({exc}): no pose colouring")
        return colorings, aux
    if rot is None or len(rot) != n:
        return colorings, aux
    direction = np.asarray(rot[:, 2, :], dtype=np.float64)
    direction /= np.maximum(np.linalg.norm(direction, axis=1, keepdims=True), 1e-9)
    aux["direction"] = direction
    colorings["tilt"] = coloring(np.degrees(np.arccos(np.clip(direction[:, 2], -1, 1))), "Viewing tilt", "°", lo=0, hi=180,
                                 note="From the learned poses: angle between the viewing direction and the z axis of the map.")
    colorings["azimuth"] = coloring(np.degrees(np.arctan2(direction[:, 1], direction[:, 0])), "Viewing azimuth", "°",
                                    cyclic=True, lo=-180, hi=180, note="From the learned poses: direction of view around z.")
    if trans is not None:
        shift = np.linalg.norm(np.asarray(trans, dtype=np.float64), axis=1) * (extent or box)
        if np.ptp(shift) > 0:
            colorings["shift"] = coloring(shift, "In-plane shift", "Å" if extent else "px",
                                          note="Learned shift: large values can mean off-centre or mis-picked particles.")
            aux["shift"] = shift
    cons = consensus_poses(ctx, prepared.get("consensus_poses"), ind, n)
    if cons is not None:
        symmetry = (lineage.get("pose_comparison") or {}).get("symmetry", "C1")
        cmp = ps.compare_poses(rot, trans, cons[0], cons[1], symmetry=symmetry)
        colorings["pose_diff"] = coloring(cmp["angle"], "Pose difference from the consensus", "°", lo=0, hi=90,
                                          note="Angle between the learned and the consensus pose of each particle. "
                                               "Clusters of large values: junk or another species.")
        colorings["view_diff"] = coloring(cmp["view_angle"], "Viewing direction difference", "°", lo=0, hi=90,
                                          note="Angle between the learned and the consensus viewing directions.")
        if cmp["shift_error"] is not None and extent:
            colorings["shift_diff"] = coloring(cmp["shift_error"] * extent, "Shift difference from the consensus", "Å",
                                               note="Distance between the learned and the consensus particle centres.")
    return colorings, aux
