"""Shared parts of the RELION subtomogram-averaging jobs: subtomogram particles, which flow from job to job as RELION 5's
optimisation sets (the particle set, the tomogram set and the particle trajectories they name; cryoplug.tomo)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from cryoplug import relion as rl
from cryoplug import tomo as tm
from cryoplug.jobs.base import InputData, JobContext, JobError


def is_tomo(inp: InputData | None) -> bool:
    return inp is not None and (inp.type == "tomo_particles" or bool((inp.meta.get("relion") or {}).get("tomo")))


def tomo_particles(ctx: JobContext, inp: InputData, extracted: bool = True) -> dict[str, Any]:
    """Subtomogram particles as relion_particles gives single particles: the particle STAR file, the optimisation set
    relion_refine reads (--ios), the box and pixel size of the extracted subtomograms."""
    opt = Path(inp.files[0] if inp.files else inp.path)
    if not opt.exists():
        raise JobError(f"{inp.source}: {opt.name} is not available any more")
    files = tm.read_optimisation_set(opt)
    if "particles" not in files:
        raise JobError(f"{inp.source}: {opt.name} names no particle set")
    star = ctx.abs(files["particles"])
    if not star.exists():
        raise JobError(f"{inp.source}: the particle set {files['particles']} is missing")
    tables = rl.read_star(star)
    parts = tables.get("particles") or rl.particle_table(tables)
    if extracted and not parts.has("rlnImageName"):
        raise JobError("These particles have no subtomograms yet: extract them first (Extract subtomograms)")
    optics = tables.get("optics")
    box = int(float(optics.value("rlnImageSize") or 0)) if optics is not None and optics.rows else 0
    apix = float(optics.value("rlnImagePixelSize") or 0) if optics is not None and optics.rows else 0.0
    if extracted and not (box and apix):
        raise JobError(f"{files['particles']}: no box or pixel size in its optics table")
    info = {"n_particles": len(parts), "box": box, "pixel_size": apix, "has_poses": parts.has("rlnAngleRot"),
            "half_sets": parts.has("rlnRandomSubset")}
    ctx.log(f"Subtomogram particles: {len(parts):,} in {len(set(parts.column('rlnTomoName'))) if parts.has('rlnTomoName') else '?'} "
            f"tomograms" + (f"; box {box} px, {apix:g} Å/px" if box else ""))
    return {"star": star, "ref": files["particles"], "ios": ctx.rel(opt), "opt": opt, "box": box, "pixel_size": apix,
            "frame": None, "n": len(parts), "tables": tables, "has_poses": info["has_poses"], "info": info, "meta": inp.meta,
            "tomo": True, "tomograms": files.get("tomograms"), "trajectories": files.get("trajectories")}


def particle_args(parts: dict[str, Any]) -> list[str]:
    """The particles of a relion_refine command: --i for single particles, --ios (the optimisation set) for
    subtomograms, as RELION's GUI passes them."""
    return ["--ios", parts["ios"]] if parts.get("tomo") else ["--i", parts["ref"]]


def tomo_output(ctx: JobContext, name: str, star: Path, label: str, parts: dict[str, Any], kind: str,
                opt: Path | None = None, **extra: Any) -> dict[str, Any]:
    """Subtomogram particles written in this job as an output: the optimisation set RELION wrote with them, or a new one
    naming them with the tomograms (and trajectories) of the input."""
    if opt is None or not opt.exists():
        opt = star.with_name(star.name.replace("_data.star", "").replace(".star", "") + "_optimisation_set.star")
        tm.write_optimisation_set(opt, particles=ctx.rel(star), tomograms=parts.get("tomograms"),
                                  trajectories=parts.get("trajectories"))
    meta = tm.particle_meta(ctx.rel, opt, ctx.project_dir, kind, **extra)
    return ctx.add_output(name, "tomo_particles", opt, label, meta=meta)
