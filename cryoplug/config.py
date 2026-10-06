"""Configuration loading.

CryoPlug reads a single TOML file (``~/.cryoplug/config.toml`` by default, or
the path in ``$CRYOPLUG_CONFIG``).  Every key is optional; missing keys fall
back to the defaults defined here.  ``cryoplug init`` writes a commented
example file.
"""
from __future__ import annotations

import os
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


@dataclass
class LaneConfig:
    """A compute lane: the local machine or a cluster queue."""

    name: str = "local"
    type: str = "local"  # "local" or "cluster"
    description: str = ""
    max_jobs: int = 2  # concurrent jobs on this lane
    gpus: list[int] = field(default_factory=list)  # GPU ids managed on a local lane ([] = unmanaged)
    submit_cmd: str = "sbatch {script}"
    status_cmd: str = "squeue -h -j {cluster_job_id} -o %T"
    kill_cmd: str = "scancel {cluster_job_id}"
    script_template: str = DEFAULT_SLURM_TEMPLATE
    python: str = ""  # python interpreter used to start the worker (default: the server's)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Config:
    host: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    data_dir: Path = Path("~/.cryoplug").expanduser()
    projects_root: Path = Path("~/cryoplug_projects").expanduser()
    browse_roots: list[str] = field(default_factory=lambda: ["/"])
    username: str = "cryoplug"
    password: str = ""
    display: str = ""  # X display used to launch Coot / ChimeraX-ISOLDE on the server
    molstar_js: str = ""
    molstar_css: str = ""
    lanes: list[LaneConfig] = field(default_factory=lambda: [LaneConfig()])
    tools: dict[str, ToolConfig] = field(default_factory=dict)
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
    cfg.username = str(server.get("username", cfg.username))
    cfg.password = str(server.get("password", cfg.password))

    cfg.display = str(data.get("interactive", {}).get("display", os.environ.get("DISPLAY", "")))

    viewer = data.get("viewer", {})
    cfg.molstar_js = str(viewer.get("molstar_js", ""))
    cfg.molstar_css = str(viewer.get("molstar_css", ""))

    lanes = []
    for raw in data.get("lanes", []):
        lane = LaneConfig()
        for key in ("name", "type", "description", "submit_cmd", "status_cmd", "kill_cmd", "script_template", "python"):
            if key in raw:
                setattr(lane, key, str(raw[key]))
        if "max_jobs" in raw:
            lane.max_jobs = max(1, int(raw["max_jobs"]))
        if "gpus" in raw:
            lane.gpus = [int(g) for g in raw["gpus"]]
        if lane.type not in ("local", "cluster"):
            raise ValueError(f"Lane '{lane.name}': type must be 'local' or 'cluster'")
        lanes.append(lane)
    if lanes:
        cfg.lanes = lanes

    cfg.tools = {name: ToolConfig.from_dict(name, raw) for name, raw in data.get("tools", {}).items()}
    return cfg


EXAMPLE_CONFIG = f"""# CryoPlug configuration file.
# Every key is optional. Restart the server after editing.

[server]
# Use "0.0.0.0" to expose CryoPlug on the local network (then set a password!).
host = "127.0.0.1"
port = {DEFAULT_PORT}
# Database, cached tool checks and the downloaded 3D viewer live here.
data_dir = "~/.cryoplug"
# Default parent directory for new projects.
projects_root = "~/cryoplug_projects"
# Directories visible in the file browser (import of CryoSPARC jobs, maps, models...).
browse_roots = ["/"]
# Optional HTTP basic authentication.
# username = "cryoplug"
# password = "change-me"

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

# Example SLURM lane. The project directories must be on a shared filesystem.
# [[lanes]]
# name = "slurm-gpu"
# type = "cluster"
# max_jobs = 20
# Placeholders: {{script}} {{cluster_job_id}} {{project_uid}} {{job_uid}} {{job_type}} {{job_dir}}
#               {{num_gpus}} {{num_cpus}} {{worker_cmd}}. Other braces (e.g. ${{SLURM_JOB_ID}}) are kept.
# submit_cmd = "sbatch {{script}}"
# status_cmd = "squeue -h -j {{cluster_job_id}} -o %T"
# kill_cmd = "scancel {{cluster_job_id}}"
# script_template = '''#!/bin/bash
# #SBATCH --job-name=cryoplug_{{project_uid}}_{{job_uid}}
# #SBATCH --output={{job_dir}}/cluster_stdout.log
# #SBATCH --cpus-per-task={{num_cpus}}
# #SBATCH --gres=gpu:{{num_gpus}}
# #SBATCH --partition=gpu
# {{worker_cmd}}
# '''

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
