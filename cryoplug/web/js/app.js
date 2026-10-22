// CryoPlug single-page application: router, polling and layout.
import { api } from './api.js';
import { closeBuilder, openBuilder, refreshBuilderInputs, renderBuilder } from './builder.js';
import { closeDetail, detailIsLive, detailMode, detailUid, openDetail, refreshDetail } from './detail.js';
import { installFileDrop } from './importer.js';
import { renderHelp, renderProjects, renderTools } from './pages.js';
import { clearSelection, renderJobs, renderProject, selectAllShown } from './project.js';
import { renderResources, stopResources } from './resources.js';
import { renderPasswordChange, renderSettings } from './settings.js';
import { treeKey } from './tree.js';
import { builderHref, loadStatic, navigate, onJobsChanged, refreshJobs, state } from './state.js';
import { avatar, clear, guard, h, lastInput, modal, showError, tickTimes, toast } from './ui.js';

const content = document.getElementById('content');
const panel = document.getElementById('panel');
const crumbs = document.getElementById('crumbs');
const topRight = document.getElementById('topbar-right');
let jobsSignature = ''; // the job list the cards show
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

// A drag from the page (an output chip, a job card on its way to the builder): the cards are not drawn again under it,
// that would take away what is dragged. Its dragend does not always reach the document: there is none after a drag
// cancelled as it starts (a 3D link of the tree), and it goes nowhere when what is dragged has been replaced meanwhile
// (the details of a job that changed). So a drag that has given no sign of life for a second is over.
const drag = { on: false, seen: 0 };
const dragging = () => drag.on && performance.now() - drag.seen < 1000;
document.addEventListener('dragstart', (e) => {
  if (e.defaultPrevented) return; // no drag
  drag.on = true;
  drag.seen = performance.now();
});
for (const type of ['drag', 'dragover']) document.addEventListener(type, () => { drag.seen = performance.now(); }, true);
for (const type of ['dragend', 'drop']) {
  document.addEventListener(type, () => {
    if (!drag.on) return;
    drag.on = false;
    setTimeout(() => { if (state.project) showJobs(state.jobs); }); // what changed during the drag, once the drop is done
  }, true);
}
document.addEventListener('cryoplug-builder-type', () => renderJobs()); // output chips, cards dragged to the builder

// ------------------------------------------------------------------ scroll after a reload
// The pages scroll inside .content, whose position browsers do not restore on a reload (F5): it is kept for the tab
// and given back once the page is long enough again (live data and job cards arrive after the first paint).
const SCROLL_KEY = 'cryoplug.scroll';
let scrollSaving = false;
content.addEventListener('scroll', () => {
  if (scrollSaving) return;
  scrollSaving = true;
  requestAnimationFrame(() => {
    scrollSaving = false;
    try {
      sessionStorage.setItem(SCROLL_KEY, JSON.stringify({ hash: location.hash, top: Math.round(content.scrollTop) }));
    } catch { /* storage unavailable */ }
  });
}, { passive: true });

function restoreScroll() {
  let saved = null;
  try { saved = JSON.parse(sessionStorage.getItem(SCROLL_KEY) || 'null'); } catch { /* storage unavailable */ }
  const reload = performance.getEntriesByType?.('navigation')?.[0]?.type === 'reload';
  if (!reload || !saved || !(saved.top > 0) || saved.hash !== location.hash) return;
  const since = performance.now();
  const step = () => {
    if (lastInput() > since || location.hash !== saved.hash) return; // the reader is already elsewhere
    if (content.scrollHeight - content.clientHeight >= saved.top - 1) { content.scrollTop = saved.top; return; }
    if (performance.now() - since < 8000) setTimeout(step, 150);
  };
  step();
}

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
  const nav = { project: 'projects', queue: 'resources' }[page] || page;
  document.querySelectorAll('.nav-link').forEach((a) => a.classList.toggle('active', a.dataset.nav === nav));
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
    const names = { help: 'Aide', queue: 'Resources', resources: 'Resources', tools: 'Tools', settings: 'Settings' };
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
  stopResources(); // the live view only refreshes while it is shown

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
    else if (r.page === 'resources') renderResources(content, r.sub);
    else if (r.page === 'queue') renderResources(content, 'queue'); // older links
    else if (r.page === 'tools') await renderTools(content);
    else if (r.page === 'help') await renderHelp(content, r.sub, r.item);
    else if (r.page === 'settings') await renderSettings(content, r.sub);
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
  ['G', 'Switch between the cards and the tree view'],
  ['T · F', 'Tree view: centre on the selected jobs · show the whole tree'],
  ['+ · − · 0', 'Tree view: zoom in · out · actual size (or the mouse wheel; Shift + wheel moves the tree)'],
  ['Arrows (on a job of the tree)', 'Move to an input, a job using it, or along the row'],
  ['Ctrl/⌘ + A', 'Select all the jobs shown'],
  ['Ctrl/⌘ + click · Shift + click', 'Add a job / a range of jobs to the selection'],
  ['Space (on a card)', 'Add the card to the selection'],
  ['Esc', 'Clear the selection, or close the job details (double-click the tree background: the same)'],
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
  } else if (state.view === 'graph' && !r.inspect && treeKey(e)) {
    e.preventDefault();
  }
});

// ------------------------------------------------------------------ polling
// The cards (or the tree), the builder's inputs and the job details follow any change of the job list; under a drag,
// once it is over.
function showJobs(jobs) {
  const sig = JSON.stringify(jobs);
  if (sig === jobsSignature || dragging()) return;
  jobsSignature = sig;
  renderJobs();
  refreshBuilderInputs();
  if (detailUid()) refreshDetail();
}
onJobsChanged(showJobs);

let tick = 0;
async function poll() {
  tick += 1;
  try {
    if (state.route.page === 'project' && state.project) {
      await refreshJobs();
      if (detailUid() && detailIsLive() && !dragging()) await refreshDetail();
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

// The logged-in user, at the bottom of the sidebar (opens My account).
function renderUserBadge() {
  const badge = document.getElementById('user-badge');
  const user = state.info.user;
  badge.hidden = !user || user.builtin;
  if (badge.hidden) return;
  badge.title = `${user.full_name || user.username} (${user.username}, ${user.role === 'admin' ? 'administrator' : 'user'}): my account`;
  badge.setAttribute('aria-label', badge.title);
  clear(badge, avatar(user));
}
document.addEventListener('cryoplug-user', renderUserBadge);
// A temporary password must be replaced first: reloading shows that form.
window.addEventListener('cryoplug-password-change', () => location.reload());

async function start() {
  let ready;
  try {
    ready = await loadStatic();
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
  renderUserBadge();
  if (!ready) {
    document.querySelectorAll('.nav-link').forEach((a) => { a.hidden = true; });
    renderPasswordChange(content);
    return;
  }
  window.addEventListener('hashchange', () => { route().catch((e) => guard(Promise.reject(e))); });
  installFileDrop(); // files dragged from the computer onto a project: import them
  await route();
  restoreScroll();
  setInterval(poll, 2500);
  setInterval(tickTimes, 1000); // "5 min ago", the run time of running jobs
  document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); }); // timers slow down in a hidden tab
}

start();
