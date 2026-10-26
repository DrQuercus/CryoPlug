// Particles curated on their statistics, as CryoSPARC's Subset Particles by Statistic made interactive: the distribution
// of each statistic with its limits dragged on the histogram (the bars count the particles the other limits keep; all of
// them in grey behind), a density plot of two statistics, and images of particles kept and left out. The work in
// progress is saved on the server while the job waits; Done finishes the job. Once done: the same views, limits applied.
import { api } from './api.js';
import { btn, h, icon, s } from './ui.js';

const BINS = 48;
const UNKNOWN = 65535, TOP = 65534; // codes of the values: 0 to TOP over [lo, hi], UNKNOWN for a missing value
const GRID = [96, 64]; // bins of the density plot
const SAMPLE = 16; // particle images per strip

const fmtN = (n) => Number(Math.round(n)).toLocaleString();

// A statistic's value as read: defocus in µm, the rest with a precision that suits its size.
function fmtVal(m, v, unit = true) {
  if (v === null || v === undefined || !Number.isFinite(v)) return '–';
  if (m.key === 'defocus' || m.key === 'astigmatism') return `${(v / 1e4).toFixed(m.key === 'defocus' ? 2 : 3)}${unit ? ' µm' : ''}`;
  const a = Math.abs(v);
  const txt = a >= 1e4 ? v.toExponential(3) : a >= 100 ? v.toFixed(0) : a >= 10 ? v.toFixed(1) : a >= 1 ? v.toFixed(2) : v.toFixed(3);
  return unit && m.unit ? `${txt} ${m.unit}` : txt;
}

// `finish(payload)`: Done of a waiting curation.
export function particleCurator(sec, { puid, job, finish = null, widen = null }) {
  const root = h('div', { class: 'pc', tabindex: 0, role: 'application', 'aria-label': sec.title });
  const editable = !!(sec.editable && sec.waiting && job.status === 'waiting' && finish);
  root.append(h('div', { class: 'muted small' }, 'Loading the statistics of the particles…'));
  load().catch((err) => root.replaceChildren(h('div', { class: 'alert error' }, `Statistics not available: ${err.message || err}`)));
  return root;

  async function load() {
    const json = (url) => fetch(url).then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r; });
    const info = await json(api.fileUrl(puid, sec.data)).then((r) => r.json());
    const [codes, rows, saved] = await Promise.all([
      json(api.fileUrl(puid, info.binary)).then((r) => r.arrayBuffer()).then((b) => new Uint16Array(b)),
      info.rows ? json(api.fileUrl(puid, info.rows)).then((r) => r.arrayBuffer()).then((b) => new Uint32Array(b)) : Promise.resolve(null),
      editable ? api.session(puid, job.uid, 'pcurate').catch(() => ({})) : Promise.resolve({}),
    ]);
    build(info, codes, rows, saved || {});
    if (editable && widen) widen();
  }

  function build(info, codes, rows, saved) {
    const N = info.n_display, total = info.n;
    const sampled = N < total;
    const metrics = info.metrics.map((m, j) => ({ ...m, j, codes: codes.subarray(j * N, (j + 1) * N), span: (m.hi - m.lo) || 1 }));
    const byKey = Object.fromEntries(metrics.map((m) => [m.key, m]));
    const limits = new Map(); // statistic -> [lowest | null, highest | null]
    for (const [k, b] of Object.entries((editable && saved.limits) || sec.limits || {})) {
      if (byKey[k] && Array.isArray(b) && (b[0] !== null || b[1] !== null)) limits.set(k, [b[0] ?? null, b[1] ?? null]);
    }
    const failCount = new Uint8Array(N); // limits each particle is out of
    const outCount = new Map();
    let dirty = false, saveTimer = null, fullTimer = null, imageTimer = null;

    const codeLimits = (m) => { // the limits of a statistic in codes (null: none)
      const b = limits.get(m.key);
      if (!b) return [null, null];
      return [b[0] === null ? null : ((b[0] - m.lo) / m.span) * TOP, b[1] === null ? null : ((b[1] - m.lo) / m.span) * TOP];
    };
    function computeFails() {
      failCount.fill(0);
      for (const m of metrics) {
        const [lo, hi] = codeLimits(m);
        let c = 0;
        if (lo !== null || hi !== null) {
          const v = m.codes;
          for (let i = 0; i < N; i += 1) {
            const x = v[i];
            if (x !== UNKNOWN && ((lo !== null && x < lo) || (hi !== null && x > hi))) { c += 1; failCount[i] += 1; }
          }
        }
        outCount.set(m.key, c);
      }
    }
    // per statistic, the particles kept by the limits of the others, by bin
    function crossCounts(m) {
      const counts = new Array(BINS).fill(0);
      const [lo, hi] = codeLimits(m);
      const v = m.codes;
      for (let i = 0; i < N; i += 1) {
        const x = v[i];
        if (x === UNKNOWN) continue;
        const f = failCount[i];
        const own = (lo !== null && x < lo) || (hi !== null && x > hi) ? 1 : 0;
        if (f - own === 0) counts[Math.min(BINS - 1, Math.floor((x / (TOP + 1)) * BINS))] += 1;
      }
      return counts;
    }

    // ------------------------------------------------------- histograms
    const histos = metrics.map((m) => histogram(m));
    function histogram(m) {
      const W = 300, H = 46, T = 12;
      const all = new Array(BINS).fill(0);
      for (const x of m.codes) if (x !== UNKNOWN) all[Math.min(BINS - 1, Math.floor((x / (TOP + 1)) * BINS))] += 1;
      const top = Math.max(...all) || 1;
      const X = (v) => ((v - m.lo) / m.span) * W;
      const svg = s('svg', { viewBox: `0 -${T} ${W} ${H + T + 21}`, class: `xc-hist pc-hist${editable ? ' drag' : ''}`, role: 'img',
        'aria-label': `${m.title}: distribution${editable ? ' (drag the limits)' : ''}` });
      const bw = W / BINS;
      const ghosts = all.map((c, k) => s('rect', { class: 'pc-ghost', x: k * bw + 0.5, y: H - (c / top) * H, width: Math.max(1, bw - 1), height: (c / top) * H }));
      const bars = all.map((c, k) => s('rect', { x: k * bw + 0.5, width: Math.max(1, bw - 1) },
        s('title', {}, `${fmtVal(m, m.lo + (k / BINS) * m.span, false)} – ${fmtVal(m, m.lo + ((k + 1) / BINS) * m.span)}`)));
      const shadeLo = s('rect', { y: 0, height: H, class: 'xc-shade' });
      const shadeHi = s('rect', { y: 0, height: H, class: 'xc-shade' });
      const lineLo = s('line', { y1: -2, y2: H, class: 'xc-lim' });
      const lineHi = s('line', { y1: -2, y2: H, class: 'xc-lim' });
      const label = s('text', { y: -3, class: 'xc-lim-label' });
      const ends = m.clipped ? ['≤ ', '≥ '] : ['', ''];
      svg.append(shadeLo, shadeHi, ...ghosts, ...bars, lineLo, lineHi, label,
        s('text', { x: 0, y: H + 19, class: 'xc-tick' }, ends[0] + fmtVal(m, m.lo)),
        s('text', { x: W / 2, y: H + 19, 'text-anchor': 'middle', class: 'xc-tick' }, `median ${fmtVal(m, m.median)}`),
        s('text', { x: W, y: H + 19, 'text-anchor': 'end', class: 'xc-tick' }, ends[1] + fmtVal(m, m.hi)));
      const out = h('span', { class: 'xc-out' });
      const reset = editable ? h('button', { type: 'button', class: 'xc-reset', title: 'No limit on this statistic', hidden: true,
        onclick: () => { limits.delete(m.key); changed(); } }, icon('close')) : null;
      const box = h('div', { class: 'xc-histo' }, h('div', { class: 'xc-histo-head' },
        h('span', { class: 'xc-histo-title', title: m.clipped ? `${m.title} (the most extreme 0.1 % are drawn at the ends)` : m.title }, m.title),
        out, reset), svg);
      const drawBars = (counts) => {
        const topCross = Math.max(...counts, 1);
        const scale = Math.max(top, topCross);
        bars.forEach((r, k) => { const hh = (counts[k] / scale) * H; r.setAttribute('y', H - hh); r.setAttribute('height', hh); });
        ghosts.forEach((r, k) => { const hh = (all[k] / scale) * H; r.setAttribute('y', H - hh); r.setAttribute('height', hh); });
      };
      const place = () => {
        const b = limits.get(m.key) || [null, null];
        const lo = b[0] !== null ? Math.max(0, Math.min(W, X(b[0]))) : 0;
        const hi = b[1] !== null ? Math.max(0, Math.min(W, X(b[1]))) : W;
        shadeLo.setAttribute('x', 0); shadeLo.setAttribute('width', lo);
        shadeHi.setAttribute('x', hi); shadeHi.setAttribute('width', W - hi);
        lineLo.setAttribute('x1', lo); lineLo.setAttribute('x2', lo); lineLo.classList.toggle('open', b[0] === null);
        lineHi.setAttribute('x1', hi); lineHi.setAttribute('x2', hi); lineHi.classList.toggle('open', b[1] === null);
        label.textContent = b[0] !== null && b[1] !== null ? `${fmtVal(m, b[0], false)} – ${fmtVal(m, b[1])}`
          : b[0] !== null ? `≥ ${fmtVal(m, b[0])}` : b[1] !== null ? `≤ ${fmtVal(m, b[1])}` : '';
        const mid = b[0] !== null && b[1] !== null ? (lo + hi) / 2 : b[0] !== null ? lo : hi;
        label.setAttribute('x', Math.min(W - 44, Math.max(44, mid)));
        label.setAttribute('text-anchor', 'middle');
        bars.forEach((r, k) => {
          const a = m.lo + (k / BINS) * m.span, z = m.lo + ((k + 1) / BINS) * m.span;
          r.classList.toggle('off', (b[0] !== null && z <= b[0]) || (b[1] !== null && a >= b[1]));
        });
        const c = outCount.get(m.key) || 0;
        out.textContent = c ? `${sampled ? '≈ ' : ''}${fmtN(c * (total / N))} out` : '';
        if (reset) reset.hidden = b[0] === null && b[1] === null;
      };
      if (editable) {
        let side = null;
        const valueAt = (ev) => {
          const rect = svg.getBoundingClientRect();
          return m.lo + Math.max(0, Math.min(1, (ev.clientX - rect.left) / rect.width)) * m.span;
        };
        const move = (ev) => {
          const v = valueAt(ev);
          const b = (limits.get(m.key) || [null, null]).slice();
          if (side === 0) b[0] = v <= m.lo + m.span * 0.002 ? null : Math.min(v, b[1] ?? Infinity);
          else b[1] = v >= m.hi - m.span * 0.002 ? null : Math.max(v, b[0] ?? -Infinity);
          if (b[0] === null && b[1] === null) limits.delete(m.key); else limits.set(m.key, b.map((x) => (x === null ? null : +x.toPrecision(5))));
          changed(false);
        };
        svg.addEventListener('pointerdown', (ev) => {
          const v = valueAt(ev);
          const b = limits.get(m.key) || [null, null];
          side = Math.abs(v - (b[0] ?? m.lo)) < Math.abs(v - (b[1] ?? m.hi)) ? 0 : 1;
          svg.setPointerCapture(ev.pointerId);
          move(ev);
        });
        svg.addEventListener('pointermove', (ev) => { if (side !== null) move(ev); });
        const end = () => { if (side !== null) { side = null; changed(); } };
        svg.addEventListener('pointerup', end);
        svg.addEventListener('pointercancel', end);
        svg.addEventListener('dblclick', () => { limits.delete(m.key); changed(); });
      }
      return { m, box, place, drawBars };
    }

    // ------------------------------------------------------ density plot
    const keys = metrics.map((m) => m.key);
    let sx = keys.includes('max_prob') ? 'max_prob' : keys[0];
    let sy = keys.includes('loglike') ? 'loglike' : keys.find((k) => k !== sx) || keys[0];
    const axisSel = (get, set, label) => h('select', { 'aria-label': label, onchange: (e) => { set(e.target.value); drawDensity(); } },
      metrics.map((m) => h('option', { value: m.key, selected: m.key === get() ? 'selected' : null }, m.title)));
    const plot = h('canvas', { class: 'xc-scatter pc-density', role: 'img', 'aria-label': 'Density of the particles over two statistics' });
    const pg = plot.getContext('2d');
    const densityBox = metrics.length >= 2 ? h('div', { class: 'xc-scatter-box' },
      h('div', { class: 'xc-scatter-head muted small' }, axisSel(() => sy, (v) => { sy = v; }, 'Vertical axis'), ' against ',
        axisSel(() => sx, (v) => { sx = v; }, 'Horizontal axis')), plot,
      h('div', { class: 'pc-legend muted small' }, h('i', { class: 'kept' }), 'kept', h('i', { class: 'out' }), 'left out')) : null;
    const PAD = { l: 48, r: 10, t: 10, b: 30 };
    function drawDensity() {
      if (!densityBox) return;
      const dpr = window.devicePixelRatio || 1;
      const w = plot.clientWidth || 340, hh = plot.clientHeight || 260;
      if (plot.width !== Math.round(w * dpr) || plot.height !== Math.round(hh * dpr)) { plot.width = Math.round(w * dpr); plot.height = Math.round(hh * dpr); }
      const css = getComputedStyle(root);
      const ink = css.getPropertyValue('--ink-3').trim() || '#888';
      const accent = css.getPropertyValue('--series-1').trim() || '#2a78d6';
      const bad = css.getPropertyValue('--series-8').trim() || '#e34948';
      const mx = byKey[sx], my = byKey[sy];
      const [GX, GY] = GRID;
      const kept = new Float32Array(GX * GY), left = new Float32Array(GX * GY);
      for (let i = 0; i < N; i += 1) {
        const a = mx.codes[i], b = my.codes[i];
        if (a === UNKNOWN || b === UNKNOWN) continue;
        const cell = Math.min(GY - 1, Math.floor((b / (TOP + 1)) * GY)) * GX + Math.min(GX - 1, Math.floor((a / (TOP + 1)) * GX));
        if (failCount[i]) left[cell] += 1; else kept[cell] += 1;
      }
      let peak = 1;
      for (let c = 0; c < GX * GY; c += 1) peak = Math.max(peak, kept[c], left[c]);
      const lp = Math.log1p(peak);
      pg.setTransform(dpr, 0, 0, dpr, 0, 0);
      pg.clearRect(0, 0, w, hh);
      const pw = w - PAD.l - PAD.r, ph = hh - PAD.t - PAD.b;
      const cw = pw / GX, ch = ph / GY;
      for (const [grid, colour] of [[left, ink], [kept, accent]]) {
        pg.fillStyle = colour;
        for (let c = 0; c < GX * GY; c += 1) {
          if (!grid[c]) continue;
          pg.globalAlpha = 0.18 + 0.82 * (Math.log1p(grid[c]) / lp);
          const gx = c % GX, gy = Math.floor(c / GX);
          pg.fillRect(PAD.l + gx * cw, PAD.t + ph - (gy + 1) * ch, Math.ceil(cw), Math.ceil(ch));
        }
      }
      pg.globalAlpha = 0.5; pg.strokeStyle = ink; pg.lineWidth = 1;
      pg.beginPath(); pg.moveTo(PAD.l, PAD.t); pg.lineTo(PAD.l, hh - PAD.b); pg.lineTo(w - PAD.r, hh - PAD.b); pg.stroke();
      pg.globalAlpha = 1;
      pg.fillStyle = ink; pg.font = '10px system-ui, sans-serif';
      pg.textAlign = 'left'; pg.fillText(fmtVal(mx, mx.lo), PAD.l, hh - PAD.b + 13);
      pg.textAlign = 'right'; pg.fillText(fmtVal(mx, mx.hi), w - PAD.r, hh - PAD.b + 13);
      pg.textAlign = 'center'; pg.fillText(mx.title, PAD.l + pw / 2, hh - 4);
      pg.textAlign = 'right'; pg.fillText(fmtVal(my, my.hi, false), PAD.l - 4, PAD.t + 8); pg.fillText(fmtVal(my, my.lo, false), PAD.l - 4, hh - PAD.b);
      const X = (v) => PAD.l + ((v - mx.lo) / mx.span) * pw, Y = (v) => PAD.t + ph - ((v - my.lo) / my.span) * ph;
      pg.setLineDash([4, 3]); pg.strokeStyle = bad; pg.globalAlpha = 0.85;
      for (const v of limits.get(sx) || []) if (v !== null) { pg.beginPath(); pg.moveTo(X(v), PAD.t); pg.lineTo(X(v), hh - PAD.b); pg.stroke(); }
      for (const v of limits.get(sy) || []) if (v !== null) { pg.beginPath(); pg.moveTo(PAD.l, Y(v)); pg.lineTo(w - PAD.r, Y(v)); pg.stroke(); }
      pg.setLineDash([]); pg.globalAlpha = 1;
    }

    // --------------------------------------------------- particle images
    const src = info.source;
    const strips = src ? [['kept', 'Particles kept'], ['out', 'Particles left out']].map(([kind, title]) => {
      const img = h('img', { class: 'pc-strip', alt: `${title}: a random sample`, draggable: 'false' });
      const note = h('span', { class: 'muted small' });
      return { kind, img, note, box: h('div', { class: 'pc-images' }, h('div', { class: 'pc-images-head small' }, h('b', {}, title), note), img) };
    }) : [];
    function refreshImages() {
      for (const st of strips) {
        const pool = [];
        let seen = 0;
        for (let i = 0; i < N; i += 1) { // reservoir sample of the particles of that kind
          if ((failCount[i] === 0) !== (st.kind === 'kept')) continue;
          seen += 1;
          if (pool.length < SAMPLE) pool.push(i); else { const r = Math.floor(Math.random() * seen); if (r < SAMPLE) pool[r] = i; }
        }
        if (!pool.length) { st.img.removeAttribute('src'); st.img.hidden = true; st.note.textContent = 'none'; continue; }
        const ids = pool.map((i) => (rows ? rows[i] : i)).sort((a, b) => a - b);
        st.img.hidden = false;
        st.note.textContent = `${ids.length} at random`;
        st.img.src = api.particleImagesUrl(puid, src.job, ids, 64, src.output, 8);
      }
    }
    const otherBtn = src ? btn('Other particles', refreshImages, { cls: 'small', title: 'Another random sample of the particles kept and left out' }) : null;

    // ----------------------------------------------------- summary, save
    const summary = h('span', { class: 'xc-sum' });
    const saveState = h('span', { class: 'muted small' });
    function refreshSummary() {
      let kept = 0;
      for (let i = 0; i < N; i += 1) if (!failCount[i]) kept += 1;
      const n = sampled ? kept * (total / N) : kept;
      summary.replaceChildren(h('b', {}, `${sampled ? '≈ ' : ''}${fmtN(n)}`), ` of ${fmtN(total)} particles kept (${((100 * kept) / N).toFixed(1)} %)`,
        ...(sampled ? [h('span', { class: 'muted' }, ` · plots of ${fmtN(N)} at random`)] : []));
      if (doneBtn) doneBtn.disabled = !kept;
    }
    const payload = () => ({ limits: Object.fromEntries(limits) });
    function full() { // the histograms of each statistic under the limits of the others, and the density plot
      clearTimeout(fullTimer);
      histos.forEach((x) => x.drawBars(crossCounts(x.m)));
      drawDensity();
    }
    function changed(final = true) {
      computeFails();
      histos.forEach((x) => x.place());
      refreshSummary();
      clearTimeout(fullTimer);
      fullTimer = setTimeout(full, final ? 0 : 160);
      clearTimeout(imageTimer);
      if (src) imageTimer = setTimeout(refreshImages, final ? 600 : 1500);
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
        await api.saveSession(puid, job.uid, 'pcurate', payload());
        saveState.textContent = 'saved';
      } catch (err) {
        dirty = true;
        saveState.textContent = `not saved: ${err.message || err}`;
      }
    }
    const doneBtn = editable ? btn('Done', async () => { clearTimeout(saveTimer); await finish(payload()); }, { cls: 'primary', ic: 'check',
      title: 'Keep the particles within the limits (and write those left out)' }) : null;
    const resetAll = editable ? btn('No limits', () => { limits.clear(); changed(); }, { cls: 'small', title: 'Remove every limit' }) : null;

    const hint = h('div', { class: 'muted small xc-hint' }, editable
      ? 'Drag on a histogram to set its limits (double-click: none). The blue bars count the particles the other limits keep; grey, all of them.'
      : 'The limits applied: the blue bars count the particles the other limits keep; grey, all of them.');
    const grid = h('div', { class: 'pc-grid' }, ...histos.map((x) => x.box));
    const side = h('div', { class: 'pc-side' }, densityBox,
      strips.length ? h('div', { class: 'pc-images-box' }, ...strips.map((st) => st.box), otherBtn) : null);
    root.replaceChildren(
      h('div', { class: 'xc-head' }, h('h4', {}, sec.title), summary, h('span', { class: 'grow' }), saveState, resetAll, doneBtn),
      hint,
      h('div', { class: 'pc-body' }, grid, side));
    computeFails();
    histos.forEach((x) => x.place());
    refreshSummary();
    full();
    if (src) refreshImages();
    new ResizeObserver(() => requestAnimationFrame(drawDensity)).observe(plot);
  }
}
