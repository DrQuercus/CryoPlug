// Latent space explorer (cryoDRGN, CryoSPARC 3D variability): a sample of the particles in 2D (UMAP, PCA, the
// variability components...), coloured by latent cluster or by a property of the particles (density, distance
// from the centre, defocus, viewing angles...). Clusters picked on the plot, in the legend or in the gallery open
// in the 3D viewer (one volume, or several played as a series) or feed a new job: keep or remove their particles,
// a trajectory through them, one volume extracted as a map. A region drawn with the lasso can be kept, removed or
// turned into volumes; any particle can be turned into its volume; the images of the picked particles are shown.
import { btn, h } from './ui.js';

const RATIO = 0.68; // plot height / width
const PAD = { l: 26, r: 10, t: 10, b: 26 };
const HIT_CENTRE = 13; // px
const HIT_POINT = 7;
const BINS = 48; // colour steps of continuous colourings
const IMAGES = 24; // particle images shown at once

let probe = null;
// Any CSS colour as 0xRRGGBB (the 3D viewer takes numbers).
export function colorInt(css) {
  probe ||= document.createElement('canvas').getContext('2d');
  probe.fillStyle = '#000000';
  probe.fillStyle = css;
  const v = probe.fillStyle;
  if (v.startsWith('#')) return parseInt(v.slice(1, 7), 16);
  const [r, g, b] = (v.match(/[\d.]+/g) || [0, 0, 0]).map(Number);
  return (r << 16) | (g << 8) | b;
}
const rgbOf = (css) => { const v = colorInt(css); return [(v >> 16) & 255, (v >> 8) & 255, v & 255]; };
const cssOf = ([r, g, b]) => `rgb(${Math.round(r)} ${Math.round(g)} ${Math.round(b)})`;

// Categorical colours of the clusters: the chart series up to 8, evenly spread hues beyond.
function clusterColors(k, el) {
  const css = getComputedStyle(el);
  const series = Array.from({ length: 8 }, (_, i) => css.getPropertyValue(`--series-${i + 1}`).trim());
  if (k <= 8 && series.every(Boolean)) return series.slice(0, k);
  return Array.from({ length: k }, (_, i) => `hsl(${Math.round((i * 137.508 + 215) % 360)} ${[68, 58, 74][i % 3]}% ${[50, 40, 60][i % 3]}%)`);
}

// Sequential ramp (one hue, faint to strong in both themes) and a two-hue cyclic ramp for angles.
function ramps(el) {
  const css = getComputedStyle(el);
  const v = (name, fb) => css.getPropertyValue(name).trim() || fb;
  const seq = [1, 2, 3, 4, 5, 6].map((i) => rgbOf(v(`--seq-${i}`, '#3987e5')));
  const cyc = [v('--series-1', '#2a78d6'), v('--surface-3', '#e6e5e0'), v('--series-2', '#eb6834'), v('--ink-2', '#52514e'), v('--series-1', '#2a78d6')].map(rgbOf);
  return { seq, cyc };
}
function rampAt(stops, t) {
  const x = Math.min(1, Math.max(0, t)) * (stops.length - 1);
  const i = Math.min(stops.length - 2, Math.floor(x));
  const f = x - i;
  return stops[i].map((c, j) => c + (stops[i + 1][j] - c) * f);
}
const gradient = (stops) => `linear-gradient(90deg, ${stops.map((s, i) => `${cssOf(s)} ${(100 * i) / (stops.length - 1)}%`).join(', ')})`;

const luminance = (rgb) => (0.2126 * ((rgb >> 16) & 255) + 0.7152 * ((rgb >> 8) & 255) + 0.0722 * (rgb & 255)) / 255;

// "1, 4-6" from [0, 3, 4, 5] (clusters are numbered from 1, as in the job parameters).
export function clusterList(ids) {
  const sorted = [...new Set(ids)].sort((a, b) => a - b).map((i) => i + 1);
  const parts = [];
  for (let i = 0; i < sorted.length; i++) {
    let j = i;
    while (j + 1 < sorted.length && sorted[j + 1] === sorted[j] + 1) j++;
    parts.push(j - i >= 2 ? `${sorted[i]}-${sorted[j]}` : sorted.slice(i, j + 1).join(', '));
    i = j;
  }
  return parts.join(', ');
}

function quantile(values, q) {
  const s = Float32Array.from(values.filter((v) => v !== null && Number.isFinite(v))).sort();
  if (!s.length) return 0;
  return s[Math.min(s.length - 1, Math.max(0, Math.round(q * (s.length - 1))))];
}

function insidePolygon(x, y, poly) {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i];
    const [xj, yj] = poly[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

function sample(list, count) {
  const a = [...list];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a.slice(0, count).sort((x, y) => x - y);
}

const fmtValue = (v, unit) => (v === null || v === undefined || !Number.isFinite(v) ? '–'
  : `${Math.abs(v) >= 100 ? v.toFixed(0) : Math.abs(v) >= 10 ? v.toFixed(1) : v.toFixed(2)}${unit ? ` ${unit}` : ''}`);

// Explorers repaint when the theme changes (their colours come from the CSS variables).
const painters = new Set();
function repaintAll() {
  for (const p of [...painters]) p();
}
new MutationObserver(repaintAll).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
window.matchMedia?.('(prefers-color-scheme: dark)').addEventListener?.('change', repaintAll);

/**
 * sec: report section {title, path}; opts: {url, jobUid, openViewer(items), newJob(type, params) or null,
 * method ('cryoDRGN', '3DVA'), extract (the cluster volumes can be extracted as maps), fileUrl(path),
 * particleImages(ids) -> URL of a contact sheet of these particles, or null}.
 */
export function latentExplorer(sec, opts) {
  const root = h('div', { class: 'box lx' }, h('div', { class: 'muted small' }, 'Loading the latent space…'));
  fetch(opts.url)
    .then((r) => { if (!r.ok) throw new Error(`${r.status} ${r.statusText}`); return r.json(); })
    .then((data) => build(root, data, opts))
    .catch((err) => root.replaceChildren(h('div', { class: 'muted small' }, `Latent space not available: ${err.message}`)));
  return root;
}

function build(root, data, opts) {
  const keys = Object.keys(data.embeddings || {});
  if (!keys.length) { root.replaceChildren(h('div', { class: 'muted small' }, 'No coordinates to show')); return; }
  const clusters = data.clusters || [];
  const labels = data.labels || null;
  const k = clusters.length;
  const n = (data.embeddings[keys[0]].x || []).length;
  const index = data.index || Array.from({ length: n }, (_, i) => i); // position of each shown point in the latent space
  const word = data.cluster_name || 'Cluster';
  const members = clusters.map(() => []);
  if (labels) labels.forEach((c, i) => { if (members[c]) members[c].push(i); });
  const colorings = data.colorings || {};
  const view = { key: keys[0], hover: null, hoverPoint: null, color: labels && k ? 'clusters' : 'density', outliers: false };
  const selected = []; // cluster ids in the order they were picked (the order of a trajectory)
  for (const c of data.selected || []) if (clusters[c]) selected.push(c);
  let region = null; // {key, poly (data coordinates), inside (shown points)}
  let pinned = null; // a particle clicked on the plot (shown point)
  let lasso = null; // polygon being drawn (canvas pixels)
  let lassoMode = false;
  let swallowClick = false; // the click that ends a lasso is not a pick
  let colors = clusterColors(k, root);
  let ramp = ramps(root);
  let frame = null; // plot transform of the current coordinates and width
  let density = null; // per point, for the current embedding
  const canAct = !!opts.newJob;

  const canvas = h('canvas', { class: 'lx-plot', role: 'img' });
  const tip = h('div', { class: 'tip', hidden: true });
  const plot = h('div', { class: 'plot lx-stage' }, canvas, tip);
  const bar = h('div', { class: 'lx-bar', 'aria-live': 'polite' });
  const scale = h('div', { class: 'lx-scale', hidden: true });
  const images = h('div', { class: 'lx-images', hidden: true });
  const seg = keys.length > 1 ? h('div', { class: 'seg', role: 'group', 'aria-label': 'Coordinates' }, keys.map((key) => h('button', {
    type: 'button', dataset: { key }, onclick: () => setKey(key),
  }, key))) : h('span', { class: 'muted small' }, keys[0]);
  const epochKeys = keys.length >= 3 && keys.every((key) => key.startsWith('Epoch ')) ? keys : null;
  let player = null;
  const playBtn = epochKeys ? btn('Play the epochs', () => togglePlay(), { cls: 'small', ic: 'play', title: 'Show the epochs one after the other' }) : null;

  const options = [];
  if (labels && k) options.push(['clusters', `${word}s`]);
  options.push(['density', 'Density of particles']);
  for (const [key, c] of Object.entries(colorings)) options.push([key, c.label]);
  const colorSel = h('select', { class: 'small', 'aria-label': 'Colour the particles by', onchange: (e) => setColor(e.target.value) },
    options.map(([v, t]) => h('option', { value: v }, t)));
  colorSel.value = view.color;
  const lassoBtn = btn('Lasso', () => { lassoMode = !lassoMode; syncTools(); }, { cls: 'small', ic: 'edit', title: 'Draw a region around particles (or drag with Shift held)' });
  const volumes = clusters.filter((c) => c.volume);
  const allBtn = volumes.length > 1 ? btn(`Play the ${volumes.length} volumes`, () => opts.openViewer([seriesItem(volumes)]),
    { cls: 'small', ic: 'play', title: `Open the ${word.toLowerCase()} volumes in the 3D viewer, as a series` }) : null;
  const note = h('span', { class: 'muted small grow' }, data.n_shown < data.n_total
    ? `${data.n_shown.toLocaleString()} of ${data.n_total.toLocaleString()} particles shown` : `${data.n_total.toLocaleString()} particles`);

  const nameOf = (c) => clusters[c].name || `${word} ${c + 1}`;
  const describe = (c) => `${nameOf(c)} · ${clusters[c].count.toLocaleString()} particles (${clusters[c].percent} %)`;
  const volItem = (c) => ({ kind: 'map', path: clusters[c].volume, label: `${opts.jobUid} ${nameOf(c)}`, color: colorInt(colors[c]) });
  function seriesItem(list) {
    const what = list.length === k ? `${word.toLowerCase()} volumes` : `${word.toLowerCase()}s ${list.map((c) => c.id + 1).join(', ')}`;
    return {
      kind: 'map', path: list[0].volume, label: `${opts.jobUid} ${what}`,
      series: list.map((c) => c.volume), labels: list.map((c) => `${c.name || `${word} ${c.id + 1}`} · ${c.percent} %`),
    };
  }

  const flagText = { weak: 'weak density', noisy: 'noisy', blurry: 'no fine detail', rare: 'rare' };
  const chips = clusters.map((c) => h('button', {
    type: 'button', class: 'lx-chip', title: `${describe(c.id)}${(c.flags || []).length ? ` · ${c.flags.map((f) => flagText[f] || f).join(', ')}` : ''}`
      + `${c.volume ? ' · double-click: open its volume' : ''}`,
    onclick: () => toggle(c.id),
    ondblclick: () => { if (c.volume) opts.openViewer([volItem(c.id)]); },
    onpointerenter: () => setHover(c.id), onpointerleave: () => setHover(null),
    onfocus: () => setHover(c.id), onblur: () => setHover(null),
  }, h('i'), h('b', {}, String(c.id + 1)), h('span', {}, `${c.percent} %`),
  (c.flags || []).some((f) => f !== 'rare') ? h('span', { class: 'lx-warn', 'aria-label': 'flagged' }, '⚠') : null));
  const legend = k ? h('div', { class: 'lx-legend', role: 'group', 'aria-label': `${word}s` }, chips) : null;

  const withImages = clusters.filter((c) => c.image);
  const tiles = withImages.map((c) => h('button', {
    type: 'button', class: 'lx-tile', title: `${describe(c.id)}${c.volume ? ' · double-click: open in 3D' : ''}`,
    onclick: () => toggle(c.id), ondblclick: () => { if (c.volume) opts.openViewer([volItem(c.id)]); },
    onpointerenter: () => setHover(c.id), onpointerleave: () => setHover(null),
  },
  h('img', { src: opts.fileUrl ? opts.fileUrl(c.image) : c.image, alt: `${nameOf(c.id)}: projections of its volume`, loading: 'lazy' }),
  h('span', { class: 'lx-tile-cap' }, h('i'), h('b', {}, String(c.id + 1)), h('span', { class: 'muted' }, `${c.percent} %`),
    ...(c.flags || []).map((f) => h('span', { class: `lx-flag ${f}` }, flagText[f] || f)))));
  const gallery = tiles.length ? h('details', { class: 'lx-gallery', open: true },
    h('summary', {}, `${word} volumes`, h('span', { class: 'muted small' }, ' — three projections each (along z, y, x), same grey scale; click to pick, double-click for 3D')),
    h('div', { class: 'lx-tiles' }, tiles)) : null;

  const head = h('div', { class: 'lx-head' }, seg, playBtn, note, allBtn);
  // the lasso only when a region can be used (new jobs, particle images)
  const tools = h('div', { class: 'lx-tools' }, h('label', { class: 'row small' }, 'Colour by', colorSel),
    canAct || opts.particleImages ? lassoBtn : null);
  root.replaceChildren(...[head, tools, plot, scale, legend, bar, images, gallery].filter(Boolean));

  function syncSeg() {
    if (keys.length > 1) seg.querySelectorAll('button').forEach((b) => b.classList.toggle('on', b.dataset.key === view.key));
  }
  function syncTools() {
    lassoBtn.classList.toggle('on', lassoMode);
    lassoBtn.setAttribute('aria-pressed', lassoMode ? 'true' : 'false');
    canvas.style.cursor = lassoMode ? 'crosshair' : 'default';
  }
  function syncLegend() {
    chips.forEach((chip, c) => {
      const on = selected.includes(c);
      chip.classList.toggle('on', on);
      chip.setAttribute('aria-pressed', on ? 'true' : 'false');
      chip.firstChild.style.background = colors[c];
    });
    tiles.forEach((tile, j) => {
      const c = withImages[j].id;
      tile.classList.toggle('on', selected.includes(c));
      tile.querySelector('.lx-tile-cap i').style.background = colors[c];
    });
  }
  function setKey(key) {
    view.key = key;
    frame = null;
    density = null;
    if (region && region.key !== key) region = null;
    syncSeg();
    paint();
    renderBar();
  }
  function setColor(key) {
    view.color = key;
    colorSel.value = key;
    paint();
  }
  function togglePlay() {
    if (player) { clearInterval(player); player = null; playBtn.lastChild.textContent = 'Play the epochs'; return; }
    playBtn.lastChild.textContent = 'Stop';
    player = setInterval(() => {
      if (!canvas.isConnected) { clearInterval(player); player = null; return; }
      setKey(epochKeys[(epochKeys.indexOf(view.key) + 1) % epochKeys.length]);
    }, 1100);
  }

  // ------------------------------------------------------------- colouring
  function densityValues() {
    if (density) return density;
    const e = data.embeddings[view.key];
    const { X, Y, width, height } = frame;
    const g = new Float32Array(BINS * BINS);
    const cell = (i) => {
      const gx = Math.min(BINS - 1, Math.max(0, Math.floor((X(e.x[i]) / width) * BINS)));
      const gy = Math.min(BINS - 1, Math.max(0, Math.floor((Y(e.y[i]) / height) * BINS)));
      return gy * BINS + gx;
    };
    for (let i = 0; i < n; i++) g[cell(i)] += 1;
    for (let pass = 0; pass < 2; pass++) { // box blur, twice
      const s = Float32Array.from(g);
      for (let y = 0; y < BINS; y++) {
        for (let x = 0; x < BINS; x++) {
          let t = 0, m = 0;
          for (let dy = -1; dy <= 1; dy++) {
            for (let dx = -1; dx <= 1; dx++) {
              const yy = y + dy, xx = x + dx;
              if (yy >= 0 && yy < BINS && xx >= 0 && xx < BINS) { t += s[yy * BINS + xx]; m += 1; }
            }
          }
          g[y * BINS + x] = t / m;
        }
      }
    }
    density = Array.from({ length: n }, (_, i) => Math.log1p(g[cell(i)]));
    return density;
  }

  // the colouring in use: {values, lo, hi, cyclic, label, unit, note} or null for the clusters
  function activeColoring() {
    if (view.color === 'clusters') return null;
    if (view.color === 'density') {
      const values = densityValues();
      return { values, lo: quantile(values, 0.02), hi: quantile(values, 0.99), label: 'Density of particles', unit: '', note: 'Crowded regions are the populated states; sparse ones, transitions or rare particles.', relative: true };
    }
    return colorings[view.color] || null;
  }

  function renderScale(c) {
    if (!c) { scale.hidden = true; return; }
    const stops = c.cyclic ? ramp.cyc : ramp.seq;
    scale.hidden = false;
    scale.replaceChildren(...[
      h('span', { class: 'muted small' }, c.relative ? 'sparse' : fmtValue(c.lo, c.unit)),
      h('span', { class: 'lx-ramp', style: { background: gradient(stops) }, role: 'img', 'aria-label': `${c.label} from ${fmtValue(c.lo, c.unit)} to ${fmtValue(c.hi, c.unit)}` }),
      h('span', { class: 'muted small' }, c.relative ? 'crowded' : fmtValue(c.hi, c.unit)),
      h('b', { class: 'small' }, c.label), c.note ? h('span', { class: 'muted small lx-note' }, c.note) : null].filter(Boolean));
  }

  // ---------------------------------------------------------------- drawing
  function transform(width, height) {
    const e = data.embeddings[view.key];
    const extra = [];
    for (const c of clusters) if (c.centre?.[view.key]) extra.push(c.centre[view.key]);
    for (const p of data.paths || []) for (const q of p.points?.[view.key] || []) extra.push(q);
    for (const m of data.marks || []) if (m.points?.[view.key]) extra.push(m.points[view.key]);
    let x0 = quantile(e.x, 0.003), x1 = quantile(e.x, 0.997), y0 = quantile(e.y, 0.003), y1 = quantile(e.y, 0.997);
    for (const [x, y] of extra) { x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y); }
    const iw = width - PAD.l - PAD.r, ih = height - PAD.t - PAD.b;
    const sc = Math.min(iw / (((x1 - x0) || 1) * 1.08), ih / (((y1 - y0) || 1) * 1.08)); // same scale on both axes
    const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
    return {
      width, height, iw, ih,
      X: (x) => PAD.l + iw / 2 + (x - cx) * sc, Y: (y) => PAD.t + ih / 2 - (y - cy) * sc,
      IX: (px) => (px - PAD.l - iw / 2) / sc + cx, IY: (py) => cy - (py - PAD.t - ih / 2) / sc,
    };
  }

  function paint() {
    if (!canvas.isConnected) { if (frame) painters.delete(paint); return; } // gone with its report
    const css = getComputedStyle(root);
    const ink = (name, fallback) => css.getPropertyValue(name).trim() || fallback;
    colors = clusterColors(k, root);
    ramp = ramps(root);
    const width = Math.max(240, Math.round(plot.clientWidth || 520));
    if (!frame || frame.width !== width) { frame = transform(width, Math.round(width * RATIO)); density = null; }
    const { X, Y, iw, ih, height } = frame;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    canvas.style.height = `${height}px`;
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    const e = data.embeddings[view.key];
    ctx.strokeStyle = ink('--axis', '#c3c2b7');
    ctx.lineWidth = 1;
    ctx.strokeRect(PAD.l + 0.5, PAD.t + 0.5, iw - 1, ih - 1);
    ctx.fillStyle = ink('--ink-3', '#898781');
    ctx.font = '11px system-ui, sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText(e.xlabel || `${view.key} 1`, PAD.l + iw / 2, height - 8);
    ctx.save();
    ctx.translate(12, PAD.t + ih / 2);
    ctx.rotate(-Math.PI / 2);
    ctx.fillText(e.ylabel || `${view.key} 2`, 0, 0);
    ctx.restore();

    ctx.save();
    ctx.beginPath();
    ctx.rect(PAD.l + 1, PAD.t + 1, iw - 2, ih - 2);
    ctx.clip();
    const r = n > 6000 ? 1.4 : n > 1500 ? 1.9 : 2.6;
    const lit = new Set(view.hover !== null ? [view.hover] : selected);
    const dot = (i) => {
      const x = X(e.x[i]);
      const y = Y(e.y[i]);
      ctx.moveTo(x + r, y);
      ctx.arc(x, y, r, 0, 2 * Math.PI);
    };
    const coloring = activeColoring();
    const faded = ink('--axis', '#c3c2b7');
    const outlier = view.outliers && colorings.znorm && data.outliers ? colorings.znorm.values : null;
    if (coloring) {
      // points binned by value, drawn low to high; picked clusters (or the region) stay bright, the rest fades
      const { values, lo, hi } = coloring;
      const stops = coloring.cyclic ? ramp.cyc : ramp.seq;
      const buckets = Array.from({ length: BINS }, () => []);
      const dim = [];
      const keep = (i) => (region ? region.insideSet.has(i) : !lit.size || (labels && lit.has(labels[i])));
      for (let i = 0; i < n; i++) {
        const v = values[i];
        if (v === null || !Number.isFinite(v) || !keep(i)) { dim.push(i); continue; }
        const t = coloring.cyclic ? (((v - lo) / ((hi - lo) || 1)) % 1 + 1) % 1 : (v - lo) / ((hi - lo) || 1);
        buckets[Math.min(BINS - 1, Math.max(0, Math.floor(t * BINS)))].push(i);
      }
      ctx.fillStyle = faded;
      ctx.globalAlpha = 0.35;
      ctx.beginPath();
      for (const i of dim) dot(i);
      ctx.fill();
      ctx.globalAlpha = 0.8;
      // density: crowded points on top; other properties: interleaved layers, so no value hides the others
      const layers = coloring.relative ? 1 : 6;
      for (let layer = 0; layer < layers; layer++) {
        buckets.forEach((list, b) => {
          if (!list.length) return;
          ctx.fillStyle = cssOf(rampAt(stops, (b + 0.5) / BINS));
          ctx.beginPath();
          for (const i of list) if (i % layers === layer) dot(i);
          ctx.fill();
        });
      }
    } else if (!labels || !k) {
      ctx.fillStyle = ink('--accent', '#2a78d6');
      ctx.globalAlpha = 0.5;
      ctx.beginPath();
      for (let i = 0; i < n; i++) dot(i);
      ctx.fill();
    } else {
      const order = [...clusters.keys()].sort((a, b) => lit.has(a) - lit.has(b)); // picked clusters on top
      for (const c of order) {
        const off = (lit.size && !lit.has(c)) || region || outlier;
        ctx.fillStyle = off ? faded : colors[c];
        ctx.globalAlpha = off ? 0.45 : 0.62;
        ctx.beginPath();
        for (const i of members[c]) dot(i);
        ctx.fill();
      }
      if (region) { // the particles of the region keep their cluster colours
        for (const c of clusters.keys()) {
          ctx.fillStyle = colors[c];
          ctx.globalAlpha = 0.75;
          ctx.beginPath();
          for (const i of members[c]) if (region.insideSet.has(i)) dot(i);
          ctx.fill();
        }
      }
    }
    if (outlier) { // particles beyond the outlier threshold, on top
      ctx.globalAlpha = 0.95;
      ctx.fillStyle = ink('--series-8', '#e34948');
      ctx.beginPath();
      for (let i = 0; i < n; i++) if (outlier[i] > data.outliers.threshold) dot(i);
      ctx.fill();
    }
    ctx.globalAlpha = 1;
    // trajectories
    for (const p of data.paths || []) {
      const pts = p.points?.[view.key] || [];
      if (pts.length < 2) continue;
      ctx.strokeStyle = ink('--ink-1', '#0b0b0b');
      ctx.lineWidth = 2;
      ctx.lineJoin = 'round';
      ctx.beginPath();
      pts.forEach(([x, y], j) => (j ? ctx.lineTo(X(x), Y(y)) : ctx.moveTo(X(x), Y(y))));
      ctx.stroke();
      ctx.fillStyle = ink('--surface-1', '#fff');
      pts.forEach(([x, y], j) => {
        ctx.beginPath();
        ctx.arc(X(x), Y(y), j === 0 || j === pts.length - 1 ? 4.5 : 2.6, 0, 2 * Math.PI);
        ctx.fill();
        ctx.stroke();
      });
      ctx.fillStyle = ink('--ink-1', '#0b0b0b');
      ctx.font = '600 11px system-ui, sans-serif';
      ctx.textAlign = 'left';
      ctx.fillText('start', X(pts[0][0]) + 7, Y(pts[0][1]) - 6);
      ctx.fillText('end', X(pts[pts.length - 1][0]) + 7, Y(pts[pts.length - 1][1]) - 6);
    }
    // particles whose volume was generated (volumes job)
    ctx.font = '600 11px system-ui, sans-serif';
    ctx.textAlign = 'left';
    for (const m of data.marks || []) {
      const at = m.points?.[view.key];
      if (!at) continue;
      const x = X(at[0]), y = Y(at[1]);
      ctx.beginPath();
      ctx.moveTo(x, y - 7); ctx.lineTo(x + 7, y); ctx.lineTo(x, y + 7); ctx.lineTo(x - 7, y); ctx.closePath();
      ctx.fillStyle = ink('--surface-1', '#fff');
      ctx.fill();
      ctx.lineWidth = 2;
      ctx.strokeStyle = ink('--ink-1', '#0b0b0b');
      ctx.stroke();
      ctx.fillStyle = ink('--ink-1', '#0b0b0b');
      ctx.fillText(`#${m.particle}`, x + 9, y - 6);
    }
    // the region of the lasso, and the one being drawn
    const outline = (pts, closed) => {
      if (pts.length < 2) return;
      ctx.beginPath();
      pts.forEach(([x, y], j) => (j ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
      if (closed) ctx.closePath();
      ctx.setLineDash([5, 4]);
      ctx.lineWidth = 1.5;
      ctx.strokeStyle = ink('--ink-1', '#0b0b0b');
      ctx.stroke();
      ctx.setLineDash([]);
    };
    if (region && region.key === view.key) outline(region.poly.map(([x, y]) => [X(x), Y(y)]), true);
    if (lasso) outline(lasso, false);
    // the particle clicked
    if (pinned !== null) {
      ctx.beginPath();
      ctx.arc(X(e.x[pinned]), Y(e.y[pinned]), 6, 0, 2 * Math.PI);
      ctx.lineWidth = 2;
      ctx.strokeStyle = ink('--ink-1', '#0b0b0b');
      ctx.stroke();
    }
    // cluster centres, numbered as in the job parameters
    ctx.font = '600 10.5px system-ui, sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    for (const c of clusters) {
      const at = c.centre?.[view.key];
      if (!at) continue;
      const on = lit.has(c.id);
      const off = (lit.size && !on) || coloring || region;
      ctx.globalAlpha = lit.size && !on ? 0.55 : 1;
      ctx.beginPath();
      ctx.arc(X(at[0]), Y(at[1]), on ? 10.5 : 9, 0, 2 * Math.PI);
      ctx.fillStyle = off && !on ? ink('--surface-3', '#e6e5e0') : colors[c.id];
      ctx.fill();
      ctx.lineWidth = on ? 2.5 : 1.5;
      ctx.strokeStyle = on ? ink('--ink-1', '#0b0b0b') : ink('--surface-1', '#fff');
      ctx.stroke();
      ctx.fillStyle = off && !on ? ink('--ink-2', '#52514e') : (luminance(colorInt(colors[c.id])) > 0.6 ? '#111' : '#fff');
      ctx.fillText(String(c.id + 1), X(at[0]), Y(at[1]) + 0.5);
    }
    ctx.restore();
    canvas.setAttribute('aria-label', `${data.n_shown.toLocaleString()} particles in ${view.key} coordinates${k ? `, ${k} ${word.toLowerCase()}s` : ''}`
      + `${coloring ? `, coloured by ${coloring.label}` : ''}${selected.length ? `; selected: ${word.toLowerCase()}s ${clusterList(selected)}` : ''}`);
    renderScale(coloring);
    syncLegend();
  }

  // -------------------------------------------------------------- pointing
  function centreAt(px, py) {
    if (!frame || !k) return null;
    const { X, Y } = frame;
    let best = null;
    let bestD = HIT_CENTRE ** 2;
    for (const c of clusters) {
      const at = c.centre?.[view.key];
      if (!at) continue;
      const d = (X(at[0]) - px) ** 2 + (Y(at[1]) - py) ** 2;
      if (d < bestD) { bestD = d; best = c.id; }
    }
    return best;
  }
  function pointAt(px, py) {
    if (!frame) return null;
    const { X, Y } = frame;
    const e = data.embeddings[view.key];
    let best = null;
    let bestD = HIT_POINT ** 2;
    for (let i = 0; i < n; i++) {
      const d = (X(e.x[i]) - px) ** 2 + (Y(e.y[i]) - py) ** 2;
      if (d < bestD) { bestD = d; best = i; }
    }
    return best;
  }

  function setHover(c) {
    if (view.hover === c) return;
    view.hover = c;
    paint();
  }

  const at = (ev) => {
    const rect = canvas.getBoundingClientRect();
    return [ev.clientX - rect.left, ev.clientY - rect.top, rect.width];
  };
  function showTip(px, py, w, rows) {
    tip.replaceChildren(...rows.filter(Boolean));
    tip.hidden = false;
    tip.style.top = `${py + 14}px`;
    tip.style.left = px > w / 2 ? `${px - tip.offsetWidth - 12}px` : `${px + 12}px`;
  }
  canvas.addEventListener('pointermove', (ev) => {
    const [px, py, w] = at(ev);
    if (lasso) {
      const last = lasso[lasso.length - 1];
      if ((last[0] - px) ** 2 + (last[1] - py) ** 2 > 9) { lasso.push([px, py]); paint(); }
      return;
    }
    if (lassoMode || ev.shiftKey) { tip.hidden = true; canvas.style.cursor = 'crosshair'; return; }
    const c = centreAt(px, py);
    const i = c === null ? pointAt(px, py) : null;
    const cl = c !== null ? c : (i !== null && labels ? labels[i] : null);
    setHover(cl);
    canvas.style.cursor = c !== null || i !== null ? 'pointer' : 'default';
    if (c !== null) {
      showTip(px, py, w, [h('div', { class: 'tip-row' }, h('span', { class: 'lx-key', style: { background: colors[c] } }), h('b', {}, describe(c))),
        (clusters[c].flags || []).length ? h('div', { class: 'tip-x' }, `⚠ ${clusters[c].flags.map((f) => flagText[f] || f).join(', ')}`) : null,
        h('div', { class: 'tip-x' }, `${selected.includes(c) ? 'Click to unselect' : 'Click to select'}${clusters[c].volume ? ' · double-click to open its volume' : ''}`)]);
    } else if (i !== null) {
      const coloring = activeColoring();
      const rows = [h('div', { class: 'tip-row' }, cl !== null ? h('span', { class: 'lx-key', style: { background: colors[cl] } }) : null,
        h('b', {}, `Particle ${index[i]}`), cl !== null ? h('span', {}, ` · ${nameOf(cl)}`) : null)];
      if (coloring && !coloring.relative) rows.push(h('div', { class: 'tip-x' }, `${coloring.label}: ${fmtValue(coloring.values[i], coloring.unit)}`));
      rows.push(h('div', { class: 'tip-x' }, `Click: pick this particle${cl !== null ? ` and ${nameOf(cl).toLowerCase()}` : ''}`));
      showTip(px, py, w, rows);
    } else {
      tip.hidden = true;
    }
  });
  canvas.addEventListener('pointerleave', () => { tip.hidden = true; setHover(null); });
  canvas.addEventListener('pointerdown', (ev) => {
    if (!(lassoMode || ev.shiftKey) || ev.button !== 0) return;
    ev.preventDefault();
    canvas.setPointerCapture?.(ev.pointerId);
    const [px, py] = at(ev);
    lasso = [[px, py]];
    tip.hidden = true;
  });
  canvas.addEventListener('pointerup', () => {
    if (!lasso) return;
    const pts = lasso;
    lasso = null;
    lassoMode = false;
    swallowClick = true;
    syncTools();
    if (pts.length < 3) { paint(); return; }
    const step = Math.max(1, Math.ceil(pts.length / 200));
    const poly = pts.filter((_, j) => j % step === 0).map(([x, y]) => [frame.IX(x), frame.IY(y)]);
    const e = data.embeddings[view.key];
    const inside = [];
    for (let i = 0; i < n; i++) if (insidePolygon(e.x[i], e.y[i], poly)) inside.push(i);
    region = inside.length ? { key: view.key, poly, inside, insideSet: new Set(inside) } : null;
    selected.length = 0;
    pinned = null;
    images.hidden = true;
    paint();
    renderBar();
  });
  canvas.addEventListener('click', (ev) => {
    if (swallowClick) { swallowClick = false; return; }
    if (lassoMode || ev.shiftKey) return;
    const [px, py] = at(ev);
    const c = centreAt(px, py);
    if (c !== null) { toggle(c); return; }
    const i = pointAt(px, py);
    if (i === null) return;
    pinned = i;
    if (labels && !selected.includes(labels[i])) { region = null; selected.push(labels[i]); }
    paint();
    renderBar();
  });
  canvas.addEventListener('dblclick', (ev) => {
    const c = centreAt(...at(ev));
    if (c !== null && clusters[c].volume) opts.openViewer([volItem(c)]);
  });

  function toggle(c) {
    const i = selected.indexOf(c);
    if (i >= 0) selected.splice(i, 1);
    else selected.push(c);
    region = null;
    if (pinned !== null && labels && !selected.includes(labels[pinned])) pinned = null;
    paint();
    renderBar();
  }

  // ------------------------------------------------------------ particle images
  function showImages(points, caption) {
    if (!opts.particleImages || !points.length) return;
    const ids = sample(points, IMAGES).map((i) => index[i]);
    const img = h('img', { src: opts.particleImages(ids), alt: caption, class: 'lx-sheet' });
    img.addEventListener('error', () => img.replaceWith(h('div', { class: 'muted small' }, 'The particle images could not be read (are the image files still in place?).')));
    images.hidden = false;
    images.replaceChildren(h('div', { class: 'row small' }, h('b', {}, caption), h('span', { class: 'muted grow' }, ' — low-pass filtered, as stored (not inverted)'),
      points.length > IMAGES ? btn('Others', () => showImages(points, caption), { cls: 'small', ic: 'refresh', title: 'Another random set' }) : null,
      btn('Close', () => { images.hidden = true; }, { cls: 'small' })), img);
  }

  // ---------------------------------------------------------------- actions
  function renderBar() {
    const actions = [];
    let summary;
    if (region) {
      const est = Math.round((region.inside.length * data.n_total) / Math.max(1, data.n_shown));
      summary = h('span', { class: 'lx-sel' }, h('b', {}, 'Region'), ` · ${region.inside.length.toLocaleString()} particles shown`
        + `${data.n_shown < data.n_total ? ` (about ${est.toLocaleString()} in all)` : ''}`);
      const json = JSON.stringify({ embedding: region.key, polygon: region.poly.map(([x, y]) => [+x.toFixed(4), +y.toFixed(4)]) });
      if (canAct) {
        actions.push(btn('Keep region…', () => opts.newJob('select_particles', { selection: 'region', region: json, action: 'keep' }),
          { cls: 'small', ic: 'check', title: 'New particle selection keeping the particles inside the region (all of them, not only those shown)' }));
        actions.push(btn('Remove region…', () => opts.newJob('select_particles', { selection: 'region', region: json, action: 'remove' }),
          { cls: 'small', ic: 'x', title: 'New particle selection without the particles of the region' }));
        if (opts.method === 'cryoDRGN') {
          actions.push(btn('Volume of the region…', () => opts.newJob('cryodrgn_volumes', { selection: 'region', region: json, volumes: 1 }),
            { cls: 'small', ic: 'map', title: 'The volume of the particle at the centre of the region (or several, spread over it)' }));
        }
      }
      if (opts.particleImages) actions.push(btn('Particle images', () => showImages(region.inside, 'Particles of the region'), { cls: 'small', ic: 'grid' }));
      actions.push(btn('Clear', () => { region = null; paint(); renderBar(); }, { cls: 'small' }));
    } else if (selected.length) {
      const count = selected.reduce((t, c) => t + clusters[c].count, 0);
      const pct = selected.reduce((t, c) => t + clusters[c].percent, 0);
      const list = clusterList(selected);
      const picked = selected.map((c) => clusters[c]);
      summary = h('span', { class: 'lx-sel' }, h('b', {}, `${word}${selected.length > 1 ? 's' : ''} ${selected.map((c) => c + 1).join(', ')}`),
        ` · ${count.toLocaleString()} particles (${pct.toFixed(1)} %)`);
      if (picked.every((c) => c.volume)) {
        actions.push(btn(picked.length > 1 ? 'Play in 3D' : 'View in 3D',
          () => opts.openViewer([picked.length > 1 ? seriesItem(picked) : volItem(picked[0].id)]),
          { cls: 'small primary', ic: picked.length > 1 ? 'play' : 'eye', title: picked.length > 1 ? 'Their volumes in the order picked, as a series' : 'Open the volume' }));
      }
      if (canAct) {
        actions.push(btn('Keep…', () => opts.newJob('select_particles', { selection: 'clusters', clusters: list, action: 'keep' }),
          { cls: 'small', ic: 'check', title: 'New particle selection keeping these particles (opens the job builder)' }));
        actions.push(btn('Remove…', () => opts.newJob('select_particles', { selection: 'clusters', clusters: list, action: 'remove' }),
          { cls: 'small', ic: 'x', title: 'New particle selection without these particles (junk, unwanted state)' }));
        if (opts.method === 'cryoDRGN' && selected.length >= 2) {
          actions.push(btn('Trajectory…', () => opts.newJob('cryodrgn_trajectory', { clusters: selected.map((c) => c + 1).join(', ') }),
            { cls: 'small', ic: 'next', title: 'Volumes along a path through these clusters, in the order picked' }));
        }
        if (opts.extract && selected.length === 1 && picked[0].volume) {
          actions.push(btn('Extract map…', () => opts.newJob('extract_volume', { frame: selected[0] + 1 }),
            { cls: 'small', ic: 'map', title: 'This volume as a map (model building, refinement)' }));
        }
      }
      if (opts.particleImages && labels) {
        const pts = [];
        labels.forEach((c, i) => { if (selected.includes(c)) pts.push(i); });
        actions.push(btn('Particle images', () => showImages(pts, `Particles of ${word.toLowerCase()}${selected.length > 1 ? 's' : ''} ${list}`), { cls: 'small', ic: 'grid' }));
      }
      actions.push(btn('Clear', () => { selected.length = 0; pinned = null; paint(); renderBar(); }, { cls: 'small' }));
    } else {
      summary = h('span', { class: 'muted small' },
        `${k ? `Pick ${word.toLowerCase()}s on the plot, in the legend${tiles.length ? ' or in the gallery' : ''}` : 'Point at the particles'}`
        + `${canAct ? '; draw a region with the lasso' : ''}. ${volumes.length ? 'Double-click a centre to open its volume.' : ''}`);
      if (data.outliers?.count && colorings.znorm) {
        actions.push(btn(view.outliers ? 'Hide outliers' : `Outliers (${data.outliers.count.toLocaleString()})`, () => {
          view.outliers = !view.outliers;
          paint();
          renderBar();
        }, { cls: 'small', ic: 'eye', title: `Particles with ‖z‖ above ${data.outliers.threshold} (mean + ${data.outliers.zscore} SD): far from the others, often junk` }));
        if (canAct) {
          actions.push(btn('Remove outliers…', () => opts.newJob('select_particles', { selection: 'outliers', zscore: data.outliers.zscore, action: 'remove' }),
            { cls: 'small', ic: 'x', title: 'New particle selection without the outliers' }));
        }
      }
    }
    const rows = [h('div', { class: 'lx-bar-row' }, summary, actions.length ? h('span', { class: 'lx-actions' }, actions) : null)];
    if (pinned !== null) {
      const p = index[pinned];
      const pacts = [];
      if (canAct && opts.method === 'cryoDRGN') {
        pacts.push(btn(`Volume of particle ${p}…`, () => opts.newJob('cryodrgn_volumes', { selection: 'particles', particles: String(p) }),
          { cls: 'small', ic: 'map', title: 'Generate the volume at the latent coordinates of this particle' }));
      }
      if (opts.particleImages) pacts.push(btn('Its image', () => showImages([pinned], `Particle ${p}`), { cls: 'small', ic: 'grid' }));
      pacts.push(btn('Unpick', () => { pinned = null; paint(); renderBar(); }, { cls: 'small' }));
      rows.push(h('div', { class: 'lx-bar-row' }, h('span', { class: 'lx-sel' }, h('b', {}, `Particle ${p}`),
        labels ? ` · ${nameOf(labels[pinned])}` : ''), h('span', { class: 'lx-actions' }, pacts)));
    }
    bar.replaceChildren(...rows);
  }

  syncSeg();
  syncTools();
  renderBar();
  painters.add(paint);
  // repaint on width changes, after the layout settles (painting sets the height, which would loop)
  new ResizeObserver(() => requestAnimationFrame(() => { if (!frame || Math.round(plot.clientWidth) !== frame.width) paint(); })).observe(plot);
}
