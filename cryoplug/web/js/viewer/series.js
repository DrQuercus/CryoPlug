// Volume series (cryoDRGN cluster volumes and trajectories, 3D variability frames): every frame is loaded once
// with its own isosurface, all hidden but the current one, so that playing only switches visibility.
// A display change (level, style, colour) redraws the current frame at once and the others in the background.
import * as scene from './scene.js';
import { mapStatistics } from './stats.js';

export const SPEEDS = [1, 2, 4, 6, 10, 15, 24];

// Frames are binned previews: smaller grids for longer series keep the memory reasonable.
export const seriesBox = (n) => (n > 40 ? 80 : n > 24 ? 96 : 128);

export function initSeries(item, paths, labels = []) {
  item.series = {
    frames: paths.map((path, i) => ({ path, label: labels[i] || `Frame ${i + 1}`, refs: {}, volume: null, stats: null, version: -1 })),
    index: 0, want: 0, fps: 6, mode: 'loop', dir: 1, playing: false, version: 0, loaded: 0, closed: false,
  };
  return item.series;
}

// ctx: { url(path, n) -> preview URL, onFrame(item), onLoad(item), onError(err) }
export function createSeriesPlayer(ctx) {
  const current = (item) => item.series.frames[item.series.index];

  async function loadFrame(item, frame) {
    const s = item.series;
    const r = await scene.loadVolume(ctx.url(frame.path, s.frames.length), `${item.label} · ${frame.label}`);
    if (s.closed) { await scene.remove(r.dataRef); return; }
    frame.refs = { data: r.dataRef, volume: r.volumeRef, repr: null };
    frame.volume = r.volume;
    frame.stats = mapStatistics(r.volume);
    s.loaded += 1;
  }

  // First frame: becomes the item's map (refs, volume, statistics) before the item is shown.
  async function loadFirst(item) {
    const frame = item.series.frames[0];
    await loadFrame(item, frame);
    item.refs = frame.refs;
    item.volume = frame.volume;
    item.stats = frame.stats;
  }

  // The other frames, one after the other, each drawn (hidden) as soon as it is there.
  function loadRest(item) {
    const s = item.series;
    let chain = Promise.resolve();
    for (const frame of s.frames.slice(1)) {
      frame.ready = chain = chain.then(async () => {
        if (s.closed) return;
        try {
          await loadFrame(item, frame);
          if (!s.closed) await drawFrame(item, frame);
        } catch (err) {
          frame.error = err;
          ctx.onError(err, `Frame ${frame.label}: `);
        }
        ctx.onLoad(item);
      });
    }
    return chain;
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

  // Switch to frame i (the last request wins while a frame is being prepared).
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
        if (!next.volume && next.ready) await next.ready;
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

  function play(item, fps) {
    const s = item.series;
    if (fps) s.fps = fps;
    if (s.playing) return;
    s.playing = true;
    const tick = async () => {
      if (!s.playing || s.closed) return;
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
    s.playing = false;
    clearTimeout(s.timer);
    ctx.onFrame(item);
  }

  async function dispose(item) {
    const s = item.series;
    s.closed = true;
    s.playing = false;
    clearTimeout(s.timer);
    clearTimeout(s.refreshTimer);
    for (const frame of s.frames) if (frame.refs.data) await scene.remove(frame.refs.data);
  }

  return { loadFirst, loadRest, redraw, redrawAll, go, play, pause, dispose, step: (item, d) => go(item, item.series.index + d) };
}
