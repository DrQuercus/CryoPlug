"""Decision map of a job (cryoplug.jobs.decisions): steps added while the job runs, replaced in place as they end, the
choice of a decision marking what it kept and what it left aside; the maps and models of the steps opened in 3D until
they are deleted; and the map as the chat assistants read it."""
from __future__ import annotations

import json

import pytest

from cryoplug.agent.tools import report_lines
from cryoplug.jobs.base import JobContext
from cryoplug.jobs.decisions import DecisionMap


def _ctx(manager, title="Decisions"):
    p = manager.create_project(title)
    job = manager.create_job(p["uid"], "import_maps", {"map": "/nowhere.mrc"})
    jdir = manager.job_dir(p["uid"], job["uid"])
    jdir.mkdir(parents=True, exist_ok=True)
    ctx = JobContext({"uid": job["uid"], "project_uid": p["uid"], "project_dir": str(manager.project_dir(p["uid"]))}, jdir)
    return p["uid"], job["uid"], ctx


def _flow(ctx):
    report = json.loads(ctx.report_path.read_text())
    return next(s for s in report["sections"] if s["kind"] == "flow")


def test_steps_of_a_decision_map(manager):
    puid, juid, ctx = _ctx(manager)
    (ctx.job_dir / "map.mrc").write_text("x")
    (ctx.job_dir / "run1.cif").write_text("x")
    f = DecisionMap(ctx, note="How the job got there")
    f.add("input", "input", "Experimental map", lines=["map.mrc", "", None, "3.1 Å"], view=[{"kind": "map", "path": ctx.job_dir / "map.mrc"}])
    f.add("group:1", "group", "Round 1", parents=["input", "nowhere"])
    f.add("run:1", "run", "Standard 1", group="group:1", status="running", lines=["running…"])
    f.add("run:2", "run", "Standard 2", group="group:1", status="running")
    f.add("run:3", "run", "Standard 3", group="not a box")
    sec = _flow(ctx)
    assert sec["title"] == "Decision map" and sec["key"] == "decision_map" and sec["note"] == "How the job got there"
    nodes = {n["id"]: n for n in sec["nodes"]}
    assert nodes["input"]["lines"] == ["map.mrc", "3.1 Å"]
    assert nodes["input"]["view"] == [{"kind": "map", "path": f"{juid}/map.mrc", "label": "map.mrc"}]
    assert nodes["group:1"]["parents"] == ["input"]  # the unknown parent is left out
    assert nodes["run:1"]["group"] == "group:1" and "group" not in nodes["run:3"]
    # a run that ends: in its place, its scores and its model
    f.update("run:1", status="done", lines=["13 fitted (100%)"], view=[{"kind": "model", "path": ctx.job_dir / "run1.cif", "label": "r1"}])
    f.update("run:2", status="failed", lines=["failed"])
    f.update("missing", status="done")  # nothing to update: no error
    f.mark("run:1", ["run:1", "run:2"])  # a failed run stays failed
    f.add("best:1", "decision", "Best of 1 run", parents=["run:1"], status="chosen")
    nodes = _flow(ctx)["nodes"]
    assert [n["id"] for n in nodes] == ["input", "group:1", "run:1", "run:2", "run:3", "best:1"]
    by_id = {n["id"]: n for n in nodes}
    assert by_id["run:1"]["status"] == "chosen" and by_id["run:2"]["status"] == "failed"
    assert by_id["run:1"]["lines"] == ["13 fitted (100%)"] and by_id["run:1"]["view"][0]["path"] == f"{juid}/run1.cif"
    f.mark(None, ["run:1"])  # a later decision kept nothing of it
    assert {n["id"]: n for n in _flow(ctx)["nodes"]}["run:1"]["status"] == "discarded"
    f.update("input", lines=[], view=[])  # emptied: the keys go
    assert "lines" not in _flow(ctx)["nodes"][0] and "view" not in _flow(ctx)["nodes"][0]
    with pytest.raises(ValueError, match="kind"):
        f.add("x", "rocket", "?")
    with pytest.raises(ValueError, match="status"):
        f.add("x", "run", "?", status="great")
    # the maps and models of the steps are offered in 3D until they are deleted (models of the runs not kept)
    (ctx.job_dir / "run1.cif").unlink()
    detail = manager.job_detail(puid, juid)
    flow = next(s for s in detail["report"]["sections"] if s["kind"] == "flow")
    assert {n["id"]: n for n in flow["nodes"]}["run:1"]["view"] == [] and flow["nodes"][0].get("view") is None


def test_decision_map_for_the_assistant(manager):
    _, _, ctx = _ctx(manager, "Assistant")
    f = DecisionMap(ctx)
    f.add("input", "input", "Experimental map", lines=["3.1 Å"])
    f.add("settings", "decision", "Settings to try", parents=["input"], lines=["Standard, More residues"])
    f.add("group:1", "group", "Round 1 · 2 runs", parents=["settings"])
    f.add("run:1", "run", "Standard 1", group="group:1", status="discarded", lines=["400 fitted (60%)"])
    f.add("run:2", "run", "More residues 1", group="group:1", status="chosen", lines=["480 fitted (72%)"])
    f.add("run:3", "run", "More residues 2", group="group:1", status="failed", lines=["failed", "CUDA out of memory"])
    f.add("best:1", "decision", "Best of 2 runs", parents=["run:2"], status="chosen", lines=["More residues 1"])
    f.add("combine", "decision", "Gap filling not kept", parents=["best:1"], status="discarded", lines=["score 470 → 468: no better"])
    f.add("improve", "map", "LocScale hybrid · cycle 1", parents=["best:1"], status="running")
    lines = report_lines(json.loads(ctx.report_path.read_text()))
    assert lines[0] == "[Decision map] 9 steps (drawn as a map in the job panel)"
    assert lines[1] == ("  path to the best so far: Experimental map (3.1 Å) › Settings to try (Standard, More residues) › "
                        "More residues 1 (480 fitted (72%)) › Best of 2 runs (More residues 1)")
    text = "\n".join(lines)
    assert "  not kept: Gap filling not kept (score 470 → 468: no better)" in text and "Standard 1" not in text
    assert "  failed: More residues 2 (failed; CUDA out of memory)" in text
    assert "  running: LocScale hybrid · cycle 1" in text
    f.add("result", "result", "Best model", parents=["best:1"], status="chosen", lines=["More residues 1"])
    lines = report_lines(json.loads(ctx.report_path.read_text()))
    assert lines[1].startswith("  path to the result: Experimental map") and lines[1].endswith("› Best model (More residues 1)")
