// Job builder (right panel): pick a job type, connect inputs (select or drag & drop outputs), set parameters, queue.
import { api } from './api.js';
import { browseFiles } from './filebrowser.js';
import { CATEGORY_FR, helpDetails, helpModal } from './help.js';
import { compatibleOutputs, navigate, refreshJobs, state, typeTitle } from './state.js';
import { btn, clear, guard, h, icon, toast } from './ui.js';

const panel = () => document.getElementById('panel');

export function openBuilder({ type = null, editing = null, prefillFrom = null } = {}) {
  const b = { type, editing, inputs: {}, params: {}, title: '', lane: '', showAdvanced: false, search: '', prefillFrom };
  if (editing) {
    b.type = editing.type;
    b.inputs = JSON.parse(JSON.stringify(editing.inputs || {}));
    b.params = { ...(editing.params || {}) };
    b.title = editing.title === typeTitle(editing.type) ? '' : editing.title;
    b.lane = editing.lane || '';
  }
  state.builder = b;
  if (b.type && !editing) applyType(b.type);
  panel().scrollTop = 0;
  renderBuilder();
}

export function closeBuilder() {
  state.builder = null;
}

function applyType(name) {
  const b = state.builder;
  const t = state.types[name];
  b.type = name;
  b.params = Object.fromEntries(t.params.map((p) => [p.name, p.default]));
  b.inputs = {};
  if (b.prefillFrom) prefill(b, t, b.prefillFrom);
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
      !b.editing ? h('button', { class: 'btn small', onclick: () => { b.type = null; renderBuilder(); } }, 'Change type') : null,
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
        'Choose an output, or drag one from a job card or from a job opened on the left.'), inputsBox));
    renderInputs();
  } else {
    inputsBox = null;
  }

  const basic = t.params.filter((x) => !x.advanced);
  const adv = t.params.filter((x) => x.advanced);
  if (t.params.length) {
    const sec = h('div', { class: 'section' }, h('h4', {}, 'Parameters'), basic.map((prm) => paramField(prm, b.params)));
    if (adv.length) {
      const advBox = h('div', { hidden: !b.showAdvanced }, adv.map((prm) => paramField(prm, b.params)));
      sec.append(h('button', { class: 'adv-toggle', type: 'button', onclick: (e) => {
        b.showAdvanced = !b.showAdvanced; advBox.hidden = !b.showAdvanced;
        e.target.textContent = b.showAdvanced ? '▾ Hide advanced parameters' : `▸ Advanced parameters (${adv.length})`;
      } }, b.showAdvanced ? '▾ Hide advanced parameters' : `▸ Advanced parameters (${adv.length})`), advBox);
    }
    body.appendChild(sec);
  }

  const lanes = state.info.lanes;
  const laneSel = h('select', { onchange: (e) => { b.lane = e.target.value; } },
    lanes.map((l) => h('option', { value: l.name, selected: (b.lane || lanes[0].name) === l.name }, `${l.name} (${l.type})`)));
  body.appendChild(h('div', { class: 'section' }, h('h4', {}, 'Resources'),
    h('div', { class: 'row' }, h('div', { class: 'field grow' }, h('label', {}, 'Lane'), laneSel)),
    h('div', { class: 'muted small' }, `${t.gpu ? `${t.gpu} GPU · ` : ''}${t.cpus} CPU${t.cpus > 1 ? 's' : ''}${t.interactive ? ' · interactive (waits for you after preparation)' : ''}`)));

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

function renderInputs(flash = null) {
  const b = state.builder;
  if (!inputsBox) return;
  const t = state.types[b.type];
  const rows = t.inputs.map((slot) => {
    const cands = compatibleOutputs(slot, b.editing?.uid);
    const cur = b.inputs[slot.name];
    const curKey = cur ? `${cur.job}|${cur.output}` : '';
    const sel = h('select', { onchange: (e) => {
      if (!e.target.value) delete b.inputs[slot.name];
      else { const [job, output] = e.target.value.split('|'); b.inputs[slot.name] = { job, output }; }
      box.classList.toggle('filled', !!e.target.value);
    } }, h('option', { value: '' }, cands.length ? '— not connected —' : '— no compatible output yet —'),
    cands.map((c) => h('option', { value: `${c.job.uid}|${c.output.name}`, selected: `${c.job.uid}|${c.output.name}` === curKey }, slotLabel(c))));
    if (cur && !cands.some((c) => `${c.job.uid}|${c.output.name}` === curKey)) {
      sel.appendChild(h('option', { value: curKey, selected: true }, `${cur.job} → ${cur.output}`));
    }
    const box = h('div', {
      class: `slot ${cur ? 'filled' : ''} ${slot.name === flash ? 'flash' : ''}`,
      ondragover: (e) => { e.preventDefault(); box.classList.add('dragover'); },
      ondragleave: () => box.classList.remove('dragover'),
      ondrop: (e) => {
        e.preventDefault(); box.classList.remove('dragover');
        try { builderDrop(slot.name, JSON.parse(e.dataTransfer.getData('application/x-cryoplug-output'))); } catch { /* not ours */ }
      },
    }, h('div', { class: 'slot-head' }, slot.label, slot.required ? h('span', { class: 'req' }, '*') : h('span', { class: 'muted small' }, 'optional'),
      h('span', { class: 'grow' }), slot.types.map((ty) => h('span', { class: `tag type-${ty}` }, state.info.data_types[ty] || ty))),
    sel, slot.help ? h('div', { class: 'muted small', style: { marginTop: '3px' } }, slot.help) : null);
    return box;
  });
  clear(inputsBox, rows);
}

export function paramField(prm, values, idPrefix = 'p') {
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
  let input;
  if (prm.type === 'choice') {
    input = h('select', { id, onchange: (e) => set(e.target.value) },
      prm.choices.map((c) => h('option', { value: c, selected: c === v }, String(c).replaceAll('_', ' '))));
  } else if (prm.type === 'text') {
    input = h('textarea', { id, rows: 4, placeholder: prm.placeholder || '', oninput: (e) => set(e.target.value) });
    input.value = v ?? '';
  } else if (prm.type === 'float' || prm.type === 'int') {
    const isAuto = prm.name === 'resolution' || (prm.help || '').startsWith('0 =');
    input = h('input', { id, type: 'number', step: prm.type === 'int' ? 1 : 'any', min: prm.min ?? undefined, max: prm.max ?? undefined,
      value: isAuto && Number(v) === 0 ? '' : (v ?? ''), placeholder: isAuto ? 'auto' : '', oninput: (e) => set(e.target.value === '' ? (isAuto ? 0 : prm.default) : e.target.value) });
  } else {
    input = h('input', { id, type: 'text', value: v ?? '', placeholder: prm.placeholder || '', oninput: (e) => set(e.target.value) });
  }
  const controls = [input];
  if (prm.unit) controls.push(h('span', { class: 'unit' }, prm.unit));
  if (prm.type === 'path') {
    controls.push(btn('Browse', async () => {
      const kind = prm.path_kind === 'dir' ? 'dir' : 'file';
      const start = input.value || '';
      const chosen = await browseFiles({ start, kind, title: `Select ${prm.label.toLowerCase()}` });
      if (chosen) { input.value = chosen; set(chosen); }
    }, { cls: 'small', ic: 'folder' }));
  }
  return h('div', { class: 'field' }, label, h('div', { class: 'row' }, controls), help);
}

function renderTypePicker(p) {
  const b = state.builder;
  const search = h('input', { type: 'search', placeholder: 'Search job types (e.g. locscale, isolde, validation)…', value: b.search,
    oninput: (e) => { b.search = e.target.value; renderList(); } });
  const list = h('div', { class: 'type-list' });
  const from = b.prefillFrom;
  function renderList() {
    const q = b.search.trim().toLowerCase();
    const items = [];
    for (const cat of state.info.categories) {
      let types = state.jobtypes.filter((t) => t.category === cat);
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
      for (const t of types) {
        const choose = () => { applyType(t.name); panel().scrollTop = 0; renderBuilder(); };
        items.push(h('div', { class: 'type-item', tabindex: 0, role: 'button', onclick: choose,
          onkeydown: (e) => { if (e.key === 'Enter') choose(); } },
        h('span', { class: `tooldot ${t.tool_status}`, title: t.tool ? `${t.tool}: ${t.tool_status}` : 'built-in' }),
        h('div', { style: { minWidth: 0, flex: 1 } }, h('div', { class: 't' }, t.title),
          h('div', { class: 'd', title: t.help?.purpose || t.description }, t.help?.purpose || t.description)),
        t.gpu ? h('span', { class: 'tag' }, 'GPU') : null,
        t.interactive ? h('span', { class: 'tag' }, 'interactive') : null,
        h('button', { class: 'icon-btn', type: 'button', title: 'Aide : quand utiliser ce job', 'aria-label': `Aide ${t.title}`,
          onclick: (e) => { e.stopPropagation(); helpModal(t, { onUse: choose, onNext: (n) => { applyType(n); panel().scrollTop = 0; renderBuilder(); } }); },
        }, h('b', { 'aria-hidden': 'true', style: { width: '18px', textAlign: 'center' } }, '?'))));
      }
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
  const body = { title: b.title || null, params: b.params, inputs: b.inputs, lane: b.lane || state.info.lanes[0].name };
  let job;
  try {
    job = b.editing ? await guard(api.updateJob(puid, b.editing.uid, body)) : await guard(api.createJob(puid, { ...body, type: b.type }));
  } catch { return; /* toast already shown, keep the form */ }
  state.builder = null;
  let queued = false;
  if (queue) {
    try { job = await guard(api.queueJob(puid, job.uid, body.lane)); queued = true; } catch { /* job stays in building */ }
  }
  toast(`${job.uid} ${queued ? 'queued' : 'saved'}`, 'ok');
  await refreshJobs();
  navigate(`#/p/${puid}/${job.uid}`);
}
