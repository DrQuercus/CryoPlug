"""Pictures of the single-particle pipeline: micrographs, CTF fits, picked particles and 2D class averages.

Micrographs are shown as RELION displays them: the first row of the image at the top, particle coordinates
(``rlnCoordinateX``, ``rlnCoordinateY``) in pixels from the top-left corner. They are binned, low-pass filtered and
contrast-stretched (1st-99th percentile) so that particles are visible at a glance.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

PICK_COLOUR = (52, 199, 89)


def read_image(path: str | Path, index: int | None = None) -> np.ndarray:
    """A 2D image of an MRC file (float16 included): the image itself, one image of a stack, or the sum of a movie."""
    import mrcfile
    name = str(path).split(":")[0]  # RELION names a diagnostic file "x.ctf:mrc"
    with mrcfile.open(name, permissive=True) as m:
        data = np.asarray(m.data, dtype=np.float32)
    if data.ndim == 3:
        data = data[index] if index is not None else data.sum(axis=0)
    return data


def micrograph_shape(path: str | Path) -> tuple[int, int]:
    """(height, width) of a micrograph, from its header (a stack: one of its images)."""
    import mrcfile
    with mrcfile.open(str(path).split(":")[0], permissive=True, header_only=True) as m:
        return int(m.header.ny), int(m.header.nx)


def preview(data: np.ndarray, size: int = 512, sigma: float = 1.2) -> tuple[np.ndarray, float]:
    """A micrograph made readable at ``size`` pixels: binned, smoothed, contrast-stretched (uint8). Returns the image
    and the binning factor (micrograph pixels per preview pixel)."""
    from scipy import ndimage
    img = np.asarray(data, dtype=np.float32)
    factor = max(1, int(np.ceil(max(img.shape) / size)))
    h, w = (img.shape[0] // factor) * factor, (img.shape[1] // factor) * factor
    if factor > 1:
        img = img[:h, :w].reshape(h // factor, factor, w // factor, factor).mean(axis=(1, 3))
    img = ndimage.gaussian_filter(img, sigma)
    lo, hi = np.percentile(img, [1, 99])
    return (np.clip((img - lo) / ((hi - lo) or 1.0), 0, 1) * 255).astype(np.uint8), float(factor)


def draw_circles(gray: np.ndarray, centres: np.ndarray, radius: float, colour: tuple[int, int, int] = PICK_COLOUR,
                 width: float = 1.2) -> np.ndarray:
    """RGB copy of a grayscale image with circles (x, y in its pixels)."""
    rgb = np.repeat(gray[..., None], 3, axis=2).astype(np.uint8)
    if not len(centres) or radius <= 0:
        return rgb
    h, w = gray.shape
    r = int(np.ceil(radius + width))
    yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
    ring = np.abs(np.hypot(xx, yy) - radius) <= width / 2 + 0.3
    dy, dx = yy[ring], xx[ring]
    for x, y in np.asarray(centres, dtype=np.float64):
        cy, cx = int(round(y)), int(round(x))
        py, px = cy + dy, cx + dx
        ok = (py >= 0) & (py < h) & (px >= 0) & (px < w)
        rgb[py[ok], px[ok]] = colour
    return rgb


def picks_image(micrograph: str | Path, xy: np.ndarray, diameter_px: float, size: int = 512) -> np.ndarray:
    """A micrograph with its picked particles circled (RGB). ``diameter_px``: particle diameter in micrograph pixels."""
    gray, factor = preview(read_image(micrograph), size)
    return draw_circles(gray, np.asarray(xy, dtype=np.float64) / factor, max(2.0, diameter_px / 2 / factor))


def ctf_fit_image(path: str | Path, size: int = 256) -> np.ndarray:
    """The diagnostic image of CTFFIND (power spectrum and, in one half, the fitted CTF) as a uint8 picture."""
    from scipy import ndimage
    img = read_image(path, index=0)
    lo, hi = np.percentile(img, [2, 98])
    img = np.clip((img - lo) / ((hi - lo) or 1.0), 0, 1)
    if max(img.shape) != size:
        img = ndimage.zoom(img, size / max(img.shape), order=1)
    return (img * 255).astype(np.uint8)


def class_tile(image: np.ndarray, size: int = 128) -> np.ndarray:
    """A 2D class average as a picture (uint8): contrast from the 0.5th to the 99.5th percentile, protein white."""
    from scipy import ndimage
    img = np.asarray(image, dtype=np.float32)
    lo, hi = np.percentile(img, [0.5, 99.5])
    img = np.clip((img - lo) / ((hi - lo) or 1.0), 0, 1)
    if img.shape[0] != size:
        img = ndimage.zoom(img, size / img.shape[0], order=1)[:size, :size]
    return (img * 255).astype(np.uint8)


def tile_sheet(tiles: list[np.ndarray], per_row: int = 6, gap: int = 3) -> np.ndarray:
    """Pictures (grayscale or RGB, the same size) side by side on a transparent background (RGBA)."""
    if not tiles:
        return np.zeros((1, 1, 4), dtype=np.uint8)
    th, tw = tiles[0].shape[:2]
    cols = min(per_row, len(tiles))
    rows = int(np.ceil(len(tiles) / cols))
    sheet = np.zeros((rows * (th + gap) - gap, cols * (tw + gap) - gap, 4), dtype=np.uint8)
    for i, t in enumerate(tiles):
        r, c = divmod(i, cols)
        y, x = r * (th + gap), c * (tw + gap)
        sheet[y:y + th, x:x + tw, :3] = t[..., None] if t.ndim == 2 else t[..., :3]
        sheet[y:y + th, x:x + tw, 3] = 255
    return sheet


def crop_to(image: np.ndarray, size: int) -> np.ndarray:
    """The centre of a picture, at most ``size`` pixels wide and high (job card thumbnails)."""
    h, w = image.shape[:2]
    y0, x0 = max(0, (h - size) // 2), max(0, (w - size) // 2)
    return image[y0:y0 + size, x0:x0 + size]
