// Thin wrapper around the CryoPlug HTTP API.
async function request(method, url, body, isForm = false) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    if (isForm) {
      opts.body = body;
    } else {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
  }
  const res = await fetch(url, opts);
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (res.status === 401) {
    // Session expired or never opened: back to the login page, then here again.
    location.assign(`/login?${new URLSearchParams({ next: location.pathname })}${location.hash}`);
  }
  if (res.status === 403 && data && data.code === 'password_change') {
    // A temporary password must be replaced before anything else (handled by app.js).
    window.dispatchEvent(new CustomEvent('cryoplug-password-change'));
  }
  if (!res.ok) {
    let detail = data && data.detail !== undefined ? data.detail : text || res.statusText;
    if (Array.isArray(detail)) detail = detail.map((d) => d.msg || JSON.stringify(d)).join('; ');
    const err = new Error(String(detail));
    err.status = res.status;
    throw err;
  }
  return data;
}

const enc = encodeURIComponent;

// The workspace open on the project page ('' = all the jobs): the jobs created, cloned or made from a workflow go
// into it, as in CryoSPARC.
let workspace = '';
export function setWorkspace(uid) { workspace = uid || ''; }
const inWorkspace = (body = {}) => (workspace && !body.workspaces ? { ...body, workspaces: [workspace] } : body);

const viewOptions = (encoding, lowpass) => `${encoding ? `&enc=${encoding}` : ''}${lowpass ? `&lowpass=${lowpass}` : ''}`;

export const api = {
  info: () => request('GET', '/api/info'),
  jobtypes: () => request('GET', '/api/jobtypes'),
  tools: () => request('GET', '/api/tools'),
  checkTools: () => request('POST', '/api/tools/check'),
  workflows: () => request('GET', '/api/workflows'),
  queue: () => request('GET', '/api/queue'),
  monitor: (history = false) => request('GET', `/api/monitor${history ? '?history=true' : ''}`),
  cluster: () => request('GET', '/api/cluster'),
  lanes: () => request('GET', '/api/lanes'),
  putLanes: (lanes) => request('PUT', '/api/lanes', { lanes }),
  resetLanes: () => request('PUT', '/api/lanes', { reset: true }),
  hardware: () => request('GET', '/api/hardware'),
  testLane: (lane) => request('POST', '/api/lanes/test', { lane }),
  previewLane: (lane) => request('POST', '/api/lanes/preview', { lane }),
  fs: (path, hidden = false, project = '') => request('GET', `/api/fs?path=${enc(path || '')}&show_hidden=${hidden}`
    + (project ? `&project=${enc(project)}` : '')),
  uploads: (p) => request('GET', `/api/projects/${p}/uploads`),
  startUpload: (p, body) => request('POST', `/api/projects/${p}/uploads`, body),
  cancelUpload: (p, rel) => request('DELETE', `/api/projects/${p}/uploads?rel=${enc(rel)}`),

  me: () => request('GET', '/api/me'),
  updateMe: (body) => request('PATCH', '/api/me', body),
  changePassword: (current, password) => request('POST', '/api/me/password', { current, new: password }),
  mySessions: () => request('GET', '/api/me/sessions'),
  endOtherSessions: () => request('POST', '/api/me/sessions/end-others'),
  myTokens: () => request('GET', '/api/me/tokens'),
  createToken: (label, days) => request('POST', '/api/me/tokens', { label, days }),
  revokeToken: (id) => request('DELETE', `/api/me/tokens/${encodeURIComponent(id)}`),
  agentInfo: () => request('GET', '/api/agent'),
  users: () => request('GET', '/api/users'),
  people: () => request('GET', '/api/people'),
  createUser: (body) => request('POST', '/api/users', body),
  updateUser: (name, body) => request('PATCH', `/api/users/${enc(name)}`, body),
  resetPassword: (name, body) => request('POST', `/api/users/${enc(name)}/password`, body),
  deleteUser: (name, transferTo) => request('DELETE', `/api/users/${enc(name)}?transfer_to=${enc(transferTo || '')}`),
  settings: () => request('GET', '/api/settings'),
  updateSettings: (body) => request('PATCH', '/api/settings', body),

  projects: () => request('GET', '/api/projects'),
  createProject: (body) => request('POST', '/api/projects', body),
  project: (p) => request('GET', `/api/projects/${p}`),
  updateProject: (p, body) => request('PATCH', `/api/projects/${p}`, body),
  deleteProject: (p, files) => request('DELETE', `/api/projects/${p}?delete_files=${!!files}`),

  jobs: (p) => request('GET', `/api/projects/${p}/jobs`),
  createJob: (p, body) => request('POST', `/api/projects/${p}/jobs`, inWorkspace(body)),
  job: (p, j) => request('GET', `/api/projects/${p}/jobs/${j}`),
  updateJob: (p, j, body) => request('PATCH', `/api/projects/${p}/jobs/${j}`, body),
  queueJob: (p, j, lane) => request('POST', `/api/projects/${p}/jobs/${j}/queue`, lane ? { lane } : {}),
  killJob: (p, j) => request('POST', `/api/projects/${p}/jobs/${j}/kill`),
  clearJob: (p, j) => request('POST', `/api/projects/${p}/jobs/${j}/clear`),
  resumeJob: (p, j) => request('POST', `/api/projects/${p}/jobs/${j}/continue`, {}),
  completeJob: (p, j) => request('POST', `/api/projects/${p}/jobs/${j}/complete`, {}),
  cloneJob: (p, j, body = {}) => request('POST', `/api/projects/${p}/jobs/${j}/clone`, inWorkspace(body)),
  cloneJobs: (p, body) => request('POST', `/api/projects/${p}/clone`, inWorkspace(body)),
  workspaces: (p) => request('GET', `/api/projects/${p}/workspaces`),
  createWorkspace: (p, body) => request('POST', `/api/projects/${p}/workspaces`, body),
  updateWorkspace: (p, w, body) => request('PATCH', `/api/projects/${p}/workspaces/${w}`, body),
  deleteWorkspace: (p, w) => request('DELETE', `/api/projects/${p}/workspaces/${w}`),
  workspaceJobs: (p, w, body) => request('POST', `/api/projects/${p}/workspaces/${w}/jobs`, body),
  deleteJob: (p, j, force) => request('DELETE', `/api/projects/${p}/jobs/${j}?force=${!!force}`),
  log: (p, j, offset) => request('GET', `/api/projects/${p}/jobs/${j}/log?offset=${offset || 0}`),
  files: (p, j, sub) => request('GET', `/api/projects/${p}/jobs/${j}/files?sub=${enc(sub || '')}`),
  launch: (p, j) => request('POST', `/api/projects/${p}/jobs/${j}/interactive/launch`),
  finish: (p, j, path) => request('POST', `/api/projects/${p}/jobs/${j}/interactive/finish`, { path }),
  upload: (p, j, file) => {
    const fd = new FormData();
    fd.append('file', file);
    return request('POST', `/api/projects/${p}/jobs/${j}/interactive/upload`, fd, true);
  },
  instantiate: (p, wid, body) => request('POST', `/api/projects/${p}/workflows/${wid}`, inWorkspace(body)),

  fileUrl: (p, path, download = false) => `/api/projects/${p}/file?path=${enc(path)}${download ? '&download=true' : ''}`,
  micrographUrl: (p, path, size = 1024, lowpass = 20, apix = 0) => `/api/projects/${p}/micrograph?path=${enc(path)}&size=${size}&lowpass=${lowpass}&apix=${apix}`,
  ctfImageUrl: (p, path, size = 384) => `/api/projects/${p}/ctf-image?path=${enc(path)}&size=${size}`,
  drift: (p, path) => request('GET', `/api/projects/${p}/drift?path=${enc(path)}`),
  control: (p, j, action, value = null) => request('POST', `/api/projects/${p}/jobs/${j}/control`, { action, value }),
  session: (p, j, name = 'state') => request('GET', `/api/projects/${p}/jobs/${j}/session?name=${enc(name)}`),
  saveSession: (p, j, name, data) => request('PUT', `/api/projects/${p}/jobs/${j}/session?name=${enc(name)}`, data),
  particleImagesUrl: (p, j, ids, size = 96, output = 'latent', perRow = 8) =>
    `/api/projects/${p}/jobs/${j}/particle-images?ids=${ids.join(',')}&size=${size}&output=${enc(output)}&per_row=${perRow}`,
  // maps for the 3D viewer: maxBox voxels per side at most (0: full size), encoding f32 / i16 / i8, low-pass in A
  previewUrl: (p, path, maxBox, { encoding, lowpass } = {}) => `/api/projects/${p}/preview?path=${enc(path)}`
    + `${maxBox !== undefined && maxBox !== null ? `&max_box=${maxBox}` : ''}${viewOptions(encoding, lowpass)}`,
  zoneUrl: (p, map, model, radius, maxBox, { encoding, lowpass } = {}) => `/api/projects/${p}/zone?map=${enc(map)}`
    + `&model=${enc(model)}&radius=${radius}&max_box=${maxBox}${viewOptions(encoding, lowpass)}`,
  mapInfo: (p, path) => request('GET', `/api/projects/${p}/map-info?path=${enc(path)}`),
  // the mask being drawn in the viewer, computed on a coarse copy of its map: the bytes of an 8-bit MRC file
  async maskPreview(p, body) {
    const res = await fetch(`/api/projects/${p}/mask-preview`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch { /* not JSON */ }
      throw new Error(String(detail));
    }
    return new Uint8Array(await res.arrayBuffer());
  },
  fit: (p, body) => request('POST', `/api/projects/${p}/fit`, body),
  bundleUrl: (p, j) => `/api/projects/${p}/jobs/${j}/interactive/bundle`,
};
