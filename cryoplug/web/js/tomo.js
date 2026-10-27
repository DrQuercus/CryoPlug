// Tomography tools of the job panel, in place of RELION 5's napari tools. The tilt series image by image, in the order
// of the tilt angle, with their dose, defocus and CTF fit; while an exclusion job waits, a click excludes an image or
// keeps it (RELION's tilt-image excluder). The tomograms slice by slice: the wheel or ↑ ↓ change the slice, a slab of
// several slices averaged, a low-pass filter, zoom and pan. The picker of particles in the tomograms (RELION's napari
// picker): single particles, spheres (a centre and a radius) or filaments (points along each), drawn as their cross-
// sections on the slice shown; the job turns the picks into RELION's annotations and particle set. The work in progress
// is saved on the server while a job waits; Done finishes the job.
import { api } from './api.js';
import { btn, h } from './ui.js';

const LOWPASS = [0, 20, 30, 50, 80];
const SLABS = [1, 3, 5, 9, 15, 25];
const COLOURS = { pick: '#34c759', near: 'rgba(52, 199, 89, 0.45)', drawing: '#0a84ff', sphere: '#ff9f0a',
  filament: '#bf5af2', removed: '#ff453a' };

const fmt = (n) => Number(n).toLocaleString();
const round2 = (v) => Math.round(v * 100) / 100;
const base = (name) => (name || '').split('/').pop();
const plural = (n, word) => `${fmt(n)} ${word}${n === 1 ? '' : 's'}`;

async function readJson(puid, path) {
  const r = await fetch(api.fileUrl(puid, path));
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

// A waiting tool fills the panel: scrolled to just below the job's header (once, when it opens).
function bringIntoView(root) {
  const panel = root.closest('.panel');
  if (!panel || !root.isConnected) return;
  const head = panel.querySelector('.panel-head');
  const top = head ? head.getBoundingClientRect().bottom : panel.getBoundingClientRect().top;
  panel.scrollTop += root.getBoundingClientRect().top - top - 8;
}

// Saving the work in progress of a waiting job (1.5 s after the last change), with its state shown.
function sessionSaver(puid, job, name, payload) {
  const label = h('span', { class: 'muted small' });
  let dirty = false, timer = null;
  async function save() {
    if (!dirty) return;
    dirty = false;
    try {
      await api.saveSession(puid, job.uid, name, payload());
      label.textContent = 'saved';
    } catch (err) {
      dirty = true;
      label.textContent = `not saved: ${err.message || err}`;
    }
  }
  return {
    label,
    changed() { dirty = true; label.textContent = 'not saved yet'; clearTimeout(timer); timer = setTimeout(save, 1500); },
    stop() { clearTimeout(timer); },
  };
}

// ====================================================================================== tilt series
// `finish(payload)`: Done of a waiting exclusion job ({excluded: {tilt series: [rows]}}).
export function tiltSeriesBrowser(sec, { puid, job, finish = null, widen = null }) {
  const root = h('div', { class: 'tsb', tabindex: 0, role: 'application', 'aria-label': sec.title });
  const editable = !!(sec.editable && sec.waiting && job.status === 'waiting' && finish);
  root.append(h('div', { class: 'muted small' }, 'Loading the tilt series…'));
  Promise.all([readJson(puid, sec.data), editable ? api.session(puid, job.uid, 'exclude').catch(() => ({})) : Promise.resolve({})])
    .then(([info, saved]) => {
      build(info, saved || {});
      if (editable && widen) widen();
      if (editable) requestAnimationFrame(() => requestAnimationFrame(() => bringIntoView(root)));
    })
    .catch((err) => root.replaceChildren(h('div', { class: 'alert error' }, `Tilt series not available: ${err.message || err}`)));
  return root;

  function build(info, saved) {
    const series = info.series || [];
    if (!series.length) { root.replaceChildren(h('div', { class: 'muted' }, 'No tilt series.')); return; }
    // excluded rows of each tilt series: the saved work, else what the limits of the job excluded
    const excluded = new Map(series.map((s) => [s.name, new Set(saved.excluded && saved.excluded[s.name]
      ? saved.excluded[s.name] : s.tilts.filter((t) => t.excluded).map((t) => t.row))]));
    let cur = 0, sel = 0;
    const saver = editable ? sessionSaver(puid, job, 'exclude', () => payload()) : null;
    const payload = () => ({ excluded: Object.fromEntries([...excluded].map(([k, v]) => [k, [...v].sort((a, b) => a - b)])) });

    const title = h('span', { class: 'pk-name mono' });
    const summary = h('span', { class: 'muted small' });
    const strip = h('div', { class: 'tsb-strip', role: 'listbox', 'aria-label': 'Tilt images by tilt angle' });
    const big = h('img', { class: 'tsb-big', alt: '' });
    const ctf = h('img', { class: 'tsb-ctf', alt: 'CTF fit' });
    const facts = h('div', { class: 'box kv small tsb-facts' });
    const toggle = editable ? btn('Exclude', () => flip(sel), { cls: 'small', ic: 'close', title: 'Exclude or keep this image (X or space)' }) : null;
    const list = h('div', { class: 'pk-list', role: 'listbox', 'aria-label': 'Tilt series' });
    const rows = series.map((s, k) => h('button', { type: 'button', class: 'pk-row', role: 'option', onclick: () => { show(k); root.focus(); } },
      h('span', { class: 'muted' }, k + 1), h('span', { class: 'pk-row-name', title: s.name }, s.name), h('b', {})));
    list.append(...rows);

    const fact = (label, value) => [h('span', {}, label), h('span', {}, value)];
    function select(i) {
      const s = series[cur];
      sel = Math.max(0, Math.min(s.tilts.length - 1, i));
      const t = s.tilts[sel];
      [...strip.children].forEach((c, j) => { c.classList.toggle('on', j === sel); c.setAttribute('aria-selected', j === sel ? 'true' : 'false'); });
      const card = strip.children[sel];
      if (card) card.scrollIntoView({ block: 'nearest', inline: 'nearest' });
      big.src = t.image && !t.movie ? api.micrographUrl(puid, t.image, 768, 20, info.pixel_size || 0) : '';
      big.alt = `Tilt image at ${t.tilt}°`;
      big.style.visibility = t.image && !t.movie ? 'visible' : 'hidden';
      ctf.src = t.ctf_image ? api.ctfImageUrl(puid, t.ctf_image, 256) : '';
      ctf.style.display = t.ctf_image ? '' : 'none';
      const out = excluded.get(s.name).has(t.row);
      facts.replaceChildren(...fact('Tilt angle', `${t.tilt}°`), ...fact('Dose before', t.dose === null ? '–' : `${t.dose} e⁻/Å²`),
        ...(t.defocus !== null ? fact('Defocus', `${t.defocus.toFixed(2)} µm`) : []),
        ...(t.ctf_res !== null ? fact('CTF fit', `${t.ctf_res} Å`) : []),
        ...(t.fom !== null ? fact('CTF figure of merit', String(t.fom)) : []),
        ...(t.motion !== null ? fact('Total motion', `${t.motion} Å`) : []),
        ...fact('Image', h('span', { class: 'mono', title: t.image || '' }, base(t.image))),
        ...fact('Status', out ? h('b', { class: 'tsb-out' }, 'excluded') : 'kept'));
      if (toggle) toggle.lastChild.textContent = out ? 'Keep' : 'Exclude';
    }
    function counts() {
      series.forEach((s, k) => {
        const n = s.tilts.length - excluded.get(s.name).size;
        rows[k].lastChild.textContent = `${n}/${s.tilts.length}`;
        rows[k].classList.toggle('edited', excluded.get(s.name).size > 0);
      });
      const s = series[cur];
      const out = excluded.get(s.name).size;
      summary.textContent = `${plural(s.tilts.length, 'image')}${out ? ` · ${out} excluded` : ''}`;
      const all = series.reduce((a, x) => a + x.tilts.length, 0);
      const outAll = [...excluded.values()].reduce((a, x) => a + x.size, 0);
      total.replaceChildren(h('b', {}, fmt(all - outAll)), ` of ${fmt(all)} tilt images kept in ${plural(series.length, 'tilt series')}`);
      if (doneBtn) doneBtn.disabled = all === outAll;
    }
    function show(k, keepSel = false) {
      cur = (k + series.length) % series.length;
      const s = series[cur];
      title.textContent = `${cur + 1}/${series.length} · ${s.name}`;
      rows.forEach((r, j) => { r.classList.toggle('on', j === cur); r.setAttribute('aria-selected', j === cur ? 'true' : 'false'); });
      strip.replaceChildren(...s.tilts.map((t, j) => {
        const out = excluded.get(s.name).has(t.row);
        const card = h('button', { type: 'button', class: `tsb-card${out ? ' out' : ''}`, role: 'option',
          title: `${t.tilt}°${t.ctf_res !== null ? ` · CTF fit ${t.ctf_res} Å` : ''}${out ? ' · excluded' : ''}`,
          onclick: () => { if (editable && sel === j) flip(j); else select(j); root.focus(); },
          ondblclick: () => { if (editable) flip(j); } },
        t.image && !t.movie ? h('img', { src: api.micrographUrl(puid, t.image, 192, 30, info.pixel_size || 0), alt: '', loading: 'lazy' })
          : h('span', { class: 'tsb-movie muted small' }, 'movie'),
        h('span', { class: 'tsb-tilt' }, `${t.tilt > 0 ? '+' : ''}${t.tilt}°`),
        t.ctf_res !== null ? h('span', { class: 'tsb-res' }, `${t.ctf_res} Å`) : null);
        return card;
      }));
      counts();
      select(keepSel ? sel : s.tilts.findIndex((t) => Math.abs(t.tilt) === Math.min(...s.tilts.map((x) => Math.abs(x.tilt)))));
    }
    function flip(j) {
      const s = series[cur];
      const t = s.tilts[j];
      const set = excluded.get(s.name);
      if (set.has(t.row)) set.delete(t.row); else set.add(t.row);
      strip.children[j].classList.toggle('out', set.has(t.row));
      select(j);
      counts();
      saver.changed();
    }
    root.addEventListener('keydown', (ev) => {
      if (ev.target.closest('input, select, textarea')) return;
      const keys = { ArrowRight: () => select(sel + 1), ArrowLeft: () => select(sel - 1), ArrowDown: () => show(cur + 1),
        ArrowUp: () => show(cur - 1), PageDown: () => show(cur + 1), PageUp: () => show(cur - 1) };
      if (keys[ev.key]) { keys[ev.key](); ev.preventDefault(); return; }
      if (editable && (ev.key === 'x' || ev.key === 'X' || ev.key === ' ')) { flip(sel); ev.preventDefault(); }
    });

    const total = h('div', { class: 'pk-total' });
    const keepAll = editable ? btn('Keep all', () => { excluded.get(series[cur].name).clear(); show(cur, true); saver.changed(); },
      { cls: 'small', title: 'Keep every image of this tilt series' }) : null;
    const doneBtn = editable ? btn('Done', async () => { saver.stop(); await finish(payload()); }, { cls: 'primary', ic: 'check',
      title: 'Write the tilt series without the excluded images (selected_tilt_series.star)' }) : null;
    const toolbar = h('div', { class: 'pk-tools' },
      btn('', () => show(cur - 1), { cls: 'small', ic: 'back', title: 'Previous tilt series (↑)' }),
      btn('', () => show(cur + 1), { cls: 'small', ic: 'next', title: 'Next tilt series (↓)' }),
      title, summary, h('span', { class: 'grow' }), keepAll);
    const legend = h('div', { class: 'pk-legend muted small' }, editable
      ? h('span', {}, 'click: select, click again (or X, space): exclude / keep · ← →: images · ↑ ↓: tilt series')
      : h('span', {}, '← →: images · ↑ ↓: tilt series'));
    const viewer = h('div', { class: 'tsb-view' }, h('div', { class: 'tsb-bigwrap' }, big), h('div', { class: 'tsb-side' }, ctf, facts, toggle));
    root.replaceChildren(
      h('div', { class: 'pk-head' }, h('h4', {}, sec.title), h('span', { class: 'grow' }), saver ? saver.label : null, doneBtn),
      h('div', { class: 'pk-body' }, h('div', { class: 'pk-main' }, toolbar, strip, viewer, legend), h('div', { class: 'pk-side' }, total, list)));
    show(0);
  }
}

// ====================================================================================== slices of a tomogram
// A canvas showing a slab of a tomogram, in voxels of the tomogram: zoom (Ctrl + wheel, + -), pan (drag), the slice with
// the wheel or ↑ ↓. `overlay(g, toScreen, scale)` draws on the slice; `click(x, y)` in voxels of the tomogram.
function sliceView(root, puid, { overlay = null, click = null, hover = null, changed = null } = {}) {
  const canvas = h('canvas', { class: 'pk-canvas tm-canvas', 'aria-label': 'Slice of the tomogram' });
  const stage = h('div', { class: 'pk-stage' }, canvas);
  const g = canvas.getContext('2d');
  const status = h('span', { class: 'pk-status muted small' });
  const st = { tomo: null, z: 0, thickness: 1, lowpass: 0, img: null, url: '', factor: 1, zoom: 1, cx: 0, cy: 0 };
  const zInput = h('input', { type: 'range', min: 0, max: 0, step: 1, value: 0, 'aria-label': 'Slice',
    oninput: (e) => setZ(+e.target.value) });
  const zLabel = h('span', { class: 'mono small tm-z' });
  const slab = h('select', { 'aria-label': 'Slices averaged', onchange: (e) => { st.thickness = +e.target.value; load(); } },
    SLABS.map((v) => h('option', { value: v }, v === 1 ? '1 slice' : `${v} slices`)));
  const lp = h('select', { 'aria-label': 'Low-pass filter of the display', onchange: (e) => { st.lowpass = +e.target.value; load(); } },
    LOWPASS.map((v) => h('option', { value: v }, v ? `${v} Å` : 'none')));
  const controls = h('div', { class: 'pk-tools tm-controls' },
    h('label', { class: 'muted small grow tm-zrow', title: 'Slice (wheel or ↑ ↓)' }, 'Z', zInput, zLabel),
    h('label', { class: 'muted small', title: 'Slices averaged around the one shown' }, 'Slab', slab),
    h('label', { class: 'muted small', title: 'Low-pass filter of the display' }, 'Filter', lp),
    btn('', () => { fit(); draw(); }, { cls: 'small', ic: 'expand', title: 'Whole slice (F)' }));
  const size = () => (root.closest('.panel.wide') ? 1024 : 768);

  function setTomogram(t) {
    const same = st.tomo && t && st.tomo.path === t.path;
    st.tomo = t;
    if (!t) { st.img = null; draw(); return; }
    const depth = t.shape[2];
    zInput.max = String(Math.max(0, depth - 1));
    if (!same) st.z = Math.floor(depth / 2);
    load(!same);
  }
  function setZ(z) {
    if (!st.tomo) return;
    st.z = Math.max(0, Math.min(st.tomo.shape[2] - 1, Math.round(z)));
    load();
  }
  function load(refit = false) {
    const t = st.tomo;
    if (!t) return;
    zInput.value = String(st.z);
    zLabel.textContent = `${st.z + 1}/${t.shape[2]}`;
    const sz = size();
    st.factor = Math.max(1, Math.ceil(Math.max(t.shape[0], t.shape[1]) / Math.max(64, sz)));
    const url = api.tomoSliceUrl(puid, t.path, st.z, st.thickness, sz, st.lowpass);
    if (changed) changed(st.z);
    if (url === st.url) { draw(); return; }
    st.url = url;
    status.textContent = 'loading…';
    const im = new Image();
    im.onload = () => {
      if (st.url !== url) return;
      st.img = im;
      status.textContent = '';
      if (refit || !st.cx) fit();
      resize();
      for (const dz of [1, -1]) { // the neighbouring slices, ahead of the wheel
        const z = st.z + dz;
        if (z >= 0 && z < t.shape[2]) new Image().src = api.tomoSliceUrl(puid, t.path, z, st.thickness, sz, st.lowpass);
      }
    };
    im.onerror = () => { if (st.url === url) status.textContent = 'tomogram not readable'; };
    im.src = url;
  }
  function fit() {
    if (!st.tomo) return;
    st.zoom = 1;
    st.cx = st.tomo.shape[0] / 2;
    st.cy = st.tomo.shape[1] / 2;
  }
  function scale() { // device pixels per voxel
    if (!st.tomo) return 1;
    return Math.min(canvas.width / st.tomo.shape[0], canvas.height / st.tomo.shape[1]) * st.zoom;
  }
  let stageWidth = 0;
  function resize() {
    const dpr = window.devicePixelRatio || 1;
    const avail = stage.clientWidth || 600;
    const aspect = st.tomo ? st.tomo.shape[1] / st.tomo.shape[0] : 1;
    const panel = root.closest('.panel');
    const head = panel?.querySelector('.panel-head');
    const room = (panel ? panel.clientHeight - (head?.offsetHeight || 0) : window.innerHeight - 60) - 170;
    const hgt = Math.max(240, Math.min(Math.round(avail * aspect), room));
    const w = Math.min(avail, Math.round(hgt / aspect));
    canvas.style.width = `${w}px`;
    canvas.style.height = `${hgt}px`;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(hgt * dpr);
    draw();
  }
  function draw() {
    g.setTransform(1, 0, 0, 1, 0, 0);
    g.fillStyle = '#111';
    g.fillRect(0, 0, canvas.width, canvas.height);
    if (!st.img || !st.tomo) return;
    const sc = scale();
    const tx = canvas.width / 2 - st.cx * sc, ty = canvas.height / 2 - st.cy * sc;
    g.imageSmoothingEnabled = st.zoom < 2.5;
    // image pixel i covers voxels i·f … i·f + f − 1: the picture spans voxels −0.5 … w·f − 0.5
    g.setTransform(sc * st.factor, 0, 0, sc * st.factor, tx - 0.5 * sc, ty - 0.5 * sc);
    g.drawImage(st.img, 0, 0);
    g.setTransform(1, 0, 0, 1, 0, 0);
    if (overlay) overlay(g, (x, y) => [x * sc + tx, y * sc + ty], sc, window.devicePixelRatio || 1);
  }
  const toVoxel = (ev) => {
    const rect = canvas.getBoundingClientRect();
    const dpr = canvas.width / rect.width;
    const sc = scale();
    return [st.cx + ((ev.clientX - rect.left) * dpr - canvas.width / 2) / sc, st.cy + ((ev.clientY - rect.top) * dpr - canvas.height / 2) / sc];
  };
  let drag = null;
  canvas.addEventListener('pointerdown', (ev) => {
    canvas.setPointerCapture(ev.pointerId);
    drag = { x: ev.clientX, y: ev.clientY, cx: st.cx, cy: st.cy, moved: false };
  });
  canvas.addEventListener('pointermove', (ev) => {
    if (!drag) { if (hover && st.tomo) hover(...toVoxel(ev)); return; }
    const dx = ev.clientX - drag.x, dy = ev.clientY - drag.y;
    if (!drag.moved && Math.hypot(dx, dy) > 4) drag.moved = true;
    if (drag.moved && st.tomo) {
      const rect = canvas.getBoundingClientRect();
      const css = scale() * rect.width / canvas.width;
      st.cx = drag.cx - dx / css;
      st.cy = drag.cy - dy / css;
      draw();
    }
  });
  canvas.addEventListener('pointerup', (ev) => { if (drag && !drag.moved && click && st.tomo) click(...toVoxel(ev)); drag = null; });
  canvas.addEventListener('pointercancel', () => { drag = null; });
  canvas.addEventListener('wheel', (ev) => {
    if (!st.tomo) return;
    ev.preventDefault();
    if (ev.ctrlKey || ev.metaKey) { // zoom about the pointer
      const [px, py] = toVoxel(ev);
      st.zoom = Math.min(16, Math.max(1, st.zoom * Math.exp(-ev.deltaY / 300)));
      const [qx, qy] = toVoxel(ev);
      st.cx += px - qx;
      st.cy += py - qy;
      draw();
      return;
    }
    setZ(st.z + (ev.deltaY > 0 ? 1 : -1) * Math.max(1, Math.round(Math.abs(ev.deltaY) / 100)));
  }, { passive: false });
  new ResizeObserver(() => {
    if (stage.clientWidth !== stageWidth) { stageWidth = stage.clientWidth; requestAnimationFrame(resize); }
  }).observe(stage);
  function key(ev) {
    if (ev.key === 'ArrowUp') { setZ(st.z - 1); return true; }
    if (ev.key === 'ArrowDown') { setZ(st.z + 1); return true; }
    if (ev.key === 'f' || ev.key === 'F') { fit(); draw(); return true; }
    if (ev.key === '+' || ev.key === '=') { st.zoom = Math.min(16, st.zoom * 1.25); draw(); return true; }
    if (ev.key === '-') { st.zoom = Math.max(1, st.zoom / 1.25); draw(); return true; }
    return false;
  }
  return { el: h('div', { class: 'tm-view' }, controls, stage), status, st, setTomogram, setZ, draw, key, toVoxel };
}

function tomogramList(tomos, onPick, count = null) {
  const list = h('div', { class: 'pk-list', role: 'listbox', 'aria-label': 'Tomograms' });
  const rows = tomos.map((t, k) => h('button', { type: 'button', class: 'pk-row', role: 'option', onclick: () => onPick(k) },
    h('span', { class: 'muted' }, k + 1), h('span', { class: 'pk-row-name', title: t.path }, t.name), h('b', {}, count ? '' : `${t.shape[2]} sl.`)));
  list.append(...rows);
  return {
    el: list,
    highlight(cur) {
      rows.forEach((r, k) => { r.classList.toggle('on', k === cur); r.setAttribute('aria-selected', k === cur ? 'true' : 'false'); });
      const row = rows[cur];
      if (row) {
        if (row.offsetTop < list.scrollTop) list.scrollTop = row.offsetTop;
        else if (row.offsetTop + row.offsetHeight > list.scrollTop + list.clientHeight) list.scrollTop = row.offsetTop + row.offsetHeight - list.clientHeight;
      }
    },
    counts(values) { rows.forEach((r, k) => { r.lastChild.textContent = fmt(values[k]); r.classList.toggle('edited', values[k] > 0); }); },
  };
}

// ====================================================================================== tomogram viewer
export function tomogramViewer(sec, { puid }) {
  const root = h('div', { class: 'tmv', tabindex: 0, role: 'application', 'aria-label': sec.title });
  root.append(h('div', { class: 'muted small' }, 'Loading the tomograms…'));
  readJson(puid, sec.data).then(build)
    .catch((err) => root.replaceChildren(h('div', { class: 'alert error' }, `Tomograms not available: ${err.message || err}`)));
  return root;

  function build(info) {
    const tomos = info.tomograms || [];
    if (!tomos.length) { root.replaceChildren(h('div', { class: 'muted' }, 'No tomogram can be read.')); return; }
    let cur = 0, raw = false;
    const view = sliceView(root, puid);
    const title = h('span', { class: 'pk-name mono' });
    const list = tomogramList(tomos, (k) => { show(k); root.focus(); });
    const anyDenoised = tomos.some((t) => t.denoised && t.raw);
    const rawBox = anyDenoised ? h('input', { type: 'checkbox', onchange: (e) => { raw = e.target.checked; show(cur); } }) : null;
    function show(k) {
      cur = (k + tomos.length) % tomos.length;
      const t = tomos[cur];
      const shown = raw && t.raw ? { ...t, path: t.raw } : t;
      title.textContent = `${cur + 1}/${tomos.length} · ${t.name}${t.denoised && !raw ? ' (denoised)' : ''}`;
      title.title = shown.path;
      list.highlight(cur);
      view.setTomogram(shown);
    }
    root.addEventListener('keydown', (ev) => {
      if (ev.target.closest('input, select, textarea')) return;
      if (ev.key === 'ArrowRight') { show(cur + 1); ev.preventDefault(); return; }
      if (ev.key === 'ArrowLeft') { show(cur - 1); ev.preventDefault(); return; }
      if (view.key(ev)) ev.preventDefault();
    });
    const px = info.pixel_size && info.binning ? `${(info.pixel_size * info.binning).toFixed(2)} Å/voxel` : '';
    const facts = h('div', { class: 'box kv small' },
      h('span', {}, 'Tomograms'), h('span', {}, fmt(tomos.length)),
      h('span', {}, 'Size'), h('span', {}, `${tomos[0].shape.join(' × ')} voxels`),
      px ? h('span', {}, 'Pixel size') : null, px ? h('span', {}, px) : null);
    const toolbar = h('div', { class: 'pk-tools' },
      btn('', () => show(cur - 1), { cls: 'small', ic: 'back', title: 'Previous tomogram (←)' }),
      btn('', () => show(cur + 1), { cls: 'small', ic: 'next', title: 'Next tomogram (→)' }),
      title, h('span', { class: 'grow' }),
      rawBox ? h('label', { class: 'muted small', title: 'The reconstruction instead of the denoised tomogram' }, rawBox, 'Raw') : null,
      view.status);
    root.replaceChildren(h('div', { class: 'pk-head' }, h('h4', {}, sec.title)),
      h('div', { class: 'pk-body' }, h('div', { class: 'pk-main' }, toolbar, view.el,
        h('div', { class: 'pk-legend muted small' }, h('span', {}, 'wheel or ↑ ↓: slice · Ctrl + wheel: zoom · drag: move · ← →: tomograms · F: whole slice'))),
      h('div', { class: 'pk-side' }, facts, list.el)));
    show(0);
  }
}

// ====================================================================================== picker
// The picks of each tomogram in voxels of the tomogram shown: particles [x, y, z], spheres [x, y, z, radius], filaments
// [[x, y, z], ...]. `finish(payload)`: Done ({particles|spheres|filaments: {tomogram: picks}}).
export function tomoPicker(sec, { puid, job, finish = null, widen = null }) {
  const root = h('div', { class: 'tmp', tabindex: 0, role: 'application', 'aria-label': sec.title });
  const editable = !!(sec.editable && sec.waiting && job.status === 'waiting' && finish);
  root.append(h('div', { class: 'muted small' }, 'Loading the tomograms…'));
  Promise.all([readJson(puid, sec.data), editable ? api.session(puid, job.uid, 'picks').catch(() => ({})) : Promise.resolve({})])
    .then(([info, saved]) => {
      build(info, saved || {});
      if (editable && widen) widen();
      if (editable) requestAnimationFrame(() => requestAnimationFrame(() => bringIntoView(root)));
    })
    .catch((err) => root.replaceChildren(h('div', { class: 'alert error' }, `Tomograms not available: ${err.message || err}`)));
  return root;

  function build(info, saved) {
    const tomos = info.tomograms || [];
    if (!tomos.length) { root.replaceChildren(h('div', { class: 'muted' }, 'No tomogram can be read.')); return; }
    const mode = info.mode || sec.mode || 'particles';
    const start = (saved[mode] && Object.keys(saved[mode]).length ? saved[mode] : null) || (mode === 'particles' ? info.initial : null) || {};
    const picks = new Map(tomos.map((t) => [t.name, (start[t.name] || []).map((p) => (mode === 'filaments' ? p.map((q) => q.slice()) : p.slice()))]));
    const radius = Math.max(1.5, (info.diameter_px || 10) / 2); // voxels
    let cur = 0, pending = null, hoverAt = null, drawing = []; // a sphere's centre; the points of the filament being drawn
    const saver = editable ? sessionSaver(puid, job, 'picks', () => payload()) : null;
    const payload = () => ({ [mode]: Object.fromEntries([...picks].filter(([, v]) => v.length)) });

    const view = sliceView(root, puid, {
      overlay: drawPicks, click: editable ? clickAt : null,
      hover: editable ? (x, y) => { if (pending || drawing.length) { hoverAt = [x, y]; view.draw(); } } : null,
    });
    const z = () => view.st.z;
    // cross-section on slice z of a ball of radius r centred at depth zc (0 when it does not reach the slice)
    const section = (r, zc) => { const d = z() - zc; return d * d < r * r ? Math.sqrt(r * r - d * d) : 0; };

    function drawPicks(g, at, sc, dpr) {
      const list = picks.get(tomos[cur].name);
      g.lineWidth = 1.6 * dpr;
      const ring = (x, y, r, colour, dashed = false) => {
        const [sx, sy] = at(x, y);
        g.strokeStyle = colour;
        g.setLineDash(dashed ? [4 * dpr, 3 * dpr] : []);
        g.beginPath();
        g.arc(sx, sy, Math.max(2 * dpr, r * sc), 0, Math.PI * 2);
        g.stroke();
      };
      if (mode === 'particles') {
        for (const [x, y, zc] of list) {
          const r = section(radius, zc);
          if (r > 0) ring(x, y, r, Math.abs(z() - zc) < 0.5 ? COLOURS.pick : COLOURS.near);
        }
      } else if (mode === 'spheres') {
        for (const [x, y, zc, R] of list) {
          const r = section(R, zc);
          if (r > 0) ring(x, y, r, COLOURS.sphere);
          if (Math.abs(z() - zc) < 0.5) { const [sx, sy] = at(x, y); g.fillStyle = COLOURS.sphere; g.beginPath(); g.arc(sx, sy, 3 * dpr, 0, Math.PI * 2); g.fill(); }
        }
        if (pending) {
          const [sx, sy] = at(pending[0], pending[1]);
          g.fillStyle = COLOURS.drawing;
          g.beginPath(); g.arc(sx, sy, 3.5 * dpr, 0, Math.PI * 2); g.fill();
          if (hoverAt) ring(pending[0], pending[1], Math.hypot(hoverAt[0] - pending[0], hoverAt[1] - pending[1]), COLOURS.drawing, true);
        }
      } else {
        const line = (pts, colour, open = false) => {
          if (!pts.length) return;
          g.strokeStyle = colour;
          g.setLineDash(open ? [6 * dpr, 4 * dpr] : []);
          g.beginPath();
          pts.forEach(([x, y], i) => { const [sx, sy] = at(x, y); if (i) g.lineTo(sx, sy); else g.moveTo(sx, sy); });
          g.stroke();
          g.setLineDash([]);
          for (const [x, y, zc] of pts) { // the points near this slice, filled
            const [sx, sy] = at(x, y);
            g.fillStyle = colour;
            g.beginPath(); g.arc(sx, sy, (Math.abs(z() - zc) <= radius ? 3.5 : 1.8) * dpr, 0, Math.PI * 2); g.fill();
          }
        };
        for (const fil of list) line(fil, COLOURS.filament);
        if (drawing.length) line(hoverAt ? [...drawing, [...hoverAt, z()]] : drawing, COLOURS.drawing, true);
      }
      g.setLineDash([]);
    }
    function inside(x, y) {
      const t = tomos[cur];
      return x >= 0 && y >= 0 && x < t.shape[0] && y < t.shape[1];
    }
    function clickAt(x, y) {
      const list = picks.get(tomos[cur].name);
      const zc = z();
      if (mode === 'particles') { // a pick near the click on this slice is removed, else one is added
        let best = -1, bestD = radius;
        list.forEach(([px, py, pz], j) => { const r = section(radius, pz); const d = Math.hypot(px - x, py - y); if (r > 0 && d < Math.max(r, 2) && d < bestD) { bestD = d; best = j; } });
        if (best >= 0) list.splice(best, 1);
        else if (inside(x, y)) list.push([round2(x), round2(y), zc]);
        else return;
      } else if (mode === 'spheres') { // centre, then a point of the edge; a click on a centre removes the sphere
        if (!pending) {
          const j = list.findIndex(([px, py, pz]) => Math.abs(pz - zc) < 0.5 && Math.hypot(px - x, py - y) < Math.max(3, radius / 2));
          if (j >= 0) list.splice(j, 1);
          else if (inside(x, y)) { pending = [round2(x), round2(y), zc]; hoverAt = null; view.draw(); return; } else return;
        } else {
          const R = Math.hypot(x - pending[0], y - pending[1], zc - pending[2]);
          if (R < 1) { pending = null; view.draw(); return; } // the centre again: cancelled
          list.push([...pending, round2(R)]);
          pending = null;
        }
      } else { // points of the filament being drawn; a click on a finished filament removes it
        if (!drawing.length) {
          const j = list.findIndex((fil) => fil.some(([px, py, pz]) => Math.abs(pz - zc) <= radius && Math.hypot(px - x, py - y) < Math.max(3, radius / 2)));
          if (j >= 0) { list.splice(j, 1); changed(); return; }
        }
        if (!inside(x, y)) return;
        drawing.push([round2(x), round2(y), zc]);
        view.draw();
        hint();
        return;
      }
      changed();
    }
    function endFilament() {
      if (drawing.length >= 2) picks.get(tomos[cur].name).push(drawing);
      drawing = [];
      hoverAt = null;
      changed();
    }
    function changed() {
      view.draw();
      counts();
      hint();
      if (saver) saver.changed();
    }
    const word = { particles: 'particle', spheres: 'sphere', filaments: 'filament' }[mode];
    const total = h('div', { class: 'pk-total' });
    const list = tomogramList(tomos, (k) => { show(k); root.focus(); }, true);
    function counts() {
      const n = tomos.map((t) => picks.get(t.name).length);
      list.counts(n);
      const all = n.reduce((a, b) => a + b, 0);
      total.replaceChildren(h('b', {}, fmt(all)), ` ${word}${all === 1 ? '' : 's'} in ${plural(n.filter(Boolean).length, 'tomogram')}`);
      if (doneBtn) doneBtn.disabled = !all;
    }
    const help = h('span', { class: 'muted small' });
    function hint() {
      if (!editable) { help.textContent = ''; return; }
      help.textContent = mode === 'spheres' ? (pending ? 'now click a point of the sphere\'s edge (Esc: cancel)' : 'click the centre of a sphere on its middle slice')
        : mode === 'filaments' ? (drawing.length ? `${drawing.length} point(s): more points, then Enter to end the filament (Backspace: undo, Esc: cancel)`
          : 'click points along a filament, changing slices as it runs') : '';
    }
    function show(k) {
      if (drawing.length >= 2) endFilament(); else drawing = [];
      pending = null;
      cur = (k + tomos.length) % tomos.length;
      const t = tomos[cur];
      title.textContent = `${cur + 1}/${tomos.length} · ${t.name}${t.denoised ? ' (denoised)' : ''}`;
      title.title = t.path;
      list.highlight(cur);
      view.setTomogram(t);
      hint();
    }
    root.addEventListener('keydown', (ev) => {
      if (ev.target.closest('input, select, textarea')) return;
      if (ev.key === 'ArrowRight') { show(cur + 1); ev.preventDefault(); return; }
      if (ev.key === 'ArrowLeft') { show(cur - 1); ev.preventDefault(); return; }
      if (editable && ev.key === 'Enter' && drawing.length) { endFilament(); ev.preventDefault(); return; }
      if (editable && ev.key === 'Backspace' && drawing.length) { drawing.pop(); view.draw(); hint(); ev.preventDefault(); return; }
      if (ev.key === 'Escape' && (pending || drawing.length)) { pending = null; drawing = []; hoverAt = null; view.draw(); hint(); ev.preventDefault(); return; }
      if (view.key(ev)) ev.preventDefault();
    });
    const title = h('span', { class: 'pk-name mono' });
    const doneBtn = editable ? btn('Done', async () => {
      if (drawing.length >= 2) endFilament();
      saver.stop();
      await finish(payload());
    }, { cls: 'primary', ic: 'check', title: 'Write the annotations and the particle set (particles.star, optimisation_set.star)' }) : null;
    const endBtn = editable && mode === 'filaments' ? btn('End filament', endFilament, { cls: 'small', title: 'End the filament being drawn (Enter)' }) : null;
    const toolbar = h('div', { class: 'pk-tools' },
      btn('', () => show(cur - 1), { cls: 'small', ic: 'back', title: 'Previous tomogram (←)' }),
      btn('', () => show(cur + 1), { cls: 'small', ic: 'next', title: 'Next tomogram (→)' }),
      title, h('span', { class: 'grow' }), endBtn, view.status);
    const legendText = editable ? { particles: 'click: add / remove a particle', spheres: 'click: centre, click: edge · click on a centre: remove',
      filaments: 'click: points · Enter: end · click on a filament: remove' }[mode] : '';
    const legend = h('div', { class: 'pk-legend muted small' },
      mode === 'particles' ? h('span', {}, h('i', { style: { borderColor: COLOURS.pick } }), 'centred on this slice') : null,
      mode === 'particles' ? h('span', {}, h('i', { style: { borderColor: COLOURS.near } }), 'nearby (cross-section)') : null,
      mode === 'spheres' ? h('span', {}, h('i', { style: { borderColor: COLOURS.sphere } }), 'sphere (cross-section)') : null,
      mode === 'filaments' ? h('span', {}, h('i', { style: { borderColor: COLOURS.filament } }), 'filament (● points near this slice)') : null,
      h('span', {}, `${legendText}${legendText ? ' · ' : ''}wheel or ↑ ↓: slice · Ctrl + wheel: zoom · drag: move · ← →: tomograms`));
    root.replaceChildren(
      h('div', { class: 'pk-head' }, h('h4', {}, sec.title), h('span', { class: 'grow' }), saver ? saver.label : null, doneBtn),
      h('div', { class: 'pk-body' }, h('div', { class: 'pk-main' }, toolbar, view.el, legend, help),
        h('div', { class: 'pk-side' }, total, list.el)));
    counts();
    show(0);
  }
}
