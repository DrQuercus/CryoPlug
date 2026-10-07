"""Heterogeneity: CryoSPARC particles, cryoDRGN jobs (with a fake cryodrgn), volume series and 3D variability."""
from __future__ import annotations

import json
import pickle
import zipfile
from pathlib import Path

import numpy as np
import pytest
from conftest import log_of, run_until_done

from cryoplug import particles as pt
from cryoplug.jobs.heterogeneity import parse_clusters
from cryoplug.jobs.series import (
    chain_densities,
    chain_regions,
    group_series,
    short_labels,
)


def _run(manager, puid, jtype, params, inputs=None, ok=True):
    job = manager.create_job(puid, jtype, params, inputs or {})
    manager.queue_job(puid, job["uid"])
    run_until_done(manager, puid, [job["uid"]], timeout=180)
    done = manager.job(puid, job["uid"])
    if ok:
        assert done["status"] == "completed", f"{done['error']}\n{log_of(manager, puid, job['uid'])}"
    return done


def _outs(job):
    return {o["name"]: o for o in job["outputs"]}


def _view(manager, puid, job):
    sec = next(s for s in manager.job_detail(puid, job["uid"])["report"]["sections"] if s["kind"] == "latent")
    return json.loads((manager.project_dir(puid) / sec["path"]).read_text())


# ------------------------------------------------------------------ helpers
def test_cs_files_are_merged_by_uid(cs_particles, tmp_path):
    main, through = pt.find_particle_files(cs_particles["refine"])
    assert main.name == "J5_005_particles.cs" and through.name == "J5_passthrough_particles.cs"
    merged = pt.merged_particles(main, through)
    raw = np.load(main)
    assert len(merged) == cs_particles["n"] and "ctf/df1_A" in merged.dtype.names
    # the passthrough rows were shuffled: values must follow the uid
    passthrough = np.load(through)
    lookup = dict(zip(passthrough["uid"].tolist(), passthrough["ctf/df1_A"].tolist()))
    assert all(lookup[u] == d for u, d in zip(raw["uid"].tolist()[:50], merged["ctf/df1_A"].tolist()[:50]))
    info = pt.summary(merged)
    assert info == {"n_particles": 300, "box": 32, "pixel_size": 2.0, "n_stacks": 2, "has_images": True,
                    "has_poses": True, "has_ctf": True}
    back = pt.read_cs(pt.write_cs(merged[:7], tmp_path / "subset.cs"))
    assert back.dtype == merged.dtype and len(back) == 7 and (tmp_path / "subset.cs").exists()


def test_short_frame_labels():
    assert short_labels(["Cluster 1 · 5.9 %", "Cluster 2 · 7.7 %"]) == ["Cluster 1", "Cluster 2"]
    assert short_labels(["PC1 · 1/3", "PC1 · 2/3", "PC1 · 3/3"]) == ["1/3", "2/3", "3/3"]
    assert short_labels(["Component 0 · frame 1", "Component 0 · frame 2"]) == ["frame 1", "frame 2"]
    assert short_labels(["1/2 · cluster 4", "2/2 · cluster 9"]) == ["1/2", "2/2"]
    assert short_labels(["same", "same"]) == ["Frame 1", "Frame 2"]


def test_chain_densities_show_an_absent_chain():
    """Raw frames with a background offset: chain B present, half present, then absent; chain A always there."""
    import gemmi

    from cryoplug.demo import build_helix
    from cryoplug.mrc import MapVolume, model_map
    st = gemmi.Structure()
    model = gemmi.Model(1)
    model.add_chain(build_helix("AKLEAKLEAKLEAKLE", (20.0, 25.0, 25.0), (0, 0, 1), "A"))
    model.add_chain(build_helix("EKLAEKLAEKLAEKLA", (31.0, 25.0, 25.0), (0, 0, 1), "B"))
    st.add_model(model)
    grid = MapVolume(data=np.zeros((50, 50, 50), np.float32), voxel=np.array([1.0, 1.0, 1.0]))
    frames = []
    for occ_b in (1.0, 0.5, 0.0):
        parts = [(np.array([[a.pos.x, a.pos.y, a.pos.z] for r in ch for a in r]), 1.0 if ch.name == "A" else occ_b) for ch in st[0]]
        xyz = np.concatenate([x for x, _w in parts])
        w = np.concatenate([np.full(len(x), wt) for x, wt in parts])
        frames.append(model_map(xyz, w, grid, 6.0) + 5.0)  # + a solvent level that must not count as density
    chains, solvent = chain_regions(grid, st)
    assert [n for n, _r in chains] == ["A", "B"] and solvent.sum() > 1000
    occ = chain_densities(np.stack(frames), chains, solvent)
    assert occ.shape == (2, 3)
    assert occ[1, 0] > 0.8 and 0.3 < occ[1, 1] < 0.8 and occ[1, 2] < 0.15  # chain B fades
    assert occ[0, 2] > 1.2 and occ[1, 2] < 0.15 * occ[0, 2]  # alone, A carries the density of the model


def test_pickles_are_read_as_arrays_only(tmp_path):
    good = pt.save_array_pkl(np.arange(5), tmp_path / "ind.pkl")
    assert pt.load_array_pkl(good).tolist() == [0, 1, 2, 3, 4]
    evil = tmp_path / "evil.pkl"

    class Boom:
        def __reduce__(self):
            return (print, ("pwned",))
    evil.write_bytes(pickle.dumps(Boom()))
    with pytest.raises(pickle.UnpicklingError):
        pt.load_array_pkl(evil)


def test_small_helpers():
    assert parse_clusters("1, 4-6", 20) == [0, 3, 4, 5]
    with pytest.raises(ValueError, match="does not exist"):
        parse_clusters("21", 20)
    rng = np.random.default_rng(0)
    z = np.concatenate([rng.normal(0, 0.1, (100, 3)), rng.normal(5, 0.1, (100, 3))])
    labels, centres = pt.kmeans(z, 2)
    assert len(set(labels[:100])) == 1 and len(set(labels[100:])) == 1 and labels[0] != labels[150]
    proj, var = pt.pca2(z)
    assert proj.shape == (200, 2) and var[0] > 95
    groups = group_series([Path(f"/x/J10_component_{c:03d}_frame_{f:03d}.mrc") for c in (1, 0) for f in (2, 0, 1)])
    assert list(groups) == ["component 0", "component 1"]
    assert [p.name for p in groups["component 0"]] == [f"J10_component_000_frame_{f:03d}.mrc" for f in (0, 1, 2)]


# ------------------------------------------------------------------ jobs
@pytest.fixture()
def het_project(manager, cs_particles):
    p = manager.create_project("Heterogeneity")
    puid = p["uid"]
    imp = _run(manager, puid, "import_cryosparc", {"job_dir": str(cs_particles["refine"])})
    return puid, imp


def test_cryosparc_particles_import(manager, cs_particles, het_project):
    puid, imp = het_project
    part = _outs(imp)["particles"]
    assert part["type"] == "particles" and part["meta"]["n_particles"] == 300
    assert part["meta"]["datadir"] == str(cs_particles["root"].resolve())
    merged = pt.read_cs(manager.project_dir(puid) / part["path"])
    assert "ctf/df1_A" in merged.dtype.names and "alignments3D/pose" in merged.dtype.names
    assert {h["label"]: h["value"] for h in imp["highlights"]}["Particles"] == "300"


def test_cryodrgn_pipeline(manager, het_project, synthetic):
    puid, imp = het_project
    train = _run(manager, puid, "cryodrgn_train", {"box": 16, "zdim": 4, "epochs": 3, "ksample": 6},
                 {"particles": {"job": imp["uid"], "output": "particles"}})
    log = log_of(manager, puid, train["uid"])
    assert "parse_pose_csparc" in log and "-D 32" in log and "parse_ctf_csparc" in log
    assert "downsample" in log and "--datadir" in log and "--no-analysis" in log and "--ksample 6" in log
    outs = _outs(train)
    assert {"latent", "kmeans", "pc1", "pc2", "particles_prepared"} <= set(outs)
    assert outs["kmeans"]["type"] == "volume_series" and len(outs["kmeans"]["files"]) == 6
    assert outs["kmeans"]["meta"]["frame_labels"][0].startswith("Cluster 1 · ")
    assert outs["latent"]["meta"]["method"] == "cryoDRGN" and outs["latent"]["meta"]["zdim"] == 4
    assert outs["latent"]["meta"]["apix"] == 4.0
    view = _view(manager, puid, train)
    assert view["n_total"] == 300 and set(view["embeddings"]) == {"UMAP", "PCA"}
    assert len(view["clusters"]) == 6 and view["clusters"][0]["volume"].endswith("vol_001.mrc")
    assert sum(c["count"] for c in view["clusters"]) == 300
    kinds = [s["kind"] for s in manager.job_detail(puid, train["uid"])["report"]["sections"]]
    assert {"plot", "latent", "heatmap", "table", "image"} <= set(kinds)
    assert train["outputs"] and outs["kmeans"].get("thumbnail")

    # analysis with more clusters, then a trajectory between two clusters
    latent = {"latent": {"job": train["uid"], "output": "latent"}}
    ana = _run(manager, puid, "cryodrgn_analyze", {"ksample": 8, "n_per_pc": 5}, latent)
    assert len(_outs(ana)["kmeans"]["files"]) == 8 and len(_outs(ana)["pc1"]["files"]) == 5
    traj = _run(manager, puid, "cryodrgn_trajectory", {"clusters": "1, 6", "frames": 6}, latent)
    tout = _outs(traj)["trajectory"]
    assert tout["type"] == "volume_series" and 2 <= len(tout["files"]) <= 6
    assert "graph_traversal" in log_of(manager, puid, traj["uid"]) and "--Apix 4" in log_of(manager, puid, traj["uid"])
    assert _view(manager, puid, traj)["paths"][0]["points"]["UMAP"]

    # keep three clusters, then train again on them: the downsampled images are reused with --ind
    sel = _run(manager, puid, "select_particles", {"clusters": "1-3"}, latent)
    sub = _outs(sel)["particles"]
    kept = pt.read_cs(manager.project_dir(puid) / sub["path"])
    assert len(kept) == sub["meta"]["n_particles"] == 150 and sub["meta"]["datadir"] == _outs(imp)["particles"]["meta"]["datadir"]
    assert sub["meta"]["parent_cryodrgn"]["box"] == 16
    assert _view(manager, puid, sel)["selected"] == [0, 1, 2]
    again = _run(manager, puid, "cryodrgn_train", {"box": 16, "zdim": 4, "epochs": 2, "ksample": 4},
                 {"particles": {"job": sel["uid"], "output": "particles"}})
    log2 = log_of(manager, puid, again["uid"])
    assert "Reusing the 16 px particles of the parent dataset" in log2 and "--ind" in log2 and "downsample" not in log2
    assert _outs(again)["latent"]["meta"]["n_particles"] == 150
    # a second selection on the retrained subset composes the indices
    sel2 = _run(manager, puid, "select_particles", {"clusters": "1", "action": "remove"},
                {"latent": {"job": again["uid"], "output": "latent"}})
    parent_ind = pt.load_array_pkl(manager.project_dir(puid) / _outs(sel2)["particles"]["meta"]["parent_indices"])
    first_ind = pt.load_array_pkl(manager.project_dir(puid) / _outs(sel)["particles"]["meta"]["parent_indices"])
    assert set(parent_ind.tolist()) <= set(first_ind.tolist())

    # series analysis of the cluster volumes, with the model
    model = _run(manager, puid, "import_model", {"source": "file", "path": str(synthetic["model"])})
    series = _run(manager, puid, "series_analysis", {}, {"series": {"job": train["uid"], "output": "kmeans"},
                                                         "model": {"job": model["uid"], "output": "model"}})
    sout = _outs(series)
    assert sout["variability"]["type"] == "variability" and sout["variability"]["meta"]["colour_map"] == sout["mean"]["path"]
    lo, hi = sout["variability"]["meta"]["display_range"]
    assert 0 <= lo < hi
    titles = [s["title"] for s in manager.job_detail(puid, series["uid"])["report"]["sections"]]
    assert "Correlation between frames" in titles and any("Density of each chain" in t for t in titles)
    one = _run(manager, puid, "extract_volume", {"frame": 2}, {"series": {"job": train["uid"], "output": "kmeans"}})
    assert _outs(one)["map"]["label"].startswith("Cluster 2")


def test_3d_variability_results(manager, cs_particles, tmp_path):
    p = manager.create_project("3DVA")
    puid = p["uid"]
    imp = _run(manager, puid, "import_cryosparc", {"job_dir": str(cs_particles["variability"])})
    lat = _outs(imp)["latent"]
    assert lat["meta"]["method"] == "3DVA" and lat["meta"]["zdim"] == 2
    view = _view(manager, puid, imp)
    assert list(view["embeddings"]) == ["Components"] and view["embeddings"]["Components"]["xlabel"] == "Component 1"
    sel = _run(manager, puid, "select_particles", {"clusters": "1"}, {"latent": {"job": imp["uid"], "output": "latent"}})
    assert 0 < _outs(sel)["particles"]["meta"]["n_particles"] < 300
    # 3D variability display: one series per component, from the folder and from a ZIP file
    ser = _run(manager, puid, "import_volume_series", {"path": str(cs_particles["display"])})
    outs = _outs(ser)
    assert set(outs) == {"series", "series_2"} and len(outs["series"]["files"]) == 5
    assert outs["series"]["meta"]["frame_labels"][0] == "Component 0 · frame 1"
    archive = tmp_path / "component_000.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for f in sorted(cs_particles["display"].glob("J10_component_000_*.mrc")):
            zf.write(f, f"J10_series_000/{f.name}")
        zf.writestr("../escape.mrc", b"x")
    zipped = _run(manager, puid, "import_volume_series", {"path": str(archive)})
    assert len(_outs(zipped)["series"]["files"]) == 5
    assert not (manager.project_dir(puid) / "escape.mrc").exists()
    ana = _run(manager, puid, "series_analysis", {}, {"series": {"job": ser["uid"], "output": "series"}})
    corr = next(s for s in manager.job_detail(puid, ana["uid"])["report"]["sections"] if s["title"] == "Correlation between frames")
    assert corr["values"][0][0] == pytest.approx(1.0) and corr["values"][0][-1] < corr["values"][0][1]


def test_cryodrgn_needs_poses(manager, cs_particles, tmp_path):
    no_pose = tmp_path / "J7"
    no_pose.mkdir()
    data = np.load(cs_particles["refine"] / "J5_passthrough_particles.cs")
    with open(no_pose / "J7_particles.cs", "wb") as fh:
        np.save(fh, data)
    p = manager.create_project("No poses")
    imp = _run(manager, p["uid"], "import_particles", {"path": str(no_pose / "J7_particles.cs")}, ok=False)
    assert imp["status"] == "failed" and "No usable particles" in imp["error"]


def test_3dva_without_passthrough_and_empty_imports(manager, cs_particles, tmp_path):
    """A 3D variability job whose passthrough file is missing: the coordinates are still explored; a folder
    with nothing usable fails instead of completing empty."""
    src = np.load(cs_particles["variability"] / "J9_particles.cs")
    keep = [n for n in src.dtype.names if not n.startswith("blob/")]
    va = tmp_path / "J30"
    va.mkdir()
    with open(va / "J30_particles.cs", "wb") as fh:
        np.save(fh, src[keep])
    p = manager.create_project("3DVA without images")
    imp = _run(manager, p["uid"], "import_cryosparc", {"job_dir": str(va)})
    assert set(_outs(imp)) == {"latent"} and _outs(imp)["latent"]["meta"]["method"] == "3DVA"
    assert "only the 3D variability coordinates are imported" in log_of(manager, p["uid"], imp["uid"])
    empty = tmp_path / "J31"
    empty.mkdir()
    with open(empty / "J31_particles.cs", "wb") as fh:
        np.save(fh, src[["uid", "alignments3D/pose"]])
    bad = _run(manager, p["uid"], "import_cryosparc", {"job_dir": str(empty)}, ok=False)
    assert bad["status"] == "failed" and "Nothing could be imported" in bad["error"]


def test_heterogeneity_workflows(manager, cs_particles):
    p = manager.create_project("Workflows")
    res = manager.instantiate_workflow(p["uid"], "heterogeneity_cryodrgn",
                                       overrides={"import": {"job_dir": str(cs_particles["refine"])}}, queue=False)
    by_type = {j["type"]: j for j in res["jobs"]}
    assert set(by_type) == {"import_cryosparc", "cryodrgn_train", "series_analysis"}  # model and PC1 off by default
    assert by_type["cryodrgn_train"]["inputs"]["particles"]["job"] == by_type["import_cryosparc"]["uid"]
    assert by_type["series_analysis"]["inputs"] == {"series": {"job": by_type["cryodrgn_train"]["uid"], "output": "kmeans"}}
    res = manager.instantiate_workflow(p["uid"], "variability_3dva", include=["model"],
                                       overrides={"import": {"job_dir": str(cs_particles["variability"])},
                                                  "frames": {"path": str(cs_particles["display"])}}, queue=False)
    ana = next(j for j in res["jobs"] if j["type"] == "series_analysis")
    assert set(ana["inputs"]) == {"series", "model"}
