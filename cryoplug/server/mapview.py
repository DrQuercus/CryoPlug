"""Maps for the browser viewer: binned, low-pass filtered and compactly encoded copies, cached on disk.

The cache lives in each project (``.cryoplug_cache``), is keyed by the source file (path and modification time) and
the display settings, and is trimmed to the least recently used files beyond a size limit. The last map read is kept
in memory for a couple of minutes, so that the coarse first look and the finer copy that follows (or a change of
detail, a zone) read a large map from disk only once.
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, Response

from cryoplug.mrc import VIEW_ENCODINGS, MapVolume, bin_map, preview_factor, view_lowpass, write_view_map, zone_map

CACHE_LIMIT = 3 * 1024 ** 3  # bytes of cached maps per project before the oldest go
CACHE_VERSION = 1  # part of every cache key (and so of the ETags): raise it when the files change format
RECENT_SECONDS = 120  # a cached map used this recently is never trimmed (it may be on its way to a browser)
MEMORY_LIMIT = 1.5e9  # largest map (float32 bytes) kept in memory between requests
MEMORY_SECONDS = 150
BUILDS = threading.BoundedSemaphore(2)  # copies computed at the same time (full-size maps, filters: memory)


class MapViews:
    def __init__(self, project_dir: Callable[[str], Path]):
        self.project_dir = project_dir
        self._lock = threading.Lock()
        self._trim_lock = threading.Lock()
        self._recent: tuple[tuple[str, int], MapVolume, float] | None = None
        self._reading: dict[tuple[str, int], _Reading] = {}
        self._timer: threading.Timer | None = None
        self._preview_base: tuple[tuple[str, int, int], MapVolume, dict] | None = None  # binned map of the mask editor

    # ---------------------------------------------------------------- reading
    def read(self, p: Path) -> MapVolume:
        """The map, read once for requests arriving together (a coarse look and the finer copy, a zone)."""
        key = (str(p.resolve()), p.stat().st_mtime_ns)
        with self._lock:
            if self._recent and self._recent[0] == key:
                self._recent = (key, self._recent[1], time.time())  # still in use: kept longer
                return self._recent[1]
            reading = self._reading.get(key)
            owner = reading is None
            if owner:
                reading = self._reading[key] = _Reading()
        if not owner:
            reading.done.wait()
            if reading.error is not None:
                raise reading.error
            return reading.volume
        try:
            reading.volume = MapVolume.read(p)
        except Exception as exc:
            reading.error = exc
            raise
        finally:
            with self._lock:
                self._reading.pop(key, None)
                if reading.volume is not None and reading.volume.data.nbytes <= MEMORY_LIMIT:
                    self._recent = (key, reading.volume, time.time())
                    self._arm()
            reading.done.set()
        return reading.volume

    def _arm(self, delay: float = MEMORY_SECONDS + 1) -> None:
        """One timer forgets the map kept in memory once unused for MEMORY_SECONDS (called with the lock held)."""
        if self._timer is None:
            self._timer = threading.Timer(delay, self._expire)
            self._timer.daemon = True
            self._timer.start()

    def _expire(self) -> None:
        with self._lock:
            self._timer = None
            if self._recent is None:
                return
            idle = time.time() - self._recent[2]
            if idle >= MEMORY_SECONDS:
                self._recent = None
            else:
                self._arm(MEMORY_SECONDS - idle + 1)

    @staticmethod
    def factor(p: Path, max_box: int) -> int:
        """Binning that brings the largest side to at most ``max_box`` voxels (64-512; 0: full size)."""
        if max_box <= 0:
            return 1
        return preview_factor(MapVolume.read(p, header_only=True).shape_xyz, max(64, min(max_box, 512)))

    # ------------------------------------------------------------------ cache
    def cached(self, puid: str, key: str, build: Callable[[], MapVolume], enc: str) -> Path:
        cache = self.project_dir(puid) / ".cryoplug_cache"
        cache.mkdir(exist_ok=True)
        out = cache / f"{hashlib.sha1(key.encode()).hexdigest()[:16]}.mrc"
        if out.exists():
            try:
                os.utime(out)  # most recently used: the last to be trimmed
            except OSError:
                pass
            return out
        tmp = out.with_name(f"{out.stem}.{os.getpid()}.{threading.get_ident()}.part")
        try:
            with BUILDS:
                write_view_map(build(), tmp, enc)
            os.replace(tmp, out)
        finally:
            tmp.unlink(missing_ok=True)
        with self._trim_lock:
            self.trim(cache, keep=out)
        return out

    @staticmethod
    def trim(cache: Path, keep: Path | None = None, limit: int = CACHE_LIMIT) -> None:
        """Remove the least recently used maps once the cache is over ``limit`` bytes, down to 3/4 of it; a map used
        in the last RECENT_SECONDS stays (it may be on its way to a browser)."""
        files = []
        for f in cache.glob("*.mrc"):
            try:
                st = f.stat()
            except OSError:
                continue
            files.append((st.st_mtime, st.st_size, f))
        total = sum(size for _, size, _ in files)
        if total <= limit:
            return
        recent = time.time() - RECENT_SECONDS
        for mtime, size, f in sorted(files, key=lambda t: t[0]):
            if total <= 0.75 * limit:
                break
            if f == keep or mtime > recent:
                continue
            try:
                f.unlink()
                total -= size
            except OSError:
                pass

    # -------------------------------------------------------------- endpoints
    def preview(self, puid: str, p: Path, max_box: int, enc: str, lowpass: float) -> Path:
        check_encoding(enc)
        factor = self.factor(p, max_box)
        lowpass = round(max(0.0, min(float(lowpass), 100.0)), 1)
        if factor <= 1 and not lowpass and enc == "f32":
            return p

        def build() -> MapVolume:
            return view_lowpass(bin_map(self.read(p), factor), lowpass)

        return self.cached(puid, f"v{CACHE_VERSION}:view:{p.resolve()}:{p.stat().st_mtime_ns}:{factor}:{lowpass}:{enc}", build, enc)

    def zone(self, puid: str, mp: Path, md: Path, radius: float, max_box: int, enc: str, lowpass: float = 0.0) -> Path:
        from cryoplug.modelio import atom_arrays, read_structure
        check_encoding(enc)
        if not 0.5 <= radius <= 30:
            raise HTTPException(400, "radius must be between 0.5 and 30 A")
        factor = self.factor(mp, max_box)
        lowpass = round(max(0.0, min(float(lowpass), 100.0)), 1)

        def build() -> MapVolume:
            try:
                xyz = atom_arrays(read_structure(md))["xyz"]
            except Exception as exc:  # noqa: BLE001 - unreadable model
                raise HTTPException(400, f"Cannot read the model: {exc}") from None
            if not len(xyz):
                raise HTTPException(400, "The model has no atoms")
            return zone_map(view_lowpass(bin_map(self.read(mp), factor), lowpass), xyz, radius)

        key = (f"v{CACHE_VERSION}:zone:{mp.resolve()}:{mp.stat().st_mtime_ns}:{md.resolve()}:{md.stat().st_mtime_ns}:"
               f"{radius:.2f}:{factor}:{lowpass}:{enc}")
        return self.cached(puid, key, build, enc)


    def mask_preview(self, puid: str, p: Path, recipe: dict[str, Any], max_box: int,
                     resolve: Callable[[str], Path]) -> bytes:
        """A mask drawn in the viewer, computed on a binned copy of its map (8-bit MRC, gzip-compressed): what the
        mask editor shows while drawing. ``resolve`` turns a project path (model, mask) into a file."""
        import gzip
        import tempfile

        from cryoplug import maskdraw
        factor = self.factor(p, max_box)
        key = (str(p.resolve()), p.stat().st_mtime_ns, factor)
        with self._lock:
            cached = self._preview_base if self._preview_base and self._preview_base[0] == key else None
        if cached is None:
            base = bin_map(self.read(p), factor)
            cached = (key, base, {})
            with self._lock:
                self._preview_base = cached
        base, memo = cached[1], cached[2]

        def models(path: str, selection: str):
            f = resolve(path)
            k = ("model", str(f), f.stat().st_mtime_ns, selection)
            if k not in memo:
                memo[k] = maskdraw.atom_positions(str(f), selection)
            return memo[k]

        def masks(path: str):
            f = resolve(path)
            k = ("mask", str(f), f.stat().st_mtime_ns)
            if k not in memo:
                memo[k] = maskdraw.resample_mask(str(f), base)
            return memo[k]

        core = maskdraw.draw(recipe, base, models=models, masks=masks)
        shown = base.like(maskdraw.preview(core, base.voxel, recipe))
        with tempfile.NamedTemporaryFile(suffix=".mrc") as tmp:
            write_view_map(shown, tmp.name, "i8")
            raw = Path(tmp.name).read_bytes()
        return gzip.compress(raw, compresslevel=3)


class _Reading:
    def __init__(self) -> None:
        self.done = threading.Event()
        self.volume: MapVolume | None = None
        self.error: Exception | None = None


def check_encoding(enc: str) -> None:
    if enc not in VIEW_ENCODINGS:
        raise HTTPException(400, f"enc must be one of {', '.join(VIEW_ENCODINGS)}")


def map_response(request: Request, path: Path) -> Response:
    """The map file, revalidated by the browser on each use (a 304 when it has not changed). A cached copy is named
    after its source (path, modification time) and settings, so its name identifies its content: its own date changes
    each time it is used (the least recently used go first)."""
    st = path.stat()
    cached = path.parent.name == ".cryoplug_cache"
    etag = f'"{path.stem}-{st.st_size:x}"' if cached else f'"{st.st_mtime_ns:x}-{st.st_size:x}"'
    headers = {"Cache-Control": "private, no-cache", "ETag": etag}
    if etag in request.headers.get("if-none-match", ""):
        return Response(status_code=304, headers=headers)
    return FileResponse(path, media_type="application/octet-stream", headers=headers)


def map_header(p: Path) -> dict[str, Any]:
    vol = MapVolume.read(p, header_only=True)
    info = vol.info()
    info["bytes"] = p.stat().st_size
    return info
