"""Maps for the browser viewer: compact encodings, low-pass filter, cache, revalidation and the header endpoint."""
from __future__ import annotations

import os
import shutil
import struct
import threading
import time

import mrcfile
import numpy as np
from fastapi.testclient import TestClient

from cryoplug.mrc import VIEW_TAG, MapVolume, bin_map, view_lowpass, write_view_map
from cryoplug.server import mapview
from cryoplug.server.app import create_app
from cryoplug.server.mapview import MapViews


def decode(path):
    """What the viewer gets: i16 decoded as mapdata.js does, i8 as Mol* reads mapmode2to0, f32 as is."""
    raw = path.read_bytes()
    with mrcfile.open(path, permissive=True) as m:
        data = m.data.astype(np.float64)
        h = m.header
        stats = (float(h.dmin), float(h.dmax), float(h.dmean), float(h.rms))
        mode = int(h.mode)
    if mode == 1:
        tag, scale, offset = struct.unpack("<4sff", raw[164:176])
        assert tag == VIEW_TAG
        return offset + scale * data, stats, mode
    if mode == 0:
        assert struct.unpack("<ii", raw[156:164]) == (-128, 127)
        step = (stats[1] - stats[0]) / 255.0
        return step * data + 0.5 * (stats[0] + stats[1] + step), stats, mode
    return data, stats, mode


def test_view_encodings_round_trip(tmp_path):
    rng = np.random.default_rng(3)
    vol = MapVolume(data=rng.normal(0.1, 2.0, (22, 26, 30)).astype(np.float32), voxel=np.array([0.9, 0.9, 0.9]),
                    origin=np.array([4.5, -1.8, 0.0]))
    span = float(vol.data.max() - vol.data.min())
    sizes = {}
    for enc, tolerance in (("f32", 1e-6), ("i16", span / 65534 * 0.51), ("i8", span / 255 * 0.51)):
        path = write_view_map(vol, tmp_path / f"{enc}.mrc", enc)
        sizes[enc] = path.stat().st_size
        values, stats, _ = decode(path)
        assert np.abs(values - vol.data).max() <= tolerance, enc
        # statistics of the real values in the header: Mol* uses them (min, max, mean, sigma)
        assert np.allclose(stats, (vol.data.min(), vol.data.max(), vol.data.mean(), vol.data.std()), rtol=1e-4, atol=1e-5), enc
        again = MapVolume.read(path)
        assert np.allclose(again.origin, vol.origin) and np.allclose(again.voxel, vol.voxel), enc
    assert sizes["i16"] < 0.55 * sizes["f32"] and sizes["i8"] < 0.3 * sizes["f32"]


def test_view_lowpass_removes_fine_detail_only():
    rng = np.random.default_rng(4)
    vol = MapVolume(data=rng.normal(0, 1, (32, 32, 32)).astype(np.float32), voxel=np.array([1.0, 1.0, 1.0]))
    smooth = view_lowpass(vol, 8.0)
    f = np.abs(np.fft.rfftn(smooth.data))
    s = np.sqrt(np.add.outer(np.add.outer(np.fft.fftfreq(32) ** 2, np.fft.fftfreq(32) ** 2), np.fft.rfftfreq(32) ** 2))
    assert f[s > 1 / 6].max() < 1e-3 * f.max()  # nothing finer than ~6 Å left
    assert np.abs(f[(s > 0) & (s < 1 / 12)] - np.abs(np.fft.rfftn(vol.data))[(s > 0) & (s < 1 / 12)]).max() < 1e-2 * f.max()
    assert view_lowpass(vol, 1.5) is vol  # finer than the grid: unchanged


def test_preview_endpoint_encodings_cache_and_revalidation(manager, synthetic, tmp_path):
    p = manager.create_project("Viewer maps")
    pdir = manager.project_dir(p["uid"])
    shutil.copy(synthetic["map"], pdir / "map.mrc")
    (pdir / "notes.txt").write_text("not a map\n")
    full = MapVolume.read(synthetic["map"])
    app = create_app(manager.config, start_scheduler=False, manager=manager)
    with TestClient(app, base_url="http://localhost:39500") as client:
        url = f"/api/projects/{p['uid']}/preview"
        info = client.get(f"/api/projects/{p['uid']}/map-info", params={"path": "map.mrc"}).json()
        assert info["box"] == list(full.shape_xyz) and info["bytes"] == (pdir / "map.mrc").stat().st_size
        assert client.get(f"/api/projects/{p['uid']}/map-info", params={"path": "notes.txt"}).status_code == 400
        # full size, 16-bit: half the bytes of the original, same values to 1/65534 of the range
        r = client.get(url, params={"path": "map.mrc", "max_box": 0, "enc": "i16"})
        assert r.status_code == 200 and len(r.content) < 0.55 * (pdir / "map.mrc").stat().st_size
        (tmp_path / "i16.mrc").write_bytes(r.content)
        values, _, mode = decode(tmp_path / "i16.mrc")
        assert mode == 1 and np.abs(values - full.data).max() <= np.ptp(full.data) / 65534
        # filtered at the detail asked for (a 48³ map needs no binning for 64 voxels)
        binned = client.get(url, params={"path": "map.mrc", "max_box": 64, "enc": "f32", "lowpass": 9})
        (tmp_path / "lp.mrc").write_bytes(binned.content)
        expected = view_lowpass(bin_map(full, MapViews.factor(pdir / "map.mrc", 64)), 9.0)
        assert np.allclose(MapVolume.read(tmp_path / "lp.mrc").data, expected.data, atol=1e-5)
        # the original file as it is when nothing changes it
        assert client.get(url, params={"path": "map.mrc", "max_box": 0}).content == (pdir / "map.mrc").read_bytes()
        # revalidation: the same ETag on each use of a cached copy (its own date changes), then 304
        etag = r.headers["etag"]
        again = client.get(url, params={"path": "map.mrc", "max_box": 0, "enc": "i16"})
        assert again.headers["etag"] == etag and again.content == r.content
        assert client.get(url, params={"path": "map.mrc", "max_box": 0, "enc": "i16"}, headers={"If-None-Match": etag}).status_code == 304
        assert client.get(url, params={"path": "map.mrc", "enc": "nope"}).status_code == 400
        # a zone in 16 bits keeps hiding the far voxels at every level
        shutil.copy(synthetic["model"], pdir / "model.pdb")
        z = client.get(f"/api/projects/{p['uid']}/zone", params={"map": "map.mrc", "model": "model.pdb", "radius": 3, "max_box": 0, "enc": "i16"})
        assert z.status_code == 200
        (tmp_path / "zone.mrc").write_bytes(z.content)
        zone, _, _ = decode(tmp_path / "zone.mrc")
        assert (zone < full.data.min()).any() and np.isclose(zone.max(), full.data.max(), atol=np.ptp(full.data) / 30000)


def test_cache_trim_keeps_the_newest(tmp_path):
    for i in range(6):
        f = tmp_path / f"{i}.mrc"
        f.write_bytes(b"x" * 1000)
        t = time.time() - 100 + i
        os.utime(f, (t, t))
    MapViews.trim(tmp_path, keep=tmp_path / "0.mrc", limit=3500)
    left = sorted(f.name for f in tmp_path.glob("*.mrc"))
    assert "0.mrc" in left and "5.mrc" in left and len(left) <= 3  # down to 3/4 of the limit, oldest first
    MapViews.trim(tmp_path, limit=10_000)  # under the limit: nothing goes
    assert sorted(f.name for f in tmp_path.glob("*.mrc")) == left


def test_map_read_once_for_simultaneous_requests(tmp_path, monkeypatch):
    path = MapVolume(data=np.zeros((8, 8, 8), np.float32), voxel=np.ones(3)).write(tmp_path / "m.mrc")
    calls = []
    original = MapVolume.read.__func__

    def slow_read(cls, p, header_only=False):
        if not header_only:
            calls.append(p)
            time.sleep(0.3)
        return original(cls, p, header_only)

    monkeypatch.setattr(mapview.MapVolume, "read", classmethod(slow_read))
    views = MapViews(lambda puid: tmp_path)
    got = []
    threads = [threading.Thread(target=lambda: got.append(views.read(path))) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1 and len(got) == 3 and all(g is got[0] for g in got)
    assert views.read(path) is got[0] and len(calls) == 1  # kept for the next request
