---
name: cryoplug-map-quality
description: Assess and improve cryo-EM maps in CryoPlug - gold-standard FSC and resolution, anisotropy from preferred orientation (3D FSC), local resolution, handedness, sharpening and enhancement (LocScale 2, Phenix map sharpening and density modification, spIsoNet, EMmerNet, DeepEMhancer, EMReady), and choosing the map to build into versus the map to deposit. Use for questions about map quality, resolution, stretched or blurry density, preferred orientation, or which map to use.
---

# Map quality and enhancement in CryoPlug

Start from an import job: `import_cryosparc` (half maps, unsharpened and sharpened maps, FSC mask, local resolution
map, particles) or `import_maps` (any software). Keep `compute_fsc` on at import: the FSC is recomputed and the
resolution propagates to later jobs (their `resolution` parameter stays 0).

## 1. Assess (cheap, built-in or Phenix; run them before any enhancement)

| Question | Job | Inputs | Read |
|---|---|---|---|
| Global resolution | `map_fsc` | half maps (+ mask) | corrected FSC (phase randomisation) at 0.143; masked vs unmasked |
| Anisotropy (preferred orientation) | `directional_fsc` | half maps (+ mask, + particles of the refinement) | worst/best direction resolution ratio, best and worst directions, cFAR (< 0.5: preferred orientation), SCF of the viewing directions (1 even, 0.81 side views only) |
| Local resolution | `local_resolution` (Phenix) | half maps (+ map, mask, model) | distribution in the molecule; with a model, per chain and per residue |
| More particles worth it? | `relion_reslog` (RELION) | particles of the refinement (+ its mask) | B-factor of 1/d² against ln N (smaller: each particle helps more), resolution with twice the particles, particles for a target resolution; a flat line means resolution is limited by something else (heterogeneity, poses, CTF) |
| Map header, box, pixel size | `map_info` tool | a map path or `J1.map_sharp` | box (cubic?), voxel size, origin |

Reading the numbers:

- Report the **corrected** masked FSC at 0.143. A large difference with CryoSPARC usually means another mask. A mask
  that is too tight (edge < 6 Å) inflates the resolution: compare masked and unmasked curves; a corrected curve
  that drops sharply where the uncorrected one stays high signals mask effects.
- Directional FSC, worst/best ratio: ≤ 1.25 isotropic; 1.25–1.6 moderate (LocScale, watch for stretched density);
  > 1.6 strong preferred orientation (spIsoNet; ideally new tilted data or rebalanced views). Elevation 90° is the
  map z axis. Small particles give noisy cones.
- Local resolution: a sharp core with blurry periphery, domains or micelle calls for local sharpening (LocScale) and
  maybe focused refinement (`cryoplug-relion-focused`) or heterogeneity analysis (`cryoplug-heterogeneity`).
- Hand: a mirrored map gives left-handed helices and automated builds broken into short fragments. Fix with
  `map_tools` (`flip_hand=true`) on the map used for building (and flip the other maps the same way if the
  deposited map changes). The hand cannot be judged from the FSC.

## 2. Choose an enhancement

| Situation | Recommended | Alternatives / notes |
|---|---|---|
| Uniform resolution, CryoSPARC sharpening fine | keep `map_sharp` | `phenix_map_sharpening` (`method=shell`, from half maps) to compare |
| Sharp core, blurry periphery, domains or micelle | `locscale` (`mode=model_free`, GPU) | later `model_based` (complete model) or `hybrid` (partial model) |
| 3–5 Å, hard to interpret | `phenix_resolve_cryo_em` (half maps, + sequence) | LocScale |
| Strong preferred orientation (ratio > 1.6) | `spisonet` (half maps + soft mask, GPU, tens of minutes) | deposit the original maps |
| 4–6 Å, figures or domain fitting | `deepemhancer`, `emready`, `emmernet` | never as the only evidence |
| Doubt about a detail (ligand, side chain) | compare 2–3 maps | workflow `map_enhancement` |
| Simple global B-factor sharpening/blurring, low-pass, crop/pad, pixel size, resampling, mask | `map_tools` | built-in, fast |

Notes on parameters (check `describe_job_type` for the full lists):

- `locscale`: `mode` = `model_free` (EMmerNet reference, default), `model_free_pseudomodel` (very noisy or
  low-resolution maps), `model_based`, `hybrid`; give unfiltered, unsharpened half maps; set `symmetry` if any.
- `phenix_map_sharpening`: `method=shell` (half maps, or map + fitted model) or `b-factor` (single map);
  `anisotropic`, `local`. With Phenix 2 it replaces `phenix_auto_sharpen` and `phenix_local_aniso_sharpen`.
- `spisonet`: needs half maps and a soft mask; its outputs (half maps, map, 3D FSC) are not independent.
- `map_tools`: operations in order — `set_pixel_size` (header only), `resample_pixel_size` (Fourier cropping or
  padding) or the optional `reference` input (onto the grid of another map, to compare or combine maps), `box`
  (crop/pad to a cubic box), `flip_hand`, `bfactor` (negative sharpens), `lowpass`, `apply_mask`, `normalize`.

The workflow `map_enhancement` creates directional FSC + LocScale and optional spIsoNet, EMmerNet, DeepEMhancer,
density modification, auto-sharpen and EMReady on the same half maps, for side-by-side comparison.

## 3. Compare and decide

- `get_job_images` shows the central projection thumbnails of each map; the person compares maps best in the 3D
  viewer (*View 3D*, same contour by enclosed volume). Ask them to look when the choice matters.
- After a model exists, compare maps objectively by the model fit: `mapmodel_validation` (Q-score) against each map.
- Decision to report: which map to **build into** (most interpretable), which map is **primary** (the sharpened
  gold-standard map, deposited with half maps and mask), which extra maps to deposit as additional maps.

## Pitfalls

- Enhancement cannot recover information lost by filtering: start from unfiltered, unsharpened half maps.
- Neural-network maps can hallucinate or over-smooth density: never validate, refine to completion or deposit
  against them; never conclude on a single network map.
- spIsoNet half maps are not independent: no reported FSC, no Servalcat refinement against them.
- Non-cubic boxes can offset ModelAngelo models: crop/pad to a cubic box with `map_tools` before building.
- Pixel size errors scale everything (resolution, model geometry): check `map_info` against the acquisition; fix
  with `map_tools` `set_pixel_size` only when the true value is known.
- Report resolution with the method and mask used; local resolution maps are for interpretation, not a single number.

## Related skills

`cryoplug` (working loop and rules) · `cryoplug-masks-and-fitting` (masks for FSC, LocScale, spIsoNet) ·
`cryoplug-model-building` (what to do with the chosen map) · `cryoplug-relion-focused` (blurry regions).
