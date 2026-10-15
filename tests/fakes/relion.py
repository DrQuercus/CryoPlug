"""Fake RELION 5.1 programs for the tests, dispatched on the program name (relion_refine[_mpi],
relion_particle_subtract[_mpi], relion_particle_symmetry_expand, relion_postprocess, relion_reconstruct[_mpi]) and
a fake ``mpirun -n N prog ...`` that runs ``prog`` with FAKE_MPI_SIZE=N.

Like the real programs they reject unknown options, need MPI where RELION does (gold-standard refinement: 3 processes,
subtraction of a refinement: 2), read the particle images named in the STAR files relative to the working directory
(RELION runs from the project folder), check that references and masks are on the grid of the particles, and write
the same files: run_class001.mrc, run_half[12]_class001_unfil.mrc, run_data/model/optimiser.star (auto-refinement),
run_itNNN_* (classification), particles_subtracted.star + Particles/ (subtraction), postprocess.mrc/.star."""
import os
import sys

import numpy as np

sys.path.insert(0, os.environ.get("CRYOPLUG_ROOT", ""))
from cryoplug import relion as rl  # noqa: E402
from cryoplug.mrc import MapVolume  # noqa: E402

REFINE_OPTS = {
    "--o": 1, "--i": 1, "--ref": 1, "--auto_refine": 0, "--split_random_halves": 0, "--firstiter_cc": 0,
    "--trust_ref_size": 0, "--ini_high": 1, "--dont_combine_weights_via_disc": 0, "--pool": 1, "--pad": 1, "--ctf": 0,
    "--particle_diameter": 1, "--flatten_solvent": 0, "--zero_mask": 0, "--solvent_mask": 1, "--solvent_correct_fsc": 0,
    "--oversampling": 1, "--healpix_order": 1, "--auto_local_healpix_order": 1, "--offset_range": 1, "--offset_step": 1,
    "--sym": 1, "--low_resol_join_halves": 1, "--norm": 0, "--scale": 0, "--j": 1, "--gpu": 1, "--blush": 0,
    "--relax_sym": 1, "--preread_images": 0, "--iter": 1, "--tau2_fudge": 1, "--K": 1, "--skip_align": 0,
    "--sigma_ang": 1,
}
SUBTRACT_OPTS = {"--i": 1, "--o": 1, "--mask": 1, "--data": 1, "--center_x": 1, "--center_y": 1, "--center_z": 1,
                 "--new_box": 1, "--float16": 0, "--recenter_on_mask": 0, "--ignore_class": 0}
POST_OPTS = {"--mask": 1, "--i": 1, "--i2": 1, "--o": 1, "--angpix": 1, "--auto_bfac": 0, "--autob_lowres": 1,
             "--adhoc_bfac": 1, "--mtf": 1, "--locres": 0, "--skip_fsc_weighting": 0}
RECON_OPTS = {"--i": 1, "--o": 1, "--ctf": 0, "--sym": 1, "--subset": 1, "--maxres": 1, "--angpix": 1, "--j": 1}
EXPAND_OPTS = {"--i": 1, "--o": 1, "--sym": 1}


def die(message):
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def parse(argv, spec):
    out, i = {}, 0
    while i < len(argv):
        opt = argv[i]
        if opt not in spec:
            die(f"Option {opt} is not a valid RELION argument")
        n = spec[opt]
        if i + n >= len(argv) + (0 if n else 1):
            die(f"Option {opt} needs a value")
        out[opt] = argv[i + 1] if n else True
        i += 1 + n
    return out


def mpi_size():
    return int(os.environ.get("FAKE_MPI_SIZE", "1"))


def check_images(tables):
    parts = rl.particle_table(tables)
    for name in dict.fromkeys(v.split("@", 1)[-1] for v in parts.column("rlnImageName")):
        if not name.endswith(".mrcs"):
            die(f"{name}: image stacks need the .mrcs extension")
        if not os.path.exists(name):  # relative to the working directory, as in RELION
            die(f"Cannot open image file {name} (from {os.getcwd()})")
    return parts


def grid(tables):
    box, apix = rl.grid_from_star(tables)
    if not box:
        die("No rlnImageSize in the optics table")
    return box, apix


def check_map(path, box, apix, what, trust=False):
    vol = MapVolume.read(path)
    if vol.shape_xyz != (box, box, box) and not trust:
        die(f"{what} {path} is {vol.shape_xyz}, the particles {box} px")
    if abs(vol.pixel_size - apix) > 1e-3 and not trust:
        die(f"{what} {path} has a pixel size of {vol.pixel_size}, the particles {apix}")
    return vol


def fsc_table(box, apix, res):
    t = rl.StarTable("model_class_1")
    freq = np.arange(1, box // 2 + 1) / (box * apix)
    fsc = 1.0 / (1.0 + np.exp((freq - 1.0 / res) * box * apix / 2.0))
    t.set_column("rlnSpectralIndex", np.arange(1, box // 2 + 1))
    t.set_column("rlnResolution", freq)
    t.set_column("rlnAngstromResolution", 1.0 / freq)
    t.set_column("rlnGoldStandardFsc", fsc)
    return t, freq, fsc


def refine(argv):
    a = parse(argv, REFINE_OPTS)
    for need in ("--o", "--i", "--ref"):
        if need not in a:
            die(f"missing {need}")
    if a.get("--split_random_halves") and mpi_size() < 3:
        die("Cannot split data into random halves without using MPI!" if mpi_size() == 1 else
            "MlOptimiserMpi::initialiseWorkLoad: at least 3 MPI processes are required when splitting data into random halves")
    tables = rl.read_star(a["--i"])
    parts = check_images(tables)
    box, apix = grid(tables)
    ref = check_map(a["--ref"], box, apix, "Reference", "--trust_ref_size" in a)
    if "--solvent_mask" in a:
        mask = check_map(a["--solvent_mask"], box, apix, "Solvent mask")
        if mask.data.min() < -1e-6 or mask.data.max() > 1 + 1e-6:
            die("solvent mask values outside [0, 1]")
    root = a["--o"]
    rng = np.random.default_rng(1)
    if a.get("--auto_refine"):
        res = 4.0
        for it in range(1, 5):
            print(f" Auto-refine: Iteration= {it}")
            print(f" Auto-refine: Resolution= {8.0 - it:.4f} (no gain for 0 iter) ")
        print(" Auto-refine: Refinement has converged, entering last iteration where two halves will be combined...")
        ref.write(f"{root}_class001.mrc")
        for h in (1, 2):
            ref.like(ref.data + rng.normal(0, 0.05, ref.data.shape).astype(np.float32)).write(f"{root}_half{h}_class001_unfil.mrc")
        parts.set_column("rlnClassNumber", [1] * len(parts))
        if not parts.has("rlnRandomSubset"):
            parts.set_column("rlnRandomSubset", np.arange(len(parts)) % 2 + 1)
        parts.set_column("rlnAngleRot", parts.floats("rlnAngleRot") + 0.5)
        rl.write_star(f"{root}_data.star", list(tables.values()))
        curve, _f, _c = fsc_table(box, apix, res)
        general = rl.StarTable("model_general", ["rlnCurrentResolution"], [[f"{res:.6f}"]], loop=False)
        classes = rl.StarTable("model_classes")
        classes.set_column("rlnReferenceImage", [f"{root}_class001.mrc"])
        classes.set_column("rlnClassDistribution", [1.0])
        classes.set_column("rlnAccuracyRotations", [1.2])
        classes.set_column("rlnAccuracyTranslationsAngst", [0.6])
        classes.set_column("rlnEstimatedResolution", [res])
        rl.write_star(f"{root}_model.star", [general, classes, curve])
        write_optimiser(f"{root}_optimiser.star", f"{root}_data.star", f"{root}_model.star", True, root)
        return
    k, n_iter = int(a.get("--K", 1)), int(a.get("--iter", 25))
    if mpi_size() == 2:
        die("relion_refine_mpi needs at least 3 processes or a single one")
    for it in range(1, n_iter + 1):
        print(f" Expectation iteration {it} of {n_iter}")
    last = f"{root}_it{n_iter:03d}"
    for c in range(1, k + 1):
        z, y, x = np.indices(ref.data.shape) - box // 2
        bump = np.exp(-((x - c) ** 2 + y ** 2 + z ** 2) / 8.0).astype(np.float32)
        ref.like(ref.data + 0.3 * bump).write(f"{last}_class{c:03d}.mrc")
    parts.set_column("rlnClassNumber", np.arange(len(parts)) % k + 1)
    rl.write_star(f"{last}_data.star", list(tables.values()))
    classes = rl.StarTable("model_classes")
    classes.set_column("rlnReferenceImage", [f"{last}_class{c:03d}.mrc" for c in range(1, k + 1)])
    classes.set_column("rlnClassDistribution", [1.0 / k] * k)
    classes.set_column("rlnEstimatedResolution", [6.0] * k)
    general = rl.StarTable("model_general", ["rlnCurrentResolution"], [["6.0"]], loop=False)
    rl.write_star(f"{last}_model.star", [general, classes])
    write_optimiser(f"{last}_optimiser.star", f"{last}_data.star", f"{last}_model.star", False, root)


def write_optimiser(path, data, model, halves, root):
    t = rl.StarTable("optimiser_general", ["rlnOutputRootName", "rlnModelStarFile", "rlnExperimentalDataStarFile",
                                           "rlnDoSplitRandomHalves"], [[root, model, data, "1" if halves else "0"]],
                     loop=False)
    rl.write_star(path, [t])


def subtract(argv):
    a = parse(argv, SUBTRACT_OPTS)
    if not os.path.exists(a.get("--i", "")):
        die(f"Cannot read optimiser file {a.get('--i')}")
    opt = rl.read_star(a["--i"])["optimiser_general"]
    halves = opt.value("rlnDoSplitRandomHalves") == "1"
    if halves and mpi_size() < 2:
        die("You need to run this program with at least 2 MPI processes for subtraction of 3D auto-refine jobs")
    model = rl.read_star(opt.value("rlnModelStarFile"))
    ref_path = model["model_classes"].column("rlnReferenceImage")[0]
    tables = rl.read_star(a.get("--data") or opt.value("rlnExperimentalDataStarFile"))
    parts = check_images(tables)
    box, apix = grid(tables)
    mask = check_map(a["--mask"], box, apix, "Mask")
    if mask.data.min() < -1e-6 or mask.data.max() > 1 + 1e-6:
        die("the keep_inside mask has values outside the range [0,1]")
    check_map(ref_path, box, apix, "Reference")
    out = a["--o"]
    if not out.endswith("/"):
        out += "/"
    os.makedirs(out + "Particles", exist_ok=True)
    new_box = int(a.get("--new_box", -1))
    nb = new_box if new_box > 0 else box
    center = [int(a.get(f"--center_{c}", 9999)) for c in "xyz"]
    if all(c != 9999 for c in center):
        print(f" + The subtracted particles will be re-centred on projections of 3D-coordinate: ({center[0]} , {center[1]} , {center[2]})")
    n = len(parts)
    stack = out + "Particles/subtracted_rank1.mrcs"
    MapVolume(data=np.zeros((n, nb, nb), np.float32), voxel=np.array([apix, apix, 1.0])).write(stack)
    parts.set_column("rlnImageOriginalName", parts.column("rlnImageName"))
    parts.set_column("rlnImageName", [f"{i + 1:06d}@{stack}" for i in range(n)])
    tables["optics"].set_column("rlnImageSize", [nb] * len(tables["optics"]))
    rl.write_star(out + "particles_subtracted.star", list(tables.values()))
    print(f" Done subtracting {n} particles")


def expand(argv):
    a = parse(argv, EXPAND_OPTS)
    sym = a.get("--sym", "C1").upper()
    order = int(sym[1:]) * (2 if sym[0] == "D" else 1) if sym[0] in "CD" else 1
    tables = rl.read_star(a["--i"])
    parts = rl.particle_table(tables)
    rows = [list(r) for r in parts.rows for _ in range(order)]
    parts.rows = rows
    rl.write_star(a["--o"], list(tables.values()))
    print(f" Expanded {len(rows) // order} particles {order} times")


def postprocess(argv):
    a = parse(argv, POST_OPTS)
    h1 = MapVolume.read(a["--i"])
    h2 = MapVolume.read(a["--i2"])
    mask = MapVolume.read(a["--mask"])
    if h1.shape_xyz != mask.shape_xyz:
        die("mask and half maps have different sizes")
    root = a["--o"]
    full = h1.like((h1.data + h2.data) / 2)
    full.write(f"{root}.mrc")
    full.like(full.data * mask.data).write(f"{root}_masked.mrc")
    box, apix = h1.shape_xyz[0], float(a.get("--angpix", h1.pixel_size))
    _t, freq, fsc = fsc_table(box, apix, 3.6)
    general = rl.StarTable("general", ["rlnFinalResolution", "rlnBfactorUsedForSharpening", "rlnRandomiseFrom"],
                           [["3.600000", f"{float(a.get('--adhoc_bfac', -85.0)):.6f}", "7.000000"]], loop=False)
    t = rl.StarTable("fsc")
    t.set_column("rlnSpectralIndex", np.arange(1, len(freq) + 1))
    t.set_column("rlnResolution", freq)
    t.set_column("rlnAngstromResolution", 1.0 / freq)
    t.set_column("rlnFourierShellCorrelationCorrected", fsc)
    t.set_column("rlnFourierShellCorrelationUnmaskedMaps", fsc * 0.9)
    t.set_column("rlnFourierShellCorrelationMaskedMaps", fsc)
    t.set_column("rlnCorrectedFourierShellCorrelationPhaseRandomizedMaskedMaps", fsc * 0.05)
    rl.write_star(f"{root}.star", [general, t])
    if a.get("--locres"):
        full.like(np.full(full.data.shape, 4.0, np.float32)).write(f"{root}_locres.mrc")


def reconstruct(argv):
    a = parse(argv, RECON_OPTS)
    tables = rl.read_star(a["--i"])
    parts = check_images(tables)
    box, apix = grid(tables)
    subset = int(a.get("--subset", -1))
    if subset in (1, 2):
        if not parts.has("rlnRandomSubset"):
            die("no rlnRandomSubset: nothing to reconstruct for this subset")
        if str(subset) not in parts.column("rlnRandomSubset"):
            die(f"no particle in subset {subset}")
    z, y, x = np.indices((box, box, box)) - box // 2
    data = np.exp(-(x ** 2 + y ** 2 + z ** 2) / (box / 4.0) ** 2).astype(np.float32)
    data += np.random.default_rng(subset + 2).normal(0, 0.02, data.shape).astype(np.float32)
    MapVolume(data=data, voxel=np.full(3, apix)).write(a["--o"])


def mpirun(argv):
    n = 1
    while argv and argv[0].startswith("-"):
        if argv[0] in ("-n", "-np"):
            n = int(argv[1])
            argv = argv[2:]
        else:
            argv = argv[1:]
    os.environ["FAKE_MPI_SIZE"] = str(n)
    os.execvp(argv[0], argv)


PROGRAMS = {"relion_refine": refine, "relion_refine_mpi": refine, "relion_particle_subtract": subtract,
            "relion_particle_subtract_mpi": subtract, "relion_particle_symmetry_expand": expand,
            "relion_postprocess": postprocess, "relion_reconstruct": reconstruct, "relion_reconstruct_mpi": reconstruct,
            "mpirun": mpirun}

if __name__ == "__main__":
    prog = os.path.basename(sys.argv[0])
    if len(sys.argv) > 1 and sys.argv[1] == "--version":
        print("RELION version: 5.1.1-commit-fake \nPrecision: BASE=double, CUDA-ACC=single ")
        sys.exit(0)
    if prog.endswith("_mpi") and prog != "mpirun" and mpi_size() == 1:
        print(" WARNING: running the MPI program with a single process", file=sys.stderr)
    PROGRAMS[prog](sys.argv[1:])
