// CryoPlug 3D viewer: a CryoSPARC-like volume viewer with ChimeraX habits (models panel, histogram,
// command line), drawn by Mol*. Maps arrive as binned previews unless full resolution is asked for.
import { api } from './api.js';
import { clear, h, icon, modal, showError, toast } from './ui.js';
import { COMMAND_HELP, createCommands } from './viewer/commands.js';
import { COLOR_KINDS, createPanel } from './viewer/panel.js';
import * as scene from './viewer/scene.js';
import { createSeriesPlayer, initSeries, seriesBox, SPEEDS } from './viewer/series.js';
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
  sources: [], // maps that colour other maps: [{ path, label, range, colourMap, kind, unit }]
};
const $ = (id) => document.getElementById(id);
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
let panel;
let slices;
let commands;
let toolbar;
let player;
let playerBar;

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
// the series the player controls: the active item if it is one, otherwise the first one
const activeSeries = () => (V.active?.series ? V.active : V.items.find((i) => i.series));

function syncHash() {
  const items = V.items.map((i) => ({
    kind: i.kind, path: i.path, label: i.label, ...(i.otype && i.otype !== 'map' ? { otype: i.otype } : {}),
    ...(i.kind === 'map' ? { color: i.color } : {}),
    ...(i.series ? { series: i.series.frames.map((f) => f.path), labels: i.series.frames.map((f) => f.label) } : {}),
    ...(i.colorBy ? { colorBy: i.colorBy.path, colorRange: [i.colorBy.min, i.colorBy.max], ...(i.colorBy.kind !== 'locres' ? { colorKind: i.colorBy.kind } : {}) } : {}),
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

// Whole view: the models if any are shown, otherwise the isosurfaces of the maps (not their boxes).
function resetView(opts = {}) {
  if (!V.items.some((i) => i.kind === 'model' && i.visible) && scene.frameMaps(V.items)) return;
  scene.resetView(opts);
}

// Redraw a map or a model after a display change (the frames of a series: see series.js).
function redraw(item) {
  if (item.series) player.redraw(item);
  else scene.schedule(item, fail);
}

// ------------------------------------------------------------------ items
async function addMap(spec, withModel) {
  const otype = spec.otype || (/mask/i.test(`${spec.label || ''} ${spec.path}`) ? 'mask' : 'map');
  const frames = Array.isArray(spec.series) && spec.series.length > 1 ? spec.series : null;
  const item = {
    kind: 'map', otype, label: spec.label || spec.path.split('/').pop(), path: frames ? frames[0] : spec.path, visible: true, expanded: true,
    full: frames ? false : V.full, zone: null, opacity: 0.45, refs: {},
    color: Number.isInteger(spec.color) ? spec.color : otype === 'mask' ? MASK_COLOR : MAP_COLORS[V.mapColors++ % MAP_COLORS.length],
    style: otype === 'mask' ? 'transparent' : (withModel || V.items.some((i) => i.kind === 'model') ? 'mesh' : 'surface'),
  };
  if (frames) initSeries(item, frames, spec.labels);
  loading(`Loading ${item.label}${frames ? ` (${frames.length} volumes)` : item.full ? ' (full resolution)' : ''}…`);
  try {
    if (frames) {
      await player.loadFirst(item);
    } else {
      const r = await scene.loadVolume(mapUrl(item), item.label);
      item.refs = { data: r.dataRef, volume: r.volumeRef, repr: null };
      item.volume = r.volume;
      item.stats = mapStatistics(r.volume);
    }
    item.level = defaultLevel(item);
    item.id = V.nextId++;
    V.items.push(item);
    await scene.showMap(item);
  } finally {
    loaded();
  }
  if (frames) {
    item.series.frames[0].version = item.series.version;
    player.loadRest(item).then(() => log(`#${item.id}: ${item.series.loaded} of ${frames.length} volumes ready to play`));
  }
  if (spec.colorBy) {
    const kind = spec.colorKind in COLOR_KINDS ? spec.colorKind : 'locres';
    const source = V.sources.find((s) => s.path === spec.colorBy)
      || { path: spec.colorBy, label: spec.colorBy.split('/').pop(), kind, unit: COLOR_KINDS[kind][1] };
    try {
      await applyColorBy(item, { ...source, range: spec.colorRange || source.range });
    } catch (err) {
      fail(err, `Colouring by ${COLOR_KINDS[source.kind][0].toLowerCase()}: `);
    }
  }
  return item;
}

// Maps of the project that colour other maps: local resolution (outputs of type locres, or CryoSPARC map_locres
// of older projects) and variability (volume series analysis).
function collectColorSources(jobs) {
  const found = [];
  for (const job of [...jobs].sort((a, b) => b.num - a.num)) {
    for (const o of job.outputs || []) {
      const kind = colorKindOf(o);
      if (kind) {
        found.push({ path: o.path, label: `${job.uid} ${o.label || o.name}`, range: o.meta?.display_range, colourMap: o.meta?.colour_map,
          kind, unit: COLOR_KINDS[kind][1] });
      }
    }
  }
  V.sources = found;
  return found;
}

function colorKindOf(o) {
  if (o.type === 'locres' || o.name === 'map_locres') return 'locres';
  if (o.type === 'variability') return 'variability';
  return null;
}

// Colour a map by the values of another ({ path, label, range, kind, unit }), or back to a single colour (null).
async function applyColorBy(item, source) {
  const old = item.colorBy;
  if (source) {
    loading(`Loading ${source.label}…`);
    try {
      const r = await scene.loadVolume(api.previewUrl(V.project, source.path), source.label);
      const [min, max] = source.range && source.range[1] > source.range[0] ? source.range : scene.colorRange(r.volume);
      const kind = source.kind || 'locres';
      item.colorBy = { path: source.path, label: source.label, refs: { data: r.dataRef, volume: r.volumeRef }, min, max,
        kind, unit: source.unit || COLOR_KINDS[kind][1] };
    } finally {
      loaded();
    }
  } else {
    item.colorBy = null;
  }
  if (item.series) await player.redrawAll(item);
  else await scene.showMap(item);
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
    playerBar.refresh();
    syncHash();
    if (wasEmpty) {
      // frame the model when there is one (density fit), otherwise the maps
      const model = added.find((i) => i.kind === 'model');
      if (model) scene.focusItem(model);
      else resetView({ whenReady: true });
    } else {
      added.forEach((i) => scene.keepInView(i)); // not clipped away, the view stays where it is
    }
    log(`Opened ${added.map((i) => `#${i.id} ${i.label}`).join(', ')}`);
  }
  return added;
}

async function reloadMap(item) {
  if (item.series) return; // frames are previews; zone and full resolution do not apply
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
  if (item.series) await player.dispose(item);
  else await scene.remove(item.refs.data);
  if (item.colorBy) await scene.remove(item.colorBy.refs.data);
  playerBar.refresh();
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
  activeSeries,
  setActive(item) {
    if (V.active === item) return;
    V.active = item;
    panel.markActive();
    if (slices.item && item.kind === 'map') slices.show(item);
    playerBar.refresh();
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
    if (item.kind === 'map' && item.colorBy) { // a single colour replaces the colouring by another map
      ctl.setColorBy(item, null);
      return;
    }
    redraw(item);
    if (item.kind === 'map') { panel.refreshLevel(item); slices.refresh(item); }
  },
  setLevel(item, value) {
    item.level = clamp(value, item.stats.min, item.stats.max);
    redraw(item);
    panel.refreshLevel(item);
    slices.refresh(item);
  },
  stepLevel(item, sigmas) {
    const unit = item.stats.sigma > 0 ? item.stats.sigma : (item.stats.max - item.stats.min) / 100;
    ctl.setLevel(item, item.level + sigmas * unit);
  },
  setStyle(item, style) {
    item.style = style;
    redraw(item);
    panel.renderItem(item);
  },
  setOpacity(item, opacity) {
    item.opacity = clamp(opacity, 0.05, 1);
    if (item.style !== 'transparent') { item.style = 'transparent'; panel.renderItem(item); }
    redraw(item);
  },
  setTransparency(item, percent) {
    if (percent <= 0) ctl.setStyle(item, item.style === 'transparent' ? 'surface' : item.style);
    else ctl.setOpacity(item, 1 - percent / 100);
  },
  setDisplay(item, display) {
    item.display = display;
    redraw(item);
    panel.renderItem(item);
  },
  setColorMode(item, mode) {
    item.colorMode = mode;
    redraw(item);
    panel.renderItem(item);
  },
  colorSources: () => V.sources,
  setColorBy(item, source) {
    if (source && item.colorBy?.path === source.path) { // same map: only the scale may change
      if (source.range) ctl.setColorRange(item, source.range[0], source.range[1]);
      return;
    }
    applyColorBy(item, source)
      .then(() => log(source ? `#${item.id} coloured by ${source.label} (${fmtScale(item.colorBy.min, item.colorBy.unit)}–${fmtScale(item.colorBy.max, item.colorBy.unit)} ${item.colorBy.unit})`
        : `#${item.id}: single colour`))
      .catch((err) => fail(err, 'Colouring: '));
  },
  setColorRange(item, min, max) {
    if (!item.colorBy || !(max > min)) return;
    item.colorBy.min = min;
    item.colorBy.max = max;
    redraw(item);
    panel.renderItem(item);
    syncHash();
  },
  setWaters(item, on) {
    item.waters = on;
    redraw(item);
  },
  setFullRes(item, on) {
    if (item.series) { fail('The volumes of a series are shown as previews (full resolution: extract one volume as a map)'); return; }
    if (item.full === on) return;
    item.full = on;
    reloadMap(item).then(() => log(`#${item.id} ${on ? 'at full resolution' : 'as a binned preview'}`)).catch((err) => fail(err));
  },
  setZone(item, zone) {
    if (item.series) { fail('Zones are not available for a volume series'); return; }
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
  resetView: () => resetView(),
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
  // volume series
  play(item, fps) { player.play(item, fps); },
  pause(item) { player.pause(item); },
  togglePlay(item) { if (item.series.playing) player.pause(item); else player.play(item); },
  goToFrame: (item, i) => player.go(item, i),
  stepFrame: (item, d) => { player.pause(item); return player.step(item, d); },
  setSpeed(item, fps) { item.series.fps = fps; playerBar.refresh(); },
  setPlayMode(item, mode) { item.series.mode = mode; item.series.dir = 1; playerBar.refresh(); },
};

const fmtScale = (v, unit) => v.toFixed(unit === 'σ' ? 2 : 1);

// A frame of a series is now displayed (or playback started or stopped).
function frameShown(item) {
  scene.keepInView(item);
  playerBar.refresh();
  panel.refreshLevel(item);
  if (slices.item === item) slices.refresh(item);
}

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
    reset: tool('Reset', 'refresh', 'Reset the view (R)', () => resetView()),
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

// ---------------------------------------------------------------- player
// Transport bar over the 3D view for the series being played (the active one, or the first).
function buildPlayerBar() {
  const root = $('vw-player');
  const control = (ic, title, onclick) => h('button', { type: 'button', class: 'vw-pbtn', title, 'aria-label': title, onclick }, icon(ic));
  const target = () => activeSeries();
  const play = control('play', 'Play (space)', () => { const it = target(); if (it) ctl.togglePlay(it); });
  const prev = control('skip-back', 'Previous volume (,)', () => { const it = target(); if (it) ctl.stepFrame(it, -1); });
  const next = control('skip-fwd', 'Next volume (.)', () => { const it = target(); if (it) ctl.stepFrame(it, 1); });
  const slider = h('input', {
    type: 'range', class: 'vw-pslider', min: 1, max: 2, step: 1, value: 1, 'aria-label': 'Volume of the series',
    oninput: (e) => { const it = target(); if (it) { player.pause(it); player.go(it, Number(e.target.value) - 1); } },
  });
  const which = h('span', { class: 'vw-pid' });
  const label = h('span', { class: 'vw-plabel', 'aria-live': 'off' });
  const speed = h('select', { class: 'vw-pspeed', 'aria-label': 'Playback speed', title: 'Volumes per second',
    onchange: (e) => { const it = target(); if (it) ctl.setSpeed(it, Number(e.target.value)); } },
  SPEEDS.map((f) => h('option', { value: f }, `${f}/s`)));
  const mode = h('button', { type: 'button', class: 'vw-pmode', title: 'Loop, or play back and forth',
    onclick: () => { const it = target(); if (it) ctl.setPlayMode(it, it.series.mode === 'loop' ? 'bounce' : 'loop'); } });
  clear(root, which, prev, play, next, slider, label, speed, mode);
  return {
    refresh() {
      const it = target();
      root.hidden = !it;
      if (!it) return;
      const s = it.series;
      const n = s.frames.length;
      const frame = s.frames[s.index];
      which.textContent = `#${it.id}`;
      which.title = it.label;
      clear(play, icon(s.playing ? 'pause' : 'play'));
      play.title = s.playing ? 'Pause (space)' : 'Play (space)';
      play.setAttribute('aria-label', play.title);
      play.classList.toggle('on', s.playing);
      slider.max = String(n);
      if (document.activeElement !== slider) slider.value = String(s.index + 1);
      slider.setAttribute('aria-valuetext', `${s.index + 1} of ${n}: ${frame.label}`);
      label.textContent = `${s.index + 1}/${n} · ${frame.label}${s.loaded < n ? ` · loading ${s.loaded}/${n}` : ''}`;
      label.title = frame.path;
      speed.value = String(s.fps);
      mode.textContent = s.mode === 'loop' ? 'Loop' : 'Back & forth';
    },
  };
}

// --------------------------------------------------------------- dialogs
async function openDialog() {
  let jobs;
  try { jobs = await api.jobs(V.project); } catch (err) { fail(err); return; }
  collectColorSources(jobs);
  const open = new Set(V.items.map((i) => i.path));
  const rows = [];
  for (const job of [...jobs].sort((a, b) => b.num - a.num)) {
    const outs = (job.outputs || []).filter((o) => ['map', 'mask', 'half_maps', 'model', 'locres', 'variability', 'volume_series'].includes(o.type));
    if (!outs.length) continue;
    const entries = outs.flatMap((o) => (o.type === 'half_maps' ? (o.files || [o.path]).map((f, k) => ({ o, path: f, label: `${job.uid} ${o.label || o.name} ${'AB'[k] || k + 1}` }))
      : [{ o, path: o.type === 'volume_series' ? (o.files || [o.path])[0] : o.path, label: `${job.uid} ${o.label || o.name}` }]));
    rows.push(h('div', { class: 'vw-open-job' }, h('b', {}, `${job.uid} · ${job.title}`), entries.map((e) => h('div', {
      class: 'vw-open-row', tabindex: 0, role: 'button',
      onclick: () => pick(e), onkeydown: (ev) => { if (ev.key === 'Enter') pick(e); },
    }, icon(e.o.type === 'model' ? 'model' : e.o.type === 'volume_series' ? 'play' : 'map'), h('span', {}, e.label), h('span', { class: 'p' }, e.path),
    colorKindOf(e.o) ? h('span', { class: 'tag' }, activeMap() ? 'colours the active map' : 'colours its map') : null,
    e.o.type === 'volume_series' ? h('span', { class: 'tag' }, `${(e.o.files || []).length} volumes`) : null,
    open.has(e.path) ? h('span', { class: 'tag' }, 'open') : null))));
  }
  const m = modal({
    title: 'Open maps and models', wide: true,
    body: rows.length ? h('div', { class: 'vw-open-list' }, rows) : h('p', { class: 'muted' }, 'No completed job has a map or a model yet.'),
  });
  function pick(e) {
    m.close();
    if (colorKindOf(e.o)) {
      const source = V.sources.find((s) => s.path === e.path);
      const map = activeMap();
      if (map) { ctl.setColorBy(map, source); return; }
      if (source?.colourMap) {
        openItems([{ kind: 'map', path: source.colourMap, label: source.colourMap.split('/').pop(), colorBy: source.path, colorRange: source.range, colorKind: source.kind }]);
        return;
      }
    }
    if (e.o.type === 'volume_series') {
      openItems([{ kind: 'map', path: e.path, label: e.label, series: e.o.files || [e.o.path], labels: e.o.meta?.frame_labels || [] }]);
      return;
    }
    openItems([{ kind: e.o.type === 'model' ? 'model' : 'map', path: e.path, label: e.label, otype: e.o.type === 'mask' ? 'mask' : undefined }]);
  }
}

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
        ['R', 'Reset the view'], ['S', 'Spin on / off'], ['L', 'Slices on / off'], ['Space', 'Play / pause a volume series'],
        [',  /  .', 'Previous / next volume of the series'], [':', 'Go to the command line'], ['Esc', 'Clear the selection'],
        ['↑ / ↓ in the command line', 'Previous / next command']]),
      h('h4', {}, 'Commands'),
      table(COMMAND_HELP),
      h('p', { class: 'muted small' }, 'Maps open as binned previews (at most 200 voxels per side) for speed; “Load at full resolution” in a map’s menu, or fullres #1, loads the original file. '
        + 'The volumes of a series (cryoDRGN clusters, trajectories, 3D variability frames) are previews of at most 128 voxels per side, all loaded once so that they play smoothly.')),
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
    else if (k === 'r' || k === 'R') resetView();
    else if (k === 's' || k === 'S') ctl.setSpin(!V.spin);
    else if ((k === 'm' || k === 'M') && map) ctl.setStyle(map, { surface: 'mesh', mesh: 'transparent', transparent: 'surface' }[map.style]);
    else if (k === 'l' || k === 'L') toolbarClickSlices();
    else if (k === ' ' && activeSeries()) ctl.togglePlay(activeSeries());
    else if ((k === ',' || k === '<') && activeSeries()) ctl.stepFrame(activeSeries(), -1);
    else if ((k === '.' || k === '>') && activeSeries()) ctl.stepFrame(activeSeries(), 1);
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
      const frame = item?.series ? `  ·  ${item.series.frames[item.series.index].label}` : '';
      status.textContent = item ? `#${item.id}  ${item.label}${frame}  ·  level ${fmtLevel(item.level)}  (${toSigma(item.stats, item.level).toFixed(2)} σ)` : 'Map';
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
  player = createSeriesPlayer({
    url: (path, n) => api.previewUrl(V.project, path, seriesBox(n)),
    onFrame: frameShown,
    onLoad: (item) => { playerBar.refresh(); panel.refreshLevel(item); },
    onError: (err, what = '') => fail(err, what),
  });
  playerBar = buildPlayerBar();
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
  try { collectColorSources(await api.jobs(V.project)); } catch { /* colouring sources are optional */ }
  panel.render();
  await openItems(spec.items || []);
  if (!V.items.length) panel.render();
  playerBar.refresh();
}

main().catch((err) => showError(err));
