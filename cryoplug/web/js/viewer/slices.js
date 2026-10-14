// Orthogonal slices through a map (CryoSPARC-like): docked beside the 3D view, or enlarged in a split view where one
// plane, or all three, can be zoomed and panned. Grey levels between percentiles (or averaged over a few slices),
// the contour level as a tint or as lines — in the colours of the local resolution or variability map when the map
// is coloured by one — and crosshairs at the other planes: click a slice to move them; the value under the pointer
// is read out.
import { clear, h, icon } from '../ui.js';
import { RESOLUTION_COLORS, voxelAxes } from './scene.js';
import { fmtLevel, toSigma } from './stats.js';

const PLANES = [
  { label: 'XY', axes: [0, 1], fixed: 2 },
  { label: 'XZ', axes: [0, 2], fixed: 1 },
  { label: 'YZ', axes: [1, 2], fixed: 0 },
];
const CONTRAST = { auto: ['Auto contrast', 0.01, 0.999], high: ['High contrast', 0.05, 0.995], full: ['Full range', 0, 1] };
const THICKNESS = [1, 3, 5, 9, 15];
const CONTOURS = [['tint', 'Level: tint'], ['lines', 'Level: lines'], ['off', 'Level: off']];
const CROSS = 'rgba(134, 182, 239, 0.85)';
const STOPS = RESOLUTION_COLORS.map((c) => [(c >> 16) & 255, (c >> 8) & 255, c & 255]);
const AXIS = 'xyz';

const rgb = (c) => [(c >> 16) & 255, (c >> 8) & 255, c & 255];
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

// Colour of a value on the local resolution / variability scale, interpolated as Mol* does on the surface.
function scaleColor(v, min, max, out) {
  const n = STOPS.length - 1;
  const t = clamp(((v - min) / (max - min || 1)) * n, 0, n);
  const i = Math.min(n - 1, Math.floor(t));
  const f = t - i;
  for (let c = 0; c < 3; c++) out[c] = STOPS[i][c] * (1 - f) + STOPS[i + 1][c] * f;
  return out;
}

// Contour of `level` on a w × ht grid of values (marching squares): segments [x1, y1, x2, y2, ...] in voxel units.
function contourSegments(vals, w, ht, level) {
  const out = [];
  const t = (a, b) => (a === b ? 0.5 : (level - a) / (b - a));
  for (let j = 0; j < ht - 1; j++) {
    for (let i = 0; i < w - 1; i++) {
      const v00 = vals[j * w + i];
      const v10 = vals[j * w + i + 1];
      const v01 = vals[(j + 1) * w + i];
      const v11 = vals[(j + 1) * w + i + 1];
      const code = (v00 >= level ? 1 : 0) | (v10 >= level ? 2 : 0) | (v11 >= level ? 4 : 0) | (v01 >= level ? 8 : 0);
      if (code === 0 || code === 15) continue;
      const bottom = [i + t(v00, v10), j];
      const right = [i + 1, j + t(v10, v11)];
      const top = [i + t(v01, v11), j + 1];
      const left = [i, j + t(v00, v01)];
      const seg = (p, q) => out.push(p[0], p[1], q[0], q[1]);
      switch (code) {
        case 1: case 14: seg(left, bottom); break;
        case 2: case 13: seg(bottom, right); break;
        case 3: case 12: seg(left, right); break;
        case 4: case 11: seg(right, top); break;
        case 6: case 9: seg(bottom, top); break;
        case 7: case 8: seg(left, top); break;
        case 5: if ((v00 + v10 + v01 + v11) / 4 >= level) { seg(left, top); seg(bottom, right); } else { seg(left, bottom); seg(right, top); } break;
        case 10: if ((v00 + v10 + v01 + v11) / 4 >= level) { seg(left, bottom); seg(right, top); } else { seg(left, top); seg(bottom, right); } break;
        default: break;
      }
    }
  }
  return out;
}

// ctx: { stage, onLayout(), onHover(text | null), onClose() }
export function createSlices(root, ctx) {
  let item = null;
  let pos = [0, 0, 0];
  let dims = [1, 1, 1];
  const st = { split: false, plane: 0, all: false, contour: 'tint', contrast: 'auto', invert: false, thickness: 1, ratio: 0.5 };
  const sources = PLANES.map(() => ({ dirty: true, canvas: document.createElement('canvas'), segments: null, colors: null }));

  // ------------------------------------------------------------------ DOM
  const dockViews = PLANES.map((p, k) => {
    const canvas = h('canvas', { class: 'vw-sl-canvas', tabindex: 0,
      'aria-label': `${p.label} slice: click to move the crosshair, wheel or arrow keys for the next slice, double-click to enlarge` });
    const range = h('input', { type: 'range', min: 0, max: 0, step: 1, 'aria-label': `${p.label} slice position` });
    const where = h('span', { class: 'muted' });
    range.addEventListener('input', () => setPos(p.fixed, Number(range.value)));
    const grow = h('button', { type: 'button', class: 'icon-btn', title: `Enlarge (${p.label})`, 'aria-label': `Enlarge the ${p.label} slice`,
      onclick: () => enlarge(k) }, icon('expand'));
    const el = h('div', { class: 'vw-slice' }, h('div', { class: 'vw-slice-head' }, h('b', {}, p.label), where, grow), canvas, range);
    return { plane: k, big: false, zoom: 1, panX: 0, panY: 0, canvas, range, where, el };
  });
  const dock = h('div', { class: 'vw-sl-dock' }, dockViews.map((v) => v.el));

  const bigViews = PLANES.map((p, k) => {
    const canvas = h('canvas', { class: 'vw-sl-canvas', tabindex: 0,
      'aria-label': `${p.label} slice: click to move the crosshair, drag to pan, wheel to zoom, Shift + wheel or arrow keys for the next slice` });
    const tag = h('span', { class: 'vw-sl-tag' }, p.label);
    const cell = h('div', { class: 'vw-sl-cell' }, canvas, tag);
    return { plane: k, big: true, zoom: 1, panX: 0, panY: 0, canvas, tag, cell };
  });
  const tabs = PLANES.map((p, k) => h('button', { type: 'button', onclick: () => { st.all = false; st.plane = k; layout(); } }, p.label));
  const allTab = h('button', { type: 'button', title: 'The three planes side by side', onclick: () => { st.all = true; layout(); } }, 'All');
  const bigRange = h('input', { type: 'range', class: 'vw-sl-range', min: 0, max: 0, step: 1, 'aria-label': 'Slice position',
    oninput: (e) => setPos(PLANES[st.plane].fixed, Number(e.target.value)) });
  const bigWhere = h('span', { class: 'vw-sl-where' });
  const select = (options, value, onchange, label) => h('select', { 'aria-label': label, onchange: (e) => onchange(e.target.value) },
    options.map(([v, t]) => h('option', { value: v, selected: v === value }, t)));
  const contourSel = select(CONTOURS, st.contour, (v) => { st.contour = v; invalidate(); }, 'Contour level on the slices');
  const contrastSel = select(Object.entries(CONTRAST).map(([k, v]) => [k, v[0]]), st.contrast, (v) => { st.contrast = v; invalidate(); }, 'Contrast');
  const thickSel = select(THICKNESS.map((t) => [String(t), t === 1 ? '1 slice' : `Mean of ${t} slices`]), '1',
    (v) => { st.thickness = Number(v); invalidate(); }, 'Thickness: mean of a few slices');
  const invert = h('input', { type: 'checkbox', onchange: (e) => { st.invert = e.target.checked; invalidate(); } });
  const tool = (ic, title, onclick) => h('button', { type: 'button', class: 'icon-btn', title, 'aria-label': title, onclick }, icon(ic));
  const readout = h('div', { class: 'vw-sl-readout', 'aria-live': 'off' });
  const help = h('div', { class: 'vw-sl-help' },
    h('b', {}, 'Slices'),
    h('span', {}, 'Click: move the crosshair'), h('span', {}, 'Drag: pan · wheel: zoom'),
    h('span', {}, 'Shift + wheel, ↑ / ↓: next slice'), h('span', {}, 'Double-click: fit'));
  const handle = h('div', { class: 'vw-sl-handle', role: 'separator', 'aria-orientation': 'vertical', title: 'Drag to resize', tabindex: 0 });
  const split = h('div', { class: 'vw-sl-split' },
    h('div', { class: 'vw-sl-bar' },
      h('div', { class: 'seg', role: 'group', 'aria-label': 'Plane' }, tabs, allTab),
      bigRange, bigWhere,
      h('span', { class: 'vw-sl-tools' },
        tool('target', 'Fit the slices (double-click)', () => { bigViews.forEach(resetZoom); drawAll(); }),
        tool('shrink', 'Back to small slices beside the 3D view', () => setSplit(false)),
        tool('close', 'Close the slices (L)', () => ctx.onClose?.()))),
    h('div', { class: 'vw-sl-bar vw-sl-opts' }, contourSel, contrastSel, thickSel, h('label', { class: 'chk' }, invert, 'Invert')),
    h('div', { class: 'vw-sl-body' }, bigViews.map((v) => v.cell), help),
    readout, handle);
  clear(root, dock, split);
  [...dockViews, ...bigViews].forEach(bindCanvas);
  bindHandle();
  if (typeof ResizeObserver !== 'undefined') new ResizeObserver(() => drawAll()).observe(root);

  // --------------------------------------------------------------- render
  function invalidate() {
    sources.forEach((s) => { s.dirty = true; });
    drawAll();
  }

  // values of a plane at the current position, averaged over the thickness
  function planeValues(k) {
    const p = PLANES[k];
    const { data, space } = item.volume.grid.cells;
    const [a, b] = p.axes;
    const f = p.fixed;
    const w = dims[a];
    const ht = dims[b];
    const vals = new Float32Array(w * ht);
    const half = (st.thickness - 1) >> 1;
    const k0 = Math.max(0, pos[f] - half);
    const k1 = Math.min(dims[f] - 1, pos[f] + half);
    const c = [0, 0, 0];
    for (let kk = k0; kk <= k1; kk++) {
      c[f] = kk;
      for (let j = 0; j < ht; j++) {
        c[b] = j;
        for (let i = 0; i < w; i++) {
          c[a] = i;
          vals[j * w + i] += space.get(data, c[0], c[1], c[2]);
        }
      }
    }
    if (k1 > k0) { const n = k1 - k0 + 1; for (let q = 0; q < vals.length; q++) vals[q] /= n; }
    return vals;
  }

  // value of the colouring map (local resolution, variability) at voxel (i, j) of plane k, or NaN outside it
  function colourSampler(k) {
    const by = item.colorBy;
    if (!by?.volume) return null;
    const p = PLANES[k];
    const A = voxelAxes(item.volume);
    const C = voxelAxes(by.volume);
    const cells = by.volume.grid.cells;
    const cdims = cells.space.dimensions;
    const index = (axis, i) => Math.round((A.offset[axis] + A.scale[axis] * i - C.offset[axis]) / C.scale[axis]);
    const [a, b] = p.axes;
    const ia = Int32Array.from({ length: dims[a] }, (_, i) => index(a, i));
    const ib = Int32Array.from({ length: dims[b] }, (_, j) => index(b, j));
    const fixed = index(p.fixed, pos[p.fixed]);
    const c = [0, 0, 0];
    return (i, j) => {
      c[a] = ia[i]; c[b] = ib[j]; c[p.fixed] = fixed;
      if (c[0] < 0 || c[1] < 0 || c[2] < 0 || c[0] >= cdims[0] || c[1] >= cdims[1] || c[2] >= cdims[2]) return NaN;
      return cells.space.get(cells.data, c[0], c[1], c[2]);
    };
  }

  function grey() {
    const s = item.stats;
    const [, qlo, qhi] = CONTRAST[st.contrast];
    return st.contrast === 'full' ? [s.min, s.max] : [s.percentile(qlo), s.percentile(qhi)];
  }

  function render(k) {
    const src = sources[k];
    const [a, b] = PLANES[k].axes;
    const w = dims[a];
    const ht = dims[b];
    const vals = planeValues(k);
    const [low, high] = grey();
    const range = high - low || 1;
    const level = item.level;
    const by = item.colorBy?.volume ? item.colorBy : null;
    const sample = by && st.contour !== 'off' ? colourSampler(k) : null;
    const tint = rgb(item.color);
    const col = [0, 0, 0];
    src.canvas.width = w;
    src.canvas.height = ht;
    const g = src.canvas.getContext('2d');
    const img = g.createImageData(w, ht);
    const px = img.data;
    for (let j = 0; j < ht; j++) {
      for (let i = 0; i < w; i++) {
        const v = vals[j * w + i];
        let gv = ((v - low) / range) * 255;
        gv = gv < 0 ? 0 : gv > 255 ? 255 : gv;
        if (st.invert) gv = 255 - gv;
        const o = ((ht - 1 - j) * w + i) * 4; // y axis up
        if (st.contour === 'tint' && v >= level) {
          let t = tint;
          if (sample) { const c = sample(i, j); if (c > 0) t = scaleColor(c, by.min, by.max, col); }
          px[o] = gv * 0.45 + t[0] * 0.55;
          px[o + 1] = gv * 0.45 + t[1] * 0.55;
          px[o + 2] = gv * 0.45 + t[2] * 0.55;
        } else {
          px[o] = gv; px[o + 1] = gv; px[o + 2] = gv;
        }
        px[o + 3] = 255;
      }
    }
    g.putImageData(img, 0, 0);
    src.segments = st.contour === 'lines' ? contourSegments(vals, w, ht, level) : null;
    src.colors = null;
    if (src.segments && sample) { // each segment in the colour of the colouring map at its middle
      src.colors = [];
      for (let q = 0; q < src.segments.length; q += 4) {
        const c = sample(Math.round((src.segments[q] + src.segments[q + 2]) / 2), Math.round((src.segments[q + 1] + src.segments[q + 3]) / 2));
        src.colors.push(c > 0 ? `rgb(${scaleColor(c, by.min, by.max, col).map(Math.round).join(',')})` : null);
      }
    }
    src.dirty = false;
  }

  function resetZoom(view) { view.zoom = 1; view.panX = 0; view.panY = 0; }

  function drawView(view) {
    if (!view.canvas.isConnected || !view.canvas.clientWidth) return;
    const k = view.plane;
    const src = sources[k];
    if (src.dirty) render(k);
    const cv = view.canvas;
    const dpr = window.devicePixelRatio || 1;
    const W = Math.max(1, Math.round(cv.clientWidth * dpr));
    const H = Math.max(1, Math.round(cv.clientHeight * dpr));
    if (cv.width !== W || cv.height !== H) { cv.width = W; cv.height = H; }
    const g = cv.getContext('2d');
    g.setTransform(1, 0, 0, 1, 0, 0);
    g.fillStyle = '#000';
    g.fillRect(0, 0, W, H);
    const w = src.canvas.width;
    const ht = src.canvas.height;
    const s = Math.min(W / w, H / ht) * view.zoom;
    const ox = (W - w * s) / 2 + view.panX * dpr;
    const oy = (H - ht * s) / 2 + view.panY * dpr;
    view.t = { s, ox, oy, dpr, w, ht };
    g.imageSmoothingEnabled = s < 3; // voxels drawn as squares once zoomed in
    g.drawImage(src.canvas, ox, oy, w * s, ht * s);
    const sx = (i) => ox + (i + 0.5) * s;
    const sy = (j) => oy + (ht - 1 - j + 0.5) * s;
    if (src.segments?.length) {
      g.lineWidth = Math.max(1, (view.big ? 1.5 : 1) * dpr);
      const plain = `rgb(${rgb(item.color).join(',')})`;
      const segs = src.segments;
      if (!src.colors) {
        g.strokeStyle = plain;
        g.beginPath();
        for (let q = 0; q < segs.length; q += 4) { g.moveTo(sx(segs[q]), sy(segs[q + 1])); g.lineTo(sx(segs[q + 2]), sy(segs[q + 3])); }
        g.stroke();
      } else {
        for (let q = 0, n = 0; q < segs.length; q += 4, n++) {
          g.strokeStyle = src.colors[n] || plain;
          g.beginPath();
          g.moveTo(sx(segs[q]), sy(segs[q + 1]));
          g.lineTo(sx(segs[q + 2]), sy(segs[q + 3]));
          g.stroke();
        }
      }
    }
    // crosshairs at the two other planes
    const [a, b] = PLANES[k].axes;
    const lw = Math.max(1, Math.round(dpr));
    g.fillStyle = CROSS;
    g.fillRect(Math.round(sx(pos[a]) - lw / 2), oy, lw, ht * s);
    g.fillRect(ox, Math.round(sy(pos[b]) - lw / 2), w * s, lw);
    if (view.big) scaleBar(g, s, W, H, dpr);
  }

  // a round length (Å) about a sixth of the view wide, bottom left
  function scaleBar(g, s, W, H, dpr) {
    const angPerPx = Math.abs(voxelAxes(item.volume).scale[0]) / s;
    const want = (W / 6) * angPerPx;
    const len = [1, 2, 5, 10, 20, 50, 100, 200, 500].find((v) => v >= want) || 1000;
    const px = len / angPerPx;
    const x = 14 * dpr;
    const y = H - 14 * dpr;
    g.fillStyle = 'rgba(0, 0, 0, 0.55)';
    g.fillRect(x - 6 * dpr, y - 22 * dpr, px + 12 * dpr, 30 * dpr);
    g.fillStyle = '#fff';
    g.fillRect(x, y - 3 * dpr, px, 3 * dpr);
    g.font = `${11 * dpr}px system-ui, sans-serif`;
    g.fillText(`${len} Å`, x, y - 8 * dpr);
  }

  // the three small slices share the height of the view (156 px each at most)
  function fitDock() {
    const card = dockViews[0].el;
    const extra = card.offsetHeight - dockViews[0].canvas.offsetHeight;
    if (!card.offsetHeight) return;
    const size = clamp(Math.floor((root.clientHeight - 16) / 3 - extra), 64, 156);
    if (root.style.getPropertyValue('--sl-size') !== `${size}px`) root.style.setProperty('--sl-size', `${size}px`);
  }

  function drawAll() {
    if (!item || root.hidden) return;
    if (!st.split) fitDock();
    const views = st.split ? bigViews.filter((v) => st.all || v.plane === st.plane) : dockViews;
    views.forEach(drawView);
    dockViews.forEach((v) => {
      const f = PLANES[v.plane].fixed;
      v.range.max = String(dims[f] - 1);
      if (document.activeElement !== v.range) v.range.value = String(pos[f]);
      v.where.textContent = ` ${AXIS[f]} ${pos[f] + 1}/${dims[f]}`;
    });
    const f = PLANES[st.plane].fixed;
    bigRange.max = String(dims[f] - 1);
    if (document.activeElement !== bigRange) bigRange.value = String(pos[f]);
    bigRange.disabled = st.all;
    bigWhere.textContent = st.all ? `${pos[0] + 1} · ${pos[1] + 1} · ${pos[2] + 1}` : `${AXIS[f]} ${pos[f] + 1} / ${dims[f]}`;
    bigWhere.title = `x ${pos[0] + 1}, y ${pos[1] + 1}, z ${pos[2] + 1}`;
  }

  function layout() {
    tabs.forEach((t, k) => t.classList.toggle('on', !st.all && st.plane === k));
    allTab.classList.toggle('on', st.all);
    split.classList.toggle('all', st.all);
    bigViews.forEach((v) => { v.cell.hidden = !st.all && v.plane !== st.plane; });
    help.hidden = !st.all;
    requestAnimationFrame(drawAll);
  }

  // ------------------------------------------------------------ position
  function setPos(axis, value) {
    const v = clamp(Math.round(value), 0, dims[axis] - 1);
    if (v === pos[axis]) return;
    pos[axis] = v;
    PLANES.forEach((p, k) => { if (p.fixed === axis) sources[k].dirty = true; });
    drawAll();
  }

  const movePlane = (k, d) => setPos(PLANES[k].fixed, pos[PLANES[k].fixed] + d);

  // voxel of plane `view` under the pointer, or null outside the image
  function voxelAt(view, e) {
    if (!view.t) return null;
    const r = view.canvas.getBoundingClientRect();
    const { s, ox, oy, dpr, w, ht } = view.t;
    const i = Math.floor(((e.clientX - r.left) * dpr - ox) / s);
    const j = ht - 1 - Math.floor(((e.clientY - r.top) * dpr - oy) / s);
    return i >= 0 && j >= 0 && i < w && j < ht ? [i, j] : null;
  }

  function crosshairAt(view, e) {
    const at = voxelAt(view, e);
    if (!at) return;
    const [a, b] = PLANES[view.plane].axes;
    pos[a] = at[0];
    pos[b] = at[1];
    PLANES.forEach((p, k) => { if (p.fixed === a || p.fixed === b) sources[k].dirty = true; });
    drawAll();
  }

  function hover(view, e) {
    const at = voxelAt(view, e);
    if (!at) { ctx.onHover?.(null); readout.textContent = ''; return; }
    const p = PLANES[view.plane];
    const c = [0, 0, 0];
    c[p.axes[0]] = at[0];
    c[p.axes[1]] = at[1];
    c[p.fixed] = pos[p.fixed];
    const { data, space } = item.volume.grid.cells;
    const half = (st.thickness - 1) >> 1;
    let sum = 0;
    let n = 0;
    const q = [...c];
    for (let kk = Math.max(0, c[p.fixed] - half); kk <= Math.min(dims[p.fixed] - 1, c[p.fixed] + half); kk++) {
      q[p.fixed] = kk;
      sum += space.get(data, q[0], q[1], q[2]);
      n += 1;
    }
    const v = sum / Math.max(1, n);
    const A = voxelAxes(item.volume);
    const xyz = c.map((i, axis) => (A.offset[axis] + A.scale[axis] * i).toFixed(1));
    let text = `x ${c[0] + 1}  y ${c[1] + 1}  z ${c[2] + 1}  ·  (${xyz.join(', ')}) Å  ·  ${fmtLevel(v)} (${toSigma(item.stats, v).toFixed(2)} σ)`;
    if (n > 1) text += `, mean of ${n} slices`;
    const sample = item.colorBy?.volume ? colourSampler(view.plane) : null;
    const cv = sample ? sample(at[0], at[1]) : NaN;
    if (cv > 0) text += `  ·  ${item.colorBy.kind === 'variability' ? 'variability' : 'local resolution'} ${cv.toFixed(item.colorBy.unit === 'σ' ? 2 : 1)} ${item.colorBy.unit}`;
    readout.textContent = text;
    ctx.onHover?.(text);
  }

  function zoomAt(view, e, factor) {
    const r = view.canvas.getBoundingClientRect();
    const mx = e.clientX - r.left - r.width / 2;
    const my = e.clientY - r.top - r.height / 2;
    const zoom = clamp(view.zoom * factor, 1, 48);
    const k = zoom / view.zoom;
    view.panX = mx - (mx - view.panX) * k;
    view.panY = my - (my - view.panY) * k;
    view.zoom = zoom;
    if (zoom === 1) { view.panX = 0; view.panY = 0; }
    drawView(view);
  }

  function bindCanvas(view) {
    const cv = view.canvas;
    let drag = null;
    cv.addEventListener('pointerdown', (e) => {
      if (!item || e.button > 1) return;
      cv.setPointerCapture(e.pointerId);
      cv.focus({ preventScroll: true });
      drag = { x: e.clientX, y: e.clientY, panX: view.panX, panY: view.panY, moved: false };
      if (!view.big) crosshairAt(view, e);
    });
    cv.addEventListener('pointermove', (e) => {
      if (!item) return;
      if (drag && cv.hasPointerCapture(e.pointerId)) {
        if (view.big) {
          const dx = e.clientX - drag.x;
          const dy = e.clientY - drag.y;
          if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
          if (drag.moved) { view.panX = drag.panX + dx; view.panY = drag.panY + dy; drawView(view); }
        } else {
          crosshairAt(view, e);
        }
      }
      hover(view, e);
    });
    cv.addEventListener('pointerup', (e) => {
      if (drag && view.big && !drag.moved) crosshairAt(view, e);
      drag = null;
    });
    cv.addEventListener('pointerleave', () => ctx.onHover?.(null));
    cv.addEventListener('wheel', (e) => {
      if (!item) return;
      e.preventDefault();
      const d = e.deltaY || e.deltaX;
      if (!d) return;
      if (view.big && !e.shiftKey) zoomAt(view, e, d < 0 ? 1.2 : 1 / 1.2);
      else movePlane(view.plane, (d < 0 ? 1 : -1) * (!view.big && e.shiftKey ? 5 : 1));
    }, { passive: false });
    cv.addEventListener('dblclick', () => {
      if (view.big) { resetZoom(view); drawView(view); } else enlarge(view.plane);
    });
    cv.addEventListener('keydown', (e) => {
      const step = { ArrowUp: 1, ArrowRight: 1, PageUp: 5, ArrowDown: -1, ArrowLeft: -1, PageDown: -5 }[e.key];
      if (!step || !item) return;
      e.preventDefault();
      e.stopPropagation();
      movePlane(view.plane, step);
    });
  }

  function bindHandle() {
    const apply = (ratio) => {
      st.ratio = clamp(ratio, 0.25, 0.75);
      ctx.stage.style.setProperty('--split', `${(st.ratio * 100).toFixed(1)}%`);
      ctx.onLayout?.();
      drawAll();
    };
    handle.addEventListener('pointerdown', (e) => { handle.setPointerCapture(e.pointerId); e.preventDefault(); });
    handle.addEventListener('pointermove', (e) => {
      if (!handle.hasPointerCapture(e.pointerId)) return;
      const r = ctx.stage.getBoundingClientRect();
      apply((e.clientX - r.left) / r.width);
    });
    handle.addEventListener('keydown', (e) => {
      const d = { ArrowLeft: -0.05, ArrowRight: 0.05 }[e.key];
      if (d) { e.preventDefault(); apply(st.ratio + d); }
    });
  }

  function setSplit(on) {
    st.split = on;
    root.classList.toggle('split', on);
    ctx.stage.classList.toggle('split', on);
    ctx.stage.style.setProperty('--split', `${(st.ratio * 100).toFixed(1)}%`);
    if (on) layout();
    ctx.onLayout?.();
    requestAnimationFrame(drawAll);
  }

  function enlarge(k) {
    if (k !== undefined) { st.plane = k; st.all = false; }
    setSplit(true);
  }

  return {
    show(target) {
      const d = target.volume.grid.cells.space.dimensions;
      if (item !== target) pos = d.map((n) => Math.floor(n / 2));
      else if (d.some((n, k) => n !== dims[k])) pos = pos.map((p, k) => clamp(Math.round(((p + 0.5) * d[k]) / dims[k] - 0.5), 0, d[k] - 1));
      dims = [...d];
      item = target;
      root.hidden = false;
      contourSel.value = st.contour;
      sources.forEach((s) => { s.dirty = true; });
      if (st.split) setSplit(true);
      requestAnimationFrame(drawAll);
    },
    hide() {
      root.hidden = true;
      ctx.onHover?.(null);
      if (st.split) setSplit(false);
    },
    refresh(target) {
      if (!item || (target && target !== item)) return;
      const d = item.volume.grid.cells.space.dimensions;
      if (d.some((n, k) => n !== dims[k])) { this.show(item); return; }
      sources.forEach((s) => { s.dirty = true; });
      drawAll();
    },
    redraw: () => drawAll(),
    enlarge,
    setSplit,
    get split() { return !root.hidden && st.split; },
    get item() { return root.hidden ? null : item; },
  };
}
