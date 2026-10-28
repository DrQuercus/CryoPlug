"""Matching of the command palette (cryoplug/web/js/match.js, used by palette.js), run with Node: every word typed must be found, a
label starting with the words comes before a word of the label starting with them, then the rest; the jobs of the
open project come before the job types with the same match."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
PALETTE = Path(__file__).resolve().parent.parent / "cryoplug" / "web" / "js" / "match.js"

pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is needed to run the web modules")

SCRIPT = r"""
import { pathToFileURL } from 'node:url';
const { matchScore, rankItems } = await import(pathToFileURL(process.argv[1]).href);
const { query, items } = JSON.parse(process.argv[2]);
console.log(JSON.stringify({ scores: items.map((it) => matchScore(query, it)), order: rankItems(query, items).map((it) => it.id) }));
"""


def run(query, items):
    out = subprocess.run([NODE, "--input-type=module", "-e", SCRIPT, str(PALETTE), json.dumps({"query": query, "items": items})],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


ITEMS = [
    {"id": "job5", "group": "Jobs", "label": "J5 Real-space refinement (Phenix)", "sub": "Real-space refinement (Phenix) · best so far", "keywords": "phenix_real_space_refine completed"},
    {"id": "job3", "group": "Jobs", "label": "J3 LocScale 2", "sub": "LocScale 2", "keywords": "locscale completed"},
    {"id": "type_servalcat", "group": "New job", "label": "Refinement (Servalcat)", "sub": "Affinement contre les demi-cartes", "keywords": "servalcat_refine Refinement"},
    {"id": "type_rsr", "group": "New job", "label": "Real-space refinement (Phenix)", "sub": "Affinement en espace réel", "keywords": "phenix_real_space_refine Refinement"},
    {"id": "page_settings", "group": "Pages", "label": "Settings", "sub": "AI agents, users, compute, access"},
    {"id": "act_theme", "group": "Actions", "label": "Switch the light / dark theme", "sub": "Also the moon button"},
]


def test_every_word_must_match_and_the_best_match_comes_first():
    res = run("refine", ITEMS)
    scores = dict(zip([it["id"] for it in ITEMS], res["scores"]))
    assert scores["page_settings"] == 0 and scores["act_theme"] == 0 and scores["job3"] == 0
    assert scores["type_servalcat"] > scores["type_rsr"] > 0  # the label starts with it / a word of the label does
    # the jobs of the project first, then the types to start, the one whose label starts with the word first
    assert res["order"] == ["job5", "type_servalcat", "type_rsr"]
    assert run("phenix refine", ITEMS)["order"] == ["job5", "type_rsr"]  # both words, in any order
    assert run("space real", ITEMS)["order"] == ["job5", "type_rsr"]
    assert run("locs", ITEMS)["order"] == ["job3"]
    assert run("j3", ITEMS)["order"] == ["job3"]
    assert run("dark", ITEMS)["order"] == ["act_theme"]
    assert run("", ITEMS)["order"] == [it["id"] for it in ITEMS]  # nothing typed: all, as they came
    assert run("nothing like this", ITEMS)["order"] == []


def test_special_characters_are_plain_text():
    items = [{"id": "a", "group": "Jobs", "label": "J7 map (sharpened) +40 Å²", "sub": ""}, {"id": "b", "group": "Jobs", "label": "J8 other", "sub": ""}]
    assert run("(sharp", items)["order"] == ["a"]
    assert run("+40", items)["order"] == ["a"]
    assert run("å", items)["order"] == ["a"]
