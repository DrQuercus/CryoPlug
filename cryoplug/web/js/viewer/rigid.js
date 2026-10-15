// Rigid transforms of the 3D viewer: maps, masks and models moved in space (ChimeraX's move, turn and fitmap).
// A transform is 16 numbers, row by row (the server's convention): p' = R p + t, with t in the last column.

export const identity = () => [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];

export function multiply(a, b) {
  const out = new Array(16).fill(0);
  for (let r = 0; r < 4; r++) for (let c = 0; c < 4; c++) for (let k = 0; k < 4; k++) out[r * 4 + c] += a[r * 4 + k] * b[k * 4 + c];
  return out;
}

// Inverse of a rotation and translation: R^T, -R^T t.
export function invert(m) {
  const out = identity();
  for (let r = 0; r < 3; r++) for (let c = 0; c < 3; c++) out[r * 4 + c] = m[c * 4 + r];
  for (let r = 0; r < 3; r++) out[r * 4 + 3] = -(out[r * 4] * m[3] + out[r * 4 + 1] * m[7] + out[r * 4 + 2] * m[11]);
  return out;
}

export const apply = (m, p) => [0, 1, 2].map((r) => m[r * 4] * p[0] + m[r * 4 + 1] * p[1] + m[r * 4 + 2] * p[2] + m[r * 4 + 3]);
export const rotate = (m, v) => [0, 1, 2].map((r) => m[r * 4] * v[0] + m[r * 4 + 1] * v[1] + m[r * 4 + 2] * v[2]);

export function translation(v) {
  const m = identity();
  [m[3], m[7], m[11]] = v;
  return m;
}

// Rotation by `deg` degrees about `axis` through `center` (right-handed).
export function rotation(axis, deg, center = [0, 0, 0]) {
  const n = Math.hypot(...axis);
  if (!n || !deg) return identity();
  const [x, y, z] = axis.map((v) => v / n);
  const t = (deg * Math.PI) / 180;
  const c = Math.cos(t);
  const s = Math.sin(t);
  const C = 1 - c;
  const r = [c + x * x * C, x * y * C - z * s, x * z * C + y * s, y * x * C + z * s, c + y * y * C, y * z * C - x * s,
    z * x * C - y * s, z * y * C + x * s, c + z * z * C];
  const m = [r[0], r[1], r[2], 0, r[3], r[4], r[5], 0, r[6], r[7], r[8], 0, 0, 0, 0, 1];
  const rc = rotate(m, center);
  [m[3], m[7], m[11]] = [center[0] - rc[0], center[1] - rc[1], center[2] - rc[2]];
  return m;
}

export const isIdentity = (m, tol = 1e-6) => !m || m.every((v, i) => Math.abs(v - (i % 5 === 0 ? 1 : 0)) <= tol);

// Rotation angle (degrees) and length of the translation (Å).
export function angle(m) {
  const tr = m[0] + m[5] + m[10];
  return (Math.acos(Math.max(-1, Math.min(1, (tr - 1) / 2))) * 180) / Math.PI;
}
export const shift = (m) => Math.hypot(m[3], m[7], m[11]);

// Many small rotations drift away from a rotation: the rotation part made orthonormal again (Gram-Schmidt on rows).
export function orthonormalize(m) {
  const out = [...m];
  const row = (i) => [out[i * 4], out[i * 4 + 1], out[i * 4 + 2]];
  const set = (i, v) => { [out[i * 4], out[i * 4 + 1], out[i * 4 + 2]] = v; };
  const norm = (v) => { const n = Math.hypot(...v) || 1; return v.map((x) => x / n); };
  const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  const a = norm(row(0));
  const b0 = row(1);
  const b = norm(b0.map((v, i) => v - dot(a, b0) * a[i]));
  set(0, a);
  set(1, b);
  set(2, [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]);
  return out;
}

export const rows = (m) => [0, 1, 2, 3].map((r) => m.slice(r * 4, r * 4 + 4).map((v) => Math.round(v * 1e6) / 1e6));

// A transform from rows (the server's answer, a link); null when it is not a rotation and a translation.
export function fromRows(value) {
  const flat = Array.isArray(value) ? value.flat() : [];
  if (flat.length === 12) flat.push(0, 0, 0, 1);
  if (flat.length !== 16 || !flat.every(Number.isFinite)) return null;
  const m = orthonormalize(flat);
  const close = m.every((v, i) => Math.abs(v - flat[i]) < 2e-3);
  return close && Math.abs(flat[12]) + Math.abs(flat[13]) + Math.abs(flat[14]) < 1e-9 && Math.abs(flat[15] - 1) < 1e-9 ? m : null;
}

// Column by column, as Mol*'s Mat4 stores it.
export const columnMajor = (m) => [0, 4, 8, 12, 1, 5, 9, 13, 2, 6, 10, 14, 3, 7, 11, 15].map((i) => m[i]);

export const describe = (m) => `${shift(m).toFixed(1)} Å · ${angle(m).toFixed(1)}°`;
