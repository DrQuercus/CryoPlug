// Server-side file browser (to pick CryoSPARC job directories, maps, models, FASTA files).
import { api } from './api.js';
import { btn, clear, fmtSize, guard, h, icon, modal } from './ui.js';

const MAP_EXT = /\.(mrc|map|ccp4|mrcs)$/i;
const MODEL_EXT = /\.(pdb|cif|mmcif|ent)(\.gz)?$/i;

export function browseFiles({ start = '', kind = 'any', title = 'Select a file' } = {}) {
  return new Promise((resolve) => {
    let current = '';
    let selected = null;
    let showHidden = false;
    const pathInput = h('input', { type: 'text', placeholder: '/path/to/directory', onkeydown: (e) => { if (e.key === 'Enter') load(pathInput.value); } });
    const list = h('div', { class: 'fs-list' });
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
      try { data = await guard(api.fs(path, showHidden)); } catch { return; }
      current = data.path;
      pathInput.value = data.path;
      selected = null;
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

    const m = modal({
      title,
      wide: true,
      body: h('div', {},
        h('div', { class: 'row' }, pathInput, btn('Go', () => load(pathInput.value))),
        list, info,
        h('label', { class: 'row small muted', for: 'fs-hidden', style: { marginTop: '6px' } }, hiddenBox, 'Show hidden files')),
      footer: [btn('Cancel', () => { done = true; m.close(); resolve(null); }), choose],
      onClose: () => { if (!done) resolve(null); },
    });
    load(start);
  });
}
