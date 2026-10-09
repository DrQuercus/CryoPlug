"""Configuration loading.

CryoPlug reads a single TOML file (``~/.cryoplug/config.toml`` by default, or
the path in ``$CRYOPLUG_CONFIG``).  Every key is optional; missing keys fall
back to the defaults defined here.  ``cryoplug init`` writes a commented
example file.
"""
from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

try:  # Python >= 3.11
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

DEFAULT_PORT = 39500
DEFAULT_CONFIG_PATH = Path("~/.cryoplug/config.toml")
MOLSTAR_VERSION = "5.13.0"

DEFAULT_SLURM_TEMPLATE = """#!/bin/bash
#SBATCH --job-name=cryoplug_{project_uid}_{job_uid}
#SBATCH --output={job_dir}/cluster_stdout.log
#SBATCH --error={job_dir}/cluster_stdout.log
#SBATCH --cpus-per-task={num_cpus}
#SBATCH --gres=gpu:{num_gpus}
#SBATCH --time=48:00:00

{worker_cmd}
"""


@dataclass
class ToolConfig:
    """How to run one external program family (phenix, model_angelo, ...)."""

    name: str
    setup: str = ""  # shell lines run before the command (source env, conda activate, module load)
    bin_dir: str = ""  # prepended to PATH
    executable: str = ""  # override of the main executable (name or absolute path)
    env: dict[str, str] = field(default_factory=dict)
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> "ToolConfig":
        return cls(
            name=name,
            setup=str(data.get("setup", "")),
            bin_dir=str(data.get("bin_dir", "")),
            executable=str(data.get("executable", "")),
            env={str(k): str(v) for k, v in dict(data.get("env", {})).items()},
            enabled=bool(data.get("enabled", True)),
        )


LANE_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,39}")
SLURM_WORD_RE = re.compile(r"[A-Za-z0-9._,:+-]*")
SLURM_TIME_RE = re.compile(r"(\d+-)?\d+(:\d{1,2}){0,2}|UNLIMITED|INFINITE", re.IGNORECASE | re.ASCII)
SLURM_MEM_RE = re.compile(r"\d+[KMGT]?", re.IGNORECASE | re.ASCII)  # sbatch --mem: a whole number and a unit


@dataclass
class LaneConfig:
    """A compute lane: the local machine or a cluster queue (like CryoSPARC lanes).

    Cluster lanes are written for SLURM with simple fields (partition, time, memory, GPU resource...) from which
    the submission script is generated; a custom ``script_template`` replaces it (other schedulers)."""

    name: str = "local"
    type: str = "local"  # "local" or "cluster"
    description: str = ""
    max_jobs: int = 2  # concurrent jobs on this lane
    gpus: list[int] = field(default_factory=list)  # GPU ids managed on a local lane ([] = unmanaged)
    # cluster (SLURM) lanes
    partition: str = ""  # default partition (--partition)
    partitions: list[str] = field(default_factory=list)  # partitions users may pick per job ([] = the default only)
    account: str = ""  # --account
    qos: str = ""  # --qos
    time_limit: str = "48:00:00"  # default --time per job
    mem: str = ""  # default --mem per job ("" = cluster default)
    gres: str = "gpu"  # GPU resource: --gres=<gres>:<n> ("gpu", "gpu:a100"...)
    extra_sbatch: str = ""  # more #SBATCH lines (e.g. --constraint=a100)
    setup: str = ""  # shell lines run before the worker (module load, conda activate...)
    submit_cmd: str = "sbatch {script}"
    status_cmd: str = "squeue -h -j {cluster_job_id} -o %T"
    kill_cmd: str = "scancel {cluster_job_id}"
    script_template: str = ""  # "" = generated from the fields above
    python: str = ""  # python interpreter used to start the worker (default: the server's)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def custom_script(self) -> bool:
        return bool(self.script_template.strip()) and self.script_template.strip() != DEFAULT_SLURM_TEMPLATE.strip()

    @property
    def all_partitions(self) -> list[str]:
        """The partitions jobs may use: the default one first, then those users may pick."""
        return list(dict.fromkeys(p for p in [self.partition, *self.partitions] if p))

    def sbatch_lines(self) -> list[str]:
        """The extra #SBATCH lines, normalised (a bare option gets the #SBATCH prefix)."""
        lines = []
        for line in self.extra_sbatch.splitlines():
            line = line.strip()
            if line:
                lines.append(line if line.startswith("#SBATCH") else f"#SBATCH {line}")
        return lines

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LaneConfig":
        """A lane from a TOML table or the Settings page; raises ValueError with a readable message."""
        lane = cls()
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"Lane: unknown setting(s) {', '.join(sorted(unknown))}")
        for key in ("name", "type", "description", "partition", "account", "qos", "time_limit", "mem", "gres", "extra_sbatch",
                    "setup", "submit_cmd", "status_cmd", "kill_cmd", "script_template", "python"):
            if key in data and data[key] is not None:
                setattr(lane, key, str(data[key]).strip() if key not in ("extra_sbatch", "setup", "script_template") else str(data[key]))
        name = lane.name
        if not LANE_NAME_RE.fullmatch(name):
            raise ValueError(f"Lane name '{name}': letters, digits, '.', '_' or '-' (40 characters at most)")
        if lane.type not in ("local", "cluster"):
            raise ValueError(f"Lane '{name}': type must be 'local' or 'cluster'")
        if "max_jobs" in data:
            try:
                lane.max_jobs = int(data["max_jobs"])
            except (TypeError, ValueError):
                raise ValueError(f"Lane '{name}': max_jobs must be a whole number") from None
            if not 1 <= lane.max_jobs <= 10000:
                raise ValueError(f"Lane '{name}': max_jobs must be between 1 and 10000")
        if "gpus" in data:
            raw = data["gpus"]
            if isinstance(raw, str):
                raw = [g for g in re.split(r"[\s,]+", raw) if g]
            try:
                gpus = [int(g) for g in raw or []]
            except (TypeError, ValueError):
                raise ValueError(f"Lane '{name}': GPU ids are whole numbers (0, 1, ...)") from None
            if any(not 0 <= g <= 63 for g in gpus):
                raise ValueError(f"Lane '{name}': GPU ids go from 0 to 63")
            lane.gpus = sorted(set(gpus))
        if "partitions" in data:
            raw = data["partitions"]
            if isinstance(raw, str):
                raw = re.split(r"[\s,]+", raw)
            lane.partitions = list(dict.fromkeys(str(p).strip() for p in raw or [] if str(p).strip()))
        for key in ("partition", "account", "qos", "gres", *(["partitions"] if lane.partitions else [])):
            values = lane.partitions if key == "partitions" else [getattr(lane, key)]
            for value in values:
                if not SLURM_WORD_RE.fullmatch(value):
                    raise ValueError(f"Lane '{name}': invalid {key} '{value}'")
        if lane.time_limit and not SLURM_TIME_RE.fullmatch(lane.time_limit):
            raise ValueError(f"Lane '{name}': time limit as SLURM writes it (48:00:00, 2-00:00:00...)")
        if lane.mem and not SLURM_MEM_RE.fullmatch(lane.mem):
            raise ValueError(f"Lane '{name}': memory as 64G, 128000M...")
        if lane.type == "cluster":  # without them, jobs could not be submitted, followed or cancelled
            for key, placeholder, what in (("submit_cmd", "{script}", "submit"), ("status_cmd", "{cluster_job_id}", "status"),
                                           ("kill_cmd", "{cluster_job_id}", "cancel")):
                if placeholder not in getattr(lane, key):
                    raise ValueError(f"Lane '{name}': the {what} command needs {placeholder}")
        for line in lane.extra_sbatch.splitlines():
            line = line.strip()
            if line and not (line.startswith("#SBATCH") or line.startswith("-")):
                raise ValueError(f"Lane '{name}': extra #SBATCH lines are options (--constraint=a100); "
                                 f"put shell commands in Setup ({line})")
        return lane


@dataclass
class Config:
    host: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    data_dir: Path = Path("~/.cryoplug").expanduser()
    projects_root: Path = Path("~/cryoplug_projects").expanduser()
    browse_roots: list[str] = field(default_factory=lambda: ["/"])
    password: str = ""
    auth: str = "auto"  # "auto": login required when exposed on the network or a password is set; "always"
    ssl_certfile: str = ""
    ssl_keyfile: str = ""
    display: str = ""  # X display used to launch Coot / ChimeraX-ISOLDE on the server
    molstar_js: str = ""
    molstar_css: str = ""
    lanes: list[LaneConfig] = field(default_factory=lambda: [LaneConfig()])
    tools: dict[str, ToolConfig] = field(default_factory=dict)
    nvidia_smi: str = ""  # [monitor] nvidia_smi: path of nvidia-smi if not on PATH
    config_path: Path | None = None

    @property
    def db_path(self) -> Path:
        return self.data_dir / "cryoplug.sqlite"

    @property
    def viewer_dir(self) -> Path:
        return self.data_dir / "viewer"

    def lane(self, name: str | None) -> LaneConfig:
        for lane in self.lanes:
            if lane.name == name:
                return lane
        if name:
            raise KeyError(f"Unknown lane '{name}'")
        return self.lanes[0]

    def tool(self, name: str) -> ToolConfig:
        return self.tools.get(name) or ToolConfig(name=name)


def _path(value: Any, default: Path) -> Path:
    if not value:
        return default
    return Path(os.path.expandvars(str(value))).expanduser()


def load_config(path: str | os.PathLike | None = None) -> Config:
    """Load the configuration file; a missing file yields the defaults."""
    if path is None:
        path = os.environ.get("CRYOPLUG_CONFIG") or DEFAULT_CONFIG_PATH
    cfg_path = Path(path).expanduser().absolute()
    data: dict[str, Any] = {}
    if cfg_path.is_file():
        with open(cfg_path, "rb") as fh:
            data = tomllib.load(fh)

    server = data.get("server", {})
    cfg = Config(config_path=cfg_path if cfg_path.is_file() else None)
    cfg.host = str(server.get("host", cfg.host))
    cfg.port = int(server.get("port", cfg.port))
    cfg.data_dir = _path(server.get("data_dir"), cfg.data_dir)
    cfg.projects_root = _path(server.get("projects_root"), cfg.projects_root)
    cfg.browse_roots = [str(Path(p).expanduser()) for p in server.get("browse_roots", cfg.browse_roots)]
    cfg.password = str(server.get("password", cfg.password))
    cfg.auth = str(server.get("auth", cfg.auth))
    if cfg.auth not in ("auto", "always"):
        raise ValueError('[server] auth must be "auto" or "always"')
    for key in ("ssl_certfile", "ssl_keyfile"):
        if server.get(key):
            setattr(cfg, key, str(_path(server[key], Path())))

    cfg.display = str(data.get("interactive", {}).get("display", os.environ.get("DISPLAY", "")))

    viewer = data.get("viewer", {})
    cfg.molstar_js = str(viewer.get("molstar_js", ""))
    cfg.molstar_css = str(viewer.get("molstar_css", ""))

    lanes = [LaneConfig.from_dict(dict(raw)) for raw in data.get("lanes", [])]
    if lanes:
        cfg.lanes = lanes

    monitor = data.get("monitor", {})
    cfg.nvidia_smi = str(monitor.get("nvidia_smi", ""))

    cfg.tools = {name: ToolConfig.from_dict(name, raw) for name, raw in data.get("tools", {}).items()}
    return cfg


EXAMPLE_CONFIG = f"""# CryoPlug configuration file.
# Every key is optional. Restart the server after editing.

[server]
# "127.0.0.1": only this machine can open the interface (or through an SSH tunnel).
# "0.0.0.0": reachable from other computers (laptop...) at http://<this-server>:{DEFAULT_PORT}.
# A login is then required. Best: personal accounts, as in CryoSPARC (Settings > Users in
# the interface, or `cryoplug user add NAME --admin`): as soon as one exists, everyone logs
# in with their user name and password, on this machine too. Without accounts, a random
# access token is printed at start-up (and by `cryoplug url`), or set a password below.
host = "127.0.0.1"
port = {DEFAULT_PORT}
# Database, cached tool checks and the downloaded 3D viewer live here.
data_dir = "~/.cryoplug"
# Default parent directory for new projects (with accounts: <projects_root>/<user name>,
# unless an administrator sets another projects folder for the user).
projects_root = "~/cryoplug_projects"
# Directories visible in the file browser for administrators (import of CryoSPARC jobs,
# maps, models...). Other users see their own folders, set in Settings > Users.
browse_roots = ["/"]
# Shared password for the login page, without accounts (replaces the access token).
# password = "change-me"
# "always" also requires the login on 127.0.0.1 (shared workstations).
# auth = "auto"
# HTTPS (e.g. a self-signed certificate, see the README).
# ssl_certfile = "~/.cryoplug/cert.pem"
# ssl_keyfile = "~/.cryoplug/key.pem"

[interactive]
# X display on which Coot and ChimeraX/ISOLDE are opened when launched from the
# browser (e.g. ":0" for the workstation screen or ":1" for a TurboVNC session).
# display = ":0"

[viewer]
# Mol* is served locally after `cryoplug fetch-viewer`; otherwise these URLs are used.
# molstar_js = "https://cdn.jsdelivr.net/npm/molstar@{MOLSTAR_VERSION}/build/viewer/molstar.js"
# molstar_css = "https://cdn.jsdelivr.net/npm/molstar@{MOLSTAR_VERSION}/build/viewer/molstar.css"

# ---------------------------------------------------------------------------
# Compute lanes (like CryoSPARC lanes).
# ---------------------------------------------------------------------------
[[lanes]]
name = "local"
type = "local"
description = "This workstation"
max_jobs = 2
# GPUs handed out to GPU jobs (CUDA_VISIBLE_DEVICES). Empty list = unmanaged.
gpus = [0]

# Lanes (and their GPUs) can also be edited in Settings > Compute: they are then
# saved in CryoPlug's database and take precedence over the [[lanes]] below.
#
# Example SLURM lane. The project directories must be on a shared filesystem.
# The submission script is generated from these fields (users may pick another
# partition, time limit or memory per job among those allowed).
# [[lanes]]
# name = "slurm-gpu"
# type = "cluster"
# description = "GPU partition of the cluster"
# max_jobs = 20
# partition = "gpu"
# partitions = ["gpu", "gpu-long"]
# account = ""
# qos = ""
# time_limit = "48:00:00"
# mem = "64G"
# gres = "gpu"            # --gres=gpu:<n>; "gpu:a100" for a GPU model
# extra_sbatch = "--constraint=a100"
# setup = "module load cuda/12.2"
# Fully custom script instead (other schedulers). Placeholders: {{script}} {{cluster_job_id}}
# {{project_uid}} {{job_uid}} {{job_type}} {{job_dir}} {{num_gpus}} {{num_cpus}} {{partition}} {{time}}
# {{mem}} {{account}} {{qos}} {{gres}} {{setup}} {{worker_cmd}}. Other braces (e.g. ${{SLURM_JOB_ID}}) are kept.
# submit_cmd = "sbatch {{script}}"
# status_cmd = "squeue -h -j {{cluster_job_id}} -o %T"
# kill_cmd = "scancel {{cluster_job_id}}"
# script_template = '''#!/bin/bash
# #SBATCH --job-name=cryoplug_{{project_uid}}_{{job_uid}}
# #SBATCH --output={{job_dir}}/cluster_stdout.log
# #SBATCH --cpus-per-task={{num_cpus}}
# #SBATCH --gres=gpu:{{num_gpus}}
# #SBATCH --partition={{partition}}
# {{worker_cmd}}
# '''

# [monitor]
# nvidia_smi = "/usr/bin/nvidia-smi"   # if nvidia-smi is not on the PATH of the server

# ---------------------------------------------------------------------------
# External programs. For each tool:
#   setup      shell lines executed before the program (source/conda/module)
#   bin_dir    directory prepended to PATH
#   executable name or absolute path of the main executable
#   env        extra environment variables
# Run `cryoplug tools` (or open the Tools page) to check what is detected.
# ---------------------------------------------------------------------------
# [tools.phenix]
# setup = "source /opt/phenix-1.21.2/phenix_env.sh"
#
# [tools.modelangelo]
# setup = "source ~/miniconda3/etc/profile.d/conda.sh && conda activate model_angelo"
#
# [tools.cryoatom]
# setup = "source ~/miniconda3/etc/profile.d/conda.sh && conda activate CryoAtom2"
#
# [tools.locscale]
# setup = "source ~/miniconda3/etc/profile.d/conda.sh && conda activate locscale"
#
# [tools.spisonet]
# setup = "source ~/miniconda3/etc/profile.d/conda.sh && conda activate spisonet"
#
# [tools.boltz]
# setup = "source ~/miniconda3/etc/profile.d/conda.sh && conda activate boltz"
#
# [tools.deepemhancer]
# setup = "source ~/miniconda3/etc/profile.d/conda.sh && conda activate deepEMhancer_env"
#
# [tools.emready]
# setup = "source ~/miniconda3/etc/profile.d/conda.sh && conda activate emready"
#
# [tools.servalcat]
# setup = "source /opt/ccpem/setup_ccpem.sh"
#
# [tools.chimerax]
# executable = "/usr/bin/chimerax"
#
# [tools.coot]
# executable = "/opt/coot/bin/coot"
#
# [tools.colabfold]
# bin_dir = "/opt/localcolabfold/colabfold-conda/bin"
"""
