// Shared application state and navigation helpers.
import { api } from './api.js';

export const state = {
  info: null,
  jobtypes: [],
  types: {}, // name -> schema
  projects: [],
  project: null, // current project record
  jobs: [], // jobs of the current project
  jobsByUid: {},
  route: { page: 'projects' },
  view: localStorage.getItem('cryoplug.view') || 'cards',
  filter: { text: '', status: '', category: '' },
  builder: null, // { type, editing, inputs, params, title, lane }
  selection: new Set(), // job uids picked with Ctrl/Shift-click for bulk actions
  selectionAnchor: null,
  cardOrder: [], // uids in the order shown, for Shift-click ranges
  listeners: new Set(),
};

export function navigate(hash) {
  if (location.hash !== hash) location.hash = hash;
}

// Route of the job builder currently open (new or edit), without an inspected job.
export function builderHref() {
  const r = state.route;
  const base = `#/p/${state.project.uid}`;
  if (state.builder && r.panel === 'new') return `${base}/new`;
  if (state.builder && r.panel === 'edit') return `${base}/edit/${r.juid}`;
  return base;
}

// Where clicking a job leads: while the builder is open (as in CryoSPARC) the job opens
// in the main area and the builder stays on the right, so its outputs can be dragged in.
export function jobHref(uid) {
  return `${builderHref()}/${uid}`;
}

export function onJobsChanged(fn) {
  state.listeners.add(fn);
  return () => state.listeners.delete(fn);
}

// False when the user must first replace a temporary password (nothing else is reachable until then).
export async function loadStatic() {
  state.info = await api.info();
  if (state.info.user?.must_change_password) return false;
  setJobtypes(await api.jobtypes());
  return true;
}

export function setJobtypes(jobtypes) {
  state.jobtypes = jobtypes;
  state.types = Object.fromEntries(jobtypes.map((t) => [t.name, t]));
}

export async function refreshJobs() {
  if (!state.project) return;
  const jobs = await api.jobs(state.project.uid);
  state.jobs = jobs;
  state.jobsByUid = Object.fromEntries(jobs.map((j) => [j.uid, j]));
  for (const fn of state.listeners) {
    try { fn(jobs); } catch (e) { console.error(e); }
  }
}

// With accounts, users who are not administrators cannot run arbitrary code (custom commands, ChimeraX commands).
export function restricted() {
  return state.info?.auth === 'users' && state.info.user?.role !== 'admin';
}

export function typeTitle(name) {
  return state.types[name]?.title || name;
}

// Outputs of project jobs compatible with an input slot (actual outputs, or declared ones for pending jobs).
export function compatibleOutputs(slot, excludeUid = null) {
  const out = [];
  const jobs = [...state.jobs].sort((a, b) => b.num - a.num);
  for (const job of jobs) {
    if (job.uid === excludeUid || ['failed', 'killed'].includes(job.status)) continue;
    let outputs = job.outputs || [];
    let pending = false;
    if (job.status !== 'completed') {
      outputs = (state.types[job.type]?.outputs || []).map((o) => ({ ...o }));
      pending = true;
    }
    for (const o of outputs) {
      if (slot.types.includes(o.type)) out.push({ job, output: o, pending });
    }
  }
  return out;
}
