// CryoPlug single-page application: router, polling and layout.
import { api } from './api.js';
import { closeBuilder, openBuilder, refreshBuilderInputs, renderBuilder } from './builder.js';
import { closeDetail, detailIsLive, detailUid, openDetail, refreshDetail } from './detail.js';
import { renderHelp, renderProjects, renderQueue, renderTools } from './pages.js';
import { renderJobs, renderProject } from './project.js';
import { loadStatic, onJobsChanged, refreshJobs, state } from './state.js';
import { clear, guard, h, toast } from './ui.js';

const content = document.getElementById('content');
const panel = document.getElementById('panel');
const crumbs = document.getElementById('crumbs');
const topRight = document.getElementById('topbar-right');
let dragging = false;
let jobsSignature = '';

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

document.addEventListener('dragstart', () => { dragging = true; });
document.addEventListener('dragend', () => { dragging = false; });

// ------------------------------------------------------------------ routing
function parseRoute() {
  const parts = location.hash.replace(/^#\/?/, '').split('/').filter(Boolean);
  if (!parts.length || parts[0] === 'projects') return { page: 'projects' };
  if (parts[0] === 'p' && parts[1]) {
    const r = { page: 'project', puid: parts[1] };
    if (parts[2] === 'new') r.panel = 'new';
    else if (parts[2] === 'edit' && parts[3]) Object.assign(r, { panel: 'edit', juid: parts[3] });
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
    if (route.juid) items.push(h('span', { class: 'sep' }, '›'), h('span', {}, route.juid));
    if (route.panel === 'new') items.push(h('span', { class: 'sep' }, '›'), h('span', {}, 'New job'));
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
  if (projectChanged) {
    try {
      state.project = await api.project(r.puid);
    } catch (e) {
      toast(e.message, 'error');
      location.hash = '#/projects';
      return;
    }
    state.jobs = [];
    jobsSignature = '';
    await refreshJobs();
    renderProject(content);
  }
  setCrumbs(r);

  if (r.panel === 'detail') {
    closeBuilder();
    await openDetail(r.juid);
  } else if (r.panel === 'new') {
    closeDetail();
    if (!state.builder || state.builder.editing) openBuilder();
    else renderBuilder();
  } else if (r.panel === 'edit') {
    closeDetail();
    const job = state.jobsByUid[r.juid] || (await api.job(r.puid, r.juid));
    if (!state.builder || state.builder.editing?.uid !== r.juid) openBuilder({ editing: job });
    else renderBuilder();
  } else {
    closeDetail();
    closeBuilder();
    panel.hidden = true;
  }
  if (!projectChanged) renderJobs(); // selection / builder chips
}

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
