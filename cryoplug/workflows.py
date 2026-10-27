"""Workflow templates: chains of linked jobs created in one click.

Each node: ``id``, ``type`` (job type), optional ``title``, ``params``, ``ask``
(parameters shown in the workflow dialog), ``optional``/``default`` (node can be
left out) and ``inputs``: ``{slot: [node, output]}`` or a list of alternatives
``{slot: [[node, output], [fallback_node, output]]}`` used when a node is skipped.
"""
from __future__ import annotations

from typing import Any

from cryoplug.jobs import get_job_type


def _final_steps(model_src: list, map_src: list, seq: list | None) -> list[dict[str, Any]]:
    check_inputs = {"model": model_src, "map": map_src, "half_maps": ["import", "half_maps"], "mask": ["import", "mask"]}
    if seq:
        check_inputs["sequence"] = seq
    steps = [
        {"id": "validate", "type": "phenix_validation_cryoem", "inputs": {"model": model_src, "map": map_src},
         "optional": True},
        {"id": "qscore", "type": "mapmodel_validation", "inputs": {"model": model_src, "map": map_src, "fsc": ["import", "fsc"]}},
        {"id": "check", "type": "predeposition_check", "inputs": check_inputs},
        {"id": "package", "type": "deposition_package",
         "inputs": {"model": model_src, "map": map_src, "half_maps": ["import", "half_maps"], "mask": ["import", "mask"],
                    "fsc": ["import", "fsc"], "checklist": ["check", "report"], **({"sequence": seq} if seq else {})}},
        # off by default: they need checkMySequence / the wwPDB client, and the second sends the files to the wwPDB
        {"id": "wwpdb", "type": "wwpdb_validation", "title": "Official wwPDB validation report",
         "inputs": {"model": model_src, "map": map_src}, "optional": True, "default": False},
    ]
    if seq:
        steps.insert(2, {"id": "seqcheck", "type": "checkmysequence", "title": "Sequence register check",
                         "inputs": {"model": model_src, "map": map_src, "sequence": seq}, "optional": True, "default": False})
    return steps


IMPORT_NODE = {"id": "import", "type": "import_cryosparc", "title": "Import CryoSPARC refinement",
               "ask": ["job_dir", "resolution", "symmetry"]}

WORKFLOWS: list[dict[str, Any]] = [
    {
        "id": "denovo_modelangelo",
        "title": "De novo model → deposition (ModelAngelo)",
        "description": ("CryoSPARC refinement → LocScale 2 → ModelAngelo → Phenix refinement → ISOLDE → final "
                        "refinement → validation → pre-deposition checks → deposition package."),
        "nodes": [
            IMPORT_NODE,
            {"id": "seq", "type": "import_sequence", "title": "Sample sequence", "ask": ["fasta", "uniprot"]},
            {"id": "locscale", "type": "locscale", "title": "LocScale (model-free)", "params": {"mode": "model_free"},
             "inputs": {"half_maps": ["import", "half_maps"], "mask": ["import", "mask"]}, "optional": True, "ask": ["mode"]},
            {"id": "build", "type": "modelangelo_build",
             "inputs": {"map": [["locscale", "map"], ["import", "map_sharp"]], "sequence": ["seq", "sequence"]}},
            {"id": "refine1", "type": "phenix_real_space_refine", "title": "Initial refinement",
             "inputs": {"model": ["build", "model"], "map": [["locscale", "map"], ["import", "map_sharp"]]}},
            {"id": "isolde", "type": "isolde_session", "title": "Rebuild in ISOLDE", "optional": True,
             "inputs": {"model": ["refine1", "model"], "map": [["locscale", "map"], ["import", "map_sharp"]],
                        "map2": ["import", "map_sharp"]}},
            {"id": "refine2", "type": "phenix_real_space_refine", "title": "Final refinement",
             "params": {"macro_cycles": 3, "run": "minimization_global+adp"},
             "inputs": {"model": [["isolde", "model"], ["refine1", "model"]], "map": ["import", "map_sharp"]}},
            *_final_steps(["refine2", "model"], ["import", "map_sharp"], ["seq", "sequence"]),
        ],
    },
    {
        "id": "denovo_cryoatom",
        "title": "De novo model → deposition (CryoAtom2)",
        "description": ("CryoSPARC refinement → LocScale 2 → CryoAtom2 (proteins and nucleic acids) → Phenix refinement → "
                        "ISOLDE → final refinement → validation → pre-deposition checks → deposition package."),
        "nodes": [
            IMPORT_NODE,
            {"id": "seq", "type": "import_sequence", "title": "Sample sequence", "ask": ["fasta", "uniprot"]},
            {"id": "locscale", "type": "locscale", "title": "LocScale (model-free)", "params": {"mode": "model_free"},
             "inputs": {"half_maps": ["import", "half_maps"], "mask": ["import", "mask"]}, "optional": True, "ask": ["mode"]},
            {"id": "build", "type": "cryoatom_build",
             "inputs": {"map": [["locscale", "map"], ["import", "map_sharp"]], "sequence": ["seq", "sequence"]}},
            {"id": "refine1", "type": "phenix_real_space_refine", "title": "Initial refinement",
             "inputs": {"model": ["build", "model"], "map": [["locscale", "map"], ["import", "map_sharp"]]}},
            {"id": "isolde", "type": "isolde_session", "title": "Rebuild in ISOLDE", "optional": True,
             "inputs": {"model": ["refine1", "model"], "map": [["locscale", "map"], ["import", "map_sharp"]],
                        "map2": ["import", "map_sharp"]}},
            {"id": "refine2", "type": "phenix_real_space_refine", "title": "Final refinement",
             "params": {"macro_cycles": 3, "run": "minimization_global+adp"},
             "inputs": {"model": [["isolde", "model"], ["refine1", "model"]], "map": ["import", "map_sharp"]}},
            *_final_steps(["refine2", "model"], ["import", "map_sharp"], ["seq", "sequence"]),
        ],
    },
    {
        "id": "identify_unknown",
        "title": "Identify unknown proteins in the map",
        "description": ("For maps containing unexpected or unknown subunits: ModelAngelo without sequence + HMM search "
                        "against a proteome, and/or CryoAtom2 identification, then a rebuild with the identified sequences."),
        "nodes": [
            IMPORT_NODE,
            {"id": "noseq", "type": "modelangelo_build", "title": "ModelAngelo (no sequence)",
             "inputs": {"map": ["import", "map_sharp"]}},
            {"id": "hmm", "type": "modelangelo_hmm_search", "title": "HMM search", "ask": ["database"],
             "inputs": {"model": ["noseq", "model"]}},
            {"id": "cryo_ident", "type": "cryoatom_build", "title": "CryoAtom2 identification", "optional": True,
             "default": False, "ask": ["protein_db"], "inputs": {"map": ["import", "map_sharp"]}},
            {"id": "rebuild", "type": "cryoatom_build", "title": "Rebuild with identified sequences",
             "inputs": {"map": ["import", "map_sharp"], "sequence": [["hmm", "sequence"], ["cryo_ident", "sequence"]]}},
            {"id": "qscore", "type": "mapmodel_validation", "inputs": {"model": ["rebuild", "model"], "map": ["import", "map_sharp"]}},
        ],
    },
    {
        "id": "alphafold_docking",
        "title": "Predicted model → deposition (AlphaFold)",
        "description": ("CryoSPARC refinement + AlphaFold DB / file model → trim low pLDDT → global rigid-body fit (built-in, 100 placements of the 8 Å simulated map) → "
                        "refinement → ISOLDE → final refinement → validation → deposition package."),
        "nodes": [
            IMPORT_NODE,
            {"id": "model", "type": "import_model", "title": "Predicted model", "params": {"source": "alphafold"},
             "ask": ["source", "accession", "path"]},
            {"id": "seq", "type": "import_sequence", "title": "Sample sequence", "ask": ["fasta", "uniprot"], "optional": True},
            {"id": "trim", "type": "phenix_process_predicted_model", "inputs": {"model": ["model", "model"]}, "optional": True},
            {"id": "fit", "type": "fit_in_map", "title": "Global fit in map",
             "params": {"search": 100, "resolution": 8.0, "metric": "correlation"},
             "inputs": {"model": [["trim", "model"], ["model", "model"]], "map": ["import", "map_sharp"]}},
            {"id": "refine1", "type": "phenix_real_space_refine", "title": "Initial refinement",
             "params": {"run": "minimization_global+rigid_body+local_grid_search+morphing+simulated_annealing+adp"},
             "inputs": {"model": ["fit", "model"], "map": ["import", "map_sharp"]}},
            {"id": "isolde", "type": "isolde_session", "title": "Rebuild in ISOLDE", "optional": True,
             "inputs": {"model": ["refine1", "model"], "map": ["import", "map_sharp"], "map2": ["import", "map"]}},
            {"id": "refine2", "type": "phenix_real_space_refine", "title": "Final refinement",
             "params": {"macro_cycles": 3, "run": "minimization_global+adp"},
             "inputs": {"model": [["isolde", "model"], ["refine1", "model"]], "map": ["import", "map_sharp"]}},
            *_final_steps(["refine2", "model"], ["import", "map_sharp"], ["seq", "sequence"]),
        ],
    },
    {
        "id": "map_enhancement",
        "title": "Map enhancement comparison",
        "description": ("Check the directional resolution, then run the main sharpening / enhancement methods side by "
                        "side on the same half maps to choose the most interpretable map: LocScale 2, EMmerNet, "
                        "DeepEMhancer, Phenix density modification and auto-sharpening (+ optional spIsoNet, EMReady)."),
        "nodes": [
            IMPORT_NODE,
            {"id": "dirfsc", "type": "directional_fsc", "inputs": {"half_maps": ["import", "half_maps"], "mask": ["import", "mask"]}},
            {"id": "spisonet", "type": "spisonet", "inputs": {"half_maps": ["import", "half_maps"], "mask": ["import", "mask"]},
             "optional": True, "default": False},
            {"id": "locscale", "type": "locscale", "inputs": {"half_maps": ["import", "half_maps"], "mask": ["import", "mask"]}},
            {"id": "emmernet", "type": "emmernet", "inputs": {"half_maps": ["import", "half_maps"]}, "optional": True},
            {"id": "deepemhancer", "type": "deepemhancer", "inputs": {"half_maps": ["import", "half_maps"]}, "optional": True},
            {"id": "denmod", "type": "phenix_resolve_cryo_em", "inputs": {"half_maps": ["import", "half_maps"]}, "optional": True},
            {"id": "autosharpen", "type": "phenix_auto_sharpen",
             "inputs": {"map": ["import", "map"], "half_maps": ["import", "half_maps"]}, "optional": True},
            {"id": "emready", "type": "emready", "inputs": {"map": ["import", "map"]}, "optional": True, "default": False},
        ],
    },
    {
        "id": "heterogeneity_cryodrgn",
        "title": "Conformational heterogeneity (cryoDRGN)",
        "description": ("First round of the cryoDRGN protocol (Kinman et al. 2023): particles of the CryoSPARC consensus "
                        "refinement → input check and training at 128 px with the small network (latent space, cluster "
                        "volumes with junk warnings, trajectories) → convergence check → variability of the cluster "
                        "volumes (with a model: the chains that move or come and go). Then remove the junk in the latent "
                        "space explorer and train again on the selection at 256 px (second round)."),
        "nodes": [
            {"id": "import", "type": "import_cryosparc", "title": "Import consensus refinement (particles)",
             "ask": ["job_dir", "symmetry"]},
            {"id": "model", "type": "import_model", "title": "Model fitted in the consensus map", "ask": ["path"],
             "optional": True, "default": False},
            {"id": "train", "type": "cryodrgn_train", "title": "cryoDRGN round 1 (128 px, small network)",
             "params": {"box": 128, "zdim": 8, "epochs": 50}, "ask": ["box", "zdim", "epochs", "gpus", "check_inputs"],
             "inputs": {"particles": ["import", "particles"]}},
            {"id": "convergence", "type": "cryodrgn_convergence", "title": "Convergence of round 1",
             "inputs": {"latent": ["train", "latent"]}, "optional": True},
            {"id": "clusters", "type": "series_analysis", "title": "Variability of the cluster volumes",
             "inputs": {"series": ["train", "kmeans"], "model": ["model", "model"]}},
            {"id": "pc1", "type": "series_analysis", "title": "Motion along PC1",
             "inputs": {"series": ["train", "pc1"], "model": ["model", "model"]}, "optional": True, "default": False},
        ],
    },
    {
        "id": "heterogeneity_cryodrgn_ai",
        "title": "Ab initio heterogeneity (cryoDRGN-AI)",
        "description": ("cryoDRGN-AI learns the poses and the latent space together, without a consensus refinement: "
                        "particles from CryoSPARC (a Select 2D job, or a refinement whose poses are then compared with the "
                        "learned ones) → cryoDRGN-AI at 128 px (input check, live phases, hand and frame, validation "
                        "backprojection, latent space with junk warnings) → convergence check → variability of the cluster "
                        "volumes. Then remove the junk in the explorer and run cryoDRGN-AI again on the selection (second "
                        "round), or train cryoDRGN on it with the learned or the consensus poses."),
        "nodes": [
            {"id": "import", "type": "import_cryosparc", "title": "Import particles (Select 2D or refinement job)",
             "ask": ["job_dir", "symmetry"]},
            {"id": "reference", "type": "import_maps", "title": "Reference map (frame and hand)", "ask": ["map"],
             "optional": True, "default": False},
            {"id": "model", "type": "import_model", "title": "Model fitted in the reference map", "ask": ["path"],
             "optional": True, "default": False},
            {"id": "abinit", "type": "cryodrgn_abinit", "title": "cryoDRGN-AI (128 px)",
             "params": {"box": 128, "zdim": 8, "epochs": 30}, "ask": ["box", "zdim", "epochs", "gpus", "check_inputs"],
             "inputs": {"particles": ["import", "particles"], "reference": ["reference", "map"]}},
            {"id": "convergence", "type": "cryodrgn_convergence", "title": "Convergence (latent space, volumes, poses)",
             "inputs": {"latent": ["abinit", "latent"]}, "optional": True},
            {"id": "clusters", "type": "series_analysis", "title": "Variability of the cluster volumes",
             "inputs": {"series": ["abinit", "kmeans"], "model": ["model", "model"]}},
        ],
    },
    {
        "id": "variability_3dva",
        "title": "3D variability (CryoSPARC) → interpretation",
        "description": ("Import a CryoSPARC 3D Variability job (particle coordinates along each component, for the "
                        "explorer and particle selection) and its 3D Variability Display volumes, then analyse the "
                        "first component: variability map, frame similarity and, with a model, the chains involved."),
        "nodes": [
            {"id": "import", "type": "import_cryosparc", "title": "Import 3D Variability job", "ask": ["job_dir"]},
            {"id": "frames", "type": "import_volume_series", "title": "3D Variability Display volumes", "ask": ["path"]},
            {"id": "model", "type": "import_model", "title": "Model fitted in the consensus map", "ask": ["path"],
             "optional": True, "default": False},
            {"id": "component1", "type": "series_analysis", "title": "Component 1",
             "inputs": {"series": ["frames", "series"], "model": ["model", "model"]}},
        ],
    },
    {
        "id": "relion_spa",
        "title": "Movies to map (RELION single particle)",
        "description": ("The whole single-particle pipeline in RELION, as in CryoSPARC: movies imported → motion correction "
                        "→ CTF estimation → micrographs curated on their CTF fit, motion and ice (the job shows the "
                        "distributions and the micrographs and waits for you) → Laplacian-of-Gaussian picking → "
                        "extraction (down-scaled) → 2D classification → selection of the good classes (the job shows the "
                        "class averages and waits for your clicks: the chain waits for it) → initial model → 3D "
                        "auto-refine → mask → post-processing and local resolution. CTF refinement and a second "
                        "refinement are optional."),
        "nodes": [
            {"id": "import", "type": "relion_import", "title": "Import movies",
             "ask": ["movies", "pixel_size", "voltage", "cs", "dose_per_frame", "gain"]},
            {"id": "motion", "type": "relion_motioncorr", "inputs": {"movies": ["import", "movies"]},
             "ask": ["binning", "patches_x", "patches_y", "threads"]},
            {"id": "ctf", "type": "relion_ctffind", "inputs": {"micrographs": ["motion", "micrographs"]}, "ask": ["mpi"]},
            {"id": "curate", "type": "relion_curate", "params": {"max_ctf_resolution": 8.0},
             "inputs": {"micrographs": ["ctf", "micrographs"]}, "ask": ["max_ctf_resolution", "max_motion"]},
            {"id": "pick", "type": "relion_autopick", "title": "Auto-picking (Laplacian-of-Gaussian)",
             "params": {"method": "log"}, "inputs": {"micrographs": ["curate", "micrographs"]},
             "ask": ["diameter_min", "diameter_max", "log_threshold"]},
            {"id": "junk", "type": "micrograph_junk", "title": "Junk detector (picks on contamination, film edges)",
             "optional": True, "default": False,
             "inputs": {"micrographs": ["curate", "micrographs"], "coordinates": ["pick", "coordinates"]},
             "ask": ["dark", "edges"]},
            {"id": "extract", "type": "relion_extract",
             "inputs": {"micrographs": ["curate", "micrographs"],
                        "coordinates": [["junk", "coordinates"], ["pick", "coordinates"]]},
             "ask": ["box", "rescale"]},
            {"id": "class2d", "type": "relion_class2d", "inputs": {"particles": ["extract", "particles"]},
             "ask": ["classes", "gpus"]},
            {"id": "select", "type": "relion_select2d", "title": "Select the good 2D classes",
             "inputs": {"classes": ["class2d", "classes"]}},
            {"id": "inimodel", "type": "relion_initial_model", "inputs": {"particles": ["select", "particles"]},
             "ask": ["symmetry", "gpus"]},
            {"id": "refine", "type": "relion_refine3d", "inputs": {"particles": ["select", "particles"], "map": ["inimodel", "map"]},
             "ask": ["symmetry", "mpi", "gpus"]},
            {"id": "mask", "type": "relion_mask_create", "inputs": {"map": ["refine", "map"]}, "ask": ["lowpass", "extend", "soft_edge"]},
            {"id": "post", "type": "relion_postprocess", "inputs": {"half_maps": ["refine", "half_maps"], "mask": ["mask", "mask"]}},
            {"id": "locres", "type": "relion_locres", "inputs": {"half_maps": ["refine", "half_maps"], "mask": ["mask", "mask"]},
             "optional": True},
            {"id": "ctfrefine", "type": "relion_ctf_refine", "optional": True, "default": False,
             "inputs": {"particles": ["refine", "particles"], "half_maps": ["refine", "half_maps"], "mask": ["mask", "mask"]}},
            {"id": "refine2", "type": "relion_refine3d", "title": "3D auto-refine after CTF refinement", "optional": True,
             "default": False, "params": {"ini_high": 10.0},
             "inputs": {"particles": ["ctfrefine", "particles"], "map": ["refine", "map"], "mask": ["mask", "mask"]},
             "ask": ["symmetry", "mpi", "gpus"]},
        ],
    },
    {
        "id": "relion_tomo",
        "title": "Tilt series to subtomogram average (RELION 5)",
        "description": ("RELION 5's subtomogram averaging, as in its tutorial: tilt series imported from SerialEM (movies "
                        "and mdoc files) → motion correction with the even/odd sums → CTF estimation → bad tilt images "
                        "excluded (the job shows the images by tilt angle and waits for you) → alignment (IMOD or "
                        "AreTomo2) → tomograms reconstructed for denoising → cryoCARE → particles picked in the "
                        "tomograms (the job waits for your picks: particles, spheres or filaments) → subtomograms "
                        "extracted → initial model → 3D auto-refine → mask → post-processing. CTF refinement, frame "
                        "alignment and a last refinement are optional."),
        "nodes": [
            {"id": "import", "type": "relion_tomo_import", "title": "Import tilt series",
             "ask": ["movies", "mdocs", "pixel_size", "voltage", "cs", "dose", "tilt_axis", "flip_hand"]},
            {"id": "motion", "type": "relion_tomo_motioncorr", "params": {"even_odd": True},
             "inputs": {"tiltseries": ["import", "tiltseries"]}, "ask": ["threads"]},
            {"id": "ctf", "type": "relion_tomo_ctffind", "inputs": {"tiltseries": ["motion", "tiltseries"]}, "ask": ["mpi"]},
            {"id": "exclude", "type": "relion_tomo_exclude", "params": {"max_ctf_res": 20.0},
             "inputs": {"tiltseries": ["ctf", "tiltseries"]}, "ask": ["max_tilt", "max_ctf_res"]},
            {"id": "align", "type": "relion_tomo_align", "inputs": {"tiltseries": ["exclude", "tiltseries"]},
             "ask": ["method", "thickness", "fiducial_diameter"]},
            {"id": "reconstruct", "type": "relion_tomo_reconstruct", "title": "Reconstruct tomograms (for denoising)",
             "params": {"split": True, "fourier": False}, "inputs": {"tiltseries": ["align", "tiltseries"]},
             "ask": ["binned_pixel", "thickness"]},
            {"id": "denoise", "type": "relion_tomo_denoise", "optional": True,
             "inputs": {"tomograms": ["reconstruct", "tomograms"]}, "ask": ["training"]},
            {"id": "pick", "type": "relion_tomo_pick",
             "inputs": {"tomograms": [["denoise", "tomograms"], ["reconstruct", "tomograms"]]}, "ask": ["mode", "diameter", "spacing"]},
            {"id": "extract", "type": "relion_tomo_extract", "inputs": {"particles": ["pick", "particles"]},
             "ask": ["box_A", "binned_px"]},
            {"id": "inimodel", "type": "relion_tomo_initial_model", "inputs": {"particles": ["extract", "particles"]},
             "ask": ["symmetry", "gpus"]},
            {"id": "refine", "type": "relion_tomo_refine3d",
             "inputs": {"particles": ["extract", "particles"], "map": ["inimodel", "map"]}, "ask": ["symmetry", "mpi", "gpus"]},
            {"id": "mask", "type": "relion_mask_create", "inputs": {"map": ["refine", "map"]}, "ask": ["lowpass", "extend", "soft_edge"]},
            {"id": "post", "type": "relion_postprocess", "inputs": {"half_maps": ["refine", "half_maps"], "mask": ["mask", "mask"]}},
            {"id": "ctfrefine", "type": "relion_tomo_ctf_refine", "optional": True, "default": False,
             "inputs": {"particles": ["refine", "particles"], "half_maps": ["refine", "half_maps"], "mask": ["mask", "mask"],
                        "postprocess": ["post", "map_sharp"]}},
            {"id": "framealign", "type": "relion_tomo_frame_align", "optional": True, "default": False,
             "inputs": {"particles": [["ctfrefine", "particles"], ["refine", "particles"]], "half_maps": ["refine", "half_maps"],
                        "mask": ["mask", "mask"], "postprocess": ["post", "map_sharp"]}},
            {"id": "refine2", "type": "relion_tomo_refine3d", "title": "3D auto-refine after CTF refinement and frame alignment",
             "optional": True, "default": False, "params": {"ini_high": 10.0},
             "inputs": {"particles": [["framealign", "particles"], ["ctfrefine", "particles"]], "map": ["refine", "map"],
                        "mask": ["mask", "mask"]}, "ask": ["symmetry", "mpi", "gpus"]},
        ],
    },
    {
        "id": "relion_tomo_tm",
        "title": "Tilt series to subtomogram average, picked by template matching",
        "description": ("RELION 5's subtomogram averaging for a known structure, picked automatically: tilt series "
                        "imported → motion correction → CTF estimation → tilt images excluded on limits (tilt angle, CTF "
                        "fit) → alignment → tomograms binned for template matching (10-15 Å/voxel) → the template (a map "
                        "of the particle) matched in every tomogram with pytom-match-pick → subtomograms extracted → 3D "
                        "auto-refine from the template → mask → post-processing. Runs without stopping; check the scores "
                        "and the picks of the template matching before trusting the average."),
        "nodes": [
            {"id": "import", "type": "relion_tomo_import", "title": "Import tilt series",
             "ask": ["movies", "mdocs", "pixel_size", "voltage", "cs", "dose", "tilt_axis", "flip_hand"]},
            {"id": "motion", "type": "relion_tomo_motioncorr", "inputs": {"tiltseries": ["import", "tiltseries"]},
             "ask": ["threads"]},
            {"id": "ctf", "type": "relion_tomo_ctffind", "inputs": {"tiltseries": ["motion", "tiltseries"]}, "ask": ["mpi"]},
            {"id": "exclude", "type": "relion_tomo_exclude", "params": {"mode": "limits", "max_ctf_res": 20.0},
             "inputs": {"tiltseries": ["ctf", "tiltseries"]}, "ask": ["max_tilt", "max_ctf_res"]},
            {"id": "align", "type": "relion_tomo_align", "inputs": {"tiltseries": ["exclude", "tiltseries"]},
             "ask": ["method", "thickness", "fiducial_diameter"]},
            {"id": "reconstruct", "type": "relion_tomo_reconstruct", "title": "Reconstruct tomograms for template matching",
             "params": {"binned_pixel": 12.0}, "inputs": {"tiltseries": ["align", "tiltseries"]},
             "ask": ["binned_pixel", "thickness"]},
            {"id": "template", "type": "import_maps", "title": "Template (map of the particle)",
             "params": {"compute_fsc": False}, "ask": ["map"]},
            {"id": "match", "type": "pytom_template_match",
             "inputs": {"tomograms": ["reconstruct", "tomograms"], "template": ["template", "map"]},
             "ask": ["particle_diameter", "n_particles", "false_positives", "symmetry", "gpus"]},
            {"id": "extract", "type": "relion_tomo_extract", "inputs": {"particles": ["match", "particles"]},
             "ask": ["box_A", "binned_px"]},
            {"id": "refine", "type": "relion_tomo_refine3d", "params": {"ini_high": 40.0},
             "inputs": {"particles": ["extract", "particles"], "map": ["template", "map"]}, "ask": ["symmetry", "mpi", "gpus"]},
            {"id": "mask", "type": "relion_mask_create", "inputs": {"map": ["refine", "map"]}, "ask": ["lowpass", "extend", "soft_edge"]},
            {"id": "post", "type": "relion_postprocess", "inputs": {"half_maps": ["refine", "half_maps"], "mask": ["mask", "mask"]}},
        ],
    },
    {
        "id": "relion_clean_3d",
        "title": "Clean particles in 3D (decoys, heterogeneous refinement)",
        "description": ("CryoSPARC's junk removal in RELION: particles (those of a 2D selection, a RELION .star file or a "
                        "CryoSPARC job) → initial model with three classes (a good map and decoys: junk, broken or "
                        "aggregated particles) → heterogeneous refinement against these maps → the good classes clicked "
                        "in its gallery (Select 3D classes waits for you: the chain waits for it) → 3D auto-refine of "
                        "the particles kept, from the initial model. Repeat the heterogeneous refinement on the particles "
                        "kept while junk classes still take particles."),
        "nodes": [
            {"id": "import", "type": "import_particles", "title": "Particles to clean", "ask": ["path"]},
            {"id": "inimodel", "type": "relion_initial_model", "title": "Initial model (a good class and decoys)",
             "params": {"classes": 3}, "inputs": {"particles": ["import", "particles"]}, "ask": ["classes", "symmetry", "gpus"]},
            {"id": "hetero", "type": "relion_hetero_refine", "title": "Heterogeneous refinement against the classes",
             "inputs": {"particles": ["import", "particles"], "ref_1": ["inimodel", "map_1"], "ref_2": ["inimodel", "map_2"],
                        "ref_3": ["inimodel", "map_3"], "ref_4": ["inimodel", "map_4"]},
             "ask": ["iterations", "gpus"]},
            {"id": "select", "type": "relion_select3d", "title": "Select the good 3D classes",
             "inputs": {"particles": ["hetero", "particles"]}},
            {"id": "refine", "type": "relion_refine3d", "title": "3D auto-refine of the particles kept",
             "inputs": {"particles": ["select", "particles"], "map": ["inimodel", "map"]}, "ask": ["symmetry", "mpi", "gpus"]},
        ],
    },
    {
        "id": "relion_focused_refinement",
        "title": "Focused refinement with signal subtraction (RELION)",
        "description": ("Local refinement of a region in RELION, from a CryoSPARC consensus refinement: particles and maps "
                        "imported → short gold-standard refinement of the whole complex with local searches (the RELION "
                        "model the subtraction needs) → mask of the region drawn in the 3D viewer (open the job and click "
                        "“Draw in 3D”: the rest of the chain waits for it) → signal subtraction, re-centred and re-boxed → "
                        "focused refinement with Blush, post-processed. Every map comes back on the grid of the consensus "
                        "map, ready for LocScale, model building or a composite map."),
        "nodes": [
            {"id": "import", "type": "import_cryosparc", "title": "Import CryoSPARC consensus refinement",
             "ask": ["job_dir", "symmetry"]},
            {"id": "consensus", "type": "relion_local_refine", "title": "RELION refinement of the whole complex",
             "inputs": {"particles": ["import", "particles"], "map": ["import", "map"], "mask": ["import", "mask"]},
             "ask": ["mpi", "threads", "gpus"]},
            {"id": "focus", "type": "draw_mask", "title": "Mask of the region (draw it in 3D)",
             "inputs": {"map": ["import", "map"]}, "ask": ["dilate", "soft"]},
            {"id": "subtract", "type": "relion_subtract", "title": "Signal subtraction around the region",
             "inputs": {"particles": ["consensus", "particles"], "mask": ["focus", "mask"]}, "ask": ["expand_symmetry"]},
            {"id": "focused", "type": "relion_local_refine", "title": "RELION focused refinement", "params": {"blush": True},
             "inputs": {"particles": ["subtract", "particles"], "map": ["consensus", "map"], "mask": ["focus", "mask"]},
             "ask": ["blush", "mpi", "threads", "gpus"]},
            {"id": "locscale", "type": "locscale", "title": "LocScale of the focused map",
             "inputs": {"half_maps": ["focused", "half_maps"], "mask": ["focused", "mask"]}, "optional": True, "default": False},
        ],
    },
    {
        "id": "relion_focused_classification",
        "title": "Focused 3D classification (RELION)",
        "description": ("States of one region: CryoSPARC consensus → RELION refinement with local searches → mask of the "
                        "region drawn in the 3D viewer → signal subtraction (after symmetry expansion for one copy of a "
                        "symmetric complex) → 3D classification without alignment, focused by the mask. Each class gives a "
                        "map and its particles: refine them locally, or reconstruct them at full size."),
        "nodes": [
            {"id": "import", "type": "import_cryosparc", "title": "Import CryoSPARC consensus refinement",
             "ask": ["job_dir", "symmetry"]},
            {"id": "consensus", "type": "relion_local_refine", "title": "RELION refinement of the whole complex",
             "inputs": {"particles": ["import", "particles"], "map": ["import", "map"], "mask": ["import", "mask"]},
             "ask": ["mpi", "threads", "gpus"]},
            {"id": "focus", "type": "draw_mask", "title": "Mask of the region (draw it in 3D)",
             "inputs": {"map": ["import", "map"]}, "ask": ["dilate", "soft"]},
            {"id": "subtract", "type": "relion_subtract", "title": "Signal subtraction around the region",
             "inputs": {"particles": ["consensus", "particles"], "mask": ["focus", "mask"]}, "ask": ["expand_symmetry"]},
            {"id": "classify", "type": "relion_class3d", "title": "Focused classification",
             "params": {"classes": 4, "tau2_fudge": 10.0},
             "inputs": {"particles": ["subtract", "particles"], "map": ["consensus", "map"], "mask": ["focus", "mask"]},
             "ask": ["classes", "tau2_fudge", "iterations"]},
        ],
    },
    {
        "id": "validate_deposit",
        "title": "Validate & prepare deposition",
        "description": "Bring an existing model and maps, run all validation tools and assemble the deposition package.",
        "nodes": [
            {"id": "import", "type": "import_maps", "title": "Import maps",
             "ask": ["half_map_a", "half_map_b", "map_sharp", "mask", "resolution", "symmetry"]},
            {"id": "model", "type": "import_model", "title": "Final model", "ask": ["path"]},
            {"id": "seq", "type": "import_sequence", "title": "Sample sequence", "ask": ["fasta", "uniprot"], "optional": True},
            {"id": "molprobity", "type": "phenix_molprobity", "inputs": {"model": ["model", "model"]}, "optional": True},
            {"id": "emringer", "type": "phenix_emringer", "inputs": {"model": ["model", "model"], "map": ["import", "map_sharp"]},
             "optional": True},
            *_final_steps(["model", "model"], ["import", "map_sharp"], ["seq", "sequence"]),
        ],
    },
]


def get_workflow(workflow_id: str) -> dict[str, Any]:
    for wf in WORKFLOWS:
        if wf["id"] == workflow_id:
            return wf
    raise KeyError(f"Unknown workflow '{workflow_id}'")


def workflows_overview() -> list[dict[str, Any]]:
    out = []
    for wf in WORKFLOWS:
        nodes = []
        for n in wf["nodes"]:
            jt = get_job_type(n["type"])
            ask = [p.to_dict() | {"default": n.get("params", {}).get(p.name, p.default)} for p in jt.params if p.name in n.get("ask", [])]
            nodes.append({"id": n["id"], "type": n["type"], "type_title": jt.title, "title": n.get("title") or jt.title,
                          "optional": bool(n.get("optional")), "default": n.get("default", True), "ask": ask,
                          "tool": jt.tool, "interactive": jt.interactive})
        out.append({"id": wf["id"], "title": wf["title"], "description": wf["description"], "nodes": nodes})
    return out
