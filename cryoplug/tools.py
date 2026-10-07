"""External program definitions, detection and command wrapping."""
from __future__ import annotations

import os
import shlex
import subprocess
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from cryoplug.config import Config, ToolConfig


@dataclass
class ToolDef:
    key: str
    label: str
    executables: list[str]  # candidates for the main executable, in order of preference
    description: str
    homepage: str
    citation: str
    version_args: list[str] = field(default_factory=list)  # appended to the main executable
    gui: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


TOOLS: dict[str, ToolDef] = {
    t.key: t for t in [
        ToolDef("phenix", "Phenix", ["phenix.real_space_refine", "phenix.version"],
                "Density modification, sharpening, docking, real-space refinement and validation.",
                "https://phenix-online.org",
                "Liebschner D. et al. (2019) Acta Cryst. D75, 861-877."),
        ToolDef("modelangelo", "ModelAngelo", ["model_angelo"],
                "Automated de novo model building with graph neural networks.",
                "https://github.com/3dem/model-angelo",
                "Jamali K. et al. (2024) Nature 628, 450-457."),
        ToolDef("cryoatom", "CryoAtom2", ["cryoatom"],
                "Automated model building and sequence identification for proteins, nucleic acids and complexes.",
                "https://github.com/YangLab-SDU/CryoAtom",
                "Su B. et al. (2025) Nat. Struct. Mol. Biol., doi:10.1038/s41594-025-01713-3."),
        ToolDef("locscale", "LocScale 2 / EMmerNet", ["locscale"],
                "Local amplitude scaling (model-free, model-based, hybrid) and EMmerNet feature enhancement.",
                "https://github.com/cryoTUD/locscale",
                "Jakobi A.J., Wilmanns M., Sachse C. (2017) eLife 6, e27131; Bharadwaj A., Jakobi A.J. (2022) Faraday Discuss. 240, 168-183."),
        ToolDef("deepemhancer", "DeepEMhancer", ["deepemhancer"],
                "Deep-learning based map post-processing (masking-free sharpening).",
                "https://github.com/rsanchezgarc/deepEMhancer",
                "Sanchez-Garcia R. et al. (2021) Commun. Biol. 4, 874."),
        ToolDef("emready", "EMReady", ["emready", "EMReady.sh"],
                "Deep-learning map enhancement with local and non-local features.",
                "https://github.com/huang-laboratory/EMReady",
                "He J., Li T., Huang S.-Y. (2023) Nat. Commun. 14, 3217."),
        ToolDef("spisonet", "spIsoNet", ["spisonet.py"],
                "Self-supervised correction of preferred-orientation anisotropy from half maps (3D FSC guided).",
                "https://github.com/IsoNet-cryoET/spIsoNet",
                "Liu Y.-T. et al. (2025) Nat. Methods, doi:10.1038/s41592-024-02505-1."),
        ToolDef("servalcat", "Servalcat", ["servalcat"],
                "Refinement against half maps with REFMAC-like restraints, Fo-Fc maps, FSC.",
                "https://github.com/keitaroyam/servalcat",
                "Yamashita K. et al. (2021) Acta Cryst. D77, 1282-1291.", version_args=["--version"]),
        ToolDef("chimerax", "UCSF ChimeraX (+ ISOLDE)", ["chimerax", "ChimeraX"],
                "Rigid-body fitting, rendering and interactive rebuilding with ISOLDE.",
                "https://www.cgl.ucsf.edu/chimerax/",
                "Meng E.C. et al. (2023) Protein Sci. 32, e4792; Croll T.I. (2018) Acta Cryst. D74, 519-530 (ISOLDE).",
                version_args=["--version"], gui=True),
        ToolDef("coot", "Coot", ["coot"],
                "Interactive model building and validation.",
                "https://www2.mrc-lmb.cam.ac.uk/personal/pemsley/coot/",
                "Emsley P. et al. (2010) Acta Cryst. D66, 486-501.", version_args=["--version"], gui=True),
        ToolDef("colabfold", "ColabFold (local)", ["colabfold_batch"],
                "Local AlphaFold2 structure prediction to obtain starting models.",
                "https://github.com/YoshitakaMo/localcolabfold",
                "Mirdita M. et al. (2022) Nat. Methods 19, 679-682."),
        ToolDef("boltz", "Boltz-2", ["boltz"],
                "Open-source structure prediction of protein / nucleic-acid / ligand complexes (AlphaFold3-class).",
                "https://github.com/jwohlwend/boltz",
                "Passaro S. et al. (2025) Boltz-2, bioRxiv; Wohlwend J. et al. (2024) Boltz-1, bioRxiv."),
        ToolDef("checkmysequence", "checkMySequence", ["checkmysequence"],
                "Sequence-assignment validation: register shifts, unidentified chains, sequence mismatches.",
                "https://gitlab.com/gchojnowski/checkmysequence",
                "Chojnowski G. (2022) Acta Cryst. D78, 806-816.", version_args=["--version"]),
        ToolDef("onedep", "wwPDB validation client (OneDep API)", ["onedep_validate_cli"],
                "Official wwPDB validation report from the OneDep validation web service (pip install onedep_api). "
                "Needs internet access from the CryoPlug server.",
                "https://www.wwpdb.org/validation/onedep-validation-web-service-interface",
                "Gore S. et al. (2017) Structure 25, 1916-1927.", version_args=["--version"]),
        ToolDef("shell", "Shell (custom commands)", ["bash"],
                "Environment used by the Custom command job.", "", ""),
    ]
}


def main_executable(key: str, tool: dict[str, Any] | ToolConfig | None) -> str:
    """Main executable for a tool given its (job-spec) configuration."""
    data = tool.to_dict() if isinstance(tool, ToolConfig) else (tool or {})
    if data.get("executable"):
        return str(data["executable"])
    if data.get("resolved"):
        return str(data["resolved"])
    tdef = TOOLS.get(key)
    return tdef.executables[0] if tdef else key


def wrap_command(tool: dict[str, Any] | None, args: list[str], name: str = "tool") -> list[str]:
    """Run ``args`` inside bash after the tool's setup lines (source/conda/module)."""
    tool = tool or {}
    lines = ["set -e"]
    if tool.get("bin_dir"):
        lines.append(f'export PATH={shlex.quote(str(tool["bin_dir"]))}:"$PATH"')
    if tool.get("setup"):
        lines.append("set +e")  # activation scripts often return non-zero harmlessly
        lines.append(str(tool["setup"]))
        lines.append("set -e")
    lines.append('exec "$@"')
    return ["bash", "-c", "\n".join(lines), f"cryoplug-{name}", *args]


def tool_env(tool: dict[str, Any] | None, base: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    for k, v in (tool or {}).get("env", {}).items():
        env[str(k)] = os.path.expandvars(str(v))
    return env


def check_tool(key: str, cfg: ToolConfig, timeout: float = 30.0) -> dict[str, Any]:
    """Look for the tool's executable inside its configured environment."""
    tdef = TOOLS.get(key)
    result: dict[str, Any] = {"key": key, "status": "missing", "path": "", "version": "", "message": "", "checked_at": time.time()}
    if not cfg.enabled:
        result.update(status="disabled", message="Disabled in configuration")
        return result
    candidates = [cfg.executable] if cfg.executable else (tdef.executables if tdef else [key])
    tool = cfg.to_dict()
    for exe in candidates:
        try:
            proc = subprocess.run(wrap_command(tool, ["bash", "-c", 'command -v "$1"', "_", exe], key),
                                  capture_output=True, text=True, timeout=timeout, env=tool_env(tool))
        except subprocess.TimeoutExpired:
            result["message"] = "Environment setup timed out"
            return result
        except OSError as exc:  # bash missing
            result["message"] = str(exc)
            return result
        path = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
        if proc.returncode == 0 and path:
            result.update(status="found", path=path)
            if not cfg.executable:
                result["resolved"] = path if os.path.isabs(path) else exe
            break
        if proc.stderr.strip():
            result["message"] = proc.stderr.strip().splitlines()[-1][:300]
    if result["status"] == "found" and tdef and tdef.version_args:
        try:
            proc = subprocess.run(wrap_command(tool, [result["path"], *tdef.version_args], key), capture_output=True,
                                  text=True, timeout=timeout, env=tool_env(tool))
            text = (proc.stdout + proc.stderr).strip().splitlines()
            if text:
                result["version"] = text[0][:120]
        except (subprocess.TimeoutExpired, OSError):
            pass
    elif result["status"] == "missing" and not result["message"]:
        result["message"] = f"None of {', '.join(candidates)} found on PATH"
    return result


def check_all(config: Config) -> dict[str, dict[str, Any]]:
    return {key: check_tool(key, config.tool(key)) for key in TOOLS}


def job_tool_spec(config: Config, key: str, status: dict[str, Any] | None) -> dict[str, Any]:
    """Tool configuration as embedded in a job spec (worker side has no config file)."""
    spec = config.tool(key).to_dict()
    if status and status.get("resolved") and not spec.get("executable"):
        spec["resolved"] = status["resolved"]
    return spec
