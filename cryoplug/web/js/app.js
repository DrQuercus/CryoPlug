// CryoPlug single-page application: router, polling and layout.
import { api } from './api.js';
import { closeBuilder, openBuilder, refreshBuilderInputs, renderBuilder } from './builder.js';
import { closeDetail, detailIsLive, detailMode, detailUid, openDetail, refreshDetail } from './detail.js';
import { renderHelp, renderProjects, renderQueue, renderTools } from './pages.js';
import { clearSelection, renderJobs, renderProject, selectAllShown } from './project.js';
import { builderHref, loadStatic, navigate, onJobsChanged, refreshJobs, state } from './state.js';
import { clear, guard, h, modal, showError, toast } from './ui.js';

const content = document.getElementById('content');
const panel = document.getElementById('panel');
const crumbs = document.getElementById('crumbs');
const topRight = document.getElementById('topbar-right');
let dragging = false;
let jobsSignature = '';
let cardsScroll = 0; // scroll position of the job cards while a job is opened in their place

// ------------------------------------------------------------------ theme
function applyTheme(theme) {
  if (theme) document.documentElement.setAttribute('data-theme', theme);
  else document.documentElement.removeAttribute('data-theme');
}
applyTheme(localStorage.getItem('cryoplug.theme'));
document.getElementById('theme-toggle').addEventListener('click', () => {
  const dark = document.documentElement.getAttribute('data-theme') === 'dark'
    || (!document.documentElement.getAttribute('data-theme') && matchMedia('(prefers-color-scheme: dark)').matches);
  const next = dark ? 'light' : 'dark';
  localStorage.setItem('cryoplug.theme', next);
  applyTheme(next);
});

// Never fail silently: errors that escape a handler are shown (guard() errors are already).
window.addEventListener('error', (e) => showError(e.error || e.message));
window.addEventListener('unhandledrejection', (e) => showError(e.reason));

document.addEventListener('dragstart', () => { dragging = true; });
document.addEventListener('dragend', () => { dragging = false; });

// ------------------------------------------------------------------ routing
function parseRoute() {
  const parts = location.hash.replace(/^#\/?/, '').split('/').filter(Boolean);
  if (!parts.length || parts[0] === 'projects') return { page: 'projects' };
  if (parts[0] === 'p' && parts[1]) {
    // #/p/P1/J3: job in the panel. #/p/P1/new[/J3], #/p/P1/edit/J5[/J3]: builder in the panel,
    // optionally with a job opened in the main area.
    const r = { page: 'project', puid: parts[1] };
    if (parts[2] === 'new') Object.assign(r, { panel: 'new', inspect: parts[3] || null });
    else if (parts[2] === 'edit' && parts[3]) Object.assign(r, { panel: 'edit', juid: parts[3], inspect: parts[4] || null });
    else if (parts[2]) Object.assign(r, { panel: 'detail', juid: parts[2] });
    return r;
  }
  return { page: parts[0], sub: parts[1] || null, item: parts[2] || null };
}

function setNav(page) {
  document.querySelectorAll('.nav-link').forEach((a) => a.classList.toggle('active', a.dataset.nav === (page === 'project' ? 'projects' : page)));
}

function setCrumbs(route) {
  const items = [h('a', { href: '#/projects' }, 'Projects')];
  if (route.page === 'project' && state.project) {
    items.push(h('span', { class: 'sep' }, '›'), h('a', { href: `#/p/${state.project.uid}` }, h('b', {}, state.project.uid), ` ${state.project.title}`));
    if (route.panel === 'edit') items.push(h('span', { class: 'sep' }, '›'), h('span', {}, `Edit ${route.juid}`));
    else if (route.juid) items.push(h('span', { class: 'sep' }, '›'), h('span', {}, route.juid));
    if (route.panel === 'new') items.push(h('span', { class: 'sep' }, '›'), h('span', {}, 'New job'));
    if (route.inspect) items.push(h('span', { class: 'sep' }, '›'), h('span', {}, route.inspect));
  } else if (route.page !== 'projects') {
    const names = { help: 'Aide', queue: 'Queue', tools: 'Tools' };
    items.push(h('span', { class: 'sep' }, '›'), h('span', {}, names[route.page] || route.page));
  }
  clear(crumbs, items);
}

async function route() {
  const r = parseRoute();
  const prev = state.route;
  state.route = r;
  setNav(r.page);
  document.querySelectorAll('.menu').forEach((m) => m.remove());

  if (r.page !== 'project') {
    state.project = null;
    state.jobs = [];
    jobsSignature = '';
    closeDetail();
    closeBuilder();
    panel.hidden = true;
    setCrumbs(r);
    clear(topRight);
    if (r.page === 'projects') await renderProjects(content);
    else if (r.page === 'queue') await renderQueue(content);
    else if (r.page === 'tools') await renderTools(content);
    else if (r.page === 'help') await renderHelp(content, r.sub, r.item);
    else clear(content, h('div', { class: 'empty' }, 'Page not found.'));
    return;
  }

  const projectChanged = !state.project || state.project.uid !== r.puid || prev.page !== 'project';
  const wasPage = detailMode() === 'page'; // a job was shown in the main area instead of the cards
  if (projectChanged) {
    try {
      state.project = await api.project(r.puid);
    } catch (e) {
      toast(e.message, 'error');
      location.hash = '#/projects';
      return;
    }
    state.jobs = [];
    state.selection.clear();
    jobsSignature = '';
    await refreshJobs();
    renderProject(content);
  }
  setCrumbs(r);

  const restoreCards = () => {
    if (!wasPage || projectChanged) return;
    renderProject(content);
    content.scrollTop = cardsScroll;
  };
  if (r.panel === 'detail') {
    closeBuilder();
    restoreCards();
    await openDetail(r.juid, 'panel');
  } else if (r.panel === 'new' || r.panel === 'edit') {
    // Opening or closing a job next to the builder leaves the builder (and its scroll position) alone.
    const builderShown = !projectChanged && !!state.builder && prev.panel === r.panel && prev.juid === r.juid;
    if (!builderShown) {
      if (detailMode() === 'panel') closeDetail();
      if (r.panel === 'new') {
        if (!state.builder || state.builder.editing) openBuilder();
        else renderBuilder();
      } else {
        const job = state.jobsByUid[r.juid] || (await api.job(r.puid, r.juid));
        if (!state.builder || state.builder.editing?.uid !== r.juid) openBuilder({ editing: job });
        else renderBuilder();
      }
    }
    if (r.inspect) {
      if (!wasPage) cardsScroll = content.scrollTop;
      await openDetail(r.inspect, 'page');
    } else if (wasPage) {
      closeDetail();
      restoreCards();
    }
  } else {
    closeDetail();
    closeBuilder();
    panel.hidden = true;
    restoreCards();
  }
  renderJobs(); // selected card, lineage, builder chips
}

// ------------------------------------------------------------------ keyboard
// CryoSPARC-like shortcuts on the project page; ignored while typing or when a dialog is open.
const SHORTCUTS = [
  ['N', 'New job (opens the job builder)'],
  ['/', 'Filter the jobs'],
  ['G', 'Switch between cards and graph'],
  ['Ctrl/⌘ + A', 'Select all the jobs shown'],
  ['Ctrl/⌘ + click · Shift + click', 'Add a job / a range of jobs to the selection'],
  ['Space (on a card)', 'Add the card to the selection'],
  ['Esc', 'Clear the selection, or close the job details'],
  ['?', 'This list'],
];

function shortcutsDialog() {
  modal({
    title: 'Keyboard shortcuts',
    body: h('table', { class: 'data' }, h('tbody', {}, SHORTCUTS.map(([k, d]) => h('tr', {}, h('td', { class: 'mono' }, k), h('td', {}, d))))),
  });
}

document.addEventListener('keydown', (e) => {
  if (e.defaultPrevented || document.querySelector('.modal-backdrop, .menu')) return;
  const typing = e.target.closest('input, textarea, select, [contenteditable]');
  const r = state.route;
  const onProject = r.page === 'project' && state.project;
  if (e.key === 'Escape') {
    if (typing) { e.target.blur(); return; }
    if (clearSelection()) return;
    if (onProject && r.inspect) navigate(builderHref());
    else if (onProject && r.panel === 'detail') navigate(`#/p/${state.project.uid}`);
    return;
  }
  if (typing || e.altKey) return;
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'a' && onProject && !r.inspect) {
    e.preventDefault();
    selectAllShown();
    return;
  }
  if (e.ctrlKey || e.metaKey) return;
  if (e.key === '?') { shortcutsDialog(); return; }
  if (!onProject) return;
  if (e.key === 'n' || e.key === 'N') {
    if (r.panel !== 'new') { openBuilder(); navigate(`#/p/${state.project.uid}/new`); }
  } else if (e.key === '/') {
    const search = content.querySelector('.toolbar input[type=search]');
    if (search) { e.preventDefault(); search.focus(); }
  } else if (e.key === 'g' || e.key === 'G') {
    if (r.inspect) return;
    state.view = state.view === 'graph' ? 'cards' : 'graph';
    localStorage.setItem('cryoplug.view', state.view);
    renderProject(content);
  }
});

// ------------------------------------------------------------------ polling
onJobsChanged((jobs) => {
  const sig = JSON.stringify(jobs.map((j) => [j.uid, j.status, j.progress, j.message, j.title, j.outputs.length, j.highlights, j.error]));
  if (sig === jobsSignature) return;
  jobsSignature = sig;
  if (!dragging) renderJobs();
  refreshBuilderInputs();
  if (detailUid()) refreshDetail();
});

let tick = 0;
async function poll() {
  tick += 1;
  try {
    if (state.route.page === 'project' && state.project) {
      await refreshJobs();
      if (detailUid() && detailIsLive()) await refreshDetail();
    } else if (state.route.page === 'queue' && tick % 2 === 0) {
      await renderQueue(content);
    }
    if (tick % 2 === 0) {
      const q = await api.queue();
      const n = q.jobs.filter((j) => ['queued', 'launched', 'running'].includes(j.status)).length;
      const badge = document.getElementById('queue-badge');
      badge.hidden = n === 0;
      badge.textContent = String(n);
    }
  } catch (e) {
    console.warn('poll failed', e);
  }
}

async function start() {
  try {
    await loadStatic();
  } catch (e) {
    clear(content, h('div', { class: 'alert error' }, `Cannot reach the CryoPlug server: ${e.message}`));
    return;
  }
  document.getElementById('version').textContent = `v${state.info.version}`;
  const logout = document.getElementById('logout');
  logout.hidden = !state.info.auth;
  logout.addEventListener('click', async () => {
    await fetch('/logout', { method: 'POST' });
    location.assign('/login');
  });
  window.addEventListener('hashchange', () => { route().catch((e) => guard(Promise.reject(e))); });
  await route();
  setInterval(poll, 2500);
}

start();
