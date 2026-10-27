"""Live dashboard of iterative relion_refine runs (3D auto-refine, local refinement, 3D classification, initial
model, heterogeneous refinement), as CryoSPARC's: when an iteration ends, the panel of the job gets its charts while
RELION goes on — resolution and convergence over the iterations, the gold-standard FSC, central slices of the map
with the outline of the mask, the share and resolution of each class, the classes in projection. Each iteration is
kept as a frame of a slider shared by the charts, and its maps open in the 3D viewer.

Iteration N is drawn when RELION starts iteration N + 1 (its files are then complete), the last ones when the run
ends (:meth:`IterationDashboard.finish`). Drawing never stops the job: a chart that fails is left out.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np

from cryoplug import relion as rl
from cryoplug.jobs.base import JobContext
from cryoplug.jobs.relion import Progress

GROUP = "iterations"  # the sliders of these charts move together
LIVE_DIR = "live"
SLICE = 160           # pixels of a section, of a class projection
MASK_COLOUR = (255, 140, 0)
FSC_LINES = [{"y": 0.143, "label": "0.143"}, {"y": 0.5, "label": "0.5"}]


# ------------------------------------------------------------------ pictures
def _resize(img: np.ndarray, size: int, order: int = 1) -> np.ndarray:
    from scipy import ndimage
    scale = size / max(img.shape)
    if abs(scale - 1) < 1e-3:
        return img
    return ndimage.zoom(img, scale, order=order)


def central_sections(path: str | Path) -> list[np.ndarray]:
    """The central XY, XZ and YZ sections of a map (read from the file, not the whole map)."""
    import mrcfile
    with mrcfile.mmap(str(path), mode="r", permissive=True) as m:
        d = m.data
        nz, ny, nx = d.shape
        return [np.array(d[nz // 2], dtype=np.float32), np.array(d[:, ny // 2, :], dtype=np.float32),
                np.array(d[:, :, nx // 2], dtype=np.float32)]


def sections_picture(maps: list[Path], mask: Path | None = None, size: int = SLICE, gap: int = 4) -> np.ndarray:
    """The central sections (XY, XZ, YZ, left to right) of the average of ``maps``, grey, with the outline of the mask
    in orange: CryoSPARC's real-space slices. RGB uint8."""
    from scipy import ndimage
    sections = None
    for path in maps:
        cur = central_sections(path)
        sections = cur if sections is None else [a + b for a, b in zip(sections, cur)]
    sections = [s / len(maps) for s in sections]
    lo, hi = np.percentile(np.concatenate([s.ravel() for s in sections]), [1.0, 99.8])
    masks = central_sections(mask) if mask is not None else [None] * 3
    panels = []
    for sec, msec in zip(sections, masks):
        grey = np.clip((_resize(sec, size) - lo) / ((hi - lo) or 1.0), 0, 1)[::-1]  # y up
        rgb = np.repeat((grey * 255).astype(np.uint8)[..., None], 3, axis=2)
        if msec is not None and msec.shape == sec.shape:
            inside = _resize(msec, size, order=0)[::-1] > 0.5
            edge = inside & ~ndimage.binary_erosion(inside)
            rgb[edge] = MASK_COLOUR
        panels.append(rgb)
    h = max(p.shape[0] for p in panels)
    out = np.zeros((h, sum(p.shape[1] for p in panels) + gap * (len(panels) - 1), 3), dtype=np.uint8)
    x = 0
    for p in panels:
        out[:p.shape[0], x:x + p.shape[1]] = p
        x += p.shape[1] + gap
    return out


def projections_picture(maps: list[Path], size: int = 120) -> np.ndarray:
    """Each map projected along z, side by side (classes of a 3D classification)."""
    from cryoplug.micrographs import tile_sheet
    from cryoplug.mrc import MapVolume, projection_image
    tiles = [projection_image(MapVolume.read(p), size) for p in maps]
    side = max(max(t.shape) for t in tiles)
    tiles = [np.pad(t, ((0, side - t.shape[0]), (0, side - t.shape[1]))) for t in tiles]
    return tile_sheet(tiles, per_row=min(6, len(tiles)), gap=3)


# ------------------------------------------------------------------ the dashboard
class IterationDashboard(Progress):
    """Progress of relion_refine and the live dashboard of its iterations. ``kind``: "refine" (an auto-refinement:
    half maps and gold-standard FSC at each iteration) or "classes" (a classification or initial model: one map and
    one share per class)."""

    CHANGES = re.compile(r"Auto-refine: Changes in angles=\s*([\d.]+) degrees; and in offsets=\s*([\d.]+) Angstroms")

    def __init__(self, ctx: JobContext, kind: str = "refine", total: int = 0, root: str = "run",
                 mask: Path | None = None, start: float = 0.05, end: float = 0.9):
        super().__init__(ctx, total=total, start=start, end=end)
        self.kind, self.root, self.mask = kind, root, mask
        self.records: dict[int, dict[str, Any]] = {}
        self.changes: dict[int, tuple[float, float]] = {}
        self.done: set[int] = set()
        self.tries: dict[int, int] = {}
        self.live = ctx.path(LIVE_DIR)

    def __call__(self, line: str) -> None:
        m = self.CHANGES.search(line)
        if m and self.iteration > 1 and float(m.group(1)) < 999:  # 999 before the first iteration
            # printed as iteration N starts: how much the poses moved during iteration N - 1
            self.changes[self.iteration - 1] = (float(m.group(1)), float(m.group(2)))
        before = self.iteration
        super().__call__(line)
        if self.iteration != before:
            self.update()

    # ------------------------------------------------------------- iterations
    def iterations(self) -> list[tuple[int, str]]:
        """(iteration, stem of its files) written so far, those of a continued run included."""
        pattern = re.compile(rf"({re.escape(self.root)}(?:_ct\d+)?_it(\d+))(_half1)?_model\.star")
        found: dict[int, str] = {}
        for p in self.ctx.job_dir.glob(f"{self.root}*_it*_model.star"):
            m = pattern.fullmatch(p.name)
            if m and (self.kind != "refine" or m.group(3)):
                found[int(m.group(2))] = m.group(1)
        return sorted(found.items())

    def update(self, final: bool = False) -> None:
        """Draw the iterations that ended since the last call (all of them once the run is over)."""
        shown = False
        for it, stem in self.iterations():
            if it in self.done or (not final and it >= self.iteration):
                continue
            try:
                self.show(it, stem)
                self.done.add(it)
                shown = True
            except Exception as exc:  # a file still being written: tried again at the next iteration
                self.tries[it] = self.tries.get(it, 0) + 1
                if final or self.tries[it] >= 3:
                    self.done.add(it)
                    self.ctx.log(f"(charts of iteration {it} not drawn: {exc})")
        if shown:
            try:
                self.draw_history()
            except Exception as exc:  # pragma: no cover - cosmetic
                self.ctx.log(f"(history charts not drawn: {exc})")

    def finish(self) -> None:
        """The run ended: the last iterations, and the number of iterations among the key results."""
        self.update(final=True)
        if self.records:
            self.ctx.remove_highlight("Iteration")
            self.ctx.add_highlight("Iterations", str(max(self.records)))

    def show(self, it: int, stem: str) -> None:
        self.live.mkdir(exist_ok=True)
        if self.kind == "refine":
            self.show_refine(it, stem)
        else:
            self.show_classes(it, stem)
        self.ctx.add_highlight("Iteration", str(it))

    # ------------------------------------------------------------- refinement
    def show_refine(self, it: int, stem: str) -> None:
        ctx, label = self.ctx, f"Iteration {it}"
        info = rl.model_fsc(ctx.path(f"{stem}_half1_model.star"))
        rec: dict[str, Any] = {"iteration": it}
        # iteration 0 is the starting reference: its FSC is all zeros and its accuracies 999
        if it > 0 and info.get("fsc") and max(info["fsc"]) > 0.143:
            rec["resolution"] = rl.resolution_at(info["frequency"], info["fsc"])
            ctx.add_plot_frame("FSC at each iteration", [{"name": label, "x": info["frequency"], "y": info["fsc"]}], label,
                               key="it_fsc", x_label="Resolution (Å)", y_label="FSC", x_kind="resolution",
                               hlines=FSC_LINES, y_range=[-0.1, 1.05], group=GROUP,
                               note="Gold-standard FSC between the two half maps of the iteration (no mask correction): "
                                    "the final, masked FSC comes with the post-processing at the end.")
        if not rec.get("resolution") and it > 0 and info.get("current_resolution"):
            rec["resolution"] = info["current_resolution"]
        rec["accuracy"] = (info.get("accuracy_rotations"), info.get("accuracy_translations"))
        self.records[it] = rec
        halves = [p for p in (ctx.path(f"{stem}_half1_class001.mrc"), ctx.path(f"{stem}_half2_class001.mrc")) if p.exists()]
        if halves:
            from cryoplug.imaging import write_png
            png = write_png(self.live / f"sections_it{it:03d}.png", sections_picture(halves, self.mask))
            parts = ["starting reference" if it == 0 else "",
                     f"resolution {rec['resolution']:.2f} Å" if rec.get("resolution") else ""]
            rot, trans = rec["accuracy"]
            if rot is not None and trans is not None and rot < 900:
                parts.append(f"accuracy {rot:.2f}° · {trans:.2f} Å")
            ctx.add_image_frame("Central sections of the map", png, label, key="it_sections", group=GROUP,
                                caption=" · ".join(p for p in parts if p),
                                view=[{"kind": "map", "path": h, "label": f"{ctx.uid} iteration {it} half map {i + 1}"}
                                      for i, h in enumerate(halves)],
                                note="XY, XZ and YZ sections through the centre of the box (the two half maps averaged)"
                                     + (", the mask outlined in orange" if self.mask is not None else "")
                                     + ". Streaks or a blurred direction: preferred orientation; density cut by the mask "
                                       "outline: a mask too tight.")
        if rec.get("resolution"):
            ctx.add_highlight("Resolution", f"{rec['resolution']:.2f} Å")

    # ------------------------------------------------------------- classes
    def show_classes(self, it: int, stem: str) -> None:
        ctx, label = self.ctx, f"Iteration {it}"
        classes = rl.model_classes(ctx.path(f"{stem}_model.star"))
        general = rl.model_fsc(ctx.path(f"{stem}_model.star"))
        rec: dict[str, Any] = {"iteration": it, "shares": [c["distribution"] for c in classes],
                               "class_resolution": [c["resolution"] for c in classes] if it > 0 else [],
                               "resolution": general.get("current_resolution") if it > 0 else None}
        self.records[it] = rec
        maps = [(c["class"], ctx.path(f"{stem}_class{c['class']:03d}.mrc")) for c in classes]
        maps = [(k, p) for k, p in maps if p.exists()]
        if maps:
            from cryoplug.imaging import write_png
            png = write_png(self.live / f"classes_it{it:03d}.png", projections_picture([p for _, p in maps]))
            shares = {c["class"]: c["distribution"] for c in classes}
            caption = ("Starting references · " if it == 0 else "") + (
                       f"Class{'es' if len(maps) > 1 else ''} {', '.join(str(k) for k, _ in maps)} (left to right): "
                       + ", ".join(f"{100 * shares.get(k, 0):.0f} %" for k, _ in maps))
            ctx.add_image_frame("Classes (projections)" if len(maps) > 1 else "Map (projection)", png, label,
                                key="it_classes", group=GROUP, caption=caption,
                                view=[{"kind": "map", "path": p, "label": f"{ctx.uid} iteration {it} class {k}"} for k, p in maps],
                                note="Each class map projected along z. View 3D opens the maps of the iteration shown.")
        res = [r for r in rec["class_resolution"] if r and r < 900]
        if res:
            ctx.add_highlight("Resolution", f"{min(res):.2f} Å")

    # ------------------------------------------------------------- over the iterations
    def draw_history(self) -> None:
        ctx = self.ctx
        its = sorted(self.records)
        res = [(i, self.records[i].get("resolution")) for i in its if self.records[i].get("resolution")]
        if res:
            ctx.add_plot("Resolution during the refinement" if self.kind == "refine" else "Resolution over the iterations",
                         [{"name": "Resolution", "x": [i for i, _ in res], "y": [round(r, 3) for _, r in res]}],
                         x_label="Iteration", y_label="Resolution (Å)", key="resolution_history")
        if self.kind == "refine" and self.changes:
            xs = sorted(self.changes)
            ctx.add_plot("Changes between iterations",
                         [{"name": "Orientations (°)", "x": xs, "y": [self.changes[i][0] for i in xs]},
                          {"name": "Offsets (Å)", "x": xs, "y": [self.changes[i][1] for i in xs]}],
                         x_label="Iteration", y_label="Change", key="it_changes",
                         note="How much the poses moved during each iteration. The refinement converges once they and the "
                              "resolution stop improving.")
        if self.kind == "classes":
            shares = [i for i in its if self.records[i].get("shares")]
            k = max((len(self.records[i]["shares"]) for i in shares), default=0)
            if k > 1:
                def column(name: str, c: int, scale: float = 1.0, cap: float = 1e9) -> list[float | None]:
                    out = []
                    for i in shares:
                        vals = self.records[i].get(name) or []
                        v = vals[c] if c < len(vals) else None
                        out.append(round(v * scale, 2) if v is not None and v < cap else None)
                    return out
                ctx.add_plot("Share of each class over the iterations",
                             [{"name": f"Class {c + 1}", "x": shares, "y": column("shares", c, 100.0)} for c in range(k)],
                             x_label="Iteration", y_label="Particles (%)", key="it_shares",
                             note="Classes still exchanging particles at the end have not converged.")
                if any(self.records[i].get("class_resolution") for i in shares):
                    ctx.add_plot("Estimated resolution of each class",
                                 [{"name": f"Class {c + 1}", "x": shares, "y": column("class_resolution", c, cap=900)}
                                  for c in range(k)], x_label="Iteration", y_label="Resolution (Å)", key="it_class_resolution")
