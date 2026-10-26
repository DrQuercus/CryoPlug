# CryoPlug job types

Generated from the job registry by `cryoplug ai skills --write-reference`: do not edit by hand.
One entry per job type: name, title, program, GPU use, inputs (slot: accepted data types; `?` = optional)
and outputs (name: data type).  `describe_job_type NAME` gives the parameters with their defaults and the
detailed guidance; the server tells which programs are installed (`server_status`).

## Import

### `import_model` — Import atomic model
built-in. In: none. Out: model: model, sequence: sequence.

Import a PDB/mmCIF model from disk, or fetch an entry from the PDB or the AlphaFold database (starting model for docking and refinement).
Usually followed by: `phenix_process_predicted_model`, `fit_in_map`, `chimerax_fitmap`, `phenix_dock_in_map`, `mapmodel_validation`.

### `import_cryosparc` — Import from CryoSPARC
built-in. In: none. Out: half_maps: half_maps, map: map, map_sharp: map, mask: mask, map_locres: locres, particles: particles, latent: latent.

Import the final reconstruction of a CryoSPARC refinement job (half maps, unsharpened and sharpened maps, FSC mask, local resolution) and its particles (for heterogeneity analysis) by pointing at its job directory, e.g. /data/CS-myproject/J245. The latest iteration is selected automatically.
Usually followed by: `directional_fsc`, `locscale`, `modelangelo_build`, `cryoatom_build`, `cryoatom_enhanced`, `import_sequence`, `draw_mask`, `relion_local_refine`.

### `import_maps` — Import maps
built-in. In: none. Out: half_maps: half_maps, map: map, map_sharp: map, mask: mask.

Import half maps, full maps and masks from any software (RELION, cryoDRGN, EMDB entries...).
Usually followed by: `map_fsc`, `directional_fsc`, `locscale`, `import_model`.

### `relion_import` — Import movies (RELION)
built-in. In: none. Out: movies: movies, micrographs: micrographs.

Imports raw movies (or single-frame micrographs) with their optics group: pixel size, voltage, spherical aberration, amplitude contrast, MTF and gain reference. Writes the movies.star of relion_import; the movie folders are linked into the job folder, nothing is copied. The start of the RELION pipeline: motion correction, CTF estimation, picking...
Usually followed by: `relion_motioncorr`, `relion_estimate_gain`.

### `import_particles` — Import particles
built-in. In: none. Out: particles: particles, latent: latent.

Particles for heterogeneity analysis (cryoDRGN): a CryoSPARC job folder or .cs file (merged with its passthrough file), or a RELION .star file. Only the metadata is read: the images stay in place.
Usually followed by: `cryodrgn_backproject`, `cryodrgn_train`, `cryodrgn_abinit`, `relion_local_refine`, `relion_reconstruct`, `relion_class2d`.

### `import_sequence` — Import sequence
built-in. In: none. Out: sequence: sequence.

Paste FASTA, read a FASTA file or fetch UniProt entries. Protein, RNA and DNA records are separated automatically (ModelAngelo needs them in separate files).
Usually followed by: `modelangelo_build`, `cryoatom_build`, `cryoatom_enhanced`, `boltz_predict`, `colabfold_predict`.

### `import_volume_series` — Import volume series
built-in. In: none. Out: series: volume_series.

Series of volumes from CryoSPARC 3D Variability Display, 3D Flex Generate, cryoDRGN, RELION multi-body or any program: a folder, a ZIP file or a file pattern. One series per variability component; play them in the 3D viewer and analyse them with 'Volume series analysis'.
Usually followed by: `series_analysis`, `extract_volume`.

## Preprocessing

### `relion_ctffind` — CTF estimation (CTFFIND)
RELION. In: micrographs: micrographs. Out: micrographs: micrographs.

Fits the contrast transfer function of every micrograph with CTFFIND 4.1 or 5 (run by relion_run_ctffind): defocus, astigmatism, phase shift, and the resolution to which the fit holds (the best criterion to curate the micrographs).
Usually followed by: `relion_curate`, `relion_autopick`.

### `relion_curate` — Curate micrographs
built-in · interactive: a person works in it. In: micrographs: micrographs, particles?: particles/coordinates. Out: micrographs: micrographs, rejected: micrographs, particles: particles, coordinates: coordinates.

Keeps the micrographs whose CTF fit, defocus, astigmatism, motion or ice are within limits, as CryoSPARC's Manually Curate Exposures: the job waits with the distributions (limits dragged on the histograms), a scatter plot, the table of the micrographs and each micrograph with its CTF fit and drift, accepted or rejected one by one. Or applies the thresholds below at once. The rejected micrographs are an output too.
Usually followed by: `relion_autopick`, `relion_pick_inspect`, `relion_topaz_train`.

### `relion_topaz_denoise` — Denoise micrographs (Topaz)
RELION · GPU optional. In: micrographs: micrographs. Out: micrographs: micrographs.

Denoises the micrographs with Topaz's pretrained network (topaz denoise), as CryoSPARC's Micrograph Denoiser: the particles stand out for picking, by eye or by program, and for inspection. The denoised micrographs keep the CTF of the originals; picks made on them are extracted from the originals (the extraction translates the names).
Usually followed by: `relion_pick_inspect`, `relion_autopick`.

### `relion_estimate_gain` — Estimate gain reference (RELION)
RELION. In: movies: movies. Out: movies: movies, gain: files.

relion_estimate_gain: a gain reference estimated from the movies themselves (the inverse of the average of many frames), when the gain reference of the session is missing or doubtful. The movies come out with this gain attached: motion correction applies it.
Usually followed by: `relion_motioncorr`.

### `relion_live` — Live preprocessing (RELION)
RELION · GPU optional. In: none. Out: micrographs: micrographs, all_micrographs: micrographs, rejected: micrographs, movies: movies, coordinates: coordinates, particles: particles, classes: class_averages, classified_particles: particles.

Processes the movies while they are being collected, as CryoSPARC Live: at every cycle the new movies of the folder are imported, motion-corrected, CTF-estimated, sorted on thresholds and, if asked, picked and extracted (RELION's jobs with --only_do_unfinished). The panel follows the session: counts, rate, CTF fit, defocus, motion and ice, the exposures and the picks; the acceptance limits can be changed there, the session paused and resumed, and a streaming 2D classification follows the particles. 'Stop after this cycle' ends the job with its micrographs, picks, particles and 2D classes for the next jobs.
Usually followed by: `relion_curate`, `relion_class2d`, `relion_pick_inspect`, `relion_extract`.

### `relion_motioncorr` — Motion correction (RELION)
RELION · GPU optional. In: movies: movies. Out: micrographs: micrographs.

Aligns the frames of each movie (beam-induced motion, in patches) and sums them with dose weighting: RELION's own CPU implementation of the MotionCor2 algorithm, or UCSF MotionCor2 on GPUs. Also writes the summed power spectra that CTFFIND uses. Gives the micrographs and the motion of every movie (for Bayesian polishing).
Usually followed by: `relion_ctffind`.

## Picking

### `relion_autopick` — Auto-picking (RELION)
RELION · GPU optional. In: micrographs: micrographs, references?: class_averages, map?: map, topaz_model?: topaz_model. Out: coordinates: coordinates.

Picks particles on the micrographs with relion_autopick: Laplacian-of-Gaussian blob detection (no template: the way to start a new data set), template matching against 2D class averages or projections of a 3D map, or Topaz (a neural network, general or trained on this data set).
Usually followed by: `relion_extract`, `relion_pick_inspect`.

### `relion_pick_inspect` — Inspect picks / manual picking
built-in · interactive: a person works in it. In: micrographs: micrographs, coordinates?: coordinates. Out: coordinates: coordinates.

Shows every micrograph with its picked particles, as CryoSPARC's Inspect Picks and Manual Picker: a figure-of-merit threshold set on its histogram, particles removed or added with a click, zoom, pan and low-pass filter. Without picks, particles are picked by hand (a training set for Topaz). Writes the coordinate files RELION's extraction reads.
Usually followed by: `relion_extract`, `relion_topaz_train`.

### `relion_extract` — Particle extraction (RELION)
RELION. In: micrographs: micrographs, coordinates?: coordinates, particles?: particles. Out: particles: particles.

Cuts the particles out of the micrographs (relion_preprocess --extract), with their CTF: box, down-scaling, contrast inversion and normalisation as RELION expects. Also re-extracts refined particles (re-centred on their refined positions, or at another pixel size).
Usually followed by: `relion_class2d`, `relion_initial_model`, `relion_refine3d`.

### `relion_topaz_train` — Topaz training (RELION)
RELION · GPU. In: micrographs: micrographs, particles?: particles, coordinates?: coordinates. Out: topaz_model: topaz_model.

Trains a Topaz picking network on this data set (relion_autopick --topaz_train): from particles picked and cleaned once (the particles of good 2D classes) or from coordinates. Pick the whole data set with the trained model next (Auto-picking, Topaz).
Usually followed by: `relion_autopick`.

## Classification

### `relion_class2d` — 2D classification (RELION)
RELION · GPU optional. In: particles: particles. Out: classes: class_averages, particles: particles.

Sorts the particles into 2D class averages (relion_refine): VDAM, the fast gradient algorithm of RELION 4/5, or the classic EM algorithm. The class averages show up while it runs; the particles keep their class for the selection of the good classes (Select 2D classes).
Usually followed by: `relion_select2d`, `relion_class2d`, `relion_particle_curate`.

### `relion_class3d_global` — 3D classification (RELION)
RELION · GPU optional. In: particles: particles, map: map, mask?: mask. Out: maps: volume_series, particles: particles, map_1: map, class_1: particles.

Sorts the particles into 3D classes against a reference (relion_refine --K): global angular searches, local ones, or none (classification with the poses kept). Every class gives a map and its particles; Blush regularisation helps small or flexible particles.
Usually followed by: `relion_refine3d`, `relion_class3d_global`, `relion_hetero_refine`, `relion_particle_curate`.

### `relion_helix_inimodel2d` — Amyloid initial model (RELION)
RELION. In: classes: class_averages. Out: map: map, map_1: map.

relion_helix_inimodel2d: a 3D initial model of an amyloid fibril from its 2D class averages, which are aligned into one long 2D image of a whole cross-over and turned around the axis by the cross-over distance (RELION's amyloid tutorial). One map per class; the reference of the helical 3D refinement.
Usually followed by: `relion_refine3d`, `relion_class3d_global`, `relion_helix_toolbox`.

### `relion_hetero_refine` — Heterogeneous refinement (RELION)
RELION · GPU optional. In: particles: particles, ref_1: map, ref_2: map, ref_3?: map, ref_4?: map, ref_5?: map, ref_6?: map, ref_7?: map, ref_8?: map, mask?: mask. Out: maps: volume_series, particles: particles, map_1: map, class_1: particles.

Sorts the particles among several reference maps while refining all of them, as CryoSPARC's Heterogeneous Refinement (relion_refine with one reference per class, listed in a STAR file): a good map with decoys (junk, broken particles, an empty micelle) cleans a particle set; the classes of an initial model separate conformations or compositions. References from other jobs are resampled onto the grid of the particles.
Usually followed by: `relion_refine3d`, `relion_class3d_global`.

### `relion_select2d` — Select 2D classes
RELION · interactive: a person works in it. In: classes: class_averages. Out: particles: particles, rejected: particles, class_averages: class_averages.

Keeps the particles of the good 2D classes. As in CryoSPARC, the job shows the class averages and waits: click the good classes (sorted by size or resolution), then Done. The classes can also be listed, or chosen automatically by RELION's class ranker. Gives the selected particles, the rejected ones, and the selected class averages (templates for picking).
Usually followed by: `relion_initial_model`, `relion_class2d`, `relion_class3d_global`, `relion_refine3d`, `relion_topaz_train`, `relion_autopick`.

### `relion_select3d` — Select 3D classes
built-in · interactive: a person works in it. In: particles: particles. Out: particles: particles, rejected: particles.

Keeps the particles of the 3D classes clicked in a gallery of the class maps (or listed): merges the good classes of a 3D classification or a heterogeneous refinement, the others are an output too.
Usually followed by: `relion_refine3d`, `relion_class3d_global`.

## Reconstruction

### `relion_refine3d` — 3D auto-refine (RELION)
RELION · GPU optional. In: particles: particles, map: map, mask?: mask. Out: map: map, half_maps: half_maps, map_sharp: map, mask: mask, fsc: fsc, particles: particles.

Gold-standard 3D refinement of the particles against a reference (relion_refine --auto_refine): global angular searches first, local ones as it converges, two independent half sets. Gives the map, the half maps, the FSC, the refined particles and, with a mask, the post-processed map.
Usually followed by: `relion_mask_create`, `relion_ctf_refine`, `relion_polish`, `relion_postprocess`, `relion_locres`, `relion_multibody`, `relion_dynamight`, `relion_local_refine`.

### `relion_initial_model` — 3D initial model (RELION)
RELION · GPU optional. In: particles: particles. Out: map: map, maps: volume_series, particles: particles.

Makes a first 3D map from the particles without any reference (relion_refine --grad --denovo_3dref, VDAM), then aligns it on the symmetry axes and applies the symmetry (relion_align_symmetry): RELION's ab initio. Several classes separate different structures.
Usually followed by: `relion_class3d_global`, `relion_hetero_refine`, `relion_refine3d`.

### `relion_class3d` — RELION 3D classification (focused)
RELION · GPU optional. In: particles: particles, map: map, mask?: mask. Out: maps: volume_series, particles: particles, class_1: particles, map_1: map.

3D classification in RELION of particles with poses, focused by a mask, without alignment (or with local searches): separates the states of a region, typically on subtracted particles. Every class gives a map and its particles.
Usually followed by: `relion_local_refine`, `relion_reconstruct`, `cryodrgn_train`.

### `relion_local_refine` — RELION local refinement
RELION · GPU optional. In: particles: particles, map: map, mask?: mask. Out: map: map, half_maps: half_maps, map_sharp: map, mask: mask, fsc: fsc, particles: particles.

Gold-standard 3D auto-refinement in RELION with local angular searches around the poses of the particles (a CryoSPARC consensus refinement or an earlier RELION job), focused by a mask: the RELION counterpart of CryoSPARC's local refinement, with Blush regularisation for small or flexible regions. CryoSPARC half sets are kept. Writes the map, half maps, FSC, refined particles and, with a mask, the post-processed map, all on the grid of the consensus map.
Usually followed by: `relion_subtract`, `composite_map`, `locscale`, `modelangelo_build`, `local_resolution`.

### `relion_postprocess` — RELION post-processing
RELION. In: half_maps: half_maps, mask: mask. Out: map_sharp: map, map_masked: map, fsc: fsc, map_locres: locres.

relion_postprocess of two half maps: FSC with the mask corrected by phase randomisation, B-factor sharpening (automatic Guinier fit or given), optional local resolution. Works on the half maps of any job (CryoSPARC imports, RELION refinements, composite maps).
Usually followed by: `locscale`, `modelangelo_build`, `deposition_package`.

### `relion_reconstruct` — RELION reconstruction
RELION. In: particles: particles, mask?: mask. Out: map: map, half_maps: half_maps, fsc: fsc.

Reconstructs the two half maps and the full map of particles from their poses, without alignment (relion_reconstruct): a full-resolution map of a particle subset (a cryoDRGN selection, a class), with its gold-standard FSC.
Usually followed by: `relion_local_refine`, `map_fsc`, `locscale`.

### `relion_subtract` — RELION signal subtraction
RELION. In: particles: particles, mask: mask. Out: particles: particles, map: map, mask: mask.

Removes from each particle image the projection of everything outside a mask (relion_particle_subtract), re-centres the particles on the masked region and puts them in a smaller box: the particles of a focused refinement or classification. Optionally expands the symmetry first, so that every copy of a symmetric complex becomes a particle. Needs particles refined in RELION (it uses that refinement's model).
Usually followed by: `relion_local_refine`, `relion_class3d`, `cryodrgn_train`.

## Map processing

### `spisonet` — Anisotropy correction (spIsoNet)
spIsoNet · GPU. In: half_maps: half_maps, mask: mask. Out: half_maps: half_maps, map: map, fsc3d: map.

Self-supervised correction of the resolution anisotropy caused by preferred orientation (spIsoNet, Liu et al. 2025): computes the 3D FSC, then trains a network on the half maps to restore the poorly sampled directions. The corrected half maps are no longer independent: never use them for the deposited FSC.
Usually followed by: `modelangelo_build`, `cryoatom_build`, `cryoatom_enhanced`, `isolde_session`.

### `phenix_auto_sharpen` — Auto-sharpen (Phenix)
Phenix. In: map: map, half_maps?: half_maps. Out: map: map.

Global sharpening maximising map detail and connectivity (phenix.auto_sharpen). With Phenix 2, 'Map sharpening (Phenix)' is the current tool (auto_sharpen still runs, with --force).
Usually followed by: `modelangelo_build`, `fit_in_map`, `chimerax_fitmap`.

### `composite_map` — Composite map (focused maps)
Phenix. In: model: model, reference: map, focused_1: map, focused_2?: map, focused_3?: map, focused_4?: map, focused_5?: map, focused_6?: map, focused_7?: map, focused_8?: map. Out: map: map, half_maps: half_maps.

Combines the maps of local (focused) refinements into one composite map with phenix.combine_focused_maps: each part of the model takes the map that fits it best. The half maps of every map are combined the same way when available, for the FSC and the local resolution of the composite.
Usually followed by: `local_resolution`, `phenix_real_space_refine`, `isolde_session`.

### `create_mask` — Create mask
built-in. In: map: map, model?: model, mask?: mask. Out: mask: mask, model_map: map.

Soft-edged mask from the map density (low-pass + threshold), from the atoms of chosen chains, from the density simulated from them as ChimeraX's molmap, or from the map density near them, with dilation and soft edge in Å as in CryoSPARC. For FSC, local refinement, LocScale, spIsoNet or signal subtraction; can be combined with another mask.
Usually followed by: `map_fsc`, `locscale`, `spisonet`, `local_resolution`, `relion_local_refine`, `relion_subtract`.

### `deepemhancer` — DeepEMhancer
DeepEMhancer · GPU. In: half_maps?: half_maps, map?: map. Out: map: map.

Deep-learning post-processing producing a masked, locally sharpened-like map (Sanchez-Garcia et al. 2021).
Usually followed by: `fit_in_map`, `chimerax_fitmap`, `isolde_session`.

### `phenix_resolve_cryo_em` — Density modification (Phenix)
Phenix. In: half_maps: half_maps, sequence?: sequence. Out: map: map.

Maximum-likelihood density modification from half maps (phenix.resolve_cryo_em, Terwilliger et al. 2020). Typically improves interpretability and the half-map FSC. A sequence helps.
Usually followed by: `modelangelo_build`, `cryoatom_build`, `cryoatom_enhanced`, `phenix_real_space_refine`.

### `directional_fsc` — Directional resolution (3D FSC)
built-in. In: half_maps: half_maps, mask?: mask. Out: report: report.

Conical FSC in every direction (3D FSC, Tan et al. 2017): reveals the resolution anisotropy caused by preferred orientation, with the best / worst directions and a direction map. Strong anisotropy: consider spIsoNet, tilted data collection or orientation rebalancing.
Usually followed by: `spisonet`, `locscale`.

### `emready` — EMReady2
EMReady2 · GPU. In: map: map, mask?: mask. Out: map: map.

Deep-learning map enhancement aware of the local quality of the map (EMReady2, Cao et al. 2026; EMReady 1 is run the same way). A mask restricts the result to the region of interest.
Usually followed by: `fit_in_map`, `chimerax_fitmap`, `modelangelo_build`.

### `emmernet` — EMmerNet feature enhancement
LocScale 2 / EMmerNet · GPU. In: half_maps?: half_maps, map?: map, mask?: mask. Out: map: map, confidence: map, map_locscale: map.

Confidence-weighted, feature-enhanced map from EMmerNet (locscale feature_enhance) with Monte-Carlo uncertainty estimation. Treat high-uncertainty regions with caution.
Usually followed by: `modelangelo_build`, `isolde_session`.

### `map_fsc` — Half-map FSC
built-in. In: half_maps: half_maps, mask?: mask. Out: fsc: fsc.

Gold-standard Fourier shell correlation between half maps: unmasked, masked and corrected by high-resolution phase randomisation. Writes an EMDB-compatible FSC XML for deposition.
Usually followed by: `directional_fsc`, `deposition_package`.

### `relion_helix_toolbox` — Helical symmetry (RELION)
RELION. In: map?: map. Out: map: map.

relion_helix_toolbox on the map of a filament: local search of the helical twist and rise (--search), then the symmetry found imposed in real space; a given symmetry imposed (--impose); or a cylinder as the initial reference of a helical refinement (--cylinder).
Usually followed by: `relion_refine3d`, `relion_mask_create`, `relion_postprocess`.

### `locscale` — LocScale 2
LocScale 2 / EMmerNet · GPU. In: half_maps?: half_maps, map?: map, model?: model, mask?: mask. Out: map: map.

Local amplitude scaling. Model-free (EMmerNet reference, default), pseudo-atomic model, model-based (requires a model; REFMAC/Servalcat used for B-factor refinement) or hybrid (partial model completed with pseudo-atoms). Use unfiltered, unsharpened half maps.
Usually followed by: `modelangelo_build`, `cryoatom_build`, `cryoatom_enhanced`, `fit_in_map`, `chimerax_fitmap`, `isolde_session`.

### `phenix_local_aniso_sharpen` — Local anisotropic sharpening (Phenix)
Phenix. In: half_maps: half_maps, model?: model. Out: map: map.

Local, anisotropic sharpening and resolution-dependent scaling from half maps (phenix.local_aniso_sharpen), optionally guided by a model.
Usually followed by: `modelangelo_build`, `phenix_real_space_refine`.

### `local_resolution` — Local resolution
Phenix. In: half_maps: half_maps, map?: map, mask?: mask, model?: model. Out: local_resolution: locres, per_residue: report.

Local resolution map from the two half maps (phenix.local_resolution), its distribution in the molecule and, with a model, per chain and per residue. The 3D viewer colours a map by it.
Usually followed by: `create_mask`, `locscale`, `mapmodel_validation`.

### `map_tools` — Map operations
built-in. In: map: map, mask?: mask. Out: map: map.

Built-in map manipulations applied in order: set pixel size, crop/pad, flip handedness, global B-factor sharpening/blurring, low-pass filter, mask, normalisation.
Usually followed by: `modelangelo_build`, `cryoatom_build`, `cryoatom_enhanced`, `fit_in_map`, `chimerax_fitmap`.

### `phenix_map_sharpening` — Map sharpening (Phenix)
Phenix. In: half_maps?: half_maps, map?: map, model?: model, sequence?: sequence. Out: map: map.

Phenix's sharpening tool (phenix.map_sharpening, which replaces auto_sharpen and local_aniso_sharpen in Phenix 2): shell scaling from two half maps (or a map and a fitted model), anisotropic and optionally local; or B-factor sharpening of a single map.
Usually followed by: `modelangelo_build`, `cryoatom_build`, `cryoatom_enhanced`, `phenix_real_space_refine`, `local_resolution`.

### `relion_mask_create` — Mask creation (RELION)
RELION. In: map: map. Out: mask: mask.

relion_mask_create: the map low-pass filtered, thresholded, extended by a few pixels and given a soft raised-cosine edge. The solvent mask of post-processing, refinement and CTF refinement.
Usually followed by: `relion_refine3d`, `relion_ctf_refine`, `relion_polish`, `relion_postprocess`, `relion_locres`.

### `draw_mask` — Mask drawn in the 3D viewer
built-in. In: map: map, model?: model. Out: mask: mask.

Mask drawn by hand on a map in the 3D viewer, as in Chimera: spheres, boxes and cylinders placed with the mouse, connected regions of density picked at the contour level, zones around chains, the density simulated from chains of a model (molmap), the density above a level or an existing mask, added, erased or intersected; then dilated and given a soft edge. Computed here on the full grid of the map, with its centre of mass and the box to re-centre particles on it (signal subtraction).
Usually followed by: `relion_subtract`, `relion_local_refine`, `relion_class3d`, `map_fsc`, `locscale`.

## Heterogeneity

### `relion_multibody` — 3D multi-body refinement (RELION)
RELION · GPU optional. In: particles: particles, body_1: mask, body_2: mask, body_3?: mask, body_4?: mask, body_5?: mask, body_6?: mask. Out: particles: particles, body_1: map, motion_1: volume_series, selected: particles.

Refines rigid bodies of a flexible complex separately, each with its own mask, starting from a consensus auto-refinement (relion_refine --multibody_masks), then analyses their motions (relion_flex_analyse): the main movements as series of maps to play in the 3D viewer.
Usually followed by: `relion_subset`, `relion_refine3d`, `composite_map`.

### `relion_dynamight` — DynaMight flexibility (RELION)
RELION · GPU. In: particles: particles, map: map. Out: half_maps: half_maps, map: map.

Learns the continuous motions of the particles as deformations of a Gaussian model of the consensus map (DynaMight, RELION 5), then optionally the inverse deformations and a deformed back-projection: a consensus map improved where the particle moves. Explore the latent space afterwards with relion_python_dynamight explore-latent-space (an interactive window).
Usually followed by: `relion_postprocess`, `locscale`, `relion_locres`.

### `extract_volume` — Extract volume from series
built-in. In: series: volume_series. Out: map: map.

Takes one volume of a series (a cryoDRGN cluster, a 3D variability frame) as a map, to build or refine a model of that state.
Usually followed by: `fit_in_map`, `chimerax_fitmap`, `isolde_session`, `mapmodel_validation`.

### `select_particles` — Select particles (latent space)
built-in. In: latent: latent. Out: particles: particles, excluded: particles.

Keeps or removes particles chosen in a latent space (cryoDRGN or 3D variability): whole clusters, a region drawn with the lasso of the explorer, or the outliers far from the other particles. To discard junk or to isolate a state. Writes the selection and the particles left out (CryoSPARC .cs or RELION .star, plus cryoDRGN indices): train again with cryoDRGN, or classify / refine in CryoSPARC.
Usually followed by: `cryodrgn_train`, `cryodrgn_abinit`, `cryodrgn_backproject`.

### `series_analysis` — Volume series analysis
built-in. In: series: volume_series, model?: model, mask?: mask. Out: variability: variability, mean: map, difference: map.

Interprets a volume series (3D variability frames, cryoDRGN clusters or trajectories): variability map (where the density changes), mean and difference maps, similarity between frames and, with a model, the chains that move or come and go.
Usually followed by: `extract_volume`, `select_particles`.

### `cryodrgn_analyze` — cryoDRGN analysis
cryoDRGN · GPU. In: latent: latent. Out: latent: latent, kmeans: volume_series, pc1: volume_series, pc2: volume_series, particles: particles.

Analyses a trained cryoDRGN model again: another epoch (an earlier one when the last looks overtrained), more clusters (to find rare states), longer trajectories, flipped hand... Gives the same explorer, volumes and diagnostics as the training.
Usually followed by: `select_particles`, `cryodrgn_trajectory`, `cryodrgn_landscape`, `series_analysis`.

### `cryodrgn_continue` — cryoDRGN continue training
cryoDRGN · GPU. In: latent: latent. Out: latent: latent, kmeans: volume_series, pc1: volume_series, pc2: volume_series, particles: particles, reconstruction: map.

Trains a cryoDRGN model for more epochs from its last checkpoint (same network, same particles), then analyses it again: for a training that has not converged yet (see the convergence check).
Usually followed by: `cryodrgn_convergence`, `select_particles`, `cryodrgn_landscape`.

### `cryodrgn_convergence` — cryoDRGN convergence check
cryoDRGN · GPU. In: latent: latent. Out: rep_1: volume_series.

Tells whether a cryoDRGN training has converged (Kinman et al. 2023): the losses, how far the particles still move in the latent space from one epoch to the next, whether their neighbours stay the same, and how the volumes of representative particles change across epochs. Gives the epoch from which the results are stable, or advises to train longer.
Usually followed by: `cryodrgn_continue`, `cryodrgn_analyze`, `select_particles`, `cryodrgn_landscape`.

### `cryodrgn_backproject` — cryoDRGN input check (backprojection)
cryoDRGN · GPU. In: particles: particles. Out: backprojection: map, particles_prepared: particles.

Checks that cryoDRGN reads the particles correctly before a long training (Kinman et al. 2023): the poses and CTF are converted, the images downsampled, and the first particles backprojected into a map to compare with the consensus refinement (same shape, positive density, signal in the half-set FSC). The prepared particles are reused by the trainings that follow.
Usually followed by: `cryodrgn_train`.

### `cryodrgn_landscape` — cryoDRGN landscape analysis
cryoDRGN · GPU. In: latent: latent, mask?: mask. Out: latent: latent, states: volume_series, vol_pc1: volume_series, vol_pc2: volume_series.

Maps the conformational landscape from the volumes rather than from the latent space (cryodrgn analyze_landscape): many volumes are generated across the latent space, compared by PCA inside a mask, and grouped into states by hierarchical clustering. Results: the state volumes, trajectories along the main volume components, and the particles of each state, ready to select.
Usually followed by: `select_particles`, `series_analysis`, `extract_volume`, `cryodrgn_trajectory`.

### `cryodrgn_train` — cryoDRGN training
cryoDRGN · GPU. In: particles: particles. Out: latent: latent, kmeans: volume_series, pc1: volume_series, pc2: volume_series, particles_prepared: particles, backprojection: map.

Heterogeneous reconstruction with cryoDRGN: a neural network learns a latent space of the conformations and compositions in the particles (poses of the consensus refinement). The inputs are checked first, the losses followed live, then the latent space is analysed: interactive explorer (colour by defocus, views...), cluster volumes with junk warnings, trajectories along the main components, and advice on what to do next.
Usually followed by: `cryodrgn_convergence`, `select_particles`, `cryodrgn_continue`, `cryodrgn_analyze`, `cryodrgn_landscape`, `cryodrgn_trajectory`, `series_analysis`.

### `cryodrgn_trajectory` — cryoDRGN trajectory
cryoDRGN · GPU. In: latent: latent. Out: trajectory: volume_series.

Volumes along a path in the cryoDRGN latent space between chosen clusters or particles: through the particles (a path the data support, cryodrgn graph_traversal) or in a straight line. A movie of the transition, to play in the 3D viewer.
Usually followed by: `series_analysis`, `extract_volume`.

### `cryodrgn_volumes` — cryoDRGN volumes at particles
cryoDRGN · GPU. In: latent: latent. Out: volume: map, volumes: volume_series.

Generates the volumes of chosen particles (from their latent coordinates), or of a region drawn with the lasso of the latent explorer: the volume at its centre, or several volumes spread over it. To look at a part of the latent space that the clusters do not show.
Usually followed by: `series_analysis`, `extract_volume`.

### `cryodrgn_abinit` — cryoDRGN-AI ab initio reconstruction
cryoDRGN · GPU. In: particles: particles, reference?: map. Out: latent: latent, kmeans: volume_series, pc1: volume_series, pc2: volume_series, reconstruction: map, particles: particles, validation: map, validation_half_maps: half_maps, reconstructions: volume_series, particles_prepared: particles, backprojection: map.

Heterogeneous reconstruction without consensus poses (cryoDRGN-AI, cryodrgn abinit): the pose and the latent coordinates of every particle are learnt together, from the images and their CTF. Free from the bias of a consensus refinement and robust to junk, it can start right after a 2D classification. Live progress per phase, pose stability, comparison with the consensus poses (hand, frame, pose difference per particle), validation backprojection, particles written back with their learned poses, and the same latent space tools as the cryoDRGN trainings.
Usually followed by: `cryodrgn_convergence`, `select_particles`, `cryodrgn_continue`, `cryodrgn_analyze`, `cryodrgn_landscape`, `cryodrgn_trajectory`, `cryodrgn_train`, `series_analysis`.

## Model building

### `colabfold_predict` — AlphaFold2 prediction (ColabFold)
ColabFold (local) · GPU. In: sequence: sequence. Out: model: model.

Predict starting models with a local ColabFold installation (AlphaFold2). Records can be predicted separately or as one complex (with copies for homo-oligomers). The MSAs come from the ColabFold server unless single-sequence mode is chosen (confidential sequences).
Usually followed by: `phenix_process_predicted_model`, `fit_in_map`, `chimerax_fitmap`.

### `boltz_predict` — Complex prediction (Boltz-2)
Boltz-2 · GPU. In: sequence: sequence. Out: model: model.

Predict a starting model of the whole complex — proteins, RNA/DNA and ligands (CCD code or SMILES) — with a local Boltz-2 installation (open AlphaFold3-class model), then fit it into the map.
Usually followed by: `phenix_process_predicted_model`, `fit_in_map`, `chimerax_fitmap`, `phenix_dock_in_map`.

### `cryoatom_build` — CryoAtom2 build
CryoAtom2 · GPU. In: map: map, sequence?: sequence, mask?: mask, backbone?: model. Out: model: model, model_raw: model, sequence: sequence.

Automated building of proteins, RNA/DNA and their complexes (CryoAtom2, Su et al. 2025). With a sequence: sequence-guided building. Without: residue types are assigned from the density; give a sequence database (e.g. the UniProt proteome of your organism) to identify the chains, then rebuild with the 'Identified sequences' output. A mask restricts building to one region.
Usually followed by: `phenix_real_space_refine`, `isolde_session`, `mapmodel_validation`.

### `cryoatom_enhanced` — CryoAtom2 enhanced (best of several runs)
CryoAtom2 · GPU. In: map: map, half_maps?: half_maps, sequence?: sequence, mask?: mask, alt_map_1?: map, alt_map_2?: map, alt_map_3?: map. Out: model: model, model_raw: model, sequence: sequence, candidates: files, map_improved: map, map_other_hand: map, model_other_hand: model.

Runs CryoAtom2 several times (it builds differently each time) with several settings, and optionally on re-sharpened or other maps; scores every model against the experimental map (residues fitted to the density by Q-score, the same density traced twice counted once) and keeps the best, with its gaps filled by well-fitted pieces of the other runs. With sequence databases, identifies the chains and rebuilds with them until no new sequence appears. Can improve the map with the best model (LocScale hybrid or model-based, Phenix) and build again while the score improves, or try other sharpening methods (EMmerNet, DeepEMhancer, EMReady2, density modification). Checks the hand of the map when the model comes out in short pieces.
Usually followed by: `phenix_real_space_refine`, `isolde_session`, `mapmodel_validation`, `checkmysequence`.

### `phenix_dock_in_map` — Dock in map (Phenix)
Phenix. In: model: model, map: map. Out: model: model.

Global search for the position of a model (or domains) in the map (phenix.dock_in_map).
Usually followed by: `phenix_real_space_refine`, `merge_models`.

### `fit_in_map` — Fit in map (rigid body)
built-in. In: model?: model, moving_map?: map/mask, map?: map. Out: model: model, map: map, mask: mask.

Rigid-body placement of a model, a map or a mask in a map, as ChimeraX's fitmap (no ChimeraX needed): from the position set by hand in the 3D viewer (Move), a local optimisation of the average map value at the atoms, or of the overlap / correlation with the model's simulated map at a resolution, or a search from random placements. Writes the placed model, or the map resampled on the target's grid.
Usually followed by: `phenix_real_space_refine`, `servalcat_refine`, `isolde_session`, `mapmodel_validation`, `merge_models`.

### `modelangelo_hmm_search` — Identify chains (ModelAngelo HMM search)
ModelAngelo. In: model: model. Out: sequence: sequence, report: report.

Identify unknown proteins (or RNA/DNA) in the map: the HMM profiles written by 'ModelAngelo build' without a sequence are searched against a sequence database (model_angelo hmm_search). The best hits become a sequence output to rebuild the model with ModelAngelo or CryoAtom2.
Usually followed by: `cryoatom_build`, `cryoatom_enhanced`, `modelangelo_build`.

### `phenix_elbow` — Ligand restraints (eLBOW)
Phenix. In: none. Out: restraints: restraints, ligand: model.

Geometry restraints (CIF) and starting coordinates for a ligand from its CCD code or SMILES (phenix.elbow). Connect the restraints to real-space refinement or Servalcat.
Usually followed by: `phenix_real_space_refine`, `servalcat_refine`, `merge_models`.

### `modelangelo_build` — ModelAngelo build
ModelAngelo · GPU. In: map: map, sequence?: sequence, mask?: mask. Out: model: model, model_raw: model.

Automated model building (Jamali et al. 2024). With a sequence: 'build' (protein, RNA and DNA FASTA are passed separately). Without: 'build_no_seq', which also writes HMM profiles to search sequence databases for unknown proteins. Use the best sharpened / post-processed map.
Usually followed by: `phenix_real_space_refine`, `isolde_session`, `mapmodel_validation`, `modelangelo_hmm_search`.

### `phenix_process_predicted_model` — Process predicted model (Phenix)
Phenix. In: model: model. Out: model: model.

Trim low-confidence residues of an AlphaFold/ColabFold model, convert pLDDT to B-factors and optionally split into compact domains for docking (phenix.process_predicted_model).
Usually followed by: `fit_in_map`, `chimerax_fitmap`, `phenix_dock_in_map`.

### `chimerax_fitmap` — Rigid-body fit (ChimeraX)
UCSF ChimeraX (+ ISOLDE). In: model: model, map: map. Out: model: model.

Rigid-body fit of a model into the map with ChimeraX 'fitmap' using a simulated map at the map resolution. Local optimisation from the current position, or a global search.
Usually followed by: `phenix_real_space_refine`, `isolde_session`, `merge_models`.

## Interactive

### `coot_session` — Coot session
Coot · interactive: a person works in it. In: model: model, map: map, map2?: map, diff_map?: map. Out: model: model.

Interactive model building and real-space refinement in Coot. Save coordinates in the job folder (File > Save Coordinates) then finish the job.
Usually followed by: `phenix_real_space_refine`.

### `isolde_session` — ISOLDE session
UCSF ChimeraX (+ ISOLDE) · interactive: a person works in it. In: model: model, map: map, map2?: map, diff_map?: map. Out: model: model.

Interactive, physics-based rebuilding in ISOLDE (molecular dynamics flexible fitting in ChimeraX). The model and maps are loaded and associated automatically; save the result in the job folder (e.g. 'save isolde_model.cif models #1') then finish the job.
Usually followed by: `phenix_real_space_refine`, `servalcat_refine`.

## Refinement

### `phenix_douse` — Add waters (phenix.douse)
Phenix. In: model: model, map: map. Out: model: model.

Place ordered water molecules in the map around the model (phenix.douse). Useful for maps better than about 2.5-3 Å; refine and inspect the waters afterwards.
Usually followed by: `phenix_real_space_refine`, `servalcat_refine`.

### `relion_polish` — Bayesian polishing (RELION)
RELION. In: particles: particles, micrographs: micrographs, half_maps: half_maps, mask: mask, params?: polish_params. Out: particles: particles, polish_params: polish_params.

Tracks the motion of every particle through the movie frames and re-extracts them with frame weights fitted to the data (relion_motion_refine). Train the motion parameters once on a subset (optional), then polish; refine the polished ('shiny') particles again.
Usually followed by: `relion_refine3d`.

### `relion_ctf_refine` — CTF refinement (RELION)
RELION. In: particles: particles, half_maps: half_maps, mask: mask. Out: particles: particles.

Refines the optics of the data set and the CTF of every particle against the refined map (relion_ctf_refine): anisotropic magnification, beam tilt, trefoil and 4th-order aberrations per optics group, then defocus per particle and astigmatism per micrograph. 'Complete' runs the three passes of the RELION tutorial in a row; refine the particles again afterwards.
Usually followed by: `relion_refine3d`, `relion_polish`.

### `phenix_real_space_refine` — Real-space refinement (Phenix)
Phenix. In: model: model, map: map, restraints?: restraints. Out: model: model.

phenix.real_space_refine: global minimisation with secondary-structure, Ramachandran and rotamer restraints, morphing, simulated annealing and ADP refinement against the map.
Usually followed by: `mapmodel_validation`, `phenix_validation_cryoem`, `isolde_session`, `predeposition_check`.

### `servalcat_refine` — Refinement (Servalcat)
Servalcat. In: model: model, half_maps?: half_maps, map?: map, mask?: mask, restraints?: restraints. Out: model: model, fofc: map, model_expanded: model.

servalcat refine_spa_norefmac: reciprocal-space refinement against unsharpened half maps with a Gaussian likelihood target and Servalcat's own engine (no REFMAC needed), atomic B-factors, sharpened Fo and Fo-Fc difference maps (Yamashita et al. 2021).
Usually followed by: `mapmodel_validation`, `coot_session`, `phenix_douse`.

## Validation

### `phenix_validation_cryoem` — Comprehensive validation (Phenix)
Phenix. In: model: model, map: map. Out: report: report.

phenix.validation_cryoem: MolProbity geometry, model-map correlation (CC_mask, CC_box...), map-model FSC and per-residue fit, as reported in wwPDB/EMDB validation.
Usually followed by: `predeposition_check`, `isolde_session`.

### `phenix_emringer` — EMRinger
Phenix. In: model: model, map: map. Out: report: report.

Side-chain density-based validation of model-to-map fit (Barad et al. 2015). Meaningful below ~4.5 Å.
Usually followed by: `isolde_session`.

### `relion_locres` — Local resolution (RELION)
RELION. In: half_maps: half_maps, mask?: mask. Out: locres: locres, map: map.

Local resolution with relion_postprocess --locres: FSC in a soft sphere moved over the map. Gives the local resolution map (to colour the map in the 3D viewer) and the map locally filtered to it.
Usually followed by: `locscale`, `modelangelo_build`, `relion_multibody`.

### `mapmodel_validation` — Map-model validation (Q-score, FSC)
built-in. In: model: model, map: map, fsc?: fsc. Out: report: report.

Built-in map-model metrics, no external program needed: per-atom/residue Q-scores (after Pintilie et al. 2020) compared with the value expected at this resolution, map-model FSC, CC_mask/CC_box and atom inclusion at the suggested contour level.
Usually followed by: `isolde_session`, `checkmysequence`, `predeposition_check`.

### `phenix_molprobity` — MolProbity geometry
Phenix. In: model: model. Out: report: report.

All-atom contacts, Ramachandran, rotamers and covalent geometry (phenix.molprobity).
Usually followed by: `coot_session`, `isolde_session`.

### `checkmysequence` — Sequence register check (checkMySequence)
checkMySequence. In: model: model, map: map, sequence: sequence. Out: report: report.

Finds sequence-register shifts, chains matching no input sequence, sequence mismatches and numbering issues in a model built into a cryo-EM map (Chojnowski 2022): errors that geometry validation misses.
Usually followed by: `isolde_session`, `coot_session`, `wwpdb_validation`.

### `wwpdb_validation` — wwPDB validation report (OneDep)
wwPDB validation client (OneDep API). In: model: model, map: map. Out: report: report.

Official wwPDB validation report (PDF and XML) from the OneDep validation web service: the report journals and reviewers receive. The model and the primary map are uploaded to the wwPDB server (note: the onedep_api client does not check the server's certificate).
Usually followed by: `deposition_package`.

## Deposition

### `deposition_package` — Deposition package
built-in. In: model: model, map: map, half_maps: half_maps, mask?: mask, fsc?: fsc, checklist?: report, sequence?: sequence. Out: package: files.

Assemble everything for wwPDB OneDep: model as mmCIF (entities set up), primary map, half maps, mask, EMDB FSC XML, validation reports, a deposition checklist with the suggested contour level, a draft methods paragraph with citations and a draft 'Table 1' built from the job history.

### `predeposition_check` — Pre-deposition checks
built-in. In: model: model, map: map, half_maps?: half_maps, mask?: mask, sequence?: sequence. Out: report: report.

Automated checklist before submitting to the wwPDB/EMDB: map/half-map/mask consistency, model inside the map, fit at the suggested contour, chain IDs, unknown residues, occupancies, B-factors, severe clashes, chain breaks and agreement with the deposited sequence.
Usually followed by: `deposition_package`, `wwpdb_validation`.

## Utilities

### `relion_particle_curate` — Curate particles (statistics)
built-in · interactive: a person works in it. In: particles: particles. Out: particles: particles, rejected: particles, cluster_1: particles, cluster_2: particles.

Keeps the particles whose statistics are within limits, as CryoSPARC's Subset Particles by Statistic: probability of the best pose, significant samples, log-likelihood, normalisation, pick figure of merit, defocus, CTF fit, alignment shift, particles on the micrograph and any other RELION column. Interactive, the job waits with the distribution of each statistic (limits dragged on the histograms, each showing the particles the other limits keep), a density plot of two of them and images of particles kept and left out. Or applies the limits written below at once, or splits the particles into clusters of one statistic (Gaussian mixture).
Usually followed by: `relion_refine3d`, `relion_class2d`, `relion_class3d_global`, `relion_particle_sets`.

### `custom_command` — Custom command
Shell (custom commands) · GPU optional · administrators only, not for agents. In: map?: map, map2?: map, half_maps?: half_maps, model?: model, mask?: mask, sequence?: sequence. Out: map: map, model: model.

Run any program on CryoPlug data (bash script template). Placeholders: {map} {map2} {half_map_a} {half_map_b} {model} {mask} {sequence} {job_dir} {resolution} {pixel_size} {gpus}. Files matching the output patterns are registered as outputs.

### `relion_downsample` — Downsample particles
built-in. In: particles: particles. Out: particles: particles.

Smaller particle images by Fourier cropping, as CryoSPARC's Downsample Particles: faster 2D and 3D at a coarser pixel (the resolution limit becomes twice the new pixel size). Writes new stacks and a RELION particle file with the new box and pixel size; poses and shifts (in Å) are kept.
Usually followed by: `relion_class2d`, `relion_initial_model`, `relion_class3d_global`.

### `relion_join` — Join star files (RELION)
RELION. In: set_1: particles/micrographs/movies, set_2: particles/micrographs/movies, set_3?: particles/micrographs/movies, set_4?: particles/micrographs/movies. Out: particles: particles, micrographs: micrographs, movies: movies.

Merges two to four particle, micrograph or movie sets into one (relion_star_handler --combine), removing particles present twice.
Usually followed by: `relion_class2d`, `relion_refine3d`, `relion_subset`.

### `merge_models` — Merge models
built-in. In: model_a: model, model_b: model, model_c?: model, model_d?: model. Out: model: model.

Combine chains from several models (e.g. docked domains or subunits) into one; clashing chain IDs are renamed.
Usually followed by: `phenix_real_space_refine`, `isolde_session`.

### `model_tools` — Model operations
built-in. In: model: model. Out: model: model.

Built-in model editing with gemmi: convert PDB/mmCIF, remove hydrogens/waters/ligands/alternative conformations, select or rename chains, reset B-factors and occupancies.
Usually followed by: `phenix_real_space_refine`.

### `relion_optics_groups` — Optics groups (beam shift, file names)
built-in. In: data: movies/micrographs/particles. Out: particles: particles, micrographs: micrographs, movies: movies.

Splits movies, micrographs or particles into optics groups, as CryoSPARC's Exposure Group Utilities: by the beam (image) shift of EPU's acquisition files (aberration-free image shift, clustered), by a part of the file names, or in groups of acquisition order. CTF refinement then fits the beam tilt and higher-order aberrations of each group.
Usually followed by: `relion_ctf_refine`, `relion_motioncorr`, `relion_extract`.

### `relion_particle_sets` — Particle sets (intersection, difference)
built-in. In: set_a: particles/micrographs, set_b: particles/micrographs. Out: both: particles, a_only: particles, b_only: particles.

Compares two particle sets, as CryoSPARC's Particle Sets Tool: the particles in both, those of A only and those of B only. Particles are matched by image (the same extracted particle) or by position on the micrograph (particles picked or extracted twice). Two micrograph sets are compared by micrograph name, as CryoSPARC's Exposure Sets Tool. Join star files gives the union.
Usually followed by: `relion_refine3d`, `relion_class2d`, `relion_join`.

### `relion_rebalance` — Rebalance orientations
built-in. In: particles: particles. Out: particles: particles, removed: particles.

Removes particles from over-represented views (preferred orientation), as CryoSPARC's Rebalance Orientations: the viewing directions are cut in bins of about equal area and each bin keeps at most as many particles as a chosen percentile of the bins. The anisotropy of the map drops, at the cost of particles.
Usually followed by: `relion_refine3d`.

### `chimerax_render` — Render images (ChimeraX)
UCSF ChimeraX (+ ISOLDE). In: map?: map, model?: model. Out: images: files.

Offscreen ChimeraX rendering of the map and/or model from three orthogonal views (figures, reports).

### `relion_restack` — Restack particles
RELION. In: particles: particles. Out: particles: particles.

Writes the images of a particle set into new stacks (relion_stack_create), as CryoSPARC's Restack Particles: a subset kept after cleaning then reads only its own images, from fewer and smaller files (faster on network disks, and the original extraction can be removed). Optionally re-centred on the alignment shifts, rounded to whole pixels (no interpolation).
Usually followed by: `relion_refine3d`, `relion_class2d`, `relion_class3d_global`.

### `relion_subset` — Subset selection (RELION)
RELION. In: data: particles/micrographs. Out: particles: particles, micrographs: micrographs, subset_1: particles, subset_2: particles.

relion_star_handler on a particle or micrograph set: remove duplicated particles (closer than a distance after alignment: they inflate the FSC), split into subsets, keep the rows of a metadata range, or discard images with outlying statistics.
Usually followed by: `relion_refine3d`, `relion_class2d`, `relion_join`.

### `relion_symmetry_expand` — Symmetry expansion (RELION)
RELION. In: particles: particles. Out: particles: particles.

Copies every particle once per symmetry-related pose (relion_particle_symmetry_expand), as CryoSPARC's Symmetry Expansion: each copy of a symmetric complex becomes a particle, for focused classification or refinement of one subunit in C1. Helical expansion along the axis too.
Usually followed by: `relion_subtract`, `relion_class3d_global`, `relion_local_refine`.
