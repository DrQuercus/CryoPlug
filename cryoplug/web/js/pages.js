// Top-level pages: projects, queue / resource manager, external tools, help.
import { api } from './api.js';
import { browseFiles } from './filebrowser.js';
import { navigate, setJobtypes, state } from './state.js';
import { ago, btn, clear, confirmDialog, guard, h, icon, jobDuration, modal, statusChip, toast } from './ui.js';

// ----------------------------------------------------------------- projects
export async function renderProjects(content) {
  let projects;
  try { projects = await api.projects(); } catch (e) { clear(content, h('div', { class: 'alert error' }, e.message)); return; }
  state.projects = projects;
  const cards = projects.map((p) => h('div', { class: 'card project-card', tabindex: 0, role: 'button', onclick: () => navigate(`#/p/${p.uid}`),
    onkeydown: (e) => { if (e.key === 'Enter') navigate(`#/p/${p.uid}`); } },
  h('div', { class: 'card-head' }, h('span', { class: 'uid' }, p.uid), p.num_active ? h('span', { class: 'chip running' }, `${p.num_active} active`) : null),
  h('div', { class: 'card-title' }, p.title),
  p.description ? h('div', { class: 'card-type' }, p.description) : null,
  h('div', { class: 'muted small mono', style: { wordBreak: 'break-all' } }, p.dir),
  h('div', { class: 'card-foot' }, h('span', {}, `${p.num_jobs} job${p.num_jobs === 1 ? '' : 's'}`), h('span', {}, `updated ${ago(p.updated_at)}`)),
  h('div', { class: 'row' }, h('span', { class: 'grow' }), h('button', { class: 'icon-btn', title: 'Remove project', 'aria-label': `Remove ${p.title}`,
    onclick: async (e) => {
      e.stopPropagation();
      if (!(await confirmDialog('Remove project', `Remove ${p.uid} "${p.title}" from CryoPlug?\nThe project directory is kept on disk:\n${p.dir}`, 'Remove', true))) return;
      await guard(api.deleteProject(p.uid, false), 'Project removed');
      renderProjects(content);
    } }, icon('trash')))));
  clear(content,
    h('div', { class: 'toolbar' }, btn('New project', () => newProjectDialog(), { cls: 'primary', ic: 'plus' }), h('span', { class: 'grow' }),
      h('span', { class: 'muted small' }, `Server ${state.info.hostname} · projects in ${state.info.projects_root}`)),
    projects.length ? h('div', { class: 'cards' }, cards) : h('div', { class: 'empty' }, h('h3', {}, 'Welcome to CryoPlug'),
      h('p', {}, 'Create a project to post-process a CryoSPARC map: sharpening, model building, refinement, validation and deposition.'),
      btn('New project', () => newProjectDialog(), { cls: 'primary', ic: 'plus' })));
}

function newProjectDialog() {
  const title = h('input', { type: 'text', id: 'np-title', placeholder: 'e.g. Complex I — nanodisc' });
  const desc = h('textarea', { id: 'np-desc', rows: 2, placeholder: 'Optional description' });
  const parent = h('input', { type: 'text', id: 'np-parent', value: state.info.projects_root });
  const create = async () => {
    try {
      const p = await guard(api.createProject({ title: title.value, description: desc.value, parent: parent.value }));
      m.close();
      toast(`Project ${p.uid} created in ${p.dir}`, 'ok');
      navigate(`#/p/${p.uid}`);
    } catch { /* shown */ }
  };
  title.addEventListener('keydown', (e) => { if (e.key === 'Enter') create(); });
  const m = modal({
    title: 'New project',
    body: h('div', {},
      h('div', { class: 'field' }, h('label', { for: 'np-title' }, 'Title'), title),
      h('div', { class: 'field' }, h('label', { for: 'np-desc' }, 'Description'), desc),
      h('div', { class: 'field' }, h('label', { for: 'np-parent' }, 'Parent directory'),
        h('div', { class: 'row' }, parent, btn('Browse', async () => {
          const d = await browseFiles({ start: parent.value, kind: 'dir', title: 'Parent directory for the project' });
          if (d) parent.value = d;
        }, { cls: 'small', ic: 'folder' })),
        h('div', { class: 'help' }, 'A folder CP-<title> is created inside; every job writes into its own J<n> sub-folder.'))),
    footer: [btn('Cancel', () => m.close()), btn('Create project', create, { cls: 'primary' })],
  });
}

// -------------------------------------------------------------------- queue
export async function renderQueue(content) {
  let q;
  try { q = await api.queue(); } catch (e) { clear(content, h('div', { class: 'alert error' }, e.message)); return; }
  const lanes = q.lanes.map((l) => h('div', { class: 'box lane-card' },
    h('div', { class: 'row' }, h('b', {}, l.name), h('span', { class: 'tag' }, l.type)),
    l.description ? h('div', { class: 'muted small' }, l.description) : null,
    h('div', { class: 'tiles', style: { marginTop: '8px' } },
      h('div', { class: 'tile' }, h('div', { class: 'label' }, 'Running jobs'), h('div', { class: 'value' }, `${l.running} / ${l.max_jobs}`)),
      l.gpus.length ? h('div', { class: 'tile' }, h('div', { class: 'label' }, 'GPUs in use'), h('div', { class: 'value' }, `${l.gpus_used.length} / ${l.gpus.length}`),
        h('div', { class: 'muted small' }, l.gpus.map((g) => `${g}${l.gpus_used.includes(g) ? '●' : '○'}`).join(' '))) : null)));
  const rows = q.jobs.map((j) => h('tr', {},
    h('td', {}, h('a', { href: `#/p/${j.project_uid}/${j.uid}` }, `${j.project_uid} / ${j.uid}`)),
    h('td', {}, j.title, h('div', { class: 'muted small' }, j.project_title)),
    h('td', {}, statusChip(j.status)),
    h('td', { class: 'small' }, j.message || ''),
    h('td', {}, j.lane || ''), h('td', {}, (j.gpus || []).join(',')), h('td', {}, jobDuration(j)),
    h('td', {}, btn(j.status === 'queued' ? 'Dequeue' : 'Kill', async () => {
      if (j.status !== 'queued' && !(await confirmDialog('Stop job', `Stop ${j.project_uid}/${j.uid}?`, 'Stop', true))) return;
      await guard(api.killJob(j.project_uid, j.uid), 'Stopped');
      renderQueue(content);
    }, { cls: 'small danger' }))));
  clear(content,
    h('h2', { style: { marginTop: 0 } }, 'Resource manager'),
    h('div', { class: 'row wrap', style: { alignItems: 'stretch', gap: '12px' } }, lanes),
    h('div', { class: 'section' }, h('h4', {}, 'Active and queued jobs'),
      q.jobs.length ? h('div', { class: 'box' }, h('table', { class: 'data' },
        h('thead', {}, h('tr', {}, ['Job', 'Title', 'Status', 'Message', 'Lane', 'GPU', 'Time', ''].map((c) => h('th', {}, c)))), h('tbody', {}, rows)))
        : h('div', { class: 'empty' }, 'Nothing is running or queued.')));
}

// -------------------------------------------------------------------- tools
export async function renderTools(content) {
  let tools;
  try { tools = await api.tools(); } catch (e) { clear(content, h('div', { class: 'alert error' }, e.message)); return; }
  const recheck = btn('Re-check all tools', async () => {
    recheck.disabled = true;
    recheck.textContent = 'Checking…';
    try {
      await guard(api.checkTools(), 'Tools checked');
      setJobtypes(await api.jobtypes());
    } catch { /* shown */ }
    renderTools(content);
  }, { cls: 'primary', ic: 'refresh' });
  const label = { found: 'detected', missing: 'not found', disabled: 'disabled', unknown: 'not checked' };
  const rows = tools.map((t) => {
    const st = t.status || {};
    const cfg = t.config || {};
    const cfgText = [cfg.setup && `setup: ${cfg.setup}`, cfg.bin_dir && `bin_dir: ${cfg.bin_dir}`, cfg.executable && `executable: ${cfg.executable}`]
      .filter(Boolean).join('\n') || '(defaults: looked up on PATH)';
    return h('tr', {},
      h('td', {}, h('b', {}, t.label), h('div', { class: 'muted small' }, t.description),
        t.homepage ? h('a', { class: 'small', href: t.homepage, target: '_blank', rel: 'noopener' }, t.homepage) : null),
      h('td', {}, h('span', { class: `chip ${st.status === 'found' ? 'completed' : st.status === 'missing' ? 'failed' : 'building'}` }, label[st.status] || st.status || 'not checked')),
      h('td', { class: 'small' }, st.path ? h('span', { class: 'mono' }, st.path) : h('span', { class: 'muted' }, st.message || ''),
        st.version ? h('div', { class: 'muted' }, st.version) : null),
      h('td', { class: 'small mono', style: { whiteSpace: 'pre-wrap' } }, `[tools.${t.key}]\n${cfgText}`));
  });
  clear(content,
    h('div', { class: 'toolbar' }, h('h2', { style: { margin: 0 } }, 'External programs'), h('span', { class: 'grow' }), recheck),
    h('p', { class: 'muted' }, 'CryoPlug drives programs installed on this server. Configure how each one is started (source script, conda environment, module, path) in ',
      h('span', { class: 'mono' }, state.info.config_path || '~/.cryoplug/config.toml'), ', restart the server, then re-check.'),
    h('div', { class: 'box' }, h('table', { class: 'data' }, h('thead', {}, h('tr', {}, ['Program', 'Status', 'Location', 'Configuration'].map((c) => h('th', {}, c)))),
      h('tbody', {}, rows))),
    h('div', { class: 'section' }, h('h4', {}, 'Example configuration'), h('pre', { class: 'box mono', style: { whiteSpace: 'pre-wrap' } },
      '[tools.phenix]\nsetup = "source /opt/phenix-1.21.2/phenix_env.sh"\n\n[tools.modelangelo]\nsetup = "source ~/miniconda3/etc/profile.d/conda.sh && conda activate model_angelo"\n\n'
      + '[tools.cryoatom]\nsetup = "source ~/miniconda3/etc/profile.d/conda.sh && conda activate CryoAtom2"\n\n'
      + '[tools.locscale]\nsetup = "source ~/miniconda3/etc/profile.d/conda.sh && conda activate locscale"\n\n[tools.chimerax]\nexecutable = "/usr/bin/chimerax"\n\n'
      + '[interactive]\ndisplay = ":0"   # screen used to open Coot / ISOLDE from the browser')));
}

// --------------------------------------------------------------------- help
export function renderHelp(content) {
  const sec = (title, ...children) => [h('h2', {}, title), ...children];
  clear(content, h('div', { class: 'help-page' },
    h('h1', { style: { marginTop: 0 } }, 'CryoPlug guide'),
    h('p', {}, 'CryoPlug takes over once your best map is refined in CryoSPARC and drives every step to a deposition-ready model: map enhancement, model building, interactive rebuilding, refinement, validation and the wwPDB/EMDB package. Concepts mirror CryoSPARC: projects contain jobs (J1, J2…), jobs consume outputs of other jobs, queued jobs start when their inputs are ready and a lane slot / GPU is free.'),
    ...sec('Typical pipeline',
      h('ol', {},
        h('li', {}, h('b', {}, 'Import from CryoSPARC'), ' — point to the refinement job folder (e.g. /data/CS-proj/J245). Half maps, sharpened map and FSC mask are found automatically and the gold-standard FSC is recomputed.'),
        h('li', {}, h('b', {}, 'Map processing'), ' — LocScale 2, EMmerNet, DeepEMhancer, EMReady, Phenix density modification / sharpening. Use the “Map enhancement comparison” workflow to run them side by side.'),
        h('li', {}, h('b', {}, 'Model building'), ' — ModelAngelo (with or without sequence), AlphaFold models (AFDB or local ColabFold) trimmed and fitted with ChimeraX or Phenix.'),
        h('li', {}, h('b', {}, 'Interactive'), ' — ISOLDE and Coot sessions open on the server screen in one click, or download a ready-to-run bundle. Save the model in the job folder (or upload it) and click Finish.'),
        h('li', {}, h('b', {}, 'Refinement'), ' — phenix.real_space_refine or Servalcat against half maps.'),
        h('li', {}, h('b', {}, 'Validation'), ' — MolProbity, EMRinger, Phenix cryo-EM validation, and built-in Q-scores / map-model FSC / atom inclusion.'),
        h('li', {}, h('b', {}, 'Deposition'), ' — automated pre-deposition checklist, then a package with mmCIF, maps, FSC XML, recommended contour level, draft methods and Table 1.'))),
    ...sec('Building jobs',
      h('p', {}, 'Click ', h('b', {}, 'New job'), ' or ', h('b', {}, 'Continue with…'), ' on a finished job. Inputs are chosen from compatible outputs, or dragged from the output chips that appear on job cards while the builder is open. A resolution of “auto” is taken from the imported maps. You can queue a whole chain at once: each job waits for its parents.')),
    ...sec('Interactive sessions',
      h('p', {}, 'ISOLDE/Coot jobs prepare a session then wait (purple). Set ', h('code', {}, '[interactive] display'), ' in the configuration to open them on the workstation screen or a VNC desktop; otherwise use the session bundle on your own computer and upload the result.')),
    ...sec('Remote access',
      h('p', {}, 'By default the server listens on localhost. From your laptop: ', h('code', {}, `ssh -N -L 39500:localhost:39500 user@${state.info.hostname}`), ' then open http://localhost:39500. To expose it on the lab network set ', h('code', {}, 'host = "0.0.0.0"'), ' and a password in the configuration.')),
    ...sec('Command line',
      h('pre', {}, 'cryoplug init          # write ~/.cryoplug/config.toml\ncryoplug tools         # detect external programs\ncryoplug fetch-viewer  # install the Mol* 3D viewer for offline use\ncryoplug start         # web server + scheduler\ncryoplug status        # lanes and running jobs\ncryoplug service       # print a systemd unit')),
    h('p', { class: 'muted small' }, `CryoPlug ${state.info.version}`)));
}
