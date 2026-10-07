// Job details: overview, report, outputs, interactive session controls, live log and files.
// Shown in the right panel, or in the main area while the job builder occupies the panel
// (as in CryoSPARC), where its outputs can be dragged onto the builder's inputs.
import { api } from './api.js';
import { builderConnect, builderPrefill, builderSlotsFor, openBuilder } from './builder.js';
import { helpSheet } from './help.js';
import { heatmap, lineChart } from './plots.js';
import { builderHref, jobHref, navigate, refreshJobs, state, typeTitle } from './state.js';
import {
  ACTIVE, ago, btn, clear, confirmDialog, copyText, fmtSize, fmtTime, guard, h, icon, jobDuration, LIVE, popupMenu,
  statusChip, statusMark, toast,
} from './ui.js';

const D = { uid: null, job: null, mode: 'panel', tab: 'overview', log: '', logOffset: 0, filesSub: '', autoscroll: true, busy: false };
const container = () => document.getElementById(D.mode === 'page' ? 'content' : 'panel');

// mode: 'panel' (right side) or 'page' (main area, next to the job builder).
export function openDetail(uid, mode = 'panel') {
  if (D.uid !== uid || D.mode !== mode) {
    Object.assign(D, { uid, mode, job: null, tab: 'overview', log: '', logOffset: 0, filesSub: '' });
    container().scrollTop = 0;
  }
  container().hidden = false;
  return refreshDetail(true);
}

export function closeDetail() {
  D.uid = null;
  D.job = null;
}

export function detailUid() {
  return D.uid;
}

export function detailMode() {
  return D.uid ? D.mode : null;
}

// Outputs of a job shown next to the builder can be connected only to the builder's current job type.
document.addEventListener('builder-type', (e) => {
  if (D.mode === 'page' && D.job && D.tab === 'overview' && (D.linkType || null) !== (e.detail || null)) render();
});

// Where to go when the job is closed or deleted.
const closeHref = () => (D.mode === 'page' ? builderHref() : `#/p/${state.project.uid}`);

export async function refreshDetail(force = false) {
  if (!D.uid || !state.project || D.busy) return;
  const puid = state.project.uid;
  const uid = D.uid;
  D.busy = true;
  try {
    const job = await api.job(puid, uid);
    if (D.uid !== uid) return;
    const changed = force || !D.job || D.job.status !== job.status || JSON.stringify(D.job.outputs) !== JSON.stringify(job.outputs)
      || D.job.progress !== job.progress || D.job.message !== job.message || JSON.stringify(D.job.candidates) !== JSON.stringify(job.candidates)
      || D.job.title !== job.title;
    D.job = job;
    if (D.tab === 'log') await pollLog();
    const editing = bodyEl && bodyEl.contains(document.activeElement) && ['TEXTAREA', 'INPUT', 'SELECT'].includes(document.activeElement.tagName);
    if (changed && D.tab !== 'log' && !editing) render();
    else if (changed) renderHeaderOnly();
  } catch (err) {
    if (err.status === 404) { toast(`${uid} no longer exists`, 'error'); navigate(closeHref()); }
  } finally {
    D.busy = false;
  }
}

export function detailIsLive() {
  return !!D.job && (LIVE.includes(D.job.status) || D.tab === 'log');
}

// ------------------------------------------------------------------ render
let headEl = null;
let bodyEl = null;

function render() {
  headEl = h('div', { class: 'panel-head' });
  bodyEl = h('div', { class: 'panel-body' });
  if (D.mode === 'page') clear(container(), h('div', { class: 'job-page' }, headEl, bodyEl));
  else clear(container(), headEl, bodyEl);
  renderHeaderOnly();
  if (D.tab === 'overview') renderOverview(bodyEl);
  else if (D.tab === 'log') renderLog(bodyEl);
  else renderFiles(bodyEl);
}

function renderHeaderOnly() {
  const job = D.job;
  if (!headEl || !job) return;
  const puid = state.project.uid;
  const titleEl = h('span', { class: 'grow', style: { minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
    title: 'Click to rename', onclick: () => rename(job) }, job.title);
  const actions = [];
  if (job.status === 'building') {
    actions.push(btn('Queue', () => act(api.queueJob(puid, job.uid), 'Queued'), { cls: 'primary', ic: 'play' }));
    actions.push(btn('Edit', () => { openBuilder({ editing: job }); navigate(`#/p/${puid}/edit/${job.uid}`); }, { ic: 'edit' }));
  }
  if (ACTIVE.includes(job.status) || job.status === 'waiting') {
    actions.push(btn(job.status === 'queued' ? 'Dequeue' : job.status === 'waiting' ? 'Cancel session' : 'Kill', async () => {
      if (job.status !== 'queued' && !(await confirmDialog('Stop job', `Stop ${job.uid} (${job.title})?`, 'Stop', true))) return;
      act(api.killJob(puid, job.uid), 'Stopped');
    }, { cls: 'danger', ic: 'stop' }));
  }
  if (['completed', 'failed', 'killed'].includes(job.status)) {
    actions.push(btn('Clear', async () => {
      if (!(await confirmDialog('Clear job', `Delete all outputs and files of ${job.uid} and return it to building state?`, 'Clear', true))) return;
      act(api.clearJob(puid, job.uid), 'Cleared');
    }, { ic: 'undo' }));
  }
  actions.push(btn('Clone', async () => {
    const c = await guard(api.cloneJob(puid, job.uid), 'Cloned');
    await refreshJobs();
    navigate(jobHref(c.uid));
  }, { ic: 'copy' }));
  const cont = btn('Continue with…', (e) => continueMenu(e.currentTarget, job), { ic: 'next' });
  if (job.status === 'completed' || job.status === 'running' || job.status === 'queued') actions.push(cont);
  actions.push(btn('Delete', async () => {
    const deps = job.dependents || [];
    const msg = deps.length ? `${job.uid} is used by ${deps.join(', ')}. Delete it anyway (their inputs become invalid)?` : `Delete ${job.uid} and its files?`;
    if (!(await confirmDialog('Delete job', msg, 'Delete', true))) return;
    await guard(api.deleteJob(puid, job.uid, deps.length > 0), 'Deleted');
    await refreshJobs();
    navigate(closeHref());
  }, { cls: 'danger', ic: 'trash' }));

  const page = D.mode === 'page';
  clear(headEl,
    h('div', { class: 'panel-title' },
      page ? btn('Jobs', () => navigate(closeHref()), { cls: 'small', ic: 'back', title: 'Back to the job cards (the builder stays open)' }) : null,
      h('span', { class: 'uid' }, job.uid), titleEl, statusChip(job.status),
      page ? null : h('button', { class: 'icon-btn', 'aria-label': 'Close', onclick: () => navigate(closeHref()) }, icon('close'))),
    h('div', { class: 'muted small' }, typeTitle(job.type), job.lane ? ` · lane ${job.lane}` : '', jobDuration(job) ? ` · ${jobDuration(job)}` : ''),
    h('div', { class: 'actions-bar' }, actions),
    h('div', { class: 'tabs', role: 'tablist' }, ['overview', 'log', 'files'].map((t) => h('button', {
      class: D.tab === t ? 'on' : '', role: 'tab', 'aria-selected': D.tab === t ? 'true' : 'false',
      onclick: () => { D.tab = t; if (t === 'log') { D.log = ''; D.logOffset = 0; } render(); if (t === 'log') pollLog(); },
    }, t[0].toUpperCase() + t.slice(1)))));
}

async function act(promise, msg) {
  try { await guard(promise, msg); } catch { return; }
  await refreshJobs();
  await refreshDetail(true);
}

async function rename(job) {
  const title = prompt('Job title', job.title);
  if (title === null) return;
  await act(api.updateJob(state.project.uid, job.uid, { title }), 'Renamed');
}

function continueMenu(anchor, job) {
  const outs = job.status === 'completed' ? job.outputs : (state.types[job.type]?.outputs || []);
  const outTypes = new Set(outs.map((o) => o.type));
  const items = [];
  for (const cat of state.info.categories) {
    const types = state.jobtypes.filter((t) => t.category === cat && t.inputs.some((s) => s.types.some((ty) => outTypes.has(ty))));
    if (!types.length) continue;
    items.push({ category: cat });
    for (const t of types) {
      items.push({ label: t.title, action: () => continueWith(job, t.name) });
    }
  }
  if (!items.length) { toast('No job type consumes these outputs'); return; }
  popupMenu(anchor, items);
}

// Prepare a job of type `typeName` fed by `job`'s outputs. When the job is open next to the builder
// it stays open; if the builder already has that type, the outputs are added to its inputs.
function continueWith(job, typeName) {
  const puid = state.project.uid;
  const title = state.types[typeName].title;
  if (D.mode === 'page' && state.builder?.type === typeName) {
    const changed = builderPrefill(job);
    toast(changed.length ? `${job.uid} → ${changed.length} input(s) of “${title}”` : `“${title}” already uses ${job.uid}'s outputs`,
      changed.length ? 'ok' : 'info', 3000);
    return;
  }
  openBuilder({ type: typeName, prefillFrom: job });
  navigate(D.mode === 'page' ? `#/p/${puid}/new/${job.uid}` : `#/p/${puid}/new`);
  toast(`New “${title}” job prepared from ${job.uid}: check the inputs on the right`, 'ok', 3500);
}

// ---------------------------------------------------------------- overview
export function viewerUrl(items) {
  return `viewer.html#${encodeURIComponent(JSON.stringify({ project: state.project.uid, items }))}`;
}

// Everything of a job the 3D viewer can show (its maps, or its model with the map it was fitted in).
export function jobViewItems(job) {
  return (job.outputs || []).flatMap((o) => outputViewItems(job, o)).filter((it, i, arr) => arr.findIndex((x) => x.path === it.path) === i);
}

function outputViewItems(job, out) {
  const items = [];
  if (out.type === 'model') {
    items.push({ kind: 'model', path: out.path, label: `${job.uid} ${out.label}` });
    const mapIn = job.input_details && (job.input_details.map || job.input_details.half_maps);
    if (mapIn && mapIn.output_info) {
      items.push({ kind: 'map', path: mapIn.output_info.path, label: `${mapIn.job} ${mapIn.output_info.label}` });
    } else {
      // job lists carry the input references only: find the map among the parent's outputs
      const ref = job.inputs && (job.inputs.map || job.inputs.half_maps);
      const parentOut = ref && (state.jobsByUid[ref.job]?.outputs || []).find((o) => o.name === ref.output);
      if (parentOut) items.push({ kind: 'map', path: parentOut.path, label: `${ref.job} ${parentOut.label || parentOut.name}` });
    }
  } else if (out.type === 'locres' && out.meta?.colour_map) {
    // a local resolution map is shown as the colours of the map it describes
    items.push({ kind: 'map', path: out.meta.colour_map, label: `${job.uid} ${out.meta.colour_map.split('/').pop()}`,
      colorBy: out.path, colorRange: out.meta.display_range });
  } else if (['map', 'mask', 'half_maps', 'locres'].includes(out.type)) {
    items.push({ kind: 'map', path: out.path, label: `${job.uid} ${out.label}`, ...(out.type === 'mask' ? { otype: 'mask' } : {}) });
  }
  return items;
}

function renderOverview(body) {
  const job = D.job;
  const puid = state.project.uid;
  if (job.error) body.appendChild(h('div', { class: 'alert error' }, job.error));
  if (ACTIVE.includes(job.status)) {
    body.appendChild(h('div', { class: 'alert info' }, job.message || job.status,
      job.status === 'running' ? h('div', { class: 'progress', style: { marginTop: '6px' } }, h('i', { style: { width: `${Math.round((job.progress || 0) * 100)}%` } })) : null));
  }
  if (job.status === 'building' && job.problems && job.problems.length) {
    body.appendChild(h('div', { class: 'alert warn' }, 'Before queueing:', h('ul', { class: 'problems' }, job.problems.map((p) => h('li', {}, p)))));
  }
  if (job.status === 'waiting') body.appendChild(interactivePanel(job));

  if (job.highlights && job.highlights.length) {
    body.appendChild(h('div', { class: 'section' }, h('div', { class: 'tiles' }, job.highlights.map((hl) => h('div', { class: 'tile' },
      h('div', { class: 'label' }, hl.label), h('div', { class: 'value' }, String(hl.value)), hl.status ? statusMark(hl.status) : null)))));
  }

  D.linkType = D.mode === 'page' ? state.builder?.type : null;
  if (job.outputs && job.outputs.length) {
    const linking = !!D.linkType;
    const rows = job.outputs.map((o) => {
      const items = outputViewItems(job, o);
      const ref = { job: job.uid, output: o.name, type: o.type };
      const slots = linking ? builderSlotsFor(o.type) : [];
      return h('div', {
        class: `output-row ${slots.length ? 'linkable' : ''}`, draggable: slots.length ? 'true' : null,
        title: slots.length ? `Drag onto an input of the builder: ${slots.map((sl) => sl.label).join(', ')}` : null,
        ondragstart: (e) => {
          if (!slots.length || e.target.closest('a, button')) return;
          e.dataTransfer.setData('application/x-cryoplug-output', JSON.stringify(ref));
          e.dataTransfer.effectAllowed = 'link';
        },
      },
        o.thumbnail ? h('img', { src: api.fileUrl(puid, o.thumbnail), alt: '', loading: 'lazy' })
          : h('div', { class: 'noimg' }, icon({ model: 'model', map: 'map', mask: 'map', half_maps: 'map', report: 'check', fsc: 'graph', restraints: 'file' }[o.type] || 'file')),
        h('div', {},
          h('div', { class: 'row' }, h('b', {}, o.label || o.name), h('span', { class: `tag type-${o.type}` }, state.info.data_types[o.type] || o.type),
            h('span', { class: 'muted small' }, o.name)),
          o.meta && (o.meta.resolution || o.meta.pixel_size) ? h('div', { class: 'muted small' },
            [o.meta.resolution ? `${(+o.meta.resolution).toFixed(2)} Å` : null, o.meta.pixel_size ? `${o.meta.pixel_size} Å/px` : null,
              o.meta.box ? `box ${o.meta.box.join('×')}` : null].filter(Boolean).join(' · ')) : null,
          h('div', { class: 'files' }, (o.files || [o.path]).map((f) => h('div', { class: 'row' },
            h('a', { href: api.fileUrl(puid, f, true), title: 'Download' }, f.split('/').pop()),
            h('button', { class: 'icon-btn', title: 'Copy full path', 'aria-label': 'Copy path', onclick: () => copyText(`${state.project.dir}/${f}`) }, icon('copy'))))),
          h('div', { class: 'actions' },
            slots.length ? btn('Use as input', () => {
              const slot = builderConnect(ref);
              if (slot) toast(`${job.uid} ${o.name} → '${slot.label}'`, 'ok', 2500);
            }, { cls: 'small', ic: 'next', title: `Connect to the builder (${slots.map((sl) => sl.label).join(' / ')})` }) : null,
            items.length ? h('a', { class: 'btn small', href: viewerUrl(items), target: '_blank', rel: 'noopener' }, icon('eye'), 'View 3D') : null)));
    });
    const viewAll = jobViewItems(job);
    body.appendChild(h('div', { class: 'section' }, h('h4', {}, 'Outputs'),
      linking && rows.some((r) => r.classList.contains('linkable'))
        ? h('div', { class: 'muted small', style: { marginBottom: '6px' } }, 'Drag an output onto an input of the builder on the right, or use “Use as input”.') : null,
      viewAll.length > 1 ? h('a', { class: 'btn small', href: viewerUrl(viewAll), target: '_blank', rel: 'noopener', style: { marginBottom: '6px' } }, icon('eye'), 'View all in 3D') : null,
      h('div', { class: 'box' }, rows)));
  }

  for (const sec of job.report?.sections || []) body.appendChild(reportSection(sec, puid));

  const inputs = Object.entries(job.input_details || {});
  if (inputs.length) {
    body.appendChild(h('div', { class: 'section' }, h('h4', {}, 'Inputs'), h('div', { class: 'box kv' },
      inputs.flatMap(([slot, ref]) => [h('span', {}, slot),
        h('span', {}, h('a', { href: jobHref(ref.job) }, `${ref.job}`), ` ${ref.title} → ${ref.output} `, statusChip(ref.status))]))));
  }

  const t = state.types[job.type];
  if (t && t.params.length) {
    body.appendChild(h('div', { class: 'section' }, h('h4', {}, 'Parameters'), h('div', { class: 'box kv' },
      t.params.flatMap((p) => {
        const v = job.params[p.name];
        const nd = JSON.stringify(v) !== JSON.stringify(p.default);
        const shown = v === '' || v === null || v === undefined ? '—' : (p.name === 'resolution' && Number(v) === 0 ? 'auto' : String(v));
        return [h('span', {}, p.label), h('span', { class: nd ? 'nondefault mono' : 'mono' }, shown.length > 300 ? `${shown.slice(0, 300)}…` : shown)];
      }))));
  }

  if (t && t.help) {
    const canContinue = job.status === 'completed';
    body.appendChild(h('div', { class: 'section' }, h('h4', {}, 'À propos de ce job'),
      h('details', { class: 'help-details box' }, h('summary', {}, t.help.purpose),
        helpSheet(t, { showPurpose: false, onNext: canContinue ? (n) => continueWith(job, n) : null }))));
  }

  const notes = h('textarea', { rows: 3, placeholder: 'Notes for this job (saved automatically)…', onchange: async (e) => {
    try { await guard(api.updateJob(puid, job.uid, { notes: e.target.value }), 'Notes saved'); } catch { /* shown */ }
  } });
  notes.value = job.notes || '';
  body.appendChild(h('div', { class: 'section' }, h('h4', {}, 'Notes'), notes));

  body.appendChild(h('div', { class: 'section' }, h('h4', {}, 'Details'), h('div', { class: 'box kv small' },
    'Job type', job.type, 'Created', fmtTime(job.created_at), 'Started', fmtTime(job.started_at) || '—', 'Ended', fmtTime(job.ended_at) || '—',
    'GPUs', (job.gpus || []).join(', ') || '—', 'Process', job.pid ? String(job.pid) : (job.cluster_job_id ? `cluster job ${job.cluster_job_id}` : '—'),
    'Directory', h('span', { class: 'row' }, h('span', { class: 'mono', style: { wordBreak: 'break-all' } }, job.job_dir),
      h('button', { class: 'icon-btn', 'aria-label': 'Copy path', onclick: () => copyText(job.job_dir) }, icon('copy'))))));
}

function reportSection(sec, puid) {
  const wrap = h('div', { class: 'section' });
  if (sec.kind === 'metrics') {
    wrap.append(h('h4', {}, sec.title), h('div', { class: 'box' }, h('table', { class: 'data' },
      h('thead', {}, h('tr', {}, h('th', {}, 'Metric'), h('th', {}, 'Value'), h('th', {}, 'Assessment'), h('th', {}, 'Target'))),
      h('tbody', {}, sec.metrics.map((m) => h('tr', {}, h('td', {}, m.label), h('td', {}, h('b', {}, String(m.value))),
        h('td', {}, statusMark(m.status) || ''), h('td', { class: 'muted' }, m.target || '')))))));
  } else if (sec.kind === 'plot') {
    wrap.append(h('div', { class: 'box' }, lineChart(sec)));
  } else if (sec.kind === 'heatmap') {
    wrap.append(h('div', { class: 'box' }, heatmap(sec)));
  } else if (sec.kind === 'table') {
    const statusCol = sec.columns.indexOf('Status');
    wrap.append(h('h4', {}, sec.title), h('div', { class: 'box table-scroll' }, h('table', { class: 'data' },
      h('thead', {}, h('tr', {}, sec.columns.map((c) => h('th', {}, c)))),
      h('tbody', {}, sec.rows.map((r) => h('tr', {}, r.map((cell, i) => h('td', {},
        i === statusCol ? statusMark(String(cell).toLowerCase()) : String(cell)))))))));
  } else if (sec.kind === 'text') {
    wrap.append(h('h4', {}, sec.title), h('div', { class: sec.mono ? 'box mono' : 'box', style: { whiteSpace: 'pre-wrap', overflowX: 'auto' } }, sec.text));
  } else if (sec.kind === 'image') {
    wrap.append(h('h4', {}, sec.title), h('a', { href: api.fileUrl(puid, sec.path), target: '_blank', rel: 'noopener' },
      h('img', { src: api.fileUrl(puid, sec.path), alt: sec.title, style: { maxWidth: '100%', borderRadius: '8px' } })));
  }
  return wrap;
}

function interactivePanel(job) {
  const puid = state.project.uid;
  const display = state.info.display;
  const fileInput = h('input', { type: 'file', accept: '.pdb,.cif,.mmcif,.ent', hidden: true, onchange: async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    await act(api.upload(puid, job.uid, f), `Uploaded ${f.name}`);
  } });
  const cands = job.candidates || [];
  return h('div', { class: 'alert wait' },
    h('b', {}, 'Interactive session ready'),
    h('div', { class: 'small', style: { margin: '4px 0 8px' } },
      'Open the session on the server display, or download the bundle and run it on your workstation. Save the rebuilt model ',
      'into the job folder (or upload it), then finish the job to register it as output.'),
    h('div', { class: 'row wrap' },
      btn(display ? `Open on server (${display})` : 'Open on server', async () => {
        try { const r = await guard(api.launch(puid, job.uid)); toast(`Started ${r.command[0]} on ${r.display}`, 'ok'); } catch { /* shown */ }
      }, { cls: 'primary', ic: 'monitor', title: display ? '' : 'No display configured on the server' }),
      h('a', { class: 'btn', href: api.bundleUrl(puid, job.uid) }, icon('download'), 'Download session bundle'),
      btn('Upload model…', () => fileInput.click(), { ic: 'upload' }), fileInput),
    h('div', { class: 'section' }, h('h4', {}, 'Saved models in the job folder'),
      cands.length ? h('table', { class: 'data' }, h('tbody', {}, cands.map((c, i) => h('tr', {},
        h('td', { class: 'mono' }, c.path), h('td', { class: 'muted' }, fmtSize(c.size)), h('td', { class: 'muted' }, ago(c.mtime)),
        h('td', {}, btn(i === 0 ? 'Finish with this model' : 'Use this one', () => act(api.finish(puid, job.uid, c.path), 'Session finished'),
          { cls: i === 0 ? 'primary small' : 'small', ic: 'check' }))))))
        : h('div', { class: 'muted small' }, 'Nothing saved yet. In ChimeraX: save isolde_model.cif models #1 — in Coot: File › Save Coordinates.')),
    h('div', { class: 'small muted', style: { marginTop: '6px' } }, 'Folder: ', h('span', { class: 'mono' }, job.job_dir),
      h('button', { class: 'icon-btn', 'aria-label': 'Copy path', onclick: () => copyText(job.job_dir) }, icon('copy'))));
}

// ---------------------------------------------------------------------- log
let logPre = null;

function renderLog(body) {
  logPre = h('pre', { class: 'log', 'aria-live': 'off' });
  const auto = h('input', { type: 'checkbox', id: 'log-auto', checked: D.autoscroll, onchange: (e) => { D.autoscroll = e.target.checked; } });
  clear(body,
    h('div', { class: 'row', style: { marginBottom: '8px' } },
      h('label', { class: 'row small', for: 'log-auto' }, auto, 'Follow'), h('span', { class: 'grow' }),
      h('a', { class: 'btn small', href: api.fileUrl(state.project.uid, `${D.uid}/job.log`), target: '_blank', rel: 'noopener' }, 'Open raw log')),
    logPre);
  paintLog(D.log, true);
}

function paintLog(text, reset) {
  if (!logPre) return;
  if (reset) logPre.replaceChildren();
  const frag = document.createDocumentFragment();
  for (const line of text.split(/(?<=\n)/)) {
    if (!line) continue;
    let cls = '';
    if (/ERROR|Traceback|exited with code/.test(line)) cls = 'err';
    else if (/WARNING/.test(line)) cls = 'warn';
    else if (/^\[[^\]]+\] \$ /.test(line)) cls = 'cmd';
    frag.appendChild(cls ? h('span', { class: cls }, line) : document.createTextNode(line));
  }
  logPre.appendChild(frag);
  if (D.autoscroll) logPre.scrollTop = logPre.scrollHeight;
}

async function pollLog() {
  if (!D.uid || D.tab !== 'log') return;
  const res = await api.log(state.project.uid, D.uid, D.logOffset);
  if (res.offset < D.logOffset || (res.truncated && D.logOffset === 0)) {
    D.log = (res.truncated ? '… (beginning truncated, open the raw log)\n' : '') + res.text;
    D.logOffset = res.offset;
    paintLog(D.log, true);
  } else if (res.text) {
    D.log += res.text;
    D.logOffset = res.offset;
    paintLog(res.text, false);
  }
}

// -------------------------------------------------------------------- files
async function renderFiles(body) {
  const puid = state.project.uid;
  let entries;
  try { entries = await api.files(puid, D.uid, D.filesSub); } catch (e) { clear(body, h('div', { class: 'muted' }, e.message)); return; }
  const parts = D.filesSub ? D.filesSub.split('/') : [];
  const crumbs = [h('a', { href: '#', onclick: (e) => { e.preventDefault(); D.filesSub = ''; render(); } }, D.uid)];
  parts.forEach((part, i) => {
    crumbs.push(' / ', h('a', { href: '#', onclick: (e) => { e.preventDefault(); D.filesSub = parts.slice(0, i + 1).join('/'); render(); } }, part));
  });
  const rows = entries.map((e) => {
    const name = e.is_dir
      ? h('a', { href: '#', onclick: (ev) => { ev.preventDefault(); D.filesSub = [D.filesSub, e.name].filter(Boolean).join('/'); render(); } }, `${e.name}/`)
      : h('a', { href: api.fileUrl(puid, e.path), target: '_blank', rel: 'noopener' }, e.name);
    const dl = e.is_dir ? null : h('a', { href: api.fileUrl(puid, e.path, true), title: 'Download', 'aria-label': `Download ${e.name}` }, icon('download'));
    return h('tr', {},
      h('td', {}, name, e.symlink ? h('span', { class: 'muted small' }, ' (link)') : null),
      h('td', { class: 'muted' }, e.is_dir ? '' : fmtSize(e.size)),
      h('td', { class: 'muted' }, ago(e.mtime)),
      h('td', {}, dl));
  });
  clear(body, h('div', { class: 'mono small', style: { marginBottom: '8px' } }, crumbs),
    h('table', { class: 'data' }, h('tbody', {}, rows)));
}
