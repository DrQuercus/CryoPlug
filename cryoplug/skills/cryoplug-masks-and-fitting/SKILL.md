---
name: cryoplug-masks-and-fitting
description: Make masks and place models or maps in CryoPlug - soft masks from map density, from model atoms, from the simulated density of a model (molmap) or from the density near a model (create_mask), hand-drawn masks (draw_mask), rigid-body fitting of models, maps and masks into a map (fit_in_map with the ChimeraX fitmap algorithm, chimerax_fitmap, phenix_dock_in_map), and composite maps from focused refinements. Use for masks for FSC, LocScale, spIsoNet, local refinement or signal subtraction, docking predicted models, or superposing maps.
---

# Masks, rigid-body fitting and composite maps in CryoPlug

## Masks with `create_mask` (built-in, seconds)

Inputs: `map` (the grid of the mask), optional `model` (model-based sources) and `mask` (to combine). The mask is
written on the grid of the map (same box, pixel size and origin), with a soft edge, ready for CryoSPARC
(*Import 3D Volumes*, type mask), RELION, LocScale or spIsoNet.

| `source` | Typical use | Starting settings |
|---|---|---|
| `map_density` (low-pass + threshold) | FSC, LocScale, spIsoNet, deposition mask | `lowpass=15`, `dilate=3`, `soft=6`; give `mass_kda` for a reliable automatic threshold |
| `model_atoms` (chosen chains) | Tight mask around a model | `atom_radius=3`, `dilate=2`–3, `soft=6`, `chains` |
| `model_map` (simulated density, as ChimeraX `molmap`) | Smooth mask of a domain or sub-complex, without the holes of an atom mask | `molmap_resolution=10`–15, `molmap_contour=95`, `dilate=2`–3, `soft=6`–8 |
| `density_near_model` | Local (focused) refinement mask that follows the density, including unmodelled parts | `near_distance=6`–8, `dilate=3`–5, `soft=8`–12 |

- Selections: `chains` like `A, B, C:10-250`; `exclude` leaves chains or segments out; `dust` removes small blobs;
  `keep_largest` keeps the biggest connected piece.
- `threshold=0` means automatic: the level enclosing the volume of `mass_kda` (or of the selected atoms), else Otsu.
  The report shows the histogram, the threshold in σ, the volume, the equivalent mass and three slices with the
  mask: check that it does not cut density (`get_job_images`, `get_job`).
- `combine` with the input `mask`: `union`, `subtract` (whole complex minus the region to keep, for signal
  subtraction) or `intersect`; `invert` flips it.
- `model_map` also writes the simulated map (output `model_map`), useful to compare with the experimental map.

Hand-drawn masks: `draw_mask` is filled in by a person in the 3D viewer (*Mask*: spheres, boxes, cylinders,
density regions, zones around chains, molmap of chains, erase/keep), then saved as a job. Create it only to hand
over to a person; as an agent prefer `create_mask` with `model_map` or `density_near_model`.

Mask rules: soft edges of at least 6 Å; masks that are too tight inflate the FSC and remove signal in signal
subtraction (dilate a few Å); a focused-refinement region below ~100–150 kDa is hard to align (see
`cryoplug-relion-focused`).

## Rigid-body fitting with `fit_in_map` (built-in, ChimeraX fitmap algorithm)

Inputs: the object to place — `model`, or `moving_map` (a map or a mask) — and the target `map`. It writes the
placed model, or the moving map/mask resampled on the target's grid.

| Situation | Settings |
|---|---|
| Model already close (< 3 Å off) | `resolution=0` (atoms, average map value), local optimisation (`fit=true`) |
| Model farther away, low-resolution map, a domain | `resolution=6`–10 with `metric=correlation` (simulated map reaches farther); keep `polish=true` (then the atoms for the exact position) |
| Position unknown | `search=50`–100 random placements, optionally `radius` (Å) around the start; best solution first |
| Fit using some chains only (a domain), move the whole model | `chains="A"` |
| Superpose a map or mask (local refinement map, another state) | `moving_map` + `map`; overlap/correlation of voxels above the contour (`level`, `target_level`, 0 = automatic) |

Read the report: correlation (or average map value) before → after, shift and rotation, inside fraction (atoms
inside the contour, for atom fits), and the list of distinct solutions for a search (clustered at 6° and 3 Å).
A local optimisation climbs only to the nearest maximum: a model starting more than 3–4 Å away should be fitted
first with its simulated map at 6–8 Å, then on the atoms (that is what `polish` does). A person can also bring the
model close by hand in the 3D viewer (*Move*), press *Fit*, and *Save as job* (which fills `matrix`).

Alternatives: `chimerax_fitmap` (ChimeraX installed, same algorithm with ChimeraX itself) and `phenix_dock_in_map`
(global search for models or domains). After placing: `merge_models` assembles docked domains (renames clashing
chain ids), `model_tools` renames or selects chains; then real-space refinement with morphing.

## Composite maps with `composite_map` (Phenix)

For large complexes solved by a consensus map plus local refinements:

1. Import each map (one `import_cryosparc` per refinement, or the RELION focused maps, which are already on the
   consensus grid).
2. Fit the model into the consensus map.
3. `composite_map` with `model`, `reference` = consensus map, `focused_1`…`focused_8` = local maps; half maps are
   combined the same way when available (`use_half_maps`), for the FSC and local resolution of the composite.
4. Build and refine in the composite; deposit it with the consensus and local maps and their half maps. Never present
   the FSC of a composite as a gold-standard FSC.

## Related skills

`cryoplug` · `cryoplug-map-quality` (FSC and LocScale masks) · `cryoplug-model-building` (predicted models to fit) ·
`cryoplug-relion-focused` (focused refinement masks).
