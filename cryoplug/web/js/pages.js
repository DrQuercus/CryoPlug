// Top-level pages: projects, external tools, help (the resources are in resources.js).
import { api } from './api.js';
import { browseFiles } from './filebrowser.js';
import { navigate, setJobtypes, state } from './state.js';
import { ago, avatar, btn, clear, confirmDialog, copyText, guard, h, icon, modal, popupMenu, shortPath, toast } from './ui.js';

// Who may do what (the server checks again): administrators and owners manage a project, and without
// accounts whoever opened CryoPlug may do everything.
const me = () => state.info.user || { builtin: true, role: 'admin' };
const isAdmin = () => me().builtin || me().role === 'admin';
const accounts = () => state.info.auth === 'users';
const canManage = (p) => isAdmin() || p.owner === me().username;

// ----------------------------------------------------------------- projects
export async function renderProjects(content) {
  let projects;
  try { projects = await api.projects(); } catch (e) { clear(content, h('div', { class: 'alert error' }, e.message)); return; }
  state.projects = projects;
  const remove = async (p) => {
    if (!(await confirmDialog('Remove project', `Remove ${p.uid} "${p.title}" from CryoPlug?\nThe project directory is kept on disk:\n${p.dir}`, 'Remove', true))) return;
    await guard(api.deleteProject(p.uid, false), 'Project removed');
    renderProjects(content);
  };
  const cards = projects.map((p) => {
    const open = () => navigate(`#/p/${p.uid}`);
    const more = h('button', { class: 'card-more icon-btn', type: 'button', title: 'Actions', 'aria-label': `Actions for ${p.uid}`, 'aria-haspopup': 'menu',
      onclick: (e) => menu(e) }, icon('more'));
    const menu = (e) => {
      e.preventDefault();
      e.stopPropagation();
      popupMenu(more, [
        { label: 'Open', ic: 'next', action: open },
        accounts() && canManage(p) ? { label: 'Share…', ic: 'share', action: () => shareDialog(p, () => renderProjects(content)) } : null,
        { label: 'Copy folder path', ic: 'folder', action: () => copyText(p.dir) },
        ...(canManage(p) ? [{ separator: true }, { label: 'Remove from CryoPlug…', ic: 'trash', danger: true, action: () => remove(p) }] : []),
      ].filter(Boolean), e.type === 'contextmenu' ? { x: e.clientX, y: e.clientY } : null);
    };
    // With accounts: whose project it is (when not one's own) and with whom it is shared.
    const people = accounts() && (p.owner !== me().username || p.members.length) ? h('div', { class: 'project-people' },
      p.owner !== me().username ? h('span', { class: 'owner-chip', title: p.owner ? `Owner: ${p.owner}` : 'No owner: administrators only' },
        p.owner ? avatar({ username: p.owner }, 'small') : icon('lock'), p.owner || 'no owner') : null,
      p.members.length ? h('span', { class: 'owner-chip shared', title: `Shared with ${p.members.join(', ')}` }, icon('users'),
        sharedLabel(p.members)) : null) : null;
    return h('div', { class: 'card project-card', tabindex: 0, role: 'button', onclick: (e) => { if (!e.target.closest('.card-more')) open(); },
      oncontextmenu: menu, onkeydown: (e) => { if (e.target === e.currentTarget && e.key === 'Enter') open(); } },
    h('div', { class: 'card-head' }, h('span', { class: 'uid' }, p.uid),
      p.num_active ? h('span', { class: 'chip running' }, `${p.num_active} active`) : null, h('span', { class: 'grow' }), more),
    h('div', { class: 'card-title' }, p.title),
    p.description ? h('div', { class: 'project-card-desc' }, p.description) : null,
    h('div', { class: 'project-path', title: p.dir }, icon('folder'), h('span', {}, shortPath(p.dir, 40))),
    people,
    h('div', { class: 'card-foot' }, h('span', {}, `${p.num_jobs} job${p.num_jobs === 1 ? '' : 's'}`), h('span', {}, `updated ${ago(p.updated_at)}`)));
  });
  clear(content,
    h('div', { class: 'toolbar' }, btn('New project', () => newProjectDialog(), { cls: 'primary', ic: 'plus' }), h('span', { class: 'grow' }),
      h('span', { class: 'muted small', title: state.info.projects_root }, `Server ${state.info.hostname} · projects in ${shortPath(state.info.projects_root, 50)}`)),
    projects.length ? h('div', { class: 'cards projects' }, cards) : h('div', { class: 'empty' }, h('h3', {}, 'Welcome to CryoPlug'),
      h('p', {}, 'Create a project to post-process a CryoSPARC map: sharpening, model building, refinement, validation and deposition.'),
      btn('New project', () => newProjectDialog(), { cls: 'primary', ic: 'plus' })));
}

// Members of a project, as seen by the user ("shared with you", "alice", "3 members").
function sharedLabel(members) {
  const others = members.filter((m) => m !== me().username);
  if (others.length < members.length) return others.length ? `you + ${others.length}` : 'shared with you';
  return members.length === 1 ? members[0] : `${members.length} members`;
}

// Owner and members of a project (with accounts).
async function shareDialog(project, onDone) {
  let people;
  try { people = await guard(api.people()); } catch { return; }
  const owner = h('select', { id: 'share-owner', disabled: !isAdmin() }, h('option', { value: '' }, 'Nobody: administrators only'),
    people.map((u) => h('option', { value: u.username }, u.full_name ? `${u.full_name} (${u.username})` : u.username)));
  owner.value = project.owner || '';
  const chosen = new Set(project.members || []);
  const list = h('div', { class: 'member-list' });
  const draw = () => clear(list, people.filter((u) => u.username !== owner.value).map((u) => {
    const box = h('input', { type: 'checkbox', id: `share-${u.username}`, checked: chosen.has(u.username),
      onchange: () => { if (box.checked) chosen.add(u.username); else chosen.delete(u.username); } });
    return h('label', { class: 'member-row', for: box.id }, box, avatar(u, 'small'), h('span', {}, u.full_name || u.username),
      u.full_name ? h('span', { class: 'muted small' }, u.username) : null);
  }));
  owner.addEventListener('change', draw);
  draw();
  const save = async () => {
    const body = { members: [...chosen].filter((m) => m !== owner.value) };
    if (isAdmin() && owner.value !== (project.owner || '')) body.owner = owner.value;
    try { await guard(api.updateProject(project.uid, body), 'Sharing saved'); } catch { return; }
    m.close();
    onDone();
  };
  const m = modal({
    title: `Share ${project.uid} · ${project.title}`,
    body: h('div', {},
      h('div', { class: 'field' }, h('label', { for: 'share-owner' }, 'Owner'), owner,
        h('div', { class: 'help' }, isAdmin() ? 'The owner shares, renames and removes the project.' : 'Only an administrator can give the project to someone else.')),
      h('div', { class: 'field' }, h('label', {}, 'Members'), list,
        h('div', { class: 'help' }, 'Members see the project and work in it (jobs, files, 3D viewer). The data they add still has to be in their own folders.'))),
    footer: [btn('Cancel', () => m.close()), btn('Save', save, { cls: 'primary' })],
  });
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
        h('div', { class: 'help' }, accounts() && !isAdmin() ? 'Your projects folder (or another of your folders). A folder CP-<title> is created inside; every job writes into its own J<n> sub-folder.'
          : 'A folder CP-<title> is created inside; every job writes into its own J<n> sub-folder.'))),
    footer: [btn('Cancel', () => m.close()), btn('Create project', create, { cls: 'primary' })],
  });
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
    const cfgLines = [cfg.setup && `setup: ${cfg.setup}`, cfg.bin_dir && `bin_dir: ${shortPath(cfg.bin_dir, 40)}`,
      cfg.executable && `executable: ${shortPath(cfg.executable, 40)}`].filter(Boolean);
    const cfgText = cfgLines.join('\n') || '(defaults: looked up on PATH)';
    const cfgFull = [cfg.setup && `setup: ${cfg.setup}`, cfg.bin_dir && `bin_dir: ${cfg.bin_dir}`, cfg.executable && `executable: ${cfg.executable}`]
      .filter(Boolean).join('\n');
    return h('tr', {},
      h('td', {}, h('b', {}, t.label), h('div', { class: 'muted small' }, t.description),
        t.homepage ? h('a', { class: 'small', href: t.homepage, target: '_blank', rel: 'noopener' }, t.homepage) : null),
      h('td', {}, h('span', { class: `chip ${st.status === 'found' ? 'completed' : st.status === 'missing' ? 'failed' : 'building'}` }, label[st.status] || st.status || 'not checked')),
      h('td', { class: 'small' }, st.path ? h('span', { class: 'mono path', title: st.path }, shortPath(st.path, 46),
        h('button', { class: 'icon-btn', title: 'Copy full path', 'aria-label': `Copy the path of ${t.label}`, onclick: () => copyText(st.path) }, icon('copy')))
        : h('span', { class: 'muted' }, st.message || ''),
      st.version ? h('div', { class: 'muted' }, st.version) : null),
      h('td', { class: 'small mono', style: { whiteSpace: 'pre-wrap' }, title: cfgFull || null }, `[tools.${t.key}]\n${cfgText}`));
  });
  clear(content,
    h('div', { class: 'toolbar' }, h('h2', { style: { margin: 0 } }, 'External programs'), h('span', { class: 'grow' }), isAdmin() ? recheck : null),
    h('p', { class: 'muted' }, 'CryoPlug drives programs installed on this server. Configure how each one is started (source script, conda environment, module, path) in ',
      h('span', { class: 'mono', title: state.info.config_path || '' }, shortPath(state.info.config_path, 60) || '~/.cryoplug/config.toml'), ', restart the server, then re-check.'),
    h('div', { class: 'box' }, h('table', { class: 'data' }, h('thead', {}, h('tr', {}, ['Program', 'Status', 'Location', 'Configuration'].map((c) => h('th', {}, c)))),
      h('tbody', {}, rows))),
    h('div', { class: 'section' }, h('h4', {}, 'Example configuration'), h('pre', { class: 'box mono', style: { whiteSpace: 'pre-wrap' } },
      '[tools.phenix]\nsetup = "source /opt/phenix-1.21.2/phenix_env.sh"\n\n[tools.modelangelo]\nsetup = "source ~/miniconda3/etc/profile.d/conda.sh && conda activate model_angelo"\n\n'
      + '[tools.cryoatom]\nsetup = "source ~/miniconda3/etc/profile.d/conda.sh && conda activate CryoAtom2"\n\n'
      + '[tools.locscale]\nsetup = "source ~/miniconda3/etc/profile.d/conda.sh && conda activate locscale"\n\n[tools.chimerax]\nexecutable = "/usr/bin/chimerax"\n\n'
      + '[interactive]\ndisplay = ":0"   # screen used to open Coot / ISOLDE from the browser')));
}

// --------------------------------------------------------------------- help
let guideText = null;

export async function renderHelp(content, sub = 'guide', item = null) {
  const tab = sub || 'guide';
  const tabs = h('div', { class: 'tabs help-tabs', role: 'tablist' }, [['guide', 'Guide'], ['jobs', 'Référence des jobs'], ['setup', 'Installation & commandes']]
    .map(([key, label]) => h('button', { class: tab === key ? 'on' : '', role: 'tab', 'aria-selected': tab === key ? 'true' : 'false',
      onclick: () => navigate(key === 'guide' ? '#/help' : `#/help/${key}`) }, label)));
  const body = h('div', {});
  clear(content, h('div', { class: 'help-page' }, tabs, body));
  if (tab === 'jobs') renderJobReference(body, item);
  else if (tab === 'setup') renderSetup(body);
  else await renderGuide(body);
}

async function renderGuide(body) {
  if (guideText === null) {
    try {
      const res = await fetch('/docs/GUIDE.md');
      guideText = res.ok ? await res.text() : '';
    } catch { guideText = ''; }
  }
  if (!guideText) { clear(body, h('div', { class: 'alert error' }, 'Guide introuvable (/docs/GUIDE.md).')); return; }
  const { renderMarkdown } = await import('./markdown.js');
  const scrollTo = (id) => { const el = document.getElementById(id); if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' }); };
  const doc = renderMarkdown(guideText, { onAnchor: scrollTo });
  const toc = h('nav', { class: 'doc-toc', 'aria-label': 'Sommaire du guide' }, h('div', { class: 'muted small' }, 'Sommaire'),
    Array.from(doc.querySelectorAll('h2')).filter((hd) => hd.textContent !== 'Sommaire').map((hd) => h('a', { href: '#', onclick: (e) => { e.preventDefault(); scrollTo(hd.id); } }, hd.textContent)));
  clear(body, h('div', { class: 'doc-layout' }, toc, doc));
}

async function renderJobReference(body, item) {
  const { CATEGORY_FR, helpSheet } = await import('./help.js');
  const search = h('input', { type: 'search', placeholder: 'Chercher un job, un logiciel ou une situation (ex. anisotropie, ligand, inconnue)…',
    'aria-label': 'Chercher un job', style: { width: '100%' }, oninput: () => draw() });
  const list = h('div', {});
  const draw = () => {
    const q = search.value.trim().toLowerCase();
    const blocks = [];
    for (const cat of state.info.categories) {
      let types = state.jobtypes.filter((t) => t.category === cat);
      if (q) {
        types = types.filter((t) => [t.title, t.name, t.tool || '', t.help?.purpose || '', ...(t.help?.when || []), ...(t.help?.tips || []),
          ...(t.help?.avoid || []), t.help?.inputs || ''].join(' ').toLowerCase().includes(q));
      }
      if (!types.length) continue;
      const fr = CATEGORY_FR[cat] || cat;
      blocks.push(h('h2', { class: 'ref-cat' }, fr, fr !== cat ? h('span', { class: 'muted small' }, `  ${cat}`) : null));
      blocks.push(h('div', { class: 'ref-grid' }, types.map((t) => h('article', { class: 'box ref-card', id: `job-${t.name}` },
        h('div', { class: 'row' }, h('h3', {}, t.title), h('span', { class: 'grow' }),
          h('span', { class: `tooldot ${t.tool_status}`, title: t.tool ? `${t.tool} : ${t.tool_status}` : 'intégré' })),
        h('div', { class: 'muted small mono' }, t.name),
        helpSheet(t, { onNext: (n) => { search.value = ''; draw(); const el = document.getElementById(`job-${n}`); if (el) { el.scrollIntoView({ behavior: 'smooth', block: 'center' }); el.classList.add('flash'); setTimeout(() => el.classList.remove('flash'), 1500); } } })))));
    }
    clear(list, blocks.length ? blocks : h('div', { class: 'empty' }, 'Aucun job ne correspond.'));
  };
  clear(body, h('p', { class: 'muted' }, 'Chaque fiche explique à quoi sert le job, quand il est pertinent, les pièges à éviter, les entrées à utiliser et les étapes qui suivent. Les mêmes fiches apparaissent dans le constructeur de jobs (bouton « ? ») et dans le détail de chaque job.'),
    h('div', { class: 'toolbar' }, h('div', { style: { flex: 1, maxWidth: '640px' } }, search)), list);
  draw();
  if (item) {
    const el = document.getElementById(`job-${item}`);
    if (el) el.scrollIntoView({ block: 'center' });
  }
}

function remoteAccess() {
  const { listen, hostname, auth, config_path: configPath } = state.info;
  const { port } = listen;
  const firewall = h('pre', {}, `sudo ufw allow ${port}/tcp            # Ubuntu / Debian\n`
    + `sudo firewall-cmd --permanent --add-port=${port}/tcp && sudo firewall-cmd --reload   # Rocky / RHEL`);
  if (listen.network) {
    const login = auth === 'users' ? 'votre compte CryoPlug (nom d\'utilisateur et mot de passe, gérés par les administrateurs dans Settings › Users)'
      : auth === 'password' ? 'le mot de passe de [server] password'
        : 'le jeton d\'accès affiché au démarrage du serveur (la commande cryoplug url le réaffiche)';
    return [
      h('p', {}, 'Le serveur est ouvert au réseau. Depuis un portable ou un autre poste, ouvrez :'),
      h('pre', {}, `${location.protocol}//${hostname}:${port}`),
      h('p', {}, `(ou l'adresse IP du serveur si ce nom n'est pas connu du portable). Connexion avec ${login} ; `
        + 'le bouton de déconnexion est en bas de la barre latérale.'),
      h('p', {}, 'Si la page ne répond pas, le pare-feu du serveur bloque probablement le port :'), firewall,
      h('p', {}, 'Hors du labo, passez par le VPN de l\'institut ou par un tunnel SSH (ci-dessous).'),
      h('pre', {}, `ssh -N -L ${port}:localhost:${port} utilisateur@${hostname}`),
    ];
  }
  return [
    h('p', {}, 'Le serveur n\'écoute que sur cette machine (127.0.0.1). Pour l\'ouvrir depuis un portable, deux options.'),
    h('p', {}, h('b', {}, '1. Réseau du labo.'), ' Dans ', h('code', {}, configPath || '~/.cryoplug/config.toml'), ' :'),
    h('pre', {}, '[server]\nhost = "0.0.0.0"'),
    h('p', {}, `Redémarrez CryoPlug puis ouvrez http://${hostname}:${port} (ou l'IP du serveur) depuis le portable. `
      + (auth === 'users' ? 'Chacun se connecte avec son compte CryoPlug. '
        : 'Une connexion est alors demandée : jeton d\'accès affiché au démarrage (ou par cryoplug url), ou mot de passe si vous en définissez un '
          + '([server] password) ; mieux, créez des comptes personnels dans Settings › Users. ')
      + 'Si la page ne répond pas, ouvrez le port :'),
    firewall,
    h('p', {}, h('b', {}, '2. Tunnel SSH'), ' (hors du labo, pare-feu strict) : sur le portable,'),
    h('pre', {}, `ssh -N -L ${port}:localhost:${port} utilisateur@${hostname}`),
    h('p', {}, `puis ouvrez http://localhost:${port} sur le portable.`),
  ];
}

function renderSetup(body) {
  const sec = (title, ...children) => [h('h2', {}, title), ...children];
  clear(body, h('div', { class: 'doc' },
    ...sec('Installation sur le serveur',
      h('pre', {}, 'python3 -m venv ~/cryoplug-venv\n~/cryoplug-venv/bin/pip install /chemin/vers/CryoPlug\n~/cryoplug-venv/bin/cryoplug init\n# éditer ~/.cryoplug/config.toml (logiciels, lanes, affichage)\n~/cryoplug-venv/bin/cryoplug tools\n~/cryoplug-venv/bin/cryoplug fetch-viewer\n~/cryoplug-venv/bin/cryoplug start')),
    ...sec('Déclarer un logiciel',
      h('p', {}, 'Chaque logiciel est lancé dans bash après ses lignes « setup » (source, conda, module). Exemple :'),
      h('pre', {}, '[tools.phenix]\nsetup = "source /opt/phenix-1.21.2/phenix_env.sh"\n\n[tools.cryoatom]\nsetup = "source ~/miniconda3/etc/profile.d/conda.sh && conda activate CryoAtom2"\n\n[interactive]\ndisplay = ":0"   # écran où ouvrir ISOLDE / Coot depuis le navigateur'),
      h('p', {}, 'La page ', h('a', { href: '#/tools' }, 'Tools'), ' indique ce qui est détecté. Fichier de configuration : ',
        h('code', {}, state.info.config_path || '~/.cryoplug/config.toml'), '.')),
    ...sec('Accès depuis un autre ordinateur', ...remoteAccess()),
    ...sec('Comptes utilisateurs',
      h('p', {}, 'Comme dans CryoSPARC, chacun peut avoir son compte : il se connecte avec son nom d\'utilisateur et son mot de passe, '
        + 'voit ses projets (et ceux partagés avec lui), crée ses projets dans son dossier et ne choisit des données que dans les dossiers '
        + 'que l\'administrateur lui ouvre. Les administrateurs gèrent tout dans ', h('a', { href: '#/settings' }, 'Settings'),
      ' (ajout, suppression, mots de passe, dossiers, durée des sessions). Sur le serveur :'),
      h('pre', {}, 'cryoplug user add admin --admin          # premier compte (administrateur)\ncryoplug user add alice --generate --allow /data/alice\ncryoplug user passwd admin               # mot de passe oublié\ncryoplug user list'),
      h('p', { class: 'muted small' }, 'Les jobs tournent sous le compte Unix qui a lancé CryoPlug (comme le compte cryosparc) : pour une séparation stricte des données, utilisez aussi les permissions Unix.')),
    ...sec('Commandes',
      h('pre', {}, 'cryoplug init          # écrit ~/.cryoplug/config.toml\ncryoplug tools         # détecte les logiciels\ncryoplug fetch-viewer  # installe le visualiseur 3D Mol* (hors-ligne)\ncryoplug start         # serveur web + planificateur\ncryoplug url           # adresses à ouvrir (avec le jeton d\'accès)\ncryoplug user …        # comptes utilisateurs (add, passwd, list, set, delete)\ncryoplug status        # lanes et jobs actifs\ncryoplug jobtypes      # liste des jobs\ncryoplug docs-jobs     # régénère docs/JOBS.md\ncryoplug demo-data DIR # jeu de données synthétique\ncryoplug service       # fichier systemd')),
    h('p', { class: 'muted small' }, `CryoPlug ${state.info.version}`)));
}
