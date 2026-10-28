// Job builder (right panel): pick a job type, connect inputs (select or drag & drop outputs), set parameters, queue.
import { api } from './api.js';
import { computeSection, cpuParams, initCompute, requestedBody } from './compute.js';
import { browseFiles } from './filebrowser.js';
import { CATEGORY_FR, helpDetails, helpModal } from './help.js';
import { compatibleOutputs, navigate, refreshJobs, restricted, state, typeTitle } from './state.js';
import { btn, clear, guard, h, icon, popupMenu, toast } from './ui.js';
import { draggingFiles, filesFromDrop, pickFiles, uploadForField } from './upload.js';

const panel = () => document.getElementById('panel');

// params: parameter values to start from (e.g. the clusters picked in a latent space explorer).
export function openBuilder({ type = null, editing = null, prefillFrom = null, params = null } = {}) {
  const b = { type, editing, inputs: {}, params: {}, title: '', lane: '', showAdvanced: false, search: '', prefillFrom };
  if (editing) {
    b.type = editing.type;
    b.inputs = JSON.parse(JSON.stringify(editing.inputs || {}));
    b.params = { ...(editing.params || {}) };
    b.title = editing.title === typeTitle(editing.type) ? '' : editing.title;
    b.lane = editing.lane || '';
  }
  initCompute(b, editing);
  state.builder = b;
  if (b.type && !editing) applyType(b.type);
  if (params && b.type) setKnownParams(b, params);
  panel().scrollTop = 0;
  renderBuilder();
}

function setKnownParams(b, params) {
  const known = new Set(state.types[b.type].params.map((p) => p.name));
  for (const [name, value] of Object.entries(params)) if (known.has(name)) b.params[name] = value;
}

// Parameter values set from elsewhere while the builder already shows that job type.
export function builderSetParams(params) {
  const b = state.builder;
  if (!b?.type || !params) return;
  setKnownParams(b, params);
  renderBuilder();
}

export function closeBuilder() {
  state.builder = null;
}

// The project page shows what can be dragged into the builder (output chips, tree cards) once it has a job type.
const typeChanged = () => document.dispatchEvent(new Event('cryoplug-builder-type'));

function applyType(name) {
  const b = state.builder;
  const t = state.types[name];
  b.type = name;
  b.params = Object.fromEntries(t.params.map((p) => [p.name, p.default]));
  b.inputs = {};
  if (b.prefillFrom) prefill(b, t, b.prefillFrom);
  typeChanged();
}

function prefill(b, t, job) {
  const outs = job.status === 'completed' ? job.outputs : (state.types[job.type]?.outputs || []);
  for (const slot of t.inputs) {
    const cands = outs.filter((o) => slot.types.includes(o.type));
    if (cands.length) {
      const rank = (o) => {
        const i = (slot.prefer || []).indexOf(o.name);
        if (i >= 0) return i;
        return o.name === slot.name ? 50 : 100;
      };
      cands.sort((a, c) => rank(a) - rank(c));
      b.inputs[slot.name] = { job: job.uid, output: cands[0].name };
      continue;
    }
    // reuse what the source job itself consumed (e.g. the map ModelAngelo was built into)
    for (const ref of Object.values(job.inputs || {})) {
      const parent = state.jobsByUid[ref.job];
      const o = parent && (parent.outputs || []).find((x) => x.name === ref.output);
      if (o && slot.types.includes(o.type) && !Object.values(b.inputs).some((r) => r.job === ref.job && r.output === ref.output)) {
        b.inputs[slot.name] = { ...ref };
        break;
      }
    }
  }
}

export function builderDrop(slotName, ref) {
  const b = state.builder;
  if (!b || !b.type) return;
  const slot = state.types[b.type].inputs.find((s) => s.name === slotName);
  if (!slot.types.includes(ref.type)) {
    toast(`'${slot.label}' expects ${slot.types.join(' / ')}, not ${ref.type}`, 'error');
    return;
  }
  b.inputs[slotName] = { job: ref.job, output: ref.output };
  renderInputs(slotName);
}

// A whole job dropped on an input (from the tree view): its output that fits the input, the preferred one first
// (declared outputs while the job has not run yet).
export function builderDropJob(slotName, uid) {
  const b = state.builder;
  const job = state.jobsByUid[uid];
  if (!b || !b.type || !job) return;
  const slot = state.types[b.type].inputs.find((s) => s.name === slotName);
  if (['failed', 'killed'].includes(job.status)) {
    toast(`${uid} ${job.status}: it has no output to use`, 'error');
    return;
  }
  const outs = job.status === 'completed' ? job.outputs : (state.types[job.type]?.outputs || []);
  const fits = outs.filter((o) => slot.types.includes(o.type));
  if (!fits.length) {
    toast(`${uid} has no output for '${slot.label}' (${slot.types.map((ty) => state.info.data_types[ty] || ty).join(' / ')})`, 'error');
    return;
  }
  const best = (slot.prefer || []).map((name) => fits.find((o) => o.name === name)).find(Boolean) || fits[0];
  builderDrop(slotName, { job: uid, output: best.name, type: best.type });
}

// "Continue with" a job when the builder already has that job type: connect the job's outputs
// (they replace what was there) and, for still-empty slots, what the job itself consumed.
// Returns the names of the slots that changed.
export function builderPrefill(job) {
  const b = state.builder;
  const found = {};
  prefill({ inputs: found }, state.types[b.type], job);
  const changed = Object.entries(found)
    .filter(([slot, ref]) => ref.job === job.uid || !b.inputs[slot])
    .filter(([slot, ref]) => JSON.stringify(b.inputs[slot]) !== JSON.stringify(ref))
    .map(([slot, ref]) => { b.inputs[slot] = ref; return slot; });
  renderInputs(changed);
  return changed;
}

// Slots of the open builder that accept an output of this type.
export function builderSlotsFor(type) {
  const b = state.builder;
  return b?.type ? state.types[b.type].inputs.filter((s) => s.types.includes(type)) : [];
}

// Connect an output to the best slot (an empty one first, then the slot that prefers it).
export function builderConnect(ref) {
  const b = state.builder;
  const rank = (s) => (b.inputs[s.name] ? 1000 : 0) + ((s.prefer || []).includes(ref.output) ? s.prefer.indexOf(ref.output) : 100);
  const slot = builderSlotsFor(ref.type).sort((a, c) => rank(a) - rank(c))[0];
  if (!slot) return null;
  b.inputs[slot.name] = { job: ref.job, output: ref.output };
  renderInputs(slot.name);
  return slot;
}

export function refreshBuilderInputs() {
  if (state.builder?.type) renderInputs();
}

// ------------------------------------------------------------------ render
let inputsBox = null;

export function renderBuilder() {
  const b = state.builder;
  const p = panel();
  p.hidden = false;
  document.dispatchEvent(new CustomEvent('builder-type', { detail: b.type })); // a job opened next to it adapts its outputs
  if (!b.type) return renderTypePicker(p);
  const t = state.types[b.type];
  const head = h('div', { class: 'panel-head' },
    h('div', { class: 'panel-title' }, b.editing ? h('span', { class: 'uid' }, b.editing.uid) : icon('plus'),
      b.editing ? `Edit ${t.title}` : t.title, h('span', { class: 'grow' }),
      !b.editing ? h('button', { class: 'btn small', onclick: () => { b.type = null; renderBuilder(); typeChanged(); } }, 'Change type') : null,
      h('button', { class: 'icon-btn', 'aria-label': 'Close', onclick: () => navigate(`#/p/${state.project.uid}`) }, icon('close'))),
    h('div', { class: 'help-purpose small', style: { marginTop: '4px' }, title: t.description }, t.help?.purpose || t.description));
  const body = h('div', { class: 'panel-body' });
  if (t.help) body.appendChild(helpDetails(t));
  if (t.tool_status === 'missing') {
    body.appendChild(h('div', { class: 'alert warn' }, `The program for this job (${t.tool}) was not detected on the server. `,
      h('a', { href: '#/tools' }, 'Configure it in Tools'), '. You can still prepare the job.'));
  }
  body.appendChild(h('div', { class: 'field' }, h('label', { for: 'b-title' }, 'Job title'),
    h('input', { id: 'b-title', type: 'text', value: b.title, placeholder: t.title, oninput: (e) => { b.title = e.target.value; } })));

  if (t.inputs.length) {
    inputsBox = h('div', {});
    body.append(h('div', { class: 'section' }, h('h4', {}, 'Inputs'),
      h('div', { class: 'muted small', style: { marginBottom: '6px' } },
        'Choose an output, or drag one from a job card or a job opened on the left; in the tree view, drag the whole job.'), inputsBox));
    renderInputs();
  } else {
    inputsBox = null;
  }

  const basic = t.params.filter((x) => !x.advanced);
  const adv = t.params.filter((x) => x.advanced);
  const compute = computeSection(b, t);
  if (t.params.length) {
    const sec = h('div', { class: 'section' }, h('h4', {}, 'Parameters'), basic.map((prm) => paramField(prm, b.params)));
    if (adv.length) {
      const advBox = h('div', { hidden: !b.showAdvanced }, adv.map((prm) => paramField(prm, b.params)));
      sec.append(h('button', { class: 'adv-toggle', type: 'button', onclick: (e) => {
        b.showAdvanced = !b.showAdvanced; advBox.hidden = !b.showAdvanced;
        e.target.textContent = b.showAdvanced ? '▾ Hide advanced parameters' : `▸ Advanced parameters (${adv.length})`;
      } }, b.showAdvanced ? '▾ Hide advanced parameters' : `▸ Advanced parameters (${adv.length})`), advBox);
    }
    // parameters shown only when another one is set (the helical options once "Helical" is ticked...)
    const showIf = () => {
      for (const el of sec.querySelectorAll('[data-show-if]')) el.hidden = !paramShown(el.dataset.showIf, b.params);
    };
    showIf();
    sec.addEventListener('input', showIf);
    sec.addEventListener('change', showIf);
    // parameters setting the GPU count or the processes change what Compute shows
    const needs = new Set([t.gpu_param, ...cpuParams(t)].filter(Boolean).map((n) => `p-${n}`));
    if (needs.size) sec.addEventListener('input', (e) => { if (needs.has(e.target.id)) compute.render(); });
    if (needs.size) sec.addEventListener('change', (e) => { if (needs.has(e.target.id)) compute.render(); });
    body.appendChild(sec);
  }
  body.appendChild(compute.el);

  const foot = h('div', { class: 'sticky-foot' },
    h('span', { class: 'grow' }),
    btn(b.editing ? 'Save' : 'Create', () => submit(false), { ic: b.editing ? 'check' : 'plus' }),
    btn(b.editing ? 'Save & queue' : 'Queue job', () => submit(true), { cls: 'primary', ic: 'play' }));
  clear(p, head, body, foot);
}

function slotLabel(c) {
  const st = c.pending ? ` (${c.job.status})` : '';
  return `${c.job.uid} · ${c.job.title} → ${c.output.label || c.output.name}${st}`;
}

function renderInputs(flash = []) {
  const b = state.builder;
  if (!inputsBox) return;
  const flashed = new Set([].concat(flash || []));
  const t = state.types[b.type];
  // grouped slots (e.g. focused maps 1..8): show the filled ones and the first empty one only
  const emptyShown = new Set();
  const visible = t.inputs.filter((slot) => {
    if (!slot.group || b.inputs[slot.name]) return true;
    if (emptyShown.has(slot.group)) return false;
    emptyShown.add(slot.group);
    return true;
  });
  const rows = visible.map((slot) => {
    const cands = compatibleOutputs(slot, b.editing?.uid);
    const cur = b.inputs[slot.name];
    const curKey = cur ? `${cur.job}|${cur.output}` : '';
    const sel = h('select', { onchange: (e) => {
      if (!e.target.value) delete b.inputs[slot.name];
      else { const [job, output] = e.target.value.split('|'); b.inputs[slot.name] = { job, output }; }
      box.classList.toggle('filled', !!e.target.value);
      if (slot.group) renderInputs(); // reveal (or hide) the next slot of the group
    } }, h('option', { value: '' }, cands.length ? '— not connected —' : '— no compatible output yet —'),
    cands.map((c) => h('option', { value: `${c.job.uid}|${c.output.name}`, selected: `${c.job.uid}|${c.output.name}` === curKey }, slotLabel(c))));
    if (cur && !cands.some((c) => `${c.job.uid}|${c.output.name}` === curKey)) {
      sel.appendChild(h('option', { value: curKey, selected: true }, `${cur.job} → ${cur.output}`));
    }
    const box = h('div', {
      class: `slot ${cur ? 'filled' : ''} ${flashed.has(slot.name) ? 'flash' : ''}`,
      ondragover: (e) => { e.preventDefault(); box.classList.add('dragover'); },
      ondragleave: () => box.classList.remove('dragover'),
      ondrop: (e) => {
        e.preventDefault(); box.classList.remove('dragover');
        const output = e.dataTransfer.getData('application/x-cryoplug-output');
        const job = e.dataTransfer.getData('application/x-cryoplug-job');
        try {
          if (output) builderDrop(slot.name, JSON.parse(output));
          else if (job) builderDropJob(slot.name, job);
        } catch { /* not ours */ }
      },
    }, h('div', { class: 'slot-head' }, slot.label, slot.required ? h('span', { class: 'req' }, '*') : h('span', { class: 'muted small' }, 'optional'),
      h('span', { class: 'grow' }), slot.types.map((ty) => h('span', { class: `tag type-${ty}` }, state.info.data_types[ty] || ty))),
    sel, slot.help ? h('div', { class: 'muted small', style: { marginTop: '3px' } }, slot.help) : null);
    return box;
  });
  clear(inputsBox, rows);
}

// "name": shown when that parameter is set (ticked, non-zero); "name=a|b": when it has one of these values.
export function paramShown(cond, values) {
  const [name, want] = cond.split('=');
  const v = values[name];
  if (want === undefined) return !!v && v !== '0' && v !== 'false' && v !== 'none';
  return want.split('|').includes(String(v));
}

export function paramField(prm, values, idPrefix = 'p') {
  const field = paramFieldInner(prm, values, idPrefix);
  if (prm.show_if) field.dataset.showIf = prm.show_if;
  return field;
}

function paramFieldInner(prm, values, idPrefix) {
  const id = `${idPrefix}-${prm.name}`;
  const v = values[prm.name];
  const set = (val) => { values[prm.name] = val; };
  const help = prm.help ? h('div', { class: 'help' }, prm.help) : null;
  const label = h('label', { for: id }, prm.label, prm.required ? h('span', { class: 'req' }, '*') : null);
  if (prm.type === 'bool') {
    return h('div', { class: 'field' }, h('div', { class: 'field check' },
      h('input', { id, type: 'checkbox', checked: !!v, onchange: (e) => set(e.target.checked) }),
      h('label', { for: id }, prm.label)), help);
  }
  if (prm.type === 'multichoice') { // several of the choices: one box each
    const chosen = new Set(Array.isArray(v) ? v : []);
    return h('div', { class: 'field' }, h('label', {}, prm.label),
      h('div', { class: 'multi-choice', role: 'group', 'aria-label': prm.label }, prm.choices.map((c) => h('label', { class: 'check' },
        h('input', { type: 'checkbox', checked: chosen.has(c), onchange: (e) => {
          if (e.target.checked) chosen.add(c); else chosen.delete(c);
          set(prm.choices.filter((x) => chosen.has(x)));
        } }), prm.labels?.[c] ?? String(c).replaceAll('_', ' ')))), help);
  }
  let input;
  if (prm.type === 'choice') {
    input = h('select', { id, onchange: (e) => set(e.target.value) },
      prm.choices.map((c) => h('option', { value: c, selected: c === v }, prm.labels?.[c] ?? String(c).replaceAll('_', ' '))));
  } else if (prm.type === 'text') {
    input = h('textarea', { id, class: 'mono', rows: 4, placeholder: prm.placeholder || '', oninput: (e) => set(e.target.value) });
    input.value = v ?? '';
  } else if (prm.type === 'float' || prm.type === 'int') {
    const isAuto = prm.name === 'resolution' || (prm.help || '').startsWith('0 =');
    input = h('input', { id, type: 'number', step: prm.type === 'int' ? 1 : 'any', min: prm.min ?? undefined, max: prm.max ?? undefined,
      value: isAuto && Number(v) === 0 ? '' : (v ?? ''), placeholder: isAuto ? 'auto' : '', oninput: (e) => set(e.target.value === '' ? (isAuto ? 0 : prm.default) : e.target.value) });
  } else {
    input = h('input', { id, type: 'text', value: v ?? '', placeholder: prm.placeholder || '', oninput: (e) => set(e.target.value) });
  }
  const controls = [input];
  const locked = prm.admin_only && restricted();
  if (locked) input.disabled = true;
  if (prm.unit) controls.push(h('span', { class: 'unit' }, prm.unit));
  const row = h('div', { class: 'row' }, controls);
  if (!locked && (prm.type === 'path' || (prm.type === 'str' && prm.path_kind === 'files'))) fileControls(prm, input, set, row);
  return h('div', { class: 'field' }, label, row, help,
    locked ? h('div', { class: 'help' }, icon('lock'), ' Administrators only: it can run code on the server.') : null);
}

// A file field: Browse (on the server) and, in a project, Upload (from this computer; or drop files on the field).
function fileControls(prm, input, set, row) {
  const kind = prm.type !== 'path' ? 'files' : prm.path_kind === 'dir' ? 'dir' : prm.path_kind === 'any' ? 'any' : 'file';
  const apply = (value) => { input.value = value; set(value); };
  row.appendChild(btn('Browse', async () => {
    const parts = input.value.split(';').map((x) => x.trim()).filter(Boolean);
    const chosen = await browseFiles({ start: (kind === 'files' ? parts[parts.length - 1] : input.value) || '', kind: kind === 'dir' ? 'dir' : 'file',
      title: `Select ${prm.label.toLowerCase()}`, project: state.project });
    if (chosen) apply(kind === 'files' ? [...parts, chosen].join('; ') : chosen);
  }, { cls: 'small', ic: 'folder', title: 'Choose a file on the server' }));
  if (!state.project) return;
  const upload = async (items) => {
    const value = await uploadForField(state.project.uid, kind, items, input.value);
    if (value) apply(value);
  };
  row.appendChild(btn('Upload', (e) => {
    if (kind !== 'any') { pickFiles({ multiple: kind === 'files', directory: kind === 'dir' }).then(upload); return; }
    popupMenu(e.currentTarget, [
      { label: 'Files…', ic: 'file', action: async () => upload(await pickFiles({ multiple: true })) },
      { label: 'A folder…', ic: 'folder', action: async () => upload(await pickFiles({ directory: true })) },
    ]);
  }, { cls: 'small', ic: 'upload', title: 'Send from this computer to the project, then use it here (or drop it on the field)' }));
  row.addEventListener('dragover', (e) => { if (draggingFiles(e)) { e.preventDefault(); row.classList.add('file-target'); } });
  row.addEventListener('dragleave', () => row.classList.remove('file-target'));
  row.addEventListener('drop', async (e) => {
    if (!draggingFiles(e)) return;
    e.preventDefault();
    row.classList.remove('file-target');
    upload(await filesFromDrop(e.dataTransfer));
  });
}

// The job types used last in this browser, first in the picker.
const RECENT_TYPES_KEY = 'cryoplug.recentTypes';
export function recentTypes() {
  try { return JSON.parse(localStorage.getItem(RECENT_TYPES_KEY) || '[]'); } catch { return []; }
}
function rememberType(name) {
  const list = [name, ...recentTypes().filter((n) => n !== name)].slice(0, 8);
  try { localStorage.setItem(RECENT_TYPES_KEY, JSON.stringify(list)); } catch { /* storage unavailable */ }
}

function renderTypePicker(p) {
  const b = state.builder;
  const search = h('input', { type: 'search', placeholder: 'Search job types (e.g. locscale, isolde, validation)…', value: b.search,
    oninput: (e) => { b.search = e.target.value; renderList(); } });
  const list = h('div', { class: 'type-list' });
  const from = b.prefillFrom;
  const typeItem = (t) => {
    const choose = () => { applyType(t.name); panel().scrollTop = 0; renderBuilder(); };
    return h('div', { class: 'type-item', tabindex: 0, role: 'button', onclick: choose,
      onkeydown: (e) => { if (e.key === 'Enter') choose(); } },
    h('span', { class: `tooldot ${t.tool_status}`, title: t.tool ? `${t.tool}: ${t.tool_status}` : 'built-in' }),
    h('div', { style: { minWidth: 0, flex: 1 } }, h('div', { class: 't' }, t.title),
      h('div', { class: 'd', title: t.help?.purpose || t.description }, t.help?.purpose || t.description)),
    t.gpu ? h('span', { class: 'tag' }, 'GPU') : null,
    t.interactive ? h('span', { class: 'tag' }, 'interactive') : null,
    h('button', { class: 'icon-btn', type: 'button', title: 'Aide : quand utiliser ce job', 'aria-label': `Aide ${t.title}`,
      onclick: (e) => { e.stopPropagation(); helpModal(t, { onUse: choose, onNext: (n) => { applyType(n); panel().scrollTop = 0; renderBuilder(); } }); },
    }, h('b', { 'aria-hidden': 'true', style: { width: '18px', textAlign: 'center' } }, '?')));
  };
  function renderList() {
    const q = b.search.trim().toLowerCase();
    const items = [];
    if (!q && !from) {
      const recent = recentTypes().map((n) => state.types[n]).filter((t) => t && !(t.admin_only && restricted()));
      if (recent.length) {
        items.push(h('div', { class: 'cat' }, 'Recently used'));
        for (const t of recent) items.push(typeItem(t));
      }
    }
    for (const cat of state.info.categories) {
      let types = state.jobtypes.filter((t) => t.category === cat && !(t.admin_only && restricted()));
      if (from) {
        const outTypes = new Set((from.status === 'completed' ? from.outputs : state.types[from.type].outputs).map((o) => o.type));
        types = types.filter((t) => t.inputs.some((s) => s.types.some((ty) => outTypes.has(ty))));
      }
      if (q) {
        types = types.filter((t) => [t.title, t.name, t.description, t.tool || '', t.help?.purpose || '', ...(t.help?.when || [])]
          .join(' ').toLowerCase().includes(q));
      }
      if (!types.length) continue;
      items.push(h('div', { class: 'cat' }, CATEGORY_FR[cat] && CATEGORY_FR[cat] !== cat ? `${cat} · ${CATEGORY_FR[cat]}` : cat));
      for (const t of types) items.push(typeItem(t));
    }
    clear(list, items.length ? items : h('div', { class: 'muted' }, 'No matching job type.'));
  }
  renderList();
  clear(p,
    h('div', { class: 'panel-head' }, h('div', { class: 'panel-title' }, icon('plus'), from ? `Continue from ${from.uid}` : 'New job',
      h('span', { class: 'grow' }), h('button', { class: 'icon-btn', 'aria-label': 'Close', onclick: () => navigate(`#/p/${state.project.uid}`) }, icon('close'))),
    h('div', { style: { marginTop: '8px' } }, search),
    h('div', { class: 'muted small', style: { marginTop: '6px' } }, h('span', { class: 'tooldot found', style: { display: 'inline-block' } }), ' program detected  ',
      h('span', { class: 'tooldot missing', style: { display: 'inline-block', marginLeft: '8px' } }), ' not detected')),
    h('div', { class: 'panel-body' }, list));
  setTimeout(() => search.focus(), 20);
}

async function submit(queue) {
  const b = state.builder;
  const puid = state.project.uid;
  let resources;
  try { resources = requestedBody(b, state.types[b.type]); } catch (err) { toast(err.message, 'error', 6000); return; }
  const body = { title: b.title || null, params: b.params, inputs: b.inputs, lane: b.lane || state.info.lanes[0].name, resources };
  let job;
  try {
    job = b.editing ? await guard(api.updateJob(puid, b.editing.uid, body)) : await guard(api.createJob(puid, { ...body, type: b.type }));
  } catch { return; /* toast already shown, keep the form */ }
  if (!b.editing) rememberType(b.type);
  state.builder = null;
  let queued = false;
  if (queue) {
    try { job = await guard(api.queueJob(puid, job.uid, body.lane)); queued = true; } catch { /* job stays in building */ }
  }
  toast(`${job.uid} ${queued ? 'queued' : 'saved'}`, 'ok');
  await refreshJobs();
  navigate(`#/p/${puid}/${job.uid}`);
}
