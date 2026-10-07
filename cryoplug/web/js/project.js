// Project page: job cards (CryoSPARC-like), pipeline graph, filters and workflow templates.
import { api } from './api.js';
import { openBuilder, paramField } from './builder.js';
import { detailUid, jobViewItems, viewerUrl } from './detail.js';
import { jobHref, navigate, refreshJobs, state, typeTitle } from './state.js';
import { ACTIVE, btn, clear, confirmDialog, guard, h, icon, jobDuration, modal, s, statusChip, toast } from './ui.js';

const STATUSES = ['building', 'queued', 'launched', 'running', 'waiting', 'completed', 'failed', 'killed'];

export function renderProject(content) {
  const p = state.project;
  const search = h('input', { type: 'search', placeholder: 'Filter jobs…', value: state.filter.text, 'aria-label': 'Filter jobs',
    oninput: (e) => { state.filter.text = e.target.value; renderJobs(); } });
  const statusSel = h('select', { 'aria-label': 'Status filter', onchange: (e) => { state.filter.status = e.target.value; renderJobs(); } },
    h('option', { value: '' }, 'All statuses'), STATUSES.map((st) => h('option', { value: st, selected: state.filter.status === st }, st)));
  const catSel = h('select', { 'aria-label': 'Category filter', onchange: (e) => { state.filter.category = e.target.value; renderJobs(); } },
    h('option', { value: '' }, 'All categories'), state.info.categories.map((c) => h('option', { value: c, selected: state.filter.category === c }, c)));
  const seg = h('div', { class: 'seg', role: 'group', 'aria-label': 'View' },
    ['cards', 'graph'].map((v) => h('button', { class: state.view === v ? 'on' : '', type: 'button', 'aria-pressed': state.view === v ? 'true' : 'false',
      onclick: () => { state.view = v; localStorage.setItem('cryoplug.view', v); renderProject(content); } }, icon(v === 'cards' ? 'grid' : 'graph'), ` ${v[0].toUpperCase()}${v.slice(1)}`)));
  clear(content,
    h('div', { class: 'toolbar' },
      btn('New job', () => { openBuilder(); navigate(`#/p/${p.uid}/new`); }, { cls: 'primary', ic: 'plus' }),
      btn('Workflows', () => workflowDialog(), { ic: 'flow' }),
      h('span', { style: { width: '8px' } }), search, statusSel, catSel, h('span', { class: 'grow' }), seg),
    p.description ? h('p', { class: 'muted', style: { margin: '-4px 0 12px' } }, p.description) : null,
    h('div', { id: 'jobs-area' }));
  renderJobs();
}

function filtered() {
  const f = state.filter;
  const q = f.text.trim().toLowerCase();
  return state.jobs.filter((j) => (!f.status || j.status === f.status)
    && (!f.category || state.types[j.type]?.category === f.category)
    && (!q || `${j.uid} ${j.title} ${j.type} ${typeTitle(j.type)} ${j.notes || ''}`.toLowerCase().includes(q)));
}

export function renderJobs() {
  const area = document.getElementById('jobs-area');
  if (!area) return;
  if (!state.jobs.length) {
    clear(area, h('div', { class: 'empty' }, h('h3', {}, 'No jobs yet'),
      h('p', {}, 'Start by importing your CryoSPARC refinement (half maps, sharpened map, mask), or create a whole pipeline from a workflow template.'),
      h('div', { class: 'row', style: { justifyContent: 'center' } },
        btn('Import from CryoSPARC', () => { openBuilder({ type: 'import_cryosparc' }); navigate(`#/p/${state.project.uid}/new`); }, { cls: 'primary', ic: 'plus' }),
        btn('Use a workflow', () => workflowDialog(), { ic: 'flow' }))));
    return;
  }
  const jobs = filtered();
  const sorted = [...jobs].sort((a, b) => b.num - a.num);
  state.cardOrder = sorted.map((j) => j.uid);
  for (const uid of [...state.selection]) if (!state.jobsByUid[uid]) state.selection.delete(uid);
  const rel = lineage();
  if (state.view === 'graph') renderGraph(area, jobs, rel);
  else clear(area, h('div', { class: 'cards' }, sorted.map((j) => jobCard(j, rel))));
  const bar = selectionBar();
  if (bar) area.prepend(bar);
}

// ------------------------------------------------------------- selection
// Ctrl/Cmd-click toggles a job, Shift-click selects a range, a plain click opens the job (as in CryoSPARC).
export function pickJob(e, uid) {
  if (e.ctrlKey || e.metaKey) {
    if (state.selection.has(uid)) state.selection.delete(uid);
    else state.selection.add(uid);
    state.selectionAnchor = uid;
    renderJobs();
    return true;
  }
  if (e.shiftKey) {
    const order = state.cardOrder;
    const a = order.indexOf(state.selectionAnchor ?? detailUid() ?? uid);
    const b = order.indexOf(uid);
    if (a >= 0 && b >= 0) order.slice(Math.min(a, b), Math.max(a, b) + 1).forEach((u) => state.selection.add(u));
    else state.selection.add(uid);
    renderJobs();
    return true;
  }
  return false;
}

function openJob(uid) {
  const had = state.selection.size > 0;
  state.selection.clear();
  state.selectionAnchor = uid;
  navigate(jobHref(uid));
  if (had) renderJobs();
}

export function clearSelection() {
  if (!state.selection.size) return false;
  state.selection.clear();
  renderJobs();
  return true;
}

export function selectAllShown() {
  state.cardOrder.forEach((u) => state.selection.add(u));
  renderJobs();
}

const CAN = {
  queue: (j) => j.status === 'building',
  kill: (j) => ACTIVE.includes(j.status) || j.status === 'waiting',
  clear: (j) => ['completed', 'failed', 'killed'].includes(j.status),
  delete: () => true,
};

function selectionBar() {
  const jobs = [...state.selection].map((u) => state.jobsByUid[u]).filter(Boolean);
  if (!jobs.length) return null;
  const act = (kind, label, ic, cls = '') => {
    const targets = jobs.filter(CAN[kind]);
    return btn(`${label} (${targets.length})`, () => bulk(kind, targets), { ic, cls, disabled: !targets.length,
      title: targets.length ? targets.map((j) => j.uid).join(', ') : 'Does not apply to the selected jobs' });
  };
  return h('div', { class: 'selbar', role: 'region', 'aria-label': 'Selected jobs' },
    h('b', {}, `${jobs.length} selected`),
    h('span', { class: 'muted small selbar-uids' }, jobs.map((j) => j.uid).join(', ')),
    h('span', { class: 'grow' }),
    act('queue', 'Queue', 'play'), act('kill', 'Stop', 'stop'), act('clear', 'Clear', 'undo'), act('delete', 'Delete', 'trash', 'danger'),
    btn('Deselect', () => clearSelection(), { ic: 'x', title: 'Esc' }));
}

async function bulk(kind, jobs) {
  const puid = state.project.uid;
  const verb = { queue: 'Queue', kill: 'Stop', clear: 'Clear', delete: 'Delete' }[kind];
  const past = { queue: 'Queued', kill: 'Stopped', clear: 'Cleared', delete: 'Deleted' }[kind];
  const uids = new Set(jobs.map((j) => j.uid));
  if (kind !== 'queue') {
    let detail = '';
    if (kind === 'clear') detail = '\nTheir outputs and files are deleted; they go back to building.';
    if (kind === 'delete') {
      const outside = state.jobs.filter((j) => !uids.has(j.uid) && Object.values(j.inputs || {}).some((r) => uids.has(r.job)));
      detail = '\nTheir folders are deleted.'
        + (outside.length ? `\n${outside.map((j) => j.uid).join(', ')} use their outputs: those inputs become invalid.` : '');
    }
    if (!(await confirmDialog(`${verb} ${jobs.length} job${jobs.length > 1 ? 's' : ''}`, `${verb} ${[...uids].join(', ')}?${detail}`, verb, true))) return;
  }
  // parents first when queueing, children first when deleting
  const order = [...jobs].sort((a, b) => (kind === 'delete' ? b.num - a.num : a.num - b.num));
  const failed = [];
  for (const j of order) {
    try {
      if (kind === 'queue') await api.queueJob(puid, j.uid);
      else if (kind === 'kill') await api.killJob(puid, j.uid);
      else if (kind === 'clear') await api.clearJob(puid, j.uid);
      else await api.deleteJob(puid, j.uid, true);
    } catch (err) {
      failed.push(`${j.uid}: ${err.message}`);
    }
  }
  const done = order.length - failed.length;
  if (kind === 'delete') order.forEach((j) => state.selection.delete(j.uid));
  if (done) toast(`${past} ${done} job${done > 1 ? 's' : ''}`, 'ok');
  if (failed.length) toast(failed.join('\n'), 'error', 10000);
  await refreshJobs();
  renderJobs();
  if (kind === 'delete' && detailUid() && !state.jobsByUid[detailUid()]) navigate(`#/p/${puid}`);
}

// Direct parents and children of the job open in the details, highlighted on the cards.
function lineage() {
  const uid = detailUid();
  const job = uid && state.jobsByUid[uid];
  if (!job) return null;
  return {
    uid,
    parents: new Set(Object.values(job.inputs || {}).map((r) => r.job)),
    children: new Set(state.jobs.filter((j) => Object.values(j.inputs || {}).some((r) => r.job === uid)).map((j) => j.uid)),
  };
}

function typeIcon(type) {
  const cat = state.types[type]?.category || '';
  if (cat === 'Import') return icon('download');
  if (cat === 'Map processing') return icon('map');
  if (['Model building', 'Interactive', 'Refinement'].includes(cat)) return icon('model');
  if (cat === 'Validation' || cat === 'Deposition') return icon('check');
  return icon('file');
}

function jobCard(job, rel) {
  const puid = state.project.uid;
  const thumbOut = (job.outputs || []).find((o) => o.thumbnail);
  const builderOpen = !!state.builder?.type;
  const parent = rel?.parents.has(job.uid);
  const child = rel?.children.has(job.uid);
  const picked = state.selection.has(job.uid);
  const view3d = job.status === 'completed' ? jobViewItems(job) : [];
  const card = h('div', {
    class: `card ${job.status} ${detailUid() === job.uid ? 'selected' : ''} ${picked ? 'picked' : ''} ${parent ? 'lineage-parent' : ''} ${child ? 'lineage-child' : ''}`,
    tabindex: 0, role: 'button', dataset: { uid: job.uid }, 'aria-pressed': picked ? 'true' : 'false',
    'aria-label': `${job.uid} ${job.title} ${job.status}${parent ? `, input of ${rel.uid}` : ''}${child ? `, uses ${rel.uid}` : ''}`,
    onmousedown: (e) => { if (e.shiftKey) e.preventDefault(); },
    onclick: (e) => { if (e.target.closest('.out-chip') || pickJob(e, job.uid)) return; openJob(job.uid); },
    onkeydown: (e) => {
      if (e.key === 'Enter') openJob(job.uid);
      else if (e.key === ' ') { e.preventDefault(); pickJob({ ctrlKey: true }, job.uid); }
    },
  },
  h('div', { class: 'card-head' }, h('span', { class: 'uid' }, job.uid), statusChip(job.status)),
  h('div', { class: 'card-thumb' }, thumbOut ? h('img', { src: api.fileUrl(puid, thumbOut.thumbnail), alt: '', loading: 'lazy' }) : typeIcon(job.type),
    parent ? h('span', { class: 'lineage-tag up' }, `input of ${rel.uid}`) : null,
    child ? h('span', { class: 'lineage-tag down' }, `uses ${rel.uid}`) : null,
    view3d.length ? h('a', {
      class: 'card-3d', href: viewerUrl(view3d), target: '_blank', rel: 'noopener', title: 'Open in the 3D viewer', 'aria-label': `Open ${job.uid} in the 3D viewer`,
      onclick: (e) => e.stopPropagation(),
    }, icon('eye'), '3D') : null),
  h('div', { class: 'card-title' }, job.title),
  job.title !== typeTitle(job.type) ? h('div', { class: 'card-type' }, typeTitle(job.type)) : null,
  (job.highlights || []).length ? h('div', { class: 'card-hl' }, job.highlights.slice(0, 3).map((hl) => h('span', {}, `${hl.label} `, h('b', {}, String(hl.value))))) : null,
  ['queued', 'launched', 'running'].includes(job.status) && job.message ? h('div', { class: 'card-msg', title: job.message }, job.message) : null,
  job.status === 'running' ? h('div', { class: 'progress' }, h('i', { style: { width: `${Math.max(3, Math.round((job.progress || 0) * 100))}%` } })) : null,
  job.status === 'failed' && job.error ? h('div', { class: 'card-msg', title: job.error }, job.error) : null,
  builderOpen && job.status === 'completed' && job.outputs.length ? outputChips(job) : null,
  h('div', { class: 'card-foot' }, h('span', {}, jobDuration(job)), h('span', {}, job.lane || '')));
  return card;
}

function outputChips(job) {
  return h('div', { class: 'out-chips' }, job.outputs.map((o) => h('span', {
    class: 'out-chip', draggable: 'true', title: `Drag onto an input of the job builder (${o.type})`,
    ondragstart: (e) => {
      e.dataTransfer.setData('application/x-cryoplug-output', JSON.stringify({ job: job.uid, output: o.name, type: o.type }));
      e.dataTransfer.effectAllowed = 'link';
    },
  }, o.name)));
}

// -------------------------------------------------------------------- graph
function renderGraph(area, jobs, rel) {
  const visible = new Set(jobs.map((j) => j.uid));
  const depth = {};
  const byUid = state.jobsByUid;
  const depthOf = (uid, seen = new Set()) => {
    if (depth[uid] !== undefined) return depth[uid];
    if (seen.has(uid)) return 0;
    seen.add(uid);
    const j = byUid[uid];
    const parents = j ? Object.values(j.inputs || {}).map((r) => r.job).filter((u) => byUid[u]) : [];
    depth[uid] = parents.length ? 1 + Math.max(...parents.map((u) => depthOf(u, seen))) : 0;
    return depth[uid];
  };
  jobs.forEach((j) => depthOf(j.uid));
  const cols = {};
  [...jobs].sort((a, b) => a.num - b.num).forEach((j) => { (cols[depth[j.uid]] ||= []).push(j); });
  const NW = 178, NH = 50, GX = 70, GY = 18, PAD = 20;
  const pos = {};
  let maxRows = 0;
  Object.entries(cols).forEach(([d, list]) => {
    list.forEach((j, i) => { pos[j.uid] = { x: PAD + d * (NW + GX), y: PAD + i * (NH + GY) }; });
    maxRows = Math.max(maxRows, list.length);
  });
  const ncols = Object.keys(cols).length;
  const W = PAD * 2 + ncols * NW + (ncols - 1) * GX;
  const H = PAD * 2 + maxRows * NH + (maxRows - 1) * GY;
  const svg = s('svg', { width: W, height: Math.max(H, 120), viewBox: `0 0 ${W} ${Math.max(H, 120)}`, role: 'img', 'aria-label': 'Job dependency graph' });
  for (const j of jobs) {
    for (const [slot, ref] of Object.entries(j.inputs || {})) {
      if (!visible.has(ref.job) || !pos[ref.job]) continue;
      const a = pos[ref.job], b = pos[j.uid];
      const x1 = a.x + NW, y1 = a.y + NH / 2, x2 = b.x, y2 = b.y + NH / 2, mx = (x1 + x2) / 2;
      svg.appendChild(s('path', { class: 'gedge', d: `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}` }, s('title', {}, `${ref.job}.${ref.output} → ${j.uid}.${slot}`)));
    }
  }
  const colorVar = { completed: '--st-good', running: '--st-running', launched: '--st-running', queued: '--st-warn', waiting: '--st-waiting',
    failed: '--st-critical', killed: '--st-serious', building: '--st-idle' };
  for (const j of jobs) {
    const p = pos[j.uid];
    const title = clip(j.title, 24);
    const cls = [detailUid() === j.uid ? 'selected' : '', state.selection.has(j.uid) ? 'picked' : '',
      rel?.parents.has(j.uid) ? 'lineage-parent' : '', rel?.children.has(j.uid) ? 'lineage-child' : ''].join(' ');
    const g = s('g', { class: `gnode ${cls}`, transform: `translate(${p.x},${p.y})`, tabindex: 0, role: 'button',
      style: 'cursor:pointer', onclick: (e) => { if (!pickJob(e, j.uid)) openJob(j.uid); }, onkeydown: (e) => { if (e.key === 'Enter') openJob(j.uid); } },
    s('rect', { class: 'body', width: NW, height: NH, rx: 7 }),
    s('rect', { width: 5, height: NH, rx: 2, fill: `var(${colorVar[j.status] || '--st-idle'})` }),
    s('text', { x: 14, y: 20, 'font-weight': 600 }, `${j.uid}  `, s('tspan', { 'font-weight': 400 }, title)),
    s('text', { x: 14, y: 38, class: 'sub' }, clip(`${j.status}${j.highlights?.[0] ? ` · ${j.highlights[0].label} ${j.highlights[0].value}` : ''}`, 30)),
    s('title', {}, `${j.uid} ${j.title} (${typeTitle(j.type)}) — ${j.status}`));
    svg.appendChild(g);
  }
  const fit = localStorage.getItem('cryoplug.graphFit') !== '0';
  if (fit) Object.assign(svg.style, { width: '100%', height: 'auto', maxWidth: `${W}px` });
  const zoom = h('div', { class: 'seg', role: 'group', 'aria-label': 'Graph zoom' },
    [['Fit', true], ['100%', false]].map(([label, val]) => h('button', { type: 'button', class: fit === val ? 'on' : '', 'aria-pressed': fit === val ? 'true' : 'false',
      onclick: () => { localStorage.setItem('cryoplug.graphFit', val ? '1' : '0'); renderGraph(area, jobs); } }, label)));
  clear(area, h('div', { class: 'row', style: { marginBottom: '8px' } }, zoom,
    h('span', { class: 'muted small' }, 'Each column is one step further from the imported data. Lines follow outputs into inputs; the left bar shows the job status.')),
  h('div', { class: 'graph-wrap' }, svg));
}

function clip(text, n) {
  return text.length > n ? `${text.slice(0, n - 1)}…` : text;
}

// ---------------------------------------------------------------- workflows
async function workflowDialog() {
  let workflows;
  try { workflows = await guard(api.workflows()); } catch { return; }
  const body = h('div', {});
  const m = modal({ title: 'Create jobs from a workflow', wide: true, body });
  const showList = () => clear(body,
    h('p', { class: 'muted', style: { marginTop: 0 } }, 'A workflow creates a chain of linked jobs in one go. Optional steps can be left out; downstream jobs are re-wired automatically.'),
    workflows.map((wf) => h('div', { class: 'wf-card', tabindex: 0, role: 'button', onclick: () => showForm(wf), onkeydown: (e) => { if (e.key === 'Enter') showForm(wf); } },
      h('b', {}, wf.title), h('div', { class: 'muted small' }, wf.description),
      h('div', { class: 'small', style: { marginTop: '4px' } }, wf.nodes.map((n) => n.title).join(' → ')))));
  const showForm = (wf) => {
    const include = new Set(wf.nodes.filter((n) => n.optional && n.default).map((n) => n.id));
    const overrides = {};
    const lane = h('select', {}, state.info.lanes.map((l) => h('option', { value: l.name }, `${l.name} (${l.type})`)));
    const nodes = wf.nodes.map((n) => {
      overrides[n.id] = Object.fromEntries(n.ask.map((p) => [p.name, p.default]));
      const cb = n.optional ? h('input', { type: 'checkbox', checked: include.has(n.id), 'aria-label': `Include ${n.title}`,
        onchange: (e) => { if (e.target.checked) include.add(n.id); else include.delete(n.id); el.classList.toggle('off', !e.target.checked); } }) : null;
      const el = h('div', { class: `wf-node ${n.optional && !include.has(n.id) ? 'off' : ''}` },
        h('div', { class: 'row' }, cb, h('b', {}, n.title), h('span', { class: 'muted small' }, n.type_title),
          n.interactive ? h('span', { class: 'tag' }, 'interactive') : null, n.optional ? h('span', { class: 'muted small' }, 'optional') : null),
        n.ask.length ? h('div', { style: { marginTop: '8px', maxWidth: '620px' } }, n.ask.map((p) => paramField(p, overrides[n.id], `wf-${n.id}`))) : null);
      return el;
    });
    const create = async (queue) => {
      try {
        const res = await guard(api.instantiate(state.project.uid, wf.id, { overrides, include: [...include], queue, lane: lane.value }));
        m.close();
        const nprob = Object.keys(res.problems || {}).length;
        toast(`${res.jobs.length} jobs created${queue ? `, ${res.queued.length} queued` : ''}${nprob ? ` (${nprob} need parameters before queueing)` : ''}`, nprob ? 'info' : 'ok', 7000);
        await refreshJobs();
        renderJobs();
      } catch { /* shown */ }
    };
    clear(body,
      h('div', { class: 'row', style: { marginBottom: '10px' } }, btn('← All workflows', showList, { cls: 'small' }), h('b', {}, wf.title)),
      h('p', { class: 'muted small' }, wf.description),
      nodes,
      h('div', { class: 'row', style: { marginTop: '14px' } }, h('label', {}, 'Lane'), h('div', { style: { width: '240px' } }, lane), h('span', { class: 'grow' }),
        btn('Create jobs (building)', () => create(false), { ic: 'plus' }), btn('Create & queue all', () => create(true), { cls: 'primary', ic: 'play' })));
  };
  showList();
}
