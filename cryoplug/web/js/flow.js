// Decision map of a job (CryoAtom2 enhanced): the steps it took and the choices it made, as tiles joined by lines from
// the inputs down, like a mind map. The runs of a round sit side by side in one box; the path that leads to the result
// (or to the tile clicked) is lit up, what was tried and left aside is faded. Clicking a tile shows its details and
// its maps and model in 3D; the map fits the panel, + and − zoom (Ctrl/⌘ + scroll too), dragging moves it; it can be
// saved as an SVG or PNG picture.
import { place } from './tree.js';
import { clear, h, icon, s } from './ui.js';

export const FLOW = { w: 240, h: 70, runW: 180, runH: 58, gapX: 26, gapY: 36, gap: 8, head: 28, cols: 4, pad: 18, lane: 12 };
const OFF = new Set(['discarded', 'failed', 'skipped']); // a step that led nowhere
const K_MIN = 0.2;
const K_MAX = 1.6;
const KIND_ICON = { input: 'map', map: 'map', run: 'model', group: 'grid', decision: 'flow', result: 'target' };
const ICONS = new Set(['map', 'model', 'grid', 'flow', 'target', 'sliders', 'search', 'check', 'shield', 'alert', 'layers', 'refresh', 'cube']);
const LABEL = { running: 'running', chosen: 'kept', discarded: 'not kept', failed: 'failed', skipped: 'not needed', warning: 'check', stopped: 'stopped' };

// ------------------------------------------------------------------ layout
// Layered, as the tree view: a step sits one row below its lowest parent, a step used only further down comes down next
// to its first use. A line crossing rows passes between the tiles of each row (a point of it is placed with them),
// the order in each row reduces the crossings, and each tile goes as close as it can to the middle of the steps it is
// joined to. Lines are smooth curves from the bottom of a tile (of the run, under its box) to the top of the next.
export function layoutFlow(nodes, D = FLOW) {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const members = new Map(nodes.filter((n) => n.kind === 'group').map((n) => [n.id, []]));
  for (const n of nodes) if (n.kind !== 'group' && n.group && members.has(n.group)) members.get(n.group).push(n);
  const owner = (id) => {
    const n = byId.get(id);
    return n && n.kind !== 'group' && n.group && members.has(n.group) ? n.group : id;
  };
  const tops = nodes.filter((n) => owner(n.id) === n.id);
  const order = new Map(tops.map((n, i) => [n.id, i]));
  const boxes = new Map();
  for (const n of tops) {
    const b = { id: n.id, node: n, w: D.w, h: D.h, row: 0, x: 0, up: [], down: [], ord: order.get(n.id) };
    if (n.kind === 'group') {
      const k = members.get(n.id).length;
      b.cols = Math.max(1, Math.min(D.cols, k));
      const rows = Math.max(1, Math.ceil(k / b.cols));
      b.w = Math.max(D.w, b.cols * D.runW + (b.cols + 1) * D.gap);
      b.h = D.head + rows * (D.runH + D.gap);
    }
    boxes.set(n.id, b);
  }

  // the lines between the boxes, each once
  const links = [];
  const seen = new Set();
  for (const n of nodes) {
    for (const p of n.parents || []) {
      const a = owner(p);
      const b = owner(n.id);
      if (!byId.has(p) || a === b || seen.has(`${p}>${n.id}`)) continue;
      seen.add(`${p}>${n.id}`);
      links.push({ from: p, to: n.id, a, b });
    }
  }
  const ups = new Map(tops.map((n) => [n.id, new Set()]));
  const downs = new Map(tops.map((n) => [n.id, new Set()]));
  for (const l of links) {
    ups.get(l.b).add(l.a);
    downs.get(l.a).add(l.b);
  }
  const row = new Map();
  const visiting = new Set();
  const rowOf = (id) => {
    if (row.has(id)) return row.get(id);
    if (visiting.has(id)) return -1; // a loop: cut where it closes
    visiting.add(id);
    let r = 0;
    for (const p of ups.get(id)) r = Math.max(r, rowOf(p) + 1);
    visiting.delete(id);
    row.set(id, r);
    return r;
  };
  tops.forEach((n) => rowOf(n.id));
  for (const n of [...tops].sort((a, b) => row.get(b.id) - row.get(a.id))) {
    const below = [...downs.get(n.id)].map((c) => row.get(c)).filter((r) => r > row.get(n.id));
    if (below.length) row.set(n.id, Math.max(row.get(n.id), Math.min(...below) - 1));
  }
  const nrows = 1 + Math.max(0, ...tops.map((n) => row.get(n.id)));
  let rows = Array.from({ length: nrows }, () => []);
  for (const b of boxes.values()) {
    b.row = row.get(b.id);
    rows[b.row].push(b);
  }
  let extra = 0;
  for (const l of links) {
    const ra = row.get(l.a);
    const rb = row.get(l.b);
    l.path = [l.a];
    l.back = rb <= ra;
    if (!l.back) {
      for (let r = ra + 1; r < rb; r++) {
        const d = { id: `~${extra++}`, dummy: true, w: D.lane, h: 0, row: r, x: 0, up: [], down: [], ord: order.get(l.a) + 0.5 };
        boxes.set(d.id, d);
        rows[r].push(d);
        l.path.push(d.id);
      }
    }
    l.path.push(l.b);
    if (l.back) continue;
    for (let i = 1; i < l.path.length; i++) {
      const u = boxes.get(l.path[i - 1]);
      const v = boxes.get(l.path[i]);
      if (!u.down.includes(v.id)) u.down.push(v.id);
      if (!v.up.includes(u.id)) v.up.push(u.id);
    }
  }

  // the order in each row: as the steps came, then by the mean place of their neighbours while it removes crossings
  rows.forEach((list) => list.sort((a, b) => a.ord - b.ord));
  let best = rows.map((list) => list.slice());
  let fewest = crossings(rows);
  for (let it = 0; it < 8 && fewest > 0; it++) {
    if (it % 2) for (let r = nrows - 2; r >= 0; r--) rows[r] = sortRow(rows[r], rows[r + 1], 'down');
    else for (let r = 1; r < nrows; r++) rows[r] = sortRow(rows[r], rows[r - 1], 'up');
    const c = crossings(rows);
    if (c < fewest) { fewest = c; best = rows.map((list) => list.slice()); }
  }
  rows = best;

  // horizontal places: packed, then pulled towards the steps each box is joined to (line points pull harder)
  const spacing = (a, b) => (a.w + b.w) / 2 + (a.dummy || b.dummy ? D.lane : D.gapX);
  for (const list of rows) list.forEach((v, i) => { v.x = i ? list[i - 1].x + spacing(list[i - 1], v) : 0; });
  for (let it = 0; it < 10; it++) {
    const both = it >= 8;
    const downward = it % 2 === 0;
    for (const list of downward || both ? rows : [...rows].reverse()) {
      const desired = [];
      const weights = [];
      for (const v of list) {
        const ids = both ? [...v.up, ...v.down] : downward ? v.up : v.down;
        if (ids.length) {
          desired.push(ids.reduce((sum, id) => sum + boxes.get(id).x, 0) / ids.length);
          weights.push(v.dummy ? 6 : ids.length);
        } else {
          desired.push(v.x);
          weights.push(0.2);
        }
      }
      const xs = place(list, desired, weights, spacing);
      list.forEach((v, i) => { v.x = xs[i]; });
    }
  }
  let left = Infinity;
  let right = -Infinity;
  for (const v of boxes.values()) {
    left = Math.min(left, v.x - v.w / 2);
    right = Math.max(right, v.x + v.w / 2);
  }
  for (const v of boxes.values()) v.x += D.pad - left;
  const rowH = rows.map((list) => Math.max(0, ...list.map((v) => v.h)));
  const rowY = [D.pad];
  for (let r = 1; r < nrows; r++) rowY.push(rowY[r - 1] + rowH[r - 1] + D.gapY);

  const pos = new Map();
  for (const v of boxes.values()) {
    if (v.dummy) continue;
    pos.set(v.id, { id: v.id, left: v.x - v.w / 2, top: rowY[v.row], w: v.w, h: v.h, row: v.row, kind: v.node.kind });
  }
  const runs = new Map(); // the runs inside their box
  for (const [gid, list] of members) {
    const g = pos.get(gid);
    if (!g) continue;
    const cols = boxes.get(gid).cols;
    const inset = (g.w - (cols * D.runW + (cols - 1) * D.gap)) / 2;
    list.forEach((n, i) => {
      runs.set(n.id, { id: n.id, group: gid, left: g.left + inset + (i % cols) * (D.runW + D.gap),
        top: g.top + D.head + Math.floor(i / cols) * (D.runH + D.gap), w: D.runW, h: D.runH });
    });
  }

  // the lines: out of the bottom of a tile (of a run: under its box), into the top of the next, spread along its edge
  const f = (n) => Math.round(n * 10) / 10;
  for (const l of links) {
    const src = runs.get(l.from) || pos.get(l.a);
    const box = pos.get(l.a);
    l.x1 = src.left + src.w / 2;
    l.y1 = box.top + box.h;
    l.via = l.path.slice(1, -1).map((id) => boxes.get(id));
  }
  const reaching = new Map();
  for (const l of links) {
    if (!reaching.has(l.b)) reaching.set(l.b, []);
    reaching.get(l.b).push(l);
  }
  for (const [id, list] of reaching) {
    const t = pos.get(id);
    const before = (l) => (l.via.length ? l.via[l.via.length - 1].x : l.x1);
    list.sort((a, b) => before(a) - before(b));
    const step = list.length > 1 ? Math.min(16, (t.w - 48) / (list.length - 1)) : 0;
    list.forEach((l, i) => {
      l.x2 = t.left + t.w / 2 + (i - (list.length - 1) / 2) * step;
      l.y2 = t.top;
    });
  }
  for (const l of links) {
    const pts = [[l.x1, l.y1]];
    for (const d of l.via) pts.push([d.x, rowY[d.row]], [d.x, rowY[d.row] + rowH[d.row]]);
    pts.push([l.x2, l.y2]);
    let d = `M${f(pts[0][0])},${f(pts[0][1])}`;
    for (let i = 1; i < pts.length; i++) {
      const [px, py] = pts[i - 1];
      const [qx, qy] = pts[i];
      if (Math.abs(px - qx) < 0.5 && qy >= py) d += ` L${f(qx)},${f(qy)}`;
      else {
        const my = l.back ? Math.max(py, qy) + D.gapY / 2 : (py + qy) / 2;
        d += ` C${f(px)},${f(my)} ${f(qx)},${f(l.back ? Math.min(py, qy) - D.gapY / 2 : my)} ${f(qx)},${f(qy)}`;
      }
    }
    l.d = d;
  }
  return {
    pos, runs, owner,
    edges: links.map(({ from, to, a, b, d, x1, y1, x2, y2, back }) => ({ from, to, a, b, d, x1, y1, x2, y2, back })),
    rows: rows.map((list) => list.filter((v) => !v.dummy).map((v) => v.id)),
    width: Math.ceil(right - left + 2 * D.pad),
    height: Math.ceil(rowY[nrows - 1] + rowH[nrows - 1] + D.pad),
  };
}

function sortRow(list, ref, side) {
  const at = new Map(ref.map((v, i) => [v.id, i]));
  return list.map((v, i) => {
    const ps = v[side].map((id) => at.get(id)).filter((p) => p !== undefined);
    return { v, i, bc: ps.length ? ps.reduce((a, b) => a + b, 0) / ps.length : i * ref.length / Math.max(1, list.length) };
  }).sort((a, b) => a.bc - b.bc || a.v.ord - b.v.ord).map((k) => k.v);
}

function crossings(rows) {
  let total = 0;
  for (let r = 0; r + 1 < rows.length; r++) {
    const at = new Map(rows[r + 1].map((v, i) => [v.id, i]));
    const ends = [];
    rows[r].forEach((v, i) => { for (const id of v.down) if (at.has(id)) ends.push([i, at.get(id)]); });
    for (let i = 0; i < ends.length; i++) {
      for (let j = i + 1; j < ends.length; j++) {
        if ((ends[i][0] - ends[j][0]) * (ends[i][1] - ends[j][1]) < 0) total += 1;
      }
    }
  }
  return total;
}

// ------------------------------------------------------------------ states
// The word on a tile: a run kept is the best one; the runs not kept say it by fading (most of them are).
function chipLabel(n, status) {
  if (n.kind === 'result') return '';
  if (n.kind === 'run') return status === 'chosen' ? 'best' : status === 'discarded' ? '' : LABEL[status] || '';
  return LABEL[status] || '';
}

// A box shows what its runs are doing: running while one runs, kept when one was kept, not kept when none was.
export function groupStatus(members, own = 'done') {
  if (!members.length) return own;
  const sts = members.map((m) => m.status);
  if (sts.includes('running')) return 'running';
  if (sts.includes('chosen')) return 'chosen';
  if (sts.every((x) => x === 'failed')) return 'failed';
  if (sts.every((x) => OFF.has(x))) return 'discarded';
  return 'done';
}

// The steps that lead to a step: up through its parents; of a decision taken among several (the best of runs and of
// other boxes), only those that were kept, unless none was (the step came from what was not kept). A run brings its box
// and what the box comes from.
export function lineageOf(target, byId, statusOf, owner) {
  const lit = new Set();
  const todo = [target];
  const up = (n) => {
    const parents = (n.parents || []).filter((p) => byId.has(p));
    const kept = parents.filter((p) => !OFF.has(statusOf(byId.get(p))));
    todo.push(...(kept.length ? kept : parents));
  };
  while (todo.length) {
    const id = todo.pop();
    if (lit.has(id) || !byId.has(id)) continue;
    lit.add(id);
    up(byId.get(id));
    const g = owner(id);
    if (g !== id && !lit.has(g)) {
      lit.add(g);
      up(byId.get(g));
    }
  }
  return lit;
}

// ------------------------------------------------------------------ drawing
// o: { state (kept across redraws: zoom, selection, place), active (the job still runs), viewerUrl(items),
// scroller() (the element scrolling the panel), name (for the pictures saved) }
export function decisionMap(sec, o = {}) {
  const nodes = (sec.nodes || []).filter((n) => n && n.id);
  const st = o.state || {};
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const L = layoutFlow(nodes);
  const kids = new Map(); // the runs of each box
  for (const n of nodes) {
    if (n.kind === 'group' || L.owner(n.id) === n.id) continue;
    if (!kids.has(n.group)) kids.set(n.group, []);
    kids.get(n.group).push(n);
  }
  const statusOf = (n) => {
    const v = n.kind === 'group' ? groupStatus(kids.get(n.id) || [], n.status) : n.status || 'done';
    return v === 'running' && !o.active ? 'stopped' : v;
  };
  const resultId = nodes.find((n) => n.kind === 'result')?.id;
  const lastKept = [...nodes].reverse().find((n) => n.kind === 'decision' && n.status === 'chosen')?.id;

  const els = new Map();
  const inner = h('div', { class: 'flow-inner', style: { width: `${L.width}px`, height: `${L.height}px` } });
  const svg = s('svg', { class: 'flow-lines', width: L.width, height: L.height, viewBox: `0 0 ${L.width} ${L.height}`, 'aria-hidden': 'true' });
  const paths = L.edges.map((e) => {
    const p = s('path', { d: e.d });
    svg.appendChild(p);
    return { e, p };
  });
  inner.appendChild(svg);
  const tile = (n, box, small) => {
    const status = statusOf(n);
    const label = chipLabel(n, status);
    const chip = label ? h('span', { class: 'ft-chip' }, label) : null;
    const lines = (n.lines || []).slice(0, 2);
    const base = `ftile k-${n.kind}${small ? ' small' : ''}`;
    const el = h('button', {
      type: 'button', class: base, 'data-id': n.id,
      style: { left: `${box.left}px`, top: `${box.top}px`, width: `${box.w}px`, height: `${box.h}px` },
      title: [n.title, ...(n.lines || [])].join('\n'), onclick: (ev) => { ev.stopPropagation(); select(st.sel === n.id ? null : n.id); },
    }, h('span', { class: 'ft-head' }, icon(ICONS.has(n.icon) ? n.icon : KIND_ICON[n.kind] || 'flow'), h('span', { class: 'ft-title' }, n.title), chip),
    lines.map((t, i) => h('span', { class: `ft-line${i ? ' sub' : ''}` }, t)));
    els.set(n.id, { el, status, base });
    return el;
  };
  for (const [id, box] of L.pos) {
    const n = byId.get(id);
    if (n.kind === 'group') {
      const list = kids.get(id) || [];
      const finished = list.filter((m) => m.status !== 'running').length;
      const count = !list.length ? 'waiting' : finished < list.length ? `${finished}/${list.length} done` : `${list.length} run${list.length > 1 ? 's' : ''}`;
      const pick = (ev) => { ev.stopPropagation(); select(st.sel === id ? null : id); };
      const el = h('div', {
        class: 'fgroup', 'data-id': id, role: 'button', tabindex: '0', 'aria-label': `${n.title}: ${count}`,
        style: { left: `${box.left}px`, top: `${box.top}px`, width: `${box.w}px`, height: `${box.h}px` },
        onclick: pick, onkeydown: (ev) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); pick(ev); } },
      }, h('div', { class: 'fg-head', title: n.title }, icon(ICONS.has(n.icon) ? n.icon : 'grid'), h('span', { class: 'fg-title' }, n.title),
        h('span', { class: 'fg-count' }, count)));
      els.set(id, { el, status: statusOf(n), base: 'fgroup' });
      inner.appendChild(el);
    } else {
      inner.appendChild(tile(n, box, false));
    }
  }
  for (const [id, box] of L.runs) inner.appendChild(tile(byId.get(id), box, true));

  // the path lit up, the tile chosen, its details
  const detail = h('div', { class: 'flow-detail' });
  const paint = () => {
    const target = st.sel && byId.has(st.sel) ? st.sel : resultId || lastKept;
    const lit = target ? lineageOf(target, byId, statusOf, L.owner) : new Set();
    for (const [id, { el, status, base }] of els) {
      el.className = `${base} st-${status}${lit.has(id) ? ' lit' : ''}${st.sel === id ? ' selected' : ''}`;
      if (el.tagName === 'BUTTON') el.setAttribute('aria-pressed', st.sel === id ? 'true' : 'false');
    }
    for (const { e, p } of paths) {
      const cls = ['flink'];
      if (lit.has(e.from) && lit.has(e.to)) cls.push('lit');
      else if (OFF.has(statusOf(byId.get(e.from)))) cls.push('off');
      if (statusOf(byId.get(e.to)) === 'running') cls.push('busy');
      p.setAttribute('class', cls.join(' '));
    }
    for (const { p } of paths.filter((x) => x.p.classList.contains('lit'))) svg.appendChild(p); // the path over the other lines
    showDetail(detail, st.sel && byId.get(st.sel), statusOf, kids, o);
  };
  const select = (id) => {
    st.sel = id || null;
    paint();
  };

  // zoom and move
  const canvas = h('div', { class: 'flow-canvas' }, inner);
  const view = h('div', { class: 'flow-view' }, canvas);
  const zoomLabel = h('span', { class: 'flow-zoom mono' });
  let k = st.fit === false && st.k ? st.k : 1; // zoomed by hand: kept across redraws
  const fitK = () => Math.max(K_MIN, Math.min(1, (view.clientWidth - 2) / L.width));
  const apply = () => {
    canvas.style.width = `${Math.ceil(L.width * k)}px`;
    canvas.style.height = `${Math.ceil(L.height * k)}px`;
    inner.style.transform = `scale(${k})`;
    zoomLabel.textContent = `${Math.round(k * 100)}%`;
    st.k = k;
  };
  const zoomTo = (next, px = view.clientWidth / 2) => {
    next = Math.max(K_MIN, Math.min(K_MAX, next));
    const cx = (view.scrollLeft + px) / k;
    k = next;
    st.fit = false;
    apply();
    view.scrollLeft = Math.max(0, cx * k - px);
  };
  const fit = () => { st.fit = true; k = fitK(); apply(); view.scrollLeft = 0; };
  view.addEventListener('wheel', (e) => {
    if (!e.ctrlKey && !e.metaKey) return;
    e.preventDefault();
    zoomTo(k * Math.exp(-e.deltaY * 0.0015), e.clientX - view.getBoundingClientRect().left);
  }, { passive: false });
  let drag = null;
  view.addEventListener('pointerdown', (e) => {
    if (e.button !== 0 || e.target.closest('.ftile, .fgroup')) return;
    drag = { x: e.clientX, y: e.clientY, left: view.scrollLeft, top: o.scroller?.()?.scrollTop ?? 0, moved: false };
    view.setPointerCapture(e.pointerId);
  });
  view.addEventListener('pointermove', (e) => {
    if (!drag) return;
    const dx = e.clientX - drag.x;
    const dy = e.clientY - drag.y;
    if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
    view.scrollLeft = drag.left - dx;
    const sc = o.scroller?.();
    if (sc) sc.scrollTop = drag.top - dy;
  });
  const endDrag = (e) => {
    if (!drag) return;
    if (!drag.moved && st.sel) select(null); // a click on the background: back to the path of the result
    drag = null;
    if (view.hasPointerCapture(e.pointerId)) view.releasePointerCapture(e.pointerId);
  };
  view.addEventListener('pointerup', endDrag);
  view.addEventListener('pointercancel', endDrag);
  view.addEventListener('scroll', () => { st.left = view.scrollLeft; }, { passive: true });
  let sized = false;
  let width = 0;
  const resize = () => {
    if (!view.isConnected || !view.clientWidth || view.clientWidth === width) return;
    width = view.clientWidth;
    if (st.fit !== false) { k = fitK(); apply(); }
    if (!sized && st.left) view.scrollLeft = st.left;
    sized = true;
  };
  const watch = new ResizeObserver(() => {
    if (!view.isConnected) { watch.disconnect(); return; } // redrawn: this one is gone
    requestAnimationFrame(resize); // the canvas resized inside the observer's call would notify it again
  });
  watch.observe(view);
  apply();

  const btnIcon = (ic, label, onclick) => h('button', { type: 'button', class: 'icon-btn', 'aria-label': label, title: label, onclick }, icon(ic));
  const save = (kind) => {
    const target = st.sel && byId.has(st.sel) ? st.sel : resultId || lastKept;
    const lit = target ? lineageOf(target, byId, statusOf, L.owner) : new Set();
    savePicture(kind, pictureSvg(view, L, byId, kids, statusOf, lit), L, o.name || 'decision_map');
  };
  const bar = h('div', { class: 'flow-bar' },
    h('span', { class: 'flow-legend' },
      h('span', { class: 'lg lg-path' }, 'path to the result'), h('span', { class: 'lg lg-kept' }, 'kept'),
      h('span', { class: 'lg lg-off' }, 'not kept'), h('span', { class: 'lg lg-failed' }, 'failed'),
      o.active ? h('span', { class: 'lg lg-running' }, 'running') : null),
    h('span', { class: 'grow' }),
    btnIcon('minus', 'Zoom out', () => zoomTo(k / 1.25)), zoomLabel, btnIcon('plus', 'Zoom in', () => zoomTo(k * 1.25)),
    h('button', { type: 'button', class: 'btn small', title: 'Fit the map to the panel', onclick: fit }, 'Fit'),
    h('button', { type: 'button', class: 'btn small', title: 'Save the map as an SVG picture (slides, papers)', onclick: () => save('svg') }, icon('download'), 'SVG'),
    h('button', { type: 'button', class: 'btn small', title: 'Save the map as a PNG picture', onclick: () => save('png') }, icon('download'), 'PNG'));
  paint();
  return h('div', { class: 'flowmap' }, bar, view, detail);
}

function showDetail(el, n, statusOf, kids, o) {
  if (!n) {
    clear(el, h('span', { class: 'muted small' }, 'Click a tile for its details: the path leading to it lights up. '
      + 'Drag to move, Ctrl/⌘ + scroll to zoom.'));
    return;
  }
  const status = statusOf(n);
  const list = n.kind === 'group' ? kids.get(n.id) || [] : [];
  const tally = (x) => list.filter((m) => m.status === x).length;
  const counts = list.length ? [['chosen', 'kept'], ['discarded', 'not kept'], ['failed', 'failed'], ['running', 'running']]
    .filter(([x]) => tally(x)).map(([x, label]) => `${tally(x)} ${label}`).join(' · ') : '';
  clear(el,
    h('div', { class: 'fd-head' }, icon(ICONS.has(n.icon) ? n.icon : KIND_ICON[n.kind] || 'flow'), h('b', {}, n.title),
      LABEL[status] && n.kind !== 'result' ? h('span', { class: `ft-chip st-${status}` }, LABEL[status]) : null, h('span', { class: 'grow' }),
      n.view && n.view.length && o.viewerUrl ? h('a', { class: 'btn small', href: o.viewerUrl(n.view), target: '_blank', rel: 'noopener',
        title: `Open ${n.view.map((v) => v.label).join(', ')} in the 3D viewer` }, icon('eye'), 'View 3D') : null),
    n.lines && n.lines.length ? h('div', { class: 'fd-lines' }, n.lines.join(' · ')) : null,
    counts ? h('div', { class: 'fd-lines' }, counts) : null,
    n.detail ? h('div', { class: 'fd-text' }, n.detail) : null);
}

// ------------------------------------------------------------------ pictures
// The map as it is drawn (colours of the current theme), as a standalone SVG; PNG at twice the size.
function pictureSvg(root, L, byId, kids, statusOf, lit) {
  const cs = getComputedStyle(root);
  const v = (name, fallback) => cs.getPropertyValue(name).trim() || fallback;
  const C = {
    bg: v('--surface-1', '#fcfcfb'), box: v('--surface-2', '#f0efec'), line: v('--hairline', '#e1e0d9'), ink1: v('--ink-1', '#0b0b0b'),
    ink2: v('--ink-2', '#52514e'), ink3: v('--ink-3', '#898781'), accent: v('--accent', '#2a78d6'), wash: v('--accent-wash', 'rgba(42,120,214,.1)'),
  };
  const stripe = {
    running: v('--st-running', '#2a78d6'), chosen: v('--st-good', '#0ca30c'), discarded: v('--st-idle', '#898781'), failed: v('--st-critical', '#d03b3b'),
    skipped: v('--st-idle', '#898781'), warning: v('--st-warn', '#fab219'), stopped: v('--st-serious', '#ec835a'),
  };
  const kindStripe = { input: v('--series-1', '#2a78d6'), map: v('--series-1', '#2a78d6'), decision: v('--series-7', '#4a3aa7'), run: C.ink3, result: C.accent };
  const font = cs.fontFamily || 'sans-serif';
  const ctx = document.createElement('canvas').getContext('2d');
  const fitText = (text, px, weight, max) => {
    ctx.font = `${weight} ${px}px ${font}`;
    const t = String(text);
    if (ctx.measureText(t).width <= max) return t;
    let lo = 0;
    let hi = t.length;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (ctx.measureText(`${t.slice(0, mid)}…`).width <= max) lo = mid; else hi = mid - 1;
    }
    return `${t.slice(0, lo)}…`;
  };
  const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const out = [`<svg xmlns="http://www.w3.org/2000/svg" width="${L.width}" height="${L.height}" viewBox="0 0 ${L.width} ${L.height}" font-family="${esc(font)}">`,
    `<rect width="${L.width}" height="${L.height}" fill="${C.bg}"/>`];
  const edgeOf = (e) => {
    if (lit.has(e.from) && lit.has(e.to)) return `stroke="${C.accent}" stroke-width="2.5"`;
    return OFF.has(statusOf(byId.get(e.from))) ? `stroke="${C.ink3}" stroke-width="1.5" stroke-dasharray="4 4" opacity=".45"` : `stroke="${C.ink3}" stroke-width="1.5" opacity=".8"`;
  };
  for (const e of L.edges) out.push(`<path d="${e.d}" fill="none" ${edgeOf(e)}/>`);
  const tileSvg = (n, b) => {
    const status = statusOf(n);
    const on = lit.has(n.id);
    const dim = OFF.has(status) && !on ? ' opacity=".55"' : '';
    const fill = n.kind === 'result' ? C.wash : status === 'failed' ? 'rgba(208,59,59,.08)' : C.bg;
    out.push(`<g${dim}><rect x="${b.left}" y="${b.top}" width="${b.w}" height="${b.h}" rx="8" fill="${C.bg}"/>`,
      `<rect x="${b.left}" y="${b.top}" width="${b.w}" height="${b.h}" rx="8" fill="${fill}" stroke="${on ? C.accent : C.line}" stroke-width="${on ? 2 : 1}"/>`,
      `<rect x="${b.left + 1}" y="${b.top + 6}" width="4" height="${b.h - 12}" rx="2" fill="${status === 'done' ? kindStripe[n.kind] || C.ink3 : stripe[status] || C.ink3}"/>`);
    const small = b.w < FLOW.w;
    const label = chipLabel(n, status);
    ctx.font = `600 10px ${font}`;
    const chipW = label ? Math.ceil(ctx.measureText(label).width) + 10 : 0;
    const px = small ? 12 : 12.5;
    out.push(`<text x="${b.left + 12}" y="${b.top + 19}" font-size="${px}" font-weight="600" fill="${C.ink1}">${esc(fitText(n.title, px, 600, b.w - 22 - chipW))}</text>`);
    if (label) {
      out.push(`<rect x="${b.left + b.w - chipW - 7}" y="${b.top + 8}" width="${chipW}" height="15" rx="7.5" fill="${C.box}"/>`,
        `<text x="${b.left + b.w - chipW / 2 - 7}" y="${b.top + 19}" font-size="10" font-weight="600" text-anchor="middle" fill="${C.ink2}">${esc(label)}</text>`);
    }
    (n.lines || []).slice(0, 2).forEach((t, i) => {
      out.push(`<text x="${b.left + 12}" y="${b.top + 37 + i * 15}" font-size="11" fill="${i ? C.ink3 : C.ink2}">${esc(fitText(t, 11, 400, b.w - 20))}</text>`);
    });
    out.push('</g>');
  };
  for (const [id, b] of L.pos) {
    const n = byId.get(id);
    if (n.kind !== 'group') { tileSvg(n, b); continue; }
    const on = lit.has(id);
    const dim = statusOf(n) === 'discarded' && !on ? ' opacity=".6"' : '';
    const list = kids.get(id) || [];
    out.push(`<g${dim}><rect x="${b.left}" y="${b.top}" width="${b.w}" height="${b.h}" rx="12" fill="${C.box}" stroke="${on ? C.accent : C.line}" stroke-width="${on ? 1.5 : 1}"/>`,
      `<text x="${b.left + 12}" y="${b.top + 19}" font-size="11.5" font-weight="600" fill="${C.ink2}">${esc(fitText(n.title, 11.5, 600, b.w - 80))}</text>`,
      `<text x="${b.left + b.w - 10}" y="${b.top + 19}" font-size="10.5" text-anchor="end" fill="${C.ink3}">${list.length} run${list.length === 1 ? '' : 's'}</text></g>`);
  }
  for (const [id, b] of L.runs) tileSvg(byId.get(id), b);
  out.push('</svg>');
  return out.join('\n');
}

function savePicture(kind, svgText, L, name) {
  const download = (blob, ext) => {
    const url = URL.createObjectURL(blob);
    const a = h('a', { href: url, download: `${name}.${ext}` });
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  };
  const svgBlob = new Blob([svgText], { type: 'image/svg+xml' });
  if (kind === 'svg') { download(svgBlob, 'svg'); return; }
  const img = new Image();
  const url = URL.createObjectURL(svgBlob);
  img.onload = () => {
    const scale = 2;
    const canvas = h('canvas', { width: String(L.width * scale), height: String(L.height * scale) });
    const g = canvas.getContext('2d');
    g.scale(scale, scale);
    g.drawImage(img, 0, 0);
    URL.revokeObjectURL(url);
    canvas.toBlob((blob) => { if (blob) download(blob, 'png'); }, 'image/png');
  };
  img.src = url;
}
