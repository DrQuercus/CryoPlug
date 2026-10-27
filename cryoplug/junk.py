"""Junk on micrographs, the areas CryoSPARC's Micrograph Junk Detector rejects, found here from the micrograph itself
without a trained network: where its background (the micrograph binned to a few pixels per particle and blurred at the
particle size, the linear trend of ice thickness removed) is far darker than the rest (ice contamination, thick ice,
carbon), far brighter (holes, broken ice), or crossed by a sharp step (edges of the support film). Robust z-scores
(median, MAD) set "far" (each junk taken to half that score around its core); the areas, grown by a margin, reject the
picks inside, and their share of each micrograph
sorts the micrographs.
"""
from __future__ import annotations

import numpy as np

JUNK_COLOUR = (230, 57, 70)
KEPT_COLOUR = (52, 199, 89)


def _robust_z(values: np.ndarray) -> np.ndarray:
    med = float(np.median(values))
    mad = 1.4826 * float(np.median(np.abs(values - med)))
    return (values - med) / (mad if mad > 0 else (float(values.std()) or 1.0))


def _hysteresis(score: np.ndarray, high: float) -> np.ndarray:
    """The areas above ``high / 2`` holding at least one point above ``high`` (the whole extent of each junk, found
    from its core)."""
    from scipy import ndimage
    core = score > high
    if not core.any():
        return core
    labels, n = ndimage.label(score > high / 2)
    keep = np.zeros(n + 1, dtype=bool)
    keep[np.unique(labels[core])] = True
    keep[0] = False
    return keep[labels]


def detect(image: np.ndarray, apix: float, diameter: float, dark: float = 4.0, bright: float = 0.0, edges: float = 5.0,
           margin: float | None = None) -> tuple[np.ndarray, int]:
    """The junk areas of a micrograph (boolean, binned) and the binning (micrograph pixels per mask pixel)."""
    from scipy import ndimage
    img = np.asarray(image, dtype=np.float32)
    f = max(1, int(diameter / (4.0 * apix)))  # about 4 mask pixels per particle diameter
    h, w = img.shape[0] // f * f, img.shape[1] // f * f
    small = img[:h, :w].reshape(h // f, f, w // f, f).mean(axis=(1, 3)) if f > 1 else img[:h, :w]
    px = apix * f
    sigma = diameter / px
    bg = ndimage.gaussian_filter(small, sigma)
    yy, xx = np.mgrid[0:bg.shape[0], 0:bg.shape[1]]
    design = np.stack([np.ones(bg.size), xx.ravel(), yy.ravel()], axis=1)
    coef, *_ = np.linalg.lstsq(design, bg.ravel(), rcond=None)  # the ice thickness gradient is not junk
    flat = bg - (design @ coef).reshape(bg.shape)
    z = _robust_z(flat)
    junk = np.zeros(bg.shape, dtype=bool)
    if dark > 0:
        junk |= _hysteresis(-z, dark)
    if bright > 0:
        junk |= _hysteresis(z, bright)
    if edges > 0:
        junk |= _hysteresis(_robust_z(ndimage.gaussian_gradient_magnitude(small, sigma)), edges)
    labels, n = ndimage.label(junk)
    if n:  # specks smaller than a particle are noise
        sizes = np.bincount(labels.ravel())
        sizes[0] = 0
        junk = (sizes >= np.pi * (diameter / 2 / px) ** 2)[labels]
    grow = diameter / 2 if margin is None else margin
    if grow > 0 and junk.any():
        junk = ndimage.distance_transform_edt(~junk) * px <= grow
    return junk, f


def on_junk(junk: np.ndarray, factor: int, xy: np.ndarray) -> np.ndarray:
    """Whether each position (x, y in micrograph pixels) lies on junk (positions beyond the mask: no)."""
    xy = np.asarray(xy, dtype=np.float64).reshape(-1, 2)
    if not len(xy):
        return np.zeros(0, dtype=bool)
    ix = (xy[:, 0] // factor).astype(int)
    iy = (xy[:, 1] // factor).astype(int)
    ok = (ix >= 0) & (iy >= 0) & (ix < junk.shape[1]) & (iy < junk.shape[0])
    out = np.zeros(len(xy), dtype=bool)
    out[ok] = junk[iy[ok], ix[ok]]
    return out


def _rings(rgb: np.ndarray, centres: np.ndarray, radius: float, colour: tuple[int, int, int], width: float = 1.2) -> None:
    if not len(centres) or radius <= 0:
        return
    h, w = rgb.shape[:2]
    r = int(np.ceil(radius + width))
    yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
    ring = np.abs(np.hypot(xx, yy) - radius) <= width / 2 + 0.3
    dy, dx = yy[ring], xx[ring]
    for x, y in np.asarray(centres, dtype=np.float64):
        py, px = int(round(y)) + dy, int(round(x)) + dx
        ok = (py >= 0) & (py < h) & (px >= 0) & (px < w)
        rgb[py[ok], px[ok]] = colour


def overlay(image: np.ndarray, junk: np.ndarray, factor: int, kept: np.ndarray, rejected: np.ndarray, diameter_px: float,
            size: int = 360) -> np.ndarray:
    """The micrograph (RGB) with its junk tinted red, the picks kept circled in green and those rejected in red."""
    from cryoplug.micrographs import preview
    gray, pf = preview(image, size)
    rgb = np.repeat(gray[..., None], 3, axis=2).astype(np.float32)
    ys = np.clip((np.arange(gray.shape[0]) * pf / factor).astype(int), 0, junk.shape[0] - 1)
    xs = np.clip((np.arange(gray.shape[1]) * pf / factor).astype(int), 0, junk.shape[1] - 1)
    tint = junk[ys][:, xs]
    rgb[tint] = 0.55 * rgb[tint] + 0.45 * np.array(JUNK_COLOUR, dtype=np.float32)
    out = np.clip(rgb, 0, 255).astype(np.uint8)
    radius = max(2.0, diameter_px / 2 / pf)
    _rings(out, np.asarray(kept, dtype=np.float64).reshape(-1, 2) / pf, radius, KEPT_COLOUR)
    _rings(out, np.asarray(rejected, dtype=np.float64).reshape(-1, 2) / pf, radius, JUNK_COLOUR)
    return out
