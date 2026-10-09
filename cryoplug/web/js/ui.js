// DOM helpers, modals, toasts and formatting. All text is inserted with textContent.

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'style' && typeof v === 'object') {
      for (const [prop, val] of Object.entries(v)) {
        if (prop.startsWith('--')) el.style.setProperty(prop, val); // custom properties need setProperty
        else el.style[prop] = val;
      }
    }
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else if (k === 'dataset') Object.assign(el.dataset, v);
    else if (v === true) el.setAttribute(k, '');
    else el.setAttribute(k, v);
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c === undefined || c === null || c === false) continue;
    el.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

export function clear(el, ...children) {
  el.replaceChildren();
  append(el, children);
  return el;
}

const SVG_NS = 'http://www.w3.org/2000/svg';
export function s(tag, attrs = {}, ...children) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === undefined || c === null || c === false) continue;
    el.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

// Small inline icons (stroke-based, 24x24 viewBox)
const ICONS = {
  plus: 'M12 5v14M5 12h14',
  play: 'M7 5l11 7-11 7z',
  pause: 'M8 5v14M16 5v14',
  'skip-back': 'M18 6l-9 6 9 6zM6 6v12',
  'skip-fwd': 'M6 6l9 6-9 6zM18 6v12',
  stop: 'M6 6h12v12H6z',
  copy: 'M9 9h10v10H9zM5 15V5h10',
  trash: 'M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13',
  undo: 'M9 14L4 9l5-5M4 9h10a6 6 0 0 1 0 12h-3',
  edit: 'M4 20h4L19 9l-4-4L4 16zM13 7l4 4',
  eye: 'M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z',
  download: 'M12 4v11M7 10l5 5 5-5M5 20h14',
  folder: 'M3 6.5A1.5 1.5 0 0 1 4.5 5h4.6l2 2h8.4A1.5 1.5 0 0 1 21 8.5v9A1.5 1.5 0 0 1 19.5 19h-15A1.5 1.5 0 0 1 3 17.5z',
  file: 'M6 3h8l4 4v14H6zM14 3v4h4',
  graph: 'M5 6h4v4H5zM15 14h4v4h-4zM15 4h4v4h-4zM9 8h6M9 8l6 8',
  grid: 'M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z',
  flow: 'M4 6h6v4H4zM14 14h6v4h-6zM7 10v4a2 2 0 0 0 2 2h5',
  next: 'M5 12h14M13 6l6 6-6 6',
  back: 'M19 12H5M11 6l-6 6 6 6',
  refresh: 'M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7',
  close: 'M6 6l12 12M18 6L6 18',
  upload: 'M12 20V9M7 14l5-5 5 5M5 4h14',
  monitor: 'M3 5h18v11H3zM8 20h8M12 16v4',
  check: 'M5 12l5 5L20 7',
  alert: 'M12 4l9 16H3zM12 10v4M12 17v.01',
  x: 'M7 7l10 10M17 7L7 17',
  map: 'M12 3l8 4.5v9L12 21l-8-4.5v-9zM12 12l8-4.5M12 12v9M12 12L4 7.5',
  model: 'M5 18c2-6 4-10 7-10s3 6 7 4M5 6c3 0 4 3 7 3',
  'eye-off': 'M3 3l18 18M10.6 5.1A10 10 0 0 1 12 5c6 0 10 7 10 7a17 17 0 0 1-3.2 4M6.6 6.6C3.9 8.4 2 12 2 12s4 7 10 7c1.8 0 3.4-.6 4.8-1.4M9.9 9.9a3 3 0 0 0 4.2 4.2',
  more: 'M5 12h.01M12 12h.01M19 12h.01',
  chevron: 'M6 9l6 6 6-6',
  rotate: 'M21 12a9 9 0 1 1-3-6.7M21 4v5h-5',
  layers: 'M12 3l9 5-9 5-9-5zM3 13l9 5 9-5',
  cube: 'M12 3l8 4.5v9L12 21l-8-4.5v-9zM4 7.5l8 4.5 8-4.5M12 12v9',
  sun: 'M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4',
  camera: 'M4 8h3l2-3h6l2 3h3v11H4zM12 17a4 4 0 1 0 0-8 4 4 0 0 0 0 8z',
  help: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .8-1 1.5V14M12 17.5v.01',
  target: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM12 12h.01',
  search: 'M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM20 20l-4.3-4.3',
  clock: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7v5l3 2',
  package: 'M12 3l8 4.5v9L12 21l-8-4.5v-9zM8 5.2l8 4.6M12 12v9M4 7.5l8 4.5 8-4.5',
  shield: 'M12 3l8 3v6c0 4.5-3.4 8.2-8 9-4.6-.8-8-4.5-8-9V6zM8.5 12l2.5 2.5 4.5-5',
  sliders: 'M4 7h10M18 7h2M4 17h4M12 17h8M14 4v6M8 14v6',
  user: 'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4.5 20c1.1-3.4 4-5 7.5-5s6.4 1.6 7.5 5',
  users: 'M9 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM2.5 19.5c.9-3 3.4-4.6 6.5-4.6s5.6 1.6 6.5 4.6M16 4.3a3.5 3.5 0 0 1 0 6.4M18.2 14.9c1.6.7 2.7 2 3.3 4.4',
  key: 'M8 19.5a4.5 4.5 0 1 0 0-9 4.5 4.5 0 0 0 0 9zM11.2 11.8L20 3M16.5 6.5l3 3M14.5 8.5l2 2',
  lock: 'M6 11h12v10H6zM8.5 11V7.5a3.5 3.5 0 0 1 7 0V11M12 15v2',
  share: 'M18 8a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM6 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM18 22a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM8.6 13.5l6.8 4M15.4 6.5l-6.8 4',
  gear: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM12 18.5a6.5 6.5 0 1 0 0-13 6.5 6.5 0 0 0 0 13zM12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3M5.3 5.3l2.1 2.1M16.6 16.6l2.1 2.1M5.3 18.7l2.1-2.1M16.6 7.4l2.1-2.1',
  logout: 'M14 4h4.5A1.5 1.5 0 0 1 20 5.5v13a1.5 1.5 0 0 1-1.5 1.5H14M10 16l-4-4 4-4M6 12h10',
};

export function icon(name, cls = '') {
  return s('svg', { viewBox: '0 0 24 24', class: `ic ${cls}`.trim(), 'aria-hidden': 'true' }, s('path', { d: ICONS[name] || ICONS.file }));
}

export function btn(label, onclick, { cls = '', ic = null, title = null, disabled = false } = {}) {
  return h('button', { class: `btn ${cls}`, onclick, title, disabled, type: 'button' }, ic ? icon(ic) : null, label);
}

export function statusChip(status) {
  return h('span', { class: `chip ${status}` }, status);
}

export function statusMark(status) {
  // Status shown with icon + label, never color alone.
  if (!status) return null;
  const map = { good: ['check', 'good'], warn: ['alert', 'needs attention'], bad: ['x', 'problem'],
    pass: ['check', 'pass'], fail: ['x', 'fail'], info: ['alert', 'info'] };
  const [ic, label] = map[status] || ['alert', status];
  const cls = { pass: 'good', fail: 'bad', info: '' }[status] ?? status;
  return h('span', { class: `st ${cls}` }, icon(ic), label);
}

// ---------------------------------------------------------------- toasts
export function toast(message, kind = 'info', ms = 4500) {
  const el = h('div', { class: `toast ${kind}`, role: kind === 'error' ? 'alert' : 'status' }, message);
  document.getElementById('toasts').appendChild(el);
  setTimeout(() => el.remove(), ms);
}

export async function guard(promise, okMessage) {
  try {
    const result = await promise;
    if (okMessage) toast(okMessage, 'ok');
    return result;
  } catch (err) {
    toast(err.message || String(err), 'error', 8000);
    if (err && typeof err === 'object') err.shown = true;
    throw err;
  }
}

// ---------------------------------------------------------------- modals
export function modal({ title, body, footer = [], wide = false, onClose = null }) {
  const root = document.getElementById('modal-root');
  const close = () => {
    backdrop.remove();
    document.removeEventListener('keydown', onKey);
    if (onClose) onClose();
  };
  const onKey = (e) => { if (e.key === 'Escape') close(); };
  const backdrop = h('div', { class: 'modal-backdrop', onmousedown: (e) => { if (e.target === backdrop) close(); } },
    h('div', { class: `modal ${wide ? 'wide' : ''}`, role: 'dialog', 'aria-modal': 'true', 'aria-label': title },
      h('div', { class: 'modal-head' }, h('h3', {}, title), h('span', { class: 'grow' }),
        h('button', { class: 'icon-btn', onclick: close, 'aria-label': 'Close' }, icon('close'))),
      h('div', { class: 'modal-body' }, body),
      footer.length ? h('div', { class: 'modal-foot' }, footer) : null));
  root.appendChild(backdrop);
  document.addEventListener('keydown', onKey);
  const first = backdrop.querySelector('input, select, textarea, button.primary');
  if (first) setTimeout(() => first.focus(), 30);
  return { close, el: backdrop };
}

export function confirmDialog(title, message, okLabel = 'Confirm', danger = false) {
  return new Promise((resolve) => {
    let done = false;
    const m = modal({
      title,
      body: h('p', { style: { margin: 0, whiteSpace: 'pre-wrap' } }, message),
      footer: [
        btn('Cancel', () => { done = true; m.close(); resolve(false); }),
        btn(okLabel, () => { done = true; m.close(); resolve(true); }, { cls: danger ? 'primary danger' : 'primary' }),
      ],
      onClose: () => { if (!done) resolve(false); },
    });
  });
}

// Items: { label, action, ic, danger, disabled }, { category } (heading) or { separator: true }.
// at: { x, y } opens the menu at a point (right-click) instead of below the anchor.
export function popupMenu(anchor, items, at = null) {
  document.querySelectorAll('.menu').forEach((m) => m.remove());
  const menu = h('div', { class: 'menu', role: 'menu' });
  const close = () => {
    menu.remove();
    document.removeEventListener('pointerdown', outside, true);
    document.removeEventListener('keydown', onKey, true);
  };
  const outside = (e) => { if (!menu.contains(e.target)) close(); };
  const buttons = () => [...menu.querySelectorAll('button:not(:disabled)')];
  const onKey = (e) => {
    if (e.key === 'Escape') { close(); anchor?.focus?.(); return; }
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
    e.preventDefault();
    const list = buttons();
    const i = list.indexOf(document.activeElement);
    list[(i + (e.key === 'ArrowDown' ? 1 : -1) + list.length) % list.length]?.focus();
  };
  for (const it of items) {
    if (it.category) menu.appendChild(h('div', { class: 'menu-cat' }, it.category));
    else if (it.separator) {
      if (menu.lastChild && !menu.lastChild.classList.contains('menu-sep')) menu.appendChild(h('div', { class: 'menu-sep', role: 'separator' }));
    } else {
      menu.appendChild(h('button', { type: 'button', role: 'menuitem', class: it.danger ? 'danger' : '', disabled: it.disabled,
        onclick: () => { close(); runAction(it.action); } }, it.ic ? icon(it.ic) : null, it.label));
    }
  }
  if (menu.lastChild?.classList.contains('menu-sep')) menu.lastChild.remove();
  document.body.appendChild(menu);
  if (at) {
    menu.style.top = `${Math.max(8, Math.min(at.y, window.innerHeight - menu.offsetHeight - 8))}px`;
    menu.style.left = `${Math.max(8, Math.min(at.x, window.innerWidth - menu.offsetWidth - 8))}px`;
    document.addEventListener('pointerdown', outside, true);
    document.addEventListener('keydown', onKey, true);
    return menu;
  }
  // Fixed next to the anchor and kept inside the window (opens upwards when there is more room there).
  const r = anchor.getBoundingClientRect();
  const below = window.innerHeight - r.bottom - 12;
  const above = r.top - 12;
  const up = below < 220 && above > below;
  menu.style.maxHeight = `${Math.max(120, Math.min(360, up ? above : below))}px`;
  menu.style.top = `${up ? r.top - 4 - menu.offsetHeight : r.bottom + 4}px`;
  menu.style.left = `${Math.max(8, Math.min(r.left, window.innerWidth - menu.offsetWidth - 8))}px`;
  // Menus open on click, i.e. after the pointer went down: listening right away is safe.
  document.addEventListener('pointerdown', outside, true);
  document.addEventListener('keydown', onKey, true);
  return menu;
}

// Run a click action; a failure is shown instead of silently doing nothing.
export function runAction(fn) {
  try {
    const result = fn();
    if (result && typeof result.catch === 'function') result.catch(showError);
  } catch (err) {
    showError(err);
  }
}

export function showError(err) {
  if (err && err.shown) return; // already reported (guard)
  console.error(err);
  toast(`Interface error: ${err?.message || err}`, 'error', 10000);
}

// ------------------------------------------------------------- formatting
export function fmtTime(ts) {
  if (!ts) return '';
  const d = new Date(ts * 1000);
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

export function fmtDuration(sec) {
  if (sec === null || sec === undefined || Number.isNaN(sec)) return '';
  sec = Math.max(0, Math.round(sec));
  if (sec < 60) return `${sec}s`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ${sec % 60}s`;
  const hh = Math.floor(sec / 3600);
  if (hh < 48) return `${hh}h ${Math.floor((sec % 3600) / 60)}m`;
  return `${Math.floor(hh / 24)}d ${hh % 24}h`;
}

export function jobDuration(job) {
  if (!job.started_at) return '';
  const end = job.ended_at || (['launched', 'running'].includes(job.status) ? Date.now() / 1000 : null);
  return end ? fmtDuration(end - job.started_at) : '';
}

export function fmtSize(n) {
  if (n === undefined || n === null) return '';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i && n < 10 ? 1 : 0)} ${units[i]}`;
}

export function ago(ts) {
  if (!ts) return '';
  const d = Date.now() / 1000 - ts;
  if (d < 60) return 'just now';
  if (d < 3600) return `${Math.floor(d / 60)} min ago`;
  if (d < 86400) return `${Math.floor(d / 3600)} h ago`;
  return `${Math.floor(d / 86400)} d ago`;
}

// A long path shortened to its last folders ("…/projects/CP-demo"), for display only.
export function shortPath(path, max = 44) {
  if (!path || path.length <= max) return path || '';
  const parts = path.split('/');
  let out = parts.pop();
  while (parts.length && out.length + parts[parts.length - 1].length + 1 <= max - 2) out = `${parts.pop()}/${out}`;
  return `…/${out}`;
}

function initials(user) {
  const parts = (user.full_name || user.username || '?').trim().split(/[\s._@-]+/).filter(Boolean);
  return ((parts[0]?.[0] || '?') + (parts.length > 1 ? parts[parts.length - 1][0] : '')).toUpperCase();
}

// A round badge with a user's initials, in a colour of its own for each user name.
export function avatar(user, cls = '') {
  let hue = 0;
  for (const c of user.username || '') hue = (hue * 31 + c.charCodeAt(0)) % 360;
  return h('span', { class: `avatar ${cls}`, style: { '--hue': String(hue) }, 'aria-hidden': 'true' }, initials(user));
}

export function copyText(text) {
  navigator.clipboard?.writeText(text).then(() => toast('Copied to clipboard', 'ok', 1500), () => toast(text));
}

export const ACTIVE = ['queued', 'launched', 'running'];
export const LIVE = ['queued', 'launched', 'running', 'waiting'];
