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
    write_cif = f"{sys.executable} -c \"import gemmi,sys; gemmi.read_structure('{model}').make_mmcif_document().write_file(sys.argv[1])\""
    _script(d / "model_angelo", f"""
# model_angelo build|build_no_seq -v map [-pf fasta] -o out --device 0  |  model_angelo hmm_search -i dir -f db -o out
echo "fake ModelAngelo $@"
cmd="$1"; out=""
while [ $# -gt 0 ]; do case "$1" in -o) out="$2"; shift;; esac; shift; done
mkdir -p "$out"
name="$(basename "$out")"  # like ModelAngelo: files are named after the output folder
if [ "$cmd" = "hmm_search" ]; then
  printf 'target_name,query_name,accession,E-value,score,bias,description\nsp|P0TEST|HELIX_TEST,A,,1e-30,250.0,0.1,Test helix protein\ntr|Q0WEAK|WEAK,B,,0.5,12.0,0.0,Weak hit\n' > "$out/best_hits.csv"
  cp "$out/best_hits.csv" "$out/all_hits.csv"
  exit 0
fi
{write_cif} "$out/$name.cif"
[ "$cmd" = "build_no_seq" ] && mkdir -p "$out/hmm_profiles" && touch "$out/hmm_profiles/A.hmm"
exit 0
""")
    _script(d / "cryoatom", f"""
# cryoatom build -v map [-ps p.fasta] [-pf db] -o out -d 0
echo "fake CryoAtom2 $@"
out=""; db=""
while [ $# -gt 0 ]; do case "$1" in -o) out="$2"; shift;; -pf) db="$2"; shift;; esac; shift; done
mkdir -p "$out"
name="$(basename "$out")"
{write_cif} "$out/$name.cif"
cp "$out/$name.cif" "$out/${{name}}_raw.cif"
[ -n "$db" ] && head -2 "$db" > "$out/${{name}}_prot.fasta"
exit 0
""")
    _script(d / "boltz", f"""
# boltz predict input.yaml --out_dir out ...
echo "fake boltz $@"
cat "$2"
out=""
while [ $# -gt 0 ]; do case "$1" in --out_dir) out="$2"; shift;; esac; shift; done
p="$out/boltz_results_boltz_input/predictions/boltz_input"
mkdir -p "$p"
{write_cif} "$p/boltz_input_model_0.cif"
echo '{{"confidence_score": 0.83, "ptm": 0.81, "iptm": 0.77, "complex_plddt": 0.86}}' > "$p/confidence_boltz_input_model_0.json"
""")
    _script(d / "spisonet.py", """
echo "fake spisonet $@"
if [ "$1" = "fsc3d" ]; then cp "$2" FSC3D.mrc; exit 0; fi
h1="$2"; h2="$3"; od=isonet_maps
while [ $# -gt 0 ]; do case "$1" in --output_dir) od="$2"; shift;; esac; shift; done
mkdir -p "$od"
cp "$(readlink -f "$h1")" "$od/corrected_half_map_1.mrc"
cp "$(readlink -f "$h2")" "$od/corrected_half_map_2.mrc"
""")
    _script(d / "phenix.douse", f"""
echo "fake phenix.douse $@"
{sys.executable} - <<PY
import gemmi
st = gemmi.read_structure('{model}')
ch = gemmi.Chain('W')
for i in range(3):
    r = gemmi.Residue(); r.name = 'HOH'; r.seqid = gemmi.SeqId(i + 1, ' '); r.het_flag = 'H'
    a = gemmi.Atom(); a.name = 'O'; a.element = gemmi.Element('O'); a.pos = gemmi.Position(30 + i, 30, 30); a.occ = 1; a.b_iso = 30
    r.add_atom(a); ch.add_residue(r)
st[0].add_chain(ch)
st.write_pdb('helix_douse_000.pdb')
PY
""")
    _script(d / "phenix.elbow", """
echo "fake phenix.elbow $@"
name=""
for a in "$@"; do case "$a" in --output=*) name="${a#--output=}";; esac; done
printf 'data_comp_list\nloop_\n_chem_comp.id\n%s\n' "$name" > "$name.cif"
printf 'HETATM    1  C1  %s A   1       0.000   0.000   0.000  1.00 20.00           C\nEND\n' "$name" > "$name.pdb"
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
    # local resolution worsening from 2.5 Å at the centre of the box towards the edges
    _script(d / "phenix.local_resolution", f"""
echo "fake phenix.local_resolution $@"
{sys.executable} - "$1" <<PY
import sys
import numpy as np
sys.path.insert(0, '{ROOT}')
from cryoplug.mrc import MapVolume
half = MapVolume.read(sys.argv[1])
z, y, x = np.indices(half.data.shape)
c = (np.array(half.data.shape) - 1) / 2
r = np.sqrt((z - c[0]) ** 2 + (y - c[1]) ** 2 + (x - c[2]) ** 2) * half.pixel_size
half.like(2.5 + 0.12 * r).write('local_resolution_map.ccp4')
PY
echo "Wrote local_resolution_map.ccp4"
""")
    # combine_focused_maps: average of the maps (and of the half maps), one contribution map per input
    _script(d / "phenix.combine_focused_maps", f"""
echo "fake phenix.combine_focused_maps $@"
{sys.executable} - "$@" <<PY
import sys
import numpy as np
sys.path.insert(0, '{ROOT}')
from cryoplug.mrc import MapVolume
opts = {{}}
for a in sys.argv[1:]:
    if '=' in a:
        k, v = a.split('=', 1)
        opts.setdefault(k, []).append(v)
def mean(paths, name):
    vols = [MapVolume.read(p) for p in paths]
    vols[0].like(sum(v.data for v in vols) / len(vols)).write(name)
mean(opts['map_file'], 'combined_map.ccp4')
for i, p in enumerate(opts['map_file'], 1):
    MapVolume.read(p).write(f'contribution_map_{{i}}.ccp4')
if 'half_map_1_file' in opts:
    mean(opts['half_map_1_file'], 'combined_half_map_A.ccp4')
    mean(opts['half_map_2_file'], 'combined_half_map_B.ccp4')
PY
""")
    # checkMySequence: one register shift in chain A (JSON written where --jsonout says, PDF plot in the cwd)
    _script(d / "checkmysequence", """
echo "fake checkMySequence $@"
out=""; plot=0; model=""
while [ $# -gt 0 ]; do case "$1" in --jsonout) out="$2"; shift;; --modelin) model="$2"; shift;; --plot) plot=1;; esac; shift; done
[ -n "$out" ] || exit 0
cat > "$out" <<'JSON'
{"clean_report": false, "indexing_issues": {}, "unidentified_chains": {}, "sequence_mismatches": {}, "tracing_issues": {},
 "register_shifts": {"protein": [{"chain_id_reference": "A", "resid_start_reference": 5, "resid_end_reference": 18,
   "resid_start_new": 7, "resid_end_new": 20, "shift": 2, "mlogpv": 1.42, "tracing_issues": false, "si": 100.0,
   "model_seq": "kqrQISFVKSHFSrq", "new_seq": "kqrqiSFVKSHFSRQ"}]},
 "raw_results": {"protein": {"A": [{"s": 1, "e": 20, "mlogpv": 0.9, "match": true, "error": false},
                                   {"s": 3, "e": 22, "mlogpv": 1.42, "match": false, "error": true}]}}}
JSON
[ "$plot" = 1 ] && printf '%%PDF-1.4\n' > "$(basename "${model%.*}")_plot.pdf"
echo " ==> Output wrote to $out"
""")
    # wwPDB OneDep validation client: session file, uploads, status, downloads
    _script(d / "onedep_validate_cli", """
echo "fake onedep $@"
sess=""; out=""; otype=""
while [ $# -gt 0 ]; do case "$1" in
  --session_file) sess="$2"; shift;;
  --new_session) echo "fake-session-123" > "$sess";;
  --input_file) if [ ! -s "$2" ]; then echo "OneDep error: Input file access or processing error"; exit 0; fi; shift;;
  --status) echo "OneDep status: completed";;
  --output_file) out="$2"; shift;;
  --output_type) otype="$2"; shift;;
esac; shift; done
[ -s "$sess" ] || { echo "Error reading session file"; exit 1; }
if [ -n "$out" ]; then case "$otype" in
  validation-report-full) printf '%%PDF-1.4\n%% fake wwPDB report\n' > "$out";;
  validation-data) cat > "$out" <<'XML'
<?xml version="1.0" encoding="UTF-8"?>
<wwPDB-validation-information>
  <Entry clashscore="3.21" absolute-percentile-clashscore="92.0" relative-percentile-clashscore="95.1"
         percent-rama-outliers="0.10" absolute-percentile-percent-rama-outliers="81" relative-percentile-percent-rama-outliers="83"
         percent-rota-outliers="1.80" absolute-percentile-percent-rota-outliers="15" relative-percentile-percent-rota-outliers="22"
         atom_inclusion_all_atoms="0.85" EMDB-resolution="3.2"/>
  <ModelledSubgroup chain="A" resnum="12" resname="LEU" rama="OUTLIER" rota="OUTLIER"><clash atom="CD1" clashmag="0.5"/></ModelledSubgroup>
  <ModelledSubgroup chain="A" resnum="13" resname="ALA" rama="Favored"/>
</wwPDB-validation-information>
XML
  ;;
  validation-data-cif) echo "data_validation" > "$out";;
  validation-report-slider) echo '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="10"><rect width="40" height="10"/></svg>' > "$out";;
  validation-report-log) echo "validation finished" > "$out";;
esac; fi
exit 0
""")
    # Mimics LocScale 2.4.1 path handling: relative -o / -op are resolved against the folder of the first
    # input map (not the working directory), and the inputs are copied into the processing folder.
    _script(d / "locscale", """
echo "fake locscale $@"
mode=locscale
if [ "$1" = "feature_enhance" ]; then mode=emmernet; shift; fi
out=""; op=""; hm=""; em=""
while [ $# -gt 0 ]; do case "$1" in -o) out="$2"; shift;; -op) op="$2"; shift;; -hm) hm="$2"; shift; shift;; -em) em="$2"; shift;; esac; shift; done
src="${hm:-$em}"
indir="$(cd "$(dirname "$src")" && pwd)"
case "$op" in /*) ;; *) op="$indir/${op:-processing_files}";; esac
mkdir -p "$op"
cp "$src" "$op/$(basename "$src")"
case "$out" in /*) var="${out%.mrc}_variance.mrc";; *) var="$op/${out%.mrc}_variance.mrc"; out="$indir/$out";; esac
echo "Saving as MRC file: $out"
cp "$src" "$out"
if [ "$mode" = emmernet ]; then cp "$src" "$var"; fi
""")
    return d


@pytest.fixture()
def manager(tmp_path, fakebin) -> Manager:
    cfg = Config(
        data_dir=tmp_path / "data",
        projects_root=tmp_path / "projects",
        browse_roots=[str(tmp_path.parent), "/tmp"],
        lanes=[LaneConfig(name="local", type="local", max_jobs=4, gpus=[0, 1])],
        tools={k: ToolConfig(name=k, bin_dir=str(fakebin))
               for k in ("modelangelo", "phenix", "locscale", "cryoatom", "boltz", "spisonet", "checkmysequence", "onedep")},
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
