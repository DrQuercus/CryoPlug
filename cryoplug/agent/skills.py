"""Agent skills: folders holding a ``SKILL.md`` (YAML front matter with ``name`` and ``description``, then Markdown
instructions) and reference files, in the open Agent Skills format read by Claude Code, Codex and other agents.

They ship in ``cryoplug/skills``.  ``cryoplug ai skills --install`` copies them where an agent looks for skills
(``~/.claude/skills`` for Claude Code, ``~/.agents/skills`` for Codex and the other Agent Skills clients); the MCP
server also offers them as resources and through the ``read_skill`` tool, for clients without skill support.
The job type index of the core skill is generated from the registry (:func:`job_index`), like ``docs/JOBS.md``.
"""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"
CORE = "cryoplug"
JOB_INDEX = "references/job-types.md"  # in the core skill
DESTINATIONS = {
    "claude": ("~/.claude/skills", "Claude Code (personal skills)"),
    "agents": ("~/.agents/skills", "Codex and other clients of the Agent Skills format"),
}
NAME_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def front_matter(text: str) -> tuple[dict[str, str], str]:
    """The ``key: value`` lines between the opening ``---`` lines, and the Markdown after them."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    meta: dict[str, str] = {}
    for line in text[3:end].splitlines():
        if ":" in line and not line.startswith((" ", "\t", "#")):
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip().strip("\"'")
    return meta, text[end + 4:].lstrip("\n")


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    path: Path

    def files(self) -> list[str]:
        """SKILL.md first, then the reference files (paths relative to the skill folder)."""
        extra = sorted(p.relative_to(self.path).as_posix() for p in self.path.rglob("*")
                       if p.is_file() and p.name != "SKILL.md" and not p.name.startswith(".") and "__pycache__" not in p.parts)
        return ["SKILL.md", *extra]

    def read(self, file: str = "SKILL.md") -> str:
        rel = PurePosixPath(str(file or "SKILL.md").strip().lstrip("./") or "SKILL.md")
        if ".." in rel.parts or not (self.path / rel).is_file():
            raise KeyError(f"The skill {self.name} has no file '{file}' (its files: {', '.join(self.files())})")
        return (self.path / rel).read_text(encoding="utf-8")


def all_skills() -> list[Skill]:
    """The skills shipped with CryoPlug, the core skill first."""
    found = []
    for path in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        meta, _ = front_matter(path.read_text(encoding="utf-8"))
        found.append(Skill(meta.get("name") or path.parent.name, meta.get("description", ""), path.parent))
    return sorted(found, key=lambda s: (s.name != CORE, s.name))


def get_skill(name: Any) -> Skill:
    name = str(name or "").strip().lower().removesuffix("/skill.md")
    skills = {s.name: s for s in all_skills()}
    for candidate in (name, f"{CORE}-{name}"):
        if candidate in skills:
            return skills[candidate]
    raise KeyError(f"No skill '{name}'. Skills: {', '.join(skills)}")


def problems(skill: Skill) -> list[str]:
    """What breaks the Agent Skills format (name, description) or points to a missing file."""
    out = []
    if not NAME_RE.fullmatch(skill.name) or len(skill.name) > 64:
        out.append(f"{skill.name}: the name must be lowercase letters, digits and single hyphens (64 at most)")
    if skill.name != skill.path.name:
        out.append(f"{skill.name}: the name must be the folder name ({skill.path.name})")
    if not 1 <= len(skill.description) <= 1024:
        out.append(f"{skill.name}: the description must have 1 to 1024 characters")
    for target in re.findall(r"\]\(((?:references|scripts|assets)/[^)#\s]+)", skill.read()):
        if not (skill.path / target).is_file():
            out.append(f"{skill.name}: SKILL.md links to a missing file {target}")
    return out


def install(dest: str | Path, names: list[str] | None = None, force: bool = False) -> list[Path]:
    """Copy the skills (all, or ``names``) into ``dest``; an existing copy is replaced only with ``force``."""
    dest = Path(dest).expanduser()
    chosen = [get_skill(n) for n in names] if names else all_skills()
    existing = [dest / s.name for s in chosen if (dest / s.name).exists()]
    if existing and not force:
        raise FileExistsError("Already installed (use --force to replace): " + ", ".join(map(str, existing)))
    dest.mkdir(parents=True, exist_ok=True)
    written = []
    for skill in chosen:
        target = dest / skill.name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(skill.path, target, ignore=shutil.ignore_patterns("__pycache__", ".*"))
        written.append(target)
    return written


# ------------------------------------------------------------------ job type index
def job_index() -> str:
    """The reference of the job types in the core skill (English), generated from the registry."""
    from cryoplug.jobhelp import job_help
    from cryoplug.jobs import CATEGORIES, all_job_types
    from cryoplug.tools import TOOLS

    types = all_job_types()
    lines = ["# CryoPlug job types", "",
             "Generated from the job registry by `cryoplug ai skills --write-reference`: do not edit by hand.",
             "One entry per job type: name, title, program, GPU use, inputs (slot: accepted data types; `?` = optional)",
             "and outputs (name: data type).  `describe_job_type NAME` gives the parameters with their defaults and the",
             "detailed guidance; the server tells which programs are installed (`server_status`).", ""]
    for category in CATEGORIES:
        members = [jt for jt in types if jt.category == category]
        if not members:
            continue
        lines += [f"## {category}", ""]
        for jt in members:
            program = TOOLS[jt.tool].label if jt.tool in TOOLS else "built-in"
            flags = [program]
            if jt.gpu_param:
                gp = jt.param(jt.gpu_param)
                flags.append("GPU optional" if gp.type == "bool" or gp.min == 0 else "GPU")
            elif jt.gpu:
                flags.append("GPU")
            if jt.interactive:
                flags.append("interactive: a person works in it")
            if jt.admin_only:
                flags.append("administrators only, not for agents")
            inputs = ", ".join(f"{s.name}{'' if s.required else '?'}: {'/'.join(s.types)}" for s in jt.inputs) or "none"
            outputs = ", ".join(f"{o.name}: {o.type}" for o in jt.outputs) or "none"
            lines.append(f"### `{jt.name}` — {jt.title}")
            lines.append(f"{' · '.join(flags)}. In: {inputs}. Out: {outputs}.")
            lines.append("")
            lines.append(jt.description)
            nxt = (job_help(jt.name) or {}).get("next") or []
            if nxt:
                lines.append(f"Usually followed by: {', '.join(f'`{n}`' for n in nxt)}.")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_job_index() -> Path:
    path = SKILLS_DIR / CORE / JOB_INDEX
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(job_index(), encoding="utf-8")
    return path
