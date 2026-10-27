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


@pytest.fixture(scope="session")
def cs_particles(tmp_path_factory) -> dict[str, Path]:
    """A small CryoSPARC project: particle stacks, a refinement job (particles + shuffled passthrough), a 3D
    variability job (particles with components) and a 3D variability display job (volume series)."""
    root = tmp_path_factory.mktemp("cs") / "CS-het"
    stacks = root / "J2" / "imported"
    stacks.mkdir(parents=True)
    n_per, box = 150, 32
    for name in ("stack_A.mrcs", "stack_B.mrcs"):
        MapVolume(data=np.zeros((n_per, box, box), np.float32), voxel=np.array([2.0, 2.0, 2.0])).write(stacks / name)
    n = 2 * n_per
    rng = np.random.default_rng(3)
    uid = rng.permutation(np.arange(10**9, 10**9 + n, dtype=np.uint64))
    main_dtype = [("uid", "<u8"), ("alignments3D/pose", "<f4", (3,)), ("alignments3D/shift", "<f4", (2,)),
                  ("blob/path", "S28"), ("blob/idx", "<u4"), ("blob/shape", "<u4", (2,)), ("blob/psize_A", "<f4")]
    main = np.zeros(n, dtype=main_dtype)
    main["uid"] = uid
    main["alignments3D/pose"] = rng.normal(0, 1, (n, 3))
    main["alignments3D/shift"] = np.random.default_rng(7).normal(0, 2, (n, 2))
    main["blob/path"] = [b">J2/imported/stack_A.mrcs" if i < n_per else b"J2/imported/stack_B.mrcs" for i in range(n)]
    main["blob/idx"] = np.arange(n) % n_per
    main["blob/shape"] = box
    main["blob/psize_A"] = 2.0
    job = root / "J5"
    job.mkdir()
    ctf_fields = ("ctf/df1_A", "ctf/df2_A", "ctf/df_angle_rad", "ctf/accel_kv", "ctf/cs_mm", "ctf/amp_contrast", "ctf/phase_shift_rad")
    through = np.zeros(n, dtype=[("uid", "<u8")] + [(f, "<f4") for f in ctf_fields] + [("location/micrograph_path", "S16")])
    order = rng.permutation(n)
    through["uid"] = uid[order]
    through["ctf/df1_A"] = 10000 + np.arange(n)[order]
    for path, data in ((job / "J5_005_particles.cs", main), (job / "J5_passthrough_particles.cs", through),
                       (job / "J5_003_particles.cs", main[:10])):
        with open(path, "wb") as fh:
            np.save(fh, data)
    os.utime(job / "J5_003_particles.cs", (time.time() + 50, time.time() + 50))  # newer file, older iteration
    va = root / "J9"
    va.mkdir()
    comps = np.zeros(n, dtype=main_dtype + [("components_mode_0/value", "<f4"), ("components_mode_1/value", "<f4")])
    for name in main.dtype.names:
        comps[name] = main[name]
    comps["components_mode_0/value"] = np.where(np.arange(n) % 2, 3.0, -3.0) + rng.normal(0, 0.3, n)
    comps["components_mode_1/value"] = rng.normal(0, 1, n)
    with open(va / "J9_particles.cs", "wb") as fh:
        np.save(fh, comps)
    display = root / "J10"
    display.mkdir()
    z, y, x = np.indices((box, box, box))
    for comp in range(2):
        for f in range(5):
            data = np.exp(-((x - 12 - (f if comp == 0 else 0)) ** 2 + (y - 16 - (f if comp else 0)) ** 2 + (z - 16) ** 2) / 10.0)
            MapVolume(data=data.astype(np.float32), voxel=np.array([2.0] * 3)).write(display / f"J10_component_{comp:03d}_frame_{f:03d}.mrc")
    return {"root": root, "refine": job, "variability": va, "display": display, "n": n}


@pytest.fixture(scope="session")
def movies(tmp_path_factory) -> dict:
    """Synthetic movies (MRC stacks of 6 frames, 1 Å/px) with a flat gain reference, for the RELION pipeline."""
    sys.path.insert(0, str(ROOT / "tests" / "fakes"))
    from spa_data import write_movies
    return write_movies(tmp_path_factory.mktemp("movies") / "Movies", n_movies=4)


@pytest.fixture(scope="session")
def tilt_series(tmp_path_factory) -> dict:
    """Synthetic tilt series (movies of 4 frames, 2 Å/px) with SerialEM mdoc files, for the RELION tomography jobs."""
    sys.path.insert(0, str(ROOT / "tests" / "fakes"))
    from tomo_data import write_tilt_series
    return write_tilt_series(tmp_path_factory.mktemp("tomo") / "session1")


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
    # With a config (-c): the Cα threshold sets how much is built (0.5 or less: everything, else the first 60%, or the
    # last 60% in a map re-sharpened with a negative B-factor); everything in a map improved by CryoAtom2 enhanced (its
    # improve/ folder); with $CRYOPLUG_FAKE_WRONG_HAND the map is mirrored: short misplaced pieces, unless the map is the
    # job's mirrored copy (other_hand), where the model is mirrored to fit it.
    _script(d / "cryoatom", f"""
# cryoatom build -v map [-ps p.fasta] [-pf db] [-c config.json] -o out -d 0
echo "fake CryoAtom2 $@"
out=""; db=""; cfg=""; map=""
while [ $# -gt 0 ]; do case "$1" in -o) out="$2"; shift;; -pf) db="$2"; shift;; -c) cfg="$2"; shift;; -v) map="$2"; shift;; esac; shift; done
mkdir -p "$out"
name="$(basename "$out")"
echo "--------------------- CryoAtom2 Stage1 (Predict C-alpha or P atoms by RU-Net) ---------------------"
echo "------------------ CryoAtom2 Stage2 (Build all-atom structure by Cryo-Net), round 1 / 3 ------------------"
{sys.executable} - "$map" "$cfg" "$out/$name.cif" <<'PY'
import json, os, sys
import gemmi
from cryoplug.mrc import MapVolume
map_path, cfg_path, out = sys.argv[1:4]
st = gemmi.read_structure('{model}')
chain = st[0][0]
n = len(chain)
keep = range(n)
wrong_hand = bool(os.environ.get("CRYOPLUG_FAKE_WRONG_HAND")) and "other_hand" not in map_path
if cfg_path and not wrong_hand and "/improve/" not in map_path:
    threshold = json.load(open(cfg_path)).get("RUNet_args", {{}}).get("ca_threshold", 0.6)
    if threshold > 0.5:
        k = int(n * 0.6)
        keep = range(n - k, n) if "_B-" in os.path.basename(map_path) else range(k)
for i in range(n - 1, -1, -1):
    if i not in keep:
        del chain[i]
if wrong_hand:
    residues = [r.clone() for r in chain]
    st[0].remove_chain(chain.name)
    for j in range(0, len(residues), 5):
        piece = gemmi.Chain("P" + str(j // 5))
        for r in residues[j:j + 5]:
            for a in r:
                a.pos = gemmi.Position(a.pos.x + 3, a.pos.y + 3, a.pos.z)
            piece.add_residue(r)
        st[0].add_chain(piece)
if "other_hand" in map_path:
    vol = MapVolume.read(map_path, header_only=True)
    top = 2 * vol.origin[2] + (vol.shape_xyz[2] - 1) * vol.voxel[2]
    for ch in st[0]:
        for r in ch:
            for a in r:
                a.pos = gemmi.Position(a.pos.x, a.pos.y, top - a.pos.z)
st.setup_entities()
st.make_mmcif_document().write_file(out)
PY
cp "$out/$name.cif" "$out/${{name}}_raw.cif"
[ -n "$db" ] && head -2 "$db" > "$out/${{name}}_prot.fasta"
exit 0
""")
    _script(d / "boltz", f"""
# boltz predict input.yaml --out_dir out ... (Boltz 2.2: unknown options are refused; a CCD code it does not know is
# reported and skipped, with exit code 0)
echo "fake boltz $@"
yaml="$2"
cat "$yaml"
out=""
shift 2
while [ $# -gt 0 ]; do case "$1" in
  --out_dir) out="$2"; shift;;
  --output_format|--diffusion_samples|--recycling_steps|--method|--seed) shift;;
  --use_msa_server|--use_potentials) ;;
  *) echo "Error: No such option: $1" >&2; exit 2;;
esac; shift; done
if grep -q "ccd: '[a-z]\\|FAIL" "$yaml"; then echo "Failed to process $yaml. Skipping. Error: CCD component not found!."; exit 0; fi
p="$out/boltz_results_boltz_input/predictions/boltz_input"
mkdir -p "$p"
{write_cif} "$p/boltz_input_model_0.cif"
echo '{{"confidence_score": 0.83, "ptm": 0.81, "iptm": 0.77, "complex_plddt": 0.86, "chains_ptm": {{"0": 0.8, "1": 0.8}}}}' > "$p/confidence_boltz_input_model_0.json"
""")
    # ColabFold 1.6: files named after the FASTA record (its safe_filename); a job name seen before is skipped
    _script(d / "colabfold_batch", f"""
echo "fake colabfold_batch $@"
in="$1"; out="$2"; shift 2; amber=0
while [ $# -gt 0 ]; do case "$1" in
  --num-models|--num-recycle|--msa-mode|--num-relax) shift;;
  --templates|--use-gpu-relax) ;;
  --amber) amber=1;;
  *) echo "colabfold_batch: error: unrecognized arguments: $1" >&2; exit 2;;
esac; shift; done
mkdir -p "$out"
grep '^>' "$in" | cut -c2- | while read -r name; do
  safe=$(printf '%s' "$name" | tr -c 'A-Za-z0-9_.-' '_')
  tag=rank_001_alphafold2_ptm_model_1_seed_000
  {write_cif.replace("make_mmcif_document().write_file(sys.argv[1])", "write_pdb(sys.argv[1])")} "$out/${{safe}}_unrelaxed_$tag.pdb"
  [ $amber = 1 ] && cp "$out/${{safe}}_unrelaxed_$tag.pdb" "$out/${{safe}}_relaxed_$tag.pdb"
  echo '{{"plddt": [80.0, 90.0], "ptm": 0.74, "iptm": 0.66, "max_pae": 12.5}}' > "$out/${{safe}}_scores_$tag.json"
done
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
    # real_space_refine checks its parameters and the model/map overlap like Phenix 2 does (a test model containing
    # MISALIGNED stands for a model outside the map)
    _script(d / "phenix.real_space_refine", f"""
echo "fake phenix.real_space_refine $@"
bad=""; skip=0
for a in "$@"; do case "$a" in
  resolution=*|macro_cycles=*|nproc=*|run=*|secondary_structure.enabled=*|ramachandran_plot_restraints.enabled=*) ;;
  ignore_symmetry_conflicts=*) ;;
  skip_map_model_overlap_check=True) skip=1;;
  *=*) bad="$bad    ${{a/=/ = }}\n";;
esac; done
if [ -n "$bad" ]; then
  printf '  Unrecognized PHIL parameters:\n  -----------------------------\n%b\n' "$bad"
  echo "Sorry: Some PHIL parameters are not recognized by phenix.real_space_refine."
  echo "Please run this program with the --show-defaults option to see what parameters are available."
  exit 1
fi
if grep -q MISALIGNED "$1" && [ "$skip" = 0 ]; then
  echo "Sorry: Map and model are not aligned! Use skip_map_model_overlap_check=True to continue."
  exit 1
fi
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
    _script(d / "phenix.resolve_cryo_em", """
echo "fake phenix.resolve_cryo_em $@"
first=""
for a in "$@"; do case "$a" in
  resolution=*|nproc=*|seq_file=*|solvent_content=*) ;;
  *=*) echo "Sorry: Some PHIL parameters are not recognized by phenix.resolve_cryo_em."; exit 1;;
  *) [ -z "$first" ] && first="$a";;
esac; done
cp "$(readlink -f "$first")" denmod_map.ccp4
""")
    _script(d / "deepemhancer", """
# deepemhancer -i half1 [-i2 half2] -o out -p network -b batch -g gpus
echo "fake deepemhancer $@"
in=""; out=""
while [ $# -gt 0 ]; do case "$1" in -i) in="$2"; shift;; -o) out="$2"; shift;; -i2|-p|-b|-g|--deepLearningModelPath) shift;;
  *) echo "deepemhancer: error: unrecognized arguments: $1" >&2; exit 2;; esac; shift; done
cp "$(readlink -f "$in")" "$out"
""")
    # Phenix 2 replaced auto_sharpen and local_aniso_sharpen with map_sharpening: they only run with --force
    for name, out in (("auto_sharpen", "sharpened_map.ccp4"), ("local_aniso_sharpen", "local_aniso_sharpened_map.ccp4")):
        _script(d / f"phenix.{name}", f"""
echo "fake phenix.{name} $@"
force=0; map=""
for a in "$@"; do case "$a" in --force) force=1;; *=*) ;; *) [ -z "$map" ] && map="$a";; esac; done
if [ "$force" = 0 ]; then
  echo "Sorry: phenix.{name} is replaced by phenix.map_sharpening Add the keyword '--force' to use anyway"
  exit 1
fi
cp "$(readlink -f "$map")" {out}
""")
    # map_sharpening (Phenix 2): positional maps / model, PHIL keywords checked
    _script(d / "phenix.map_sharpening", """
echo "fake phenix.map_sharpening $@"
first=""
for a in "$@"; do case "$a" in
  resolution=*|seq_file=*|sharpening_method=*|anisotropic_sharpen=*|local_sharpen=*) ;;
  *=*) echo "Sorry: Some PHIL parameters are not recognized by phenix.map_sharpening."; exit 1;;
  *) [ -z "$first" ] && first="$a";;
esac; done
cp "$(readlink -f "$first")" sharpened_map.ccp4
""")
    # servalcat 0.4 refine_spa_norefmac: unknown options refused (argparse), outputs and Final Summary as upstream
    _script(d / "servalcat", f"""
echo "fake servalcat $@"
[ "$1" = "refine_spa_norefmac" ] || {{ echo "Servalcat 0.4.99"; exit 0; }}
shift
pg=""; prefix=refined; half=""
while [ $# -gt 0 ]; do case "$1" in
  --model|--resolution|--ncycle|--hydrogen|--mask_for_fofc|--weight|--map) shift;;
  -o) prefix="$2"; shift;;
  --pg) pg="$2"; shift;;
  --halfmaps) half="$2"; shift 2;;
  --ligand) shift; while [ $# -gt 0 ] && [ "${{1#--}}" = "$1" ]; do shift; done; continue;;
  --jellybody) ;;
  *) echo "servalcat refine_spa_norefmac: error: unrecognized arguments: $1" >&2; exit 2;;
esac; shift; done
{write_cif} "$prefix.mmcif"
cp "{model}" "$prefix.pdb"
[ -n "$pg" ] && cp "$prefix.mmcif" "${{prefix}}_expanded.mmcif"
[ -n "$half" ] && cp "$(readlink -f "$half")" "${{prefix}}_normalized_fofc.mrc" && cp "$(readlink -f "$half")" "${{prefix}}_normalized_fo.mrc"
printf '%s\n' "* Final Summary *" "" "Rmsd from ideal" "  bond lengths: 0.0062 A" "  bond  angles: 1.153 deg" "" \
  "Map-model FSCaverages (at 3.20 A):" " FSCaverage(full) =  0.7811"
""")
    # process_predicted_model: keeps the model, named like Phenix does
    _script(d / "phenix.process_predicted_model", """
echo "fake phenix.process_predicted_model $@"
cp "$1" "$(basename "${1%.*}")_processed.pdb"
""")
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
    # cryoDRGN 4.3 (python fake: same commands and output files, synthetic latent space and volumes)
    fake = d / "cryodrgn"
    fake.write_text(f"#!{sys.executable}\n" + (ROOT / "tests" / "fakes" / "cryodrgn.py").read_text())
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    os.environ["CRYOPLUG_ROOT"] = str(ROOT)
    # RELION 5.1 (python fake dispatched on the program name) and an mpirun passing the number of processes
    relion = d / "relion_fake.py"
    relion.write_text(f"#!{sys.executable}\n" + (ROOT / "tests" / "fakes" / "relion.py").read_text())
    relion.chmod(relion.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    for prog in ("relion_refine", "relion_refine_mpi", "relion_particle_subtract", "relion_particle_subtract_mpi",
                 "relion_particle_symmetry_expand", "relion_postprocess", "relion_postprocess_mpi", "relion_reconstruct",
                 "relion_reconstruct_mpi", "mpirun", "relion_run_motioncorr", "relion_run_motioncorr_mpi",
                 "relion_run_ctffind", "relion_run_ctffind_mpi", "relion_autopick", "relion_autopick_mpi",
                 "relion_preprocess", "relion_preprocess_mpi", "relion_align_symmetry", "relion_class_ranker", "relion_project",
                 "relion_mask_create", "relion_ctf_refine", "relion_ctf_refine_mpi", "relion_motion_refine",
                 "relion_motion_refine_mpi", "relion_star_handler", "relion_flex_analyse", "relion_python_dynamight",
                 "relion_stack_create", "relion_helix_toolbox", "relion_estimate_gain", "relion_helix_inimodel2d",
                 # tomography
                 "relion_python_tomo_import", "relion_align_tiltseries", "relion_align_tiltseries_mpi",
                 "relion_tomo_reconstruct_tomogram", "relion_tomo_reconstruct_tomogram_mpi", "relion_python_tomo_denoise",
                 "relion_tomo_import_coordinates", "relion_tomo_subtomo", "relion_tomo_subtomo_mpi",
                 "relion_tomo_reconstruct_particle", "relion_tomo_reconstruct_particle_mpi", "relion_tomo_refine_ctf",
                 "relion_tomo_refine_ctf_mpi", "relion_tomo_align", "relion_tomo_align_mpi"):
        (d / prog).symlink_to(relion)
    # pytom-match-pick 0.14 (python fake dispatched on the program name)
    pytom = d / "pytom_fake.py"
    pytom.write_text(f"#!{sys.executable}\n" + (ROOT / "tests" / "fakes" / "pytom.py").read_text())
    pytom.chmod(pytom.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    for prog in ("pytom_create_template.py", "pytom_create_mask.py", "pytom_match_template.py", "pytom_extract_candidates.py"):
        (d / prog).symlink_to(pytom)
    # programs RELION calls (the fake RELION only checks that they can be run)
    for prog in ("ctffind", "MotionCor2", "AreTomo2", "batchruntomo", "cryoCARE_train.py", "cryoCARE_extract_train_data.py",
                 "cryoCARE_predict.py"):
        _script(d / prog, f'echo "fake {prog} $@"\n')
    # Topaz: run by relion_autopick, and directly for denoising (topaz denoise -o DIR ... micrographs: DIR/<name>.mrc)
    topaz = d / "relion_python_topaz"
    topaz.write_text(f"""#!{sys.executable}
import os, shutil, sys
args = sys.argv[1:]
print("fake relion_python_topaz", *args)
if args and args[0] == "denoise":
    out, mics, i, valued = "", [], 1, {{"-o", "--output", "-m", "--model", "-d", "--device", "-s", "--patch-size",
                                       "-p", "--patch-padding", "--format", "--suffix"}}
    while i < len(args):
        if args[i] in valued:
            out = args[i + 1] if args[i] in ("-o", "--output") else out
            i += 2
        elif args[i].startswith("-"):
            sys.exit("topaz denoise: unknown option " + args[i])
        else:
            mics.append(args[i])
            i += 1
    if out and not os.path.exists(out):
        os.makedirs(out)
    for m in mics:
        name = os.path.splitext(os.path.basename(m))[0]
        shutil.copy(m, (out + os.sep + name if out else os.path.splitext(m)[0] + ".denoised") + ".mrc")
""")
    topaz.chmod(topaz.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
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
if [ "$mode" = emmernet ]; then
  cp "$src" "$var"
  # LocScale 2.4.1 feature_enhance: the final LocScale map and the pVDDT map go next to the input maps
  stem="$(basename "${out%.mrc}")"
  cp "$src" "$indir/${stem}_baseline.mrc"
  cp "$src" "$indir/${stem}_pVDDT.mrc"
fi
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
               for k in ("modelangelo", "phenix", "locscale", "cryoatom", "boltz", "spisonet", "checkmysequence", "onedep",
                         "cryodrgn", "relion", "colabfold", "servalcat", "deepemhancer", "ctffind", "motioncor", "aretomo",
                         "imod", "cryocare", "pytom")},
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
