// Resources: the machine live (CPU, memory, GPUs, storage, network, processes, as htop and nvtop show them),
// the queue of the lanes, and the partitions of the SLURM lanes.
import { api } from './api.js';
import { fmtGiB, holderLabel } from './compute.js';
import { navigate, state } from './state.js';
import { btn, clear, confirmDialog, fmtDuration, fmtSize, guard, h, icon, jobDuration, meter, s, statusChip } from './ui.js';

const WINDOW = 300; // seconds shown by the graphs (the server keeps 5 minutes)
const R = { gen: 0, timer: null, history: [], hover: {}, jobsOnly: false, sort: 'cpu', snap: null };

const me = () => state.info.user || { builtin: true, role: 'admin' };
const accounts = () => state.info.auth === 'users';
const pct = (v) => (v === null || v === undefined ? '–' : `${Math.round(v)} %`);
const fmtRate = (v) => (v === null || v === undefined ? '–' : `${fmtSize(v)}/s`);
const present = (v) => v !== null && v !== undefined;

// Leaving the page stops the refreshes (app.js calls it on every route change).
export function stopResources() {
  R.gen += 1;
  clearTimeout(R.timer);
  R.timer = null;
}

function later(fn, ms) {
  clearTimeout(R.timer);
  R.timer = setTimeout(fn, ms);
}

export function renderResources(content, sub) {
  stopResources();
  const gen = R.gen;
  const hasCluster = (state.info.lanes || []).some((l) => l.type === 'cluster');
  const tab = sub === 'queue' || (sub === 'cluster' && hasCluster) ? sub : 'live';
  const tabs = [['live', 'Live usage', 'pulse'], ['queue', 'Queue', 'list'], ...(hasCluster ? [['cluster', 'Cluster', 'server']] : [])];
  const status = h('div', { class: 'res-status muted small' });
  const body = h('div', { class: 'res-body' });
  clear(content, h('div', { class: 'res-page' },
    h('div', { class: 'res-head' }, h('h2', {}, 'Resources'), status),
    h('div', { class: 'tabs res-tabs', role: 'tablist' }, tabs.map(([key, label, ic]) => h('button', {
      class: tab === key ? 'on' : '', role: 'tab', 'aria-selected': tab === key ? 'true' : 'false',
      onclick: () => navigate(key === 'live' ? '#/resources' : `#/resources/${key}`),
    }, icon(ic), label))),
    body));
  if (tab === 'live') startLive(body, status, gen);
  else if (tab === 'queue') startQueue(body, gen);
  else startCluster(body, gen);
}

// ------------------------------------------------------------------ live usage
function card(ic, title, cls = '') {
  const value = h('span', { class: 'res-value' });
  const body = h('div', { class: 'res-card-body' });
  const el = h('section', { class: `box res-card ${cls}`.trim(), 'aria-label': title },
    h('div', { class: 'res-card-head' }, icon(ic), h('h3', {}, title), h('span', { class: 'grow' }), value), body);
  return { el, value, body };
}

function startLive(body, status, gen) {
  R.history = [];
  const cards = { cpu: card('cpu', 'CPU'), mem: card('memory', 'Memory'), gpus: h('div', { class: 'res-gpus' }),
    net: card('network', 'Network'), disk: card('disk', 'Disk activity'), storage: card('folder', 'Storage', 'full'),
    procs: processCard() };
  const banner = h('div', {});
  clear(body, banner, h('div', { class: 'res-grid', onpointerleave: () => { R.hover = {}; } },
    cards.cpu.el, cards.mem.el, cards.gpus, cards.net.el, cards.disk.el, cards.storage.el, cards.procs.el));
  let first = true;
  const tick = async () => {
    if (gen !== R.gen) return;
    if (document.hidden) { later(tick, 2000); return; } // nobody looks: no sampling
    let snap;
    try {
      snap = await api.monitor(first);
    } catch (e) {
      if (gen !== R.gen) return;
      clear(banner, h('div', { class: 'alert error' }, `Live usage unavailable: ${e.message}`));
      later(tick, 5000);
      return;
    }
    if (gen !== R.gen) return;
    first = false;
    clear(banner);
    remember(snap);
    R.snap = snap;
    clear(status, snap.hostname || '', snap.uptime ? ` · up ${fmtDuration(snap.uptime)}` : '',
      h('span', { class: 'live-dot', title: 'Refreshed every 2 seconds while this page is open' }, 'live'));
    drawCpu(cards.cpu, snap);
    drawMemory(cards.mem, snap);
    drawGpus(cards.gpus, snap);
    drawRates(cards.net, snap, 'net');
    drawRates(cards.disk, snap, 'disk');
    drawStorage(cards.storage, snap);
    drawProcesses(cards.procs, snap);
    later(tick, 2000);
  };
  tick();
}

// The first answer brings the server's 5 minutes of history; then each sample is added here.
function remember(snap) {
  if (snap.history) { R.history = snap.history.slice(); return; }
  const last = R.history[R.history.length - 1];
  if (last && snap.time - last.t < 0.5) return;
  R.history.push({ t: snap.time, cpu: snap.cpu?.total ?? null, mem: snap.memory?.percent ?? null,
    gpus: (snap.gpus || []).map((g) => [g.utilization, g.memory_percent]),
    rx: snap.network?.rx ?? null, tx: snap.network?.tx ?? null, read: snap.disk_io?.read ?? null, write: snap.disk_io?.write ?? null });
  while (R.history.length && R.history[0].t < snap.time - WINDOW - 10) R.history.shift();
}

const history = (fn) => R.history.map(fn);

function niceBytes(m) {
  if (!(m > 0)) return 1024;
  const unit = 1024 ** Math.max(0, Math.floor(Math.log(m) / Math.log(1024)));
  for (const k of [1, 2, 5, 10, 20, 50, 100, 200, 500]) if (k * unit >= m) return k * unit;
  return 1024 * unit;
}

const clock = (t) => new Date(t * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });

// Five minutes of one or two series (same unit, one axis), 2 px lines, with a crosshair and tooltip on hover.
// series: [{ label, values (aligned with R.history), color }]; max: fixed top (100 for %), or null to fit the data.
function spark(key, series, { max = 100, fmt = pct, label = '', height = 56 } = {}) {
  const W = 300;
  const H = height;
  const PAD = 3;
  const times = R.history.map((e) => e.t);
  const now = times.length ? times[times.length - 1] : Date.now() / 1000;
  const t0 = now - WINDOW;
  const x = (t) => ((t - t0) / WINDOW) * W;
  const top = max || niceBytes(Math.max(0, ...series.flatMap((sr) => sr.values.filter(present))));
  const y = (v) => H - PAD - (Math.min(Math.max(v, 0), top) / top) * (H - 2 * PAD);
  const base = H - PAD;
  const segments = (values) => { // broken where a value is missing or the sampler paused
    const segs = [];
    let cur = null;
    values.forEach((v, i) => {
      if (!present(v) || times[i] < t0) { cur = null; return; }
      if (!cur || times[i] - times[i - 1] > 8) { cur = []; segs.push(cur); }
      cur.push([x(times[i]), y(v)]);
    });
    return segs.filter((seg) => seg.length > 1);
  };
  const path = (seg) => seg.map(([px, py], i) => `${i ? 'L' : 'M'}${px.toFixed(1)},${py.toFixed(1)}`).join('');
  const layers = [];
  for (const sr of series) {
    const segs = segments(sr.values);
    if (series.length === 1) {
      for (const seg of segs) {
        layers.push(s('path', { class: 'spark-area', style: `fill: ${sr.color}`,
          d: `${path(seg)}L${seg[seg.length - 1][0].toFixed(1)},${base}L${seg[0][0].toFixed(1)},${base}Z` }));
      }
    }
    for (const seg of segs) layers.push(s('path', { class: 'spark-line', style: `stroke: ${sr.color}`, d: path(seg) }));
  }
  const cross = s('line', { class: 'spark-cross', x1: 0, x2: 0, y1: 0, y2: H, visibility: 'hidden' });
  const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: 'none', 'aria-hidden': 'true' },
    s('line', { class: 'spark-grid', x1: 0, x2: W, y1: y(top), y2: y(top) }),
    s('line', { class: 'spark-grid', x1: 0, x2: W, y1: y(top / 2), y2: y(top / 2) }),
    s('line', { class: 'spark-base', x1: 0, x2: W, y1: base, y2: base }), layers, cross);
  const lastOf = (values) => { for (let i = values.length - 1; i >= 0; i--) if (present(values[i])) return values[i]; return null; };
  const peakOf = (values) => Math.max(...values.filter(present), 0);
  const aria = `${label}, last 5 minutes: ${series.map((sr) => `${sr.label} now ${present(lastOf(sr.values)) ? fmt(lastOf(sr.values)) : 'unknown'}, peak ${fmt(peakOf(sr.values))}`).join('; ')}`;
  const tip = h('div', { class: 'tip', hidden: true });
  const plot = h('div', { class: 'plot spark', role: 'img', 'aria-label': aria, style: { height: `${H}px` } },
    svg, h('span', { class: 'spark-top' }, fmt(top)), tip);
  const hide = () => { tip.hidden = true; cross.setAttribute('visibility', 'hidden'); };
  const showAt = (fx) => {
    if (!times.length) return;
    const t = t0 + fx * WINDOW;
    let best = 0;
    for (let i = 1; i < times.length; i++) if (Math.abs(times[i] - t) < Math.abs(times[best] - t)) best = i;
    if (times[best] < t0) { hide(); return; }
    const cx = x(times[best]);
    cross.setAttribute('x1', cx);
    cross.setAttribute('x2', cx);
    cross.removeAttribute('visibility');
    clear(tip, h('div', { class: 'tip-x' }, clock(times[best])), series.map((sr) => h('div', { class: 'tip-row' },
      h('i', { class: 'key', style: { background: sr.color } }), sr.label, h('span', { class: 'grow' }),
      h('b', {}, present(sr.values[best]) ? fmt(sr.values[best]) : '–'))));
    tip.hidden = false;
    const w = plot.clientWidth;
    const px = (cx / W) * w;
    tip.style.left = `${px + 14 + tip.offsetWidth > w ? Math.max(0, px - 14 - tip.offsetWidth) : px + 14}px`;
  };
  plot.addEventListener('pointermove', (e) => {
    const r = plot.getBoundingClientRect();
    R.hover[key] = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
    showAt(R.hover[key]);
  });
  plot.addEventListener('pointerleave', () => { delete R.hover[key]; hide(); });
  if (present(R.hover[key])) requestAnimationFrame(() => { if (plot.isConnected && present(R.hover[key])) showAt(R.hover[key]); });
  return h('div', { class: 'spark-wrap' },
    series.length > 1 ? h('div', { class: 'legend' }, series.map((sr) => h('span', {}, h('i', { style: { background: sr.color } }), sr.label))) : null,
    plot, h('div', { class: 'spark-axis', 'aria-hidden': 'true' }, h('span', {}, '5 min ago'), h('span', {}, 'now')));
}

function row(label, bar, value) {
  return h('div', { class: 'res-row' }, h('span', { class: 'res-label' }, label), bar, h('span', { class: 'res-num' }, value));
}

function drawCpu(c, snap) {
  const cpu = snap.cpu;
  if (!cpu) {
    c.value.textContent = '–';
    clear(c.body, h('div', { class: 'muted' }, snap.available === false ? 'CPU usage is read from /proc: Linux servers only.' : 'Measuring…'));
    return;
  }
  c.value.textContent = pct(cpu.total);
  clear(c.body,
    h('div', { class: 'res-sub' }, `${cpu.count} core${cpu.count > 1 ? 's' : ''}${cpu.model ? ` · ${cpu.model}` : ''}`),
    snap.load ? h('div', { class: 'res-sub' }, `Load average ${snap.load.map((v) => v.toFixed(2)).join(' · ')}`,
      h('span', { class: 'muted' }, ' (1, 5, 15 min)')) : null,
    spark('cpu', [{ label: 'CPU', values: history((e) => e.cpu), color: 'var(--series-1)' }], { label: 'CPU usage' }),
    h('div', { class: 'cores', role: 'list', 'aria-label': 'Usage of each core' }, (cpu.cores || []).map((v, i) => h('div', {
      class: 'core', role: 'listitem', title: `CPU ${i}: ${pct(v)}` }, h('span', { class: 'core-n' }, i), meter(v, { severity: false, label: `CPU ${i}` })))));
}

function drawMemory(c, snap) {
  const m = snap.memory || {};
  if (!m.total) { c.value.textContent = '–'; clear(c.body, h('div', { class: 'muted' }, 'Memory is read from /proc: Linux servers only.')); return; }
  c.value.textContent = pct(m.percent);
  const used = (100 * m.used) / m.total;
  const cache = Math.max(0, Math.min(100 - used, (100 * m.cache) / m.total));
  const level = used >= 95 ? 'crit' : used >= 80 ? 'warn' : '';
  clear(c.body,
    h('div', { class: 'res-sub' }, `${fmtSize(m.used)} used of ${fmtSize(m.total)} · ${fmtSize(m.available)} available`),
    h('div', { class: `mem-block ${level}` },
      h('div', { class: 'stack-meter', role: 'img', 'aria-label': `Memory: ${pct(used)} used by programs, ${pct(cache)} cache` },
        h('i', { class: 'used', style: { width: `${used}%` } }), cache > 0.5 ? h('i', { class: 'cache', style: { width: `${cache}%` } }) : null),
      h('div', { class: 'legend' }, h('span', {}, h('i', { class: 'sq used' }), 'Used by programs', level ? icon('alert') : null),
        h('span', {}, h('i', { class: 'sq cache' }), `Cache ${fmtSize(m.cache)} (given back when needed)`))),
    m.swap_total ? row('Swap', meter((100 * m.swap_used) / m.swap_total, { label: 'Swap used' }), `${fmtSize(m.swap_used)} / ${fmtSize(m.swap_total)}`) : null,
    spark('mem', [{ label: 'Memory', values: history((e) => e.mem), color: 'var(--series-1)' }], { label: 'Memory used' }));
}

function jobLink(p) {
  if (!p.job) return null;
  const uid = p.job.split('/').pop();
  return p.project ? h('a', { class: 'job-chip', href: `#/p/${p.project}/${uid}`, title: `CryoPlug job ${p.job}` }, p.job)
    : h('span', { class: 'job-chip' }, p.job);
}

function fact(label, value) {
  return h('div', { class: 'fact' }, h('span', { class: 'res-label' }, label), h('b', {}, value));
}

function drawGpus(wrap, snap) {
  const gpus = snap.gpus || [];
  if (!gpus.length) {
    const c = card('gpu', 'GPUs');
    c.body.append(snap.gpu_error ? h('div', { class: 'alert error' }, snap.gpu_error)
      : h('div', { class: 'muted' }, 'No NVIDIA GPU found: nvidia-smi is not available on this server ',
        h('span', { class: 'mono' }, '([monitor] nvidia_smi'), ' in the configuration gives its path).'));
    clear(wrap, c.el);
    return;
  }
  clear(wrap, gpus.map((g, pos) => {
    const c = card('gpu', `GPU ${g.index}`);
    c.value.textContent = pct(g.utilization);
    const hist = (k) => history((e) => (e.gpus?.[pos] || [])[k] ?? null);
    const procs = g.processes || [];
    const power = present(g.power) ? `${Math.round(g.power)}${g.power_limit ? ` / ${Math.round(g.power_limit)}` : ''} W` : '–';
    c.body.append(
      h('div', { class: 'res-sub', title: g.uuid }, g.name),
      spark(`gpu${g.index}`, [{ label: 'Utilization', values: hist(0), color: 'var(--series-1)' },
        { label: 'Memory', values: hist(1), color: 'var(--series-2)' }], { label: `GPU ${g.index}` }),
      row('Memory', meter(g.memory_percent, { severity: false, label: `GPU ${g.index} memory used` }), `${fmtGiB(g.memory_used)} / ${fmtGiB(g.memory_total)}`),
      h('div', { class: 'facts' }, fact('Temperature', present(g.temperature) ? `${g.temperature} °C` : '–'), fact('Power', power),
        fact('Fan', present(g.fan) ? `${g.fan} %` : '–')),
      procs.length ? h('table', { class: 'data gpu-procs' }, h('thead', {}, h('tr', {}, ['PID', 'Process', 'Job', 'Memory'].map((t, i) => h('th', { class: i === 3 ? 'num' : null }, t)))),
        h('tbody', {}, procs.map((p) => h('tr', {}, h('td', { class: 'mono' }, p.pid),
          h('td', {}, p.name || '?', p.user ? h('span', { class: 'muted' }, ` · ${p.user}`) : null), h('td', {}, jobLink(p) || h('span', { class: 'muted' }, '—')),
          h('td', { class: 'num' }, fmtGiB(p.memory)))))) : h('div', { class: 'muted small' }, 'No program on this GPU.'));
    return c.el;
  }));
}

function drawRates(c, snap, kind) {
  const [a, b, la, lb] = kind === 'net' ? ['rx', 'tx', 'Received', 'Sent'] : ['read', 'write', 'Read', 'Written'];
  const now = (kind === 'net' ? snap.network : snap.disk_io) || {};
  clear(c.body,
    h('div', { class: 'rates' }, [[la, now[a], 'var(--series-1)'], [lb, now[b], 'var(--series-2)']].map(([label, v, color]) => h('div', { class: 'rate' },
      h('span', { class: 'res-label' }, h('i', { class: 'sq', style: { background: color } }), label), h('b', {}, fmtRate(v))))),
    spark(kind, [{ label: la, values: history((e) => e[a]), color: 'var(--series-1)' }, { label: lb, values: history((e) => e[b]), color: 'var(--series-2)' }],
      { max: null, fmt: fmtRate, label: kind === 'net' ? 'Network traffic' : 'Disk reads and writes' }),
    h('div', { class: 'muted small' }, kind === 'net' ? 'All network interfaces except loopback.' : 'All local disks (NVMe, SATA, RAID).'));
}

function drawStorage(c, snap) {
  const list = snap.filesystems || [];
  const projects = list.find((fs) => (fs.roles || []).includes('projects'));
  c.value.textContent = projects?.total ? `${fmtSize(projects.free)} free` : '';
  c.value.title = projects?.total ? `Free space where the projects are (${projects.mount})` : '';
  clear(c.body, list.length ? h('div', { class: 'fs-rows' }, list.map((fs) => h('div', { class: 'fs-row' },
    h('div', { class: 'fs-name' }, h('div', {}, h('span', { class: 'mono strong', title: fs.mount }, fs.mount),
      (fs.roles || []).map((r) => h('span', { class: 'tag role' }, r))), h('div', { class: 'muted small', title: fs.device }, `${fs.type} · ${fs.device}`)),
    fs.error ? h('div', { class: 'fs-error' }, icon('alert'), `${fs.error} (network filesystem?)`)
      : [meter(fs.percent, { label: `${fs.mount} used` }), h('span', { class: 'res-num' }, `${fmtSize(fs.free)} free of ${fmtSize(fs.total)}`),
        h('span', { class: `res-num strong ${fs.percent >= 95 ? 'bad' : fs.percent >= 80 ? 'warn' : ''}`, title: fs.percent >= 80 ? 'Almost full' : null },
          fs.percent >= 80 ? icon('alert') : null, pct(fs.percent))]))) : h('div', { class: 'muted' }, 'No filesystem found.'));
}

function processCard() {
  const c = card('list', 'Processes', 'full');
  const only = h('input', { type: 'checkbox', id: 'res-jobs-only', checked: R.jobsOnly, onchange: (e) => {
    R.jobsOnly = e.target.checked;
    if (R.snap) drawProcesses(c, R.snap);
  } });
  c.count = h('span', { class: 'muted small' });
  c.table = h('div', { class: 'table-scroll proc-scroll' });
  c.body.append(h('div', { class: 'row res-tools' }, h('div', { class: 'field check' }, only, h('label', { for: 'res-jobs-only' }, 'CryoPlug jobs only')),
    h('span', { class: 'grow' }), c.count), c.table);
  return c;
}

const cpuTime = (sec) => {
  const hh = Math.floor(sec / 3600);
  const mm = Math.floor((sec % 3600) / 60);
  const ss = String(sec % 60).padStart(2, '0');
  return hh ? `${hh}:${String(mm).padStart(2, '0')}:${ss}` : `${mm}:${ss}`;
};

function drawProcesses(c, snap) {
  const procs = snap.processes || { top: [], count: 0, running: 0 };
  const keys = { cpu: (p) => p.cpu, memory: (p) => p.memory, gpu: (p) => p.gpu_memory || 0, time: (p) => p.time };
  const rows = procs.top.filter((p) => !R.jobsOnly || p.job).sort((a, b) => keys[R.sort](b) - keys[R.sort](a));
  c.count.textContent = `${procs.count} processes · ${procs.running} running · busiest shown`;
  const cols = [['PID'], ['User'], ['CPU %', 'cpu'], ['Memory', 'memory'], ['GPU memory', 'gpu'], ['CPU time', 'time'], ['Command']];
  const scroll = c.table.scrollTop;
  clear(c.table, rows.length ? h('table', { class: 'data procs' },
    h('thead', {}, h('tr', {}, cols.map(([label, key]) => h('th', { class: key ? 'num' : null, 'aria-sort': key && R.sort === key ? 'descending' : null },
      key ? h('button', { type: 'button', class: `th-sort ${R.sort === key ? 'on' : ''}`, title: `Sort by ${label.toLowerCase()}`,
        onclick: () => { R.sort = key; drawProcesses(c, R.snap); } }, label, R.sort === key ? ' ▾' : '') : label)))),
    h('tbody', {}, rows.map((p) => h('tr', { class: p.job ? 'job-proc' : null },
      h('td', { class: 'mono' }, p.pid), h('td', {}, p.user),
      h('td', { class: 'num' }, p.cpu.toFixed(1)),
      h('td', { class: 'num' }, fmtSize(p.memory), present(p.memory_percent) ? h('span', { class: 'muted' }, ` ${p.memory_percent.toFixed(1)}%`) : null),
      h('td', { class: 'num' }, p.gpu_memory ? `${fmtGiB(p.gpu_memory)} · GPU ${p.gpus.join(',')}` : ''),
      h('td', { class: 'num mono' }, cpuTime(p.time)),
      h('td', { class: 'cmd' }, jobLink(p), h('span', { class: 'mono', title: p.cmd }, p.cmd))))))
    : h('div', { class: 'muted', style: { padding: '10px 4px' } }, R.jobsOnly ? 'No CryoPlug job is running.' : 'No process.'));
  c.table.scrollTop = scroll;
}

// ------------------------------------------------------------------ queue
function startQueue(body, gen) {
  const tick = async () => {
    if (gen !== R.gen) return;
    if (!document.hidden) {
      try {
        const q = await api.queue();
        if (gen !== R.gen) return;
        drawQueue(body, q, tick);
      } catch (e) {
        if (gen === R.gen) clear(body, h('div', { class: 'alert error' }, e.message));
      }
    }
    if (gen === R.gen) later(tick, 4000);
  };
  tick();
}

function laneCard(l, isDefault, holders) {
  const gpus = l.type === 'local' ? l.gpus || [] : [];
  const busy = gpus.filter((g) => holders[String(g)]);
  return h('div', { class: 'box lane-card' },
    h('div', { class: 'row' }, icon(l.type === 'cluster' ? 'server' : 'monitor'), h('b', {}, l.name),
      h('span', { class: 'tag' }, l.type === 'cluster' ? 'cluster' : 'this machine'), isDefault ? h('span', { class: 'tag here' }, 'default') : null),
    l.description ? h('div', { class: 'muted small' }, l.description) : null,
    h('div', { class: 'lane-stats' },
      h('div', {}, h('span', { class: 'res-label' }, 'Running'), h('b', {}, `${l.running} / ${l.max_jobs}`)),
      h('div', {}, h('span', { class: 'res-label' }, 'Queued'), h('b', {}, String(l.queued ?? 0))),
      gpus.length ? h('div', {}, h('span', { class: 'res-label' }, 'GPUs in use'), h('b', {}, `${busy.length} / ${gpus.length}`)) : null),
    gpus.length ? h('div', { class: 'gpus' }, gpus.map((g) => {
      const holder = holders[String(g)];
      return h('span', { class: `gpu ${holder ? 'busy' : ''}`, title: holder ? `${holderLabel(holder)}: ${holder.title}` : 'Free' },
        `GPU ${g} · ${holder ? holderLabel(holder) : 'free'}`);
    })) : null,
    l.type === 'cluster' && (l.partitions || []).length ? h('div', { class: 'muted small' }, `Partitions: ${l.partitions.join(', ')}`) : null);
}

function drawQueue(body, q, refresh) {
  const holders = {}; // GPU -> job, over all local lanes (they may share GPUs)
  for (const l of q.lanes) if (l.type === 'local') Object.assign(holders, l.gpu_jobs || {});
  // Other people's jobs (with accounts) only show what occupies the lanes.
  const rows = q.jobs.map((j) => h('tr', { class: j.hidden ? 'other' : '' },
    h('td', {}, j.hidden ? h('span', { class: 'muted' }, '—') : h('a', { href: `#/p/${j.project_uid}/${j.uid}` }, `${j.project_uid} / ${j.uid}`)),
    h('td', {}, j.title, h('div', { class: 'muted small' }, j.hidden ? `job of ${j.owner || 'another user'}`
      : [j.project_title, accounts() && j.owner && j.owner !== me().username ? j.owner : null].filter(Boolean).join(' · '))),
    h('td', {}, statusChip(j.status)),
    h('td', { class: 'small' }, j.message || ''),
    h('td', {}, j.lane || ''), h('td', {}, (j.gpus || []).join(', ')), h('td', {}, jobDuration(j)),
    h('td', {}, j.hidden ? null : btn(j.status === 'queued' ? 'Dequeue' : 'Stop', async () => {
      if (j.status !== 'queued' && !(await confirmDialog('Stop job', `Stop ${j.project_uid}/${j.uid}?`, 'Stop', true))) return;
      try { await guard(api.killJob(j.project_uid, j.uid), 'Stopped'); } catch { return; }
      refresh();
    }, { cls: 'small danger' }))));
  clear(body,
    h('div', { class: 'lane-cards' }, q.lanes.map((l, i) => laneCard(l, i === 0, holders))),
    h('div', { class: 'section' }, h('h4', {}, 'Active and queued jobs'),
      q.jobs.length ? h('div', { class: 'box table-box' }, h('table', { class: 'data' },
        h('thead', {}, h('tr', {}, ['Job', 'Title', 'Status', 'Message', 'Lane', 'GPU', 'Time', ''].map((c) => h('th', {}, c)))), h('tbody', {}, rows)))
        : h('div', { class: 'empty' }, 'Nothing is running or queued.')));
}

// ------------------------------------------------------------------ cluster
function startCluster(body, gen) {
  clear(body, h('div', { class: 'muted' }, 'Asking SLURM (sinfo, squeue)…'));
  const tick = async () => {
    if (gen !== R.gen) return;
    if (!document.hidden) {
      try {
        const lanes = await api.cluster();
        if (gen !== R.gen) return;
        drawCluster(body, lanes);
      } catch (e) {
        if (gen === R.gen) clear(body, h('div', { class: 'alert error' }, e.message));
      }
    }
    if (gen === R.gen) later(tick, 20000);
  };
  tick();
}

const nodeClass = (st) => (/^idle/.test(st) ? 'idle' : /^(mix|alloc|comp)/.test(st) ? 'busy' : /^(down|drain|fail|maint|inval|no_resp|unk)/.test(st) ? 'bad' : '');

function memPerNode(v) {
  const n = parseInt(v, 10);
  return Number.isNaN(n) ? v || '—' : `${fmtSize(n * 1024 * 1024)}${String(v).endsWith('+') ? '+' : ''}`;
}

function drawCluster(body, lanes) {
  if (!lanes.length) { clear(body, h('div', { class: 'empty' }, 'No cluster lane.')); return; }
  clear(body, lanes.map((c) => h('section', { class: 'box res-card cluster-card' },
    h('div', { class: 'res-card-head' }, icon('server'), h('h3', {}, c.lane), c.description ? h('span', { class: 'muted small' }, c.description) : null,
      h('span', { class: 'grow' }), c.available && c.jobs ? h('span', { class: 'res-sub' }, `${c.jobs.running} running · ${c.jobs.pending} pending in these partitions`) : null),
    !c.available ? h('div', { class: 'alert warn' }, c.message || 'SLURM did not answer.')
      : h('div', { class: 'table-box' }, h('table', { class: 'data partitions' },
        h('thead', {}, h('tr', {}, ['Partition', 'State', 'Nodes', 'CPUs in use', 'GPUs (gres)', 'Memory / node', 'Time limit'].map((t) => h('th', {}, t)))),
        h('tbody', {}, (c.partitions || []).map((p) => {
          const used = p.cpus.total ? (100 * p.cpus.allocated) / p.cpus.total : 0;
          return h('tr', {},
            h('td', {}, h('b', {}, p.name), p.default ? h('span', { class: 'tag here' }, 'default') : null),
            h('td', {}, p.available),
            h('td', {}, h('div', { class: 'node-states' }, Object.entries(p.nodes).map(([st, n]) => h('span', { class: `node-st ${nodeClass(st)}` }, `${n} ${st}`)))),
            h('td', {}, h('div', { class: 'res-row tight' }, meter(used, { severity: false, label: `${p.name}: CPUs in use` }),
              h('span', { class: 'res-num' }, `${p.cpus.allocated} / ${p.cpus.total}`))),
            h('td', { class: 'mono small' }, (p.gres || []).join(', ') || '—'),
            h('td', {}, memPerNode(p.memory_mb)),
            h('td', { class: 'mono' }, p.time_limit));
        })))),
    (c.users || []).length ? h('div', { class: 'cluster-users' }, h('span', { class: 'res-label' }, 'Most jobs'),
      c.users.map(([u, n]) => h('span', { class: 'owner-chip' }, `${u} · ${n}`))) : null)));
}
