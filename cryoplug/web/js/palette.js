// Command palette (Ctrl/⌘ + K, or the search button of the sidebar): type a few letters to open a job of the project,
// go to a project or a page, start a job of a type, or run an action (new job, workflows, upload, cards/tree, theme).
// Arrows move, Enter runs, Esc closes; what was used last comes first when nothing is typed yet.
import { api } from './api.js';
import { openBuilder } from './builder.js';
import { importFromComputer } from './importer.js';
import { renderProject, workflowDialog } from './project.js';
import { navigate, state, typeTitle } from './state.js';
import { rankItems } from './match.js';
import { clear, h, icon, statusChip } from './ui.js';

const RECENT_KEY = 'cryoplug.palette.recent';
const RECENT_MAX = 8;
const SHOWN_MAX = 60;
const PAGES = [
  { id: 'page:projects', label: 'Projects', sub: 'All the projects of this server', ic: 'folder', href: '#/projects' },
  { id: 'page:resources', label: 'Resources', sub: 'Live usage of the hardware, queue of the lanes', ic: 'pulse', href: '#/resources' },
  { id: 'page:queue', label: 'Queue', sub: 'The jobs waiting and running on every lane', ic: 'list', href: '#/resources/queue' },
  { id: 'page:tools', label: 'Tools', sub: 'The external programs found on this server', ic: 'gear', href: '#/tools' },
  { id: 'page:settings', label: 'Settings', sub: 'AI agents, users, compute, access', ic: 'sliders', href: '#/settings' },
  { id: 'page:help', label: 'Help', sub: 'Guide, job reference, installation', ic: 'help', href: '#/help' },
];

// ------------------------------------------------------------------ items
const readRecent = () => { try { return JSON.parse(localStorage.getItem(RECENT_KEY) || '[]'); } catch { return []; } };
function remember(id) {
  const list = [id, ...readRecent().filter((x) => x !== id)].slice(0, RECENT_MAX);
  try { localStorage.setItem(RECENT_KEY, JSON.stringify(list)); } catch { /* storage unavailable */ }
}

let projectsCache = { at: 0, list: [] };
async function allProjects() {
  if (Date.now() - projectsCache.at < 60000) return projectsCache.list;
  try {
    projectsCache = { at: Date.now(), list: await api.projects() };
  } catch {
    projectsCache = { at: Date.now(), list: state.projects || [] };
  }
  return projectsCache.list;
}

function jobItems() {
  if (!state.project) return [];
  const puid = state.project.uid;
  return [...state.jobs].sort((a, b) => b.num - a.num).map((j) => ({
    id: `job:${puid}:${j.uid}`, group: 'Jobs', label: `${j.uid} ${j.title || typeTitle(j.type)}`,
    sub: [typeTitle(j.type), ...(j.tags || []), j.message && j.status !== 'completed' ? j.message : ''].filter(Boolean).join(' · '),
    keywords: `${j.type} ${j.status} ${j.notes || ''}`, status: j.status, ic: 'file',
    run: () => navigate(`#/p/${puid}/${j.uid}`),
  }));
}

function projectItems(projects) {
  return projects.map((p) => ({
    id: `project:${p.uid}`, group: 'Projects', label: `${p.uid} ${p.title}`, sub: p.description || p.dir, keywords: p.dir, ic: 'folder',
    hint: p.num_active ? `${p.num_active} active` : `${p.num_jobs} job${p.num_jobs === 1 ? '' : 's'}`,
    run: () => navigate(`#/p/${p.uid}`),
  }));
}

function typeItems() {
  if (!state.project) return [];
  const puid = state.project.uid;
  const restricted = state.info?.auth === 'users' && state.info.user?.role !== 'admin';
  return state.jobtypes.filter((t) => !(t.admin_only && restricted)).map((t) => ({
    id: `type:${t.name}`, group: 'New job', label: t.title, sub: t.help?.purpose || t.description, keywords: `${t.name} ${t.category} ${t.tool || ''}`,
    ic: 'plus', hint: t.category,
    run: () => { openBuilder({ type: t.name }); navigate(`#/p/${puid}/new`); },
  }));
}

function actionItems() {
  const items = [];
  const onProject = !!state.project;
  if (onProject) {
    const puid = state.project.uid;
    items.push({ id: 'act:new', label: 'New job', sub: 'Open the job builder', ic: 'plus', hint: 'N',
      run: () => { openBuilder(); navigate(`#/p/${puid}/new`); } });
    items.push({ id: 'act:workflow', label: 'Create jobs from a workflow', sub: 'A chain of linked jobs from a template', ic: 'flow',
      run: () => workflowDialog() });
    items.push({ id: 'act:upload', label: 'Upload from this computer', sub: 'Maps, models, sequences or folders, imported into the project', ic: 'upload',
      run: () => importFromComputer() });
    items.push({ id: 'act:view', label: state.view === 'graph' ? 'Show the job cards' : 'Show the tree view', sub: 'Cards or the pipeline from the imports down',
      ic: state.view === 'graph' ? 'grid' : 'tree', hint: 'G',
      run: () => {
        state.view = state.view === 'graph' ? 'cards' : 'graph';
        try { localStorage.setItem('cryoplug.view', state.view); } catch { /* storage unavailable */ }
        if (state.route.inspect) navigate(`#/p/${puid}`); else renderProject(document.getElementById('content'));
      } });
  }
  items.push({ id: 'act:theme', label: 'Switch the light / dark theme', sub: 'Also the moon button of the sidebar', ic: 'sun',
    run: () => document.getElementById('theme-toggle')?.click() });
  items.push({ id: 'act:notify', label: 'Job notifications on / off', sub: 'Be told when a job finishes or waits for you', ic: 'bell',
    run: () => document.getElementById('notify-toggle')?.click() });
  items.push({ id: 'act:keys', label: 'Keyboard shortcuts', sub: 'The keys of the project page', ic: 'help', hint: '?',
    run: () => document.dispatchEvent(new CustomEvent('cryoplug-shortcuts')) });
  return items.map((it) => ({ group: 'Actions', ...it }));
}

function pageItems() {
  return PAGES.map((p) => ({ group: 'Pages', ...p, run: () => navigate(p.href) }));
}

// ------------------------------------------------------------------ the palette
let open = null;

export function openPalette() {
  if (open) { open.input.focus(); return; }
  const input = h('input', { type: 'search', class: 'palette-input', placeholder: state.project
    ? 'Job, project, job type, page or action…' : 'Project, page or action…', 'aria-label': 'Search', autocomplete: 'off', spellcheck: 'false' });
  const list = h('div', { class: 'palette-list', role: 'listbox' });
  const foot = h('div', { class: 'palette-foot muted small' }, h('kbd', {}, '↑↓'), ' move  ', h('kbd', {}, 'Enter'), ' open  ', h('kbd', {}, 'Esc'), ' close');
  const backdrop = h('div', { class: 'modal-backdrop palette-backdrop', onmousedown: (e) => { if (e.target === backdrop) close(); } },
    h('div', { class: 'palette', role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Search' },
      h('div', { class: 'palette-head' }, icon('search'), input), list, foot));
  let items = [];
  let shown = [];
  let cursor = 0;
  const close = () => {
    backdrop.remove();
    document.removeEventListener('keydown', onKey, true);
    open = null;
  };
  const pick = (it) => {
    close();
    remember(it.id);
    it.run();
  };
  const draw = () => {
    const q = input.value.trim();
    if (q) {
      shown = rankItems(q, items).slice(0, SHOWN_MAX);
    } else {
      const recent = readRecent().map((id) => items.find((it) => it.id === id)).filter(Boolean).map((it) => ({ ...it, group: 'Recent' }));
      const seen = new Set(recent.map((it) => it.id));
      const some = (group, n) => items.filter((it) => it.group === group && !seen.has(it.id)).slice(0, n);
      shown = [...recent, ...some('Jobs', 6), ...some('Projects', 6), ...some('Pages', 6), ...some('Actions', 8)];
    }
    cursor = Math.max(0, Math.min(cursor, shown.length - 1));
    const rows = [];
    let group = null;
    shown.forEach((it, i) => {
      if (it.group !== group) {
        group = it.group;
        rows.push(h('div', { class: 'palette-group' }, group));
      }
      rows.push(h('button', { type: 'button', class: `palette-item${i === cursor ? ' on' : ''}`, role: 'option', 'aria-selected': i === cursor ? 'true' : 'false',
        onclick: () => pick(it), onpointermove: () => { if (cursor !== i) { cursor = i; mark(); } } },
      icon(it.ic || 'file'), h('span', { class: 'palette-label' }, it.label, it.sub ? h('span', { class: 'palette-sub' }, it.sub) : null),
      it.status ? statusChip(it.status) : it.hint ? h('span', { class: 'palette-hint' }, it.hint) : null));
    });
    clear(list, rows.length ? rows : h('div', { class: 'muted small', style: { padding: '14px' } }, 'Nothing matches.'));
  };
  const mark = () => {
    list.querySelectorAll('.palette-item').forEach((el, i) => {
      el.classList.toggle('on', i === cursor);
      el.setAttribute('aria-selected', i === cursor ? 'true' : 'false');
    });
    list.querySelectorAll('.palette-item')[cursor]?.scrollIntoView({ block: 'nearest' });
  };
  const onKey = (e) => {
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); return; }
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      if (!shown.length) return;
      cursor = (cursor + (e.key === 'ArrowDown' ? 1 : -1) + shown.length) % shown.length;
      mark();
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (shown[cursor]) pick(shown[cursor]);
    }
  };
  input.addEventListener('input', () => { cursor = 0; draw(); });
  document.addEventListener('keydown', onKey, true);
  document.getElementById('modal-root').appendChild(backdrop);
  open = { input, close };
  items = [...jobItems(), ...typeItems(), ...actionItems(), ...pageItems()];
  draw();
  input.focus();
  allProjects().then((projects) => {
    if (!open || open.input !== input) return;
    items = [...jobItems(), ...projectItems(projects), ...typeItems(), ...actionItems(), ...pageItems()];
    draw();
  });
}

export function closePalette() {
  open?.close();
}

export function installPalette() {
  document.getElementById('palette-open')?.addEventListener('click', () => openPalette());
  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      if (open) closePalette(); else openPalette();
    }
  });
}
