// Workspaces (named groups of the jobs of a project, as in CryoSPARC) and job tags.
import { api, setWorkspace } from './api.js';
import { refreshJobs, state } from './state.js';
import { btn, clear, confirmDialog, guard, h, icon, modal, popupMenu, toast } from './ui.js';

const storeKey = (puid) => `cryoplug.workspace.${puid}`;
const stored = (key) => { try { return localStorage.getItem(key) || ''; } catch { return ''; } };
const store = (key, value) => { try { if (value) localStorage.setItem(key, value); else localStorage.removeItem(key); } catch { /* unavailable */ } };

// The workspaces of the open project, and the one open (remembered per project in this browser).
export async function loadWorkspaces() {
  const puid = state.project?.uid;
  if (!puid) return;
  try {
    state.workspaces = await api.workspaces(puid);
  } catch {
    state.workspaces = [];
  }
  if (state.project?.uid !== puid) return;
  const wanted = state.workspace || stored(storeKey(puid));
  state.workspace = state.workspaces.some((w) => w.uid === wanted) ? wanted : '';
  setWorkspace(state.workspace);
}

export function leaveWorkspaces() {
  state.workspaces = [];
  state.workspace = '';
  setWorkspace('');
}

export function currentWorkspace() {
  return state.workspaces.find((w) => w.uid === state.workspace) || null;
}

export const inWorkspace = (job) => !state.workspace || (job.workspaces || []).includes(state.workspace);

const announce = () => document.dispatchEvent(new CustomEvent('cryoplug-workspace')); // the breadcrumb follows

export function chooseWorkspace(uid, rerender) {
  state.workspace = uid || '';
  store(storeKey(state.project.uid), state.workspace);
  setWorkspace(state.workspace);
  state.selection.clear();
  rerender();
  announce();
}

const countIn = (uid) => state.jobs.filter((j) => (j.workspaces || []).includes(uid)).length;

// The workspace menu of the project toolbar: all the jobs, each workspace, then new / rename / delete.
export function workspaceButton(rerender) {
  const ws = currentWorkspace();
  return h('button', {
    class: `btn ws-btn${ws ? ' on' : ''}`, type: 'button', 'aria-haspopup': 'menu',
    title: ws ? `Workspace ${ws.uid}: only its jobs are shown, new jobs go into it` : 'Workspaces: groups of jobs of this project (as in CryoSPARC)',
    onclick: (e) => {
      const items = [
        { label: `All jobs (${state.jobs.length})`, ic: ws ? 'blank' : 'check', action: () => chooseWorkspace('', rerender) },
        ...state.workspaces.map((w) => ({ label: `${w.uid} · ${w.title} (${countIn(w.uid)})`, ic: w.uid === state.workspace ? 'check' : 'blank',
          action: () => chooseWorkspace(w.uid, rerender) })),
        { separator: true },
        { label: 'New workspace…', ic: 'plus', action: () => editWorkspace(null, rerender) },
      ];
      if (ws) {
        items.push({ label: `Rename or describe ${ws.uid}…`, ic: 'edit', action: () => editWorkspace(ws, rerender) });
        items.push({ label: `Delete ${ws.uid}…`, ic: 'trash', danger: true, action: () => deleteWorkspace(ws, rerender) });
      }
      popupMenu(e.currentTarget, items);
    },
  }, icon('workspace'), h('span', { class: 'lbl' }, ws ? `${ws.uid} ${ws.title}` : 'All jobs'), icon('chevron', 'chev'));
}

// New workspace (with `jobs` in it, when made from a selection) or new title and description of one.
export function editWorkspace(ws, rerender, jobs = []) {
  const title = h('input', { type: 'text', value: ws?.title || '', placeholder: 'e.g. 2D cleaning, Dataset 2, Focused refinements',
    'aria-label': 'Workspace title', maxlength: 120 });
  const desc = h('textarea', { rows: 3, placeholder: 'What these jobs are for (optional)', 'aria-label': 'Description' });
  desc.value = ws?.description || '';
  const save = async () => {
    const body = { title: title.value.trim(), description: desc.value.trim() };
    if (!body.title) { title.focus(); return; }
    try {
      const puid = state.project.uid;
      const res = ws ? await guard(api.updateWorkspace(puid, ws.uid, body), 'Workspace saved')
        : await guard(api.createWorkspace(puid, { ...body, jobs }), 'Workspace created');
      m.close();
      await loadWorkspaces();
      if (!ws) {
        await refreshJobs();
        chooseWorkspace(res.uid, rerender);
      } else {
        rerender();
        announce();
      }
    } catch { /* shown */ }
  };
  title.addEventListener('keydown', (e) => { if (e.key === 'Enter') save(); });
  const m = modal({
    title: ws ? `Workspace ${ws.uid}` : 'New workspace',
    body: h('div', { class: 'ws-form' },
      h('label', {}, 'Title', title), h('label', {}, 'Description', desc),
      !ws ? h('p', { class: 'muted small' }, jobs.length ? `${jobs.length} selected job${jobs.length > 1 ? 's' : ''} go into it. `
        : '', 'The jobs created while it is open go into it; a job can be in several workspaces.') : null),
    footer: [btn('Cancel', () => m.close()), btn(ws ? 'Save' : 'Create', save, { cls: 'primary' })],
  });
}

async function deleteWorkspace(ws, rerender) {
  if (!(await confirmDialog('Delete workspace', `Delete ${ws.uid} (${ws.title})?\nIts ${countIn(ws.uid)} jobs stay in the project.`, 'Delete', true))) return;
  try {
    await guard(api.deleteWorkspace(state.project.uid, ws.uid), 'Workspace deleted');
  } catch { return; }
  await loadWorkspaces();
  await refreshJobs();
  chooseWorkspace('', rerender);
}

// Which workspaces the jobs are in: a box per workspace (half-ticked when only some of the jobs are in it).
export function workspacesDialog(jobs, rerender) {
  if (!state.workspaces.length) {
    editWorkspace(null, rerender, jobs.map((j) => j.uid));
    return;
  }
  const boxes = state.workspaces.map((w) => {
    const n = jobs.filter((j) => (j.workspaces || []).includes(w.uid)).length;
    const cb = h('input', { type: 'checkbox', checked: n === jobs.length });
    cb.indeterminate = n > 0 && n < jobs.length;
    return { w, cb, before: cb.checked, mixed: cb.indeterminate };
  });
  const apply = async () => {
    const puid = state.project.uid;
    const uids = jobs.map((j) => j.uid);
    try {
      for (const b of boxes) {
        if (b.cb.indeterminate) continue; // left half-ticked: unchanged
        if (b.cb.checked && (!b.before || b.mixed)) await api.workspaceJobs(puid, b.w.uid, { add: uids });
        if (!b.cb.checked && (b.before || b.mixed)) await api.workspaceJobs(puid, b.w.uid, { remove: uids });
      }
      toast('Workspaces updated', 'ok');
    } catch (err) {
      toast(err.message, 'error');
    }
    m.close();
    await refreshJobs();
    await loadWorkspaces();
    rerender();
  };
  const m = modal({
    title: jobs.length === 1 ? `Workspaces of ${jobs[0].uid}` : `Workspaces of ${jobs.length} jobs`,
    body: h('div', { class: 'ws-list' },
      boxes.map((b) => h('label', { class: 'ws-row' }, b.cb, h('b', {}, b.w.uid), h('span', { class: 'grow' }, b.w.title),
        h('span', { class: 'muted small' }, `${countIn(b.w.uid)} jobs`))),
      h('button', { class: 'btn small', type: 'button', onclick: () => { m.close(); editWorkspace(null, rerender, jobs.map((j) => j.uid)); } },
        icon('plus'), 'New workspace with ', jobs.length === 1 ? 'this job' : 'these jobs')),
    footer: [btn('Cancel', () => m.close()), btn('Apply', apply, { cls: 'primary' })],
  });
}

// ------------------------------------------------------------------ tags
const TAG_COLOURS = 8;
export function tagColour(tag) {
  let x = 0;
  for (const ch of tag.toLowerCase()) x = (x * 31 + ch.codePointAt(0)) >>> 0;
  return `var(--series-${(x % TAG_COLOURS) + 1})`;
}

export function tagChip(tag, { onClick = null, title = null, removable = false } = {}) {
  return h(onClick ? 'button' : 'span', {
    class: `tag-chip${removable ? ' removable' : ''}`, type: onClick ? 'button' : null, style: { '--tc': tagColour(tag) },
    title: title || (onClick ? `Show only the jobs tagged “${tag}”` : tag),
    onclick: onClick ? (e) => { e.stopPropagation(); onClick(tag); } : null,
  }, h('i', { 'aria-hidden': 'true' }), tag, removable ? icon('x') : null);
}

export function projectTags() {
  const seen = new Map();
  for (const j of state.jobs) for (const t of j.tags || []) seen.set(t.toLowerCase(), t);
  return [...seen.values()].sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }));
}

// Tags of one job (its list edited) or of several (tags added to all, or taken from all).
export function tagsDialog(jobs, rerender) {
  const puid = state.project.uid;
  const single = jobs.length === 1;
  let chosen = single ? [...(jobs[0].tags || [])] : [];
  const removed = new Set();
  const input = h('input', { type: 'text', placeholder: 'New tag, then Enter (e.g. best, to check, dataset 2)', maxlength: 40, 'aria-label': 'New tag' });
  const chosenBox = h('div', { class: 'tag-box' });
  const known = h('div', { class: 'tag-box' });
  const has = (tag) => chosen.some((t) => t.toLowerCase() === tag.toLowerCase());
  const paint = () => {
    clear(chosenBox, chosen.length ? chosen.map((t) => tagChip(t, { removable: true, title: 'Remove', onClick: () => { chosen = chosen.filter((x) => x !== t); paint(); } }))
      : h('span', { class: 'muted small' }, single ? 'No tags' : 'Tags to add to every selected job'));
    const others = projectTags().filter((t) => !has(t));
    clear(known, others.length ? others.map((t) => tagChip(t, { title: 'Add', onClick: () => { chosen.push(t); removed.delete(t.toLowerCase()); paint(); } }))
      : h('span', { class: 'muted small' }, 'No other tags in this project yet'));
  };
  const addTyped = () => {
    const tag = input.value.replace(/\s+/g, ' ').trim();
    if (tag && !has(tag)) chosen.push(tag);
    input.value = '';
    paint();
  };
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); addTyped(); } });
  // several jobs: the tags common to all can be taken away
  const common = single ? [] : projectTags().filter((t) => jobs.every((j) => (j.tags || []).some((x) => x.toLowerCase() === t.toLowerCase())));
  const removeBox = h('div', { class: 'tag-box' });
  const paintRemove = () => clear(removeBox, common.length ? common.map((t) => {
    const off = removed.has(t.toLowerCase());
    const chip = tagChip(t, { removable: !off, title: off ? 'Keep' : 'Take away from every selected job',
      onClick: () => { if (off) removed.delete(t.toLowerCase()); else removed.add(t.toLowerCase()); paintRemove(); } });
    if (off) chip.classList.add('off');
    return chip;
  }) : h('span', { class: 'muted small' }, 'No tag common to all of them'));
  const save = async () => {
    addTyped();
    try {
      for (const j of jobs) {
        const tags = single ? chosen : [...(j.tags || []).filter((t) => !removed.has(t.toLowerCase())),
          ...chosen.filter((t) => !(j.tags || []).some((x) => x.toLowerCase() === t.toLowerCase()))];
        await api.updateJob(puid, j.uid, { tags });
      }
      toast('Tags saved', 'ok');
    } catch (err) {
      toast(err.message, 'error');
    }
    m.close();
    await refreshJobs();
    rerender();
  };
  paint();
  paintRemove();
  const m = modal({
    title: single ? `Tags of ${jobs[0].uid}` : `Tags of ${jobs.length} jobs`,
    body: h('div', { class: 'ws-form' },
      h('label', {}, single ? 'Tags' : 'Add', chosenBox, input),
      single ? null : h('label', {}, 'Take away', removeBox),
      h('div', {}, h('div', { class: 'muted small', style: { marginBottom: '4px' } }, 'Tags of the project (click to add)'), known)),
    footer: [btn('Cancel', () => m.close()), btn('Save', save, { cls: 'primary' })],
  });
}
