---
name: cryoplug-heterogeneity
description: Analyse conformational and compositional heterogeneity with CryoPlug - CryoSPARC 3D variability imports, the cryoDRGN protocol (input check, two training rounds, junk removal, convergence check, analysis, trajectories, landscape, volumes at particles), cryoDRGN-AI ab initio reconstruction without consensus poses, particle selection, volume series analysis and the validation of states. Use for flexible or partially occupied regions, mixtures of states, junk particles, or any cryoDRGN or 3DVA question.
---

# Heterogeneity analysis in CryoPlug

A blurry region of the consensus map (poor local resolution, weak density) can come from motion, partial occupancy
or a mixture of states. Generated volumes (cryoDRGN, 3DVA frames) show states; they are not gold-standard
reconstructions: every state that matters is validated by reconstructing its particles (half maps, FSC).

| | CryoSPARC 3D variability (3DVA) | cryoDRGN | cryoDRGN-AI |
|---|---|---|---|
| Principle | linear components around the consensus | neural network, non-linear latent space, consensus poses | learns poses and latent space together, no consensus needed |
| In CryoPlug | `import_cryosparc` of the 3DVA job + `import_volume_series` of the display job (workflow `variability_3dva`) | `import_cryosparc` of the consensus refinement → `cryodrgn_train` (workflow `heterogeneity_cryodrgn`) | `import_cryosparc` of a Select 2D or refinement job → `cryodrgn_abinit` (workflow `heterogeneity_cryodrgn_ai`) |
| Cost | already computed | 1 to several GPU hours | longer (pose search); 128 px |

GPU hours are expensive and shared: confirm with the person before every training.

## The cryoDRGN protocol (Kinman et al. 2023)

| Step | Job | Settings | Check |
|---|---|---|---|
| 1. Particles | `import_cryosparc` of the consensus refinement (or `import_particles`) | — | particles with poses (`alignments3D`) and CTF |
| 2. Input check | `cryodrgn_backproject`, or `check_inputs` of the training | 128 px, 10 000 images | backprojection like the consensus, positive density, signal in the half-set FSC |
| 3. Round 1: cleaning | `cryodrgn_train` | `box=128`, `zdim=8`, `epochs=50`, `model` auto (small 256 × 3) | losses, junk in the gallery, outliers |
| 4. Remove junk | `select_particles` (`selection=clusters`, `clusters="3,7"`, `action=remove`; or `selection=outliers`) | — | 2D classification of the excluded particles in CryoSPARC |
| 5. Round 2: states | `cryodrgn_train` on the selection | `box=256`, `model` auto (large 1024 × 3), `epochs=50` | states, motions |
| 6. Convergence | `cryodrgn_convergence` → `cryodrgn_continue` if needed | up to 100 epochs | verdict: converged / nearly / not converged |
| 7. Explore | `cryodrgn_analyze` (more volumes `ksample=50`–100, another epoch), `cryodrgn_trajectory`, `cryodrgn_landscape`, `cryodrgn_volumes` | — | states, data-supported paths |
| 8. Interpret | `series_analysis` of cluster volumes, states or a trajectory (+ model) | — | variability map, chains that move or vanish |
| 9. Validate | `select_particles` (`action=keep`) → CryoSPARC (Import Particle Stack → NU refinement), or `relion_reconstruct` | — | one gold-standard map per state |

Image size: Fourier cropping limits resolution to twice the new pixel size (e.g. 400 px at 0.83 Å → 128 px at
2.6 Å/px, Nyquist 5.2 Å: enough to see states; 256 px for the final round). Prepared images are reused by later
jobs at the same size.

## Reading a training report

- Input check: the backprojection must look like the consensus map with **positive** density; negative density
  means inverted contrast (set `uninvert`/"Do not invert the images", else all volumes come out hollow); an FSC that
  collapses at once means poses read wrongly (`pose_source`).
- Losses: reconstruction loss drops then plateaus; KL rises then stabilises.
- Cluster gallery flags: `weak` (little density: pieces missing or junk), `noisy`, `blurry` (aggregates, junk),
  `rare` (< 1 % of particles). Hints only: a small sharp cluster may be the rare state you want.
- "Is the latent space structural?": R² of defocus, viewing direction and shift predicted from the latent space;
  above 0.3, part of the heterogeneity comes from the images (CTF, ice, views), not the molecules.
- Similarity of cluster volumes: blocks = distinct states; a gradient along the diagonal = continuous motion.
- "What to do next" summarises the advice for the next round.

## cryoDRGN-AI (`cryodrgn_abinit`)

Use when the consensus is unreliable (large motion, mixture, little filtering). Defaults from the paper: 128 px,
`zdim=8`, `epochs=30` (50–100 for large datasets), `pose_epochs=3`. Without consensus poses, connect a `reference`
map of the same molecule to fix the frame and the hand. Read: input check (contrast `auto`), pose changes between
checkpoints (large during pose search, then a few degrees), reconstruction across epochs, comparison with the
consensus poses (hand, median difference, fraction within 10°), validation backprojection FSC. Then remove junk and
run a second round on the selection, or a classic `cryodrgn_train` at 256 px on cleaned particles when the poses
agree with the consensus.

## Agents and the interactive explorer

The latent-space explorer (lasso regions, cluster clicks, particle images) is in the web interface. As an agent:
use `select_particles` with `selection=clusters` and cluster numbers from the report, or `selection=outliers`
(`zscore`, default 2); region (lasso) selections are prepared by a person in the explorer.

## Pitfalls

| Symptom | Cause | Fix |
|---|---|---|
| Hollow volumes | inverted contrast | input check; do not invert the images |
| All volumes identical | particles over-filtered upstream, little heterogeneity | start from less filtered particles; large network in round 2 |
| Latent space follows defocus or views | CTF or pose errors, preferred orientation | not structural: remove those particles or ignore that axis |
| Volumes noisier at later epochs | over-training | analyse the epoch given by the convergence check |
| `CUDA out of memory` | batch too large | `batch_size` 8, smaller box, small network |
| `MemoryError`, job killed | images too large for RAM | `lazy` loading; more memory on the lane |
| AssertionError during training | mixed precision instability | `amp=false` |
| "Cannot find file … under datadir" | CryoSPARC project moved | `import_particles` with the image folder |

Never report a "resolution" for a cryoDRGN volume. For the paper: cryoDRGN version, image size, latent dimension,
network, epochs, analysed epoch, convergence evidence, particles per state, cleaning criteria.

## Related skills

`cryoplug` · `cryoplug-relion-focused` (focused classification of a region) · `cryoplug-model-building` (models of
states: `extract_volume` gives one volume of a series as a map).
