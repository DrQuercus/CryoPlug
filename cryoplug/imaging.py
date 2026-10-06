"""Tiny dependency-free PNG writer and thumbnail renderers."""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

import numpy as np

CHAIN_COLORS = [
    (66, 133, 244), (234, 67, 53), (251, 188, 5), (52, 168, 83), (171, 71, 188),
    (0, 172, 193), (255, 112, 67), (158, 157, 36), (92, 107, 192), (240, 98, 146),
]


def write_png(path: str | Path, image: np.ndarray) -> Path:
    """Write a (H, W) grayscale, (H, W, 3) RGB or (H, W, 4) RGBA uint8 image."""
    img = np.ascontiguousarray(image, dtype=np.uint8)
    if img.ndim == 2:
        color_type = 0
    elif img.ndim == 3 and img.shape[2] in (3, 4):
        color_type = 2 if img.shape[2] == 3 else 6
    else:
        raise ValueError("image must be (H, W), (H, W, 3) or (H, W, 4)")
    h, w = img.shape[:2]
    raw = b"".join(b"\x00" + img[y].tobytes() for y in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, color_type, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 6))
    png += chunk(b"IEND", b"")
    path = Path(path)
    path.write_bytes(png)
    return path


def _draw_line(img: np.ndarray, x0: int, y0: int, x1: int, y1: int, color: tuple[int, ...]) -> None:
    h, w = img.shape[:2]
    n = max(abs(x1 - x0), abs(y1 - y0), 1)
    xs = np.rint(np.linspace(x0, x1, n + 1)).astype(int)
    ys = np.rint(np.linspace(y0, y1, n + 1)).astype(int)
    ok = (xs >= 0) & (xs < w) & (ys >= 0) & (ys < h)
    img[ys[ok], xs[ok]] = color
    # 2-px thick lines read better at thumbnail size
    xs2 = np.clip(xs[ok] + 1, 0, w - 1)
    img[ys[ok], xs2] = color


def trace_image(chains: list[np.ndarray], size: int = 160) -> np.ndarray:
    """Render C-alpha traces (one (N, 3) array per chain) projected on the XY plane, on a transparent background."""
    img = np.zeros((size, size, 4), dtype=np.uint8)
    pts = [c for c in chains if len(c)]
    if not pts:
        return img
    allp = np.concatenate(pts)[:, :2]
    lo, hi = allp.min(axis=0), allp.max(axis=0)
    span = float(max(hi - lo)) or 1.0
    margin = 8
    scale = (size - 2 * margin) / span
    center = (lo + hi) / 2.0
    for ci, chain in enumerate(pts):
        color = (*CHAIN_COLORS[ci % len(CHAIN_COLORS)], 255)
        xy = (chain[:, :2] - center) * scale + size / 2.0
        xy[:, 1] = size - xy[:, 1]
        for i in range(len(xy) - 1):
            # do not connect residues that are far apart (chain breaks)
            if np.linalg.norm(chain[i + 1] - chain[i]) > 4.5:
                continue
            _draw_line(img, int(xy[i, 0]), int(xy[i, 1]), int(xy[i + 1, 0]), int(xy[i + 1, 1]), color)
    return img
