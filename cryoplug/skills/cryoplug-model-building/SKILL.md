---
name: cryoplug-model-building
description: Build atomic models into cryo-EM maps with CryoPlug - choose the strategy from the resolution and what is known (de novo building with ModelAngelo or CryoAtom2, identifying unknown chains by HMM search or sequence databases, predicted models from AlphaFold DB, ColabFold or Boltz-2 placed by rigid-body fitting, protein-nucleic acid complexes, ligands), and check a build. Use when a model, a starting model, docking or the identity of unknown density is needed.
---

# Model building in CryoPlug

## Choose the strategy

| Situation | Strategy | Jobs |
|---|---|---|
| ≲ 4 Å (best < 3.5 Å), sequence known | De novo | `modelangelo_build` and/or `cryoatom_build` with a `sequence`; keep the better |
| Protein–RNA/DNA complex | De novo with separate protein and nucleic-acid records | `cryoatom_build` (or `modelangelo_build`) |
| Unknown subunit or unexpected density | Identify, then rebuild | `modelangelo_build` without sequence → `modelangelo_hmm_search` → `cryoatom_build` with the identified sequences (workflow `identify_unknown`) |
| 4–8 Å, or poorly resolved regions | Predicted model, then fitting | `import_model` (`source=alphafold` or `pdb`), `colabfold_predict` or `boltz_predict` → `phenix_process_predicted_model` → `fit_in_map` (or `phenix_dock_in_map`) → `phenix_real_space_refine` with morphing (workflow `alphafold_docking`) |
| Ligand, cofactor, drug | Initial pose + restraints | `boltz_predict` (ligand as CCD code or SMILES) and/or placement in Coot/ISOLDE; `phenix_elbow` for restraints (see `cryoplug-refinement-validation`) |

## Inputs

- **Map**: the most interpretable gold-standard-derived map — the sharpened map (`J1.map_sharp`), a LocScale or
  density-modified map. Not a DeepEMhancer map for automated building. Check the hand and a cubic box first
  (`cryoplug-map-quality`).
- **Sequence**: `import_sequence` with pasted FASTA (`fasta`), a file (`path`) or UniProt accessions (`uniprot`);
  one record per distinct entity (no copies); protein, RNA and DNA records are separated automatically (ModelAngelo
  needs separate files, CryoPlug passes them).
- **Mask** (optional): restricts building to one region (ModelAngelo, CryoAtom2).

## De novo building

1. `modelangelo_build` (GPU; tens of minutes for large complexes) with `map` and `sequence`; outputs `model` (pruned)
   and `model_raw` (everything traced, useful to see unassigned regions).
2. `cryoatom_build` (GPU ≥ ~14 GB; first run downloads ~3 GB of weights) on the same map: often more complete at
   medium resolution and handles nucleic acids well. Running both is cheap and informative.
3. Compare: residues built per chain against the sequence (report table), then `mapmodel_validation` on each model
   (Q-score, CC_mask, atom inclusion). Keep the better model, or merge chains from both with `merge_models`.
   Difficult map (low local resolution, flexible regions, unsure settings): `cryoatom_enhanced` runs CryoAtom2 several
   times (`runs` per setting; `settings=auto` adds "more residues" at better than 3.5 Å, "low resolution" beyond),
   optionally on re-sharpened copies (`bfactors`, e.g. "-30, 40") or other maps (`alt_map_1..3`), scores every model
   against the experimental `map` (fitted residues by Q-score, duplicates counted once), keeps the best with its gaps
   filled from the other runs, identifies chains with `protein_db`/`na_db` until no new sequence appears, and checks
   the hand. It costs runs × one CryoAtom2 build of GPU time (`max_runs` caps it): ask before launching. Its "Runs
   compared" table tells which setting works for this map; "probably in the WRONG HAND" means flipping the map first.
   Map-model cycles: connect `half_maps` and give `improve_map` a list, e.g. `improve_map=["locscale_hybrid"]`.
   locscale_hybrid (recommended for partial models; needs REFMAC5 in LocScale's environment), locscale_model_based and
   phenix_model_sharpening sharpen with the fitted part of the best model and rebuild while the score against the
   experimental map improves (`improve_rounds` cycles at most); emmernet, locscale_model_free, deepemhancer, emready and
   phenix_density_modification add one map each. The best model's map is the `map_improved` output; the experimental
   map stays the deposited primary map.
4. Continue with refinement and validation (`cryoplug-refinement-validation`). Workflows `denovo_modelangelo` and
   `denovo_cryoatom` create the whole chain up to the deposition package.

## Unknown proteins

1. `modelangelo_build` without a sequence (`build_no_seq`): writes HMM profiles.
2. `modelangelo_hmm_search` with `database` = a FASTA of candidate sequences (e.g. the UniProt proteome of the
   organism) → output `sequence` with the best hits, and a report table (E-values, scores).
3. `cryoatom_build` with the identified sequences (or `cryoatom_build` with `protein_db`/`na_db` to identify chains
   directly; then rebuild with its `sequence` output, iterating until no new sequence appears).
4. Check side-chain density against the sequence (aromatics, glycines, prolines) and run `checkmysequence`.

## Predicted models

1. Get the model: `import_model` (`source=alphafold` + UniProt `accession`, `source=pdb` + code, or a `path`),
   `colabfold_predict` (`as_complex`, `copies`; `msa_mode=single_sequence` for confidential sequences, otherwise the
   ColabFold server receives them), or `boltz_predict` (whole complex with RNA/DNA and ligands; `copies`, `ligands`).
2. `phenix_process_predicted_model`: trims residues below `minimum_plddt` (70), converts pLDDT to B-factors,
   optionally `split_by_domains` for domain-by-domain docking.
3. Place it: `fit_in_map` (`resolution=8`, `metric=correlation`, `search=100` when the position is unknown; `chains`
   to fit one domain), or `phenix_dock_in_map`; see `cryoplug-masks-and-fitting`.
4. Assemble domains/subunits with `merge_models`; then `phenix_real_space_refine` (morphing helps right after
   docking) and ISOLDE for the flexible parts.

Sending sequences to external servers (ColabFold MSA server, PDB/UniProt fetches) needs internet access from the
server and may disclose unpublished sequences: ask the person first.

## Checking a build

- Completeness: residues built vs sequence length, chain breaks, `UNK` residues (sequence missing or misassigned).
- Fit: `mapmodel_validation` (mean Q-score close to the value expected at this resolution, residues with Q < 0.3
  to inspect; see the table in `cryoplug-refinement-validation`), CC_mask.
- Register: `checkmysequence` (model, map, sequence) detects register shifts that geometry misses.
- Visual check by a person in the 3D viewer: right-handed helices, continuous chains, side chains in density.

## Pitfalls

| Symptom | Cause | Fix |
|---|---|---|
| Many short fragments, left-handed helices | Mirrored map | `map_tools` `flip_hand=true`, rebuild |
| Model offset from the map | Non-cubic map given to ModelAngelo | `map_tools` `box` to a cubic box, rebuild |
| Many `UNK` residues | No or wrong sequence | Identify chains, rebuild with the sequence |
| Building fails beyond ~4.5 Å | Resolution too low for de novo | Predicted model + fitting |
| CUDA out of memory | Map too big or GPU too small | Crop the box (`map_tools`), another GPU, a mask |

## Related skills

`cryoplug` · `cryoplug-map-quality` (map to build into) · `cryoplug-masks-and-fitting` (fitting, merging) ·
`cryoplug-refinement-validation` (what comes next).
