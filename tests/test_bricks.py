"""The brick plan of the 3D viewer (cryoplug/web/js/viewer/bricks.js, run with Node): Mol*'s rule for GPU isosurfaces
reproduced, the fewest parts whose bricks pass it, the same cut as the server's parts_plan."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from cryoplug.mrc import parts_plan

NODE = shutil.which("node")
MODULE = Path(__file__).resolve().parent.parent / "cryoplug" / "web" / "js" / "viewer" / "bricks.js"
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is needed to run the web modules")

SCRIPT = r"""
import { pathToFileURL } from 'node:url';
const m = await import(pathToFileURL(process.argv[1]).href);
const { call, args } = JSON.parse(process.argv[2]);
console.log(JSON.stringify(m[call](...args)));
"""


def run(call, *args):
    out = subprocess.run([NODE, "--input-type=module", "-e", SCRIPT, str(MODULE), json.dumps({"call": call, "args": list(args)})],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_gpu_rule_matches_molstar():
    # measured: a 320³ map falls back to the CPU on a GPU with 8192-wide textures, its 161³ bricks do not; 200³ passes
    assert run("suitableForGpu", [200, 200, 200], 8192) is True
    assert run("suitableForGpu", [320, 320, 320], 8192) is False
    assert run("suitableForGpu", [161, 161, 161], 8192) is True
    assert run("suitableForGpu", [400, 400, 400], 16384) is True and run("suitableForGpu", [512, 512, 512], 16384) is False
    assert run("suitableForGpu", [8, 8, 8], 8192) is False  # tiny: Mol* keeps it on the CPU
    layout = run("textureLayout", [161, 161, 161])
    assert layout["powerOfTwoSize"] == 4096 and layout["columns"] == 12 and layout["rows"] == 14


def test_plan_uses_the_fewest_parts_and_the_server_cut():
    plan = run("brickPlan", [320, 320, 320], {"ok": True, "maxTextureSize": 8192})
    assert plan["parts"] == [2, 2, 2] and plan["gpu"] is True and len(plan["bricks"]) == 8
    assert run("brickPlan", [200, 200, 200], {"ok": True, "maxTextureSize": 8192})["parts"] == [1, 1, 1]
    assert run("brickPlan", [400, 400, 400], {"ok": True, "maxTextureSize": 16384})["parts"] == [1, 1, 1]
    assert run("brickPlan", [512, 512, 512], {"ok": True, "maxTextureSize": 8192})["parts"] == [3, 3, 3]
    # a flat box: the short axis is cut less
    plan = run("brickPlan", [400, 400, 200], {"ok": True, "maxTextureSize": 8192})
    assert plan["parts"] == [2, 2, 1]
    # no float textures: one part, the CPU path
    plan = run("brickPlan", [320, 320, 320], {"ok": False, "maxTextureSize": 8192})
    assert plan["parts"] == [1, 1, 1] and plan["gpu"] is False
    # the bricks are the server's parts (margins included); each draws the voxels it owns, the shared edge voxel included
    bricks = run("brickBoxes", [100, 90, 7], [3, 2, 1])
    server = parts_plan((100, 90, 7), (3, 2, 1))
    assert [(b["index"], b["start"], b["size"]) for b in bricks] == [(list(e["index"]), list(e["start"]), list(e["size"])) for e in server]
    owned = sorted({(b["start"][0] + b["core"][0][0], b["start"][0] + b["core"][0][1]) for b in bricks})
    assert owned == [(0, 33), (33, 66), (66, 99)]
    assert bricks[0]["core"] == [[0, 33], [0, 45], [0, 6]] and bricks[1]["core"] == [[1, 34], [0, 45], [0, 6]]
    mem = run("memoryEstimate", plan)
    assert mem["voxels"] == 320 ** 3 and mem["bytes"] == 320 ** 3 * 4  # the CPU plan: one part, float values only
    mem = run("memoryEstimate", run("brickPlan", [320, 320, 320], {"ok": True, "maxTextureSize": 8192}))
    assert mem["voxels"] == 323 ** 3 and mem["bytes"] == mem["voxels"] * 5  # per axis 162 + 161 voxels (shared edge, margins), plus the GPU bytes
