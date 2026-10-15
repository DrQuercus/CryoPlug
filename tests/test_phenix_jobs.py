"""Phenix jobs with Phenix 2: the program's own error in failed jobs, the checks Phenix offers to skip, the programs it
replaced (auto_sharpen, local_aniso_sharpen), real_space_refine's strategy and process_predicted_model's cutoff."""
from __future__ import annotations

import os

import gemmi
import pytest

from conftest import log_of, run_until_done
from cryoplug.config import Config, LaneConfig, ToolConfig
from cryoplug.jobs.base import failure_message, failure_reason, phenix_fix
from cryoplug.jobs.building import plddt_cutoff_args
from cryoplug.jobs.refinement import rsr_strategy
from cryoplug.manager import Manager
from cryoplug.tools import version_text

UNRECOGNIZED = """Processing PHIL parameters:
  Unrecognized PHIL parameters:
  -----------------------------
    nqh_flips = True

Sorry: Some PHIL parameters are not recognized by phenix.real_space_refine.
Please run this program with the --show-defaults option to see what parameters are available.
PHIL parameters in files should be fully specified (e.g. "output.overwrite" instead of just "overwrite")
"""
TRACEBACK = """Running auto_sharpen to get sharpening parameters
Traceback (most recent call last):
  File "/opt/phenix/lib/python3.12/site-packages/cctbx/maptbx/segment_and_split_map.py", line 9869, in select_box_map_data
    from mmtbx.command_line.map_box import run as run_map_box
          ^^^^^^^^^^^^^^^^^^^^^^^^^
ModuleNotFoundError: No module named 'wxGUI2'
"""


def lines(text: str) -> list[str]:
    return text.splitlines(keepends=True)


def test_failure_reason_in_the_programs_words():
    assert failure_reason(lines(UNRECOGNIZED)) == ("Sorry: Some PHIL parameters are not recognized by "
                                                   "phenix.real_space_refine: nqh_flips = True")
    assert failure_reason(lines(TRACEBACK)) == "ModuleNotFoundError: No module named 'wxGUI2'"
    # a message going on on the next line; ANSI colours and progress bars (\r) are cleaned
    assert failure_reason(["Sorry: The following arguments are not recognized:\n", "  af.pdb\n"]) == \
        "Sorry: The following arguments are not recognized: af.pdb"
    assert failure_reason(["\x1b[31mERROR\x1b[0m: CUDA out of memory\n", "cleanup 10%\r100%\n"]) == "ERROR: CUDA out of memory"
    assert failure_reason(["done\n", "cryoplug-phenix: line 3: exec: phenix.foo: not found\n"]).endswith("not found")
    assert failure_reason([]) == ""
    assert len(failure_reason(["x" * 1000 + "\n"])) == 400
    # the last error wins: a traceback after an earlier Sorry
    assert failure_reason(lines("Sorry: first\n" + TRACEBACK)).startswith("ModuleNotFoundError")


def test_failure_message():
    msg = failure_message("/opt/phenix/bin/phenix.real_space_refine", 1, failure_reason(lines(UNRECOGNIZED)))
    assert msg.startswith("phenix.real_space_refine exited with code 1: Sorry: Some PHIL parameters")
    assert "Tools page" in failure_message("phenix.foo", 127, "")
    assert failure_message("prog", -9, "") == "prog was stopped by SIGKILL (out of memory?), without any output"


def test_phenix_fixes_only_what_phenix_suggests_once():
    overlap = ["Sorry: Map and model are not aligned! Use skip_map_model_overlap_check=True to continue.\n"]
    symmetry = ["Sorry: Model is not similar to 'map.mrc': \n", "unit cell mismatch\n", "Try 'ignore_symmetry_conflicts=True'\n"]
    replaced = ["Sorry: phenix.auto_sharpen is replaced by phenix.map_sharpening Add the keyword '--force' to use anyway\n"]
    assert phenix_fix(overlap, ["p", "m.pdb"]) == "skip_map_model_overlap_check=True"
    assert phenix_fix(symmetry, ["p"]) == "ignore_symmetry_conflicts=True"
    assert phenix_fix(replaced, ["p"]) == "--force"
    assert phenix_fix(symmetry, ["p", "ignore_symmetry_conflicts=False"]) is None  # the user's own choice is kept
    assert phenix_fix(replaced, ["p", "--force"]) is None
    assert phenix_fix(lines(UNRECOGNIZED), ["p"]) is None  # anything else is reported, not guessed


def test_rsr_strategy():
    assert rsr_strategy("", False) == ""  # Phenix's default strategy
    assert rsr_strategy("", True) == "minimization_global+adp+nqh_flips"
    assert rsr_strategy("minimization_global+morphing", True) == "minimization_global+morphing+nqh_flips"
    assert rsr_strategy(" minimization_global, nqh_flips ", True) == "minimization_global+nqh_flips"
    assert rsr_strategy("minimization_global+nqh_flips", False) == "minimization_global+nqh_flips"  # as typed


def test_version_text():
    assert version_text("Phenix version 2.2.1\nRelease tag: 6327\nPlatform: linux\n") == "Phenix version 2.2.1 · Release tag: 6327"
    assert version_text("Phenix fake 1.21\n") == "Phenix fake 1.21"


def _model(tmp_path, name, b_values):
    st = gemmi.Structure()
    st.add_model(gemmi.Model("1"))
    ch = gemmi.Chain("A")
    for i, b in enumerate(b_values, start=1):
        r = gemmi.Residue()
        r.name, r.seqid = "ALA", gemmi.SeqId(i, " ")
        a = gemmi.Atom()
        a.name, a.element, a.pos, a.occ, a.b_iso = "CA", gemmi.Element("C"), gemmi.Position(0, 0, 1.5 * i), 1.0, b
        r.add_atom(a)
        ch.add_residue(r)
    st[0].add_chain(ch)
    path = tmp_path / name
    st.write_pdb(str(path))
    return str(path)


def test_plddt_cutoff_on_the_scale_of_the_model(tmp_path):
    # Phenix only uses minimum_plddt once its default maximum_rmsd is cleared, on the scale of the B-factor column
    af = _model(tmp_path, "af.pdb", [92.0, 85.5, 40.0])
    frac = _model(tmp_path, "frac.pdb", [0.92, 0.855, 0.4])
    assert plddt_cutoff_args(af, 70) == ["maximum_rmsd=None", "input_plddt_is_fractional=False", "minimum_plddt=70"]
    assert plddt_cutoff_args(frac, 70) == ["maximum_rmsd=None", "input_plddt_is_fractional=True", "minimum_plddt=0.7"]
    assert plddt_cutoff_args(af, 0.5)[-1] == "minimum_plddt=50"
    assert plddt_cutoff_args(str(tmp_path / "missing.pdb"), 70) == []  # Phenix's own default then


# ------------------------------------------------------------- jobs, with the fake Phenix of conftest
def _imports(manager, synthetic, model_path=None):
    p = manager.create_project("Phenix 2")
    imp = manager.create_job(p["uid"], "import_cryosparc", {"job_dir": str(synthetic["cryosparc_job"])})
    mdl = manager.create_job(p["uid"], "import_model", {"source": "file", "path": str(model_path or synthetic["model"])})
    for j in (imp, mdl):
        manager.queue_job(p["uid"], j["uid"])
    run_until_done(manager, p["uid"], [imp["uid"], mdl["uid"]])
    return p["uid"], imp["uid"], mdl["uid"]


def _run(manager, puid, job_type, params, inputs):
    job = manager.create_job(puid, job_type, params, inputs)
    manager.queue_job(puid, job["uid"])
    run_until_done(manager, puid, [job["uid"]])
    return manager.job(puid, job["uid"]), log_of(manager, puid, job["uid"])


def test_real_space_refine_strategy_and_overlap_check(manager, synthetic, tmp_path):
    misaligned = tmp_path / "misaligned.pdb"
    misaligned.write_text("REMARK 999 MISALIGNED\n" + synthetic["model"].read_text())
    puid, imp, mdl = _imports(manager, synthetic, misaligned)
    inputs = {"model": {"job": mdl, "output": "model"}, "map": {"job": imp, "output": "map_sharp"}}
    job, log = _run(manager, puid, "phenix_real_space_refine", {"nqh_flips": True}, inputs)
    assert job["status"] == "completed", job["error"]
    commands = (manager.job_dir(puid, job["uid"]) / "commands.sh").read_text()
    assert "run=minimization_global+adp+nqh_flips" in commands and "nqh_flips=True" not in commands
    # Phenix stopped on the overlap check, CryoPlug ran it again with the keyword Phenix asked for
    assert "Map and model are not aligned" in log and "Running it again with skip_map_model_overlap_check=True" in log
    assert commands.count("phenix.real_space_refine") == 2 and "skip_map_model_overlap_check=True" in commands

    # a parameter Phenix rejects: the job error says which, in Phenix's words
    job, log = _run(manager, puid, "phenix_real_space_refine", {"extra_args": "nqh_flips=True"}, inputs)
    assert job["status"] == "failed"
    assert job["error"] == ("phenix.real_space_refine exited with code 1: Sorry: Some PHIL parameters are not recognized "
                            "by phenix.real_space_refine: nqh_flips = True")


def _phenix_dir(fakebin, tmp_path, with_map_sharpening):
    """The fake Phenix, with phenix.map_sharpening (Phenix 2) or without it."""
    d = tmp_path / ("phenix2" if with_map_sharpening else "phenix_old")
    d.mkdir()
    for f in fakebin.iterdir():
        if f.name.startswith("phenix."):
            os.symlink(f, d / f.name)
    if with_map_sharpening:
        os.symlink(fakebin / "phenix.version", d / "phenix.map_sharpening")
    return d


@pytest.mark.parametrize("with_map_sharpening", [True, False])
def test_programs_replaced_in_phenix2_run_with_force(tmp_path, fakebin, synthetic, with_map_sharpening):
    cfg = Config(data_dir=tmp_path / "data", projects_root=tmp_path / "projects", browse_roots=[str(tmp_path.parent), "/tmp"],
                 lanes=[LaneConfig(name="local", type="local", max_jobs=4, gpus=[0])],
                 tools={"phenix": ToolConfig(name="phenix", bin_dir=str(_phenix_dir(fakebin, tmp_path, with_map_sharpening)))})
    manager = Manager(cfg)
    manager.check_tools()
    puid, imp, _ = _imports(manager, synthetic)
    halves = {"job": imp, "output": "half_maps"}
    for job_type, inputs in (("phenix_auto_sharpen", {"map": {"job": imp, "output": "map"}, "half_maps": halves}),
                             ("phenix_local_aniso_sharpen", {"half_maps": halves})):
        job, log = _run(manager, puid, job_type, {}, inputs)
        assert job["status"] == "completed", f"{job_type}: {job['error']}\n{log}"
        assert {o["name"] for o in job["outputs"]} == {"map"}
        # with map_sharpening next to it, the Phenix 2 way from the start; else after Phenix's own message
        assert ("is replaced by phenix.map_sharpening" in log) is (not with_map_sharpening)
        assert "--force" in log


def test_process_predicted_model_cutoff(manager, synthetic):
    puid, _, mdl = _imports(manager, synthetic)
    job, log = _run(manager, puid, "phenix_process_predicted_model", {"minimum_plddt": 80},
                    {"model": {"job": mdl, "output": "model"}})
    assert job["status"] == "completed", job["error"]
    # the synthetic helix has B = 30-52: read as pLDDT 0-100
    assert "maximum_rmsd=None input_plddt_is_fractional=False minimum_plddt=80" in log


def test_suggested_keyword_the_program_does_not_take(tmp_path, fakebin, synthetic):
    """Phenix suggests a keyword its program then rejects: the job reports the first error, not the second."""
    d = _phenix_dir(fakebin, tmp_path, True)
    (d / "phenix.validation_cryoem").unlink(missing_ok=True)
    script = d / "phenix.validation_cryoem"
    script.write_text("""#!/bin/bash
for a in "$@"; do case "$a" in ignore_symmetry_conflicts=*)
  printf '  Unrecognized PHIL parameters:\\n  -----------------------------\\n    ignore_symmetry_conflicts = True\\n\\n'
  echo "Sorry: Some PHIL parameters are not recognized by phenix.validation_cryoem."; exit 1;;
esac; done
echo "Sorry: Model is not similar to 'map.mrc': "
echo "Unit cell mismatch"
echo "Try 'ignore_symmetry_conflicts=True'"
exit 1
""")
    script.chmod(0o755)
    cfg = Config(data_dir=tmp_path / "data", projects_root=tmp_path / "projects", browse_roots=[str(tmp_path.parent), "/tmp"],
                 lanes=[LaneConfig(name="local", type="local", max_jobs=4, gpus=[0])],
                 tools={"phenix": ToolConfig(name="phenix", bin_dir=str(d))})
    manager = Manager(cfg)
    manager.check_tools()
    puid, imp, mdl = _imports(manager, synthetic)
    job, log = _run(manager, puid, "phenix_validation_cryoem", {},
                    {"model": {"job": mdl, "output": "model"}, "map": {"job": imp, "output": "map_sharp"}})
    assert job["status"] == "failed"
    assert job["error"].startswith("phenix.validation_cryoem exited with code 1: Sorry: Model is not similar to 'map.mrc': "
                                   "Unit cell mismatch (running it again with ignore_symmetry_conflicts=True did not work")
