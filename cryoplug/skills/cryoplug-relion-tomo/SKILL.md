---
name: cryoplug-relion-tomo
description: Subtomogram averaging with RELION 5 inside CryoPlug - tilt series imported from SerialEM, motion correction, CTF estimation, exclusion of bad tilt images, alignment (IMOD fiducials or patch tracking, AreTomo2), tomogram reconstruction, cryoCARE denoising, particle picking in tomograms (particles, spheres, filaments) or coordinate import, subtomogram extraction, particle reconstruction, initial model, 3D classification and auto-refinement of subtomograms, CTF refinement and frame alignment. Use when the data are tilt series (cryo-ET) rather than single-particle movies.
---

# From tilt series to a subtomogram average with RELION 5 in CryoPlug

Every job runs the program RELION 5.1's GUI runs, with the options it passes, from the project folder: the tilt-series
sets (`tilt_series.star` and one STAR file per tilt series), tomogram sets (`tomograms.star`), particle sets and
optimisation sets (`optimisation_set.star`: particles + tomograms + trajectories) are RELION's own. Needs RELION and
CTFFIND configured (`server_status`), plus AreTomo2 or IMOD (`batchruntomo`) for the alignment and cryoCARE for the
denoising. Long GPU jobs (AreTomo2, cryoCARE, refinements): confirm with the person first.

## The chain

| Step | Job | Starting settings | Check in the report |
|---|---|---|---|
| 1. Import | `relion_tomo_import` | `movies` and `mdocs` wildcards (SerialEM), `pixel_size`, `dose` per tilt image (0 = the mdoc doses), `tilt_axis`, `flip_hand` | tilt series, images, tilt range, dose |
| 2. Motion | `relion_tomo_motioncorr` | `even_odd=true` when denoising follows; 1 × 1 patches | motion against tilt angle |
| 3. CTF | `relion_tomo_ctffind` | `nominal_range` 10000 Å around the mdoc defocus, `dose_fading` 100 | defocus and CTF fit against tilt angle |
| 4. Exclusion | `relion_tomo_exclude` | `mode=limits` as an agent with `max_tilt`, `max_dose`, `max_ctf_res` (15–20 Å); `interactive` (default) waits for a person | images kept per tilt series |
| 5. Alignment | `relion_tomo_align` | `method=imod_fiducials` (gold beads, `fiducial_diameter`), `imod_patch` or `aretomo` (GPU); `thickness` in nm | IMOD residual error or AreTomo2 tilt-axis score per tilt series |
| 6. Tomograms | `relion_tomo_reconstruct` | `binned_pixel` 5–10 Å; `split=true` with `fourier=false` for denoising | projections, tomograms |
| 7. Denoising (optional) | `relion_tomo_denoise` | `mode=both`, `training` 2–3 tomograms spanning the defocus range | denoised tomograms |
| 8. Picking | `relion_tomo_pick` (people only: waits for clicks), `pytom_template_match` (known structure: `template` map, `particle_diameter`, `false_positives` or `cut_off`) or `relion_tomo_import_coords` | `mode` particles / spheres / filaments, `diameter`, `spacing` | particles per tomogram; for template matching the score histogram and the score-map slice |
| 9. Extraction | `relion_tomo_extract` | `box_A` ≈ 1.5–2 × the particle, `binned_px` (binned first, 4–6 Å/px), `format=stack2d` | subtomogram count, box, pixel size |
| 10. Average | `relion_tomo_reconstruct_particle` | `box`, `binning` | the map (half maps and FSC once the particles have half sets) |
| 11. Initial model | `relion_tomo_initial_model` | `symmetry`; `sigma_tilt` 5–10° for particles picked on spheres or filaments | map |
| 12. Refinement | `relion_tomo_refine3d` | `mpi` ≥ 3, reference from step 10 or 11 on the same box and pixel | FSC, resolution, refined particles |
| 13. Mask and post-processing | `relion_mask_create`, `relion_postprocess` | as in single particle | corrected FSC |
| 14. Polishing loop | `relion_tomo_ctf_refine` → `relion_tomo_frame_align` (`motion=true`) → `relion_tomo_refine3d` | half maps, mask and the post-processed map of the last refinement | resolution gain after each round |

Workflow `relion_tomo_tm` runs the whole chain for a known structure without waiting (exclusion on limits, tomograms
at 12 Å/voxel, `pytom_template_match` with the map given to its `template` step, refinement from that map).
Workflow `relion_tomo` creates steps 1–13 (denoising on; CTF refinement, frame alignment and a last refinement
optional). Its exclusion and picking jobs wait for a person. As an agent, use `relion_tomo_exclude` with
`mode=limits` after showing the person the defocus and CTF-fit plots, and `relion_tomo_import_coords` for positions
found elsewhere (crYOLO, other template matching; `pytom_template_match` does template matching itself): one STAR or text file (X Y Z, or six columns with the subtomogram angles)
per tomogram, named after it (`remove` strips a suffix such as `_coords`); `scale` brings binned coordinates to
unbinned tilt-series pixels.

`relion_tomo_class3d` sorts subtomograms (each class: a map and its particles); `relion_tomo_reconstruct_particle`
remakes the average at any box or binning (e.g. at full size after polishing). The maps chain with the rest of
CryoPlug (masks, post-processing, local resolution, LocScale, model building).

## Settings that matter

- Defocus handedness (`flip_hand`): a wrong hand mirrors the map; yes for the RELION tutorial data (EMPIAR-10164),
  decided once per microscope.
- The dose before each image comes from the acquisition order of the mdoc files; give `dose` per tilt image (or per
  frame with `dose_per_frame=true`).
- Exclude tilt images before the alignment: one bad image spoils the alignment of its tilt series.
- Particles picked on spheres or filaments carry tilt and psi priors: `sigma_tilt` keeps the searches near them.
- `pytom_template_match` (GPU, tool `pytom`): tomograms binned 6-8× (10-20 Å/voxel), raw rather than denoised;
  `invert=true` (protein dark in tomograms, white in maps); the score cut-off follows `false_positives` per tomogram
  unless `cut_off` is set; check the score histogram (true particles separate from the noise) and the picks in
  `relion_tomo_pick` (input `particles`) before long refinements.
- Particles need half sets (a refinement) before `relion_tomo_reconstruct_particle` gives half maps and an FSC.
- `relion_tomo_ctf_refine`: defocus (±`defocus_range`), contrast scale per tilt image or per tomogram (not both).
- `relion_tomo_frame_align` with `motion=true` writes particle trajectories that the next reconstruction and
  refinement apply; `shift_only=true` rescues badly aligned tilt series.

## Limits and pitfalls

- Import: an mdoc file naming no image among the files is refused (RELION 5.1.1 would pair the other tilt series with
  the wrong mdoc files), and the `prefix` is added by CryoPlug after the import (RELION's `--prefix` finds no tilt
  series).
- Reconstructions for denoising write only the two halves of each tomogram; picking and viewing use the first half
  or the denoised tomogram.
- Real RELION only warns about unknown options (and ignores them): when adding `extra_args`, spell them exactly.

## Related skills

`cryoplug` · `cryoplug-relion-spa` · `cryoplug-map-quality` · `cryoplug-troubleshooting`.
