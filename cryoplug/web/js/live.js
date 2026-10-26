// The dashboard of a live job (as CryoSPARC Live): the counts of the session, its rate and state, and "Stop after this
// cycle", which ends the job with its outputs (where Kill would discard them).
import { api } from './api.js';
import { btn, h, liveAgo } from './ui.js';

const COUNTS = [['movies', 'Movies'], ['corrected', 'Motion-corrected'], ['ctf', 'With CTF'], ['accepted', 'Accepted'],
  ['rejected', 'Rejected'], ['picked', 'Picked'], ['particles', 'Particles']];

// `stop()`: asks the running job to stop after its current cycle (resolves when asked).
export function liveDashboard(sec, { job, stop = null }) {
  const running = ['launched', 'running'].includes(job.status);
  const stopping = running && /^Stopping/.test(job.message || '');
  const status = running ? (stopping ? 'stopping after this cycle' : sec.status || 'watching') : job.status === 'completed' ? 'stopped' : job.status;
  const counts = sec.counts || {};
  const accepted = counts.accepted !== undefined && counts.ctf ? ` (${((100 * counts.accepted) / counts.ctf).toFixed(0)} %)` : '';
  const tiles = COUNTS.filter(([k]) => counts[k] !== undefined && counts[k] !== null).map(([k, label]) => h('div', { class: 'live-tile' },
    h('span', { class: 'muted small' }, label), h('b', {}, Number(counts[k]).toLocaleString(), k === 'accepted' ? h('span', { class: 'muted small' }, accepted) : null)));
  const facts = [
    sec.started ? h('span', {}, 'started ', liveAgo(sec.started)) : null,
    sec.processed ? h('span', {}, `${sec.processed} processing cycle${sec.processed > 1 ? 's' : ''}`) : null,
    sec.rate ? h('span', {}, `≈ ${Math.round(sec.rate).toLocaleString()} movies / hour`) : null,
    counts.movies ? h('span', {}, 'last new movie ', liveAgo(sec.last_new)) : null,
    running && !stopping && sec.interval ? h('span', {}, `checks every ${sec.interval} s`) : null,
  ].filter(Boolean);
  const stopBtn = running && !stopping && stop ? btn('Stop after this cycle', async (e) => {
    e.currentTarget.disabled = true;
    await stop();
  }, { cls: 'primary', ic: 'check', title: 'Processes the movies arrived meanwhile, then ends the job with its outputs' }) : null;
  return h('div', { class: 'live' },
    h('div', { class: 'live-head' }, h('h4', {}, sec.title || 'Live session'),
      h('span', { class: `chip ${running ? 'running' : job.status}` }, status), h('span', { class: 'grow' }), stopBtn),
    h('div', { class: 'live-tiles' }, tiles.length ? tiles : h('span', { class: 'muted' }, 'Waiting for the first movies…')),
    facts.length ? h('div', { class: 'live-facts muted small' }, facts.flatMap((f, i) => (i ? [' · ', f] : [f]))) : null,
    sec.error ? h('div', { class: 'alert warn small' }, `Last cycle failed (tried again at the next one): ${sec.error}`) : null);
}
