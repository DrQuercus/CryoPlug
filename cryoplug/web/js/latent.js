// Latent space explorer (cryoDRGN, CryoSPARC 3D variability): a sample of the particles in 2D (UMAP, PCA or the
// variability components), coloured by latent cluster. Clusters picked on the plot or in the legend open in the
// 3D viewer (one volume, or several played as a series) or feed a new job: keep or remove their particles,
// a trajectory through them, one volume extracted as a map.
import { btn, h } from './ui.js';

const RATIO = 0.68; // plot height / width
const PAD = { l: 26, r: 10, t: 10, b: 26 };
const HIT_CENTRE = 13; // px
const HIT_POINT = 7;

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

// Categorical colours of the clusters: the chart series up to 8, evenly spread hues beyond.
function clusterColors(k, el) {
  const css = getComputedStyle(el);
  const series = Array.from({ length: 8 }, (_, i) => css.getPropertyValue(`--series-${i + 1}`).trim());
  if (k <= 8 && series.every(Boolean)) return series.slice(0, k);
  return Array.from({ length: k }, (_, i) => `hsl(${Math.round((i * 137.508 + 215) % 360)} ${[68, 58, 74][i % 3]}% ${[50, 40, 60][i % 3]}%)`);
}

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
  const s = Float32Array.from(values).sort();
  return s[Math.min(s.length - 1, Math.max(0, Math.round(q * (s.length - 1))))];
}

// Explorers repaint when the theme changes (their colours come from the CSS variables).
const painters = new Set();
function repaintAll() {
  for (const p of [...painters]) p();
}
new MutationObserver(repaintAll).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
window.matchMedia?.('(prefers-color-scheme: dark)').addEventListener?.('change', repaintAll);

/**
 * sec: report section {title, path}; opts: {url, jobUid, openViewer(items), newJob(type, params) or null,
 * method ('cryoDRGN', '3DVA'), extract (the cluster volumes can be extracted as maps)}.
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
  const members = clusters.map(() => []);
  if (labels) labels.forEach((c, i) => { if (members[c]) members[c].push(i); });
  const view = { key: keys[0], hover: null };
  const selected = []; // cluster ids in the order they were picked (the order of a trajectory)
  for (const c of data.selected || []) if (clusters[c]) selected.push(c);
  let colors = clusterColors(k, root);
  let frame = null; // plot transform of the current coordinates and width

  const canvas = h('canvas', { class: 'lx-plot', role: 'img' });
  const tip = h('div', { class: 'tip', hidden: true });
  const plot = h('div', { class: 'plot lx-stage' }, canvas, tip);
  const bar = h('div', { class: 'lx-bar', 'aria-live': 'polite' });
  const seg = keys.length > 1 ? h('div', { class: 'seg', role: 'group', 'aria-label': 'Coordinates' }, keys.map((key) => h('button', {
    type: 'button', dataset: { key }, onclick: () => { view.key = key; frame = null; syncSeg(); paint(); },
  }, key))) : h('span', { class: 'muted small' }, keys[0]);
  const volumes = clusters.filter((c) => c.volume);
  const allBtn = volumes.length > 1 ? btn(`Play the ${volumes.length} volumes`, () => opts.openViewer([seriesItem(volumes)]),
    { cls: 'small', ic: 'play', title: 'Open the cluster volumes in the 3D viewer, as a series' }) : null;
  const note = h('span', { class: 'muted small grow' }, data.n_shown < data.n_total
    ? `${data.n_shown.toLocaleString()} of ${data.n_total.toLocaleString()} particles shown` : `${data.n_total.toLocaleString()} particles`);

  const nameOf = (c) => clusters[c].name || `Cluster ${c + 1}`;
  const describe = (c) => `${nameOf(c)} · ${clusters[c].count.toLocaleString()} particles (${clusters[c].percent} %)`;
  const volItem = (c) => ({ kind: 'map', path: clusters[c].volume, label: `${opts.jobUid} ${nameOf(c)}`, color: colorInt(colors[c]) });
  function seriesItem(list) {
    const what = list.length === k ? 'cluster volumes' : `clusters ${list.map((c) => c.id + 1).join(', ')}`;
    return {
      kind: 'map', path: list[0].volume, label: `${opts.jobUid} ${what}`,
      series: list.map((c) => c.volume), labels: list.map((c) => `${c.name || `Cluster ${c.id + 1}`} · ${c.percent} %`),
    };
  }

  const chips = clusters.map((c) => h('button', {
    type: 'button', class: 'lx-chip', title: `${describe(c.id)}${c.volume ? ' · double-click: open its volume' : ''}`,
    onclick: () => toggle(c.id),
    ondblclick: () => { if (c.volume) opts.openViewer([volItem(c.id)]); },
    onpointerenter: () => setHover(c.id), onpointerleave: () => setHover(null),
    onfocus: () => setHover(c.id), onblur: () => setHover(null),
  }, h('i'), h('b', {}, String(c.id + 1)), h('span', {}, `${c.percent} %`)));
  const legend = k ? h('div', { class: 'lx-legend', role: 'group', 'aria-label': 'Clusters' }, chips) : null;
  root.replaceChildren(h('div', { class: 'lx-head' }, seg, note, allBtn), plot, legend, k ? bar : null);

  function syncSeg() {
    if (keys.length > 1) seg.querySelectorAll('button').forEach((b) => b.classList.toggle('on', b.dataset.key === view.key));
  }

  function syncLegend() {
    chips.forEach((chip, c) => {
      const on = selected.includes(c);
      chip.classList.toggle('on', on);
      chip.setAttribute('aria-pressed', on ? 'true' : 'false');
      chip.firstChild.style.background = colors[c];
    });
  }

  // ---------------------------------------------------------------- drawing
  function transform(width, height) {
    const e = data.embeddings[view.key];
    const extra = [];
    for (const c of clusters) if (c.centre?.[view.key]) extra.push(c.centre[view.key]);
    for (const p of data.paths || []) for (const q of p.points?.[view.key] || []) extra.push(q);
    let x0 = quantile(e.x, 0.003), x1 = quantile(e.x, 0.997), y0 = quantile(e.y, 0.003), y1 = quantile(e.y, 0.997);
    for (const [x, y] of extra) { x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y); }
    const iw = width - PAD.l - PAD.r, ih = height - PAD.t - PAD.b;
    const scale = Math.min(iw / (((x1 - x0) || 1) * 1.08), ih / (((y1 - y0) || 1) * 1.08)); // same scale on both axes
    const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
    return { width, height, iw, ih, X: (x) => PAD.l + iw / 2 + (x - cx) * scale, Y: (y) => PAD.t + ih / 2 - (y - cy) * scale };
  }

  function paint() {
    if (!canvas.isConnected) { if (frame) painters.delete(paint); return; } // gone with its report
    const css = getComputedStyle(root);
    const ink = (name, fallback) => css.getPropertyValue(name).trim() || fallback;
    colors = clusterColors(k, root);
    const width = Math.max(240, Math.round(plot.clientWidth || 520));
    if (!frame || frame.width !== width) frame = transform(width, Math.round(width * RATIO));
    const { X, Y, iw, ih, height } = frame;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    canvas.style.height = `${height}px`;
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    const e = data.embeddings[view.key];
    // frame and axis titles
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
    if (!labels || !k) {
      ctx.fillStyle = ink('--accent', '#2a78d6');
      ctx.globalAlpha = 0.5;
      ctx.beginPath();
      for (let i = 0; i < n; i++) dot(i);
      ctx.fill();
    } else {
      const order = [...clusters.keys()].sort((a, b) => lit.has(a) - lit.has(b)); // picked clusters on top
      for (const c of order) {
        const faded = lit.size && !lit.has(c);
        ctx.fillStyle = faded ? ink('--axis', '#c3c2b7') : colors[c];
        ctx.globalAlpha = faded ? 0.45 : 0.62;
        ctx.beginPath();
        for (const i of members[c]) dot(i);
        ctx.fill();
      }
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
    // cluster centres, numbered as in the job parameters
    ctx.font = '600 10.5px system-ui, sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    for (const c of clusters) {
      const at = c.centre?.[view.key];
      if (!at) continue;
      const on = lit.has(c.id);
      const faded = lit.size && !on;
      ctx.globalAlpha = faded ? 0.55 : 1;
      ctx.beginPath();
      ctx.arc(X(at[0]), Y(at[1]), on ? 10.5 : 9, 0, 2 * Math.PI);
      ctx.fillStyle = faded ? ink('--surface-3', '#e6e5e0') : colors[c.id];
      ctx.fill();
      ctx.lineWidth = on ? 2.5 : 1.5;
      ctx.strokeStyle = on ? ink('--ink-1', '#0b0b0b') : ink('--surface-1', '#fff');
      ctx.stroke();
      ctx.fillStyle = faded ? ink('--ink-2', '#52514e') : (luminance(colorInt(colors[c.id])) > 0.6 ? '#111' : '#fff');
      ctx.fillText(String(c.id + 1), X(at[0]), Y(at[1]) + 0.5);
    }
    ctx.restore();
    canvas.setAttribute('aria-label', `${data.n_shown.toLocaleString()} particles in ${view.key} coordinates${k ? `, ${k} clusters` : ''}`
      + `${selected.length ? `; selected: clusters ${clusterList(selected)}` : ''}`);
    syncLegend();
  }

  // -------------------------------------------------------------- pointing
  function clusterAt(px, py) {
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
    if (best !== null || !labels) return best;
    const e = data.embeddings[view.key];
    bestD = HIT_POINT ** 2;
    for (let i = 0; i < n; i++) {
      const d = (X(e.x[i]) - px) ** 2 + (Y(e.y[i]) - py) ** 2;
      if (d < bestD) { bestD = d; best = labels[i]; }
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
  canvas.addEventListener('pointermove', (ev) => {
    const [px, py, w] = at(ev);
    const c = clusterAt(px, py);
    setHover(c);
    canvas.style.cursor = c === null ? 'default' : 'pointer';
    if (c === null) { tip.hidden = true; return; }
    tip.replaceChildren(h('div', { class: 'tip-row' }, h('span', { class: 'lx-key', style: { background: colors[c] } }), h('b', {}, describe(c))),
      h('div', { class: 'tip-x' }, `${selected.includes(c) ? 'Click to unselect' : 'Click to select'}${clusters[c].volume ? ' · double-click to open its volume' : ''}`));
    tip.hidden = false;
    tip.style.top = `${py + 14}px`;
    tip.style.left = px > w / 2 ? `${px - tip.offsetWidth - 12}px` : `${px + 12}px`;
  });
  canvas.addEventListener('pointerleave', () => { tip.hidden = true; setHover(null); });
  canvas.addEventListener('click', (ev) => {
    const c = clusterAt(...at(ev));
    if (c !== null) toggle(c);
  });
  canvas.addEventListener('dblclick', (ev) => {
    const c = clusterAt(...at(ev));
    if (c !== null && clusters[c].volume) opts.openViewer([volItem(c)]);
  });

  function toggle(c) {
    const i = selected.indexOf(c);
    if (i >= 0) selected.splice(i, 1);
    else selected.push(c);
    paint();
    renderBar();
  }

  // ---------------------------------------------------------------- actions
  function renderBar() {
    if (!k) return;
    if (!selected.length) {
      bar.replaceChildren(h('span', { class: 'muted small' },
        `Pick clusters on the plot or below${volumes.length ? ' to compare their volumes in 3D' : ''}`
        + `${opts.newJob ? `${volumes.length ? ',' : ''} to keep or remove their particles` : ''}.`
        + `${volumes.length ? ' Double-click a cluster to open its volume.' : ''}`));
      return;
    }
    const count = selected.reduce((t, c) => t + clusters[c].count, 0);
    const pct = selected.reduce((t, c) => t + clusters[c].percent, 0);
    const list = clusterList(selected);
    const picked = selected.map((c) => clusters[c]);
    const actions = [];
    if (picked.every((c) => c.volume)) {
      actions.push(btn(picked.length > 1 ? 'Play in 3D' : 'View in 3D',
        () => opts.openViewer([picked.length > 1 ? seriesItem(picked) : volItem(picked[0].id)]),
        { cls: 'small primary', ic: picked.length > 1 ? 'play' : 'eye', title: picked.length > 1 ? 'Their volumes in the order picked, as a series' : 'Open the volume of this cluster' }));
    }
    if (opts.newJob) {
      actions.push(btn('Keep…', () => opts.newJob('select_particles', { clusters: list, action: 'keep' }),
        { cls: 'small', ic: 'check', title: 'New particle selection keeping these clusters (opens the job builder)' }));
      actions.push(btn('Remove…', () => opts.newJob('select_particles', { clusters: list, action: 'remove' }),
        { cls: 'small', ic: 'x', title: 'New particle selection without these clusters (junk, unwanted state)' }));
      if (opts.method === 'cryoDRGN' && selected.length >= 2) {
        actions.push(btn('Trajectory…', () => opts.newJob('cryodrgn_trajectory', { clusters: selected.map((c) => c + 1).join(', ') }),
          { cls: 'small', ic: 'next', title: 'Volumes along a path through these clusters, in the order picked' }));
      }
      if (opts.extract && selected.length === 1 && picked[0].volume) {
        actions.push(btn('Extract map…', () => opts.newJob('extract_volume', { frame: selected[0] + 1 }),
          { cls: 'small', ic: 'map', title: 'This cluster volume as a map (model building, refinement)' }));
      }
    }
    actions.push(btn('Clear', () => { selected.length = 0; paint(); renderBar(); }, { cls: 'small' }));
    bar.replaceChildren(h('span', { class: 'lx-sel' }, h('b', {}, `Cluster${selected.length > 1 ? 's' : ''} ${selected.map((c) => c + 1).join(', ')}`),
      ` · ${count.toLocaleString()} particles (${pct.toFixed(1)} %)`), h('span', { class: 'lx-actions' }, actions));
  }

  syncSeg();
  renderBar();
  painters.add(paint);
  // repaint on width changes, after the layout settles (painting sets the height, which would loop)
  new ResizeObserver(() => requestAnimationFrame(() => { if (!frame || Math.round(plot.clientWidth) !== frame.width) paint(); })).observe(plot);
}
