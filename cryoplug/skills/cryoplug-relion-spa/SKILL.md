---
name: cryoplug-relion-spa
description: Single-particle processing from raw movies to a refined map with RELION 5 inside CryoPlug, as one would do it in CryoSPARC - movie import with optics, motion correction, CTF estimation (CTFFIND), micrograph curation, Laplacian-of-Gaussian / template / Topaz picking, extraction, 2D classification and class selection, initial model, 3D classification, auto-refine, masks, post-processing, CTF refinement, Bayesian polishing, local resolution, multi-body refinement and DynaMight. Use when the data are movies or micrographs rather than a finished CryoSPARC refinement.
---

# From movies to map with RELION in CryoPlug

Every job runs the command RELION 5.1's GUI would build for the same settings, from the project folder with
project-relative paths, so the STAR files are RELION's own. Needs RELION and CTFFIND configured (`server_status`);
MotionCor2 is optional (RELION's own motion correction runs on CPUs). Long GPU jobs (2D/3D, refinement, Topaz,
DynaMight): confirm with the person first.

## The chain

| Step | Job | Starting settings | Check in the report |
|---|---|---|---|
| 1. Import | `relion_import` | `movies` (wildcard or folder), `pixel_size` as recorded, `dose_per_frame`, `gain` if not gain-corrected | movie count, frames |
| 2. Motion | `relion_motioncorr` | `patches_x`/`patches_y` 5 (K2/K3/Falcon 4), `binning` 2 for super-resolution, `float16`, power spectra on | accumulated motion, drift |
| 3. CTF | `relion_ctffind` | `res_max` 5 Å (3–4 Å for good data), power spectra of step 2 | CTF fit resolution histogram, fits image |
| 4. Curation | `relion_curate` | `mode=thresholds` as an agent, `max_ctf_resolution` 6–8 Å, `max_motion` 30–50 Å; `interactive` (default) waits for a person | histograms with thresholds, rejected output |
| 5. Picking | `relion_autopick` | `method=log`, `diameter_min`/`diameter_max` around the particle | picks drawn on micrographs, particles per micrograph |
| 6. Extraction | `relion_extract` | `box` ≈ 2 × diameter (even), `rescale` to 64–128 px for 2D/3D | particle count, box, pixel size |
| 7. 2D | `relion_class2d` | `algorithm=vdam`, `classes` 50–200, `vdam_batches` 200 | class gallery |
| 8. Selection | `relion_select2d` | `mode=list` with `classes` (e.g. "1, 4, 7-9") as an agent; `interactive` waits for a person's clicks; `auto` uses RELION's class ranker; `reference` keeps the classes that correlate (`min_correlation` 0.8) with projections of a map connected as `reference` | kept / rejected particles |
| 9. Initial model | `relion_initial_model` | `symmetry` (made in C1, symmetry applied after), `iterations` 200 | map |
| 10. 3D classes (optional) | `relion_class3d_global` | `classes` 4–8, `iterations` 25, `alignment=global` | class maps, shares over the iterations, views per class |
| 10b. Heterogeneous refinement (optional) | `relion_hetero_refine` | `ref_1`, `ref_2`… one map per class (good map + decoys, or the classes of an initial model), `ini_high` 40 | class shares, particles per class |
| 11. Refinement | `relion_refine3d` | `mpi` ≥ 3, `symmetry`, `ini_high` 40–60 Å from an initial model | FSC, resolution |
| 11b. Unknown symmetry (optional) | `relion_symmetry_search` on a C1 map | `groups` "C2-C12, D2-D8, T, O, I", `resolution` ≈ 2 × the map's, `threshold` 0.9 | correlation per group; proposed group and the map aligned on its axes |
| 12. Mask | `relion_mask_create` | `lowpass` 15 Å, `extend` 3, `soft_edge` 6 (threshold 0 = automatic) | mask statistics; look at it on the map |
| 13. Post-processing | `relion_postprocess`, `relion_locres` | automatic B-factor | corrected FSC, B-factor, local resolution |
| 14. Polishing loop | `relion_ctf_refine` (`mode=complete`) → `relion_polish` (`mode=train` once, then polish) → `relion_refine3d` | | resolution gain after each round |

Workflow `relion_spa` creates steps 1–13 (CTF refinement and the second refinement optional); workflow
`relion_clean_3d` cleans a particle set in 3D (initial model with decoy classes → heterogeneous refinement → Select 3D
classes, which waits for a person or takes `mode=list` → auto-refine). `relion_curate` also takes the picks or
particles of the micrographs (`min_particles` / `max_particles`, and their subset as an output). Its curation and
selection jobs are interactive: a person drags the limits on the distributions of the micrographs and clicks the
classes in the job panel. As an agent, read the CTF and motion statistics and the class table of the 2D job (particles
and resolution per class), and use a `relion_curate` job with `mode=thresholds` and a `relion_select2d` job with
`mode=list` instead, after showing the person your choice. `relion_pick_inspect` (picks shown on the micrographs,
figure-of-merit threshold and power-score range, particles removed or added by hand, manual picking, or
`mode=filaments`: start and end of each tube clicked) is for people only.

During a collection, `relion_live` runs steps 1–6 cycle after cycle on the movies that arrive (`movies` wildcard,
`pixel_size`, `dose_per_frame`, `gain`, acceptance thresholds, optional `picking` and `extract`); it runs until
`stop_after` minutes without a new movie, or until "Stop after this cycle" (`control_job` with `action=stop`); its
outputs exist once it stops. `control_job` also pauses and resumes it and changes the acceptance limits during the
session (`action=thresholds`, `limits={"ctf_res": [null, 6]}`); `class2d=true` runs a streaming 2D classification
whose classes (`classes` output) go to `relion_select2d`.

## Settings that matter

- Pixel size: that of the movies as recorded; for super-resolution movies bin by 2 in motion correction.
- Dose per frame drives dose weighting and the power spectra (motion correction refuses to run without it).
- Picking: Laplacian-of-Gaussian first; then templates (`references` = the `class_averages` output of a selection,
  `recentre=true` there) or Topaz (`relion_topaz_train` on the good particles, then `relion_autopick`
  `method=topaz` with that model; `particle_diameter` is required, the extraction `min_fom` filters picks).
- 2D: VDAM runs without MPI (threads only); EM accepts MPI. `highres_limit` 7–12 Å against over-fitting of small
  particles; `tau2_fudge` 2.
- Selection: `max_per_class` evens out the views before an initial model; keep borderline classes rather than
  losing rare views, and run a second 2D round instead. With a map of the particle already at hand, `mode=reference`
  scores every class against its projections (both hands; the scores are in the log and on the gallery).
- Symmetry: `relion_symmetry_search` proposes the largest group whose correlation reaches `threshold`; its subgroups
  pass too, and pseudo-symmetry (near-identical subunits) scores high as well, so look at the map and the table
  before refining with that `symmetry` (its `map` output, aligned on the axes of the group, is the reference).
- Work down-scaled until the final refinement, then re-extract at full scale: `relion_extract` with `particles`
  (instead of coordinates) and `recentre=recentre`.
- Refinement: `absolute_greyscale=auto` skips `--firstiter_cc` for maps made by RELION; masks only after a first
  refinement; `solvent_fsc` for small particles in large boxes; `blush` for small or flexible particles.
- Polishing needs the movies: any micrographs that descend from `relion_motioncorr` work (the motion is found there).
- Particles of these jobs chain with `relion_local_refine`, `relion_subtract` (`cryoplug-relion-focused`) and the
  cryoDRGN jobs (`cryoplug-heterogeneity`); `relion_multibody` (rigid domains, needs ≥ 3 MPI processes) and
  `relion_dynamight` (continuous deformations, GPU) describe flexibility within RELION.

## Particle utilities

`relion_select3d` (classes of a 3D classification or heterogeneous refinement, `mode=list` as an agent),
`relion_particle_sets` (A ∩ B, A − B, B − A, matched by image or by position), `relion_join` (union),
`relion_rebalance` (caps over-represented views), `relion_downsample` (Fourier cropping to a smaller box),
`relion_symmetry_expand` (one particle per symmetry copy, then `relion_subtract` / focused classification in C1),
`relion_subset` (duplicates, splits, metadata ranges), `relion_restack` (a subset rewritten into its own stacks,
relion_stack_create), `relion_particle_curate` (particles kept on their statistics:
interactive for people; as an agent `mode=thresholds` with `limits` such as `max_prob 0.1 -` and `norm 0.6 1.4`, one
per line, or `mode=cluster` with a `statistic` and a number of `clusters`; read the statistics of the particles first).

Also: `relion_optics_groups` (optics groups from EPU beam shifts, file names or acquisition order, before CTF
refinement of AFIS data), `relion_topaz_denoise` (denoised micrographs for picking; extraction maps the picks back to
the originals), and `helical=true` on autopick (templates), extraction, 2D/3D classification, auto-refine and mask
creation for filaments (tube diameter, twist, rise, symmetry search). Filaments picked by hand (start-end coordinates)
need `helical=true` in `relion_extract`, which cuts them into segments one rise apart. `relion_helix_toolbox` searches
the twist and rise of a helical map locally (`mode=search` with `twist_min/max`, `rise_min/max`, then imposes them),
imposes a given symmetry (`mode=impose`) or makes a cylinder reference (`mode=cylinder`); `relion_helix_inimodel2d`
makes the initial model of an amyloid from its 2D class averages and `crossover_distance`. `relion_estimate_gain` makes
a gain reference from the movies (its `movies` output carries it to motion correction). `relion_ctffind` has
`tilt_search` and `extra_ice` (ice ring power fraction and kurtosis for the curation).

## Limits and pitfalls

- Several sessions with different pixel sizes or detectors: import them separately, join with `relion_join`.
- A tight extraction box loses the signal the CTF delocalises: 1.5–2 × the diameter.
- Duplicated particles after re-centring inflate the FSC: `relion_subset` with `operation=duplicates`.
- Real RELION only warns about unknown options (and ignores them): when adding `extra_args`, spell them exactly.

## Related skills

`cryoplug` · `cryoplug-relion-focused` · `cryoplug-heterogeneity` · `cryoplug-map-quality` ·
`cryoplug-troubleshooting`.
