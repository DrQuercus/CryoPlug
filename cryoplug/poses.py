"""Particle orientations: conversions between CryoSPARC, RELION and cryoDRGN, comparison of two sets of poses of
the same particles (ab initio poses against consensus poses), and alignment of a map on a reference map.

Conventions are cryoDRGN's: a pose is a rotation matrix ``R`` and an in-plane shift ``t`` in fractions of the box.
The image of a particle is the projection of the volume ``v`` along the third row of ``R``:
``image(x) = ∫ v(Rᵀ (x, z)) dz`` (the decoder evaluates the central slice at ``coords @ R``).

Two reconstructions of the same particles made independently (an ab initio run and a consensus refinement) agree up
to a rigid change of frame, and possibly a mirror: a reconstruction from projections cannot tell a molecule from its
mirror image. A *frame* holds that change: ``rotation`` G (3 × 3), ``mirror`` and ``shift`` w (fraction of the box of
the ab initio volume). The ab initio volume ``v`` is brought onto the reference as
``V(r) = v(M (r + w))`` with ``M = Gᵀ`` (or ``diag(1, 1, -1) Gᵀ`` when mirrored), and its poses become
``R' = R Gᵀ`` (or ``D R D Gᵀ``), ``t' = t − (R' w)ₓᵧ``.
"""
from __future__ import annotations

from typing import Any

import numpy as np

MIRROR = np.diag([1.0, 1.0, -1.0])
_REVERSE = np.array([[0.0, 0.0, 1.0], [0.0, 1.0, 0.0], [1.0, 0.0, 0.0]])  # (x, y, z) <-> (z, y, x) array order


# --------------------------------------------------------------- conversions
def rotvec_to_matrix(v: np.ndarray) -> np.ndarray:
    """Rotation matrices (N, 3, 3) from axis-angle vectors (N, 3) (Rodrigues; cryoDRGN's lie_tools.expmap)."""
    v = np.atleast_2d(np.asarray(v, dtype=np.float64))
    theta = np.linalg.norm(v, axis=1, keepdims=True)
    k = v / np.maximum(theta, 1e-12)
    kx = np.zeros((len(v), 3, 3))
    kx[:, 0, 1], kx[:, 0, 2], kx[:, 1, 2] = -k[:, 2], k[:, 1], -k[:, 0]
    kx = kx - kx.transpose(0, 2, 1)
    t = theta[:, :, None]
    return np.eye(3)[None] + np.sin(t) * kx + (1 - np.cos(t)) * kx @ kx


def matrix_to_rotvec(rot: np.ndarray) -> np.ndarray:
    """Axis-angle vectors (N, 3) of rotation matrices (N, 3, 3)."""
    from scipy.spatial.transform import Rotation
    return Rotation.from_matrix(np.asarray(rot, dtype=np.float64).reshape(-1, 3, 3)).as_rotvec()


def cryosparc_to_cryodrgn(pose: np.ndarray, shift: np.ndarray, box: float) -> tuple[np.ndarray, np.ndarray]:
    """CryoSPARC ``alignments3D/pose`` (axis-angle) and ``/shift`` (pixels) as cryoDRGN poses (parse_pose_csparc)."""
    rot = rotvec_to_matrix(pose).transpose(0, 2, 1)
    return rot, np.asarray(shift, dtype=np.float64) / float(box)


def cryodrgn_to_cryosparc(rot: np.ndarray, trans: np.ndarray, box: float) -> tuple[np.ndarray, np.ndarray]:
    """Inverse of :func:`cryosparc_to_cryodrgn`: axis-angle poses and shifts in pixels of the original images."""
    return matrix_to_rotvec(np.asarray(rot).transpose(0, 2, 1)), np.asarray(trans, dtype=np.float64) * float(box)


def relion_to_matrix(euler: np.ndarray) -> np.ndarray:
    """Rotation matrices from RELION Euler angles (rot, tilt, psi in degrees), as cryoDRGN's R_from_relion."""
    a, b, y = np.radians(np.atleast_2d(np.asarray(euler, dtype=np.float64))).T
    n = len(a)
    zero, one = np.zeros(n), np.ones(n)
    ra = np.stack([np.stack([np.cos(a), -np.sin(a), zero], -1), np.stack([np.sin(a), np.cos(a), zero], -1),
                   np.stack([zero, zero, one], -1)], 1)
    rb = np.stack([np.stack([np.cos(b), zero, -np.sin(b)], -1), np.stack([zero, one, zero], -1),
                   np.stack([np.sin(b), zero, np.cos(b)], -1)], 1)
    ry = np.stack([np.stack([np.cos(y), -np.sin(y), zero], -1), np.stack([np.sin(y), np.cos(y), zero], -1),
                   np.stack([zero, zero, one], -1)], 1)
    tr = (0, 2, 1)
    rot = ry.transpose(tr) @ rb.transpose(tr) @ ra.transpose(tr)
    for i, j in ((0, 2), (2, 0), (1, 2), (2, 1)):
        rot[:, i, j] *= -1
    return rot


def matrix_to_relion(rot: np.ndarray) -> np.ndarray:
    """RELION Euler angles (degrees, in [-180, 180)) of rotation matrices, as cryoDRGN's R_to_relion_scipy."""
    from scipy.spatial.transform import Rotation
    f = np.ones((3, 3))
    f[0, 1] = f[1, 0] = f[1, 2] = f[2, 1] = -1
    euler = Rotation.from_matrix(np.asarray(rot, dtype=np.float64).reshape(-1, 3, 3) * f).as_euler("zxz", degrees=True)
    euler[:, 0] -= 90
    euler[:, 2] += 90
    return (euler + 180) % 360 - 180


# ------------------------------------------------------------------ rotations
def rotation_angle(rot: np.ndarray) -> np.ndarray:
    """Angle (degrees) of rotation matrices (..., 3, 3)."""
    tr = np.trace(np.asarray(rot, dtype=np.float64), axis1=-2, axis2=-1)
    return np.degrees(np.arccos(np.clip((tr - 1) / 2, -1.0, 1.0)))


def pose_change(rot1: np.ndarray, rot2: np.ndarray) -> np.ndarray:
    """Angle (degrees) between the poses of the same particles in two sets (e.g. two epochs)."""
    a, b = np.asarray(rot1, dtype=np.float64), np.asarray(rot2, dtype=np.float64)
    return rotation_angle(np.einsum("nji,njk->nik", a, b))


def nearest_rotation(m: np.ndarray) -> np.ndarray:
    """The rotation matrix closest to a 3 × 3 matrix (orthogonal Procrustes)."""
    u, _s, vt = np.linalg.svd(np.asarray(m, dtype=np.float64))
    d = np.sign(np.linalg.det(u @ vt)) or 1.0
    return u @ np.diag([1.0, 1.0, d]) @ vt


def axis_rotation(axis: str, degrees: float) -> np.ndarray:
    t = np.radians(degrees)
    c, s = np.cos(t), np.sin(t)
    if axis == "x":
        return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])
    if axis == "y":
        return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


def symmetry_operators(symbol: str) -> tuple[np.ndarray, str]:
    """Rotations of a point group (RELION/CryoSPARC axes: Cn about z, the 2-folds of Dn along x) and the symbol
    used. T, O and I are not handled: C1 is used instead (the caller is told by the returned symbol)."""
    s = str(symbol or "C1").strip().upper()
    try:
        n = int(s[1:]) if len(s) > 1 else 1
    except ValueError:
        n = 0
    if s[:1] == "C" and n >= 1:
        return np.stack([axis_rotation("z", 360.0 * k / n) for k in range(n)]), f"C{n}"
    if s[:1] == "D" and n >= 2:
        cn = [axis_rotation("z", 360.0 * k / n) for k in range(n)]
        flip = axis_rotation("x", 180.0)
        return np.stack(cn + [flip @ r for r in cn]), f"D{n}"
    return np.eye(3)[None], "C1"


def uniform_rotations(n: int) -> np.ndarray:
    """``n`` rotations spread evenly over SO(3) (super-Fibonacci spirals, Alexa 2022): about 15° apart for 4,000."""
    from scipy.spatial.transform import Rotation
    phi, psi = np.sqrt(2.0), 1.533751168755204288118041
    s = np.arange(n) + 0.5
    r, big_r = np.sqrt(s / n), np.sqrt(1.0 - s / n)
    alpha, beta = 2 * np.pi * s / phi, 2 * np.pi * s / psi
    quat = np.stack([r * np.sin(alpha), r * np.cos(alpha), big_r * np.sin(beta), big_r * np.cos(beta)], 1)
    return Rotation.from_quat(quat).as_matrix()


def _small_rotations(n: int, degrees: float, rng: np.random.Generator) -> np.ndarray:
    axes = rng.normal(size=(n, 3))
    axes /= np.linalg.norm(axes, axis=1, keepdims=True)
    return rotvec_to_matrix(axes * np.radians(degrees) * rng.uniform(0.3, 1.0, (n, 1)))


# --------------------------------------------------------------- comparison
def _residuals(q: np.ndarray, ops: np.ndarray, g: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Angle between each relative rotation Qᵢ and the frame G, over the symmetry copies (smallest), and the copy."""
    flat = q.reshape(len(q), 9)
    traces = np.stack([flat @ (op.T @ g).reshape(9) for op in ops], 1)  # trace(Gᵀ Sₖ Qᵢ)
    best = np.argmax(traces, axis=1)
    tr = traces[np.arange(len(q)), best]
    return np.degrees(np.arccos(np.clip((tr - 1) / 2, -1.0, 1.0))), best


def _fit_frame(q: np.ndarray, ops: np.ndarray, threshold: float, rng: np.random.Generator) -> np.ndarray:
    """Rotation G with Qᵢ ≈ Sₖ⁻¹ G for most particles: best of candidate particles (most within ``threshold``), then
    the mean rotation of those particles, refined a few times."""
    picks = rng.choice(len(q), size=min(len(q), 64), replace=False)
    best_g, best_count = np.eye(3), -1
    for j in picks:
        count = int((_residuals(q, ops, q[j])[0] < threshold).sum())
        if count > best_count:
            best_g, best_count = q[j], count
    g = best_g
    for _ in range(6):
        res, copy = _residuals(q, ops, g)
        inside = res < max(threshold, float(np.percentile(res, 25)))
        if inside.sum() < 3:
            break
        aligned = np.einsum("nij,njk->nik", ops[copy[inside]], q[inside])  # Sₖ Qᵢ ≈ G
        g = nearest_rotation(aligned.sum(axis=0))
    return g


def compare_poses(rot_a: np.ndarray, trans_a: np.ndarray | None, rot_c: np.ndarray, trans_c: np.ndarray | None,
                  symmetry: str = "C1", threshold: float = 15.0, seed: int = 0, sample: int = 20_000) -> dict[str, Any]:
    """Compares the poses of the same particles from two independent reconstructions (``a``: ab initio, ``c``:
    consensus). Finds the frame that maps one onto the other (rotation, hand, shift) from the particles that agree,
    then measures every particle: angle between its two poses, between its two viewing directions, and the shift
    difference (fractions of the box). The hand is the one under which the relative rotations agree best."""
    rot_a = np.asarray(rot_a, dtype=np.float64)
    rot_c = np.asarray(rot_c, dtype=np.float64)
    n = len(rot_a)
    if n == 0 or len(rot_c) != n:
        raise ValueError(f"The two pose sets differ in size ({n} and {len(rot_c)})")
    ops, used = symmetry_operators(symmetry)
    rng = np.random.default_rng(seed)
    sub = np.sort(rng.choice(n, size=min(n, sample), replace=False))
    hands: dict[bool, dict[str, Any]] = {}
    for mirror in (False, True):
        ra = MIRROR @ rot_a @ MIRROR if mirror else rot_a
        q = np.einsum("nji,njk->nik", rot_c, ra)  # Rcᵀ Ra: the same rotation G for every particle
        g = _fit_frame(q[sub], ops, threshold, rng)
        res, copy = _residuals(q, ops, g)
        hands[mirror] = {"rotation": g, "angle": res, "copy": copy, "median": float(np.median(res)), "ra": ra}
    mirror = hands[True]["median"] < hands[False]["median"]
    chosen, other = hands[mirror], hands[not mirror]
    g, angle, copy = chosen["rotation"], chosen["angle"], chosen["copy"]
    aligned = chosen["ra"] @ g.T  # R' ≈ Rc Sₖᵀ
    rc_sym = np.einsum("nij,nkj->nik", rot_c, ops[copy])  # Rc Sₖᵀ (the symmetry copy each particle matches)
    cosv = np.clip(np.einsum("ni,ni->n", aligned[:, 2, :], rc_sym[:, 2, :]), -1.0, 1.0)
    view = np.degrees(np.arccos(cosv))
    inliers = angle < max(threshold, 1e-6)
    shift = np.zeros(3)
    shift_error = None
    if trans_a is not None and trans_c is not None:
        ta, tc = np.asarray(trans_a, dtype=np.float64), np.asarray(trans_c, dtype=np.float64)
        a = aligned[:, :2, :]
        use = inliers if inliers.sum() >= 10 else np.ones(n, bool)
        shift, *_ = np.linalg.lstsq(a[use].reshape(-1, 3), (ta - tc)[use].reshape(-1), rcond=None)
        shift_error = np.linalg.norm(ta - tc - np.einsum("nij,j->ni", a, shift), axis=1)
    med, med_other = chosen["median"], other["median"]
    if med < 20 and med_other > 2 * med + 10:
        confidence = "clear"
    elif med < 45 and med_other > med + 5:
        confidence = "weak"
    else:
        confidence = "none"
    return {"rotation": g, "mirror": bool(mirror), "shift": shift, "symmetry": used, "angle": angle, "view_angle": view,
            "shift_error": shift_error, "median": med, "other_hand_median": med_other, "hand_confidence": confidence,
            "within": {t: float((angle < t).mean()) for t in (5, 10, 20, 30)}, "global_angle": float(rotation_angle(g)),
            "inliers": float(inliers.mean())}


def frame_dict(rotation: np.ndarray, mirror: bool, shift: np.ndarray, **extra: Any) -> dict[str, Any]:
    """A frame in the JSON form kept in the metadata of outputs."""
    return {"rotation": np.round(np.asarray(rotation, dtype=np.float64), 8).tolist(), "mirror": bool(mirror),
            "shift": np.round(np.asarray(shift, dtype=np.float64), 8).tolist(), **extra}


IDENTITY_FRAME: dict[str, Any] = {"rotation": np.eye(3).tolist(), "mirror": False, "shift": [0.0, 0.0, 0.0]}


def flipped_frame(frame: dict[str, Any] | None) -> dict[str, Any]:
    """The frame followed by a change of hand (reflection z → -z about the centre), as one frame: volumes and poses
    are then resampled once."""
    g, mirror, w = _frame_parts(frame or IDENTITY_FRAME)
    out = dict(frame or {})
    out.update(frame_dict(MIRROR @ g @ MIRROR, not mirror, MIRROR @ w))
    return out


def _frame_parts(frame: dict[str, Any]) -> tuple[np.ndarray, bool, np.ndarray]:
    g = np.asarray(frame.get("rotation", np.eye(3)), dtype=np.float64).reshape(3, 3)
    return g, bool(frame.get("mirror")), np.asarray(frame.get("shift", [0, 0, 0]), dtype=np.float64).reshape(3)


def apply_frame(rot: np.ndarray, trans: np.ndarray | None, frame: dict[str, Any]) -> tuple[np.ndarray, np.ndarray | None]:
    """Poses of the ab initio volume expressed for the volume brought into the reference frame."""
    g, mirror, w = _frame_parts(frame)
    rot = np.asarray(rot, dtype=np.float64)
    ra = MIRROR @ rot @ MIRROR if mirror else rot
    out = ra @ g.T
    if trans is None:
        return out, None
    return out, np.asarray(trans, dtype=np.float64) - np.einsum("nij,j->ni", out[:, :2, :], w)


def frame_volume(data: np.ndarray, frame: dict[str, Any], inverse: bool = False, order: int = 1,
                 frame_box: int | None = None) -> np.ndarray:
    """A volume (z, y, x) of the ab initio frame resampled into the reference frame (``inverse``: the other way, e.g.
    a mask drawn on the reference to use with the ab initio model). Rotation about the centre of the box (index
    D/2, as cryoDRGN and the FFT); the grid and the pixel size are kept. ``frame_box``: size of the box the shift of
    the frame refers to (the training images), when the volume has the same pixel size but another box."""
    from scipy import ndimage
    g, mirror, w = _frame_parts(frame)
    data = np.asarray(data, dtype=np.float32)
    d = data.shape[0]
    c = np.full(3, d // 2, dtype=np.float64)
    m = (MIRROR @ g.T) if mirror else g.T
    w_px = w * (frame_box or d)
    if not inverse:  # V(r) = v(M (r + w))
        a = _REVERSE @ m @ _REVERSE
        offset = c - a @ c + _REVERSE @ (m @ w_px)
    else:  # v(s) = V(Mᵀ s − w)
        a = _REVERSE @ m.T @ _REVERSE
        offset = c - a @ c - _REVERSE @ w_px
    shell = np.concatenate([data[0].ravel(), data[-1].ravel(), data[:, 0].ravel(), data[:, -1].ravel()])
    return ndimage.affine_transform(data, a, offset=offset, order=order, mode="constant",
                                    cval=float(np.median(shell))).astype(np.float32)


# ----------------------------------------------------------- map alignment
def _resample_centred(data: np.ndarray, apix: float, size: int, pixel: float) -> np.ndarray:
    """A volume on a size³ grid of the given pixel size, centres matched (index D/2), low-passed against aliasing."""
    from scipy import ndimage
    data = np.asarray(data, dtype=np.float32)
    step = pixel / apix
    if step > 1.0:
        data = ndimage.gaussian_filter(data, sigma=0.5 * step)
    c_in = np.full(3, data.shape[0] // 2, dtype=np.float64)
    c_out = np.full(3, size // 2, dtype=np.float64)
    return ndimage.affine_transform(data, np.eye(3) * step, offset=c_in - step * c_out, output_shape=(size,) * 3,
                                    order=1, mode="constant", cval=0.0)


def _prepared_for_search(data: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Masked, zero-mean, unit-norm copy (the correlation of two of them is their normalised cross-correlation)."""
    v = data * mask
    v = v - v[mask > 0.5].mean()
    v *= mask
    return v / (np.linalg.norm(v) or 1.0)


class _MapSearch:
    """Correlation of the moving map, rotated by M, with the reference, for every translation at once (FFT), on
    low-passed copies resampled to size³ voxels of a common pixel size."""

    def __init__(self, moving: np.ndarray, apix_m: float, reference: np.ndarray, apix_r: float, size: int):
        from scipy import ndimage
        self.size, self.c = size, size // 2
        self.extent_m = moving.shape[0] * apix_m
        self.pixel = max(self.extent_m, reference.shape[0] * apix_r) / size
        zz, yy, xx = (np.indices((size,) * 3) - self.c).astype(np.float64)
        radius = np.sqrt(xx ** 2 + yy ** 2 + zz ** 2)
        self.mask = np.clip(0.5 + 0.5 * np.cos(np.pi * np.clip((radius - 0.38 * size) / (0.08 * size), 0, 1)), 0, 1)
        self.mov = ndimage.gaussian_filter(_resample_centred(moving, apix_m, size, self.pixel), 1.0)
        ref = ndimage.gaussian_filter(_resample_centred(reference, apix_r, size, self.pixel), 1.0)
        self.ref_fft = np.conj(np.fft.rfftn(_prepared_for_search(ref, self.mask)))
        self.grid = np.stack([xx.ravel(), yy.ravel(), zz.ravel()])  # positions (x, y, z) relative to the centre
        limit = size // 4  # translations considered: up to a quarter of the box
        lim = np.r_[np.arange(0, limit + 1), np.arange(size - limit, size)]
        self.ok = np.zeros((size,) * 3, bool)
        self.ok[np.ix_(lim, lim, lim)] = True

    def score(self, m: np.ndarray) -> tuple[float, np.ndarray]:
        """Best correlation and the shift t (x, y, z, in search voxels, sub-voxel) with V(r) = u(r + t), u(r) = v(M r)."""
        from scipy import ndimage
        size = self.size
        pos = m @ self.grid
        coords = np.stack([pos[2], pos[1], pos[0]]) + self.c
        u = ndimage.map_coordinates(self.mov, coords, order=1, mode="constant", cval=0.0).reshape((size,) * 3)
        cc = np.fft.irfftn(np.fft.rfftn(_prepared_for_search(u, self.mask)) * self.ref_fft, s=u.shape, axes=(0, 1, 2))
        masked = np.where(self.ok, cc, -np.inf)
        idx = np.unravel_index(int(np.argmax(masked)), cc.shape)
        t = []
        for axis, i in enumerate(idx):  # parabola through the peak and its neighbours: sub-voxel shift
            lo, hi = list(idx), list(idx)
            lo[axis], hi[axis] = (i - 1) % size, (i + 1) % size
            a, b, c = cc[tuple(lo)], cc[idx], cc[tuple(hi)]
            den = a - 2 * b + c
            frac = 0.5 * (a - c) / den if den < 0 else 0.0
            t.append((i + size // 2) % size - size // 2 + float(np.clip(frac, -0.5, 0.5)))
        return float(cc[idx]), np.array(t[::-1])

    def refine(self, s: float, m: np.ndarray, t: np.ndarray, steps: tuple[float, ...], rng: np.random.Generator,
               trials: int = 24) -> tuple[float, np.ndarray, np.ndarray]:
        for step in steps:
            for small in _small_rotations(trials, step, rng):
                s2, t2 = self.score(m @ small)
                if s2 > s:
                    s, m, t = s2, m @ small, t2
        return s, m, t


def align_maps(moving: np.ndarray, apix_moving: float, reference: np.ndarray, apix_reference: float, size: int = 32,
               n_rotations: int = 4000, keep: int = 6, fine: int = 64, seed: int = 0) -> dict[str, Any]:
    """Frame that brings ``moving`` onto ``reference`` (both hands tried): exhaustive search over evenly spread
    rotations on low-passed copies (size³ voxels), every translation at once by FFT, local refinement of the best
    candidates, then a final refinement of the best of each hand on a finer grid (fine³), where the hands are
    compared. Returns the frame (rotation, mirror, shift as a fraction of the moving box), the correlation reached
    and the best correlation with the other hand."""
    moving, reference = np.asarray(moving, dtype=np.float32), np.asarray(reference, dtype=np.float32)
    rng = np.random.default_rng(seed)
    coarse = _MapSearch(moving, apix_moving, reference, apix_reference, size)
    fine = max(size, min(fine, max(moving.shape[0], reference.shape[0])))
    fine_search = _MapSearch(moving, apix_moving, reference, apix_reference, fine) if fine > size else coarse
    base = uniform_rotations(n_rotations)
    best: dict[bool, tuple[float, np.ndarray, np.ndarray]] = {}
    for mirror in (False, True):
        cands = []
        for r in base:
            m = MIRROR @ r if mirror else r
            s, t = coarse.score(m)
            cands.append((s, m, t))
        cands.sort(key=lambda x: -x[0])
        refined = [coarse.refine(s, m, t, (10.0, 5.0, 2.5), rng) for s, m, t in cands[:keep]]
        _s, m, _t = max(refined, key=lambda x: x[0])
        s, t = fine_search.score(m)
        best[mirror] = fine_search.refine(s, m, t, (2.0, 1.0, 0.5), rng)
    mirror = best[True][0] > best[False][0]
    s, m, t = best[mirror]
    g = (MIRROR @ m).T if mirror else m.T  # M = D Gᵀ (mirror) or Gᵀ
    other = best[not mirror][0]
    gap = s - other
    confidence = "clear" if gap > 0.05 and s > 0.5 else ("weak" if gap > 0.015 else "none")
    return {"rotation": nearest_rotation(g), "mirror": bool(mirror), "shift": t * fine_search.pixel / fine_search.extent_m,
            "score": s, "other_hand_score": other, "hand_confidence": confidence, "pixel": fine_search.pixel}
