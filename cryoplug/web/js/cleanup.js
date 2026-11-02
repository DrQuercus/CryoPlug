// Clean up project data, as CryoSPARC's: the options on the left (preprocessing jobs, intermediate results, non-final,
// killed and failed jobs), on the right the estimate and the jobs each option affects, the cleanup confirmed by typing
// the project id. Nothing is changed before the Cleanup button; the estimate comes from the server for every change.
import { api } from './api.js';
import { canManageProject } from './pages.js';
import { state } from './state.js';
import { btn, clear, fmtSize, h, icon, modal, statusChip, toast } from './ui.js';

const KINDS = [['motioncorr', '1a'], ['ctf', '1b'], ['extract', '1c'], ['downsample', '1d'], ['restack', '1e'], ['tomograms', '1f'], ['denoise', '1g']];
const TABS = [['estimate', 'Estimate'], ['preprocessing', 'Preprocessing'], ['intermediate', 'Intermediate results'],
  ['nonfinal', 'Non-final jobs'], ['killedfailed', 'Killed/Failed jobs']];
const ACTION_DOT = { clear: 'clear', intermediate: 'trim', delete: 'delete' };

function defaultOptions() {
  return { preprocessing: Object.fromEntries(KINDS.map(([k]) => [k, true])), intermediate: true, nonfinal: '', killed: 'delete', failed: 'delete', exclude: [] };
}

export function cleanupDialog(project, { onDone = null } = {}) {
  const p = project || state.project;
  const puid = p.uid;
  const opts = defaultOptions();
  let plan = null;
  let tab = 'estimate';
  let busy = false;
  let pollTimer = null;
  const excluded = new Set();

  // --- the pieces redrawn after each estimate
  const stats = h('div', { class: 'cleanup-stats' });
  const banner = h('div', { class: 'cleanup-banner' });
  const options = h('div', { class: 'cleanup-options' });
  const tabsEl = h('div', { class: 'tabs cleanup-tabs', role: 'tablist' });
  const pane = h('div', { class: 'cleanup-pane' });
  const confirmInput = h('input', { type: 'text', placeholder: puid, 'aria-label': 'Project id to confirm', autocomplete: 'off', spellcheck: 'false',
    oninput: () => drawFooter() });
  const runBtn = btn('Cleanup', () => run(), { cls: 'primary danger', disabled: true, title: `Type ${puid} to enable` });
  const body = h('div', { class: 'cleanup' }, stats, banner,
    h('div', { class: 'cleanup-main' }, options, h('div', { class: 'cleanup-right' }, tabsEl, pane)));
  const dlg = modal({ title: `Clean up project data: ${puid}`, body, wide: true,
    footer: [btn('Cancel', () => dlg.close()), h('span', { class: 'grow' }),
      h('span', { class: 'cleanup-confirm' }, h('span', { class: 'muted small' }, 'Please confirm the project id to proceed.'), confirmInput, runBtn)],
    onClose: () => { if (pollTimer) clearTimeout(pollTimer); } });
  dlg.el.querySelector('.modal').classList.add('cleanup-modal');

  const stat = (label, value, title = '') => h('div', { class: 'cleanup-stat', title }, h('span', { class: 'k' }, label), h('b', {}, value));

  function drawStats() {
    if (!plan) { clear(stats, h('div', { class: 'muted small' }, 'Estimating…')); return; }
    clear(stats, stat('Jobs', String(plan.jobs)), stat('Size of jobs', fmtSize(plan.disk_bytes || 0), 'Sum of the job folders, as measured when each job ended'),
      stat('Intermediate results', fmtSize(plan.intermediate_bytes || 0), 'Files of every iteration but the last of the RELION runs'),
      plan.finals.length ? stat('Final results', `${plan.finals.length} job${plan.finals.length > 1 ? 's' : ''} · ${plan.kept} kept`,
        `Marked as final: ${plan.finals.join(', ')}; with everything upstream of them, ${plan.kept} jobs are kept by the non-final options`) : null);
    const m = plan.measuring || {};
    if (m.measuring) {
      clear(banner, h('div', { class: 'alert info' }, icon('pulse'), ` Calculating the project size… ${m.done} of ${m.total} job folders measured.`));
      pollTimer = setTimeout(() => refresh(), 1500);
    } else if (plan.unmeasured > 0) {
      clear(banner, h('div', { class: 'alert warn' }, icon('alert'), ' ',
        h('button', { class: 'link-btn', type: 'button', onclick: () => measure() }, 'Calculate project size'),
        ` for a more accurate representation of sizes on disk (${plan.unmeasured} job folder${plan.unmeasured > 1 ? 's' : ''} never measured).`));
    } else {
      const when = plan.measured_at ? new Date(plan.measured_at * 1000).toLocaleString() : null;
      const last = plan.last_cleanup ? ` Last cleanup ${new Date(plan.last_cleanup.at * 1000).toLocaleString()}: ${fmtSize(plan.last_cleanup.freed_bytes)} freed.` : '';
      clear(banner, h('div', { class: 'muted small cleanup-note' },
        when ? `Sizes measured ${when}. ` : 'Sizes as measured when each job ended. ',
        h('button', { class: 'link-btn', type: 'button', onclick: () => measure() }, 'Calculate project size'), ` again.${last}`));
    }
  }

  // --- the option tree: a box per option, the sub-options indented; counts of the jobs available to each
  function check(id, label, checked, onchange, { indeterminate = false, count = null, sub = false, disabled = false, hint = '' } = {}) {
    const box = h('input', { type: 'checkbox', id, checked, disabled, onchange: (e) => onchange(e.target.checked) });
    box.indeterminate = indeterminate;
    return h('label', { class: `opt ${sub ? 'sub' : ''} ${disabled ? 'off' : ''}`, for: id, title: hint }, box, h('span', {}, label),
      count !== null ? h('span', { class: 'muted small' }, ` (${count})`) : null);
  }

  function drawOptions() {
    const av = plan?.available || { preprocessing: {} };
    const labels = plan?.labels?.preprocessing || {};
    const kinds = Object.values(opts.preprocessing);
    const allKinds = kinds.every(Boolean);
    const someKinds = kinds.some(Boolean);
    const pre = h('div', { class: 'opt-group' },
      check('cu-pre', '1. Clear preprocessing jobs', allKinds, (on) => { KINDS.forEach(([k]) => { opts.preprocessing[k] = on; }); changed(); },
        { indeterminate: !allKinds && someKinds, hint: 'Their micrographs, CTF images and particle stacks are deleted and the jobs go back to building: run them again to make them' }),
      KINDS.map(([k, num]) => check(`cu-pre-${k}`, `${num}. Clear ${labels[k] || k} jobs`, !!opts.preprocessing[k],
        (on) => { opts.preprocessing[k] = on; changed(); }, { sub: true, count: av.preprocessing?.[k] ?? 0 })));
    const twoLevel = (key, num, label, hint) => {
      const value = opts[key];
      return h('div', { class: 'opt-group' },
        check(`cu-${key}`, `${num}. Clear ${label}`, value === 'clear', (on) => { opts[key] = on ? 'clear' : ''; changed(); },
          { indeterminate: value === 'delete', count: av[key] ?? 0, hint }),
        check(`cu-${key}-del`, `${num}a. Delete ${label}`, value === 'delete', (on) => { opts[key] = on ? 'delete' : (value === 'delete' ? 'clear' : value); changed(); },
          { sub: true, hint: 'The jobs are removed from the project with their folders (for good)' }));
    };
    const finals = plan?.finals?.length || 0;
    clear(options,
      pre,
      h('div', { class: 'opt-group' }, check('cu-int', '2. Clear intermediate results', opts.intermediate, (on) => { opts.intermediate = on; changed(); },
        { count: av.intermediate ?? 0, hint: 'The files of every iteration of the RELION runs but the last: outputs, reports and Resume are kept' })),
      twoLevel('nonfinal', '3', 'non-final jobs', 'Jobs that are neither marked as a final result nor upstream of one'),
      finals ? null : h('div', { class: 'muted small opt-note' }, 'No job is marked as a final result yet: every finished job counts as non-final. ',
        'Mark the jobs to keep (job menu › Mark as final result) before clearing or deleting them.'),
      twoLevel('killed', '4', 'killed jobs', 'Jobs stopped before their end'),
      twoLevel('failed', '5', 'failed jobs', 'Jobs that ended on an error'),
      h('a', { class: 'btn small cleanup-doc', href: '#/help', target: '_blank', title: 'The guide, section « Nettoyer un projet »' }, icon('book'), 'Data cleanup documentation'));
  }

  // --- the tabs on the right
  function countOf(key) {
    if (!plan) return 0;
    if (key === 'killedfailed') return plan.entries.filter((e) => e.category === 'killed' || e.category === 'failed').length;
    return plan.entries.filter((e) => e.category === key).length;
  }

  function drawTabs() {
    clear(tabsEl, TABS.map(([key, label]) => h('button', { type: 'button', role: 'tab', class: tab === key ? 'on' : '', 'aria-selected': tab === key ? 'true' : 'false',
      onclick: () => { tab = key; drawTabs(); drawPane(); } }, label, key !== 'estimate' && plan ? h('span', { class: 'count' }, String(countOf(key))) : null)));
  }

  function estimateTable() {
    const s = plan.summary;
    const row = (cls, label, v) => h('tr', { class: cls === 'total' ? 'total' : '' },
      h('td', {}, h('span', { class: `dot ${cls}` }), label), h('td', { class: 'num' }, String(v.jobs)), h('td', { class: 'num' }, fmtSize(v.bytes)));
    return h('table', { class: 'data cleanup-estimate' },
      h('thead', {}, h('tr', {}, h('th', {}, ''), h('th', {}, 'Jobs affected'), h('th', {}, 'Estimated savings'))),
      h('tbody', {}, row('clear', 'Clear', s.clear), row('trim', 'Clear intermediate results', s.intermediate), row('delete', 'Delete', s.delete),
        row('total', 'Total', s.total)));
  }

  function jobsTable(entries, { withExclude = true } = {}) {
    if (!entries.length) return h('div', { class: 'muted small cleanup-empty' }, 'No job affected by the options ticked.');
    const rows = entries.map((e) => {
      const box = withExclude ? h('input', { type: 'checkbox', checked: !excluded.has(e.uid), title: 'Untick to leave this job alone',
        onchange: (ev) => { if (ev.target.checked) excluded.delete(e.uid); else excluded.add(e.uid); opts.exclude = [...excluded]; changed(); } }) : null;
      return h('tr', {},
        h('td', {}, box), h('td', { class: 'mono' }, e.uid), h('td', {}, e.title, e.final ? h('span', { class: 'final-mark', title: 'Final result' }, ' ★') : null,
          e.title !== e.type_title ? h('div', { class: 'muted small' }, e.type_title) : null),
        h('td', {}, statusChip(e.status)), h('td', { class: 'num' }, fmtSize(e.disk_bytes || 0)), h('td', { class: 'num' }, fmtSize(e.savings || 0)),
        h('td', {}, h('span', { class: `dot ${ACTION_DOT[e.action]}` }), plan.labels.actions[e.action] + (e.action === 'intermediate' && e.last_iteration ? ` (iteration ${e.last_iteration} kept)` : ''),
          e.downgraded ? h('div', { class: 'muted small' }, 'cleared, not deleted: a job it feeds stays') : null),
        h('td', { class: 'num' }, e.dependents ? String(e.dependents) : '—'));
    });
    return h('div', { class: 'table-box' }, h('table', { class: 'data cleanup-jobs' },
      h('thead', {}, h('tr', {}, h('th', {}, ''), h('th', {}, 'Job'), h('th', {}, 'Title'), h('th', {}, 'Status'), h('th', {}, 'Size'), h('th', {}, 'Savings'),
        h('th', {}, 'Action'), h('th', {}, 'Used by'))),
      h('tbody', {}, rows)));
  }

  function drawPane() {
    if (!plan) { clear(pane, h('div', { class: 'muted' }, 'Estimating…')); return; }
    if (tab === 'estimate') {
      const protectedJobs = plan.protected || [];
      clear(pane, estimateTable(),
        excluded.size ? h('div', { class: 'muted small cleanup-note' }, `${excluded.size} job${excluded.size > 1 ? 's' : ''} left alone (unticked in the other tabs): ${[...excluded].join(', ')}`) : null,
        protectedJobs.length ? h('div', { class: 'muted small cleanup-note' }, 'Left alone: ', protectedJobs.map((x) => `${x.uid} (${x.reason})`).join(', '), '.') : null,
        h('p', { class: 'muted small cleanup-note' }, 'Clear: the files of the job are deleted and it goes back to building (its parameters and inputs stay; run it again to make the results). ',
          'Clear intermediate results: the files of every iteration but the last. Delete: the job is removed from the project with its folder.'));
      return;
    }
    const keys = tab === 'killedfailed' ? ['killed', 'failed'] : [tab];
    const entries = plan.entries.filter((e) => keys.includes(e.category));
    const off = keys.every((k) => (k === 'preprocessing' ? !Object.values(opts.preprocessing).some(Boolean) : k === 'intermediate' ? !opts.intermediate : !opts[k]));
    const available = keys.reduce((n, k) => n + (k === 'preprocessing' ? Object.values(plan.available.preprocessing || {}).reduce((a, b) => a + b, 0) : plan.available[k] || 0), 0);
    clear(pane, off ? h('div', { class: 'muted small cleanup-empty' }, `Tick the option to see the jobs it would affect (${available} available).`) : jobsTable(entries));
  }

  function drawFooter() {
    const allowed = canManageProject(p);
    const ok = allowed && confirmInput.value.trim() === puid && plan && plan.summary.total.jobs > 0 && !busy;
    runBtn.disabled = !ok;
    runBtn.title = !allowed ? 'Only the owner of the project or an administrator can clean it up'
      : ok ? `Clear or delete ${plan.summary.total.jobs} jobs (${fmtSize(plan.summary.total.bytes)})`
        : !plan || !plan.summary.total.jobs ? 'Nothing to clean with the options ticked' : `Type ${puid} to enable`;
  }

  // --- data
  async function refresh() {
    if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
    try {
      plan = await api.cleanupEstimate(puid, opts);
    } catch (e) {
      clear(pane, h('div', { class: 'alert error' }, `Estimate failed: ${e.message}`));
      return;
    }
    drawStats(); drawOptions(); drawTabs(); drawPane(); drawFooter();
  }

  function changed() { drawOptions(); refresh(); }

  async function measure() {
    try { await api.measureProject(puid); } catch (e) { toast(`Could not start the measurement: ${e.message}`, 'error'); return; }
    refresh();
  }

  async function run() {
    if (busy || !plan) return;
    const n = plan.summary.total.jobs;
    busy = true;
    drawFooter();
    runBtn.textContent = 'Cleaning up…';
    let res;
    try {
      res = await api.cleanupProject(puid, opts, confirmInput.value.trim());
    } catch (e) {
      busy = false;
      runBtn.textContent = 'Cleanup';
      drawFooter();
      toast(`Cleanup failed: ${e.message}`, 'error', 8000);
      return;
    }
    dlg.close();
    const parts = [res.deleted.length ? `${res.deleted.length} deleted` : '', res.cleared.length ? `${res.cleared.length} cleared` : '',
      res.trimmed.length ? `${res.trimmed.length} trimmed of their intermediate results` : ''].filter(Boolean).join(', ');
    toast(`${puid}: ${parts || `nothing done (${n} planned)`}; ${fmtSize(res.freed_bytes)} freed${res.errors.length ? `; ${res.errors.length} errors (see below)` : ''}`,
      res.errors.length ? 'warn' : 'ok', 9000);
    if (res.errors.length) {
      modal({ title: 'Cleanup: some jobs could not be cleaned', body: h('ul', {}, res.errors.map((x) => h('li', {}, h('b', {}, x.uid), ` ${x.error}`))),
        footer: [] });
    }
    if (onDone) onDone(res);
  }

  drawOptions();
  drawTabs();
  drawPane();
  refresh();
  return dlg;
}
