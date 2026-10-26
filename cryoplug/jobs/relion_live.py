"""Live preprocessing with RELION, as CryoSPARC Live: while the microscope collects, the new movies of a folder are
imported, motion-corrected, CTF-estimated, sorted on thresholds and optionally picked and extracted, cycle after cycle.

Each step is the RELION job of CryoPlug (same commands as RELION's GUI) run in a sub-folder of the live job with
``--only_do_unfinished``, as RELION's own continued jobs: a cycle processes only the movies that arrived since the last
one, and every step rewrites its STAR file for all the movies. The panel follows the session; "Stop after this cycle"
ends the job with its outputs (micrographs, picks, particles) for the next jobs.
"""
from __future__ import annotations

import json
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from cryoplug.jobs import register
from cryoplug.jobs.base import JobContext, JobError, JobType, OutputDef, Param, StepContext
from cryoplug.jobs.relion_classify import RelionClass2D
from cryoplug.jobs.relion_common import RelionJob, mpi, threads
from cryoplug.jobs.relion_picking import RelionAutopick, RelionExtract
from cryoplug.jobs.relion_preprocess import (
    CURATION, GAIN_FLIPS, GAIN_ROTATIONS, METRIC, RelionCtffind, RelionCurate, RelionImport, RelionMotionCorr, range_text,
)

UNFINISHED = "--only_do_unfinished"
COPIED = {  # report sections of the steps shown on the live job
    "Motion correction": ("plot", "image"),
    "CTF": ("plot", "image", "exposures"),
    "Curation": ("table",),
    "Picking": ("plot", "image", "picks"),
    "Extraction": ("plot", "image"),
}
MAX_FAILURES = 3


def defaults(jt: type[JobType], **over: Any) -> dict[str, Any]:
    params = {p.name: p.default for p in jt.params}
    params.update(over)
    return params


def clean_limits(value: Any) -> dict[str, list[float | None]]:
    """Acceptance limits sent from the panel: {metric: [lower, upper]} for known metrics, empty bounds dropped."""
    out: dict[str, list[float | None]] = {}
    for key, bounds in (value or {}).items() if isinstance(value, dict) else ():
        if key not in METRIC or not isinstance(bounds, (list, tuple)) or len(bounds) != 2:
            continue
        lo, hi = (None if b is None else float(b) for b in bounds)
        if lo is not None or hi is not None:
            out[key] = [lo, hi]
    return out


class Streaming2D:
    """2D classification of the particles extracted so far, run beside the live cycles (as CryoSPARC Live's
    streaming 2D classification): each run in its own folder, on a copy of the particle file of that moment."""

    def __init__(self, ctx: JobContext):
        self.ctx = ctx
        self.thread: threading.Thread | None = None
        self.step: StepContext | None = None  # the run in progress or the last one started
        self.done: StepContext | None = None  # the last run finished
        self.runs = 0
        self.particles = 0  # particles of the last run started
        self.done_particles = 0
        self.published = 0  # run published as the outputs of the live job
        self.error = ""

    def busy(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    def start(self, parts: dict[str, Any], n: int) -> None:
        p = self.ctx.params
        self.runs += 1
        folder = f"class2d/run{self.runs:03d}"
        out_dir = self.ctx.path(folder)
        out_dir.mkdir(parents=True, exist_ok=True)
        snapshot = out_dir / "particles_snapshot.star"  # the extraction rewrites its file at the next cycle
        shutil.copy(parts["path"], snapshot)
        meta = {**parts["meta"], "relion": {**(parts["meta"].get("relion") or {}), "star": self.ctx.rel(snapshot)}}
        ref = {**parts, "path": str(snapshot), "files": [str(snapshot)], "meta": meta}
        step = StepContext(self.ctx, folder, defaults(
            RelionClass2D, classes=p["class2d_classes"], vdam_batches=p["class2d_batches"],
            particle_diameter=p["class2d_diameter"], threads=p["threads"], gpus=min(1, int(p["gpus"])), mpi=1),
            {"particles": ref}, "Streaming 2D", forward=False)
        self.step, self.particles = step, n
        self.ctx.log(f"Streaming 2D classification {self.runs}: {n:,} particles, {p['class2d_classes']} classes")
        self.thread = threading.Thread(target=self._run, args=(step, n), name="streaming-2d", daemon=True)
        self.thread.start()

    def _run(self, step: StepContext, n: int) -> None:
        try:
            RelionClass2D().run(step)
            self.done, self.done_particles, self.error = step, n, ""
        except Exception as exc:  # a failed run, or the program stopped with the session
            self.error = (str(exc).splitlines() or [type(exc).__name__])[0][:300]
            step.log(f"Streaming 2D classification stopped: {self.error}")

    def stop(self) -> None:
        if self.busy() and self.step is not None and self.step._current_proc is not None:
            self.ctx.log("Stopping the streaming 2D classification in progress (the last finished one is kept)")
            self.step._terminate(self.step._current_proc)
        if self.thread:
            self.thread.join(timeout=60)

    def gallery(self) -> dict[str, Any] | None:
        """The class gallery of the run in progress (as written so far) or of the last finished run."""
        step = self.step if self.busy() else self.done
        if step is None:
            return None
        try:
            sections = json.loads(step.report_path.read_text()).get("sections", [])
        except (OSError, ValueError):
            return None
        return next((s for s in sections if s.get("kind") == "classes2d"), None)


@register
class RelionLive(RelionJob):
    name = "relion_live"
    title = "Live preprocessing (RELION)"
    category = "Preprocessing"
    controls = ("stop", "pause", "resume", "thresholds")
    gpu_param = "gpus"
    cpu_param = "threads"
    description = ("Processes the movies while they are being collected, as CryoSPARC Live: at every cycle the new "
                   "movies of the folder are imported, motion-corrected, CTF-estimated, sorted on thresholds and, if "
                   "asked, picked and extracted (RELION's jobs with --only_do_unfinished). The panel follows the "
                   "session: counts, rate, CTF fit, defocus, motion and ice, the exposures and the picks; the "
                   "acceptance limits can be changed there, the session paused and resumed, and a streaming 2D "
                   "classification follows the particles. 'Stop after this cycle' ends the job with its micrographs, "
                   "picks, particles and 2D classes for the next jobs.")
    inputs = []
    params = [
        Param("movies", "path", "", label="Movies", required=True, placeholder="/data/session/Movies/*.tiff",
              help="Wildcard of the movies as they arrive (or their folder); '**' searches sub-folders."),
        Param("pixel_size", "float", 0.0, label="Pixel size", unit="Å", min=0.0, max=20, required=True),
        Param("voltage", "float", 300.0, label="Voltage", unit="kV", min=50, max=500),
        Param("cs", "float", 2.7, label="Spherical aberration", unit="mm", min=0, max=10),
        Param("amplitude_contrast", "float", 0.1, label="Amplitude contrast", min=0, max=0.5, advanced=True),
        Param("dose_per_frame", "float", 0.0, label="Dose per frame", unit="e⁻/Å²", min=0, required=True),
        Param("gain", "path", "", label="Gain reference", placeholder="optional"),
        Param("gain_rotation", "choice", "0", choices=GAIN_ROTATIONS, label="Gain rotation", advanced=True,
              labels={"0": "No rotation", "1": "90° (1)", "2": "180° (2)", "3": "270° (3)"}),
        Param("gain_flip", "choice", "0", choices=GAIN_FLIPS, label="Gain flip", advanced=True,
              labels={"0": "No flipping", "1": "Upside down (1)", "2": "Left to right (2)"}),
        Param("implementation", "choice", "relion", choices=["relion", "motioncor2"], label="Motion correction",
              labels={"relion": "RELION's own (CPU)", "motioncor2": "UCSF MotionCor2 (GPU)"}),
        Param("patches_x", "int", 5, label="Patches in X", min=1, max=20),
        Param("patches_y", "int", 5, label="Patches in Y", min=1, max=20),
        Param("binning", "float", 1.0, label="Binning", min=1, max=8, help="2 for super-resolution movies."),
        Param("eer_grouping", "int", 32, label="EER fractionation", min=1, max=1000, advanced=True),
        Param("res_max", "float", 5.0, label="CTF fit up to", unit="Å", min=1, max=30),
        Param("defocus_min", "float", 5000.0, label="Minimum defocus searched", unit="Å", min=0, advanced=True),
        Param("defocus_max", "float", 50000.0, label="Maximum defocus searched", unit="Å", min=1000, advanced=True),
        Param("max_ctf_resolution", "float", 0.0, label="Accept: CTF fit at least to", unit="Å", min=0,
              help="Micrographs whose CTF fits worse are rejected (e.g. 6-8 Å). 0 = no limit."),
        Param("max_motion", "float", 0.0, label="Accept: total motion at most", unit="Å", min=0, help="0 = no limit."),
        Param("max_astigmatism", "float", 0.0, label="Accept: astigmatism at most", unit="Å", min=0, advanced=True),
        Param("min_defocus", "float", 0.0, label="Accept: defocus at least", unit="Å", min=0, advanced=True),
        Param("max_defocus", "float", 0.0, label="Accept: defocus at most", unit="Å", min=0, advanced=True),
        Param("picking", "choice", "none", choices=["none", "log", "topaz"], label="Picking",
              labels={"none": "None", "log": "Laplacian-of-Gaussian", "topaz": "Topaz (general model)"}),
        Param("diameter_min", "float", 0.0, label="Particle diameter: minimum", unit="Å", min=0,
              help="Laplacian-of-Gaussian picking."),
        Param("diameter_max", "float", 0.0, label="Particle diameter: maximum", unit="Å", min=0),
        Param("log_threshold", "float", 0.0, label="LoG threshold adjustment", unit="σ", min=-5, max=5, advanced=True),
        Param("particle_diameter", "float", 0.0, label="Particle diameter (Topaz)", unit="Å", min=0),
        Param("extract", "bool", False, label="Extract the particles", help="Needs picking."),
        Param("box", "int", 0, label="Box size", unit="px", min=0, max=2048, help="≈ 2 × the particle diameter, even."),
        Param("rescale", "int", 0, label="Re-scaled box", unit="px", min=0, max=2048,
              help="Down-scaled particles for 2D (e.g. 64-128 px). 0 = not re-scaled."),
        Param("class2d", "bool", False, label="Streaming 2D classification",
              help="2D classes of the particles so far, run again as particles accumulate (needs the extraction)."),
        Param("class2d_classes", "int", 50, label="2D classes", min=2, max=300, show_if="class2d"),
        Param("class2d_min", "int", 2000, label="First 2D classification from", unit="particles", min=10,
              show_if="class2d"),
        Param("class2d_every", "int", 10000, label="Classify again after", unit="new particles", min=10,
              show_if="class2d"),
        Param("class2d_batches", "int", 100, label="VDAM mini-batches", min=10, max=1000, show_if="class2d"),
        Param("class2d_diameter", "float", 0.0, label="2D mask diameter", unit="Å", min=0, show_if="class2d",
              help="0 = 90 % of the box."),
        Param("interval", "int", 60, label="Look for new movies every", unit="s", min=2, max=3600),
        Param("settle", "int", 30, label="A movie is complete after", unit="s", min=0, max=3600,
              help="A file that changed less than this long ago is still being written: left for the next cycle."),
        Param("stop_after", "int", 0, label="Stop after no new movie for", unit="min", min=0,
              help="0 = run until stopped from the panel."),
        mpi(4, help="MPI processes for CTFFIND, picking and extraction."),
        threads(8, help="Threads of RELION's motion correction."),
        Param("gpus", "int", 0, label="GPUs", min=0, max=16, help="For MotionCor2 and Topaz."),
    ]
    outputs = [
        OutputDef("micrographs", "micrographs", "Accepted micrographs"),
        OutputDef("all_micrographs", "micrographs", "All micrographs with CTF"),
        OutputDef("rejected", "micrographs", "Rejected micrographs"),
        OutputDef("movies", "movies", "Movies"),
        OutputDef("coordinates", "coordinates", "Picks"),
        OutputDef("particles", "particles", "Particles"),
        OutputDef("classes", "class_averages", "2D classes (streaming)"),
        OutputDef("classified_particles", "particles", "Particles of the 2D classes"),
    ]

    @classmethod
    def validate(cls, params: dict[str, Any], connected: dict[str, str]) -> list[str]:
        problems = []
        if not float(params.get("pixel_size") or 0) > 0:
            problems.append("Give the pixel size of the movies")
        if not float(params.get("dose_per_frame") or 0) > 0:
            problems.append("Give the dose per frame (dose weighting and the power spectra need it)")
        if params.get("implementation") == "motioncor2" and not int(params.get("gpus") or 0):
            problems.append("MotionCor2 needs at least one GPU")
        pick = params.get("picking", "none")
        if pick == "log" and not (float(params.get("diameter_min") or 0) > 0 and float(params.get("diameter_max") or 0) > 0):
            problems.append("Laplacian-of-Gaussian picking: give the minimum and maximum particle diameters")
        if pick == "topaz" and not float(params.get("particle_diameter") or 0) > 0:
            problems.append("Topaz picking: give the particle diameter")
        if params.get("extract"):
            if pick == "none":
                problems.append("Extraction needs picking")
            if not int(params.get("box") or 0) > 0 or int(params.get("box") or 0) % 2:
                problems.append("Extraction: give an even box size")
        if params.get("class2d") and not params.get("extract"):
            problems.append("The streaming 2D classification needs the extraction")
        return problems

    # ---------------------------------------------------------------- loop
    def run(self, ctx: JobContext) -> None:
        p = ctx.params
        state: dict[str, Any] = {"started": time.time(), "last_new": time.time(), "cycles": 0, "processed": 0, "movies": 0,
                                 "history": [], "failures": 0, "status": "watching", "last_error": "",
                                 "limits": self.limits(ctx), "limits_at": 0.0}
        s2d = Streaming2D(ctx)
        stopping = False
        try:
            while True:
                final = stopping or ctx.control_requested("stop")
                self.take_limits(ctx, state)
                if self.paused(ctx) and not final:
                    state["status"] = "paused"
                else:
                    state["status"] = "processing"
                    self.dashboard(ctx, state, s2d)
                    new = self.cycle(ctx, state)
                    if new:
                        state["last_new"] = time.time()
                    self.stream_2d(ctx, state, s2d)
                    if final:
                        break
                    idle = time.time() - state["last_new"]
                    if p["stop_after"] > 0 and state["movies"] and idle > p["stop_after"] * 60:
                        ctx.log(f"No new movie for {idle / 60:.0f} min: stopping")
                        break
                    state["status"] = "watching" if state["movies"] else "waiting for movies"
                state["next"] = time.time() + p["interval"]
                self.dashboard(ctx, state, s2d)
                ctx.progress(0.5, f"{state['movies']:,} movies; " + ("paused" if state["status"] == "paused"
                                                                      else f"next look in {p['interval']} s"), log=False)
                end = time.time() + p["interval"]
                while time.time() < end:
                    if ctx.control_requested("stop"):
                        ctx.log("Stop asked: a last cycle for the movies arrived meanwhile")
                        stopping = True
                        break
                    if state["status"] == "paused" and not self.paused(ctx):
                        ctx.log("Resumed")
                        break
                    if self.new_limits(ctx, state):
                        break
                    if s2d.done is not None and s2d.published != s2d.runs and not s2d.busy():
                        break  # a streaming 2D classification finished: show it
                    ctx.write_state(heartbeat=time.time())
                    time.sleep(min(1.0, max(0.05, end - time.time())))
        finally:
            s2d.stop()
        self.publish_2d(ctx, s2d)
        state["status"] = "stopped"
        state.pop("next", None)
        self.dashboard(ctx, state, s2d)
        if not any(o["name"] == "movies" for o in ctx.outputs):
            raise JobError(f"No movie arrived in {p['movies']}")
        ctx.add_highlight("Movies", f"{state['movies']:,}")

    @staticmethod
    def paused(ctx: JobContext) -> bool:
        pause, resume = ctx.control_entry("pause"), ctx.control_entry("resume")
        return pause is not None and (resume is None or float(pause.get("at") or 0) > float(resume.get("at") or 0))

    @staticmethod
    def new_limits(ctx: JobContext, state: dict[str, Any]) -> bool:
        entry = ctx.control_entry("thresholds")
        return entry is not None and float(entry.get("at") or 0) > state["limits_at"]

    def take_limits(self, ctx: JobContext, state: dict[str, Any]) -> None:
        """Acceptance limits changed from the panel: the micrographs are sorted again at the next cycle."""
        if not self.new_limits(ctx, state):
            return
        entry = ctx.control_entry("thresholds") or {}
        state["limits"], state["limits_at"] = clean_limits(entry.get("value")), float(entry.get("at") or 0)
        state["pending"] = True
        shown = "; ".join(range_text(k, *b) for k, b in state["limits"].items()) or "none"
        ctx.log(f"New acceptance limits: {shown}")

    def stream_2d(self, ctx: JobContext, state: dict[str, Any], s2d: Streaming2D) -> None:
        """Starts a streaming 2D classification when enough new particles came since the last one."""
        p = ctx.params
        self.publish_2d(ctx, s2d)
        if not p["class2d"] or s2d.busy():
            return
        parts = next((o for o in ctx.outputs if o["name"] == "particles"), None)
        n = int(state.get("particles") or 0)
        if parts is None or n < p["class2d_min"] or (s2d.runs and n < s2d.particles + p["class2d_every"]):
            return
        pdir = ctx.project_dir
        s2d.start({"type": "particles", "label": parts.get("label", ""), "source": f"{ctx.uid}.particles",
                   "path": str(pdir / parts["path"]), "files": [str(pdir / f) for f in parts.get("files", [parts["path"]])],
                   "meta": parts.get("meta", {}), "siblings": {}}, n)

    def publish_2d(self, ctx: JobContext, s2d: Streaming2D) -> None:
        """The last finished streaming 2D classification as outputs of the live job (to select its good classes)."""
        if s2d.done is None or s2d.published == s2d.runs or s2d.busy():
            return
        if s2d.done is s2d.step:
            self.publish(ctx, s2d.done, "classes", "classes", f"2D classes ({s2d.done_particles:,} particles)")
            self.publish(ctx, s2d.done, "particles", "classified_particles", f"Particles of the 2D classes ({s2d.done_particles:,})")
        s2d.published = s2d.runs

    def cycle(self, ctx: JobContext, state: dict[str, Any]) -> int:
        """Imports the movies that arrived and runs the steps on them; returns the number of new movies."""
        p = ctx.params
        state["cycles"] += 1
        imp = StepContext(ctx, "import", defaults(
            RelionImport, movies=p["movies"], pixel_size=p["pixel_size"], voltage=p["voltage"], cs=p["cs"],
            amplitude_contrast=p["amplitude_contrast"], dose_per_frame=p["dose_per_frame"], gain=p["gain"],
            gain_rotation=p["gain_rotation"], gain_flip=p["gain_flip"], settle=p["settle"]), {}, "Import")
        try:
            RelionImport().run(imp)
        except JobError as exc:
            if "No file matches" in str(exc):
                return 0
            raise
        movies = imp.output_ref("movies")
        n = int(movies["meta"].get("n_movies") or 0)
        new = n - state["movies"]
        if new <= 0 and not state.get("pending"):
            return 0
        state["movies"] = n
        state["pending"] = True  # cleared once the steps went through (a failed cycle is tried again)
        ctx.log(f"Cycle {state['cycles']}: {new:,} new movie(s), {n:,} in all")
        try:
            counts = self.steps(ctx, imp, movies, state)
        except JobError as exc:
            state["failures"] += 1
            state["last_error"] = str(exc).splitlines()[0][:300]
            ctx.warn(f"Cycle {state['cycles']} failed ({state['failures']}/{MAX_FAILURES}): {exc}")
            if state["failures"] >= MAX_FAILURES:
                raise JobError(f"Live processing failed {MAX_FAILURES} times in a row: {exc}") from None
            return max(new, 0)
        state["failures"], state["last_error"], state["pending"] = 0, "", False
        state["processed"] += 1
        state.update(counts)
        state["history"].append({"t": round(time.time() - state["started"], 1), "movies": n,
                                 **{k: counts.get(k, 0) for k in ("ctf", "accepted", "particles")}})
        return max(new, 0)

    def steps(self, ctx: JobContext, imp: StepContext, movies: dict[str, Any], state: dict[str, Any]) -> dict[str, int]:
        p = ctx.params
        counts: dict[str, int] = {}
        mc = StepContext(ctx, "motion", defaults(
            RelionMotionCorr, implementation=p["implementation"], patches_x=p["patches_x"], patches_y=p["patches_y"],
            binning=p["binning"], eer_grouping=p["eer_grouping"], threads=p["threads"], gpus=p["gpus"],
            mpi=max(1, p["gpus"]) if p["implementation"] == "motioncor2" else 1, extra_args=UNFINISHED),
            {"movies": movies}, "Motion correction")
        RelionMotionCorr().run(mc)
        mics = mc.output_ref("micrographs")
        counts["corrected"] = int(mics["meta"].get("n_micrographs") or 0)
        ctf = StepContext(ctx, "ctf", defaults(
            RelionCtffind, res_max=p["res_max"], defocus_min=p["defocus_min"], defocus_max=p["defocus_max"],
            mpi=p["mpi"], extra_args=UNFINISHED), {"micrographs": mics}, "CTF")
        RelionCtffind().run(ctf)
        with_ctf = ctf.output_ref("micrographs")
        counts["ctf"] = int(with_ctf["meta"].get("n_micrographs") or 0)
        accepted, cur = with_ctf, None
        if state["limits"]:  # those of the parameters, or the last ones sent from the panel
            cur = StepContext(ctx, "curate", defaults(RelionCurate, mode="thresholds"), {"micrographs": with_ctf}, "Curation")
            jt = RelionCurate()
            inp, star, _, table, values, _ = jt.load(cur)
            usable = {k: b for k, b in state["limits"].items() if k in values}
            if usable:
                try:
                    jt.apply(cur, inp, star, table, values, usable, {}, how="live")
                    accepted = cur.output_ref("micrographs")
                except JobError as exc:
                    if "No micrograph kept" not in str(exc):
                        raise
                    ctx.warn(f"Curation: {exc}")
                    accepted = None
            else:
                cur = None
        counts["accepted"] = int(accepted["meta"].get("n_micrographs") or 0) if accepted else 0
        counts["rejected"] = counts["ctf"] - counts["accepted"]
        pick = ext = None
        if p["picking"] != "none" and accepted:
            over: dict[str, Any] = {"method": p["picking"], "mpi": p["mpi"], "gpus": p["gpus"], "extra_args": UNFINISHED}
            if p["picking"] == "log":
                over.update(diameter_min=p["diameter_min"], diameter_max=p["diameter_max"], log_threshold=p["log_threshold"])
            else:
                over.update(particle_diameter=p["particle_diameter"])
            pick = StepContext(ctx, "autopick", defaults(RelionAutopick, **over), {"micrographs": accepted}, "Picking")
            RelionAutopick().run(pick)
            coords = pick.output_ref("coordinates")
            counts["picked"] = int(coords["meta"].get("n_particles") or 0) if coords else 0
            if p["extract"] and coords:
                ext = StepContext(ctx, "extract", defaults(RelionExtract, box=p["box"], rescale=p["rescale"], mpi=p["mpi"],
                                                           extra_args=UNFINISHED),
                                  {"micrographs": accepted, "coordinates": coords}, "Extraction")
                RelionExtract().run(ext)
                parts = ext.output_ref("particles")
                counts["particles"] = int(parts["meta"].get("n_particles") or 0) if parts else 0
        if p["picking"] != "none" and not accepted:  # no micrograph within the limits: no picks either
            counts["picked"] = 0
            if p["extract"]:
                counts["particles"] = 0
        # the steps' plots, exposures and picks on the live job, the outputs so far
        for step in (mc, ctf, cur, pick, ext):
            if step is not None:
                self.copy_sections(ctx, step, state)
        self.publish(ctx, imp, "movies", "movies")
        self.publish(ctx, ctf, "micrographs", "all_micrographs", "All micrographs with CTF")
        if accepted:
            self.publish(ctx, cur or ctf, "micrographs", "micrographs", "Accepted micrographs")
        else:  # nothing within the limits now: no accepted micrographs left from before
            self.drop(ctx, "micrographs")
        if cur is not None and cur.output_ref("rejected"):
            self.publish(ctx, cur, "rejected", "rejected", "Rejected micrographs")
        elif not accepted:
            self.publish(ctx, ctf, "micrographs", "rejected", "Rejected micrographs")
        else:
            self.drop(ctx, "rejected")
        if pick is not None:
            self.publish(ctx, pick, "coordinates", "coordinates")
        elif p["picking"] != "none":
            self.drop(ctx, "coordinates")
        if ext is not None:
            self.publish(ctx, ext, "particles", "particles")
        elif p["extract"]:
            self.drop(ctx, "particles")
        return counts

    @staticmethod
    def copy_sections(ctx: JobContext, step: StepContext, state: dict[str, Any]) -> None:
        for sec in step.report.get("sections", []):
            if sec.get("kind") not in COPIED.get(step.label, ()):
                continue
            data = {k: v for k, v in sec.items() if k not in ("kind", "title", "key")}
            if sec["kind"] == "exposures":
                data.update(curate=False, source_output="all_micrographs", live=True, thresholds=state["limits"])
            ctx._section(sec["kind"], f"{sec['title']}" if sec["kind"] != "exposures" else "Exposures",
                         key=f"{step.label}:{sec.get('key') or sec['title']}", **data)

    @staticmethod
    def limits(ctx: JobContext) -> dict[str, list[float | None]]:
        out: dict[str, list[float | None]] = {}
        for param, (key, side) in CURATION.items():
            v = float(ctx.params.get(param) or 0)
            if v > 0:
                out.setdefault(key, [None, None])[side] = v
        return out

    @staticmethod
    def drop(ctx: JobContext, name: str) -> None:
        if any(o["name"] == name for o in ctx.outputs):
            ctx.outputs = [o for o in ctx.outputs if o["name"] != name]
            ctx.write_state()

    @staticmethod
    def publish(ctx: JobContext, step: StepContext, name: str, as_name: str, label: str | None = None) -> None:
        out = next((o for o in step.outputs if o["name"] == name), None)
        if out is None:
            return
        thumb = ctx.abs(out["thumbnail"]) if out.get("thumbnail") else None
        ctx.add_output(as_name, out["type"], [ctx.abs(f) for f in out.get("files", [out["path"]])], label or out.get("label"),
                       meta=out.get("meta", {}), thumbnail=thumb)

    @staticmethod
    def dashboard(ctx: JobContext, state: dict[str, Any], s2d: Streaming2D | None = None) -> None:
        hist = state["history"]
        now = time.time() - state["started"]
        recent = [h for h in hist if h["t"] >= now - 3600]
        rate = None
        if len(recent) >= 2 and recent[-1]["t"] > recent[0]["t"]:
            rate = (recent[-1]["movies"] - recent[0]["movies"]) * 3600 / (recent[-1]["t"] - recent[0]["t"])
        counts = {k: state.get(k) for k in ("movies", "corrected", "ctf", "accepted", "rejected", "picked", "particles")
                  if state.get(k) is not None}
        stream = None
        if s2d is not None and s2d.runs:
            stream = {"runs": s2d.runs, "running": s2d.busy(), "particles": s2d.particles,
                      "done_particles": s2d.done_particles, "error": s2d.error}
        ctx._section("live", "Live session", key="live", status=state["status"], counts=counts, cycles=state["cycles"],
                     processed=state["processed"],
                     started=state["started"], updated=time.time(), last_new=state["last_new"], next=state.get("next"),
                     rate=round(rate, 1) if rate else None, error=state.get("last_error", ""),
                     interval=int(ctx.params["interval"]), class2d=stream,
                     limits=[range_text(k, *b) for k, b in state["limits"].items()])
        gallery = s2d.gallery() if s2d is not None else None
        if gallery is not None:
            busy = s2d.busy()
            n = s2d.particles if busy else s2d.done_particles
            data = {k: v for k, v in gallery.items() if k not in ("kind", "title", "key", "actions", "running")}
            ctx._section("classes2d", f"2D classes (streaming, {n:,} particles)", key="class2d:classes", running=busy,
                         actions=not busy and state["status"] == "stopped", **data)
        if len(hist) >= 2:
            x = [round(h["t"] / 60, 2) for h in hist]
            series = [{"name": "Movies", "x": x, "y": [h["movies"] for h in hist]},
                      {"name": "With CTF", "x": x, "y": [h["ctf"] for h in hist]},
                      {"name": "Accepted", "x": x, "y": [h["accepted"] for h in hist]}]
            ctx.add_plot("Movies over the session", series, x_label="Minutes since the start", y_label="Movies",
                         key="live_counts")
        (ctx.path("live.json")).write_text(json.dumps({k: v for k, v in state.items()}, default=str))
