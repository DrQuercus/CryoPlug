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
    return [
        {"id": "validate", "type": "phenix_validation_cryoem", "inputs": {"model": model_src, "map": map_src},
         "optional": True},
        {"id": "qscore", "type": "mapmodel_validation", "inputs": {"model": model_src, "map": map_src, "fsc": ["import", "fsc"]}},
        {"id": "check", "type": "predeposition_check", "inputs": check_inputs},
        {"id": "package", "type": "deposition_package",
         "inputs": {"model": model_src, "map": map_src, "half_maps": ["import", "half_maps"], "mask": ["import", "mask"],
                    "fsc": ["import", "fsc"], "checklist": ["check", "report"], **({"sequence": seq} if seq else {})}},
    ]


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
        "id": "alphafold_docking",
        "title": "Predicted model → deposition (AlphaFold)",
        "description": ("CryoSPARC refinement + AlphaFold DB / file model → trim low pLDDT → global rigid-body fit → "
                        "refinement → ISOLDE → final refinement → validation → deposition package."),
        "nodes": [
            IMPORT_NODE,
            {"id": "model", "type": "import_model", "title": "Predicted model", "params": {"source": "alphafold"},
             "ask": ["source", "accession", "path"]},
            {"id": "seq", "type": "import_sequence", "title": "Sample sequence", "ask": ["fasta", "uniprot"], "optional": True},
            {"id": "trim", "type": "phenix_process_predicted_model", "inputs": {"model": ["model", "model"]}, "optional": True},
            {"id": "fit", "type": "chimerax_fitmap", "title": "Global fit in map", "params": {"search": 200},
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
        "description": ("Run the main sharpening / enhancement methods side by side on the same half maps to choose "
                        "the most interpretable map: LocScale 2, EMmerNet, DeepEMhancer, Phenix density modification "
                        "and auto-sharpening."),
        "nodes": [
            IMPORT_NODE,
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
