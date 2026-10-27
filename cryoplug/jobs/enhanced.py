"""Automated model building improved by search: CryoAtom2 enhanced.

CryoAtom2 builds differently at each run (residues are visited in a random order), its settings trade completeness
against false residues, and the map it reads matters. Its authors suggest running it several times and keeping the
best model on difficult maps, raising the rounds at low resolution, lowering the Cα/P thresholds for more residues,
rebuilding with the identified sequences until none is new, and flipping a map whose helices come out in short pieces.
This job does all of that by itself and scores every model against the experimental map
(:mod:`cryoplug.modelsearch`): residues fitted to the density, counted once where the density is traced twice.

It can also improve the map with the model and build again, the cycle LocScale's authors describe (Jakobi et al.
2017: scaling, building and refinement iterated for successively better maps and models): the parts of the best model
fitted to the density sharpen the half maps (LocScale hybrid or model-based, Phenix model-based sharpening), CryoAtom2
builds into the new map, and the cycle goes on while the score against the experimental map improves. Methods that do
not use a model (EMmerNet, model-free LocScale, DeepEMhancer, EMReady2, Phenix density modification) give one more map
to build into.
"""
from __future__ import annotations

import copy
import json
import math
import re
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

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
from cryoplug.jobs.building import _sequences_from, map_file_for, report_model, sequence_files

# CryoAtom2 2.1.1 defaults (CryoAtom2/config.json). "threshold" is the name of the Cα threshold before 2.1.
CRYOATOM_CONFIG: dict[str, dict[str, Any]] = {
    "RUNet_args": {"batch_size": 4, "stride": 50, "windows_size": 65, "ca_threshold": 0.6, "p_threshold": 0.465},
    "CryoNet_args": {"num_rounds": 3, "repeat_per_residue": 3, "crop_length": 300, "aggressive_repeat": True,
                     "raw_filter": False, "filter_threshold": 50, "mask_threshold": 0.3, "seq_attention_batch_size": -1},
    "HMM_search": {"confidence_threshold": 25, "Evalue": 1, "cpus": 4},
}
PRESETS: dict[str, tuple[str, str, dict[str, dict[str, Any]]]] = {
    "standard": ("Standard", "the settings CryoAtom2 ships with", {}),
    "more_residues": ("More residues", "Cα / P probability thresholds lowered to 0.5 / 0.4: more residues, also in weak "
                      "density", {"RUNet_args": {"ca_threshold": 0.5, "p_threshold": 0.4, "threshold": 0.5}}),
    "low_resolution": ("Low resolution", "4 rounds, each residue visited 4 times (CryoAtom2 manual, for low-resolution "
                       "maps)", {"CryoNet_args": {"num_rounds": 4, "repeat_per_residue": 4}}),
    "strict": ("Strict", "Cα / P thresholds raised to 0.7 / 0.55: fewer residues built in noise",
               {"RUNet_args": {"ca_threshold": 0.7, "p_threshold": 0.55, "threshold": 0.7}}),
}
SETTING_SETS = {
    "standard": ["standard"],
    "standard + more residues": ["standard", "more_residues"],
    "standard + low resolution": ["standard", "low_resolution"],
    "all": ["standard", "more_residues", "low_resolution", "strict"],
}
FLAGS = {"protein": "-ps", "rna": "-rs", "dna": "-ds"}
ALT_MAPS = 3
# Map improvement: (label, uses the model). Those using the model are cycled: sharpen with the best model, rebuild.
IMPROVE_METHODS: dict[str, tuple[str, bool]] = {
    "locscale_hybrid": ("LocScale hybrid", True),
    "locscale_model_based": ("LocScale model-based", True),
    "phenix_model_sharpening": ("Phenix model-based sharpening", True),
    "emmernet": ("EMmerNet", False),
    "locscale_model_free": ("LocScale model-free", False),
    "deepemhancer": ("DeepEMhancer", False),
    "emready": ("EMReady2", False),
    "phenix_density_modification": ("Phenix density modification", False),
}


def merged(base: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for section, values in changes.items():
        out.setdefault(section, {}).update(values)
    return out


def auto_settings(resolution: float) -> list[str]:
    """Beyond 3.5 Å the rounds are raised (manual); at better resolution more residues are tried for."""
    return ["standard", "low_resolution"] if resolution > 3.5 else ["standard", "more_residues"]


def parse_bfactors(text: str) -> list[float]:
    values = []
    for item in re.split(r"[,;\s]+", str(text or "").strip()):
        if item:
            values.append(float(item))  # ValueError: reported by validate
    return [b for b in dict.fromkeys(values) if b]


def consensus_hits(found: list[list[tuple[str, str]]]) -> list[tuple[str, str]]:
    """Sequences identified by at least half of the runs of a round (a hit of one run in four is left out)."""
    counts: Counter[str] = Counter()
    names: dict[str, str] = {}
    for records in found:
        for name, seq in {s.upper(): (n, s.upper()) for n, s in records}.values():
            counts[seq] += 1
            names.setdefault(seq, name)
    need = max(1, math.ceil(len(found) / 2))
    return [(names[s], s) for s, c in counts.items() if c >= need]


@register
class CryoAtomEnhanced(JobType):
    name = "cryoatom_enhanced"
    title = "CryoAtom2 enhanced (best of several runs)"
    category = "Model building"
    tool = "cryoatom"
    software = ["CryoAtom2"]
    gpu = 1
    gpu_param = "gpus"
    cpus = 4
    description = ("Runs CryoAtom2 several times (it builds differently each time) with several settings, and "
                   "optionally on re-sharpened or other maps; scores every model against the experimental map (residues "
                   "fitted to the density by Q-score, the same density traced twice counted once) and keeps the best, "
                   "with its gaps filled by well-fitted pieces of the other runs. With sequence databases, identifies the "
                   "chains and rebuilds with them until no new sequence appears. Can improve the map with the best model "
                   "(LocScale hybrid or model-based, Phenix) and build again while the score improves, or try other "
                   "sharpening methods (EMmerNet, DeepEMhancer, EMReady2, density modification). Checks the hand of the "
                   "map when the model comes out in short pieces.")
    inputs = [
        Slot("map", ("map",), "Map", help="The experimental map to build into: every model is scored against it."),
        Slot("half_maps", ("half_maps",), "Half maps", required=False,
             help="Unfiltered half maps, for the map improvement (LocScale and density modification read them)."),
        Slot("sequence", ("sequence",), "Sequence", required=False,
             help="Each unique chain once; protein, RNA and DNA records are passed separately."),
        Slot("mask", ("mask",), "Mask", required=False, help="Build only inside this region (same box as the map)."),
        *(Slot(f"alt_map_{i}", ("map",), f"Other map {i}", required=False, group="alt",
               help="Another map of the same particle to build into as well (LocScale, EMReady, density modification...): "
                    "its models are scored against the experimental map.") for i in range(1, ALT_MAPS + 1)),
    ]
    params = [
        resolution_param(),
        Param("settings", "choice", "auto", choices=["auto", *SETTING_SETS], label="Settings to try",
              help="auto: standard and 'low resolution' (4 rounds) beyond 3.5 Å, standard and 'more residues' (lower Cα/P "
                   "thresholds) otherwise. 'all' adds 'strict' (higher thresholds, fewer residues in noise)."),
        Param("runs", "int", 2, label="Runs per setting", min=1, max=10,
              help="CryoAtom2 visits the residues in a random order: each run differs, and the best is kept (as its "
                   "authors suggest on difficult maps)."),
        Param("bfactors", "str", "", label="Also try re-sharpened maps", unit="Å²", placeholder="-30, 40",
              help="B-factors applied to the map to build into as well, with the best setting: negative sharpens (then "
                   "low-pass filtered at the resolution), positive blurs."),
        Param("max_runs", "int", 12, label="Maximum runs", min=1, max=60,
              help="Limit on the runs of the first round; each takes minutes to an hour on a GPU (about 30 min for "
                   "5000 residues on an A100)."),
        Param("combine", "bool", True, label="Fill gaps from the other runs",
              help="Add to the best model the pieces of the other runs that fit the density where it has nothing "
                   "(kept only if the score improves)."),
        Param("check_hand", "choice", "auto", choices=["auto", "always", "never"], label="Check the hand",
              help="auto: when most of the model is in short pieces (helices broken up: the sign of a mirrored map), "
                   "build once into the mirrored map and compare."),
        Param("improve_map", "multichoice", [], choices=list(IMPROVE_METHODS), label="Improve the map and build again",
              labels={k: v[0] for k, v in IMPROVE_METHODS.items()},
              help="Model-based methods (LocScale hybrid or model-based, Phenix model-based sharpening) sharpen the half "
                   "maps with the parts of the best model fitted to the density, then CryoAtom2 builds into the new map, "
                   "for as many cycles as the score improves. The others (EMmerNet, LocScale model-free, DeepEMhancer, "
                   "EMReady2, Phenix density modification) give one more map to build into. All models are scored "
                   "against the experimental map."),
        Param("improve_rounds", "int", 2, label="Map-model cycles", min=1, max=5,
              help="At most this many sharpen-and-build cycles per model-based method."),
        Param("protein_db", "path", "", label="Protein sequence database", path_kind="file",
              help="FASTA database to identify the protein chains (e.g. the UniProt proteome of the organism); the job "
                   "then rebuilds with the sequences found until no new one appears."),
        Param("na_db", "path", "", label="Nucleic-acid sequence database", path_kind="file",
              help="FASTA database to identify RNA / DNA chains."),
        Param("identify_rounds", "int", 3, label="Identification rounds", min=1, max=6, advanced=True,
              help="Rebuilds with the identified sequences, at most this many times."),
        Param("gpus", "int", 1, label="GPUs", min=1, max=8, advanced=True,
              help="GPUs given to each run (CryoAtom2 2.1 shares a run between them)."),
        Param("config", "path", "", label="Base config.json", advanced=True, path_kind="file",
              help="The settings are applied on top of this CryoAtom2 config instead of the 2.1.1 defaults."),
        Param("keep_runs", "bool", True, label="Keep every run's model", advanced=True),
        extra_args_param(),
    ]
    outputs = [
        OutputDef("model", "model", "Best model"),
        OutputDef("model_raw", "model", "Raw model of the best run"),
        OutputDef("sequence", "sequence", "Sequences (with the identified ones)"),
        OutputDef("candidates", "files", "Models of every run"),
        OutputDef("map_improved", "map", "Improved map (the best model's)"),
        OutputDef("map_other_hand", "map", "Map in the other hand"),
        OutputDef("model_other_hand", "model", "Model in the other hand"),
    ]

    @classmethod
    def validate(cls, params: dict[str, Any], connected: dict[str, str]) -> list[str]:
        problems = []
        try:
            parse_bfactors(params.get("bfactors", ""))
        except ValueError:
            problems.append("Re-sharpened maps: give B-factors as numbers separated by commas, e.g. -30, 40")
        if "phenix_density_modification" in (params.get("improve_map") or []) and "half_maps" not in connected:
            problems.append("Phenix density modification needs the half maps")
        return problems

    # ------------------------------------------------------------------ run
    def run(self, ctx: JobContext) -> None:
        from cryoplug import modelsearch as ms
        from cryoplug.modelio import read_structure, write_structure
        from cryoplug.mrc import MapVolume, flip_hand, fourier_filter

        self.ctx = ctx
        m = ctx.require("map")
        resolution = ctx.resolution(slots=["map", "alt_map_1"])
        self.ref = MapVolume.read(m.path)
        self.resolution = resolution
        self.ms, self.read_structure = ms, read_structure
        self.exe = ctx.executable("cryoatom")
        self.base_config = self._base_config()
        self.runs_dir = ctx.path("runs")
        self.runs_dir.mkdir(exist_ok=True)
        self.candidates: list[dict[str, Any]] = []
        self._links: dict[str, str] = {}  # maps linked as .mrc for CryoAtom2
        self.done = 0
        self.mask = ctx.input("mask")
        self.dbs = []
        for key, flag in (("protein_db", "-pf"), ("na_db", "-nf")):
            if ctx.params.get(key):
                if not Path(ctx.params[key]).is_file():
                    raise JobError(f"File not found: {ctx.params[key]}")
                self.dbs += [flag, ctx.params[key]]

        # Sequences by kind (protein / rna / dna), each once
        self.records: dict[str, list[tuple[str, str]]] = {}
        seq = ctx.input("sequence")
        if seq:
            for kind, path in sequence_files(ctx, seq, unique=True).items():
                self.records[kind] = _sequences_from([Path(path)])

        # The maps to build into
        maps = [{"label": "map", "short": "experimental", "path": m.path, "note": "experimental map"}]
        for i in range(1, ALT_MAPS + 1):
            alt = ctx.input(f"alt_map_{i}")
            if alt:
                maps.append({"label": f"other {i}", "short": f"other {i}", "path": alt.path,
                             "note": alt.label or Path(alt.path).name})
        for b in parse_bfactors(ctx.params["bfactors"]):
            data = fourier_filter(self.ref, bfactor=b, lowpass=resolution if b < 0 else 0.0)
            path = self.ref.like(data).write(ctx.path(f"map_B{b:+g}.mrc"))
            maps.append({"label": f"B {b:+g}", "short": f"B {b:+g} Å²", "path": str(path),
                         "note": f"map {'sharpened' if b < 0 else 'blurred'} by B = {b:+g} Å²"})

        settings = SETTING_SETS.get(ctx.params["settings"]) or auto_settings(resolution)
        runs = int(ctx.params["runs"])
        first = [(maps[0], s, r) for s in settings for r in range(1, runs + 1)]
        later = [(mp, None, r) for mp in maps[1:] for r in range(1, runs + 1)]  # the best setting, once known
        limit = int(ctx.params["max_runs"])
        if len(first) + len(later) > limit:
            ctx.warn(f"{len(first) + len(later)} runs planned, {limit} at most (Maximum runs): the last ones are left out")
            later = later[: max(0, limit - len(first))]
            first = first[:limit]
        self.planned = len(first) + len(later)
        ctx.log(f"CryoAtom2 enhanced: {self.planned} runs to start with — settings {', '.join(settings)} × {runs} run(s)"
                + (f", then {len(later)} on {len(maps) - 1} other map(s)" if later else "")
                + f"; models scored against {Path(m.path).name} at {resolution:g} Å (fitted: residue Q ≥ "
                  f"{ms.fitted_cutoff(resolution):.2f})")

        sequences = self.sequences = self._write_sequences(1)
        round1 = [c for c in (self._build(mp, s, r, sequences, 1) for mp, s, r in first) if c]
        if not round1:
            raise JobError("No CryoAtom2 run produced a model (see the log)")
        best_setting = self._best(round1)["setting"]
        round1 += [c for c in (self._build(mp, best_setting, r, sequences, 1) for mp, _, r in later) if c]
        best = self._best(round1)
        ctx.log(f"Round 1: best run {best['label']} ({self._summary(best)})")

        if ctx.params["combine"] and len(round1) > 1:
            best = self._combine(best, round1, write_structure) or best

        # Identify the chains in databases, rebuild with what was found, until nothing new appears
        last_round, rounds = round1, 1
        if self.dbs:
            for rnd in range(2, int(ctx.params["identify_rounds"]) + 2):
                new = self._new_sequences(last_round)
                if not new:
                    ctx.log("No new sequence identified: the identification has converged")
                    break
                for kind, recs in new.items():
                    self.records.setdefault(kind, []).extend(recs)
                    ctx.log(f"Round {rnd}: rebuilding with {len(recs)} new {kind} sequence(s): "
                            + ", ".join(n[:40] for n, _ in recs))
                self.planned += runs
                sequences = self.sequences = self._write_sequences(rnd)
                mp = {x["label"]: x for x in maps}.get(best["map"], maps[0])
                last_round = [c for c in (self._build(mp, best["setting"], r, sequences, rnd) for r in range(1, runs + 1)) if c]
                rounds = rnd
                if not last_round:
                    ctx.warn(f"Round {rnd}: no run produced a model")
                    break
                top = self._best(last_round)
                if top["score"]["score"] > best["score"]["score"]:
                    best = top
                    if ctx.params["combine"] and len(last_round) > 1:
                        best = self._combine(best, last_round, write_structure) or best
                ctx.log(f"Round {rnd}: best run {top['label']} ({self._summary(top)})")

        self.round = rounds
        if ctx.params["improve_map"]:
            best = self._improve(best, maps, write_structure)

        # The hand of the map
        hand = "not checked"
        mode = ctx.params["check_hand"]
        if mode == "always" or (mode == "auto" and ms.wrong_hand_suspected(best["score"])):
            if mode == "auto":
                ctx.warn(f"{best['score']['short_fraction']:.0%} of the residues are in pieces shorter than {ms.SHORT}: "
                         "is the map in the wrong hand? Building once into the mirrored map")
            hand = self._check_hand(best, maps[0], flip_hand, MapVolume)

        self._report(best, rounds, hand)

    # --------------------------------------------------------------- helpers
    def _base_config(self) -> dict[str, Any]:
        path = self.ctx.params.get("config")
        if not path:
            return CRYOATOM_CONFIG
        try:
            return json.loads(Path(path).read_text())
        except (OSError, ValueError) as exc:
            raise JobError(f"Cannot read the base config {path}: {exc}") from None

    def _write_sequences(self, rnd: int) -> dict[str, str]:
        from cryoplug.modelio import write_fasta
        files = {}
        for kind, recs in self.records.items():
            if recs:
                files[kind] = str(write_fasta(recs, self.ctx.path(f"round{rnd}_{kind}.fasta")))
        return files

    def _mask_for(self, map_path: str) -> str | None:
        """The mask, when it has the box of this map (CryoAtom2 multiplies them voxel by voxel)."""
        if not self.mask:
            return None
        from cryoplug.mrc import MapVolume
        vol, mask = MapVolume.read(map_path, header_only=True), MapVolume.read(self.mask.path, header_only=True)
        if tuple(vol.shape_xyz) != tuple(mask.shape_xyz):
            self.ctx.warn(f"The mask ({mask.shape_xyz}) does not have the box of {Path(map_path).name} ({vol.shape_xyz}): "
                          "not used for this map")
            return None
        return map_file_for(self.ctx, self.mask.path, "input_mask")

    def _build(self, mp: dict[str, Any], setting: str, run: int, sequences: dict[str, str], rnd: int,
               label: str | None = None, mask_path: str | None | bool = True, ref: Any = None) -> dict[str, Any] | None:
        ctx, ms = self.ctx, self.ms
        label = label or f"r{rnd}_{re.sub(r'[^A-Za-z0-9+-]+', '', mp['label'])}_{setting}_{run}"
        folder = self.runs_dir / label
        folder.mkdir(exist_ok=True)
        config = merged(self.base_config, PRESETS[setting][2])
        (folder / "config.json").write_text(json.dumps(config, indent=1))
        out = folder / "cryoatom"
        map_path = self._readable(mp)
        args = [self.exe, "build", "-v", map_path, "-o", str(out), "-c", str(folder / "config.json")]
        for kind, path in sequences.items():
            args += [FLAGS[kind], path]
        mask = self._mask_for(map_path) if mask_path is True else (mask_path or None)
        if mask:
            args += ["-m", mask]
        args += self.dbs
        args += ["-d", ",".join(ctx.relative_gpus())] + ctx.split_extra()
        index = self.done
        ctx.progress(index / max(self.planned, 1), f"Run {index + 1}/{self.planned}: {PRESETS[setting][0].lower()} settings on "
                     f"the {mp['note']} " + (f"(run {run}, round {rnd})" if rnd else "(hand check)"))

        def on_line(line: str) -> None:
            part = None
            if "Stage1" in line:
                part = 0.05
            elif (hit := re.search(r"round (\d+) / (\d+)", line)):
                part = 0.1 + 0.8 * (int(hit.group(1)) - 1) / max(1, int(hit.group(2)))
            elif "hmmer_search" in line:
                part = 0.92
            if part is not None:
                ctx.progress((index + part) / max(self.planned, 1), None, log=False)

        try:
            ctx.run(args, tool="cryoatom", on_line=on_line)
        except JobError as exc:
            ctx.warn(f"Run {label} failed: {exc}")
            self.done += 1
            return None
        self.done += 1
        model = first_existing([out / "cryoatom.cif"]) or next(iter(sorted(out.glob("*.cif"))), None)
        if model is None:
            ctx.warn(f"Run {label}: no model written")
            return None
        try:
            st = self.read_structure(model)
            score = ms.score_model(st, ref if ref is not None else self.ref, self.resolution)
        except Exception as exc:  # an unreadable model is one bad run, not the end of the search
            ctx.warn(f"Run {label}: model not scored ({exc})")
            return None
        found_prot = _sequences_from([out / "cryoatom_prot.fasta"])
        found_na = [(n, s.upper().replace("T", "U")) for n, s in _sequences_from([out / "cryoatom_na.fasta"])]
        cand = {"label": label, "map": mp["label"], "map_short": mp["short"], "map_note": mp["note"], "map_path": mp["path"],
                "setting": setting, "run": run, "round": rnd,
                "model": model, "raw": first_existing([out / "cryoatom_raw.cif"]), "score": score,
                "found_prot": found_prot, "found_na": found_na}
        ctx.log(f"Run {label}: {self._summary(cand)}")
        if ref is None:
            self.candidates.append(cand)
            self._live(cand, st)
        return cand

    def _readable(self, mp: dict[str, Any]) -> str:
        """The map as CryoAtom2 reads it: named .mrc or .map (a link otherwise, made once per map)."""
        path = mp["path"]
        if path.lower().endswith((".mrc", ".map")):
            return path
        links = self._links
        if path not in links:
            target = Path(path)
            try:  # next to the map when it is in this job's folder (an improved map), else at the top of the folder
                rel = str(target.with_suffix(".mrc").relative_to(self.ctx.job_dir))
            except ValueError:
                rel = f"input_{re.sub(r'[^A-Za-z0-9+-]+', '_', mp['label'])}.mrc"
            links[path] = str(self.ctx.link_input(path, rel))
        return links[path]

    @staticmethod
    def _best(cands: list[dict[str, Any]]) -> dict[str, Any]:
        return max(cands, key=lambda c: (c["score"]["score"], c["score"]["q_mean"], c["score"]["cc_mask"]))

    @staticmethod
    def _summary(c: dict[str, Any]) -> str:
        s = c["score"]
        return (f"{s['residues']} residues, {s['fitted']} fitted ({s['fitted_fraction']:.0%}), Q {s['q_mean']:.3f}, "
                f"CC {s['cc_mask']:.3f}, {s['fragments']} fragments, {s['duplicated']} duplicated, score {s['score']:.1f}")

    def _combine(self, best: dict[str, Any], cands: list[dict[str, Any]], write_structure) -> dict[str, Any] | None:
        ctx, ms = self.ctx, self.ms
        others = sorted((c for c in cands if c is not best and "combined_from" not in c), key=lambda c: -c["score"]["score"])
        if not others:
            return None
        base = self.read_structure(best["model"])
        combined, added = ms.combine(base, [(c["label"], self.read_structure(c["model"]), c["score"]["per_residue"]) for c in others],
                                     ms.fitted_cutoff(self.resolution))
        if not added:
            ctx.log("Combination: no fitted piece of the other runs where the best model has nothing")
            return None
        path = write_structure(combined, ctx.path(f"combined_round{best['round']}.cif"))
        score = ms.score_model(self.read_structure(path), self.ref, self.resolution)
        cand = {**best, "label": f"{best['label']} + {len(added)} piece(s)", "model": path, "score": score,
                "combined_from": added}
        self.candidates.append(cand)
        verdict = "kept" if score["score"] > best["score"]["score"] else "not kept (the score does not improve)"
        ctx.log(f"Combination: {sum(a['length'] for a in added)} residues in {len(added)} piece(s) from "
                f"{len({a['source'] for a in added})} other run(s) — {self._summary(cand)} — {verdict}")
        if score["score"] > best["score"]["score"]:
            self._live(cand)
            return cand
        return None

    def _new_sequences(self, cands: list[dict[str, Any]]) -> dict[str, list[tuple[str, str]]]:
        known = {s.upper() for recs in self.records.values() for _, s in recs}
        new: dict[str, list[tuple[str, str]]] = {}
        for kind, key in (("protein", "found_prot"), ("rna", "found_na")):
            hits = consensus_hits([c[key] for c in cands])
            fresh = [(n, s) for n, s in hits if s.upper() not in known]
            if fresh:
                new[kind] = fresh
        return new

    # ------------------------------------------------------- map improvement
    def _map_sources(self, model: str | None):
        """The maps the enhancement programs read: the unfiltered half maps when they are connected (with their average
        as the full map), else the experimental map."""
        from cryoplug.jobs.sharpening import LOCSCALE_SYMMETRY, MapSources
        from cryoplug.modelio import write_fasta
        from cryoplug.mrc import MapVolume

        ctx = self.ctx
        hm = ctx.input("half_maps")
        if not hasattr(self, "_full_map"):
            self._full_map = ctx.require("map").path
            if hm:
                a, b = MapVolume.read(hm.files[0]), MapVolume.read(hm.files[1])
                if tuple(a.shape_xyz) == tuple(b.shape_xyz):
                    folder = ctx.path("improve")
                    folder.mkdir(exist_ok=True)
                    self._full_map = str(a.write(folder / "half_map_average.mrc", (a.data + b.data) / 2))
            sym = str(ctx.inherited_meta("half_maps", "map").get("symmetry") or "C1").strip().upper()
            if not LOCSCALE_SYMMETRY.fullmatch(sym):
                ctx.warn(f"Point group {sym} not understood by LocScale: the map improvement uses none (C1)")
                sym = "C1"
            self._symmetry = sym
        records = [rec for recs in self.records.values() for rec in recs]
        sequence = str(write_fasta(records, ctx.path("improve", "sequences.fasta"))) if records else None
        return MapSources(half_maps=list(hm.files[:2]) if hm else None, full_map=self._full_map,
                          mask=self.mask.path if self.mask else None, sequence=sequence, model=model,
                          resolution=self.resolution, symmetry=self._symmetry)

    def _improve_map(self, method: str, model: str | None, folder: Path) -> Path:
        from cryoplug.jobs import sharpening as sh

        ctx = self.ctx
        src = self._map_sources(model)
        gpus = ctx.physical_gpus()
        if method in ("locscale_hybrid", "locscale_model_based", "locscale_model_free"):
            if not src.half_maps:
                ctx.warn("LocScale reads unfiltered maps: without half maps it scales the experimental map, which may "
                         "be filtered already (connect the half maps)")
            mode = {"locscale_hybrid": "hybrid", "locscale_model_based": "model_based"}.get(method, "model_free")
            return sh.run_locscale(ctx, src, mode, folder, nproc=4, gpus=gpus)
        if method == "emmernet":
            return sh.run_emmernet(ctx, src, folder, gpus=gpus)["map"]
        if method == "deepemhancer":
            return sh.run_deepemhancer(ctx, src, folder, gpus=gpus)
        if method == "emready":
            return sh.run_emready(ctx, ctx.require("map").path, folder, mask=src.mask, gpus=gpus)
        if method == "phenix_model_sharpening":
            return sh.run_map_sharpening(ctx, src, folder, use_model=True)
        if method == "phenix_density_modification":
            return sh.run_resolve_cryo_em(ctx, src, folder, nproc=4)
        raise JobError(f"Unknown map improvement {method}")

    def _improve(self, best: dict[str, Any], maps: list[dict[str, Any]], write_structure) -> dict[str, Any]:
        """Sharpen with the best model and build again, while the score improves (model-based methods); one more map
        to build into for the others."""
        ctx, ms = self.ctx, self.ms
        runs = int(ctx.params["runs"])
        cutoff = ms.fitted_cutoff(self.resolution)
        self.improved: dict[str, Path] = {}
        for method in ctx.params["improve_map"]:
            label, uses_model = IMPROVE_METHODS[method]
            for cycle in range(1, (int(ctx.params["improve_rounds"]) if uses_model else 1) + 1):
                folder = ctx.path("improve", f"{method}_{cycle}" if uses_model else method)
                folder.mkdir(parents=True, exist_ok=True)
                model = None
                if uses_model:
                    part = ms.reliable_part(self.read_structure(best["model"]), best["score"]["per_residue"], cutoff)
                    n = len(ms.polymer_residues(part))
                    if n < 10:
                        ctx.warn(f"{label}: only {n} residues of the best model fit the density, too few to sharpen with")
                        break
                    model = str(write_structure(part, folder / "model_fitted.cif"))
                    ctx.log(f"{label}, cycle {cycle}: sharpening with the {n} fitted residues of {best['label']}")
                self.planned += runs
                ctx.progress(self.done / max(self.planned, 1), f"Map improvement: {label}"
                             + (f", cycle {cycle} (with the model of {best['label']})" if uses_model else ""),
                             log=not uses_model)  # the model-based cycles have logged it in detail
                try:
                    new_map = self._improve_map(method, model, folder)
                except JobError as exc:
                    self.planned -= runs
                    ctx.warn(f"{label} left out: {exc}")
                    break
                self.round += 1
                mp = {"label": f"{label} {cycle}" if uses_model else label,
                      "short": f"{label} ⟳{cycle}" if uses_model else label, "path": str(new_map),
                      "note": f"{label} map" + (f", cycle {cycle}" if uses_model else "")}
                maps.append(mp)
                self.improved[mp["label"]] = new_map
                cands = [c for c in (self._build(mp, best["setting"], r, self.sequences, self.round)
                                     for r in range(1, runs + 1)) if c]
                if not cands:
                    ctx.warn(f"{label}: no run produced a model in the new map")
                    break
                top = self._best(cands)
                better = top["score"]["score"] > best["score"]["score"]
                ctx.log(f"{mp['note']}: best run {top['label']} ({self._summary(top)}) — "
                        + ("better than the best so far" if better else f"not better than {best['label']}"))
                if better:
                    best = top
                    if ctx.params["combine"] and len(cands) > 1:
                        best = self._combine(best, cands, write_structure) or best
                if not better or not uses_model:
                    break
        return best

    def _check_hand(self, best: dict[str, Any], main: dict[str, Any], flip_hand, MapVolume) -> str:
        ctx = self.ctx
        flipped = self.ref.like(flip_hand(self.ref))
        path = flipped.write(ctx.path("map_other_hand.mrc"))
        mask = None
        if self.mask:
            mvol = MapVolume.read(self.mask.path)
            if tuple(mvol.shape_xyz) == tuple(self.ref.shape_xyz):
                mask = str(mvol.like(flip_hand(mvol)).write(ctx.path("mask_other_hand.mrc")))
        self.planned += 1
        mp = {"label": "other hand", "short": "mirrored", "path": str(path), "note": "map mirrored (other hand)"}
        cand = self._build(mp, best["setting"], 1, self.sequences, 0, label="other_hand", mask_path=mask or False,
                           ref=flipped)
        if cand is None:
            return "check failed (the run in the mirrored map gave no model)"
        mine, theirs = best["score"]["score"], cand["score"]["score"]
        self.hand_candidate = cand
        if theirs > 1.25 * mine and theirs - mine >= max(5.0, 0.1 * best["score"]["residues"]):
            ctx.warn(f"The map is probably in the WRONG HAND: built into the mirrored map, the model scores {theirs:.0f} "
                     f"against {mine:.0f} ({cand['score']['fitted']} fitted residues against {best['score']['fitted']}). Flip "
                     "the map (and the half maps) before building further: the mirrored map and its model are outputs.")
            ctx.add_output("map_other_hand", "map", path, "Map in the other hand (mirrored)", inherit=["map"])
            model = shutil.copyfile(cand["model"], ctx.path("model_other_hand.cif"))
            ctx.add_output("model_other_hand", "model", model, "Model built in the mirrored map")
            ctx.add_highlight("Hand", "probably wrong", "bad")
            return f"probably wrong: the mirrored map scores {theirs:.0f} against {mine:.0f}"
        ctx.log(f"Hand check: the mirrored map does not do better (score {theirs:.0f} against {mine:.0f}): the hand looks right")
        ctx.add_highlight("Hand", "checked", "good")
        return f"looks right (mirrored map: score {theirs:.0f} against {mine:.0f})"

    # --------------------------------------------------------------- live dashboard
    def _live(self, cand: dict[str, Any], st: Any = None) -> None:
        """The dashboard while the runs go on, as CryoSPARC's: the model of every run as a frame of a slider (View 3D
        opens it in the map it was built into), the runs so far, and the best model so far with its scores."""
        ctx = self.ctx
        try:
            from cryoplug.imaging import trace_image, write_png
            from cryoplug.modelio import ca_traces
            live = ctx.path("live")
            live.mkdir(exist_ok=True)
            st = st if st is not None else self.read_structure(cand["model"])
            n = len(self.candidates)
            png = write_png(live / f"run_{n:03d}.png", trace_image(ca_traces(st), 240))
            s = cand["score"]
            caption = (f"{cand['label']}: {PRESETS[cand['setting']][0].lower()} settings on the {cand['map_note']} — "
                       f"{s['fitted']} fitted residues ({s['fitted_fraction']:.0%}), Q {s['q_mean']:.3f}, CC {s['cc_mask']:.3f}")
            view = [{"kind": "map", "path": cand["map_path"], "label": cand["map_note"]},
                    {"kind": "model", "path": cand["model"], "label": cand["label"]}]
            best = self._best(self.candidates)
            if best is cand:
                ctx.add_image_frame("Best model so far", png, cand["label"], key="best_preview", caption=caption, view=view,
                                    keep=False, note="Cα trace of the best model built so far, seen down z: View 3D opens it "
                                                     "in its map while the job goes on.")
            ctx.add_image_frame("Models of the runs", png, f"Run {n}", key="runs_models", group="runs", caption=caption,
                                view=view, note="Cα trace of the model of each run, seen down z (the latest while the job runs).")
            self._runs_table("Runs so far", self._run_rows(best))
            self._runs_plot()
            self._highlights(best, f"{self.done} of {self.planned}")
        except Exception as exc:  # the dashboard never stops the job
            ctx.log(f"(live view of run {cand.get('label')} not drawn: {exc})")

    def _highlights(self, best: dict[str, Any], runs: str) -> None:
        ctx, s = self.ctx, best["score"]
        ctx.add_highlight("Fitted", f"{s['fitted']} ({s['fitted_fraction']:.0%})",
                          "good" if s["fitted_fraction"] >= 0.7 else "warn" if s["fitted_fraction"] >= 0.5 else "bad")
        ctx.add_highlight("Q-score", f"{s['q_mean']:.3f}")
        ctx.add_highlight("CC (mask)", f"{s['cc_mask']:.3f}", "good" if s["cc_mask"] >= 0.7 else "warn" if s["cc_mask"] >= 0.5 else "bad")
        ctx.add_highlight("Runs", runs)
        ctx.add_highlight("Best run", f"{PRESETS[best['setting']][0]} · {best['map_short']}")

    def _run_rows(self, best: dict[str, Any] | None) -> list[list[Any]]:
        rows = []
        for c in self.candidates:
            sc = c["score"]
            rows.append([c["label"], c["map_short"], PRESETS[c["setting"]][0], f"{sc['fitted']} ({sc['fitted_fraction']:.0%})",
                         f"{sc['score']:.1f}", "✓ best" if c is best else "", sc["residues"], f"{sc['q_mean']:.3f}",
                         f"{sc['cc_mask']:.3f}", sc["fragments"], sc["duplicated"], c["round"]])
        return rows

    def _runs_table(self, title: str, rows: list[list[Any]]) -> None:
        self.ctx.add_table(title, ["Run", "Map", "Settings", "Fitted", "Score", "Best", "Residues", "Q-score", "CC (mask)",
                                   "Fragments", "Duplicated", "Round"], rows, key="runs",
                           note=f"Fitted: residues whose mean atom Q-score is at least "
                                f"{self.ms.fitted_cutoff(self.resolution):.2f} (expected Q at {self.resolution:g} Å minus 0.2). "
                                "Score: fitted residues, those traced twice counted once. All scored against the "
                                "experimental map.")

    def _runs_plot(self) -> None:
        runs = [c for c in self.candidates if "combined_from" not in c]
        if len(runs) > 1:
            x = list(range(1, len(runs) + 1))
            self.ctx.add_plot("Fitted residues by run", [{"name": "Fitted residues", "x": x, "y": [c["score"]["fitted"] for c in runs]},
                                                         {"name": "Residues built", "x": x, "y": [c["score"]["residues"] for c in runs]}],
                              x_label="Run", y_label="Residues", key="runs_plot")

    def _report(self, best: dict[str, Any], rounds: int, hand: str) -> None:
        ctx = self.ctx
        final = ctx.path("best_model.cif")
        shutil.copyfile(best["model"], final)
        report_model(ctx, final)
        ctx.add_output("model", "model", final, f"Best model ({best['label']})", inherit=["map"])
        if best.get("raw"):
            raw = ctx.path("best_model_raw.cif")
            shutil.copyfile(best["raw"], raw)
            ctx.add_output("model_raw", "model", raw, "Raw model of the best run (all built residues)", inherit=["map"])
        s = best["score"]
        self._highlights(best, str(len([c for c in self.candidates if "combined_from" not in c])))

        improved = getattr(self, "improved", {}).get(best["map"])
        if improved:
            ctx.add_output("map_improved", "map", improved, f"{best['map_note']} (the best model's map)", inherit=["map"])
        identified = [rec for recs in self.records.values() for rec in recs]
        given = ctx.input("sequence")
        if identified and (rounds > 1 or not given):
            from cryoplug.jobs.imports import write_sequence_outputs
            write_sequence_outputs(ctx, identified, "sequence", "Sequences (with the identified ones)")
        ctx.remove_section("best_preview")
        rows = self._run_rows(best)
        order = self.candidates
        if getattr(self, "hand_candidate", None):
            sc = self.hand_candidate["score"]
            rows.append(["other hand", "mirrored (scored against it)", PRESETS[self.hand_candidate["setting"]][0],
                         f"{sc['fitted']} ({sc['fitted_fraction']:.0%})", f"{sc['score']:.1f}", "", sc["residues"],
                         f"{sc['q_mean']:.3f}", f"{sc['cc_mask']:.3f}", sc["fragments"], sc["duplicated"], "—"])
        self._runs_table("Runs compared", rows)
        self._runs_plot()
        if best.get("combined_from"):
            ctx.add_table("Pieces added from other runs", ["From run", "Chain", "Residues", "Length", "Q-score", "New chain"],
                          [[a["source"], a["chain"], f"{a['first']}-{a['last']}", a["length"], f"{a['q_mean']:.3f}", a["new_chain"]]
                           for a in best["combined_from"]])
        lines = [f"Best model: run {best['label']} — {PRESETS[best['setting']][0]} settings "
                 f"({PRESETS[best['setting']][1]}) on the {best['map_note']}.",
                 f"Rounds: {rounds} (identification {'on' if self.dbs else 'off: no sequence database'}). Hand: {hand}."]
        if ctx.params["improve_map"]:
            tried = ", ".join(IMPROVE_METHODS[m][0] for m in ctx.params["improve_map"])
            lines.append(f"Map improvement tried: {tried}. "
                         + (f"The best model was built into the {best['map_note']}, an output."
                            if improved else "No improved map gave a better model than the maps before."))
        if s["short_fraction"] > 0.3:
            lines.append(f"{s['short_fraction']:.0%} of the residues are in pieces shorter than {self.ms.SHORT} residues.")
        ctx.add_text("Summary", "\n".join(lines))
        if ctx.params["keep_runs"]:
            folder = ctx.path("candidates")
            folder.mkdir(exist_ok=True)
            files = []
            for c in order:
                name = re.sub(r"[^A-Za-z0-9_+.-]+", "_", c["label"].replace(" + ", "_plus_")).strip("_")
                files.append(shutil.copyfile(c["model"], folder / f"{name}.cif"))
            if files:
                ctx.add_output("candidates", "files", files, "Models of every run")
        else:  # the outputs are copies at the top of the job folder
            shutil.rmtree(self.runs_dir, ignore_errors=True)
        ctx.log(f"Done in {self.done} CryoAtom2 run(s): {self._summary(best)}")
