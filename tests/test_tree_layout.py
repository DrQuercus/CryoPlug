"""Layout of the tree view (cryoplug/web/js/tree.js), run with Node: rows, spacing, shared trunks, loops."""
from __future__ import annotations

import json
import random
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
TREE = Path(__file__).resolve().parent.parent / "cryoplug" / "web" / "js" / "tree.js"
GAP = 30  # between two cards of a row (tree.js GAP_X)

pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is needed to run the web modules")

# The module only needs these two browser objects to load.
SCRIPT = r"""
import { pathToFileURL } from 'node:url';
globalThis.document = { addEventListener() {} };
globalThis.localStorage = { getItem() { return null; }, setItem() {} };
const { layoutTree, NODE_W, NODE_H } = await import(pathToFileURL(process.argv[1]).href);
const jobs = JSON.parse(process.argv[2]);
const t0 = performance.now();
const L = layoutTree(jobs, (ref) => (ref.output.startsWith('model') ? 'model' : ref.output.startsWith('report') ? 'report' : 'map'));
console.log(JSON.stringify({
  ms: performance.now() - t0, nodeW: NODE_W, nodeH: NODE_H, width: L.width, height: L.height, rows: L.rows,
  pos: Object.fromEntries(L.pos),
  links: L.links.map((l) => ({ from: l.from, to: l.to, family: l.family, path: l.path, back: !!l.back })),
  segs: L.segs.map((s) => ({ from: s.from, to: s.to, family: s.family, d: s.d, head: s.head, links: s.links.length })),
}));
"""


def layout(jobs):
    out = subprocess.run([NODE, "--input-type=module", "-e", SCRIPT, str(TREE), json.dumps(jobs)],
                         capture_output=True, text=True, timeout=60, check=True)
    return json.loads(out.stdout)


def job(num, **inputs):
    return {"uid": f"J{num}", "num": num, "status": "completed",
            "inputs": {slot: {"job": f"J{src}", "output": out} for slot, (src, out) in inputs.items()}}


def check_drawing(jobs, lay):
    """Each input sits on a row above its job; the cards of a row never overlap; every link is drawn."""
    pos = lay["pos"]
    for j in jobs:
        for ref in j["inputs"].values():
            if ref["job"] in pos and ref["job"] != j["uid"]:
                assert pos[ref["job"]]["row"] < pos[j["uid"]]["row"], (ref["job"], j["uid"])
    rows = {}
    for p in pos.values():
        rows.setdefault(p["row"], []).append(p["x"])
    for xs in rows.values():
        xs.sort()
        assert all(b - a >= lay["nodeW"] + GAP - 0.5 for a, b in zip(xs, xs[1:])), xs
    for p in pos.values():
        assert 0 <= p["x"] and p["x"] + lay["nodeW"] <= lay["width"] + 0.5 and p["y"] + lay["nodeH"] <= lay["height"] + 0.5
    for s in lay["segs"]:
        assert s["d"].startswith("M") and " C" in s["d"]
        assert (s["head"] is not None) == (s["to"] in pos)  # an arrowhead where a line reaches a job


def test_pipeline_rows_trunks_and_late_imports():
    # an import feeding most of the pipeline, a sequence import used late, a model branch and a report
    jobs = [
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
    lay = layout(jobs)
    check_drawing(jobs, lay)
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
    # J8 receives two inputs from J1 (map and mask): one link, one arrowhead
    assert len([lk for lk in lay["links"] if lk["from"] == "J1" and lk["to"] == "J8"]) == 1
    assert {lk["family"] for lk in lay["links"] if lk["to"] == "J9"} == {"model", "other"}


def test_a_loop_does_not_break_the_drawing():
    # two jobs being edited that point at each other (never run): drawn, without hanging
    jobs = [job(1), job(2, a=(1, "map"), b=(3, "map")), job(3, a=(2, "map"))]
    lay = layout(jobs)
    assert set(lay["pos"]) == {"J1", "J2", "J3"}
    assert any(lk["back"] for lk in lay["links"])
    assert all(s["d"].startswith("M") for s in lay["segs"])


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
    lay = layout(jobs)
    check_drawing(jobs, lay)
    assert lay["ms"] < 3000
    # rows hold the jobs in their drawing order (keyboard moves along them)
    assert sorted(uid for row in lay["rows"] for uid in row) == sorted(j["uid"] for j in jobs)
    for row in lay["rows"]:
        xs = [lay["pos"][uid]["x"] for uid in row]
        assert xs == sorted(xs)
