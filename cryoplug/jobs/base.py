"""Job type framework: parameters, input/output slots and the run context."""
from __future__ import annotations

import datetime as _dt
import fnmatch
import json
import os
import shlex
import signal
import socket
import subprocess
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Iterable

from cryoplug import tools as toolmod

CATEGORIES = [
    "Import",
    "Map processing",
    "Model building",
    "Interactive",
    "Refinement",
    "Validation",
    "Deposition",
    "Utilities",
]
DATA_TYPES = {
    "half_maps": "Half maps",
    "map": "Map",
    "mask": "Mask",
    "model": "Atomic model",
    "sequence": "Sequence",
    "fsc": "FSC curve",
    "report": "Report",
    "restraints": "Restraints (CIF)",
    "files": "Files",
}
TERMINAL_STATUSES = {"completed", "failed", "killed"}
ACTIVE_STATUSES = {"queued", "launched", "running"}


class JobError(Exception):
    """A user-facing error: reported without a traceback."""


class JobKilled(BaseException):
    """Raised inside the worker when the job receives SIGTERM."""


# --------------------------------------------------------------- definitions
@dataclass
class Param:
    name: str
    type: str  # float | int | bool | str | text | choice | path
    default: Any = None
    label: str = ""
    help: str = ""
    choices: list[Any] | None = None
    advanced: bool = False
    required: bool = False
    min: float | None = None
    max: float | None = None
    unit: str = ""
    placeholder: str = ""
    path_kind: str = "any"  # for type=path: file | dir | any

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["label"] = self.label or self.name.replace("_", " ").capitalize()
        return d

    def coerce(self, value: Any) -> Any:
        if value is None or (isinstance(value, str) and value.strip() == "" and self.type not in ("text",)):
            return self.default
        try:
            if self.type == "float":
                value = float(value)
            elif self.type == "int":
                value = int(float(value))
            elif self.type == "bool":
                if isinstance(value, str):
                    value = value.strip().lower() in ("1", "true", "yes", "on")
                else:
                    value = bool(value)
            elif self.type in ("str", "text", "path"):
                value = str(value)
                if self.type == "path":
                    value = os.path.expanduser(value.strip())
            elif self.type == "choice":
                if self.choices and value not in self.choices:
                    raise ValueError(f"must be one of {self.choices}")
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Parameter '{self.name}': invalid value {value!r} ({exc})") from None
        if self.type in ("float", "int"):
            if self.min is not None and value < self.min:
                raise ValueError(f"Parameter '{self.name}' must be >= {self.min}")
            if self.max is not None and value > self.max:
                raise ValueError(f"Parameter '{self.name}' must be <= {self.max}")
        return value


@dataclass
class Slot:
    name: str
    types: tuple[str, ...]
    label: str = ""
    required: bool = True
    help: str = ""
    prefer: tuple[str, ...] = ()  # output names suggested first when pre-filling (default: sharpened map first)

    def to_dict(self) -> dict[str, Any]:
        prefer = self.prefer or (("map_sharp", "map") if "map" in self.types else ())
        return {"name": self.name, "types": list(self.types), "label": self.label or self.name.replace("_", " ").capitalize(),
                "required": self.required, "help": self.help, "prefer": list(prefer)}


@dataclass
class OutputDef:
    name: str
    type: str
    label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "type": self.type, "label": self.label or self.name.replace("_", " ").capitalize()}


def resolution_param(**kw: Any) -> Param:
    return Param("resolution", "float", 0.0, label="Resolution", unit="Å", min=0.0,
                 help="Map resolution. 0 = take it from the inputs (CryoSPARC import / FSC).", **kw)


def extra_args_param() -> Param:
    return Param("extra_args", "str", "", label="Extra arguments", advanced=True,
                 help="Additional command-line arguments appended verbatim to the program call.")


class JobType:
    name: ClassVar[str] = ""
    title: ClassVar[str] = ""
    category: ClassVar[str] = "Utilities"
    description: ClassVar[str] = ""
    tool: ClassVar[str | None] = None  # key in cryoplug.tools.TOOLS
    gpu: ClassVar[int] = 0
    cpus: ClassVar[int] = 1
    interactive: ClassVar[bool] = False
    software: ClassVar[list[str]] = []  # names used in the methods draft
    params: ClassVar[list[Param]] = []
    inputs: ClassVar[list[Slot]] = []
    outputs: ClassVar[list[OutputDef]] = []

    # ----------------------------------------------------------- schema
    @classmethod
    def schema(cls) -> dict[str, Any]:
        return {
            "name": cls.name,
            "title": cls.title,
            "category": cls.category,
            "description": cls.description,
            "tool": cls.tool,
            "gpu": cls.gpu,
            "cpus": cls.cpus,
            "interactive": cls.interactive,
            "params": [p.to_dict() for p in cls.params],
            "inputs": [s.to_dict() for s in cls.inputs],
            "outputs": [o.to_dict() for o in cls.outputs],
        }

    @classmethod
    def param(cls, name: str) -> Param:
        for p in cls.params:
            if p.name == name:
                return p
        raise KeyError(name)

    @classmethod
    def slot(cls, name: str) -> Slot:
        for s in cls.inputs:
            if s.name == name:
                return s
        raise KeyError(name)

    @classmethod
    def coerce_params(cls, raw: dict[str, Any] | None) -> dict[str, Any]:
        raw = dict(raw or {})
        known = {p.name for p in cls.params}
        unknown = set(raw) - known
        if unknown:
            raise ValueError(f"Unknown parameter(s) for {cls.name}: {', '.join(sorted(unknown))}")
        return {p.name: p.coerce(raw.get(p.name)) for p in cls.params}

    @classmethod
    def check(cls, params: dict[str, Any], connected: dict[str, str]) -> list[str]:
        """Return a list of problems preventing the job from being queued."""
        problems = []
        for p in cls.params:
            if p.required and (params.get(p.name) in (None, "")):
                problems.append(f"Parameter '{p.to_dict()['label']}' is required")
        for s in cls.inputs:
            if s.required and s.name not in connected:
                problems.append(f"Input '{s.to_dict()['label']}' is required")
        problems.extend(cls.validate(params, connected))
        return problems

    @classmethod
    def validate(cls, params: dict[str, Any], connected: dict[str, str]) -> list[str]:
        """Job-specific cross checks (override)."""
        return []

    @classmethod
    def resources(cls, params: dict[str, Any]) -> dict[str, int]:
        """Compute resources requested from the lane."""
        return {"num_gpus": 0 if cls.interactive else cls.gpu, "num_cpus": cls.cpus}

    # ---------------------------------------------------------- execution
    def run(self, ctx: "JobContext") -> None:
        raise NotImplementedError

    def prepare(self, ctx: "JobContext") -> None:
        """Interactive jobs: prepare the session, then the job waits for the user."""
        raise NotImplementedError

    def finalize(self, ctx: "JobContext", choice: str | None) -> None:
        """Interactive jobs: register the user's result."""
        raise NotImplementedError

    def launch_command(self, ctx: "JobContext") -> list[str] | None:
        """Interactive jobs: command opening the GUI program on the server display."""
        return None


# ------------------------------------------------------------------ context
class InputData:
    def __init__(self, data: dict[str, Any]):
        self.data = data
        self.type: str = data.get("type", "")
        self.path: str = data.get("path", "")
        self.files: list[str] = list(data.get("files") or ([self.path] if self.path else []))
        self.meta: dict[str, Any] = dict(data.get("meta") or {})
        self.source: str = data.get("source", "")
        self.label: str = data.get("label", "")

    def __repr__(self) -> str:  # pragma: no cover
        return f"InputData({self.source}, {self.type}, {self.path})"


def _now() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class JobContext:
    """Everything a job needs while running inside a worker."""

    def __init__(self, spec: dict[str, Any], job_dir: str | Path):
        self.spec = spec
        self.job_dir = Path(job_dir)
        self.project_dir = Path(spec.get("project_dir") or self.job_dir.parent)
        self.uid: str = spec.get("uid", "")
        self.project_uid: str = spec.get("project_uid", "")
        self.params: dict[str, Any] = dict(spec.get("params") or {})
        self.inputs: dict[str, InputData | None] = {
            name: (InputData(v) if v else None) for name, v in (spec.get("inputs") or {}).items()
        }
        self.tools: dict[str, dict[str, Any]] = dict(spec.get("tools") or {})
        self.resources: dict[str, Any] = dict(spec.get("resources") or {})
        self.ancestors: list[dict[str, Any]] = list(spec.get("ancestors") or [])
        self.state_path = self.job_dir / "state.json"
        self.log_path = self.job_dir / "job.log"
        self.report_path = self.job_dir / "report.json"
        self.state: dict[str, Any] = {}
        if self.state_path.exists():
            try:
                self.state = json.loads(self.state_path.read_text())
            except (OSError, ValueError):
                self.state = {}
        self.outputs: list[dict[str, Any]] = list(self.state.get("outputs") or [])
        self.highlights: list[dict[str, Any]] = list(self.state.get("highlights") or [])
        self.report: dict[str, Any] = {"sections": []}
        if self.report_path.exists():
            try:
                self.report = json.loads(self.report_path.read_text())
            except (OSError, ValueError):
                pass
        self._current_proc: subprocess.Popen | None = None
        self._state_lock = threading.Lock()

    # ------------------------------------------------------------ logging
    def log(self, message: str, level: str = "info") -> None:
        prefix = {"info": "", "warning": "WARNING: ", "error": "ERROR: "}.get(level, "")
        with open(self.log_path, "a", encoding="utf-8") as fh:
            for line in str(message).splitlines() or [""]:
                fh.write(f"[{_now()}] {prefix}{line}\n")

    def warn(self, message: str) -> None:
        self.log(message, "warning")

    def _raw_log(self, text: str) -> None:
        with open(self.log_path, "a", encoding="utf-8", errors="replace") as fh:
            fh.write(text)

    # -------------------------------------------------------------- state
    def write_state(self, **fields: Any) -> None:
        with self._state_lock:
            self.state.update(fields)
            self.state["outputs"] = self.outputs
            self.state["highlights"] = self.highlights
            tmp = self.state_path.with_name(f".state.{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps(self.state, indent=1, default=str))
            os.replace(tmp, self.state_path)

    def progress(self, fraction: float, message: str | None = None) -> None:
        fields: dict[str, Any] = {"progress": max(0.0, min(1.0, float(fraction))), "heartbeat": time.time()}
        if message is not None:
            fields["message"] = message
            self.log(message)
        self.write_state(**fields)

    # -------------------------------------------------------------- paths
    def path(self, *parts: str) -> Path:
        return self.job_dir.joinpath(*parts)

    def rel(self, path: str | Path) -> str:
        p = Path(path).resolve()
        try:
            return str(p.relative_to(self.project_dir.resolve()))
        except ValueError:
            return str(p)

    def abs(self, rel: str) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else self.project_dir / p

    def input(self, name: str) -> InputData | None:
        return self.inputs.get(name)

    def require(self, name: str) -> InputData:
        inp = self.inputs.get(name)
        if inp is None:
            raise JobError(f"Input '{name}' is not connected")
        return inp

    # ---------------------------------------------------------- resolution
    def resolution(self, param: str = "resolution", slots: Iterable[str] | None = None) -> float:
        value = float(self.params.get(param) or 0)
        if value > 0:
            return value
        order = list(slots) if slots else ["half_maps", "map", "mask", "model", *self.inputs.keys()]
        for name in order:
            inp = self.inputs.get(name)
            if inp and inp.meta.get("resolution"):
                return float(inp.meta["resolution"])
        raise JobError("Resolution is unknown: set the 'Resolution' parameter (or import maps with a resolution / FSC).")

    def inherited_meta(self, *slots: str) -> dict[str, Any]:
        meta: dict[str, Any] = {}
        for name in slots:
            inp = self.inputs.get(name)
            if inp:
                for key in ("resolution", "pixel_size", "box", "symmetry"):
                    if key in inp.meta and key not in meta:
                        meta[key] = inp.meta[key]
        return meta

    # ------------------------------------------------------------ programs
    def tool_spec(self, key: str) -> dict[str, Any]:
        return self.tools.get(key, {})

    def executable(self, key: str) -> str:
        return toolmod.main_executable(key, self.tools.get(key))

    def program(self, key: str, name: str) -> str:
        """A secondary program of a suite (e.g. phenix.resolve_cryo_em) next to the main executable."""
        main = self.executable(key)
        if os.path.isabs(main):
            candidate = os.path.join(os.path.dirname(main), name)
            if os.path.exists(candidate):
                return candidate
        return name

    def run(self, args: list[str], tool: str | None = None, cwd: str | Path | None = None,
            env: dict[str, str] | None = None, check: bool = True, log_output: bool = True,
            stdout_path: str | Path | None = None) -> int:
        """Run an external command inside the tool environment, streaming output to the job log."""
        args = [str(a) for a in args]
        tool_spec = self.tools.get(tool, {}) if tool else {}
        cmd = toolmod.wrap_command(tool_spec, args, tool or "cmd") if tool else args
        run_env = toolmod.tool_env(tool_spec)
        if env:
            run_env.update(env)
        cwd = Path(cwd) if cwd else self.job_dir
        printable = shlex.join(args)
        self.log(f"$ {printable}")
        with open(self.path("commands.sh"), "a") as fh:
            fh.write(f"# {_now()}\ncd {shlex.quote(str(cwd))}\n{printable}\n")
        out_fh = open(stdout_path, "w") if stdout_path else None
        try:
            proc = subprocess.Popen(cmd, cwd=str(cwd), env=run_env, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, errors="replace", bufsize=1)
        except FileNotFoundError as exc:
            raise JobError(f"Cannot start {args[0]}: {exc}") from None
        self._current_proc = proc
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                if log_output:
                    self._raw_log(line)
                if out_fh:
                    out_fh.write(line)
                self.state["heartbeat"] = time.time()
            code = proc.wait()
        except JobKilled:
            self._terminate(proc)
            raise
        finally:
            self._current_proc = None
            if out_fh:
                out_fh.close()
        self.write_state(heartbeat=time.time())
        if check and code != 0:
            raise JobError(f"{os.path.basename(args[0])} exited with code {code} (see log above)")
        return code

    @staticmethod
    def _terminate(proc: subprocess.Popen) -> None:
        try:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def split_extra(self, name: str = "extra_args") -> list[str]:
        return shlex.split(str(self.params.get(name) or ""))

    # ---------------------------------------------------------------- GPUs
    def physical_gpus(self, override: str = "") -> list[str]:
        """GPU ids as seen by the machine, for programs that set CUDA_VISIBLE_DEVICES themselves."""
        if str(override).strip():
            return [g.strip() for g in str(override).replace(" ", ",").split(",") if g.strip()]
        assigned = [str(g) for g in self.resources.get("gpus") or []]
        if assigned:
            return assigned
        visible = os.environ.get("CUDA_VISIBLE_DEVICES", "").strip()
        if visible:
            return [g for g in visible.split(",") if g]
        return ["0"]

    def relative_gpus(self, override: str = "") -> list[str]:
        """GPU indices inside CUDA_VISIBLE_DEVICES (torch/cuda device numbers)."""
        if str(override).strip():
            return [g.strip() for g in str(override).replace(" ", ",").split(",") if g.strip()]
        n = len(self.resources.get("gpus") or []) or 1
        return [str(i) for i in range(n)]

    # ------------------------------------------------------------- outputs
    def add_output(self, name: str, type: str, files: str | Path | list[str | Path], label: str | None = None,
                   meta: dict[str, Any] | None = None, inherit: Iterable[str] = ()) -> dict[str, Any]:
        file_list = [files] if isinstance(files, (str, Path)) else list(files)
        for f in file_list:
            if not Path(f).exists():
                raise JobError(f"Expected output file is missing: {f}")
        full_meta = self.inherited_meta(*inherit)
        full_meta.update(meta or {})
        if type in ("map", "mask", "half_maps") and "box" not in (meta or {}):
            try:
                from cryoplug.mrc import map_info
                info = map_info(file_list[0])
                full_meta.update({k: v for k, v in info.items() if k in ("box", "pixel_size", "voxel_size", "origin")})
            except Exception as exc:  # pragma: no cover - unreadable map
                self.warn(f"Could not read map header of {file_list[0]}: {exc}")
        out = {
            "name": name,
            "type": type,
            "label": label or name.replace("_", " "),
            "path": self.rel(file_list[0]),
            "files": [self.rel(f) for f in file_list],
            "meta": full_meta,
        }
        thumb = self._thumbnail(name, type, Path(file_list[0]))
        if thumb:
            out["thumbnail"] = self.rel(thumb)
        self.outputs = [o for o in self.outputs if o["name"] != name] + [out]
        self.write_state()
        self.log(f"Output '{name}' ({type}): {', '.join(out['files'])}")
        return out

    def _thumbnail(self, name: str, type: str, path: Path) -> Path | None:
        try:
            from cryoplug.imaging import trace_image, write_png
            target = self.path(f"thumb_{name}.png")
            if type in ("map", "half_maps"):
                from cryoplug.mrc import MapVolume, projection_image
                write_png(target, projection_image(MapVolume.read(path)))
                return target
            if type == "model":
                from cryoplug.modelio import ca_traces, read_structure
                traces = ca_traces(read_structure(path))
                if sum(len(t) for t in traces) < 3:  # a ligand or a few atoms: no meaningful trace
                    return None
                write_png(target, trace_image(traces))
                return target
        except Exception as exc:  # thumbnails are cosmetic
            self.warn(f"Thumbnail for '{name}' failed: {exc}")
        return None

    def add_highlight(self, label: str, value: Any, status: str | None = None) -> None:
        self.highlights = [h for h in self.highlights if h["label"] != label]
        self.highlights.append({"label": label, "value": value, "status": status})
        self.write_state()

    # -------------------------------------------------------------- report
    def _section(self, kind: str, title: str, **data: Any) -> None:
        self.report.setdefault("sections", []).append({"kind": kind, "title": title, **data})
        self.save_report()

    def add_metrics(self, title: str, metrics: list[dict[str, Any]]) -> None:
        """metrics: [{label, value, status: good|warn|bad|None, target}]"""
        self._section("metrics", title, metrics=metrics)

    def add_plot(self, title: str, series: list[dict[str, Any]], x_label: str = "", y_label: str = "",
                 x_kind: str = "linear", hlines: list[dict[str, Any]] | None = None, y_range: list[float] | None = None) -> None:
        """series: [{name, x: [...], y: [...]}]; x_kind 'resolution' labels 1/A axes in A."""
        self._section("plot", title, series=series, x_label=x_label, y_label=y_label, x_kind=x_kind,
                      hlines=hlines or [], y_range=y_range)

    def add_heatmap(self, title: str, x_labels: list[str], y_labels: list[str], values: list[list[float]],
                    unit: str = "", x_label: str = "", y_label: str = "", note: str = "") -> None:
        """Grid of values (rows follow y_labels) drawn with a sequential colour ramp."""
        self._section("heatmap", title, x_labels=x_labels, y_labels=y_labels, values=values, unit=unit,
                      x_label=x_label, y_label=y_label, note=note)

    def add_table(self, title: str, columns: list[str], rows: list[list[Any]]) -> None:
        self._section("table", title, columns=columns, rows=rows)

    def add_text(self, title: str, text: str) -> None:
        self._section("text", title, text=text)

    def add_image(self, title: str, path: str | Path) -> None:
        self._section("image", title, path=self.rel(path))

    def save_report(self) -> None:
        tmp = self.report_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.report, indent=1, default=str))
        os.replace(tmp, self.report_path)

    # ------------------------------------------------------------- helpers
    def find_new_files(self, patterns: Iterable[str], since: float, root: str | Path | None = None) -> list[Path]:
        """Files under ``root`` (default job dir) matching patterns, modified after ``since``, newest first."""
        root = Path(root) if root else self.job_dir
        found = []
        for dirpath, _dirs, files in os.walk(root):
            for fn in files:
                p = Path(dirpath) / fn
                if any(fnmatch.fnmatch(fn, pat) for pat in patterns):
                    try:
                        if p.stat().st_mtime >= since - 1:
                            found.append(p)
                    except OSError:
                        continue
        return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)

    def link_input(self, src: str | Path, name: str | None = None) -> Path:
        """Symlink (or copy as fallback) an input file into the job directory."""
        src = Path(src)
        dst = self.path(name or src.name)
        if dst.exists() or dst.is_symlink():
            dst.unlink()
        try:
            dst.symlink_to(src.resolve())
        except OSError:
            import shutil
            shutil.copy2(src, dst)
        return dst

    @staticmethod
    def hostname() -> str:
        return socket.gethostname()


def first_existing(paths: Iterable[str | Path]) -> Path | None:
    for p in paths:
        if p and Path(p).exists():
            return Path(p)
    return None
