// Tree view of a project's jobs, as in CryoSPARC: the pipeline drawn from the imports down, one condensed card per
// job (status, number, title, key result) and lines coloured by the data they carry. Clicking a job opens it and
// lights up the jobs it comes from and the jobs that use it; the rest fades. Drag or scroll to move around,
// Ctrl/⌘ + scroll or a pinch to zoom; T centres on the selected jobs, F shows the whole tree.
import { ACTIVE, clear, h, icon, s } from './ui.js';

export const NODE_W = 220;
export const NODE_H = 64;
const GAP_X = 30; // between two cards of a row
const GAP_LINE = 12; // beside a line that passes through a row
const GAP_Y = 52; // between two rows: room for the curves and the arrowheads
const LINE_W = 10; // room kept on a row for a line passing through it
const PAD = 36; // around the tree
const K_MIN = 0.15;
const K_MAX = 2;
const FAR = 0.5; // below this zoom the cards only show their number

// The kind of data a line carries (its colour): density maps, models (and what builds them), particles, the rest.
const FAMILY = {
  map: 'map', half_maps: 'map', mask: 'map', locres: 'map', variability: 'map', volume_series: 'map',
  model: 'model', restraints: 'model', sequence: 'model', particles: 'particles', latent: 'particles',
};
const FAMILIES = [['map', 'Maps'], ['model', 'Models, sequences'], ['particles', 'Particles'], ['other', 'Other data']];
const familyOf = (types) => {
  const fams = new Set(types.map((t) => FAMILY[t] || 'other'));
  return ['model', 'particles', 'map'].find((f) => fams.has(f)) || 'other';
};

// ------------------------------------------------------------------ layout
// Layered drawing: a job sits one row below its lowest input; a job without inputs sits just above its first use.
// The lines of a job towards rows further down share one trunk per kind of data, which passes between the cards of
// each row it crosses and branches off where it is used. The order in each row reduces the crossings (barycentres,
// then swaps of neighbours); then each card goes as close as it can to the middle of the jobs it is linked to,
// keeping the order and the spacing (isotonic regression).
export function layoutTree(jobs, typeOf = () => '') {
  const byUid = new Map(jobs.map((j) => [j.uid, j]));
  const links = new Map(); // one per parent → child, with the inputs it feeds
  for (const j of jobs) {
    for (const [slot, ref] of Object.entries(j.inputs || {})) {
      if (!ref?.job || ref.job === j.uid || !byUid.has(ref.job)) continue;
      const key = `${ref.job}>${j.uid}`;
      if (!links.has(key)) links.set(key, { from: ref.job, to: j.uid, feeds: [] });
      links.get(key).feeds.push({ slot, output: ref.output, type: typeOf(ref) || '' });
    }
  }
  const ups = new Map(jobs.map((j) => [j.uid, []]));
  const downs = new Map(jobs.map((j) => [j.uid, []]));
  for (const l of links.values()) {
    ups.get(l.to).push(l.from);
    downs.get(l.from).push(l.to);
    l.family = familyOf(l.feeds.map((f) => f.type));
  }

  // rows (a loop, which the builder should never allow, is cut where it closes)
  const row = new Map();
  const visiting = new Set();
  const rowOf = (uid) => {
    if (row.has(uid)) return row.get(uid);
    if (visiting.has(uid)) return -1;
    visiting.add(uid);
    let r = 0;
    for (const p of ups.get(uid)) r = Math.max(r, rowOf(p) + 1);
    visiting.delete(uid);
    row.set(uid, r);
    return r;
  };
  jobs.forEach((j) => rowOf(j.uid));
  for (const j of jobs) { // an import used late in the pipeline comes down next to its use
    const d = downs.get(j.uid);
    if (!ups.get(j.uid).length && d.length) row.set(j.uid, Math.max(0, Math.min(...d.map((c) => row.get(c))) - 1));
  }

  // the drawing graph: the jobs, plus the points where the trunks cross a row
  const nodes = new Map();
  for (const j of jobs) nodes.set(j.uid, { id: j.uid, job: j, num: j.num, row: row.get(j.uid), w: NODE_W, up: [], down: [], x: 0 });
  const trunks = new Map();
  let extra = 0;
  for (const l of links.values()) {
    const a = nodes.get(l.from);
    const b = nodes.get(l.to);
    l.path = [a.id];
    if (b.row <= a.row) { // a loop: drawn apart, it does not order the rows
      l.back = true;
      l.path.push(b.id);
      continue;
    }
    const key = `${a.id}|${l.family}`;
    if (!trunks.has(key)) trunks.set(key, []);
    const trunk = trunks.get(key); // its point on each row below the job
    for (let r = a.row + 1; r < b.row; r++) {
      const i = r - a.row - 1;
      if (!trunk[i]) {
        trunk[i] = `~${extra++}`;
        nodes.set(trunk[i], { id: trunk[i], job: null, num: a.num + 0.5, row: r, w: LINE_W, up: [], down: [], x: 0 });
      }
      l.path.push(trunk[i]);
    }
    l.path.push(b.id);
  }
  // each step of a path is one segment, drawn once however many lines share it
  const segs = new Map();
  for (const l of links.values()) {
    for (let i = 1; i < l.path.length; i++) {
      const key = `${l.path[i - 1]}>${l.path[i]}`;
      if (!segs.has(key)) {
        segs.set(key, { from: l.path[i - 1], to: l.path[i], links: [], family: l.family, back: !!l.back });
        if (!l.back) {
          nodes.get(l.path[i - 1]).down.push(l.path[i]);
          nodes.get(l.path[i]).up.push(l.path[i - 1]);
        }
      }
      segs.get(key).links.push(l);
    }
  }
  const nrows = 1 + Math.max(0, ...[...nodes.values()].map((v) => v.row));
  let rows = Array.from({ length: nrows }, () => []);
  for (const v of nodes.values()) rows[v.row].push(v);
  rows.forEach((list) => list.sort((a, b) => a.num - b.num));
  for (let r = 1; r < nrows; r++) rows[r] = sortRow(rows[r], rows[r - 1], 'up');
  transpose(rows);
  let best = rows.map((l) => l.slice());
  let fewest = crossings(rows);
  for (let it = 0, stale = 0; it < 24 && fewest > 0 && stale < 4; it++) {
    if (it % 2) for (let r = nrows - 2; r >= 0; r--) rows[r] = sortRow(rows[r], rows[r + 1], 'down');
    else for (let r = 1; r < nrows; r++) rows[r] = sortRow(rows[r], rows[r - 1], 'up');
    transpose(rows);
    const c = crossings(rows);
    if (c < fewest) { fewest = c; best = rows.map((l) => l.slice()); stale = 0; } else stale += 1;
  }
  rows = best;

  // horizontal places: packed, then pulled towards the jobs each one is linked to (lines pull harder: they stay straight)
  for (const list of rows) list.forEach((v, i) => { v.x = i ? list[i - 1].x + spacing(list[i - 1], v) : 0; });
  for (let it = 0; it < 12; it++) {
    const both = it >= 9;
    const downward = it % 2 === 0;
    const order = downward || both ? rows : [...rows].reverse();
    for (const list of order) {
      const desired = [];
      const weights = [];
      for (const v of list) {
        // a trunk follows the line above it more than its branches: it stays straight
        const ids = !v.job ? [...v.up, ...v.up, ...v.up, ...v.down] : both ? [...v.up, ...v.down] : downward ? v.up : v.down;
        if (ids.length) {
          desired.push(ids.reduce((sum, id) => sum + nodes.get(id).x, 0) / ids.length);
          weights.push(v.job ? ids.length : 8);
        } else {
          desired.push(v.x);
          weights.push(0.2);
        }
      }
      const xs = place(list, desired, weights);
      list.forEach((v, i) => { v.x = xs[i]; });
    }
  }
  let left = Infinity;
  let right = -Infinity;
  for (const v of nodes.values()) { left = Math.min(left, v.x - v.w / 2); right = Math.max(right, v.x + v.w / 2); }
  for (const v of nodes.values()) {
    v.x += PAD - left;
    v.y = PAD + v.row * (NODE_H + GAP_Y);
  }

  // segments leave a card along its bottom edge and reach a card along its top edge, in the order of their other ends
  const outs = new Map();
  const ins = new Map();
  const add = (map, id, sg) => { if (!map.has(id)) map.set(id, []); map.get(id).push(sg); };
  for (const sg of segs.values()) {
    if (nodes.get(sg.from).job) add(outs, sg.from, sg);
    if (nodes.get(sg.to).job) add(ins, sg.to, sg);
  }
  const spread = (list, key, other) => {
    list.sort((a, b) => nodes.get(a[other]).x - nodes.get(b[other]).x);
    const step = Math.min(14, (NODE_W - 64) / Math.max(1, list.length - 1));
    list.forEach((sg, i) => { sg[key] = (i - (list.length - 1) / 2) * step; });
  };
  for (const list of outs.values()) spread(list, 'dxOut', 'to');
  for (const list of ins.values()) spread(list, 'dxIn', 'from');
  const f = (n) => Math.round(n * 10) / 10;
  for (const sg of segs.values()) {
    const a = nodes.get(sg.from);
    const b = nodes.get(sg.to);
    const x1 = a.x + (sg.dxOut || 0);
    const y1 = a.y + NODE_H; // the bottom of a card, or of the row a trunk crosses
    const x2 = b.x + (sg.dxIn || 0);
    const y2 = b.job ? b.y - 7 : b.y; // the arrowhead covers the last 7 px
    const bend = sg.back ? 90 : (y2 - y1) / 2;
    sg.d = `M${f(x1)},${f(y1)} C${f(x1)},${f(y1 + bend)} ${f(x2)},${f(y2 - bend)} ${f(x2)},${f(y2)}${b.job ? '' : ` L${f(x2)},${f(b.y + NODE_H)}`}`;
    sg.head = b.job ? `M${f(x2 - 4.5)},${f(y2 - 1)} L${f(x2 + 4.5)},${f(y2 - 1)} L${f(x2)},${f(b.y)} Z` : null;
  }

  const pos = new Map();
  for (const j of jobs) {
    const v = nodes.get(j.uid);
    pos.set(j.uid, { x: v.x - NODE_W / 2, y: v.y, row: v.row });
  }
  return {
    pos, ups, downs,
    links: [...links.values()],
    segs: [...segs.values()],
    rows: rows.map((list) => list.filter((v) => v.job).map((v) => v.id)),
    width: right - left + 2 * PAD,
    height: 2 * PAD + nrows * NODE_H + (nrows - 1) * GAP_Y,
  };
}

const spacing = (a, b) => (a.w + b.w) / 2 + (a.job && b.job ? GAP_X : GAP_LINE);

// Reorder a row by the mean place of its neighbours in the reference row; a node without any keeps its place.
function sortRow(list, ref, side) {
  const at = new Map(ref.map((v, i) => [v.id, i]));
  const keyed = list.map((v, i) => {
    const ps = v[side].map((id) => at.get(id)).filter((p) => p !== undefined);
    return { v, i, bc: ps.length ? ps.reduce((a, b) => a + b, 0) / ps.length : null };
  });
  const movable = keyed.filter((k) => k.bc !== null).sort((a, b) => a.bc - b.bc || a.i - b.i);
  let next = 0;
  return keyed.map((k) => (k.bc === null ? k.v : movable[next++].v));
}

// Swap neighbours of a row while it removes crossings with the rows above and below.
function transpose(rows) {
  for (let pass = 0; pass < 4; pass++) {
    let improved = false;
    rows.forEach((list, r) => {
      const sides = [];
      if (r > 0) sides.push(['up', new Map(rows[r - 1].map((v, i) => [v.id, i]))]);
      if (r + 1 < rows.length) sides.push(['down', new Map(rows[r + 1].map((v, i) => [v.id, i]))]);
      const cross = (u, v) => { // crossings between the lines of u and those of v, u on the left
        let c = 0;
        for (const [side, at] of sides) for (const a of u[side]) for (const b of v[side]) if (at.get(a) > at.get(b)) c += 1;
        return c;
      };
      for (let i = 0; i + 1 < list.length; i++) {
        if (cross(list[i], list[i + 1]) > cross(list[i + 1], list[i])) {
          [list[i], list[i + 1]] = [list[i + 1], list[i]];
          improved = true;
        }
      }
    });
    if (!improved) break;
  }
}

function crossings(rows) {
  let total = 0;
  for (let r = 0; r + 1 < rows.length; r++) {
    const lower = rows[r + 1];
    const at = new Map(lower.map((v, i) => [v.id, i]));
    const seq = [];
    for (const v of rows[r]) seq.push(...v.down.map((id) => at.get(id)).filter((p) => p !== undefined).sort((a, b) => a - b));
    const tree = new Array(lower.length + 1).fill(0); // inversions of seq, with a Fenwick tree
    seq.forEach((p, i) => {
      let notAbove = 0;
      for (let k = p + 1; k > 0; k -= k & -k) notAbove += tree[k];
      total += i - notAbove;
      for (let k = p + 1; k <= lower.length; k += k & -k) tree[k] += 1;
    });
  }
  return total;
}

// Centres as close as possible (weighted least squares) to the desired ones, in the same order and spaced enough.
function place(list, desired, weights) {
  const offset = [0];
  for (let i = 1; i < list.length; i++) offset.push(offset[i - 1] + spacing(list[i - 1], list[i]));
  const blocks = [];
  list.forEach((_, i) => {
    let b = { w: weights[i], sum: weights[i] * (desired[i] - offset[i]), start: i, end: i };
    while (blocks.length && blocks[blocks.length - 1].sum / blocks[blocks.length - 1].w > b.sum / b.w) {
      const prev = blocks.pop();
      b = { w: prev.w + b.w, sum: prev.sum + b.sum, start: prev.start, end: b.end };
    }
    blocks.push(b);
  });
  const xs = [];
  for (const b of blocks) for (let i = b.start; i <= b.end; i++) xs[i] = b.sum / b.w + offset[i];
  return xs;
}

// The jobs a job comes from and the jobs that use it, at any distance.
function lineage(layout, uid) {
  const walk = (map) => {
    const seen = new Set();
    const todo = [...map.get(uid)];
    while (todo.length) {
      const u = todo.pop();
      if (seen.has(u) || u === uid) continue;
      seen.add(u);
      todo.push(...map.get(u));
    }
    return seen;
  };
  return { anc: walk(layout.ups), desc: walk(layout.downs), parents: new Set(layout.ups.get(uid)), children: new Set(layout.downs.get(uid)) };
}

// ------------------------------------------------------------------ view
const stored = (key) => { try { return localStorage.getItem(key); } catch { return null; } };
const store = (key, value) => { try { localStorage.setItem(key, value); } catch { /* storage unavailable */ } };
const T = { mounted: null, views: new Map(), anim: 0, saveTimer: 0, fade: stored('cryoplug.treeFade') !== '0' };

const structureKey = (jobs) => jobs.map((j) => `${j.uid}<${Object.values(j.inputs || {}).map((r) => r?.job).join(',')}`).join(';');

// o: { puid, jobs, shown (Set of the jobs the filters keep, or null), open (uid of the job open), picked (Set), bar (the
//      selection bar), typeOf(ref), typeLabel(type), slotLabel(job, slot), typeTitle(type), ago(job),
//      thumb(job) -> { el, style, img }, view3d(job) -> url, toBuilder() (true: cards can be dragged onto the inputs
//      of the job builder), onOpen(uid), onPick(event, uid) -> handled,
//      onMenu(event, job, anchor), onQuick(job, key, anchor), onDeselect(), rerender() }
export function renderTree(area, o) {
  let m = T.mounted;
  if (m?.root.isConnected && (m.drag?.moved || m.pinch)) { m.pending = o.rerender; return; } // never under a moving pointer
  const key = structureKey(o.jobs);
  if (!m || !m.root.isConnected || m.root.parentNode !== area || m.puid !== o.puid || m.key !== key) {
    const focused = document.activeElement?.closest?.('.tnode')?.dataset.uid;
    m = mount(area, o, key);
    if (focused) m.els.get(focused)?.focus({ preventScroll: true });
  }
  m.o = o;
  area.querySelector(':scope > .selbar')?.remove();
  if (o.bar) area.prepend(o.bar);
  sizeView(m);
  paint(m);
  if (m.v) setView(m, m.v);
  else initialView(m);
  if (m.v) followOpen(m);
}

// Keys of the tree view (app.js passes them while it is shown): T, F, +, −, 0.
export function treeKey(e) {
  const m = T.mounted;
  if (!m?.root.isConnected || !m.v || !m.vw) return false;
  if (e.key === 't' || e.key === 'T') centreSelection(m);
  else if (e.key === 'f' || e.key === 'F') animateTo(m, fitBox(m, { x: 0, y: 0, w: m.layout.width, h: m.layout.height }));
  else if (e.key === '+' || e.key === '=') zoomBy(m, 1.25);
  else if (e.key === '-' || e.key === '_') zoomBy(m, 0.8);
  else if (e.key === '0') animateTo(m, zoomAround(m, 1, m.vw / 2, m.vh / 2));
  else return false;
  return true;
}

function mount(area, o, key) {
  const layout = layoutTree(o.jobs, o.typeOf);
  const m = { puid: o.puid, key, layout, o, els: new Map(), sigs: new Map(), pointers: new Map(), drag: null, pinch: null,
    pending: null, v: T.views.get(o.puid) || saved(o.puid), lastOpen: undefined, vw: 0, vh: 0, dragEnd: 0, pressed: 0 };
  const jobOf = new Map(o.jobs.map((j) => [j.uid, j]));
  m.canvas = h('div', { class: 'tree-canvas', style: { width: `${layout.width}px`, height: `${layout.height}px` } });
  const svg = s('svg', { class: 'tree-links', width: layout.width, height: layout.height, 'aria-hidden': 'true' });
  m.byJob = new Map(o.jobs.map((j) => [j.uid, []]));
  m.segs = layout.segs.map((sg) => {
    const lines = sg.links.flatMap((l) => l.feeds.map((fd) => `${l.from} ${fd.output} → ${l.to} ${o.slotLabel(jobOf.get(l.to), fd.slot)}${fd.type ? ` (${o.typeLabel(fd.type)})` : ''}`));
    const tip = lines.length > 12 ? [...lines.slice(0, 12), `… ${lines.length - 12} more`].join('\n') : lines.join('\n');
    const g = s('g', { class: `tlink fam-${sg.family}` },
      s('path', { class: 'hit', d: sg.d }, s('title', {}, tip)), s('path', { class: 'line', d: sg.d }), sg.head ? s('path', { class: 'head', d: sg.head }) : null);
    svg.appendChild(g);
    const item = { sg, g };
    for (const uid of new Set(sg.links.flatMap((l) => [l.from, l.to]))) m.byJob.get(uid).push(item);
    return item;
  });
  m.canvas.appendChild(svg);
  const at = (j) => layout.pos.get(j.uid);
  for (const j of [...o.jobs].sort((a, b) => at(a).row - at(b).row || at(a).x - at(b).x)) {
    const p = at(j);
    const el = h('div', { class: 'tnode', tabindex: 0, role: 'button', dataset: { uid: j.uid }, style: { left: `${p.x}px`, top: `${p.y}px` } });
    nodeEvents(m, el, j.uid);
    m.els.set(j.uid, el);
    m.canvas.appendChild(el);
  }
  m.hint = h('div', { class: 'tree-hint', id: 'tree-hint', 'aria-live': 'polite' });
  m.mini = minimap(m);
  m.view = h('div', { class: 'tree-view', role: 'region', 'aria-label': 'Job tree', 'aria-describedby': 'tree-hint' }, m.canvas, m.mini);
  viewEvents(m);
  m.zoomLabel = h('button', { type: 'button', class: 'tree-zoom', title: 'Actual size (0)', onclick: () => animateTo(m, zoomAround(m, 1, m.vw / 2, m.vh / 2)) }, '100%');
  const fade = h('button', { type: 'button', class: `btn small ${T.fade ? 'on' : ''}`, 'aria-pressed': T.fade ? 'true' : 'false',
    title: 'Fade the jobs that are not linked to the job open',
    onclick: () => {
      T.fade = !T.fade;
      store('cryoplug.treeFade', T.fade ? '1' : '0');
      fade.classList.toggle('on', T.fade);
      fade.setAttribute('aria-pressed', T.fade ? 'true' : 'false');
      paint(m);
    } }, icon('layers'), 'Lineage');
  const families = FAMILIES.filter(([fam]) => layout.links.some((l) => l.family === fam));
  m.root = h('div', { class: 'tree' },
    h('div', { class: 'tree-bar' },
      h('div', { class: 'seg tree-zoombox', role: 'group', 'aria-label': 'Zoom' },
        h('button', { type: 'button', title: 'Zoom out (−)', 'aria-label': 'Zoom out', onclick: () => zoomBy(m, 0.8) }, icon('minus')),
        m.zoomLabel,
        h('button', { type: 'button', title: 'Zoom in (+)', 'aria-label': 'Zoom in', onclick: () => zoomBy(m, 1.25) }, icon('plus'))),
      h('button', { type: 'button', class: 'btn small', title: 'Show the whole tree (F)',
        onclick: () => animateTo(m, fitBox(m, { x: 0, y: 0, w: layout.width, h: layout.height })) }, icon('expand'), 'Fit'),
      h('button', { type: 'button', class: 'btn small', title: 'Centre on the selected jobs (T)', onclick: () => centreSelection(m) }, icon('target'), 'Centre'),
      fade,
      h('span', { class: 'grow' }),
      families.length ? h('div', { class: 'tree-legend', role: 'list', 'aria-label': 'Colour of the lines: data carried' },
        families.map(([fam, label]) => h('span', { role: 'listitem' },
          s('svg', { class: `sw fam-${fam}`, width: 20, height: 8, 'aria-hidden': 'true' }, s('line', { x1: 1, y1: 4, x2: 19, y2: 4 })), label))) : null),
    m.view, m.hint);
  clear(area, m.root);
  T.mounted = m;
  watchSize();
  return m;
}

function nodeEvents(m, el, uid) {
  const job = () => m.o.jobs.find((j) => j.uid === uid);
  el.addEventListener('mousedown', (e) => { if (e.shiftKey) e.preventDefault(); });
  el.addEventListener('click', (e) => {
    if (e.target.closest('.t-actions') || performance.now() - m.dragEnd < 250) return;
    if (!m.o.onPick(e, uid)) m.o.onOpen(uid);
  });
  el.addEventListener('dblclick', (e) => e.stopPropagation());
  el.addEventListener('dragstart', (e) => { // onto an input of the job builder (the builder picks the output that fits)
    if (!m.o.toBuilder()) { e.preventDefault(); return; }
    e.dataTransfer.setData('application/x-cryoplug-job', uid);
    e.dataTransfer.effectAllowed = 'link';
  });
  el.addEventListener('contextmenu', (e) => {
    e.preventDefault();
    m.o.onMenu(e, job(), el.querySelector('.t-more') || el);
  });
  el.addEventListener('keydown', (e) => {
    if (e.target !== el) return;
    if (e.key === 'Enter') m.o.onOpen(uid);
    else if (e.key === ' ') { e.preventDefault(); m.o.onPick({ ctrlKey: true }, uid); }
    else if (e.key === 'ContextMenu' || (e.shiftKey && e.key === 'F10')) { e.preventDefault(); m.o.onMenu(e, job(), el.querySelector('.t-more') || el); }
    else {
      const dir = { ArrowUp: 'up', ArrowDown: 'down', ArrowLeft: 'left', ArrowRight: 'right' }[e.key];
      if (!dir) return;
      e.preventDefault();
      const next = neighbour(m, uid, dir);
      if (next) m.els.get(next)?.focus({ preventScroll: true }); // reveal() moves the tree instead
    }
  });
  el.addEventListener('focus', () => { // from the keyboard: the tree moves to show the card (not under a click)
    if (performance.now() - m.pressed > 600) reveal(m, uid);
  });
  el.addEventListener('pointerenter', () => { for (const it of m.byJob.get(uid) || []) it.g.classList.add('hot'); });
  el.addEventListener('pointerleave', () => { for (const it of m.byJob.get(uid) || []) it.g.classList.remove('hot'); });
}

// Arrow keys: up to an input, down to a job using this one (the nearest), left and right along the row.
function neighbour(m, uid, dir) {
  const p = m.layout.pos.get(uid);
  if (dir === 'left' || dir === 'right') {
    const list = m.layout.rows[p.row];
    return list[list.indexOf(uid) + (dir === 'left' ? -1 : 1)] || null;
  }
  const list = (dir === 'up' ? m.layout.ups : m.layout.downs).get(uid);
  let best = null;
  for (const u of list) if (!best || Math.abs(m.layout.pos.get(u).x - p.x) < Math.abs(m.layout.pos.get(best).x - p.x)) best = u;
  return best;
}

function viewEvents(m) {
  const view = m.view;
  view.addEventListener('pointerdown', () => { m.pressed = performance.now(); }, true);
  view.addEventListener('pointerdown', (e) => {
    if (!m.v || (e.pointerType === 'mouse' && e.button !== 0 && e.button !== 1) || e.target.closest('.tree-mini, .t-actions')) return;
    const card = e.target.closest('.tnode');
    if (card) card.draggable = m.o.toBuilder(); // the builder may have got its job type since the last drawing
    if (card?.draggable && e.pointerType === 'mouse') return; // the card goes to the builder: no panning
    cancelAnimationFrame(T.anim);
    if (e.isPrimary) m.pointers.clear(); // a pointer released outside without a capture
    m.pointers.set(e.pointerId, [e.clientX, e.clientY]);
    if (m.pointers.size === 2) {
      const [[x1, y1], [x2, y2]] = [...m.pointers.values()];
      m.pinch = { d0: Math.hypot(x2 - x1, y2 - y1) || 1, mx: (x1 + x2) / 2, my: (y1 + y2) / 2, v0: { ...m.v } };
      m.drag = null;
      for (const id of m.pointers.keys()) view.setPointerCapture(id);
    } else if (m.pointers.size === 1) {
      m.drag = { id: e.pointerId, x0: e.clientX, y0: e.clientY, v0: { ...m.v }, moved: false };
    }
  });
  view.addEventListener('pointermove', (e) => {
    if (!m.pointers.has(e.pointerId)) return;
    m.pointers.set(e.pointerId, [e.clientX, e.clientY]);
    if (m.pinch && m.pointers.size === 2) {
      const [[x1, y1], [x2, y2]] = [...m.pointers.values()];
      const r = view.getBoundingClientRect();
      const { v0, d0 } = m.pinch;
      const k = clampK(v0.k * Math.hypot(x2 - x1, y2 - y1) / d0);
      const gx = (m.pinch.mx - r.left - v0.x) / v0.k;
      const gy = (m.pinch.my - r.top - v0.y) / v0.k;
      setView(m, { k, x: (x1 + x2) / 2 - r.left - gx * k, y: (y1 + y2) / 2 - r.top - gy * k });
      return;
    }
    const d = m.drag;
    if (!d || d.id !== e.pointerId) return;
    const dx = e.clientX - d.x0;
    const dy = e.clientY - d.y0;
    if (!d.moved) {
      if (Math.hypot(dx, dy) < 4) return;
      d.moved = true;
      view.setPointerCapture(e.pointerId);
      view.classList.add('panning');
    }
    setView(m, { k: d.v0.k, x: d.v0.x + dx, y: d.v0.y + dy });
  });
  const up = (e) => {
    if (!m.pointers.delete(e.pointerId)) return;
    if (m.pinch) {
      m.pinch = null;
      m.dragEnd = performance.now();
      save(m);
    }
    const d = m.drag;
    if (d && d.id === e.pointerId) {
      m.drag = null;
      view.classList.remove('panning');
      if (d.moved) { m.dragEnd = performance.now(); save(m); }
    }
    if (!m.pointers.size && m.pending) {
      const redraw = m.pending;
      m.pending = null;
      redraw();
    }
  };
  for (const type of ['pointerup', 'pointercancel', 'lostpointercapture']) view.addEventListener(type, up);
  view.addEventListener('click', (e) => { // the click that ends a drag does not open a job
    if (performance.now() - m.dragEnd < 250) { e.stopPropagation(); e.preventDefault(); }
  }, true);
  view.addEventListener('wheel', (e) => {
    e.preventDefault();
    if (!m.v) return;
    cancelAnimationFrame(T.anim);
    const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? m.vh : 1;
    if (e.ctrlKey || e.metaKey || e.altKey) { // a pinch on a touchpad comes as Ctrl + wheel
      const r = view.getBoundingClientRect();
      setView(m, zoomAround(m, m.v.k * Math.exp(-e.deltaY * unit * 0.0022), e.clientX - r.left, e.clientY - r.top));
    } else {
      const sideways = e.shiftKey && !e.deltaX;
      setView(m, { k: m.v.k, x: m.v.x - (sideways ? e.deltaY : e.deltaX) * unit, y: m.v.y - (sideways ? 0 : e.deltaY) * unit });
    }
    save(m);
  }, { passive: false });
  view.addEventListener('dblclick', (e) => {
    if (!e.target.closest('.tnode, .tree-mini, button, a')) m.o.onDeselect();
  });
  // the browser scrolls the view itself to show a card (Tab, find in page): that becomes a move of the tree
  view.addEventListener('scroll', () => {
    const dx = view.scrollLeft;
    const dy = view.scrollTop;
    if ((!dx && !dy) || !m.v) return;
    view.scrollLeft = 0;
    view.scrollTop = 0;
    setView(m, { k: m.v.k, x: m.v.x - dx, y: m.v.y - dy });
    save(m);
  });
}

// ------------------------------------------------------------------ painting
function paint(m) {
  const o = m.o;
  const lin = o.open && m.layout.pos.has(o.open) ? lineage(m.layout, o.open) : null;
  const fade = !!lin && T.fade;
  const related = (u) => u === o.open || lin.anc.has(u) || lin.desc.has(u);
  for (const j of o.jobs) {
    const el = m.els.get(j.uid);
    if (!el) continue;
    const parent = lin?.parents.has(j.uid);
    const child = lin?.children.has(j.uid);
    const picked = o.picked.has(j.uid);
    const cls = ['tnode', j.status, o.open === j.uid ? 'selected' : '', picked ? 'picked' : '', parent ? 'lineage-parent' : '',
      child ? 'lineage-child' : '', fade && !related(j.uid) ? 'faded' : '', o.shown && !o.shown.has(j.uid) ? 'dim' : ''].filter(Boolean).join(' ');
    if (el.className !== cls) el.className = cls;
    el.draggable = o.toBuilder();
    el.setAttribute('aria-pressed', picked ? 'true' : 'false');
    el.setAttribute('aria-label', `${j.uid} ${j.title}, ${j.status}${parent ? `, input of ${o.open}` : ''}${child ? `, uses ${o.open}` : ''}`);
    const sig = JSON.stringify([j.status, j.title, j.type, Math.round((j.progress || 0) * 100), j.message, j.error, j.highlights,
      (j.outputs || []).map((x) => x.thumbnail || x.name)]);
    if (m.sigs.get(j.uid) !== sig) {
      m.sigs.set(j.uid, sig);
      clear(el, card(m, j));
    }
    const mini = m.miniNodes.get(j.uid);
    const mcls = `mn ${j.status}${fade && !related(j.uid) ? ' faded' : ''}${o.shown && !o.shown.has(j.uid) ? ' dim' : ''}${o.open === j.uid ? ' on' : ''}`;
    if (mini.getAttribute('class') !== mcls) mini.setAttribute('class', mcls);
  }
  const onPath = (l) => l.to === o.open || l.from === o.open || (lin.anc.has(l.from) && lin.anc.has(l.to)) || (lin.desc.has(l.from) && lin.desc.has(l.to));
  for (const { sg, g } of m.segs) {
    let cls = `tlink fam-${sg.family}`;
    if (lin) {
      if (sg.links.some(onPath)) cls += ' lit';
      else if (fade) cls += ' faded';
    }
    if (o.shown && sg.links.every((l) => !o.shown.has(l.from) || !o.shown.has(l.to))) cls += ' dim';
    if (g.classList.contains('hot')) cls += ' hot';
    if (g.getAttribute('class') !== cls) g.setAttribute('class', cls);
  }
  if (lin) {
    m.hint.replaceChildren(h('b', {}, o.open), ' ', h('i', { class: 'key-box parent' }), ' its inputs ', h('i', { class: 'key-box child' }),
      ' jobs using it · Esc or double-click the background to deselect');
  } else if (o.shown) {
    m.hint.replaceChildren(`${o.shown.size} of ${o.jobs.length} jobs match the filters · T centres on them`);
  } else if (o.toBuilder()) {
    m.hint.replaceChildren('Drag a job onto an input of the job builder · click it to see its details · drag the background to move');
  } else {
    m.hint.replaceChildren('Click a job to open it · drag to move · Ctrl + scroll to zoom · T centre · F fit');
  }
}

function card(m, j) {
  const o = m.o;
  const th = o.thumb(j);
  const acts = [];
  const quick = (label, ic, key) => h('button', { type: 'button', class: 't-act', title: label, 'aria-label': `${label} ${j.uid}`,
    onclick: (e) => { e.stopPropagation(); o.onQuick(j, key, e.currentTarget); } }, icon(ic));
  if (j.status === 'building') acts.push(quick('Queue', 'play', 'queue'));
  else if (ACTIVE.includes(j.status) || j.status === 'waiting') acts.push(quick('Stop', 'stop', 'stop'));
  const url = o.view3d(j);
  if (url) {
    acts.push(h('a', { class: 't-act', href: url, target: '_blank', rel: 'noopener', title: 'Open in the 3D viewer',
      'aria-label': `Open ${j.uid} in the 3D viewer`, onclick: (e) => e.stopPropagation() }, icon('eye')));
  }
  acts.push(h('button', { type: 'button', class: 't-act t-more', title: 'Actions', 'aria-label': `Actions for ${j.uid}`, 'aria-haspopup': 'menu',
    onclick: (e) => { e.stopPropagation(); o.onMenu(e, j, e.currentTarget); } }, icon('more')));
  const sub = subline(j, o);
  const pctDone = Math.round((j.progress || 0) * 100);
  return [
    h('div', { class: `t-thumb ${th.img ? '' : 'ph'}`, style: th.style }, th.el),
    h('div', { class: 't-main' },
      h('div', { class: 't-head' }, h('span', { class: 't-uid' }, j.uid),
        h('span', { class: 't-status' }, j.status === 'running' && pctDone ? `running ${pctDone}%` : j.status)),
      h('div', { class: 't-title', title: j.title }, j.title),
      h('div', { class: 't-sub', title: sub }, sub)),
    j.status === 'running' ? h('div', { class: 't-progress' }, h('i', { style: { width: `${Math.max(3, pctDone)}%` } })) : null,
    h('div', { class: 't-actions' }, acts),
  ];
}

function subline(j, o) {
  if (j.status === 'failed') return (j.error || 'Failed').split('\n')[0];
  if (['queued', 'launched', 'running', 'waiting'].includes(j.status) && j.message) return j.message;
  const hl = j.status === 'completed' ? (j.highlights || []).slice(0, 2) : [];
  if (hl.length) return hl.map((x) => `${x.label} ${x.value}`).join(' · ');
  return j.title !== o.typeTitle(j.type) ? o.typeTitle(j.type) : o.ago(j);
}

// ------------------------------------------------------------------ pan and zoom
const clampK = (k) => Math.min(K_MAX, Math.max(K_MIN, k));

function sizeView(m) {
  const content = document.getElementById('content');
  if (!content || !m.view.isConnected) return;
  const top = m.view.getBoundingClientRect().top - content.getBoundingClientRect().top + content.scrollTop;
  const pad = parseFloat(getComputedStyle(content).paddingBottom) || 0;
  const below = m.hint.getBoundingClientRect().bottom - m.view.getBoundingClientRect().bottom; // the line under the tree
  const height = Math.max(340, Math.floor(content.clientHeight - top - pad - below));
  if (m.view.style.height !== `${height}px`) m.view.style.height = `${height}px`;
  m.vw = m.view.clientWidth;
  m.vh = height;
}

function setView(m, v) {
  if (!m.vw || !m.vh) { m.v = { ...v }; return; } // not laid out yet
  const k = clampK(v.k);
  const keep = 60; // a part of the tree always stays on screen
  const x = Math.min(m.vw - keep, Math.max(keep - m.layout.width * k, v.x));
  const y = Math.min(m.vh - keep, Math.max(keep - m.layout.height * k, v.y));
  m.v = { x, y, k };
  T.views.set(m.puid, m.v);
  m.canvas.style.transform = `translate(${x}px, ${y}px) scale(${k})`;
  const grid = 24 * k * (k < 0.4 ? 4 : 1); // the dotted background moves and zooms with the tree
  m.view.style.backgroundSize = `${grid}px ${grid}px`;
  m.view.style.backgroundPosition = `${x}px ${y}px`;
  m.view.classList.toggle('far', k < FAR);
  m.zoomLabel.textContent = `${Math.round(k * 100)}%`;
  drawMiniView(m);
}

function zoomAround(m, k, px, py) {
  const kk = clampK(k);
  return { k: kk, x: px - ((px - m.v.x) / m.v.k) * kk, y: py - ((py - m.v.y) / m.v.k) * kk };
}

const zoomBy = (m, factor) => animateTo(m, zoomAround(m, m.v.k * factor, m.vw / 2, m.vh / 2));

function fitBox(m, box, maxK = 1) {
  const k = clampK(Math.min(maxK, (m.vw - 40) / box.w, (m.vh - 40) / box.h));
  return { k, x: m.vw / 2 - (box.x + box.w / 2) * k, y: m.vh / 2 - (box.y + box.h / 2) * k };
}

function boxOf(m, uids) {
  const ps = uids.map((u) => m.layout.pos.get(u)).filter(Boolean);
  if (!ps.length) return null;
  const x = Math.min(...ps.map((p) => p.x));
  const y = Math.min(...ps.map((p) => p.y));
  return { x, y, w: Math.max(...ps.map((p) => p.x)) + NODE_W - x, h: Math.max(...ps.map((p) => p.y)) + NODE_H - y };
}

// Centre on jobs, zoomed in enough to read them unless they do not fit.
function centreOn(m, uids, animate = true) {
  const box = boxOf(m, uids);
  if (!box) return;
  const k = Math.min(Math.max(m.v.k, 0.8), fitBox(m, box, 1.25).k);
  const v = { k, x: m.vw / 2 - (box.x + box.w / 2) * k, y: m.vh / 2 - (box.y + box.h / 2) * k };
  if (animate) animateTo(m, v);
  else setView(m, v);
}

// T: the jobs picked, else the job open, else those the filters keep, else the newest.
function centreSelection(m) {
  const o = m.o;
  let uids = [...o.picked].filter((u) => m.layout.pos.has(u));
  if (!uids.length && o.open) uids = [o.open];
  if (!uids.length && o.shown?.size) uids = [...o.shown];
  if (!uids.length) uids = [[...o.jobs].sort((a, b) => b.num - a.num)[0]?.uid];
  centreOn(m, uids);
}

function initialView(m) {
  if (!m.vw || !m.vh) return;
  const all = { x: 0, y: 0, w: m.layout.width, h: m.layout.height };
  const fit = fitBox(m, all);
  if (fit.k >= 0.55) { setView(m, fit); return; }
  m.v = { x: 0, y: 0, k: 0.85 };
  centreOn(m, [[...m.o.jobs].sort((a, b) => b.num - a.num)[0].uid], false); // a large project: where the work is
}

// The job just opened comes into view with its inputs and the jobs using it when they fit (the panel that opens
// beside narrows the view); a job opened elsewhere (a link, the builder) is brought in too.
function followOpen(m) {
  const open = m.o.open;
  if (open === m.lastOpen) return;
  m.lastOpen = open;
  if (!open || !m.layout.pos.has(open)) return;
  if (m.v.k < 0.6) { reveal(m, open, 0.6); return; }
  const box = boxOf(m, [open, ...m.layout.ups.get(open), ...m.layout.downs.get(open)]);
  const margin = 24;
  const { v } = m;
  if (box.w * v.k > m.vw - 2 * margin || box.h * v.k > m.vh - 2 * margin) { reveal(m, open); return; }
  const sx = box.x * v.k + v.x;
  const sy = box.y * v.k + v.y;
  const dx = sx < margin ? margin - sx : sx + box.w * v.k > m.vw - margin ? m.vw - margin - sx - box.w * v.k : 0;
  const dy = sy < margin ? margin - sy : sy + box.h * v.k > m.vh - margin ? m.vh - margin - sy - box.h * v.k : 0;
  if (dx || dy) animateTo(m, { k: v.k, x: v.x + dx, y: v.y + dy });
}

// A card partly hidden comes in with the smallest move; a card out of sight (or a change of zoom) is centred.
function reveal(m, uid, k = m.v?.k) {
  const p = m.layout.pos.get(uid);
  const { v } = m;
  if (!v || !m.vw || !p) return; // not laid out yet
  const centre = () => animateTo(m, { k, x: m.vw / 2 - (p.x + NODE_W / 2) * k, y: m.vh / 2 - (p.y + NODE_H / 2) * k });
  if (Math.abs(k - v.k) > 1e-3) { centre(); return; }
  const margin = 24;
  const sx = p.x * v.k + v.x;
  const sy = p.y * v.k + v.y;
  const w = NODE_W * v.k;
  const hh = NODE_H * v.k;
  if (sx + w < 0 || sx > m.vw || sy + hh < 0 || sy > m.vh) { centre(); return; }
  const dx = sx < margin ? margin - sx : sx + w > m.vw - margin ? m.vw - margin - sx - w : 0;
  const dy = sy < margin ? margin - sy : sy + hh > m.vh - margin ? m.vh - margin - sy - hh : 0;
  if (dx || dy) animateTo(m, { k: v.k, x: v.x + dx, y: v.y + dy });
}

function animateTo(m, to) {
  cancelAnimationFrame(T.anim);
  const from = { ...m.v };
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) { setView(m, to); save(m); return; }
  const t0 = performance.now();
  const step = (now) => {
    const t = Math.min(1, (now - t0) / 240);
    const e = 1 - (1 - t) ** 3;
    setView(m, { x: from.x + (to.x - from.x) * e, y: from.y + (to.y - from.y) * e, k: from.k * (to.k / from.k) ** e });
    if (t < 1) T.anim = requestAnimationFrame(step);
    else save(m);
  };
  T.anim = requestAnimationFrame(step);
}

function saved(puid) {
  try {
    const v = JSON.parse(stored(`cryoplug.tree.${puid}`) || 'null');
    return v && [v.x, v.y, v.k].every(Number.isFinite) ? v : null;
  } catch { return null; }
}

function save(m) {
  clearTimeout(T.saveTimer);
  T.saveTimer = setTimeout(() => store(`cryoplug.tree.${m.puid}`, JSON.stringify(m.v)), 300);
}

let watching = false;
function watchSize() {
  if (watching || typeof ResizeObserver !== 'function') return;
  watching = true;
  new ResizeObserver(() => {
    const m = T.mounted;
    if (!m?.root.isConnected) return;
    sizeView(m);
    if (m.v) setView(m, m.v);
    else initialView(m);
  }).observe(document.getElementById('content'));
}

// ------------------------------------------------------------------ overview
function minimap(m) {
  const { width: W, height: H } = m.layout;
  const scale = Math.min(160 / W, 104 / H);
  const svg = s('svg', { class: 'tree-mini', width: Math.round(W * scale), height: Math.round(H * scale), viewBox: `0 0 ${W} ${H}`,
    role: 'img', 'aria-label': 'Overview of the tree: click or drag to move there' });
  m.miniNodes = new Map();
  for (const [uid, p] of m.layout.pos) {
    const r = s('rect', { x: p.x, y: p.y, width: NODE_W, height: NODE_H, rx: 10 });
    svg.appendChild(r);
    m.miniNodes.set(uid, r);
  }
  m.miniView = s('rect', { class: 'mini-view', rx: 6 });
  svg.appendChild(m.miniView);
  const go = (e) => {
    const r = svg.getBoundingClientRect();
    const gx = ((e.clientX - r.left) / r.width) * W;
    const gy = ((e.clientY - r.top) / r.height) * H;
    setView(m, { k: m.v.k, x: m.vw / 2 - gx * m.v.k, y: m.vh / 2 - gy * m.v.k });
  };
  svg.addEventListener('pointerdown', (e) => {
    e.preventDefault();
    cancelAnimationFrame(T.anim);
    svg.setPointerCapture(e.pointerId);
    go(e);
    svg.onpointermove = go;
  });
  svg.addEventListener('pointerup', () => { svg.onpointermove = null; save(m); });
  svg.addEventListener('lostpointercapture', () => { svg.onpointermove = null; });
  return svg;
}

function drawMiniView(m) {
  const { v } = m;
  const { width: W, height: H } = m.layout;
  const whole = v.x >= -1 && v.y >= -1 && v.x + W * v.k <= m.vw + 1 && v.y + H * v.k <= m.vh + 1;
  m.mini.toggleAttribute('hidden', whole);
  if (whole) return;
  const x = Math.max(0, -v.x / v.k);
  const y = Math.max(0, -v.y / v.k);
  m.miniView.setAttribute('x', x);
  m.miniView.setAttribute('y', y);
  m.miniView.setAttribute('width', Math.max(0, Math.min(W, (m.vw - v.x) / v.k) - x));
  m.miniView.setAttribute('height', Math.max(0, Math.min(H, (m.vh - v.y) / v.k) - y));
}
