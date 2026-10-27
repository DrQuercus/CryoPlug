"""2D class averages compared with projections of a reference map, as CryoSPARC's Reference Based Auto Select 2D: the
map is projected along directions covering the sphere, then each class average is aligned to each projection (in-plane
rotations, translations by FFT cross-correlation) at a low resolution; the best normalised correlation of each class
tells whether it looks like a view of the reference.

The comparison runs on a small box (Fourier-cropped, low-pass filtered, inside a soft circle) so that a few hundred
classes against a few hundred projections take seconds.
"""
from __future__ import annotations

import numpy as np


def directions(step: float) -> np.ndarray:
    """Unit vectors about ``step`` degrees apart over the whole sphere (Fibonacci lattice): both hands of every view."""
    n = max(12, int(round(4 * np.pi / np.radians(step) ** 2)))
    i = np.arange(n) + 0.5
    phi = np.arccos(1 - 2 * i / n)
    theta = np.pi * (1 + 5 ** 0.5) * i
    return np.stack([np.cos(theta) * np.sin(phi), np.sin(theta) * np.sin(phi), np.cos(phi)], axis=1)


def _to_z(d: np.ndarray) -> np.ndarray:
    """A rotation taking the direction ``d`` (x, y, z) onto z."""
    z = np.array([0.0, 0.0, 1.0])
    v = np.cross(d, z)
    s, c = np.linalg.norm(v), float(np.dot(d, z))
    if s < 1e-9:
        return np.eye(3) if c > 0 else np.diag([1.0, -1.0, -1.0])
    k = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]]) / s
    a = np.arctan2(s, c)
    return np.eye(3) + np.sin(a) * k + (1 - np.cos(a)) * k @ k


def project(volume: np.ndarray, dirs: np.ndarray) -> np.ndarray:
    """Projections (m, n, n) of a cubic map (z, y, x) along the directions: the map turned so that each direction is z,
    summed along z (rotation about the centre voxel, trilinear)."""
    from scipy import ndimage
    n = volume.shape[0]
    c = np.array(volume.shape, dtype=float) // 2
    p = np.eye(3)[::-1]
    out = np.empty((len(dirs), n, n), dtype=np.float32)
    for i, d in enumerate(dirs):
        rot = _to_z(d).T  # sample the map at R⁻¹ r: the direction d looks along z
        m = p @ rot @ p
        out[i] = ndimage.affine_transform(volume, m, offset=c - m @ c, order=1).sum(axis=0)
    return out


def shrink_map(volume: np.ndarray, apix: float, box: int, resolution: float) -> tuple[np.ndarray, float]:
    """A cubic map Fourier-cropped to ``box`` and low-pass filtered to ``resolution`` Å."""
    data = np.asarray(volume, dtype=np.float32)
    n = data.shape[0]
    f = np.fft.fftshift(np.fft.fftn(data))
    target = min(n, box)
    s = (n - target) // 2
    f = f[s:s + target, s:s + target, s:s + target]
    new_apix = apix * n / target
    k = (np.arange(target) - target // 2) / (target * new_apix)
    k2 = k[:, None, None] ** 2 + k[None, :, None] ** 2 + k[None, None, :] ** 2
    f = f * np.exp(-k2 * resolution ** 2 * np.log(2))
    return np.fft.ifftn(np.fft.ifftshift(f)).real.astype(np.float32), new_apix


def shrink_images(images: np.ndarray, apix: float, box: int, resolution: float) -> tuple[np.ndarray, float]:
    """Images (k, n, n) Fourier-cropped to ``box`` and low-pass filtered to ``resolution`` Å."""
    data = np.asarray(images, dtype=np.float32)
    n = data.shape[-1]
    f = np.fft.fftshift(np.fft.fft2(data), axes=(-2, -1))
    target = min(n, box)
    s = (n - target) // 2
    f = f[:, s:s + target, s:s + target]
    new_apix = apix * n / target
    k = (np.arange(target) - target // 2) / (target * new_apix)
    k2 = k[:, None] ** 2 + k[None, :] ** 2
    f = f * np.exp(-k2 * resolution ** 2 * np.log(2))
    return np.fft.ifft2(np.fft.ifftshift(f, axes=(-2, -1))).real.astype(np.float32), new_apix


def _normalise(images: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Zero mean and unit norm inside the (soft) mask, zero outside."""
    w = mask[None]
    mean = (images * w).sum(axis=(1, 2)) / w.sum()
    out = (images - mean[:, None, None]) * w
    norm = np.sqrt((out ** 2).sum(axis=(1, 2)))
    return out / np.where(norm > 0, norm, 1.0)[:, None, None]


def soft_circle(n: int, radius: float, edge: float = 2.0) -> np.ndarray:
    y, x = np.indices((n, n)) - n // 2
    r = np.hypot(x, y)
    return np.clip((radius + edge - r) / edge, 0.0, 1.0).astype(np.float32)


def match(classes: np.ndarray, projections: np.ndarray, angle_step: float = 10.0, max_shift: float = 0.1,
          chunk: int = 64) -> list[dict]:
    """For every class average (k, n, n): the best correlation with any projection (m, n, n) over in-plane rotations
    and shifts up to ``max_shift`` × the box, with the projection, rotation and shift that give it."""
    from scipy import fft, ndimage
    n = classes.shape[-1]
    mask = soft_circle(n, 0.42 * n)
    projs = _normalise(np.asarray(projections, dtype=np.float32), mask)
    fp = np.conj(fft.rfft2(projs, workers=-1))
    angles = np.arange(0.0, 360.0, angle_step)
    lim = max(1, int(round(max_shift * n)))
    yy, xx = np.indices((n, n))
    near = (np.minimum(yy, n - yy) <= lim) & (np.minimum(xx, n - xx) <= lim)  # shifts within ± lim (circular)
    out = []
    for img in np.asarray(classes, dtype=np.float32):
        if not np.isfinite(img).all() or float(img.std()) == 0.0:
            out.append({"cc": 0.0, "projection": None, "angle": 0.0, "shift": [0, 0]})
            continue
        rot = np.stack([ndimage.rotate(img, a, reshape=False, order=1, mode="constant") for a in angles])
        fr = fft.rfft2(_normalise(rot, mask), workers=-1)
        best = (-np.inf, 0, 0, (0, 0))
        for j0 in range(0, len(projs), chunk):
            cc = fft.irfft2(fr[:, None] * fp[None, j0:j0 + chunk], s=(n, n), workers=-1)  # (rotations, projections, n, n)
            cc = np.where(near, cc, -np.inf)
            flat = int(np.argmax(cc))
            a, j, sy, sx = np.unravel_index(flat, cc.shape)
            if cc[a, j, sy, sx] > best[0]:
                best = (float(cc[a, j, sy, sx]), int(a), int(j0 + j), (int(sy), int(sx)))
        value, a, j, (sy, sx) = best
        out.append({"cc": value, "projection": j, "angle": float(angles[a]),
                    "shift": [sx if sx <= n // 2 else sx - n, sy if sy <= n // 2 else sy - n]})
    return out
