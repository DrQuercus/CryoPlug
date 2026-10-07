"""Import jobs: CryoSPARC results, maps, atomic models and sequences."""
from __future__ import annotations

import json
import re
import shutil
import urllib.request
from pathlib import Path
from typing import Any

from cryoplug.jobs import register
from cryoplug.jobs.base import JobContext, JobError, JobType, OutputDef, Param, resolution_param
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
                   "sharpened maps, FSC mask, local resolution) by pointing at its job directory, e.g. "
                   "/data/CS-myproject/J245. The latest iteration is selected automatically.")
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
    ]
    outputs = [
        OutputDef("half_maps", "half_maps", "Half maps"),
        OutputDef("map", "map", "Unsharpened map"),
        OutputDef("map_sharp", "map", "Sharpened map"),
        OutputDef("mask", "mask", "FSC mask"),
        OutputDef("map_locres", "locres", "Local resolution map"),
    ]

    def run(self, ctx: JobContext) -> None:
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
        if not found:
            raise JobError(f"No map files recognised in {src}")
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
        ctx.add_table("Imported files", ["Kind", "File", "Source"], rows)


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
