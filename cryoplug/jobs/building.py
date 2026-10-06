"""Model building jobs: ModelAngelo, structure prediction, predicted-model processing and docking."""
from __future__ import annotations

import json
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


def pick_new_model(ctx: JobContext, since: float, preferred: list[str], root: Path | None = None,
                   exclude: tuple[str, ...] = ()) -> Path:
    candidates = [p for p in ctx.find_new_files(MODEL_PATTERNS, since, root)
                  if not p.is_symlink() and not any(x in p.name for x in exclude)]
    for pattern in preferred:
        for p in candidates:
            if p.match(pattern):
                return p
    if candidates:
        return candidates[0]
    raise JobError("The program finished but no output model was found")


def report_model(ctx: JobContext, path: Path, title: str = "Model content") -> dict:
    from cryoplug.jobs.imports import describe_model
    summary = describe_model(ctx, path)
    ctx.add_highlight("Residues", summary["n_residues"])
    return summary


def sequence_files(ctx: JobContext, seq) -> dict[str, str]:
    """FASTA file per molecule kind (protein / rna / dna) of a sequence input."""
    files = {kind: str(ctx.abs(seq.meta[f"{kind}_file"])) for kind in ("protein", "rna", "dna") if seq.meta.get(f"{kind}_file")}
    return files or {"protein": seq.files[0]}


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
    inputs = [Slot("map", ("map",), "Map"), Slot("sequence", ("sequence",), "Sequence", required=False)]
    params = [
        Param("device", "str", "", label="CUDA device(s)", advanced=True,
              help="Value for --device (index inside the GPUs given to the job). Empty = automatic."),
        Param("legacy_fasta_flag", "bool", False, label="Old ModelAngelo (-f)", advanced=True,
              help="ModelAngelo < 1.0 used -f instead of -pf for the protein FASTA."),
        extra_args_param(),
    ]
    outputs = [OutputDef("model", "model", "ModelAngelo model")]

    def run(self, ctx: JobContext) -> None:
        m = ctx.require("map")
        seq = ctx.input("sequence")
        out_name = "modelangelo"
        exe = ctx.executable("modelangelo")
        if seq:
            args = [exe, "build", "-v", m.path, "-o", out_name]
            flags = {"protein": "-f" if ctx.params["legacy_fasta_flag"] else "-pf", "rna": "-rf", "dna": "-df"}
            for kind, path in sequence_files(ctx, seq).items():
                args += [flags[kind], path]
        else:
            args = [exe, "build_no_seq", "-v", m.path, "-o", out_name]
        args += ["--device", ",".join(ctx.relative_gpus(ctx.params.get("device", "")))]
        args += ctx.split_extra()
        start = time.time()
        ctx.run(args, tool="modelangelo")
        out = first_existing([ctx.path(out_name, f"{out_name}.cif"), ctx.path(f"{out_name}.cif")])
        if out is None:
            out = pick_new_model(ctx, start, ["*.cif"], root=ctx.path(out_name), exclude=("raw",))
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
        args = [ctx.executable("cryoatom"), "build", "-v", m.path, "-o", out_name]
        seq = ctx.input("sequence")
        if seq:
            flags = {"protein": "-ps", "rna": "-rs", "dna": "-ds"}
            for kind, path in sequence_files(ctx, seq).items():
                args += [flags[kind], path]
        for slot, flag in (("mask", "-m"), ("backbone", "-r")):
            inp = ctx.input(slot)
            if inp:
                args += [flag, inp.path]
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
        out = first_existing([ctx.path(out_name, f"{out_name}.cif")])
        if out is None:
            out = pick_new_model(ctx, start, ["*.cif"], root=ctx.path(out_name), exclude=("raw", "model_net"))
        report_model(ctx, out)
        ctx.add_output("model", "model", out, "CryoAtom2 model", inherit=["map"])
        raw = first_existing([ctx.path(out_name, f"{out_name}_raw.cif")])
        if raw:
            ctx.add_output("model_raw", "model", raw, "CryoAtom2 raw model (all built residues)", inherit=["map"])
        identified = _sequences_from([ctx.path(out_name, f"{out_name}_prot.fasta"), ctx.path(out_name, f"{out_name}_na.fasta")])
        if identified:
            from cryoplug.jobs.imports import write_sequence_outputs
            write_sequence_outputs(ctx, identified, "sequence", "Identified sequences")
            ctx.add_table("Identified sequences", ["Record", "Length"], [[n[:70], len(sq)] for n, sq in identified])
            ctx.log("Rebuild with the identified sequences (connect the 'sequence' output to a new CryoAtom2 build) "
                    "until no new sequence appears.")
        for xlsx in sorted(ctx.path(out_name).glob("new_hits_*.xlsx")):
            ctx.log(f"Search results: {xlsx}")
        quick_cc(ctx, m.path, out, m.meta.get("resolution"))


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
        ctx.run([ctx.executable("modelangelo"), "hmm_search", "-i", str(model_dir), "-f", str(db), "-o", "hmm_output",
                 "-a", ctx.params["alphabet"], "--E", str(ctx.params["evalue"]), *ctx.split_extra()], tool="modelangelo")
        best_csv = first_existing([ctx.path("hmm_output", "best_hits.csv"), model_dir / "best_hits.csv"])
        all_csv = first_existing([ctx.path("hmm_output", "all_hits.csv"), model_dir / "all_hits.csv"])
        if best_csv is None:
            raise JobError("best_hits.csv was not produced (see log)")
        with open(best_csv, newline="") as fh:
            hits = list(csv.DictReader(fh))
        hits.sort(key=lambda r: float(r.get("E-value") or 1e9))
        ctx.add_table("Best hits", ["Target", "Chain", "E-value", "Score", "Description"],
                      [[r.get("target_name", ""), r.get("query_name", ""), f"{float(r.get('E-value') or 0):.2e}",
                        f"{float(r.get('score') or 0):.1f}", (r.get("description") or "")[:80]] for r in hits[:40]])
        selected = [r["target_name"] for r in hits if float(r.get("E-value") or 1e9) <= ctx.params["seq_evalue"]]
        selected = selected[: ctx.params["max_sequences"]]
        ctx.add_highlight("Hits", len(hits))
        report_files = [p for p in (best_csv, all_csv) if p]
        ctx.add_output("report", "report", report_files, "HMM search hits", meta={"n_hits": len(hits)})
        if not selected:
            ctx.warn(f"No hit below E-value {ctx.params['seq_evalue']:g}: no sequence output.")
            return
        records = fetch_fasta_records(db, selected)
        from cryoplug.jobs.imports import write_sequence_outputs
        write_sequence_outputs(ctx, records, "sequence", "Identified sequences")
        ctx.add_highlight("Identified", len(records), "good")


# ------------------------------------------------------------------ ColabFold
@register
class ColabFoldPredict(JobType):
    name = "colabfold_predict"
    title = "AlphaFold2 prediction (ColabFold)"
    category = "Model building"
    tool = "colabfold"
    software = ["ColabFold", "AlphaFold2"]
    gpu = 1
    description = ("Predict starting models with a local ColabFold installation. Records can be predicted separately "
                   "or as one complex (sequences joined with ':').")
    inputs = [Slot("sequence", ("sequence",), "Sequence")]
    params = [
        Param("as_complex", "bool", True, label="Predict as a complex"),
        Param("num_models", "int", 5, label="Models", min=1, max=5),
        Param("num_recycle", "int", 3, label="Recycles", min=0),
        Param("templates", "bool", False, label="Use templates"),
        Param("amber", "bool", False, label="Amber relaxation"),
        extra_args_param(),
    ]
    outputs = [OutputDef("model", "model", "Top-ranked model")]

    def run(self, ctx: JobContext) -> None:
        from cryoplug.modelio import classify_sequence, parse_fasta, write_fasta
        seq = ctx.require("sequence")
        records = [(n, s) for n, s in parse_fasta(Path(seq.files[0]).read_text()) if classify_sequence(s) == "protein"]
        if not records:
            raise JobError("No protein sequence to predict")
        if ctx.params["as_complex"] and len(records) > 1:
            records = [("complex", ":".join(s for _, s in records))]
        fasta = write_fasta([(n.split()[0].replace("|", "_"), s) for n, s in records], ctx.path("colabfold_input.fasta"))
        args = [ctx.executable("colabfold"), str(fasta), "colabfold_out", "--num-models", str(ctx.params["num_models"]),
                "--num-recycle", str(ctx.params["num_recycle"])]
        if ctx.params["templates"]:
            args.append("--templates")
        if ctx.params["amber"]:
            args += ["--amber", "--use-gpu-relax"]
        args += ctx.split_extra()
        ctx.run(args, tool="colabfold")
        out_dir = ctx.path("colabfold_out")
        n = 0
        for name, _ in records:
            safe = name.split()[0].replace("|", "_")
            hits = sorted(out_dir.glob(f"{safe}*rank_001*.pdb"))
            if ctx.params["amber"]:
                hits = [p for p in hits if "_relaxed_" in p.name] or hits
            if not hits:
                continue
            n += 1
            out_name = "model" if n == 1 else f"model_{n}"
            ctx.add_output(out_name, "model", hits[0], f"ColabFold {safe} (rank 1)", meta={"predicted": True})
        if n == 0:
            raise JobError("No ranked model found in colabfold_out")
        ctx.add_highlight("Models", n)


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
        kind = kind.strip().lower()
        if not sep or kind not in ("ccd", "smiles") or not value.strip():
            raise ValueError(f"Ligand line '{raw}' must look like CCD:ATP, SMILES:CCO or 2xCCD:MG")
        out.append((copies, kind, value.strip()))
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
        return path

    def run(self, ctx: JobContext) -> None:
        yaml_path = self.write_input(ctx)
        args = [ctx.executable("boltz"), "predict", str(yaml_path), "--out_dir", "boltz_out", "--output_format", "mmcif",
                "--diffusion_samples", str(ctx.params["diffusion_samples"]),
                "--recycling_steps", str(ctx.params["recycling_steps"])]
        if ctx.params["use_msa_server"]:
            args.append("--use_msa_server")
        if ctx.params["use_potentials"]:
            args.append("--use_potentials")
        args += ctx.split_extra()
        ctx.run(args, tool="boltz")
        models = sorted(ctx.path("boltz_out").rglob("*_model_*.cif"),
                        key=lambda p: int(p.stem.rsplit("_", 1)[-1]) if p.stem.rsplit("_", 1)[-1].isdigit() else 99)
        if not models:
            raise JobError("No predicted model found in boltz_out")
        for i, mpath in enumerate(models):
            ctx.add_output("model" if i == 0 else f"model_{i + 1}", "model", mpath, f"Boltz-2 rank {i + 1}",
                           meta={"predicted": True})
        conf = next(iter(models[0].parent.glob(f"confidence_{models[0].stem}.json")), None)
        if conf:
            data = json.loads(conf.read_text())
            labels = {"confidence_score": "Confidence score", "complex_plddt": "Complex pLDDT", "ptm": "pTM",
                      "iptm": "ipTM", "complex_iplddt": "Interface pLDDT", "ligand_iptm": "Ligand ipTM"}
            rows = [{"label": lab, "value": f"{float(data[k]):.3f}"} for k, lab in labels.items() if k in data]
            ctx.add_metrics("Prediction confidence (rank 1)", rows)
            if "iptm" in data:
                ctx.add_highlight("ipTM", f"{float(data['iptm']):.2f}")
            if "complex_plddt" in data:
                ctx.add_highlight("pLDDT", f"{float(data['complex_plddt']):.2f}")


# ------------------------------------------------------------ Phenix helpers
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
        plddt = float(ctx.params["minimum_plddt"])
        args = [ctx.program("phenix", "phenix.process_predicted_model"), model.path,
                f"b_value_field_is={ctx.params['b_value_field_is']}",
                f"minimum_plddt={plddt / 100.0 if plddt > 1 else plddt:.2f}",
                f"split_model_by_compact_regions={ctx.params['split_by_domains']}",
                f"maximum_domains={ctx.params['maximum_domains']}", *ctx.split_extra()]
        start = time.time()
        ctx.run(args, tool="phenix")
        out = pick_new_model(ctx, start, ["*_processed.pdb", "*_processed.cif", "*processed*"])
        report_model(ctx, out)
        ctx.add_output("model", "model", out, "Processed predicted model", inherit=["model"])
        for i, extra in enumerate(sorted(ctx.find_new_files(["*_processed_*.pdb"], start)), start=1):
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
        ctx.run([ctx.executable("chimerax"), "--nogui", "--exit", "--script", str(ctx.path("fitmap.py"))], tool="chimerax")
        if not out.exists():
            raise JobError("ChimeraX did not write the fitted model (see log)")
        ctx.add_output("model", "model", out, "Fitted model", inherit=["map"])
        cc = quick_cc(ctx, m.path, out, res)
        if cc is not None:
            ctx.add_metrics("Fit", [{"label": "Model-map CC (mask)", "value": f"{cc:.3f}"}])
        (ctx.path("fit_result.json")).write_text(json.dumps({"cc_mask": cc}))
