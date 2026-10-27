"""Synthetic tilt series for the tests and the demo: tilt series of a slab holding a dozen particles (blobs), the movie
of every tilt (a few frames, a little drift) and SerialEM's mdoc file of each tilt series, recorded dose-symmetrically
from 0° as the RELION tutorial data (EMPIAR-10164): TS_01.mrc.mdoc, frames TS_01_000_0.0.mrc, TS_01_001_6.0.mrc..."""
from __future__ import annotations

import zlib
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

PIXEL = 2.0  # Å, unbinned
SIZE = 128  # px, the tilt images (square)
THICKNESS = 64  # px (unbinned) of the slab holding the particles
PARTICLE_SIGMA = 8.0  # Å


def particle_positions(name: str, n: int = 12) -> np.ndarray:
    """Positions of the particles of a tilt series, in Å from the tomogram centre (the same for the fake programs)."""
    rng = np.random.default_rng(zlib.crc32(name.encode()))
    half = SIZE * PIXEL / 2 * 0.65
    xy = rng.uniform(-half, half, (n, 2))
    z = rng.uniform(-THICKNESS * PIXEL / 4, THICKNESS * PIXEL / 4, n)
    return np.column_stack([xy, z])


def dose_symmetric(n_tilts: int, step: float) -> list[float]:
    """Tilt angles in the order of acquisition: 0, +step, -step, +2 step, -2 step..."""
    angles = [0.0]
    k = 1
    while len(angles) < n_tilts:
        angles.append(k * step)
        if len(angles) < n_tilts:
            angles.append(-k * step)
        k += 1
    return angles


def projection(name: str, tilt: float, size: int = SIZE, apix: float = PIXEL) -> np.ndarray:
    """The particles of a tilt series seen at ``tilt`` degrees (tilt axis along Y): dark blobs on a grey background."""
    t = np.radians(tilt)
    y, x = np.indices((size, size), dtype=np.float32)
    img = np.zeros((size, size), np.float32)
    for px, py, pz in particle_positions(name):
        u = (px * np.cos(t) + pz * np.sin(t)) / apix + size / 2
        v = py / apix + size / 2
        img -= np.exp(-((x - u) ** 2 + (y - v) ** 2) / (2 * (PARTICLE_SIGMA / apix) ** 2))
    return img


def write_tilt_series(folder: str | Path, names: tuple[str, ...] = ("TS_01", "TS_02"), n_tilts: int = 9,
                      step: float = 6.0, frames: int = 4, dose: float = 3.0, seed: int = 0,
                      missing_image: bool = True) -> dict:
    """The movies (frames/) and mdoc files (mdoc/) of the tilt series. With ``missing_image``, the mdoc file of the last
    tilt series names one more tilt image than there are files (RELION leaves it out)."""
    import mrcfile
    folder = Path(folder)
    frames_dir, mdoc_dir = folder / "frames", folder / "mdoc"
    frames_dir.mkdir(parents=True, exist_ok=True)
    mdoc_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    start = datetime(2024, 3, 5, 14, 0, 0)
    movies: dict[str, list[Path]] = {}
    for k, name in enumerate(names):
        angles = dose_symmetric(n_tilts + (1 if missing_image and k == len(names) - 1 else 0), step)
        lines = [f"PixelSpacing = {PIXEL:g}", "Voltage = 300", f"ImageFile = {name}.mrc", "ImageSize = "
                 f"{SIZE} {SIZE}", "DataMode = 1", "", f"[T = SerialEM: tilt series {name}]", ""]
        movies[name] = []
        for i, tilt in enumerate(angles):
            fname = f"{name}_{i:03d}_{tilt:.1f}.mrc"
            if i < n_tilts:  # the extra tilt of the mdoc file has no movie
                clean = projection(name, tilt)
                stack = np.empty((frames, SIZE, SIZE), np.float32)
                for f in range(frames):
                    shifted = np.roll(clean, (f // 2, f // 3), axis=(0, 1))  # a little drift
                    stack[f] = 1.0 + 0.6 * shifted + rng.normal(0, 0.35, (SIZE, SIZE)).astype(np.float32)
                with mrcfile.new(str(frames_dir / fname), overwrite=True) as mrc:
                    mrc.set_data(stack)
                    mrc.voxel_size = PIXEL
                movies[name].append(frames_dir / fname)
            when = start + timedelta(minutes=30 * k, seconds=50 * i)
            lines += [f"[ZValue = {i}]", f"TiltAngle = {tilt:.2f}", f"ExposureDose = {dose:g}", f"NumSubFrames = {frames}",
                      f"SubFramePath = D:\\DATA\\Session1\\frames\\{fname}", f"TargetDefocus = {-3.0 - 0.5 * k:g}",
                      f"PixelSpacing = {PIXEL:g}", f"DateTime = {when.strftime('%d-%b-%y  %H:%M:%S')}", ""]
        (mdoc_dir / f"{name}.mrc.mdoc").write_text("\n".join(lines))
    return {"folder": folder, "frames": frames_dir, "mdoc": mdoc_dir, "names": list(names), "n_tilts": n_tilts,
            "step": step, "pixel": PIXEL, "size": SIZE, "frames_per_movie": frames, "dose": dose, "movies": movies,
            "positions": {name: particle_positions(name) for name in names}}
