// Server-side file browser (to pick CryoSPARC job directories, maps, models, FASTA files). From a project, files can
// also be sent from this computer (Upload, or dropped on the list): they land in the project's uploads folder.
import { api } from './api.js';
import { btn, clear, fmtSize, guard, h, icon, modal, shortPath } from './ui.js';
import { draggingFiles, filesFromDrop, pickFiles, uploadDialog } from './upload.js';

const MAP_EXT = /\.(mrc|map|ccp4|mrcs)$/i;
const MODEL_EXT = /\.(pdb|cif|mmcif|ent)(\.gz)?$/i;

export function browseFiles({ start = '', kind = 'any', title = 'Select a file', project = null } = {}) {
  return new Promise((resolve) => {
    let current = '';
    let selected = null;
    let showHidden = false;
    const pathInput = h('input', { type: 'text', placeholder: '/path/to/directory', onkeydown: (e) => { if (e.key === 'Enter') load(pathInput.value); } });
    const list = h('div', { class: 'fs-list' });
    // Shortcuts to each folder one may browse (with accounts, a user's projects folder and data folders).
    const roots = h('div', { class: 'fs-roots', hidden: true });
    const info = h('div', { class: 'muted small', style: { marginTop: '6px' } });
    const hiddenBox = h('input', { type: 'checkbox', id: 'fs-hidden', onchange: () => { showHidden = hiddenBox.checked; load(current); } });
    const choose = btn(kind === 'dir' ? 'Select this folder' : 'Select', () => {
      const value = kind === 'dir' ? (selected && selected.is_dir ? selected.path : current) : selected?.path;
      if (!value) return;
      done = true; m.close(); resolve(value);
    }, { cls: 'primary' });
    let done = false;

    async function load(path) {
      let data;
      try { data = await guard(api.fs(path, showHidden, project?.uid)); } catch { return; }
      current = data.path;
      pathInput.value = data.path;
      selected = null;
      roots.hidden = !(data.roots.length > 1 || (data.roots.length === 1 && data.roots[0] !== '/') || uploadsDir);
      clear(roots, h('span', { class: 'muted small' }, 'Your folders:'), data.roots.map((r) => h('button', {
        type: 'button', class: `fs-root ${data.path === r || data.path.startsWith(`${r}/`) ? 'on' : ''}`, title: r, onclick: () => load(r),
      }, icon('folder'), shortPath(r, 34))), uploadsDir ? h('button', {
        type: 'button', class: `fs-root ${data.path === uploadsDir || data.path.startsWith(`${uploadsDir}/`) ? 'on' : ''}`,
        title: `Files sent from your computer: ${uploadsDir}`, onclick: () => load(uploadsDir),
      }, icon('upload'), 'Uploads of this project') : null);
      const items = [];
      if (data.parent) {
        items.push(h('div', { class: 'fs-item', ondblclick: () => load(data.parent), onclick: () => load(data.parent) }, icon('folder'), '..'));
      }
      for (const e of data.entries) {
        const isMap = MAP_EXT.test(e.name), isModel = MODEL_EXT.test(e.name);
        const item = h('div', {
          class: 'fs-item', title: e.path,
          onclick: () => {
            list.querySelectorAll('.fs-item.sel').forEach((x) => x.classList.remove('sel'));
            item.classList.add('sel');
            selected = e;
            info.textContent = e.path;
            if (e.is_dir && kind !== 'dir') load(e.path);
          },
          ondblclick: () => {
            if (e.is_dir) load(e.path);
            else if (kind !== 'dir') { done = true; m.close(); resolve(e.path); }
          },
        }, icon(e.is_dir ? 'folder' : isMap ? 'map' : isModel ? 'model' : 'file'), e.name,
        e.is_dir ? null : h('span', { class: 'size' }, fmtSize(e.size)));
        items.push(item);
      }
      if (!data.entries.length) items.push(h('div', { class: 'fs-item muted' }, '(empty)'));
      clear(list, items);
      info.textContent = kind === 'dir' ? `Current folder: ${data.path}` : '';
    }

    // From this computer: the file (or the folder) uploaded is the one chosen.
    let uploadsDir = null;
    const upload = async (items) => {
      if (!items.length) return;
      if (kind === 'dir' && !items.some((it) => it.relPath.includes('/'))) { guard(Promise.reject(new Error('Choose or drop a folder'))); return; }
      const res = await uploadDialog(project.uid, items);
      if (!res) return;
      const folder = Object.values(res.folders)[0];
      done = true;
      m.close();
      resolve(kind === 'dir' || (folder && kind !== 'file') ? folder.path : res.files[0].info.path);
    };
    const uploadBtn = project ? btn(kind === 'dir' ? 'Upload a folder' : 'Upload from this computer',
      async () => upload(await pickFiles({ directory: kind === 'dir' })), { ic: 'upload',
        title: 'Send it from this computer to the uploads folder of the project, and choose it' }) : null;
    if (project) {
      list.addEventListener('dragover', (e) => { if (draggingFiles(e)) { e.preventDefault(); list.classList.add('dragover'); } });
      list.addEventListener('dragleave', () => list.classList.remove('dragover'));
      list.addEventListener('drop', async (e) => {
        if (!draggingFiles(e)) return;
        e.preventDefault();
        list.classList.remove('dragover');
        upload(await filesFromDrop(e.dataTransfer));
      });
    }

    const m = modal({
      title,
      wide: true,
      body: h('div', {},
        h('div', { class: 'row' }, pathInput, btn('Go', () => load(pathInput.value))),
        roots, list, info,
        h('label', { class: 'row small muted', for: 'fs-hidden', style: { marginTop: '6px' } }, hiddenBox, 'Show hidden files')),
      footer: [uploadBtn, uploadBtn ? h('span', { class: 'grow' }) : null, btn('Cancel', () => { done = true; m.close(); resolve(null); }), choose],
      onClose: () => { if (!done) resolve(null); },
    });
    // the shortcut to the project's uploads, once there are some
    const ready = project ? api.uploads(project.uid).then((u) => { if (u.files.length) uploadsDir = u.folder; }).catch(() => {}) : Promise.resolve();
    ready.then(() => load(start));
  });
}
