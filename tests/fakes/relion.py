"""Fake RELION 5.1 programs for the tests, dispatched on the program name (relion_refine[_mpi],
relion_particle_subtract[_mpi], relion_particle_symmetry_expand, relion_postprocess, relion_reconstruct[_mpi], and the
single-particle programs of relion_spa.py: motion correction to DynaMight, the tomography programs of relion_tomo.py) and a fake ``mpirun -n N prog ...`` that
runs ``prog`` with FAKE_MPI_SIZE=N.

They refuse options RELION 5.1 does not know (relion_options.json; RELION warns and ignores them), need MPI where
RELION does (gold-standard refinement: 3 processes, subtraction with separate half sets: 2), read the particle images
named in the STAR files relative to the working directory (RELION runs from the project folder), check that references
and masks are on the grid of the particles, and write the same files: run_class001.mrc, run_half[12]_class001_unfil.mrc,
run_data/model/optimiser.star and run_itNNN_* (auto-refinement), run_itNNN_* (classification),
particles_subtracted.star + Particles/ (subtraction), postprocess.mrc/.star, *_locres*.mrc (local resolution)."""
import os
import sys

import numpy as np

sys.path.insert(0, os.environ.get("CRYOPLUG_ROOT", ""))
sys.path.insert(0, os.path.join(os.environ.get("CRYOPLUG_ROOT", ""), "tests", "fakes"))
import relion_spa  # noqa: E402
import relion_tomo  # noqa: E402
from cryoplug import relion as rl  # noqa: E402
from cryoplug.mrc import MapVolume  # noqa: E402

# the options of RELION 5.1 (read from its sources), so that a misspelt or removed option fails as in RELION
REFINE_OPTS = relion_spa.OPTIONS["relion_refine"]
POST_OPTS = relion_spa.OPTIONS["relion_postprocess"]
SUBTRACT_OPTS = relion_spa.OPTIONS["relion_particle_subtract"]
RECON_OPTS = relion_spa.OPTIONS["relion_reconstruct"]
FAKE_B_FACTOR = 100.0  # Å²: what a ResLog analysis of the fake reconstructions finds
EXPAND_OPTS = relion_spa.OPTIONS["relion_particle_symmetry_expand"]


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
        if not name.endswith(".mrcs") and not (os.environ.get("FAKE_TOMO") and name.endswith(".mrc")):  # 3D subtomograms
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


def continue_run(a):
    """relion_refine --continue <root>_itNNN_optimiser.star: the run goes on from that iteration, under <root>_ct<NNN>
    when --o is left out or repeats the root (MlOptimiser::parseContinue)."""
    import re
    opt_path = a["--continue"]
    if not os.path.exists(opt_path):
        die(f"Cannot read file {opt_path}")
    m = re.search(r"_it(\d+)_optimiser\.star$", opt_path)
    if not m:
        die("--continue needs the optimiser.star of an iteration (_itNNN_optimiser.star)")
    it = int(m.group(1))
    opt = rl.read_star(opt_path)["optimiser_general"]
    root = opt.value("rlnOutputRootName")
    out = a.get("--o", "OLD_ctX")
    if out in ("OLD_ctX", root):
        out = f"{root}_ct{it}"
    tables = rl.read_star(opt.value("rlnExperimentalDataStarFile"))
    parts = check_images(tables)
    box, apix = grid(tables)
    rng = np.random.default_rng(3)
    refs = [MapVolume.read(r) for r in rl.read_star(opt.value("rlnModelStarFile"))["model_classes"].column("rlnReferenceImage")]
    if opt.value("rlnDoSplitRandomHalves") == "1":  # auto-refine: two more iterations, then the halves combined
        if mpi_size() < 3:
            die("Cannot split data into random halves without using MPI!")
        res = 3.8
        for n in (it + 1, it + 2):
            print(f" Auto-refine: Iteration= {n}")
            print(f" Auto-refine: Resolution= {res + 0.1 * (it + 2 - n):.4f} (no gain for 0 iter) ")
            last = f"{out}_it{n:03d}"
            models = []
            for h in (1, 2):
                refs[0].write(f"{last}_half{h}_class001.mrc")
                classes = rl.StarTable("model_classes")
                classes.set_column("rlnReferenceImage", [f"{last}_half{h}_class001.mrc"])
                classes.set_column("rlnClassDistribution", [1.0])
                rl.write_star(f"{last}_half{h}_model.star", [classes])
                models.append(f"{last}_half{h}_model.star")
            rl.write_star(f"{last}_data.star", list(tables.values()))
            write_optimiser(f"{last}_optimiser.star", f"{last}_data.star", models[0], True, out, models[1])
        print(" Auto-refine: Refinement has converged, entering last iteration where two halves will be combined...")
        refs[0].write(f"{out}_class001.mrc")
        for h in (1, 2):
            refs[0].like(refs[0].data + rng.normal(0, 0.05, refs[0].data.shape).astype(np.float32)).write(f"{out}_half{h}_class001_unfil.mrc")
        aligned(parts, rng)
        rl.write_star(f"{out}_data.star", list(tables.values()))
        curve, _f, _c = fsc_table(box, apix, res)
        general = rl.StarTable("model_general", ["rlnCurrentResolution"], [[f"{res:.6f}"]], loop=False)
        classes = rl.StarTable("model_classes")
        classes.set_column("rlnReferenceImage", [f"{out}_class001.mrc"])
        classes.set_column("rlnClassDistribution", [1.0])
        classes.set_column("rlnEstimatedResolution", [res])
        rl.write_star(f"{out}_model.star", [general, classes, curve])
        write_optimiser(f"{out}_optimiser.star", f"{out}_data.star", f"{out}_model.star", False, out)
        return
    k = len(refs)
    n_iter = int(a.get("--iter", it + 3))
    if n_iter <= it:
        die(f"Nothing to do: the run is at iteration {it} of {n_iter}")
    for n in range(it + 1, n_iter + 1):
        print(f" Expectation iteration {n} of {n_iter}")
        classes = rl.StarTable("model_classes")
        classes.set_column("rlnReferenceImage", [f"{out}_it{n:03d}_class{c:03d}.mrc" for c in range(1, k + 1)])
        classes.set_column("rlnClassDistribution", [1.0 / k] * k)
        classes.set_column("rlnEstimatedResolution", [7.0 + c for c in range(k)])
        general = rl.StarTable("model_general", ["rlnCurrentResolution"], [["7.000000"]], loop=False)
        rl.write_star(f"{out}_it{n:03d}_model.star", [general, classes])
    last = f"{out}_it{n_iter:03d}"
    for c in range(1, k + 1):
        refs[c - 1].write(f"{last}_class{c:03d}.mrc")
    n = len(parts)
    parts.set_column("rlnClassNumber", np.arange(n) % k + 1)
    rl.write_star(f"{last}_data.star", list(tables.values()))
    write_optimiser(f"{last}_optimiser.star", f"{last}_data.star", f"{last}_model.star", False, out)


def refine(argv):
    a = parse(argv, REFINE_OPTS)
    if "--continue" in a:
        return continue_run(a)
    for need in ("--o", "--i", "--ref"):
        if need not in a:
            die(f"missing {need}")
    if a.get("--split_random_halves") and mpi_size() < 3:
        die("Cannot split data into random halves without using MPI!" if mpi_size() == 1 else
            "MlOptimiserMpi::initialiseWorkLoad: at least 3 MPI processes are required when splitting data into random halves")
    tables = rl.read_star(a["--i"])
    parts = check_images(tables)
    box, apix = grid(tables)
    refs = None
    if a["--ref"].endswith(".star"):  # one reference per class (MlModel::initialiseFromImages); --K is ignored
        listing = rl.read_star(a["--ref"])
        table = listing.get("model_classes") or next(iter(listing.values()))
        if not table.has("rlnReferenceImage"):
            die("When specifying a .star-file as --ref input, you need to have the _rlnReferenceImage field")
        refs = [check_map(name, box, apix, "Reference", "--trust_ref_size" in a) for name in table.column("rlnReferenceImage")]
        ref = refs[0]
    else:
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
            # every iteration with the half sets apart: their maps, models, particles and optimiser
            last = f"{root}_it{it:03d}"
            models = []
            for h in (1, 2):
                ref.write(f"{last}_half{h}_class001.mrc")
                classes = rl.StarTable("model_classes")
                classes.set_column("rlnReferenceImage", [f"{last}_half{h}_class001.mrc"])
                classes.set_column("rlnClassDistribution", [1.0])
                general = rl.StarTable("model_general", ["rlnCurrentResolution"], [[f"{8.0 - it:.6f}"]], loop=False)
                rl.write_star(f"{last}_half{h}_model.star", [general, classes])
                models.append(f"{last}_half{h}_model.star")
            rl.write_star(f"{last}_data.star", list(tables.values()))
            write_optimiser(f"{last}_optimiser.star", f"{last}_data.star", models[0], True, root, models[1])
        print(" Auto-refine: Refinement has converged, entering last iteration where two halves will be combined...")
        ref.write(f"{root}_class001.mrc")
        for h in (1, 2):
            ref.like(ref.data + rng.normal(0, 0.05, ref.data.shape).astype(np.float32)).write(f"{root}_half{h}_class001_unfil.mrc")
        parts.set_column("rlnClassNumber", [1] * len(parts))
        if not parts.has("rlnRandomSubset"):
            parts.set_column("rlnRandomSubset", np.arange(len(parts)) % 2 + 1)
        aligned(parts, rng)
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
        # the last iteration joins the half sets: its optimiser no longer splits them (ml_optimiser_mpi.cpp)
        write_optimiser(f"{root}_optimiser.star", f"{root}_data.star", f"{root}_model.star", False, root)
        return
    k, n_iter = (len(refs) if refs else int(a.get("--K", 1))), int(a.get("--iter", 25))
    if mpi_size() == 2:
        die("relion_refine_mpi needs at least 3 processes or a single one")
    # class shares drifting from even to uneven: class c ends with a share proportional to k - c + 1
    final = np.arange(k, 0, -1, dtype=float)
    final /= final.sum()
    n = len(parts)
    counts = np.round(final * n).astype(int)
    counts[0] += n - counts.sum()
    labels = np.repeat(np.arange(1, k + 1), counts)[rng.permutation(n)]
    if "--skip_align" not in a:
        aligned(parts, rng)
    z, y, x = np.indices(ref.data.shape) - box // 2
    for it in range(1, n_iter + 1):  # every iteration writes its maps, model, particles and optimiser
        print(f" Expectation iteration {it} of {n_iter}")
        w = it / n_iter
        share = (1 - w) / k + w * final
        stem = f"{root}_it{it:03d}"
        classes = rl.StarTable("model_classes")
        classes.set_column("rlnReferenceImage", [f"{stem}_class{c:03d}.mrc" for c in range(1, k + 1)])
        classes.set_column("rlnClassDistribution", share)
        classes.set_column("rlnEstimatedResolution", [12.0 - 6.0 * w + c for c in range(k)])
        general = rl.StarTable("model_general", ["rlnCurrentResolution"], [[f"{12.0 - 6.0 * w:.6f}"]], loop=False)
        rl.write_star(f"{stem}_model.star", [general, classes])
        for c in range(1, k + 1):
            bump = np.exp(-((x - c) ** 2 + y ** 2 + z ** 2) / 8.0).astype(np.float32)
            start = refs[c - 1] if refs else ref
            start.like(start.data + 0.3 * w * bump).write(f"{stem}_class{c:03d}.mrc")
        parts.set_column("rlnClassNumber", labels if it == n_iter else rng.integers(1, k + 1, n))
        rl.write_star(f"{stem}_data.star", list(tables.values()))
        write_optimiser(f"{stem}_optimiser.star", f"{stem}_data.star", f"{stem}_model.star", False, root)


def aligned(parts, rng):
    """Poses after an alignment: those of the particles moved a little, or new ones (global searches), with the
    probability of the best pose and the number of significant samples."""
    n = len(parts)
    parts.set_column("rlnMaxValueProbDistribution", np.clip(rng.beta(5, 2, n), 0, 1))
    parts.set_column("rlnNrOfSignificantSamples", rng.integers(1, 40, n))
    if parts.has("rlnAngleRot") and parts.has("rlnAngleTilt"):
        parts.set_column("rlnAngleRot", parts.floats("rlnAngleRot") + 0.5)
        return
    for lab, hi in (("rlnAngleRot", 360.0), ("rlnAngleTilt", 180.0), ("rlnAnglePsi", 360.0)):
        parts.set_column(lab, rng.uniform(0, hi, n))
    for lab in ("rlnOriginXAngst", "rlnOriginYAngst"):
        parts.set_column(lab, rng.normal(0, 1.0, n))


def write_optimiser(path, data, model, halves, root, model2=None):
    labels = ["rlnOutputRootName", "rlnModelStarFile", "rlnExperimentalDataStarFile", "rlnDoSplitRandomHalves"]
    values = [root, model, data, "1" if halves else "0"]
    if model2:
        labels.insert(2, "rlnModelStarFile2")
        values.insert(2, model2)
    rl.write_star(path, [rl.StarTable("optimiser_general", labels, [values], loop=False)])


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
    for need in ("--i", "--i2", "--o"):
        if need not in a:
            die(f"missing {need}")
    h1 = MapVolume.read(a["--i"])
    h2 = MapVolume.read(a["--i2"])
    if h1.shape_xyz != h2.shape_xyz:
        die("Postprocessing::initialise ERROR: The two half reconstructions are not of the same size!")
    root = a["--o"]
    if a.get("--locres"):
        return locres(a, h1, h2, root)
    if "--mask" not in a:
        die("no mask: make a mask with relion_mask_create beforehand")
    mask = MapVolume.read(a["--mask"])
    if h1.shape_xyz != mask.shape_xyz:
        die("mask and half maps have different sizes")
    full = h1.like((h1.data + h2.data) / 2)
    full.write(f"{root}.mrc")
    full.like(full.data * mask.data).write(f"{root}_masked.mrc")
    box, apix = h1.shape_xyz[0], float(a.get("--angpix", h1.pixel_size))
    _t, freq, fsc = fsc_table(box, apix, 3.6)
    general = rl.StarTable("general", ["rlnFinalResolution", "rlnBfactorUsedForSharpening", "rlnUnfilteredMapHalf1",
                                       "rlnUnfilteredMapHalf2", "rlnPostprocessedMap", "rlnMaskName", "rlnRandomiseFrom",
                                       "rlnPostprocessedMapMasked"],
                           [["3.600000", f"{float(a.get('--adhoc_bfac', -85.0)):.6f}", a["--i"], a["--i2"], f"{root}.mrc",
                             a["--mask"], "7.000000", f"{root}_masked.mrc"]], loop=False)
    t = rl.StarTable("fsc")
    t.set_column("rlnSpectralIndex", np.arange(1, len(freq) + 1))
    t.set_column("rlnResolution", freq)
    t.set_column("rlnAngstromResolution", 1.0 / freq)
    t.set_column("rlnFourierShellCorrelationCorrected", fsc)
    t.set_column("rlnFourierShellCorrelationUnmaskedMaps", fsc * 0.9)
    t.set_column("rlnFourierShellCorrelationMaskedMaps", fsc)
    t.set_column("rlnCorrectedFourierShellCorrelationPhaseRandomizedMaskedMaps", fsc * 0.05)
    rl.write_star(f"{root}.star", [general, t])


def locres(a, h1, h2, root):
    """relion_postprocess --locres: local resolution (best at the centre), the map filtered with it, local FSCs."""
    if a.get("--auto_bfac"):
        die("Postprocessing::initialise ERROR: for --locres, you cannot do --auto_bfac, use --adhoc_bfac instead!")
    apix = float(a.get("--angpix", h1.pixel_size))
    z, y, x = np.indices(h1.data.shape) - h1.shape_xyz[0] / 2
    res = (3.5 + 0.08 * np.sqrt(x ** 2 + y ** 2 + z ** 2) * apix).astype(np.float32)
    h1.like(res).write(f"{root}_locres.mrc")
    h1.like((h1.data + h2.data) / 2).write(f"{root}_locres_filtered.mrc")
    t = rl.StarTable("fsc")
    t.set_column("rlnSpectralIndex", [1, 2, 3])
    t.set_column("rlnFourierShellCorrelation", [1.0, 0.8, 0.1])
    rl.write_star(f"{root}_locres_fscs.star", [t])
    if "--mask" in a:
        print(" WARNING: --mask is used only to make a histogram of local resolutions; it is not used for local "
              "resolution calculation itself.", file=sys.stderr)
        if MapVolume.read(a["--mask"]).shape_xyz != h1.shape_xyz:
            die("The shape of the input half maps and the mask is not the same.")
        print("Calculating a histogram of local resolutions within the mask.")


def reconstruct(argv):
    a = parse(argv, RECON_OPTS)
    tables = rl.read_star(a["--i"])
    parts = check_images(tables)
    box, apix = grid(tables)
    chosen = int(a.get("--class", -1))
    if chosen > 0:  # only the particles of that class
        rows = [i for i, v in enumerate(parts.column("rlnClassNumber") if parts.has("rlnClassNumber") else [])
                if int(float(v)) == chosen]
        if not rows:
            die(f"no particle in class {chosen}")
        parts = parts.select(rows)
    subset = int(a.get("--subset", -1))
    if subset in (1, 2):
        if not parts.has("rlnRandomSubset"):
            die("no rlnRandomSubset: nothing to reconstruct for this subset")
        if str(subset) not in parts.column("rlnRandomSubset"):
            die(f"no particle in subset {subset}")
    z, y, x = np.indices((box, box, box)) - box // 2
    blob = np.exp(-(x ** 2 + y ** 2 + z ** 2) / (box / 4.0) ** 2)
    # high-resolution detail fading with the B-factor of the fake data (the same in every reconstruction), and the noise
    # of an average of n images: the FSC of two halves reaches further with more particles (Rosenthal & Henderson)
    n = len(parts) if subset not in (1, 2) else parts.column("rlnRandomSubset").count(str(subset))
    k = np.fft.fftfreq(box, d=apix)
    k2 = k[:, None, None] ** 2 + k[None, :, None] ** 2 + k[None, None, :] ** 2
    shape = (box, box, box)
    rng, noise_rng = np.random.default_rng(7), np.random.default_rng(subset + 2)
    detail = (rng.normal(size=shape) + 1j * rng.normal(size=shape)) * np.exp(-FAKE_B_FACTOR * k2 / 4)
    noise = (noise_rng.normal(size=shape) + 1j * noise_rng.normal(size=shape)) * (9.0 / np.sqrt(max(n, 1)))
    data = (blob + 0.2 * np.fft.ifftn(detail + noise).real * box ** 1.5).astype(np.float32)
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
            "relion_postprocess": postprocess, "relion_postprocess_mpi": postprocess, "relion_reconstruct": reconstruct,
            "relion_reconstruct_mpi": reconstruct, "mpirun": mpirun}
PROGRAMS.update(relion_spa.PROGRAMS)
PROGRAMS.update(relion_tomo.PROGRAMS)


def refine_any(argv):
    """relion_refine: 2D classification, initial model and multi-body refinement in relion_spa, the rest here; the
    particles of an optimisation set (--ios, subtomograms) in relion_tomo."""
    if "--ios" in argv:
        return relion_tomo.refine_ios(argv, lambda av: relion_spa.refine_spa(av, refine))
    return relion_spa.refine_spa(argv, refine)


PROGRAMS["relion_refine"] = PROGRAMS["relion_refine_mpi"] = refine_any

if __name__ == "__main__":
    prog = os.path.basename(sys.argv[0])
    if len(sys.argv) > 1 and sys.argv[1] == "--version":
        print("RELION version: 5.1.1-commit-fake \nPrecision: BASE=double, CUDA-ACC=single ")
        sys.exit(0)
    if prog.endswith("_mpi") and prog != "mpirun" and mpi_size() == 1:
        print(" WARNING: running the MPI program with a single process", file=sys.stderr)
    PROGRAMS[prog](sys.argv[1:])
