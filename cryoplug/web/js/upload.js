// Files sent from the user's computer into the project (its uploads/ folder on the server), chunk by chunk:
// progress and speed, cancel, a lost chunk sent again; an interrupted file sent again later resumes where it
// stopped, and a file already uploaded is not sent twice (the server recognises it by its fingerprint).
import { api } from './api.js';
import { btn, clear, fmtSize, h, icon, modal, toast } from './ui.js';

const ONE_MB = 1 << 20;

export class UploadCancelled extends Error {
  constructor() { super('Upload cancelled'); }
}

// name, size, date and (when the browser allows it: https or localhost) a hash of the first and last MB
async function fingerprint(file) {
  let fp = `${file.name}:${file.size}:${file.lastModified}`;
  if (window.crypto?.subtle && file.size > 0) {
    try {
      const head = new Uint8Array(await file.slice(0, ONE_MB).arrayBuffer());
      const tail = file.size > ONE_MB ? new Uint8Array(await file.slice(file.size - ONE_MB).arrayBuffer()) : new Uint8Array(0);
      const both = new Uint8Array(head.length + tail.length);
      both.set(head);
      both.set(tail, head.length);
      const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', both));
      fp += `:${[...digest.slice(0, 12)].map((b) => b.toString(16).padStart(2, '0')).join('')}`;
    } catch { /* the name, size and date are enough */ }
  }
  return fp;
}

function textHash(text) { // cyrb53: a short identifier of a folder's content (names and sizes)
  let h1 = 0xdeadbeef;
  let h2 = 0x41c6ce57;
  for (let i = 0; i < text.length; i++) {
    const ch = text.charCodeAt(i);
    h1 = Math.imul(h1 ^ ch, 2654435761);
    h2 = Math.imul(h2 ^ ch, 1597334677);
  }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
  return (4294967296 * (2097151 & h2) + (h1 >>> 0)).toString(16);
}

// ------------------------------------------------------------------ choosing files
// Hidden files are left out (.DS_Store; macOS's ._name files would even take the name of the file they describe).
const visible = (items) => items.filter((it) => !it.relPath.split('/').some((part) => part.startsWith('.')));

// The browser's file dialog: files (several if `multiple`), or a whole folder (`directory`).
export function pickFiles({ multiple = false, directory = false, accept = '' } = {}) {
  return new Promise((resolve) => {
    const input = h('input', { type: 'file', multiple: multiple || directory, accept: accept || null, hidden: true });
    if (directory) input.webkitdirectory = true;
    const finish = (list) => { input.remove(); resolve(list); };
    input.addEventListener('change', () => finish(visible([...input.files].map((file) => ({ file, relPath: file.webkitRelativePath || file.name })))));
    input.addEventListener('cancel', () => finish([]));
    document.body.appendChild(input);
    input.click();
  });
}

// Files dragged from the computer onto the page (folders included, with their inner paths).
export function draggingFiles(e) {
  return [...(e.dataTransfer?.types || [])].includes('Files');
}

export async function filesFromDrop(dataTransfer) {
  const out = [];
  const readAll = (reader) => new Promise((resolve) => {
    const all = [];
    const next = () => reader.readEntries((batch) => { if (!batch.length) resolve(all); else { all.push(...batch); next(); } }, () => resolve(all));
    next();
  });
  const walk = async (entry, prefix) => {
    if (entry.isFile) {
      const file = await new Promise((resolve) => entry.file(resolve, () => resolve(null)));
      if (file) out.push({ file, relPath: prefix + file.name });
    } else if (entry.isDirectory) {
      for (const child of await readAll(entry.createReader())) await walk(child, `${prefix}${entry.name}/`);
    }
  };
  const entries = [...(dataTransfer.items || [])].map((it) => (it.kind === 'file' && it.webkitGetAsEntry ? it.webkitGetAsEntry() : null));
  if (entries.some(Boolean)) {
    for (const entry of entries) if (entry) await walk(entry, '');
  } else {
    for (const file of dataTransfer.files || []) out.push({ file, relPath: file.name });
  }
  return visible(out);
}

// ------------------------------------------------------------------ sending
function putChunk(puid, rel, offset, blob, onProgress, current) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    current.xhr = xhr;
    xhr.open('PUT', `/api/projects/${puid}/uploads?rel=${encodeURIComponent(rel)}&offset=${offset}`);
    xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    xhr.upload.onprogress = (e) => onProgress(e.loaded);
    xhr.onload = () => {
      let data = null;
      try { data = JSON.parse(xhr.responseText); } catch { data = null; }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else if (xhr.status === 409 && data && Number.isFinite(data.offset)) resolve({ conflict: true, offset: data.offset });
      else {
        if (xhr.status === 401) location.assign(`/login?${new URLSearchParams({ next: location.pathname })}${location.hash}`);
        const err = new Error(data?.detail || (xhr.status === 413 ? 'Chunk refused as too large (a proxy in front of CryoPlug limits the size of requests?)'
          : `Upload failed (HTTP ${xhr.status})`));
        err.status = xhr.status;
        reject(err);
      }
    };
    xhr.onerror = () => { const err = new Error('Network error'); err.network = true; reject(err); };
    xhr.onabort = () => reject(new UploadCancelled());
    xhr.send(blob);
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// One file into the uploads folder (`folder`: a subfolder reserved for a folder upload). Returns the server's
// description: rel, path (on the server), done, reused (identical file already there).
async function uploadFile(puid, file, folder, onProgress, current) {
  const fp = await fingerprint(file);
  let info = await api.startUpload(puid, { name: file.name, size: file.size, folder, fingerprint: fp });
  const chunk = info.chunk_size || 16 * ONE_MB;
  let offset = info.offset;
  onProgress(offset);
  let failures = 0;
  while (!info.done) {
    if (current.cancelled) throw new UploadCancelled();
    const end = Math.min(file.size, offset + chunk);
    try {
      const res = await putChunk(puid, info.rel, offset, file.slice(offset, end), (loaded) => onProgress(offset + loaded), current);
      failures = 0;
      if (res.conflict) { offset = res.offset; continue; } // a chunk already there (its answer was lost): go on from the server's offset
      info = res;
      offset = res.offset;
      onProgress(offset);
    } catch (err) {
      if (err instanceof UploadCancelled || current.cancelled) throw new UploadCancelled();
      if (!err.network || failures >= 6) throw err;
      failures += 1; // network trouble: wait, then send the same chunk again (409 tells if it had arrived)
      await sleep(Math.min(30000, 1000 * 2 ** failures));
    }
  }
  return info;
}

// Upload items [{file, relPath}] (a relPath with folders: a folder upload, kept whole in its own subfolder).
// progress({index, file, sent, total, done, reused}) is called as bytes go. Returns {files: [{file, relPath, info}],
// folders: {name: {folder, path}}}.
export async function uploadItems(puid, items, progress = () => {}, current = {}) {
  const folders = {};
  const tops = [...new Set(items.filter((it) => it.relPath.includes('/')).map((it) => it.relPath.split('/')[0]))];
  for (const top of tops) {
    const inside = items.filter((it) => it.relPath.startsWith(`${top}/`));
    const fp = textHash(inside.map((it) => `${it.relPath}:${it.file.size}`).sort().join('|'));
    folders[top] = await api.startUpload(puid, { reserve_folder: top, fingerprint: fp });
  }
  const files = [];
  for (const [index, it] of items.entries()) {
    const parts = it.relPath.split('/');
    const folder = parts.length > 1 ? [folders[parts[0]].folder, ...parts.slice(1, -1)].join('/') : '';
    const info = await uploadFile(puid, it.file, folder, (sent) => progress({ index, file: it.file, sent, total: it.file.size }), current);
    progress({ index, file: it.file, sent: it.file.size, total: it.file.size, done: true, reused: info.reused });
    files.push({ ...it, info });
  }
  return { files, folders };
}

// The same, with a dialog showing each file, the total, the speed and the time left. Resolves with the result,
// or null when cancelled (the files already complete stay; an interrupted one resumes when sent again).
export function uploadDialog(puid, items, { title = 'Upload from this computer' } = {}) {
  return new Promise((resolve) => {
    const total = items.reduce((s, it) => s + it.file.size, 0);
    const sent = items.map(() => 0);
    const rows = items.map((it) => {
      const bar = h('i', { style: { width: '0%' } });
      const status = h('span', { class: 'muted small up-status' }, fmtSize(it.file.size));
      return { bar, status, el: h('div', { class: 'up-row' }, h('div', { class: 'up-name', title: it.relPath }, icon('file'), it.relPath),
        h('span', { class: 'meter up-meter' }, bar), status) };
    });
    const summary = h('div', { class: 'up-summary' });
    const current = { cancelled: false, xhr: null };
    const started = performance.now();
    let finished = false;
    const draw = () => {
      const done = sent.reduce((s, n) => s + n, 0);
      const secs = (performance.now() - started) / 1000;
      const speed = secs > 1 ? done / secs : 0;
      const left = speed > 0 ? (total - done) / speed : 0;
      clear(summary, `${total ? Math.floor((100 * done) / total) : 100} % · ${fmtSize(done)} of ${fmtSize(total)}`,
        speed ? ` · ${fmtSize(speed)}/s` : '', speed && left > 2 ? ` · about ${left > 90 ? `${Math.round(left / 60)} min` : `${Math.round(left)} s`} left` : '');
    };
    const guardLeave = (e) => { e.preventDefault(); e.returnValue = ''; };
    window.addEventListener('beforeunload', guardLeave);
    const stop = (value) => {
      if (finished) return;
      finished = true;
      window.removeEventListener('beforeunload', guardLeave);
      resolve(value);
    };
    const cancel = () => {
      current.cancelled = true;
      current.xhr?.abort();
      stop(null);
      m.close();
    };
    const m = modal({
      title,
      wide: true,
      body: h('div', {}, h('p', { class: 'muted small', style: { marginTop: 0 } },
        `${items.length} file${items.length === 1 ? '' : 's'} to the uploads folder of the project, on the server. Closing this window `
        + 'stops the transfer; sending the same files again resumes it.'), h('div', { class: 'up-list' }, rows.map((r) => r.el)), summary),
      footer: [btn('Cancel', cancel)],
      onClose: () => { if (!finished) { current.cancelled = true; current.xhr?.abort(); stop(null); } },
    });
    draw();
    let lastDraw = 0;
    uploadItems(puid, items, (p) => {
      sent[p.index] = p.sent;
      const r = rows[p.index];
      r.bar.style.width = `${p.total ? (100 * p.sent) / p.total : 100}%`;
      if (p.done) r.status.textContent = p.reused ? 'already on the server' : 'done';
      else r.status.textContent = `${fmtSize(p.sent)} / ${fmtSize(p.total)}`;
      if (p.done || performance.now() - lastDraw > 250) { lastDraw = performance.now(); draw(); }
    }, current).then((result) => {
      draw();
      stop(result);
      setTimeout(() => m.close(), 400);
    }, (err) => {
      if (err instanceof UploadCancelled) return;
      clear(summary, h('div', { class: 'alert error' }, err.message));
      stop(null);
    });
  });
}

// For a file field of a job: upload what was chosen or dropped and give the value of the field (server path).
// kind: file (one file), dir (a folder), files (paths separated by ';', added to `current`), any (a file or a folder;
// several files are kept together in a folder of their own, e.g. the volumes of a series).
export async function uploadForField(puid, kind, items, current = '') {
  if (!items.length) return null;
  const folder = items.some((it) => it.relPath.includes('/'));
  if (kind === 'dir' && !folder) { toast('This field takes a folder: choose or drop a folder', 'error'); return null; }
  if (kind === 'file' && (items.length > 1 || folder)) { toast('This field takes a single file', 'error'); return null; }
  let list = items;
  if (kind === 'any' && !folder && items.length > 1) {
    const stem = items[0].file.name.replace(/(\.[a-z0-9]+)+$/i, '').replace(/[_-]?\d+$/, '') || 'files';
    list = items.map((it) => ({ ...it, relPath: `${stem}/${it.file.name}` }));
  }
  const res = await uploadDialog(puid, list);
  if (!res) return null;
  if (kind === 'files') {
    return [...String(current || '').split(';').map((x) => x.trim()).filter(Boolean), ...res.files.map((f) => f.info.path)].join('; ');
  }
  const tops = Object.values(res.folders);
  return tops.length ? tops[0].path : res.files[0].info.path;
}
