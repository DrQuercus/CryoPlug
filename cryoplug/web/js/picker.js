// Picked particles on the micrographs, as CryoSPARC's Inspect Picks and Manual Picker: a figure-of-merit threshold set
// on its histogram, particles removed or added with a click, zoom (wheel), pan (drag), low-pass filter of the display,
// keyboard navigation (← →). The work in progress is saved on the server while the job waits; Done finishes the job.
import { api } from './api.js';
import { btn, h, icon, s } from './ui.js';

const LOWPASS = [0, 10, 15, 20, 30, 50];
const COLOURS = { kept: '#34c759', added: '#0a84ff', below: 'rgba(255, 255, 255, 0.55)', removed: '#ff453a' };
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
const base = (name) => name.split('/').pop();

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
    const hasFom = !!info.has_fom;
    const sorted = mics.map((m) => Float32Array.from({ length: m.count }, (_, i) => picks[(m.offset + i) * 3 + 2]).sort());
    const edits = new Map(Object.entries(saved.edits || {}).map(([k, e]) => [k, { removed: new Set(e.removed || []), added: (e.added || []).slice() }]));
    const [fmin, fmax] = info.fom_range || [0, 1];
    let threshold = saved.threshold ?? (sec.threshold > -900 ? sec.threshold : fmin);
    if (!hasFom) threshold = -Infinity;
    let cur = 0;
    let lowpass = sec.lowpass ?? 20;
    let sizeFactor = 1; // circle size relative to the particle diameter
    let showHidden = false;
    let dirty = false, saveTimer = null;

    const edit = (name) => {
      if (!edits.has(name)) edits.set(name, { removed: new Set(), added: [] });
      return edits.get(name);
    };
    const countOf = (k) => {
      const m = mics[k];
      const e = edits.get(m.name);
      let n = hasFom ? sorted[k].length - lowerBound(sorted[k], threshold) : m.count;
      if (e) {
        for (const i of e.removed) if (!hasFom || picks[(m.offset + i) * 3 + 2] >= threshold) n -= 1;
        n += e.added.length;
      }
      return n;
    };

    // ---------------------------------------------------------------- viewer
    const canvas = h('canvas', { class: 'pk-canvas', 'aria-label': 'Micrograph with its picks' });
    const stage = h('div', { class: 'pk-stage' }, canvas);
    const g = canvas.getContext('2d');
    let img = null, imgUrl = '', factor = 1, loading = false;
    const view = { zoom: 1, cx: 0, cy: 0 };
    const title = h('span', { class: 'pk-name mono' });
    const status = h('span', { class: 'pk-status muted small' });
    const lp = h('select', { 'aria-label': 'Low-pass filter of the display', onchange: (e) => { lowpass = +e.target.value; showMic(cur, true); } },
      LOWPASS.map((v) => h('option', { value: v, selected: v === lowpass ? 'selected' : null }, v ? `${v} Å` : 'none')));
    const sizeInput = h('input', { type: 'range', min: 0.4, max: 2, step: 0.05, value: 1, 'aria-label': 'Size of the circles',
      oninput: (e) => { sizeFactor = +e.target.value; draw(); } });
    const hiddenBox = h('input', { type: 'checkbox', onchange: (e) => { showHidden = e.target.checked; draw(); } });
    const toolbar = h('div', { class: 'pk-tools' },
      btn('', () => showMic(cur - 1), { cls: 'small', ic: 'back', title: 'Previous micrograph (←)' }),
      btn('', () => showMic(cur + 1), { cls: 'small', ic: 'next', title: 'Next micrograph (→)' }),
      title, h('span', { class: 'grow' }),
      h('label', { class: 'muted small', title: 'Low-pass filter of the display' }, 'Filter', lp),
      h('label', { class: 'muted small', title: 'Size of the circles' }, 'Circles', sizeInput),
      h('label', { class: 'muted small', title: 'Also show the picks below the threshold (dashed) and those removed (red)' },
        hiddenBox, 'Hidden'),
      btn('', () => { fit(); draw(); }, { cls: 'small', ic: 'expand', title: 'Whole micrograph (F)' }));

    const imgSize = () => (root.closest('.panel.wide') ? 1536 : 1024);
    function showMic(k, reload = false) {
      if (!mics.length) return;
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
      loading = true;
      status.textContent = 'loading…';
      const im = new Image();
      im.onload = () => {
        if (imgUrl !== url) return;
        img = im;
        loading = false;
        status.textContent = '';
        if (!keepView) fit();
        resize();
      };
      im.onerror = () => { if (imgUrl === url) { loading = false; status.textContent = 'micrograph not readable'; } };
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
      const e = edits.get(m.name);
      const r = Math.max(2, ((sec.diameter_px || 50) / 2 / factor) * sc * sizeFactor);
      g.lineWidth = 1.6 * dpr;
      const circle = (x, y, colour, dashed = false) => {
        const X = (x / factor) * sc + tx, Y = (y / factor) * sc + ty;
        if (X < -r || Y < -r || X > canvas.width + r || Y > canvas.height + r) return;
        g.strokeStyle = colour;
        g.setLineDash(dashed ? [4 * dpr, 3 * dpr] : []);
        g.beginPath();
        g.arc(X, Y, r, 0, Math.PI * 2);
        g.stroke();
      };
      for (let i = 0; i < m.count; i += 1) {
        const o = (m.offset + i) * 3;
        const removed = e && e.removed.has(i);
        const below = hasFom && picks[o + 2] < threshold;
        if (removed) { if (showHidden) circle(picks[o], picks[o + 1], COLOURS.removed, true); } else if (below) {
          if (showHidden) circle(picks[o], picks[o + 1], COLOURS.below, true);
        } else circle(picks[o], picks[o + 1], COLOURS.kept);
      }
      for (const [x, y] of (e ? e.added : [])) circle(x, y, COLOURS.added);
      g.setLineDash([]);
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
      const e = edit(m.name);
      const reach = Math.max(4, ((sec.diameter_px || 50) / 2) * sizeFactor);
      let best = null, bestD = reach;
      e.added.forEach(([ax, ay], j) => { const d = Math.hypot(ax - x, ay - y); if (d < bestD) { bestD = d; best = ['added', j]; } });
      for (let i = 0; i < m.count; i += 1) {
        const o = (m.offset + i) * 3;
        const visible = !(hasFom && picks[o + 2] < threshold) && (!e.removed.has(i) || showHidden);
        if (!visible) continue;
        const d = Math.hypot(picks[o] - x, picks[o + 1] - y);
        if (d < bestD) { bestD = d; best = ['pick', i]; }
      }
      if (best && best[0] === 'added') e.added.splice(best[1], 1);
      else if (best) { if (e.removed.has(best[1])) e.removed.delete(best[1]); else e.removed.add(best[1]); } else {
        const [hgt, wid] = m.shape;
        if (x < 0 || y < 0 || x >= wid || y >= hgt) return;
        e.added.push([Math.round(x * 10) / 10, Math.round(y * 10) / 10]);
      }
      changed();
    }
    let drag = null;
    canvas.addEventListener('pointerdown', (ev) => {
      canvas.setPointerCapture(ev.pointerId);
      drag = { x: ev.clientX, y: ev.clientY, cx: view.cx, cy: view.cy, moved: false };
    });
    canvas.addEventListener('pointermove', (ev) => {
      if (!drag) return;
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
      if (ev.target.closest('input, select, textarea')) return;
      if (ev.key === 'ArrowRight') { showMic(cur + 1); ev.preventDefault(); }
      if (ev.key === 'ArrowLeft') { showMic(cur - 1); ev.preventDefault(); }
      if (ev.key === 'f' || ev.key === 'F') { fit(); draw(); }
      if (ev.key === '+' || ev.key === '=') { view.zoom = Math.min(16, view.zoom * 1.25); draw(); }
      if (ev.key === '-') { view.zoom = Math.max(1, view.zoom / 1.25); draw(); }
    });

    // ------------------------------------------------- threshold histogram
    const total = h('div', { class: 'pk-total' });
    let histo = null;
    if (hasFom && info.n_picks) {
      const counts = new Array(BINS).fill(0);
      const span = (fmax - fmin) || 1;
      for (let i = 0; i < info.n_picks; i += 1) counts[Math.min(BINS - 1, Math.floor(((picks[i * 3 + 2] - fmin) / span) * BINS))] += 1;
      const W = 280, H = 90, T = 14, top = Math.max(...counts) || 1;
      const svg = s('svg', { viewBox: `0 -${T} ${W} ${H + 16 + T}`, class: 'pk-hist', role: 'slider', tabindex: 0,
        'aria-label': 'Figure-of-merit threshold', 'aria-valuemin': fmin, 'aria-valuemax': fmax });
      const bw = W / BINS;
      const bars = counts.map((c, i) => s('rect', { x: i * bw + 0.5, y: H - (c / top) * H, width: Math.max(1, bw - 1), height: (c / top) * H }));
      bars.forEach((b) => svg.appendChild(b));
      const line = s('line', { y1: -3, y2: H, class: 'pk-thr' });
      const label = s('text', { y: -5, class: 'pk-thr-label' });
      svg.append(line, label, s('text', { x: 0, y: H + 13, class: 'pk-axis' }, fmin.toFixed(2)),
        s('text', { x: W, y: H + 13, 'text-anchor': 'end', class: 'pk-axis' }, fmax.toFixed(2)));
      const place = () => {
        const x = ((Math.max(fmin, Math.min(fmax, threshold)) - fmin) / span) * W;
        line.setAttribute('x1', x); line.setAttribute('x2', x);
        label.setAttribute('x', Math.min(W - 30, Math.max(30, x)));
        label.setAttribute('text-anchor', 'middle');
        label.textContent = `≥ ${threshold.toFixed(3)}`;
        svg.setAttribute('aria-valuenow', threshold);
        bars.forEach((b, i) => b.classList.toggle('off', fmin + ((i + 1) / BINS) * span <= threshold));
      };
      const setFrom = (ev) => {
        const rect = svg.getBoundingClientRect();
        threshold = fmin + Math.max(0, Math.min(1, (ev.clientX - rect.left) / rect.width)) * span;
        place();
        changed(false);
      };
      let sliding = false;
      svg.addEventListener('pointerdown', (ev) => { sliding = true; svg.setPointerCapture(ev.pointerId); setFrom(ev); });
      svg.addEventListener('pointermove', (ev) => { if (sliding) setFrom(ev); });
      svg.addEventListener('pointerup', () => { sliding = false; });
      svg.addEventListener('keydown', (ev) => {
        if (ev.key !== 'ArrowLeft' && ev.key !== 'ArrowRight') return;
        threshold += (ev.key === 'ArrowRight' ? 1 : -1) * span / 100;
        place();
        changed(false);
        ev.preventDefault();
      });
      place();
      histo = h('div', { class: 'pk-histo' }, h('div', { class: 'muted small' }, 'Figure of merit — drag to set the threshold'), svg);
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
          rows[k].classList.toggle('edited', !!(edits.get(m.name) && (edits.get(m.name).removed.size || edits.get(m.name).added.length)));
        });
        const all = info.n_picks || 0;
        total.replaceChildren(h('b', {}, fmt(n)), ` particles kept on ${fmt(mics.length)} micrographs`,
          all ? h('span', { class: 'muted' }, ` (${fmt(all)} picked)`) : null);
        if (doneBtn) doneBtn.disabled = !n;
      });
    }

    // ------------------------------------------------------------- saving
    const saveState = h('span', { class: 'muted small' });
    const payload = () => ({
      threshold: hasFom ? threshold : null,
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
      title: 'Write the picks kept (coordinate files for the extraction)' }) : null;
    const editBtn = !editable && newInspection ? btn('Inspect and edit…', () => newInspection(hasFom ? threshold : null),
      { cls: 'primary', ic: 'edit', title: 'An inspection job with these picks and this threshold (removes and adds particles)' }) : null;
    const legend = h('div', { class: 'pk-legend muted small' },
      h('span', {}, h('i', { style: { borderColor: COLOURS.kept } }), 'kept'),
      editable ? h('span', {}, h('i', { style: { borderColor: COLOURS.added } }), 'added by hand') : null,
      h('span', {}, h('i', { class: 'dashed', style: { borderColor: '#999' } }), 'below the threshold (Hidden)'),
      editable ? h('span', {}, 'click: remove / add · wheel: zoom · drag: move · ← →: micrographs') : h('span', {}, 'wheel: zoom · drag: move · ← →'));

    const side = h('div', { class: 'pk-side' }, histo, total, list);
    root.replaceChildren(
      h('div', { class: 'pk-head' }, h('h4', {}, sec.title), h('span', { class: 'grow' }), saveState, editBtn, doneBtn),
      h('div', { class: 'pk-body' }, h('div', { class: 'pk-main' }, toolbar, stage, legend, status), side));
    refreshCounts();
    showMic(0);
  }
}
