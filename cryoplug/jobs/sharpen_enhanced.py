"""Sharpening enhanced: the map sharpened several ways, each result judged by the same model.

A map is sharpened for the eye, and the eye is a poor judge: a B-factor too strong gives crisp noise, too weak a
blurred map, and the neural networks give maps that look wonderful without being more true. With a model fitted in
the map, each sharpened map can be judged the same way: the Q-score of the atoms (Pintilie et al. 2020, Nat. Methods
17:328), which is highest when the density around each atom has the shape expected at the resolution, falls for an
over-sharpened map (noise) as for a blurred one (density spread out), the correlation of the map with the model inside
its envelope, and the resolution at which the map-model FSC falls to 0.5. This job makes the maps (a B-factor estimated
from the Guinier plot of the map, the B-factors asked for, and the methods installed: LocScale, EMmerNet, DeepEMhancer,
EMReady2, Phenix map sharpening and density modification), scores them all, keeps the best by Q-score and shows the
comparison: sections of each map side by side, the Guinier plots, the scores, the decision map. Without a model the
maps are made and shown, and the one at the Guinier B-factor is kept.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

import numpy as np

from cryoplug.jobs import register
from cryoplug.jobs.base import JobContext, JobError, JobType, OutputDef, Param, Slot, resolution_param
from cryoplug.mrc import MapVolume, _frequency_shells, fourier_filter

B_MIN, B_MAX = -300.0, 0.0  # the automatic sharpening B-factor is kept within this range
GUINIER_FROM = 10.0  # Å: the Guinier fit runs from here to the resolution
Q_SUBSAMPLE = 15000  # atoms scored at most with 'Quick Q-score'
# method key: (label, tool key, needs the half maps, the output slot)
METHODS: dict[str, tuple[str, str, bool, str]] = {
    "locscale_model_free": ("LocScale model-free", "locscale", False, "map_locscale"),
    "emmernet": ("EMmerNet", "locscale", False, "map_emmernet"),
    "deepemhancer": ("DeepEMhancer", "deepemhancer", False, "map_deepemhancer"),
    "emready": ("EMReady2", "emready", False, "map_emready"),
    "phenix_map_sharpening": ("Phenix map sharpening", "phenix", False, "map_sharpening"),
    "phenix_density_modification": ("Phenix density modification", "phenix", True, "map_denmod"),
}


# ------------------------------------------------------------------ helpers
def fmt_res(value: float | None) -> str:
    return f"{value:.2f}" if value is not None else "—"


def parse_bfactors(text: str) -> list[str | float]:
    """'auto, -40, -80' → ["auto", -40.0, -80.0] (each once, in that order); ValueError otherwise."""
    out: list[str | float] = []
    for item in re.split(r"[,;\s]+", str(text or "").strip()):
        if not item:
            continue
        if item.lower() == "auto":
            value: str | float = "auto"
        else:
            try:
                value = float(item)
            except ValueError:
                raise ValueError(f"B-factors: '{item}' is not a number nor 'auto'") from None
        if value not in out:
            out.append(value)
    if not out:
        raise ValueError("B-factors: give at least one value (e.g. auto, -40, -80)")
    return out


def guinier(vol: MapVolume, resolution: float, from_resolution: float = GUINIER_FROM) -> dict[str, Any]:
    """The Guinier plot of a map (ln of the mean amplitude of each shell against 1/d²) and the B-factor of its fall-off
    between ``from_resolution`` and ``resolution`` Å: ln A = c − B/4 · (1/d²), so B = −4 × slope. A positive B is a
    fall-off (the map can be sharpened by −B); a negative one, a map already sharpened beyond that."""
    f = np.fft.rfftn(vol.data.astype(np.float32))
    shells, freqs, nshell = _frequency_shells(vol.data.shape, vol.voxel)
    sel = shells < nshell
    power = np.bincount(shells[sel], weights=np.abs(f[sel]) ** 2, minlength=nshell)
    count = np.bincount(shells[sel], minlength=nshell)
    with np.errstate(divide="ignore", invalid="ignore"):
        amp = np.sqrt(power / np.maximum(count, 1))
        ln_amp = np.where(amp > 0, np.log(np.maximum(amp, 1e-30)), np.nan)
    s2 = freqs ** 2
    lo, hi = 1.0 / from_resolution, 1.0 / max(resolution, 2.0 * float(np.max(vol.voxel)))
    fit = (freqs >= lo) & (freqs <= hi) & np.isfinite(ln_amp) & (count > 0)
    if fit.sum() < 4:  # a coarse map or a box too small: every shell beyond 10 Å
        fit = (freqs >= lo) & np.isfinite(ln_amp) & (count > 0)
    bfactor = None
    if fit.sum() >= 3:
        slope, _intercept = np.polyfit(s2[fit], ln_amp[fit], 1)
        bfactor = float(-4.0 * slope)
    keep = np.isfinite(ln_amp) & (count > 0) & (freqs > 0)
    return {"s2": [round(float(v), 6) for v in s2[keep]], "ln_amplitude": [round(float(v), 4) for v in ln_amp[keep]],
            "bfactor": bfactor, "fit_from": from_resolution, "fit_to": 1.0 / hi}


def auto_bfactor(estimate: float | None) -> float:
    """The sharpening B-factor applied for 'auto': minus the Guinier fall-off, within [B_MIN, B_MAX]."""
    if estimate is None:
        return -100.0
    return float(min(B_MAX, max(B_MIN, -estimate)))


def choose_map(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The scored map with the best Q-score (then the better map-model FSC, then the correlation); None when no map
    is scored."""
    scored = [c for c in candidates if c.get("score")]
    if not scored:
        return None
    return max(scored, key=lambda c: (round(c["score"]["q_mean"], 3), -round(c["score"]["fsc05"] or 99.0, 2),
                                      round(c["score"]["cc_mask"], 3)))


def subsample(n: int, limit: int) -> np.ndarray:
    """Indices of at most ``limit`` atoms, evenly spaced."""
    if n <= limit:
        return np.arange(n)
    return np.unique(np.linspace(0, n - 1, limit).astype(int))


# ---------------------------------------------------------------------- job
@register
class SharpenEnhanced(JobType):
    name = "sharpen_enhanced"
    title = "Sharpening enhanced (B-factors and methods compared)"
    category = "Map processing"
    software = ["CryoPlug"]
    cpus = 4
    gpu = 0
    gpu_param = "gpus"
    description = ("The map sharpened several ways — the B-factor estimated from its Guinier plot, the B-factors asked for, "
                   "and the methods installed (LocScale, EMmerNet, DeepEMhancer, EMReady2, Phenix map sharpening and "
                   "density modification) — every result scored the same way with the model (Q-score of the atoms, "
                   "map-model correlation and FSC) and the best kept, with the sections of each map side by side, "
                   "the Guinier plots and the decision map. Without a model, the map at the Guinier B-factor is kept.")
    inputs = [
        Slot("map", ("map",), "Map", help="The map to sharpen (unsharpened, or the one to re-sharpen): the B-factors apply "
                                           "to it, and it is the reference of the Guinier plot."),
        Slot("half_maps", ("half_maps",), "Half maps", required=False,
             help="Unfiltered half maps: LocScale, EMmerNet, DeepEMhancer, Phenix map sharpening and density modification "
                  "read them when they are given."),
        Slot("model", ("model",), "Model", required=False,
             help="A model fitted in the map: it scores every map (Q-score, CC, map-model FSC). Without it the maps are "
                  "shown and the Guinier B-factor one is kept."),
        Slot("mask", ("mask",), "Mask", required=False, help="For LocScale, EMmerNet and EMReady2, and the sections."),
        Slot("sequence", ("sequence",), "Sequence", required=False, help="For the density modification (solvent content)."),
    ]
    params = [
        resolution_param(),
        Param("bfactors", "str", "auto, -40, -80, -120", label="B-factors", unit="Å²",
              help="Sharpening B-factors to try (negative sharpens, positive blurs); 'auto' is minus the fall-off fitted "
                   f"on the Guinier plot between {GUINIER_FROM:g} Å and the resolution (RELION's auto-B)."),
        Param("lowpass", "bool", True, label="Low-pass at the resolution",
              help="The B-factor maps are low-pass filtered at the resolution (as RELION post-processing does), so that "
                   "the noise beyond it is not sharpened."),
        Param("methods", "multichoice", [], choices=list(METHODS), labels={k: v[0] for k, v in METHODS.items()},
              label="Also try", help="Other sharpening and enhancement methods, each giving one more map to compare; "
                                     "a method whose program is not installed is skipped with a warning."),
        Param("quick_q", "bool", True, label="Quick Q-score",
              help=f"Q-scores on at most {Q_SUBSAMPLE:,} atoms evenly spaced along the model (the mean is the same to "
                   "the third decimal on large models); off: every heavy atom."),
        Param("keep_maps", "bool", True, label="Keep every map", advanced=True,
              help="All the maps as outputs (files), not only the best one and the method maps."),
        Param("gpus", "int", 0, label="GPUs", min=0, max=8, advanced=True,
              help="GPUs for EMmerNet, LocScale model-free, DeepEMhancer and EMReady2 (0: none asked from the lane)."),
    ]
    outputs = [
        OutputDef("map", "map", "Best map"),
        OutputDef("map_auto", "map", "Map at the Guinier B-factor"),
        OutputDef("map_locscale", "map", "LocScale model-free map"),
        OutputDef("map_emmernet", "map", "EMmerNet map"),
        OutputDef("map_deepemhancer", "map", "DeepEMhancer map"),
        OutputDef("map_emready", "map", "EMReady2 map"),
        OutputDef("map_sharpening", "map", "Phenix map sharpening"),
        OutputDef("map_denmod", "map", "Phenix density-modified map"),
        OutputDef("maps", "files", "All the maps compared"),
        OutputDef("report", "report", "Comparison (JSON)"),
    ]

    @classmethod
    def validate(cls, params: dict[str, Any], connected: dict[str, str]) -> list[str]:
        problems = []
        try:
            parse_bfactors(params.get("bfactors", ""))
        except ValueError as exc:
            problems.append(str(exc))
        if "phenix_density_modification" in (params.get("methods") or []) and "half_maps" not in connected:
            problems.append("Phenix density modification needs the half maps")
        return problems

    # ------------------------------------------------------------------ run
    def run(self, ctx: JobContext) -> None:
        from cryoplug.jobs.decisions import DecisionMap

        self.ctx = ctx
        p = ctx.params
        m = ctx.require("map")
        self.resolution = res = ctx.resolution(slots=["map", "half_maps", "model"])
        self.ref = MapVolume.read(m.path)
        self.mask = ctx.input("mask")
        self.model = self._load_model()
        self.envelopes: dict[tuple, np.ndarray] = {}
        self.candidates: list[dict[str, Any]] = []
        bfactors = parse_bfactors(p["bfactors"])
        methods = [k for k in (p.get("methods") or []) if k in METHODS]
        live = ctx.path("live")
        live.mkdir(exist_ok=True)
        ctx.path("maps").mkdir(exist_ok=True)

        self.flow = flow = DecisionMap(ctx, note="The maps compared: the B-factor maps in one box, the other methods in "
                                                "another, each scored by the model; the one kept. Click a tile for its "
                                                "scores and its 3D view.")
        given = [n for n, there in (("half maps", ctx.input("half_maps")), ("model", self.model), ("mask", self.mask)) if there]
        flow.add("input", "input", "Map", icon="map", lines=[Path(m.path).name, f"{res:g} Å" + (f" · {', '.join(given)}" if given else "")],
                 view=[{"kind": "map", "path": m.path, "label": "map as given"}])
        if not self.model:
            ctx.warn("No model connected: the maps are made and shown, but cannot be judged objectively; the map at the "
                     "Guinier B-factor is kept. Connect a model fitted in the map to choose by its Q-score.")

        # The Guinier plot of the map and the automatic B-factor
        ctx.progress(0.03, "Guinier plot of the map")
        g = guinier(self.ref, res)
        self.guinier_curves: list[dict[str, Any]] = []  # one per map, the map as given first
        estimate = g["bfactor"]
        auto = auto_bfactor(estimate)
        if estimate is None:
            ctx.warn("The Guinier fall-off could not be fitted (box too small?): 'auto' uses −100 Å²")
            est_text = "not fitted"
        else:
            est_text = f"B = {estimate:+.0f} Å² between {g['fit_from']:g} and {g['fit_to']:.1f} Å"
            ctx.log(f"Guinier plot: fall-off {est_text}; automatic sharpening B-factor {auto:+.0f} Å²"
                    + (f" (clamped to [{B_MIN:g}, {B_MAX:g}])" if auto != -estimate else ""))
            if estimate <= 0:
                ctx.warn(f"The map is already sharpened (Guinier fall-off {estimate:+.0f} Å²): 'auto' applies no B-factor")
        ctx.add_highlight("B estimate", est_text if estimate is None else f"{estimate:+.0f} Å²")
        flow.add("estimate", "decision", "Guinier B-factor", parents=["input"], icon="sliders",
                 lines=[est_text, f"auto sharpening {auto:+.0f} Å²"],
                 detail=f"ln amplitude against 1/d² fitted between {g['fit_from']:g} and {g['fit_to']:.1f} Å: B = −4 × slope. "
                        f"The automatic B-factor is −B within [{B_MIN:g}, {B_MAX:g}] Å².")

        # The map as given, then the B-factor maps
        self.planned = 1 + len(bfactors) + len(methods)
        self._score_candidate({"key": "input", "label": "as given", "kind": "input", "path": m.path, "bfactor": None,
                               "note": "the map as given", "node": "map:input"}, parents=["input"])
        flow.add("group:b", "group", f"B-factor maps · {len(bfactors)}", parents=["estimate"], icon="grid")
        for item in bfactors:
            b = auto if item == "auto" else float(item)
            label = f"B {b:+g} Å²" + (" (auto)" if item == "auto" else "")
            key = "auto" if item == "auto" else f"B{b:+g}"
            cand = {"key": key, "label": label, "kind": "bfactor", "bfactor": b, "node": f"map:{key}",
                    "note": f"the map {'sharpened' if b < 0 else 'blurred' if b > 0 else 'unchanged'} by B = {b:+g} Å²"
                            + (", low-pass filtered at the resolution" if p["lowpass"] else "")}
            flow.add(cand["node"], "run", label, group="group:b", status="running", lines=["sharpening…"], icon="map")
            ctx.progress(0.05 + 0.6 * len(self.candidates) / max(self.planned, 1), f"Sharpening by B = {b:+g} Å²")
            data = fourier_filter(self.ref, bfactor=b, lowpass=res if p["lowpass"] else 0.0)
            cand["path"] = str(self.ref.like(data).write(ctx.path("maps", f"map_{key}.mrc")))
            self._score_candidate(cand)

        # The other methods, each when its program is there
        if methods:
            flow.add("group:methods", "group", f"Other methods · {len(methods)}", parents=["input"], icon="grid")
        for method in methods:
            label, tool, needs_halves, _slot = METHODS[method]
            cand = {"key": method, "label": label, "kind": "method", "bfactor": None, "node": f"map:{method}",
                    "note": f"{label} map"}
            flow.add(cand["node"], "run", label, group="group:methods", status="running", lines=["running…"], icon="map")
            ctx.progress(0.05 + 0.6 * len(self.candidates) / max(self.planned, 1), f"{label}")
            if not self._tool_available(tool):
                ctx.warn(f"{label} skipped: its program is not found on this server (Tools page)")
                flow.update(cand["node"], status="skipped", lines=["program not found", "skipped"])
                continue
            if needs_halves and not ctx.input("half_maps"):
                ctx.warn(f"{label} skipped: it needs the half maps")
                flow.update(cand["node"], status="skipped", lines=["needs the half maps", "skipped"])
                continue
            folder = ctx.path("maps", method)
            folder.mkdir(exist_ok=True)
            try:
                cand["path"] = str(self._run_method(method, folder))
            except JobError as exc:
                ctx.warn(f"{label} left out: {exc}")
                flow.update(cand["node"], status="failed", lines=["failed", str(exc).splitlines()[0][:80]])
                continue
            self._score_candidate(cand)

        self._finish(auto)

    # --------------------------------------------------------------- the model
    def _load_model(self) -> dict[str, Any] | None:
        inp = self.ctx.input("model")
        if not inp:
            return None
        from cryoplug.modelio import atom_arrays, read_structure
        st = read_structure(inp.path)
        atoms = atom_arrays(st)
        if not len(atoms["xyz"]):
            raise JobError("The model has no atoms")
        quick = bool(self.ctx.params.get("quick_q", True))
        sel = subsample(len(atoms["xyz"]), Q_SUBSAMPLE) if quick else np.arange(len(atoms["xyz"]))
        self.ctx.log(f"Model {Path(inp.path).name}: {len(atoms['xyz'])} heavy atoms"
                     + (f", Q-score on {len(sel)} of them (Quick Q-score)" if len(sel) < len(atoms["xyz"]) else ""))
        return {"path": inp.path, "structure": st, "atoms": atoms, "q_atoms": sel}

    def _envelope(self, vol: MapVolume) -> np.ndarray:
        from cryoplug.analysis import model_envelope
        key = (vol.data.shape, tuple(np.round(vol.voxel, 4)), tuple(np.round(vol.origin, 3)))
        if key not in self.envelopes:
            self.envelopes[key] = model_envelope(vol, self.model["atoms"]["xyz"])  # type: ignore[index]
        return self.envelopes[key]

    def _score(self, vol: MapVolume) -> dict[str, Any]:
        """Q-score, CC in the model's envelope and the map-model FSC of a map against the model."""
        from cryoplug.analysis import cc_mask, expected_q, map_model_fsc, q_scores
        model = self.model
        assert model is not None
        atoms = model["atoms"]
        q = q_scores(vol, atoms["xyz"][model["q_atoms"]])
        q_mean = float(np.nanmean(q)) if np.any(~np.isnan(q)) else float("nan")
        env = self._envelope(vol)
        fsc = map_model_fsc(vol, atoms["xyz"], atoms["z"], env)
        return {"q_mean": q_mean, "q_expected": expected_q(self.resolution), "cc_mask": cc_mask(vol, model["structure"], self.resolution),
                "fsc05": fsc["resolution_0.5"], "fsc0143": fsc["resolution_0.143"],
                "fsc_curve": {"x": fsc["frequency"], "y": fsc["fsc"]}}

    # ------------------------------------------------------------- the maps
    def _tool_available(self, key: str) -> bool:
        exe = self.ctx.executable(key)
        return bool(os.path.isabs(exe) or shutil.which(exe))

    def _sources(self):
        from cryoplug.jobs.sharpening import LOCSCALE_SYMMETRY, MapSources
        ctx = self.ctx
        hm, seq = ctx.input("half_maps"), ctx.input("sequence")
        sym = str(ctx.inherited_meta("half_maps", "map").get("symmetry") or "C1").strip().upper()
        if not LOCSCALE_SYMMETRY.fullmatch(sym):
            sym = "C1"
        return MapSources(half_maps=list(hm.files[:2]) if hm else None, full_map=ctx.require("map").path,
                          mask=self.mask.path if self.mask else None, sequence=seq.files[0] if seq else None,
                          model=self.model["path"] if self.model else None, resolution=self.resolution, symmetry=sym)

    def _run_method(self, method: str, folder: Path) -> Path:
        from cryoplug.jobs import sharpening as sh
        ctx = self.ctx
        src = self._sources()
        gpus = ctx.physical_gpus()
        if method == "locscale_model_free":
            return sh.run_locscale(ctx, src, "model_free", folder, nproc=4, gpus=gpus)
        if method == "emmernet":
            return sh.run_emmernet(ctx, src, folder, gpus=gpus)["map"]
        if method == "deepemhancer":
            return sh.run_deepemhancer(ctx, src, folder, gpus=gpus)
        if method == "emready":
            return sh.run_emready(ctx, ctx.require("map").path, folder, mask=src.mask, gpus=gpus)
        if method == "phenix_map_sharpening":
            return sh.run_map_sharpening(ctx, src, folder, method="shell" if src.half_maps else "b-factor")
        if method == "phenix_density_modification":
            return sh.run_resolve_cryo_em(ctx, src, folder, nproc=4)
        raise JobError(f"Unknown method {method}")

    def _score_candidate(self, cand: dict[str, Any], parents: list[str] | None = None) -> None:
        """Score one map (when there is a model), draw its sections and its Guinier plot, update the live sections."""
        from cryoplug.imaging import write_png
        from cryoplug.jobs.iterations import sections_picture
        ctx, flow = self.ctx, self.flow
        node = cand["node"]
        if parents is not None:
            flow.add(node, "run", cand["label"], parents=parents, status="running", lines=["scoring…"], icon="map")
        vol = MapVolume.read(cand["path"])
        if self.model:
            ctx.progress(0.05 + 0.6 * (len(self.candidates) + 0.5) / max(self.planned, 1), f"Scoring {cand['label']} with the model",
                         log=False)
            try:
                cand["score"] = self._score(vol)
            except Exception as exc:  # a map in another box or an odd file: shown, not scored
                ctx.warn(f"{cand['label']} not scored: {exc}")
                cand["score"] = None
        else:
            cand["score"] = None
        g = guinier(vol, self.resolution)
        cand["guinier_b"] = g["bfactor"]
        self.guinier_curves.append({"name": cand["label"], "x": g["s2"], "y": g["ln_amplitude"]})
        mask = None
        if self.mask:
            mvol = MapVolume.read(self.mask.path, header_only=True)
            if tuple(mvol.shape_xyz) == tuple(vol.shape_xyz):
                mask = Path(self.mask.path)
        n = len(self.candidates) + 1
        try:
            cand["png"] = str(write_png(ctx.path("live", f"map_{n:02d}.png"), sections_picture([Path(cand["path"])], mask, size=200)))
        except Exception as exc:  # the picture never stops the job
            ctx.log(f"(sections of {cand['label']} not drawn: {exc})")
            cand["png"] = None
        self.candidates.append(cand)
        s = cand["score"]
        lines = ([f"Q {s['q_mean']:.3f} · CC {s['cc_mask']:.2f}", f"map-model FSC 0.5 at {fmt_res(s['fsc05'])} Å"] if s
                 else [f"Guinier fall-off {g['bfactor']:+.0f} Å²" if g["bfactor"] is not None else "made", "not scored (no model)"])
        flow.update(node, status="done", lines=lines, detail=self._summary(cand),
                    view=[{"kind": "map", "path": cand["path"], "label": cand["label"]}]
                         + ([{"kind": "model", "path": self.model["path"], "label": "model"}] if self.model else []))
        ctx.log(f"{cand['label']}: {self._summary(cand)}")
        self._live(cand)

    # ------------------------------------------------------------ reporting
    @staticmethod
    def _summary(c: dict[str, Any]) -> str:
        s = c.get("score")
        made = c["note"]
        if not s:
            return made + (f"; Guinier fall-off {c['guinier_b']:+.0f} Å²" if c.get("guinier_b") is not None else "")
        return (f"{made}; Q-score {s['q_mean']:.3f} (expected {s['q_expected']:.2f}), CC (mask) {s['cc_mask']:.3f}, "
                f"map-model FSC 0.5 at {fmt_res(s['fsc05'])} Å, 0.143 at {fmt_res(s['fsc0143'])} Å")

    def _rows(self, best: dict[str, Any] | None) -> list[list[Any]]:
        rows = []
        for c in self.candidates:
            s = c.get("score")
            rows.append([c["label"], "" if c["bfactor"] is None else f"{c['bfactor']:+g}",
                         "" if c.get("guinier_b") is None else f"{c['guinier_b']:+.0f}",
                         f"{s['q_mean']:.3f}" if s else "—", f"{s['cc_mask']:.3f}" if s else "—",
                         fmt_res(s["fsc05"]) if s else "—", "✓ best" if c is best else ""])
        return rows

    def _live(self, cand: dict[str, Any], frame: bool = True) -> None:
        """The maps so far: the table, the sections of each (slider), the Guinier plots, the scores by map."""
        ctx = self.ctx
        best = choose_map(self.candidates)
        ctx.add_table("Maps compared", ["Map", "B applied (Å²)", "Guinier fall-off (Å²)", "Q-score", "CC (mask)", "FSC 0.5 (Å)", "Best"],
                      self._rows(best), key="maps",
                      note=(f"Q-score: mean over the atoms of the model (expected ≈ {best['score']['q_expected']:.2f} at "
                            f"{self.resolution:g} Å for a well-resolved map); it falls when a map is over-sharpened (noise) as "
                            "when it is blurred. CC: correlation of the map with the model's map inside its envelope. FSC 0.5: "
                            "resolution at which the map-model FSC falls to 0.5. Guinier fall-off: the B-factor fitted on "
                            "each map (near 0 for a sharpened map)." if best and best.get("score")
                            else "No model: the maps are not scored. Guinier fall-off: the B-factor fitted on each map "
                                 "between 10 Å and the resolution (near 0 for a sharpened map)."))
        if frame and cand.get("png"):
            s = cand.get("score")
            caption = f"{cand['label']}: " + (f"Q {s['q_mean']:.3f}, CC {s['cc_mask']:.3f}, FSC 0.5 at {fmt_res(s['fsc05'])} Å" if s else cand["note"])
            ctx.add_image_frame("Sections of each map", cand["png"], cand["label"], key="map_sections", group="maps", caption=caption,
                                view=[{"kind": "map", "path": cand["path"], "label": cand["label"]}]
                                     + ([{"kind": "model", "path": self.model["path"], "label": "model"}] if self.model else []),
                                note="Central sections (XY, XZ, YZ) of each map, the mask outlined in orange: the slider "
                                     "compares them at the same contrast.")
        ctx.add_plot("Guinier plots", self.guinier_curves[:12], x_label="1/d² (Å⁻²)", y_label="ln amplitude", key="guinier",
                     note="Mean Fourier amplitude of each map by shell: the slope of a map's fall-off is −B/4. A flat "
                          "curve up to the resolution is a sharpened map; rising beyond it, sharpened noise.")
        scored = [c for c in self.candidates if c.get("score")]
        if len(scored) > 1:
            x = list(range(1, len(scored) + 1))
            ctx.add_plot("Scores by map", [{"name": "Q-score", "x": x, "y": [round(c["score"]["q_mean"], 4) for c in scored]},
                                           {"name": "CC (mask)", "x": x, "y": [round(c["score"]["cc_mask"], 4) for c in scored]}],
                         x_label="Map", y_label="Q-score / CC", key="scores_plot",
                         hlines=[{"y": round(scored[0]["score"]["q_expected"], 3), "label": "expected Q"}],
                         note="Maps in the order made: " + ", ".join(f"{i + 1} = {c['label']}" for i, c in enumerate(scored)) + ".")
            ctx.add_plot("Map-model FSC of each map", [{"name": c["label"], **c["score"]["fsc_curve"]} for c in scored[:12]],
                         x_label="Resolution (Å)", y_label="FSC", x_kind="resolution", key="model_fsc",
                         hlines=[{"y": 0.5, "label": "0.5"}, {"y": 0.143, "label": "0.143"}], y_range=[-0.1, 1.05])
        if best and best.get("score"):
            s = best["score"]
            ctx.add_highlight("Best map", best["label"])
            ctx.add_highlight("Q-score", f"{s['q_mean']:.3f}",
                              "good" if s["q_mean"] >= s["q_expected"] - 0.05 else "warn" if s["q_mean"] >= s["q_expected"] - 0.15 else "bad")
            ctx.add_highlight("CC (mask)", f"{s['cc_mask']:.3f}", "good" if s["cc_mask"] >= 0.7 else "warn" if s["cc_mask"] >= 0.5 else "bad")
        ctx.add_highlight("Maps", str(len(self.candidates)))

    def _finish(self, auto: float) -> None:
        ctx, flow = self.ctx, self.flow
        p = ctx.params
        made = [c for c in self.candidates if c["kind"] != "input"]
        if not made:
            raise JobError("No map was made (see the warnings)")
        best = choose_map(self.candidates)
        by_model = best is not None
        if best is None:  # no model: the Guinier B-factor map, else the first B-factor map, else the first made
            best = next((c for c in self.candidates if c["key"] == "auto"), None) or next(
                (c for c in self.candidates if c["kind"] == "bfactor"), made[0])
            why = "no model to score the maps: the map at the Guinier B-factor is kept"
        else:
            scored = [c for c in self.candidates if c.get("score")]
            why = f"best Q-score of the {len(scored)} maps scored"
        ctx.log(f"Map kept: {best['label']} ({why})")
        flow.mark(best["node"], [c["node"] for c in self.candidates])
        s = best.get("score")
        flow.add("best", "decision", f"{best['label']} kept", parents=[best["node"]], status="chosen" if by_model else "warning",
                 icon="target", lines=[f"Q {s['q_mean']:.3f} (expected {s['q_expected']:.2f}) · CC {s['cc_mask']:.2f}" if s else "not scored", why],
                 detail=self._summary(best))

        # Outputs: the best map, the auto map, each method's map, all the maps
        out = ctx.path("best_map.mrc")
        shutil.copyfile(best["path"], out)
        if best["kind"] == "input":
            ctx.warn("The map as given scores better than every sharpened map: it is kept unchanged")
        meta = {**ctx.inherited_meta("map"), "sharpening": best["label"], "chosen_by": "q_score" if by_model else "guinier"}
        if best["bfactor"] is not None:
            meta["bfactor"] = best["bfactor"]
        if s:
            meta.update(q_score=round(s["q_mean"], 4), cc_mask=round(s["cc_mask"], 4),
                        **({"map_model_fsc_05": round(s["fsc05"], 3)} if s["fsc05"] is not None else {}))
        ctx.add_output("map", "map", out, f"Best map ({best['label']})", meta=meta, thumbnail=best.get("png"))
        for c in self.candidates:
            slot = "map_auto" if c["key"] == "auto" else METHODS.get(c["key"], ("", "", False, ""))[3]
            if slot and c is not best:
                ctx.add_output(slot, "map", c["path"], c["label"], inherit=["map"],
                               meta={"sharpening": c["label"], **({"bfactor": c["bfactor"]} if c["bfactor"] is not None else {}),
                                     **({"q_score": round(c["score"]["q_mean"], 4)} if c.get("score") else {})}, thumbnail=c.get("png"))
        if p["keep_maps"]:
            ctx.add_output("maps", "files", [c["path"] for c in made], "All the maps compared")
        report = {"resolution": self.resolution, "auto_bfactor": auto, "chosen": best["key"], "chosen_by": meta["chosen_by"],
                  "maps": [{k: v for k, v in c.items() if k not in ("png", "node")} for c in self.candidates]}
        for entry in report["maps"]:
            if entry.get("score"):
                entry["score"] = {k: v for k, v in entry["score"].items() if k != "fsc_curve"}
        path = ctx.path("comparison.json")
        path.write_text(json.dumps(report, indent=1, default=float))
        ctx.add_output("report", "report", path, "Comparison of the maps (JSON)")

        self._live(best, frame=False)
        ctx.add_highlight("Best map", best["label"])
        flow.add("result", "result", "Map kept", parents=["best"], status="chosen", icon="target",
                 lines=[best["label"], f"Q {s['q_mean']:.3f} · CC {s['cc_mask']:.2f}" if s else "chosen by the Guinier plot"],
                 view=[{"kind": "map", "path": out, "label": best["label"]}]
                      + ([{"kind": "model", "path": self.model["path"], "label": "model"}] if self.model else []))
        lines = [f"Map kept: {best['label']} — {why}. {self._summary(best)}.",
                 f"{len(made)} maps made" + (f", {len([c for c in self.candidates if c.get('score')])} scored with the model."
                                            if self.model else ", none scored (no model).")]
        if by_model:
            lines.append("The Q-score is highest when the density around each atom has the shape expected at the resolution: "
                         "it falls for an over-sharpened map (crisp noise) as for a blurred one. Use the map kept to build and "
                         "to show; deposit the unsharpened map and the half maps with it.")
        else:
            lines.append("Connect a model fitted in the map (even partial) to choose the sharpening objectively.")
        ctx.add_text("Summary", "\n".join(lines))
        ctx.log(f"Done: {len(made)} maps compared, {best['label']} kept")
