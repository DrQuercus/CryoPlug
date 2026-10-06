"""Map (MRC/CCP4) utilities: I/O, FSC, filtering, binning and model maps.

All arrays are stored in ``(z, y, x)`` order, all physical vectors
(voxel size, origin, coordinates) in ``(x, y, z)`` order and in Angstrom.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import mrcfile
import numpy as np


@dataclass
class MapVolume:
    data: np.ndarray  # (z, y, x) float32
    voxel: np.ndarray  # (3,) x, y, z voxel size in Angstrom
    origin: np.ndarray = field(default_factory=lambda: np.zeros(3))  # position (A) of voxel (0, 0, 0)

    # ------------------------------------------------------------------- I/O
    @classmethod
    def read(cls, path: str | Path, header_only: bool = False) -> "MapVolume":
        with mrcfile.open(str(path), mode="r", permissive=True, header_only=header_only) as mrc:
            h = mrc.header
            voxel = np.array([float(mrc.voxel_size.x), float(mrc.voxel_size.y), float(mrc.voxel_size.z)])
            voxel[voxel <= 0] = 1.0
            axes = (int(h.mapc), int(h.mapr), int(h.maps))  # labels of columns, rows, sections
            if sorted(axes) != [1, 2, 3]:
                axes = (1, 2, 3)
            starts_crs = (int(h.nxstart), int(h.nystart), int(h.nzstart))
            start_xyz = np.zeros(3)
            for pos, label in enumerate(axes):
                start_xyz[label - 1] = starts_crs[pos]
            header_origin = np.array([float(h.origin.x), float(h.origin.y), float(h.origin.z)])
            if np.any(np.abs(header_origin) > 1e-6):
                origin = header_origin
            else:
                origin = start_xyz * voxel
            if header_only:
                nx, ny, nz = int(h.nx), int(h.ny), int(h.nz)
                shape_crs = (nx, ny, nz)
                shape_xyz = [0, 0, 0]
                for pos, label in enumerate(axes):
                    shape_xyz[label - 1] = shape_crs[pos]
                data = np.zeros((0, 0, 0), dtype=np.float32)
                vol = cls(data=data, voxel=voxel, origin=origin)
                vol._shape_xyz = tuple(shape_xyz)  # type: ignore[attr-defined]
                return vol
            data = np.asarray(mrc.data, dtype=np.float32)
            if data.ndim == 2:
                data = data[np.newaxis]
            if axes != (1, 2, 3):
                # data axes are (sections, rows, columns) -> labels (axes[2], axes[1], axes[0])
                current = [axes[2], axes[1], axes[0]]
                data = np.transpose(data, [current.index(lbl) for lbl in (3, 2, 1)])
            return cls(data=np.ascontiguousarray(data), voxel=voxel, origin=origin)

    def write(self, path: str | Path, data: np.ndarray | None = None) -> Path:
        path = Path(path)
        arr = np.ascontiguousarray(self.data if data is None else data, dtype=np.float32)
        with mrcfile.new(str(path), overwrite=True) as mrc:
            mrc.set_data(arr)
            mrc.voxel_size = tuple(float(v) for v in self.voxel)
            start = self.origin / self.voxel
            if np.allclose(start, np.round(start), atol=1e-3):
                mrc.header.nxstart, mrc.header.nystart, mrc.header.nzstart = (int(round(s)) for s in start)
                mrc.header.origin = (0.0, 0.0, 0.0)
            else:
                mrc.header.nxstart = mrc.header.nystart = mrc.header.nzstart = 0
                mrc.header.origin = tuple(float(o) for o in self.origin)
            mrc.update_header_stats()
        return path

    # ------------------------------------------------------------ properties
    @property
    def shape_xyz(self) -> tuple[int, int, int]:
        if hasattr(self, "_shape_xyz"):
            return self._shape_xyz  # type: ignore[attr-defined]
        nz, ny, nx = self.data.shape
        return nx, ny, nz

    @property
    def pixel_size(self) -> float:
        return float(np.mean(self.voxel))

    def info(self) -> dict[str, Any]:
        return {
            "box": list(self.shape_xyz),
            "pixel_size": round(self.pixel_size, 4),
            "voxel_size": [round(float(v), 4) for v in self.voxel],
            "origin": [round(float(o), 3) for o in self.origin],
        }

    def like(self, data: np.ndarray) -> "MapVolume":
        return MapVolume(data=np.asarray(data, dtype=np.float32), voxel=self.voxel.copy(), origin=self.origin.copy())

    # --------------------------------------------------------- interpolation
    def to_index(self, xyz: np.ndarray) -> np.ndarray:
        return (np.asarray(xyz, dtype=np.float64) - self.origin) / self.voxel

    def interpolate(self, xyz: np.ndarray, outside: float = np.nan) -> np.ndarray:
        """Trilinear interpolation of the map at Cartesian positions ``xyz`` (N, 3)."""
        idx = self.to_index(np.atleast_2d(xyz))
        nx, ny, nz = self.shape_xyz
        x, y, z = idx[:, 0], idx[:, 1], idx[:, 2]
        x0, y0, z0 = np.floor(x).astype(np.int64), np.floor(y).astype(np.int64), np.floor(z).astype(np.int64)
        fx, fy, fz = x - x0, y - y0, z - z0
        valid = (x0 >= 0) & (y0 >= 0) & (z0 >= 0) & (x0 < nx - 1) & (y0 < ny - 1) & (z0 < nz - 1)
        x0c, y0c, z0c = np.clip(x0, 0, nx - 2), np.clip(y0, 0, ny - 2), np.clip(z0, 0, nz - 2)
        d = self.data
        c000 = d[z0c, y0c, x0c]
        c001 = d[z0c, y0c, x0c + 1]
        c010 = d[z0c, y0c + 1, x0c]
        c011 = d[z0c, y0c + 1, x0c + 1]
        c100 = d[z0c + 1, y0c, x0c]
        c101 = d[z0c + 1, y0c, x0c + 1]
        c110 = d[z0c + 1, y0c + 1, x0c]
        c111 = d[z0c + 1, y0c + 1, x0c + 1]
        c00 = c000 * (1 - fx) + c001 * fx
        c01 = c010 * (1 - fx) + c011 * fx
        c10 = c100 * (1 - fx) + c101 * fx
        c11 = c110 * (1 - fx) + c111 * fx
        c0 = c00 * (1 - fy) + c01 * fy
        c1 = c10 * (1 - fy) + c11 * fy
        out = c0 * (1 - fz) + c1 * fz
        out = out.astype(np.float64)
        out[~valid] = outside
        return out

    def contains(self, xyz: np.ndarray) -> np.ndarray:
        idx = self.to_index(np.atleast_2d(xyz))
        upper = np.array(self.shape_xyz) - 1
        return np.all((idx >= 0) & (idx <= upper), axis=1)


def map_info(path: str | Path) -> dict[str, Any]:
    """Header-only summary of a map file."""
    return MapVolume.read(path, header_only=True).info()


# ---------------------------------------------------------------------- FSC
def _frequency_shells(shape: tuple[int, int, int], voxel: np.ndarray) -> tuple[np.ndarray, np.ndarray, int]:
    """Shell index for every rfftn coefficient and the frequency (1/A) of each shell."""
    nz, ny, nx = shape
    n = max(shape)
    kz = np.fft.fftfreq(nz)[:, None, None] / voxel[2]
    ky = np.fft.fftfreq(ny)[None, :, None] / voxel[1]
    kx = np.fft.rfftfreq(nx)[None, None, :] / voxel[0]
    step = 1.0 / (n * float(np.mean(voxel)))  # shell width in 1/A
    s = np.sqrt(kz ** 2 + ky ** 2 + kx ** 2)
    shells = np.rint(s / step).astype(np.int32)
    nshell = n // 2 + 1
    freqs = np.arange(nshell) * step
    return shells, freqs, nshell


def fsc(a: np.ndarray, b: np.ndarray, voxel: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Fourier shell correlation between two maps. Returns (frequency 1/A, fsc)."""
    if a.shape != b.shape:
        raise ValueError(f"Map dimensions differ: {a.shape} vs {b.shape}")
    fa = np.fft.rfftn(a.astype(np.float32))
    fb = np.fft.rfftn(b.astype(np.float32))
    shells, freqs, nshell = _frequency_shells(a.shape, voxel)
    sel = shells < nshell
    sh = shells[sel]
    num = np.bincount(sh, weights=(fa[sel] * np.conj(fb[sel])).real, minlength=nshell)
    d1 = np.bincount(sh, weights=np.abs(fa[sel]) ** 2, minlength=nshell)
    d2 = np.bincount(sh, weights=np.abs(fb[sel]) ** 2, minlength=nshell)
    with np.errstate(invalid="ignore", divide="ignore"):
        curve = num / np.sqrt(d1 * d2)
    curve = np.nan_to_num(curve, nan=0.0)
    curve[0] = 1.0
    return freqs, curve


def resolution_at(freqs: np.ndarray, curve: np.ndarray, threshold: float = 0.143, persist: int = 1) -> float:
    """Resolution (A) where the FSC first drops below ``threshold`` (linear interpolation).

    ``persist`` > 1 ignores dips that recover within that many shells (useful for noisy conical FSCs).
    """
    for i in range(1, len(curve)):
        if all(c < threshold for c in curve[i:i + persist]):
            f0, f1 = freqs[i - 1], freqs[i]
            c0, c1 = curve[i - 1], curve[i]
            f = f1 if c0 == c1 else f0 + (threshold - c0) * (f1 - f0) / (c1 - c0)
            return float(1.0 / f) if f > 0 else float("inf")
    return float(1.0 / freqs[-1])


def randomize_phases_beyond(data: np.ndarray, voxel: np.ndarray, frequency: float, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    f = np.fft.rfftn(data.astype(np.float32))
    shells, freqs, _ = _frequency_shells(data.shape, voxel)
    cutoff_shell = int(np.searchsorted(freqs, frequency))
    sel = shells > cutoff_shell
    phases = rng.uniform(0, 2 * np.pi, size=int(sel.sum())).astype(np.float32)
    f[sel] = np.abs(f[sel]) * np.exp(1j * phases)
    return np.fft.irfftn(f, s=data.shape, axes=(0, 1, 2)).astype(np.float32)


def half_map_fsc(half_a: MapVolume, half_b: MapVolume, mask: MapVolume | None = None) -> dict[str, Any]:
    """Unmasked, masked and phase-randomisation-corrected FSC (Chen et al. 2013)."""
    voxel = half_a.voxel
    freqs, unmasked = fsc(half_a.data, half_b.data, voxel)
    result: dict[str, Any] = {
        "frequency": freqs.tolist(),
        "curves": {"unmasked": unmasked.tolist()},
        "resolution": {"unmasked": resolution_at(freqs, unmasked)},
        "pixel_size": float(np.mean(voxel)),
        "box": list(half_a.shape_xyz),
    }
    if mask is not None:
        if mask.data.shape != half_a.data.shape:
            raise ValueError("Mask and half maps have different dimensions")
        m = np.clip(mask.data, 0, 1)
        _, masked = fsc(half_a.data * m, half_b.data * m, voxel)
        # Phase randomisation beyond the point where the unmasked FSC drops below 0.8
        idx = next((i for i in range(1, len(unmasked)) if unmasked[i] < 0.8), len(unmasked) - 1)
        rand_freq = freqs[idx]
        ra = randomize_phases_beyond(half_a.data, voxel, rand_freq, seed=1)
        rb = randomize_phases_beyond(half_b.data, voxel, rand_freq, seed=2)
        _, rand = fsc(ra * m, rb * m, voxel)
        corrected = masked.copy()
        start = idx + 2
        with np.errstate(invalid="ignore", divide="ignore"):
            corr = (masked[start:] - rand[start:]) / (1.0 - rand[start:])
        corrected[start:] = np.nan_to_num(corr)
        result["curves"].update({
            "masked": masked.tolist(),
            "phase_randomized": rand.tolist(),
            "corrected": corrected.tolist(),
        })
        result["resolution"].update({
            "masked": resolution_at(freqs, masked),
            "corrected": resolution_at(freqs, corrected),
        })
        result["randomized_from"] = float(1.0 / rand_freq) if rand_freq > 0 else None
    best = result["resolution"].get("corrected", result["resolution"]["unmasked"])
    result["resolution"]["best"] = best
    return result


def directional_fsc(a: np.ndarray, b: np.ndarray, voxel: np.ndarray, mask: np.ndarray | None = None,
                    half_angle: float = 20.0, az_step: float = 15.0, el_step: float = 15.0,
                    threshold: float = 0.143, min_coefficients: int = 20) -> dict[str, Any]:
    """Conical FSC (3D FSC, Tan et al. 2017) over a hemisphere of directions.

    Returns the FSC curve and the threshold resolution for every direction (azimuth, elevation in degrees),
    plus the global curve. Directions d and -d are equivalent (Friedel symmetry), hence the hemisphere.
    """
    if a.shape != b.shape:
        raise ValueError(f"Map dimensions differ: {a.shape} vs {b.shape}")
    if mask is not None:
        m = np.clip(mask, 0, 1).astype(np.float32)
        a, b = a * m, b * m
    fa = np.fft.rfftn(a.astype(np.float32))
    fb = np.fft.rfftn(b.astype(np.float32))
    shells, freqs, nshell = _frequency_shells(a.shape, voxel)
    nz, ny, nx = a.shape
    kz = np.broadcast_to((np.fft.fftfreq(nz) / voxel[2])[:, None, None], fa.shape)
    ky = np.broadcast_to((np.fft.fftfreq(ny) / voxel[1])[None, :, None], fa.shape)
    kx = np.broadcast_to((np.fft.rfftfreq(nx) / voxel[0])[None, None, :], fa.shape)
    sel = (shells < nshell) & (shells > 0)
    sh = shells[sel]
    cross = (fa[sel] * np.conj(fb[sel])).real.astype(np.float64)
    p1 = (np.abs(fa[sel]) ** 2).astype(np.float64)
    p2 = (np.abs(fb[sel]) ** 2).astype(np.float64)
    del fa, fb
    norm = np.sqrt(kx[sel] ** 2 + ky[sel] ** 2 + kz[sel] ** 2)
    ux = (kx[sel] / norm).astype(np.float32)
    uy = (ky[sel] / norm).astype(np.float32)
    uz = (kz[sel] / norm).astype(np.float32)

    def curve(keep: np.ndarray | None, fallback: np.ndarray | None = None) -> np.ndarray:
        s_ = sh if keep is None else sh[keep]
        num = np.bincount(s_, weights=cross if keep is None else cross[keep], minlength=nshell)
        d1 = np.bincount(s_, weights=p1 if keep is None else p1[keep], minlength=nshell)
        d2 = np.bincount(s_, weights=p2 if keep is None else p2[keep], minlength=nshell)
        with np.errstate(invalid="ignore", divide="ignore"):
            c = np.nan_to_num(num / np.sqrt(d1 * d2), nan=0.0)
        if fallback is not None:
            # low-frequency shells hold too few coefficients inside a narrow cone: use the global value there
            sparse = np.bincount(s_, minlength=nshell) < min_coefficients
            c[sparse] = fallback[sparse]
        c[0] = 1.0
        return c

    global_curve = curve(None)
    cos_lim = float(np.cos(np.radians(half_angle)))
    elevations = list(np.arange(0.0, 90.0 + 1e-6, el_step))
    azimuths = list(np.arange(0.0, 360.0 - 1e-6, az_step))
    directions = []
    for el in elevations:
        for az in (azimuths if el < 89.999 else [0.0]):
            d = (np.cos(np.radians(el)) * np.cos(np.radians(az)), np.cos(np.radians(el)) * np.sin(np.radians(az)),
                 np.sin(np.radians(el)))
            keep = np.abs(ux * d[0] + uy * d[1] + uz * d[2]) >= cos_lim
            c = curve(keep, global_curve)
            # 3-shell running mean: conical FSCs are noisy because each cone holds few coefficients
            smooth = c.copy()
            smooth[1:-1] = (c[:-2] + c[1:-1] + c[2:]) / 3.0
            directions.append({"azimuth": float(az), "elevation": float(el), "fsc": smooth,
                               "resolution": resolution_at(freqs, smooth, threshold, persist=2)})
    return {"frequency": freqs, "global": global_curve, "global_resolution": resolution_at(freqs, global_curve, threshold),
            "directions": directions, "azimuths": [float(x) for x in azimuths],
            "elevations": [float(x) for x in elevations], "half_angle": half_angle, "threshold": threshold}


def fsc_to_emdb_xml(freqs: list[float], curve: list[float], title: str = "FSC") -> str:
    """FSC curve in the XML format accepted by EMDB/OneDep."""
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', f'<fsc title="{title}" xaxis="Resolution (A-1)" yaxis="Correlation Coefficient">']
    for f, c in zip(freqs, curve):
        lines.append(f"  <coordinate>\n    <x>{f:.6f}</x>\n    <y>{c:.6f}</y>\n  </coordinate>")
    lines.append("</fsc>")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ filters
def fourier_filter(vol: MapVolume, bfactor: float = 0.0, lowpass: float = 0.0, highpass: float = 0.0,
                   edge_width: float = 0.01) -> np.ndarray:
    """Apply a global B-factor (negative = sharpen) and/or cosine-edged low/high-pass filter."""
    f = np.fft.rfftn(vol.data.astype(np.float32))
    nz, ny, nx = vol.data.shape
    kz = np.fft.fftfreq(nz)[:, None, None] / vol.voxel[2]
    ky = np.fft.fftfreq(ny)[None, :, None] / vol.voxel[1]
    kx = np.fft.rfftfreq(nx)[None, None, :] / vol.voxel[0]
    s2 = kz ** 2 + ky ** 2 + kx ** 2
    weight = np.ones_like(s2, dtype=np.float32)
    if bfactor:
        weight *= np.exp(-bfactor * s2 / 4.0).astype(np.float32)
    s = np.sqrt(s2)
    if lowpass and lowpass > 0:
        fc = 1.0 / lowpass
        w = np.clip((s - fc) / edge_width, 0, 1)
        weight *= (0.5 + 0.5 * np.cos(np.pi * w)).astype(np.float32)
    if highpass and highpass > 0:
        fc = 1.0 / highpass
        w = np.clip((fc - s) / edge_width, 0, 1)
        weight *= (0.5 + 0.5 * np.cos(np.pi * w)).astype(np.float32)
    return np.fft.irfftn(f * weight, s=vol.data.shape, axes=(0, 1, 2)).astype(np.float32)


def flip_hand(vol: MapVolume) -> np.ndarray:
    return np.ascontiguousarray(vol.data[::-1, :, :])


def crop_or_pad(vol: MapVolume, box: int) -> MapVolume:
    """Centered crop/pad to a cubic box, keeping the physical position of the content."""
    data = vol.data
    out = np.zeros((box, box, box), dtype=np.float32)
    nz, ny, nx = data.shape
    src, dst = [], []
    for n in (nz, ny, nx):
        if n >= box:
            s0 = (n - box) // 2
            src.append(slice(s0, s0 + box))
            dst.append(slice(0, box))
        else:
            d0 = (box - n) // 2
            src.append(slice(0, n))
            dst.append(slice(d0, d0 + n))
    out[tuple(dst)] = data[tuple(src)]
    # shift of voxel (0,0,0): source index of output index 0 along each axis
    shift_zyx = np.array([s.start - d.start for s, d in zip(src, dst)], dtype=np.float64)
    origin = vol.origin + shift_zyx[::-1] * vol.voxel
    return MapVolume(data=out, voxel=vol.voxel.copy(), origin=origin)


def bin_map(vol: MapVolume, factor: int) -> MapVolume:
    if factor <= 1:
        return vol
    nz, ny, nx = (s // factor * factor for s in vol.data.shape)
    d = vol.data[:nz, :ny, :nx]
    d = d.reshape(nz // factor, factor, ny // factor, factor, nx // factor, factor).mean(axis=(1, 3, 5))
    origin = vol.origin + (factor - 1) / 2.0 * vol.voxel
    return MapVolume(data=d.astype(np.float32), voxel=vol.voxel * factor, origin=origin)


def preview_factor(shape_xyz: tuple[int, int, int], max_box: int = 256) -> int:
    return max(1, int(math.ceil(max(shape_xyz) / float(max_box))))


def projection_image(vol: MapVolume, size: int = 160) -> np.ndarray:
    """Mean projection along z, contrast-stretched to uint8 (for job card thumbnails)."""
    work = bin_map(vol, preview_factor(vol.shape_xyz, size)) if max(vol.shape_xyz) > size else vol
    proj = work.data.mean(axis=0)
    lo, hi = np.percentile(proj, [1, 99.7])
    if hi <= lo:
        hi = lo + 1
    img = np.clip((proj - lo) / (hi - lo), 0, 1)
    return (img[::-1] * 255).astype(np.uint8)  # y up


def suggest_contour(vol: MapVolume, molecular_mass_da: float, mask: np.ndarray | None = None) -> float:
    """Threshold enclosing the volume expected for ``molecular_mass_da`` (1.21 A^3/Da)."""
    values = vol.data[mask > 0.5] if mask is not None else vol.data.ravel()
    voxel_volume = float(np.prod(vol.voxel))
    n_expected = int(molecular_mass_da * 1.21 / voxel_volume)
    n_expected = max(1, min(n_expected, values.size - 1))
    kth = values.size - n_expected
    return float(np.partition(values.ravel(), kth)[kth])


# --------------------------------------------------------------- model maps
def model_map(xyz: np.ndarray, weights: np.ndarray, like: MapVolume, resolution: float) -> np.ndarray:
    """Gaussian model map on the grid of ``like`` (Chimera ``molmap``-like, sigma = 0.225 d)."""
    sigma = max(0.225 * resolution, 0.5 * float(np.min(like.voxel)))
    radius = int(math.ceil(3.0 * sigma / float(np.min(like.voxel))))
    rng = np.arange(-radius, radius + 1)
    oz, oy, ox = np.meshgrid(rng, rng, rng, indexing="ij")
    offsets = np.stack([ox.ravel(), oy.ravel(), oz.ravel()], axis=1)  # (M, 3) in x, y, z index units
    nx, ny, nz = like.shape_xyz
    out = np.zeros(nx * ny * nz, dtype=np.float64)
    idx = like.to_index(xyz)
    base = np.rint(idx).astype(np.int64)
    frac = idx - base
    chunk = max(1, 2_000_000 // len(offsets))
    for start in range(0, len(xyz), chunk):
        b = base[start:start + chunk, None, :] + offsets[None, :, :]  # (n, M, 3)
        d = (offsets[None, :, :] - frac[start:start + chunk, None, :]) * like.voxel  # A
        val = np.exp(-np.sum(d ** 2, axis=2) / (2 * sigma ** 2)) * weights[start:start + chunk, None]
        ok = (b[..., 0] >= 0) & (b[..., 0] < nx) & (b[..., 1] >= 0) & (b[..., 1] < ny) & (b[..., 2] >= 0) & (b[..., 2] < nz)
        flat = (b[..., 2] * ny + b[..., 1]) * nx + b[..., 0]
        np.add.at(out, flat[ok], val[ok])
    return out.reshape(nz, ny, nx).astype(np.float32)


def correlation(a: np.ndarray, b: np.ndarray, mask: np.ndarray | None = None) -> float:
    if mask is not None:
        sel = mask > 0.5
        a, b = a[sel], b[sel]
    a = a.ravel().astype(np.float64)
    b = b.ravel().astype(np.float64)
    a = a - a.mean()
    b = b - b.mean()
    denom = math.sqrt(float(np.dot(a, a)) * float(np.dot(b, b)))
    return float(np.dot(a, b) / denom) if denom > 0 else 0.0
