// ChimeraX-like command line. Each command maps onto the same actions as the buttons and the models panel.

export const COMMAND_HELP = [
  ['level <value>[σ] [#id] · level <mass>kDa', 'Contour level of a map, absolute, in σ or enclosing a protein mass (level 3σ · level 250kDa)'],
  ['style [#id] surface | mesh | transparent', 'Map style'],
  ['style [#id] cartoon | both | sticks | spheres | surface | cartoon+surface', 'Model style (both = cartoon + side chains)'],
  ['transparency [#id] <0–100>', 'Transparency of a map surface, or of the surface of a model'],
  ['color [#id] <name | #hex>', 'Single colour for a map or a model'],
  ['color [#id] bychain | byss | byelement | bfactor | rainbow | hydrophobicity', 'Model colouring'],
  ['color [#id] localres [min max]', 'Colour a map by local resolution (blue best → red worst, in Å)'],
  ['color [#id] variability [min max]', 'Colour a map by the variability of a volume series (blue stable → red variable, in σ)'],
  ['show | hide [#id]', 'Show or hide items (all of them without #id)'],
  ['view', 'Reset the view'],
  ['view #id | /A:45 | /A', 'Centre on an item, a residue or a chain'],
  ['zone [#map] [#model] [radius] | zone off', 'Show the map only near the model (ChimeraX volume zone)'],
  ['detail [#id] <voxels> | full | default', 'How finely a map is shown: largest side in voxels (fewer: faster), or the original grid'],
  ['lowpass [#id] <Å> | off', 'Show a map low-pass filtered to a resolution (flexible regions, weak density)'],
  ['volume [#id] level <v> style <s> color <c> transparency <t>', 'Several map settings at once, ChimeraX syntax (also step <n>, sdLevel, rmsLevel, encloseVolume)'],
  ['volume zone #map nearAtoms #model range <r>', 'Same as zone, ChimeraX syntax'],
  ['molmap [#model[/A,B:10-250]] <Å> [displayThreshold 0.95] [onGrid #map] [erase | keep]', 'Mask from the density simulated from a model (ChimeraX molmap): added to the mask editor, cut at the contour enclosing 95 % of it'],
  ['move x | y | z | <vector> <Å> #id [coordinateSystem #id]', 'Move a map, mask or model in space (screen axes, or those of an item), as ChimeraX’s move … models #id'],
  ['turn x | y | z | <vector> <degrees> #id [center x,y,z | #id] [coordinateSystem #id]', 'Rotate an item in space about its centre (or the centre given)'],
  ['view initial [#id]', 'Put moved items back where their files put them'],
  ['fitmap #id[/A,B] inMap #map [resolution <Å>] [metric overlap | correlation | cam] [search <n>] [radius <Å>]', 'Rigid-body fit of a model, map or mask into a map, as ChimeraX’s fitmap (atoms: average map value; with a resolution: the simulated map)'],
  ['bg black | white | gray | <colour>', 'Background (also: set bgColor white)'],
  ['slab <1–99> | slab <Å>A | slab off', 'Keep only a slab around the centre, Coot-like: % of the thickness shown, or Å (slab 12A); the cut density is drawn filled'],
  ['spin [off]', 'Rotate continuously'],
  ['camera ortho | persp', 'Projection'],
  ['lighting soft | simple', 'Ambient occlusion on or off'],
  ['slices [#id] [big | small | off]', 'Orthogonal 2D slices of a map, small beside the view or large in a split view'],
  ['fullres [#id] [off]', 'Full-resolution map instead of the binned preview'],
  ['play [#id] [volumes per second]', 'Play a volume series (cryoDRGN clusters, trajectory, 3D variability frames)'],
  ['stop', 'Stop playing (also: pause; ChimeraX: vseries play #1 · vseries stop)'],
  ['frame [#id] <n> | next | prev | first | last', 'Show one volume of a series'],
  ['record series | spin [seconds] | stop', 'Record a video of the view: one pass of the series, a full turn, or until stopped (also: movie record)'],
  ['distance | distance clear', 'Shift + click two atoms to measure their distance; clear removes the measurements'],
  ['save [name.png] [scale <n>] [transparent]', 'Save an image of the view (with the colour scale of a coloured map)'],
  ['link', 'Copy a link to this view (items, settings, camera)'],
  ['open', 'Open other maps or models of the project'],
  ['close #id | close all', 'Remove items'],
];

const MODEL_COLOR_WORDS = {
  bychain: 'chain', chain: 'chain', byss: 'ss', bysecondary: 'ss', ss: 'ss', byelement: 'element', byhetero: 'element',
  element: 'element', byatom: 'element', bfactor: 'bfactor', byattribute: 'bfactor', rainbow: 'rainbow', bypolymer: 'chain',
  hydrophobicity: 'hydrophobicity', byhydrophobicity: 'hydrophobicity', hydrophobic: 'hydrophobicity',
};
const MAP_STYLE_WORDS = { surface: 'surface', solid: 'surface', mesh: 'mesh', wire: 'mesh', wireframe: 'mesh', transparent: 'transparent' };
const MODEL_STYLE_WORDS = {
  cartoon: 'cartoon', ribbon: 'cartoon', both: 'both', sidechains: 'both', sticks: 'sticks', stick: 'sticks', atoms: 'sticks',
  spheres: 'spheres', sphere: 'spheres', spacefill: 'spheres', surface: 'surface', surf: 'surface',
  'cartoon+surface': 'cartoon-surface', 'cartoon-surface': 'cartoon-surface', ribbonsurface: 'cartoon-surface',
};

let colorCtx = null;
export function parseColor(word) {
  if (/^#?[0-9a-f]{6}$/i.test(word)) return parseInt(word.replace('#', ''), 16);
  if (/^#[0-9a-f]{3}$/i.test(word)) return parseInt(word.slice(1).split('').map((c) => c + c).join(''), 16);
  colorCtx ||= document.createElement('canvas').getContext('2d');
  colorCtx.fillStyle = '#010203';
  colorCtx.fillStyle = word;
  const v = colorCtx.fillStyle;
  if (v === '#010203' && word.toLowerCase() !== '#010203') throw new Error(`Unknown colour '${word}'`);
  return parseInt(v.slice(1), 16);
}

function parseLevel(words) {
  const text = words.join('').replace(/^mass/i, '');
  const mass = /^((?:\d+\.?\d*|\.\d+))(kda|kd|mda)$/i.exec(text);
  if (mass) {
    const kda = Number(mass[1]) * (/^mda$/i.test(mass[2]) ? 1000 : 1);
    if (!(kda > 0)) throw new Error('The mass enclosed must be more than 0 kDa');
    return { mass: kda };
  }
  const m = /^(-?(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?)(σ|s|sd|sigma|rms)?$/i.exec(text);
  if (!m) throw new Error(`Expected a level such as 0.42, 3σ or 250kDa, got '${words.join(' ')}'`);
  return { value: Number(m[1]), sigma: !!m[2] };
}

const onOff = (word, current) => {
  if (word === undefined) return !current;
  if (/^(on|true|yes|1)$/i.test(word)) return true;
  if (/^(off|false|no|0)$/i.test(word)) return false;
  throw new Error(`Expected on or off, got '${word}'`);
};

export function createCommands(ctl) {
  function split(words) {
    const items = [];
    const rest = [];
    for (const w of words) {
      const m = /^#(\d+)$/.exec(w);
      if (m) {
        const item = ctl.byId(Number(m[1]));
        if (!item) throw new Error(`No item #${m[1]}`);
        items.push(item);
      } else {
        rest.push(w);
      }
    }
    return { items, rest };
  }
  const maps = (items) => {
    const list = items.length ? items.filter((i) => i.kind === 'map') : [ctl.activeMap()].filter(Boolean);
    if (!list.length) throw new Error(items.length ? 'These items are not maps' : 'No map displayed');
    return list;
  };
  const models = (items) => {
    const list = items.length ? items.filter((i) => i.kind === 'model') : [ctl.activeModel()].filter(Boolean);
    if (!list.length) throw new Error(items.length ? 'These items are not models' : 'No model displayed');
    return list;
  };
  const ids = (list) => list.map((i) => `#${i.id}`).join(' ');
  const series = (items) => {
    const list = items.length ? items.filter((i) => i.series) : [ctl.activeSeries()].filter(Boolean);
    if (!list.length) throw new Error(items.length ? 'These items are not volume series' : 'No volume series displayed (open cluster volumes, a trajectory or 3D variability frames)');
    return list;
  };

  function setLevels(list, words) {
    const { value, sigma, mass } = parseLevel(words);
    if (mass !== undefined) {
      const missed = list.filter((m) => !ctl.setLevelByMass(m, mass));
      if (missed.length) throw new Error(`${ids(missed)}: ${mass} kDa is more than the box holds`);
      return `Level of ${ids(list)}: encloses ${mass} kDa of protein`;
    }
    list.forEach((m) => ctl.setLevel(m, sigma ? m.stats.mean + value * m.stats.sigma : value));
    return `Level of ${ids(list)} set to ${words.join('')}`;
  }

  // ChimeraX conventions: sdLevel in SD from zero, rmsLevel in RMS from zero, encloseVolume in Å³
  function chimeraLevel(list, key, value) {
    const v = Number(value);
    if (!Number.isFinite(v)) throw new Error(`${key} needs a number`);
    if (key === 'enclosevolume') return setLevels(list, [`${(v / 1.21 / 1000).toFixed(3)}kDa`]);
    list.forEach((m) => ctl.setLevel(m, key === 'sdlevel' ? v * m.stats.sigma : v * Math.hypot(m.stats.mean, m.stats.sigma)));
    return `${key} ${v}`;
  }

  function detail(list, word) {
    const w = (word || '').toLowerCase();
    if (!w) return list.map((m) => `#${m.id}: ${ctl.describeDetail(m)}`).join(' · '); // what is shown now
    const box = w === 'full' || w === '0' ? 0 : w === 'default' || w === 'preview' ? null : Number(w);
    if (box !== null && box !== 0 && !(box >= 32 && box <= 2048)) throw new Error('detail [#id] <voxels per side, e.g. 128 or 300> | full | default');
    list.forEach((m) => ctl.setDetail(m, box)); // reported once reloaded
    return null;
  }

  function colorBySource(list, rest, kind) {
    const ms = list.length ? maps(list) : [ctl.activeMap()].filter(Boolean);
    if (!ms.length) throw new Error('No map displayed');
    const sources = ctl.colorSources().filter((s) => s.kind === kind);
    if (!sources.length) {
      throw new Error(kind === 'variability' ? 'No variability map in this project (run a Volume series analysis job)'
        : 'No local resolution map in this project (run a Local resolution job)');
    }
    const range = rest.slice(0, 2).map(Number);
    if (rest.length && !(range.length === 2 && range[1] > range[0])) {
      throw new Error(kind === 'variability' ? 'Scale: color #1 variability 0.1 0.6 (lowest and highest variability, in σ)'
        : 'Scale: color #1 localres 3 6 (best and worst resolution in Å)');
    }
    for (const m of ms) {
      const source = sources.find((s) => s.colourMap === m.path) || sources[0];
      ctl.setColorBy(m, rest.length ? { ...source, range } : source); // reported once loaded
    }
    return null;
  }

  function colorItems(list, word) {
    const mode = MODEL_COLOR_WORDS[word.toLowerCase()];
    if (mode) {
      const ms = models(list);
      ms.forEach((m) => ctl.setColorMode(m, mode));
      return `Colouring of ${ids(ms)}: ${mode}`;
    }
    const color = parseColor(word);
    const targets = list.length ? list : [ctl.active()].filter(Boolean);
    if (!targets.length) throw new Error('Nothing to colour');
    targets.forEach((i) => ctl.setColor(i, color));
    return `Colour of ${ids(targets)}: ${word}`;
  }

  function zone(list, rest) {
    if (rest[0] && /^off$/i.test(rest[0])) {
      const ms = list.length ? maps(list) : ctl.items().filter((i) => i.kind === 'map' && i.zone);
      ms.forEach((m) => ctl.setZone(m, null)); // reported once the map is reloaded
      return ms.length ? null : 'No zone to remove';
    }
    const map = (list.find((i) => i.kind === 'map')) || ctl.activeMap();
    const model = (list.find((i) => i.kind === 'model')) || ctl.activeModel();
    if (!map) throw new Error('No map displayed');
    if (!model) throw new Error('A model is needed for a zone');
    const words = rest.filter((w) => !/^(nearAtoms|range)$/i.test(w));
    const radius = words.length ? Number(words[0]) : 3;
    if (!(radius >= 0.5 && radius <= 30)) throw new Error('The zone radius must be between 0.5 and 30 Å');
    ctl.setZone(map, { model: model.id, radius }); // reported once the zone is computed
    return null;
  }

  // ChimeraX atom spec after the model number: /A,B:10-250,300-400 -> "A:10-250, A:300-400, B:10-250, B:300-400"
  function chainSelection(spec) {
    const [chains, ranges] = spec.replace(/^\//, '').split(':');
    const names = chains.split(',').map((c) => c.trim()).filter(Boolean);
    const spans = (ranges || '').split(',').map((r) => r.trim()).filter(Boolean);
    if (!names.length || names.some((c) => !/^[A-Za-z0-9]+$/.test(c)) || spans.some((r) => !/^-?\d+(-(-?\d+))?$/.test(r))) {
      throw new Error(`Cannot read the chains '${spec}': write them as in ChimeraX, e.g. #2/A,B or #2/C:10-250`);
    }
    return names.flatMap((c) => (spans.length ? spans.map((r) => `${c}:${r}`) : [c])).join(', ');
  }

  const itemArg = (word, what) => {
    const m = /^#(\d+)$/.exec(word || '');
    const item = m && ctl.byId(Number(m[1]));
    if (!item) throw new Error(`${what}: an item such as #2${word ? `, not '${word}'` : ''}`);
    return item;
  };

  // An axis of ChimeraX's move / turn: x, y, z or a vector, in the axes of the screen, or of an item (coordinateSystem).
  function axisVector(word, frame) {
    const unit = { x: [1, 0, 0], y: [0, 1, 0], z: [0, 0, 1] }[word.toLowerCase()];
    const v = unit || word.split(',').map(Number);
    if (v.length !== 3 || !v.every(Number.isFinite) || !Math.hypot(...v)) throw new Error(`Axis: x, y, z or a vector such as 1,0,0 — got '${word}'`);
    if (frame) { // the axes of that item, as it is turned in the viewer
      const m = frame.transform;
      return m ? [0, 1, 2].map((r) => m[r * 4] * v[0] + m[r * 4 + 1] * v[1] + m[r * 4 + 2] * v[2]) : v;
    }
    const f = ctl.screenFrame();
    return [0, 1, 2].map((k) => f.right[k] * v[0] + f.up[k] * v[1] + f.toward[k] * v[2]);
  }

  // move x 10 #2 · move x 10 models #2 · turn y 30 #2 center #1 coordinateSystem #1
  async function moveTurn(kind, words) {
    let axisWord = null;
    let amount = null;
    let item = null;
    let frame = null;
    let center = null;
    for (let k = 0; k < words.length; k++) {
      const w = words[k];
      const lw = w.toLowerCase();
      if (lw === 'models') continue;
      if (/^#\d+$/.test(w)) { item = itemArg(w, kind); continue; }
      if (lw === 'coordinatesystem') { frame = itemArg(words[++k], 'coordinateSystem'); continue; }
      if (lw === 'frames') { k += 1; continue; } // ChimeraX animates the motion: here it is done at once
      if (lw === 'center' && kind === 'turn') {
        const v = words[++k] || '';
        if (v.startsWith('#')) center = ctl.itemCenter(itemArg(v, 'center'));
        else center = v.split(',').map(Number);
        if (!center || center.length !== 3 || !center.every(Number.isFinite)) throw new Error('center: x,y,z in Å, or an item such as #1');
        continue;
      }
      if (axisWord === null) { axisWord = w; continue; }
      if (amount === null) {
        amount = Number(w.replace(/(a|å|°|deg)$/i, ''));
        if (!Number.isFinite(amount)) throw new Error(`${kind}: ${kind === 'move' ? 'a distance in Å' : 'an angle in degrees'}, not '${w}'`);
        continue;
      }
      throw new Error(`${kind}: unexpected '${w}'`);
    }
    const usage = kind === 'move' ? 'move x 5 #2 (Å along the screen x axis)' : 'turn y 30 #2 (degrees about the screen y axis)';
    if (axisWord === null || amount === null) throw new Error(usage);
    if (!item) throw new Error(`Say which item to ${kind}: ${usage} — without an item ChimeraX moves the camera; drag with the mouse for that`);
    const axis = axisVector(axisWord, frame);
    if (kind === 'move') {
      const n = Math.hypot(...axis);
      await ctl.moveItem(item, axis.map((v) => (v / n) * amount));
    } else {
      await ctl.turnItem(item, axis, amount, center);
    }
    return null; // reported by Move & fit
  }

  // fitmap #2[/A,B] inMap #1 [resolution 8] [metric correlation] [search 20] [radius 10] [shift false] [rotate false]
  async function fitmap(words) {
    let item = null;
    let target = null;
    let chains = '';
    const opts = {};
    for (let k = 0; k < words.length; k++) {
      const w = words[k];
      const lw = w.toLowerCase();
      const spec = /^#(\d+)(\/.+)?$/.exec(w);
      if (lw === 'inmap' || lw === 'in') {
        target = itemArg(words[++k], 'inMap');
        if (target.kind !== 'map' || target.series) throw new Error(`inMap: #${target.id} is not a map`);
      } else if (spec && !item) {
        item = itemArg(`#${spec[1]}`, 'fitmap');
        if (spec[2]) {
          if (item.kind !== 'model') throw new Error(`#${item.id} is not a model: chains are chosen on models`);
          chains = chainSelection(spec[2]);
        }
      } else if (['resolution', 'search', 'radius'].includes(lw)) {
        const v = Number(words[++k]);
        if (!Number.isFinite(v) || v < 0) throw new Error(`${w}: a number (0 or more)`);
        opts[lw] = v;
      } else if (lw === 'metric') {
        const v = (words[++k] || '').toLowerCase();
        const m = v && ['overlap', 'correlation', 'cam'].find((x) => x.startsWith(v));
        if (!m) throw new Error('metric: overlap, correlation or cam');
        opts.metric = m;
      } else if (lw === 'shift' || lw === 'rotate') {
        opts[lw] = onOff(words[++k]);
      } else if (/^(envelope|zeros|symmetric|movewholemolecules|placement|clusterangle|clustershift|asymmetricunit|levelinside|sequence|eachmodel|listfits|logfits|maxsteps|gridstepmin|gridstepmax|subtractmaps|seed)$/.test(lw)) {
        throw new Error(`fitmap: ${w} is not available here (supported: resolution, metric, search, radius, shift, rotate)`);
      } else {
        throw new Error(`fitmap: unexpected '${w}' (fitmap #2 inMap #1 resolution 8)`);
      }
    }
    if (!item) throw new Error('fitmap #2 inMap #1: the item to fit first');
    if (!target) throw new Error(`fitmap #${item.id} inMap #1: the map to fit into`);
    if (target === item) throw new Error('An item cannot be fitted into itself');
    if (item.kind === 'model' && !opts.resolution && opts.metric && opts.metric !== 'overlap') {
      throw new Error(`Correlation needs a resolution for a model: fitmap #${item.id} inMap #${target.id} resolution 8 metric ${opts.metric}`);
    }
    await ctl.fitItem(item, target, { ...opts, chains, fromCommand: true });
    return null; // the fit is written in the log
  }

  // molmap #2/A,B 10 [displayThreshold 0.95] [onGrid #1]: ChimeraX's command, turned into a mask operation
  function molmap(words) {
    let model = null;
    let selection = '';
    let resolution = null;
    let enclose;
    let map = null;
    let mode;
    for (let k = 0; k < words.length; k++) {
      const w = words[k];
      const spec = /^#(\d+)(\/.+)?$/.exec(w);
      if (spec) {
        const item = ctl.byId(Number(spec[1]));
        if (!item) throw new Error(`No item #${spec[1]}`);
        if (item.kind !== 'model') throw new Error(`#${item.id} is not a model: molmap #2 10 (the model, then the resolution in Å)`);
        model = item;
        selection = spec[2] ? chainSelection(spec[2]) : '';
      } else if (/^\//.test(w)) selection = chainSelection(w);
      else if (/^displaythreshold$/i.test(w)) {
        const f = Number(words[++k]);
        if (!(f >= 0.1 && f < 1)) throw new Error('displayThreshold: the fraction of the simulated density enclosed, e.g. 0.95');
        enclose = Math.round(f * 1000) / 10;
      } else if (/^ongrid$/i.test(w)) {
        const m = /^#(\d+)$/.exec(words[++k] || '');
        map = m ? ctl.byId(Number(m[1])) : null;
        if (!map || map.kind !== 'map' || map.series) throw new Error('onGrid #id: the map whose grid the mask gets');
      } else if (/^(add|erase|keep)$/i.test(w)) mode = w.toLowerCase();
      else if (/^(gridspacing|edgepadding|cube|cutoffrange|sigmafactor|balls|symmetry|center|axis|coordinatesystem|replace|showdialog|modelid)$/i.test(w)) {
        throw new Error(`molmap: ${w} is not used here — the model map is computed on the grid of the map, with Gaussians of σ = resolution / (π√2) cut at 5 σ`);
      } else if (resolution === null && Number.isFinite(Number(w.replace(/(a|å)$/i, '')))) resolution = Number(w.replace(/(a|å)$/i, ''));
      else throw new Error(`molmap: unexpected '${w}' (molmap #2/A,B 10 displayThreshold 0.95)`);
    }
    model ||= ctl.activeModel();
    if (!model) throw new Error('molmap needs a model: molmap #2 10');
    if (resolution === null) throw new Error(`Give the resolution in Å: molmap #${model.id} 10`);
    if (!(resolution >= 1 && resolution <= 60)) throw new Error('The resolution of a model map is between 1 and 60 Å');
    const added = ctl.molmapMask({ model, selection, resolution, enclose, map, mode });
    return `Model map of #${model.id}${selection ? ` (${selection})` : ''} at ${resolution} Å ${added.mode === 'add' ? 'added to' : added.mode === 'erase' ? 'erased from' : 'intersected with'} the mask — contour enclosing ${added.enclose} % of its density (orange preview); Save as job computes it on the full grid`;
  }

  const handlers = {
    help: () => { ctl.showHelp(); return 'Commands: see the help window'; },
    open: () => { ctl.open(); return 'Pick maps or models to open'; },
    close: ({ items, rest }) => {
      const list = rest[0] === 'all' ? [...ctl.items()] : items;
      if (!list.length) throw new Error("Say which items to close: close #2 (or 'close all')");
      list.forEach((i) => ctl.close(i));
      return `Closed ${ids(list)}`;
    },
    show: ({ items }) => { const list = items.length ? items : ctl.items(); list.forEach((i) => ctl.setVisible(i, true)); return `Shown ${ids(list)}`; },
    hide: ({ items }) => { const list = items.length ? items : ctl.items(); list.forEach((i) => ctl.setVisible(i, false)); return `Hidden ${ids(list)}`; },
    view: async ({ items, rest }) => {
      if (rest[0] && /^initial$/i.test(rest[0])) { // ChimeraX: moved models back to their positions
        const moved = await ctl.resetPositions(items.length ? items : ctl.items());
        return moved.length ? `Back where their files put them: ${ids(moved)}` : 'Nothing was moved';
      }
      if (rest.length) {
        const m = /^\/?([A-Za-z0-9]+)(?::(-?\d+))?$/.exec(rest[0]);
        if (!m) throw new Error(`Expected /chain or /chain:residue, e.g. /A:45 — got '${rest[0]}'`);
        const model = models(items)[0];
        const where = m[2] !== undefined ? `/${m[1]}:${m[2]}` : `/${m[1]}`;
        const error = ctl.goToQuiet(model, `${m[1]}${m[2] !== undefined ? `:${m[2]}` : ''}`);
        if (error) throw new Error(error);
        return `Centred on ${where} of #${model.id}`;
      }
      if (items.length) { ctl.focus(items[0]); return `Centred on #${items[0].id}`; }
      ctl.resetView();
      return 'View reset';
    },
    level: ({ items, rest }) => setLevels(maps(items), rest),
    style: ({ items, rest }) => {
      const word = rest.join('').toLowerCase();
      const targets = items.length ? items : [ctl.active()].filter(Boolean);
      // 'surface' is a style of both: the items named (or the active one) decide; a style only maps have is for maps
      const forModels = targets.length ? targets.every((i) => i.kind === 'model') : !MAP_STYLE_WORDS[word];
      if (MAP_STYLE_WORDS[word] && (!forModels || (!items.length && !MODEL_STYLE_WORDS[word]))) {
        const ms = maps(items);
        ms.forEach((m) => ctl.setStyle(m, MAP_STYLE_WORDS[word]));
        return `Style of ${ids(ms)}: ${MAP_STYLE_WORDS[word]}`;
      }
      if (MODEL_STYLE_WORDS[word]) { const ms = models(items); ms.forEach((m) => ctl.setDisplay(m, MODEL_STYLE_WORDS[word])); return `Style of ${ids(ms)}: ${MODEL_STYLE_WORDS[word]}`; }
      throw new Error('Style: surface, mesh or transparent for maps; cartoon, both, sticks, spheres, surface or cartoon+surface for models');
    },
    transparency: ({ items, rest }) => {
      const t = Number(rest[0]);
      if (!(t >= 0 && t <= 100)) throw new Error('Transparency is a percentage, 0–100');
      // models named get a surface if they have none; without #id, the active model only if it shows one, else the maps
      const active = ctl.active();
      const surfaces = items.length ? items.filter((i) => i.kind === 'model')
        : active?.kind === 'model' && /surface/.test(active.display) ? [active] : [];
      surfaces.forEach((m) => ctl.setSurfaceOpacity(m, 1 - t / 100, { show: true }));
      const ms = items.length ? items.filter((i) => i.kind === 'map') : surfaces.length ? [] : maps(items);
      ms.forEach((m) => ctl.setTransparency(m, t));
      if (!ms.length && !surfaces.length) throw new Error('Nothing to make transparent');
      return `Transparency of ${ids([...ms, ...surfaces])}: ${t} %`;
    },
    surface: ({ items, rest }) => {
      const ms = models(items);
      const off = rest[0] && /^(hide|off)$/i.test(rest[0]);
      ms.forEach((m) => ctl.setDisplay(m, off ? 'cartoon' : 'surface'));
      return off ? `Surface hidden for ${ids(ms)}` : `Surface of ${ids(ms)}`;
    },
    color: ({ items, rest }) => {
      if (!rest[0]) throw new Error('Which colour?');
      if (/^(localres|locres|local_resolution)$/i.test(rest[0])) return colorBySource(items, rest.slice(1), 'locres');
      if (/^(variability|var|variance|sd)$/i.test(rest[0])) return colorBySource(items, rest.slice(1), 'variability');
      return colorItems(items, rest[0]);
    },
    rainbow: ({ items }) => colorItems(items, 'rainbow'),
    cartoon: ({ items }) => { const ms = models(items); ms.forEach((m) => ctl.setDisplay(m, 'cartoon')); return `Cartoon for ${ids(ms)}`; },
    sticks: ({ items }) => { const ms = models(items); ms.forEach((m) => ctl.setDisplay(m, 'sticks')); return `Sticks for ${ids(ms)}`; },
    zone: ({ items, rest }) => zone(items, rest),
    volume: ({ items, rest }) => {
      if (rest[0] && rest[0].toLowerCase() === 'zone') return zone(items, rest.slice(1));
      const ms = maps(items);
      const done = [];
      for (let k = 0; k < rest.length; k += 2) {
        const key = rest[k].toLowerCase();
        const value = rest[k + 1];
        if (key === 'show' || key === 'hide') { ms.forEach((m) => ctl.setVisible(m, key === 'show')); done.push(key); k -= 1; continue; }
        if (value === undefined) throw new Error(`Missing value after '${rest[k]}'`);
        if (key === 'level') done.push(setLevels(ms, [value]));
        else if (key === 'style') {
          const s = MAP_STYLE_WORDS[value.toLowerCase()];
          if (!s) throw new Error(`Unknown style '${value}'`);
          ms.forEach((m) => ctl.setStyle(m, s));
          done.push(`style ${s}`);
        } else if (key === 'color') { const c = parseColor(value); ms.forEach((m) => ctl.setColor(m, c)); done.push(`color ${value}`); }
        else if (key === 'transparency') { ms.forEach((m) => ctl.setTransparency(m, Number(value))); done.push(`transparency ${value}`); }
        else if (key === 'sdlevel' || key === 'rmslevel' || key === 'enclosevolume') done.push(chimeraLevel(ms, key, value));
        else if (key === 'step') {
          const n = Number(value);
          if (!(n >= 1 && n <= 16)) throw new Error('step: binning of the display, 1 (full) to 16');
          ms.forEach((m) => ctl.setDetail(m, n === 1 ? 0 : { step: n }));
          done.push(`step ${n}`);
        } else throw new Error(`Unknown volume option '${rest[k]}' (level, style, color, transparency, step, sdLevel, rmsLevel, encloseVolume, show, hide, zone)`);
      }
      if (!done.length) throw new Error('Nothing to do: volume #1 level 0.5 style mesh');
      return `${ids(ms)}: ${done.join(' · ')}`;
    },
    bg: ({ rest }) => { if (!rest[0]) throw new Error('bg black, white, gray or a colour'); ctl.setBackground(parseColor(rest[0])); return `Background: ${rest[0]}`; },
    set: ({ rest }) => {
      if (!rest[0] || !/^(bgColor|bg|background)$/i.test(rest[0]) || !rest[1]) throw new Error('Supported: set bgColor <colour>');
      ctl.setBackground(parseColor(rest[1]));
      return `Background: ${rest[1]}`;
    },
    slab: ({ rest }) => {
      if (!rest[0] || /^off$/i.test(rest[0])) { ctl.setSlab(null); return 'Slab off'; }
      const m = /^(\d+(?:\.\d+)?)(a|å|angstroms?)?$/i.exec(rest.join(''));
      const v = m ? Number(m[1]) : NaN;
      if (m?.[2]) {
        if (!(v >= 1 && v <= 1000)) throw new Error('Slab thickness in Å: 1–1000');
        ctl.setSlab({ angstrom: v });
        return `Slab: ${v} Å`;
      }
      if (!(v >= 1 && v <= 99)) throw new Error('Slab: 1–99 (percent of the thickness shown, kept around the centre), a thickness such as 12A, or off');
      ctl.setSlab(v);
      return `Slab: ${v} % (${ctl.slabThickness().toFixed(0)} Å)`;
    },
    clip: ({ rest }) => { if (rest[0] && /^off$/i.test(rest[0])) { ctl.setSlab(null); return 'Clipping off'; } throw new Error('Use slab <1–99> for a slab, clip off to remove it'); },
    spin: ({ rest }) => { const on = onOff(rest[0], ctl.state().spin); ctl.setSpin(on); return on ? 'Spinning' : 'Spin off'; },
    camera: ({ rest }) => {
      const w = (rest[0] || '').toLowerCase();
      if (/^ortho/.test(w)) { ctl.setProjection('orthographic'); return 'Orthographic projection'; }
      if (/^(persp|mono)/.test(w)) { ctl.setProjection('perspective'); return 'Perspective projection'; }
      throw new Error('camera ortho or camera persp');
    },
    lighting: ({ rest }) => {
      const w = (rest[0] || '').toLowerCase();
      if (/^(soft|full|shadows|gentle)$/.test(w)) { ctl.setSoftLighting(true); return 'Soft lighting (ambient occlusion)'; }
      if (/^(simple|default|flat|off)$/.test(w)) { ctl.setSoftLighting(false); return 'Simple lighting'; }
      throw new Error('lighting soft or lighting simple');
    },
    slices: ({ items, rest }) => {
      const w = (rest[0] || '').toLowerCase();
      if (w === 'off' || w === 'hide') { ctl.hideSlices(); return 'Slices hidden'; }
      const m = maps(items)[0];
      ctl.showSlices(m, { enlarge: w === 'big' || w === 'large' || w === 'split' ? true : w === 'small' ? false : undefined });
      return `Slices of #${m.id}`;
    },
    detail: ({ items, rest }) => detail(maps(items), rest[0]),
    lowpass: ({ items, rest }) => {
      const w = (rest[0] || '').toLowerCase().replace(/å|a$/i, '');
      const d = w === 'off' || w === 'none' || w === '0' ? 0 : Number(w);
      if (!(d === 0 || (d >= 2 && d <= 60))) throw new Error('lowpass [#id] <resolution in Å, 2–60> | off');
      maps(items).forEach((m) => ctl.setLowpass(m, d)); // reported once reloaded
      return null;
    },
    record: async ({ rest }) => {
      const w = (rest[0] || '').toLowerCase();
      if (w === 'stop' || w === 'end' || w === 'encode') { ctl.stopRecording(); return null; }
      const seconds = Number(rest[1] ?? rest[0]);
      const kind = w === 'series' || w === 'play' ? 'series' : w === 'spin' || w === 'turn' ? 'spin' : 'free';
      await ctl.record(kind, Number.isFinite(seconds) && seconds > 0 ? seconds : undefined);
      return null;
    },
    distance: async ({ rest }) => {
      if (rest[0] && /^(clear|off|delete|remove)$/i.test(rest[0])) { const n = await ctl.clearDistances(); return n ? `Removed ${n} measurement${n > 1 ? 's' : ''}` : 'No measurement to remove'; }
      return 'Shift + click two atoms to measure their distance (distance clear to remove them)';
    },
    link: async () => ctl.copyLink(),
    fullres: ({ items, rest }) => {
      const on = rest[0] ? onOff(rest[0]) : true;
      const ms = maps(items);
      ms.forEach((m) => ctl.setFullRes(m, on)); // reported once the map is reloaded
      return null;
    },
    play: ({ items, rest }) => {
      const list = series(items);
      const fps = rest[0] !== undefined ? Number(rest[0]) : null;
      if (fps !== null && !(fps > 0 && fps <= 60)) throw new Error('play [#id] [volumes per second, 1–60]');
      list.forEach((m) => ctl.play(m, fps));
      return `Playing ${ids(list)} at ${list[0].series.fps} volumes per second`;
    },
    stop: ({ items }) => {
      const list = items.length ? series(items) : ctl.items().filter((i) => i.series?.playing);
      list.forEach((m) => ctl.pause(m));
      return list.length ? `Stopped ${ids(list)}` : 'Nothing is playing';
    },
    frame: async ({ items, rest }) => {
      const m = series(items)[0];
      const s = m.series;
      const n = s.frames.length;
      const w = (rest[0] || '').toLowerCase();
      const target = { next: s.index + 1, prev: s.index - 1, previous: s.index - 1, first: 0, last: n - 1 }[w] ?? Number(w) - 1;
      if (!Number.isInteger(target) || (!(w in { next: 1, prev: 1, previous: 1 }) && (target < 0 || target >= n))) {
        throw new Error(`frame [#id] 1–${n}, next, prev, first or last`);
      }
      ctl.pause(m);
      await ctl.goToFrame(m, target);
      return `#${m.id}: volume ${s.index + 1}/${n} · ${s.frames[s.index].label}`;
    },
    save: async ({ rest }) => {
      let name = 'cryoplug.png';
      let scale = 2;
      let transparent = false;
      for (let k = 0; k < rest.length; k++) {
        const w = rest[k];
        if (/^(scale|supersample)$/i.test(w)) { scale = Number(rest[k + 1]); k += 1; }
        else if (/^transparent/i.test(w)) transparent = true;
        else name = /\.png$/i.test(w) ? w : `${w}.png`;
      }
      if (!(scale >= 1 && scale <= 4)) throw new Error('scale: 1 to 4');
      const size = await ctl.saveImage(name, { scale, transparent });
      return `Saved ${name} (${size.width}×${size.height}${transparent ? ', transparent' : ''})`;
    },
  };
  handlers.stick = handlers.sticks;
  handlers.pause = handlers.stop;
  // ChimeraX syntax: vseries play #1 [direction oscillate] [maxFrameRate 10] · vseries stop #1
  handlers.vseries = ({ items, rest }) => {
    const verb = (rest[0] || '').toLowerCase();
    if (verb === 'stop') return handlers.stop({ items });
    if (verb !== 'play') throw new Error('vseries play [#id] [direction oscillate] [maxFrameRate n] · vseries stop');
    const list = series(items);
    for (let k = 1; k < rest.length; k += 2) {
      const key = rest[k].toLowerCase();
      const value = (rest[k + 1] || '').toLowerCase();
      if (key === 'direction') list.forEach((m) => ctl.setPlayMode(m, value === 'oscillate' ? 'bounce' : 'loop'));
      else if (key === 'maxframerate' && Number(value) > 0) list.forEach((m) => ctl.setSpeed(m, Math.min(60, Number(value))));
    }
    return handlers.play({ items: list, rest: [] });
  };
  handlers.ribbon = handlers.cartoon;
  handlers.movie = ({ items, rest }) => handlers.record({ items, rest: /^record$/i.test(rest[0] || '') ? rest.slice(1) : rest });
  handlers.filter = handlers.lowpass;
  handlers.measure = handlers.distance;
  handlers.spheres = ({ items }) => { const ms = models(items); ms.forEach((m) => ctl.setDisplay(m, 'spheres')); return `Spheres for ${ids(ms)}`; };
  handlers.windowsize = () => 'The view follows the window size';
  handlers.molmap = ({ words }) => molmap(words);
  handlers.fitmap = ({ words }) => fitmap(words);
  handlers.fit = handlers.fitmap;
  handlers.move = ({ words }) => moveTurn('move', words);
  handlers.turn = ({ words }) => moveTurn('turn', words);

  async function run(line) {
    const words = line.trim().split(/\s+/).filter(Boolean);
    if (!words.length) return null;
    const verb = words.shift().toLowerCase();
    const handler = handlers[verb];
    if (!handler) throw new Error(`Unknown command '${verb}'. Type help for the list.`);
    // these read #2/A,B, inMap #1, center #1… themselves
    return handler(['molmap', 'fitmap', 'fit', 'move', 'turn'].includes(verb) ? { words } : split(words));
  }

  return { run };
}
