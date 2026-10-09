// Settings, as in CryoSPARC's admin panel: "My account" for everyone; the user accounts (with their
// password, role and folders), the compute lanes (GPUs, SLURM) and the access settings for administrators.
import { api } from './api.js';
import { fmtGiB } from './compute.js';
import { browseFiles } from './filebrowser.js';
import { navigate, state } from './state.js';
import { ago, avatar, btn, clear, confirmDialog, copyText, fmtSize, fmtTime, guard, h, icon, modal, popupMenu, shortPath, toast } from './ui.js';

const ROLE = { admin: 'Administrator', user: 'User' };

// ------------------------------------------------------------------ small parts
const field = (label, input, help = null) => h('div', { class: 'field' },
  h('label', { for: input.id }, label), input, help ? h('div', { class: 'help' }, help) : null);

const section = (title, ...children) => h('section', { class: 'settings-section' }, h('h4', {}, title), ...children);

function browserName(agent = '') {
  const b = /Edg\//.test(agent) ? 'Edge' : /Firefox\//.test(agent) ? 'Firefox' : /Chrome\//.test(agent) ? 'Chrome'
    : /Safari\//.test(agent) ? 'Safari' : agent ? agent.split(/[ /]/)[0] : 'Script';
  const os = /Windows/.test(agent) ? 'Windows' : /Mac OS X|Macintosh/.test(agent) ? 'macOS' : /Linux/.test(agent) ? 'Linux' : '';
  return os ? `${b} · ${os}` : b;
}

function passwordInput(id, autocomplete = 'new-password') {
  return h('input', { type: 'password', id, autocomplete, required: true });
}

// Folders a user may read: one row per folder, typed or picked with the file browser.
function folderList(values, idPrefix) {
  const list = h('div', { class: 'folder-list' });
  const addRow = (value = '', focus = false) => {
    const input = h('input', { type: 'text', value, placeholder: '/data/…', class: 'mono', 'aria-label': 'Folder' });
    const row = h('div', { class: 'folder-row' }, icon('folder'), input,
      h('button', { type: 'button', class: 'btn small', title: 'Choose with the file browser', onclick: async () => {
        const dir = await browseFiles({ start: input.value, kind: 'dir', title: 'Folder the user may read' });
        if (dir) input.value = dir;
      } }, 'Browse'),
      h('button', { type: 'button', class: 'icon-btn', title: 'Remove', 'aria-label': `Remove ${value || 'this folder'}`, onclick: () => row.remove() }, icon('x')));
    list.appendChild(row);
    if (focus) input.focus();
  };
  values.forEach((v) => addRow(v));
  const add = h('button', { type: 'button', class: 'btn small', id: `${idPrefix}-add` }, icon('plus'), 'Add a folder');
  add.addEventListener('click', () => addRow('', true));
  return { el: h('div', {}, list, add), values: () => [...list.querySelectorAll('input')].map((i) => i.value.trim()).filter(Boolean) };
}

// The password created for someone else: shown once, to be passed on.
function showPassword(user, password, mustChange) {
  const m = modal({
    title: `Password of ${user.username}`,
    body: h('div', {},
      h('p', { style: { marginTop: 0 } }, 'Give this password to ', h('b', {}, user.full_name || user.username), '. It is shown only once.'),
      h('div', { class: 'secret' }, h('code', {}, password), btn('Copy', () => copyText(password), { cls: 'small', ic: 'copy' })),
      h('p', { class: 'muted small' }, mustChange ? 'They will choose their own password the first time they log in.'
        : 'They can change it in Settings › My account.')),
    footer: [btn('Done', () => m.close(), { cls: 'primary' })],
  });
}

// "Generate" or "Choose" a password (new account, or a new password set by an administrator).
function passwordChoice(idPrefix, { mustChangeDefault = true, showMustChange = true } = {}) {
  const gen = h('input', { type: 'radio', name: `${idPrefix}-mode`, id: `${idPrefix}-gen`, checked: true });
  const set = h('input', { type: 'radio', name: `${idPrefix}-mode`, id: `${idPrefix}-set` });
  const input = h('input', { type: 'password', id: `${idPrefix}-pw`, autocomplete: 'new-password', disabled: true,
    placeholder: `At least ${state.info.password_min || 8} characters`, 'aria-label': 'Password' });
  const must = h('input', { type: 'checkbox', id: `${idPrefix}-must`, checked: mustChangeDefault });
  const sync = () => { input.disabled = !set.checked; if (set.checked) input.focus(); };
  gen.addEventListener('change', sync);
  set.addEventListener('change', sync);
  const el = h('div', { class: 'field' }, h('label', {}, 'Password'),
    h('label', { class: 'radio-row', for: gen.id }, gen, 'Generate a password (shown once, to pass on)'),
    h('label', { class: 'radio-row', for: set.id }, set, 'Choose it'), input,
    showMustChange ? h('label', { class: 'radio-row', for: must.id }, must, 'Ask for a new password at the next login') : null);
  return {
    el,
    body() {
      if (set.checked && !input.value) throw new Error('Type the password, or let CryoPlug generate one');
      return { ...(set.checked ? { password: input.value } : {}), must_change_password: showMustChange && must.checked };
    },
    mustChange: () => showMustChange && must.checked,
  };
}

// ------------------------------------------------------------------ page
export async function renderSettings(content, sub) {
  const me = state.info.user || { builtin: true, role: 'admin' };
  const tabs = [];
  if (!me.builtin) tabs.push(['account', 'My account', 'user']);
  if (me.role === 'admin') tabs.push(['users', 'Users', 'users'], ['compute', 'Compute', 'cpu'], ['access', 'Access & security', 'lock']);
  const tab = tabs.some(([k]) => k === sub) ? sub : tabs[0][0];
  const body = h('div', { class: 'settings-body' });
  clear(content, h('div', { class: 'settings-page' },
    h('div', { class: 'tabs help-tabs', role: 'tablist' }, tabs.map(([key, label, ic]) => h('button', {
      class: tab === key ? 'on' : '', role: 'tab', 'aria-selected': tab === key ? 'true' : 'false', onclick: () => navigate(`#/settings/${key}`),
    }, icon(ic), label))),
    body));
  if (tab === 'account') await renderAccount(body, me);
  else if (tab === 'users') await (me.builtin ? renderFirstAdmin(body) : renderUsers(body, me));
  else if (tab === 'compute') await renderCompute(body);
  else await renderAccess(body);
}

// ------------------------------------------------------------------ my account
async function renderAccount(body, me) {
  const fullName = h('input', { type: 'text', id: 'acc-name', value: me.full_name || '', autocomplete: 'name' });
  const email = h('input', { type: 'email', id: 'acc-email', value: me.email || '', autocomplete: 'email' });
  const profile = h('form', { class: 'box settings-box', onsubmit: async (e) => {
    e.preventDefault();
    try {
      const updated = await guard(api.updateMe({ full_name: fullName.value, email: email.value }), 'Profile saved');
      state.info.user = { ...state.info.user, ...updated };
      document.dispatchEvent(new CustomEvent('cryoplug-user'));
    } catch { /* shown */ }
  } },
  h('div', { class: 'profile-head' }, avatar(me, 'big'), h('div', {},
    h('div', { class: 'profile-name' }, me.full_name || me.username),
    h('div', { class: 'muted small' }, `${me.username} · ${ROLE[me.role] || me.role}`))),
  field('Full name', fullName), field('E-mail', email, 'Optional, for the administrators.'),
  h('div', { class: 'row end' }, h('button', { class: 'btn primary', type: 'submit' }, 'Save')));

  const folders = h('div', { class: 'box settings-box' },
    h('div', { class: 'kv' }, h('div', { class: 'k' }, 'New projects go to'), h('div', { class: 'v mono' }, me.projects_folder)),
    h('div', { class: 'kv' }, h('div', { class: 'k' }, 'You can read data from'),
      me.folders === null ? h('div', { class: 'v' }, 'every folder of the server (administrator)')
        : h('ul', { class: 'v folder-ul' }, me.folders.map((f) => h('li', { class: 'mono' }, f)))),
    h('p', { class: 'muted small', style: { marginBottom: 0 } }, me.role === 'admin' ? 'Change them in Settings › Users.'
      : 'Set by the administrators: ask them for another folder.'));

  const cur = passwordInput('pw-current', 'current-password');
  const pw1 = passwordInput('pw-new');
  const pw2 = passwordInput('pw-again');
  const sessionsBox = h('div', { class: 'box settings-box' }, h('div', { class: 'muted small' }, 'Loading…'));
  const password = h('form', { class: 'box settings-box', onsubmit: async (e) => {
    e.preventDefault();
    if (pw1.value !== pw2.value) { toast('The two new passwords differ', 'error'); return; }
    try {
      await guard(api.changePassword(cur.value, pw1.value), 'Password changed: your other sessions were logged out');
      cur.value = ''; pw1.value = ''; pw2.value = '';
      loadSessions();
    } catch { /* shown */ }
  } },
  h('input', { type: 'text', autocomplete: 'username', value: me.username, hidden: true, readonly: true }),
  field('Current password', cur), field('New password', pw1, `At least ${state.info.password_min || 8} characters.`),
  field('New password again', pw2),
  h('div', { class: 'row end' }, h('button', { class: 'btn primary', type: 'submit' }, icon('key'), 'Change password')));

  async function loadSessions() {
    let list;
    try { list = await api.mySessions(); } catch (e) { clear(sessionsBox, h('div', { class: 'alert error' }, e.message)); return; }
    const others = list.filter((s) => !s.current).length;
    clear(sessionsBox,
      h('table', { class: 'data' }, h('thead', {}, h('tr', {}, ['Browser', 'Address', 'Logged in', 'Last seen', 'Ends'].map((c) => h('th', {}, c)))),
        h('tbody', {}, list.map((s) => h('tr', {},
          h('td', {}, browserName(s.agent), s.current ? h('span', { class: 'tag here' }, 'this browser') : null),
          h('td', { class: 'mono' }, s.ip || '—'), h('td', {}, ago(s.created_at)), h('td', {}, ago(s.last_seen_at)),
          h('td', {}, fmtTime(s.expires_at)))))),
      h('div', { class: 'row end', style: { marginTop: '10px' } },
        btn(others ? `Log out the ${others} other session${others > 1 ? 's' : ''}` : 'No other session', async () => {
          try { await guard(api.endOtherSessions(), 'Other sessions logged out'); } catch { return; }
          loadSessions();
        }, { ic: 'logout', disabled: !others })));
  }

  clear(body, h('div', { class: 'settings-grid' },
    h('div', {}, section('Profile', profile), section('Password', password)),
    h('div', {}, section('My folders', folders), section('Where I am logged in', sessionsBox))));
  loadSessions();
}

// ------------------------------------------------------------------ users (administrators)
async function renderUsers(body, me) {
  let users;
  let settings;
  try { [users, settings] = await Promise.all([api.users(), api.settings()]); } catch (e) {
    clear(body, h('div', { class: 'alert error' }, e.message));
    return;
  }
  const reload = () => renderUsers(body, me);
  const ctx = { me, users, root: settings.projects_root, reload };
  const search = h('input', { type: 'search', placeholder: 'Filter users', 'aria-label': 'Filter users', oninput: () => draw() });
  const tbody = h('tbody');
  const draw = () => {
    const q = search.value.trim().toLowerCase();
    const shown = users.filter((u) => !q || [u.username, u.full_name, u.email].join(' ').toLowerCase().includes(q));
    clear(tbody, shown.length ? shown.map((u) => userRow(u, ctx)) : h('tr', {}, h('td', { colspan: 8, class: 'muted' }, 'No user matches.')));
  };
  const admins = users.filter((u) => u.role === 'admin').length;
  clear(body,
    h('div', { class: 'toolbar' }, btn('Add user', () => userDialog(null, ctx), { cls: 'primary', ic: 'plus' }),
      h('label', { class: 'search-field' }, icon('search'), search), h('span', { class: 'grow' }),
      h('span', { class: 'muted small' }, `${users.length} account${users.length === 1 ? '' : 's'} · ${admins} administrator${admins === 1 ? '' : 's'}`)),
    h('div', { class: 'box table-box' }, h('table', { class: 'data users-table' },
      h('thead', {}, h('tr', {}, ['User', 'Role', 'Projects folder', 'May read data from', 'Projects', 'Last login', 'Status', '']
        .map((c) => h('th', {}, c)))), tbody)),
    h('p', { class: 'muted small' }, 'Users see their own projects and those shared with them, and pick files only in their folders. '
      + 'Administrators see every project, read every folder and manage the accounts. Double-click a row to edit it.'));
  draw();
}

function userRow(u, ctx) {
  const status = u.disabled ? h('span', { class: 'chip killed' }, 'disabled')
    : u.must_change_password ? h('span', { class: 'chip queued', title: 'Must choose a new password at the next login' }, 'temporary password')
      : h('span', { class: 'chip completed' }, 'active');
  const more = h('button', { class: 'icon-btn', type: 'button', title: 'Actions', 'aria-label': `Actions for ${u.username}`, 'aria-haspopup': 'menu' }, icon('more'));
  const menu = (e) => {
    e.preventDefault();
    e.stopPropagation();
    popupMenu(more, [
      { label: 'Edit…', ic: 'edit', action: () => userDialog(u, ctx) },
      { label: 'Set a new password…', ic: 'key', action: () => passwordDialog(u, ctx) },
      { label: u.disabled ? 'Enable the account' : 'Disable the account', ic: u.disabled ? 'check' : 'stop', action: async () => {
        try { await guard(api.updateUser(u.username, { disabled: !u.disabled }), u.disabled ? `${u.username} enabled` : `${u.username} disabled`); } catch { return; }
        ctx.reload();
      } },
      { separator: true },
      { label: 'Delete…', ic: 'trash', danger: true, action: () => deleteDialog(u, ctx) },
    ], e.type === 'contextmenu' ? { x: e.clientX, y: e.clientY } : null);
  };
  more.addEventListener('click', menu);
  const folders = u.role === 'admin' ? h('span', { class: 'muted' }, 'every folder')
    : u.allowed_paths.length ? h('ul', { class: 'folder-ul' }, u.allowed_paths.map((f) => h('li', { class: 'mono', title: f }, shortPath(f, 36))))
      : h('span', { class: 'muted' }, 'projects folder only');
  return h('tr', { class: u.disabled ? 'off' : '', ondblclick: () => userDialog(u, ctx), oncontextmenu: menu },
    h('td', {}, h('div', { class: 'user-cell' }, avatar(u), h('div', {},
      h('div', { class: 'strong' }, u.full_name || u.username, u.username === ctx.me.username ? h('span', { class: 'tag here' }, 'you') : null),
      h('div', { class: 'muted small' }, [u.username, u.email].filter(Boolean).join(' · '))))),
    h('td', {}, h('span', { class: `role ${u.role}` }, u.role === 'admin' ? icon('shield') : icon('user'), ROLE[u.role] || u.role)),
    h('td', { class: 'small' }, h('div', { class: 'mono', title: u.projects_folder }, shortPath(u.projects_folder, 36)),
      u.projects_dir ? null : h('div', { class: 'muted' }, 'default')),
    h('td', { class: 'small' }, folders),
    h('td', { class: 'num' }, String(u.num_projects ?? 0)),
    h('td', { class: 'small', title: u.last_login_at ? fmtTime(u.last_login_at) : '' }, u.last_login_at ? ago(u.last_login_at) : h('span', { class: 'muted' }, 'never')),
    h('td', {}, status),
    h('td', {}, more));
}

function userDialog(user, ctx) {
  const editing = !!user;
  const username = h('input', { type: 'text', id: 'u-name', value: user?.username || '', disabled: editing, autocomplete: 'off',
    autocapitalize: 'none', spellcheck: 'false', placeholder: 'e.g. alice' });
  const fullName = h('input', { type: 'text', id: 'u-full', value: user?.full_name || '', placeholder: 'e.g. Alice Martin' });
  const email = h('input', { type: 'email', id: 'u-email', value: user?.email || '' });
  const role = h('select', { id: 'u-role' }, h('option', { value: 'user' }, 'User: own and shared projects, own folders'),
    h('option', { value: 'admin' }, 'Administrator: everything, manages the accounts'));
  role.value = user?.role || 'user';
  const projectsDir = h('input', { type: 'text', id: 'u-pdir', class: 'mono', value: user?.projects_dir || '' });
  const placeholder = () => { projectsDir.placeholder = `${ctx.root}/${username.value.trim().toLowerCase() || '<user name>'}`; };
  username.addEventListener('input', placeholder);
  placeholder();
  const folders = folderList(user?.allowed_paths || [], 'u-folders');
  const foldersField = h('div', { class: 'field' }, h('label', { for: 'u-folders-add' }, 'Folders the user may read'), folders.el,
    h('div', { class: 'help' }, 'Data the user can browse and give to jobs: CryoSPARC projects, maps, models, sequence databases. '
      + 'Their projects folder is always included.'));
  const adminNote = h('div', { class: 'alert info' }, 'Administrators can read every folder.');
  const syncRole = () => { foldersField.hidden = role.value === 'admin'; adminNote.hidden = role.value !== 'admin'; };
  role.addEventListener('change', syncRole);
  syncRole();
  const pw = editing ? null : passwordChoice('u-pw');

  const save = async () => {
    const body = { full_name: fullName.value, email: email.value, role: role.value, projects_dir: projectsDir.value.trim(),
      allowed_paths: folders.values() };
    try {
      if (editing) {
        await guard(api.updateUser(user.username, body), `${user.username} updated`);
      } else {
        let extra;
        try { extra = pw.body(); } catch (e) { toast(e.message, 'error'); return; }
        const res = await guard(api.createUser({ ...body, username: username.value, ...extra }));
        toast(`Account ${res.user.username} created`, 'ok');
        if (res.password) showPassword(res.user, res.password, extra.must_change_password);
      }
      m.close();
      ctx.reload();
    } catch { /* shown */ }
  };
  const m = modal({
    title: editing ? `Edit ${user.username}` : 'Add a user',
    wide: true,
    body: h('form', { class: 'user-form', onsubmit: (e) => { e.preventDefault(); save(); } },
      h('div', { class: 'form-grid' },
        h('div', {},
          field('User name', username, editing ? 'User names cannot be changed.' : 'Lower-case letters, digits, . _ - @ (used to log in).'),
          field('Full name', fullName), field('E-mail', email), field('Role', role),
          pw ? pw.el : null),
        h('div', {},
          field('Projects folder', projectsDir, 'Where the user\'s new projects are created. Empty: the default shown.'),
          foldersField, adminNote))),
    footer: [btn('Cancel', () => m.close()), btn(editing ? 'Save' : 'Create the account', save, { cls: 'primary' })],
  });
  if (!editing) setTimeout(() => username.focus(), 40);
}

function passwordDialog(user, ctx) {
  const own = user.username === ctx.me.username;
  const pw = passwordChoice('rp', { mustChangeDefault: !own, showMustChange: !own });
  const save = async () => {
    let body;
    try { body = pw.body(); } catch (e) { toast(e.message, 'error'); return; }
    let res;
    try { res = await guard(api.resetPassword(user.username, body), `New password set for ${user.username}`); } catch { return; }
    m.close();
    if (res.password) showPassword(user, res.password, body.must_change_password);
    ctx.reload();
  };
  const m = modal({
    title: `New password for ${user.username}`,
    body: h('div', {}, pw.el, h('p', { class: 'muted small' }, own ? 'Your other sessions will be logged out.'
      : `${user.full_name || user.username} will be logged out everywhere.`)),
    footer: [btn('Cancel', () => m.close()), btn('Set the password', save, { cls: 'primary', ic: 'key' })],
  });
}

function deleteDialog(user, ctx) {
  const heirs = ctx.users.filter((x) => x.username !== user.username && !x.disabled);
  const heir = h('select', { id: 'del-heir' }, h('option', { value: '' }, 'Nobody: administrators only'),
    heirs.map((x) => h('option', { value: x.username }, x.full_name ? `${x.full_name} (${x.username})` : x.username)));
  const del = async () => {
    try { await guard(api.deleteUser(user.username, heir.value), `${user.username} deleted`); } catch { return; }
    m.close();
    if (user.username === ctx.me.username) location.reload();
    else ctx.reload();
  };
  const m = modal({
    title: `Delete ${user.username}`,
    body: h('div', {},
      h('p', { style: { marginTop: 0 } }, `${user.full_name || user.username} will no longer be able to log in. `
        + 'Their project folders stay on disk.'),
      user.num_projects ? field(`Give their ${user.num_projects} project${user.num_projects > 1 ? 's' : ''} to`, heir) : null,
      user.username === ctx.me.username ? h('div', { class: 'alert warn' }, 'This is your own account: you will be logged out.') : null),
    footer: [btn('Cancel', () => m.close()), btn('Delete the account', del, { cls: 'primary danger', ic: 'trash' })],
  });
}

// Without accounts: whoever opened CryoPlug creates the administrator account (and becomes it).
function renderFirstAdmin(body) {
  const how = { token: 'with the access token printed when the server starts', password: 'with the shared password',
    null: 'on this computer' }[state.info.auth] || 'here';
  const username = h('input', { type: 'text', id: 'fa-name', autocomplete: 'username', autocapitalize: 'none', spellcheck: 'false', placeholder: 'e.g. admin' });
  const fullName = h('input', { type: 'text', id: 'fa-full', autocomplete: 'name' });
  const pw1 = passwordInput('fa-pw');
  const pw2 = passwordInput('fa-pw2');
  const projectsDir = h('input', { type: 'text', id: 'fa-pdir', class: 'mono' });
  const placeholder = () => { projectsDir.placeholder = `${state.info.projects_root}/${username.value.trim().toLowerCase() || '<user name>'}`; };
  username.addEventListener('input', placeholder);
  placeholder();
  const create = async (e) => {
    e.preventDefault();
    if (pw1.value !== pw2.value) { toast('The two passwords differ', 'error'); return; }
    try {
      await guard(api.createUser({ username: username.value, full_name: fullName.value, password: pw1.value, role: 'admin',
        projects_dir: projectsDir.value.trim() }));
    } catch { return; }
    toast('Accounts are on: you are logged in as the administrator', 'ok');
    setTimeout(() => { location.hash = '#/settings/users'; location.reload(); }, 600);
  };
  clear(body, h('div', { class: 'settings-grid' },
    h('div', {}, section('Personal accounts, as in CryoSPARC', h('div', { class: 'box settings-box intro' },
      h('p', { style: { marginTop: 0 } }, `CryoPlug is used without accounts: anyone who opens it ${how} has every right.`),
      h('p', {}, 'With accounts, everyone logs in with their own user name and password, on this computer too. '
        + 'Each user sees their own projects (and those shared with them), works in their projects folder and only picks data in the '
        + 'folders you give them. Administrators manage the accounts here.'),
      h('ol', { class: 'steps' },
        h('li', {}, 'Create your administrator account: you stay logged in with it, and the existing projects become yours.'),
        h('li', {}, 'Add the users, with their projects folder and the folders they may read.'),
        state.info.listen?.network
          ? h('li', {}, 'The port is open: from then on the access token no longer opens CryoPlug, everyone logs in with their account.')
          : h('li', {}, 'Open the port to the other computers: ', h('code', {}, '[server] host = "0.0.0.0"'), ' in the configuration, then restart CryoPlug.')),
      h('p', { class: 'muted small', style: { marginBottom: 0 } }, 'Accounts can also be managed on the server with ', h('code', {}, 'cryoplug user'), '.')))),
    h('div', {}, section('Administrator account', h('form', { class: 'box settings-box', onsubmit: create },
      field('User name', username, 'Lower-case letters, digits, . _ - @'), field('Full name', fullName),
      field('Password', pw1, `At least ${state.info.password_min || 8} characters.`), field('Password again', pw2),
      field('Projects folder', projectsDir, 'Where your new projects go. Empty: the default shown.'),
      h('div', { class: 'row end' }, h('button', { class: 'btn primary', type: 'submit' }, icon('shield'), 'Create the administrator account')))))));
}

// ------------------------------------------------------------------ compute lanes (administrators)
// Lanes as in CryoSPARC: this machine (with the GPUs it hands out, one job per GPU) or SLURM partitions.
// Jobs go to the first lane unless another one is picked in the job builder.
const LANE_DEFAULTS = { name: '', type: 'local', description: '', max_jobs: 2, gpus: [], partition: '', partitions: [], account: '', qos: '',
  time_limit: '48:00:00', mem: '', gres: 'gpu', extra_sbatch: '', setup: '', submit_cmd: 'sbatch {script}',
  status_cmd: 'squeue -h -j {cluster_job_id} -o %T', kill_cmd: 'scancel {cluster_job_id}', script_template: '', python: '' };
const isSlurm = (l) => (l.submit_cmd || '').trim().startsWith('sbatch');

async function renderCompute(body) {
  let data;
  try { data = await api.lanes(); } catch (e) { clear(body, h('div', { class: 'alert error' }, e.message)); return; }
  const hw = await api.hardware().catch(() => null);
  const ctx = { lanes: data.lanes, hw, reload: () => renderCompute(body) };
  ctx.save = async (lanes, message) => {
    try { await guard(api.putLanes(lanes), message); } catch { return false; }
    state.info = await api.info(); // the job builder and the queue see the new lanes
    ctx.reload();
    return true;
  };
  const gpus = hw?.gpus || [];
  const hardware = h('div', { class: 'box settings-box hw' },
    h('div', { class: 'hw-head' }, icon('server'), h('b', {}, hw?.hostname || state.info.hostname),
      h('span', { class: 'muted' }, [hw?.cpus ? `${hw.cpus} CPU threads` : null, hw?.memory ? `${fmtSize(hw.memory)} of memory` : null].filter(Boolean).join(' · '))),
    gpus.length ? h('div', { class: 'hw-gpus' }, gpus.map((g) => h('div', { class: 'hw-gpu' }, icon('gpu'), h('b', {}, `GPU ${g.index}`),
      h('span', {}, g.name), h('span', { class: 'muted' }, fmtGiB(g.memory_total)))))
      : h('div', { class: 'muted small' }, hw?.nvidia_smi ? 'nvidia-smi found no GPU.' : 'No NVIDIA GPU detected: nvidia-smi is not installed '
        + '(its path can be given as [monitor] nvidia_smi in config.toml).'),
    h('div', { class: 'small' }, h('a', { href: '#/resources' }, 'Live usage on the Resources page ›')));
  const fromFile = data.source === 'config';
  const addMenu = (e) => popupMenu(e.currentTarget, [
    { label: 'This machine (local GPUs)', ic: 'monitor', action: () => laneDialog(null, ctx, { type: 'local' }) },
    { label: 'SLURM cluster', ic: 'server', action: () => laneDialog(null, ctx, { type: 'cluster' }) },
  ]);
  clear(body, h('div', { class: 'compute-settings' },
    section('Hardware of this server', hardware),
    section('Lanes',
      h('div', { class: 'toolbar' }, h('button', { class: 'btn primary', type: 'button', 'aria-haspopup': 'menu', onclick: addMenu }, icon('plus'), 'Add a lane', icon('chevron')),
        h('span', { class: 'grow' }),
        h('span', { class: 'muted small' }, fromFile ? 'From the configuration file (config.toml).' : 'Saved in CryoPlug: they replace the [[lanes]] of config.toml.'),
        !fromFile && data.file_lanes.length ? btn('Use config.toml again', async () => {
          if (!(await confirmDialog('Lanes of config.toml', `Replace these lanes with those of the configuration file (${data.file_lanes.join(', ')})?`, 'Replace'))) return;
          try { await guard(api.resetLanes(), 'Lanes of config.toml restored'); } catch { return; }
          state.info = await api.info();
          ctx.reload();
        }, { cls: 'small', ic: 'undo' }) : null),
      h('div', { class: 'lane-grid' }, ctx.lanes.map((l, i) => laneTile(l, i, ctx))),
      h('p', { class: 'muted small' }, 'Jobs go to the default lane (the first one) unless another lane is picked in the Compute section of the job builder, '
        + 'where GPUs can also be chosen. On a local lane each job gets its own GPU(s) (CUDA_VISIBLE_DEVICES); a SLURM lane submits a script '
        + 'written from its settings. Changes apply to the next jobs started.'))));
}

function laneTile(l, i, ctx) {
  const others = (l.partitions || []).filter((p) => p !== l.partition);
  const facts = l.type === 'cluster'
    ? [l.partition ? `partition ${l.partition}` : 'default partition', others.length ? `or ${others.join(', ')}` : null,
      l.time_limit ? `${l.time_limit} per job` : null, l.mem ? `${l.mem} memory` : null, l.account ? `account ${l.account}` : null,
      l.script_template.trim() ? 'own script' : null]
    : [l.gpus.length ? `hands out GPU ${l.gpus.join(', ')}` : 'does not hand out GPUs'];
  facts.push(`up to ${l.max_jobs} job${l.max_jobs > 1 ? 's' : ''} at once`);
  const without = ctx.lanes.filter((x) => x !== l);
  const more = h('button', { class: 'icon-btn', type: 'button', title: 'Actions', 'aria-label': `Actions for lane ${l.name}`, 'aria-haspopup': 'menu' }, icon('more'));
  more.addEventListener('click', () => popupMenu(more, [
    { label: 'Edit…', ic: 'edit', action: () => laneDialog(l, ctx) },
    { label: 'Make it the default lane', ic: 'check', disabled: i === 0, action: () => ctx.save([l, ...without], `${l.name} is the default lane`) },
    { label: 'Duplicate…', ic: 'copy', action: () => laneDialog(l, ctx, { copy: true }) },
    { separator: true },
    { label: 'Delete…', ic: 'trash', danger: true, disabled: ctx.lanes.length < 2, action: async () => {
      if (!(await confirmDialog('Delete lane', `Delete lane ${l.name}? Jobs not started yet that use it will go to the default lane.`, 'Delete', true))) return;
      ctx.save(without, `Lane ${l.name} deleted`);
    } },
  ]));
  return h('div', { class: 'box lane-tile', ondblclick: () => laneDialog(l, ctx) },
    h('div', { class: 'row' }, icon(l.type === 'cluster' ? 'server' : 'monitor'), h('b', {}, l.name),
      h('span', { class: 'tag' }, l.type === 'cluster' ? (isSlurm(l) ? 'SLURM' : 'cluster') : 'this machine'),
      i === 0 ? h('span', { class: 'tag here' }, 'default') : null, h('span', { class: 'grow' }), more),
    l.description ? h('div', { class: 'muted small' }, l.description) : null,
    h('div', { class: 'lane-facts' }, facts.filter(Boolean).join(' · ')),
    h('div', { class: 'row' }, btn('Edit', () => laneDialog(l, ctx), { cls: 'small', ic: 'edit' }),
      i > 0 ? btn('Make default', () => ctx.save([l, ...without], `${l.name} is the default lane`), { cls: 'small' }) : null));
}

function freeName(ctx, stem) {
  const names = new Set(ctx.lanes.map((l) => l.name));
  if (!names.has(stem)) return stem;
  let n = 2;
  while (names.has(`${stem}-${n}`)) n += 1;
  return `${stem}-${n}`;
}

function laneDialog(lane, ctx, { type = 'local', copy = false } = {}) {
  const editing = !!lane && !copy;
  const base = lane ? { ...LANE_DEFAULTS, ...lane } : { ...LANE_DEFAULTS, type, max_jobs: type === 'cluster' ? 20 : 2 };
  if (!editing) base.name = freeName(ctx, lane ? `${lane.name}-copy` : type === 'cluster' ? 'slurm' : 'local');
  const input = (id, value, extra = {}) => h('input', { type: 'text', id, value: value ?? '', autocomplete: 'off', spellcheck: 'false', ...extra });
  const area = (id, value, rows, placeholder) => { const t = h('textarea', { id, class: 'mono', rows, placeholder }); t.value = value || ''; return t; };
  const name = input('ln-name', base.name);
  const kind = h('select', { id: 'ln-type' }, h('option', { value: 'local' }, 'This machine'), h('option', { value: 'cluster' }, 'Cluster (SLURM)'));
  kind.value = base.type;
  const desc = input('ln-desc', base.description, { placeholder: type === 'cluster' ? 'e.g. GPU nodes of the institute cluster' : 'e.g. Workstation with 4 GPUs' });
  const maxJobs = h('input', { type: 'number', id: 'ln-max', min: 1, max: 10000, value: base.max_jobs });

  // local: GPUs handed out, ticked among those nvidia-smi sees (or typed)
  const detected = ctx.hw?.gpus || [];
  const gpuText = input('ln-gpus', (base.gpus || []).join(', '), { class: 'mono', placeholder: detected.length ? detected.map((g) => g.index).join(', ') : 'e.g. 0, 1' });
  const ids = () => gpuText.value.split(/[\s,]+/).filter(Boolean);
  const boxes = detected.map((g) => {
    const box = h('input', { type: 'checkbox', id: `ln-gpu-${g.index}`, checked: ids().includes(String(g.index)), onchange: () => {
      const set = new Set(ids());
      if (box.checked) set.add(String(g.index)); else set.delete(String(g.index));
      gpuText.value = [...set].sort((a, b) => Number(a) - Number(b)).join(', ');
    } });
    return h('label', { class: 'radio-row gpu-check', for: box.id }, box, h('b', {}, `GPU ${g.index}`), ` ${g.name} · ${fmtGiB(g.memory_total)}`);
  });
  gpuText.addEventListener('input', () => detected.forEach((g, k) => { boxes[k].querySelector('input').checked = ids().includes(String(g.index)); }));
  const allGpus = detected.length ? btn('All', () => { gpuText.value = detected.map((g) => g.index).join(', '); gpuText.dispatchEvent(new Event('input')); }, { cls: 'small' }) : null;
  const localBox = h('div', {},
    h('div', { class: 'field' }, h('label', { for: 'ln-gpus' }, 'GPUs this lane hands out'),
      boxes.length ? h('div', { class: 'gpu-checks' }, boxes) : null,
      h('div', { class: 'row' }, gpuText, allGpus),
      h('div', { class: 'help' }, 'Each job gets its own GPU(s), the free ones with the least memory in use unless the user chooses them. '
        + 'None: the lane does not hand out GPUs (programs see them all). Numbers as nvidia-smi shows them.')));

  // cluster: SLURM settings the submission script is written from
  const partList = h('datalist', { id: 'ln-partlist' });
  const partition = input('ln-part', base.partition, { class: 'mono', list: 'ln-partlist', placeholder: 'cluster default' });
  const partitions = input('ln-parts', (base.partitions || []).join(', '), { class: 'mono', placeholder: 'e.g. gpu, gpu-long' });
  const account = input('ln-account', base.account, { class: 'mono' });
  const qos = input('ln-qos', base.qos, { class: 'mono' });
  const time = input('ln-time', base.time_limit, { class: 'mono', placeholder: 'e.g. 48:00:00 or 2-00:00:00' });
  const mem = input('ln-mem', base.mem, { class: 'mono', placeholder: 'cluster default (e.g. 64G)' });
  const gres = input('ln-gres', base.gres, { class: 'mono', placeholder: 'gpu' });
  const extra = area('ln-extra', base.extra_sbatch, 3, '--constraint=a100\n--exclude=node12');
  const setup = area('ln-setup', base.setup, 3, 'module load cuda/12.2\nsource /opt/conda/etc/profile.d/conda.sh');
  const submit = input('ln-submit', base.submit_cmd, { class: 'mono' });
  const status = input('ln-status', base.status_cmd, { class: 'mono' });
  const kill = input('ln-kill', base.kill_cmd, { class: 'mono' });
  const template = area('ln-template', base.script_template, 8, '#!/bin/bash\n#SBATCH --gres=gpu:{num_gpus}\n…\n{worker_cmd}');
  const python = input('ln-python', base.python, { class: 'mono', placeholder: 'the server\'s python' });
  const clusterBox = h('div', {},
    h('div', { class: 'form-grid' },
      field('Default partition', partition, '--partition of the jobs.'),
      field('Partitions users may pick', partitions, 'Offered in the job builder, besides the default one.'),
      field('Time limit per job', time, '--time; users can ask for another one.'),
      field('Memory per job', mem, '--mem; empty: the cluster default.'),
      field('Account', account, '--account (empty: none).'), field('QOS', qos, '--qos (empty: none).'),
      field('GPU resource', gres, 'GPU jobs ask --gres=<this>:<number> (e.g. gpu or gpu:a100). Empty: no --gres line.')),
    field('More #SBATCH options', extra, 'One per line, with or without “#SBATCH”.'),
    field('Before the job starts', setup, 'Shell lines run on the node before CryoPlug’s worker (modules, conda…).'), partList);
  const advanced = h('details', { class: 'help-details' }, h('summary', {}, 'Advanced'),
    h('div', { class: 'cluster-only form-grid' }, field('Submit command', submit, '{script}: the script file.'),
      field('Status command', status, 'Prints the state of {cluster_job_id}.'), field('Cancel command', kill)),
    h('div', { class: 'cluster-only' }, field('Own submission script', template, 'Replaces the script written from the settings above (other schedulers). '
      + 'Placeholders: {num_gpus} {num_cpus} {partition} {time} {mem} {account} {qos} {gres} {setup} {job_dir} {project_uid} {job_uid} {worker_cmd}.')),
    field('Python of the worker', python, 'Interpreter with CryoPlug installed, on the machines that run the jobs.'));

  const out = h('pre', { class: 'box mono lane-out', hidden: true });
  const show = (text) => { out.hidden = false; out.textContent = text; };
  const collect = () => ({ ...base, name: name.value.trim(), type: kind.value, description: desc.value.trim(), max_jobs: Number(maxJobs.value || 1),
    gpus: gpuText.value, partition: partition.value.trim(), partitions: partitions.value, account: account.value.trim(), qos: qos.value.trim(),
    time_limit: time.value.trim(), mem: mem.value.trim(), gres: gres.value.trim(), extra_sbatch: extra.value, setup: setup.value,
    submit_cmd: submit.value.trim(), status_cmd: status.value.trim(), kill_cmd: kill.value.trim(), script_template: template.value, python: python.value.trim() });
  const test = btn('Test', async () => {
    test.disabled = true;
    show(kind.value === 'cluster' ? 'Asking SLURM (sinfo)…' : 'Asking nvidia-smi…');
    try {
      const res = await guard(api.testLane(collect()));
      show(`${res.ok ? '✓' : '✗'} ${res.output || ''}`);
      clear(partList, (res.partitions || []).map((p) => h('option', { value: p })));
    } catch (e) { show(e.message); }
    test.disabled = false;
  }, { cls: 'small', ic: 'check', title: 'Check the lane from the server (sinfo for SLURM, nvidia-smi for the GPUs)' });
  const preview = btn('Preview script', async () => {
    try { show((await guard(api.previewLane(collect()))).script); } catch (e) { show(e.message); }
  }, { cls: 'small', ic: 'file', title: 'The script submitted for a 1-GPU job' });
  const sync = () => {
    const cluster = kind.value === 'cluster';
    localBox.hidden = cluster;
    clusterBox.hidden = !cluster;
    preview.hidden = !cluster;
    advanced.querySelectorAll('.cluster-only').forEach((el) => { el.hidden = !cluster; });
    out.hidden = true;
  };
  kind.addEventListener('change', sync);
  sync();

  const save = async () => {
    const next = collect();
    const list = editing ? ctx.lanes.map((l) => (l === lane ? next : l)) : [...ctx.lanes, next];
    if (await ctx.save(list, editing ? `Lane ${next.name} saved` : `Lane ${next.name} added`)) m.close();
  };
  const m = modal({
    title: editing ? `Lane ${lane.name}` : 'New lane',
    wide: true,
    body: h('form', { class: 'lane-form', onsubmit: (e) => { e.preventDefault(); save(); } },
      h('div', { class: 'form-grid' }, field('Name', name, 'Shown in the job builder: letters, digits, . _ -'), field('Runs on', kind),
        field('Description', desc), field('Jobs at once', maxJobs, 'Jobs of this lane running at the same time; the others wait in the queue.')),
      localBox, clusterBox, advanced,
      h('div', { class: 'row', style: { marginTop: '8px' } }, test, preview), out),
    footer: [btn('Cancel', () => m.close()), btn(editing ? 'Save' : 'Add the lane', save, { cls: 'primary' })],
  });
}

// ------------------------------------------------------------------ access (administrators)
async function renderAccess(body) {
  let s;
  try { s = await api.settings(); } catch (e) { clear(body, h('div', { class: 'alert error' }, e.message)); return; }
  const modes = {
    users: ['shield', 'Personal accounts', `Everyone logs in with their own user name and password (${s.num_users} account${s.num_users === 1 ? '' : 's'}), on this computer too.`],
    token: ['key', 'Access token', 'Whoever has the token printed when the server starts uses CryoPlug with every right. Create accounts (Users tab) to give everyone their own login.'],
    password: ['key', 'Shared password', 'Everyone uses the password of [server] password, with every right. Create accounts (Users tab) to give everyone their own login.'],
    null: ['monitor', 'This computer only', 'CryoPlug answers on this computer only, without login. Create accounts (Users tab), then open the port to use it from other computers.'],
  };
  const [ic, title, text] = modes[s.mode] || modes.null;
  const days = h('input', { type: 'number', id: 'set-days', min: s.limits.session_days.min, max: s.limits.session_days.max, value: s.values.session_days });
  const minLen = h('input', { type: 'number', id: 'set-minlen', min: s.limits.min_password_length.min, max: s.limits.min_password_length.max,
    value: s.values.min_password_length });
  const save = async (e) => {
    e.preventDefault();
    try {
      const res = await guard(api.updateSettings({ session_days: days.value, min_password_length: minLen.value }), 'Settings saved');
      state.info.password_min = res.values.min_password_length;
    } catch { /* shown */ }
  };
  const listen = `${s.listen.host}:${s.listen.port}`;
  clear(body, h('div', { class: 'settings-grid' },
    h('div', {},
      section('How CryoPlug is protected', h('div', { class: 'box settings-box' },
        h('div', { class: 'mode-head' }, h('span', { class: `mode-icon ${s.mode || 'local'}` }, icon(ic)), h('div', {}, h('div', { class: 'strong' }, title),
          h('div', { class: 'muted small' }, text))),
        h('div', { style: { marginTop: '10px' } },
          h('div', { class: 'kv' }, h('div', { class: 'k' }, 'Listening on'), h('div', { class: 'v mono' }, listen)),
          h('div', { class: 'kv' }, h('div', { class: 'k' }, 'Other computers'), h('div', { class: 'v' }, s.listen.network ? 'can connect' : 'cannot connect (localhost only)')),
          h('div', { class: 'kv' }, h('div', { class: 'k' }, 'Encryption'), h('div', { class: 'v' }, s.https ? 'HTTPS' : 'none (http)'))),
        s.listen.network && !s.https ? h('div', { class: 'alert warn' }, 'Passwords and data cross the network unencrypted. '
          + 'On an open port, set up HTTPS ([server] ssl_certfile and ssl_keyfile, see the README).') : null,
        s.accounts && s.shared_password ? h('div', { class: 'alert info' }, '[server] password is no longer used: accounts replace it.') : null,
        !s.listen.network ? h('div', { class: 'alert info' }, 'To open the port to the other computers, set [server] host = "0.0.0.0" in ',
          h('code', {}, s.config_path || '~/.cryoplug/config.toml'), ' and restart CryoPlug (open the firewall port if needed).') : null)),
      section('Logins', h('form', { class: 'box settings-box', onsubmit: save },
        field('Session duration (days)', days, 'How long a browser stays logged in. Changes apply to the next logins.'),
        field('Minimum password length', minLen, 'For new passwords.'),
        h('div', { class: 'row end' }, h('button', { class: 'btn primary', type: 'submit' }, 'Save'))))),
    h('div', {}, section('Good to know', h('div', { class: 'box settings-box' }, h('ul', { class: 'notes' },
      h('li', {}, 'Jobs run under the Unix account that started CryoPlug, as CryoSPARC jobs run as the cryosparc user. '
        + 'The folders of each user limit what they can pick in CryoPlug; for a strict separation of the data, also use Unix permissions.'),
      h('li', {}, 'After 5 wrong passwords for one account from one computer (20 for all accounts), logins from there pause for 5 minutes.'),
      h('li', {}, 'A forgotten administrator password is reset on the server: ', h('code', {}, 'cryoplug user passwd <name>'), '.'),
      h('li', {}, 'Scripts use the API with the account\'s credentials: ', h('code', {}, 'curl -u name:password …'), '.'),
      h('li', {}, 'Each project has an owner; the owner (or an administrator) shares it from its menu on the Projects page.')))))));
}

// ------------------------------------------------------------------ temporary password
// Shown instead of the application until the user replaces the password an administrator gave them.
export function renderPasswordChange(content) {
  const me = state.info.user;
  const cur = passwordInput('fp-current', 'current-password');
  const pw1 = passwordInput('fp-new');
  const pw2 = passwordInput('fp-again');
  const submit = async (e) => {
    e.preventDefault();
    if (pw1.value !== pw2.value) { toast('The two new passwords differ', 'error'); return; }
    try { await guard(api.changePassword(cur.value, pw1.value)); } catch { return; }
    toast('Password saved', 'ok');
    setTimeout(() => location.reload(), 400);
  };
  const logout = async () => { await fetch('/logout', { method: 'POST' }); location.assign('/login'); };
  clear(content, h('div', { class: 'login-page' }, h('form', { class: 'login-card', onsubmit: submit },
    h('div', { class: 'login-brand' }, avatar(me, 'big'), h('div', {}, h('h1', {}, 'Choose your password'),
      h('div', { class: 'muted small' }, me.full_name || me.username))),
    h('p', { class: 'small', style: { marginTop: 0 } }, 'You logged in with a password given by an administrator: replace it with your own to continue.'),
    h('input', { type: 'text', autocomplete: 'username', value: me.username, hidden: true, readonly: true }),
    field('Password you were given', cur), field('New password', pw1, `At least ${state.info.password_min || 8} characters.`),
    field('New password again', pw2),
    h('button', { class: 'btn primary', type: 'submit' }, 'Save and continue'),
    h('p', { class: 'login-hint' }, h('a', { href: '#', onclick: (e) => { e.preventDefault(); logout(); } }, 'Log out')))));
  setTimeout(() => cur.focus(), 50);
}
