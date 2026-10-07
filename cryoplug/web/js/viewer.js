// CryoPlug 3D viewer: a CryoSPARC-like volume viewer with ChimeraX habits (models panel, histogram,
// command line), drawn by Mol*. Maps arrive as binned previews unless full resolution is asked for.
import { api } from './api.js';
import { clear, h, icon, modal, showError, toast } from './ui.js';
import { COMMAND_HELP, createCommands } from './viewer/commands.js';
import { createPanel } from './viewer/panel.js';
import * as scene from './viewer/scene.js';
import { createSlices } from './viewer/slices.js';
import { defaultLevel, fmtLevel, mapStatistics, toSigma } from './viewer/stats.js';

const MAP_COLORS = [0x8fb3f0, 0xf0b46e, 0x8fd3a5, 0xe899c3, 0xc3a6f0, 0xe8e27a];
const MASK_COLOR = 0x9aa0a6;
const MODEL_COLOR = 0xd8d8d8;
const BACKGROUNDS = [['Black', 0x000000], ['Grey', 0x5a5a5a], ['White', 0xffffff]];
const HINT = 'Left-drag rotate · right-drag move · wheel zoom · click an atom to centre on it · + / − level · ? help';

const V = {
  project: null, items: [], nextId: 1, active: null, mapColors: 0, full: false,
  background: 0x000000, spin: false, slab: null, ortho: false, soft: false,
  locres: [], // local resolution maps of the project: [{ path, label, range, colourMap }]
};
const $ = (id) => document.getElementById(id);
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
let panel;
let slices;
let commands;
let toolbar;

// ------------------------------------------------------------------ theme
try {
  const theme = localStorage.getItem('cryoplug.theme');
  if (theme) document.documentElement.setAttribute('data-theme', theme);
} catch { /* storage unavailable */ }

// ---------------------------------------------------------------- helpers
let loadingDepth = 0;
function loading(text) {
  loadingDepth += 1;
  $('vw-loading-text').textContent = text;
  $('vw-loading').hidden = false;
}
function loaded() {
  loadingDepth = Math.max(0, loadingDepth - 1);
  if (!loadingDepth) $('vw-loading').hidden = true;
}

function log(text, kind = '') {
  const el = $('vw-log');
  el.appendChild(h('div', { class: kind }, text));
  while (el.children.length > 40) el.firstChild.remove();
  el.scrollTop = el.scrollHeight;
}

function fail(err, what = '') {
  const msg = `${what}${err?.message || err}`;
  log(msg, 'err');
  toast(msg, 'error', 8000);
}

const byId = (id) => V.items.find((i) => i.id === id);
const activeMap = () => (V.active?.kind === 'map' ? V.active : V.items.find((i) => i.kind === 'map'));
const activeModel = () => (V.active?.kind === 'model' ? V.active : V.items.find((i) => i.kind === 'model'));

function syncHash() {
  const items = V.items.map((i) => ({
    kind: i.kind, path: i.path, label: i.label, ...(i.otype && i.otype !== 'map' ? { otype: i.otype } : {}),
    ...(i.colorBy ? { colorBy: i.colorBy.path, colorRange: [i.colorBy.min, i.colorBy.max] } : {}),
  }));
  history.replaceState(null, '', `${location.pathname}${location.search}#${encodeURIComponent(JSON.stringify({ project: V.project, items }))}`);
  const labels = V.items.map((i) => i.label);
  $('vw-title').textContent = labels.length ? `${V.project} · ${labels.join('  ·  ')}` : V.project;
  document.title = labels.length ? `${labels.join(', ')} — CryoPlug viewer` : 'CryoPlug viewer';
}

function mapUrl(item) {
  const model = item.zone && byId(item.zone.model);
  if (model) return api.zoneUrl(V.project, item.path, model.path, item.zone.radius, item.full ? 0 : 200);
  return item.full ? api.fileUrl(V.project, item.path) : api.previewUrl(V.project, item.path);
}

// ------------------------------------------------------------------ items
async function addMap(spec, withModel) {
  const otype = spec.otype || (/mask/i.test(`${spec.label || ''} ${spec.path}`) ? 'mask' : 'map');
  const item = {
    kind: 'map', otype, label: spec.label || spec.path.split('/').pop(), path: spec.path, visible: true, expanded: true,
    full: V.full, zone: null, opacity: 0.45, refs: {},
    color: otype === 'mask' ? MASK_COLOR : MAP_COLORS[V.mapColors++ % MAP_COLORS.length],
    style: otype === 'mask' ? 'transparent' : (withModel || V.items.some((i) => i.kind === 'model') ? 'mesh' : 'surface'),
  };
  loading(`Loading ${item.label}${item.full ? ' (full resolution)' : ''}…`);
  try {
    const r = await scene.loadVolume(mapUrl(item), item.label);
    item.refs = { data: r.dataRef, volume: r.volumeRef, repr: null };
    item.volume = r.volume;
    item.stats = mapStatistics(r.volume);
    item.level = defaultLevel(item);
    item.id = V.nextId++;
    V.items.push(item);
    await scene.showMap(item);
  } finally {
    loaded();
  }
  if (spec.colorBy) {
    const source = V.locres.find((s) => s.path === spec.colorBy) || { path: spec.colorBy, label: spec.colorBy.split('/').pop() };
    try {
      await applyColorBy(item, { ...source, range: spec.colorRange || source.range });
    } catch (err) {
      fail(err, 'Colouring by local resolution: ');
    }
  }
  return item;
}

// Local resolution maps of the project (outputs of type locres, or CryoSPARC map_locres of older projects).
function collectLocres(jobs) {
  const found = [];
  for (const job of [...jobs].sort((a, b) => b.num - a.num)) {
    for (const o of job.outputs || []) {
      if (o.type === 'locres' || o.name === 'map_locres') {
        found.push({ path: o.path, label: `${job.uid} ${o.label || o.name}`, range: o.meta?.display_range, colourMap: o.meta?.colour_map });
      }
    }
  }
  V.locres = found;
  return found;
}

// Colour a map by a local resolution map ({ path, label, range }), or back to a single colour (null).
async function applyColorBy(item, source) {
  const old = item.colorBy;
  if (source) {
    loading(`Loading ${source.label}…`);
    try {
      const r = await scene.loadVolume(api.previewUrl(V.project, source.path), source.label);
      const [min, max] = source.range && source.range[1] > source.range[0] ? source.range : scene.colorRange(r.volume);
      item.colorBy = { path: source.path, label: source.label, refs: { data: r.dataRef, volume: r.volumeRef }, min, max };
    } finally {
      loaded();
    }
  } else {
    item.colorBy = null;
  }
  await scene.showMap(item);
  if (old) await scene.remove(old.refs.data);
  panel.renderItem(item);
  syncHash();
}

async function addModel(spec, withMap) {
  const item = {
    kind: 'model', label: spec.label || spec.path.split('/').pop(), path: spec.path, visible: true, expanded: true, refs: {},
    color: MODEL_COLOR, colorMode: 'chain', waters: false,
    display: withMap || V.items.some((i) => i.kind === 'map') ? 'both' : 'cartoon',
  };
  loading(`Loading ${item.label}…`);
  try {
    const format = /\.(cif|mmcif)$/i.test(item.path) ? 'mmcif' : 'pdb';
    const r = await scene.loadModel(api.fileUrl(V.project, item.path), item.label, format);
    item.refs = { data: r.dataRef, structure: r.structureRef, components: r.components, reprs: [] };
    item.structure = r.structure;
    item.id = V.nextId++;
    V.items.push(item);
    await scene.showModel(item);
    return item;
  } finally {
    loaded();
  }
}

async function openItems(specs) {
  const withModel = specs.some((s) => s.kind === 'model') || V.items.some((i) => i.kind === 'model');
  const withMap = specs.some((s) => s.kind !== 'model') || V.items.some((i) => i.kind === 'map');
  const wasEmpty = !V.items.length;
  const added = [];
  // maps first: models then pick their default style knowing a map is there
  for (const spec of [...specs].sort((a, b) => (a.kind === 'model') - (b.kind === 'model'))) {
    try {
      added.push(spec.kind === 'model' ? await addModel(spec, withMap) : await addMap(spec, withModel));
    } catch (err) {
      fail(err, `Could not open ${spec.label || spec.path}: `);
    }
  }
  if (added.length) {
    if (!V.active) V.active = added.find((i) => i.kind === 'map') || added[0];
    panel.render();
    syncHash();
    if (wasEmpty) {
      // frame the model when there is one (density fit), otherwise everything
      const model = added.find((i) => i.kind === 'model');
      if (model) scene.focusItem(model);
      else scene.resetView({ whenReady: true });
    }
    log(`Opened ${added.map((i) => `#${i.id} ${i.label}`).join(', ')}`);
  }
  return added;
}

async function reloadMap(item) {
  loading(`Loading ${item.label}${item.zone ? ` (zone ${item.zone.radius} Å)` : item.full ? ' (full resolution)' : ''}…`);
  try {
    const old = item.refs.data;
    const r = await scene.loadVolume(mapUrl(item), item.label);
    item.refs = { data: r.dataRef, volume: r.volumeRef, repr: null };
    item.volume = r.volume;
    if (!item.zone) {
      item.stats = mapStatistics(r.volume);
      item.level = clamp(item.level, item.stats.min, item.stats.max);
    }
    await scene.showMap(item);
    await scene.remove(old);
  } finally {
    loaded();
  }
  panel.renderItem(item);
  if (slices.item === item) slices.show(item);
}

async function closeItem(item) {
  V.items = V.items.filter((i) => i !== item);
  if (V.active === item) V.active = V.items[0] || null;
  if (slices.item === item) hideSlices();
  await scene.remove(item.refs.data);
  if (item.colorBy) await scene.remove(item.colorBy.refs.data);
  for (const m of V.items) {
    if (m.kind === 'map' && m.zone?.model === item.id) {
      m.zone = null;
      reloadMap(m).catch((err) => fail(err));
    }
  }
  panel.render();
  syncHash();
}

// Centre on chain:residue (or a whole chain) of a model; returns an error message, or '' when done.
function goTo(model, text) {
  const m = /^\s*\/?([A-Za-z0-9]+)\s*(?::\s*(-?\d+))?\s*$/.exec(text || '');
  if (!m) return 'Write chain:residue, e.g. A:45 (or just A for a chain)';
  const loci = scene.residueLoci(model, m[1], m[2] !== undefined ? Number(m[2]) : undefined);
  if (!loci) return `/${m[1]}${m[2] !== undefined ? `:${m[2]}` : ''} not found in #${model.id}`;
  scene.centerOn(loci, { zoom: true });
  scene.select(m[2] !== undefined ? loci : null);
  return '';
}

// -------------------------------------------------------------- actions
const ctl = {
  items: () => V.items,
  byId,
  active: () => V.active,
  activeMap,
  activeModel,
  state: () => V,
  setActive(item) {
    if (V.active === item) return;
    V.active = item;
    panel.markActive();
    if (slices.item && item.kind === 'map') slices.show(item);
  },
  setVisible(item, on) {
    item.visible = on;
    scene.setHidden(item.kind === 'map' ? item.refs.repr : item.refs.structure, !on);
    panel.renderItem(item);
  },
  setColor(item, color) {
    item.color = color;
    if (item.kind === 'model' && item.colorMode !== 'uniform') {
      item.colorMode = 'uniform';
      panel.syncColorMode(item);
    }
    if (item.kind === 'map' && item.colorBy) { // a single colour replaces the local resolution colouring
      ctl.setColorBy(item, null);
      return;
    }
    scene.schedule(item, fail);
    if (item.kind === 'map') { panel.refreshLevel(item); slices.refresh(item); }
  },
  setLevel(item, value) {
    item.level = clamp(value, item.stats.min, item.stats.max);
    scene.schedule(item, fail);
    panel.refreshLevel(item);
    slices.refresh(item);
  },
  stepLevel(item, sigmas) {
    const unit = item.stats.sigma > 0 ? item.stats.sigma : (item.stats.max - item.stats.min) / 100;
    ctl.setLevel(item, item.level + sigmas * unit);
  },
  setStyle(item, style) {
    item.style = style;
    scene.schedule(item, fail);
    panel.renderItem(item);
  },
  setOpacity(item, opacity) {
    item.opacity = clamp(opacity, 0.05, 1);
    if (item.style !== 'transparent') { item.style = 'transparent'; panel.renderItem(item); }
    scene.schedule(item, fail);
  },
  setTransparency(item, percent) {
    if (percent <= 0) ctl.setStyle(item, item.style === 'transparent' ? 'surface' : item.style);
    else ctl.setOpacity(item, 1 - percent / 100);
  },
  setDisplay(item, display) {
    item.display = display;
    scene.schedule(item, fail);
    panel.renderItem(item);
  },
  setColorMode(item, mode) {
    item.colorMode = mode;
    scene.schedule(item, fail);
    panel.renderItem(item);
  },
  locresSources: () => V.locres,
  setColorBy(item, source) {
    if (source && item.colorBy?.path === source.path) { // same map: only the scale may change
      if (source.range) ctl.setColorRange(item, source.range[0], source.range[1]);
      return;
    }
    applyColorBy(item, source)
      .then(() => log(source ? `#${item.id} coloured by ${source.label} (${item.colorBy.min.toFixed(1)}–${item.colorBy.max.toFixed(1)} Å)` : `#${item.id}: single colour`))
      .catch((err) => fail(err, 'Colouring: '));
  },
  setColorRange(item, min, max) {
    if (!item.colorBy || !(max > min)) return;
    item.colorBy.min = min;
    item.colorBy.max = max;
    scene.schedule(item, fail);
    panel.renderItem(item);
    syncHash();
  },
  setWaters(item, on) {
    item.waters = on;
    scene.schedule(item, fail);
  },
  setFullRes(item, on) {
    if (item.full === on) return;
    item.full = on;
    reloadMap(item).then(() => log(`#${item.id} ${on ? 'at full resolution' : 'as a binned preview'}`)).catch((err) => fail(err));
  },
  setZone(item, zone) {
    item.zone = zone;
    reloadMap(item).then(() => log(zone ? `Zone: #${item.id} within ${zone.radius} Å of #${zone.model}` : `Zone off for #${item.id}`))
      .catch((err) => { item.zone = null; fail(err, 'Zone: '); panel.renderItem(item); });
  },
  focus: (item) => scene.focusItem(item),
  goTo(model, text) {
    const error = goTo(model, text);
    if (error) fail(error);
    else log(`Centred on /${text.trim().replace(/^\//, '')} of #${model.id}`);
    return !error;
  },
  goToQuiet: goTo,
  close: (item) => closeItem(item).catch((err) => fail(err)),
  open: () => openDialog(),
  showHelp: () => helpDialog(),
  resetView: () => scene.resetView(),
  setBackground(color) {
    V.background = color;
    scene.setBackground(color);
    const lum = 0.2126 * ((color >> 16) & 255) + 0.7152 * ((color >> 8) & 255) + 0.0722 * (color & 255);
    $('vw-stage').classList.toggle('light', lum > 140);
    toolbar.refresh();
  },
  setSlab(percent) { V.slab = percent; scene.setSlab(percent); toolbar.refresh(); },
  setSpin(on) { V.spin = on; scene.setSpin(on); toolbar.refresh(); },
  setProjection(mode) { V.ortho = mode === 'orthographic'; scene.setProjection(mode); toolbar.refresh(); },
  setSoftLighting(on) {
    if (!scene.setSoftLighting(on)) { fail('Soft lighting is not available with this graphics card'); return; }
    V.soft = on;
    toolbar.refresh();
  },
  showSlices(item) { slices.show(item); toolbar.refresh(); },
  hideSlices,
  saveImage: (name, opts) => scene.saveImage(name, opts),
};

function hideSlices() {
  slices.hide();
  toolbar.refresh();
}

// ------------------------------------------------------------- toolbar
function buildToolbar() {
  const root = $('vw-tools');
  const tool = (label, ic, title, onclick) => h('button', { type: 'button', class: 'vw-tool', title, 'aria-label': title, onclick },
    icon(ic), h('span', { class: 'lbl' }, label));
  const t = {
    reset: tool('Reset', 'refresh', 'Reset the view (R)', () => scene.resetView()),
    spin: tool('Spin', 'rotate', 'Rotate continuously (S)', () => ctl.setSpin(!V.spin)),
    slab: tool('Slab', 'layers', 'Clip the scene to a slab around the centre', () => ctl.setSlab(V.slab === null ? 30 : null)),
    ortho: tool('Ortho', 'cube', 'Orthographic projection', () => ctl.setProjection(V.ortho ? 'perspective' : 'orthographic')),
    soft: tool('Soft light', 'sun', 'Ambient occlusion (soft lighting)', () => ctl.setSoftLighting(!V.soft)),
    slices: tool('Slices', 'grid', 'Orthogonal 2D slices of the active map', () => {
      if (slices.item) hideSlices();
      else { const m = activeMap(); if (m) ctl.showSlices(m); else fail('No map displayed'); }
    }),
    image: tool('Image', 'camera', 'Save a PNG image of the view (2× the window size)', async () => {
      try { const s = await scene.saveImage('cryoplug.png'); log(`Saved cryoplug.png (${s.width}×${s.height})`); } catch (err) { fail(err); }
    }),
    help: tool('Help', 'help', 'Mouse, keys and commands (?)', () => helpDialog()),
  };
  const slab = h('input', {
    type: 'range', class: 'vw-slab', min: 3, max: 99, step: 1, value: 30, hidden: true, 'aria-label': 'Slab thickness (% of the scene)',
    oninput: (e) => ctl.setSlab(Number(e.target.value)),
  });
  const bgs = h('div', { class: 'vw-bgs', role: 'group', 'aria-label': 'Background' }, BACKGROUNDS.map(([name, color]) => h('button', {
    type: 'button', class: 'vw-bg', title: `${name} background`, 'aria-label': `${name} background`, style: { background: `#${color.toString(16).padStart(6, '0')}` },
    dataset: { color: String(color) }, onclick: () => ctl.setBackground(color),
  })));
  clear(root, t.reset, t.spin, t.slab, slab, t.ortho, t.soft, h('span', { class: 'vw-sep' }), bgs, h('span', { class: 'vw-sep' }),
    t.slices, t.image, t.help);
  return {
    refresh() {
      t.spin.classList.toggle('on', V.spin);
      t.slab.classList.toggle('on', V.slab !== null);
      slab.hidden = V.slab === null;
      if (V.slab !== null && document.activeElement !== slab) slab.value = String(V.slab);
      t.ortho.classList.toggle('on', V.ortho);
      t.soft.classList.toggle('on', V.soft);
      t.slices.classList.toggle('on', !!slices.item);
      bgs.querySelectorAll('.vw-bg').forEach((b) => b.classList.toggle('on', Number(b.dataset.color) === V.background));
    },
  };
}

// --------------------------------------------------------------- dialogs
async function openDialog() {
  let jobs;
  try { jobs = await api.jobs(V.project); } catch (err) { fail(err); return; }
  collectLocres(jobs);
  const open = new Set(V.items.map((i) => i.path));
  const rows = [];
  for (const job of [...jobs].sort((a, b) => b.num - a.num)) {
    const outs = (job.outputs || []).filter((o) => ['map', 'mask', 'half_maps', 'model', 'locres'].includes(o.type));
    if (!outs.length) continue;
    const entries = outs.flatMap((o) => (o.type === 'half_maps' ? (o.files || [o.path]).map((f, k) => ({ o, path: f, label: `${job.uid} ${o.label || o.name} ${'AB'[k] || k + 1}` }))
      : [{ o, path: o.path, label: `${job.uid} ${o.label || o.name}` }]));
    rows.push(h('div', { class: 'vw-open-job' }, h('b', {}, `${job.uid} · ${job.title}`), entries.map((e) => h('div', {
      class: 'vw-open-row', tabindex: 0, role: 'button',
      onclick: () => pick(e), onkeydown: (ev) => { if (ev.key === 'Enter') pick(e); },
    }, icon(e.o.type === 'model' ? 'model' : 'map'), h('span', {}, e.label), h('span', { class: 'p' }, e.path),
    isLocres(e.o) ? h('span', { class: 'tag' }, activeMap() ? 'colours the active map' : 'colours its map') : null,
    open.has(e.path) ? h('span', { class: 'tag' }, 'open') : null))));
  }
  const m = modal({
    title: 'Open maps and models', wide: true,
    body: rows.length ? h('div', { class: 'vw-open-list' }, rows) : h('p', { class: 'muted' }, 'No completed job has a map or a model yet.'),
  });
  function pick(e) {
    m.close();
    if (isLocres(e.o)) {
      const source = V.locres.find((s) => s.path === e.path);
      const map = activeMap();
      if (map) { ctl.setColorBy(map, source); return; }
      if (source?.colourMap) {
        openItems([{ kind: 'map', path: source.colourMap, label: source.colourMap.split('/').pop(), colorBy: source.path, colorRange: source.range }]);
        return;
      }
    }
    openItems([{ kind: e.o.type === 'model' ? 'model' : 'map', path: e.path, label: e.label, otype: e.o.type === 'mask' ? 'mask' : undefined }]);
  }
}

const isLocres = (o) => o.type === 'locres' || o.name === 'map_locres';

function helpDialog() {
  const table = (rows) => h('table', {}, h('tbody', {}, rows.map(([a, b]) => h('tr', {}, h('td', {}, a), h('td', {}, b)))));
  modal({
    title: '3D viewer — mouse, keys and commands', wide: true,
    body: h('div', { class: 'vw-help' },
      h('h4', {}, 'Mouse'),
      table([['Left drag', 'Rotate'], ['Right drag, or Ctrl + left drag', 'Move'], ['Ctrl + Shift + left drag', 'Roll around the viewing axis'],
        ['Wheel', 'Zoom'], ['Shift + wheel', 'Move the clipping planes'], ['Click an atom', 'Centre on its residue (Coot-like)'],
        ['Histogram', 'Drag the line to set the contour level of a map']]),
      h('h4', {}, 'Keys'),
      table([['+  /  −', 'Raise / lower the level of the active map by 0.1 σ (Shift: 0.5 σ)'], ['M', 'Next map style (surface, mesh, transparent)'],
        ['R', 'Reset the view'], ['S', 'Spin on / off'], ['L', 'Slices on / off'], [':', 'Go to the command line'], ['Esc', 'Clear the selection'],
        ['↑ / ↓ in the command line', 'Previous / next command']]),
      h('h4', {}, 'Commands'),
      table(COMMAND_HELP),
      h('p', { class: 'muted small' }, 'Maps open as binned previews (at most 200 voxels per side) for speed; “Load at full resolution” in a map’s menu, or fullres #1, loads the original file.')),
  });
}

// ------------------------------------------------------------- keyboard
function bindKeys() {
  document.addEventListener('keydown', (e) => {
    if (e.target.closest('input, textarea, select, [contenteditable]') || e.ctrlKey || e.metaKey || e.altKey) return;
    if (document.querySelector('.modal-backdrop')) return;
    const map = activeMap();
    const k = e.key;
    if ((k === '+' || k === '=') && map) ctl.stepLevel(map, e.shiftKey ? 0.5 : 0.1);
    else if ((k === '-' || k === '_') && map) ctl.stepLevel(map, e.shiftKey ? -0.5 : -0.1);
    else if (k === 'r' || k === 'R') scene.resetView();
    else if (k === 's' || k === 'S') ctl.setSpin(!V.spin);
    else if ((k === 'm' || k === 'M') && map) ctl.setStyle(map, { surface: 'mesh', mesh: 'transparent', transparent: 'surface' }[map.style]);
    else if (k === 'l' || k === 'L') toolbarClickSlices();
    else if (k === ':') { e.preventDefault(); $('vw-cmdline').focus(); }
    else if (k === '?') helpDialog();
    else if (k === 'Escape') scene.select(null);
    else return;
    e.preventDefault();
  });
}

function toolbarClickSlices() {
  if (slices.item) hideSlices();
  else { const m = activeMap(); if (m) ctl.showSlices(m); }
}

function bindCommandLine() {
  const input = $('vw-cmdline');
  const history = [];
  let cursor = 0;
  input.addEventListener('keydown', async (e) => {
    if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
      if (!history.length) return;
      e.preventDefault();
      cursor = clamp(cursor + (e.key === 'ArrowUp' ? -1 : 1), 0, history.length);
      input.value = history[cursor] || '';
      return;
    }
    if (e.key === 'Escape') { input.blur(); return; }
    if (e.key !== 'Enter') return;
    const line = input.value.trim();
    if (!line) return;
    history.push(line);
    cursor = history.length;
    input.value = '';
    log(`> ${line}`, 'echo');
    try {
      const out = await commands.run(line);
      if (out) log(out);
    } catch (err) {
      log(err.message || String(err), 'err');
    }
  });
}

// -------------------------------------------------------------- hover/click
function bindPointer() {
  const status = $('vw-status');
  status.textContent = HINT;
  scene.onHover((d) => {
    if (!d) { status.textContent = HINT; return; }
    if (d.kind === 'atom') {
      const item = V.items.find((i) => i.kind === 'model' && i.structure === d.root);
      status.textContent = d.chain === undefined ? `${item ? `#${item.id} ` : ''}atom`
        : `${item ? `#${item.id}  ` : ''}/${d.chain} ${d.resn} ${d.resi}${d.ins}  ${d.atom}  ·  ${d.element}  ·  B ${Number(d.b).toFixed(1)}`;
    } else {
      const item = V.items.find((i) => i.kind === 'map' && i.volume === d.volume);
      status.textContent = item ? `#${item.id}  ${item.label}  ·  level ${fmtLevel(item.level)}  (${toSigma(item.stats, item.level).toFixed(2)} σ)` : 'Map';
    }
  });
  scene.onClick((d, e) => {
    if (e.button !== undefined && e.button !== 0 && e.button !== 1) return;
    if (d?.kind !== 'atom' || d.chain === undefined) return;
    const residue = scene.wholeResidue(d.loci);
    scene.centerOn(residue);
    scene.select(residue);
    log(`Centred on /${d.chain} ${d.resn} ${d.resi}${d.ins}`);
  });
}

// ------------------------------------------------------------------ main
function showMessage(...nodes) {
  const msg = $('vw-msg');
  msg.hidden = false;
  clear(msg, ...nodes);
}

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const el = document.createElement('script');
    el.src = src;
    el.onload = resolve;
    el.onerror = () => reject(new Error(`Could not load ${src}`));
    document.head.appendChild(el);
  });
}

async function main() {
  let spec;
  try {
    spec = JSON.parse(decodeURIComponent(location.hash.slice(1)));
  } catch {
    spec = null;
  }
  if (!spec?.project) {
    showMessage(h('b', {}, 'Nothing to display.'), h('span', {}, 'Open the viewer from a job’s outputs (View 3D).'));
    return;
  }
  V.project = spec.project;
  V.full = new URLSearchParams(location.search).get('full') === '1';
  $('vw-home').href = `./#/p/${encodeURIComponent(V.project)}`;
  $('vw-title').textContent = V.project;

  const info = await api.info();
  try {
    const css = document.createElement('link');
    css.rel = 'stylesheet';
    css.href = info.molstar.css;
    document.head.prepend(css);
    await loadScript(info.molstar.js);
  } catch (err) {
    showMessage(h('b', {}, err.message),
      h('span', {}, 'The 3D engine (Mol*) is loaded from the internet unless it is installed on the server: run ',
        h('code', {}, 'cryoplug fetch-viewer'), ' there (or copy a molstar tarball with --tgz) and reload.'));
    return;
  }
  try {
    await scene.createScene($('vw-canvas'));
  } catch (err) {
    showMessage(h('b', {}, 'The 3D view could not start.'), h('span', {}, err.message || String(err)),
      h('span', {}, 'WebGL may be disabled in this browser (check hardware acceleration).'));
    return;
  }
  slices = createSlices($('vw-slices'));
  toolbar = buildToolbar();
  panel = createPanel($('vw-items'), ctl);
  commands = createCommands(ctl);
  ctl.setBackground(V.background);
  bindPointer();
  bindKeys();
  bindCommandLine();
  $('vw-open').addEventListener('click', () => openDialog());
  let resizeTimer = 0;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => panel.render(), 200); // histograms are drawn at the panel's width
  });
  log('Type help for the commands, ? for mouse and keys.', 'echo');
  try { collectLocres(await api.jobs(V.project)); } catch { /* colouring sources are optional */ }
  panel.render();
  await openItems(spec.items || []);
  if (!V.items.length) panel.render();
}

main().catch((err) => showError(err));
