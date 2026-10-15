// Move & fit panel of the 3D viewer, the ChimeraX way: maps, masks and models moved in space with the mouse (ChimeraX's
// "move picked models"), or with the move / turn commands, fitted into a map as with fitmap (the server optimises the
// position: average map value at the atoms, or overlap / correlation of maps), and saved as a "Fit in map" job that
// writes the placed model, or the map resampled on the grid of the target. Ctrl + Z undoes the last move.
import { api } from '../api.js';
import { clear, h, icon, toast } from '../ui.js';
import * as R from './rigid.js';
import * as scene from './scene.js';

const METRICS = [
  ['overlap', 'Overlap', 'Sum of the products of the map values (ChimeraX’s default)'],
  ['correlation', 'Correlation', 'Correlation about zero'],
  ['cam', 'Cam', 'Correlation about the mean'],
];
const DEGREES_PER_PIXEL = 0.4;

export function createPlacePanel(root, ctl) {
  const P = {
    open: false, item: null, mouse: false, target: null, resolution: 0, metric: 'overlap', search: 0, radius: 0, chains: '',
    busy: false, result: null, history: [], job: null, saving: false, error: '', polish: true,
  };
  const drag = { on: false };
  const queue = { pending: null, running: false };

  const movable = () => ctl.items().filter((i) => !i.series);
  const targets = () => ctl.items().filter((i) => i.kind === 'map' && !i.series && i !== P.item);
  const label = (i) => `#${i.id} ${i.label}`;
  const add = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];

  // How far an item is from where its file puts it: the shift of its centre and the rotation angle.
  function moved(item) {
    const m = item.transform;
    if (!m) return 'where its file puts it';
    const c = scene.itemCenter(item);
    const c0 = c ? R.apply(R.invert(m), c) : null;
    const d = c ? Math.hypot(c[0] - c0[0], c[1] - c0[1], c[2] - c0[2]) : R.shift(m);
    return `moved ${d.toFixed(1)} Å · ${R.angle(m).toFixed(1)}° from where its file puts it`;
  }

  // ---------------------------------------------------------- moving
  // Updates sent while dragging: only the latest position matters, one Mol* update at a time.
  function schedule(item, m) {
    queue.pending = { item, m };
    if (queue.running) return;
    queue.running = true;
    (async () => {
      while (queue.pending) {
        const next = queue.pending;
        queue.pending = null;
        try { await ctl.setTransform(next.item, next.m); } catch (err) { ctl.fail(err, 'Move: '); }
      }
      queue.running = false;
      renderPosition();
    })();
  }

  function remember(item, before) {
    P.history.push({ item, before: before ? [...before] : null });
    if (P.history.length > 100) P.history.shift();
  }

  // A new position for an item (a command, a fit, a reset): undoable.
  async function place(item, m, what = '') {
    if (item.series) throw new Error('A volume series cannot be moved');
    remember(item, item.transform);
    await ctl.setTransform(item, m);
    if (what) ctl.log(what);
    ctl.syncHash();
    render();
  }

  async function undo() {
    const last = P.history.pop();
    if (!last) { setStatus('Nothing to undo'); return; }
    if (!ctl.items().includes(last.item)) { undo(); return; }
    await ctl.setTransform(last.item, last.before);
    ctl.log(`Undone: #${last.item.id} ${moved(last.item)}`);
    ctl.syncHash();
    render();
  }

  function pointerDown(e) {
    if (!P.open || !P.mouse || !P.item || drag.on || (e.button !== 0 && e.button !== 2)) return;
    const along = e.button === 2 || e.ctrlKey || e.metaKey;
    Object.assign(drag, {
      on: true, moved: false, x: e.clientX, y: e.clientY, item: P.item,
      mode: along ? (e.shiftKey ? 'depth' : 'shift') : 'rotate',
      before: P.item.transform ? [...P.item.transform] : null,
      current: P.item.transform ? [...P.item.transform] : R.identity(),
      center: scene.itemCenter(P.item) || scene.cameraState().target,
    });
  }

  function pointerMove(e) {
    if (!drag.on) return;
    const dx = e.clientX - drag.x;
    const dy = e.clientY - drag.y;
    if (!dx && !dy) return;
    drag.x = e.clientX;
    drag.y = e.clientY;
    drag.moved = true;
    const f = scene.screenFrame(drag.center);
    let inc;
    if (drag.mode === 'rotate') { // the side facing the viewer follows the mouse (screen y points down)
      inc = R.rotation([0, 1, 2].map((k) => f.right[k] * dy + f.up[k] * dx), Math.hypot(dx, dy) * DEGREES_PER_PIXEL, drag.center);
    } else {
      const v = drag.mode === 'shift' ? [0, 1, 2].map((k) => (f.right[k] * dx - f.up[k] * dy) * f.pixel)
        : f.toward.map((t) => t * dy * f.pixel); // dragging up pushes it away
      inc = R.translation(v);
      drag.center = add(drag.center, v);
    }
    drag.current = R.orthonormalize(R.multiply(inc, drag.current));
    schedule(drag.item, drag.current);
  }

  function pointerUp() {
    if (!drag.on) return;
    drag.on = false;
    if (!drag.moved) return;
    remember(drag.item, drag.before);
    P.result = null;
    ctl.syncHash();
    setStatus(`#${drag.item.id} ${moved(drag.item)}`);
  }

  // A click in the view while moving with the mouse picks the item to move. Returns true when used.
  function pick(d) {
    if (!P.open || !P.mouse || !d) return false;
    const item = ctl.itemOf(d);
    if (item && item !== P.item && !item.series) {
      setItem(item);
      ctl.log(`Mouse moves #${item.id} ${item.label}`);
    }
    return true;
  }

  function setMouse(on) {
    P.mouse = !!on && !!P.item;
    scene.setCameraDrag(!P.mouse);
    ctl.canvasElement().classList.toggle('moving', P.mouse);
    ctl.setHint(P.mouse ? `Moving #${P.item.id}: left-drag rotates it about its centre · right-drag (Ctrl + drag) moves it · Shift + right-drag: along the view · click another item to move it · Esc: camera` : null);
    render();
  }

  // --------------------------------------------------------- commands
  async function moveBy(item, vector) {
    await place(item, R.multiply(R.translation(vector), item.transform || R.identity()));
    ctl.log(`#${item.id} moved by ${Math.hypot(...vector).toFixed(2)} Å: now ${moved(item)}`);
  }

  async function turnBy(item, axis, degrees, center) {
    const c = center || scene.itemCenter(item);
    if (!c) throw new Error(`#${item.id} has nothing drawn to turn about`);
    await place(item, R.orthonormalize(R.multiply(R.rotation(axis, degrees, c), item.transform || R.identity())));
    ctl.log(`#${item.id} turned by ${degrees}°: now ${moved(item)}`);
  }

  async function reset(list) {
    const moved = list.filter((i) => i.transform);
    for (const item of moved) await place(item, null);
    return moved;
  }

  // ------------------------------------------------------------- fit
  async function fit(opts = {}) {
    const item = opts.item || P.item;
    const target = opts.target || P.target;
    if (!item) throw new Error('Pick the item to fit');
    if (!target || target === item) throw new Error('Pick the map to fit into');
    const isModel = item.kind === 'model';
    const resolution = isModel ? Number(opts.resolution ?? P.resolution) || 0 : 0;
    const metric = isModel && !resolution ? 'overlap' : opts.metric || P.metric;
    const body = {
      target: target.path, target_label: label(target), target_matrix: R.rows(target.transform || R.identity()), target_level: target.level,
      moving: { kind: isModel ? 'model' : 'map', path: item.path, label: label(item), matrix: R.rows(item.transform || R.identity()),
        ...(isModel ? { chains: opts.chains ?? P.chains } : { level: item.level }) },
      resolution, metric, search: Number(opts.search ?? P.search) || 0, radius: Number(opts.radius ?? P.radius) || 0,
      polish: !!(opts.polish ?? (opts.fromCommand ? false : P.polish)), // the command is ChimeraX's fitmap: no second stage
      ...(opts.shift === false ? { shift: false } : {}), ...(opts.rotate === false ? { rotate: false } : {}),
    };
    P.busy = true;
    setStatus(body.search ? `Searching from ${body.search} random placements…` : `Fitting ${label(item)} in ${label(target)}…`);
    render();
    try {
      const r = await api.fit(ctl.project(), body);
      const m = R.fromRows(r.matrix);
      if (!m) throw new Error('The server returned no usable position');
      await place(item, m);
      P.result = { ...r, item, target, resolution };
      r.message.split('\n').forEach((line, i) => ctl.log(line, i ? '' : 'echo'));
      setStatus('');
      return r;
    } finally {
      P.busy = false;
      render();
    }
  }

  async function applyFit(f) {
    const m = R.fromRows(f.matrix);
    if (m && P.result?.item) await place(P.result.item, m, `#${P.result.item.id}: fit with score ${f.score.toFixed(4)} (found ${f.hits}×)`);
  }

  // ----------------------------------------------------------- saving
  async function outputRef(path, types) {
    for (const job of await api.jobs(ctl.project())) {
      for (const o of job.outputs || []) if (o.path === path && types.includes(o.type)) return { job: job.uid, output: o.name };
    }
    return null;
  }

  async function save() {
    const item = P.item;
    if (!item || P.saving) return;
    P.saving = true;
    render();
    try {
      const project = ctl.project();
      const isModel = item.kind === 'model';
      const ref = await outputRef(item.path, isModel ? ['model'] : ['map', 'mask']);
      if (!ref) throw new Error(`${item.label} is not an output of this project`);
      const inputs = { [isModel ? 'model' : 'moving_map']: ref };
      let matrix = item.transform || R.identity();
      const target = P.target && P.target !== item ? P.target : null;
      const targetRef = target && await outputRef(target.path, ['map']);
      if (targetRef) { // placed relative to the target map as it is on disk
        inputs.map = targetRef;
        matrix = R.multiply(R.invert(target.transform || R.identity()), matrix);
      }
      if (R.isIdentity(matrix)) throw new Error('Nothing to save: the item is where its file puts it (move or fit it first)');
      const params = { matrix: JSON.stringify(R.rows(matrix)), fit: false, chains: isModel ? P.chains : '', polish: P.polish,
        resolution: isModel ? P.resolution : 0, metric: isModel && !P.resolution ? 'overlap' : P.metric,
        level: isModel ? 0 : item.level, target_level: targetRef ? target.level : 0 };
      let job = await api.createJob(project, { type: 'fit_in_map', params, inputs,
        title: targetRef ? `${item.label} placed in ${target.label}` : `${item.label} moved in the viewer` });
      job = await api.queueJob(project, job.uid);
      P.job = job.uid;
      ctl.log(`Position of #${item.id} saved as job ${job.uid}: it writes the ${isModel ? 'placed model' : `placed ${item.otype === 'mask' ? 'mask' : 'map'}`}`);
      toast(`Job ${job.uid} queued`, 'success', 4000);
    } catch (err) {
      setStatus(`Not saved: ${err.message || err}`, true);
    } finally {
      P.saving = false;
      render();
    }
  }

  // ------------------------------------------------------------ panel
  let statusEl = null;
  let positionEl = null;
  function setStatus(text, bad = false) {
    P.error = bad ? text : '';
    if (statusEl) {
      statusEl.textContent = text || '';
      statusEl.classList.toggle('bad', !!bad);
    }
  }

  function renderPosition() {
    if (!positionEl || !P.item) return;
    positionEl.textContent = moved(P.item);
  }

  const seg = (options, current, onPick, labelText) => h('div', { class: 'seg', role: 'group', 'aria-label': labelText },
    options.map(([v, text, tip]) => h('button', { type: 'button', class: v === current ? 'on' : '', title: tip || text, 'aria-pressed': String(v === current),
      onclick: () => onPick(v) }, text)));

  function numberRow(text, value, unit, onChange, opts = {}) {
    return h('div', { class: 'vw-row' }, h('span', { class: 'lbl' }, text),
      h('input', { type: 'number', value, min: opts.min ?? 0, max: opts.max, step: opts.step ?? 1, 'aria-label': text,
        onchange: (e) => { const v = Number(e.target.value); if (Number.isFinite(v)) onChange(v); } }),
      unit ? h('span', { class: 'unit' }, unit) : null);
  }

  function resultBlock() {
    const r = P.result;
    if (!r || r.item !== P.item) return null;
    const s = r.stats;
    const b = r.before;
    const lines = [];
    if (s.correlation !== undefined) lines.push(`correlation ${b.correlation.toFixed(3)} → ${s.correlation.toFixed(3)} · cam ${s.cam.toFixed(3)}`);
    else lines.push(`average map value ${b.average_map_value.toPrecision(3)} → ${s.average_map_value.toPrecision(3)}`);
    if (s.steps !== undefined) lines.push(`${s.steps} steps · moved ${s.shift.toFixed(2)} Å, ${s.angle.toFixed(2)}°`);
    const atoms = r.item.kind === 'model' && !r.resolution; // as ChimeraX, only atoms are counted outside the contour
    if (atoms && s.outside_contour !== undefined) lines.push(`${(100 * (1 - s.inside_fraction)).toFixed(1)} % of the atoms outside the contour of #${r.target.id}`);
    if (r.polish) {
      const a = r.polish.stats;
      lines.push(`then the atoms: average map value ${r.polish.before.average_map_value.toPrecision(3)} → ${a.average_map_value.toPrecision(3)} · moved ${a.shift.toFixed(2)} Å, ${a.angle.toFixed(2)}°`);
      if (a.outside_contour !== undefined) lines.push(`${(100 * (1 - a.inside_fraction)).toFixed(1)} % of the atoms outside the contour of #${r.target.id}`);
    }
    const inside = r.polish ? r.polish.stats.inside_fraction : atoms ? s.inside_fraction : undefined;
    if (inside !== undefined && inside < 0.5 && !(r.fits || []).length) {
      // a local fit only climbs to the nearest maximum: the simulated map reaches further than the atoms
      lines.push(r.item.kind === 'model' && !r.resolution
        ? 'Not in the density yet: fit with a resolution of 6–8 Å (its simulated map reaches further), then again at 0 — or move it closer, or Search.'
        : 'Not in the density yet: move it closer by hand, or Search.');
    }
    const fits = (r.fits || []).length > 1 ? h('div', { class: 'vw-pl-fits' }, r.fits.map((f, i) => h('button', { type: 'button', class: 'vw-pl-fit',
      title: 'Place it at this fit', onclick: () => applyFit(f).catch((err) => ctl.fail(err)) },
    h('span', { class: 'n' }, String(i + 1)), h('span', {}, `${f.score.toFixed(4)} · found ${f.hits}× · ${f.shift.toFixed(1)} Å, ${f.angle.toFixed(0)}°`)))) : null;
    return h('div', { class: 'vw-pl-result' }, lines.map((l) => h('div', {}, l)), fits);
  }

  function render() {
    root.hidden = !P.open;
    if (!P.open) return;
    const items = movable();
    const item = P.item;
    const head = h('div', { class: 'vw-mk-head' }, icon('move'), h('b', {}, 'Move & fit'), h('span', { class: 'grow' }),
      h('button', { type: 'button', class: 'icon-btn', title: 'Close (the positions stay, and in the link)', 'aria-label': 'Close move and fit', onclick: () => close() }, icon('close')));
    const itemRow = h('div', { class: 'vw-row' }, h('span', { class: 'lbl' }, 'Item'),
      h('select', { 'aria-label': 'Item to move', onchange: (e) => setItem(items.find((i) => i.id === Number(e.target.value))) },
        items.map((i) => h('option', { value: i.id, selected: i === item }, `${label(i)}${i.otype === 'mask' ? ' (mask)' : ''}`))));
    positionEl = h('span', { class: 'vw-static grow' });
    const position = h('div', { class: 'vw-row' }, h('span', { class: 'lbl' }, 'Position'), positionEl,
      h('button', { type: 'button', class: 'btn small', disabled: !item?.transform, title: 'Back to where its file puts it',
        onclick: () => item && place(item, null, `#${item.id} back to where its file puts it`).catch((err) => ctl.fail(err)) }, icon('undo'), 'Reset'));
    const mouse = h('div', { class: 'vw-mk-actions' },
      h('button', { type: 'button', class: `btn small vw-pl-mouse${P.mouse ? ' on' : ''}`, disabled: !item, 'aria-pressed': String(P.mouse),
        title: 'Mouse drags move the item instead of the camera (Esc: camera again)', onclick: () => setMouse(!P.mouse) },
      icon('move'), P.mouse ? 'Moving with the mouse' : 'Move with the mouse'),
      h('button', { type: 'button', class: 'btn small', disabled: !P.history.length, title: 'Undo the last move (Ctrl + Z)', onclick: () => undo().catch((err) => ctl.fail(err)) }, 'Undo'));
    const hint = h('div', { class: 'vw-meta' }, P.mouse
      ? 'Left-drag: rotate about its centre · right-drag (or Ctrl + drag): move · Shift + right-drag: along the view · click an item in the view to move it instead.'
      : 'Commands: move x 5 #2 · turn y 30 #2 · fitmap #2 inMap #1 · view initial #2.');
    const tgts = targets();
    if (P.target && !tgts.includes(P.target)) P.target = null;
    if (!P.target && tgts.length) P.target = tgts[0];
    const isModel = item?.kind === 'model';
    const fitRows = tgts.length && item ? [
      h('div', { class: 'vw-row' }, h('span', { class: 'lbl' }, 'In map'),
        h('select', { 'aria-label': 'Map to fit into', onchange: (e) => { P.target = tgts.find((i) => i.id === Number(e.target.value)); render(); } },
          tgts.map((i) => h('option', { value: i.id, selected: i === P.target }, label(i))))),
      isModel ? numberRow('Resolution', P.resolution, 'Å', (v) => { P.resolution = Math.max(0, Math.min(60, v)); render(); }, { step: 0.5, max: 60 }) : null,
      isModel && !P.resolution ? h('div', { class: 'vw-meta' }, 'Resolution 0: the atoms are fitted (average map value at the atoms, ChimeraX’s default). A resolution (6–8 Å) fits the model’s simulated map, which reaches further.') : null,
      isModel && P.resolution ? h('div', { class: 'vw-row' }, h('span', { class: 'lbl' }, ''),
        h('label', { class: 'chk' }, h('input', { type: 'checkbox', checked: P.polish, onchange: (e) => { P.polish = e.target.checked; } }),
          'then fit the atoms (exact position)')) : null,
      !isModel || P.resolution ? h('div', { class: 'vw-row' }, h('span', { class: 'lbl' }, 'Metric'), seg(METRICS, P.metric, (v) => { P.metric = v; render(); }, 'Fitting metric')) : null,
      isModel ? h('div', { class: 'vw-row' }, h('span', { class: 'lbl' }, 'Chains'),
        h('input', { type: 'text', value: P.chains, placeholder: 'A, B:10-250 (empty: all; the whole model moves)', 'aria-label': 'Chains fitted',
          oninput: (e) => { P.chains = e.target.value; } })) : null,
      numberRow('Search', P.search, 'random starts', (v) => { P.search = Math.max(0, Math.min(100, Math.round(v))); render(); }, { max: 100 }),
      P.search ? numberRow('Radius', P.radius, 'Å (0: whole map)', (v) => { P.radius = Math.max(0, v); }, { step: 1 }) : null,
      h('div', { class: 'vw-mk-actions' },
        h('button', { type: 'button', class: 'btn small primary vw-pl-fitbtn', disabled: P.busy, onclick: () => fit().catch((err) => setStatus(`Fit: ${err.message || err}`, true)) },
          icon('target'), P.busy ? 'Fitting…' : P.search ? `Search (${P.search})` : 'Fit')),
      resultBlock(),
    ] : [h('div', { class: 'vw-meta' }, item ? 'Open the map to fit into (Open…) to fit this item.' : 'Open a map or a model first.')];
    statusEl = h('div', { class: `vw-mk-status${P.error ? ' bad' : ''}`, 'aria-live': 'polite' }, P.error || '');
    const jobLink = P.job ? h('span', { class: 'vw-meta' }, 'Job ', h('a', { href: `./#/p/${encodeURIComponent(ctl.project())}/${P.job}`, target: '_blank', rel: 'noopener' }, `${P.job} ↗`)) : null;
    const saveRow = h('div', { class: 'vw-mk-save' },
      h('button', { type: 'button', class: 'btn primary', disabled: !item || P.saving,
        title: 'Queue a "Fit in map" job writing the item at this position (on the grid of the target map for a map or a mask)', onclick: () => save() },
      icon('check'), P.saving ? 'Saving…' : 'Save as job'), jobLink);
    clear(root, head, h('div', { class: 'vw-mk-body' }, itemRow, position, mouse, hint,
      h('div', { class: 'vw-mk-sub' }, 'Fit in map (rigid body)'), ...fitRows, statusEl, saveRow));
    renderPosition();
  }

  // ------------------------------------------------------- open/close
  function setItem(item) {
    if (!item || item.series) return;
    P.item = item;
    if (P.target === item) P.target = null;
    if (P.mouse) setMouse(true); // the hint names the new item
    else render();
  }

  function open(item) {
    // the item asked for (a card's menu, a model made active), otherwise a model: what is usually fitted into a map
    const pickFrom = movable();
    const chosen = item && !item.series ? item : pickFrom.find((i) => i.kind === 'model') || pickFrom[0];
    if (!chosen) { toast('Open a map or a model first', 'error'); return false; }
    P.open = true;
    P.item = chosen;
    setMouse(true);
    ctl.syncHash?.();
    return true;
  }

  function close() {
    drag.on = false;
    P.open = false;
    setMouse(false);
    render();
  }

  function itemClosed(item) {
    P.history = P.history.filter((hst) => hst.item !== item);
    if (P.result?.item === item || P.result?.target === item) P.result = null;
    if (P.target === item) P.target = null;
    if (P.item === item) {
      const other = movable()[0];
      if (other) setItem(other);
      else close();
    } else render();
  }

  function onKey(e) {
    if (!P.open) return false;
    if (e.key === 'Escape' && P.mouse) { setMouse(false); return true; }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z' && !e.shiftKey && P.history.length) { undo().catch((err) => ctl.fail(err)); return true; }
    return false;
  }

  const el = ctl.canvasElement();
  el.addEventListener('pointerdown', pointerDown);
  window.addEventListener('pointermove', pointerMove);
  window.addEventListener('pointerup', pointerUp);
  window.addEventListener('pointercancel', pointerUp);

  return {
    open, close, toggle: (item) => (P.open ? close() : open(item)), isOpen: () => P.open, refresh: () => render(),
    pick, onKey, itemClosed, moveBy, turnBy, reset, fit, isMoving: () => drag.on,
  };
}
