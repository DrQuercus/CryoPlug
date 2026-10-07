// Orthogonal slices through the active map (CryoSPARC-like): grey levels between the 1st and 99.9th
// percentiles, voxels above the contour level tinted with the map colour, crosshairs at the other slices.
import { clear, h } from '../ui.js';

const VIEWS = [
  { label: 'XY', axes: [0, 1], fixed: 2 },
  { label: 'XZ', axes: [0, 2], fixed: 1 },
  { label: 'YZ', axes: [1, 2], fixed: 0 },
];

export function createSlices(root) {
  let item = null;
  let pos = [0, 0, 0];
  const views = VIEWS.map((v) => {
    const canvas = h('canvas', { width: 1, height: 1, 'aria-label': `${v.label} slice` });
    const range = h('input', { type: 'range', min: 0, max: 0, step: 1, 'aria-label': `${v.label} slice position` });
    const where = h('span', { class: 'muted' });
    range.addEventListener('input', () => { pos[v.fixed] = Number(range.value); drawAll(); });
    const el = h('div', { class: 'vw-slice' }, h('div', { class: 'vw-slice-head' }, h('b', {}, v.label), where), canvas, range);
    return { ...v, canvas, range, where, el };
  });
  clear(root, views.map((v) => v.el));

  function draw(v) {
    const { data, space } = item.volume.grid.cells;
    const dims = space.dimensions;
    const [a, b] = v.axes;
    const w = dims[a];
    const ht = dims[b];
    const { low, high } = item.stats;
    const range = high - low || 1;
    const tint = [(item.color >> 16) & 255, (item.color >> 8) & 255, item.color & 255];
    const level = item.level;
    v.canvas.width = w;
    v.canvas.height = ht;
    const ctx = v.canvas.getContext('2d');
    const img = ctx.createImageData(w, ht);
    const c = [0, 0, 0];
    c[v.fixed] = pos[v.fixed];
    for (let j = 0; j < ht; j++) {
      c[b] = j;
      for (let i = 0; i < w; i++) {
        c[a] = i;
        const value = space.get(data, c[0], c[1], c[2]);
        let g = ((value - low) / range) * 255;
        g = g < 0 ? 0 : g > 255 ? 255 : g;
        const o = ((ht - 1 - j) * w + i) * 4; // y axis up
        if (value >= level) {
          img.data[o] = g * 0.5 + tint[0] * 0.5;
          img.data[o + 1] = g * 0.5 + tint[1] * 0.5;
          img.data[o + 2] = g * 0.5 + tint[2] * 0.5;
        } else {
          img.data[o] = g;
          img.data[o + 1] = g;
          img.data[o + 2] = g;
        }
        img.data[o + 3] = 255;
      }
    }
    ctx.putImageData(img, 0, 0);
    ctx.fillStyle = 'rgba(134, 182, 239, 0.75)';
    ctx.fillRect(pos[a], 0, Math.max(1, w / 156), ht);
    ctx.fillRect(0, ht - 1 - pos[b], w, Math.max(1, ht / 156));
    v.where.textContent = ` ${'xyz'[v.fixed]} ${pos[v.fixed] + 1}/${dims[v.fixed]}`;
  }

  function drawAll() {
    if (item && !root.hidden) views.forEach(draw);
  }

  return {
    show(target) {
      const dims = target.volume.grid.cells.space.dimensions;
      if (!item || item !== target || pos.some((p, k) => p >= dims[k])) pos = dims.map((d) => Math.floor(d / 2));
      item = target;
      views.forEach((v) => { v.range.max = String(dims[v.fixed] - 1); v.range.value = String(pos[v.fixed]); });
      root.hidden = false;
      drawAll();
    },
    hide() { root.hidden = true; },
    refresh(target) { if (target && target !== item) return; drawAll(); },
    get item() { return root.hidden ? null : item; },
  };
}
