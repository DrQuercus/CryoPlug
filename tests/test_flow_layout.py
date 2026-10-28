"""Layout of the decision map (cryoplug/web/js/flow.js), run with Node: the steps one row below what they follow from,
the runs of a round inside their box, lines from the bottom of a tile to the top of the next passing between the tiles
of the rows they cross, the path lit up and the state of a box."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
FLOW = Path(__file__).resolve().parent.parent / "cryoplug" / "web" / "js" / "flow.js"

pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is needed to run the web modules")

# The modules only need these two browser objects to load.
SCRIPT = r"""
import { pathToFileURL } from 'node:url';
globalThis.document = { addEventListener() {} };
globalThis.localStorage = { getItem() { return null; }, setItem() {} };
const { layoutFlow, lineageOf, groupStatus, FLOW } = await import(pathToFileURL(process.argv[1]).href);
const { nodes, target, members } = JSON.parse(process.argv[2]);
if (members) {
  console.log(JSON.stringify({ status: groupStatus(members) }));
} else {
  const L = layoutFlow(nodes);
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const kids = (g) => nodes.filter((n) => n.group === g.id && n.kind !== 'group');
  const statusOf = (n) => (n.kind === 'group' ? groupStatus(kids(n), n.status) : n.status || 'done'); // as the panel
  const lit = target ? [...lineageOf(target, byId, statusOf, L.owner)] : [];
  console.log(JSON.stringify({ dims: FLOW, pos: Object.fromEntries(L.pos), runs: Object.fromEntries(L.runs), edges: L.edges,
    rows: L.rows, width: L.width, height: L.height, lit }));
}
"""


def run(payload):
    out = subprocess.run([NODE, "--input-type=module", "-e", SCRIPT, str(FLOW), json.dumps(payload)],
                         capture_output=True, text=True, timeout=60, check=True)
    return json.loads(out.stdout)


def node(node_id, kind="decision", parents=(), status="done", group=None):
    n = {"id": node_id, "kind": kind, "title": node_id, "status": status, "parents": list(parents)}
    if group:
        n["group"] = group
    return n


# A job as CryoAtom2 enhanced draws it: four runs, the best settings tried on a re-sharpened map, the best of round 1,
# its gaps filled, a map improvement cycle that is not better, the hand not checked, the result.
JOB = [
    node("input", "input"),
    node("map:B", "map", ["input"]),
    node("settings", parents=["input"]),
    node("group:1", "group", ["settings"]),
    *(node(f"run:{i}", "run", status="chosen" if i == 3 else "discarded", group="group:1") for i in range(1, 5)),
    node("best_setting", parents=["run:3"], status="chosen"),
    node("group:B", "group", ["map:B", "best_setting"]),
    node("run:b1", "run", status="discarded", group="group:B"),
    node("run:b2", "run", status="failed", group="group:B"),
    node("best:1", parents=["best_setting", "group:B"], status="chosen"),
    node("combine", parents=["best:1"], status="chosen"),
    node("improve", "map", ["combine"], status="discarded"),
    node("group:improve", "group", ["improve"]),
    node("run:i1", "run", status="discarded", group="group:improve"),
    node("best:improve", parents=["run:i1"], status="discarded"),
    node("hand", parents=["combine"], status="skipped"),
    node("result", "result", ["combine"], status="chosen"),
]


def overlap(a, b, gap=0.5):
    return a["left"] < b["left"] + b["w"] - gap and b["left"] < a["left"] + a["w"] - gap and \
        a["top"] < b["top"] + b["h"] - gap and b["top"] < a["top"] + a["h"] - gap


def test_layout_of_a_decision_map():
    lay = run({"nodes": JOB, "target": "result"})
    pos, runs, dims = lay["pos"], lay["runs"], lay["dims"]
    assert set(pos) == {n["id"] for n in JOB if not n.get("group")} and set(runs) == {n["id"] for n in JOB if n.get("group")}
    # each step below what it follows from (a run through its box)
    owner = {n["id"]: n.get("group") or n["id"] for n in JOB}
    for n in JOB:
        for p in n["parents"]:
            assert pos[owner[p]]["row"] < pos[owner[n["id"]]]["row"], (p, n["id"])
    # a map used only further down comes down next to its use
    assert pos["map:B"]["row"] == pos["group:B"]["row"] - 1 == pos["best_setting"]["row"]
    # boxes never overlap; the runs sit inside their box, side by side, in order
    boxes = list(pos.values())
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            assert not overlap(a, b), (a["id"], b["id"])
    for r in runs.values():
        g = pos[r["group"]]
        assert g["left"] <= r["left"] and r["left"] + r["w"] <= g["left"] + g["w"] + 0.01
        assert g["top"] + dims["head"] <= r["top"] and r["top"] + r["h"] <= g["top"] + g["h"] + 0.01
    lefts = [runs[f"run:{i}"]["left"] for i in range(1, 5)]
    assert lefts == sorted(lefts) and len({runs[f"run:{i}"]["top"] for i in range(1, 5)}) == 1
    assert pos["group:1"]["w"] >= 4 * dims["runW"] and pos["result"]["w"] == dims["w"]
    # lines: from under the run (or the bottom of the tile) to the top of the next tile
    edges = {(e["from"], e["to"]): e for e in lay["edges"]}
    assert set(edges) == {(p, n["id"]) for n in JOB for p in n["parents"]}
    e = edges[("run:3", "best_setting")]
    r, g, t = runs["run:3"], pos["group:1"], pos["best_setting"]
    assert e["x1"] == pytest.approx(r["left"] + r["w"] / 2) and e["y1"] == pytest.approx(g["top"] + g["h"])
    assert t["left"] < e["x2"] < t["left"] + t["w"] and e["y2"] == pytest.approx(t["top"]) and e["d"].startswith("M")
    # two lines into one tile reach it at two places
    into = [edges[("best_setting", "best:1")], edges[("group:B", "best:1")]]
    assert into[0]["x2"] != into[1]["x2"]
    # a line crossing a row passes between its tiles: the input's line down to the re-sharpened map
    long = edges[("input", "map:B")]
    assert pos["map:B"]["row"] - pos["input"]["row"] > 1 and long["d"].count("C") >= 2
    assert lay["width"] >= max(b["left"] + b["w"] for b in boxes) and lay["height"] >= max(b["top"] + b["h"] for b in boxes)


def test_path_lit_up():
    # to the result: the steps kept, not the boxes compared and left aside, nor the branches that led nowhere
    lit = set(run({"nodes": JOB, "target": "result"})["lit"])
    assert lit == {"result", "combine", "best:1", "best_setting", "run:3", "group:1", "settings", "input"}
    # to a step not kept: how the job got there, through what was not kept
    lit = set(run({"nodes": JOB, "target": "best:improve"})["lit"])
    assert {"best:improve", "run:i1", "group:improve", "improve", "combine", "best:1", "input"} <= lit
    assert "group:B" not in lit and "hand" not in lit and "result" not in lit


def test_state_of_a_box():
    assert run({"members": []})["status"] == "done"
    assert run({"members": [{"status": "done"}, {"status": "running"}]})["status"] == "running"
    assert run({"members": [{"status": "chosen"}, {"status": "discarded"}]})["status"] == "chosen"
    assert run({"members": [{"status": "discarded"}, {"status": "failed"}]})["status"] == "discarded"
    assert run({"members": [{"status": "failed"}, {"status": "failed"}]})["status"] == "failed"
    assert run({"members": [{"status": "done"}, {"status": "done"}]})["status"] == "done"


def test_a_map_still_growing():
    """While the job runs: a box without runs yet, a step whose parent is not on the map, a single step."""
    lay = run({"nodes": [node("input", "input"), node("settings", parents=["input"]), node("group:1", "group", ["settings"]),
                         node("late", parents=["missing"])], "target": "settings"})
    assert lay["pos"]["group:1"]["h"] > 0 and lay["pos"]["late"]["row"] == 0 and set(lay["lit"]) == {"settings", "input"}
    one = run({"nodes": [node("input", "input")]})
    assert one["edges"] == [] and one["rows"] == [["input"]]
