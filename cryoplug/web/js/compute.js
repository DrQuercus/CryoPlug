// Compute choices of a job, as in CryoSPARC: the lane (the default one is preselected), its GPUs (picked
// automatically, or chosen on a live view of the GPUs), CPU threads, and the SLURM partition / time / memory.
import { api } from './api.js';
import { state } from './state.js';
import { clear, h, icon, meter } from './ui.js';

export const lanes = () => state.info.lanes || [];
export const laneOf = (name) => lanes().find((l) => l.name === name) || lanes()[0];
// A local lane that hands out GPUs (each job gets its own through CUDA_VISIBLE_DEVICES).
export const managesGpus = (lane) => lane?.type === 'local' && (lane.gpus || []).length > 0;
const plural = (n, word) => `${n} ${word}${n > 1 ? 's' : ''}`;

// GPUs and CPU threads a job asks for: the same rules as JobType.resources on the server.
export function gpuNeed(t, params) {
  if (!t || t.interactive) return 0;
  if (t.gpu_param) {
    const p = t.params.find((x) => x.name === t.gpu_param);
    const v = params[t.gpu_param];
    return p?.type === 'bool' ? (v ? 1 : 0) : Math.max(1, Math.floor(Number(v) || 1));
  }
  return t.gpu;
}

export function cpuDefault(t, params) {
  return t.cpu_param ? Math.max(1, Math.floor(Number(params[t.cpu_param]) || t.cpus)) : t.cpus;
}

export function laneKind(lane) {
  if (lane.type === 'cluster') return lane.partitions?.length ? `cluster · ${lane.partitions.join(', ')}` : 'cluster';
  return managesGpus(lane) ? `this machine · ${plural(lane.gpus.length, 'GPU')}` : 'this machine';
}

export function fmtGiB(mib) {
  if (mib === null || mib === undefined) return '–';
  const gb = mib / 1024;
  return `${gb.toFixed(gb < 10 ? 1 : 0)} GB`;
}

// Live GPU usage (monitor) and the CryoPlug job holding each GPU (queue), shared by the builders for 4 s.
let gpuCache = { at: 0, data: null, pending: null };
export async function gpuStatus(maxAge = 4000) {
  if (gpuCache.data && Date.now() - gpuCache.at < maxAge) return gpuCache.data;
  if (!gpuCache.pending) {
    gpuCache.pending = Promise.all([api.monitor().catch(() => null), api.queue().catch(() => null)]).then(([mon, q]) => {
      const holders = {};
      for (const l of q?.lanes || []) if (l.type === 'local') Object.assign(holders, l.gpu_jobs || {});
      const data = { gpus: mon?.gpus || [], error: mon?.gpu_error || null, holders };
      gpuCache = { at: Date.now(), data, pending: null };
      return data;
    }).finally(() => { gpuCache.pending = null; });
  }
  return gpuCache.pending;
}

export function holderLabel(holder) {
  if (!holder) return '';
  return holder.hidden ? `job of ${holder.owner || 'another user'}` : `${holder.project_uid}/${holder.uid}`;
}

// Builder state: b.lane, b.requested ({ gpu_ids, num_cpus, partition, time, mem }), b.gpuMode ('auto' | 'choose').
export function initCompute(b, editing) {
  b.lane = laneOf(b.lane)?.name || ''; // a job prepared for a lane deleted since goes to the default one
  b.requested = { gpu_ids: [], num_cpus: '', partition: '', time: '', mem: '', ...(editing?.requested || {}) };
  b.gpuMode = (b.requested.gpu_ids || []).length ? 'choose' : 'auto';
}

// Choices that no longer apply once another lane is picked are dropped.
function adaptToLane(b) {
  const lane = laneOf(b.lane);
  const req = b.requested;
  if (!managesGpus(lane)) { req.gpu_ids = []; b.gpuMode = 'auto'; } else req.gpu_ids = (req.gpu_ids || []).filter((g) => lane.gpus.includes(g));
  if (lane.type !== 'cluster') { req.partition = ''; req.time = ''; req.mem = ''; }
  else if (req.partition && (req.partition === lane.partition || !(lane.partitions || []).includes(req.partition))) req.partition = '';
}

// What the job submission sends as "resources"; throws when the GPU choice is incomplete.
export function requestedBody(b, t) {
  const lane = laneOf(b.lane);
  const req = b.requested || {};
  const out = {};
  const cpus = String(req.num_cpus ?? '').trim();
  if (cpus && !t.cpu_param) out.num_cpus = Number(cpus);
  const need = gpuNeed(t, b.params);
  if (managesGpus(lane) && need > 0 && b.gpuMode === 'choose') {
    const ids = (req.gpu_ids || []).filter((g) => lane.gpus.includes(g));
    if (ids.length !== need) throw new Error(`Choose ${plural(need, 'GPU')} in Compute (or leave the GPU choice automatic)`);
    out.gpu_ids = ids;
  }
  if (lane.type === 'cluster') {
    for (const key of ['partition', 'time', 'mem']) {
      const v = String(req[key] || '').trim();
      if (v) out[key] = v;
    }
  }
  return out;
}

// The "Compute" section of the job builder. render() again when parameters that change the needs are edited.
export function computeSection(b, t) {
  const root = h('div', { class: 'section compute' });
  let status = null;
  let loading = false;

  async function load(force = false) {
    if (loading) return;
    loading = true;
    try { status = await gpuStatus(force ? 0 : 4000); } catch { status = null; }
    loading = false;
    if (root.isConnected) render(); // not when the builder was closed or redrawn meanwhile
  }

  function toggle(g, need) {
    const ids = b.requested.gpu_ids || [];
    if (need === 1) b.requested.gpu_ids = [g];
    else if (ids.includes(g)) b.requested.gpu_ids = ids.filter((x) => x !== g);
    else b.requested.gpu_ids = [...ids, g].slice(-need); // the oldest choice gives way
    render();
  }

  function gpuOption(g, need) {
    const info = status?.gpus.find((x) => x.index === g);
    const holder = status?.holders[String(g)];
    const on = (b.requested.gpu_ids || []).includes(g);
    return h('button', { type: 'button', class: `gpu-opt ${on ? 'on' : ''}`, 'aria-pressed': on ? 'true' : 'false',
      title: holder ? `Used by ${holderLabel(holder)} (${holder.title}): the job waits for it` : 'No CryoPlug job on this GPU',
      onclick: () => toggle(g, need) },
    h('span', { class: 'gpu-opt-head' }, h('span', { class: 'check-box', 'aria-hidden': 'true' }, on ? icon('check') : null),
      h('b', {}, `GPU ${g}`), h('span', { class: `gpu-state ${holder ? 'busy' : ''}` }, holder ? holderLabel(holder) : 'available')),
    h('span', { class: 'gpu-opt-name' }, info ? info.name : status ? 'not seen by nvidia-smi' : '…'),
    info ? h('span', { class: 'gpu-opt-mem' }, meter(info.memory_percent, { severity: false, label: `GPU ${g} memory used` }),
      h('span', {}, `${fmtGiB(info.memory_used)} / ${fmtGiB(info.memory_total)}`)) : null,
    info ? h('span', { class: 'gpu-opt-util muted' }, `${info.utilization ?? '–'} % busy${info.temperature !== null && info.temperature !== undefined ? ` · ${info.temperature} °C` : ''}`) : null);
  }

  function gpuBlock(lane, need) {
    if (need === 0) return h('div', { class: 'muted small compute-line' }, icon('gpu'), t.interactive ? 'No GPU · interactive: it waits for you after preparation.' : 'This job does not use a GPU.');
    if (lane.type === 'cluster') {
      return h('div', { class: 'muted small compute-line' }, icon('gpu'), `SLURM allocates ${plural(need, 'GPU')} on a node of the partition (--gres).`);
    }
    if (!managesGpus(lane)) {
      return h('div', { class: 'muted small compute-line' }, icon('gpu'),
        `${plural(need, 'GPU')} needed. This lane does not hand out GPUs: the program uses those it finds. `
        + 'An administrator can list the lane\'s GPUs in Settings › Compute.');
    }
    if (!status && !loading) load();
    const holders = status?.holders || {};
    const free = lane.gpus.filter((g) => !holders[String(g)]);
    const memoryUsed = (g) => status?.gpus.find((x) => x.index === g)?.memory_used ?? 0;
    const suggested = free.slice().sort((a, c) => memoryUsed(a) - memoryUsed(c) || a - c).slice(0, need); // as the scheduler would
    const seg = h('div', { class: 'seg', role: 'radiogroup', 'aria-label': 'GPU choice' },
      [['auto', 'Automatic'], ['choose', 'Choose GPUs']].map(([mode, label]) => h('button', {
        type: 'button', role: 'radio', class: b.gpuMode === mode ? 'on' : '', 'aria-checked': b.gpuMode === mode ? 'true' : 'false',
        onclick: () => {
          b.gpuMode = mode;
          if (mode === 'choose' && !(b.requested.gpu_ids || []).length) b.requested.gpu_ids = suggested;
          if (mode === 'choose') load(true);
          render();
        },
      }, label)));
    const head = h('div', { class: 'field' }, h('label', {}, `GPU${need > 1 ? 's' : ''}`, h('span', { class: 'muted small' }, `· this job uses ${need}`)),
      h('div', { class: 'row wrap' }, seg, status ? h('span', { class: 'muted small' }, `${free.length} of ${lane.gpus.length} available now`) : null,
        h('span', { class: 'grow' }),
        b.gpuMode === 'choose' ? h('button', { class: 'icon-btn', type: 'button', title: 'Refresh the GPU usage', 'aria-label': 'Refresh the GPU usage',
          onclick: () => load(true) }, icon('refresh')) : null));
    if (b.gpuMode !== 'choose') {
      return [head, h('div', { class: 'help compute-help' },
        `When the job starts, CryoPlug takes the free GPU${need > 1 ? 's' : ''} with the least memory in use`
        + `${free.length < need && status ? '; it waits while the GPUs are taken by other jobs' : ''}.`)];
    }
    const ids = b.requested.gpu_ids || [];
    const busy = ids.filter((g) => holders[String(g)]);
    const hint = ids.length !== need ? h('div', { class: 'compute-hint warn', role: 'status' }, icon('alert'), `Choose ${plural(need, 'GPU')} (${ids.length} selected).`)
      : busy.length ? h('div', { class: 'compute-hint', role: 'status' }, icon('clock'), `GPU ${busy.join(', ')} ${busy.length > 1 ? 'are' : 'is'} in use: the job waits until ${busy.length > 1 ? 'they are' : 'it is'} free.`)
        : null;
    return [head, h('div', { class: 'gpu-pick', role: 'group', 'aria-label': 'GPUs of the lane' }, lane.gpus.map((g) => gpuOption(g, need))),
      status?.error ? h('div', { class: 'help' }, status.error) : null, hint];
  }

  function render() {
    const lane = laneOf(b.lane);
    const need = gpuNeed(t, b.params);
    const req = b.requested;
    const all = lanes();
    const laneSel = h('select', { id: 'b-lane', onchange: (e) => { b.lane = e.target.value; adaptToLane(b); render(); } },
      all.map((l, i) => h('option', { value: l.name, selected: l.name === lane.name }, `${l.name}${i === 0 ? ' (default)' : ''} — ${laneKind(l)}`)));
    const laneInfo = [lane.description, `up to ${plural(lane.max_jobs, 'job')} at once`].filter(Boolean).join(' · ');
    const cpuDef = cpuDefault(t, b.params);
    const cpuParam = t.cpu_param ? t.params.find((x) => x.name === t.cpu_param) : null;
    // a program started with N processes (Phenix nproc) gets N CPUs: set by its parameter, not here
    const cpus = cpuParam ? h('div', { class: 'field' }, h('label', {}, 'CPU threads'), h('div', { class: 'compute-fixed' }, String(cpuDef)),
      h('div', { class: 'help' }, `From “${cpuParam.label}” in the parameters${lane.type === 'cluster' ? ' (--cpus-per-task)' : ''}.`))
      : h('div', { class: 'field' }, h('label', { for: 'b-cpus' }, 'CPU threads'),
        h('input', { id: 'b-cpus', type: 'number', min: 1, max: 1024, step: 1, value: req.num_cpus || '', placeholder: String(cpuDef),
          oninput: (e) => { req.num_cpus = e.target.value; } }),
        h('div', { class: 'help' }, `Default for this job: ${cpuDef}.`,
          lane.type === 'cluster' ? ' Sent as --cpus-per-task.' : ' Sets the threads of the program (OMP_NUM_THREADS).'));
    const parts = [h('div', { class: 'field' }, h('label', { for: 'b-lane' }, 'Lane'), laneSel,
      laneInfo ? h('div', { class: 'help' }, laneInfo) : null)];
    parts.push(gpuBlock(lane, need));
    if (lane.type === 'cluster') {
      const others = (lane.partitions || []).filter((p) => p !== lane.partition);
      parts.push(h('div', { class: 'compute-grid' },
        h('div', { class: 'field' }, h('label', { for: 'b-partition' }, 'Partition'),
          h('select', { id: 'b-partition', disabled: !others.length, onchange: (e) => { req.partition = e.target.value; } },
            h('option', { value: '' }, `${lane.partition || 'cluster default'} (lane default)`),
            others.map((p) => h('option', { value: p, selected: req.partition === p }, p)))),
        h('div', { class: 'field' }, h('label', { for: 'b-time' }, 'Time limit'),
          h('input', { id: 'b-time', type: 'text', class: 'mono', value: req.time || '', placeholder: lane.time_limit || 'cluster default',
            oninput: (e) => { req.time = e.target.value; } })),
        h('div', { class: 'field' }, h('label', { for: 'b-mem' }, 'Memory'),
          h('input', { id: 'b-mem', type: 'text', class: 'mono', value: req.mem || '', placeholder: lane.mem || 'cluster default',
            oninput: (e) => { req.mem = e.target.value; } })),
        cpus));
      parts.push(h('div', { class: 'help' }, 'Empty fields keep the lane settings. Time as 12:00:00 or 2-00:00:00, memory as 64G.'));
    } else {
      parts.push(h('div', { class: 'compute-grid' }, cpus));
    }
    clear(root, h('h4', {}, 'Compute'), parts);
  }

  render();
  return { el: root, render };
}

// "Compute" block of the job details: where it runs and with what.
export function computeRows(job) {
  const res = job.resources || {};
  const req = job.requested || {};
  const lane = lanes().find((l) => l.name === job.lane);
  const need = Number(res.num_gpus || 0);
  const chosen = (req.gpu_ids || []).length > 0;
  let gpus = 'none';
  if (need > 0) {
    if ((job.gpus || []).length) gpus = `GPU ${job.gpus.join(', ')} · ${chosen ? 'chosen' : 'picked automatically'}`;
    else if (chosen) gpus = `GPU ${req.gpu_ids.join(', ')} · chosen`;
    else if (lane?.type === 'cluster') gpus = `${need} · allocated by SLURM`;
    else gpus = `${need} · automatic`;
  }
  const rows = [['Lane', job.lane ? `${job.lane}${lane ? ` (${laneKind(lane)})` : ' (removed)'}` : 'default lane'], ['GPUs', gpus],
    ['CPU threads', `${res.num_cpus ?? '–'}${req.num_cpus ? ' · chosen' : ''}`]];
  if (lane?.type === 'cluster' || req.partition || job.cluster_job_id) {
    rows.push(['Partition', req.partition || lane?.partition || 'lane default'], ['Time limit', req.time || lane?.time_limit || 'lane default'],
      ['Memory', req.mem || lane?.mem || 'lane default']);
    if (job.cluster_job_id) rows.push(['Cluster job', job.cluster_job_id]);
  }
  return rows;
}
