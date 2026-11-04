// How a map is cut into bricks for the 3D viewer, so that its isosurfaces are computed on the GPU whatever its size.
// Mol* computes an isosurface on the GPU only when the whole grid fits in one 2D texture of at most half the
// largest texture the GPU allows (its own rule, reproduced here); above that it falls back to marching cubes on the
// CPU, seconds per contour level for a 300³ map. Cut into bricks that each pass the rule, every brick gets its own
// GPU surface; the bricks overlap by one voxel so that the surfaces meet. Pure functions: tested with Node.

export const MAX_PARTS = 6; // per axis, as the server allows
export const BRICK_MARGIN = 1; // voxels of margin on each side of a brick, as the server cuts them (mrc.parts_plan)
const PADDING = 1; // Mol* pads the grid by one voxel in its texture

// The 2D texture layout Mol* uses for a grid of `dims` voxels (mol-repr/volume/util getVolumeTexture2dLayout).
export function textureLayout(dims, padding = PADDING) {
  const area = dims[0] * dims[1] * dims[2];
  const squareDim = Math.sqrt(area);
  let powerOfTwoSize = 2 ** Math.ceil(Math.log(squareDim) / Math.log(2));
  let width = dims[0] + padding;
  let height = dims[1] + padding;
  let rows = 1;
  let columns = width;
  if (powerOfTwoSize < width * dims[2]) {
    columns = Math.floor(powerOfTwoSize / width);
    rows = Math.ceil(dims[2] / columns);
    width *= columns;
    height *= rows;
  } else {
    width *= dims[2];
  }
  if (height >= powerOfTwoSize) powerOfTwoSize *= 2;
  return { width, height, columns, rows, powerOfTwoSize };
}

// Whether Mol* would compute the isosurface of a grid on a GPU whose largest texture is `maxTextureSize` voxels wide.
export function suitableForGpu(dims, maxTextureSize) {
  if (!(maxTextureSize > 0) || dims[0] * dims[1] * dims[2] < 1000) return false;
  return textureLayout(dims).powerOfTwoSize <= maxTextureSize / 2;
}

// The edges of `p` parts of `n` voxels, as the server cuts them: part i spans [edges[i], edges[i + 1] + 1).
export function partEdges(n, p) {
  const count = Math.max(1, Math.min(p, MAX_PARTS, n));
  const edges = [];
  for (let i = 0; i < count; i++) edges.push(Math.floor((n * i) / count));
  edges.push(n);
  return edges;
}

// The bricks of a map, as the server cuts them: [{ index: [i, j, k], start: [x, y, z], size: [nx, ny, nz], core }],
// x fastest. Brick i owns the voxels [edge_i, edge_i+1] of each axis (the edge voxel is shared with the next brick,
// so that their isosurfaces meet) and carries `margin` more voxels on each side, so that Mol* computes right surface
// normals at the seam. `core` is the owned box in the brick's own voxel indices, [[x0, x1], [y0, y1], [z0, z1]]
// inclusive: the viewer draws the brick's surface inside it only, which hides the margins (drawn by the neighbour)
// and the surfaces Mol*'s GPU isosurface adds at the edges of a grid.
export function brickBoxes(dims, parts, margin = BRICK_MARGIN) {
  const edges = dims.map((n, a) => partEdges(n, parts[a]));
  const out = [];
  for (let k = 0; k < edges[2].length - 1; k++) {
    for (let j = 0; j < edges[1].length - 1; j++) {
      for (let i = 0; i < edges[0].length - 1; i++) {
        const idx = [i, j, k];
        const own = idx.map((v, a) => [edges[a][v], Math.min(dims[a] - 1, edges[a][v + 1])]);
        const start = own.map(([lo]) => Math.max(0, lo - margin));
        const end = own.map(([, hi], a) => Math.min(dims[a], hi + 1 + margin));
        out.push({ index: idx, start, size: end.map((e, a) => e - start[a]), core: own.map(([lo, hi], a) => [lo - start[a], hi - start[a]]) });
      }
    }
  }
  return out;
}

// How to cut a grid of `dims` voxels so that every brick gets a GPU isosurface: the fewest parts per axis (the same
// on every axis, the largest axis deciding) whose bricks pass Mol*'s rule. gpu: { ok, maxTextureSize }. Without a
// GPU able to do it (no float textures), one part: the CPU path, which the viewer then keeps responsive otherwise.
export function brickPlan(dims, gpu) {
  const voxels = dims[0] * dims[1] * dims[2];
  if (!gpu?.ok || !(gpu.maxTextureSize > 0)) return { parts: [1, 1, 1], gpu: false, bricks: brickBoxes(dims, [1, 1, 1]), voxels };
  for (let p = 1; p <= MAX_PARTS; p++) {
    const parts = dims.map((n) => Math.min(p, Math.max(1, Math.ceil(n / Math.max(...dims) * p))));
    const bricks = brickBoxes(dims, parts);
    if (bricks.every((b) => suitableForGpu(b.size, gpu.maxTextureSize) || b.size[0] * b.size[1] * b.size[2] < 1000)) {
      return { parts, gpu: true, bricks, voxels };
    }
  }
  // too large even in MAX_PARTS³ bricks (a map over ~1500³ on a small GPU): the CPU path, part by part
  return { parts: [MAX_PARTS, MAX_PARTS, MAX_PARTS], gpu: false, bricks: brickBoxes(dims, [MAX_PARTS, MAX_PARTS, MAX_PARTS]), voxels };
}

// Bytes a map costs the browser: the float32 values of every brick (overlaps included) plus the GPU's byte texture.
export function memoryEstimate(plan) {
  const voxels = plan.bricks.reduce((n, b) => n + b.size[0] * b.size[1] * b.size[2], 0);
  return { voxels, bytes: voxels * (4 + (plan.gpu ? 1 : 0)) };
}
