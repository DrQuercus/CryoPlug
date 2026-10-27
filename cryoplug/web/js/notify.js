// Job notifications (the bell of the sidebar): a desktop notification when a job completes, fails, is stopped or waits
// for you, while the page is in the background; a notice in the page while it is in front. Browsers allow desktop
// notifications on HTTPS and localhost only: elsewhere the notices and the tab title ("(2) CryoPlug") do.
import { api } from './api.js';
import { h, toast } from './ui.js';

const KEY = 'cryoplug.notify';
// start: when they were turned on (jobs ended before are not announced); last: the server time of the last check;
// seen: what was announced (the checks overlap: a job's end reaches the database a little after it happened)
const N = { start: 0, last: 0, seen: new Map(), waiting: null, unseen: 0, base: document.title };

const desktop = () => 'Notification' in window && window.isSecureContext;

export function notifyOn() {
  try { return localStorage.getItem(KEY) === '1'; } catch { return false; }
}

function paint() {
  const b = document.getElementById('notify-toggle');
  if (!b) return;
  const on = notifyOn();
  b.classList.toggle('on', on);
  b.setAttribute('aria-pressed', on ? 'true' : 'false');
  b.title = on ? 'Job notifications on: click to turn them off' : 'Notify me when jobs finish or wait for me';
}

function setOn(on) {
  try { localStorage.setItem(KEY, on ? '1' : '0'); } catch { /* storage blocked: on for this page only */ }
  Object.assign(N, { start: 0, last: 0, waiting: null });
  N.seen.clear();
  paint();
}

export function installNotifications() {
  paint();
  document.getElementById('notify-toggle').addEventListener('click', async () => {
    if (notifyOn()) {
      setOn(false);
      toast('Job notifications off');
      return;
    }
    if (desktop() && Notification.permission !== 'granted') {
      const answer = await Notification.requestPermission();
      if (answer === 'denied') {
        toast('This browser blocks notifications from this site: allow them in its site settings, then click the bell again', 'error', 9000);
        return;
      }
    }
    setOn(true);
    toast(desktop() && Notification.permission === 'granted'
      ? 'You will be notified when a job completes, fails or waits for you'
      : 'Notices in this page and its tab title (desktop notifications need HTTPS or localhost)', 'ok', 7000);
  });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) return;
    N.unseen = 0;
    document.title = N.base;
  });
}

const SAID = { completed: 'completed', failed: 'failed', killed: 'was stopped', waiting: 'is waiting for you' };
const KIND = { completed: 'ok', failed: 'error', killed: 'info', waiting: 'info' };

function announce(j) {
  const head = `${j.project_uid} ${j.uid} ${SAID[j.status] || j.status}`;
  const about = [j.title, j.project_title].filter(Boolean).join(' · ');
  const href = `#/p/${j.project_uid}/${j.uid}`;
  if (desktop() && Notification.permission === 'granted' && (document.hidden || !document.hasFocus())) {
    const n = new Notification(head, {
      body: about + (j.status === 'failed' && j.error ? `\n${j.error}` : ''),
      tag: `cryoplug-${j.project_uid}-${j.uid}-${j.status}`, // one notification however many CryoPlug tabs are open
      icon: 'favicon.svg',
    });
    n.onclick = () => { window.focus(); location.hash = href; n.close(); };
  } else {
    toast(h('span', {}, h('a', { href }, head), about ? ` — ${about}` : ''), KIND[j.status] || 'info', j.status === 'failed' ? 10000 : 6000);
  }
  if (document.hidden) {
    N.unseen += 1;
    document.title = `(${N.unseen}) ${N.base}`;
  }
}

// After each look at the queue (whose jobs include those waiting for you): what ended since the last check.
export async function checkNotifications(queue) {
  if (!notifyOn()) return;
  const waiting = new Set(queue.jobs.filter((j) => j.status === 'waiting' && !j.hidden).map((j) => `${j.project_uid}/${j.uid}`));
  if (N.waiting) for (const j of queue.jobs) if (waiting.has(`${j.project_uid}/${j.uid}`) && !N.waiting.has(`${j.project_uid}/${j.uid}`)) announce(j);
  N.waiting = waiting;
  const res = await api.notifications(N.last ? Math.max(N.start, N.last - 120) : 0);
  if (!N.last) N.start = res.now;
  for (const j of res.jobs) {
    const key = `${j.project_uid}/${j.uid}@${j.ended_at}`;
    if (N.seen.has(key)) continue;
    N.seen.set(key, j.ended_at);
    announce(j);
  }
  N.last = res.now;
  for (const [key, t] of N.seen) if (t < res.now - 900) N.seen.delete(key);
}
