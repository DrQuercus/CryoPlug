// Several jobs side by side: their FSC curves overlaid, their key results in one table, their maps in one viewer.
import { api } from './api.js';
import { jobViewItems, viewerUrl } from './detail.js';
import { lineChart } from './plots.js';
import { state, typeTitle } from './state.js';
import { btn, guard, h, icon, modal } from './ui.js';

// The FSC curve of a job: the first FSC plot of its report, the corrected (or gold-standard, masked) series.
function fscOf(job) {
  const sec = (job.report?.sections || []).find((s) => s.kind === 'plot' && s.x_kind === 'resolution'
    && /fsc|fourier shell/i.test(s.title) && !/directional|extreme/i.test(s.title));
  if (!sec) return null;
  const series = (sec.series || []).filter((sr) => sr.x?.length);
  const pick = series.find((sr) => /corrected/i.test(sr.name)) || series.find((sr) => /gold|masked/i.test(sr.name)) || series[0];
  if (!pick) return null;
  const same = sec.title.toLowerCase().includes(String(pick.name).toLowerCase());
  return { x: pick.x, y: pick.y, from: same ? sec.title : `${sec.title} — ${pick.name}` };
}

export async function compareJobs(jobs) {
  const puid = state.project.uid;
  let details;
  try {
    details = await guard(Promise.all(jobs.map((j) => api.job(puid, j.uid))));
  } catch { return; }
  const curves = [];
  const missing = [];
  for (const d of details) {
    const f = fscOf(d);
    if (f) curves.push({ name: `${d.uid} ${d.title}`.slice(0, 48), x: f.x, y: f.y, from: f.from });
    else missing.push(d.uid);
  }
  // key results: the highlights of every job, one column per label (those most jobs have first)
  const labels = new Map();
  for (const d of details) for (const hl of d.highlights || []) labels.set(hl.label, (labels.get(hl.label) || 0) + 1);
  const columns = [...labels.entries()].sort((a, b) => b[1] - a[1]).slice(0, 7).map(([l]) => l);
  const table = h('table', { class: 'data' },
    h('thead', {}, h('tr', {}, h('th', {}, 'Job'), h('th', {}, 'Type'), columns.map((c) => h('th', {}, c)))),
    h('tbody', {}, details.map((d) => {
      const hl = Object.fromEntries((d.highlights || []).map((x) => [x.label, x.value]));
      return h('tr', {}, h('td', {}, h('b', {}, d.uid), ` ${d.title}`), h('td', { class: 'muted' }, d.title === typeTitle(d.type) ? '' : typeTitle(d.type)),
        columns.map((c) => h('td', {}, hl[c] === undefined ? '—' : String(hl[c]))));
    })));
  const items = details.flatMap((d) => (d.status === 'completed' ? jobViewItems(d).slice(0, 2) : []));
  const body = h('div', { class: 'compare' },
    curves.length ? [
      h('h4', {}, 'Fourier shell correlation'),
      lineChart({ series: curves.slice(0, 8), x_kind: 'resolution', x_label: 'Resolution (Å)', y_label: 'FSC',
        hlines: [{ y: 0.143, label: '0.143' }, { y: 0.5, label: '0.5' }], y_range: [-0.1, 1.05] }),
      h('p', { class: 'muted small' }, curves.map((c) => `${c.name.split(' ')[0]}: ${c.from}`).join(' · ')
        + (curves.length > 8 ? ` — ${curves.length - 8} more not drawn` : '')),
    ] : h('p', { class: 'muted' }, 'None of these jobs has an FSC curve.'),
    missing.length && curves.length ? h('p', { class: 'muted small' }, `No FSC curve: ${missing.join(', ')}`) : null,
    h('h4', {}, 'Key results'), h('div', { class: 'table-scroll' }, table));
  const m = modal({
    title: `Compare ${details.length} jobs`, wide: true, body,
    footer: [
      items.length ? h('a', { class: 'btn', href: viewerUrl(items), target: '_blank', rel: 'noopener', title: 'Their maps and models together in the 3D viewer' },
        icon('eye'), 'View together in 3D') : null,
      btn('Close', () => m.close(), { cls: 'primary' }),
    ].filter(Boolean),
  });
}
