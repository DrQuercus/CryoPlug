---
name: cryoplug-troubleshooting
description: Diagnose and fix CryoPlug jobs that fail, stay queued or give strange results - reading the job error and log, missing programs, Phenix errors (Sorry messages, map and model not aligned), GPU memory, wrong inputs (pixel size, box, hand, contrast), interactive sessions, queue and lane problems - then clear, update and re-queue or clone. Use whenever a CryoPlug job failed, is stuck, or its output looks wrong.
---

# Troubleshooting CryoPlug jobs

## Procedure

1. `get_job`: status, the error (CryoPlug puts the program's own explanation first: Phenix "Sorry: …", the last
   line of a Python traceback, an "error" line), the end of the log and the inputs.
2. `get_job_log` with `search` (e.g. `error|sorry|exception|killed|memory|not found`) for context, or `tail` for the
   end. `read_project_file` reads other files of the job (`commands.sh` holds the exact commands run; program logs).
3. Check the inputs: `get_job` of the parent jobs, `map_info` for box/pixel size, the job type's requirements in
   `describe_job_type`.
4. Explain the cause in plain words to the person, with the evidence (the error line).
5. Fix: `clear_job` (failed/killed job → building; deletes its files) then `update_job` and `queue_job`; or
   `clone_job` with new parameters (keeps the failed job for comparison). Never re-queue an unchanged failed job,
   unless the cause was transient (a GPU taken, a lost cluster node) and you say so.

## Common causes

| Symptom | Cause | Fix |
|---|---|---|
| Fails at once, "not found" / program missing | external program not installed or not configured | `server_status` shows it missing: tell the person (the administrator configures it on the Tools page); choose a built-in alternative if one exists |
| `phenix.… exited with code 1: Sorry: …` | Phenix refused an input or parameter; the Sorry text is the explanation | fix what it says; a refused option ("not recognized") comes from extra arguments |
| Phenix: "Map and model are not aligned", "Model is not similar to …" | model box/symmetry (CRYST1) differs from the map, or model outside the map | CryoPlug retries once with Phenix's own override (warning in the log); if the model really is outside: `fit_in_map` or `phenix_dock_in_map` first |
| "Resolution is unknown" | no FSC at import | set `resolution`, or import with `compute_fsc` |
| Stuck in `queued` | parent not finished or failed, lane full, GPU busy | the job message says ("Waiting for J3", "Blocked: J3 failed", "Waiting for GPU 1"); `server_status` shows the queue |
| CUDA out of memory | map or batch too large for the GPU | smaller batch, crop the box (`map_tools` `box`), mask the region, larger GPU (`resources` `gpu_ids`) |
| ModelAngelo: fragments, left-handed helices | mirrored map | `map_tools` `flip_hand=true`, rebuild |
| Model offset from the map | non-cubic box given to ModelAngelo | `map_tools` `box` to a cubic box |
| "The program finished but wrote no map / model" | the program stopped on an error without a failure code | find its last error in the log |
| "… was written next to an input … moved to …" (warning) | the program wrote into an input's folder | nothing to do: CryoPlug moved it back |
| `waiting` forever | interactive session (ISOLDE, Coot) waiting for a person | tell the person; they save in the job folder and press Finish |
| Coot/ISOLDE: "No saved model found in the job folder" | model saved elsewhere | save into the job folder (or upload), then Finish |
| wwPDB validation server error | no internet from the server, server down, file refused | check the log message; retry later |
| checkMySequence "needs HMMER" | `hmmsearch` missing in its environment | administrator installs HMMER there |
| cryoDRGN hollow volumes, memory, convergence | see `cryoplug-heterogeneity` | — |
| "These particles have no poses" | particles from extraction or 2D classification | import the refinement job |
| "… uses CryoSPARC's compressed format" | recent CryoSPARC `.cs` files | administrator installs `cryosparc-tools` in CryoPlug's environment |
| SLURM job pending for long | partition full, time or memory too large | ask less (`resources` `time`, `mem`) or another `partition` |

## Strange but successful results

- Map looks noisy or empty in thumbnails: contour/threshold issue or wrong map type (half map vs sharpened);
  compare with `map_info` and the parents' outputs.
- Resolution much better than expected: mask too tight or not independent half maps (spIsoNet); check the masked
  vs unmasked FSC.
- Model statistics excellent but fit poor (or the reverse): see the red flags in `cryoplug-refinement-validation`.

## What not to do

- Do not change program installation, server configuration or other people's jobs; report what the administrator
  must do.
- Do not use `kill_job` on someone else's running job, nor `clear_job` on a completed job (refused anyway).
- Do not hide a failure: report it with the cause and the fix you propose.

## Related skills

`cryoplug` · the skill of the task that failed (map quality, model building, refinement, heterogeneity, RELION).
