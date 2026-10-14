"""Layout of the tree view (cryoplug/web/js/tree.js), run with Node: rows, spacing, shared trunks, square lines in
lanes, loops, and the jobs upstream or downstream of one."""
from __future__ import annotations

import json
import random
import re
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
TREE = Path(__file__).resolve().parent.parent / "cryoplug" / "web" / "js" / "tree.js"

pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is needed to run the web modules")

# The module only needs these two browser objects to load.
SCRIPT = r"""
import { pathToFileURL } from 'node:url';
globalThis.document = { addEventListener() {} };
globalThis.localStorage = { getItem() { return null; }, setItem() {} };
const { layoutTree, scopeOf, DIMS } = await import(pathToFileURL(process.argv[1]).href);
const { jobs, look, scope } = JSON.parse(process.argv[2]);
if (scope) {
  const sc = scopeOf(jobs, scope);
  console.log(JSON.stringify({ uids: sc.jobs.map((j) => j.uid), scope: sc.scope }));
} else {
  const t0 = performance.now();
  const L = layoutTree(jobs, (ref) => (ref.output.startsWith('model') ? 'model' : ref.output.startsWith('report') ? 'report' : 'map'), DIMS[look]);
  console.log(JSON.stringify({
    ms: performance.now() - t0, w: L.w, h: L.h, gapX: DIMS[look].gapX, width: L.width, height: L.height, rows: L.rows,
    pos: Object.fromEntries(L.pos),
    links: L.links.map((l) => ({ from: l.from, to: l.to, family: l.family, path: l.path, back: !!l.back })),
    segs: L.segs.map((s) => ({ from: s.from, to: s.to, family: s.family, d: s.d, dots: s.dots, links: s.links.length,
      lane: s.lane ?? null, x1: s.x1, x2: s.x2, group: s.group ? s.group.key : null, back: s.back })),
  }));
}
"""


def run(payload):
    out = subprocess.run([NODE, "--input-type=module", "-e", SCRIPT, str(TREE), json.dumps(payload)],
                         capture_output=True, text=True, timeout=60, check=True)
    return json.loads(out.stdout)


def layout(jobs, look="cards"):
    return run({"jobs": jobs, "look": look})


def job(num, **inputs):
    return {"uid": f"J{num}", "num": num, "status": "completed",
            "inputs": {slot: {"job": f"J{src}", "output": out} for slot, (src, out) in inputs.items()}}


PIPELINE = [
    job(1),
    job(2),  # sequence: used by J5 only
    job(3, map=(1, "map")),
    job(4, map=(3, "map"), half=(1, "half_maps")),
    job(5, map=(4, "map"), seq=(2, "model_sequence")),
    job(6, model=(5, "model"), map=(1, "map")),
    job(7, model=(6, "model"), map=(1, "map")),
    job(8, model=(6, "model"), map=(1, "map"), mask=(1, "mask")),
    job(9, report=(8, "report"), model=(7, "model")),
]


def check_drawing(jobs, lay):
    """Inputs sit on rows above; cards never overlap; square lines; lanes are never shared by overlapping lines."""
    pos = lay["pos"]
    for j in jobs:
        for ref in j["inputs"].values():
            if ref["job"] in pos and ref["job"] != j["uid"]:
                assert pos[ref["job"]]["row"] < pos[j["uid"]]["row"], (ref["job"], j["uid"])
    rows = {}
    for p in pos.values():
        rows.setdefault(p["row"], []).append(p)
    for row in rows.values():
        assert len({round(p["y"], 3) for p in row}) == 1  # a row is aligned
        xs = sorted(p["x"] for p in row)
        assert all(b - a >= lay["w"] + lay["gapX"] - 0.5 for a, b in zip(xs, xs[1:])), xs
    tops = sorted({p["y"] for p in pos.values()})
    assert all(b - a >= lay["h"] + 30 for a, b in zip(tops, tops[1:]))  # room for the lanes between the rows
    for p in pos.values():
        assert 0 <= p["x"] and p["x"] + lay["w"] <= lay["width"] + 0.5 and p["y"] + lay["h"] <= lay["height"] + 0.5
    for s in lay["segs"]:
        assert re.fullmatch(r"M[\d.,-]+( [VHQ][\d., -]+)*", s["d"]), s["d"]  # square lines, rounded corners
        assert len([d for d in s["dots"] if s["to"] in pos and d == [s["x2"], pos[s["to"]]["y"]]]) == (1 if s["to"] in pos else 0)
    lanes = [s for s in lay["segs"] if s["lane"] is not None]
    for i, a in enumerate(lanes):
        for b in lanes[i + 1:]:
            if a["group"] != b["group"] and abs(a["lane"] - b["lane"]) < 0.01:
                lo = max(min(a["x1"], a["x2"]), min(b["x1"], b["x2"]))
                hi = min(max(a["x1"], a["x2"]), max(b["x1"], b["x2"]))
                assert lo > hi, (a, b)  # two sources on one lane: their lines do not overlap


@pytest.mark.parametrize("look", ["cards", "compact"])
def test_pipeline_rows_trunks_and_late_imports(look):
    lay = layout(PIPELINE, look)
    check_drawing(PIPELINE, lay)
    pos = lay["pos"]
    assert [pos[f"J{n}"]["row"] for n in (1, 3, 4, 5, 6, 7, 8, 9)] == [0, 1, 2, 3, 4, 5, 5, 6]
    assert pos["J2"]["row"] == pos["J5"]["row"] - 1  # the sequence comes down next to its use
    # the maps of J1 towards rows 2, 4 and 5 (J4, J6, J7, J8) share one trunk: a single line leaves J1 for them
    long_links = [lk for lk in lay["links"] if lk["from"] == "J1" and len(lk["path"]) > 2]
    assert len(long_links) == 4
    first_steps = {lk["path"][1] for lk in long_links}
    assert len(first_steps) == 1
    trunk = [s for s in lay["segs"] if s["from"] == "J1" and s["to"] in first_steps]
    assert len(trunk) == 1 and trunk[0]["links"] == 4 and trunk[0]["family"] == "map"
    # the maps of J1 leave it from one place, as a bus: to J3 directly and down the trunk
    starts = {(s["x1"], s["group"]) for s in lay["segs"] if s["from"] == "J1"}
    assert len(starts) == 1
    # J8 receives two inputs from J1 (map and mask): one link
    assert len([lk for lk in lay["links"] if lk["from"] == "J1" and lk["to"] == "J8"]) == 1
    assert {lk["family"] for lk in lay["links"] if lk["to"] == "J9"} == {"model", "other"}


def test_a_loop_does_not_break_the_drawing():
    # two jobs being edited that point at each other (never run): drawn round the side, without hanging
    jobs = [job(1), job(2, a=(1, "map"), b=(3, "map")), job(3, a=(2, "map"))]
    lay = layout(jobs)
    assert set(lay["pos"]) == {"J1", "J2", "J3"}
    loop = [s for s in lay["segs"] if s["back"]]
    assert loop and all(s["d"].startswith("M") for s in lay["segs"])
    assert lay["width"] > max(p["x"] + lay["w"] for p in lay["pos"].values())  # the detour is inside the drawing


def test_large_project_is_laid_out_quickly_without_overlaps():
    rnd = random.Random(7)
    jobs = []
    for n in range(1, 241):
        inputs = {}
        if n > 3:
            for q in range(rnd.randint(1, 3)):
                back = rnd.randrange(n - 1) if rnd.random() < 0.2 else rnd.randrange(min(6, n - 1))
                inputs[f"in{q}"] = (n - 1 - back, rnd.choice(["map", "half_maps", "model"]))
        jobs.append(job(n, **inputs))
    lay = layout(jobs, "compact")
    check_drawing(jobs, lay)
    assert lay["ms"] < 3000
    # rows hold the jobs in their drawing order (keyboard moves along them)
    assert sorted(uid for row in lay["rows"] for uid in row) == sorted(j["uid"] for j in jobs)
    for row in lay["rows"]:
        xs = [lay["pos"][uid]["x"] for uid in row]
        assert xs == sorted(xs)


def test_upstream_and_downstream_of_a_job():
    up = run({"jobs": PIPELINE, "scope": {"uid": "J5", "dir": "up", "depth": 0}})
    assert sorted(up["uids"]) == ["J1", "J2", "J3", "J4", "J5"] and up["scope"]["levels"] == 2  # J1 feeds J4 directly
    near = run({"jobs": PIPELINE, "scope": {"uid": "J5", "dir": "up", "depth": 1}})
    assert sorted(near["uids"]) == ["J2", "J4", "J5"] and near["scope"]["depth"] == 1
    down = run({"jobs": PIPELINE, "scope": {"uid": "J6", "dir": "down", "depth": 0}})
    assert sorted(down["uids"]) == ["J6", "J7", "J8", "J9"]
    whole = run({"jobs": PIPELINE, "scope": {"uid": "J6", "dir": "down", "depth": 9}})  # deeper than the tree: all
    assert whole["scope"]["depth"] == 0 and len(whole["uids"]) == 4
    gone = run({"jobs": PIPELINE, "scope": {"uid": "J99", "dir": "up", "depth": 0}})  # a deleted job: everything
    assert len(gone["uids"]) == len(PIPELINE) and gone["scope"] is None
