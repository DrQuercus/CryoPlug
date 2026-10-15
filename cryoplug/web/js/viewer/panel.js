// Models panel (ChimeraX-like): one card per map or model with visibility, colour and display settings.
// Maps get ChimeraX's Volume Viewer histogram: drag the line to set the contour level (absolute or σ).
import { clear, h, icon, popupMenu } from '../ui.js';
import { binning, detailChoices, fmtBytes, fmtDims, lowpassChoices, MAP_BOXES, SERIES_BOXES } from './mapdata.js';
import { hasSurface, RESOLUTION_COLORS, voxelAxes } from './scene.js';
import { enclosed, fmtLevel, fromSigma, toSigma } from './stats.js';

export const hex = (c) => `#${(c >>> 0).toString(16).padStart(6, '0').slice(-6)}`;
export const fromHex = (s) => parseInt(s.replace('#', ''), 16);

// Maps that colour other maps: kind -> [name in the menus, unit of the colour scale]
export const COLOR_KINDS = { locres: ['Local resolution', 'Å'], variability: ['Variability', 'σ'] };
const fmtScale = (v, unit) => v.toFixed(unit === 'σ' ? 2 : 1);

const MAP_STYLES = [['surface', 'Surface'], ['mesh', 'Mesh'], ['transparent', 'Transparent']];
const MODEL_STYLES = [['cartoon', 'Cartoon'], ['both', 'Cartoon + side chains'], ['sticks', 'Sticks'], ['spheres', 'Spheres'],
  ['surface', 'Surface'], ['cartoon-surface', 'Cartoon + surface']];
const MODEL_COLORS = [['chain', 'By chain'], ['ss', 'Secondary structure'], ['element', 'By element'],
  ['bfactor', 'B-factor'], ['rainbow', 'Rainbow (N→C)'], ['hydrophobicity', 'Hydrophobicity'], ['uniform', 'Single colour']];

export const voxelSize = (item) => Math.abs(voxelAxes(item.volume).scale[0]);
const voxelVolume = (item) => voxelAxes(item.volume).scale.reduce((a, b) => a * Math.abs(b), 1);
const fmtVolume = (v) => (v >= 1e6 ? `${(v / 1e6).toFixed(2)} M` : `${Math.round(v / 1e3)} k`);

function seg(options, current, onPick, label) {
  return h('div', { class: 'seg', role: 'group', 'aria-label': label }, options.map(([value, text]) => h('button', {
    type: 'button', class: value === current ? 'on' : '', 'aria-pressed': value === current ? 'true' : 'false',
    onclick: () => { if (value !== current) onPick(value); },
  }, text)));
}

function row(label, ...children) {
  return h('div', { class: 'vw-row' }, label ? h('span', { class: 'lbl' }, label) : null, ...children);
}

// Colour bar of the scale with its end values: best resolution or least variable (blue) to worst or most variable (red).
export function colorLegend(by) {
  const stops = RESOLUTION_COLORS.map((c, i) => `${hex(c)} ${Math.round((100 * i) / (RESOLUTION_COLORS.length - 1))}%`).join(', ');
  const u = by.unit || 'Å';
  const [lo, mid, hi] = [by.min, (by.min + by.max) / 2, by.max].map((v) => fmtScale(v, u));
  const ends = by.kind === 'variability' ? ['least variable', 'most variable'] : ['best', 'worst'];
  return h('div', { class: 'vw-legend', role: 'img', 'aria-label': `Colour scale from ${lo} ${u} (blue, ${ends[0]}) to ${hi} ${u} (red, ${ends[1]})` },
    h('div', { class: 'bar', style: { background: `linear-gradient(to right, ${stops})` } }),
    h('div', { class: 'ticks' }, h('span', {}, `${lo} ${u}`), h('span', {}, mid), h('span', {}, `${hi} ${u}`)),
    h('div', { class: 'ends' }, h('span', {}, ends[0]), h('span', {}, ends[1])));
}

export function drawHistogram(canvas, item) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth || 280;
  const ht = canvas.clientHeight || 66;
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(ht * dpr);
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const css = getComputedStyle(canvas);
  const bar = css.getPropertyValue('--axis').trim() || '#c3c2b7';
  const ink = css.getPropertyValue('--ink-1').trim() || '#0b0b0b';
  const { bins, min, max } = item.stats;
  const logs = Array.from(bins, (c) => Math.log10(1 + c));
  const top = Math.max(...logs) || 1;
  const bw = w / bins.length;
  const x = ((item.level - min) / (max - min || 1)) * w;
  for (let i = 0; i < bins.length; i++) {
    const bh = (logs[i] / top) * (ht - 8);
    ctx.fillStyle = i * bw + bw / 2 >= x ? hex(item.color) : bar;
    ctx.fillRect(i * bw, ht - bh, Math.max(1, bw - 0.6), bh);
  }
  ctx.fillStyle = ink;
  ctx.fillRect(Math.round(x) - 1, 0, 2, ht);
  ctx.beginPath();
  ctx.moveTo(x - 5, 0);
  ctx.lineTo(x + 5, 0);
  ctx.lineTo(x, 6);
  ctx.closePath();
  ctx.fill();
}

export function createPanel(root, ctl) {
  const levelViews = new Map(); // item -> function refreshing the level widgets

  function render() {
    levelViews.clear();
    const items = ctl.items();
    if (!items.length) {
      clear(root, h('div', { class: 'vw-empty' }, 'Nothing displayed. Use “Open…” to add maps or models of this project.'));
      return;
    }
    clear(root, items.map(card));
  }

  function renderItem(item) {
    const old = root.querySelector(`[data-vid="${item.id}"]`);
    if (!old) { render(); return; }
    levelViews.delete(item);
    old.replaceWith(card(item));
  }

  function card(item) {
    const isMap = item.kind === 'map';
    const head = h('div', { class: 'vw-item-head', onclick: () => ctl.setActive(item) },
      h('button', {
        class: 'icon-btn', type: 'button', title: item.visible ? 'Hide' : 'Show', 'aria-label': `${item.visible ? 'Hide' : 'Show'} #${item.id}`,
        onclick: (e) => { e.stopPropagation(); ctl.setVisible(item, !item.visible); },
      }, icon(item.visible ? 'eye' : 'eye-off')),
      h('input', {
        type: 'color', class: 'vw-color', value: hex(item.color), title: 'Colour', 'aria-label': `Colour of #${item.id}`,
        onclick: (e) => e.stopPropagation(), oninput: (e) => ctl.setColor(item, fromHex(e.target.value)),
      }),
      h('span', { class: 'vid' }, `#${item.id}`),
      h('span', { class: 'name', title: `${item.label}\n${item.path}` }, item.label),
      h('span', { class: 'kind', title: item.transform ? 'Moved in space (Move & fit)' : '' },
        `${item.kind === 'model' ? 'model' : item.series ? `series · ${item.series.frames.length}` : item.otype === 'mask' ? 'mask' : 'map'}${item.transform ? ' · moved' : ''}`),
      h('button', {
        class: 'icon-btn', type: 'button', title: 'More', 'aria-label': `More actions for #${item.id}`,
        onclick: (e) => { e.stopPropagation(); popupMenu(e.currentTarget, menuItems(item)); },
      }, icon('more')),
      h('button', {
        class: 'icon-btn', type: 'button', title: item.expanded ? 'Collapse' : 'Expand', 'aria-expanded': item.expanded ? 'true' : 'false',
        'aria-label': `${item.expanded ? 'Collapse' : 'Expand'} #${item.id}`,
        style: { transform: item.expanded ? '' : 'rotate(-90deg)' },
        onclick: (e) => { e.stopPropagation(); item.expanded = !item.expanded; renderItem(item); },
      }, icon('chevron')));
    const body = item.expanded ? h('div', { class: 'vw-item-body' }, isMap ? mapBody(item) : modelBody(item)) : null;
    return h('div', {
      class: `vw-item ${ctl.active() === item ? 'active' : ''} ${item.visible ? '' : 'off'}`, dataset: { vid: String(item.id) },
    }, head, body);
  }

  function menuItems(item) {
    const items = [{ label: 'Centre the view on it', action: () => ctl.focus(item) }];
    if (item.series) {
      items.push({ label: item.series.playing ? 'Pause' : 'Play the series', action: () => ctl.togglePlay(item) });
    } else if (item.kind === 'map') {
      items.push({ label: item.full ? 'Use the binned preview' : 'Load at full resolution', action: () => ctl.setFullRes(item, !item.full) });
    }
    if (item.kind === 'map') {
      items.push({ label: 'Show slices', action: () => ctl.showSlices(item) });
      items.push({ label: 'Large slices beside the 3D view', action: () => ctl.showSlices(item, { enlarge: true }) });
    }
    if (!item.series) {
      items.push({ label: 'Move / fit in a map…', action: () => ctl.openMove(item) });
      if (item.transform) items.push({ label: 'Back where its file puts it', action: () => ctl.resetPositions([item]) });
    }
    items.push({ label: 'Close', action: () => ctl.close(item) });
    return items;
  }

  function mapBody(item) {
    // the statistics are read at each use: those of a series change with the volume shown
    const hist = h('canvas', {
      class: 'vw-hist', tabindex: 0, role: 'slider', 'aria-label': `Contour level of #${item.id}: drag, or use the arrow keys`,
    });
    const abs = h('input', { type: 'number', step: 'any', 'aria-label': 'Contour level',
      onchange: (e) => { const v = Number(e.target.value); if (Number.isFinite(v)) ctl.setLevel(item, v); } });
    const sig = h('input', { type: 'number', step: '0.1', 'aria-label': 'Contour level in σ',
      onchange: (e) => { const v = Number(e.target.value); if (Number.isFinite(v)) ctl.setLevel(item, fromSigma(item.stats, v)); } });
    const meta = h('div', { class: 'vw-meta' });
    const mass = h('input', { type: 'number', min: 0, step: 'any', style: { width: '74px' },
      'aria-label': 'Protein mass enclosed by the surface (kDa): type one to set the level',
      title: 'Mass of protein the surface encloses (1.21 Å³ per dalton); type a mass to set the level',
      onchange: (e) => { const v = Number(e.target.value); if (v > 0) ctl.setLevelByMass(item, v); } });
    const volume = h('span', { class: 'unit' });
    const scale = [h('span'), h('span'), h('span')];
    const frameInfo = item.series ? h('span', { class: 'vw-frame' }) : null;
    const playBtn = item.series ? h('button', { type: 'button', class: 'icon-btn', onclick: () => ctl.togglePlay(item) }) : null;
    const refresh = () => {
      const st = item.stats;
      if (document.activeElement !== abs) abs.value = fmtLevel(item.level);
      if (document.activeElement !== sig) sig.value = toSigma(st, item.level).toFixed(2);
      hist.setAttribute('aria-valuemin', String(st.min));
      hist.setAttribute('aria-valuemax', String(st.max));
      hist.setAttribute('aria-valuenow', String(item.level));
      hist.setAttribute('aria-valuetext', `${fmtLevel(item.level)} (${toSigma(st, item.level).toFixed(2)} σ)`);
      scale[0].textContent = fmtLevel(st.min);
      scale[1].textContent = `mean ${fmtLevel(st.mean)} · σ ${fmtLevel(st.sigma)}`;
      scale[2].textContent = fmtLevel(st.max);
      const s = item.series;
      if (frameInfo) {
        const n = s.frames.length;
        frameInfo.textContent = `${s.index + 1}/${n} · ${s.frames[s.index].label}${s.loaded < n ? ` · loading ${s.loaded}/${n}` : ''}`;
        const what = `${s.playing ? 'Pause' : 'Play'} #${item.id} (space)`;
        if (playBtn.dataset.state !== String(s.playing)) {
          playBtn.dataset.state = String(s.playing);
          clear(playBtn, icon(s.playing ? 'pause' : 'play'));
          playBtn.title = what;
          playBtn.setAttribute('aria-label', what);
        }
      }
      const vox = voxelSize(item);
      const inside = enclosed(item, voxelVolume(item));
      if (document.activeElement !== mass) mass.value = inside.kda >= 100 ? inside.kda.toFixed(0) : inside.kda.toPrecision(3);
      volume.textContent = `kDa · ${fmtVolume(inside.volume)} Å³`;
      meta.textContent = `${(st.fractionAbove(item.level) * 100).toFixed(2)} % of the voxels above the level · `
        + `${fmtDims(item.volume.grid.cells.space.dimensions)} voxels of ${vox.toFixed(2)} Å (details down to ${(2 * vox).toFixed(1)} Å)`
        + `${item.lowpass ? ` · low-pass ${item.lowpass} Å` : ''}${item.zone ? ` · zone ${item.zone.radius} Å around #${item.zone.model}` : ''}`
        + `${s ? ' · same level for every volume of the series' : ''}`;
      if (hist.isConnected) drawHistogram(hist, item);
    };
    levelViews.set(item, refresh);
    const valueAt = (clientX) => {
      const r = hist.getBoundingClientRect();
      const st = item.stats;
      return st.min + Math.min(1, Math.max(0, (clientX - r.left) / r.width)) * (st.max - st.min);
    };
    hist.addEventListener('pointerdown', (e) => { hist.setPointerCapture(e.pointerId); ctl.setActive(item); ctl.setLevel(item, valueAt(e.clientX)); });
    hist.addEventListener('pointermove', (e) => { if (hist.hasPointerCapture(e.pointerId)) ctl.setLevel(item, valueAt(e.clientX)); });
    hist.addEventListener('keydown', (e) => {
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
      e.preventDefault();
      ctl.stepLevel(item, (e.key === 'ArrowRight' ? 1 : -1) * (e.shiftKey ? 0.5 : 0.1));
    });
    requestAnimationFrame(refresh);

    const parts = [
      frameInfo ? row('Volume', frameInfo, playBtn) : null,
      hist,
      h('div', { class: 'vw-hist-scale' }, scale),
      row('Level', abs, sig, h('span', { class: 'unit' }, 'σ')),
      row('Encloses', mass, volume),
      row('Style', seg(MAP_STYLES, item.style, (v) => ctl.setStyle(item, v), 'Map style')),
      row('Opacity', h('input', {
        type: 'range', min: 0.05, max: 0.95, step: 0.05, value: item.opacity, disabled: item.style !== 'transparent',
        'aria-label': 'Opacity of the transparent surface', oninput: (e) => ctl.setOpacity(item, Number(e.target.value)),
      })),
    ];
    const sources = ctl.colorSources();
    if (item.otype !== 'mask' && (sources.length || item.colorBy)) {
      const known = sources.some((s) => s.path === item.colorBy?.path);
      const name = (s) => `${COLOR_KINDS[s.kind || 'locres'][0]} · ${s.label}`;
      parts.push(row('Colour', h('select', {
        'aria-label': 'Map colouring',
        onchange: (e) => ctl.setColorBy(item, e.target.value ? sources.find((s) => s.path === e.target.value) : null),
      }, h('option', { value: '' }, 'Single colour'),
      sources.map((s) => h('option', { value: s.path, selected: item.colorBy?.path === s.path }, name(s))),
      item.colorBy && !known ? h('option', { value: item.colorBy.path, selected: true }, name(item.colorBy)) : null)));
      if (item.colorBy) {
        const by = item.colorBy;
        const u = by.unit || 'Å';
        const what = by.kind === 'variability' ? ['Lowest variability', 'Highest variability'] : ['Best resolution', 'Worst resolution'];
        const bound = (value, label) => h('input', { type: 'number', step: u === 'σ' ? 0.01 : 0.1, min: 0, value: fmtScale(value, u), 'aria-label': label, style: { width: '64px' } });
        const lo = bound(by.min, `${what[0]} of the colour scale (${u})`);
        const hi = bound(by.max, `${what[1]} of the colour scale (${u})`);
        const apply = () => {
          const a = Number(lo.value);
          const b = Number(hi.value);
          if (Number.isFinite(a) && Number.isFinite(b) && b > a) ctl.setColorRange(item, a, b);
        };
        lo.addEventListener('change', apply);
        hi.addEventListener('change', apply);
        parts.push(row('Scale', lo, h('span', { class: 'unit' }, 'to'), hi, h('span', { class: 'unit' }, u)), colorLegend(by));
      }
    }
    const models = item.series ? [] : ctl.items().filter((i) => i.kind === 'model');
    if (models.length) {
      const pick = h('select', { 'aria-label': 'Model for the zone' }, models.map((m) => h('option', {
        value: String(m.id), selected: (item.zone ? item.zone.model : models[0].id) === m.id }, `#${m.id} ${m.label}`)));
      const radius = h('input', { type: 'number', min: 0.5, max: 30, step: 0.5, value: item.zone ? item.zone.radius : 3, 'aria-label': 'Zone radius (Å)', style: { width: '58px' } });
      const on = h('input', { type: 'checkbox', checked: !!item.zone, 'aria-label': 'Show the map only near the model' });
      const apply = () => ctl.setZone(item, on.checked ? { model: Number(pick.value), radius: Number(radius.value) || 3 } : null);
      on.addEventListener('change', apply);
      pick.addEventListener('change', () => { if (on.checked) apply(); });
      radius.addEventListener('change', () => { if (on.checked) apply(); });
      parts.push(row('Zone', on, pick, radius, h('span', { class: 'unit' }, 'Å')));
    }
    parts.push(detailRow(item), filterRow(item), meta);
    return parts.filter(Boolean);
  }

  // How finely the map is shown: one choice per binning the server can apply (series: memory for all the volumes).
  function detailRow(item) {
    if (!item.info?.box) return null;
    const frames = item.series ? item.series.frames.length : 0;
    const choices = detailChoices(item.info, frames ? SERIES_BOXES : MAP_BOXES);
    const now = binning(item.info, item.box);
    const label = (c) => `${fmtDims(c.dims)} · ${c.voxel.toFixed(2)} Å${c.factor > 1 ? ` (${c.factor}× binned)` : ' (full)'}`
      + (frames ? ` · ${fmtBytes(c.voxels * 4 * frames)} for ${frames}` : ` · ${fmtBytes(c.voxels * 2)}`);
    if (choices.length < 2) return row('Detail', h('span', { class: 'vw-static' }, label(choices[0])));
    return row('Detail', h('select', {
      'aria-label': 'Detail: voxels shown (binning)', title: 'Finer: sharper surfaces, slower to load',
      onchange: (e) => ctl.setDetail(item, Number(e.target.value)),
    }, choices.map((c) => h('option', { value: String(c.box), selected: c.factor === now }, label(c)))));
  }

  // Low-pass filter applied by the server: the map as seen at a lower resolution (flexible parts, weak density).
  function filterRow(item) {
    if (!item.volume) return null;
    const options = lowpassChoices(voxelSize(item));
    if (item.lowpass && !options.includes(item.lowpass)) options.push(item.lowpass);
    if (options.length < 2) return null;
    return row('Filter', h('select', {
      'aria-label': 'Low-pass filter', title: 'Show the map at a lower resolution: smoother, flexible regions easier to see',
      onchange: (e) => ctl.setLowpass(item, Number(e.target.value)),
    }, options.map((d) => h('option', { value: String(d), selected: d === (item.lowpass || 0) }, d ? `Low-pass ${d} Å` : 'None (as computed)'))));
  }

  function modelBody(item) {
    const goto = h('input', { type: 'text', placeholder: 'A:45 or A', 'aria-label': 'Go to residue (chain:number)', style: { flex: '1' },
      onkeydown: (e) => { if (e.key === 'Enter') ctl.goTo(item, e.target.value); } });
    const parts = [
      row('Style', h('select', { 'aria-label': 'Model style', onchange: (e) => ctl.setDisplay(item, e.target.value) },
        MODEL_STYLES.map(([v, t]) => h('option', { value: v, selected: v === item.display }, t)))),
      row('Colour', h('select', { 'aria-label': 'Model colouring', onchange: (e) => ctl.setColorMode(item, e.target.value) },
        MODEL_COLORS.map(([v, t]) => h('option', { value: v, selected: v === item.colorMode }, t)))),
      hasSurface(item.display) ? row('Opacity', h('input', {
        type: 'range', min: 0.1, max: 1, step: 0.05, value: item.surfaceOpacity ?? (item.display === 'surface' ? 1 : 0.35),
        'aria-label': 'Opacity of the molecular surface', oninput: (e) => ctl.setSurfaceOpacity(item, Number(e.target.value)),
      })) : null,
      row('Go to', goto),
    ].filter(Boolean);
    if (item.refs.components.water) {
      parts.push(row('', h('label', { class: 'chk' }, h('input', { type: 'checkbox', checked: item.waters, onchange: (e) => ctl.setWaters(item, e.target.checked) }), 'Show waters')));
    }
    parts.push(h('div', { class: 'vw-meta' }, `${item.structure.elementCount.toLocaleString()} atoms · click an atom in the view to centre on it`));
    return parts;
  }

  return {
    render,
    renderItem,
    refreshLevel(item) { levelViews.get(item)?.(); },
    // a colour picked in the swatch switches a model to "Single colour" without rebuilding its card
    // (that would close the colour picker the user is still dragging in)
    syncColorMode(item) {
      const select = root.querySelector(`[data-vid="${item.id}"] select[aria-label="Model colouring"]`);
      if (select) select.value = item.colorMode;
    },
    // the opacity slider of a model surface follows a command without rebuilding the card (nor stopping a drag)
    syncSurfaceOpacity(item) {
      const input = root.querySelector(`[data-vid="${item.id}"] input[aria-label="Opacity of the molecular surface"]`);
      if (input && document.activeElement !== input) input.value = String(item.surfaceOpacity);
    },
    // selection only restyles the cards: rebuilding them would interrupt a drag on a histogram
    markActive() {
      const id = String(ctl.active()?.id);
      root.querySelectorAll('.vw-item').forEach((el) => el.classList.toggle('active', el.dataset.vid === id));
    },
  };
}
