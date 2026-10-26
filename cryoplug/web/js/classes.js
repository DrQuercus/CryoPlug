// 2D class averages as in CryoSPARC: a gallery sorted by size or resolution, whose classes are clicked to select them.
// A waiting "Select 2D classes" job finishes with the selection; a 2D classification makes a new selection job from it.
import { api } from './api.js';
import { btn, h, icon } from './ui.js';

const picked = new Map(); // `${project}/${job}` -> Set of class numbers: survives the panel being redrawn
const view = { sort: 'particles', size: 112 };

const SORTS = [
  ['particles', 'Particles', (a, b) => b.particles - a.particles || a.class - b.class],
  ['resolution', 'Resolution', (a, b) => (a.resolution || 999) - (b.resolution || 999) || b.particles - a.particles],
  ['class', 'Class', (a, b) => a.class - b.class],
];

const fmtN = (n) => Number(n || 0).toLocaleString();

// `finish(classes)`: a waiting selection job is done with these classes; `newSelection(classes)`: a new selection job.
export function classGallery(sec, { puid, job, finish = null, newSelection = null }) {
  const records = sec.classes || [];
  const decided = Array.isArray(sec.selected) ? new Set(sec.selected) : null;
  const waiting = !!(sec.selectable && sec.waiting && job.status === 'waiting' && finish);
  const selectable = waiting || (!!sec.actions && job.status === 'completed' && !!newSelection);
  const key = `${puid}/${job.uid}`;
  if (!picked.has(key)) picked.set(key, new Set());
  const chosen = picked.get(key);
  const total = records.reduce((a, r) => a + (r.particles || 0), 0) || 1;
  let anchor = null; // last clicked class (shift-click selects the run in between)

  const grid = h('div', { class: 'cls-grid', role: selectable ? 'group' : 'list', 'aria-label': sec.title });
  const summary = h('span', { class: 'cls-sum' });
  const doneBtn = waiting ? btn('Done', () => finish([...chosen].sort((a, b) => a - b)), { cls: 'primary', ic: 'check',
    title: 'Keep the particles of the selected classes' }) : null;
  const newBtn = !waiting && selectable ? btn('Select these classes', () => newSelection([...chosen].sort((a, b) => a - b)),
    { cls: 'primary', ic: 'next', title: 'A “Select 2D classes” job keeping the particles of the selected classes' }) : null;

  const sorted = () => [...records].sort(SORTS.find(([k]) => k === view.sort)[2]);
  const refresh = () => {
    const n = [...chosen].reduce((a, c) => a + (records.find((r) => r.class === c)?.particles || 0), 0);
    if (selectable) {
      summary.replaceChildren(chosen.size
        ? h('span', {}, h('b', {}, `${chosen.size}`), ` of ${records.length} classes · `, h('b', {}, fmtN(n)), ` particles (${((100 * n) / total).toFixed(0)} %)`)
        : h('span', { class: 'muted' }, waiting ? 'Click the good classes (shift-click: a run of classes), then Done' : 'Click classes to make a new selection'));
    } else if (decided) {
      const kept = records.filter((r) => decided.has(r.class)).reduce((a, r) => a + r.particles, 0);
      summary.replaceChildren(h('b', {}, `${decided.size}`), ` of ${records.length} classes kept${sec.how ? ` (${sec.how})` : ''} · `,
        h('b', {}, fmtN(kept)), ` particles (${((100 * kept) / total).toFixed(0)} %)`);
    } else {
      summary.replaceChildren(`${records.length} classes · ${fmtN(total)} particles`);
    }
    if (doneBtn) doneBtn.disabled = !chosen.size;
    if (newBtn) newBtn.disabled = !chosen.size;
    for (const tile of grid.children) {
      const on = chosen.has(+tile.dataset.cls);
      tile.classList.toggle('on', selectable ? on : !!decided?.has(+tile.dataset.cls));
      if (selectable) tile.setAttribute('aria-pressed', on ? 'true' : 'false');
    }
  };
  const toggle = (r, e) => {
    const order = sorted().map((x) => x.class);
    if (e.shiftKey && anchor !== null && order.includes(anchor)) {
      const [a, b] = [order.indexOf(anchor), order.indexOf(r.class)].sort((x, y) => x - y);
      const turnOn = !chosen.has(r.class);
      order.slice(a, b + 1).forEach((c) => (turnOn ? chosen.add(c) : chosen.delete(c)));
    } else if (chosen.has(r.class)) chosen.delete(r.class);
    else chosen.add(r.class);
    anchor = r.class;
    refresh();
  };
  const tile = (r) => {
    const empty = !r.particles;
    const tip = [`Class ${r.class}`, `${fmtN(r.particles)} particles (${(100 * (r.fraction ?? r.particles / total)).toFixed(1)} %)`,
      r.resolution && r.resolution < 900 ? `resolution ${r.resolution.toFixed(1)} Å` : null,
      r.accuracy_rotations ? `rotational accuracy ${r.accuracy_rotations}°` : null,
      r.accuracy_translations ? `translational accuracy ${r.accuracy_translations} Å` : null,
      r.score !== undefined ? `class ranker score ${r.score}` : null].filter(Boolean).join(' · ');
    const body = [
      h('img', { src: api.fileUrl(puid, r.image), alt: `Class ${r.class}`, loading: 'lazy', draggable: 'false' }),
      h('span', { class: 'cls-num' }, r.class),
      r.score !== undefined ? h('span', { class: 'cls-score', title: 'Class ranker score (0-1)' }, r.score.toFixed(2)) : null,
      h('span', { class: 'cls-meta' }, h('b', {}, fmtN(r.particles)),
        r.resolution && r.resolution < 900 ? h('span', {}, `${r.resolution.toFixed(1)} Å`) : h('span', { class: 'muted' }, empty ? 'empty' : '–')),
    ];
    return selectable
      ? h('button', { class: `cls-tile${empty ? ' empty' : ''}`, type: 'button', dataset: { cls: r.class }, title: tip, 'aria-label': tip,
        'aria-pressed': 'false', onclick: (e) => toggle(r, e) }, body)
      : h('div', { class: `cls-tile static${empty ? ' empty' : ''}`, role: 'listitem', dataset: { cls: r.class }, title: tip }, body);
  };
  const draw = () => {
    grid.style.setProperty('--cls-size', `${view.size}px`);
    grid.replaceChildren(...sorted().map(tile));
    refresh();
  };

  const sortSeg = h('div', { class: 'seg', role: 'group', 'aria-label': 'Sort the classes by' }, SORTS.map(([k, label]) => h('button', {
    type: 'button', class: view.sort === k ? 'on' : '', 'aria-pressed': view.sort === k ? 'true' : 'false',
    onclick: (e) => {
      view.sort = k;
      for (const b of sortSeg.children) { b.classList.toggle('on', b === e.currentTarget); b.setAttribute('aria-pressed', b === e.currentTarget ? 'true' : 'false'); }
      draw();
    },
  }, label)));
  const zoom = h('input', { type: 'range', min: 72, max: 200, step: 8, value: view.size, 'aria-label': 'Size of the class averages',
    oninput: (e) => { view.size = +e.target.value; grid.style.setProperty('--cls-size', `${view.size}px`); } });
  const pickAll = (fn) => () => { records.forEach((r) => (fn(r) ? chosen.add(r.class) : chosen.delete(r.class))); refresh(); };
  const tools = selectable ? h('span', { class: 'cls-tools' },
    btn('All', pickAll(() => true), { cls: 'small', title: 'Select every class' }),
    btn('None', pickAll(() => false), { cls: 'small', title: 'Clear the selection' }),
    btn('Invert', pickAll((r) => !chosen.has(r.class)), { cls: 'small', title: 'Invert the selection' })) : null;

  const head = h('div', { class: 'cls-head' },
    h('h4', {}, sec.title, sec.running ? h('span', { class: 'chip running', title: 'Redrawn at every iteration' }, 'live') : null),
    h('span', { class: 'grow' }),
    h('span', { class: 'muted small' }, 'Sort'), sortSeg,
    h('label', { class: 'cls-zoom muted small', title: 'Size of the class averages' }, icon('search'), zoom));
  const meta = [sec.particles ? `${fmtN(sec.particles)} particles` : null, sec.box ? `box ${sec.box} px` : null,
    sec.pixel_size ? `${(+sec.pixel_size).toFixed(3).replace(/0+$/, '').replace(/\.$/, '')} Å/px` : null].filter(Boolean).join(' · ');
  const bar = h('div', { class: 'cls-bar' }, summary, h('span', { class: 'grow' }), tools, doneBtn, newBtn);
  draw();
  // the bar follows the grid: it stays at the bottom of the panel while the classes are scrolled through
  return h('div', { class: `cls-gallery${selectable ? ' selectable' : ''}${decided ? ' decided' : ''}` },
    head, meta ? h('div', { class: 'muted small cls-meta-line' }, meta) : null, grid, bar);
}

// Forget a finished selection (a new job of the same uid starts afresh).
export function forgetSelection(puid, uid) {
  picked.delete(`${puid}/${uid}`);
}
