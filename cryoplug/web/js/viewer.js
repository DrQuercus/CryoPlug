// Mol* viewer page: shows maps (binned previews by default) and models from a CryoPlug project.
import { api } from './api.js';

const MAP_COLORS = [0x3362b2, 0xd95926, 0x199e70];

function showMessage(nodes) {
  const msg = document.getElementById('msg');
  msg.hidden = false;
  msg.replaceChildren(...nodes);
  document.getElementById('app').hidden = true;
}

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const el = document.createElement('script');
    el.src = src;
    el.onload = resolve;
    el.onerror = () => reject(new Error(`Could not load ${src}`));
    document.head.appendChild(el);
  });
}

function loadCss(href) {
  const el = document.createElement('link');
  el.rel = 'stylesheet';
  el.href = href;
  document.head.appendChild(el);
}

async function main() {
  let spec;
  try {
    spec = JSON.parse(decodeURIComponent(location.hash.slice(1)));
  } catch {
    showMessage([document.createTextNode('Nothing to display.')]);
    return;
  }
  const fullBox = document.getElementById('fullres');
  fullBox.checked = new URLSearchParams(location.search).get('full') === '1';
  fullBox.addEventListener('change', () => {
    const url = new URL(location.href);
    if (fullBox.checked) url.searchParams.set('full', '1'); else url.searchParams.delete('full');
    location.href = url.toString();
  });
  document.getElementById('items').textContent = spec.items.map((i) => i.label).join('  ·  ');
  document.title = `CryoPlug viewer — ${spec.items.map((i) => i.label).join(', ')}`;

  const info = await api.info();
  try {
    loadCss(info.molstar.css);
    await loadScript(info.molstar.js);
  } catch (err) {
    const code = document.createElement('code');
    code.textContent = 'cryoplug fetch-viewer';
    showMessage([document.createTextNode(`${err.message}. The 3D viewer (Mol*) is loaded from the internet unless it is installed locally: run `),
      code, document.createTextNode(' on the server (or copy a molstar tarball with --tgz) and reload.')]);
    return;
  }
  const viewer = await window.molstar.Viewer.create('app', {
    layoutIsExpanded: false,
    layoutShowControls: true,
    layoutShowRemoteState: false,
    layoutShowSequence: true,
    layoutShowLog: false,
    layoutShowLeftPanel: true,
    viewportShowExpand: true,
    viewportShowSelectionMode: false,
    viewportShowAnimation: false,
    pdbProvider: 'rcsb',
    emdbProvider: 'rcsb',
  });
  let mapIndex = 0;
  for (const item of spec.items) {
    try {
      if (item.kind === 'model') {
        const format = /\.(cif|mmcif)$/i.test(item.path) ? 'mmcif' : 'pdb';
        await viewer.loadStructureFromUrl(api.fileUrl(spec.project, item.path), format, false, { label: item.label });
      } else {
        const url = fullBox.checked ? api.fileUrl(spec.project, item.path) : api.previewUrl(spec.project, item.path);
        await viewer.loadVolumeFromUrl({ url, format: 'ccp4', isBinary: true },
          [{ type: 'relative', value: 2.5, color: MAP_COLORS[mapIndex % MAP_COLORS.length], alpha: spec.items.some((i) => i.kind === 'model') ? 0.35 : 1 }],
          { entryId: item.label });
        mapIndex += 1;
      }
    } catch (err) {
      console.error(err);
      alert(`Could not load ${item.label}: ${err.message || err}`);
    }
  }
}

main();
