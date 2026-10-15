// CryoPlug 3D viewer: a CryoSPARC-like volume viewer with ChimeraX habits (models panel, histogram, slices, command
// line), drawn by Mol*. Maps arrive as compact binned copies at the detail and filter picked in their card — a quick
// coarse look first when the connection is slow — and the URL keeps the items, their settings and the camera.
import { api } from './api.js';
import { clear, h, icon, modal, popupMenu, showError, toast } from './ui.js';
import { COMMAND_HELP, createCommands } from './viewer/commands.js';
import { binning, COARSE_BOX, DEFAULT_BOX, expectedSeconds, fetchMap, fmtBytes, fmtDims, mapInfo, SERIES_BOXES, seriesBox } from './viewer/mapdata.js';
import { createMaskEditor } from './viewer/maskedit.js';
import { COLOR_KINDS, colorLegend, createPanel, hex } from './viewer/panel.js';
import * as scene from './viewer/scene.js';
import { createSeriesPlayer, initSeries, SPEEDS } from './viewer/series.js';
import { createSlices } from './viewer/slices.js';
import { defaultLevel, fmtLevel, levelForMass, mapStatistics, toSigma } from './viewer/stats.js';

const MAP_COLORS = [0x8fb3f0, 0xf0b46e, 0x8fd3a5, 0xe899c3, 0xc3a6f0, 0xe8e27a];
const MASK_COLOR = 0x9aa0a6;
const MODEL_COLOR = 0xd8d8d8;
const BACKGROUNDS = [['Black', 0x000000], ['Grey', 0x5a5a5a], ['White', 0xffffff]];
const MAP_STYLES = ['surface', 'mesh', 'transparent'];
const MODEL_DISPLAYS = ['cartoon', 'both', 'sticks', 'spheres', 'surface', 'cartoon-surface'];
const HINT = 'Left-drag rotate · right-drag move · wheel zoom · click an atom to centre on it · Shift + click two atoms: distance · ? help';

const V = {
  project: null, items: [], nextId: 1, active: null, mapColors: 0, full: false,
  background: 0x000000, spin: false, slab: null, ortho: false, soft: false,
  sources: [], // maps that colour other maps: [{ path, label, range, colourMap, kind, unit }]
  ready: false, // items of the link opened: from then on the URL follows the view
};
const $ = (id) => document.getElementById(id);
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
let panel;
let slices;
let commands;
let toolbar;
let player;
let playerBar;
let maskEditor;

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
function loadingText(text) {
  if (loadingDepth) $('vw-loading-text').textContent = text;
}
function loaded() {
  loadingDepth = Math.max(0, loadingDepth - 1);
  if (!loadingDepth) $('vw-loading').hidden = true;
}
const progress = (what) => (got, total) => loadingText(`${what} · ${fmtBytes(got)} of ${fmtBytes(total)}`);

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
const voxelVolume = (item) => scene.voxelAxes(item.volume).scale.reduce((a, b) => a * Math.abs(b), 1);
const defaultBox = (item) => (item.series ? seriesBox(item.series.frames.length) : DEFAULT_BOX);
const round = (v) => Number(v.toPrecision(6));

// The URL keeps what is open, how it is shown and the camera: reloading the page, or sending the link to a colleague
// on the same server, gives the same view.
function itemState(i) {
  const out = { kind: i.kind, path: i.path, label: i.label, ...(i.otype && i.otype !== 'map' ? { otype: i.otype } : {}) };
  if (!i.visible) out.visible = false;
  if (i.kind === 'map') {
    Object.assign(out, { color: i.color, level: round(i.level), style: i.style });
    if (i.style === 'transparent') out.opacity = i.opacity;
    if (i.box !== defaultBox(i)) out.box = i.box;
    if (i.lowpass) out.lowpass = i.lowpass;
    if (i.series) Object.assign(out, { series: i.series.frames.map((f) => f.path), labels: i.series.frames.map((f) => f.label) });
    if (i.colorBy) {
      Object.assign(out, { colorBy: i.colorBy.path, colorRange: [i.colorBy.min, i.colorBy.max] });
      if (i.colorBy.kind !== 'locres') out.colorKind = i.colorBy.kind;
    }
  } else {
    Object.assign(out, { display: i.display, colorMode: i.colorMode });
    if (i.colorMode === 'uniform') out.color = i.color;
    if (i.surfaceOpacity !== undefined) out.surfaceOpacity = i.surfaceOpacity;
    if (i.waters) out.waters = true;
  }
  return out;
}

function syncHash() {
  if (!V.ready) return;
  const state = { project: V.project, items: V.items.map(itemState) };
  if (V.items.length) state.view = scene.cameraState();
  if (V.slab !== null) state.slab = V.slab;
  const mask = maskEditor?.state();
  if (mask) state.mask = mask;
  history.replaceState(null, '', `${location.pathname}${location.search}#${encodeURIComponent(JSON.stringify(state))}`);
  const labels = V.items.map((i) => i.label);
  $('vw-title').textContent = labels.length ? `${V.project} · ${labels.join('  ·  ')}` : V.project;
  document.title = labels.length ? `${labels.join(', ')} — CryoPlug viewer` : 'CryoPlug viewer';
}
let hashTimer = 0;
const syncHashSoon = () => { clearTimeout(hashTimer); hashTimer = setTimeout(syncHash, 500); };

// URL of a map as the viewer loads it: binned to the item's detail, filtered, 16-bit (zone: only near its model).
function mapUrl(item, { path = item.path, box = item.box, lowpass = item.lowpass, encoding = 'i16' } = {}) {
  const opts = { encoding, lowpass: lowpass || 0 };
  const model = !item.series && item.zone && byId(item.zone.model);
  if (model) return api.zoneUrl(V.project, path, model.path, item.zone.radius, box, opts);
  return api.previewUrl(V.project, path, box, opts);
}

async function loadMapData(item, opts = {}) {
  const bytes = await fetchMap(mapUrl(item, opts), opts.onProgress);
  return scene.loadVolume(bytes, item.label);
}

// settings: those the map was fetched with (the item's may have changed while it downloaded).
function setVolume(item, r, { coarse = false, settings = shownSettings(item) } = {}) {
  item.refs = { data: r.dataRef, volume: r.volumeRef, repr: null };
  item.volume = r.volume;
  item.stats = mapStatistics(r.volume);
  item.coarse = coarse;
  item.shown = settings;
  item.full = !coarse && (item.info ? binning(item.info, settings.box) === 1 : settings.box === 0);
}

// Settings of the map on screen: put back when loading other settings fails.
const shownSettings = (item) => ({ box: item.box, lowpass: item.lowpass, zone: item.zone });

// A series is shown 64-256 voxels per side: every volume is held in memory.
const seriesLimit = (box) => clamp(box > 0 ? box : SERIES_BOXES[SERIES_BOXES.length - 1], SERIES_BOXES[0], SERIES_BOXES[SERIES_BOXES.length - 1]);

// A quick 8-bit look at 96 voxels first when the map asked for would take a while to arrive.
function previewFirst(item) {
  if (!item.info?.box || item.zone) return false;
  const target = binning(item.info, item.box);
  if (binning(item.info, COARSE_BOX) < 1.5 * target) return false;
  const voxels = item.info.box.reduce((a, d) => a * Math.floor(d / target), 1);
  return expectedSeconds(voxels * 2) > 0.8;
}

// Level after the map changed. keep: 'fraction' (another binning or filter changes the values: same volume enclosed),
// 'absolute' (a zone only hides voxels), 'refine' (the finer copy after a quick look: the level of the link, or the
// one set meanwhile, as it is). A level never set by the user follows the map's own default.
function keepLevel(item, keep, fraction) {
  if (item.levelAuto && keep !== 'absolute') item.level = defaultLevel(item);
  else if (keep === 'refine' && Number.isFinite(item.linkLevel)) item.level = item.linkLevel;
  else if (keep === 'fraction' && fraction > 0 && fraction < 1) item.level = item.stats.percentile(1 - fraction);
  item.level = clamp(item.level, item.stats.min, item.stats.max);
}

// Whole view: the models if any are shown, otherwise the isosurfaces of the maps (not their boxes).
function resetView(opts = {}) {
  if (!V.items.some((i) => i.kind === 'model' && i.visible) && scene.frameMaps(V.items)) { scene.reapplySlab(400); return; }
  scene.resetView(opts);
  scene.reapplySlab(400);
}

// Thickness (Å) of a slab given as a percentage of what is shown, or directly in Å.
function slabThickness(value) {
  if (value && typeof value === 'object') return value.angstrom;
  const sphere = scene.contentSphere(V.items);
  return sphere ? Math.max(1, (2 * sphere.radius * value) / 100) : value;
}

// Redraw a map or a model after a display change (the frames of a series: see series.js).
function redraw(item) {
  if (item.series) player.redraw(item);
  else scene.schedule(item, fail);
  syncHashSoon();
}

// ------------------------------------------------------------------ items
async function addMap(spec, withModel) {
  const otype = spec.otype || (/mask/i.test(`${spec.label || ''} ${spec.path}`) ? 'mask' : 'map');
  const frames = Array.isArray(spec.series) && spec.series.length > 1 ? spec.series : null;
  const item = {
    kind: 'map', otype, label: spec.label || spec.path.split('/').pop(), path: frames ? frames[0] : spec.path, visible: spec.visible !== false,
    expanded: true, zone: null, refs: {}, lowpass: Number(spec.lowpass) || 0, levelAuto: !Number.isFinite(spec.level),
    linkLevel: Number.isFinite(spec.level) ? spec.level : null, // the level of a link, until the user sets another
    opacity: Number.isFinite(spec.opacity) ? clamp(spec.opacity, 0.05, 1) : 0.45,
    color: Number.isInteger(spec.color) ? spec.color : otype === 'mask' ? MASK_COLOR : MAP_COLORS[V.mapColors++ % MAP_COLORS.length],
    style: MAP_STYLES.includes(spec.style) ? spec.style
      : otype === 'mask' ? 'transparent' : (withModel || V.items.some((i) => i.kind === 'model') ? 'mesh' : 'surface'),
  };
  if (frames) {
    item.box = seriesLimit(Number.isFinite(spec.box) ? spec.box : seriesBox(frames.length));
    initSeries(item, frames, spec.labels, null, { box: item.box, lowpass: item.lowpass });
    item.shown = shownSettings(item);
  } else {
    item.box = Number.isFinite(spec.box) ? spec.box : V.full ? 0 : DEFAULT_BOX;
  }
  try { item.info = await mapInfo(V.project, item.path); } catch { item.info = null; } // detail choices: optional
  const coarse = !frames && previewFirst(item);
  let fine = null; // the detail asked for, downloading while the quick look is shown
  loading(`Loading ${item.label}${frames ? ` (${frames.length} volumes)` : coarse ? ' (quick look first)' : ''}…`);
  try {
    if (frames) {
      await player.loadFirst(item);
    } else if (coarse) {
      const refining = `Refining ${item.label} (${fmtDims(item.info.box.map((d) => Math.floor(d / binning(item.info, item.box))))})`;
      const quick = fetchMap(mapUrl(item, { box: COARSE_BOX, encoding: 'i8' }));
      fine = { text: refining, bytes: fetchMap(mapUrl(item), progress(refining)) };
      fine.bytes.catch(() => {}); // reported by the refinement
      setVolume(item, await scene.loadVolume(await quick, item.label), { coarse: true });
    } else {
      setVolume(item, await loadMapData(item, { onProgress: progress(`Loading ${item.label}`) }));
    }
    item.level = item.levelAuto ? defaultLevel(item) : clamp(spec.level, item.stats.min, item.stats.max);
    item.id = V.nextId++;
    V.items.push(item);
    await scene.showMap(item);
  } finally {
    loaded();
  }
  if (frames) {
    item.series.frames[item.series.index].version = item.series.version;
    item.series.rest = player.loadRest(item);
    item.series.rest.then(() => {
      const s = item.series;
      if (!s.closed) log(`#${item.id}: ${s.loaded} of ${s.frames.length} volumes ready to play${s.failed ? ` (${s.failed} could not be read)` : ''}`);
    });
  }
  if (fine) reloadMap(item, { keep: 'refine', text: fine.text, bytes: fine.bytes }).catch((err) => fail(err, `${item.label}: `));
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
// The last request wins: false when a later one (or closing the map) made this one moot.
async function applyColorBy(item, source) {
  const token = (item.colorToken || 0) + 1;
  item.colorToken = token;
  let next = null;
  if (source) {
    loading(`Loading ${source.label}…`);
    try {
      const bytes = await fetchMap(api.previewUrl(V.project, source.path, DEFAULT_BOX, { encoding: 'i16' }));
      const r = await scene.loadVolume(bytes, source.label);
      if (token !== item.colorToken || !V.items.includes(item)) { await scene.remove(r.dataRef); return false; }
      const [min, max] = source.range && source.range[1] > source.range[0] ? source.range : scene.colorRange(r.volume);
      const kind = source.kind || 'locres';
      next = { path: source.path, label: source.label, refs: { data: r.dataRef, volume: r.volumeRef }, volume: r.volume, min, max,
        kind, unit: source.unit || COLOR_KINDS[kind][1] };
    } finally {
      loaded();
    }
  }
  const old = item.colorBy;
  item.colorBy = next;
  if (item.series) await player.redrawAll(item);
  else await scene.showMap(item);
  if (old) await scene.remove(old.refs.data);
  panel.renderItem(item);
  slices.refresh(item);
  refreshLegend();
  syncHash();
  return true;
}

async function addModel(spec, withMap) {
  const item = {
    kind: 'model', label: spec.label || spec.path.split('/').pop(), path: spec.path, visible: spec.visible !== false, expanded: true, refs: {},
    color: Number.isInteger(spec.color) ? spec.color : MODEL_COLOR, colorMode: spec.colorMode || 'chain', waters: !!spec.waters,
    display: MODEL_DISPLAYS.includes(spec.display) ? spec.display : withMap || V.items.some((i) => i.kind === 'map') ? 'both' : 'cartoon',
    ...(Number.isFinite(spec.surfaceOpacity) ? { surfaceOpacity: clamp(spec.surfaceOpacity, 0.05, 1) } : {}),
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

async function openItems(specs, { view = null } = {}) {
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
    refreshLegend();
    if (wasEmpty && view) {
      requestAnimationFrame(() => scene.setCameraState(view)); // the view of the link
    } else if (wasEmpty) {
      // frame the model when there is one (density fit), otherwise the maps
      const model = added.find((i) => i.kind === 'model');
      if (model) scene.focusItem(model);
      else resetView({ whenReady: true });
    } else {
      added.forEach((i) => scene.keepInView(i)); // not clipped away, the view stays where it is
    }
    syncHash();
    log(`Opened ${added.map((i) => `#${i.id} ${i.label}`).join(', ')}`);
  }
  return added;
}

// Load a map again (another detail, filter or zone; bytes: a download already started). The previous one stays
// shown until the new one is there. One reload at a time per map: requests made meanwhile are merged into the next
// one (the last settings win) and settle with it; a failed reload puts back the settings of the map shown.
function reloadMap(item, opts = {}) {
  return new Promise((resolve, reject) => {
    item.reloadNext = { opts, waiting: [...(item.reloadNext?.waiting || []), { resolve, reject }] };
    if (!item.reloading) item.reloading = runReloads(item);
  });
}

async function runReloads(item) {
  try {
    while (item.reloadNext) {
      const { opts, waiting } = item.reloadNext;
      item.reloadNext = null;
      if (!V.items.includes(item)) { waiting.forEach((w) => w.resolve()); continue; } // closed meanwhile
      try {
        await (item.series ? reloadSeriesOnce(item) : reloadMapOnce(item, opts));
        waiting.forEach((w) => w.resolve());
      } catch (err) {
        if (item.shown && !item.reloadNext) { // nothing else asked meanwhile: the settings of what is on screen
          Object.assign(item, item.shown);
          panel.renderItem(item);
          syncHash();
        }
        waiting.forEach((w) => w.reject(err));
      }
    }
  } finally {
    item.reloading = null;
  }
}

async function reloadMapOnce(item, { keep = 'fraction', text = '', bytes = null } = {}) {
  const what = text || `Loading ${item.label}${item.zone ? ` (zone ${item.zone.radius} Å)` : ''}`;
  const settings = shownSettings(item);
  loading(`${what}…`);
  try {
    const r = bytes ? await scene.loadVolume(await bytes, item.label) : await loadMapData(item, { onProgress: progress(what) });
    if (!V.items.includes(item)) { await scene.remove(r.dataRef); return; } // closed meanwhile
    const old = item.refs.data;
    const fraction = item.stats ? item.stats.fractionAbove(item.level) : 0; // the level may have moved meanwhile
    setVolume(item, r, { settings });
    keepLevel(item, keep, fraction);
    await scene.showMap(item);
    await scene.remove(old);
  } finally {
    loaded();
  }
  panel.renderItem(item);
  if (slices.item === item) slices.show(item);
  syncHash();
}

async function reloadSeriesOnce(item) {
  const fraction = item.stats.fractionAbove(item.level);
  loading(`Loading ${item.label} (${item.series.frames.length} volumes)…`);
  try {
    await player.reload(item, (it) => keepLevel(it, 'fraction', fraction));
  } finally {
    loaded();
  }
  if (!V.items.includes(item)) return;
  item.shown = shownSettings(item);
  panel.renderItem(item);
  playerBar.refresh();
  if (slices.item === item) slices.show(item);
  syncHash();
  const s = item.series;
  s.rest?.then(() => { if (!s.closed) log(`#${item.id}: ${s.loaded} of ${s.frames.length} volumes ready to play`); });
}

async function closeItem(item) {
  V.items = V.items.filter((i) => i !== item);
  maskEditor?.itemClosed(item);
  if (V.active === item) V.active = V.items[0] || null;
  if (slices.item === item) hideSlices();
  if (item.series) await player.dispose(item);
  else await scene.remove(item.refs.data);
  if (item.colorBy) await scene.remove(item.colorBy.refs.data);
  playerBar.refresh();
  for (const m of V.items) {
    if (m.kind === 'map' && m.zone?.model === item.id) {
      m.zone = null;
      reloadMap(m, { keep: 'absolute' }).catch((err) => fail(err));
    }
  }
  panel.render();
  refreshLegend();
  syncHash();
}

// Centre on chain:residue (or a whole chain) of a model; returns an error message, or '' when done.
function goTo(model, text) {
  const m = /^\s*\/?([A-Za-z0-9]+)\s*(?::\s*(-?\d+))?\s*$/.exec(text || '');
  if (!m) return 'Write chain:residue, e.g. A:45 (or just A for a chain)';
  const loci = scene.residueLoci(model, m[1], m[2] !== undefined ? Number(m[2]) : undefined);
  if (!loci) return `/${m[1]}${m[2] !== undefined ? `:${m[2]}` : ''} not found in #${model.id}`;
  scene.centerOn(loci, { zoom: true });
  scene.reapplySlab(400);
  scene.select(m[2] !== undefined ? loci : null);
  return '';
}

function describeDetail(item) {
  const dims = fmtDims(item.volume.grid.cells.space.dimensions);
  const vox = Math.abs(scene.voxelAxes(item.volume).scale[0]);
  return `${dims} voxels of ${vox.toFixed(2)} Å${item.full ? ' (full resolution)' : ''}`;
}

// ---------------------------------------------------------- colour scale
// Colour scale of the coloured map over the view (also drawn into saved images).
function legendItem() {
  if (V.active?.kind === 'map' && V.active.colorBy && V.active.visible) return V.active;
  return V.items.find((i) => i.kind === 'map' && i.visible && i.colorBy) || null;
}

function refreshLegend() {
  const el = $('vw-legend');
  const item = legendItem();
  el.hidden = !item;
  if (!item) return;
  const by = item.colorBy;
  clear(el, h('div', { class: 'vw-l3-title' }, `${COLOR_KINDS[by.kind][0]} (${by.unit}) · #${item.id}`), colorLegend(by));
}

function drawLegend(ctx, width, height, scale) {
  const item = legendItem();
  if (!item) return;
  const by = item.colorBy;
  const s = Math.max(1, scale);
  const w = 220 * s;
  const x = width - w - 18 * s;
  const y = height - 64 * s;
  const light = (V.background >> 16) + ((V.background >> 8) & 255) + (V.background & 255) > 380;
  ctx.fillStyle = light ? 'rgba(255,255,255,0.85)' : 'rgba(0,0,0,0.6)';
  ctx.fillRect(x - 10 * s, y - 22 * s, w + 20 * s, 74 * s);
  const grad = ctx.createLinearGradient(x, 0, x + w, 0);
  scene.RESOLUTION_COLORS.forEach((c, i) => grad.addColorStop(i / (scene.RESOLUTION_COLORS.length - 1), hex(c)));
  ctx.fillStyle = grad;
  ctx.fillRect(x, y, w, 12 * s);
  ctx.fillStyle = light ? '#111' : '#f2f2f2';
  ctx.font = `${12 * s}px system-ui, sans-serif`;
  const fmt = (v) => v.toFixed(by.unit === 'σ' ? 2 : 1);
  ctx.textAlign = 'left';
  ctx.fillText(`${COLOR_KINDS[by.kind][0]} (${by.unit})`, x, y - 7 * s);
  ctx.fillText(fmt(by.min), x, y + 27 * s);
  ctx.textAlign = 'right';
  ctx.fillText(fmt(by.max), x + w, y + 27 * s);
  ctx.font = `${10.5 * s}px system-ui, sans-serif`;
  const ends = by.kind === 'variability' ? ['stable', 'variable'] : ['best', 'worst'];
  ctx.fillText(ends[1], x + w, y + 42 * s);
  ctx.textAlign = 'left';
  ctx.fillText(ends[0], x, y + 42 * s);
}

// ------------------------------------------------------------- recording
const rec = { recorder: null, chunks: [], timer: 0, clock: 0, started: 0, spun: false, name: '' };

function videoType() {
  if (typeof MediaRecorder === 'undefined') return null;
  return ['video/mp4;codecs=avc1.42E01E', 'video/mp4', 'video/webm;codecs=vp9', 'video/webm;codecs=vp8', 'video/webm']
    .find((t) => MediaRecorder.isTypeSupported(t)) || null;
}

// Record the view as a video: one pass of the series, a full turn, or until stopped.
async function startRecording(kind = 'free', seconds) {
  if (rec.recorder) { fail('Already recording: record stop'); return; }
  const canvas = scene.canvas();
  const type = videoType();
  if (!type || !canvas.captureStream) { fail('This browser cannot record videos of the view'); return; }
  const series = kind === 'series' ? activeSeries() : null;
  if (kind === 'series' && !series) { fail('No volume series displayed'); return; }
  if (series) { // a smooth video: every volume ready, the first one shown
    const s = series.series;
    if (s.loaded + s.failed < s.frames.length) { log('The recording starts once every volume is loaded…'); await s.rest; }
    player.pause(series);
    await player.go(series, 0);
    if (rec.recorder) return;
  }
  const stream = canvas.captureStream(30);
  const recorder = new MediaRecorder(stream, { mimeType: type, videoBitsPerSecond: 12e6 });
  rec.chunks = [];
  rec.name = `cryoplug_${V.project}_${(series || activeMap() || activeModel())?.label || 'view'}`.replace(/[^\w.-]+/g, '_');
  recorder.ondataavailable = (e) => { if (e.data.size) rec.chunks.push(e.data); };
  recorder.onstop = () => {
    stream.getTracks().forEach((t) => t.stop());
    const blob = new Blob(rec.chunks, { type: type.split(';')[0] });
    const name = `${rec.name}.${type.startsWith('video/mp4') ? 'mp4' : 'webm'}`;
    const a = h('a', { href: URL.createObjectURL(blob), download: name });
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 10000);
    log(`Saved ${name} (${fmtBytes(blob.size)}, ${((performance.now() - rec.started) / 1000).toFixed(1)} s)`);
    rec.recorder = null;
    clearInterval(rec.clock);
    $('vw-rec').hidden = true;
    toolbar.refresh();
  };
  rec.recorder = recorder;
  rec.started = performance.now();
  recorder.start(500);
  $('vw-rec').hidden = false;
  rec.clock = setInterval(() => { $('vw-rec').textContent = `● REC ${((performance.now() - rec.started) / 1000).toFixed(0)} s`; }, 250);
  $('vw-rec').textContent = '● REC 0 s';
  toolbar.refresh();
  if (series) {
    log(`Recording one pass of #${series.id} at ${series.series.fps} volumes per second…`);
    player.play(series, null, { once: true, onEnd: () => setTimeout(stopRecording, 400) });
  } else if (kind === 'spin') {
    const secs = clamp(seconds || 12, 2, 120);
    rec.spun = !V.spin;
    scene.setSpin(true, 1 / secs);
    log(`Recording a full turn (${secs} s)…`);
    rec.timer = setTimeout(stopRecording, secs * 1000);
  } else {
    log(seconds ? `Recording for ${seconds} s…` : 'Recording… (record stop, or the Movie button, to finish)');
    if (seconds) rec.timer = setTimeout(stopRecording, clamp(seconds, 1, 600) * 1000);
  }
}

function stopRecording() {
  clearTimeout(rec.timer);
  if (!rec.recorder) return;
  if (rec.spun || V.spin) scene.setSpin(V.spin && !rec.spun); // back to how it was (spin speed restored)
  rec.spun = false;
  if (rec.recorder.state !== 'inactive') rec.recorder.stop();
}

// --------------------------------------------------------- measurements
let firstAtom = null;
async function measure(d) {
  const name = (x) => `/${x.chain} ${x.resn} ${x.resi}${x.ins} ${x.atom}`;
  if (!firstAtom) {
    firstAtom = d;
    scene.select(d.loci);
    log(`Distance from ${name(d)}: Shift + click the second atom`);
    return;
  }
  const a = firstAtom;
  firstAtom = null;
  scene.select(null);
  const dist = scene.atomDistance(a.loci, d.loci);
  try {
    await scene.addDistance(a.loci, d.loci);
    log(`Distance ${name(a)} — ${name(d)}: ${dist.toFixed(2)} Å`);
  } catch (err) {
    fail(err, 'Distance: ');
  }
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
    refreshLegend();
  },
  setVisible(item, on) {
    item.visible = on;
    scene.setHidden(item.kind === 'map' ? item.refs.repr : item.refs.structure, !on);
    panel.renderItem(item);
    refreshLegend();
    syncHashSoon();
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
    if (!Number.isFinite(value)) return;
    item.level = clamp(value, item.stats.min, item.stats.max);
    item.levelAuto = false;
    item.linkLevel = null;
    redraw(item);
    panel.refreshLevel(item);
    slices.refresh(item);
  },
  stepLevel(item, sigmas) {
    const unit = item.stats.sigma > 0 ? item.stats.sigma : (item.stats.max - item.stats.min) / 100;
    ctl.setLevel(item, item.level + sigmas * unit);
  },
  // level enclosing a protein mass (kDa); false when the box cannot hold it
  setLevelByMass(item, kda) {
    const level = levelForMass(item, kda, voxelVolume(item));
    if (level === null) return false;
    ctl.setLevel(item, level);
    return true;
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
  setSurfaceOpacity(item, opacity, { show = false } = {}) {
    item.surfaceOpacity = clamp(opacity, 0.05, 1);
    if (!scene.hasSurface(item.display)) {
      if (!show) return;
      ctl.setDisplay(item, 'cartoon-surface');
      return;
    }
    scene.schedule(item, fail, 'alpha'); // the surface itself is not computed again
    panel.syncSurfaceOpacity(item);
    syncHashSoon();
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
      .then((applied) => {
        if (!applied) return; // a later choice replaced it
        log(item.colorBy ? `#${item.id} coloured by ${item.colorBy.label} (${fmtScale(item.colorBy.min, item.colorBy.unit)}–${fmtScale(item.colorBy.max, item.colorBy.unit)} ${item.colorBy.unit})`
          : `#${item.id}: single colour`);
      })
      .catch((err) => fail(err, 'Colouring: '));
  },
  setColorRange(item, min, max) {
    if (!item.colorBy || !(max > min)) return;
    item.colorBy.min = min;
    item.colorBy.max = max;
    redraw(item);
    panel.renderItem(item);
    slices.refresh(item);
    refreshLegend();
  },
  setWaters(item, on) {
    item.waters = on;
    redraw(item);
  },
  // detail: largest side in voxels (0: full size, null: the default, { step: n }: binning n as ChimeraX's step)
  setDetail(item, box) {
    let want = box;
    if (want && typeof want === 'object') want = item.info ? Math.ceil(Math.max(...item.info.box) / want.step) : DEFAULT_BOX;
    if (want === null || want === undefined) want = defaultBox(item);
    if (item.series) {
      if (!(want > 0)) fail(`A volume series is shown at most ${seriesLimit(0)} voxels per side (every volume is held in memory): extract one volume for its full resolution`);
      want = seriesLimit(want);
    }
    const same = item.info && binning(item.info, want) === binning(item.info, item.box) && !item.coarse;
    item.box = want;
    if (same) { panel.renderItem(item); syncHash(); return; }
    reloadMap(item, { keep: 'fraction' }).then(() => log(`#${item.id}: ${describeDetail(item)}`)).catch((err) => fail(err));
  },
  setFullRes(item, on) {
    if (item.series) { fail('Pick a finer detail in the card of the series (full resolution: extract one volume as a map)'); return; }
    if (!!item.full === on) return;
    ctl.setDetail(item, on ? 0 : null);
  },
  setLowpass(item, resolution) {
    if ((item.lowpass || 0) === resolution) return;
    item.lowpass = resolution;
    reloadMap(item, { keep: 'fraction' })
      .then(() => log(resolution ? `#${item.id} low-pass filtered to ${resolution} Å` : `#${item.id}: no filter`))
      .catch((err) => fail(err));
  },
  setZone(item, zone) {
    if (item.series) { fail('Zones are not available for a volume series'); return; }
    item.zone = zone;
    reloadMap(item, { keep: 'absolute' }).then(() => log(zone ? `Zone: #${item.id} within ${zone.radius} Å of #${zone.model}` : `Zone off for #${item.id}`))
      .catch((err) => fail(err, 'Zone: ')); // the settings of the map shown are put back
  },
  describeDetail: (item) => describeDetail(item),
  focus: (item) => { scene.focusItem(item); scene.reapplySlab(400); },
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
  // slab: null (off), a percentage of the thickness of what is shown, or { angstrom }
  setSlab(value) {
    V.slab = value;
    scene.setSlab(value === null ? null : slabThickness(value), V.items);
    toolbar.refresh();
    syncHashSoon();
  },
  slabThickness: () => (V.slab === null ? null : slabThickness(V.slab)),
  setSpin(on) { V.spin = on; scene.setSpin(on); toolbar.refresh(); },
  setProjection(mode) { V.ortho = mode === 'orthographic'; scene.setProjection(mode); toolbar.refresh(); },
  setSoftLighting(on) {
    if (!scene.setSoftLighting(on)) { fail('Soft lighting is not available with this graphics card'); return; }
    V.soft = on;
    toolbar.refresh();
  },
  // enlarge: true for the large slices beside the view, false for the small ones, undefined as they were
  showSlices(item, { enlarge } = {}) {
    slices.show(item);
    if (enlarge === true) slices.enlarge();
    else if (enlarge === false) slices.setSplit(false);
    toolbar.refresh();
  },
  hideSlices,
  saveImage: (name, opts = {}) => scene.saveImage(name, { ...opts, overlay: legendItem() ? drawLegend : null }),
  record: (kind, seconds) => startRecording(kind, seconds),
  stopRecording,
  clearDistances: () => scene.clearDistances(),
  async copyLink() {
    syncHash();
    try {
      await navigator.clipboard.writeText(location.href);
      toast('Link to this view copied', 'success', 3000);
      return 'Link to this view copied to the clipboard';
    } catch {
      return `Link to this view: ${location.href}`;
    }
  },
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
  if (statusText.startsWith(`#${item.id}  ${item.label}`)) { statusText = mapStatus(item); setStatus(); } // the volume under the pointer changed
  playerBar.refresh();
  panel.refreshLevel(item);
  if (slices.item === item) slices.refresh(item);
}

function hideSlices() {
  slices.hide();
  toolbar.refresh();
}

function toggleSlices() {
  if (slices.item) hideSlices();
  else { const m = activeMap(); if (m) ctl.showSlices(m); else fail('No map displayed'); }
}

// ------------------------------------------------------------- toolbar
function buildToolbar() {
  const root = $('vw-tools');
  const tool = (label, ic, title, onclick) => h('button', { type: 'button', class: 'vw-tool', title, 'aria-label': title, onclick },
    icon(ic), h('span', { class: 'lbl' }, label));
  const t = {
    reset: tool('Reset', 'refresh', 'Reset the view (R)', () => resetView()),
    spin: tool('Spin', 'rotate', 'Rotate continuously (S)', () => ctl.setSpin(!V.spin)),
    slab: tool('Slab', 'layers', 'Keep only a slab around the centre (the cut density is drawn filled)', () => {
      ctl.setSlab(V.slab === null ? 30 : null);
      if (V.slab !== null) log(`Slab: 30 % of the thickness shown (${ctl.slabThickness().toFixed(0)} Å) — slider beside the button, or slab 10A`);
    }),
    ortho: tool('Ortho', 'cube', 'Orthographic projection', () => ctl.setProjection(V.ortho ? 'perspective' : 'orthographic')),
    soft: tool('Soft light', 'sun', 'Ambient occlusion (soft lighting)', () => ctl.setSoftLighting(!V.soft)),
    slices: tool('Slices', 'grid', 'Orthogonal 2D slices of the active map (L)', () => toggleSlices()),
    image: tool('Image', 'camera', 'Save a PNG image of the view (2× the window size, with the colour scale)', async () => {
      try { const s = await ctl.saveImage('cryoplug.png'); log(`Saved cryoplug.png (${s.width}×${s.height})`); } catch (err) { fail(err); }
    }),
    movie: tool('Movie', 'record', 'Record a video of the view', (e) => {
      if (rec.recorder) { stopRecording(); return; }
      const series = activeSeries();
      popupMenu(e.currentTarget, [
        { label: series ? `Play #${series.id} once and record it` : 'Record a series (none open)', disabled: !series, ic: 'play', action: () => startRecording('series') },
        { label: 'Record a full turn (12 s)', ic: 'rotate', action: () => startRecording('spin', 12) },
        { label: 'Record until stopped', ic: 'record', action: () => startRecording('free') },
      ]);
    }),
    mask: tool('Mask', 'edit', 'Draw a mask on the map with the mouse (spheres, boxes, regions, zones), saved as a job', () => {
      maskEditor.toggle();
      toolbar.refresh();
    }),
    link: tool('Link', 'link', 'Copy a link to this view (items, settings, camera)', async () => { log(await ctl.copyLink()); }),
    help: tool('Help', 'help', 'Mouse, keys and commands (?)', () => helpDialog()),
  };
  const slab = h('input', {
    type: 'range', class: 'vw-slab', min: 2, max: 99, step: 1, value: 30, hidden: true, 'aria-label': 'Slab thickness (% of the thickness shown)',
    oninput: (e) => ctl.setSlab(Number(e.target.value)),
  });
  const bgs = h('div', { class: 'vw-bgs', role: 'group', 'aria-label': 'Background' }, BACKGROUNDS.map(([name, color]) => h('button', {
    type: 'button', class: 'vw-bg', title: `${name} background`, 'aria-label': `${name} background`, style: { background: `#${color.toString(16).padStart(6, '0')}` },
    dataset: { color: String(color) }, onclick: () => ctl.setBackground(color),
  })));
  clear(root, t.reset, t.spin, t.slab, slab, t.ortho, t.soft, h('span', { class: 'vw-sep' }), bgs, h('span', { class: 'vw-sep' }),
    t.slices, t.mask, t.image, t.movie, t.link, t.help);
  return {
    refresh() {
      t.spin.classList.toggle('on', V.spin);
      t.slab.classList.toggle('on', V.slab !== null);
      slab.hidden = V.slab === null;
      if (typeof V.slab === 'number' && document.activeElement !== slab) slab.value = String(V.slab);
      slab.title = V.slab === null ? '' : `Slab: ${ctl.slabThickness().toFixed(0)} Å`;
      t.ortho.classList.toggle('on', V.ortho);
      t.soft.classList.toggle('on', V.soft);
      t.slices.classList.toggle('on', !!slices.item);
      t.mask.classList.toggle('on', !!maskEditor?.isOpen());
      t.movie.classList.toggle('rec', !!rec.recorder);
      t.movie.querySelector('.lbl').textContent = rec.recorder ? 'Stop' : 'Movie';
      t.movie.title = rec.recorder ? 'Stop recording and save the video' : 'Record a video of the view';
      bgs.querySelectorAll('.vw-bg').forEach((b) => b.classList.toggle('on', Number(b.dataset.color) === V.background));
    },
  };
}

// ---------------------------------------------------------------- player
// Track of the slider: one block per volume, bright once loaded (red when it could not be read), aligned with the
// positions of the thumb (14 px wide).
function bufferGradient(s) {
  const n = s.frames.length;
  const colour = (f) => (f.volume ? 'rgba(134, 182, 239, 0.9)' : f.state === 'failed' ? 'rgba(229, 96, 80, 0.9)' : 'rgba(255, 255, 255, 0.2)');
  const at = (t) => `calc(7px + (100% - 14px) * ${t.toFixed(4)})`;
  const stops = [];
  let start = 0;
  for (let i = 1; i <= n; i++) {
    if (i < n && colour(s.frames[i]) === colour(s.frames[start])) continue;
    const c = colour(s.frames[start]);
    stops.push(`${c} ${start === 0 ? '0%' : at((start - 0.5) / (n - 1))}`, `${c} ${i === n ? '100%' : at((i - 0.5) / (n - 1))}`);
    start = i;
  }
  return `linear-gradient(to right, ${stops.join(', ')})`;
}

// Transport bar over the 3D view for the series being played (the active one, or the first).
function buildPlayerBar() {
  const root = $('vw-player');
  const control = (ic, title, onclick) => h('button', { type: 'button', class: 'vw-pbtn', title, 'aria-label': title, onclick }, icon(ic));
  const target = () => activeSeries();
  const play = control('play', 'Play (space)', () => { const it = target(); if (it) ctl.togglePlay(it); });
  const prev = control('skip-back', 'Previous volume (,)', () => { const it = target(); if (it) ctl.stepFrame(it, -1); });
  const next = control('skip-fwd', 'Next volume (.)', () => { const it = target(); if (it) ctl.stepFrame(it, 1); });
  // While the thumb is held, the bar does not move it: the volume follows the thumb, not the other way round.
  let dragging = false;
  const slider = h('input', { type: 'range', class: 'vw-pslider', min: 1, max: 2, step: 1, value: 1, 'aria-label': 'Volume of the series' });
  slider.addEventListener('pointerdown', () => { dragging = true; const it = target(); if (it) player.pause(it); });
  const release = () => { if (dragging) { dragging = false; bar.refresh(); } };
  window.addEventListener('pointerup', release);
  window.addEventListener('pointercancel', release);
  slider.addEventListener('change', release);
  slider.addEventListener('input', () => {
    const it = target();
    if (!it) return;
    player.pause(it);
    player.go(it, Number(slider.value) - 1);
    bar.refresh();
  });
  const which = h('span', { class: 'vw-pid' });
  const label = h('span', { class: 'vw-plabel', 'aria-live': 'off' });
  const speed = h('select', { class: 'vw-pspeed', 'aria-label': 'Playback speed', title: 'Volumes per second',
    onchange: (e) => { const it = target(); if (it) ctl.setSpeed(it, Number(e.target.value)); } });
  const mode = h('button', { type: 'button', class: 'vw-pmode', title: 'Loop, or play back and forth',
    onclick: () => { const it = target(); if (it) ctl.setPlayMode(it, it.series.mode === 'loop' ? 'bounce' : 'loop'); } });
  clear(root, which, prev, play, next, slider, label, speed, mode);
  const bar = {
    refresh() {
      const it = target();
      root.hidden = !it;
      if (!it) return;
      const s = it.series;
      const n = s.frames.length;
      const shown = s.want; // the volume asked for: shown, or being prepared
      const frame = s.frames[shown];
      which.textContent = `#${it.id}`;
      which.title = it.label;
      if (play.dataset.state !== String(s.playing)) {
        play.dataset.state = String(s.playing);
        clear(play, icon(s.playing ? 'pause' : 'play'));
        play.title = s.playing ? 'Pause (space)' : 'Play (space)';
        play.setAttribute('aria-label', play.title);
        play.classList.toggle('on', s.playing);
      }
      slider.max = String(n);
      if (!dragging) slider.value = String(shown + 1);
      slider.style.setProperty('--buffer', bufferGradient(s));
      slider.setAttribute('aria-valuetext', `${shown + 1} of ${n}: ${frame.label}`);
      const waiting = shown !== s.index && !frame.volume;
      label.textContent = `${shown + 1}/${n} · ${frame.label}${waiting ? ' · loading…' : ''}${s.loaded < n ? ` · ${s.loaded}/${n} loaded` : ''}`;
      label.title = frame.path;
      const speeds = SPEEDS.includes(s.fps) ? SPEEDS : [...SPEEDS, s.fps].sort((a, b) => a - b);
      if (speed.options.length !== speeds.length) clear(speed, speeds.map((f) => h('option', { value: f }, `${f}/s`)));
      speed.value = String(s.fps);
      mode.textContent = s.mode === 'loop' ? 'Loop' : 'Back & forth';
    },
  };
  return bar;
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
        ['Wheel', 'Zoom'], ['Shift + wheel', 'Move the clipping planes (the cut density is drawn filled)'], ['Click an atom', 'Centre on its residue (Coot-like)'],
        ['Shift + click two atoms', 'Measure their distance (distance clear removes the measurements)'],
        ['Histogram', 'Drag the line to set the contour level of a map'],
        ['Slices', 'Click: move the crosshair · wheel: next slice (large slices: zoom; Shift + wheel: next slice) · drag: pan · double-click: enlarge or fit'],
        ['Mask editor (Mask button)', 'Click on the map: place a sphere, a box, a cylinder (two clicks) or pick a region of density · Ctrl + Z: undo · Delete: remove the selected operation']]),
      h('h4', {}, 'Keys'),
      table([['+  /  −', 'Raise / lower the level of the active map by 0.1 σ (Shift: 0.5 σ)'], ['M', 'Next map style (surface, mesh, transparent)'],
        ['R', 'Reset the view'], ['S', 'Spin on / off'], ['L', 'Slices on / off'], ['Space', 'Play / pause a volume series'],
        [',  /  .', 'Previous / next volume of the series'], ['↑ / ↓ on a slice', 'Next / previous slice (Page Up / Down: 5 slices)'],
        [':', 'Go to the command line'], ['Esc', 'Clear the selection'], ['↑ / ↓ in the command line', 'Previous / next command']]),
      h('h4', {}, 'Commands'),
      table(COMMAND_HELP),
      h('p', { class: 'muted small' }, 'Maps arrive as binned copies (at most 200 voxels per side, half-size 16-bit transfers) for speed: “Detail” in a map’s card picks '
        + 'a finer grid, up to the original one, and “Filter” shows it at a lower resolution. The volumes of a series are binned more (80–128 voxels) and all '
        + 'loaded once, the one asked for first, so that they play smoothly. The address of the page keeps what is open, the settings and the camera.')),
  });
}

// ------------------------------------------------------------- keyboard
function bindKeys() {
  document.addEventListener('keydown', (e) => {
    const k = e.key;
    if (!e.target.closest?.('input, textarea, select, [contenteditable]') && !document.querySelector('.modal-backdrop')
        && maskEditor?.onKey(e)) { e.preventDefault(); return; }
    // the series slider keeps the player keys (its own arrows move it)
    const onSlider = e.target.classList?.contains('vw-pslider') && [' ', ',', '.', '<', '>'].includes(k);
    if ((!onSlider && e.target.closest('input, textarea, select, [contenteditable]')) || e.ctrlKey || e.metaKey || e.altKey) return;
    if (document.querySelector('.modal-backdrop')) return;
    const map = activeMap();
    if ((k === '+' || k === '=') && map) ctl.stepLevel(map, e.shiftKey ? 0.5 : 0.1);
    else if ((k === '-' || k === '_') && map) ctl.stepLevel(map, e.shiftKey ? -0.5 : -0.1);
    else if (k === 'r' || k === 'R') resetView();
    else if (k === 's' || k === 'S') ctl.setSpin(!V.spin);
    else if ((k === 'm' || k === 'M') && map) ctl.setStyle(map, { surface: 'mesh', mesh: 'transparent', transparent: 'surface' }[map.style]);
    else if (k === 'l' || k === 'L') toggleSlices();
    else if (k === ' ' && activeSeries()) ctl.togglePlay(activeSeries());
    else if ((k === ',' || k === '<') && activeSeries()) ctl.stepFrame(activeSeries(), -1);
    else if ((k === '.' || k === '>') && activeSeries()) ctl.stepFrame(activeSeries(), 1);
    else if (k === ':') { e.preventDefault(); $('vw-cmdline').focus(); }
    else if (k === '?') helpDialog();
    else if (k === 'Escape') { firstAtom = null; scene.select(null); }
    else return;
    e.preventDefault();
  });
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
let statusText = HINT;
function setStatus(text) { $('vw-status').textContent = text || statusText; }
function mapStatus(item) {
  const frame = item.series ? `  ·  ${item.series.frames[item.series.index].label}` : '';
  return `#${item.id}  ${item.label}${frame}  ·  level ${fmtLevel(item.level)}  (${toSigma(item.stats, item.level).toFixed(2)} σ)`;
}

function bindPointer() {
  setStatus(HINT);
  scene.onHover((d) => {
    if (!d) { statusText = HINT; setStatus(); return; }
    if (d.kind === 'atom') {
      const item = V.items.find((i) => i.kind === 'model' && i.structure === d.root);
      statusText = d.chain === undefined ? `${item ? `#${item.id} ` : ''}atom`
        : `${item ? `#${item.id}  ` : ''}/${d.chain} ${d.resn} ${d.resi}${d.ins}  ${d.atom}  ·  ${d.element}  ·  B ${Number(d.b).toFixed(1)}`;
    } else {
      const item = V.items.find((i) => i.kind === 'map' && i.volume === d.volume);
      statusText = item ? mapStatus(item) : 'Map';
    }
    setStatus();
  });
  scene.onClick((d, e) => {
    if (maskEditor?.isOpen() && maskEditor.click(d, e)) return; // the mask editor places its shapes
    if (e.button !== undefined && e.button !== 0 && e.button !== 1) return;
    if (d?.kind !== 'atom' || d.chain === undefined) return;
    if (e.modifiers?.shift) { measure(d); return; }
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
  let layoutTimer = 0;
  slices = createSlices($('vw-slices'), {
    stage: $('vw-stage'),
    onLayout: () => { cancelAnimationFrame(layoutTimer); layoutTimer = requestAnimationFrame(() => scene.resize()); toolbar?.refresh(); },
    onHover: (text) => setStatus(text),
    onClose: () => hideSlices(),
  });
  toolbar = buildToolbar();
  player = createSeriesPlayer({
    fetch: (item, frame, settings) => fetchMap(mapUrl(item, { path: frame.path, box: settings.box, lowpass: settings.lowpass })),
    settings: (item) => ({ box: item.box, lowpass: item.lowpass }),
    onFrame: frameShown,
    onLoad: (item) => { playerBar.refresh(); panel.refreshLevel(item); },
    onError: (err, what = '') => fail(err, what),
  });
  playerBar = buildPlayerBar();
  panel = createPanel($('vw-items'), ctl);
  commands = createCommands(ctl);
  maskEditor = createMaskEditor($('vw-mask'), {
    items: () => V.items, activeMap, project: () => V.project, log,
    syncHash: () => { syncHashSoon(); toolbar?.refresh(); },
  });
  ctl.setBackground(V.background);
  bindPointer();
  bindKeys();
  bindCommandLine();
  $('vw-open').addEventListener('click', () => openDialog());
  let resizeTimer = 0;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => { panel.render(); slices.redraw(); }, 200); // histograms are drawn at the panel's width
  });
  log('Type help for the commands, ? for mouse and keys.', 'echo');
  try { collectColorSources(await api.jobs(V.project)); } catch { /* colouring sources are optional */ }
  panel.render();
  await openItems(spec.items || [], { view: spec.view });
  if (!V.items.length) panel.render();
  if (spec.mask && V.items.some((i) => i.kind === 'map')) maskEditor.open(spec.mask);
  if (V.items.length && (typeof spec.slab === 'number' || spec.slab?.angstrom > 0)) setTimeout(() => ctl.setSlab(spec.slab), 300);
  playerBar.refresh();
  V.ready = true;
  syncHash();
  scene.onCamera(syncHashSoon);
}

main().catch((err) => showError(err));
