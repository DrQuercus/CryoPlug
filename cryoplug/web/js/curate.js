// Micrographs (exposures) as CryoSPARC's Manually Curate Exposures: the distribution of each metric (CTF fit, defocus,
// astigmatism, motion, ice...) with limits dragged on its histogram, a scatter plot of two metrics, the table of the
// micrographs (sortable, drawn as it scrolls) and each micrograph with its CTF fit and drift, accepted or rejected one by
// one (A / R / U, ← →). The work in progress is saved on the server while the job waits; Done finishes the job.
// Read-only (motion correction, CTF estimation, a finished curation): the same views, and "Curate…" makes a curation job.
import { api } from './api.js';
import { btn, h, icon, s } from './ui.js';

const BINS = 40;
const LOWPASS = [0, 10, 15, 20, 30, 50];
const ROW = 26; // px: rows of the table
const SHOW = [['all', 'All'], ['accepted', 'Accepted'], ['rejected', 'Rejected'], ['edited', 'Changed by hand']];

const fmtN = (n) => Number(n).toLocaleString();
const base = (name) => name.split('/').pop();

// A metric's value as read: defocus in µm, the rest with a precision that suits its size.
function fmtVal(m, v, unit = true) {
  if (v === null || v === undefined || !Number.isFinite(v)) return '–';
  if (m.key === 'defocus') return `${(v / 1e4).toFixed(2)}${unit ? ' µm' : ''}`;
  const a = Math.abs(v);
  const txt = a >= 100 ? v.toFixed(0) : a >= 10 ? v.toFixed(1) : a >= 1 ? v.toFixed(2) : v.toFixed(3);
  return unit && m.unit ? `${txt} ${m.unit}` : txt;
}

// `finish(payload)`: Done of a waiting curation; `curate(limits)`: a curation job of these micrographs from these limits.
export function exposureCurator(sec, { puid, job, finish = null, widen = null, curate = null }) {
  const root = h('div', { class: 'xc', tabindex: 0, role: 'application', 'aria-label': sec.title });
  const editable = !!(sec.editable && sec.waiting && job.status === 'waiting' && finish);
  root.append(h('div', { class: 'muted small' }, 'Loading the micrographs…'));
  load().catch((err) => root.replaceChildren(h('div', { class: 'alert error' }, `Micrographs not available: ${err.message || err}`)));
  return root;

  async function load() {
    const [info, saved] = await Promise.all([
      fetch(api.fileUrl(puid, sec.data)).then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); }),
      editable ? api.session(puid, job.uid, 'curate').catch(() => ({})) : Promise.resolve({}),
    ]);
    build(info, saved || {});
    if (editable && widen) widen();
    if (editable) requestAnimationFrame(() => requestAnimationFrame(bringIntoView));
  }

  // A waiting curation fills the panel: scrolled to just below the job's header (once, when it opens).
  function bringIntoView() {
    const panel = root.closest('.panel');
    if (!panel || !root.isConnected) return;
    const head = panel.querySelector('.panel-head');
    const top = head ? head.getBoundingClientRect().bottom : panel.getBoundingClientRect().top;
    panel.scrollTop += root.getBoundingClientRect().top - top - 8;
  }

  function build(info, saved) {
    const names = info.names || [];
    const n = names.length;
    if (!n) { root.replaceChildren(h('div', { class: 'muted' }, 'No micrograph.')); return; }
    const metrics = (info.metrics || []).map((m) => {
      const values = Float64Array.from(m.values, (v) => (v === null ? NaN : v));
      let lo = Infinity, hi = -Infinity;
      for (const v of values) if (Number.isFinite(v)) { if (v < lo) lo = v; if (v > hi) hi = v; }
      if (lo === Infinity) { lo = 0; hi = 1; }
      if (hi - lo < 1e-9) { lo -= 0.5; hi += 0.5; }
      return { ...m, values, lo, hi };
    });
    const byKey = Object.fromEntries(metrics.map((m) => [m.key, m]));
    const decided = !editable && typeof sec.decided === 'string' && sec.decided.length === n ? sec.decided : null;
    const draggable = !decided; // a finished curation shows its limits; elsewhere they are tried out (and saved while waiting)
    const limits = new Map(); // metric key -> [lower | null, upper | null]
    for (const [k, b] of Object.entries((editable && saved.thresholds) || sec.thresholds || {})) {
      if (byKey[k] && Array.isArray(b)) limits.set(k, [b[0] ?? null, b[1] ?? null]);
    }
    const index = new Map(names.map((name, i) => [name, i]));
    const hand = new Int8Array(n); // 1 accepted by hand, -1 rejected by hand
    for (const [name, what] of Object.entries((editable ? saved.overrides : sec.overrides) || {})) {
      const i = index.get(name);
      if (i !== undefined) hand[i] = what === 'accept' ? 1 : what === 'reject' ? -1 : 0;
    }
    const failing = new Int16Array(n); // 1 + the first metric whose limits reject the micrograph; 0: within all
    const outCount = new Map();
    let cur = 0, lowpass = 20, show = 'all', dirty = false, saveTimer = null;
    const sort = { key: '#', dir: 1 };

    function computeAuto() {
      failing.fill(0);
      metrics.forEach((m, j) => {
        const b = limits.get(m.key);
        let c = 0;
        if (b && (b[0] !== null || b[1] !== null)) {
          const [lo, hi] = b;
          const v = m.values;
          for (let i = 0; i < n; i += 1) {
            const x = v[i];
            if (x === x && ((lo !== null && x < lo) || (hi !== null && x > hi))) { c += 1; if (!failing[i]) failing[i] = j + 1; }
          }
        }
        outCount.set(m.key, c);
      });
    }
    const isIn = (i) => (decided ? decided[i] === '1' : hand[i] ? hand[i] > 0 : !failing[i]);

    // ------------------------------------------------------------- viewer
    const title = h('span', { class: 'xc-name mono' });
    const badge = h('span', { class: 'xc-badge' });
    const micImg = h('img', { class: 'xc-mic', alt: 'Micrograph', draggable: 'false' });
    const micLink = h('a', { class: 'xc-mic-link', target: '_blank', rel: 'noopener', title: 'Open the micrograph at full size' }, micImg);
    const ctfImg = info.ctf_images ? h('img', { class: 'xc-ctf', alt: 'CTF fit', draggable: 'false' }) : null;
    const driftBox = info.motion_files ? h('div', { class: 'xc-drift' }) : null;
    const values = h('div', { class: 'xc-values' });
    const showSel = h('select', { 'aria-label': 'Micrographs browsed', onchange: (e) => { show = e.target.value; reorder(); refreshTable(true); } },
      SHOW.filter(([k]) => k !== 'edited' || editable).map(([k, label]) => h('option', { value: k }, label)));
    const lp = h('select', { 'aria-label': 'Low-pass filter of the display', onchange: (e) => { lowpass = +e.target.value; showMic(cur, true); } },
      LOWPASS.map((v) => h('option', { value: v, selected: v === lowpass ? 'selected' : null }, v ? `${v} Å` : 'none')));
    const actions = editable ? h('div', { class: 'xc-actions' },
      btn('Accept', () => decide(1), { cls: 'small', ic: 'check', title: 'Keep this micrograph whatever the limits (A)' }),
      btn('Reject', () => decide(-1), { cls: 'small danger', ic: 'close', title: 'Leave this micrograph out (R)' }),
      btn('By the limits', () => decide(0), { cls: 'small', title: 'Accepted or rejected by the limits (U)' })) : null;
    const toolbar = h('div', { class: 'xc-tools' },
      btn('', () => step(-1), { cls: 'small', ic: 'back', title: 'Previous micrograph (←)' }),
      btn('', () => step(1), { cls: 'small', ic: 'next', title: 'Next micrograph (→)' }),
      title, badge, h('span', { class: 'grow' }),
      h('label', { class: 'muted small', title: 'Micrographs browsed with ← →' }, 'Browse', showSel),
      h('label', { class: 'muted small', title: 'Low-pass filter of the display' }, 'Filter', lp));
    const imgSize = () => (root.closest('.panel.wide') ? 1536 : 1024);
    let micUrl = '';
    const drifts = new Map();

    function showMic(i, reload = false) {
      cur = i;
      const name = names[i];
      title.textContent = `${i + 1}/${fmtN(n)} · ${base(name)}`;
      title.title = name;
      const url = api.micrographUrl(puid, name, imgSize(), lowpass, info.apix || 0);
      if (url !== micUrl || reload) {
        micUrl = url;
        micImg.classList.add('loading');
        micImg.onload = () => micImg.classList.remove('loading');
        micImg.src = url;
        micLink.href = api.micrographUrl(puid, name, 4096, lowpass, info.apix || 0);
      }
      if (ctfImg) ctfImg.src = api.ctfImageUrl(puid, info.ctf_images[i], 320);
      if (driftBox) drawDrift(i);
      refreshCurrent();
      prefetch();
      highlightRow();
      drawScatter();
      histos.forEach((x) => x.mark());
    }
    function prefetch() { // the next micrograph in the browsing order, for a quick ←/→
      const k = order.indexOf(cur);
      const nxt = order[(k + 1) % order.length];
      if (nxt !== undefined && nxt !== cur) {
        new Image().src = api.micrographUrl(puid, names[nxt], imgSize(), lowpass, info.apix || 0);
        if (info.ctf_images) new Image().src = api.ctfImageUrl(puid, info.ctf_images[nxt], 320);
      }
    }
    async function drawDrift(i) {
      const path = info.motion_files[i];
      if (!path) { driftBox.replaceChildren(h('div', { class: 'muted small' }, 'No drift')); return; }
      let d = drifts.get(path);
      if (!d) {
        try { d = await api.drift(puid, path); } catch { d = { x: [], y: [] }; }
        drifts.set(path, d);
        if (cur !== i) return;
      }
      const { x, y } = d;
      if (!x.length) { driftBox.replaceChildren(h('div', { class: 'muted small' }, 'No drift')); return; }
      const S = 150, pad = 12;
      const xs = x.map((v) => v - x[0]), ys = y.map((v) => v - y[0]);
      const span = Math.max(4, ...xs.map(Math.abs), ...ys.map(Math.abs)) * 1.15;
      const px = (v) => S / 2 + (v / span) * (S / 2 - pad), py = (v) => S / 2 - (v / span) * (S / 2 - pad);
      let len = 0;
      for (let k = 1; k < xs.length; k += 1) len += Math.hypot(xs[k] - xs[k - 1], ys[k] - ys[k - 1]);
      const svg = s('svg', { viewBox: `0 0 ${S} ${S}`, class: 'xc-drift-svg', role: 'img', 'aria-label': `Drift: ${len.toFixed(1)} px over ${xs.length} frames` },
        s('line', { x1: pad, x2: S - pad, y1: S / 2, y2: S / 2, class: 'xc-axis' }),
        s('line', { y1: pad, y2: S - pad, x1: S / 2, x2: S / 2, class: 'xc-axis' }),
        s('polyline', { points: xs.map((v, k) => `${px(v).toFixed(1)},${py(ys[k]).toFixed(1)}`).join(' '), class: 'xc-path' }),
        s('circle', { cx: px(0), cy: py(0), r: 3, class: 'xc-start' }),
        s('circle', { cx: px(xs[xs.length - 1]), cy: py(ys[ys.length - 1]), r: 2.5, class: 'xc-end' }));
      driftBox.replaceChildren(h('div', { class: 'muted small' }, `Drift · ${len.toFixed(1)} px, ${xs.length} frames`), svg);
    }
    function refreshCurrent() {
      const i = cur;
      const inside = isIn(i);
      const why = failing[i] ? metrics[failing[i] - 1] : null;
      badge.className = `xc-badge ${inside ? 'in' : 'out'}`;
      badge.textContent = decided ? (inside ? 'kept' : 'rejected')
        : hand[i] ? `${inside ? 'accepted' : 'rejected'} by hand`
          : inside ? 'accepted' : `rejected: ${why ? why.title.toLowerCase() : ''}`;
      values.replaceChildren(...metrics.map((m) => {
        const b = limits.get(m.key);
        const v = m.values[i];
        const bad = b && Number.isFinite(v) && ((b[0] !== null && v < b[0]) || (b[1] !== null && v > b[1]));
        return h('div', { class: `xc-val${bad ? ' bad' : ''}` }, h('span', { class: 'muted' }, m.title), h('b', {}, fmtVal(m, v)));
      }));
    }
    function decide(what) {
      if (!editable) return;
      hand[cur] = what;
      changed();
      if (what !== 0) step(1);
    }

    // ------------------------------------------------------- histograms
    const histos = metrics.map((m) => histogram(m));
    function histogram(m) {
      const W = 300, H = 40, T = 12;
      const span = m.hi - m.lo;
      const counts = new Array(BINS).fill(0);
      for (const v of m.values) if (Number.isFinite(v)) counts[Math.min(BINS - 1, Math.floor(((v - m.lo) / span) * BINS))] += 1;
      const top = Math.max(...counts) || 1;
      const X = (v) => ((v - m.lo) / span) * W;
      const svg = s('svg', { viewBox: `0 -${T} ${W} ${H + T + 21}`, class: `xc-hist${draggable ? ' drag' : ''}`, role: 'img',
        'aria-label': `${m.title}: distribution${draggable ? ' (drag the limits)' : ''}` });
      const bw = W / BINS;
      const bars = counts.map((c, k) => s('rect', { x: k * bw + 0.5, y: H - (c / top) * H, width: Math.max(1, bw - 1), height: (c / top) * H },
        s('title', {}, `${fmtVal(m, m.lo + (k / BINS) * span, false)} – ${fmtVal(m, m.lo + ((k + 1) / BINS) * span)}: ${fmtN(c)} micrographs`)));
      const shadeLo = s('rect', { y: 0, height: H, class: 'xc-shade' });
      const shadeHi = s('rect', { y: 0, height: H, class: 'xc-shade' });
      const lineLo = s('line', { y1: -2, y2: H, class: 'xc-lim' });
      const lineHi = s('line', { y1: -2, y2: H, class: 'xc-lim' });
      const label = s('text', { y: -3, class: 'xc-lim-label' });
      const marker = s('path', { class: 'xc-mark' });
      svg.append(shadeLo, shadeHi, ...bars, lineLo, lineHi, label, marker,
        s('text', { x: 0, y: H + 19, class: 'xc-tick' }, fmtVal(m, m.lo)),
        s('text', { x: W, y: H + 19, 'text-anchor': 'end', class: 'xc-tick' }, fmtVal(m, m.hi)));
      const out = h('span', { class: 'xc-out' });
      const reset = draggable ? h('button', { type: 'button', class: 'xc-reset', title: 'No limit on this metric', hidden: true,
        onclick: () => { limits.delete(m.key); changed(); } }, icon('close')) : null;
      const box = h('div', { class: 'xc-histo' }, h('div', { class: 'xc-histo-head' }, h('span', { class: 'xc-histo-title' }, m.title), out, reset), svg);
      const place = () => {
        const b = limits.get(m.key) || [null, null];
        const lo = b[0] !== null ? Math.max(0, Math.min(W, X(b[0]))) : 0;
        const hi = b[1] !== null ? Math.max(0, Math.min(W, X(b[1]))) : W;
        shadeLo.setAttribute('x', 0); shadeLo.setAttribute('width', lo);
        shadeHi.setAttribute('x', hi); shadeHi.setAttribute('width', W - hi);
        lineLo.setAttribute('x1', lo); lineLo.setAttribute('x2', lo); lineLo.classList.toggle('open', b[0] === null);
        lineHi.setAttribute('x1', hi); lineHi.setAttribute('x2', hi); lineHi.classList.toggle('open', b[1] === null);
        const txt = b[0] !== null && b[1] !== null ? `${fmtVal(m, b[0], false)} – ${fmtVal(m, b[1])}`
          : b[0] !== null ? `≥ ${fmtVal(m, b[0])}` : b[1] !== null ? `≤ ${fmtVal(m, b[1])}` : '';
        label.textContent = txt;
        const mid = b[0] !== null && b[1] !== null ? (lo + hi) / 2 : b[0] !== null ? lo : hi;
        label.setAttribute('x', Math.min(W - 40, Math.max(40, mid)));
        label.setAttribute('text-anchor', 'middle');
        bars.forEach((r, k) => {
          const a = m.lo + (k / BINS) * span, z = m.lo + ((k + 1) / BINS) * span;
          r.classList.toggle('off', (b[0] !== null && z <= b[0]) || (b[1] !== null && a >= b[1]));
        });
        const c = outCount.get(m.key) || 0;
        out.textContent = c ? `${fmtN(c)} out` : '';
        if (reset) reset.hidden = b[0] === null && b[1] === null;
      };
      const mark = () => {
        const v = m.values[cur];
        if (!Number.isFinite(v)) { marker.setAttribute('d', ''); return; }
        const x = X(v);
        marker.setAttribute('d', `M${x.toFixed(1)},${H + 1} l-4,6 h8 z`);
      };
      if (draggable) {
        let side = null;
        const valueAt = (ev) => {
          const rect = svg.getBoundingClientRect();
          return m.lo + Math.max(0, Math.min(1, (ev.clientX - rect.left) / rect.width)) * span;
        };
        const move = (ev) => {
          const v = valueAt(ev);
          const b = (limits.get(m.key) || [null, null]).slice();
          if (side === 0) b[0] = v <= m.lo + span * 0.002 ? null : Math.min(v, b[1] ?? Infinity);
          else b[1] = v >= m.hi - span * 0.002 ? null : Math.max(v, b[0] ?? -Infinity);
          if (b[0] === null && b[1] === null) limits.delete(m.key); else limits.set(m.key, b.map((x) => (x === null ? null : +x.toPrecision(5))));
          changed(false);
        };
        svg.addEventListener('pointerdown', (ev) => {
          const v = valueAt(ev);
          const b = limits.get(m.key) || [null, null];
          const dLo = Math.abs(v - (b[0] ?? m.lo)), dHi = Math.abs(v - (b[1] ?? m.hi));
          side = dLo < dHi ? 0 : 1;
          svg.setPointerCapture(ev.pointerId);
          move(ev);
        });
        svg.addEventListener('pointermove', (ev) => { if (side !== null) move(ev); });
        svg.addEventListener('pointerup', () => { side = null; });
        svg.addEventListener('pointercancel', () => { side = null; });
        svg.addEventListener('dblclick', () => { limits.delete(m.key); changed(); });
      }
      return { box, place, mark };
    }

    // ----------------------------------------------------------- scatter
    const keys = metrics.map((m) => m.key);
    let sx = keys.includes('defocus') ? 'defocus' : keys[0];
    let sy = keys.includes('ctf_res') ? 'ctf_res' : keys[Math.min(1, keys.length - 1)];
    const axisSel = (get, set, label) => h('select', { 'aria-label': label, onchange: (e) => { set(e.target.value); drawScatter(); } },
      metrics.map((m) => h('option', { value: m.key, selected: m.key === get() ? 'selected' : null }, m.title)));
    const scatter = h('canvas', { class: 'xc-scatter', 'aria-label': 'Scatter plot of two metrics; click a point to show its micrograph' });
    const sg = scatter.getContext('2d');
    const scatterBox = metrics.length >= 2 ? h('div', { class: 'xc-scatter-box' },
      h('div', { class: 'xc-scatter-head muted small' }, axisSel(() => sy, (v) => { sy = v; }, 'Vertical axis'), ' against ',
        axisSel(() => sx, (v) => { sx = v; }, 'Horizontal axis')), scatter) : null;
    const PAD = { l: 44, r: 10, t: 10, b: 30 };
    let scatterPts = null;
    function drawScatter() {
      if (!scatterBox) return;
      const dpr = window.devicePixelRatio || 1;
      const w = scatter.clientWidth || 320, hh = scatter.clientHeight || 260;
      if (scatter.width !== Math.round(w * dpr) || scatter.height !== Math.round(hh * dpr)) {
        scatter.width = Math.round(w * dpr); scatter.height = Math.round(hh * dpr);
      }
      const css = getComputedStyle(root);
      const ink = css.getPropertyValue('--ink-3').trim() || '#888';
      const accent = css.getPropertyValue('--series-1').trim() || '#2a78d6';
      const bad = css.getPropertyValue('--series-8').trim() || '#e34948';
      const mx = byKey[sx], my = byKey[sy];
      sg.setTransform(dpr, 0, 0, dpr, 0, 0);
      sg.clearRect(0, 0, w, hh);
      const X = (v) => PAD.l + ((v - mx.lo) / (mx.hi - mx.lo)) * (w - PAD.l - PAD.r);
      const Y = (v) => hh - PAD.b - ((v - my.lo) / (my.hi - my.lo)) * (hh - PAD.t - PAD.b);
      sg.strokeStyle = ink; sg.globalAlpha = 0.5; sg.lineWidth = 1;
      sg.beginPath(); sg.moveTo(PAD.l, PAD.t); sg.lineTo(PAD.l, hh - PAD.b); sg.lineTo(w - PAD.r, hh - PAD.b); sg.stroke();
      sg.globalAlpha = 1;
      sg.fillStyle = ink; sg.font = '10px system-ui, sans-serif';
      sg.textAlign = 'left'; sg.fillText(fmtVal(mx, mx.lo), PAD.l, hh - PAD.b + 13);
      sg.textAlign = 'right'; sg.fillText(fmtVal(mx, mx.hi), w - PAD.r, hh - PAD.b + 13);
      sg.textAlign = 'center'; sg.fillText(mx.title, (PAD.l + w - PAD.r) / 2, hh - 4);
      sg.textAlign = 'right'; sg.fillText(fmtVal(my, my.hi, false), PAD.l - 4, PAD.t + 8); sg.fillText(fmtVal(my, my.lo, false), PAD.l - 4, hh - PAD.b);
      // limits of the two metrics: dashed lines
      sg.setLineDash([4, 3]); sg.strokeStyle = bad; sg.globalAlpha = 0.8;
      for (const v of limits.get(sx) || []) if (v !== null) { sg.beginPath(); sg.moveTo(X(v), PAD.t); sg.lineTo(X(v), hh - PAD.b); sg.stroke(); }
      for (const v of limits.get(sy) || []) if (v !== null) { sg.beginPath(); sg.moveTo(PAD.l, Y(v)); sg.lineTo(w - PAD.r, Y(v)); sg.stroke(); }
      sg.setLineDash([]); sg.globalAlpha = 1;
      const r = n > 3000 ? 1.6 : n > 500 ? 2.2 : 3;
      scatterPts = new Float32Array(n * 2).fill(NaN);
      for (const pass of [0, 1]) { // rejected underneath, accepted on top
        sg.fillStyle = pass ? accent : ink;
        sg.globalAlpha = pass ? 0.75 : 0.45;
        sg.beginPath();
        for (let i = 0; i < n; i += 1) {
          if (isIn(i) !== !!pass) continue;
          const a = mx.values[i], b = my.values[i];
          if (!Number.isFinite(a) || !Number.isFinite(b)) continue;
          const x = X(a), y = Y(b);
          scatterPts[i * 2] = x; scatterPts[i * 2 + 1] = y;
          sg.moveTo(x + r, y); sg.arc(x, y, r, 0, Math.PI * 2);
        }
        sg.fill();
      }
      sg.globalAlpha = 1;
      const cx = scatterPts[cur * 2], cy = scatterPts[cur * 2 + 1];
      if (Number.isFinite(cx)) {
        sg.strokeStyle = css.getPropertyValue('--ink-1').trim() || '#000'; sg.lineWidth = 2;
        sg.beginPath(); sg.arc(cx, cy, r + 4, 0, Math.PI * 2); sg.stroke();
      }
    }
    const nearest = (ev) => {
      if (!scatterPts) return -1;
      const rect = scatter.getBoundingClientRect();
      const x = ev.clientX - rect.left, y = ev.clientY - rect.top;
      let best = -1, bd = 64;
      for (let i = 0; i < n; i += 1) {
        const dx = scatterPts[i * 2] - x, dy = scatterPts[i * 2 + 1] - y;
        const d = dx * dx + dy * dy;
        if (d < bd) { bd = d; best = i; }
      }
      return best;
    };
    scatter.addEventListener('click', (ev) => { const i = nearest(ev); if (i >= 0) showMic(i); });
    scatter.addEventListener('mousemove', (ev) => {
      const i = nearest(ev);
      scatter.title = i >= 0 ? `${base(names[i])} · ${byKey[sx].title} ${fmtVal(byKey[sx], byKey[sx].values[i])} · ${byKey[sy].title} ${fmtVal(byKey[sy], byKey[sy].values[i])}` : '';
      scatter.style.cursor = i >= 0 ? 'pointer' : 'default';
    });

    // ------------------------------------------------------------- table
    const cols = [['#', '#', '44px'], ['name', 'Micrograph', 'minmax(140px, 1.6fr)'], ['status', 'Status', '96px'],
      ...metrics.map((m) => [m.key, m.title, 'minmax(76px, 1fr)'])];
    const template = cols.map((c) => c[2]).join(' ');
    const headRow = h('div', { class: 'xc-row xc-thead', style: { gridTemplateColumns: template } });
    const spacer = h('div', { class: 'xc-spacer' });
    const tbody = h('div', { class: 'xc-tbody', role: 'listbox', 'aria-label': 'Micrographs' }, spacer);
    const table = h('div', { class: 'xc-table', style: { '--xc-min': `${44 + 140 + 96 + 76 * metrics.length + 6 * (cols.length - 1) + 16}px` } },
      headRow, tbody);
    let order = [];
    function drawHead() {
      headRow.replaceChildren(...cols.map(([k, label]) => h('button', { type: 'button', class: `xc-th${sort.key === k ? ' on' : ''}`,
        title: `Sort by ${label.toLowerCase()}`, onclick: () => { sort.dir = sort.key === k ? -sort.dir : 1; sort.key = k; drawHead(); reorder(); refreshTable(true); } },
      label, sort.key === k ? (sort.dir > 0 ? ' ▲' : ' ▼') : '')));
    }
    function reorder() {
      let idx = Array.from({ length: n }, (_, i) => i);
      if (show === 'accepted') idx = idx.filter(isIn);
      else if (show === 'rejected') idx = idx.filter((i) => !isIn(i));
      else if (show === 'edited') idx = idx.filter((i) => hand[i] !== 0);
      const d = sort.dir;
      if (sort.key === 'name') idx.sort((a, b) => d * names[a].localeCompare(names[b]));
      else if (sort.key === 'status') idx.sort((a, b) => d * ((isIn(a) ? 1 : 0) - (isIn(b) ? 1 : 0)) || a - b);
      else if (byKey[sort.key]) {
        const v = byKey[sort.key].values;
        idx.sort((a, b) => {
          const x = v[a], y = v[b];
          if (!Number.isFinite(x)) return Number.isFinite(y) ? 1 : a - b;
          if (!Number.isFinite(y)) return -1;
          return d * (x - y) || a - b;
        });
      } else if (d < 0) idx.reverse();
      order = idx;
    }
    function refreshTable(resetScroll = false) {
      spacer.style.height = `${order.length * ROW}px`;
      if (resetScroll) tbody.scrollTop = Math.max(0, Math.min(tbody.scrollTop, order.length * ROW - tbody.clientHeight));
      drawRows();
    }
    function drawRows() {
      const top = tbody.scrollTop, height = tbody.clientHeight || 300;
      const first = Math.max(0, Math.floor(top / ROW) - 4), last = Math.min(order.length, Math.ceil((top + height) / ROW) + 4);
      const rows = [];
      for (let k = first; k < last; k += 1) {
        const i = order[k];
        const inside = isIn(i);
        rows.push(h('div', { class: `xc-row${i === cur ? ' on' : ''}${inside ? '' : ' out'}`, role: 'option', 'aria-selected': i === cur ? 'true' : 'false',
          style: { top: `${k * ROW}px`, gridTemplateColumns: template }, onclick: () => { showMic(i); root.focus({ preventScroll: true }); } },
        h('span', { class: 'muted' }, i + 1), h('span', { class: 'xc-cell-name', title: names[i] }, base(names[i])),
        h('span', { class: `xc-status ${inside ? 'in' : 'out'}` }, inside ? 'accepted' : 'rejected', hand[i] && !decided ? ' ✎' : ''),
        ...metrics.map((m) => {
          const b = limits.get(m.key);
          const v = m.values[i];
          const bad = b && Number.isFinite(v) && ((b[0] !== null && v < b[0]) || (b[1] !== null && v > b[1]));
          return h('span', { class: `xc-num${bad ? ' bad' : ''}` }, fmtVal(m, v, false));
        })));
      }
      spacer.replaceChildren(...rows);
    }
    tbody.addEventListener('scroll', () => requestAnimationFrame(drawRows), { passive: true });
    function highlightRow() {
      const k = order.indexOf(cur);
      if (k >= 0) { // scroll the table only (not the panel around it)
        const y = k * ROW;
        if (y < tbody.scrollTop) tbody.scrollTop = y;
        else if (y + ROW > tbody.scrollTop + tbody.clientHeight) tbody.scrollTop = y + ROW - tbody.clientHeight;
      }
      drawRows();
    }
    function step(d) {
      if (!order.length) return;
      const k = order.indexOf(cur);
      const next = k < 0 ? order[0] : order[(k + d + order.length) % order.length];
      showMic(next);
    }

    // ----------------------------------------------------- summary, save
    const summary = h('span', { class: 'xc-sum' });
    const saveState = h('span', { class: 'muted small' });
    function refreshSummary() {
      let kept = 0, byHandIn = 0, byHandOut = 0;
      for (let i = 0; i < n; i += 1) {
        if (isIn(i)) kept += 1;
        if (!decided && hand[i] > 0 && failing[i]) byHandIn += 1;
        if (!decided && hand[i] < 0 && !failing[i]) byHandOut += 1;
      }
      const parts = [h('b', {}, fmtN(kept)), ` of ${fmtN(n)} micrographs ${decided ? 'kept' : 'accepted'} (${((100 * kept) / n).toFixed(1)} %)`];
      if (byHandOut || byHandIn) parts.push(h('span', { class: 'muted' }, ` · by hand: ${byHandOut ? `${fmtN(byHandOut)} rejected` : ''}${byHandOut && byHandIn ? ', ' : ''}${byHandIn ? `${fmtN(byHandIn)} accepted` : ''}`));
      summary.replaceChildren(...parts);
      if (doneBtn) doneBtn.disabled = !kept;
    }
    const payload = () => ({
      thresholds: Object.fromEntries(limits),
      overrides: Object.fromEntries(names.flatMap((name, i) => (hand[i] ? [[name, hand[i] > 0 ? 'accept' : 'reject']] : []))),
    });
    function changed(full = true) {
      computeAuto();
      histos.forEach((x) => x.place());
      refreshSummary();
      refreshCurrent();
      if (show !== 'all' || ['status'].includes(sort.key)) reorder();
      refreshTable();
      requestAnimationFrame(drawScatter);
      if (!editable) return;
      dirty = true;
      saveState.textContent = 'not saved yet';
      clearTimeout(saveTimer);
      saveTimer = setTimeout(save, full ? 800 : 1500);
    }
    async function save() {
      if (!dirty) return;
      dirty = false;
      try {
        await api.saveSession(puid, job.uid, 'curate', payload());
        saveState.textContent = 'saved';
      } catch (err) {
        dirty = true;
        saveState.textContent = `not saved: ${err.message || err}`;
      }
    }
    const doneBtn = editable ? btn('Done', async () => { clearTimeout(saveTimer); await finish(payload()); }, { cls: 'primary', ic: 'check',
      title: 'Keep the accepted micrographs' }) : null;
    const curateBtn = !editable && !decided && curate ? btn('Curate…', () => curate(Object.fromEntries(limits)), { cls: 'primary', ic: 'next',
      title: 'A curation job of these micrographs, starting from the limits set here' }) : null;
    const resetAll = draggable ? btn('No limits', () => { limits.clear(); changed(); }, { cls: 'small', title: 'Remove every limit' }) : null;

    root.addEventListener('keydown', (ev) => {
      if (ev.target.closest('input, select, textarea')) return;
      const k = ev.key.toLowerCase();
      if (ev.key === 'ArrowRight' || ev.key === 'ArrowDown') { step(1); ev.preventDefault(); }
      else if (ev.key === 'ArrowLeft' || ev.key === 'ArrowUp') { step(-1); ev.preventDefault(); }
      else if (editable && k === 'a') decide(1);
      else if (editable && k === 'r') decide(-1);
      else if (editable && k === 'u') decide(0);
    });

    const hint = h('div', { class: 'muted small xc-hint' }, editable
      ? 'Drag on a histogram to set its limits (double-click: none) · A accept, R reject, U by the limits · ← → micrographs · click the scatter plot or the table to show a micrograph'
      : draggable ? 'Drag on a histogram to try limits (double-click: none) · ← → micrographs' : '← → micrographs');
    const side = h('div', { class: 'xc-side' }, h('div', { class: 'xc-side-head muted small' }, decided ? 'Limits of the curation' : 'Distributions', h('span', { class: 'grow' }), resetAll),
      ...histos.map((x) => x.box));
    const viewer = h('div', { class: 'xc-main' }, toolbar,
      h('div', { class: 'xc-view' }, micLink, h('div', { class: 'xc-aside' }, ctfImg ? h('div', { class: 'xc-ctf-box' }, h('div', { class: 'muted small' }, 'CTF fit'), ctfImg) : null,
        driftBox, values)),
      actions);
    root.replaceChildren(
      h('div', { class: 'xc-head' }, h('h4', {}, sec.title), summary, h('span', { class: 'grow' }), saveState, curateBtn, doneBtn),
      hint,
      h('div', { class: 'xc-body' }, viewer, side),
      h('div', { class: 'xc-bottom' }, table, scatterBox));
    computeAuto();
    histos.forEach((x) => x.place());
    drawHead();
    reorder();
    refreshSummary();
    refreshTable();
    showMic(0);
    new ResizeObserver(() => requestAnimationFrame(() => { drawScatter(); drawRows(); })).observe(table);
  }
}
