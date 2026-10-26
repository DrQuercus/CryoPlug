// Picked particles on the micrographs, as CryoSPARC's Inspect Picks and Manual Picker: a figure-of-merit threshold and a
// range of local power set on their histograms, particles removed or added with a click, zoom (wheel), pan (drag),
// low-pass filter of the display, keyboard navigation (← →). Filaments, as RELION's manual picking of helices: the
// start then the end of each tube are clicked. The work in progress is saved on the server while the job waits; Done
// finishes the job.
import { api } from './api.js';
import { btn, h, s } from './ui.js';

const LOWPASS = [0, 10, 15, 20, 30, 50];
const COLOURS = { kept: '#34c759', added: '#0a84ff', below: 'rgba(255, 255, 255, 0.55)', power: 'rgba(255, 159, 10, 0.9)',
  removed: '#ff453a', tube: 'rgba(52, 199, 89, 0.25)', drawing: '#0a84ff' };
const BINS = 48;

function lowerBound(sorted, t) { // first index with sorted[i] >= t
  let lo = 0, hi = sorted.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (sorted[mid] < t) lo = mid + 1; else hi = mid;
  }
  return lo;
}

const fmt = (n) => Number(n).toLocaleString();
const count = (n, word) => `${fmt(n)} ${word}${n === 1 ? '' : 's'}`;
const base = (name) => name.split('/').pop();
const digits = (v) => { const a = Math.abs(v); return v.toFixed(a >= 100 ? 0 : a >= 10 ? 1 : a >= 1 ? 2 : 3); };
const round1 = (v) => Math.round(v * 10) / 10;

// Distance from (x, y) to the segment of a filament [x1, y1, x2, y2].
function segmentDistance(x, y, [x1, y1, x2, y2]) {
  const dx = x2 - x1, dy = y2 - y1, len2 = dx * dx + dy * dy;
  const t = len2 ? Math.max(0, Math.min(1, ((x - x1) * dx + (y - y1) * dy) / len2)) : 0;
  return Math.hypot(x - (x1 + t * dx), y - (y1 + t * dy));
}

// A histogram with one handle (a threshold: the values at or above it are kept) or two (a range), dragged or moved with
// the arrow keys. `log`: logarithmic axis. `onChange(lo, hi)` as a handle moves; null: no limit on that side.
function histoSlider({ values, range: [min, max], log = false, two = false, lo = null, hi = null, name, onChange }) {
  const t = (v) => (log ? Math.log(v) : v), inv = (u) => (log ? Math.exp(u) : u);
  const u0 = t(min), u1 = t(max), span = (u1 - u0) || 1;
  const counts = new Array(BINS).fill(0);
  for (let i = 0; i < values.length; i += 1) {
    const v = values[i];
    if (Number.isFinite(v) && !(log && v <= 0)) counts[Math.max(0, Math.min(BINS - 1, Math.floor(((t(v) - u0) / span) * BINS)))] += 1;
  }
  const W = 280, H = two ? 64 : 90, T = 14, top = Math.max(...counts) || 1;
  const svg = s('svg', { viewBox: `0 -${T} ${W} ${H + 16 + T}`, class: 'pk-hist', role: 'group', 'aria-label': name });
  const bw = W / BINS;
  const bars = counts.map((c, i) => s('rect', { x: i * bw + 0.5, y: H - (c / top) * H, width: Math.max(1, bw - 1), height: (c / top) * H }));
  bars.forEach((b) => svg.appendChild(b));
  svg.append(s('text', { x: 0, y: H + 13, class: 'pk-axis' }, digits(min)),
    s('text', { x: W, y: H + 13, 'text-anchor': 'end', class: 'pk-axis' }, digits(max)),
    log ? s('text', { x: W / 2, y: H + 13, 'text-anchor': 'middle', class: 'pk-axis' }, 'log scale') : null);
  const xOf = (v) => ((Math.max(u0, Math.min(u1, t(v))) - u0) / span) * W;
  const vals = { lo, hi };
  const sides = two ? ['lo', 'hi'] : ['lo'];
  const at = (side) => vals[side] ?? (side === 'lo' ? min : max);
  const parts = Object.fromEntries(sides.map((side) => {
    const line = s('line', { y1: -3, y2: H, class: 'pk-thr' });
    const label = s('text', { y: -5, class: 'pk-thr-label' });
    const grip = s('rect', { y: H / 2 - 9, width: 8, height: 18, rx: 2, class: 'pk-grip', tabindex: 0, role: 'slider',
      'aria-label': two ? `${name}: ${side === 'lo' ? 'lower' : 'upper'} limit` : name, 'aria-valuemin': min, 'aria-valuemax': max });
    grip.addEventListener('keydown', (ev) => {
      if (ev.key !== 'ArrowLeft' && ev.key !== 'ArrowRight') return;
      set(side, inv(t(at(side)) + (ev.key === 'ArrowRight' ? 1 : -1) * span / 100));
      ev.preventDefault();
    });
    svg.append(line, label, grip);
    return [side, { line, label, grip }];
  }));
  function place() {
    for (const side of sides) {
      const { line, label, grip } = parts[side];
      const x = xOf(at(side));
      line.setAttribute('x1', x); line.setAttribute('x2', x);
      grip.setAttribute('x', x - 4);
      grip.setAttribute('aria-valuenow', at(side));
      if (!two) {
        label.setAttribute('x', Math.min(W - 30, Math.max(30, x)));
        label.setAttribute('text-anchor', 'middle');
        label.textContent = `≥ ${at(side).toFixed(3)}`;
      } else { // the lower limit written left of its handle, the upper one right of it
        const left = side === 'lo' ? x > 44 : x > W - 44;
        label.setAttribute('x', left ? x - 5 : x + 5);
        label.setAttribute('text-anchor', left ? 'end' : 'start');
        label.textContent = vals[side] == null ? '' : `${side === 'lo' ? '≥' : '≤'} ${digits(vals[side])}`;
      }
    }
    bars.forEach((b, i) => {
      const b0 = u0 + (i / BINS) * span, b1 = u0 + ((i + 1) / BINS) * span;
      b.classList.toggle('off', (vals.lo != null && b1 <= t(vals.lo)) || (vals.hi != null && b0 >= t(vals.hi)));
    });
  }
  function set(side, v) {
    v = Math.max(min, Math.min(max, v));
    if (two) {
      if (side === 'lo' && vals.hi != null) v = Math.min(v, vals.hi);
      if (side === 'hi' && vals.lo != null) v = Math.max(v, vals.lo);
      const edge = span / BINS / 2; // a handle taken to the end of the axis: no limit on that side
      vals[side] = (side === 'lo' ? t(v) - u0 <= edge : u1 - t(v) <= edge) ? null : v;
    } else vals[side] = v;
    place();
    onChange(vals.lo, vals.hi);
  }
  let active = null;
  const xAt = (ev) => { const rect = svg.getBoundingClientRect(); return ((ev.clientX - rect.left) / rect.width) * W; };
  const move = (ev) => set(active, inv(u0 + Math.max(0, Math.min(1, xAt(ev) / W)) * span));
  svg.addEventListener('pointerdown', (ev) => {
    const x = xAt(ev);
    active = !two || Math.abs(x - xOf(at('lo'))) <= Math.abs(x - xOf(at('hi'))) ? 'lo' : 'hi';
    svg.setPointerCapture(ev.pointerId);
    parts[active].grip.focus({ preventScroll: true });
    move(ev);
  });
  svg.addEventListener('pointermove', (ev) => { if (active) move(ev); });
  svg.addEventListener('pointerup', () => { active = null; });
  place();
  return svg;
}

// Figure of merit (x) against local power (y) of every pick, as CryoSPARC's joint view: the box kept is outlined.
function jointView(picks, n, S, [fmin, fmax], [pmin, pmax], log) {
  const W = 280, H = 150, NX = 70, NY = 40;
  const canvas = h('canvas', { class: 'pk-joint', width: W * 2, height: H * 2, role: 'img',
    'aria-label': 'Figure of merit against power score of the picks' });
  const ty = (v) => (log ? Math.log(Math.max(v, pmin)) : v);
  const y0 = ty(pmin), yspan = (ty(pmax) - y0) || 1, xspan = (fmax - fmin) || 1;
  const grid = new Float32Array(NX * NY);
  for (let i = 0; i < n; i += 1) {
    const f = picks[i * S + 2], p = picks[i * S + 3];
    if (!Number.isFinite(p)) continue;
    const bx = Math.max(0, Math.min(NX - 1, Math.floor(((f - fmin) / xspan) * NX)));
    const by = Math.max(0, Math.min(NY - 1, Math.floor(((ty(p) - y0) / yspan) * NY)));
    grid[by * NX + bx] += 1;
  }
  const top = Math.log1p(Math.max(...grid)) || 1;
  const g = canvas.getContext('2d');
  return {
    el: canvas,
    draw(threshold, plo, phi) {
      const css = getComputedStyle(canvas);
      const ink = css.getPropertyValue('--ink-1').trim() || '#222';
      g.clearRect(0, 0, canvas.width, canvas.height);
      g.fillStyle = css.getPropertyValue('--series-1').trim() || '#3a7bd5';
      const cw = canvas.width / NX, ch = canvas.height / NY;
      for (let by = 0; by < NY; by += 1) {
        for (let bx = 0; bx < NX; bx += 1) {
          const c = grid[by * NX + bx];
          if (!c) continue;
          g.globalAlpha = 0.15 + 0.85 * (Math.log1p(c) / top);
          g.fillRect(bx * cw, canvas.height - (by + 1) * ch, Math.ceil(cw), Math.ceil(ch));
        }
      }
      g.globalAlpha = 1;
      const X = (f) => ((Math.max(fmin, Math.min(fmax, f)) - fmin) / xspan) * canvas.width;
      const Y = (p) => canvas.height - ((ty(Math.max(pmin, Math.min(pmax, p))) - y0) / yspan) * canvas.height;
      const x1 = Number.isFinite(threshold) ? X(threshold) : 0;
      const ya = Y(phi ?? pmax), yb = Y(plo ?? pmin);
      g.strokeStyle = ink;
      g.lineWidth = 3;
      g.setLineDash([10, 6]);
      g.strokeRect(x1, ya, canvas.width - x1, yb - ya);
      g.setLineDash([]);
    },
  };
}

// `finish(payload)`: Done of a waiting job; `newInspection(threshold)`: an inspection job from these picks.
export function pickInspector(sec, { puid, job, finish = null, widen = null, newInspection = null }) {
  const root = h('div', { class: 'pk', tabindex: 0, role: 'application', 'aria-label': sec.title });
  const editable = !!(sec.editable && sec.waiting && job.status === 'waiting' && finish);
  root.append(h('div', { class: 'muted small' }, 'Loading the picks…'));
  load().catch((err) => root.replaceChildren(h('div', { class: 'alert error' }, `Picks not available: ${err.message || err}`)));
  return root;

  async function load() {
    const [info, saved] = await Promise.all([
      fetch(api.fileUrl(puid, sec.data)).then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); }),
      editable ? api.session(puid, job.uid, 'picks').catch(() => ({})) : Promise.resolve({}),
    ]);
    const buf = info.n_picks ? await fetch(api.fileUrl(puid, info.binary)).then((r) => r.arrayBuffer()) : new ArrayBuffer(0);
    build(info, new Float32Array(buf), saved || {});
    if (editable && widen) widen();
    if (editable) requestAnimationFrame(() => requestAnimationFrame(bringIntoView));
  }

  // A waiting inspection fills the panel: scrolled to just below the job's header (once, when it opens).
  function bringIntoView() {
    const panel = root.closest('.panel');
    if (!panel || !root.isConnected) return;
    const head = panel.querySelector('.panel-head');
    const top = head ? head.getBoundingClientRect().bottom : panel.getBoundingClientRect().top;
    panel.scrollTop += root.getBoundingClientRect().top - top - 8;
  }

  function build(info, picks, saved) {
    const mics = info.micrographs;
    const S = info.stride || 3; // values per pick: x, y, figure of merit (, local power)
    const filament = sec.mode === 'filaments' || info.pick_type === 'startend';
    const hasFom = !!info.has_fom && !filament;
    const hasPower = !!info.has_power && !!info.power_range && !filament;
    const sorted = mics.map((m) => Float32Array.from({ length: m.count }, (_, i) => picks[(m.offset + i) * S + 2]).sort());
    const edits = new Map(Object.entries(saved.edits || {}).map(([k, e]) => [k, { removed: new Set(e.removed || []), added: (e.added || []).slice() }]));
    const fils = new Map(Object.entries(saved.filaments || {}).map(([k, v]) => [k, v.filter((f) => f.length === 4)]));
    const [fmin, fmax] = info.fom_range || [0, 1];
    const [pmin, pmax] = info.power_range || [0, 1];
    let threshold = saved.threshold ?? (sec.threshold > -900 ? sec.threshold : fmin);
    if (!hasFom) threshold = -Infinity;
    let [plo, phi] = hasPower && Array.isArray(saved.power_range) ? saved.power_range : [null, null];
    let cur = 0;
    let lowpass = sec.lowpass ?? 20;
    let sizeFactor = 1; // circle size relative to the particle diameter
    let showHidden = false;
    let dirty = false, saveTimer = null;
    let pending = null, hover = null; // filament being drawn: its start, and the pointer

    const edit = (name) => {
      if (!edits.has(name)) edits.set(name, { removed: new Set(), added: [] });
      return edits.get(name);
    };
    const inPower = (o) => !hasPower || !(picks[o + 3] < plo || picks[o + 3] > phi); // an unknown power passes
    const powerActive = () => hasPower && (plo !== null || phi !== null);
    const shown = (o) => !(hasFom && picks[o + 2] < threshold) && inPower(o);
    // filaments of a micrograph: drawn in this job, or read from the picks (start and end, one after the other)
    const filamentsOf = (m) => {
      if (editable) return fils.get(m.name) || [];
      const out = [];
      for (let i = 0; i + 1 < m.count; i += 2) {
        const o = (m.offset + i) * S, q = (m.offset + i + 1) * S;
        out.push([picks[o], picks[o + 1], picks[q], picks[q + 1]]);
      }
      return out;
    };
    const countOf = (k) => {
      const m = mics[k];
      if (filament) return filamentsOf(m).length;
      const e = edits.get(m.name);
      let n = 0;
      if (powerActive()) { for (let i = 0; i < m.count; i += 1) if (shown((m.offset + i) * S)) n += 1; } else {
        n = hasFom ? sorted[k].length - lowerBound(sorted[k], threshold) : m.count;
      }
      if (e) {
        for (const i of e.removed) if (shown((m.offset + i) * S)) n -= 1;
        n += e.added.length;
      }
      return n;
    };

    // ---------------------------------------------------------------- viewer
    const canvas = h('canvas', { class: 'pk-canvas', 'aria-label': filament ? 'Micrograph with its filaments' : 'Micrograph with its picks' });
    const stage = h('div', { class: 'pk-stage' }, canvas);
    const g = canvas.getContext('2d');
    let img = null, imgUrl = '', factor = 1;
    const view = { zoom: 1, cx: 0, cy: 0 };
    const title = h('span', { class: 'pk-name mono' });
    const status = h('span', { class: 'pk-status muted small' });
    const lp = h('select', { 'aria-label': 'Low-pass filter of the display', onchange: (e) => { lowpass = +e.target.value; showMic(cur, true); } },
      LOWPASS.map((v) => h('option', { value: v, selected: v === lowpass ? 'selected' : null }, v ? `${v} Å` : 'none')));
    const sizeInput = h('input', { type: 'range', min: 0.4, max: 2, step: 0.05, value: 1,
      'aria-label': filament ? 'Width of the tubes' : 'Size of the circles', oninput: (e) => { sizeFactor = +e.target.value; draw(); } });
    const hiddenBox = h('input', { type: 'checkbox', onchange: (e) => { showHidden = e.target.checked; draw(); } });
    const toolbar = h('div', { class: 'pk-tools' },
      btn('', () => showMic(cur - 1), { cls: 'small', ic: 'back', title: 'Previous micrograph (←)' }),
      btn('', () => showMic(cur + 1), { cls: 'small', ic: 'next', title: 'Next micrograph (→)' }),
      title, h('span', { class: 'grow' }),
      h('label', { class: 'muted small', title: 'Low-pass filter of the display' }, 'Filter', lp),
      h('label', { class: 'muted small', title: filament ? 'Width of the tubes' : 'Size of the circles' }, filament ? 'Tubes' : 'Circles', sizeInput),
      filament ? null : h('label', { class: 'muted small',
        title: 'Also show the picks left out by the threshold or the power range (dashed) and those removed (red)' }, hiddenBox, 'Hidden'),
      btn('', () => { fit(); draw(); }, { cls: 'small', ic: 'expand', title: 'Whole micrograph (F)' }));

    const imgSize = () => (root.closest('.panel.wide') ? 1536 : 1024);
    function showMic(k, reload = false) {
      if (!mics.length) return;
      if (k !== cur) pending = null;
      cur = (k + mics.length) % mics.length;
      const m = mics[cur];
      const size = imgSize();
      factor = Math.max(1, Math.ceil(Math.max(m.shape[0], m.shape[1]) / Math.max(64, size)));
      const url = api.micrographUrl(puid, m.name, size, lowpass, info.apix || 0);
      title.textContent = `${cur + 1}/${mics.length} · ${base(m.name)}`;
      title.title = m.name;
      highlightRow();
      if (url === imgUrl && !reload) { draw(); return; }
      const keepView = img && imgUrl.split('&lowpass')[0] === url.split('&lowpass')[0]; // same micrograph, new filter
      imgUrl = url;
      status.textContent = 'loading…';
      const im = new Image();
      im.onload = () => {
        if (imgUrl !== url) return;
        img = im;
        status.textContent = '';
        if (!keepView) fit();
        resize();
      };
      im.onerror = () => { if (imgUrl === url) status.textContent = 'micrograph not readable'; };
      im.src = url;
    }
    function fit() {
      if (!img) return;
      view.zoom = 1;
      view.cx = img.naturalWidth / 2;
      view.cy = img.naturalHeight / 2;
    }
    function scale() { // device pixels per preview pixel
      if (!img) return 1;
      return Math.min(canvas.width / img.naturalWidth, canvas.height / img.naturalHeight) * view.zoom;
    }
    let stageWidth = 0;
    function resize() { // the canvas takes the shape of the micrograph, as large as the visible part of the panel allows
      const dpr = window.devicePixelRatio || 1;
      const avail = stage.clientWidth || 600;
      const aspect = img ? img.naturalHeight / img.naturalWidth : 0.7;
      const panel = root.closest('.panel');
      const head = panel?.querySelector('.panel-head');
      const room = (panel ? panel.clientHeight - (head?.offsetHeight || 0) : window.innerHeight - 60) - 120; // title, tools, legend
      const hgt = Math.max(240, Math.min(Math.round(avail * aspect), room));
      const w = Math.min(avail, Math.round(hgt / aspect));
      canvas.style.width = `${w}px`;
      canvas.style.height = `${hgt}px`;
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(hgt * dpr);
      draw();
    }
    function draw() {
      const dpr = window.devicePixelRatio || 1;
      g.setTransform(1, 0, 0, 1, 0, 0);
      g.fillStyle = '#111';
      g.fillRect(0, 0, canvas.width, canvas.height);
      if (!img) return;
      const sc = scale();
      const tx = canvas.width / 2 - view.cx * sc, ty = canvas.height / 2 - view.cy * sc;
      g.imageSmoothingEnabled = view.zoom < 2.5;
      g.setTransform(sc, 0, 0, sc, tx, ty);
      g.drawImage(img, 0, 0);
      g.setTransform(1, 0, 0, 1, 0, 0);
      const m = mics[cur];
      const r = Math.max(2, ((sec.diameter_px || 50) / 2 / factor) * sc * sizeFactor);
      const X = (x) => (x / factor) * sc + tx, Y = (y) => (y / factor) * sc + ty;
      g.lineWidth = 1.6 * dpr;
      if (filament) { drawFilaments(m, r, X, Y, dpr); return; }
      const e = edits.get(m.name);
      const circle = (x, y, colour, dashed = false) => {
        const cx = X(x), cy = Y(y);
        if (cx < -r || cy < -r || cx > canvas.width + r || cy > canvas.height + r) return;
        g.strokeStyle = colour;
        g.setLineDash(dashed ? [4 * dpr, 3 * dpr] : []);
        g.beginPath();
        g.arc(cx, cy, r, 0, Math.PI * 2);
        g.stroke();
      };
      for (let i = 0; i < m.count; i += 1) {
        const o = (m.offset + i) * S;
        if (e && e.removed.has(i)) { if (showHidden) circle(picks[o], picks[o + 1], COLOURS.removed, true); } else if (hasFom && picks[o + 2] < threshold) {
          if (showHidden) circle(picks[o], picks[o + 1], COLOURS.below, true);
        } else if (!inPower(o)) { if (showHidden) circle(picks[o], picks[o + 1], COLOURS.power, true); } else circle(picks[o], picks[o + 1], COLOURS.kept);
      }
      for (const [x, y] of (e ? e.added : [])) circle(x, y, COLOURS.added);
      g.setLineDash([]);
    }
    // a filament: a translucent tube of the given diameter, its axis, a filled dot at its start and a ring at its end
    function drawFilaments(m, r, X, Y, dpr) {
      g.lineCap = 'round';
      for (const [x1, y1, x2, y2] of filamentsOf(m)) {
        g.strokeStyle = COLOURS.tube;
        g.lineWidth = 2 * r;
        g.beginPath(); g.moveTo(X(x1), Y(y1)); g.lineTo(X(x2), Y(y2)); g.stroke();
        g.strokeStyle = COLOURS.kept;
        g.lineWidth = 1.6 * dpr;
        g.beginPath(); g.moveTo(X(x1), Y(y1)); g.lineTo(X(x2), Y(y2)); g.stroke();
        g.fillStyle = COLOURS.kept;
        g.beginPath(); g.arc(X(x1), Y(y1), 3.5 * dpr, 0, Math.PI * 2); g.fill();
        g.beginPath(); g.arc(X(x2), Y(y2), 3.5 * dpr, 0, Math.PI * 2); g.stroke();
      }
      if (pending) {
        g.fillStyle = COLOURS.drawing;
        g.beginPath(); g.arc(X(pending[0]), Y(pending[1]), 4 * dpr, 0, Math.PI * 2); g.fill();
        if (hover) {
          g.strokeStyle = COLOURS.drawing;
          g.setLineDash([6 * dpr, 4 * dpr]);
          g.beginPath(); g.moveTo(X(pending[0]), Y(pending[1])); g.lineTo(X(hover[0]), Y(hover[1])); g.stroke();
          g.setLineDash([]);
        }
      }
      g.lineCap = 'butt';
    }
    // micrograph pixel under the pointer
    const toMic = (ev) => {
      const rect = canvas.getBoundingClientRect();
      const dpr = canvas.width / rect.width;
      const sc = scale();
      const px = view.cx + ((ev.clientX - rect.left) * dpr - canvas.width / 2) / sc;
      const py = view.cy + ((ev.clientY - rect.top) * dpr - canvas.height / 2) / sc;
      return [px * factor, py * factor];
    };
    function clickAt(ev) {
      if (!editable || !img) return;
      const [x, y] = toMic(ev);
      const m = mics[cur];
      const [hgt, wid] = m.shape;
      const reach = Math.max(4, ((sec.diameter_px || 50) / 2) * sizeFactor);
      if (filament) { // start, then end; a click on a filament removes it
        const list = fils.get(m.name) || [];
        if (!pending) {
          let best = -1, bestD = reach;
          list.forEach((f, j) => { const d = segmentDistance(x, y, f); if (d < bestD) { bestD = d; best = j; } });
          if (best >= 0) { list.splice(best, 1); fils.set(m.name, list); changed(); return; }
          if (x < 0 || y < 0 || x >= wid || y >= hgt) return;
          pending = [x, y];
          hover = null;
          draw();
          return;
        }
        if (Math.hypot(x - pending[0], y - pending[1]) < reach / 2) { pending = null; draw(); return; } // the start again: cancelled
        const ex = Math.max(0, Math.min(wid - 1, x)), ey = Math.max(0, Math.min(hgt - 1, y));
        list.push([round1(pending[0]), round1(pending[1]), round1(ex), round1(ey)]);
        fils.set(m.name, list);
        pending = null;
        changed();
        return;
      }
      const e = edit(m.name);
      let best = null, bestD = reach;
      e.added.forEach(([ax, ay], j) => { const d = Math.hypot(ax - x, ay - y); if (d < bestD) { bestD = d; best = ['added', j]; } });
      for (let i = 0; i < m.count; i += 1) {
        const o = (m.offset + i) * S;
        const visible = !(hasFom && picks[o + 2] < threshold) && inPower(o) && (!e.removed.has(i) || showHidden);
        if (!visible) continue;
        const d = Math.hypot(picks[o] - x, picks[o + 1] - y);
        if (d < bestD) { bestD = d; best = ['pick', i]; }
      }
      if (best && best[0] === 'added') e.added.splice(best[1], 1);
      else if (best) { if (e.removed.has(best[1])) e.removed.delete(best[1]); else e.removed.add(best[1]); } else {
        if (x < 0 || y < 0 || x >= wid || y >= hgt) return;
        e.added.push([round1(x), round1(y)]);
      }
      changed();
    }
    let drag = null;
    canvas.addEventListener('pointerdown', (ev) => {
      canvas.setPointerCapture(ev.pointerId);
      drag = { x: ev.clientX, y: ev.clientY, cx: view.cx, cy: view.cy, moved: false };
    });
    canvas.addEventListener('pointermove', (ev) => {
      if (!drag) { // the filament being drawn follows the pointer
        if (pending) { hover = toMic(ev); draw(); }
        return;
      }
      const dx = ev.clientX - drag.x, dy = ev.clientY - drag.y;
      if (!drag.moved && Math.hypot(dx, dy) > 4) drag.moved = true;
      if (drag.moved && img) {
        const rect = canvas.getBoundingClientRect();
        const cssScale = scale() * rect.width / canvas.width;
        view.cx = drag.cx - dx / cssScale;
        view.cy = drag.cy - dy / cssScale;
        draw();
      }
    });
    canvas.addEventListener('pointerup', (ev) => { if (drag && !drag.moved) clickAt(ev); drag = null; });
    canvas.addEventListener('pointercancel', () => { drag = null; });
    canvas.addEventListener('wheel', (ev) => {
      if (!img) return;
      ev.preventDefault();
      const rect = canvas.getBoundingClientRect();
      const dpr = canvas.width / rect.width;
      const before = scale();
      const px = view.cx + ((ev.clientX - rect.left) * dpr - canvas.width / 2) / before;
      const py = view.cy + ((ev.clientY - rect.top) * dpr - canvas.height / 2) / before;
      view.zoom = Math.min(16, Math.max(1, view.zoom * Math.exp(-ev.deltaY / 400)));
      const after = scale();
      view.cx = px - ((ev.clientX - rect.left) * dpr - canvas.width / 2) / after;
      view.cy = py - ((ev.clientY - rect.top) * dpr - canvas.height / 2) / after;
      draw();
    }, { passive: false });
    new ResizeObserver(() => { // width changes only (the canvas sets the height): no resize loop
      if (stage.clientWidth !== stageWidth) { stageWidth = stage.clientWidth; requestAnimationFrame(resize); }
    }).observe(stage);
    root.addEventListener('keydown', (ev) => {
      if (ev.target.closest('input, select, textarea, .pk-hist')) return;
      if (ev.key === 'ArrowRight') { showMic(cur + 1); ev.preventDefault(); }
      if (ev.key === 'ArrowLeft') { showMic(cur - 1); ev.preventDefault(); }
      if (ev.key === 'Escape' && pending) { pending = null; draw(); ev.preventDefault(); }
      if (ev.key === 'f' || ev.key === 'F') { fit(); draw(); }
      if (ev.key === '+' || ev.key === '=') { view.zoom = Math.min(16, view.zoom * 1.25); draw(); }
      if (ev.key === '-') { view.zoom = Math.max(1, view.zoom / 1.25); draw(); }
    });

    // ---------------------------------------- figure-of-merit and power histograms
    const total = h('div', { class: 'pk-total' });
    let joint = null;
    const plog = pmin > 0 && pmax / pmin > 20;
    const histos = [];
    if (hasFom && info.n_picks) {
      const values = Float32Array.from({ length: info.n_picks }, (_, i) => picks[i * S + 2]);
      histos.push(h('div', { class: 'pk-histo' }, h('div', { class: 'muted small' }, 'Figure of merit — drag to set the threshold'),
        histoSlider({ values, range: [fmin, fmax], lo: threshold, name: 'Figure-of-merit threshold',
          onChange: (lo) => { threshold = lo; changed(false); } })));
    }
    if (hasPower && info.n_picks) {
      const values = Float32Array.from({ length: info.n_picks }, (_, i) => picks[i * S + 3]);
      histos.push(h('div', { class: 'pk-histo' },
        h('div', { class: 'muted small', title: 'Local power around each pick: ice crystals, carbon and contamination are high, empty ice is low' },
          'Power score — drag the two handles to keep a range'),
        histoSlider({ values, range: [pmin, pmax], log: plog, two: true, lo: plo, hi: phi, name: 'Power score range',
          onChange: (lo, hi) => { plo = lo; phi = hi; changed(false); } })));
      if (hasFom) {
        joint = jointView(picks, info.n_picks, S, [fmin, fmax], [pmin, pmax], plog);
        histos.push(h('div', { class: 'pk-histo' }, h('div', { class: 'muted small' }, 'Figure of merit (→) against power score (↑): the box is kept'), joint.el));
      }
    }

    // ------------------------------------------------------ micrograph list
    const list = h('div', { class: 'pk-list', role: 'listbox', 'aria-label': 'Micrographs' });
    const rows = mics.map((m, k) => h('button', { type: 'button', class: 'pk-row', role: 'option', onclick: () => { showMic(k); root.focus(); } },
      h('span', { class: 'muted' }, k + 1), h('span', { class: 'pk-row-name', title: m.name }, base(m.name)), h('b', {})));
    list.append(...rows);
    function highlightRow() {
      rows.forEach((r, k) => { r.classList.toggle('on', k === cur); r.setAttribute('aria-selected', k === cur ? 'true' : 'false'); });
      const row = rows[cur];
      if (row) { // scroll the list only (not the panel around it)
        if (row.offsetTop < list.scrollTop) list.scrollTop = row.offsetTop;
        else if (row.offsetTop + row.offsetHeight > list.scrollTop + list.clientHeight) list.scrollTop = row.offsetTop + row.offsetHeight - list.clientHeight;
      }
    }
    const isEdited = (m) => (filament ? editable && (fils.get(m.name) || []).length > 0
      : !!(edits.get(m.name) && (edits.get(m.name).removed.size || edits.get(m.name).added.length)));
    let raf = 0;
    function refreshCounts() {
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        let n = 0;
        mics.forEach((m, k) => {
          const c = countOf(k);
          n += c;
          rows[k].lastChild.textContent = fmt(c);
          rows[k].classList.toggle('edited', isEdited(m));
        });
        const all = info.n_picks || 0;
        const on = ` on ${count(mics.length, 'micrograph')}`;
        if (filament) total.replaceChildren(h('b', {}, fmt(n)), ` filament${n === 1 ? '' : 's'}${on}`);
        else {
          total.replaceChildren(h('b', {}, fmt(n)), ` particle${n === 1 ? '' : 's'} kept${on}`,
            all ? h('span', { class: 'muted' }, ` (${fmt(all)} picked)`) : null);
        }
        if (joint) joint.draw(threshold, plo, phi);
        if (doneBtn) doneBtn.disabled = !n;
      });
    }

    // ------------------------------------------------------------- saving
    const saveState = h('span', { class: 'muted small' });
    const payload = () => (filament ? { filaments: Object.fromEntries([...fils].filter(([, v]) => v.length)) } : {
      threshold: hasFom ? threshold : null,
      power_range: hasPower ? [plo, phi] : null,
      edits: Object.fromEntries([...edits].filter(([, e]) => e.removed.size || e.added.length)
        .map(([k, e]) => [k, { removed: [...e.removed].sort((a, b) => a - b), added: e.added }])),
    });
    function changed(redraw = true) {
      if (redraw) draw(); else requestAnimationFrame(draw);
      refreshCounts();
      if (!editable) return;
      dirty = true;
      saveState.textContent = 'not saved yet';
      clearTimeout(saveTimer);
      saveTimer = setTimeout(save, 1500);
    }
    async function save() {
      if (!dirty) return;
      dirty = false;
      try {
        await api.saveSession(puid, job.uid, 'picks', payload());
        saveState.textContent = 'saved';
      } catch (err) {
        dirty = true;
        saveState.textContent = `not saved: ${err.message || err}`;
      }
    }
    const doneBtn = editable ? btn('Done', async () => { clearTimeout(saveTimer); await finish(payload()); }, { cls: 'primary', ic: 'check',
      title: filament ? 'Write the filaments (start and end of each, for the helical extraction)'
        : 'Write the picks kept (coordinate files for the extraction)' }) : null;
    const editBtn = !editable && newInspection && !filament ? btn('Inspect and edit…', () => newInspection(hasFom ? threshold : null),
      { cls: 'primary', ic: 'edit', title: 'An inspection job with these picks and this threshold (removes and adds particles)' }) : null;
    const legend = filament
      ? h('div', { class: 'pk-legend muted small' },
        h('span', {}, h('i', { class: 'tube', style: { borderColor: COLOURS.kept } }), 'filament (● start, ○ end)'),
        editable ? h('span', {}, 'click: start, click: end · click on a filament: remove · Esc: cancel · wheel: zoom · drag: move · ← →')
          : h('span', {}, 'wheel: zoom · drag: move · ← →'))
      : h('div', { class: 'pk-legend muted small' },
        h('span', {}, h('i', { style: { borderColor: COLOURS.kept } }), 'kept'),
        editable ? h('span', {}, h('i', { style: { borderColor: COLOURS.added } }), 'added by hand') : null,
        h('span', {}, h('i', { class: 'dashed', style: { borderColor: '#999' } }), 'below the threshold (Hidden)'),
        hasPower ? h('span', {}, h('i', { class: 'dashed', style: { borderColor: COLOURS.power } }), 'outside the power range (Hidden)') : null,
        editable ? h('span', {}, 'click: remove / add · wheel: zoom · drag: move · ← →: micrographs') : h('span', {}, 'wheel: zoom · drag: move · ← →'));

    const side = h('div', { class: 'pk-side' }, ...histos, total, list);
    root.replaceChildren(
      h('div', { class: 'pk-head' }, h('h4', {}, sec.title), h('span', { class: 'grow' }), saveState, editBtn, doneBtn),
      h('div', { class: 'pk-body' }, h('div', { class: 'pk-main' }, toolbar, stage, legend, status), side));
    refreshCounts();
    showMic(0);
  }
}
