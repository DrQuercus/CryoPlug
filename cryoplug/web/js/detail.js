// Job details: overview, report, outputs, interactive session controls, live log and files.
// Shown in the right panel, or in the main area while the job builder occupies the panel
// (as in CryoSPARC), where its outputs can be dragged onto the builder's inputs.
import { api } from './api.js';
import { builderConnect, builderPrefill, builderSetParams, builderSlotsFor, openBuilder } from './builder.js';
import { helpSheet } from './help.js';
import { latentExplorer } from './latent.js';
import { heatmap, lineChart } from './plots.js';
import { builderHref, jobHref, navigate, refreshJobs, restricted, state, typeTitle } from './state.js';
import {
  ACTIVE, ago, btn, clear, confirmDialog, copyText, fmtSize, fmtTime, guard, h, icon, jobDuration, LIVE, popupMenu,
  statusChip, statusMark, toast,
} from './ui.js';

const D = { puid: null, uid: null, job: null, mode: 'panel', tab: 'overview', log: '', logOffset: 0, filesSub: '', autoscroll: true, busy: false, again: false };
const container = () => document.getElementById(D.mode === 'page' ? 'content' : 'panel');

// mode: 'panel' (right side) or 'page' (main area, next to the job builder).
export function openDetail(uid, mode = 'panel') {
  const puid = state.project?.uid || null;
  if (D.uid !== uid || D.mode !== mode || D.puid !== puid) {
    Object.assign(D, { puid, uid, mode, job: null, tab: 'overview', log: '', logOffset: 0, filesSub: '' });
    container().scrollTop = 0;
    renderLoading();
  }
  container().hidden = false;
  return refreshDetail(true);
}

// Shown at once when another job is opened, until its details arrive (never the previous job's).
function renderLoading() {
  headEl = h('div', { class: 'panel-head' }, h('div', { class: 'panel-title' }, h('span', { class: 'uid' }, D.uid),
    h('span', { class: 'grow muted' }, 'Loading…'),
    D.mode === 'page' ? null : h('button', { class: 'icon-btn', 'aria-label': 'Close', onclick: () => navigate(closeHref()) }, icon('close'))));
  bodyEl = h('div', { class: 'panel-body' }, h('div', { class: 'skeleton' }), h('div', { class: 'skeleton short' }));
  if (D.mode === 'page') clear(container(), h('div', { class: 'job-page' }, headEl, bodyEl));
  else clear(container(), headEl, bodyEl);
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
  if (!D.uid || !state.project) return;
  if (D.busy) { // the latest request wins: refresh again once the one in flight is back
    D.again = D.again === true || force ? true : 'soft';
    return;
  }
  const puid = state.project.uid;
  const uid = D.uid;
  D.busy = true;
  try {
    const job = await api.job(puid, uid);
    if (D.uid !== uid || state.project?.uid !== puid) return; // another job was opened meanwhile
    const changed = force || !D.job || D.job.status !== job.status || JSON.stringify(D.job.outputs) !== JSON.stringify(job.outputs)
      || D.job.progress !== job.progress || D.job.message !== job.message || JSON.stringify(D.job.candidates) !== JSON.stringify(job.candidates)
      || D.job.title !== job.title;
    D.job = job;
    // newer than the job list (the status changed in between): bring the cards and the summary up to date now
    if (state.jobsByUid[uid] && state.jobsByUid[uid].status !== job.status) refreshJobs().catch(() => {});
    if (D.tab === 'log') await pollLog();
    const editing = bodyEl && bodyEl.contains(document.activeElement) && ['TEXTAREA', 'INPUT', 'SELECT'].includes(document.activeElement.tagName);
    if (changed && D.tab !== 'log' && !editing) render();
    else if (changed) renderHeaderOnly();
  } catch (err) {
    if (err.status === 404 && D.uid === uid) { toast(`${uid} no longer exists`, 'error'); navigate(closeHref()); }
  } finally {
    D.busy = false;
    if (D.again) {
      const again = D.again === true;
      D.again = false;
      refreshDetail(again);
    }
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
  const titleEl = h('span', { class: 'grow panel-name', title: 'Click to rename', onclick: () => rename(job) }, job.title);
  // the usual next step as buttons (as in CryoSPARC), everything else in the ⋯ menu
  const shown = [];
  const actions = [];
  const add = (key, el) => { shown.push(key); actions.push(el); };
  if (job.status === 'building') {
    add('queue', btn('Queue', () => jobOps.queue(job), { cls: 'primary', ic: 'play' }));
    add('edit', btn('Edit', () => jobOps.edit(job), { ic: 'edit' }));
  } else if (ACTIVE.includes(job.status) || job.status === 'waiting') {
    add('stop', btn(stopLabel(job), () => jobOps.stop(job), { cls: 'danger', ic: 'stop' }));
  }
  if (['completed', 'running', 'queued'].includes(job.status)) {
    add('continue', btn('Continue with…', (e) => continueMenu(e.currentTarget, job), { cls: job.status === 'completed' ? 'primary' : '', ic: 'next',
      title: 'New job using the outputs of this one' }));
  }
  if (['failed', 'killed'].includes(job.status)) {
    add('clear', btn('Clear', () => jobOps.clear(job), { ic: 'undo', title: 'Delete its outputs and go back to building: fix the parameters, then queue again' }));
    add('clone', btn('Clone', () => jobOps.clone(job), { ic: 'copy', title: 'A copy with the same inputs and parameters' }));
  }
  const view = job.status === 'completed' ? jobViewItems(job) : [];
  if (view.length > 0) add('view', h('a', { class: 'btn', href: viewerUrl(view), target: '_blank', rel: 'noopener', title: 'Open the maps and models of this job in the 3D viewer' }, icon('eye'), 'View 3D'));
  actions.push(h('button', {
    class: 'btn icon-only', type: 'button', title: 'More actions', 'aria-label': `More actions for ${job.uid}`, 'aria-haspopup': 'menu',
    onclick: (e) => popupMenu(e.currentTarget, jobMenuItems(job, e.currentTarget).filter((it) => !shown.includes(it.key))),
  }, icon('more')));

  const page = D.mode === 'page';
  const when = job.ended_at ? `${job.status === 'completed' ? 'finished' : job.status} ${ago(job.ended_at)}` : job.started_at ? `started ${ago(job.started_at)}` : `created ${ago(job.created_at)}`;
  clear(headEl,
    h('div', { class: 'panel-title' },
      page ? btn('Jobs', () => navigate(closeHref()), { cls: 'small', ic: 'back', title: 'Back to the job cards (the builder stays open)' }) : null,
      h('span', { class: 'uid' }, job.uid), titleEl, statusChip(job.status),
      page ? null : h('button', { class: 'icon-btn', 'aria-label': 'Close', onclick: () => navigate(closeHref()) }, icon('close'))),
    h('div', { class: 'panel-sub muted small' }, h('span', {}, typeTitle(job.type)), h('span', {}, when),
      jobDuration(job) ? h('span', { title: 'Run time' }, icon('clock'), jobDuration(job)) : null, job.lane ? h('span', {}, `lane ${job.lane}`) : null,
      job.created_by && state.info.auth === 'users' ? h('span', { title: 'Created by' }, icon('user'), job.created_by) : null),
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

// ------------------------------------------------------------------ actions
// What can be done with a job: buttons of the details header and the ⋯ menu of the job cards.
const stopLabel = (job) => (job.status === 'queued' ? 'Dequeue' : job.status === 'waiting' ? 'Cancel session' : 'Stop');
const dependentsOf = (job) => job.dependents || state.jobs.filter((j) => Object.values(j.inputs || {}).some((r) => r.job === job.uid)).map((j) => j.uid);

const jobOps = {
  queue: (job) => act(api.queueJob(state.project.uid, job.uid), 'Queued'),
  edit: (job) => { openBuilder({ editing: job }); navigate(`#/p/${state.project.uid}/edit/${job.uid}`); },
  async stop(job) {
    if (job.status !== 'queued' && !(await confirmDialog('Stop job', `Stop ${job.uid} (${job.title})?`, 'Stop', true))) return;
    act(api.killJob(state.project.uid, job.uid), 'Stopped');
  },
  async clear(job) {
    if (!(await confirmDialog('Clear job', `Delete all outputs and files of ${job.uid} and return it to building state?`, 'Clear', true))) return;
    act(api.clearJob(state.project.uid, job.uid), 'Cleared');
  },
  async clone(job) {
    const c = await guard(api.cloneJob(state.project.uid, job.uid), 'Cloned');
    await refreshJobs();
    navigate(jobHref(c.uid));
  },
  async remove(job) {
    const deps = dependentsOf(job);
    const msg = deps.length ? `${job.uid} is used by ${deps.join(', ')}. Delete it anyway (their inputs become invalid)?` : `Delete ${job.uid} and its files?`;
    if (!(await confirmDialog('Delete job', msg, 'Delete', true))) return;
    await guard(api.deleteJob(state.project.uid, job.uid, deps.length > 0), 'Deleted');
    await refreshJobs();
    if (D.uid === job.uid) navigate(closeHref());
  },
};

// Items of the ⋯ menu of a job (each with a key, so that the details header can leave out its buttons).
export function jobMenuItems(job, anchor, { withOpen = false } = {}) {
  const items = [];
  if (withOpen) items.push({ key: 'open', label: 'Open', ic: 'next', action: () => navigate(jobHref(job.uid)) });
  if (job.status === 'building') {
    items.push({ key: 'queue', label: 'Queue', ic: 'play', action: () => jobOps.queue(job) });
    items.push({ key: 'edit', label: 'Edit parameters', ic: 'edit', action: () => jobOps.edit(job) });
  }
  if (ACTIVE.includes(job.status) || job.status === 'waiting') items.push({ key: 'stop', label: stopLabel(job), ic: 'stop', action: () => jobOps.stop(job) });
  if (['completed', 'running', 'queued'].includes(job.status)) {
    items.push({ key: 'continue', label: 'Continue with…', ic: 'next', action: () => continueMenu(anchor, job) });
  }
  const view = job.status === 'completed' ? jobViewItems(job) : [];
  if (view.length) items.push({ key: 'view', label: 'View in 3D', ic: 'eye', action: () => window.open(viewerUrl(view), '_blank', 'noopener') });
  items.push({ separator: true });
  items.push({ key: 'clone', label: 'Clone', ic: 'copy', action: () => jobOps.clone(job) });
  if (['completed', 'failed', 'killed'].includes(job.status)) items.push({ key: 'clear', label: 'Clear (back to building)', ic: 'undo', action: () => jobOps.clear(job) });
  items.push({ key: 'path', label: 'Copy folder path', ic: 'folder', action: () => copyText(job.job_dir || `${state.project.dir}/${job.uid}`) });
  items.push({ separator: true });
  items.push({ key: 'delete', label: 'Delete…', ic: 'trash', danger: true, action: () => jobOps.remove(job) });
  return items;
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
    const types = state.jobtypes.filter((t) => t.category === cat && !(t.admin_only && restricted())
      && t.inputs.some((s) => s.types.some((ty) => outTypes.has(ty))));
    if (!types.length) continue;
    items.push({ category: cat });
    for (const t of types) {
      items.push({ label: t.title, action: () => continueWith(job, t.name) });
    }
  }
  if (!items.length) { toast('No job type consumes these outputs'); return; }
  popupMenu(anchor, items);
}

// Prepare a job of type `typeName` fed by `job`'s outputs (and starting from `params`). When the job is open
// next to the builder it stays open; if the builder already has that type, the outputs are added to its inputs.
function continueWith(job, typeName, params = null) {
  const puid = state.project.uid;
  const title = state.types[typeName].title;
  if (D.mode === 'page' && state.builder?.type === typeName) {
    const changed = builderPrefill(job);
    builderSetParams(params);
    toast(changed.length ? `${job.uid} → ${changed.length} input(s) of “${title}”` : `“${title}” ${params ? 'updated' : `already uses ${job.uid}'s outputs`}`,
      changed.length ? 'ok' : 'info', 3000);
    return;
  }
  openBuilder({ type: typeName, prefillFrom: job, params });
  navigate(D.mode === 'page' ? `#/p/${puid}/new/${job.uid}` : `#/p/${puid}/new`);
  toast(`New “${title}” job prepared from ${job.uid}: check the inputs on the right`, 'ok', 3500);
}

// ---------------------------------------------------------------- overview
export function viewerUrl(items) {
  return `viewer.html#${encodeURIComponent(JSON.stringify({ project: state.project.uid, items }))}`;
}

// Everything of a job the 3D viewer can show (its maps, or its model with the map it was fitted in);
// a single volume series (the first: cluster volumes before trajectories), as each one holds many maps.
export function jobViewItems(job) {
  const items = (job.outputs || []).flatMap((o) => outputViewItems(job, o)).filter((it, i, arr) => arr.findIndex((x) => x.path === it.path) === i);
  const firstSeries = items.find((it) => it.series);
  return items.filter((it) => !it.series || it === firstSeries);
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
  } else if ((out.type === 'locres' || out.type === 'variability') && out.meta?.colour_map) {
    // a local resolution or variability map is shown as the colours of the map it describes
    items.push({ kind: 'map', path: out.meta.colour_map, label: `${job.uid} ${out.meta.colour_map.split('/').pop()}`,
      colorBy: out.path, colorRange: out.meta.display_range, ...(out.type === 'variability' ? { colorKind: 'variability' } : {}) });
  } else if (out.type === 'volume_series') {
    const files = out.files || [out.path];
    items.push({ kind: 'map', path: files[0], label: `${job.uid} ${out.label || out.name}`, series: files, labels: out.meta?.frame_labels || [] });
  } else if (['map', 'mask', 'half_maps', 'locres', 'variability'].includes(out.type)) {
    items.push({ kind: 'map', path: out.path, label: `${job.uid} ${out.label}`, ...(out.type === 'mask' ? { otype: 'mask' } : {}) });
  }
  return items;
}

// Files of an output: the first few, the others behind a toggle (a volume series has dozens).
const FILES_SHOWN = 3;
function fileList(files, puid) {
  const line = (f) => h('div', { class: 'row file' },
    h('a', { href: api.fileUrl(puid, f, true), title: `Download ${f}` }, f.split('/').pop()),
    h('button', { class: 'icon-btn', title: 'Copy full path', 'aria-label': `Copy the path of ${f.split('/').pop()}`, onclick: () => copyText(`${state.project.dir}/${f}`) }, icon('copy')));
  if (files.length <= FILES_SHOWN + 1) return h('div', { class: 'files' }, files.map(line));
  const rest = h('div', { class: 'files', hidden: true }, files.slice(FILES_SHOWN).map(line));
  const toggle = h('button', { type: 'button', class: 'link-btn small', 'aria-expanded': 'false', onclick: () => {
    rest.hidden = !rest.hidden;
    toggle.setAttribute('aria-expanded', rest.hidden ? 'false' : 'true');
    toggle.textContent = rest.hidden ? `+ ${files.length - FILES_SHOWN} more files` : 'Show fewer files';
  } }, `+ ${files.length - FILES_SHOWN} more files`);
  return h('div', { class: 'files' }, files.slice(0, FILES_SHOWN).map(line), rest, toggle);
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
          : h('div', { class: 'noimg' }, icon({ model: 'model', map: 'map', mask: 'map', half_maps: 'map', locres: 'map', variability: 'map', volume_series: 'map',
            report: 'check', fsc: 'graph', restraints: 'file', particles: 'grid', latent: 'graph' }[o.type] || 'file')),
        h('div', {},
          h('div', { class: 'row' }, h('b', {}, o.label || o.name), h('span', { class: `tag type-${o.type}` }, state.info.data_types[o.type] || o.type),
            h('span', { class: 'muted small' }, o.name)),
          o.meta && (o.meta.resolution || o.meta.pixel_size || o.meta.frames || o.meta.n_particles) ? h('div', { class: 'muted small' },
            [o.meta.resolution ? `${(+o.meta.resolution).toFixed(2)} Å` : null, o.meta.pixel_size ? `${o.meta.pixel_size} Å/px` : null,
              Array.isArray(o.meta.box) ? `box ${o.meta.box.join('×')}` : (o.meta.box ? `${o.meta.box} px` : null),
              o.type === 'volume_series' && o.meta.frames ? `${o.meta.frames} volumes` : null,
              o.meta.n_particles ? `${Number(o.meta.n_particles).toLocaleString()} particles` : null,
              o.type === 'latent' && o.meta.zdim ? `${o.meta.zdim}-D${o.meta.k ? ` · ${o.meta.k} clusters` : ''}` : null].filter(Boolean).join(' · ')) : null,
          fileList(o.files || [o.path], puid),
          h('div', { class: 'actions' },
            slots.length ? btn('Use as input', () => {
              const slot = builderConnect(ref);
              if (slot) toast(`${job.uid} ${o.name} → '${slot.label}'`, 'ok', 2500);
            }, { cls: 'small', ic: 'next', title: `Connect to the builder (${slots.map((sl) => sl.label).join(' / ')})` }) : null,
            items.length ? h('a', { class: 'btn small', href: viewerUrl(items), target: '_blank', rel: 'noopener' },
              icon(o.type === 'volume_series' ? 'play' : 'eye'), o.type === 'volume_series' ? 'Play in 3D' : 'View 3D') : null)));
    });
    const viewAll = jobViewItems(job);
    body.appendChild(h('div', { class: 'section' }, h('h4', {}, 'Outputs'),
      linking && rows.some((r) => r.classList.contains('linkable'))
        ? h('div', { class: 'muted small', style: { marginBottom: '6px' } }, 'Drag an output onto an input of the builder on the right, or use “Use as input”.') : null,
      viewAll.length > 1 ? h('a', { class: 'btn small', href: viewerUrl(viewAll), target: '_blank', rel: 'noopener', style: { marginBottom: '6px' } }, icon('eye'), 'View all in 3D') : null,
      h('div', { class: 'box' }, rows)));
  }

  for (const sec of job.report?.sections || []) body.appendChild(reportSection(sec, puid, job));

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

function reportSection(sec, puid, job) {
  const wrap = h('div', { class: 'section' });
  if (sec.kind === 'latent') {
    const latent = (job.outputs || []).find((o) => o.type === 'latent');
    wrap.append(h('h4', {}, sec.title), latentExplorer(sec, {
      url: api.fileUrl(puid, sec.path),
      jobUid: job.uid,
      openViewer: (items) => window.open(viewerUrl(items), '_blank', 'noopener'),
      newJob: latent && job.status === 'completed' ? (type, params) => continueWith(job, type, params) : null,
      method: latent?.meta?.method,
      extract: (job.outputs || []).some((o) => o.name === 'kmeans'),
    }));
    return wrap;
  }
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
