"""The tools an AI agent calls to drive CryoPlug, served by the MCP server (:mod:`cryoplug.agent.mcp`) and by
``cryoplug ai call``.

Each tool is a function of a :class:`Session` (the HTTP client of the server, and a few cached answers) and of its
arguments, answering compact text written for language models, small local ones included: one line per job, the
numbers of the reports, and what to do next.  Arguments are read leniently (``"J3"``, ``"3"`` or ``"j3"``; numbers
and booleans given as text; objects given as JSON text), and a mistake is answered with the valid choices, so that
the agent can correct itself.

Everything goes through the HTTP API with the rights of the agent's credential: the server checks permissions as
for a person.  Deleting projects or jobs and managing accounts are not offered; job types and parameters that run
arbitrary commands (administrators only) are refused unless the server was started with ``--allow-commands``.
"""
from __future__ import annotations

import base64
import difflib
import fnmatch
import json
import os
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from cryoplug.agent.client import AgentError, ApiError

ACTIVE = ("queued", "launched", "running")
DONE = ("completed", "failed", "killed")
TEXT_SUFFIXES = (".log", ".out", ".txt", ".star", ".csv", ".tsv", ".json", ".xml", ".fasta", ".fa", ".seq", ".pdb",
                 ".cif", ".mmcif", ".ent", ".py", ".sh", ".cxc", ".md", ".yaml", ".yml", ".toml", ".def", ".eff", ".params",
                 ".html", ".htm")
BINARY_SUFFIXES = (".mrc", ".map", ".ccp4", ".mrcs", ".cs", ".npy", ".npz", ".pkl", ".pt", ".zip", ".gz", ".tgz", ".bz2",
                   ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".pdf", ".h5", ".hdf5", ".mtz", ".bin", ".so")
AUTH_NAMES = {"users": "user accounts", "password": "shared password", "token": "access token",
              None: "none (localhost only)"}


class ToolError(AgentError):
    """A wrong or impossible request: answered to the agent as an error it can correct."""


# ================================================================== framework
@dataclass
class Reply:
    text: str
    images: list[tuple[str, bytes]] = field(default_factory=list)  # (MIME type, data)
    error: bool = False


@dataclass
class Tool:
    name: str
    title: str
    description: str
    properties: dict[str, dict[str, Any]]
    required: tuple[str, ...]
    run: Callable[[Session, Args], Reply | str]
    read_only: bool = True
    destructive: bool = False
    idempotent: bool = True
    local_only: bool = False  # reads files of the agent's computer: only where the tool runs there (cryoplug mcp)

    def definition(self) -> dict[str, Any]:
        """The MCP description of the tool (tools/list)."""
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": {"type": "object", "properties": self.properties, "required": list(self.required)},
            "annotations": {"title": self.title, "readOnlyHint": self.read_only, "destructiveHint": self.destructive,
                            "idempotentHint": self.idempotent, "openWorldHint": False},
        }


TOOLS: dict[str, Tool] = {}


def tool(name: str, title: str, description: str, properties: dict[str, dict[str, Any]] | None = None,
         required: tuple[str, ...] = (), *, read_only: bool = True, destructive: bool = False,
         idempotent: bool | None = None, local_only: bool = False) -> Callable:
    def register(fn: Callable) -> Callable:
        TOOLS[name] = Tool(name, title, " ".join(description.split()), properties or {}, required, fn, read_only,
                           destructive, read_only if idempotent is None else idempotent, local_only)
        return fn
    return register


def _string(description: str, **extra: Any) -> dict[str, Any]:
    return {"type": "string", "description": description, **extra}


def _integer(description: str, **extra: Any) -> dict[str, Any]:
    return {"type": "integer", "description": description, **extra}


def _boolean(description: str, **extra: Any) -> dict[str, Any]:
    return {"type": "boolean", "description": description, **extra}


def _object(description: str) -> dict[str, Any]:
    return {"type": "object", "description": description, "additionalProperties": True}


def _strings(description: str) -> dict[str, Any]:
    return {"type": "array", "items": {"type": "string"}, "description": description}


PROJECT = _string("Project id (P1) or title.")
JOB = _string("Job id in the project (J3).")


class Args:
    """The arguments of a call, read leniently, with the hooks of the call (progress, cancellation)."""

    def __init__(self, raw: dict[str, Any] | None = None, progress: Callable[[float, float | None, str], None] | None = None,
                 cancelled: Callable[[], bool] | None = None):
        self.raw = dict(raw or {})
        self.used: set[str] = set()  # the arguments read: the others are reported as ignored
        self._progress = progress
        self._cancelled = cancelled

    def _get(self, name: str) -> Any:
        self.used.add(name)
        return self.raw.get(name)

    def has(self, name: str) -> bool:
        return self._get(name) not in (None, "")

    def given(self, name: str) -> bool:
        """Present, even empty (an empty list asks for none)."""
        self.used.add(name)
        return name in self.raw

    def str(self, name: str, default: str = "") -> str:
        value = self._get(name)
        if value is None:
            return default
        if isinstance(value, (dict, list)):
            return json.dumps(value)
        return str(value).strip()

    def int(self, name: str, default: int, lo: int | None = None, hi: int | None = None) -> int:
        value = self._get(name)
        if value in (None, ""):
            return default
        try:
            number = int(float(value))
        except (TypeError, ValueError):
            raise ToolError(f"'{name}' must be a whole number, not {value!r}") from None
        if lo is not None:
            number = max(lo, number)
        if hi is not None:
            number = min(hi, number)
        return number

    def bool(self, name: str, default: bool = False) -> bool:
        value = self._get(name)
        if value in (None, ""):
            return default
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "y", "on")
        return bool(value)

    def dict(self, name: str) -> dict[str, Any]:
        value = self._get(name)
        if value in (None, "", {}):
            return {}
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                raise ToolError(f"'{name}' must be an object, e.g. {{\"resolution\": 3.2}}") from None
        if not isinstance(value, dict):
            raise ToolError(f"'{name}' must be an object of name: value pairs")
        return value

    def list(self, name: str, sep: str = r"[,\s]+") -> list[str]:
        value = self._get(name)
        if value in (None, "", []):
            return []
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                try:
                    value = json.loads(text)
                except ValueError:
                    value = text.strip("[]").split(",")
            else:
                value = re.split(sep, text)
        if not isinstance(value, (list, tuple)):
            value = [value]
        return [str(v).strip().strip("'\"") for v in value if str(v).strip().strip("'\"")]

    def progress(self, done: float, total: float | None = None, message: str = "") -> None:
        if self._progress:
            self._progress(done, total, message)

    def cancelled(self) -> bool:
        return bool(self._cancelled and self._cancelled())


class Session:
    """What the tools of one agent connection share: the client of the server and cached answers.
    ``local_files``: the tools run on the agent's computer (``cryoplug mcp``, ``cryoplug ai call``), so that its files
    can be uploaded; never inside the server (``/mcp``), where they would be the server's files."""

    def __init__(self, client: Any, allow_commands: bool = False, local_files: bool = False):
        self.client = client
        self.allow_commands = allow_commands
        self.local_files = local_files
        self._jobtypes: dict[str, dict[str, Any]] | None = None
        self._programs: dict[str, dict[str, Any]] | None = None

    # ------------------------------------------------------------ requests
    def get(self, path: str, /, **params: Any) -> Any:
        return self.client.request("GET", path, params=params)

    def post(self, path: str, body: Any = None) -> Any:
        return self.client.request("POST", path, body={} if body is None else body)

    def patch(self, path: str, body: Any) -> Any:
        return self.client.request("PATCH", path, body=body)

    def raw(self, path: str, /, **params: Any) -> tuple[bytes, str]:
        return self.client.request("GET", path, params=params, raw=True)

    # ------------------------------------------------------------ cached
    def jobtypes(self) -> dict[str, dict[str, Any]]:
        if self._jobtypes is None:
            self._jobtypes = {t["name"]: t for t in self.get("/api/jobtypes")}
        return self._jobtypes

    def programs(self) -> dict[str, dict[str, Any]]:
        if self._programs is None:
            self._programs = {t["key"]: t for t in self.get("/api/tools")}
        return self._programs

    # ------------------------------------------------------------ lookups
    def jobtype(self, name: Any) -> dict[str, Any]:
        text = str(name or "").strip()
        if not text:
            raise ToolError("Give the job type (list_job_types shows them)")
        types = self.jobtypes()
        key = text.lower().replace("-", "_").replace(" ", "_")
        if key in types:
            return types[key]
        for t in types.values():
            if t["title"].lower() == text.lower():
                return t
        titles = {t["title"].lower(): n for n, t in types.items()}
        guesses = difflib.get_close_matches(key, list(types), n=4, cutoff=0.5)
        guesses += [titles[g] for g in difflib.get_close_matches(text.lower(), list(titles), n=3, cutoff=0.5)]
        guesses += [n for n in types if key in n]
        guesses = list(dict.fromkeys(guesses))[:6]
        raise ToolError(f"Unknown job type '{text}'." + (f" Did you mean: {', '.join(guesses)}?" if guesses else "")
                        + " (list_job_types shows them all)")

    def project(self, ref: Any) -> dict[str, Any]:
        text = str(ref or "").strip()
        if not text:
            raise ToolError("Give the project: its id (P1) or its title (list_projects shows them)")
        match = re.fullmatch(r"[Pp]?\s*(\d+)", text)
        if match:
            uid = f"P{match.group(1)}"
            try:
                return self.get(f"/api/projects/{uid}")
            except ApiError as exc:
                if exc.status == 404:
                    raise ToolError(f"Project {uid} not found (list_projects shows your projects)") from None
                raise
        projects = self.get("/api/projects", archived=True)
        exact = [p for p in projects if p["title"].lower() == text.lower()]
        partial = exact or [p for p in projects if text.lower() in p["title"].lower()]
        if len(partial) == 1:
            return partial[0]
        if partial:
            raise ToolError(f"Several projects match '{text}': " + ", ".join(f"{p['uid']} ({p['title']})" for p in partial))
        raise ToolError(f"No project '{text}' (list_projects shows your projects)")

    def jobs(self, puid: str) -> list[dict[str, Any]]:
        return self.get(f"/api/projects/{puid}/jobs")

    def job(self, puid: str, ref: Any) -> dict[str, Any]:
        uid = job_uid(ref)
        try:
            return self.get(f"/api/projects/{puid}/jobs/{uid}")
        except ApiError as exc:
            if exc.status == 404:
                raise ToolError(f"{puid} has no job {uid} (get_project lists its jobs)") from None
            raise

    def program_label(self, key: str | None) -> str:
        if not key:
            return "built-in"
        info = self.programs().get(key)
        return info["label"] if info else key


def job_uid(ref: Any) -> str:
    text = str(ref or "").strip()
    if "/" in text:  # "P1/J3"
        text = text.rsplit("/", 1)[1]
    match = re.fullmatch(r"[Jj]?\s*(\d+)", text)
    if not match:
        raise ToolError(f"'{ref}' is not a job id: write it J3 (or 3)")
    return f"J{match.group(1)}"


def project_and_job(s: Session, a: Args, name: str = "job") -> tuple[dict[str, Any], dict[str, Any]]:
    """The project (from 'project', or from a job written P1/J3) and the job's details."""
    ref = a.str(name)
    if not ref:
        raise ToolError(f"Give the {name} (J3)")
    project = s.project(a.str("project") or (ref.split("/")[0] if "/" in ref else ""))
    return project, s.job(project["uid"], ref)


# ================================================================== text helpers
def when(ts: Any) -> str:
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M") if ts else "—"
    except (TypeError, ValueError, OSError):
        return "—"


def duration(seconds: float) -> str:
    sec = round(max(0.0, seconds))
    if sec < 90:
        return f"{sec} s"
    minutes = sec // 60
    if minutes < 90:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d}" if hours < 48 else f"{hours // 24} d {hours % 24} h"


def size(n: Any) -> str:
    value = float(n or 0)
    for unit in ("B", "kB", "MB", "GB", "TB"):
        if value < 1000 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1000
    return f"{value:.1f} TB"


def short(text: Any, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def value_text(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.4g}"
    if isinstance(value, (list, tuple)):
        return ", ".join(value_text(v) for v in value[:12]) + (" …" if len(value) > 12 else "")
    if isinstance(value, dict):
        return short(json.dumps(value), 120)
    return short(value, 200)


def first_sentence(text: str) -> str:
    match = re.match(r"(.+?[.!?])(\s|$)", text or "")
    return (match.group(1) if match else text or "").strip()


def highlights_text(highlights: Any, limit: int = 6) -> str:
    items = []
    for h in (highlights or [])[:limit]:
        status = f" ({h['status']})" if h.get("status") in ("warn", "bad") else ""
        items.append(f"{h.get('label')}: {value_text(h.get('value'))}{status}")
    return "; ".join(items)


def _box(value: Any) -> str:
    if isinstance(value, (list, tuple)) and value:
        return f"{value[0]}³" if len(set(value)) == 1 else "×".join(str(v) for v in value)
    return str(value)


def meta_text(meta: Any) -> str:
    meta = meta or {}
    parts = []
    if meta.get("resolution"):
        parts.append(f"resolution {value_text(meta['resolution'])} Å")
    pixel = meta.get("pixel_size") or meta.get("voxel_size") or meta.get("apix")
    if isinstance(pixel, (list, tuple)) and pixel:
        pixel = pixel[0] if len({round(float(v), 4) for v in pixel}) == 1 else pixel
    if pixel:
        parts.append(f"pixel {value_text(pixel)} Å")
    if meta.get("box"):
        parts.append(f"box {_box(meta['box'])}")
    for key, label in (("symmetry", "symmetry"), ("n_particles", "particles"), ("zdim", "zdim"), ("epoch", "epoch"),
                       ("contour", "contour"), ("method", "method")):
        if meta.get(key) not in (None, "", [], {}):
            parts.append(f"{label} {value_text(meta[key])}")
    if meta.get("frame_labels"):
        parts.append(f"{len(meta['frame_labels'])} volumes")
    return ", ".join(parts)


def output_line(job_uid_: str, out: dict[str, Any]) -> str:
    text = f"{job_uid_}.{out['name']} ({out['type']})"
    if out.get("label") and out["label"].replace(" ", "_") != out["name"]:
        text += f" “{out['label']}”"
    if out.get("path"):
        text += f" {out['path']}"
        extra = len(out.get("files") or []) - 1
        if extra > 0:
            text += f" (+{extra} file{'s' if extra > 1 else ''})"
    meta = meta_text(out.get("meta"))
    return text + (f" — {meta}" if meta else "")


def job_line(job: dict[str, Any], types: dict[str, dict[str, Any]]) -> str:
    jt = types.get(job["type"], {})
    text = f"{job['uid']:<4} {job['status']:<9} {job['type']}"
    if job.get("title") and job["title"] != jt.get("title"):
        text += f" “{short(job['title'], 60)}”"
    if job.get("inputs"):
        text += " (" + ", ".join(f"{slot}←{ref['job']}.{ref['output']}" for slot, ref in job["inputs"].items()) + ")"
    if job.get("tags"):
        text += " [" + ", ".join(job["tags"]) + "]"
    if job.get("workspaces"):
        text += " {" + ",".join(job["workspaces"]) + "}"
    status = job["status"]
    if status == "completed" and job.get("outputs"):
        text += " → " + ", ".join(o["name"] for o in job["outputs"])
    highlights = highlights_text(job.get("highlights"), 4)
    if highlights:
        text += f" | {highlights}"
    if status in ("queued", "launched", "running", "waiting") and job.get("message"):
        text += f" — {short(job['message'], 120)}"
        if status == "running" and job.get("progress"):
            text += f" ({float(job['progress']):.0%})"
    elif status in ("failed", "killed") and (job.get("error") or job.get("message")):
        text += f" — {short(job.get('error') or job.get('message'), 200)}"
    return text


def timing(job: dict[str, Any]) -> str:
    parts = [f"created {when(job.get('created_at'))}" + (f" by {job['created_by']}" if job.get("created_by") else "")]
    if job.get("started_at"):
        end = job.get("ended_at") or time.time()
        parts.append(f"started {when(job['started_at'])}")
        parts.append(f"{'ran' if job.get('ended_at') else 'running for'} {duration(end - float(job['started_at']))}")
    elif job.get("queued_at"):
        parts.append(f"queued {when(job['queued_at'])}")
    if job.get("lane"):
        parts.append(f"lane {job['lane']}")
    if job.get("gpus"):
        parts.append(f"GPU {', '.join(map(str, job['gpus']))}")
    return ", ".join(parts)


def _crossing(xs: list[float], ys: list[float], level: float) -> float | None:
    """First x where y falls below ``level`` (linear interpolation)."""
    for i in range(1, min(len(xs), len(ys))):
        y0, y1 = ys[i - 1], ys[i]
        if y0 is None or y1 is None:
            continue
        if y0 >= level > y1:
            t = (y0 - level) / (y0 - y1) if y0 != y1 else 0.0
            return xs[i - 1] + t * (xs[i] - xs[i - 1])
    return None


def plot_lines(sec: dict[str, Any]) -> list[str]:
    resolution_axis = sec.get("x_kind") == "resolution"  # x in 1/Å, shown in Å
    head = f"[{sec.get('title', 'Plot')}] plot"
    if sec.get("y_label") or sec.get("x_label"):
        head += f" of {sec.get('y_label') or 'y'} vs {'resolution (Å)' if resolution_axis else sec.get('x_label') or 'x'}"
    if sec.get("note"):
        head += f" — {short(sec['note'], 200)}"
    lines = [head]
    for series in sec.get("series") or []:
        xs = [float(x) for x in series.get("x") or [] if x is not None]
        ys = list(series.get("y") or [])[: len(xs)]
        if not xs or not ys:
            continue
        n = len(ys)
        picks = sorted({round(i * (n - 1) / 6) for i in range(7)})
        fmt_x = (lambda x: f"{1 / x:.2f} Å" if x > 0 else "∞") if resolution_axis else value_text
        points = ", ".join(f"{fmt_x(xs[i])}: {value_text(ys[i])}" for i in picks if ys[i] is not None)
        text = f"  {series.get('name') or 'series'} ({n} points): {points}"
        for hline in sec.get("hlines") or []:
            level = hline.get("y")
            if level is None:
                continue
            x = _crossing(xs, [y if y is None else float(y) for y in ys], float(level))
            if x:
                text += f"; crosses {value_text(level)} at {fmt_x(x)}"
        lines.append(text)
    return lines


def report_lines(report: dict[str, Any] | None, full: bool = False) -> list[str]:
    rows_shown = 1000 if full else 12
    text_shown = 20000 if full else 1500
    lines: list[str] = []
    images = 0
    for sec in (report or {}).get("sections") or []:
        kind, title = sec.get("kind"), sec.get("title", "")
        if kind == "metrics":
            items = []
            for m in sec.get("metrics") or []:
                item = f"{m.get('label')}: {value_text(m.get('value'))}"
                if m.get("status"):
                    item += f" ({m['status']})"
                if m.get("target") not in (None, ""):
                    item += f" [target {value_text(m['target'])}]"
                items.append(item)
            lines.append(f"[{title}] " + "; ".join(items))
        elif kind == "table":
            rows = sec.get("rows") or []
            lines.append(f"[{title}] table, {len(rows)} row{'s' if len(rows) != 1 else ''}"
                         + (f" — {short(sec['note'], 300)}" if sec.get("note") else ""))
            lines.append("  " + " | ".join(str(c) for c in sec.get("columns") or []))
            lines += ["  " + " | ".join(value_text(v) for v in row) for row in rows[:rows_shown]]
            if len(rows) > rows_shown:
                lines.append(f"  … {len(rows) - rows_shown} more rows (get_job with full_report=true)")
        elif kind == "text":
            text = str(sec.get("text") or "")
            body = text if len(text) <= text_shown else text[:text_shown] + "\n… (get_job with full_report=true)"
            lines.append(f"[{title}]")
            lines += ["  " + line for line in body.splitlines()]
        elif kind == "plot":
            lines += plot_lines(sec)
        elif kind == "heatmap":
            values = [v for row in sec.get("values") or [] for v in row if v is not None]
            span = f", values {value_text(min(values))} to {value_text(max(values))} {sec.get('unit', '')}" if values else ""
            lines.append(f"[{title}] heatmap {len(sec.get('y_labels') or [])}×{len(sec.get('x_labels') or [])}{span}"
                         + (f" — {short(sec['note'], 200)}" if sec.get("note") else ""))
        elif kind == "image":
            images += 1
            lines.append(f"[{title}] image {sec.get('path', '')} (get_job_images shows it)")
        elif kind == "latent":
            lines.append(f"[{title}] interactive latent-space explorer (in the web interface)")
        elif kind == "classes2d":
            lines += class_lines(sec, rows_shown)
        elif kind == "exposures":
            line = f"[{title}] micrograph browser (CTF fit, drift, distributions) in the web interface"
            if sec.get("waiting"):
                line += ("; WAITING for a person to curate the micrographs in the job panel (as an agent, create a "
                         "relion_curate job with mode=thresholds instead)")
            elif isinstance(sec.get("decided"), str):
                line += f"; kept {sec['decided'].count('1'):,} of {len(sec['decided']):,}"
                if sec.get("overrides"):
                    line += f" ({len(sec['overrides'])} decided by hand)"
            lines.append(line)
        elif kind == "picks":
            filaments = sec.get("mode") == "filaments"
            line = f"[{title}] {'filaments' if filaments else 'picks'} drawn on the micrographs in the web interface"
            if sec.get("waiting") and filaments:
                line += ("; WAITING for a person to click the start and end of each filament in the job panel (an agent "
                         "cannot pick filaments: ask the user)")
            elif sec.get("waiting"):
                line += ("; WAITING for a person to inspect the picks (threshold, power range, particles removed or added) "
                         "in the job panel (as an agent, set min_fom in relion_extract instead)")
            lines.append(line)
        elif kind == "particle_stats":
            line = f"[{title}] distributions of the particle statistics in the web interface"
            if sec.get("waiting"):
                line += ("; WAITING for a person to set the limits in the job panel (as an agent, create a "
                         "relion_particle_curate job with mode=thresholds and limits, e.g. 'max_prob 0.1 -')")
            elif sec.get("limits") is not None:
                limits = "; ".join(f"{k} {'-' if lo is None else f'{lo:g}'} {'-' if hi is None else f'{hi:g}'}"
                                   for k, (lo, hi) in (sec.get("limits") or {}).items()) or "none"
                line += f"; limits applied: {limits}; kept {int(sec.get('kept') or 0):,} particles"
            lines.append(line)
    return lines


def class_lines(sec: dict[str, Any], rows_shown: int) -> list[str]:
    """2D class averages: the largest classes with their particles, share and resolution, and the selection made."""
    classes = sorted(sec.get("classes") or [], key=lambda c: -(c.get("particles") or 0))
    total = sum(c.get("particles") or 0 for c in classes) or 1
    head = f"[{sec.get('title', '')}] {len(classes)} {'3D classes' if sec.get('three_d') else 'class averages'}, {total:,} particles"
    if sec.get("selected"):
        head += f"; kept: classes {', '.join(map(str, sec['selected']))}" + (f" ({sec['how']})" if sec.get("how") else "")
    elif sec.get("selectable") and sec.get("waiting"):
        head += ("; WAITING for a person to click the good classes in the job panel (or create a "
                 f"{'relion_select3d' if sec.get('three_d') else 'relion_select2d'} job with mode=list)")
    lines = [head, "  class | particles | share | resolution (Å)" + (" | score" if any("score" in c for c in classes) else "")]
    for c in classes[: max(rows_shown, 24)]:
        res = c.get("resolution")
        row = (f"  {c.get('class')} | {c.get('particles', 0):,} | {100 * (c.get('particles') or 0) / total:.1f} % | "
               f"{value_text(res) if res and res < 900 else '-'}")
        if "score" in c:
            row += f" | {value_text(c['score'])}"
        lines.append(row)
    if len(classes) > max(rows_shown, 24):
        lines.append(f"  … {len(classes) - max(rows_shown, 24)} smaller classes (get_job with full_report=true)")
    return lines


def _program_note(s: Session, jt: dict[str, Any]) -> str:
    status = jt.get("tool_status")
    if status == "builtin":
        return "built-in"
    label = s.program_label(jt.get("tool"))
    return {"found": label, "missing": f"{label} — NOT INSTALLED", "disabled": f"{label} — disabled"}.get(
        status, f"{label} — not checked")


def _gpu_note(jt: dict[str, Any]) -> str:
    if jt.get("interactive"):
        return "interactive: a person works in it"
    if jt.get("gpu_param"):
        param = next((p for p in jt["params"] if p["name"] == jt["gpu_param"]), {})
        optional = param.get("type") == "bool" or param.get("min") == 0
        return f"GPU {'optional' if optional else 'yes'} (parameter {jt['gpu_param']})"
    return f"{jt['gpu']} GPU" if jt.get("gpu") else ""


# ================================================================== orientation
@tool("server_status", "CryoPlug status", """
Start here. The CryoPlug server you are connected to: version, the account you act as, compute lanes and GPUs,
the job queue, which external programs are installed (jobs needing a missing program cannot run), your projects,
and the skills to read for guidance (read_skill).""")
def server_status(s: Session, a: Args) -> str:
    info = s.get("/api/info")
    queue = s.get("/api/queue")
    projects = s.get("/api/projects")
    user = info.get("user") or {}
    who = ("the owner of the server (no user accounts)" if user.get("builtin")
           else f"{user.get('username')} ({'administrator' if user.get('role') == 'admin' else 'user'})")
    lines = [f"CryoPlug {info.get('version')} on {info.get('hostname')} — {s.client.url}",
             f"Acting as {who}; protection: {AUTH_NAMES.get(info.get('auth'), info.get('auth'))}.",
             f"New projects go to {info.get('projects_root')}."]
    if info.get("browse_roots"):
        lines.append(f"Data can be read from: {', '.join(info['browse_roots'])}")
    lines.append("Lanes (where jobs run):")
    for lane in queue.get("lanes") or []:
        gpus = f", GPUs {','.join(map(str, lane['gpus']))}" if lane.get("gpus") else ""
        busy = f", GPUs in use {','.join(map(str, lane['gpus_used']))}" if lane.get("gpus_used") else ""
        desc = f" — {lane['description']}" if lane.get("description") else ""
        lines.append(f"  {lane['name']} ({lane['type']}{gpus}): {lane['running']}/{lane['max_jobs']} running, "
                     f"{lane['queued']} queued{busy}{desc}")
    active = queue.get("jobs") or []
    lines.append(f"Active jobs: {len(active) or 'none'}")
    for j in active[:15]:
        where = "another user's job" if j.get("hidden") else f"{j['project_uid']}/{j['uid']}"
        lines.append(f"  {where} {j['status']} {j['type']}" + (f" — {short(j.get('message'), 100)}" if j.get("message") else ""))
    if len(active) > 15:
        lines.append(f"  … {len(active) - 15} more")
    found, missing, off = [], [], []
    for key, prog in s.programs().items():
        if key == "shell":
            continue
        status = (prog.get("status") or {}).get("status")
        version = (prog.get("status") or {}).get("version")
        label = prog["label"] + (f" {version}" if version and status == "found" else "")
        (found if status == "found" else off if status == "disabled" else missing).append(label)
    lines.append(f"Programs installed: {', '.join(found) or 'none'}.")
    if missing:
        lines.append(f"Not installed (their jobs cannot run): {', '.join(missing)}.")
    if off:
        lines.append(f"Disabled in the configuration: {', '.join(off)}.")
    lines.append("Built-in jobs (imports, masks, map operations, FSC, Q-score validation, fit in map, deposition) need no program.")
    shown = ", ".join(f"{p['uid']} “{p['title']}”" for p in projects[:12])
    lines.append(f"Projects ({len(projects)}): {shown or 'none yet'}" + (" …" if len(projects) > 12 else ""))
    from cryoplug.agent.skills import all_skills
    lines.append("Skills (read_skill NAME): " + ", ".join(sk.name for sk in all_skills()) + ". Start with “cryoplug”.")
    return "\n".join(lines)


@tool("list_projects", "List projects", """
Your projects (id, title, folder, owner, last update). Archived projects only with include_archived.""",
      {"include_archived": _boolean("Also list archived projects (default false).")})
def list_projects(s: Session, a: Args) -> str:
    projects = s.get("/api/projects", archived=a.bool("include_archived"))
    if not projects:
        return "No project yet: create_project makes one."
    lines = [f"{len(projects)} project{'s' if len(projects) > 1 else ''}:"]
    for p in projects:
        flags = " [archived]" if p.get("archived") else ""
        owner = f", owner {p['owner']}" if p.get("owner") else ""
        members = f", shared with {', '.join(p['members'])}" if p.get("members") else ""
        lines.append(f"{p['uid']:<4} “{p['title']}”{flags} — jobs up to J{p.get('job_counter', 0)}, updated "
                     f"{when(p.get('updated_at'))}{owner}{members}")
        lines.append(f"     {p['dir']}" + (f" — {short(p['description'], 150)}" if p.get("description") else ""))
    return "\n".join(lines)


@tool("create_project", "Create a project", """
Create a project (one per dataset or structure): a folder CP-<title> in your projects folder, or in parent_dir.
Then import data into it with create_job (import_cryosparc, import_maps, import_model, import_sequence...).""",
      {"title": _string("Project title."), "description": _string("Optional description."),
       "parent_dir": _string("Optional folder on the server where the project folder is created.")},
      ("title",), read_only=False, idempotent=False)
def create_project(s: Session, a: Args) -> str:
    title = a.str("title")
    if not title:
        raise ToolError("Give the project a title")
    p = s.post("/api/projects", {"title": title, "description": a.str("description"), "parent": a.str("parent_dir") or None})
    return (f"Created {p['uid']} “{p['title']}” in {p['dir']}.\n"
            "Next: import the data, e.g. create_job with type import_cryosparc (a CryoSPARC refinement job folder), "
            "import_maps (half maps, map, mask), import_model or import_sequence; browse_server_files finds the files.")


@tool("get_project", "Project and its jobs", """
A project and its jobs, one line each: id, status, type, inputs (slot←job.output), outputs, key results
(highlights), and the message or error. Filter with status (e.g. "failed" or "running,queued") and type
(part of the job type name).""",
      {"project": PROJECT, "status": _string("Only jobs with these statuses (comma-separated)."),
       "type": _string("Only job types containing this text."),
       "workspace": _string("Only the jobs of this workspace (W2)."), "tag": _string("Only the jobs with this tag."),
       "limit": _integer("At most this many jobs, the most recent (default 60).")},
      ("project",))
def get_project(s: Session, a: Args) -> str:
    p = s.project(a.str("project"))
    jobs = sorted(s.jobs(p["uid"]), key=lambda j: j.get("num") or 0)
    workspaces = s.get(f"/api/projects/{p['uid']}/workspaces")
    ws = a.str("workspace").upper()
    if ws:
        if not any(w["uid"] == ws for w in workspaces):
            raise ToolError(f"No workspace {ws} in {p['uid']}" + (f" (there are {', '.join(w['uid'] for w in workspaces)})" if workspaces else ""))
        jobs = [j for j in jobs if ws in (j.get("workspaces") or [])]
    tag = a.str("tag").lower()
    if tag:
        jobs = [j for j in jobs if tag in {x.lower() for x in j.get("tags") or []}]
    types = s.jobtypes()
    counts = Counter(j["status"] for j in jobs)
    lines = [f"{p['uid']} “{p['title']}” — {p['dir']}"]
    who = [f"owner {p['owner']}"] if p.get("owner") else []
    if p.get("members"):
        who.append(f"shared with {', '.join(p['members'])}")
    if who:
        lines.append(", ".join(who))
    if p.get("description"):
        lines.append(short(p["description"], 400))
    if workspaces:
        lines.append("Workspaces: " + "; ".join(f"{w['uid']} “{short(w['title'], 50)}” ({w['num_jobs']} jobs)" for w in workspaces)
                     + (f" — showing {ws}" if ws else ""))
    lines.append(f"{len(jobs)} job{'s' if len(jobs) != 1 else ''}" + (": " + ", ".join(f"{n} {st}" for st, n in counts.most_common()) if jobs else ""))
    wanted = {x.lower() for x in a.list("status")}
    kind = a.str("type").lower()
    shown = [j for j in jobs if (not wanted or j["status"] in wanted) and (not kind or kind in j["type"])]
    limit = a.int("limit", 60, 1, 500)
    if len(shown) > limit:
        lines.append(f"(showing the last {limit} of {len(shown)}: use limit, status or type to see others)")
        shown = shown[-limit:]
    lines += [job_line(j, types) for j in shown]
    if not jobs:
        lines.append("Empty project: import data first (list_job_types category Import).")
    else:
        lines.append("get_job gives a job's results and report; find_outputs lists what can feed a new job.")
    return "\n".join(lines)


# ================================================================== job types
@tool("list_job_types", "List job types", """
What CryoPlug can run, grouped by category (Import, Reconstruction, Map processing, Heterogeneity, Model building,
Interactive, Refinement, Validation, Deposition, Utilities), with the program each needs and whether it is
installed. Narrow with category and/or search (matches name, title and description).""",
      {"category": _string("Only this category (or part of its name)."),
       "search": _string("Only job types whose name, title or description contains this text.")})
def list_job_types(s: Session, a: Args) -> str:
    category, search = a.str("category").lower(), a.str("search").lower()
    lines, current, count = [], None, 0
    for jt in s.jobtypes().values():
        if category and category not in jt["category"].lower():
            continue
        if search and not any(search in str(jt.get(k, "")).lower() for k in ("name", "title", "description")):
            continue
        if jt["category"] != current:
            current = jt["category"]
            lines.append(f"{current}:")
        flags = [_program_note(s, jt)]
        gpu = _gpu_note(jt)
        if gpu:
            flags.append(gpu)
        if jt.get("admin_only"):
            flags.append("not available to agents" if not s.allow_commands else "administrators only")
        lines.append(f"  {jt['name']} — {jt['title']} [{'; '.join(flags)}]: {first_sentence(jt.get('description', ''))}")
        count += 1
    if not count:
        return "No job type matches. Categories: " + ", ".join(dict.fromkeys(t["category"] for t in s.jobtypes().values()))
    lines.insert(0, f"{count} job type{'s' if count > 1 else ''} (describe_job_type NAME for inputs and parameters):")
    return "\n".join(lines)


@tool("describe_job_type", "Describe a job type", """
Everything needed to create a job of this type: what it does, the program and GPU it needs, its inputs (slots and
accepted data types), its outputs, every parameter (type, default, choices, limits, help) and the CryoPlug guidance
(when to use it, pitfalls, tips, usual next jobs; partly in French).""",
      {"name": _string("Job type name (e.g. locscale) or title.")}, ("name",))
def describe_job_type(s: Session, a: Args) -> str:
    jt = s.jobtype(a.str("name") or a.str("type"))
    lines = [f"{jt['name']} — {jt['title']} [{jt['category']}]", jt.get("description", "")]
    needs = [f"program: {_program_note(s, jt)}"]
    gpu = _gpu_note(jt)
    if gpu:
        needs.append(gpu)
    if jt.get("cpu_param"):
        needs.append(f"CPUs from parameter {jt['cpu_param']}")
    if jt.get("admin_only"):
        needs.append("runs any command: administrators only" + ("" if s.allow_commands else ", refused to agents"))
    lines.append("Needs: " + "; ".join(needs))
    lines.append("Inputs (connect with inputs={slot: \"J3.output\"} or {slot: \"J3\"}):")
    for slot in jt.get("inputs") or []:
        line = f"  {slot['name']} ({'/'.join(slot['types'])}{'' if slot['required'] else ', optional'})"
        if slot.get("label") and slot["label"].lower() != slot["name"].replace("_", " ").lower():
            line += f" “{slot['label']}”"
        if slot.get("help"):
            line += f": {short(slot['help'], 300)}"
        if slot.get("prefer"):
            line += f" [preferred outputs: {', '.join(slot['prefer'])}]"
        lines.append(line)
    if not jt.get("inputs"):
        lines.append("  none (files are given as parameters)")
    lines.append("Outputs: " + (", ".join(f"{o['name']} ({o['type']})" for o in jt.get("outputs") or []) or "none"))
    for advanced in (False, True):
        params = [p for p in jt.get("params") or [] if bool(p.get("advanced")) == advanced]
        if not params:
            continue
        lines.append("Advanced parameters:" if advanced else "Parameters (leave out to keep the default):")
        for p in params:
            spec = [p["type"]]
            if p.get("choices"):
                spec.append(("a list of any of " if p["type"] == "multichoice" else "one of ")
                            + " | ".join(map(str, p["choices"])))
            if p.get("min") is not None or p.get("max") is not None:
                spec.append(f"{'' if p.get('min') is None else value_text(p['min'])}..{'' if p.get('max') is None else value_text(p['max'])}")
            if p.get("unit"):
                spec.append(p["unit"])
            spec.append(f"default {value_text(p.get('default')) if p.get('default') not in ('', None) else 'empty'}")
            if p.get("required"):
                spec.append("REQUIRED")
            if p.get("admin_only"):
                spec.append("administrators only")
            line = f"  {p['name']} ({', '.join(spec)})"
            if p.get("label") and p["label"].lower() != p["name"].replace("_", " ").lower():
                line += f" “{p['label']}”"
            if p.get("help"):
                line += f": {short(p['help'], 400)}"
            lines.append(line)
    help_ = jt.get("help") or {}
    if help_:
        lines.append("Guidance (CryoPlug job reference):")
        if help_.get("purpose"):
            lines.append(f"  Purpose: {help_['purpose']}")
        for key, title in (("when", "When to use"), ("avoid", "Avoid / pitfalls"), ("tips", "Tips")):
            for item in help_.get(key) or []:
                lines.append(f"  {title}: {item}")
        if help_.get("inputs"):
            lines.append(f"  Recommended inputs: {help_['inputs']}")
        if help_.get("next"):
            lines.append(f"  Usually followed by: {', '.join(help_['next'])}")
    return "\n".join(lines)


# ================================================================== inputs
def _parse_ref(ref: Any) -> tuple[str, str | None]:
    if isinstance(ref, dict):
        job = ref.get("job") or ref.get("job_uid") or ref.get("uid")
        return job_uid(job), (str(ref.get("output") or ref.get("name") or "").strip() or None)
    text = str(ref).strip()
    if "." in text:
        job, output = text.split(".", 1)
        return job_uid(job), output.strip() or None
    if ":" in text:
        job, output = text.split(":", 1)
        return job_uid(job), output.strip() or None
    return job_uid(text), None


def _outputs_of(job: dict[str, Any], types: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    """(outputs, planned): the outputs written, or those the job type declares while the job has not finished."""
    if job["status"] == "completed" or job.get("outputs"):
        return list(job.get("outputs") or []), False
    return list(types.get(job["type"], {}).get("outputs") or []), True


def resolve_inputs(s: Session, puid: str, jt: dict[str, Any], raw: dict[str, Any],
                   jobs: list[dict[str, Any]] | None = None) -> tuple[dict[str, Any], list[str]]:
    """{slot: {job, output}} from the agent's references (None: disconnect), and notes on the choices made."""
    slots = {slot["name"]: slot for slot in jt.get("inputs") or []}
    jobs_by_uid = {j["uid"]: j for j in (jobs if jobs is not None else s.jobs(puid))}
    types = s.jobtypes()
    out: dict[str, Any] = {}
    notes: list[str] = []
    for name, ref in raw.items():
        if name not in slots:
            accepted = ", ".join(f"{n} ({'/'.join(sl['types'])})" for n, sl in slots.items()) or "none"
            guess = difflib.get_close_matches(name, list(slots), n=1)
            raise ToolError(f"{jt['name']} has no input '{name}'" + (f" (did you mean {guess[0]}?)" if guess else "")
                            + f". Its inputs: {accepted}")
        if ref in (None, "", [], {}):
            out[name] = None
            continue
        slot = slots[name]
        uid, output = _parse_ref(ref)
        parent = jobs_by_uid.get(uid)
        if parent is None:
            raise ToolError(f"Input {name}: {puid} has no job {uid}")
        outputs, _ = _outputs_of(parent, types)
        if output is None:
            fitting = [o for o in outputs if o["type"] in slot["types"]]
            if not fitting:
                raise ToolError(f"Input {name} takes {'/'.join(slot['types'])}, and {uid} ({parent['type']}) has no such "
                                f"output (its outputs: {', '.join(o['name'] + ' (' + o['type'] + ')' for o in outputs) or 'none'})")
            chosen = next((o for p in slot.get("prefer") or [] for o in fitting if o["name"] == p), None)
            chosen = chosen or next((o for o in fitting if o["name"] in slot["types"]), None) or fitting[0]
            output = chosen["name"]
            others = [o["name"] for o in fitting if o["name"] != output]
            if others:
                notes.append(f"{name}: chose {uid}.{output} (also possible: {', '.join(others)})")
        else:
            match = next((o for o in outputs if o["name"] == output), None)
            if match is None:
                raise ToolError(f"Input {name}: {uid} has no output '{output}' (its outputs: "
                                f"{', '.join(o['name'] + ' (' + o['type'] + ')' for o in outputs) or 'none'})")
            if match["type"] not in slot["types"]:
                raise ToolError(f"Input {name} takes {'/'.join(slot['types'])}, but {uid}.{output} is a {match['type']}")
        out[name] = {"job": uid, "output": output}
    return out, notes


def check_params(s: Session, jt: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    known = {p["name"]: p for p in jt.get("params") or []}
    unknown = [k for k in params if k not in known]
    if unknown:
        hints = []
        for k in unknown:
            guess = difflib.get_close_matches(k, list(known), n=1, cutoff=0.6)
            hints.append(f"{k}" + (f" (did you mean {guess[0]}?)" if guess else ""))
        raise ToolError(f"Unknown parameter(s) for {jt['name']}: {', '.join(hints)}. "
                        + (f"Valid: {', '.join(known)}" if known else f"{jt['name']} has no parameters"))
    if not s.allow_commands:
        if jt.get("admin_only"):
            raise ToolError(f"{jt['title']} runs arbitrary commands: not available to agents (ask a person to create it "
                            "in the web interface)")
        locked = [k for k, v in params.items() if known[k].get("admin_only") and v not in (None, "", known[k].get("default"))]
        if locked:
            raise ToolError(f"Parameter(s) {', '.join(locked)} can run code on the server: not available to agents")
    return params


def _params_set(jt: dict[str, Any], params: dict[str, Any]) -> str:
    defaults = {p["name"]: p.get("default") for p in jt.get("params") or []}
    changed = {k: v for k, v in params.items() if v != defaults.get(k)}
    return ", ".join(f"{k}={value_text(v)}" for k, v in changed.items())


def _job_summary(s: Session, puid: str, job: dict[str, Any], notes: list[str]) -> list[str]:
    jt = s.jobtypes().get(job["type"], {})
    lines = [f"{puid}/{job['uid']} “{job['title']}” ({job['type']}) — status {job['status']}"]
    if job.get("inputs"):
        lines.append("Inputs: " + ", ".join(f"{slot} ← {r['job']}.{r['output']}" for slot, r in job["inputs"].items()))
    params = _params_set(jt, job.get("params") or {})
    if params:
        lines.append(f"Parameters changed from the defaults: {params}")
    lines += [f"Note: {n}" for n in notes]
    return lines


def _queue(s: Session, puid: str, uid: str, lane: str = "") -> tuple[dict[str, Any] | None, str]:
    try:
        job = s.post(f"/api/projects/{puid}/jobs/{uid}/queue", {"lane": lane} if lane else {})
    except ApiError as exc:
        return None, str(exc)
    return job, ""


def _waiting_for(s: Session, puid: str, job: dict[str, Any]) -> list[str]:
    parents = {r["job"] for r in (job.get("inputs") or {}).values()}
    if not parents:
        return []
    states = {j["uid"]: j["status"] for j in s.jobs(puid) if j["uid"] in parents}
    return [f"{uid} ({st})" for uid, st in sorted(states.items()) if st != "completed"]


# ================================================================== jobs
@tool("find_outputs", "Find outputs to use as inputs", """
The outputs of a project's jobs that can feed a new job: completed outputs, and the outputs that jobs still queued
or running will write (they can be connected already; the new job waits). Filter by data type: half_maps, map,
mask, locres, variability, volume_series, particles, latent, model, sequence, fsc, report, restraints, files.""",
      {"project": PROJECT, "type": _string("Only outputs of this data type (e.g. map, half_maps, model)."),
       "job": _string("Only the outputs of this job.")},
      ("project",))
def find_outputs(s: Session, a: Args) -> str:
    p = s.project(a.str("project"))
    types = s.jobtypes()
    kind = a.str("type").lower()
    only = job_uid(a.str("job")) if a.has("job") else ""
    lines: list[str] = []
    for job in sorted(s.jobs(p["uid"]), key=lambda j: j.get("num") or 0):
        if (only and job["uid"] != only) or job["status"] in ("failed", "killed"):
            continue
        outputs, planned = _outputs_of(job, types)
        for out in outputs:
            if kind and out["type"] != kind:
                continue
            state = f"{job['status']}, planned" if planned else job["status"]
            lines.append(f"{output_line(job['uid'], out)} — from {job['uid']} {job['type']} ({state})")
    if not lines:
        return (f"No {kind + ' ' if kind else ''}output in {p['uid']}" + (f" for {only}" if only else "")
                + ". Import data first, or check the job ids with get_project.")
    lines.insert(0, f"Outputs in {p['uid']} (connect as inputs={{slot: \"J3.name\"}}):")
    return "\n".join(lines)


@tool("create_job", "Create (and queue) a job", """
Create a job in a project. inputs maps input slots to outputs of other jobs: {"half_maps": "J1.half_maps",
"mask": "J1"} (a job id alone picks its matching output). params sets parameters by name (others keep their
defaults; "resolution": 0 means taken from the inputs). queue=true also queues it (it waits for unfinished inputs).
Check the job type first with describe_job_type. Answers the job id and what still prevents queueing.""",
      {"project": PROJECT, "type": _string("Job type name (list_job_types)."),
       "inputs": _object("Input slot → \"J3.output\" or \"J3\"."), "params": _object("Parameter name → value."),
       "title": _string("Optional title (default: the job type title)."),
       "queue": _boolean("Queue it right away (default false)."),
       "lane": _string("Optional lane (default: the default lane)."),
       "notes": _string("Optional notes kept with the job (e.g. why it was run)."),
       "workspace": _string("Optional workspace (W2) to put the job in."),
       "tags": _strings("Optional tags (e.g. \"best\", \"dataset 2\")."),
       "resources": _object("Optional compute choices: gpu_ids (list), num_cpus, partition, time, mem (SLURM).")},
      ("project", "type"), read_only=False, idempotent=False)
def create_job(s: Session, a: Args) -> str:
    p = s.project(a.str("project"))
    jt = s.jobtype(a.str("type") or a.str("job_type"))
    params = check_params(s, jt, a.dict("params"))
    inputs, notes = resolve_inputs(s, p["uid"], jt, a.dict("inputs"))
    body = {"type": jt["name"], "params": params, "inputs": {k: v for k, v in inputs.items() if v},
            "title": a.str("title") or None, "lane": a.str("lane") or None, "notes": a.str("notes")}
    if a.dict("resources"):
        body["resources"] = a.dict("resources")
    if a.str("workspace"):
        body["workspaces"] = [a.str("workspace").upper()]
    if a.list("tags", sep=","):
        body["tags"] = a.list("tags", sep=",")
    job = s.post(f"/api/projects/{p['uid']}/jobs", body)
    lines = [f"Created {p['uid']}/{job['uid']}."]
    queued_error = ""
    if a.bool("queue"):
        queued, queued_error = _queue(s, p["uid"], job["uid"], a.str("lane"))
        job = queued or job
    detail = s.job(p["uid"], job["uid"])
    lines += _job_summary(s, p["uid"], detail, notes)
    lines += _queue_state(s, p["uid"], detail, queued_error)
    return "\n".join(lines)


def _queue_state(s: Session, puid: str, job: dict[str, Any], queue_error: str = "") -> list[str]:
    if queue_error:
        return [f"NOT QUEUED: {queue_error}", "Fix it with update_job, then queue_job."]
    if job["status"] == "building":
        if job.get("problems"):
            return ["Cannot be queued yet: " + "; ".join(job["problems"]), "Fix it with update_job, then queue_job."]
        waiting = _waiting_for(s, puid, job)
        return ["Ready: queue_job runs it" + (f" (it will wait for {', '.join(waiting)})" if waiting else "")
                + ", or update_job changes it first."]
    if job["status"] == "queued":
        waiting = _waiting_for(s, puid, job)
        return [f"Queued on lane {job.get('lane')}" + (f", waiting for {', '.join(waiting)}" if waiting else "")
                + ". wait_for_jobs follows it."]
    return [f"Status: {job['status']}" + (f" — {job['message']}" if job.get("message") else "")]


@tool("update_job", "Change a job before it runs", """
Change the parameters, inputs, title, notes, lane or compute choices of a job still in building state (clear_job
first for a failed job). Only the parameters and inputs given change; an input set to null is disconnected.
Title, notes, tags and workspaces can change whatever the state of the job.""",
      {"project": PROJECT, "job": JOB, "params": _object("Parameter name → new value."),
       "inputs": _object("Input slot → \"J3.output\", \"J3\" or null."), "title": _string("New title."),
       "notes": _string("New notes."), "lane": _string("New lane."),
       "tags": _strings("The tags of the job (replace the current ones; [] removes them)."),
       "workspaces": _strings("The workspaces the job is in (W1, W2; replace the current ones)."),
       "resources": _object("Compute choices: gpu_ids, num_cpus, partition, time, mem.")},
      ("project", "job"), read_only=False, idempotent=True)
def update_job(s: Session, a: Args) -> str:
    p, job = project_and_job(s, a)
    jt = s.jobtype(job["type"])
    body: dict[str, Any] = {}
    if a.has("params"):
        body["params"] = check_params(s, jt, a.dict("params"))
    notes: list[str] = []
    if a.has("inputs"):
        changes, notes = resolve_inputs(s, p["uid"], jt, a.dict("inputs"))
        merged = {**(job.get("inputs") or {}), **changes}
        body["inputs"] = {k: v for k, v in merged.items() if v}
    for key in ("title", "notes", "lane"):
        if a.has(key):
            body[key] = a.str(key)
    if a.has("resources"):
        body["resources"] = a.dict("resources")
    for key in ("tags", "workspaces"):
        if a.has(key):
            body[key] = [x.upper() for x in a.list(key)] if key == "workspaces" else a.list(key, sep=",")
    if not body:
        raise ToolError("Nothing to change: give params, inputs, title, notes, lane, tags, workspaces or resources")
    s.patch(f"/api/projects/{p['uid']}/jobs/{job['uid']}", body)
    detail = s.job(p["uid"], job["uid"])
    return "\n".join(["Updated."] + _job_summary(s, p["uid"], detail, notes) + _queue_state(s, p["uid"], detail))


@tool("queue_job", "Queue a job", """
Queue a job in building state: it starts when its inputs are complete and its lane has room (and a GPU, if it
needs one). Answers the problems that prevent queueing, if any.""",
      {"project": PROJECT, "job": JOB, "lane": _string("Optional lane (default: the job's or the default lane).")},
      ("project", "job"), read_only=False, idempotent=True)
def queue_job(s: Session, a: Args) -> str:
    p, job = project_and_job(s, a)
    if job["status"] in ACTIVE:
        return f"{job['uid']} is already {job['status']}."
    if job["status"] != "building":
        raise ToolError(f"{job['uid']} is {job['status']}: clear_job resets a failed or killed job; clone_job copies a "
                        "completed one to run it again")
    _, error = _queue(s, p["uid"], job["uid"], a.str("lane"))
    detail = s.job(p["uid"], job["uid"])
    return "\n".join([f"{p['uid']}/{job['uid']} {job['type']}"] + _queue_state(s, p["uid"], detail, error))


@tool("get_job", "Job details and results", """
One job in detail: status and timing, inputs, parameters changed from the defaults, outputs (paths and map/model
facts), key results and the report (metrics, tables, plots summarised with their numbers, text). For a failed job,
the error and the end of the log. full_report=true shows all table rows and texts; all_params=true every parameter.""",
      {"project": PROJECT, "job": JOB, "full_report": _boolean("All table rows and complete texts (default false)."),
       "all_params": _boolean("List every parameter, not only those changed (default false).")},
      ("project", "job"))
def get_job(s: Session, a: Args) -> str:
    p, job = project_and_job(s, a)
    jt = s.jobtypes().get(job["type"], {})
    lines = [f"{p['uid']}/{job['uid']} “{job['title']}” — {job['type']} ({jt.get('title', '')}) — status {job['status']}"
             + (f" {float(job['progress']):.0%}" if job["status"] == "running" and job.get("progress") else "")]
    lines.append(timing(job))
    if job.get("message") and job["status"] not in ("completed",):
        lines.append(f"Message: {short(job['message'], 300)}")
    if job.get("notes"):
        lines.append(f"Notes: {short(job['notes'], 500)}")
    if job.get("input_details"):
        lines.append("Inputs:")
        for slot, ref in job["input_details"].items():
            info = ref.get("output_info") or {}
            extra = meta_text(info.get("meta"))
            lines.append(f"  {slot} ← {ref['job']}.{ref['output']} (“{ref.get('title')}”, {ref.get('status')})"
                         + (f" — {extra}" if extra else ""))
    params = job.get("params") or {}
    if a.bool("all_params"):
        lines.append("Parameters: " + ", ".join(f"{k}={value_text(v)}" for k, v in params.items()))
    else:
        changed = _params_set(jt, params)
        lines.append(f"Parameters changed from the defaults: {changed or 'none'}")
    if job.get("outputs"):
        lines.append("Outputs:")
        lines += ["  " + output_line(job["uid"], o) for o in job["outputs"]]
    highlights = highlights_text(job.get("highlights"), 20)
    if highlights:
        lines.append(f"Key results: {highlights}")
    report = report_lines(job.get("report"), a.bool("full_report"))
    if report:
        lines.append("Report:")
        lines += ["  " + line for line in report]
    if job["status"] == "building" and job.get("problems"):
        lines.append("Cannot be queued yet: " + "; ".join(job["problems"]))
    if job["status"] == "waiting":
        lines.append("Waiting for a person in the web interface (an ISOLDE / Coot session, a class selection, a curation "
                     "of micrographs or an inspection of picks).")
    if job["status"] in ("failed", "killed"):
        if job.get("error"):
            lines.append(f"Error: {job['error']}")
        log = s.get(f"/api/projects/{p['uid']}/jobs/{job['uid']}/log").get("text", "")
        tail = log.rstrip().splitlines()[-25:]
        if tail:
            lines.append("End of the log:")
            lines += ["  " + short(line, 300) for line in tail]
    if job.get("dependents"):
        lines.append(f"Used by: {', '.join(job['dependents'])}")
    lines.append(f"Folder: {job.get('job_dir', '')}")
    return "\n".join(lines)


@tool("get_job_log", "Read a job's log", """
The log of a job (the program output, CryoPlug messages): the last lines, or the lines matching a search
(regular expression, case-insensitive) with some context.""",
      {"project": PROJECT, "job": JOB, "tail": _integer("Number of last lines (default 120, at most 2000)."),
       "search": _string("Only lines matching this regular expression (case-insensitive)."),
       "context": _integer("Lines of context around each match (default 2).")},
      ("project", "job"))
def get_job_log(s: Session, a: Args) -> str:
    p, job = project_and_job(s, a)
    log = s.get(f"/api/projects/{p['uid']}/jobs/{job['uid']}/log")
    lines = (log.get("text") or "").splitlines()
    head = f"Log of {p['uid']}/{job['uid']} ({job['status']}, {size(log.get('size', 0))}"
    head += ", only its end is available)" if log.get("truncated") else ")"
    if not lines:
        return head + ": empty (the job has not started)."
    if a.has("search"):
        try:
            pattern = re.compile(a.str("search"), re.IGNORECASE)
        except re.error as exc:
            raise ToolError(f"Invalid regular expression: {exc}") from None
        ctx = a.int("context", 2, 0, 20)
        keep: set[int] = set()
        hits = 0
        for i, line in enumerate(lines):
            if pattern.search(line):
                hits += 1
                keep.update(range(max(0, i - ctx), min(len(lines), i + ctx + 1)))
        if not hits:
            return head + f": no line matches '{a.str('search')}'."
        out, last = [], -2
        for i in sorted(keep)[:600]:
            if i != last + 1:
                out.append("--")
            out.append(short(lines[i], 400))
            last = i
        return head + f": {hits} matching line{'s' if hits > 1 else ''}\n" + "\n".join(out)
    tail = a.int("tail", 120, 1, 2000)
    return head + f", last {min(tail, len(lines))} of {len(lines)} lines:\n" + "\n".join(short(x, 400) for x in lines[-tail:])


@tool("wait_for_jobs", "Wait for jobs to finish", """
Wait until jobs finish (completed, failed or killed; or an interactive session waits for a person), at most
timeout_seconds, then report their state and key results. Without jobs: every queued or running job of the
project. Long jobs take hours: rather than waiting again and again, tell the person and check later.""",
      {"project": PROJECT, "jobs": _strings("Job ids (J3, J4...). Default: the project's active jobs."),
       "timeout_seconds": _integer("Longest wait (default 50; up to 3600 if your client allows long tool calls).")},
      ("project",))
def wait_for_jobs(s: Session, a: Args) -> str:
    p = s.project(a.str("project"))
    wanted = [job_uid(j) for j in a.list("jobs")]
    jobs = {j["uid"]: j for j in s.jobs(p["uid"])}
    missing = [u for u in wanted if u not in jobs]
    if missing:
        raise ToolError(f"{p['uid']} has no job {', '.join(missing)}")
    if not wanted:
        wanted = [u for u, j in jobs.items() if j["status"] in ACTIVE]
        if not wanted:
            return f"No job is queued or running in {p['uid']}."
    timeout = a.int("timeout_seconds", 50, 1, 3600)
    start = time.monotonic()
    while True:
        pending = [u for u in wanted if jobs[u]["status"] not in DONE and jobs[u]["status"] != "waiting"]
        waited = time.monotonic() - start
        if not pending or waited >= timeout or a.cancelled():
            break
        a.progress(len(wanted) - len(pending), len(wanted), f"{len(pending)} job(s) still running after {duration(waited)}")
        time.sleep(min(5.0, max(0.2, timeout - waited)))
        jobs = {j["uid"]: j for j in s.jobs(p["uid"])}
    types = s.jobtypes()
    lines = [f"After {duration(time.monotonic() - start)}:"]
    lines += [job_line(jobs[u], types) for u in wanted]
    if pending:
        lines.append(f"Still running: {', '.join(pending)}. Call wait_for_jobs again, or tell the person and check later.")
    failed = [u for u in wanted if jobs[u]["status"] == "failed"]
    if failed:
        lines.append(f"Failed: {', '.join(failed)} — get_job shows the error and the end of the log.")
    done = [u for u in wanted if jobs[u]["status"] == "completed"]
    if done:
        lines.append(f"Completed: {', '.join(done)} — get_job gives their reports.")
    return "\n".join(lines)


@tool("kill_job", "Stop a job", """
Stop a running job (or take a queued job out of the queue, back to building). Ask the person first unless they
asked for it: the work done so far is lost.""",
      {"project": PROJECT, "job": JOB}, ("project", "job"), read_only=False, destructive=True, idempotent=True)
def kill_job(s: Session, a: Args) -> str:
    p, job = project_and_job(s, a)
    if job["status"] not in ("queued", "launched", "running", "waiting"):
        return f"{job['uid']} is {job['status']}: nothing to stop."
    after = s.post(f"/api/projects/{p['uid']}/jobs/{job['uid']}/kill")
    return f"{p['uid']}/{job['uid']}: {job['status']} → {after['status']}" + (f" ({after['message']})" if after.get("message") else "")


@tool("control_job", "Steer a running live job", """
Send a request to a running job that works in cycles (live preprocessing, relion_live): "stop" processes the movies
arrived meanwhile and ends the job with its outputs (micrographs, picks, particles, 2D classes; kill_job would discard
them), "pause" lets the movies wait, "resume" goes on, "thresholds" changes the acceptance limits from the next cycle
(limits: {"ctf_res": [null, 6], "motion": [null, 40], "defocus": [5000, 30000]}; metrics ctf_res, defocus, astigmatism,
ctf_fom, motion, motion_early, motion_late, ice, ice_fraction, ice_thickness, phase_shift). Ask the person first unless
they asked for it: the collection may still be running.""",
      {"project": PROJECT, "job": JOB, "action": _string("stop, pause, resume or thresholds.", enum=["stop", "pause", "resume", "thresholds"]),
       "limits": _object("thresholds: the new limits {metric: [lower, upper]} (null: no bound).")},
      ("project", "job", "action"), read_only=False, destructive=False, idempotent=True)
def control_job(s: Session, a: Args) -> str:
    p, job = project_and_job(s, a)
    action = a.str("action")
    if job["status"] not in ("launched", "running"):
        return f"{job['uid']} is {job['status']}: not running."
    limits = a.dict("limits") if action == "thresholds" else None
    if action == "thresholds" and not limits:
        raise ToolError("thresholds: give the limits, e.g. {\"ctf_res\": [null, 6]}")
    after = s.post(f"/api/projects/{p['uid']}/jobs/{job['uid']}/control", {"action": action, "value": limits})
    tail = " It finishes its cycle first: wait_for_jobs follows it to the end." if action == "stop" else ""
    return f"{p['uid']}/{job['uid']}: {after.get('message') or action}.{tail}"


@tool("clear_job", "Reset a failed job", """
Reset a failed or killed job to building state, deleting its files, so that update_job can fix it and queue_job
run it again. Completed jobs are not cleared: clone_job runs a copy instead and keeps the results.""",
      {"project": PROJECT, "job": JOB}, ("project", "job"), read_only=False, destructive=True, idempotent=True)
def clear_job(s: Session, a: Args) -> str:
    p, job = project_and_job(s, a)
    if job["status"] == "building":
        return f"{job['uid']} is already in building state."
    if job["status"] not in ("failed", "killed"):
        raise ToolError(f"{job['uid']} is {job['status']}: only failed or killed jobs are cleared by agents "
                        "(clone_job makes a copy to run again)")
    s.post(f"/api/projects/{p['uid']}/jobs/{job['uid']}/clear")
    detail = s.job(p["uid"], job["uid"])
    return "\n".join([f"{p['uid']}/{job['uid']} cleared: back to building."] + _queue_state(s, p["uid"], detail))


@tool("resume_job", "Resume a failed RELION run", """
Queue a failed or killed RELION 3D auto-refine or 3D classification again from the last iteration it wrote, its files
kept (RELION's Continue, relion_refine --continue): hours of work are not redone. For other jobs, or to change
parameters, clear_job or clone_job instead. Check the error first (get_job): a run that failed for a reason that
remains (memory, a wrong input) fails again.""",
      {"project": PROJECT, "job": JOB}, ("project", "job"), read_only=False, destructive=False, idempotent=False)
def resume_job(s: Session, a: Args) -> str:
    p, job = project_and_job(s, a)
    if job["status"] not in ("failed", "killed"):
        raise ToolError(f"{job['uid']} is {job['status']}: only a failed or killed run is resumed")
    after = s.post(f"/api/projects/{p['uid']}/jobs/{job['uid']}/continue", {})
    return "\n".join([f"{p['uid']}/{job['uid']}: {after.get('message') or 'queued again'} ({after['status']})."]
                     + _queue_state(s, p["uid"], s.job(p["uid"], job["uid"])))


@tool("mark_job_complete", "Use the last iteration of a stopped run", """
Mark a failed or killed iterative RELION job (2D or 3D classification, initial model, 3D auto-refine, local
refinement) as complete with the outputs of the last iteration it wrote, as CryoSPARC's "Mark as complete": a 2D
classification stopped early because the classes were good enough, or a refinement that crashed near the end (its
map is then the average of the two half maps of that iteration). resume_job goes on with the run instead.""",
      {"project": PROJECT, "job": JOB}, ("project", "job"), read_only=False, destructive=False, idempotent=False)
def mark_job_complete(s: Session, a: Args) -> str:
    p, job = project_and_job(s, a)
    if job["status"] not in ("failed", "killed"):
        raise ToolError(f"{job['uid']} is {job['status']}: only a failed or killed run can be marked as complete")
    done = s.post(f"/api/projects/{p['uid']}/jobs/{job['uid']}/complete", {})
    detail = s.job(p["uid"], job["uid"])
    return "\n".join([f"{p['uid']}/{job['uid']}: {done.get('message') or 'completed'}."]
                     + _job_summary(s, p["uid"], detail, []))


@tool("clone_job", "Copy a job to run it again", """
Copy a job (same type, inputs and parameters) as a new job in building state, optionally changing parameters,
inputs or the title, and queueing it: to run it again with other settings while keeping the original.""",
      {"project": PROJECT, "job": JOB, "params": _object("Parameters to change in the copy."),
       "inputs": _object("Inputs to change in the copy."), "title": _string("Title of the copy."),
       "queue": _boolean("Queue the copy (default false).")},
      ("project", "job"), read_only=False, idempotent=False)
def clone_job(s: Session, a: Args) -> str:
    p, job = project_and_job(s, a)
    jt = s.jobtype(job["type"])
    params = check_params(s, jt, a.dict("params"))
    changes, notes = resolve_inputs(s, p["uid"], jt, a.dict("inputs")) if a.has("inputs") else ({}, [])
    copy = s.post(f"/api/projects/{p['uid']}/jobs/{job['uid']}/clone")
    body: dict[str, Any] = {}
    if params:
        body["params"] = params
    if changes:
        body["inputs"] = {k: v for k, v in {**(copy.get("inputs") or {}), **changes}.items() if v}
    if a.has("title"):
        body["title"] = a.str("title")
    if body:
        s.patch(f"/api/projects/{p['uid']}/jobs/{copy['uid']}", body)
    error = ""
    if a.bool("queue"):
        _, error = _queue(s, p["uid"], copy["uid"])
    detail = s.job(p["uid"], copy["uid"])
    return "\n".join([f"Copied {job['uid']} as {copy['uid']}."] + _job_summary(s, p["uid"], detail, notes)
                     + _queue_state(s, p["uid"], detail, error))


@tool("clone_jobs", "Copy a chain of jobs", """
Copy several jobs together, as CryoSPARC's Clone of a selection: each copy is in building state, and the inputs between
the jobs copied link the copies (a chain copied as a chain; inputs from other jobs are kept). To re-run part of a pipeline
with other settings: then update_job the copies and queue_job them (parents first).""",
      {"project": PROJECT, "jobs": _strings("Job ids (J3, J4, J7)."), "workspace": _string("Optional workspace for the copies.")},
      ("project", "jobs"), read_only=False, idempotent=False)
def clone_jobs(s: Session, a: Args) -> str:
    p = s.project(a.str("project"))
    uids = [job_uid(u) for u in a.list("jobs")]
    if not uids:
        raise ToolError("Give the jobs to copy")
    body: dict[str, Any] = {"jobs": uids}
    if a.str("workspace"):
        body["workspaces"] = [a.str("workspace").upper()]
    copies = s.post(f"/api/projects/{p['uid']}/clone", body)
    types = s.jobtypes()
    order = sorted(uids, key=lambda u: int(u[1:]) if u[1:].isdigit() else 0)
    return "\n".join([f"Copied {', '.join(order)} as {', '.join(c['uid'] for c in copies)} (building):"]
                     + [job_line(c, types) for c in copies]
                     + ["update_job changes them; queue_job runs them (each waits for its inputs)."])


@tool("create_workspace", "Create a workspace", """
A workspace of a project, as in CryoSPARC: a named group of its jobs (one dataset, one stage, one question); a job can be
in several. Give the jobs to put in it now; create_job (workspace) and update_job (workspaces) add others.""",
      {"project": PROJECT, "title": _string("Title, e.g. \"2D cleaning\"."), "description": _string("Optional description."),
       "jobs": _strings("Optional jobs to put in it (J3, J4).")},
      ("project", "title"), read_only=False, idempotent=False)
def create_workspace(s: Session, a: Args) -> str:
    p = s.project(a.str("project"))
    ws = s.post(f"/api/projects/{p['uid']}/workspaces", {"title": a.str("title"), "description": a.str("description"),
                                                        "jobs": [job_uid(u) for u in a.list("jobs")]})
    return (f"Created workspace {ws['uid']} “{ws['title']}” in {p['uid']} with {ws['num_jobs']} job{'s' if ws['num_jobs'] != 1 else ''}. "
            "get_project with workspace shows its jobs.")


# ================================================================== workflows
@tool("list_workflows", "List workflows", """
The ready-made pipelines (e.g. CryoSPARC map → de novo model → refinement → validation → deposition package):
their steps (node ids and job types), the optional steps, and the parameters each step asks for.""")
def list_workflows(s: Session, a: Args) -> str:
    lines = []
    for wf in s.get("/api/workflows"):
        lines.append(f"{wf['id']} — {wf['title']}: {wf['description']}")
        for node in wf["nodes"]:
            flags = []
            if node.get("optional"):
                flags.append("optional" + ("" if node.get("default", True) else ", off by default"))
            if node.get("interactive"):
                flags.append("interactive")
            ask = ", ".join(p["name"] + (f"={value_text(p['default'])}" if p.get("default") not in (None, "") else "")
                            for p in node.get("ask") or [])
            lines.append(f"  {node['id']}: {node['type']} “{node['title']}”" + (f" [{'; '.join(flags)}]" if flags else "")
                         + (f" — asks {ask}" if ask else ""))
    lines.append("apply_workflow creates the jobs; params={node_id: {param: value}} answers what the steps ask.")
    return "\n".join(lines)


@tool("apply_workflow", "Create a workflow's jobs", """
Create all the jobs of a workflow in a project, wired together. params gives the parameters of the steps by node
id: {"import": {"job_dir": "/data/CS-x/J245", "symmetry": "C1"}, "seq": {"fasta": ">A\\nMKT..."}}. include lists
the optional steps to keep (default: those on by default). queue=true queues them all.""",
      {"project": PROJECT, "workflow": _string("Workflow id (list_workflows)."),
       "params": _object("Node id → {parameter: value}."), "include": _strings("Optional node ids to include."),
       "queue": _boolean("Queue every job (default false)."), "lane": _string("Optional lane.")},
      ("project", "workflow"), read_only=False, idempotent=False)
def apply_workflow(s: Session, a: Args) -> str:
    p = s.project(a.str("project"))
    workflows = {w["id"]: w for w in s.get("/api/workflows")}
    wid = a.str("workflow")
    if wid not in workflows:
        raise ToolError(f"Unknown workflow '{wid}'. Workflows: {', '.join(workflows)}")
    wf = workflows[wid]
    nodes = {n["id"]: n for n in wf["nodes"]}
    overrides = a.dict("params")
    for node_id, values in overrides.items():
        if node_id not in nodes:
            raise ToolError(f"{wid} has no step '{node_id}'. Steps: {', '.join(nodes)}")
        if not isinstance(values, dict):
            raise ToolError(f"params[{node_id}] must be an object of parameter: value")
        check_params(s, s.jobtype(nodes[node_id]["type"]), values)
    include = a.list("include")
    unknown = [i for i in include if i not in nodes]
    if unknown:
        raise ToolError(f"{wid} has no step {', '.join(unknown)}. Optional steps: "
                        + ", ".join(n for n, node in nodes.items() if node.get("optional")))
    body = {"overrides": overrides, "queue": a.bool("queue"), "lane": a.str("lane") or None}
    if a.given("include"):  # the optional steps to keep ([]: none of them)
        body["include"] = include
    result = s.post(f"/api/projects/{p['uid']}/workflows/{wid}", body)
    types = s.jobtypes()
    lines = [f"Created {len(result['jobs'])} jobs of {wid} in {p['uid']}:"]
    lines += ["  " + job_line(j, types) for j in result["jobs"]]
    if result.get("queued"):
        lines.append(f"Queued: {', '.join(result['queued'])}")
    for uid, problem in (result.get("problems") or {}).items():
        lines.append(f"Not queued {uid}: {problem}")
    if not a.bool("queue"):
        lines.append("Not queued yet: check them (get_job), then queue_job each, or ask the person.")
    return "\n".join(lines)


# ================================================================== files
@tool("browse_server_files", "Browse server folders", """
List a folder on the server (folders first, then files with sizes), to find data to import: CryoSPARC job folders,
half maps, models, FASTA files. Only the folders your account may read. pattern filters names (e.g. *.mrc).""",
      {"path": _string("Folder on the server (default: your home or first data folder)."),
       "pattern": _string("Only names matching this pattern (e.g. *half*.mrc)."),
       "show_hidden": _boolean("Include names starting with a dot (default false).")})
def browse_server_files(s: Session, a: Args) -> str:
    listing = s.get("/api/fs", path=a.str("path"), show_hidden=a.bool("show_hidden"))
    pattern = a.str("pattern").lower()
    entries = [e for e in listing["entries"] if not pattern or e["is_dir"] or fnmatch.fnmatch(e["name"].lower(), pattern)]
    lines = [f"{listing['path']}" + (f" (parent: {listing['parent']})" if listing.get("parent") else "")]
    shown = entries[:300]
    for e in shown:
        if e["is_dir"]:
            lines.append(f"  {e['name']}/")
        else:
            lines.append(f"  {e['name']}  {size(e['size'])}  {when(e['mtime'])}")
    if len(entries) > len(shown):
        lines.append(f"  … {len(entries) - len(shown)} more (use pattern)")
    if not entries:
        lines.append("  (empty" + (f" for {pattern}" if pattern else "") + ")")
    lines.append(f"Readable folders: {', '.join(listing.get('roots') or [])}")
    return "\n".join(lines)


MAP_FILE = re.compile(r"\.(mrc|map|ccp4)(\.gz)?$", re.I)  # not .mrcs: particle stacks
MODEL_FILE = re.compile(r"\.(pdb|cif|mmcif|ent)(\.gz)?$", re.I)
SEQUENCE_FILE = re.compile(r"\.(fasta|fa|faa|fas|fna|seq)$", re.I)
MAP_ROLES = ("half_map_a", "half_map_b", "map", "map_sharp", "mask")


def map_role(name: str) -> str:
    """What a map is, from its file name (CryoSPARC, RELION, EMDB names), as the web interface guesses it."""
    n = MAP_FILE.sub("", name.lower())
    if re.search(r"half[_-]?(map[_-]?)?(a|1)([^a-z0-9]|$)", n):
        return "half_map_a"
    if re.search(r"half[_-]?(map[_-]?)?(b|2)([^a-z0-9]|$)", n):
        return "half_map_b"
    if re.search(r"(mask|msk)(?!ed)", n):  # EMDB: emd_1234_msk_1.map; RELION's postprocess_masked is a map
        return "mask"
    if re.search(r"sharp|postprocess|post_process|(^|[^a-z0-9])pp([^a-z0-9]|$)", n):
        return "map_sharp"
    return "map"


def import_suggestions(files: list[str], folder: str = "") -> list[tuple[str, dict[str, Any], str]]:
    """The import jobs for uploaded files (server paths), guessed from their names: (job type, params, note);
    ``folder``: they were sent as this folder (a CryoSPARC job folder, a series of volumes)."""
    names = [f.rsplit("/", 1)[-1] for f in files]
    maps = [f for f in files if MAP_FILE.search(f)]
    if folder and ("job.json" in names or any(re.match(r"J\d+_\d+_volume_map", n, re.I) for n in names)):
        return [("import_cryosparc", {"job_dir": folder}, "a CryoSPARC job folder")]
    if folder and len(maps) >= 2 and all(map_role(m) == "map" for m in maps):
        return [("import_volume_series", {"path": folder}, f"{len(maps)} volumes")]
    out: list[tuple[str, dict[str, Any], str]] = []
    roles: dict[str, str] = {}
    for m in maps:
        role = map_role(m.rsplit("/", 1)[-1])
        if role in roles:
            out.append(("import_maps", {"map_sharp": m}, ""))
        else:
            roles[role] = m
    if list(roles) == ["map"]:  # a single map: the one to look at and build into
        roles = {"map_sharp": roles["map"]}
    if roles:
        half = ("half_map_a" in roles) != ("half_map_b" in roles)
        out.insert(0, ("import_maps", {r: roles[r] for r in MAP_ROLES if r in roles}, "add the other half map" if half else ""))
    for f in files:
        if MODEL_FILE.search(f):
            out.append(("import_model", {"source": "file", "path": f}, ""))
        elif SEQUENCE_FILE.search(f):
            out.append(("import_sequence", {"path": f}, ""))
        elif f.lower().endswith(".zip"):
            out.append(("import_volume_series", {"path": f}, "a ZIP of volumes"))
        elif f.lower().endswith((".cs", ".star")):
            out.append(("import_particles", {"path": f}, "metadata only: the particle images must be on the server, "
                        "give their folder as datadir"))
    return out


@tool("upload_file", "Upload a file from this computer", """
Send a file or a whole folder from the computer this agent runs on into the uploads folder of a project on the
CryoPlug server (in chunks; an interrupted upload resumes, a file already there is not sent again). Answers its path
on the server and the create_job call importing it (import_maps, import_model, import_cryosparc...). Not needed when
the server can read the file already (browse_server_files): give that path to the import job.""",
      {"project": PROJECT, "path": _string("File or folder on this computer (absolute, or ~/...).")},
      ("project", "path"), read_only=False, idempotent=True, local_only=True)
def upload_file(s: Session, a: Args) -> str:
    from cryoplug.agent import client as transfer

    if not s.local_files:
        raise ToolError("upload_file reads files of the agent's computer: it works with `cryoplug mcp` (or `cryoplug "
                        "upload`) run there, not through the server's /mcp address. Files already on the server: "
                        "give their path to the import job (browse_server_files finds them).")
    text = a.str("path")
    if not text:
        raise ToolError("Give the path of the file or folder on this computer")
    path = Path(os.path.expanduser(text))
    if not path.is_absolute():
        path = Path.cwd() / path
    if not path.exists():
        raise ToolError(f"No file or folder {path} on this computer")
    p = s.project(a.str("project"))
    started = time.time()

    def progress(sent: int, total: int) -> None:
        if a.cancelled():
            raise ToolError(f"Upload of {path.name} stopped at {size(sent)} of {size(total)}: call upload_file again "
                            "to resume it")
        a.progress(sent, total, f"{path.name}: {size(sent)} of {size(total)}")

    try:
        if path.is_dir():
            res = transfer.upload_folder(s.client, p["uid"], path, progress)
            files = [e["path"] for e in s.get(f"/api/projects/{p['uid']}/uploads")["files"]
                     if e["rel"].startswith(f"{res['folder']}/")]
            lines = [f"Uploaded the folder {path} ({res['files']} files, {size(res['size'])}) to {p['uid']}: {res['path']}"]
            suggestions = import_suggestions(files, res["path"])
        else:
            res = transfer.upload_file(s.client, p["uid"], path, progress=progress)
            lines = [f"{'Already uploaded' if res.get('reused') else 'Uploaded'} {path.name} ({size(res['size'])}) to "
                     f"{p['uid']}: {res['path']}"]
            suggestions = import_suggestions([res["path"]])
    except OSError as exc:  # unreadable here
        raise ToolError(f"Cannot read {exc.filename or path}: {exc.strerror or exc}") from None
    seconds = time.time() - started
    if seconds > 5:
        lines[0] += f" in {duration(seconds)}"
    if suggestions:
        lines.append("To import it:" if len(suggestions) == 1 else "To import them:")
        lines += [f"  create_job project={p['uid']} type={jtype} params={json.dumps(params)} queue=true"
                  + (f"  ({note})" if note else "") for jtype, params, note in suggestions]
    else:
        lines.append("Give this path to the job parameter that needs the file (describe_job_type shows them).")
    return "\n".join(lines)


@tool("list_job_files", "List a job's files", """
The files in a job's folder (or a subfolder), with their paths relative to the project, for read_project_file.""",
      {"project": PROJECT, "job": JOB, "subfolder": _string("Subfolder of the job folder (default: the folder itself).")},
      ("project", "job"))
def list_job_files(s: Session, a: Args) -> str:
    p, job = project_and_job(s, a)
    entries = s.get(f"/api/projects/{p['uid']}/jobs/{job['uid']}/files", sub=a.str("subfolder"))
    lines = [f"{p['uid']}/{job['uid']}/{a.str('subfolder')}".rstrip("/") + f" ({len(entries)} entries):"]
    for e in entries[:400]:
        lines.append(f"  {e['path']}{'/' if e['is_dir'] else ''}" + ("" if e["is_dir"] else f"  {size(e['size'])}")
                     + ("  (link)" if e.get("symlink") else ""))
    if len(entries) > 400:
        lines.append(f"  … {len(entries) - 400} more")
    return "\n".join(lines)


@tool("read_project_file", "Read a text file of a project", """
Read a text file of a project (path relative to the project folder, as listed by list_job_files or shown in
outputs): logs, STAR/CSV tables, PDB/mmCIF headers, FASTA, program parameter files. Maps and other binary files
are refused (map_info describes a map).""",
      {"project": PROJECT, "path": _string("Path relative to the project folder, e.g. J3/run.out."),
       "start_line": _integer("First line to show, from 1 (default 1)."),
       "max_lines": _integer("Number of lines (default 200, at most 2000).")},
      ("project", "path"))
def read_project_file(s: Session, a: Args) -> str:
    p = s.project(a.str("project"))
    path = a.str("path").lstrip("/")
    if path.lower().endswith(BINARY_SUFFIXES):
        raise ToolError(f"{path} is a binary file" + (": map_info describes a map" if path.lower().endswith((".mrc", ".map", ".ccp4")) else ""))
    limit = 8_000_000
    data, _ = s.client.request("GET", f"/api/projects/{p['uid']}/file", params={"path": path}, raw=True, max_bytes=limit)
    if b"\x00" in data[:8192]:
        raise ToolError(f"{path} is a binary file")
    lines = data[:limit].decode("utf-8", errors="replace").splitlines()
    if len(data) >= limit:
        lines.append(f"[… only the first {size(limit)} of the file are read]")
    start = a.int("start_line", 1, 1)
    count = a.int("max_lines", 200, 1, 2000)
    chunk = lines[start - 1: start - 1 + count]
    head = f"{p['uid']}:{path} — lines {start}-{start + len(chunk) - 1} of {len(lines)}"
    if start - 1 + count < len(lines):
        head += f" (start_line={start + count} continues)"
    return head + "\n" + "\n".join(short(line, 1000) if len(line) > 1000 else line for line in chunk)


@tool("get_job_images", "Look at a job's images", """
The images of a job: the pictures of its report (FSC curves rendered by the program, ChimeraX views...) and the
thumbnails of its outputs (central projections of maps, Cα traces of models). which picks images by number or by
a word of their title; default: up to 4.""",
      {"project": PROJECT, "job": JOB, "which": _string("Image numbers (\"1,3\") or a word of their titles.")},
      ("project", "job"))
def get_job_images(s: Session, a: Args) -> Reply:
    p, job = project_and_job(s, a)
    found = [(sec.get("title") or "image", sec["path"]) for sec in (job.get("report") or {}).get("sections") or []
             if sec.get("kind") == "image" and sec.get("path")]
    found += [(f"thumbnail of {o['name']} ({o['type']})", o["thumbnail"]) for o in job.get("outputs") or [] if o.get("thumbnail")]
    if not found:
        return Reply(f"{p['uid']}/{job['uid']} has no image (status {job['status']}).")
    which = a.str("which")
    numbers = [int(x) for x in re.findall(r"\d+", which)] if which and re.fullmatch(r"[\d,\s]+", which) else []
    if numbers:
        chosen = [(i, found[i - 1]) for i in numbers if 1 <= i <= len(found)]
    elif which:
        chosen = [(i + 1, f) for i, f in enumerate(found) if which.lower() in f[0].lower()]
    else:
        chosen = [(i + 1, f) for i, f in enumerate(found)][:4]
    lines = [f"{p['uid']}/{job['uid']}: {len(found)} image{'s' if len(found) > 1 else ''}"]
    lines += [f"  {i}. {title} — {path}" for i, (title, path) in enumerate(found, 1)]
    images = []
    for _, (title, path) in chosen[:4]:
        data, ctype = s.raw(f"/api/projects/{p['uid']}/file", path=path)
        mime = ctype.split(";")[0].strip() if ctype.startswith("image/") else (
            "image/png" if path.lower().endswith(".png") else "image/jpeg")
        if len(data) > 4_000_000:
            lines.append(f"  ({title} is too large to show: {size(len(data))})")
            continue
        images.append((mime, data))
    lines.append(f"Showing {len(images)}." if images else "No image shown.")
    return Reply("\n".join(lines), images)


@tool("map_info", "Describe a map", """
The header of a map file of a project: box size, voxel size, origin, file size. path is relative to the project,
or an output written "J3.map_sharp".""",
      {"project": PROJECT, "path": _string("Map path relative to the project, or \"J3.output\".")},
      ("project", "path"))
def map_info(s: Session, a: Args) -> str:
    p = s.project(a.str("project"))
    path = a.str("path")
    match = re.fullmatch(r"([Jj]?\d+)\.(\w+)", path)
    if match:
        job = s.job(p["uid"], match.group(1))
        out = next((o for o in job.get("outputs") or [] if o["name"] == match.group(2)), None)
        if out is None:
            raise ToolError(f"{job['uid']} has no output '{match.group(2)}'")
        path = out["path"]
    info = s.get(f"/api/projects/{p['uid']}/map-info", path=path)
    lines = [f"{p['uid']}:{path}"]
    lines += [f"  {k}: {value_text(v)}" for k, v in info.items()]
    return "\n".join(lines)


# ================================================================== skills
@tool("list_skills", "List CryoPlug skills", """
The skills that teach how to do cryo-EM work with CryoPlug (map assessment, masks and fitting, model building,
refinement and validation, heterogeneity, RELION focused refinement, troubleshooting). Read one with read_skill.""")
def list_skills(s: Session, a: Args) -> str:
    from cryoplug.agent.skills import all_skills
    lines = ["Skills (read_skill NAME; read “cryoplug” first):"]
    for sk in all_skills():
        extra = [f for f in sk.files() if f != "SKILL.md"]
        lines.append(f"  {sk.name}: {sk.description}" + (f" Files: {', '.join(extra)}." if extra else ""))
    return "\n".join(lines)


@tool("read_skill", "Read a CryoPlug skill", """
Read a skill (its SKILL.md, or one of its reference files): procedures, decision rules, reference values and
pitfalls for a cryo-EM task in CryoPlug.""",
      {"name": _string("Skill name, e.g. cryoplug or cryoplug-model-building."),
       "file": _string("Optional file of the skill, e.g. references/job-types.md (default SKILL.md).")},
      ("name",))
def read_skill(s: Session, a: Args) -> str:
    from cryoplug.agent.skills import get_skill
    try:
        skill = get_skill(a.str("name"))
        return skill.read(a.str("file") or "SKILL.md")
    except KeyError as exc:
        raise ToolError(str(exc).strip("'\"")) from None


# ================================================================== profiles
# Fewer tools for small local models (``basic``), or an assistant that only looks (``read``).
BASIC = ("server_status", "list_projects", "get_project", "list_job_types", "describe_job_type", "find_outputs",
         "create_job", "queue_job", "get_job", "get_job_log", "wait_for_jobs", "read_skill")


def select_tools(spec: str | None, local_files: bool = True) -> list[str]:
    """The tool names of a profile (``all``, ``basic``, ``read``) or of a comma-separated list; without
    ``local_files`` (the server's /mcp), not the tools reading files of the agent's computer."""
    offered = [n for n, t in TOOLS.items() if local_files or not t.local_only]
    spec = (spec or "all").strip().lower()
    if spec == "all":
        return offered
    if spec == "basic":
        return list(BASIC)
    if spec in ("read", "read-only", "readonly"):
        return [n for n in offered if TOOLS[n].read_only]
    names = [n.strip() for n in spec.split(",") if n.strip()]
    unknown = [n for n in names if n not in TOOLS]
    if unknown or not names:
        raise ValueError(f"Unknown tool(s) {', '.join(unknown) or spec!r}: use all, basic, read or names among "
                         f"{', '.join(offered)}")
    elsewhere = [n for n in names if n not in offered]
    if elsewhere:
        raise ValueError(f"{', '.join(elsewhere)} read(s) files of the agent's computer: use `cryoplug mcp` there")
    return names


# ================================================================== running a tool
def call(session: Session, name: str, arguments: dict[str, Any] | None = None, **hooks: Any) -> Reply:
    """Run a tool; mistakes and server errors become error replies the agent can read."""
    t = TOOLS.get(name)
    if t is None:
        return Reply(f"Unknown tool '{name}'. Tools: {', '.join(TOOLS)}", error=True)
    args = Args(arguments if isinstance(arguments, dict) else {}, **hooks)
    try:
        result = t.run(session, args)
    except ApiError as exc:
        if exc.status == 401:  # the message says which credential to give
            return Reply(str(exc), error=True)
        return Reply(f"CryoPlug refused: {exc}" if exc.status < 500 else f"CryoPlug server error: {exc}", error=True)
    except AgentError as exc:
        return Reply(str(exc), error=True)
    reply = result if isinstance(result, Reply) else Reply(str(result))
    unknown = [k for k in args.raw if k not in args.used]
    if unknown:
        reply.text += f"\n(Ignored unknown argument{'s' if len(unknown) > 1 else ''}: {', '.join(unknown)}.)"
    return reply


def image_blocks(reply: Reply) -> list[dict[str, Any]]:
    return [{"type": "image", "data": base64.b64encode(data).decode(), "mimeType": mime} for mime, data in reply.images]
