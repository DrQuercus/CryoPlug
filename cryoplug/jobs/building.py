"""Model building jobs: ModelAngelo, structure prediction, predicted-model processing and docking."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from cryoplug.jobs import register
from cryoplug.jobs.base import (
    JobContext,
    JobError,
    JobType,
    OutputDef,
    Param,
    Slot,
    extra_args_param,
    first_existing,
    resolution_param,
)

MODEL_PATTERNS = ["*.pdb", "*.cif", "*.mmcif"]
MODEL_SUFFIXES = (".pdb", ".cif", ".mmcif")


def pick_new_model(ctx: JobContext, since: float, preferred: list[str], root: Path | None = None,
                   exclude: tuple[str, ...] = ()) -> Path:
    """Locate the model written by an external program under ``root`` (default: the job folder), or next to
    the job's inputs. ``exclude`` applies to the path inside the job folder."""
    def candidates() -> list[Path]:
        return [p for p in ctx.find_new_files(MODEL_PATTERNS, since, root)
                if not p.is_symlink() and not any(x in ctx.rel(p) for x in exclude)]
    found = candidates()
    if not found and ctx.rescue_stray_files(preferred, since, suffixes=MODEL_SUFFIXES, dest=root):
        found = candidates()
    for pattern in preferred:
        for p in found:
            if p.match(pattern):
                return p
    if found:
        return found[0]
    where = ctx.rel(root) if root else f"{ctx.job_dir.name}/"
    raise JobError(f"The program finished but wrote no model in {where} (expected {', '.join(preferred)}). "
                   "It may have stopped on an error without returning a failure code: see the log.")


def report_model(ctx: JobContext, path: Path, title: str = "Model content") -> dict:
    """Content of a model written by a job (empty if it cannot be read). Informative only: the job keeps its
    output even when the built-in reader fails on a file the program that wrote it reads fine."""
    from cryoplug.jobs.imports import describe_model
    try:
        summary = describe_model(ctx, path)
    except Exception as exc:
        ctx.warn(f"Model content not summarised ({path.name}): {exc}")
        return {}
    ctx.add_highlight("Residues", summary["n_residues"])
    return summary


def _number(value, default: float | None = None) -> float | None:
    """A number read from a program's output file, or ``default`` if the value is missing or not a number."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def sequence_files(ctx: JobContext, seq, unique: bool = False) -> dict[str, str]:
    """FASTA file per molecule kind (protein / rna / dna) of a sequence input. ``unique``: each sequence once (written
    into the job folder), as CryoAtom2 asks (a repeated chain disturbs its sequence assignment)."""
    files = {kind: str(ctx.abs(seq.meta[f"{kind}_file"])) for kind in ("protein", "rna", "dna") if seq.meta.get(f"{kind}_file")}
    files = files or {"protein": seq.files[0]}
    if not unique:
        return files
    from cryoplug.modelio import parse_fasta, write_fasta
    out = {}
    for kind, path in files.items():
        records = parse_fasta(Path(path).read_text())
        seen: set[str] = set()
        kept = [(n, sq) for n, sq in records if not (sq.upper() in seen or seen.add(sq.upper()))]
        if len(kept) < len(records):
            ctx.log(f"{len(records) - len(kept)} repeated {kind} sequence(s) given once")
        out[kind] = str(write_fasta(kept, ctx.path(f"input_{kind}.fasta")))
    return out


def map_file_for(ctx: JobContext, path: str, name: str, suffixes: tuple[str, ...] = (".mrc", ".map")) -> str:
    """The map itself when its name ends as the program wants, otherwise a link to it with that suffix (CryoAtom2
    refuses .ccp4 files)."""
    if path.lower().endswith(suffixes):
        return path
    return str(ctx.link_input(path, f"{name}{suffixes[0]}"))


def quick_cc(ctx: JobContext, map_path: str, model_path: Path, resolution: float | None) -> float | None:
    """Model-map correlation inside the model envelope (cheap built-in check)."""
    try:
        from cryoplug.analysis import cc_mask
        from cryoplug.modelio import read_structure
        from cryoplug.mrc import MapVolume
        if not resolution:
            return None
        cc = cc_mask(MapVolume.read(map_path), read_structure(model_path), resolution)
        ctx.add_highlight("CC (mask)", f"{cc:.3f}", "good" if cc >= 0.7 else "warn" if cc >= 0.5 else "bad")
        return cc
    except Exception as exc:  # pragma: no cover - informative only
        ctx.warn(f"Model-map correlation not computed: {exc}")
        return None


# ---------------------------------------------------------------- ModelAngelo
@register
class ModelAngeloBuild(JobType):
    name = "modelangelo_build"
    title = "ModelAngelo build"
    category = "Model building"
    tool = "modelangelo"
    software = ["ModelAngelo"]
    gpu = 1
    cpus = 4
    description = ("Automated model building (Jamali et al. 2024). With a sequence: 'build' (protein, RNA and DNA "
                   "FASTA are passed separately). Without: 'build_no_seq', which also writes HMM profiles to search "
                   "sequence databases for unknown proteins. Use the best sharpened / post-processed map.")
    inputs = [Slot("map", ("map",), "Map"), Slot("sequence", ("sequence",), "Sequence", required=False),
              Slot("mask", ("mask",), "Mask", required=False, help="Build only inside this region (-m), e.g. one subcomplex.")]
    params = [
        Param("device", "str", "", label="CUDA device(s)", advanced=True,
              help="Value for --device (index inside the GPUs given to the job). Empty = automatic."),
        Param("legacy_fasta_flag", "bool", False, label="Old ModelAngelo (-f)", advanced=True,
              help="ModelAngelo < 1.0 used -f instead of -pf for the protein FASTA."),
        extra_args_param(),
    ]
    outputs = [OutputDef("model", "model", "ModelAngelo model"), OutputDef("model_raw", "model", "Raw model (no pruning)")]

    def run(self, ctx: JobContext) -> None:
        m = ctx.require("map")
        seq = ctx.input("sequence")
        out_name = "modelangelo"
        exe = ctx.executable("modelangelo")
        out_dir = str(ctx.path(out_name))  # absolute: never resolved against another folder
        if seq:
            files = sequence_files(ctx, seq)
            if "protein" not in files:  # build requires -pf
                raise JobError("ModelAngelo 'build' needs a protein sequence (-pf). For RNA or DNA only, use CryoAtom2 "
                               "(it takes -rs / -ds alone), or run ModelAngelo without a sequence.")
            args = [exe, "build", "-v", m.path, "-o", out_dir]
            flags = {"protein": "-f" if ctx.params["legacy_fasta_flag"] else "-pf", "rna": "-rf", "dna": "-df"}
            for kind, path in files.items():
                args += [flags[kind], path]
        else:
            args = [exe, "build_no_seq", "-v", m.path, "-o", out_dir]
        mask = ctx.input("mask")
        if mask:
            args += ["-m", mask.path]
        args += ["--device", ",".join(ctx.relative_gpus(ctx.params.get("device", "")))]
        args += ctx.split_extra()
        start = time.time()
        ctx.run(args, tool="modelangelo")
        out = first_existing([ctx.path(out_name, f"{out_name}.cif"), ctx.path(f"{out_name}.cif")])
        if out is None:
            out = pick_new_model(ctx, start, [f"{out_name}.cif", "*.cif"],
                                 exclude=("raw", "entropy_score", "gnn_output", "see_alpha_output"))
        report_model(ctx, out)
        ctx.add_output("model", "model", out, "ModelAngelo model", inherit=["map"])
        raw = first_existing([ctx.path(out_name, f"{out_name}_raw.cif")])
        if raw:
            ctx.add_output("model_raw", "model", raw, "ModelAngelo raw model (no pruning)", inherit=["map"])
        hmm = ctx.path(out_name, "hmm_profiles")
        if hmm.is_dir():
            ctx.log(f"HMM profiles written to {hmm}: use 'ModelAngelo sequence search' to identify the chains.")
        quick_cc(ctx, m.path, out, m.meta.get("resolution"))


# ------------------------------------------------------------- CryoAtom2
def _sequences_from(paths: list[Path]) -> list[tuple[str, str]]:
    from cryoplug.modelio import parse_fasta
    records: list[tuple[str, str]] = []
    for p in paths:
        if p.is_file():
            records += parse_fasta(p.read_text())
    return records


@register
class CryoAtomBuild(JobType):
    name = "cryoatom_build"
    title = "CryoAtom2 build"
    category = "Model building"
    tool = "cryoatom"
    software = ["CryoAtom2"]
    gpu = 1
    cpus = 4
    description = ("Automated building of proteins, RNA/DNA and their complexes (CryoAtom2, Su et al. 2025). With a "
                   "sequence: sequence-guided building. Without: residue types are assigned from the density; give a "
                   "sequence database (e.g. the UniProt proteome of your organism) to identify the chains, then rebuild "
                   "with the 'Identified sequences' output. A mask restricts building to one region.")
    inputs = [
        Slot("map", ("map",), "Map"),
        Slot("sequence", ("sequence",), "Sequence", required=False,
             help="Each unique chain once; protein, RNA and DNA records are passed separately (-ps / -rs / -ds)."),
        Slot("mask", ("mask",), "Mask", required=False, help="Build only inside this region (-m)."),
        Slot("backbone", ("model",), "Backbone to identify", required=False,
             help="Cα / P trace of a chain to identify against the databases (-r)."),
    ]
    params = [
        Param("protein_db", "path", "", label="Protein sequence database", path_kind="file",
              help="FASTA database used to identify protein chains (-pf), e.g. a UniProt reference proteome."),
        Param("na_db", "path", "", label="Nucleic-acid sequence database", path_kind="file",
              help="FASTA database used to identify RNA/DNA chains (-nf)."),
        Param("device", "str", "", label="CUDA device(s)", advanced=True,
              help="Value for -d (index inside the GPUs given to the job, e.g. 0 or 0,1). Empty = automatic."),
        Param("config", "path", "", label="config.json", advanced=True, path_kind="file",
              help="Custom CryoAtom2 parameters (num_rounds, thresholds...)."),
        Param("keep_intermediate", "bool", False, label="Keep intermediate results", advanced=True),
        extra_args_param(),
    ]
    outputs = [OutputDef("model", "model", "CryoAtom2 model"), OutputDef("model_raw", "model", "Raw model"),
               OutputDef("sequence", "sequence", "Identified sequences")]

    def run(self, ctx: JobContext) -> None:
        m = ctx.require("map")
        out_name = "cryoatom"
        # CryoAtom2 reads maps and masks named .mrc / .map only
        args = [ctx.executable("cryoatom"), "build", "-v", map_file_for(ctx, m.path, "input_map"), "-o", str(ctx.path(out_name))]
        seq = ctx.input("sequence")
        given: list[tuple[str, str]] = []
        if seq:
            flags = {"protein": "-ps", "rna": "-rs", "dna": "-ds"}
            for kind, path in sequence_files(ctx, seq, unique=True).items():
                args += [flags[kind], path]
                given += _sequences_from([Path(path)])
        mask, backbone = ctx.input("mask"), ctx.input("backbone")
        if mask and backbone:
            ctx.warn("With a backbone to identify (-r), CryoAtom2 skips the density step: the mask is not used")
        if mask:
            from cryoplug.mrc import MapVolume
            mvol, mmask = MapVolume.read(m.path, header_only=True), MapVolume.read(mask.path, header_only=True)
            if tuple(mvol.shape_xyz) != tuple(mmask.shape_xyz):
                raise JobError(f"The mask ({mmask.shape_xyz}) must have the box of the map ({mvol.shape_xyz}): CryoAtom2 "
                               "multiplies them voxel by voxel")
            args += ["-m", map_file_for(ctx, mask.path, "input_mask")]
        if backbone:
            bb = backbone.path
            if not bb.lower().endswith((".pdb", ".cif")):  # CryoAtom2 reads the type from the last 3 letters
                import gemmi
                st = gemmi.read_structure(bb)
                bb = str(ctx.path("input_backbone.cif"))
                st.make_mmcif_document().write_file(bb)
            args += ["-r", bb]
        for key, flag in (("protein_db", "-pf"), ("na_db", "-nf"), ("config", "-c")):
            if ctx.params.get(key):
                if not Path(ctx.params[key]).is_file():
                    raise JobError(f"File not found: {ctx.params[key]}")
                args += [flag, ctx.params[key]]
        args += ["-d", ",".join(ctx.relative_gpus(ctx.params.get("device", "")))]
        if ctx.params["keep_intermediate"]:
            args.append("-k")
        args += ctx.split_extra()
        start = time.time()
        ctx.run(args, tool="cryoatom")
        out = first_existing([ctx.path(out_name, f"{out_name}.cif"), ctx.path(f"{out_name}.cif")])
        if out is None:
            out = pick_new_model(ctx, start, [f"{out_name}.cif", "*.cif"],
                                 exclude=("raw", "model_net", "entropy_score", "see_alpha_output", "CryoNet_round_"))
        report_model(ctx, out)
        ctx.add_output("model", "model", out, "CryoAtom2 model", inherit=["map"])
        raw = first_existing([ctx.path(out_name, f"{out_name}_raw.cif")])
        if raw:
            ctx.add_output("model_raw", "model", raw, "CryoAtom2 raw model (all built residues)", inherit=["map"])
        found_prot = _sequences_from([ctx.path(out_name, f"{out_name}_prot.fasta")])
        # CryoAtom2 searches nucleic acids as RNA (T read as U) but writes the database text as it is
        found_na = [(n, sq.upper().replace("T", "U")) for n, sq in _sequences_from([ctx.path(out_name, f"{out_name}_na.fasta")])]
        identified = _unique_records(given + found_prot + found_na) if (found_prot or found_na) else []
        if identified:
            from cryoplug.jobs.imports import write_sequence_outputs
            write_sequence_outputs(ctx, identified, "sequence", "Identified sequences")
            ctx.add_table("Identified sequences", ["Record", "Length"], [[n[:70], len(sq)] for n, sq in identified])
            ctx.log("Rebuild with the identified sequences (connect the 'sequence' output to a new CryoAtom2 build) "
                    "until no new sequence appears.")
        for xlsx in sorted(ctx.path(out_name).glob("new_hits_*.xlsx")):
            ctx.log(f"Search results: {xlsx}")
        quick_cc(ctx, m.path, out, m.meta.get("resolution"))


def _unique_records(records: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Records with each sequence once (the first name kept): the input chains and the new hits together."""
    seen: set[str] = set()
    return [(n, sq) for n, sq in records if not (sq.upper() in seen or seen.add(sq.upper()))]


# ------------------------------------------------- ModelAngelo HMM search
def fetch_fasta_records(database: Path, names: list[str]) -> list[tuple[str, str]]:
    """Stream a (possibly large) FASTA file and return the records whose first header token is in ``names``."""
    wanted = {n: i for i, n in enumerate(names)}
    found: dict[str, tuple[str, str]] = {}
    header, chunks = None, []

    def flush() -> None:
        if header is not None:
            key = header.split()[0]
            if key in wanted and key not in found:
                found[key] = (header, "".join(chunks).replace("*", "").upper())

    with open(database, errors="replace") as fh:
        for line in fh:
            if line.startswith(">"):
                flush()
                header, chunks = line[1:].strip(), []
                if len(found) == len(wanted):
                    break
            elif header is not None:
                chunks.append(line.strip())
        else:
            flush()
    return [found[n] for n in names if n in found]


@register
class ModelAngeloHMMSearch(JobType):
    name = "modelangelo_hmm_search"
    title = "Identify chains (ModelAngelo HMM search)"
    category = "Model building"
    tool = "modelangelo"
    software = ["ModelAngelo", "HMMER"]
    cpus = 4
    description = ("Identify unknown proteins (or RNA/DNA) in the map: the HMM profiles written by 'ModelAngelo build' "
                   "without a sequence are searched against a sequence database (model_angelo hmm_search). The best "
                   "hits become a sequence output to rebuild the model with ModelAngelo or CryoAtom2.")
    inputs = [Slot("model", ("model",), "ModelAngelo model (no sequence)",
                   help="Model from a ModelAngelo build run WITHOUT sequence (its folder contains hmm_profiles).")]
    params = [
        Param("database", "path", "", label="Sequence database (FASTA)", required=True, path_kind="file",
              help="Uncompressed FASTA, e.g. the UniProt reference proteome of the organism."),
        Param("alphabet", "choice", "amino", choices=["amino", "RNA", "DNA"], label="Alphabet"),
        Param("seq_evalue", "float", 1e-5, label="E-value cutoff for output sequences"),
        Param("max_sequences", "int", 10, label="Maximum sequences kept", min=1),
        Param("evalue", "float", 10.0, label="Reporting E-value (--E)", advanced=True),
        extra_args_param(),
    ]
    outputs = [OutputDef("sequence", "sequence", "Identified sequences"), OutputDef("report", "report", "Search hits")]

    def run(self, ctx: JobContext) -> None:
        import csv
        model = ctx.require("model")
        model_dir = Path(model.path).resolve().parent
        if not (model_dir / "hmm_profiles").is_dir():
            raise JobError("No hmm_profiles next to the input model: run 'ModelAngelo build' without a sequence first.")
        db = Path(ctx.params["database"])
        if not db.is_file():
            raise JobError(f"Database not found: {db}")
        out_dir = ctx.path("hmm_output")
        ctx.run([ctx.executable("modelangelo"), "hmm_search", "-i", str(model_dir), "-f", str(db), "-o", str(out_dir),
                 "-a", ctx.params["alphabet"], "--E", str(ctx.params["evalue"]), *ctx.split_extra()], tool="modelangelo")
        if not (out_dir / "best_hits.csv").exists():  # written beside the input profiles instead?
            ctx.rescue_stray_files(["best_hits.csv", "all_hits.csv"], dest=out_dir)
        best_csv = first_existing([out_dir / "best_hits.csv"])
        all_csv = first_existing([out_dir / "all_hits.csv"])
        if best_csv is None:
            raise JobError("best_hits.csv was not produced (see the log)")
        with open(best_csv, newline="") as fh:
            hits = list(csv.DictReader(fh))
        def evalue(r: dict) -> float:
            return _number(r.get("E-value"), 1e9)
        hits.sort(key=evalue)
        ctx.add_table("Best hits", ["Target", "Chain", "E-value", "Score", "Description"],
                      [[r.get("target_name", ""), r.get("query_name", ""), f"{evalue(r):.2e}",
                        f"{_number(r.get('score'), 0.0):.1f}", (r.get("description") or "")[:80]] for r in hits[:40]])
        selected = [r["target_name"] for r in hits if r.get("target_name") and evalue(r) <= ctx.params["seq_evalue"]]
        selected = selected[: ctx.params["max_sequences"]]
        ctx.add_highlight("Hits", len(hits))
        report_files = [p for p in (best_csv, all_csv) if p]
        ctx.add_output("report", "report", report_files, "HMM search hits", meta={"n_hits": len(hits)})
        if not selected:
            ctx.warn(f"No hit below E-value {ctx.params['seq_evalue']:g}: no sequence output.")
            return
        records = fetch_fasta_records(db, selected)
        if ctx.params["alphabet"] == "RNA":  # the database may be written with T: the hits are RNA
            records = [(n, sq.replace("T", "U")) for n, sq in records]
        elif ctx.params["alphabet"] == "DNA":
            records = [(n, sq.replace("U", "T")) for n, sq in records]
        from cryoplug.jobs.imports import write_sequence_outputs
        write_sequence_outputs(ctx, records, "sequence", "Identified sequences")
        ctx.add_highlight("Identified", len(records), "good")


# ------------------------------------------------------------------ ColabFold
def colabfold_name(text: str) -> str:
    """ColabFold's safe_filename: letters, digits, '_', '.', '-' kept, anything else becomes '_'."""
    return "".join(c if c.isalnum() or c in "_.-" else "_" for c in text)


def copies_list(text: str) -> list[int]:
    """'1,1,2' -> [1, 1, 2] (copies of each record, in order)."""
    try:
        return [int(c) for c in str(text or "").replace(" ", "").split(",") if c]
    except ValueError:
        raise JobError("'Copies per record' must be comma-separated integers") from None


@register
class ColabFoldPredict(JobType):
    name = "colabfold_predict"
    title = "AlphaFold2 prediction (ColabFold)"
    category = "Model building"
    tool = "colabfold"
    software = ["ColabFold", "AlphaFold2"]
    gpu = 1
    description = ("Predict starting models with a local ColabFold installation (AlphaFold2). Records can be predicted "
                   "separately or as one complex (with copies for homo-oligomers). The MSAs come from the ColabFold "
                   "server unless single-sequence mode is chosen (confidential sequences).")
    inputs = [Slot("sequence", ("sequence",), "Sequence")]
    params = [
        Param("as_complex", "bool", True, label="Predict as a complex"),
        Param("copies", "str", "", label="Copies per record", placeholder="1,1,2",
              help="Number of copies of each FASTA record in the complex, in order (e.g. 2 for a homodimer). Empty = 1 each."),
        Param("msa_mode", "choice", "server", choices=["server", "single_sequence"], label="MSA",
              help="server: MSAs built by the ColabFold server (sends the sequences to api.colabfold.com). "
                   "single_sequence: nothing leaves the server, much lower accuracy."),
        Param("num_models", "int", 5, label="Models", min=1, max=5),
        Param("num_recycle", "int", 0, label="Recycles", min=0, max=48,
              help="0 = ColabFold's choice: 3 for a single chain, up to 20 (stopping when converged) for a complex."),
        Param("templates", "bool", False, label="Use templates"),
        Param("amber", "bool", False, label="Amber relaxation", help="Relaxes the top-ranked model only (--num-relax 1)."),
        extra_args_param(),
    ]
    outputs = [OutputDef("model", "model", "Top-ranked model")]

    def run(self, ctx: JobContext) -> None:
        from cryoplug.modelio import classify_sequence, parse_fasta, write_fasta
        seq = ctx.require("sequence")
        records = [(n, s) for n, s in parse_fasta(Path(seq.files[0]).read_text()) if classify_sequence(s) == "protein"]
        if not records:
            raise JobError("No protein sequence to predict")
        copies = copies_list(ctx.params.get("copies"))
        if ctx.params["as_complex"] and (len(records) > 1 or (copies and copies[0] > 1)):
            chains = [s for i, (_, s) in enumerate(records) for _ in range(max(1, copies[i] if i < len(copies) else 1))]
            records = [("complex", ":".join(chains))]
        # one unique, already safe job name per record: ColabFold names its files after it
        jobs = [(f"r{i + 1}_{colabfold_name(n.split()[0] if n.split() else 'seq')[:40]}", n, s) for i, (n, s) in enumerate(records)]
        fasta = write_fasta([(job, s) for job, _, s in jobs], ctx.path("colabfold_input.fasta"))
        out_dir = ctx.path("colabfold_out")
        args = [ctx.executable("colabfold"), str(fasta), str(out_dir), "--num-models", str(ctx.params["num_models"])]
        if ctx.params["num_recycle"] > 0:
            args += ["--num-recycle", str(ctx.params["num_recycle"])]
        if ctx.params["msa_mode"] == "single_sequence":
            args += ["--msa-mode", "single_sequence"]
        else:
            ctx.log("The sequences are sent to the ColabFold MSA server (api.colabfold.com)")
        if ctx.params["templates"]:
            args.append("--templates")
        if ctx.params["amber"]:
            args += ["--amber", "--num-relax", "1", "--use-gpu-relax"]
        args += ctx.split_extra()
        ctx.run(args, tool="colabfold")
        missing, rows = [], []
        for n, (job, name, _s) in enumerate(jobs, 1):
            hits = sorted(out_dir.glob(f"{job}_relaxed_rank_001_*.pdb")) or sorted(out_dir.glob(f"{job}_unrelaxed_rank_001_*.pdb"))
            if not hits:
                missing.append(name.split()[0] if name.split() else job)
                continue
            scores = self._scores(out_dir, job)
            meta = {"predicted": True, **{k: round(v, 3) for k, v in scores.items()}}
            ctx.add_output("model" if n == 1 else f"model_{n}", "model", hits[0], f"ColabFold {name.split()[0] if name.split() else job} (rank 1)",
                           meta=meta)
            rows.append([name[:50], *(f"{scores[k]:.2f}" if k in scores else "–" for k in ("plddt", "ptm", "iptm", "max_pae"))])
            if n == 1 and scores:
                ctx.add_highlight("pLDDT", f"{scores['plddt']:.0f}", "good" if scores["plddt"] >= 70 else "warn")
                key = "iptm" if "iptm" in scores else "ptm" if "ptm" in scores else None
                if key:
                    ctx.add_highlight("ipTM" if key == "iptm" else "pTM", f"{scores[key]:.2f}")
        if rows:
            ctx.add_table("Prediction confidence (rank 1)", ["Record", "Mean pLDDT", "pTM", "ipTM", "Max PAE (Å)"], rows)
        if missing:
            raise JobError(f"ColabFold wrote no ranked model for {', '.join(missing)} (see the log: out of GPU memory?)")

    @staticmethod
    def _scores(out_dir: Path, job: str) -> dict[str, float]:
        """Mean pLDDT, pTM, ipTM and largest PAE of the top-ranked model ({job}_scores_rank_001_*.json)."""
        f = next(iter(sorted(out_dir.glob(f"{job}_scores_rank_001_*.json"))), None)
        if f is None:
            return {}
        try:
            data = json.loads(f.read_text())
        except (OSError, ValueError):
            return {}
        out: dict[str, float] = {}
        plddt = data.get("plddt")
        if isinstance(plddt, list) and plddt:
            out["plddt"] = float(sum(plddt) / len(plddt))
        for k in ("ptm", "iptm", "max_pae"):
            v = _number(data.get(k))
            if v is not None:
                out[k] = v
        return out


# ---------------------------------------------------------------- Boltz-2
def _chain_ids():
    import string
    for c in string.ascii_uppercase:
        yield c
    for a in string.ascii_uppercase:
        for b in string.ascii_uppercase:
            yield a + b


def _yaml_quote(text: str) -> str:
    return "'" + str(text).replace("'", "''") + "'"


def parse_ligand_lines(text: str) -> list[tuple[int, str, str]]:
    """'2xCCD:MG' / 'SMILES:CCO' lines -> [(copies, kind, value)]."""
    out = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        copies = 1
        head, sep, rest = line.partition("x")
        if sep and head.isdigit() and ":" in rest:
            copies, line = int(head), rest
        kind, sep, value = line.partition(":")
        kind, value = kind.strip().lower(), value.strip()
        if not sep or kind not in ("ccd", "smiles") or not value:
            raise ValueError(f"Ligand line '{raw}' must look like CCD:ATP, SMILES:CCO or 2xCCD:MG")
        if kind == "ccd":  # Boltz finds components by their upper-case code only
            value = value.upper()
            if not re.fullmatch(r"[A-Z0-9]{1,5}", value):
                raise ValueError(f"Ligand line '{raw}': a CCD code has 1 to 5 letters or digits (e.g. ATP, MG, NAG)")
        out.append((copies, kind, value))
    return out


@register
class BoltzPredict(JobType):
    name = "boltz_predict"
    title = "Complex prediction (Boltz-2)"
    category = "Model building"
    tool = "boltz"
    software = ["Boltz-2"]
    gpu = 1
    description = ("Predict a starting model of the whole complex — proteins, RNA/DNA and ligands (CCD code or SMILES) — "
                   "with a local Boltz-2 installation (open AlphaFold3-class model), then fit it into the map.")
    inputs = [Slot("sequence", ("sequence",), "Sequences", help="One FASTA record per unique chain.")]
    params = [
        Param("copies", "str", "", label="Copies per record", placeholder="1,1,2",
              help="Number of copies of each FASTA record, in order (e.g. 2 for a homodimer). Empty = 1 each."),
        Param("ligands", "text", "", label="Ligands", placeholder="CCD:ATP\n2xCCD:MG\nSMILES:CC(=O)O",
              help="One ligand per line: CCD:<code> or SMILES:<smiles>, optional 'Nx' prefix for copies."),
        Param("use_msa_server", "bool", True, label="Build MSAs with the ColabFold server",
              help="Sends the sequences to api.colabfold.com. Disable for confidential sequences (single-sequence "
                   "mode, lower accuracy)."),
        Param("use_potentials", "bool", True, label="Inference-time potentials (physical plausibility)"),
        Param("method", "choice", "electron microscopy", choices=["electron microscopy", "x-ray diffraction", "none"],
              label="Condition on the experimental method",
              help="Boltz-2 can predict structures as determined by a method (--method): cryo-EM structures by default; "
                   "none = no conditioning."),
        Param("seed", "int", 0, label="Random seed", min=0, advanced=True, help="0 = random; a fixed seed reproduces a run."),
        Param("diffusion_samples", "int", 1, label="Samples", min=1),
        Param("recycling_steps", "int", 3, label="Recycling steps", min=0),
        extra_args_param(),
    ]
    outputs = [OutputDef("model", "model", "Top-ranked model")]

    @classmethod
    def validate(cls, params, connected):
        try:
            parse_ligand_lines(params.get("ligands", ""))
        except ValueError as exc:
            return [str(exc)]
        return []

    def write_input(self, ctx: JobContext) -> Path:
        from cryoplug.modelio import classify_sequence, parse_fasta
        records = parse_fasta(Path(ctx.require("sequence").files[0]).read_text())
        if not records:
            raise JobError("Empty sequence input")
        copies_txt = [c.strip() for c in str(ctx.params.get("copies") or "").split(",") if c.strip()]
        try:
            copies = [int(c) for c in copies_txt]
        except ValueError:
            raise JobError("'Copies per record' must be comma-separated integers") from None
        ids = _chain_ids()
        lines = ["version: 1", "sequences:"]
        for i, (name, sq) in enumerate(records):
            n = copies[i] if i < len(copies) else 1
            kind = classify_sequence(sq)
            chain = [next(ids) for _ in range(max(1, n))]
            lines += [f"  - {kind}:", f"      id: [{', '.join(chain)}]", f"      sequence: {sq.replace(':', '')}"]
            if kind == "protein" and not ctx.params["use_msa_server"]:
                lines.append("      msa: empty")
            ctx.log(f"Chain(s) {', '.join(chain)}: {name[:60]} ({kind}, {len(sq)} residues)")
        for n, kind, value in parse_ligand_lines(ctx.params.get("ligands", "")):
            chain = [next(ids) for _ in range(n)]
            lines += ["  - ligand:", f"      id: [{', '.join(chain)}]", f"      {kind}: {_yaml_quote(value)}"]
            ctx.log(f"Ligand {', '.join(chain)}: {kind.upper()} {value}")
        path = ctx.path("boltz_input.yaml")
        path.write_text("\n".join(lines) + "\n")
        self.n_chains = sum(ln.count(",") + 1 for ln in lines if ln.strip().startswith("id: ["))
        return path

    def run(self, ctx: JobContext) -> None:
        yaml_path = self.write_input(ctx)
        args = [ctx.executable("boltz"), "predict", str(yaml_path), "--out_dir", str(ctx.path("boltz_out")), "--output_format", "mmcif",
                "--diffusion_samples", str(ctx.params["diffusion_samples"]),
                "--recycling_steps", str(ctx.params["recycling_steps"])]
        if ctx.params["use_msa_server"]:
            args.append("--use_msa_server")
        if ctx.params["use_potentials"]:
            args.append("--use_potentials")
        if ctx.params["method"] != "none":
            args += ["--method", ctx.params["method"]]
        if ctx.params["seed"] > 0:
            args += ["--seed", str(ctx.params["seed"])]
        args += ctx.split_extra()
        problems: list[str] = []

        def watch(line: str) -> None:  # Boltz reports these and still exits with 0
            if "Failed to process" in line or "ran out of memory" in line:
                problems.append(line.strip())

        ctx.run(args, tool="boltz", on_line=watch)
        models = sorted(ctx.path("boltz_out").rglob("*_model_*.cif"),
                        key=lambda p: int(p.stem.rsplit("_", 1)[-1]) if p.stem.rsplit("_", 1)[-1].isdigit() else 99)
        if not models:
            raise JobError(("Boltz: " + problems[-1]) if problems else "No predicted model found in boltz_out")
        for line in problems:
            ctx.warn(f"Boltz: {line}")
        for i, mpath in enumerate(models):
            ctx.add_output("model" if i == 0 else f"model_{i + 1}", "model", mpath, f"Boltz-2 rank {i + 1}",
                           meta={"predicted": True})
        conf = next(iter(models[0].parent.glob(f"confidence_{models[0].stem}.json")), None)
        try:
            data = json.loads(conf.read_text()) if conf else {}
        except (OSError, ValueError) as exc:
            ctx.warn(f"Confidence scores not read ({conf.name}): {exc}")
            data = {}
        if isinstance(data, dict) and data:
            labels = {"confidence_score": "Confidence score", "complex_plddt": "Complex pLDDT", "ptm": "pTM",
                      "iptm": "ipTM", "complex_iplddt": "Interface pLDDT", "ligand_iptm": "Ligand ipTM"}
            values = {k: _number(data.get(k)) for k in labels}
            rows = [{"label": lab, "value": f"{values[k]:.3f}"} for k, lab in labels.items() if values[k] is not None]
            ctx.add_metrics("Prediction confidence (rank 1)", rows)
            chains = len(data.get("chains_ptm") or {}) or self.n_chains
            if chains > 1 and values["iptm"] is not None:  # ipTM only means something between chains
                ctx.add_highlight("ipTM", f"{values['iptm']:.2f}")
            elif values["ptm"] is not None:
                ctx.add_highlight("pTM", f"{values['ptm']:.2f}")
            if values["complex_plddt"] is not None:  # 0-1 in the JSON, 0-100 in the model's B-factors
                plddt = 100 * values["complex_plddt"] if values["complex_plddt"] <= 1 else values["complex_plddt"]
                ctx.add_highlight("pLDDT", f"{plddt:.0f}", "good" if plddt >= 70 else "warn")


# ------------------------------------------------------------ Phenix helpers
def plddt_cutoff_args(model_path: str, minimum: float) -> list[str]:
    """process_predicted_model arguments trimming residues below ``minimum`` pLDDT (0-100, or 0-1).

    Phenix reads minimum_plddt on the scale of the B-factor column (0-1 or 0-100) and only uses it once its default
    cutoff, maximum_rmsd = 1.5 Å, is cleared. Unreadable model: Phenix's default (about pLDDT 70) is kept."""
    try:
        from cryoplug.modelio import read_structure
        b = [cra.atom.b_iso for cra in read_structure(model_path)[0].all()]
    except Exception:
        return []
    if not b:
        return []
    fractional = min(b) >= 0 and max(b) <= 1
    cutoff = minimum / 100.0 if minimum > 1 else minimum
    return ["maximum_rmsd=None", f"input_plddt_is_fractional={fractional}",
            f"minimum_plddt={cutoff if fractional else cutoff * 100:g}"]


@register
class PhenixProcessPredictedModel(JobType):
    name = "phenix_process_predicted_model"
    title = "Process predicted model (Phenix)"
    category = "Model building"
    tool = "phenix"
    software = ["Phenix process_predicted_model"]
    description = ("Trim low-confidence residues of an AlphaFold/ColabFold model, convert pLDDT to B-factors and "
                   "optionally split into compact domains for docking (phenix.process_predicted_model).")
    inputs = [Slot("model", ("model",), "Predicted model")]
    params = [
        Param("minimum_plddt", "float", 70.0, label="Minimum pLDDT", min=0, max=100),
        Param("b_value_field_is", "choice", "plddt", choices=["plddt", "rmsd"], label="B-factor column contains"),
        Param("split_by_domains", "bool", False, label="Split into compact domains"),
        Param("maximum_domains", "int", 3, label="Maximum domains", min=1),
        extra_args_param(),
    ]
    outputs = [OutputDef("model", "model", "Processed model")]

    def run(self, ctx: JobContext) -> None:
        model = ctx.require("model")
        args = [ctx.program("phenix", "phenix.process_predicted_model"), model.path,
                f"b_value_field_is={ctx.params['b_value_field_is']}"]
        if ctx.params["b_value_field_is"] == "plddt":
            args += plddt_cutoff_args(model.path, float(ctx.params["minimum_plddt"]))
        args += [f"split_model_by_compact_regions={ctx.params['split_by_domains']}",
                 f"maximum_domains={ctx.params['maximum_domains']}", *ctx.split_extra()]
        start = time.time()
        ctx.run(args, tool="phenix")
        out = pick_new_model(ctx, start, ["*_processed.pdb", "*_processed.cif", "*processed*"])
        report_model(ctx, out)
        ctx.add_output("model", "model", out, "Processed predicted model", inherit=["model"])
        for i, extra in enumerate(sorted(ctx.find_new_files(["*_processed_*.pdb", "*_processed_*.cif"], start)), start=1):
            if extra != out:
                ctx.add_output(f"domain_{i}", "model", extra, f"Domain {i}", inherit=["model"])


@register
class PhenixDockInMap(JobType):
    name = "phenix_dock_in_map"
    title = "Dock in map (Phenix)"
    category = "Model building"
    tool = "phenix"
    software = ["Phenix dock_in_map"]
    cpus = 4
    cpu_param = "nproc"
    description = "Global search for the position of a model (or domains) in the map (phenix.dock_in_map)."
    inputs = [Slot("model", ("model",), "Model"), Slot("map", ("map",), "Map")]
    params = [resolution_param(), Param("nproc", "int", 4, label="Processors", min=1), extra_args_param()]
    outputs = [OutputDef("model", "model", "Docked model")]

    def run(self, ctx: JobContext) -> None:
        model, m = ctx.require("model"), ctx.require("map")
        res = ctx.resolution(slots=["map", "model"])
        args = [ctx.program("phenix", "phenix.dock_in_map"), model.path, m.path, f"resolution={res}",
                f"nproc={ctx.params['nproc']}", *ctx.split_extra()]
        start = time.time()
        ctx.run(args, tool="phenix")
        out = pick_new_model(ctx, start, ["placed_model*", "*docked*", "*placed*"])
        ctx.add_output("model", "model", out, "Docked model", inherit=["map"])
        quick_cc(ctx, m.path, out, res)


# ------------------------------------------------------------------ ChimeraX
def fitmap_script(map_path: str, model_path: str, output: str, resolution: float, search: int,
                  placement: str, radius: float) -> str:
    return f"""
from chimerax.core.commands import run
MAP, MODEL, OUTPUT = {map_path!r}, {model_path!r}, {output!r}
mp = run(session, 'open "%s"' % MAP)[0]
mdl = run(session, 'open "%s"' % MODEL)[0]
cmd = 'fitmap #%s inMap #%s resolution {resolution} metric cor' % (mdl.id_string, mp.id_string)
if {search} > 0:
    cmd += ' search {search} placement {placement} radius {radius} clusterAngle 6 clusterShift 3 listFits false'
session.logger.info(cmd)
run(session, cmd)
run(session, 'save "%s" models #%s relModel #%s' % (OUTPUT, mdl.id_string, mp.id_string))
"""


@register
class ChimeraXFit(JobType):
    name = "chimerax_fitmap"
    title = "Rigid-body fit (ChimeraX)"
    category = "Model building"
    tool = "chimerax"
    software = ["UCSF ChimeraX"]
    description = ("Rigid-body fit of a model into the map with ChimeraX 'fitmap' using a simulated map at the map "
                   "resolution. Local optimisation from the current position, or a global search.")
    inputs = [Slot("model", ("model",), "Model"), Slot("map", ("map",), "Map")]
    params = [
        resolution_param(),
        Param("search", "int", 0, label="Global search placements", min=0,
              help="0 = local optimisation only; e.g. 200 for a global search."),
        Param("placement", "choice", "sr", choices=["sr", "s", "r"], label="Search moves",
              help="sr = shift and rotate, s = shift only, r = rotate only.", advanced=True),
        Param("radius", "float", 0.0, label="Search radius", unit="Å", min=0, advanced=True,
              help="Limit the global search to this distance from the starting position (0 = whole map)."),
    ]
    outputs = [OutputDef("model", "model", "Fitted model")]

    def run(self, ctx: JobContext) -> None:
        model, m = ctx.require("model"), ctx.require("map")
        res = ctx.resolution(slots=["map", "model"])
        out = ctx.path("fitted_model.cif")
        radius = ctx.params["radius"] or 1e6
        script = fitmap_script(m.path, model.path, str(out), res, int(ctx.params["search"]),
                               ctx.params["placement"], radius)
        ctx.path("fitmap.py").write_text(script)
        # the script by name (it runs in the job folder): ChimeraX reads text after a space in --script as its arguments
        ctx.run([ctx.executable("chimerax"), "--nogui", "--exit", "--script", "fitmap.py"], tool="chimerax")
        if not out.exists():
            raise JobError("ChimeraX did not write the fitted model (see log)")
        ctx.add_output("model", "model", out, "Fitted model", inherit=["map"])
        cc = quick_cc(ctx, m.path, out, res)
        if cc is not None:
            ctx.add_metrics("Fit", [{"label": "Model-map CC (mask)", "value": f"{cc:.3f}"}])
        (ctx.path("fit_result.json")).write_text(json.dumps({"cc_mask": cc}))
