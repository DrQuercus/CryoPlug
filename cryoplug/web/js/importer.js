// Import from the computer: files and folders chosen or dropped on a project are uploaded to the server, recognised
// (half maps, maps, masks, models, sequences, CryoSPARC job folders, volume series) and turned into import jobs,
// to create at once or to open in the job builder.
import { api } from './api.js';
import { openBuilder } from './builder.js';
import { navigate, refreshJobs, state, typeTitle } from './state.js';
import { btn, clear, fmtSize, h, icon, modal, toast } from './ui.js';
import { draggingFiles, filesFromDrop, pickFiles, uploadDialog } from './upload.js';

const MAP_RE = /\.(mrc|map|ccp4)(\.gz)?$/i; // not .mrcs: particle stacks
const MODEL_RE = /\.(pdb|cif|mmcif|ent)(\.gz)?$/i;
const SEQ_RE = /\.(fasta|fa|faa|fas|fna|seq)$/i;
const ZIP_RE = /\.zip$/i;
const PARTICLES_RE = /\.(cs|star)$/i;
const ROLE_LABEL = { half_map_a: 'Half map A', half_map_b: 'Half map B', map: 'Full map (unsharpened)', map_sharp: 'Sharpened map', mask: 'Mask' };

// What a map file is, from its name (CryoSPARC, RELION and EMDB names).
export function mapRole(name) {
  const n = name.toLowerCase().replace(MAP_RE, '');
  if (/half[_-]?(map[_-]?)?(a|1)([^a-z0-9]|$)/.test(n)) return 'half_map_a';
  if (/half[_-]?(map[_-]?)?(b|2)([^a-z0-9]|$)/.test(n)) return 'half_map_b';
  if (/(mask|msk)(?!ed)/.test(n)) return 'mask'; // EMDB: emd_1234_msk_1.map; RELION's postprocess_masked is a map
  if (/sharp|postprocess|post_process|(^|[^a-z0-9])pp([^a-z0-9]|$)/.test(n)) return 'map_sharp';
  return 'map';
}

// The import jobs to propose for uploaded files: [{type, params, title, details: [[label, name]]}] and the files
// left aside, with why.
export function proposeImports(result) {
  const proposals = [];
  const aside = [];
  const loose = [];
  for (const [top, folder] of Object.entries(result.folders || {})) {
    const inside = result.files.filter((f) => f.relPath.startsWith(`${top}/`));
    const names = inside.map((f) => f.file.name);
    const maps = inside.filter((f) => MAP_RE.test(f.file.name));
    if (names.includes('job.json') || names.some((n) => /^J\d+_\d+_volume_map/i.test(n))) {
      proposals.push({ type: 'import_cryosparc', params: { job_dir: folder.path }, details: [['CryoSPARC job folder', `${top}/ (${inside.length} files)`]] });
    } else if (maps.length >= 2 && maps.every((f) => mapRole(f.file.name) === 'map')) {
      proposals.push({ type: 'import_volume_series', params: { path: folder.path }, details: [['Volumes', `${top}/ (${maps.length} maps)`]] });
    } else {
      loose.push(...inside);
    }
  }
  loose.push(...result.files.filter((f) => !f.relPath.includes('/')));
  const maps = loose.filter((f) => MAP_RE.test(f.file.name));
  if (maps.length) {
    const roles = {};
    const extra = [];
    for (const f of maps) {
      const role = mapRole(f.file.name);
      if (!roles[role]) roles[role] = f;
      else extra.push(f);
    }
    if (roles.map && !roles.map_sharp && !roles.half_map_a && !roles.half_map_b) { // a single map: the one to look at and build into
      roles.map_sharp = roles.map;
      delete roles.map;
    }
    const order = ['half_map_a', 'half_map_b', 'map', 'map_sharp', 'mask'];
    const params = Object.fromEntries(order.filter((r) => roles[r]).map((r) => [r, roles[r].info.path]));
    const details = order.filter((r) => roles[r]).map((r) => [ROLE_LABEL[r], roles[r].file.name]);
    if (Boolean(roles.half_map_a) !== Boolean(roles.half_map_b)) details.push(['Note', 'one half map only: add the other one in the job']);
    proposals.push({ type: 'import_maps', params, details });
    for (const f of extra) proposals.push({ type: 'import_maps', params: { map_sharp: f.info.path }, details: [['Sharpened map', f.file.name]] });
  }
  for (const f of loose) {
    const name = f.file.name;
    if (MAP_RE.test(name)) continue;
    if (MODEL_RE.test(name)) proposals.push({ type: 'import_model', params: { source: 'file', path: f.info.path }, details: [['Model', name]] });
    else if (SEQ_RE.test(name)) proposals.push({ type: 'import_sequence', params: { path: f.info.path }, details: [['Sequence', name]] });
    else if (ZIP_RE.test(name)) proposals.push({ type: 'import_volume_series', params: { path: f.info.path }, details: [['Volumes (ZIP)', name]] });
    else if (PARTICLES_RE.test(name)) aside.push([name, 'particle metadata: the particle images must be on the server (Import particles with its folder)']);
    else aside.push([name, 'not recognised: choose it with Browse in a job (uploads folder)']);
  }
  return { proposals: proposals.filter((p) => state.types[p.type]), aside };
}

async function createJobs(chosen, queue) {
  const puid = state.project.uid;
  const created = [];
  const problems = [];
  for (const p of chosen) {
    try {
      const job = await api.createJob(puid, { type: p.type, params: p.params });
      created.push(job.uid);
      if (queue) {
        try { await api.queueJob(puid, job.uid); } catch (err) { problems.push(`${job.uid} not queued: ${err.message}`); }
      }
    } catch (err) {
      problems.push(`${typeTitle(p.type)}: ${err.message}`);
    }
  }
  await refreshJobs();
  if (created.length) toast(`${queue ? 'Created and queued' : 'Created'} ${created.join(', ')}`, 'ok');
  if (problems.length) toast(problems.join('\n'), 'error', 9000);
  if (created.length) navigate(`#/p/${puid}/${created[0]}`);
}

function proposalsDialog(result) {
  const { proposals, aside } = proposeImports(result);
  const checks = proposals.map(() => h('input', { type: 'checkbox', checked: true }));
  const open = (p) => { m.close(); openBuilder({ type: p.type, params: p.params }); navigate(`#/p/${state.project.uid}/new`); };
  const run = async (queue) => {
    const chosen = proposals.filter((_, i) => checks[i].checked);
    if (!chosen.length) { toast('Tick at least one job', 'error'); return; }
    m.close();
    await createJobs(chosen, queue);
  };
  const m = modal({
    title: 'Import the uploaded files',
    wide: true,
    body: h('div', {},
      proposals.length ? h('p', { class: 'muted small', style: { marginTop: 0 } },
        'CryoPlug recognised these imports from the file names. Check them, or open one in the job builder to change it.') : null,
      h('div', { class: 'import-list' }, proposals.map((p, i) => h('div', { class: 'import-item' },
        h('label', { class: 'import-head' }, checks[i], h('b', {}, typeTitle(p.type)),
          h('span', { class: 'grow' }), btn('Open in builder', () => open(p), { cls: 'small', ic: 'edit' })),
        h('div', { class: 'kv' }, p.details.map(([k, v]) => [h('div', { class: 'k' }, k), h('div', { class: 'v mono' }, v)]).flat())))),
      aside.length ? h('div', { class: 'alert info' }, h('b', {}, 'Kept in the uploads folder of the project:'),
        h('ul', { class: 'notes', style: { marginTop: '6px' } }, aside.map(([name, why]) => h('li', {}, h('code', {}, name), ` — ${why}`)))) : null,
      !proposals.length ? h('p', {}, 'No import job to propose: the files are on the server, in the uploads folder of the project.') : null),
    footer: proposals.length ? [btn('Close', () => m.close()), btn('Create the jobs', () => run(false), { ic: 'plus' }),
      btn('Create and queue', () => run(true), { cls: 'primary', ic: 'play' })] : [btn('Close', () => m.close(), { cls: 'primary' })],
  });
}

// The dialog: choose or drop files and folders, upload them, then propose the import jobs.
export function importFromComputer(initial = []) {
  if (!state.project) return;
  let items = [...initial];
  const list = h('div', { class: 'up-list' });
  const total = h('span', { class: 'muted small' });
  const send = btn('Upload', async () => {
    if (!items.length) return;
    m.close();
    const result = await uploadDialog(state.project.uid, items);
    if (result) proposalsDialog(result);
  }, { cls: 'primary', ic: 'upload' });
  const add = (more) => {
    const known = new Set(items.map((it) => `${it.relPath}:${it.file.size}`));
    items.push(...more.filter((it) => !known.has(`${it.relPath}:${it.file.size}`)));
    draw();
  };
  const draw = () => {
    clear(list, items.length ? items.map((it, i) => h('div', { class: 'up-row' },
      h('div', { class: 'up-name', title: it.relPath }, icon(it.relPath.includes('/') ? 'folder' : 'file'), it.relPath),
      h('span', { class: 'muted small' }, fmtSize(it.file.size)),
      h('button', { class: 'icon-btn', type: 'button', 'aria-label': `Remove ${it.relPath}`, onclick: () => { items.splice(i, 1); draw(); } }, icon('x'))))
      : h('div', { class: 'muted small' }, 'No file yet.'));
    const bytes = items.reduce((s, it) => s + it.file.size, 0);
    total.textContent = items.length ? `${items.length} file${items.length === 1 ? '' : 's'} · ${fmtSize(bytes)}` : '';
    send.disabled = !items.length;
  };
  const zone = h('div', { class: 'drop-zone', tabindex: 0,
    ondragover: (e) => { if (draggingFiles(e)) { e.preventDefault(); zone.classList.add('dragover'); } },
    ondragleave: () => zone.classList.remove('dragover'),
    ondrop: async (e) => { e.preventDefault(); zone.classList.remove('dragover'); add(await filesFromDrop(e.dataTransfer)); },
  }, icon('upload'), h('div', {}, h('b', {}, 'Drop files or folders here'),
    h('div', { class: 'muted small' }, 'Half maps, maps, masks (.mrc, .map), models (.pdb, .cif), sequences (.fasta), a CryoSPARC job folder, a series of volumes (folder or .zip)')),
  h('div', { class: 'row', style: { justifyContent: 'center', marginTop: '8px' } },
    btn('Choose files…', async () => add(await pickFiles({ multiple: true })), { ic: 'file' }),
    btn('Choose a folder…', async () => add(await pickFiles({ directory: true })), { ic: 'folder' })));
  const m = modal({
    title: 'Import from this computer',
    wide: true,
    body: h('div', {}, zone, list,
      h('p', { class: 'muted small' }, 'The files go to the uploads folder of the project on the server; a file already sent is not sent '
        + 'again, and an interrupted transfer resumes. CryoPlug then proposes the import jobs.')),
    footer: [total, h('span', { class: 'grow' }), btn('Cancel', () => m.close()), send],
  });
  draw();
}

// Files dragged from the computer anywhere on the page: on a project, the import dialog opens with them (a field of
// the job builder takes them itself); elsewhere the browser must not open them in place of CryoPlug.
export function installFileDrop() {
  let depth = 0;
  const reset = () => { depth = 0; document.body.classList.remove('file-dragover'); };
  const onProject = () => state.route?.page === 'project' && state.project;
  document.addEventListener('dragenter', (e) => {
    if (!draggingFiles(e)) return;
    depth += 1;
    if (onProject()) document.body.classList.add('file-dragover');
  });
  document.addEventListener('dragleave', (e) => { if (draggingFiles(e) && --depth <= 0) reset(); });
  document.addEventListener('dragover', (e) => { if (draggingFiles(e)) e.preventDefault(); });
  document.addEventListener('drop', async (e) => {
    if (!draggingFiles(e)) return;
    const handled = e.defaultPrevented;
    e.preventDefault();
    reset();
    if (handled) return;
    if (!onProject()) { toast('Open a project first: the files are imported into a project', 'error'); return; }
    const items = await filesFromDrop(e.dataTransfer);
    if (items.length) importFromComputer(items);
  });
}
