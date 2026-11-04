// Map transfer for the 3D viewer: the server sends 16-bit maps (half the bytes of float32), decoded in a worker into
// the float32 MRC files Mol* reads; downloads report their progress and measure the connection, which decides whether
// a quick 8-bit first look is worth it before the requested detail. A large map comes as bricks (bricks.js), fetched
// a few at a time. Also the detail levels a map offers.
import { api } from '../api.js';
import { decodeMap } from './decode.js';

export { decodeMap };
// Bytes per second assumed until a large download has been timed: an SSH tunnel or a VPN rather than a local network,
// so that a large map first shows a quick coarse look; the speed measured is remembered for the next visits.
const DEFAULT_SPEED = 8e6;
const SPEED_KEY = 'cryoplug.viewer.speed';
let speed = 0;
try {
  const saved = JSON.parse(localStorage.getItem(SPEED_KEY) || 'null');
  if (saved && Date.now() - saved.at < 7 * 86400e3 && saved.speed > 0) speed = saved.speed;
} catch { /* storage unavailable */ }
const headers = new Map(); // project:path -> Promise of the map header (box, voxel size, file size)

// Largest side of the map shown, in voxels (0: full size): the choices offered for a map and for a volume series.
export const MAP_BOXES = [128, 200, 300, 400, 512, 0];
export const SERIES_BOXES = [64, 80, 96, 128, 160, 200, 256];
export const DEFAULT_BOX = 200;
// Volumes of a series are binned more as they are more numerous: memory stays reasonable.
export const seriesBox = (n) => (n > 40 ? 80 : n > 24 ? 96 : 128);
export const COARSE_BOX = 96; // the quick first look of a large map
export const LOWPASS = [0, 3, 4, 5, 6, 8, 10, 12, 15, 20, 30];

export function mapInfo(project, path) {
  const key = `${project}:${path}`;
  if (!headers.has(key)) headers.set(key, api.mapInfo(project, path).catch((err) => { headers.delete(key); throw err; }));
  return headers.get(key);
}

// Binning of the server for a largest side of `box` voxels (64-512; 0: none).
export function binning(info, box) {
  if (!info || !(box > 0)) return 1;
  return Math.max(1, Math.ceil(Math.max(...info.box) / Math.max(64, Math.min(box, 512))));
}

// One choice per distinct binning, coarse to fine: { box, factor, dims, voxel, voxels }.
export function detailChoices(info, boxes) {
  const seen = new Map();
  for (const box of boxes) {
    const factor = binning(info, box);
    if (seen.has(factor)) continue;
    const dims = info.box.map((d) => Math.floor(d / factor));
    seen.set(factor, { box, factor, dims, voxel: info.pixel_size * factor, voxels: dims[0] * dims[1] * dims[2] });
  }
  return [...seen.values()].sort((a, b) => b.factor - a.factor);
}

export const fmtDims = (dims) => (dims.every((d) => d === dims[0]) ? `${dims[0]}³` : dims.join('×'));
export function fmtBytes(n) {
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)} GB`;
  if (n >= 1e6) return `${Math.round(n / 1e6)} MB`;
  return `${Math.max(1, Math.round(n / 1e3))} kB`;
}

// Low-pass choices that change something on a grid of `voxel` Å (finer than its Nyquist limit is a no-op).
export const lowpassChoices = (voxel) => LOWPASS.filter((d) => !d || d > 2 * voxel);

export const expectedSeconds = (bytes) => bytes / (speed || DEFAULT_SPEED);

// Download a map; onProgress(received, total) while it arrives. Returns the bytes of an MRC file Mol* can read.
const NETWORK_ERROR = 'The map could not be downloaded: connection to the server lost or blocked';
const network = (promise) => promise.catch(() => { throw new Error(NETWORK_ERROR); });

export async function fetchMap(url, onProgress) {
  const started = performance.now();
  const res = await network(fetch(url));
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch { /* not JSON */ }
    throw new Error(`${detail} (HTTP ${res.status})`);
  }
  const total = Number(res.headers.get('content-length')) || 0;
  let buffer;
  if (onProgress && total && res.body?.getReader) {
    const reader = res.body.getReader();
    let whole = new Uint8Array(total);
    let got = 0;
    for (;;) {
      const { done, value } = await network(reader.read());
      if (done) break;
      if (got + value.length > whole.length) { // a server that announced less than it sends
        const grown = new Uint8Array(Math.max(whole.length * 2, got + value.length));
        grown.set(whole.subarray(0, got));
        whole = grown;
      }
      whole.set(value, got);
      got += value.length;
      onProgress(got, total);
    }
    buffer = got === whole.length ? whole.buffer : whole.slice(0, got).buffer;
  } else {
    buffer = await network(res.arrayBuffer());
  }
  const seconds = (performance.now() - started) / 1000;
  if (buffer.byteLength > 1.5e6 && seconds > 0.02) {
    const seen = buffer.byteLength / seconds;
    speed = speed ? 0.6 * speed + 0.4 * seen : seen;
    try { localStorage.setItem(SPEED_KEY, JSON.stringify({ speed, at: Date.now() })); } catch { /* storage unavailable */ }
  }
  return decodeAsync(buffer);
}

// Several maps (the bricks of a large one) a few at a time, the progress of all of them together; the decoded bytes
// in the order of the URLs.
export async function fetchParts(urls, onProgress, concurrency = 3) {
  const got = new Array(urls.length).fill(0);
  const totals = new Array(urls.length).fill(0);
  const report = () => onProgress?.(got.reduce((a, b) => a + b, 0), totals.reduce((a, b) => a + b, 0));
  const results = new Array(urls.length);
  let next = 0;
  const work = async () => {
    while (next < urls.length) {
      const k = next++;
      results[k] = await fetchMap(urls[k], (g, t) => { got[k] = g; totals[k] = t; report(); });
    }
  };
  await Promise.all(Array.from({ length: Math.max(1, Math.min(concurrency, urls.length)) }, work));
  return results;
}

// ------------------------------------------------------------- decoding
// A pool of workers decodes the 16-bit maps (one per brick arriving): the page keeps responding. Without workers
// (an old browser, a file:// page), the main thread does it.
const WORKER_COUNT = Math.max(1, Math.min(3, (navigator.hardwareConcurrency || 2) - 1));
let workers = null;
let nextWorker = 0;
let seq = 0;
const pending = new Map();

function getWorkers() {
  if (workers !== null) return workers;
  workers = [];
  try {
    for (let i = 0; i < WORKER_COUNT; i++) {
      const w = new Worker(new URL('./decode-worker.js', import.meta.url), { type: 'module' });
      w.onmessage = (e) => {
        const p = pending.get(e.data.id);
        if (!p) return;
        pending.delete(e.data.id);
        if (e.data.error) p.reject(new Error(`Map decoding failed: ${e.data.error}`));
        else p.resolve(new Uint8Array(e.data.buffer, e.data.offset, e.data.length));
      };
      w.onerror = (e) => {
        for (const [id, p] of pending) { pending.delete(id); p.reject(new Error(`Map decoding failed: ${e.message || 'worker error'}`)); }
        workers = []; // the main thread takes over from now on
      };
      workers.push(w);
    }
  } catch {
    workers = [];
  }
  return workers;
}

export function decodeAsync(buffer) {
  const ws = getWorkers();
  if (!ws.length) return Promise.resolve(decodeMap(buffer));
  const w = ws[nextWorker++ % ws.length];
  const id = ++seq;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    try {
      w.postMessage({ id, buffer }, [buffer]);
    } catch (err) {
      pending.delete(id);
      reject(err);
    }
  });
}
