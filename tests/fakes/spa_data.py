"""Synthetic single-particle data for the tests and the demo: movies of an asymmetric particle (three blobs, about
60 Å across) scattered on noisy micrographs, drifting a little from frame to frame, with dark particles on a light
background as in real micrographs."""
from __future__ import annotations

from pathlib import Path

import numpy as np

PARTICLE_DIAMETER = 60.0  # Å


def particle_image(size: int, apix: float, angle: float) -> np.ndarray:
    """Projection of the particle rotated in plane by ``angle`` (radians), centred in a ``size`` box."""
    y, x = np.indices((size, size), dtype=np.float32) - size / 2
    blobs = [(0.0, 0.0, 12.0, 1.0), (16.0, 4.0, 8.0, 0.8), (-6.0, 15.0, 7.0, 0.7)]  # x, y (Å), sigma (Å), weight
    c, s = np.cos(angle), np.sin(angle)
    img = np.zeros((size, size), np.float32)
    for bx, by, sig, w in blobs:
        rx, ry = (c * bx - s * by) / apix, (s * bx + c * by) / apix
        img += w * np.exp(-((x - rx) ** 2 + (y - ry) ** 2) / (2 * (sig / apix) ** 2))
    return img


def write_movies(folder: str | Path, n_movies: int = 4, frames: int = 6, size: int = 384, apix: float = 1.0,
                 seed: int = 0, gain: bool = True) -> dict:
    """Movies (MRC stacks) of ``frames`` frames; returns their paths, the particle positions (pixels of the summed
    micrograph) and the gain reference."""
    import mrcfile
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    radius = PARTICLE_DIAMETER / 2 / apix
    patch = int(2 * radius + 8)
    spacing = int(2.6 * radius)
    movies, positions = [], []
    for m in range(n_movies):
        clean = np.zeros((size, size), np.float32)
        pts = []
        for cy in range(int(radius) + 10, size - int(radius) - 10, spacing):
            for cx in range(int(radius) + 10, size - int(radius) - 10, spacing):
                if rng.random() < 0.15:  # some empty spots, as on real grids
                    continue
                px, py = cx + rng.uniform(-6, 6), cy + rng.uniform(-6, 6)
                img = particle_image(patch, apix, rng.uniform(0, 2 * np.pi))
                y0, x0 = int(round(py)) - patch // 2, int(round(px)) - patch // 2
                clean[y0:y0 + patch, x0:x0 + patch] -= img
                pts.append((float(round(px)), float(round(py))))
        stack = np.empty((frames, size, size), np.float32)
        for f in range(frames):
            shifted = np.roll(clean, (f // 3, f // 2), axis=(0, 1))  # a small drift
            stack[f] = 1.0 + 0.35 * shifted + rng.normal(0, 0.45, (size, size)).astype(np.float32)
        path = folder / f"mic_{m + 1:03d}.mrc"
        with mrcfile.new(str(path), overwrite=True) as mrc:
            mrc.set_data(stack)
            mrc.voxel_size = apix
        movies.append(path)
        positions.append(pts)
    out = {"movies": movies, "positions": positions, "apix": apix, "frames": frames, "size": size,
           "diameter": PARTICLE_DIAMETER, "folder": folder}
    if gain:
        g = folder / "gain.mrc"
        with mrcfile.new(str(g), overwrite=True) as mrc:
            mrc.set_data(np.ones((size, size), np.float32))
            mrc.voxel_size = apix
        out["gain"] = g
    return out
