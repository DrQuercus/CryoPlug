---
name: cryoplug-refinement-validation
description: Refine, validate and deposit cryo-EM models with CryoPlug - Phenix real-space refinement, Servalcat, ISOLDE and Coot sessions, waters and ligand restraints, validation (Q-scores, MolProbity, map-model FSC, CC_mask, EMRinger, checkMySequence, the official wwPDB report) with reference values and red flags, pre-deposition checks and the OneDep deposition package. Use once a model exists, to judge model quality, or to prepare a PDB/EMDB deposition and the methods/Table 1 drafts.
---

# Refinement, validation and deposition in CryoPlug

## The refinement loop

```
automated build / docking → real-space refinement → ISOLDE (or Coot) → final real-space refinement → validation
                                    ↑                                                              |
                                    └──────────────── while validation improves ───────────────────┘
```

| | `phenix_real_space_refine` | `servalcat_refine` |
|---|---|---|
| Target | one (sharpened) map | unsharpened half maps (likelihood), or a map |
| Strengths | secondary-structure restraints, morphing, simulated annealing: robust after docking and at low resolution | statistical weighting, atomic B-factors, Fo-Fc difference maps |
| Best for | every resolution, intermediate and final refinements | high resolution (< 3 Å), finding errors, ligands, waters |

- Phenix: `macro_cycles` (5), `secondary_structure` (on), `rama_restraints` (off by default: keep it off if you
  report Ramachandran statistics as validation), `run` (strategy, e.g. `minimization_global+adp`; N/Q/H flips are
  added to the strategy with `nqh_flips`), `restraints` input for ligands. Refine against the primary or sharpened
  map, not a neural-network map, for the final model.
- Servalcat: needs independent half maps (not spIsoNet's), `pg` (point group) with a model of the asymmetric unit,
  `jellybody` at low resolution; read the Fo-Fc map (peaks around ±4σ: positive = missing atoms, negative = atoms in
  excess) and the `model_expanded` output for symmetric assemblies.
- Interactive rebuilding: `isolde_session` (molecular-dynamics flexible fitting, whole-model corrections at
  2.5–4.5 Å) or `coot_session` (loops, ligands, glycans, waters). They reach status `waiting` and need a person:
  tell them it is ready; the job finishes when they save the model in the job folder and press *Finish*.
- Ligands: `phenix_elbow` (CCD code or SMILES) → `restraints` output → connect it to refinement; place the ligand
  with `boltz_predict` or interactively; merge with `merge_models`; check the Servalcat Fo-Fc around it.
- Waters below ~2.5–3 Å: `phenix_douse`, then refine and inspect.

## Validation jobs

| Job | When | Gives |
|---|---|---|
| `mapmodel_validation` (built-in, fast) | every model | per-residue Q-scores vs the expected value, map-model FSC, CC_mask/CC_box, atom inclusion at the suggested contour, worst residues |
| `phenix_validation_cryoem` | each refined model | MolProbity geometry + model-map correlation, as in the wwPDB report |
| `phenix_molprobity` | between rebuilding sessions | clashes, Ramachandran, rotamers, covalent geometry |
| `phenix_emringer` | side chains below ~4.5 Å | EMRinger score |
| `checkmysequence` | before any deposition | register shifts, chains matching no sequence, mismatches |
| `wwpdb_validation` | final model | official wwPDB report (PDF + XML) with percentiles; uploads the model and map to the wwPDB server: ask first |

## Reference values (well-refined model; they depend on resolution)

| Metric | Good | Look closer | Note |
|---|---|---|---|
| MolProbity score | ≤ 2.0 | > 3.0 | combines clashes, Ramachandran, rotamers |
| Clashscore | ≤ 10 (≤ 5 at high resolution) | > 20 | with hydrogens |
| Ramachandran favoured | ≥ 95 % | < 90 % | meaningful only without Ramachandran restraints |
| Ramachandran outliers | ≤ 0.5 % | > 2 % | each one must be justified by density |
| Rotamer outliers | ≤ 1–2 % | > 3 % | |
| Cβ deviations | 0 | > 5 | |
| RMSD bonds / angles | ≈ 0.002–0.01 Å / 0.5–1.5° | > 0.02 Å / > 2° | too low can mean over-tight restraints |
| CC_mask | ≥ 0.7 (≥ 0.8 below 3 Å) | < 0.5 | depends on resolution and mask |
| EMRinger | ≥ 2 (below 4 Å) | ≈ 1 or less | near 0: register or hand doubtful |
| Atom inclusion | ≥ 80 % | < 60 % | at the recommended contour |
| Map-model FSC at 0.5 | ≈ half-map FSC resolution at 0.143 | much better than the half maps | "too good" suggests over-fitting |

Expected Q-score (Pintilie et al. 2020): Q ≈ −0.1775 × resolution + 1.1192 — 0.76 at 2.0 Å, 0.68 at 2.5 Å, 0.59
at 3.0 Å, 0.50 at 3.5 Å, 0.41 at 4.0 Å. A mean Q-score near the expected value means good overall agreement;
residues with Q < 0.3 need a look (or sit in regions of poor local resolution).

## Red flags

| Symptom | Likely cause | Action |
|---|---|---|
| EMRinger near 0, left-handed helices | mirrored map | `map_tools` `flip_hand=true`, rebuild |
| Good CC, poor geometry | over-fitting, weak restraints | ISOLDE, secondary-structure restraints |
| Good geometry, low CC/Q-score | misplaced model or register shift | `fit_in_map`, `checkmysequence`, ISOLDE |
| Low Q-score in one region only | poor local resolution or a local error | compare with LocScale map and local resolution; fix or remove unsupported atoms |
| Map-model FSC better than half-map FSC | fitting noise | gentler refinement, half-map target (Servalcat) |
| Many `UNK` residues | sequence missing or misassigned | identify chains, rebuild with the sequence |
| checkMySequence reports a shift | register shifted by a few residues | fix in Coot/ISOLDE where indicated, check again |

## Deposition

1. `predeposition_check` (model, map, half maps, mask, sequence): map/half-map/mask consistency (box, pixel,
   origin), atoms inside the box, fit at the suggested contour, chain ids, unknown residues, zero occupancies,
   unrefined or pLDDT-like B-factors, severe clashes, chain breaks, sequence identity. Fix every FAIL; understand
   every WARN.
2. `deposition_package` (model, primary map, half maps, mask, FSC, checklist report, sequence): `model.cif`,
   `primary_map.mrc`, half maps, `mask.mrc`, `fsc.xml`, validation reports, `CHECKLIST.md` (recommended contour,
   resolution, pixel size, symmetry), `methods_draft.md` (methods and citations from the job history) and
   `table1_draft.md` (cryo-EM statistics table). The person completes the data-collection values.
3. `wwpdb_validation` on the final model (with permission), then OneDep (deposit.wwpdb.org): model (mmCIF), primary
   map, both half maps, mask, FSC curve; LocScale, density-modified or spIsoNet maps only as additional maps.

The workflow `validate_deposit` runs the whole validation and packaging for an existing model and maps.

## Related skills

`cryoplug` · `cryoplug-model-building` · `cryoplug-map-quality` (primary vs interpretation maps) ·
`cryoplug-troubleshooting` (Phenix errors).
