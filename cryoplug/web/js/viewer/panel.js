// Models panel (ChimeraX-like): one card per map or model with visibility, colour and display settings.
// Maps get ChimeraX's Volume Viewer histogram: drag the line to set the contour level (absolute or σ).
import { clear, h, icon, popupMenu } from '../ui.js';
import { fmtLevel, fromSigma, toSigma } from './stats.js';

export const hex = (c) => `#${(c >>> 0).toString(16).padStart(6, '0').slice(-6)}`;
export const fromHex = (s) => parseInt(s.replace('#', ''), 16);

const MAP_STYLES = [['surface', 'Surface'], ['mesh', 'Mesh'], ['transparent', 'Transparent']];
const MODEL_STYLES = [['cartoon', 'Cartoon'], ['both', 'Cartoon + side chains'], ['sticks', 'Sticks']];
const MODEL_COLORS = [['chain', 'By chain'], ['ss', 'Secondary structure'], ['element', 'By element'],
  ['bfactor', 'B-factor'], ['rainbow', 'Rainbow (N→C)'], ['uniform', 'Single colour']];

function seg(options, current, onPick, label) {
  return h('div', { class: 'seg', role: 'group', 'aria-label': label }, options.map(([value, text]) => h('button', {
    type: 'button', class: value === current ? 'on' : '', 'aria-pressed': value === current ? 'true' : 'false',
    onclick: () => { if (value !== current) onPick(value); },
  }, text)));
}

function row(label, ...children) {
  return h('div', { class: 'vw-row' }, label ? h('span', { class: 'lbl' }, label) : null, ...children);
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
      h('span', { class: 'kind' }, item.kind === 'model' ? 'model' : item.otype === 'mask' ? 'mask' : 'map'),
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
    if (item.kind === 'map') {
      items.push({ label: item.full ? 'Use the binned preview' : 'Load at full resolution', action: () => ctl.setFullRes(item, !item.full) });
      items.push({ label: 'Show slices', action: () => ctl.showSlices(item) });
    }
    items.push({ label: 'Close', action: () => ctl.close(item) });
    return items;
  }

  function mapBody(item) {
    const st = item.stats;
    const hist = h('canvas', {
      class: 'vw-hist', tabindex: 0, role: 'slider', 'aria-label': `Contour level of #${item.id}: drag, or use the arrow keys`,
      'aria-valuemin': String(st.min), 'aria-valuemax': String(st.max),
    });
    const abs = h('input', { type: 'number', step: 'any', 'aria-label': 'Contour level',
      onchange: (e) => { const v = Number(e.target.value); if (Number.isFinite(v)) ctl.setLevel(item, v); } });
    const sig = h('input', { type: 'number', step: '0.1', 'aria-label': 'Contour level in σ',
      onchange: (e) => { const v = Number(e.target.value); if (Number.isFinite(v)) ctl.setLevel(item, fromSigma(st, v)); } });
    const enclosed = h('div', { class: 'vw-meta' });
    const refresh = () => {
      if (document.activeElement !== abs) abs.value = fmtLevel(item.level);
      if (document.activeElement !== sig) sig.value = toSigma(st, item.level).toFixed(2);
      hist.setAttribute('aria-valuenow', String(item.level));
      hist.setAttribute('aria-valuetext', `${fmtLevel(item.level)} (${toSigma(st, item.level).toFixed(2)} σ)`);
      enclosed.textContent = `${(st.fractionAbove(item.level) * 100).toFixed(2)} % of the voxels above the level · `
        + `${item.volume.grid.cells.space.dimensions.join('×')} voxels${item.full ? '' : ' (preview)'}${item.zone ? ` · zone ${item.zone.radius} Å around #${item.zone.model}` : ''}`;
      if (hist.isConnected) drawHistogram(hist, item);
    };
    levelViews.set(item, refresh);
    const valueAt = (clientX) => {
      const r = hist.getBoundingClientRect();
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
      hist,
      h('div', { class: 'vw-hist-scale' }, h('span', {}, fmtLevel(st.min)), h('span', {}, `mean ${fmtLevel(st.mean)} · σ ${fmtLevel(st.sigma)}`), h('span', {}, fmtLevel(st.max))),
      row('Level', abs, sig, h('span', { class: 'unit' }, 'σ')),
      row('Style', seg(MAP_STYLES, item.style, (v) => ctl.setStyle(item, v), 'Map style')),
      row('Opacity', h('input', {
        type: 'range', min: 0.05, max: 0.95, step: 0.05, value: item.opacity, disabled: item.style !== 'transparent',
        'aria-label': 'Opacity of the transparent surface', oninput: (e) => ctl.setOpacity(item, Number(e.target.value)),
      })),
    ];
    const models = ctl.items().filter((i) => i.kind === 'model');
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
    parts.push(enclosed);
    return parts;
  }

  function modelBody(item) {
    const goto = h('input', { type: 'text', placeholder: 'A:45 or A', 'aria-label': 'Go to residue (chain:number)', style: { flex: '1' },
      onkeydown: (e) => { if (e.key === 'Enter') ctl.goTo(item, e.target.value); } });
    const parts = [
      row('Style', h('select', { 'aria-label': 'Model style', onchange: (e) => ctl.setDisplay(item, e.target.value) },
        MODEL_STYLES.map(([v, t]) => h('option', { value: v, selected: v === item.display }, t)))),
      row('Colour', h('select', { 'aria-label': 'Model colouring', onchange: (e) => ctl.setColorMode(item, e.target.value) },
        MODEL_COLORS.map(([v, t]) => h('option', { value: v, selected: v === item.colorMode }, t)))),
      row('Go to', goto),
    ];
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
    // selection only restyles the cards: rebuilding them would interrupt a drag on a histogram
    markActive() {
      const id = String(ctl.active()?.id);
      root.querySelectorAll('.vw-item').forEach((el) => el.classList.toggle('active', el.dataset.vid === id));
    },
  };
}
