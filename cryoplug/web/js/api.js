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

export const api = {
  info: () => request('GET', '/api/info'),
  jobtypes: () => request('GET', '/api/jobtypes'),
  tools: () => request('GET', '/api/tools'),
  checkTools: () => request('POST', '/api/tools/check'),
  workflows: () => request('GET', '/api/workflows'),
  queue: () => request('GET', '/api/queue'),
  fs: (path, hidden = false) => request('GET', `/api/fs?path=${enc(path || '')}&show_hidden=${hidden}`),

  projects: () => request('GET', '/api/projects'),
  createProject: (body) => request('POST', '/api/projects', body),
  project: (p) => request('GET', `/api/projects/${p}`),
  updateProject: (p, body) => request('PATCH', `/api/projects/${p}`, body),
  deleteProject: (p, files) => request('DELETE', `/api/projects/${p}?delete_files=${!!files}`),

  jobs: (p) => request('GET', `/api/projects/${p}/jobs`),
  createJob: (p, body) => request('POST', `/api/projects/${p}/jobs`, body),
  job: (p, j) => request('GET', `/api/projects/${p}/jobs/${j}`),
  updateJob: (p, j, body) => request('PATCH', `/api/projects/${p}/jobs/${j}`, body),
  queueJob: (p, j, lane) => request('POST', `/api/projects/${p}/jobs/${j}/queue`, { lane }),
  killJob: (p, j) => request('POST', `/api/projects/${p}/jobs/${j}/kill`),
  clearJob: (p, j) => request('POST', `/api/projects/${p}/jobs/${j}/clear`),
  cloneJob: (p, j) => request('POST', `/api/projects/${p}/jobs/${j}/clone`),
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
  instantiate: (p, wid, body) => request('POST', `/api/projects/${p}/workflows/${wid}`, body),

  fileUrl: (p, path, download = false) => `/api/projects/${p}/file?path=${enc(path)}${download ? '&download=true' : ''}`,
  previewUrl: (p, path) => `/api/projects/${p}/preview?path=${enc(path)}`,
  zoneUrl: (p, map, model, radius, maxBox) => `/api/projects/${p}/zone?map=${enc(map)}&model=${enc(model)}&radius=${radius}&max_box=${maxBox}`,
  bundleUrl: (p, j) => `/api/projects/${p}/jobs/${j}/interactive/bundle`,
};
