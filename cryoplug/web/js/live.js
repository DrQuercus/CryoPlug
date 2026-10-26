// The dashboard of a live job (as CryoSPARC Live): the counts of the session, its rate and state, the acceptance limits,
// the streaming 2D classification, Pause / Resume, and "Stop after this cycle", which ends the job with its outputs
// (where Kill would discard them).
import { btn, h, liveAgo } from './ui.js';

const COUNTS = [['movies', 'Movies'], ['corrected', 'Motion-corrected'], ['ctf', 'With CTF'], ['accepted', 'Accepted'],
  ['rejected', 'Rejected'], ['picked', 'Picked'], ['particles', 'Particles']];

// `control(action)`: a request to the running job ('stop', 'pause', 'resume'), resolved once asked.
export function liveDashboard(sec, { job, control = null }) {
  const running = ['launched', 'running'].includes(job.status);
  const stopping = running && /^Stopping/.test(job.message || '');
  const paused = running && sec.status === 'paused';
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
    running && !stopping && !paused && sec.interval ? h('span', {}, `checks every ${sec.interval} s`) : null,
  ].filter(Boolean);
  const s2 = sec.class2d;
  const stream = s2 ? h('div', { class: 'live-facts muted small' }, 'Streaming 2D: ',
    s2.running ? `classifying ${Number(s2.particles).toLocaleString()} particles (run ${s2.runs})…`
      : s2.done_particles ? `${s2.runs} run${s2.runs > 1 ? 's' : ''}, the classes of ${Number(s2.done_particles).toLocaleString()} particles below` : 'waiting for particles',
    s2.error ? h('span', { class: 'warn-text' }, ` · last run: ${s2.error}`) : null) : null;
  const ask = (action) => async (e) => {
    e.currentTarget.disabled = true;
    await control(action);
  };
  const buttons = running && !stopping && control ? [
    paused ? btn('Resume', ask('resume'), { cls: 'small', ic: 'play', title: 'Process the movies again' })
      : btn('Pause', ask('pause'), { cls: 'small', title: 'The movies wait (nothing is lost); Resume goes on' }),
    btn('Stop after this cycle', ask('stop'), { cls: 'primary', ic: 'check',
      title: 'Processes the movies arrived meanwhile, then ends the job with its outputs' }),
  ] : [];
  return h('div', { class: 'live' },
    h('div', { class: 'live-head' }, h('h4', {}, sec.title || 'Live session'),
      h('span', { class: `chip ${running ? (paused ? 'waiting' : 'running') : job.status}` }, status), h('span', { class: 'grow' }), ...buttons),
    h('div', { class: 'live-tiles' }, tiles.length ? tiles : h('span', { class: 'muted' }, 'Waiting for the first movies…')),
    facts.length ? h('div', { class: 'live-facts muted small' }, facts.flatMap((f, i) => (i ? [' · ', f] : [f]))) : null,
    h('div', { class: 'live-facts muted small' }, 'Acceptance: ', (sec.limits || []).length ? sec.limits.join(' · ') : 'every micrograph',
      running ? h('span', {}, ' (change them on the histograms of the exposures below, then “Apply to the session”)') : null),
    stream,
    sec.error ? h('div', { class: 'alert warn small' }, `Last cycle failed (tried again at the next one): ${sec.error}`) : null);
}
