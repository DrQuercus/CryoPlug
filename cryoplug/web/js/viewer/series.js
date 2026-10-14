// Volume series (cryoDRGN cluster volumes and trajectories, 3D variability frames): every frame is loaded once
// with its own isosurface, all hidden but the current one, so that playing only switches visibility.
// Frames load a few at a time, the frame asked for (slider, playback) first; a display change (level, style,
// colour) redraws the current frame at once and the others in the background.
import * as scene from './scene.js';
import { mapStatistics } from './stats.js';

export const SPEEDS = [1, 2, 4, 6, 10, 15, 24];
const WORKERS = 3; // frames downloaded and prepared at the same time

export function initSeries(item, paths, labels = [], keep = null) {
  item.series = {
    frames: paths.map((path, i) => ({ path, index: i, label: labels[i] || `Frame ${i + 1}`, refs: {}, volume: null, stats: null,
      version: -1, state: 'pending' })),
    index: keep?.index ?? 0, want: keep?.index ?? 0, fps: keep?.fps ?? 6, mode: keep?.mode ?? 'loop', dir: 1, playing: false,
    version: 0, loaded: 0, failed: 0, closed: false,
  };
  return item.series;
}

// ctx: { fetch(item, frame) -> bytes of the map, onFrame(item), onLoad(item), onError(err, what) }
export function createSeriesPlayer(ctx) {
  const current = (item) => item.series.frames[item.series.index];

  async function loadFrame(item, frame) {
    const s = item.series;
    frame.state = 'loading';
    const bytes = await ctx.fetch(item, frame);
    if (s.closed) return;
    const r = await scene.loadVolume(bytes, `${item.label} · ${frame.label}`);
    if (s.closed) { await scene.remove(r.dataRef); return; }
    frame.refs = { data: r.dataRef, volume: r.volumeRef, repr: null };
    frame.volume = r.volume;
    frame.stats = mapStatistics(r.volume);
    frame.state = 'ready';
    s.loaded += 1;
  }

  // The frame shown first (the current one): becomes the item's map (refs, volume, statistics) before it is drawn.
  async function loadFirst(item) {
    const frame = current(item);
    frame.ready = Promise.resolve();
    await loadFrame(item, frame);
    item.refs = frame.refs;
    item.volume = frame.volume;
    item.stats = frame.stats;
  }

  // Next frame to load: the nearest after the one wanted, in the direction of playback.
  function nextPending(s) {
    const n = s.frames.length;
    let best = null;
    let bestD = Infinity;
    for (const f of s.frames) {
      if (f.state !== 'pending') continue;
      const d = s.dir < 0 ? (s.want - f.index + n) % n : (f.index - s.want + n) % n;
      if (d < bestD) { bestD = d; best = f; }
    }
    if (best) best.state = 'queued';
    return best;
  }

  // The other frames, WORKERS at a time, each drawn (hidden) as soon as it is there.
  function loadRest(item) {
    const s = item.series;
    for (const f of s.frames) {
      if (f.state === 'pending') f.ready = new Promise((resolve) => { f.done = resolve; });
    }
    const work = async () => {
      for (let f = nextPending(s); f && !s.closed; f = nextPending(s)) {
        try {
          await loadFrame(item, f);
          if (!s.closed && f.volume) await drawFrame(item, f);
        } catch (err) {
          f.state = 'failed';
          f.error = err;
          s.failed += 1;
          if (!s.closed) ctx.onError(err, `Volume ${f.label}: `);
        }
        f.done?.();
        if (!s.closed) ctx.onLoad(item);
      }
    };
    return Promise.all(Array.from({ length: WORKERS }, work));
  }

  // Build or update the isosurface of a frame with the item's settings (one update at a time per frame,
  // the last settings win), then show it only if it is the current frame.
  function drawFrame(item, frame) {
    const s = item.series;
    if (!frame.volume) return Promise.resolve();
    if (frame.running) { frame.again = true; return frame.running; }
    frame.running = (async () => {
      try {
        do {
          frame.again = false;
          const version = s.version;
          await scene.showMap({ ...item, refs: frame.refs, volume: frame.volume, visible: item.visible && frame === current(item) });
          frame.version = version;
          scene.setHidden(frame.refs.repr, !(item.visible && frame === current(item)));
        } while (frame.again && !s.closed);
      } catch (err) {
        ctx.onError(err);
      } finally {
        frame.running = null;
      }
    })();
    return frame.running;
  }

  // Display settings changed: the current frame now, the others once the changes stop.
  function redraw(item) {
    const s = item.series;
    s.version += 1;
    drawFrame(item, current(item));
    clearTimeout(s.refreshTimer);
    s.refreshTimer = setTimeout(() => refreshAll(item), 350);
  }

  // Redraw every frame now, the current one first (a colouring change: the previous colour map can then go).
  async function redrawAll(item) {
    const s = item.series;
    s.version += 1;
    clearTimeout(s.refreshTimer);
    for (const frame of [current(item), ...s.frames.filter((f) => f !== current(item))]) {
      if (s.closed) return;
      await drawFrame(item, frame);
    }
  }

  async function refreshAll(item) {
    const s = item.series;
    if (s.refreshing) { s.refreshAgain = true; return; }
    s.refreshing = true;
    try {
      do {
        s.refreshAgain = false;
        for (const frame of s.frames) {
          if (s.closed) return;
          if (frame.volume && frame.version !== s.version) await drawFrame(item, frame);
        }
      } while (s.refreshAgain);
    } finally {
      s.refreshing = false;
    }
  }

  // Switch to frame i. The last request wins: scrubbing over frames still loading only waits for the last one asked.
  async function go(item, i) {
    const s = item.series;
    const n = s.frames.length;
    s.want = ((i % n) + n) % n;
    if (s.switching) return;
    s.switching = true;
    try {
      while (s.want !== s.index && !s.closed) {
        const target = s.want;
        const next = s.frames[target];
        if (!next.volume && next.ready) {
          ctx.onLoad(item); // the bar shows which volume is awaited
          await next.ready;
          if (s.want !== target) continue;
        }
        if (!next.volume) { s.want = s.index; break; } // could not be loaded
        if (next.version !== s.version || !next.refs.repr) await drawFrame(item, next);
        const prev = current(item);
        s.index = target;
        item.refs = next.refs;
        item.volume = next.volume;
        item.stats = next.stats;
        if (item.visible) scene.setHidden(next.refs.repr, false);
        if (prev !== next) scene.setHidden(prev.refs.repr, true);
        ctx.onFrame(item);
      }
    } finally {
      s.switching = false;
    }
  }

  function nextIndex(s) {
    const n = s.frames.length;
    if (s.mode !== 'bounce') return (s.index + 1) % n;
    if (s.index + s.dir < 0 || s.index + s.dir >= n) s.dir = -s.dir;
    return s.index + s.dir;
  }

  // Play; `once` stops after one pass (the last frame, or back to the first in back-and-forth), for recordings.
  function play(item, fps, { once = false, onEnd = null } = {}) {
    const s = item.series;
    if (fps) s.fps = fps;
    if (s.playing) return;
    s.playing = true;
    let steps = once ? (s.mode === 'bounce' ? 2 * (s.frames.length - 1) : s.frames.length - 1) : Infinity;
    const tick = async () => {
      if (!s.playing || s.closed) return;
      if (steps <= 0) { pause(item); onEnd?.(); return; }
      steps -= 1;
      const start = performance.now();
      await go(item, nextIndex(s));
      if (!s.playing || s.closed) return;
      s.timer = setTimeout(tick, Math.max(0, 1000 / s.fps - (performance.now() - start)));
    };
    s.timer = setTimeout(tick, 1000 / s.fps);
    ctx.onFrame(item);
  }

  function pause(item) {
    const s = item.series;
    if (!s.playing) return;
    s.playing = false;
    clearTimeout(s.timer);
    ctx.onFrame(item);
  }

  async function dispose(item, series = item.series) {
    series.closed = true;
    series.playing = false;
    clearTimeout(series.timer);
    clearTimeout(series.refreshTimer);
    for (const frame of series.frames) if (frame.refs.data) await scene.remove(frame.refs.data);
  }

  // Load the series again (another detail or filter): the current volume first, shown as soon as it is ready
  // (adjust(item) sets the level for it before it is drawn), then the old volumes go and the others follow.
  async function reload(item, adjust = null) {
    const old = item.series;
    const wasPlaying = old.playing;
    pause(item);
    old.closed = true; // stops its loaders
    initSeries(item, old.frames.map((f) => f.path), old.frames.map((f) => f.label), old);
    try {
      await loadFirst(item);
      adjust?.(item);
      await drawFrame(item, current(item));
    } catch (err) {
      await dispose(item, old);
      throw err;
    }
    await dispose(item, old);
    ctx.onFrame(item);
    item.series.rest = loadRest(item);
    if (wasPlaying) play(item);
  }

  return { loadFirst, loadRest, redraw, redrawAll, go, play, pause, dispose, reload, step: (item, d) => go(item, item.series.index + d) };
}
