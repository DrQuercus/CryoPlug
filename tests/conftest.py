"""Shared fixtures: synthetic maps/models, fake external programs and a manager with a local lane."""
from __future__ import annotations

import math
import os
import stat
import sys
import time
from pathlib import Path

import gemmi
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
os.environ["PYTHONPATH"] = str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", "")
sys.path.insert(0, str(ROOT))

from cryoplug.config import Config, LaneConfig, ToolConfig  # noqa: E402
from cryoplug.manager import Manager  # noqa: E402
from cryoplug.mrc import MapVolume, model_map  # noqa: E402
from cryoplug.scheduler import Scheduler  # noqa: E402

SEQUENCE = "MKTAYIAKQRQISFVKSHFSRQ"


def build_helix(n_res: int = len(SEQUENCE), center=(24.0, 24.0, 24.0), chain_name: str = "A") -> gemmi.Structure:
    """An idealised poly-peptide alpha-helix (good enough for map/model tests)."""
    st = gemmi.Structure()
    st.name = "helix"
    model = gemmi.Model(1)
    chain = gemmi.Chain(chain_name)
    names3 = gemmi.expand_one_letter_sequence(SEQUENCE[:n_res], gemmi.ResidueKind.AA)
    z0 = center[2] - n_res * 1.5 / 2
    for i in range(n_res):
        res = gemmi.Residue()
        res.name = names3[i]
        res.seqid = gemmi.SeqId(i + 1, " ")
        res.entity_type = gemmi.EntityType.Polymer
        ang = math.radians(100 * i)
        for name, el, r, dang, dz in (("N", "N", 1.55, -28, -0.45), ("CA", "C", 2.3, 0, 0.0), ("C", "C", 1.65, 28, 0.55),
                                      ("O", "O", 1.9, 40, 1.6), ("CB", "C", 3.3, 10, -0.4)):
            a = gemmi.Atom()
            a.name = name
            a.element = gemmi.Element(el)
            a.pos = gemmi.Position(center[0] + r * math.cos(ang + math.radians(dang)),
                                   center[1] + r * math.sin(ang + math.radians(dang)), z0 + 1.5 * i + dz)
            a.b_iso = 30.0 + i
            a.occ = 1.0
            res.add_atom(a)
        chain.add_residue(res)
    model.add_chain(chain)
    st.add_model(model)
    st.setup_entities()
    return st


@pytest.fixture(scope="session")
def synthetic(tmp_path_factory) -> dict[str, Path]:
    """Model, maps and a fake CryoSPARC job directory."""
    root = tmp_path_factory.mktemp("data")
    st = build_helix()
    model_path = root / "helix.pdb"
    st.write_pdb(str(model_path))
    xyz = np.array([[a.pos.x, a.pos.y, a.pos.z] for r in st[0][0] for a in r])
    weights = np.array([a.element.atomic_number for r in st[0][0] for a in r], dtype=float)
    like = MapVolume(data=np.zeros((48, 48, 48), np.float32), voxel=np.array([1.0, 1.0, 1.0]))
    clean = model_map(xyz, weights, like, 3.0)
    clean /= clean.max()
    rng = np.random.default_rng(0)
    cs = root / "CS-test" / "J12"
    cs.mkdir(parents=True)
    half_a = like.like(clean + rng.normal(0, 0.15, clean.shape).astype(np.float32))
    half_b = like.like(clean + rng.normal(0, 0.15, clean.shape).astype(np.float32))
    full = like.like((half_a.data + half_b.data) / 2)
    mask = like.like((model_map(xyz, np.ones(len(xyz)), like, 8.0) > 0.05).astype(np.float32))
    half_a.write(cs / "J12_005_volume_map_half_A.mrc")
    half_b.write(cs / "J12_005_volume_map_half_B.mrc")
    full.write(cs / "J12_005_volume_map.mrc")
    full.write(cs / "J12_005_volume_map_sharp.mrc")
    mask.write(cs / "J12_005_volume_mask_fsc_auto.mrc")
    # older iteration that must be ignored
    full.write(cs / "J12_003_volume_map_sharp.mrc")
    os.utime(cs / "J12_003_volume_map_sharp.mrc", (time.time() + 100, time.time() + 100))
    (root / "seq.fasta").write_text(f">helix\n{SEQUENCE}\n")
    return {"root": root, "model": model_path, "cryosparc_job": cs, "map": cs / "J12_005_volume_map_sharp.mrc",
            "half_a": cs / "J12_005_volume_map_half_A.mrc", "half_b": cs / "J12_005_volume_map_half_B.mrc",
            "mask": cs / "J12_005_volume_mask_fsc_auto.mrc", "fasta": root / "seq.fasta"}


def _script(path: Path, body: str) -> None:
    path.write_text("#!/bin/bash\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture(scope="session")
def fakebin(tmp_path_factory, synthetic) -> Path:
    """Fake external programs mimicking the CLI and outputs of the real tools."""
    d = tmp_path_factory.mktemp("fakebin")
    model = synthetic["model"]
    _script(d / "model_angelo", f"""
# model_angelo build -v map -pf fasta -o out --device 0
echo "fake ModelAngelo $@"
out=""
while [ $# -gt 0 ]; do case "$1" in -o) out="$2"; shift;; esac; shift; done
mkdir -p "$out"
{sys.executable} -c "import gemmi; gemmi.read_structure('{model}').make_mmcif_document().write_file('$out/$out.cif')"
""")
    _script(d / "phenix.real_space_refine", f"""
echo "fake phenix.real_space_refine $@"
cp "{model}" ./helix_real_space_refined_000.pdb
cat <<EOF
Final:
  Model vs map:
    CC_mask  : 0.8123
    CC_box   : 0.7012
  MolProbity statistics.
    All-atom clashscore : 4.56
    Ramachandran plot:
      outliers :  0.00 %
      allowed  :  2.10 %
      favored  : 97.90 %
    Rotamer outliers : 0.40 %
  MolProbity score : 1.42
EOF
""")
    _script(d / "phenix.version", "echo 'Phenix fake 1.21'\n")
    _script(d / "locscale", """
echo "fake locscale $@"
out=""; hm=""
while [ $# -gt 0 ]; do case "$1" in -o) out="$2"; shift;; -hm) hm="$2"; shift; shift;; esac; shift; done
cp "$hm" "$out"
""")
    return d


@pytest.fixture()
def manager(tmp_path, fakebin) -> Manager:
    cfg = Config(
        data_dir=tmp_path / "data",
        projects_root=tmp_path / "projects",
        browse_roots=[str(tmp_path.parent), "/tmp"],
        lanes=[LaneConfig(name="local", type="local", max_jobs=4, gpus=[0, 1])],
        tools={k: ToolConfig(name=k, bin_dir=str(fakebin)) for k in ("modelangelo", "phenix", "locscale")},
    )
    m = Manager(cfg)
    m.check_tools()
    return m


def run_until_done(manager: Manager, puid: str, juids: list[str], timeout: float = 120.0) -> None:
    sched = Scheduler(manager)
    end = time.time() + timeout
    while time.time() < end:
        sched.tick()
        jobs = [manager.job(puid, j) for j in juids]
        if all(j["status"] in ("completed", "failed", "killed", "waiting")
               or (j["status"] == "queued" and j["message"].startswith("Blocked")) for j in jobs):
            return
        time.sleep(0.3)
    raise TimeoutError(f"Jobs did not finish: {[(j, manager.job(puid, j)['status']) for j in juids]}")


def log_of(manager: Manager, puid: str, juid: str) -> str:
    return manager.read_log(puid, juid)["text"]
