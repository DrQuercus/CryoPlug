---
name: cryoplug-relion-focused
description: Focused (local) refinement and focused 3D classification of one region of a complex with RELION inside CryoPlug, starting from a CryoSPARC consensus refinement - RELION consensus refinement, region mask, signal subtraction with optional symmetry expansion, local refinement with Blush, classification without alignment, post-processing, reconstruction of particle subsets, and feeding the maps into composite maps. Use when a region is blurry or heterogeneous in the consensus map.
---

# RELION focused refinement and classification in CryoPlug

CryoPlug converts the particles and poses of a CryoSPARC refinement for RELION (half sets kept, so the FSC stays
gold-standard) and returns every map on the grid of the consensus map, ready for LocScale, composite maps or
building. Needs RELION installed (`server_status`), particles with poses, and GPU time: confirm with the person.

## The chain

| Step | Job | Starting settings |
|---|---|---|
| 1. Particles and maps | `import_cryosparc` of the consensus refinement (`import_particles` on) | — |
| 2. RELION model of the particles (needed by subtraction) | `relion_local_refine` on the whole complex | `angular_step=1.8°`, `local_searches=true`, FSC mask as `mask` |
| 3. Region mask | `create_mask` (`density_near_model` with the chains of the region, or `model_map`), or `draw_mask` drawn by a person | soft: `dilate` 2–3 Å (3–5 for density near model), `soft` 6–8 Å (8–12) |
| 4. Signal subtraction | `relion_subtract` (particles of step 2, mask of step 3) | `recenter=true`, box automatic (1.5 × mask extent); `expand_symmetry` for symmetric complexes |
| 5a. Focused refinement | `relion_local_refine` on the subtracted particles | `blush=true` for small/flexible regions, C1 |
| 5b. Focused classification | `relion_class3d` on the subtracted particles | `classes=4`, `alignment=none`, `iterations=25`, `tau2_fudge` 4 by default, 10–40 for small subtracted regions |

Workflows: `relion_focused_refinement` (steps 1–5a, + optional LocScale) and `relion_focused_classification`
(1–5b). In both, the mask step is a `draw_mask` job waiting for a person to draw the mask in the 3D viewer; as an
agent you may instead create the chain job by job with a `create_mask` mask.

## Settings that matter

- `relion_local_refine`: `angular_step` 1.8° with local searches from the start (±11°) is the standard local
  refinement; `offset_range`/`offset_step` for translations; `symmetry` of the region; `blush` (RELION 5,
  regularisation that helps small or flexible regions; needs the Blush model installed); `postprocess` writes the
  sharpened map and corrected FSC with the mask; MPI: at least 3 processes (one leader + one per half set), with
  GPUs 2 per GPU + 1.
- `relion_subtract`: the mask must cover the region generously (a tight mask subtracts its residual signal);
  symmetry expansion multiplies the particles (C3 → 3 copies, same half set for the copies of one particle).
- `relion_class3d`: without alignment 25 iterations usually suffice; look at class stability and occupancy; each
  class gives a map and its particles (`class_1`… outputs) — refine the interesting class locally afterwards.
- `relion_postprocess`: corrected FSC and B-factor sharpening of any half maps (CryoSPARC imports, RELION, composite
  maps), optional local resolution. `relion_reconstruct`: full-resolution map of a particle subset (a cryoDRGN
  selection, a class) without alignment, with its FSC.

## Limits and pitfalls

- A region below ~100–150 kDa without subtraction aligns poorly: subtract the rest first and try Blush.
- Particles without poses (2D classification outputs) cannot be refined locally: import a refinement.
- Continuous motion is better described by cryoDRGN or 3DVA (`cryoplug-heterogeneity`) than by discrete classes.
- Focused maps of several regions combine into one map with `composite_map` (`cryoplug-masks-and-fitting`); deposit
  the consensus and focused maps with their half maps, and never present a composite FSC as gold-standard.

## Related skills

`cryoplug` · `cryoplug-masks-and-fitting` (region masks, composite maps) · `cryoplug-heterogeneity` ·
`cryoplug-map-quality` (local resolution to choose the region).
