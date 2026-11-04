// The CryoPlug 16-bit map encoding (tag CPQ1 in the MRC header: value = offset + scale × stored int16), decoded into
// the float32 MRC file Mol* reads; other maps are left as they are. Pure: used on the main thread and in the decode
// worker (decode-worker.js).
export const TAG = 'CPQ1';

export function decodeMap(buffer) {
  const bytes = new Uint8Array(buffer);
  if (bytes.length < 1024) return bytes;
  const dv = new DataView(buffer);
  const little = bytes[212] === 0x44 || (bytes[212] !== 0x11 && dv.getUint32(12, true) <= 16);
  if (!little || dv.getInt32(12, true) !== 1 || String.fromCharCode(...bytes.subarray(164, 168)) !== TAG) return bytes;
  const start = 1024 + dv.getInt32(92, true);
  const n = dv.getInt32(0, true) * dv.getInt32(4, true) * dv.getInt32(8, true);
  if (start % 2 || !(n > 0) || bytes.length < start + 2 * n) return bytes;
  const scale = dv.getFloat32(168, true);
  const offset = dv.getFloat32(172, true);
  const out = new ArrayBuffer(1024 + 4 * n);
  const head = new Uint8Array(out, 0, 1024);
  head.set(bytes.subarray(0, 1024));
  head.fill(0, 164, 176); // the tag, scale and offset no longer apply
  const odv = new DataView(out);
  odv.setInt32(12, 2, true); // mode 2: float32
  odv.setInt32(92, 0, true); // no extended header
  const src = new Int16Array(buffer, start, n);
  const dst = new Float32Array(out, 1024, n);
  for (let i = 0; i < n; i++) dst[i] = offset + scale * src[i];
  return new Uint8Array(out);
}
