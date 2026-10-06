// Dependency-free SVG line charts (FSC curves, per-residue Q-scores...).
// Fixed categorical order, 2px lines, hairline grid, legend for >= 2 series,
// crosshair tooltip listing every series, keyboard support and a table view.
import { h, s } from './ui.js';

const MAX_SERIES = 8;
const RES_TICKS = [50, 30, 20, 15, 10, 8, 6, 5, 4.5, 4, 3.5, 3, 2.8, 2.5, 2.2, 2, 1.8, 1.6, 1.5, 1.4, 1.2, 1.1, 1];

function niceTicks(min, max, count = 5) {
  if (!(max > min)) return [min];
  const span = max - min;
  const step0 = span / count;
  const mag = 10 ** Math.floor(Math.log10(step0));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((st) => span / st <= count + 0.5) || step0;
  const ticks = [];
  for (let v = Math.ceil(min / step) * step; v <= max + step * 1e-6; v += step) ticks.push(+v.toFixed(10));
  return ticks;
}

function nearestIndex(xs, x) {
  let lo = 0, hi = xs.length - 1;
  if (hi < 0) return -1;
  const asc = xs[hi] >= xs[0];
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if ((xs[mid] < x) === asc) lo = mid; else hi = mid;
  }
  return Math.abs(xs[lo] - x) <= Math.abs(xs[hi] - x) ? lo : hi;
}

function fmtVal(v) {
  if (v === null || v === undefined || Number.isNaN(v)) return '–';
  return Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(3);
}

export function lineChart(section) {
  const isRes = section.x_kind === 'resolution';
  const all = (section.series || []).filter((sr) => sr.x && sr.x.length);
  const series = all.slice(0, MAX_SERIES);
  const folded = all.length - series.length;
  const W = 560, H = 270, m = { l: 46, r: 40, t: 12, b: 40 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;

  let xmin = Infinity, xmax = -Infinity, ymin = Infinity, ymax = -Infinity;
  for (const sr of series) {
    sr.x.forEach((x, i) => {
      const y = sr.y[i];
      if (!Number.isFinite(x) || !Number.isFinite(y)) return;
      xmin = Math.min(xmin, x); xmax = Math.max(xmax, x); ymin = Math.min(ymin, y); ymax = Math.max(ymax, y);
    });
  }
  if (!Number.isFinite(xmin)) return h('div', { class: 'muted small' }, 'No data');
  if (isRes) xmin = 0;
  if (section.y_range) [ymin, ymax] = section.y_range;
  else { const pad = (ymax - ymin) * 0.06 || 1; ymin -= pad; ymax += pad; }
  const X = (x) => m.l + ((x - xmin) / (xmax - xmin || 1)) * iw;
  const Y = (y) => m.t + (1 - (y - ymin) / (ymax - ymin || 1)) * ih;
  const xLabel = (x) => (isRes ? (x > 0 ? `${(1 / x).toFixed(2)} Å` : '∞') : `${section.x_label || 'x'} ${Number.isInteger(x) ? x : x.toFixed(2)}`);

  const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': section.title || 'Chart' });
  const grid = s('g', { class: 'grid' });
  const axis = s('g', { class: 'axis' });
  for (const t of niceTicks(ymin, ymax, 5)) {
    grid.appendChild(s('line', { x1: m.l, x2: W - m.r, y1: Y(t), y2: Y(t) }));
    axis.appendChild(s('text', { x: m.l - 6, y: Y(t) + 4, 'text-anchor': 'end' }, +t.toFixed(3)));
  }
  let xticks;
  if (isRes) {
    xticks = [];
    let lastPx = -Infinity;
    for (const r of RES_TICKS) {
      const f = 1 / r;
      if (f > xmax * 1.0001 || f < xmin) continue;
      const px = X(f);
      if (px - lastPx < 38) continue;
      xticks.push(f); lastPx = px;
    }
  } else {
    xticks = niceTicks(xmin, xmax, 6);
  }
  for (const t of xticks) {
    axis.appendChild(s('line', { x1: X(t), x2: X(t), y1: H - m.b, y2: H - m.b + 4 }));
    axis.appendChild(s('text', { x: X(t), y: H - m.b + 16, 'text-anchor': 'middle' }, isRes ? (+(1 / t).toFixed(2)) : +t.toFixed(2)));
  }
  axis.appendChild(s('line', { x1: m.l, x2: W - m.r, y1: H - m.b, y2: H - m.b }));
  if (section.x_label) axis.appendChild(s('text', { x: m.l + iw / 2, y: H - 6, 'text-anchor': 'middle' }, section.x_label));
  if (section.y_label) axis.appendChild(s('text', { x: 12, y: m.t + ih / 2, transform: `rotate(-90 12 ${m.t + ih / 2})`, 'text-anchor': 'middle' }, section.y_label));
  svg.append(grid, axis);

  const ref = s('g', { class: 'ref' });
  for (const hl of section.hlines || []) {
    if (hl.y < ymin || hl.y > ymax) continue;
    ref.appendChild(s('line', { x1: m.l, x2: W - m.r, y1: Y(hl.y), y2: Y(hl.y) }));
    ref.appendChild(s('text', { x: W - m.r + 4, y: Y(hl.y) + 4 }, hl.label ?? hl.y));
  }
  svg.appendChild(ref);

  const sg = s('g', { class: 'series' });
  series.forEach((sr, i) => {
    let d = '';
    let pen = false;
    sr.x.forEach((x, k) => {
      const y = sr.y[k];
      if (!Number.isFinite(x) || !Number.isFinite(y)) { pen = false; return; }
      d += `${pen ? 'L' : 'M'}${X(x).toFixed(1)},${Y(Math.max(ymin, Math.min(ymax, y))).toFixed(1)}`;
      pen = true;
    });
    sg.appendChild(s('path', { d, stroke: `var(--series-${i + 1})` }));
  });
  svg.appendChild(sg);

  // hover layer
  const wrap = h('div', { class: 'plot' });
  const cross = s('line', { class: 'crosshair', y1: m.t, y2: H - m.b, visibility: 'hidden' });
  const dots = series.map((_, i) => s('circle', { r: 4, fill: `var(--series-${i + 1})`, stroke: 'var(--surface-1)', 'stroke-width': 2, visibility: 'hidden' }));
  svg.append(cross, ...dots);
  const tip = h('div', { class: 'tip', hidden: true });
  const base = series[0].x;
  let current = -1;
  const show = (idx) => {
    if (idx < 0) return;
    current = idx;
    const x = base[idx];
    cross.setAttribute('x1', X(x)); cross.setAttribute('x2', X(x)); cross.setAttribute('visibility', 'visible');
    tip.replaceChildren(h('div', { class: 'tip-x' }, xLabel(x)));
    series.forEach((sr, i) => {
      const k = nearestIndex(sr.x, x);
      const y = sr.y[k];
      if (Number.isFinite(y)) {
        dots[i].setAttribute('cx', X(sr.x[k])); dots[i].setAttribute('cy', Y(Math.max(ymin, Math.min(ymax, y))));
        dots[i].setAttribute('visibility', 'visible');
      }
      tip.appendChild(h('div', { class: 'tip-row' }, h('span', { class: 'key', style: { background: `var(--series-${i + 1})` } }),
        h('b', {}, fmtVal(y)), h('span', { class: 'muted' }, sr.name)));
    });
    tip.hidden = false;
    const rect = svg.getBoundingClientRect();
    const scale = rect.width / W;
    const px = X(x) * scale;
    tip.style.top = `${m.t * scale}px`;
    tip.style.left = px > rect.width / 2 ? `${px - tip.offsetWidth - 12}px` : `${px + 12}px`;
  };
  const hide = () => {
    tip.hidden = true;
    cross.setAttribute('visibility', 'hidden');
    dots.forEach((d) => d.setAttribute('visibility', 'hidden'));
  };
  const overlay = s('rect', { x: m.l, y: m.t, width: iw, height: ih, fill: 'transparent', tabindex: 0,
    onpointermove: (e) => {
      const rect = svg.getBoundingClientRect();
      const sx = ((e.clientX - rect.left) / rect.width) * W;
      const x = xmin + ((sx - m.l) / iw) * (xmax - xmin);
      show(nearestIndex(base, x));
    },
    onpointerleave: hide,
    onblur: hide,
    onkeydown: (e) => {
      if (e.key === 'ArrowRight') { show(Math.min(base.length - 1, current + 1)); e.preventDefault(); }
      if (e.key === 'ArrowLeft') { show(Math.max(0, current < 0 ? 0 : current - 1)); e.preventDefault(); }
    } });
  svg.appendChild(overlay);

  // table view
  const tableBox = h('div', { class: 'table-scroll', hidden: true });
  const toggle = h('button', { class: 'btn small', type: 'button', onclick: () => {
    if (!tableBox.childElementCount) {
      const step = Math.max(1, Math.ceil(base.length / 300));
      const rows = [];
      for (let k = 0; k < base.length; k += step) {
        const x = base[k];
        rows.push(h('tr', {}, h('td', {}, isRes ? (x > 0 ? (1 / x).toFixed(2) : '∞') : x),
          series.map((sr) => h('td', {}, fmtVal(sr.y[nearestIndex(sr.x, x)])))));
      }
      tableBox.appendChild(h('table', { class: 'data' },
        h('thead', {}, h('tr', {}, h('th', {}, isRes ? 'Resolution (Å)' : (section.x_label || 'x')), series.map((sr) => h('th', {}, sr.name)))),
        h('tbody', {}, rows)));
    }
    tableBox.hidden = !tableBox.hidden;
    toggle.textContent = tableBox.hidden ? 'Table' : 'Chart';
    svg.style.display = tableBox.hidden ? '' : 'none';
  } }, 'Table');

  const legend = series.length >= 2
    ? h('div', { class: 'legend' }, series.map((sr, i) => h('span', {}, h('i', { style: { background: `var(--series-${i + 1})` } }), sr.name)))
    : null;
  wrap.append(...[
    h('div', { class: 'plot-head' }, h('h5', {}, section.title || ''), h('span', { class: 'grow' }), toggle),
    legend,
    svg, tip, tableBox,
    folded > 0 ? h('div', { class: 'muted small' }, `+${folded} more series not drawn (see the CSV/table output).`) : null,
  ].filter(Boolean));
  return wrap;
}

// Heatmap with a quantised single-hue sequential ramp (rows = y labels), per-cell tooltip, legend and table view.
const RAMP_STEPS = 7;

export function heatmap(section) {
  const xs = section.x_labels || [];
  const ys = section.y_labels || [];
  const values = section.values || [];
  const flat = values.flat().filter((v) => Number.isFinite(v));
  const wrap = h('div', { class: 'plot heat' });
  if (!flat.length) return h('div', { class: 'muted small' }, 'No data');
  const vmin = Math.min(...flat), vmax = Math.max(...flat);
  const step = (vmax - vmin) / RAMP_STEPS || 1;
  const bin = (v) => Math.min(RAMP_STEPS - 1, Math.max(0, Math.floor((v - vmin) / step)));
  const unit = section.unit ? ` ${section.unit}` : '';
  const fmt = (v) => (Number.isFinite(v) ? `${v.toFixed(2)}${unit}` : '–');
  const W = 560, m = { l: 46, r: 8, t: 6, b: 40 };
  const cw = (W - m.l - m.r) / Math.max(1, xs.length);
  const ch = 22;
  const H = m.t + ch * ys.length + m.b;
  const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': section.title || 'Heatmap' });
  const tip = h('div', { class: 'tip', hidden: true });
  const cells = s('g', {});
  ys.forEach((yl, r) => {
    xs.forEach((xl, c) => {
      const v = values[r]?.[c];
      const rect = s('rect', {
        x: m.l + c * cw, y: m.t + r * ch, width: Math.max(1, cw), height: ch, class: 'cell',
        fill: Number.isFinite(v) ? `var(--seq-${bin(v)})` : 'var(--surface-3)',
      });
      rect.addEventListener('pointerenter', () => {
        tip.replaceChildren(h('div', { class: 'tip-row' }, h('b', {}, fmt(v))),
          h('div', { class: 'tip-x' }, `${section.x_label || 'x'} ${xl} · ${section.y_label || 'y'} ${yl}`));
        tip.hidden = false;
        const box = svg.getBoundingClientRect();
        const sc = box.width / W;
        const px = (m.l + (c + 0.5) * cw) * sc;
        tip.style.top = `${(m.t + (r + 1) * ch) * sc + 6}px`;
        tip.style.left = px > box.width / 2 ? `${px - tip.offsetWidth - 8}px` : `${px + 8}px`;
        rect.classList.add('on');
      });
      rect.addEventListener('pointerleave', () => { tip.hidden = true; rect.classList.remove('on'); });
      cells.appendChild(rect);
    });
    svg.appendChild(s('text', { x: m.l - 6, y: m.t + r * ch + ch / 2 + 4, 'text-anchor': 'end' }, yl));
  });
  svg.appendChild(cells);
  const every = Math.max(1, Math.ceil(xs.length / 8));
  xs.forEach((xl, c) => {
    if (c % every) return;
    svg.appendChild(s('text', { x: m.l + (c + 0.5) * cw, y: m.t + ys.length * ch + 15, 'text-anchor': 'middle' }, xl));
  });
  if (section.x_label) svg.appendChild(s('text', { x: m.l + (W - m.l - m.r) / 2, y: H - 6, 'text-anchor': 'middle' }, section.x_label));
  if (section.y_label) svg.appendChild(s('text', { x: 10, y: m.t + (ys.length * ch) / 2, transform: `rotate(-90 10 ${m.t + (ys.length * ch) / 2})`, 'text-anchor': 'middle' }, section.y_label));

  const legend = h('div', { class: 'heat-legend' },
    h('span', { class: 'muted small' }, fmt(vmin)),
    h('span', { class: 'ramp' }, Array.from({ length: RAMP_STEPS }, (_, i) => h('i', { style: { background: `var(--seq-${i})` },
      title: `${(vmin + i * step).toFixed(2)}–${(vmin + (i + 1) * step).toFixed(2)}${unit}` }))),
    h('span', { class: 'muted small' }, fmt(vmax)));

  const tableBox = h('div', { class: 'table-scroll', hidden: true });
  const toggle = h('button', { class: 'btn small', type: 'button', onclick: () => {
    if (!tableBox.childElementCount) {
      tableBox.appendChild(h('table', { class: 'data' },
        h('thead', {}, h('tr', {}, h('th', {}, `${section.y_label || ''} / ${section.x_label || ''}`), xs.map((x) => h('th', {}, x)))),
        h('tbody', {}, ys.map((yl, r) => h('tr', {}, h('th', {}, yl), xs.map((_, c) => h('td', {}, Number.isFinite(values[r]?.[c]) ? values[r][c].toFixed(2) : '–')))))));
    }
    tableBox.hidden = !tableBox.hidden;
    toggle.textContent = tableBox.hidden ? 'Table' : 'Chart';
    svg.style.display = tableBox.hidden ? '' : 'none';
  } }, 'Table');
  wrap.append(...[
    h('div', { class: 'plot-head' }, h('h5', {}, section.title || ''), h('span', { class: 'grow' }), toggle),
    legend, svg, tip, tableBox,
    section.note ? h('div', { class: 'muted small' }, section.note) : null,
  ].filter(Boolean));
  return wrap;
}
