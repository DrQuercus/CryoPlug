// Project page: job cards (CryoSPARC-like), pipeline graph, filters and workflow templates.
import { api } from './api.js';
import { openBuilder, paramField } from './builder.js';
import { detailUid } from './detail.js';
import { jobHref, navigate, refreshJobs, state, typeTitle } from './state.js';
import { btn, clear, guard, h, icon, jobDuration, modal, s, statusChip, toast } from './ui.js';

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
  if (state.view === 'graph') renderGraph(area, jobs);
  else clear(area, h('div', { class: 'cards' }, [...jobs].sort((a, b) => b.num - a.num).map(jobCard)));
}

function typeIcon(type) {
  const cat = state.types[type]?.category || '';
  if (cat === 'Import') return icon('download');
  if (cat === 'Map processing') return icon('map');
  if (['Model building', 'Interactive', 'Refinement'].includes(cat)) return icon('model');
  if (cat === 'Validation' || cat === 'Deposition') return icon('check');
  return icon('file');
}

function jobCard(job) {
  const puid = state.project.uid;
  const thumbOut = (job.outputs || []).find((o) => o.thumbnail);
  const builderOpen = !!state.builder?.type;
  const card = h('div', {
    class: `card ${job.status} ${detailUid() === job.uid ? 'selected' : ''}`, tabindex: 0, role: 'button', dataset: { uid: job.uid },
    'aria-label': `${job.uid} ${job.title} ${job.status}`,
    onclick: (e) => { if (!e.target.closest('.out-chip')) navigate(jobHref(job.uid)); },
    onkeydown: (e) => { if (e.key === 'Enter') navigate(jobHref(job.uid)); },
  },
  h('div', { class: 'card-head' }, h('span', { class: 'uid' }, job.uid), statusChip(job.status)),
  h('div', { class: 'card-thumb' }, thumbOut ? h('img', { src: api.fileUrl(puid, thumbOut.thumbnail), alt: '', loading: 'lazy' }) : typeIcon(job.type)),
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
function renderGraph(area, jobs) {
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
    const g = s('g', { class: `gnode ${detailUid() === j.uid ? 'selected' : ''}`, transform: `translate(${p.x},${p.y})`, tabindex: 0, role: 'button',
      style: 'cursor:pointer', onclick: () => navigate(jobHref(j.uid)), onkeydown: (e) => { if (e.key === 'Enter') navigate(jobHref(j.uid)); } },
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
