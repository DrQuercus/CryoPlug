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
  listeners: new Set(),
};

export function navigate(hash) {
  if (location.hash !== hash) location.hash = hash;
}

export function onJobsChanged(fn) {
  state.listeners.add(fn);
  return () => state.listeners.delete(fn);
}

export async function loadStatic() {
  const [info, jobtypes] = await Promise.all([api.info(), api.jobtypes()]);
  state.info = info;
  setJobtypes(jobtypes);
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
