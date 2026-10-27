"""Methods paragraph and 'Table 1' values from the history of a processing in CryoPlug: what each RELION job did
(single particle analysis from the movies to the map, or subtomogram averaging), with its settings and results, and the
papers to cite for it (the list RELION's GUI gives under Help > About, checked against PubMed).

The history is the list of jobs upstream of the deposition (``JobContext.ancestors``, oldest first), each with its type,
status, params and outputs (name, type and metadata). Only completed jobs count.
"""
from __future__ import annotations

from typing import Any, Callable

RELION3 = "Zivanov J. et al. (2018) eLife 7, e42166 (RELION-3: CTF refinement, Laplacian-of-Gaussian picking)."
RELION5_TOMO = "Burt A. et al. (2024) FEBS Open Bio 14, 1788-1804 (RELION-5 tomography pipeline)."
TEMPLATE_PICKING = "Scheres S.H.W. (2014) J. Struct. Biol. 189, 114-122 (template-based picking in RELION)."
HELICAL = "He S., Scheres S.H.W. (2017) J. Struct. Biol. 198, 163-176 (helical reconstruction in RELION)."
GOLD_STANDARD = "Scheres S.H.W., Chen S. (2012) Nat. Methods 9, 853-854 (gold-standard FSC)."
NOISE_SUBSTITUTION = "Chen S. et al. (2013) Ultramicroscopy 135, 24-35 (mask-corrected FSC by phase randomisation)."
BFACTOR = "Rosenthal P.B., Henderson R. (2003) J. Mol. Biol. 333, 721-745 (B-factor estimation)."
POLISHING = "Zivanov J., Nakane T., Scheres S.H.W. (2019) IUCrJ 6, 5-17 (Bayesian polishing)."
ABERRATIONS = ("Zivanov J., Nakane T., Scheres S.H.W. (2020) IUCrJ 7, 253-267 (higher-order aberrations, anisotropic "
               "magnification).")
MULTIBODY = "Nakane T. et al. (2018) eLife 7, e36861 (multi-body refinement)."
DYNAMIGHT = "Schwab J. et al. (2024) Nat. Methods 21, 1855-1862 (DynaMight)."
TOPAZ = "Bepler T. et al. (2019) Nat. Methods 16, 1153-1160 (Topaz)."
TOPAZ_DENOISE = "Bepler T. et al. (2020) Nat. Commun. 11, 5208 (Topaz-Denoise)."
SUBTRACTION = "Bai X.-C. et al. (2015) eLife 4, e11182 (signal subtraction)."
SYMMETRY_RELAXATION = ("Ilca S.L. et al. (2019) Nature 570, 252-256; Abrishami V. et al. (2020) Prog. Biophys. Mol. Biol. "
                       "160, 43-52 (symmetry relaxation).")

PICKERS = {"log": "RELION's Laplacian-of-Gaussian picker", "template": "template matching in RELION", "topaz": "Topaz"}


def output_meta(job: dict[str, Any], *names: str, type: str | None = None) -> dict[str, Any]:
    """The metadata of the first output of ``job`` named one of ``names`` (any name if none), of ``type`` if given."""
    for out in job.get("outputs") or []:
        if (not names or out.get("name") in names) and (type is None or out.get("type") == type):
            return dict(out.get("meta") or {})
    return {}


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # NaN -> None


def _count(v: Any) -> str:
    f = _num(v)
    return f"{int(f):,}" if f is not None else ""


def _sym(p: dict[str, Any], meta: dict[str, Any]) -> str:
    return str(p.get("symmetry") or meta.get("symmetry") or "C1").strip().upper() or "C1"


class Processing:
    """Walks the history once: the methods sentences and citations in processing order, and the values of Table 1."""

    def __init__(self, ancestors: list[dict[str, Any]]):
        from cryoplug.tools import TOOLS
        self.tools = TOOLS
        self.jobs = [j for j in ancestors if j.get("status") == "completed"]
        self.sentences: list[str] = []
        self.citations: list[str] = []
        self.stats: dict[str, Any] = {}
        self.extracted = 0  # extraction jobs seen (the first one gives the initial particle images)
        from cryoplug.jobs import REGISTRY
        for job in self.jobs:
            build = BUILDERS.get(job["type"])
            if build:
                jt = REGISTRY.get(job["type"])
                if jt and jt.tool:
                    self.cite_tool(jt.tool)
                build(self, job, dict(job.get("params") or {}))
            for out in job.get("outputs") or []:  # local resolution: its 5th-95th percentile range in the molecule
                rng = (out.get("meta") or {}).get("display_range") if out.get("type") == "locres" else None
                if isinstance(rng, (list, tuple)) and len(rng) == 2:
                    self.stat("resolution_range", (float(rng[0]), float(rng[1])))

    # -------------------------------------------------------------- helpers
    def say(self, sentence: str, *cites: str) -> None:
        if sentence and sentence not in self.sentences:
            self.sentences.append(sentence)
        for c in cites:
            self.cite(c)

    def cite(self, citation: str) -> None:
        if citation and citation not in self.citations:
            self.citations.append(citation)

    def cite_tool(self, key: str) -> None:
        tool = self.tools.get(key)
        if tool and tool.citation:
            self.cite(f"{tool.label}: {tool.citation}")

    def stat(self, key: str, value: Any, overwrite: bool = True) -> None:
        if value in (None, "", 0) or (not overwrite and key in self.stats):
            return
        self.stats[key] = value


# ------------------------------------------------------------------ single particle
def _import(pr: Processing, job: dict[str, Any], p: dict[str, Any]) -> None:
    m = output_meta(job, "movies", "micrographs")
    movies = bool(m.get("n_movies"))
    n = m.get("n_movies") or m.get("n_micrographs")
    apix, kv = _num(m.get("pixel_size") or p.get("pixel_size")), _num(m.get("voltage") or p.get("voltage"))
    dose, frames = _num(m.get("dose_per_frame") or p.get("dose_per_frame")), _num(m.get("frames"))
    bits = [f"pixel size {apix:g} Å" if apix else "", f"{kv:g} kV" if kv else ""]
    if dose and frames and frames > 1:
        bits.append(f"total exposure {dose * frames:.1f} e⁻/Å² in {int(frames)} frames")
        pr.stat("exposure", round(dose * frames, 1))
    elif dose:
        bits.append(f"{dose:g} e⁻/Å² per frame")
    bits = [b for b in bits if b]
    pr.say(f"{_count(n)} {'movies' if movies else 'micrographs'}" + (f" ({', '.join(bits)})" if bits else "")
           + " were processed in RELION-5.")
    pr.stat("pixel_size", apix, overwrite=False)
    pr.stat("voltage", kv, overwrite=False)
    pr.stat("n_movies", _num(n), overwrite=False)


def _live(pr: Processing, job: dict[str, Any], p: dict[str, Any]) -> None:
    movies = output_meta(job, "movies")
    mics = output_meta(job, "micrographs")
    n = movies.get("n_movies") or movies.get("n_micrographs") or mics.get("n_micrographs")
    steps = ["motion correction" + (" with MotionCor2" if p.get("implementation") == "motioncor2" else ""),
             "CTF estimation with CTFFIND"]
    if p.get("picking") not in (None, "", "none"):
        steps.append(f"particle picking ({PICKERS.get(p['picking'], p['picking'])})")
    if p.get("extract"):
        steps.append("particle extraction")
    apix, kv, dose = _num(p.get("pixel_size")), _num(p.get("voltage")), _num(p.get("dose_per_frame"))
    bits = [b for b in (f"pixel size {apix:g} Å" if apix else "", f"{kv:g} kV" if kv else "",
                        f"{dose:g} e⁻/Å² per frame" if dose else "") if b]
    pr.say(f"{_count(n)} movies".strip() + (f" ({', '.join(bits)})" if bits else "")
           + f" were pre-processed during data collection with RELION-5 ({', '.join(steps)}).", RELION3)
    pr.cite_tool("ctffind")
    pr.cite_tool("motioncor")  # RELION's own implementation follows MotionCor2's algorithm
    pr.stat("pixel_size", apix, overwrite=False)
    pr.stat("voltage", kv, overwrite=False)
    pr.stat("n_movies", _num(n), overwrite=False)
    _micrograph_stats(pr, mics)
    parts = output_meta(job, "particles")
    if parts.get("n_particles") and not pr.extracted:
        pr.extracted += 1
        pr.stat("initial_particles", _num(parts["n_particles"]), overwrite=False)


def _motioncorr(pr: Processing, job: dict[str, Any], p: dict[str, Any]) -> None:
    mc2 = p.get("implementation") == "motioncor2"
    bits = [f"{p.get('patches_x', 1)} × {p.get('patches_y', 1)} patches"]
    if p.get("dose_weighting", True):
        bits.append("dose weighting")
    if (_num(p.get("binning")) or 1) > 1:
        bits.append(f"binning by {_num(p['binning']):g}")
    pr.say("Beam-induced motion was corrected with " + ("MotionCor2" if mc2 else "RELION's implementation of the "
           "MotionCor2 algorithm") + f" ({', '.join(bits)}).", *([] if mc2 else [RELION3]))
    pr.cite_tool("motioncor")  # RELION asks to cite the MotionCor2 algorithm for its own implementation too
    dose = _num(p.get("dose_per_frame"))
    if dose:
        pr.stat("dose_per_frame", dose, overwrite=False)


def _micrograph_stats(pr: Processing, meta: dict[str, Any]) -> None:
    rng = meta.get("defocus_range")
    if isinstance(rng, (list, tuple)) and len(rng) == 2 and all(_num(v) is not None for v in rng):
        pr.stat("defocus_range", (float(rng[0]) / 1e4, float(rng[1]) / 1e4))  # Å -> μm, the last set (curated) wins
    if meta.get("n_micrographs"):
        pr.stat("n_micrographs", _num(meta["n_micrographs"]))


def _ctffind(pr: Processing, job: dict[str, Any], p: dict[str, Any]) -> None:
    m = output_meta(job, "micrographs")
    bits = [f"fitted between {_num(p.get('res_min')) or 30:g} and {_num(p.get('res_max')) or 5:g} Å"]
    if p.get("use_power_spectra", True):
        bits.append("on the power spectra of the motion correction")
    if m.get("ctf_resolution"):
        bits.append(f"median fit resolution {m['ctf_resolution']:.1f} Å")
    pr.say(f"The contrast transfer function was estimated with CTFFIND ({', '.join(bits)}).")
    pr.cite_tool("ctffind")
    _micrograph_stats(pr, m)


def _curate(pr: Processing, job: dict[str, Any], p: dict[str, Any]) -> None:
    kept, rejected = output_meta(job, "micrographs"), output_meta(job, "rejected")
    n_kept, n_rej = _num(kept.get("n_micrographs")), _num(rejected.get("n_micrographs")) or 0
    if n_kept:
        pr.say(f"Micrographs were curated on their CTF fit, defocus, astigmatism and motion: {int(n_kept):,} of "
               f"{int(n_kept + n_rej):,} were kept.")
    _micrograph_stats(pr, kept)


def _denoise(pr: Processing, job: dict[str, Any], p: dict[str, Any]) -> None:
    pr.say("Micrographs were denoised with Topaz-Denoise for particle picking.", TOPAZ_DENOISE)


def _autopick(pr: Processing, job: dict[str, Any], p: dict[str, Any]) -> None:
    method = p.get("method", "log")
    n = output_meta(job, "coordinates").get("n_particles")
    detail = ""
    if method == "log" and p.get("diameter_min") and p.get("diameter_max"):
        detail = f" (particle diameter {_num(p['diameter_min']):g}-{_num(p['diameter_max']):g} Å)"
    head = f"{_count(n)} particles were picked" if n else "Particles were picked"
    pr.say(f"{head} with {PICKERS.get(method, method)}{detail}.",
           *{"log": [RELION3], "template": [TEMPLATE_PICKING], "topaz": [TOPAZ]}.get(method, []))
    if p.get("helical"):
        pr.cite(HELICAL)


def _junk(pr: Processing, job: dict[str, Any], p: dict[str, Any]) -> None:
    rej = output_meta(job, "rejected").get("n_particles") or output_meta(job, "rejected_particles").get("n_particles")
    pr.say((f"{_count(rej)} picks" if rej else "Picks") + " on ice contamination and support-film edges (background "
           "outliers of the micrographs) were removed.")


def _topaz_train(pr: Processing, job: dict[str, Any], p: dict[str, Any]) -> None:
    pr.say("A Topaz picking model was trained on particles from selected 2D classes.", TOPAZ)


def _extract(pr: Processing, job: dict[str, Any], p: dict[str, Any]) -> None:
    m = output_meta(job, "particles")
    n, box, apix = m.get("n_particles"), m.get("box"), _num(m.get("pixel_size"))
    full = m.get("extract_box") or p.get("box")
    if m.get("reextracted") or pr.extracted:
        s = f"Particles ({_count(n)}) were re-extracted in boxes of {box} pixels"
    else:
        s = f"{_count(n)} particles were extracted in boxes of {full or box} pixels"
        if full and box and int(full) != int(box):
            s += f", downscaled to {box} pixels"
        pr.stat("initial_particles", _num(n), overwrite=False)
    pr.extracted += 1
    pr.say(s + (f" ({apix:g} Å/pixel)." if apix else "."))


def _class2d(pr: Processing, job: dict[str, Any], p: dict[str, Any]) -> None:
    algo = ("the VDAM algorithm" + (f" ({p['vdam_batches']} mini-batches)" if p.get("vdam_batches") else "")
            if p.get("algorithm", "vdam") == "vdam" else f"expectation-maximisation ({p.get('em_iterations', 25)} iterations)")
    pr.say(f"Reference-free 2D classification into {p.get('classes', 50)} classes used {algo}.")
    if p.get("helical"):
        pr.cite(HELICAL)


def _select2d(pr: Processing, job: dict[str, Any], p: dict[str, Any]) -> None:
    m = output_meta(job, "particles")
    classes = m.get("classes") or []
    how = {"auto": " chosen with RELION's class ranker", "reference": " chosen by their correlation with projections of a "
           "reference map"}.get(p.get("mode", ""), "")
    pr.say(f"{_count(m.get('n_particles'))} particles in {len(classes) or 'the'} selected 2D classes{how} were kept.")


def _initial_model(pr: Processing, job: dict[str, Any], p: dict[str, Any]) -> None:
    sym = _sym(p, {})
    k = int(p.get("classes") or 1)
    pr.say((f"{k} initial models were" if k > 1 else "An initial model was") + " generated de novo with the VDAM algorithm"
           + (f" in C1, then aligned on the {sym} symmetry axes" if sym != "C1" and p.get("run_c1", True) else
              f" with {sym} symmetry" if sym != "C1" else "") + ".")


def _class3d(pr: Processing, job: dict[str, Any], p: dict[str, Any]) -> None:
    align = p.get("alignment", "global")
    how = {"none": "without image alignment", "local": "with local angular searches"}.get(align, "with global angular searches")
    sym = _sym(p, output_meta(job, "particles"))
    pr.say(f"The particles were sorted by 3D classification into {p.get('classes', 4)} classes ({p.get('iterations', 25)} "
           f"iterations, {how}"
           + (f", {sym} symmetry" if sym != "C1" else "") + (", Blush regularisation" if p.get("blush") else "") + ").")


def _hetero(pr: Processing, job: dict[str, Any], p: dict[str, Any]) -> None:
    k = len([o for o in job.get("outputs") or [] if str(o.get("name", "")).startswith("class_")])
    pr.say(f"Particles were sorted by heterogeneous refinement against {k or 'several'} references.")


def _select3d(pr: Processing, job: dict[str, Any], p: dict[str, Any]) -> None:
    m = output_meta(job, "particles")
    classes = m.get("classes") or []
    pr.say(f"{_count(m.get('n_particles'))} particles from {len(classes) or 'the'} selected 3D "
           f"class{'es' if len(classes) != 1 else ''} were kept.")


def _refine(pr: Processing, job: dict[str, Any], p: dict[str, Any], what: str = "3D auto-refinement") -> None:
    parts = output_meta(job, "particles")
    half = output_meta(job, "half_maps", "map")
    sharp = output_meta(job, "map_sharp")
    sym = _sym(p, parts)
    res = _num(sharp.get("resolution") or half.get("resolution") or output_meta(job, "map").get("resolution"))
    n = parts.get("n_particles")
    what = what[0].upper() + what[1:]
    s = f"{what} of {_count(n)} particles" if n else what
    extras = [f"{sym} symmetry" if sym != "C1" else "", "Blush regularisation" if p.get("blush") else "",
              f"symmetry relaxed to {p['relax_sym']}" if str(p.get("relax_sym") or "").strip() else "",
              "solvent-flattened FSC" if p.get("solvent_fsc") else ""]
    extras = [e for e in extras if e]
    s += f" ({', '.join(extras)})" if extras else ""
    s += f" gave a map at {res:.2f} Å resolution (gold-standard FSC = 0.143)" if res else " was run with two independent half sets"
    b = _num(sharp.get("bfactor"))
    if b is not None:
        s += f"; post-processing with a soft mask sharpened it with a B-factor of {b:.0f} Å²"
        pr.cite(NOISE_SUBSTITUTION)
        pr.cite(BFACTOR)
        pr.stat("bfactor", b)
    pr.say(s + ".", GOLD_STANDARD)
    if str(p.get("relax_sym") or "").strip():
        pr.cite(SYMMETRY_RELAXATION)
    if p.get("helical"):
        pr.cite(HELICAL)
    if n:
        pr.stat("final_particles", _num(n))
    if res:
        pr.stat("resolution", res)
    if sym:
        pr.stat("symmetry", sym)


def _local_refine(pr: Processing, job: dict[str, Any], p: dict[str, Any]) -> None:
    _refine(pr, job, p, "focused refinement with a mask")


def _ctf_refine(pr: Processing, job: dict[str, Any], p: dict[str, Any]) -> None:
    if p.get("mode", "complete") == "complete":
        pr.say("Anisotropic magnification, higher-order aberrations (beam tilt, trefoil, fourth order), then per-particle "
               "defocus and per-micrograph astigmatism were refined with CTF refinement.", RELION3, ABERRATIONS)
        return
    fits = [name for name, on in (("per-particle defocus" if p.get("fit_defocus") == "particle" else
                                   "per-micrograph defocus" if p.get("fit_defocus") == "micrograph" else "", True),
                                  ("astigmatism", p.get("fit_astigmatism", "no") != "no"),
                                  ("B-factors", p.get("fit_bfactor", "no") != "no"),
                                  ("phase shift", p.get("fit_phase", "no") != "no"),
                                  ("beam tilt", p.get("beamtilt")), ("trefoil", p.get("trefoil")),
                                  ("fourth-order aberrations", p.get("fourth_order")),
                                  ("anisotropic magnification", p.get("aniso_mag"))) if name and on]
    higher = p.get("beamtilt") or p.get("trefoil") or p.get("fourth_order") or p.get("aniso_mag")
    pr.say(f"CTF parameters were refined ({', '.join(fits) or 'defocus'}).", RELION3, *([ABERRATIONS] if higher else []))


def _polish(pr: Processing, job: dict[str, Any], p: dict[str, Any]) -> None:
    if p.get("mode") == "train":
        pr.say("Bayesian polishing parameters were trained on a subset of the particles.", POLISHING)
    else:
        pr.say("Per-particle beam-induced motion and radiation damage were corrected by Bayesian polishing.", POLISHING)


def _postprocess(pr: Processing, job: dict[str, Any], p: dict[str, Any]) -> None:
    m = output_meta(job, "map_sharp")
    res, b = _num(m.get("resolution")), _num(m.get("bfactor"))
    s = "The map was post-processed with a soft mask"
    if b is not None:
        s += f" and sharpened with a B-factor of {b:.0f} Å²" + (" (estimated automatically)" if not p.get("bfactor") else "")
        pr.stat("bfactor", b)
    if res:
        s += f"; the mask-corrected gold-standard FSC gave {res:.2f} Å"
        pr.stat("resolution", res)
    pr.say(s + ".", GOLD_STANDARD, NOISE_SUBSTITUTION, *([BFACTOR] if b is not None and not p.get("bfactor") else []))


def _locres(pr: Processing, job: dict[str, Any], p: dict[str, Any]) -> None:
    pr.say("Local resolution was estimated with RELION.")


def _mask(pr: Processing, job: dict[str, Any], p: dict[str, Any]) -> None:
    pr.say(f"A soft mask was made from the map low-pass filtered to {_num(p.get('lowpass')) or 15:g} Å, extended by "
           f"{p.get('extend', 3)} pixels with a {p.get('soft_edge', 6)}-pixel soft edge.")


def _subtract(pr: Processing, job: dict[str, Any], p: dict[str, Any]) -> None:
    pr.say("The signal outside a mask was subtracted from the particle images"
           + (", which were re-centred on the mask" if p.get("recenter", True) else "") + ".", SUBTRACTION)


def _expand(pr: Processing, job: dict[str, Any], p: dict[str, Any]) -> None:
    sym = str(p.get("symmetry") or "").strip().upper()
    pr.say(f"Particles were symmetry-expanded{f' ({sym})' if sym else ''}.")


def _multibody(pr: Processing, job: dict[str, Any], p: dict[str, Any]) -> None:
    n = len([o for o in job.get("outputs") or [] if str(o.get("name", "")).startswith("body_")])
    pr.say(f"Multi-body refinement with {n or 'several'} rigid bodies described the motions between domains.", MULTIBODY)


def _dynamight(pr: Processing, job: dict[str, Any], p: dict[str, Any]) -> None:
    pr.say("Continuous conformational heterogeneity was analysed with DynaMight.", DYNAMIGHT)


def _reconstruct(pr: Processing, job: dict[str, Any], p: dict[str, Any]) -> None:
    res = _num(output_meta(job, "map", "half_maps").get("resolution"))
    pr.say("A map was reconstructed from the particles with relion_reconstruct"
           + (f" ({res:.2f} Å, gold-standard FSC = 0.143)" if res else "") + ".")


def _gain(pr: Processing, job: dict[str, Any], p: dict[str, Any]) -> None:
    pr.say("A gain reference was estimated from the movies.")


def _optics(pr: Processing, job: dict[str, Any], p: dict[str, Any]) -> None:
    n = output_meta(job, "particles", "micrographs", "movies").get("optics_groups")
    pr.say(f"Images were assigned to {n or 'several'} optics groups.")


def _align_symmetry(pr: Processing, job: dict[str, Any], p: dict[str, Any]) -> None:
    sym = output_meta(job, "map").get("aligned_to") or p.get("symmetry")
    if sym:
        pr.say(f"The map was aligned on the {sym} symmetry axes with relion_align_symmetry.")


# ------------------------------------------------------------------ tomography
def _tomo_import(pr: Processing, job: dict[str, Any], p: dict[str, Any]) -> None:
    m = output_meta(job, "tiltseries")
    n, apix, kv = m.get("n_tomograms"), _num(m.get("pixel_size") or p.get("pixel_size")), _num(m.get("voltage") or p.get("voltage"))
    rng = m.get("tilt_range")
    bits = [f"pixel size {apix:g} Å" if apix else "", f"{kv:g} kV" if kv else "",
            f"tilts from {rng[0]:g}° to {rng[1]:g}°" if isinstance(rng, (list, tuple)) and len(rng) == 2 else "",
            f"{_num(p.get('dose')):g} e⁻/Å² per {'frame' if p.get('dose_per_frame') else 'tilt'}" if _num(p.get("dose")) else ""]
    bits = [b for b in bits if b]
    pr.say(f"{_count(n)} tilt series" + (f" ({', '.join(bits)})" if bits else "") + " were processed with the "
           "tomography pipeline of RELION-5.", RELION5_TOMO)
    pr.stat("pixel_size", apix, overwrite=False)
    pr.stat("voltage", kv, overwrite=False)
    pr.stat("n_tilt_series", _num(n), overwrite=False)


def _tomo_motioncorr(pr: Processing, job: dict[str, Any], p: dict[str, Any]) -> None:
    mc2 = p.get("implementation") == "motioncor2"
    pr.say("Motion within the tilt movies was corrected with " + ("MotionCor2" if mc2 else "RELION's implementation of "
           "the MotionCor2 algorithm") + (", saving even and odd frame sums" if p.get("even_odd") else "") + ".")
    pr.cite_tool("motioncor")


def _tomo_ctffind(pr: Processing, job: dict[str, Any], p: dict[str, Any]) -> None:
    pr.say("The CTF of every tilt image was estimated with CTFFIND.")
    pr.cite_tool("ctffind")


def _tomo_exclude(pr: Processing, job: dict[str, Any], p: dict[str, Any]) -> None:
    pr.say("Poor tilt images were excluded.")


def _tomo_align(pr: Processing, job: dict[str, Any], p: dict[str, Any]) -> None:
    method = p.get("method", "aretomo")
    if method == "aretomo":
        pr.say("Tilt series were aligned with AreTomo2.")
        pr.cite_tool("aretomo")
    else:
        pr.say("Tilt series were aligned with IMOD (" + ("fiducials" if "fid" in str(method) else "patch tracking") + ").")
        pr.cite_tool("imod")


def _tomo_reconstruct(pr: Processing, job: dict[str, Any], p: dict[str, Any]) -> None:
    m = output_meta(job, "tomograms")
    px = _num(m.get("binned_pixel_size") or p.get("binned_pixel"))
    pr.say("Tomograms were reconstructed" + (f" at {px:g} Å/voxel" if px else "") + " in RELION.")


def _tomo_denoise(pr: Processing, job: dict[str, Any], p: dict[str, Any]) -> None:
    pr.say("Tomograms were denoised with cryoCARE (trained on the tomograms of the even and odd frames).")
    pr.cite_tool("cryocare")


def _tomo_pick(pr: Processing, job: dict[str, Any], p: dict[str, Any]) -> None:
    n = output_meta(job, "particles").get("n_particles")
    pr.say((f"{_count(n)} particles were" if n else "Particles were") + " picked in the tomograms.")


def _template_match(pr: Processing, job: dict[str, Any], p: dict[str, Any]) -> None:
    n = output_meta(job, "particles").get("n_particles")
    how = [f"angular step {_num(p['angular_step']):g}°" if _num(p.get("angular_step")) else "angular sampling from the "
           "particle diameter (Crowther criterion)",
           "template weighted per tilt image with its tilt angle, defocus and dose" if p.get("per_tilt", True) else "",
           "random-phase correction" if p.get("random_phase", True) else "",
           "tophat filtering of the score maps" if p.get("tophat", True) else ""]
    how = [h for h in how if h]
    pr.say((f"{_count(n)} particles were" if n else "Particles were") + " located by template matching with "
           f"pytom-match-pick ({', '.join(how)}).")


def _tomo_import_coords(pr: Processing, job: dict[str, Any], p: dict[str, Any]) -> None:
    n = output_meta(job, "particles").get("n_particles")
    pr.say((f"The coordinates of {_count(n)} particles were" if n else "Particle coordinates were") + " imported.")


def _tomo_extract(pr: Processing, job: dict[str, Any], p: dict[str, Any]) -> None:
    m = output_meta(job, "particles")
    n, box, apix = m.get("n_particles"), m.get("box"), _num(m.get("pixel_size"))
    pr.say(f"{_count(n)} pseudo-subtomograms were extracted" + (f" in boxes of {box} pixels" if box else "")
           + (f" ({apix:g} Å/pixel)" if apix else "") + ".")
    if not pr.extracted:
        pr.stat("initial_particles", _num(n), overwrite=False)
    pr.extracted += 1


def _tomo_ctf_refine(pr: Processing, job: dict[str, Any], p: dict[str, Any]) -> None:
    fits = [f for f, on in (("defocus", p.get("defocus", True)), ("per-tilt scale", p.get("per_frame_scale")),
                            ("per-tomogram scale", p.get("per_tomo_scale"))) if on]
    pr.say(f"CTF parameters of the tilt series were refined ({', '.join(fits) or 'defocus'}).")


def _tomo_frame_align(pr: Processing, job: dict[str, Any], p: dict[str, Any]) -> None:
    pr.say("The alignment of the tilt images" + (" and the motion of the particles" if p.get("motion") else "")
           + " were refined by Bayesian polishing.", POLISHING)


def _tomo_reconstruct_particle(pr: Processing, job: dict[str, Any], p: dict[str, Any]) -> None:
    res = _num(output_meta(job, "map", "half_maps").get("resolution"))
    pr.say("The subtomogram average was reconstructed from the tilt-series images"
           + (f" ({res:.2f} Å, gold-standard FSC = 0.143)" if res else "") + ".")
    if res:
        pr.stat("resolution", res)


BUILDERS: dict[str, Callable[[Processing, dict[str, Any], dict[str, Any]], None]] = {
    "relion_import": _import, "relion_live": _live, "relion_motioncorr": _motioncorr, "relion_ctffind": _ctffind,
    "relion_curate": _curate, "relion_topaz_denoise": _denoise, "relion_autopick": _autopick,
    "relion_topaz_train": _topaz_train, "micrograph_junk": _junk, "relion_extract": _extract, "relion_class2d": _class2d,
    "relion_select2d": _select2d, "relion_initial_model": _initial_model, "relion_class3d_global": _class3d,
    "relion_class3d": _class3d, "relion_hetero_refine": _hetero, "relion_select3d": _select3d,
    "relion_refine3d": _refine, "relion_local_refine": _local_refine, "relion_ctf_refine": _ctf_refine,
    "relion_polish": _polish, "relion_postprocess": _postprocess, "relion_locres": _locres,
    "relion_mask_create": _mask, "relion_subtract": _subtract, "relion_symmetry_expand": _expand,
    "relion_multibody": _multibody, "relion_dynamight": _dynamight, "relion_reconstruct": _reconstruct,
    "relion_estimate_gain": _gain, "relion_optics_groups": _optics, "relion_align_symmetry": _align_symmetry,
    "relion_tomo_import": _tomo_import, "relion_tomo_motioncorr": _tomo_motioncorr, "relion_tomo_ctffind": _tomo_ctffind,
    "relion_tomo_exclude": _tomo_exclude, "relion_tomo_align": _tomo_align, "relion_tomo_reconstruct": _tomo_reconstruct,
    "relion_tomo_denoise": _tomo_denoise, "relion_tomo_pick": _tomo_pick, "relion_tomo_import_coords": _tomo_import_coords,
    "pytom_template_match": _template_match,
    "relion_tomo_extract": _tomo_extract,
    "relion_tomo_initial_model": _initial_model, "relion_tomo_class3d": _class3d,
    "relion_tomo_refine3d": lambda pr, job, p: _refine(pr, job, p, "subtomogram auto-refinement"),
    "relion_tomo_ctf_refine": _tomo_ctf_refine, "relion_tomo_frame_align": _tomo_frame_align,
    "relion_tomo_reconstruct_particle": _tomo_reconstruct_particle,
}
