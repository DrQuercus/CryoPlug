// Mask editor of the 3D viewer: a mask drawn on a map with the mouse, the way Chimera users make them — spheres,
// boxes and cylinders placed on the map, a connected region of density picked at the contour level, a zone around
// chains, the density above the level — each added, erased or intersected in turn. The server computes the mask on
// a coarse copy of the map while drawing (shown as an orange surface); "Save as job" queues a "Mask drawn in the 3D
// viewer" job that computes it on the full grid, with its dilation, soft edge, centre and box for re-centred particles.
import { api } from '../api.js';
import { clear, h, icon, toast } from '../ui.js';
import * as scene from './scene.js';

const TOOLS = [
  ['sphere', 'Sphere', 'Click on the map: a sphere is centred where you click'],
  ['box', 'Box', 'Click on the map: a box (cube) is centred where you click'],
  ['cylinder', 'Cylinder', 'Click one end on the map, then the other'],
  ['region', 'Region', 'Click on a blob of density: the connected density above the level is taken (as Chimera’s region pick)'],
  ['zone', 'Zone', 'Density near the atoms of chains of an open model'],
  ['map', 'Density', 'All the density above the level (then erase what is not wanted)'],
];
const MODES = [['add', 'Add'], ['erase', 'Erase'], ['keep', 'Keep only']];
const PREVIEW_COLOR = 0xff8a1f;
const PREVIEW_BOX = 128;
const fmt = (v, d = 1) => Number(v).toFixed(d);
const fmtLevel = (v) => (Math.abs(v) >= 100 || (Math.abs(v) < 0.01 && v !== 0) ? Number(v).toExponential(2) : Number(v).toPrecision(4));

export function createMaskEditor(root, ctl) {
  const M = {
    open: false, base: null, tool: 'sphere', mode: 'add', radius: 10, side: 30, level: null, lowpass: 0,
    zoneModel: null, zoneSelection: '', zoneRadius: 4, ops: [], undone: [], selected: -1, pending: null,
    dilate: 2, soft: 6, show: true, opacity: 0.4, job: null, saving: false, error: '',
  };
  const preview = { item: null, token: 0, timer: 0, busy: false };

  // -------------------------------------------------------------- helpers
  const maps = () => ctl.items().filter((i) => i.kind === 'map' && !i.series);
  const models = () => ctl.items().filter((i) => i.kind === 'model');
  const levelOf = () => (Number.isFinite(M.level) ? M.level : M.base?.level ?? 0);
  const vec = (p) => [p[0], p[1], p[2]].map((v) => Math.round(v * 100) / 100);
  const toolInfo = () => TOOLS.find((t) => t[0] === M.tool);

  function describe(op) {
    const verb = { add: 'Add', erase: 'Erase', keep: 'Keep' }[op.mode];
    switch (op.shape) {
      case 'sphere': return `${verb} sphere · r ${fmt(op.radius)} Å`;
      case 'box': return `${verb} box · ${fmt(op.size[0], 0)} Å`;
      case 'cylinder': {
        const len = Math.hypot(...op.end.map((v, k) => v - op.start[k]));
        return `${verb} cylinder · r ${fmt(op.radius)} Å · ${fmt(len, 0)} Å long`;
      }
      case 'region': return `${verb} region · level ${fmtLevel(op.level)}${op.lowpass ? ` · ${op.lowpass} Å` : ''}`;
      case 'map': return `${verb} density · level ${fmtLevel(op.level)}${op.lowpass ? ` · ${op.lowpass} Å` : ''}`;
      case 'zone': return `${verb} zone · ${op.radius} Å of ${op.selection || 'all atoms'}`;
      case 'mask': return `${verb} mask ${op.path.split('/').pop()}`;
      default: return op.shape;
    }
  }

  function push(op) {
    M.ops.push(op);
    M.undone = [];
    M.selected = M.ops.length - 1;
    changed();
  }

  function changed({ redraw = true } = {}) {
    if (redraw) render();
    schedulePreview();
    ctl.syncHash?.();
  }

  // -------------------------------------------------------------- preview
  function schedulePreview() {
    clearTimeout(preview.timer);
    preview.timer = setTimeout(runPreview, 220);
  }

  async function runPreview() {
    const token = ++preview.token;
    if (!M.open || !M.base || !M.ops.length || !M.ops.some((o) => o.mode === 'add')) {
      await dropPreview();
      setStatus(M.ops.length ? 'Add a shape or a region first: erasing needs something to erase from' : '');
      return;
    }
    setStatus('Computing the preview…');
    let r;
    try {
      const bytes = await api.maskPreview(ctl.project(), { map: M.base.path, ops: M.ops, dilate: M.dilate, max_box: PREVIEW_BOX });
      if (token !== preview.token) return;
      r = await scene.loadVolume(bytes, 'mask preview');
    } catch (err) {
      if (token === preview.token) setStatus(`Preview: ${err.message || err}`, true);
      return;
    }
    if (token !== preview.token || !M.open) { await scene.remove(r.dataRef); return; }
    const old = preview.item;
    preview.item = { kind: 'map', label: 'mask preview', volume: r.volume, refs: { data: r.dataRef, volume: r.volumeRef, repr: null },
      level: 0.5, style: 'transparent', opacity: M.opacity, color: PREVIEW_COLOR, visible: M.show };
    try {
      await scene.showMap(preview.item);
    } finally {
      if (old) await scene.remove(old.refs.data);
    }
    const voxels = countAbove(r.volume, 0.5);
    const vox = scene.voxelAxes(r.volume).scale.reduce((a, b) => a * Math.abs(b), 1);
    setStatus(voxels ? `Preview on a coarse copy: about ${fmtVolume(voxels * vox)} (dilation included, soft edge of ${M.soft} Å added by the job)`
      : 'The mask is empty (shapes outside the map, or everything erased)');
  }

  async function dropPreview() {
    const old = preview.item;
    preview.item = null;
    if (old) await scene.remove(old.refs.data);
  }

  function countAbove(volume, level) {
    const data = volume.grid.cells.data;
    let n = 0;
    for (let i = 0; i < data.length; i++) if (data[i] >= level) n++;
    return n;
  }

  // protein-equivalent mass (1.21 Å³ per Da) and volume
  function fmtVolume(a3) {
    const kda = a3 / 1.21 / 1000;
    return `${Math.round(a3 / 1000).toLocaleString()} nm³ (${kda >= 1000 ? `${(kda / 1000).toFixed(2)} MDa` : `${Math.round(kda)} kDa`} of protein)`;
  }

  function setPreviewLook() {
    if (!preview.item) return;
    preview.item.opacity = M.opacity;
    preview.item.visible = M.show;
    scene.setHidden(preview.item.refs.repr, !M.show);
    if (M.show) scene.showMap(preview.item).catch(() => {});
  }

  // --------------------------------------------------------------- clicks
  // A click in the 3D view while the editor is open: places the current shape. Returns true when used.
  function click(d, e) {
    if (!M.open || !M.base) return false;
    if (e.button !== undefined && e.button !== 0 && e.button !== 1) return false; // Mol*: 1 = primary button
    if (!['sphere', 'box', 'cylinder', 'region'].includes(M.tool)) return false;
    const p = e.position;
    if (!p) { setStatus('Click on the map surface (or an atom): nothing was under the mouse', true); return true; }
    place(vec(p));
    return true;
  }

  function place(point) {
    const mode = M.mode;
    if (M.tool === 'sphere') push({ shape: 'sphere', mode, center: point, radius: M.radius });
    else if (M.tool === 'box') push({ shape: 'box', mode, center: point, size: [M.side, M.side, M.side] });
    else if (M.tool === 'region') push({ shape: 'region', mode, seed: point, level: levelOf(), lowpass: M.lowpass });
    else if (M.tool === 'cylinder') {
      if (!M.pending) {
        M.pending = point;
        render();
        setStatus('Cylinder: now click the other end');
        return;
      }
      const start = M.pending;
      M.pending = null;
      if (Math.hypot(...point.map((v, k) => v - start[k])) < 0.5) { setStatus('The two ends are the same point', true); render(); return; }
      push({ shape: 'cylinder', mode, start, end: point, radius: M.radius });
    }
  }

  function placeAtCentre() {
    const target = scene.cameraState().target;
    place(vec(target));
  }

  function addWhole() {
    if (M.tool === 'map') push({ shape: 'map', mode: M.mode, level: levelOf(), lowpass: M.lowpass });
    else if (M.tool === 'zone') {
      const model = models().find((m) => m.id === M.zoneModel) || models()[0];
      if (!model) { setStatus('Open a model fitted in the map first (Open…)', true); return; }
      push({ shape: 'zone', mode: M.mode, model: model.path, selection: M.zoneSelection.trim(), radius: M.zoneRadius });
    }
  }

  // ------------------------------------------------------------ editing
  function undo() {
    if (M.pending) { M.pending = null; render(); return; }
    if (!M.ops.length) return;
    M.undone.push(M.ops.pop());
    M.selected = Math.min(M.selected, M.ops.length - 1);
    changed();
  }

  function redo() {
    if (!M.undone.length) return;
    M.ops.push(M.undone.pop());
    M.selected = M.ops.length - 1;
    changed();
  }

  function removeOp(i) {
    M.ops.splice(i, 1);
    M.selected = Math.min(M.selected, M.ops.length - 1);
    changed();
  }

  // the size controls change the selected operation as well (a sphere just placed, a region's level...)
  function editSelected(fn) {
    const op = M.ops[M.selected];
    if (!op || !fn(op)) return;
    changed({ redraw: false });
    const row = root.querySelector(`.vw-mk-op[data-i="${M.selected}"] .txt`);
    if (row) row.textContent = describe(op);
  }

  // --------------------------------------------------------------- saving
  async function outputRef(path, types) {
    const jobs = await api.jobs(ctl.project());
    for (const job of jobs) {
      for (const o of job.outputs || []) {
        if (o.path === path && types.includes(o.type)) return { job: job.uid, output: o.name };
      }
    }
    return null;
  }

  async function save() {
    if (M.saving) return;
    if (!M.ops.some((o) => o.mode === 'add')) { setStatus('Nothing to save: add a shape or a region', true); return; }
    M.saving = true;
    render();
    try {
      const project = ctl.project();
      const mapRef = await outputRef(M.base.path, ['map', 'mask']);
      if (!mapRef) throw new Error(`${M.base.label} is not a map output of this project (a half map?): draw on a full map`);
      const inputs = { map: mapRef };
      const zone = M.ops.find((o) => o.shape === 'zone');
      if (zone) {
        const modelRef = await outputRef(zone.model, ['model']);
        if (modelRef) inputs.model = modelRef;
      }
      const params = { recipe: JSON.stringify({ version: 1, ops: M.ops }), dilate: M.dilate, soft: M.soft };
      let job = null;
      if (M.job?.uid) {
        try { job = await api.job(project, M.job.uid); } catch { job = null; }
        if (job && job.type !== 'draw_mask') job = null;
        if (job && ['failed', 'killed'].includes(job.status)) job = await api.clearJob(project, job.uid);
        if (job && job.status !== 'building') job = null; // already ran: the drawing becomes a new job
      }
      if (job) job = await api.updateJob(project, job.uid, { params, inputs });
      else job = await api.createJob(project, { type: 'draw_mask', params, inputs, title: `Mask drawn on ${mapRef.job}` });
      job = await api.queueJob(project, job.uid);
      M.job = { uid: job.uid, status: job.status };
      ctl.log?.(`Mask saved as job ${job.uid} (queued): it is computed on the full grid of ${M.base.label}`);
      toast(`Mask job ${job.uid} queued`, 'success', 4000);
      ctl.syncHash?.();
    } catch (err) {
      setStatus(`Not saved: ${err.message || err}`, true);
      toast(`Mask not saved: ${err.message || err}`, 'error', 7000);
    } finally {
      M.saving = false;
      render();
    }
  }

  // ---------------------------------------------------------------- panel
  let statusEl = null;
  function setStatus(text, bad = false) {
    M.error = bad ? text : '';
    if (!statusEl) return;
    statusEl.textContent = text || '';
    statusEl.classList.toggle('bad', !!bad);
  }

  const seg = (options, current, onPick, label) => h('div', { class: 'seg', role: 'group', 'aria-label': label },
    options.map(([v, text, tip]) => h('button', { type: 'button', class: v === current ? 'on' : '', title: tip || text, 'aria-pressed': String(v === current),
      onclick: () => onPick(v) }, text)));

  function slider(label, value, min, max, step, unit, onInput) {
    const num = h('span', { class: 'vw-static' }, `${value} ${unit}`);
    return h('div', { class: 'vw-row' }, h('span', { class: 'lbl' }, label),
      h('input', { type: 'range', min, max, step, value, 'aria-label': label,
        oninput: (e) => { const v = Number(e.target.value); num.textContent = `${v} ${unit}`; onInput(v); } }), num);
  }

  function render() {
    root.hidden = !M.open;
    if (!M.open) return;
    const base = M.base;
    const tool = toolInfo();
    const head = h('div', { class: 'vw-mk-head' }, icon('edit'), h('b', {}, 'Mask editor'), h('span', { class: 'grow' }),
      h('button', { type: 'button', class: 'icon-btn', title: 'Close the mask editor (the drawing stays in the link)', 'aria-label': 'Close the mask editor',
        onclick: () => close() }, icon('close')));
    const baseRow = h('div', { class: 'vw-row' }, h('span', { class: 'lbl' }, 'On'),
      h('select', { 'aria-label': 'Map the mask is drawn on', onchange: (e) => setBase(maps().find((m) => m.id === Number(e.target.value))) },
        maps().map((m) => h('option', { value: m.id, selected: m === base }, `#${m.id} ${m.label}`))));
    const tools = h('div', { class: 'vw-mk-tools' }, seg(TOOLS.map(([v, t, tip]) => [v, t, tip]), M.tool, (v) => { M.tool = v; M.pending = null; render(); }, 'Tool'));
    const modes = h('div', { class: 'vw-row' }, h('span', { class: 'lbl' }, 'Mode'),
      seg(MODES, M.mode, (v) => { M.mode = v; render(); }, 'What the next shape does'));
    const controls = [];
    if (M.tool === 'sphere' || M.tool === 'cylinder') {
      controls.push(slider('Radius', M.radius, 1, 80, 0.5, 'Å', (v) => {
        M.radius = v;
        editSelected((op) => ((op.shape === 'sphere' || op.shape === 'cylinder') ? ((op.radius = v), true) : false));
      }));
    }
    if (M.tool === 'box') {
      controls.push(slider('Side', M.side, 2, 200, 1, 'Å', (v) => {
        M.side = v;
        editSelected((op) => (op.shape === 'box' ? ((op.size = [v, v, v]), true) : false));
      }));
    }
    if (M.tool === 'region' || M.tool === 'map') {
      const levelInput = h('input', { type: 'number', step: 'any', value: fmtLevel(levelOf()), 'aria-label': 'Density level',
        onchange: (e) => {
          const v = Number(e.target.value);
          if (!Number.isFinite(v)) return;
          M.level = v;
          editSelected((op) => ((op.shape === 'region' || op.shape === 'map') ? ((op.level = v), true) : false));
        } });
      controls.push(h('div', { class: 'vw-row' }, h('span', { class: 'lbl' }, 'Level'), levelInput,
        h('button', { type: 'button', class: 'btn small', title: 'Take the contour level of the map shown', onclick: () => { M.level = null; render(); } }, 'Contour')));
      controls.push(slider('Low-pass', M.lowpass, 0, 30, 1, 'Å', (v) => {
        M.lowpass = v;
        editSelected((op) => ((op.shape === 'region' || op.shape === 'map') ? ((op.lowpass = v), true) : false));
      }));
    }
    if (M.tool === 'zone') {
      const ms = models();
      controls.push(ms.length
        ? h('div', {},
          h('div', { class: 'vw-row' }, h('span', { class: 'lbl' }, 'Model'),
            h('select', { onchange: (e) => { M.zoneModel = Number(e.target.value); } },
              ms.map((m) => h('option', { value: m.id, selected: m.id === M.zoneModel }, `#${m.id} ${m.label}`)))),
          h('div', { class: 'vw-row' }, h('span', { class: 'lbl' }, 'Chains'),
            h('input', { type: 'text', value: M.zoneSelection, placeholder: 'A, B, C:10-250 (empty: all)', 'aria-label': 'Chains and residues',
              oninput: (e) => { M.zoneSelection = e.target.value; } })),
          slider('Distance', M.zoneRadius, 1, 15, 0.5, 'Å', (v) => {
            M.zoneRadius = v;
            editSelected((op) => (op.shape === 'zone' ? ((op.radius = v), true) : false));
          }))
        : h('div', { class: 'vw-meta' }, 'Open a model fitted in this map (Open…) to draw zones around its chains.'));
    }
    const actions = h('div', { class: 'vw-mk-actions' });
    if (M.tool === 'map' || M.tool === 'zone') {
      actions.append(h('button', { type: 'button', class: 'btn small primary', onclick: () => addWhole() }, icon('plus'),
        `${MODES.find((m) => m[0] === M.mode)[1]} ${M.tool === 'map' ? 'density' : 'zone'}`));
    } else {
      actions.append(h('button', { type: 'button', class: 'btn small', title: 'Place the shape at the centre of rotation (click an atom to centre on it first)',
        onclick: () => placeAtCentre() }, icon('target'), M.pending ? 'Other end at the centre' : 'At the centre of the view'));
    }
    const hint = h('div', { class: 'vw-meta' }, M.pending ? 'Cylinder: click the other end (Esc or Undo to cancel).' : tool[2]);
    const opsList = h('div', { class: 'vw-mk-ops' }, M.ops.length
      ? M.ops.map((op, i) => h('div', { class: `vw-mk-op ${op.mode}${i === M.selected ? ' sel' : ''}`, dataset: { i: String(i) },
        onclick: () => { M.selected = i; render(); } },
      h('span', { class: 'n' }, String(i + 1)), h('span', { class: 'txt' }, describe(op)),
      h('button', { type: 'button', class: 'icon-btn', title: 'Remove', 'aria-label': `Remove operation ${i + 1}`,
        onclick: (e) => { e.stopPropagation(); removeOp(i); } }, icon('x'))))
      : h('div', { class: 'vw-empty' }, 'No operation yet'));
    const edit = h('div', { class: 'vw-mk-actions' },
      h('button', { type: 'button', class: 'btn small', disabled: !M.ops.length && !M.pending, title: 'Undo (Ctrl+Z)', onclick: () => undo() }, icon('undo'), 'Undo'),
      h('button', { type: 'button', class: 'btn small', disabled: !M.undone.length, title: 'Redo (Ctrl+Y)', onclick: () => redo() }, 'Redo'),
      h('button', { type: 'button', class: 'btn small', disabled: !M.ops.length, onclick: () => { if (confirm('Remove every operation?')) { M.ops = []; M.undone = []; M.selected = -1; changed(); } } },
        icon('trash'), 'Clear'));
    const edge = [
      slider('Dilation', M.dilate, 0, 20, 0.5, 'Å', (v) => { M.dilate = v; changed({ redraw: false }); }),
      slider('Soft edge', M.soft, 0, 30, 0.5, 'Å', (v) => { M.soft = v; ctl.syncHash?.(); }),
      h('div', { class: 'vw-row' }, h('span', { class: 'lbl' }, 'Preview'),
        h('label', { class: 'chk' }, h('input', { type: 'checkbox', checked: M.show, onchange: (e) => { M.show = e.target.checked; setPreviewLook(); } }), 'show'),
        h('input', { type: 'range', min: 0.1, max: 0.9, step: 0.05, value: M.opacity, 'aria-label': 'Opacity of the preview',
          oninput: (e) => { M.opacity = Number(e.target.value); setPreviewLook(); } })),
    ];
    statusEl = h('div', { class: `vw-mk-status${M.error ? ' bad' : ''}`, 'aria-live': 'polite' }, M.error || '');
    const jobLink = M.job ? h('a', { href: `./#/p/${encodeURIComponent(ctl.project())}/${M.job.uid}`, target: '_blank', rel: 'noopener' }, `${M.job.uid} ↗`) : null;
    const saveRow = h('div', { class: 'vw-mk-save' },
      h('button', { type: 'button', class: 'btn primary', disabled: M.saving || !M.ops.length, onclick: () => save(),
        title: 'Queue a job computing this mask on the full grid of the map (dilation and soft edge included)' },
      icon('check'), M.saving ? 'Saving…' : M.job ? 'Save again' : 'Save as job'),
      jobLink ? h('span', { class: 'vw-meta' }, 'Job ', jobLink) : null);
    clear(root, head, h('div', { class: 'vw-mk-body' }, baseRow, tools, modes, ...controls, actions, hint,
      h('div', { class: 'vw-mk-sub' }, `Operations (${M.ops.length})`), opsList, edit,
      h('div', { class: 'vw-mk-sub' }, 'Edge and display'), ...edge, statusEl, saveRow));
  }

  // ----------------------------------------------------------- open/close
  function setBase(item) {
    if (!item || item === M.base) return;
    M.base = item;
    M.level = null;
    render();
    schedulePreview();
    ctl.syncHash?.();
  }

  function open(spec = {}) {
    const base = (spec.base && maps().find((m) => m.path === spec.base)) || ctl.activeMap();
    if (!base || base.series) { toast('Open a map first: the mask is drawn on it', 'error'); return false; }
    M.base = base;
    if (Array.isArray(spec.ops)) M.ops = spec.ops.filter((o) => o && typeof o === 'object');
    if (Number.isFinite(spec.dilate)) M.dilate = spec.dilate;
    if (Number.isFinite(spec.soft)) M.soft = spec.soft;
    if (spec.job) M.job = { uid: String(spec.job) };
    if (!M.zoneModel && models()[0]) M.zoneModel = models()[0].id;
    M.open = true;
    render();
    schedulePreview();
    ctl.syncHash?.();
    ctl.log?.(`Mask editor on #${base.id} ${base.label}: pick a tool, click on the map; the orange surface is the mask`);
    return true;
  }

  function close() {
    M.open = false;
    M.pending = null;
    preview.token++;
    dropPreview();
    render();
    ctl.syncHash?.();
  }

  // the base map was closed: draw on another one, or stop
  function itemClosed(item) {
    if (!M.open || item !== M.base) return;
    const other = maps().find((m) => m !== item);
    if (other) setBase(other);
    else close();
  }

  function onKey(e) {
    if (!M.open) return false;
    const k = e.key.toLowerCase();
    if ((e.ctrlKey || e.metaKey) && k === 'z' && !e.shiftKey) { undo(); return true; }
    if ((e.ctrlKey || e.metaKey) && (k === 'y' || (k === 'z' && e.shiftKey))) { redo(); return true; }
    if (e.key === 'Escape' && M.pending) { M.pending = null; render(); return true; }
    if ((e.key === 'Delete' || e.key === 'Backspace') && M.selected >= 0 && !e.target.closest('input, textarea, select')) { removeOp(M.selected); return true; }
    return false;
  }

  return {
    open,
    close,
    toggle: () => (M.open ? close() : open()),
    isOpen: () => M.open,
    click,
    onKey,
    itemClosed,
    state: () => (M.open ? { base: M.base?.path, ops: M.ops, dilate: M.dilate, soft: M.soft, ...(M.job ? { job: M.job.uid } : {}) } : null),
  };
}
