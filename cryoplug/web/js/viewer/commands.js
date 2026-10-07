// ChimeraX-like command line. Each command maps onto the same actions as the buttons and the models panel.

export const COMMAND_HELP = [
  ['level <value>[σ] [#id]', 'Contour level of a map, absolute or in σ (level 0.42 · level 3σ)'],
  ['style [#id] surface | mesh | transparent', 'Map style'],
  ['style [#id] cartoon | both | sticks', 'Model style (both = cartoon + side chains)'],
  ['transparency [#id] <0–100>', 'Transparency of a map surface'],
  ['color [#id] <name | #hex>', 'Single colour for a map or a model'],
  ['color [#id] bychain | byss | byelement | bfactor | rainbow', 'Model colouring'],
  ['show | hide [#id]', 'Show or hide items (all of them without #id)'],
  ['view', 'Reset the view'],
  ['view #id | /A:45 | /A', 'Centre on an item, a residue or a chain'],
  ['zone [#map] [#model] [radius] | zone off', 'Show the map only near the model (ChimeraX volume zone)'],
  ['volume [#id] level <v> style <s> color <c> transparency <t>', 'Several map settings at once, ChimeraX syntax'],
  ['volume zone #map nearAtoms #model range <r>', 'Same as zone, ChimeraX syntax'],
  ['bg black | white | gray | <colour>', 'Background (also: set bgColor white)'],
  ['slab <1–99> | slab off', 'Keep only a slab around the centre, Coot-like (also: clip off)'],
  ['spin [off]', 'Rotate continuously'],
  ['camera ortho | persp', 'Projection'],
  ['lighting soft | simple', 'Ambient occlusion on or off'],
  ['slices [#id] [off]', 'Orthogonal 2D slices of a map'],
  ['fullres [#id] [off]', 'Full-resolution map instead of the binned preview'],
  ['save [name.png] [scale <n>] [transparent]', 'Save an image of the view'],
  ['open', 'Open other maps or models of the project'],
  ['close #id | close all', 'Remove items'],
];

const MODEL_COLOR_WORDS = {
  bychain: 'chain', chain: 'chain', byss: 'ss', bysecondary: 'ss', ss: 'ss', byelement: 'element', byhetero: 'element',
  element: 'element', byatom: 'element', bfactor: 'bfactor', byattribute: 'bfactor', rainbow: 'rainbow', bypolymer: 'chain',
};
const MAP_STYLE_WORDS = { surface: 'surface', solid: 'surface', mesh: 'mesh', wire: 'mesh', wireframe: 'mesh', transparent: 'transparent' };
const MODEL_STYLE_WORDS = { cartoon: 'cartoon', ribbon: 'cartoon', both: 'both', sidechains: 'both', sticks: 'sticks', stick: 'sticks', atoms: 'sticks' };

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
  const text = words.join('');
  const m = /^(-?(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?)(σ|s|sd|sigma|rms)?$/i.exec(text);
  if (!m) throw new Error(`Expected a level such as 0.42 or 3σ, got '${words.join(' ')}'`);
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

  function setLevels(list, words) {
    const { value, sigma } = parseLevel(words);
    list.forEach((m) => ctl.setLevel(m, sigma ? m.stats.mean + value * m.stats.sigma : value));
    return `Level of ${ids(list)} set to ${words.join('')}`;
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
    view: ({ items, rest }) => {
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
      const word = (rest[0] || '').toLowerCase();
      if (MAP_STYLE_WORDS[word]) { const ms = maps(items); ms.forEach((m) => ctl.setStyle(m, MAP_STYLE_WORDS[word])); return `Style of ${ids(ms)}: ${MAP_STYLE_WORDS[word]}`; }
      if (MODEL_STYLE_WORDS[word]) { const ms = models(items); ms.forEach((m) => ctl.setDisplay(m, MODEL_STYLE_WORDS[word])); return `Style of ${ids(ms)}: ${MODEL_STYLE_WORDS[word]}`; }
      throw new Error('Style: surface, mesh or transparent for maps; cartoon, both or sticks for models');
    },
    transparency: ({ items, rest }) => {
      const t = Number(rest[0]);
      if (!(t >= 0 && t <= 100)) throw new Error('Transparency is a percentage, 0–100');
      const ms = maps(items);
      ms.forEach((m) => ctl.setTransparency(m, t));
      return `Transparency of ${ids(ms)}: ${t} %`;
    },
    color: ({ items, rest }) => { if (!rest[0]) throw new Error('Which colour?'); return colorItems(items, rest[0]); },
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
        else throw new Error(`Unknown volume option '${rest[k]}' (level, style, color, transparency, show, hide, zone)`);
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
      const v = Number(rest[0]);
      if (!(v >= 1 && v <= 99)) throw new Error('Slab: 1–99 (percent of the scene kept around the centre) or off');
      ctl.setSlab(v);
      return `Slab: ${v} %`;
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
      if (rest[0] && /^off$/i.test(rest[0])) { ctl.hideSlices(); return 'Slices hidden'; }
      const m = maps(items)[0];
      ctl.showSlices(m);
      return `Slices of #${m.id}`;
    },
    fullres: ({ items, rest }) => {
      const on = rest[0] ? onOff(rest[0]) : true;
      const ms = maps(items);
      ms.forEach((m) => ctl.setFullRes(m, on)); // reported once the map is reloaded
      return null;
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
  handlers.ribbon = handlers.cartoon;
  handlers.windowsize = () => 'The view follows the window size';

  async function run(line) {
    const words = line.trim().split(/\s+/).filter(Boolean);
    if (!words.length) return null;
    const verb = words.shift().toLowerCase();
    const handler = handlers[verb];
    if (!handler) throw new Error(`Unknown command '${verb}'. Type help for the list.`);
    return handler(split(words));
  }

  return { run };
}
