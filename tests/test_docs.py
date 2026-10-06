"""Every job type has a complete French help sheet, and the generated docs are up to date."""
from __future__ import annotations

from pathlib import Path

from cryoplug.jobhelp import FIELDS, HELP, render_markdown
from cryoplug.jobs import all_job_types

ROOT = Path(__file__).resolve().parent.parent


def test_every_job_has_help():
    names = {jt.name for jt in all_job_types()}
    assert set(HELP) == names
    for name, sheet in HELP.items():
        assert set(sheet) == set(FIELDS), name
        assert sheet["purpose"] and sheet["inputs"] and sheet["when"], name
        for nxt in sheet["next"]:
            assert nxt in names, (name, nxt)
    for jt in all_job_types():
        assert jt.schema()["help"]["purpose"]


def test_generated_job_reference_is_current():
    committed = (ROOT / "docs" / "JOBS.md").read_text()
    assert committed == render_markdown(), "docs/JOBS.md is stale: run `cryoplug docs-jobs`"


def test_guide_is_packaged_and_links_resolve():
    guide = (ROOT / "cryoplug" / "web" / "docs" / "GUIDE.md").read_text()
    anchors = set()
    for line in guide.splitlines():
        if line.startswith("#"):
            text = line.lstrip("#").strip()
            anchors.add("".join(c for c in text.lower() if c.isalnum() or c in " -").replace(" ", "-"))
    import re
    for target in re.findall(r"\]\(#([^)]+)\)", guide):
        assert target in anchors, target
