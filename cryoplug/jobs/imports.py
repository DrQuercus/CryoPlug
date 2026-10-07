"""Import jobs: CryoSPARC results, maps, atomic models and sequences."""
from __future__ import annotations

import json
import re
import shutil
import urllib.request
from pathlib import Path
from typing import Any

from cryoplug.jobs import register
from cryoplug.jobs.base import (
    JobContext,
    JobError,
    JobType,
    OutputDef,
    Param,
    resolution_param,
)
from cryoplug.jobs.maptools import compute_and_report_fsc

MAP_SUFFIXES = (".mrc", ".map", ".ccp4", ".mrcs")


def _transfer(ctx: JobContext, src: str | Path, mode: str, name: str | None = None) -> Path:
    src = Path(src).expanduser()
    if not src.is_file():
        raise JobError(f"File not found: {src}")
    dst = ctx.path(name or src.name)
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    if mode == "symlink":
        dst.symlink_to(src.resolve())
    else:
        ctx.log(f"Copying {src} -> {dst.name}")
        shutil.copy2(src, dst)
    return dst


# ------------------------------------------------------------------- CryoSPARC
_CS_KINDS = [
    ("half_a", re.compile(r"half_?a\b|half_?A|half1\b|half_1\b", re.I)),
    ("half_b", re.compile(r"half_?b\b|half_?B|half2\b|half_2\b", re.I)),
    ("map_sharp", re.compile(r"map_sharp", re.I)),
    ("mask_fsc_auto", re.compile(r"mask_fsc_auto", re.I)),
    ("mask_fsc", re.compile(r"mask_fsc", re.I)),
    ("mask_refine", re.compile(r"mask_refine", re.I)),
    ("map_locres", re.compile(r"locres", re.I)),
    ("map_locfilter", re.compile(r"locfilter|local_filter|locally_filtered", re.I)),
    ("mask", re.compile(r"mask", re.I)),
    ("map", re.compile(r"(volume_)?map$|volume$|_map\b", re.I)),
]


def classify_cryosparc_file(name: str) -> str | None:
    stem = name
    for suffix in MAP_SUFFIXES:
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    else:
        return None
    s = stem.replace("half_map_A", "half_A").replace("half_map_B", "half_B")
    for kind, rx in _CS_KINDS:
        if rx.search(s):
            return kind
    return None


def _iteration(name: str) -> int:
    m = re.search(r"J\d+_(\d+)_", name)
    return int(m.group(1)) if m else -1


def find_cryosparc_maps(job_dir: Path) -> dict[str, Path]:
    """Pick the latest-iteration file of each kind in a CryoSPARC job directory."""
    best: dict[str, tuple[int, float, Path]] = {}
    for p in sorted(job_dir.iterdir()):
        if not p.is_file():
            continue
        kind = classify_cryosparc_file(p.name)
        if kind is None:
            continue
        key = (_iteration(p.name), p.stat().st_mtime)
        if kind not in best or key > best[kind][:2]:
            best[kind] = (*key, p)
    return {k: v[2] for k, v in best.items()}


def read_cryosparc_job_info(job_dir: Path) -> dict[str, Any]:
    info: dict[str, Any] = {}
    jf = job_dir / "job.json"
    if jf.is_file():
        try:
            data = json.loads(jf.read_text())
            for key in ("uid", "project_uid", "job_type", "type", "title", "version", "status"):
                if key in data and isinstance(data[key], (str, int, float)):
                    info[key] = data[key]
        except (OSError, ValueError):
            pass
    return info


@register
class ImportCryoSPARC(JobType):
    name = "import_cryosparc"
    title = "Import from CryoSPARC"
    category = "Import"
    description = ("Import the final reconstruction of a CryoSPARC refinement job (half maps, unsharpened and "
                   "sharpened maps, FSC mask, local resolution) and its particles (for heterogeneity analysis) by "
                   "pointing at its job directory, e.g. /data/CS-myproject/J245. The latest iteration is selected "
                   "automatically.")
    software = ["CryoSPARC"]
    params = [
        Param("job_dir", "path", "", label="CryoSPARC job directory", required=True, path_kind="dir",
              help="Directory of the CryoSPARC refinement/sharpening job (contains J###_..._volume_map_half_A.mrc ...)."),
        resolution_param(),
        Param("compute_fsc", "bool", True, label="Compute half-map FSC",
              help="Recompute the gold-standard FSC (masked, phase-randomisation corrected) to obtain the resolution."),
        Param("link_mode", "choice", "copy", choices=["copy", "symlink"], label="Copy or link files",
              help="Copy keeps the CryoPlug project self-contained; symlink saves disk space."),
        Param("symmetry", "str", "C1", label="Symmetry", help="Point-group symmetry used in the reconstruction (C1, C2, D7, ...)."),
        Param("import_particles", "bool", True, label="Import the particles",
              help="Particle metadata (.cs) for cryoDRGN and 3D variability; the images stay in the CryoSPARC project."),
    ]
    outputs = [
        OutputDef("half_maps", "half_maps", "Half maps"),
        OutputDef("map", "map", "Unsharpened map"),
        OutputDef("map_sharp", "map", "Sharpened map"),
        OutputDef("mask", "mask", "FSC mask"),
        OutputDef("map_locres", "locres", "Local resolution map"),
        OutputDef("particles", "particles", "Particles"),
        OutputDef("latent", "latent", "3D variability coordinates"),
    ]

    def run(self, ctx: JobContext) -> None:
        from cryoplug.particles import find_particle_files
        src = Path(ctx.params["job_dir"]).expanduser()
        if src.is_file():
            src = src.parent
        if not src.is_dir():
            raise JobError(f"Not a directory: {src}")
        found = find_cryosparc_maps(src)
        info = read_cryosparc_job_info(src)
        if info:
            ctx.log("CryoSPARC job: " + ", ".join(f"{k}={v}" for k, v in info.items()))
        ctx.log("Detected files:\n" + "\n".join(f"  {k:14s} {v.name}" for k, v in sorted(found.items())))
        main_cs, passthrough_cs = find_particle_files(src) if ctx.params["import_particles"] else (None, None)
        if not found and main_cs is None:
            raise JobError(f"No map or particle file recognised in {src}")
        mode = ctx.params["link_mode"]
        local = {k: _transfer(ctx, v, mode) for k, v in found.items()}
        mask_key = next((k for k in ("mask_fsc_auto", "mask_fsc", "mask_refine", "mask") if k in local), None)

        resolution = float(ctx.params.get("resolution") or 0)
        if "half_a" in local and "half_b" in local and ctx.params["compute_fsc"]:
            ctx.progress(0.3, "Computing half-map FSC")
            fsc_res = compute_and_report_fsc(ctx, local["half_a"], local["half_b"], local.get(mask_key) if mask_key else None)
            if resolution <= 0:
                resolution = fsc_res
        meta: dict[str, Any] = {"symmetry": ctx.params.get("symmetry") or "C1", "source": str(src)}
        if resolution > 0:
            meta["resolution"] = round(resolution, 3)
            ctx.add_highlight("Resolution", f"{resolution:.2f} Å")

        if "half_a" in local and "half_b" in local:
            ctx.add_output("half_maps", "half_maps", [local["half_a"], local["half_b"]], "Half maps", meta=meta)
        if "map" in local:
            ctx.add_output("map", "map", local["map"], "Unsharpened map", meta=meta)
        if "map_sharp" in local:
            ctx.add_output("map_sharp", "map", local["map_sharp"], "Sharpened map", meta=meta)
        if "map_locfilter" in local:
            ctx.add_output("map_locfilter", "map", local["map_locfilter"], "Locally filtered map", meta=meta)
        if mask_key:
            ctx.add_output("mask", "mask", local[mask_key], "FSC mask", meta=meta)
        if "map_locres" in local:
            lmeta = {**meta, **locres_meta(ctx, local["map_locres"], local.get(mask_key) if mask_key else None)}
            shown = local.get("map_sharp") or local.get("map")
            if shown:
                lmeta["colour_map"] = ctx.rel(shown)  # the 3D viewer colours this map by local resolution
            ctx.add_output("map_locres", "locres", local["map_locres"], "Local resolution map", meta=lmeta)
        rows = [[k, v.name, str(found[k])] for k, v in sorted(local.items())]
        if main_cs is not None:
            imported = import_cs_particles(ctx, main_cs, passthrough_cs, meta)
            rows += [["particles", "particles.cs", str(p)] for p in (main_cs, passthrough_cs) if p and imported]
        if not ctx.outputs:
            raise JobError(f"Nothing could be imported from {src} (see the warnings in the log)")
        ctx.add_table("Imported files", ["Kind", "File", "Source"], rows)


def import_cs_particles(ctx: JobContext, main: Path, passthrough: Path | None, meta: dict[str, Any],
                        datadir: str | Path | None = None) -> bool:
    """Particles of a CryoSPARC job as one merged .cs file (metadata only: the images stay in place), and the 3D
    variability coordinates when the particles carry them. Returns False when nothing usable was found."""
    from cryoplug import particles as pt
    try:
        data = pt.merged_particles(main, passthrough)
    except (OSError, ValueError) as exc:
        ctx.warn(f"Particles not imported: {exc}")
        return False
    info = pt.summary(data)
    comps = pt.components(data)
    if not info["has_images"]:
        if comps is None:
            ctx.warn(f"{main.name} has no image locations (blob/path): particles not imported")
            return False
        # the 3D variability coordinates are still worth exploring; the particles cannot be trained on
        ctx.warn(f"{main.name} has no image locations (blob/path; the passthrough file of the job is missing?): only the "
                 "3D variability coordinates are imported, not the particles")
        write_variability_latent(ctx, comps, pt.write_cs(data, ctx.path("particles.cs")))
        return True
    out = pt.write_cs(data, ctx.path("particles.cs"))
    folder = Path(datadir) if datadir else pt.project_dir_of(main.parent)
    pmeta = {**{k: v for k, v in meta.items() if k in ("symmetry", "resolution")}, **info,
             "format": "cs", "datadir": str(folder), "source": str(main)}
    parts = [f"{info['n_particles']:,} particles"]
    if info.get("box"):
        parts.append(f"box {info['box']} px")
    if info.get("pixel_size"):
        parts.append(f"{info['pixel_size']} Å/px")
    ctx.log("Particles: " + ", ".join(parts) + f"; poses: {'yes' if info['has_poses'] else 'no'}, "
            f"CTF: {'yes' if info['has_ctf'] else 'no'}; images relative to {folder}")
    if not info["has_poses"]:
        ctx.warn("These particles have no 3D alignment (alignments3D): cryoDRGN needs the poses of a consensus refinement")
    ctx.add_output("particles", "particles", out, f"Particles ({info['n_particles']:,})", meta=pmeta)
    ctx.add_highlight("Particles", f"{info['n_particles']:,}")
    if comps is not None:
        write_variability_latent(ctx, comps, out)
    return True


def write_variability_latent(ctx: JobContext, comps, particles_path: Path) -> None:
    """3D variability reaction coordinates as a latent space (explorer, particle selection)."""
    import numpy as np

    from cryoplug import particles as pt
    from cryoplug.jobs.heterogeneity import latent_report
    k = min(8, len(comps))
    labels, centres = pt.kmeans(comps, k)
    centres_ind = pt.nearest_indices(comps, centres)
    axes = {"Components": comps[:, :2] if comps.shape[1] > 1 else np.concatenate([comps, np.zeros_like(comps)], 1)}
    path = ctx.path("latent.npz")
    np.savez(path, z=comps.astype(np.float32), labels=labels, centers_ind=centres_ind,
             embedding_components=axes["Components"].astype(np.float32))
    latent_report(ctx, axes, labels, title="3D variability coordinates of the particles",
                  axis_labels={"Components": ("Component 1", "Component 2")})
    ctx.add_output("latent", "latent", [path, particles_path], f"3D variability coordinates ({comps.shape[1]} components)",
                   meta={"method": "3DVA", "zdim": int(comps.shape[1]), "n_particles": int(len(comps)), "k": k,
                         "particles": ctx.rel(particles_path)})


def locres_meta(ctx: JobContext, path: str | Path, mask: str | Path | None) -> dict[str, Any]:
    """Median and colour range of a local resolution map, inside the mask when there is one."""
    from cryoplug import locres as lr
    from cryoplug.mrc import MapVolume
    try:
        vol = MapVolume.read(path)
        m = MapVolume.read(mask) if mask else None
        region, _how = lr.region(vol, m)
        stats = lr.summary(vol.data[region] if region is not None else vol.data)
    except Exception as exc:  # informative only
        ctx.warn(f"Local resolution map not analysed: {exc}")
        return {}
    if not stats.get("n"):
        return {}
    ctx.add_highlight("Local res.", f"{stats['median']:.2f} Å")
    return {"median": round(stats["median"], 3), "display_range": lr.display_range(stats)}


@register
class ImportMaps(JobType):
    name = "import_maps"
    title = "Import maps"
    category = "Import"
    description = "Import half maps, full maps and masks from any software (RELION, cryoDRGN, EMDB entries...)."
    params = [
        Param("half_map_a", "path", "", label="Half map A", path_kind="file"),
        Param("half_map_b", "path", "", label="Half map B", path_kind="file"),
        Param("map", "path", "", label="Full map (unsharpened)", path_kind="file"),
        Param("map_sharp", "path", "", label="Sharpened map", path_kind="file"),
        Param("mask", "path", "", label="Mask", path_kind="file"),
        resolution_param(),
        Param("pixel_size", "float", 0.0, label="Override pixel size", unit="Å", min=0.0, advanced=True,
              help="Rewrite the voxel size in the imported copies (0 = keep header value)."),
        Param("compute_fsc", "bool", True, label="Compute half-map FSC"),
        Param("link_mode", "choice", "copy", choices=["copy", "symlink"], label="Copy or link files"),
        Param("symmetry", "str", "C1", label="Symmetry"),
    ]
    outputs = [OutputDef("half_maps", "half_maps"), OutputDef("map", "map"), OutputDef("map_sharp", "map"), OutputDef("mask", "mask")]

    @classmethod
    def validate(cls, params, connected):
        keys = ("half_map_a", "half_map_b", "map", "map_sharp", "mask")
        problems = []
        if not any(params.get(k) for k in keys):
            problems.append("Give at least one map file")
        if bool(params.get("half_map_a")) != bool(params.get("half_map_b")):
            problems.append("Give both half maps")
        return problems

    def run(self, ctx: JobContext) -> None:
        mode = ctx.params["link_mode"]
        apix = float(ctx.params.get("pixel_size") or 0)
        if apix > 0 and mode == "symlink":
            ctx.warn("Pixel size override requires copies: switching to copy mode")
            mode = "copy"
        local: dict[str, Path] = {}
        for key in ("half_map_a", "half_map_b", "map", "map_sharp", "mask"):
            if ctx.params.get(key):
                local[key] = _transfer(ctx, ctx.params[key], mode)
        if apix > 0:
            import mrcfile
            for p in local.values():
                with mrcfile.open(str(p), mode="r+", permissive=True) as mrc:
                    mrc.voxel_size = apix
            ctx.log(f"Voxel size set to {apix} Å")
        resolution = float(ctx.params.get("resolution") or 0)
        if "half_map_a" in local and ctx.params["compute_fsc"]:
            fsc_res = compute_and_report_fsc(ctx, local["half_map_a"], local["half_map_b"], local.get("mask"))
            if resolution <= 0:
                resolution = fsc_res
        meta: dict[str, Any] = {"symmetry": ctx.params.get("symmetry") or "C1"}
        if resolution > 0:
            meta["resolution"] = round(resolution, 3)
            ctx.add_highlight("Resolution", f"{resolution:.2f} Å")
        if "half_map_a" in local:
            ctx.add_output("half_maps", "half_maps", [local["half_map_a"], local["half_map_b"]], "Half maps", meta=meta)
        for key, label in (("map", "Full map"), ("map_sharp", "Sharpened map")):
            if key in local:
                ctx.add_output(key, "map", local[key], label, meta=meta)
        if "mask" in local:
            ctx.add_output("mask", "mask", local["mask"], "Mask", meta=meta)


# ---------------------------------------------------------------- models
def fetch_url(url: str, dest: Path, timeout: float = 120) -> Path:
    req = urllib.request.Request(url, headers={"User-Agent": "CryoPlug"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest, "wb") as fh:
            shutil.copyfileobj(resp, fh)
    except Exception as exc:
        raise JobError(f"Download failed ({url}): {exc}") from None
    return dest


def describe_model(ctx: JobContext, path: Path) -> dict[str, Any]:
    from cryoplug.modelio import read_structure, structure_summary
    st = read_structure(path)
    summary = structure_summary(st)
    ctx.add_metrics("Model content", [
        {"label": "Chains", "value": summary["n_chains"]},
        {"label": "Residues", "value": summary["n_residues"]},
        {"label": "Atoms", "value": summary["n_atoms"]},
        {"label": "Mass", "value": f"{summary['mass_da'] / 1000:.1f} kDa"},
        {"label": "Waters", "value": summary["waters"]},
        {"label": "Ligands", "value": ", ".join(f"{k}×{v}" for k, v in summary["ligands"].items()) or "none"},
    ])
    rows = [[p["chain"], p["type"], p["length"], f"{p['first']}-{p['last']}", p["sequence"][:60] + ("…" if p["length"] > 60 else "")]
            for p in summary["polymers"]]
    if rows:
        ctx.add_table("Polymer chains", ["Chain", "Type", "Length", "Range", "Sequence"], rows)
    return summary


@register
class ImportModel(JobType):
    name = "import_model"
    title = "Import atomic model"
    category = "Import"
    description = ("Import a PDB/mmCIF model from disk, or fetch an entry from the PDB or the AlphaFold database "
                   "(starting model for docking and refinement).")
    params = [
        Param("source", "choice", "file", choices=["file", "pdb", "alphafold"], label="Source"),
        Param("path", "path", "", label="Model file", path_kind="file", help="PDB or mmCIF file (source = file)."),
        Param("accession", "str", "", label="PDB ID / UniProt accession", placeholder="7XYZ or P69905",
              help="PDB ID (source = pdb) or UniProt accession (source = alphafold)."),
    ]
    outputs = [OutputDef("model", "model", "Model"), OutputDef("sequence", "sequence", "Model sequence")]

    @classmethod
    def validate(cls, params, connected):
        if params.get("source") == "file" and not params.get("path"):
            return ["Select a model file"]
        if params.get("source") in ("pdb", "alphafold") and not params.get("accession"):
            return ["Give an accession code"]
        return []

    def run(self, ctx: JobContext) -> None:
        source = ctx.params["source"]
        acc = str(ctx.params.get("accession") or "").strip()
        if source == "file":
            path = _transfer(ctx, ctx.params["path"], "copy")
        elif source == "pdb":
            code = acc.lower()
            if not re.fullmatch(r"[0-9][a-z0-9]{3}", code):
                raise JobError(f"Invalid PDB ID: {acc}")
            path = fetch_url(f"https://files.rcsb.org/download/{code}.cif", ctx.path(f"{code}.cif"))
        else:
            acc = acc.upper()
            api = ctx.path("alphafold_api.json")
            fetch_url(f"https://alphafold.ebi.ac.uk/api/prediction/{acc}", api)
            entries = json.loads(api.read_text())
            if not entries:
                raise JobError(f"No AlphaFold DB entry for {acc}")
            url = entries[0].get("cifUrl") or entries[0].get("pdbUrl")
            path = fetch_url(url, ctx.path(Path(url).name))
            ctx.log(f"AlphaFold DB entry {entries[0].get('entryId', acc)} (model version {entries[0].get('latestVersion', '?')})")
        summary = describe_model(ctx, path)
        meta = {"n_chains": summary["n_chains"], "n_residues": summary["n_residues"], "mass_da": summary["mass_da"],
                "predicted": summary["predicted_like"]}
        ctx.add_output("model", "model", path, "Model", meta=meta)
        ctx.add_highlight("Residues", summary["n_residues"])
        if summary["predicted_like"]:
            ctx.log("B-factor column looks like pLDDT (predicted model): consider 'Process predicted model'.")
        records = [(f"{Path(path).stem}_{p['chain']}", p["sequence"]) for p in summary["polymers"]]
        if records:
            write_sequence_outputs(ctx, records, "sequence", "Model sequence")


# ------------------------------------------------------------- sequences
def write_sequence_outputs(ctx: JobContext, records: list[tuple[str, str]], name: str = "sequence",
                           label: str = "Sequence") -> dict[str, Any]:
    from cryoplug.modelio import classify_sequence, write_fasta
    by_kind: dict[str, list[tuple[str, str]]] = {}
    for rec_name, seq in records:
        by_kind.setdefault(classify_sequence(seq), []).append((rec_name, seq))
    all_path = write_fasta(records, ctx.path(f"{name}.fasta"))
    files = [all_path]
    meta: dict[str, Any] = {"n_records": len(records), "kinds": sorted(by_kind)}
    for kind, recs in by_kind.items():
        p = write_fasta(recs, ctx.path(f"{name}_{kind}.fasta"))
        files.append(p)
        meta[f"{kind}_file"] = ctx.rel(p)
        meta[f"{kind}_residues"] = sum(len(s) for _, s in recs)
    return ctx.add_output(name, "sequence", files, label, meta=meta)


@register
class ImportSequence(JobType):
    name = "import_sequence"
    title = "Import sequence"
    category = "Import"
    description = ("Paste FASTA, read a FASTA file or fetch UniProt entries. Protein, RNA and DNA records are "
                   "separated automatically (ModelAngelo needs them in separate files).")
    params = [
        Param("fasta", "text", "", label="FASTA text", placeholder=">chainA\nMKV...",
              help="One record per distinct chain. Repeat records for hetero-oligomers only once per entity."),
        Param("path", "path", "", label="FASTA file", path_kind="file"),
        Param("uniprot", "str", "", label="UniProt accessions", placeholder="P69905, P68871",
              help="Comma-separated accessions fetched from rest.uniprot.org."),
    ]
    outputs = [OutputDef("sequence", "sequence")]

    @classmethod
    def validate(cls, params, connected):
        if not (params.get("fasta") or params.get("path") or params.get("uniprot")):
            return ["Give FASTA text, a file or UniProt accessions"]
        return []

    def run(self, ctx: JobContext) -> None:
        from cryoplug.modelio import classify_sequence, parse_fasta
        records: list[tuple[str, str]] = []
        if ctx.params.get("fasta"):
            records += parse_fasta(ctx.params["fasta"])
        if ctx.params.get("path"):
            p = Path(ctx.params["path"]).expanduser()
            if not p.is_file():
                raise JobError(f"File not found: {p}")
            records += parse_fasta(p.read_text())
        for acc in [a.strip() for a in str(ctx.params.get("uniprot") or "").split(",") if a.strip()]:
            dest = fetch_url(f"https://rest.uniprot.org/uniprotkb/{acc}.fasta", ctx.path(f"uniprot_{acc}.fasta"))
            records += parse_fasta(dest.read_text())
        if not records:
            raise JobError("No sequence records found")
        out = write_sequence_outputs(ctx, records)
        ctx.add_table("Records", ["Name", "Type", "Length"],
                      [[n, classify_sequence(s), len(s)] for n, s in records])
        ctx.add_highlight("Records", len(records))
        ctx.log(f"Kinds: {', '.join(out['meta']['kinds'])}")


def star_summary(path: Path) -> dict[str, Any]:
    """Particle count, box and pixel size of a RELION .star file (optics table of RELION 3.1+ when present)."""
    info: dict[str, Any] = {}
    block, labels, rows, in_loop = "", [], 0, False
    optics: dict[str, str] = {}
    with open(path, errors="replace") as fh:
        for raw in fh:
            line = raw.strip()
            if line.startswith("data_"):
                block, labels, in_loop = line[5:], [], False
                continue
            if line.startswith("loop_"):
                labels, in_loop = [], True
                continue
            if line.startswith("_"):
                labels.append(line.split()[0])
                continue
            if not line or line.startswith("#") or not in_loop or not labels:
                continue
            if block == "optics" and not optics:
                optics = dict(zip(labels, line.split()))
            elif block in ("particles", ""):
                rows += 1
    info["n_particles"] = rows
    for key, out, cast in (("_rlnImageSize", "box", int), ("_rlnImagePixelSize", "pixel_size", float)):
        if key in optics:
            try:
                info[out] = cast(float(optics[key]))
            except ValueError:
                pass
    return info


@register
class ImportParticles(JobType):
    name = "import_particles"
    title = "Import particles"
    category = "Import"
    description = ("Particles for heterogeneity analysis (cryoDRGN): a CryoSPARC job folder or .cs file (merged with its "
                   "passthrough file), or a RELION .star file. Only the metadata is read: the images stay in place.")
    software = ["CryoSPARC"]
    params = [
        Param("path", "path", "", label="CryoSPARC job folder, .cs or .star file", required=True, path_kind="any",
              help="For CryoSPARC, the job of the consensus refinement (its particles carry the poses)."),
        Param("datadir", "path", "", label="Image folder", path_kind="dir",
              help="Folder the image paths of the metadata are relative to. Empty = the CryoSPARC project folder (.cs), "
                   "or the folder of the .star file."),
        Param("passthrough", "path", "", label="Passthrough .cs", path_kind="file", advanced=True,
              help="Found automatically next to the .cs file (J..._passthrough_particles.cs)."),
    ]
    outputs = [OutputDef("particles", "particles", "Particles"), OutputDef("latent", "latent", "3D variability coordinates")]

    def run(self, ctx: JobContext) -> None:
        from cryoplug.particles import find_particle_files
        src = Path(ctx.params["path"]).expanduser()
        datadir = Path(ctx.params["datadir"]).expanduser() if ctx.params["datadir"] else None
        if src.is_dir():
            main, through = find_particle_files(src)
            if main is None:
                raise JobError(f"No particles .cs file in {src}")
        elif src.suffix.lower() == ".cs" and src.is_file():
            main = src
            through = Path(ctx.params["passthrough"]).expanduser() if ctx.params["passthrough"] else find_particle_files(src.parent)[1]
            if through and through.resolve() == main.resolve():
                through = None
        elif src.suffix.lower() == ".star" and src.is_file():
            self._star(ctx, src, datadir)
            return
        else:
            raise JobError(f"Give a CryoSPARC job folder, a .cs file or a .star file (not found: {src})")
        ctx.log(f"Particles: {main}" + (f" + {through.name}" if through else ""))
        if not import_cs_particles(ctx, main, through, {}, datadir):
            raise JobError("No usable particles found (see the warnings above)")

    @staticmethod
    def _star(ctx: JobContext, src: Path, datadir: Path | None) -> None:
        info = star_summary(src)
        if not info.get("n_particles"):
            raise JobError(f"No particle found in {src.name}")
        dst = _transfer(ctx, src, "copy", "particles.star")
        meta = {**info, "format": "star", "datadir": str(datadir or src.parent), "source": str(src),
                "has_images": True, "has_poses": True, "has_ctf": True}
        ctx.add_output("particles", "particles", dst, f"Particles ({info['n_particles']:,})", meta=meta)
        ctx.add_highlight("Particles", f"{info['n_particles']:,}")
