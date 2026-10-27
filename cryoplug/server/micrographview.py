"""Micrographs and CTF fits for the browser (exposure curation, pick inspection, manual picking).

A micrograph is binned by a whole factor (``factor = ceil(largest side / size)``: the browser computes the same factor
from the micrograph's shape to place the particle coordinates), low-pass filtered (a Gaussian whose transfer is 0.5 at
the given resolution) and contrast-stretched; PNGs are cached in the project (``.cryoplug_cache/micrographs``), keyed by
the file and the settings, and trimmed to the least recently used beyond a size limit.
"""
from __future__ import annotations

import hashlib
import math
import os
import threading
import time
from pathlib import Path

import numpy as np

CACHE_LIMIT = 1024 ** 3  # bytes of cached pictures per project
CACHE_VERSION = 1
RECENT_SECONDS = 120
BUILDS = threading.BoundedSemaphore(3)
_trim_lock = threading.Lock()


def preview_factor(shape: tuple[int, int], size: int) -> int:
    """Whole binning factor bringing the largest side to at most ``size`` pixels (as the browser computes it)."""
    return max(1, math.ceil(max(shape) / max(64, size)))


def render_micrograph(data: np.ndarray, size: int, apix: float, lowpass: float) -> np.ndarray:
    """uint8 picture of a micrograph: binned, low-pass filtered to ``lowpass`` Å (0: a light smoothing only),
    contrast from the 0.5th to the 99.5th percentile."""
    from scipy import ndimage
    img = np.asarray(data, dtype=np.float32)
    factor = preview_factor(img.shape, size)
    h, w = (img.shape[0] // factor) * factor, (img.shape[1] // factor) * factor
    if factor > 1:
        img = img[:h, :w].reshape(h // factor, factor, w // factor, factor).mean(axis=(1, 3))
    if lowpass > 0 and apix > 0:
        # Gaussian with exp(-2 pi^2 sigma^2 nu^2) = 0.5 at nu = 1 / lowpass (cycles per binned pixel)
        sigma = math.sqrt(math.log(2) / 2) / math.pi * lowpass / (apix * factor)
        img = ndimage.gaussian_filter(img, max(0.5, sigma))
    else:
        img = ndimage.gaussian_filter(img, 0.7)
    lo, hi = np.percentile(img, [0.5, 99.5])
    return (np.clip((img - lo) / ((hi - lo) or 1.0), 0, 1) * 255).astype(np.uint8)


def _cache_file(project: Path, key: str) -> Path:
    cache = project / ".cryoplug_cache" / "micrographs"
    cache.mkdir(parents=True, exist_ok=True)
    return cache / f"{hashlib.sha1(key.encode()).hexdigest()[:20]}.png"


def _cached(project: Path, key: str, build) -> Path:
    out = _cache_file(project, key)
    if out.exists():
        try:
            os.utime(out)
        except OSError:
            pass
        return out
    from cryoplug.imaging import png_bytes
    tmp = out.with_name(f"{out.stem}.{os.getpid()}.{threading.get_ident()}.part")
    try:
        with BUILDS:
            tmp.write_bytes(png_bytes(build()))
        os.replace(tmp, out)
    finally:
        tmp.unlink(missing_ok=True)
    with _trim_lock:
        trim(out.parent, keep=out)
    return out


def trim(cache: Path, keep: Path | None = None, limit: int = CACHE_LIMIT) -> None:
    files = []
    for f in cache.glob("*.png"):
        try:
            st = f.stat()
        except OSError:
            continue
        files.append((st.st_mtime, st.st_size, f))
    total = sum(s for _, s, _ in files)
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


def micrograph_png(project: Path, path: Path, size: int = 1024, lowpass: float = 20.0, apix: float = 0.0) -> Path:
    """Cached picture of a micrograph (an MRC image, a float16 one or the sum of a stack)."""
    import mrcfile
    size = int(max(128, min(size, 4096)))
    lowpass = round(max(0.0, min(float(lowpass), 200.0)), 1)
    st = path.stat()
    key = f"v{CACHE_VERSION}:mic:{path.resolve()}:{st.st_mtime_ns}:{size}:{lowpass}:{apix:.4f}"

    def build() -> np.ndarray:
        from cryoplug.micrographs import read_image
        pixel = apix
        if pixel <= 0:
            with mrcfile.open(str(path), permissive=True, header_only=True) as m:
                pixel = float(m.voxel_size.x) or 1.0
        return render_micrograph(read_image(path), size, pixel, lowpass)
    return _cached(project, key, build)


def ctf_png(project: Path, path: Path, size: int = 384) -> Path:
    """Cached picture of a CTFFIND diagnostic image (power spectrum with the fitted CTF)."""
    size = int(max(64, min(size, 1024)))
    st = path.stat()
    key = f"v{CACHE_VERSION}:ctf:{path.resolve()}:{st.st_mtime_ns}:{size}"

    def build() -> np.ndarray:
        from cryoplug.micrographs import ctf_fit_image
        return ctf_fit_image(path, size)
    return _cached(project, key, build)


def tomogram_slice_png(project: Path, path: Path, z: int, thickness: int = 1, size: int = 1024,
                       lowpass: float = 0.0) -> Path:
    """Cached picture of a slab of a tomogram: the mean of ``thickness`` Z slices centred on slice ``z`` (read from the
    memory-mapped file, so that a large tomogram is not loaded), binned and filtered as a micrograph."""
    import mrcfile
    size = int(max(128, min(size, 4096)))
    lowpass = round(max(0.0, min(float(lowpass), 200.0)), 1)
    thickness = int(max(1, min(thickness, 200)))
    st = path.stat()
    with mrcfile.mmap(str(path), mode="r", permissive=True) as m:
        depth = int(m.data.shape[0]) if m.data.ndim == 3 else 1
    z = int(max(0, min(int(z), depth - 1)))
    key = f"v{CACHE_VERSION}:tomo:{path.resolve()}:{st.st_mtime_ns}:{z}:{thickness}:{size}:{lowpass}"

    def build() -> np.ndarray:
        with mrcfile.mmap(str(path), mode="r", permissive=True) as m:
            data = m.data
            pixel = float(m.voxel_size.x) or 1.0
            if data.ndim == 2:
                slab = np.asarray(data, dtype=np.float32)
            else:
                z0 = max(0, z - (thickness - 1) // 2)
                slab = np.asarray(data[z0:min(depth, z0 + thickness)], dtype=np.float32).mean(axis=0)
        return render_micrograph(slab, size, pixel, lowpass)
    return _cached(project, key, build)
