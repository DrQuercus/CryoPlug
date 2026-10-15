// Mol* adapter: the rendering engine behind the CryoPlug viewer. Every Mol*-specific call lives here,
// so the interface (models panel, command line, slices) only handles plain item objects.
import { apply as applyRigid, columnMajor, isIdentity } from './rigid.js';

const VIEWER_OPTIONS = {
  layoutIsExpanded: false,
  layoutShowControls: false,
  layoutShowRemoteState: false,
  layoutShowSequence: false,
  layoutShowLog: false,
  layoutShowLeftPanel: false,
  collapseLeftPanel: true,
  collapseRightPanel: true,
  viewportShowExpand: false,
  viewportShowControls: false,
  viewportShowSettings: false,
  viewportShowSelectionMode: false,
  viewportShowAnimation: false,
  viewportShowTrajectoryControls: false,
  viewportShowReset: false,
  viewportShowScreenshotControls: false,
  viewportShowToggleFullscreen: false,
  viewportFocusBehavior: 'disabled', // clicks are handled by CryoPlug (centre on the atom, like Coot)
  volumeStreamingDisabled: true,
  extensions: [],
  illumination: false,
};

// Atoms drawn for each model display style: [component, Mol* representation]. 'surface' is replaced by a gaussian
// surface for very large models (the molecular surface would take too long).
const DISPLAY_PLAN = {
  cartoon: [['polymer', 'cartoon'], ['ligand', 'ball-and-stick'], ['branched', 'ball-and-stick'], ['ion', 'ball-and-stick']],
  sticks: [['polymer', 'ball-and-stick'], ['ligand', 'ball-and-stick'], ['branched', 'ball-and-stick'], ['ion', 'ball-and-stick']],
  both: [['polymer', 'cartoon'], ['sidechain', 'ball-and-stick'], ['ligand', 'ball-and-stick'], ['branched', 'ball-and-stick'], ['ion', 'ball-and-stick']],
  spheres: [['polymer', 'spacefill'], ['ligand', 'spacefill'], ['branched', 'spacefill'], ['ion', 'spacefill']],
  surface: [['polymer', 'surface'], ['ligand', 'ball-and-stick'], ['branched', 'ball-and-stick'], ['ion', 'ball-and-stick']],
  'cartoon-surface': [['polymer', 'cartoon'], ['polymer', 'surface'], ['ligand', 'ball-and-stick'], ['branched', 'ball-and-stick'], ['ion', 'ball-and-stick']],
};
export const hasSurface = (display) => display === 'surface' || display === 'cartoon-surface';
const GAUSSIAN_SURFACE_ATOMS = 60000;

// Local resolution colour scale, best (blue) to worst (red): the same stops as the job thumbnails (imaging.py).
export const RESOLUTION_COLORS = [0x2b4ba0, 0x2f8fd8, 0x3cc0c0, 0x7dd36b, 0xf2e04a, 0xf39c34, 0xd7352b];

const SUBSTANCE = { metalness: 0, roughness: 1, bumpiness: 0 };

let plugin = null;
let lib = null;
let softOcclusion = null;

const defaultsOf = (params) => Object.fromEntries(Object.entries(params)
  .filter(([, p]) => !p.isOptional).map(([k, p]) => [k, p.defaultValue]));
const cellData = (ref) => plugin.state.data.cells.get(ref)?.obj?.data;
const hasCell = (ref) => !!ref && plugin.state.data.cells.has(ref);

export async function createScene(element) {
  const viewer = await window.molstar.Viewer.create(element, VIEWER_OPTIONS);
  plugin = viewer.plugin;
  lib = window.molstar.lib;
  const occlusion = plugin.canvas3d.props.postprocessing.occlusion;
  softOcclusion = occlusion.name === 'on' ? occlusion : null;
  canvasProps('postprocessing', { occlusion: { name: 'off', params: {} } });
  // No automatic camera resets when the scene changes: Mol* frames the bounding sphere of what is drawn, which for an
  // isosurface computed on the GPU is the whole box of the map (far too wide around a small molecule). CryoPlug
  // frames the view itself (on opening, R, view).
  canvasProps('camera', { helper: { ...plugin.canvas3d.props.camera.helper, axes: { name: 'off', params: {} } }, manualReset: true });
  // Mol* flies the camera with W/A/S/D/R/F and the arrows, listening on the whole window: the viewer's
  // own shortcuts (R reset, S spin, M style, + / −) replace them.
  const bindings = { ...plugin.canvas3d.attribs.trackball.bindings };
  for (const key of Object.keys(bindings)) {
    if (/^(key|boost|enablePointerLock)/.test(key)) bindings[key] = { triggers: [], action: '', description: '' };
  }
  plugin.canvas3d.setAttribs({ trackball: { bindings } });
  return viewer;
}

// ------------------------------------------------------------------- maps
// A map from a URL or from the bytes of an MRC file (see mapdata.js). The entry id keeps Mol* from looking the label
// up in the EMDB (it does for names starting with "emd").
export async function loadVolume(source, label) {
  const data = typeof source === 'string'
    ? await plugin.builders.data.download({ url: source, isBinary: true, label }, { state: { isGhost: true } })
    : await plugin.builders.data.rawData({ data: source, label }, { state: { isGhost: true } });
  try {
    const parsed = await plugin.dataFormats.get('ccp4').parse(plugin, data, { entryId: `cryoplug ${label}` });
    const volume = parsed.volume || parsed.volumes?.[0];
    if (!volume?.isOk) throw new Error('not a readable MRC/CCP4 map');
    releaseBytes(data.ref);
    return { dataRef: data.ref, volumeRef: volume.ref, volume: volume.cell.obj.data };
  } catch (err) {
    await remove(data.ref);
    throw err;
  }
}

// The file bytes are no longer needed once the map is parsed (its values live in their own array): drop them, which
// halves the memory of a map, a lot for the volumes of a series. The cell is never updated again, so nothing re-reads it.
function releaseBytes(ref) {
  const cell = plugin.state.data.cells.get(ref);
  try {
    if (cell?.obj?.data instanceof Uint8Array) cell.obj.data = new Uint8Array(0);
    const params = cell?.transform?.params;
    if (params && (params.data instanceof Uint8Array || params.data instanceof ArrayBuffer)) params.data = new Uint8Array(0);
  } catch { /* read-only in this Mol* version: keep them */ }
}

// Single colour, or the values of another map (local resolution) sampled at the surface.
function mapColorTheme(volume, s) {
  const registry = plugin.representation.volume.themes.colorThemeRegistry;
  const by = s.colorBy;
  if (by && hasCell(by.refs.volume)) {
    const theme = registry.get('external-volume');
    return {
      name: 'external-volume',
      params: {
        ...defaultsOf(theme.getParams({ volume })),
        volume: { ref: hasCell(by.refs.xform) ? by.refs.xform : by.refs.volume }, // moved with the map it colours
        coloring: { name: 'absolute-value', params: { domain: { name: 'custom', params: [by.min, by.max] }, list: { kind: 'interpolate', colors: RESOLUTION_COLORS } } },
        defaultColor: s.color,
      },
    };
  }
  return { name: 'uniform', params: { ...defaultsOf(registry.get('uniform').getParams({ volume })), value: s.color } };
}

function isosurfaceParams(volume, s) {
  const { registry, themes } = plugin.representation.volume;
  const repr = registry.get('isosurface');
  const size = themes.sizeThemeRegistry.get(repr.defaultSizeTheme.name);
  return {
    type: {
      name: 'isosurface',
      params: {
        ...defaultsOf(repr.getParams(themes, volume)),
        isoValue: { kind: 'absolute', absoluteValue: s.level },
        visuals: [s.style === 'mesh' ? 'wireframe' : 'solid'],
        alpha: s.style === 'transparent' ? s.opacity : 1,
        sizeFactor: 1.5,
        tryUseGpu: !s.colorBy, // colouring by another map samples the vertices on the CPU
        // where the view cuts the surface (slab, zoom, Shift + wheel), the inside is drawn filled, a shade darker
        solidInterior: true,
        interior: { color: 0x000000, colorStrength: 0.3, substance: SUBSTANCE, substanceStrength: 0 },
      },
    },
    colorTheme: mapColorTheme(volume, s),
    sizeTheme: { name: size.name, params: { ...defaultsOf(size.getParams({ volume })), ...repr.defaultSizeTheme.props } },
  };
}

// Statistics of a map used for colouring: 5th-95th percentile of its values between 0 and 100 (Å).
export function colorRange(volume) {
  const data = volume.grid.cells.data;
  const step = Math.max(1, Math.floor(data.length / 400000));
  const values = [];
  for (let i = 0; i < data.length; i += step) {
    const v = data[i];
    if (v > 0 && v < 100) values.push(v);
  }
  if (!values.length) return [0, 1];
  values.sort((a, b) => a - b);
  const at = (q) => values[Math.min(values.length - 1, Math.floor(q * values.length))];
  const lo = Math.round(at(0.05) * 10) / 10;
  const hi = Math.round(at(0.95) * 10) / 10;
  return [lo, hi > lo ? hi : lo + 0.1];
}

// Create or update the isosurface of a map item ({ volume, level, style, opacity, color, refs }). Calls for the same
// map run one after the other: two at once would both see no isosurface yet and create two.
const showing = new WeakMap(); // refs of a map (or of a frame of a series) -> its last update
export function showMap(item) {
  const refs = item.refs;
  const next = (showing.get(refs) || Promise.resolve()).catch(() => {}).then(() => showMapNow(item));
  showing.set(refs, next);
  return next;
}

async function showMapNow(item) {
  const moved = !isIdentity(item.transform);
  if (moved && item.colorBy && !hasCell(item.colorBy.refs.xform)) await placeVolume(item.colorBy, item.transform);
  const params = isosurfaceParams(item.volume, item);
  if (hasCell(item.refs.repr)) {
    await plugin.build().to(item.refs.repr).update(params).commit();
  } else {
    // a map moved in space: its isosurface hangs below the transform of its data (a new copy of a map gets it again)
    const b = plugin.build();
    let parent = b.to(item.refs.volume);
    if (moved) {
      parent = parent.apply(lib.plugin.StateTransforms.Volume.VolumeTransform, transformParams(item.transform));
      item.refs.xform = parent.ref;
    }
    item.refs.repr = parent.apply(lib.plugin.StateTransforms.Representation.VolumeRepresentation3D, params).ref;
    await b.commit();
  }
  if (!item.visible) setHidden(item.refs.repr, true);
}

// Latest-wins update queue per item: dragging a threshold or a colour picker sends many requests,
// only the last state matters.
const queues = new WeakMap();
export function schedule(item, onError, kind = 'show') {
  let all = queues.get(item);
  if (!all) queues.set(item, all = {});
  const q = all[kind] ||= { running: false, dirty: false };
  if (q.running) { q.dirty = true; return; }
  q.running = true;
  const task = kind === 'alpha' ? () => setSurfaceAlpha(item) : () => (item.kind === 'map' ? showMap(item) : showModel(item));
  (async () => {
    do {
      q.dirty = false;
      try { await task(); } catch (err) { onError?.(err); }
    } while (q.dirty);
    q.running = false;
  })();
}

// ----------------------------------------------------------------- models
export async function loadModel(url, label, format) {
  const S = plugin.builders.structure;
  const data = await plugin.builders.data.download({ url, isBinary: false, label }, { state: { isGhost: true } });
  try {
    const trajectory = await S.parseTrajectory(data, format);
    const model = await S.createModel(trajectory);
    const structure = await S.createStructure(model);
    const components = {};
    for (const t of ['polymer', 'ligand', 'branched', 'ion', 'water']) components[t] = await S.tryCreateComponentStatic(structure, t);
    try {
      components.sidechain = await S.tryCreateComponent(structure, {
        type: { name: 'script', params: { language: 'pymol', expression: 'sidechain or (polymer and name CA)' } },
        nullIfEmpty: true,
        label: 'Side chains',
      }, 'cryoplug-sidechains');
    } catch {
      components.sidechain = undefined; // 'both' then falls back to whole-polymer sticks
    }
    return { dataRef: data.ref, structureRef: structure.ref, components, structure: structure.cell.obj.data };
  } catch (err) {
    await remove(data.ref);
    throw err;
  }
}

const SURFACES = ['molecular-surface', 'gaussian-surface'];

function modelColor(item, type) {
  // cartoons and surfaces take the colour of the scheme; atoms (sticks, spheres) keep the element colours of
  // their heteroatoms, the carbons following the scheme
  const whole = type === 'cartoon' || SURFACES.includes(type);
  const uniform = { value: item.color, saturation: 0, lightness: 0 };
  switch (item.colorMode) {
    case 'ss': return whole ? { color: 'secondary-structure' } : { color: 'element-symbol' };
    case 'element': return { color: 'element-symbol' };
    case 'bfactor': return { color: 'uncertainty' };
    case 'rainbow': return { color: 'sequence-id' };
    case 'hydrophobicity': return { color: 'hydrophobicity' };
    case 'uniform':
      return whole ? { color: 'uniform', colorParams: uniform }
        : { color: 'element-symbol', colorParams: { carbonColor: { name: 'uniform', params: uniform } } };
    default: return whole ? { color: 'chain-id' } : { color: 'element-symbol' };
  }
}

function modelTypeParams(item, type) {
  if (type === 'ball-and-stick') return { sizeFactor: item.display === 'sticks' ? 0.17 : 0.2 };
  if (SURFACES.includes(type)) {
    const alpha = item.display === 'cartoon-surface' ? item.surfaceOpacity ?? 0.35 : item.surfaceOpacity ?? 1;
    return { quality: 'auto', alpha, solidInterior: true, ...(type === 'molecular-surface' ? { probeRadius: 1.4 } : {}) };
  }
  return {};
}

// (Re)build the representations of a model item ({ display, colorMode, color, waters, surfaceOpacity, refs }).
export async function showModel(item) {
  await plugin.dataTransaction(async () => {
    const old = (item.refs.reprs || []).filter(hasCell);
    if (old.length) {
      const b = plugin.build();
      old.forEach((r) => b.delete(r));
      await b.commit();
    }
    item.refs.reprs = [];
    item.refs.surfaces = [];
    const surface = (item.structure?.elementCount || 0) > GAUSSIAN_SURFACE_ATOMS ? 'gaussian-surface' : 'molecular-surface';
    const plan = DISPLAY_PLAN[item.display || 'cartoon']
      .map((step) => (step[0] === 'sidechain' && !item.refs.components.sidechain ? ['polymer', 'ball-and-stick'] : step))
      .map(([key, type]) => [key, type === 'surface' ? surface : type]);
    if (item.waters) plan.push(['water', item.display === 'spheres' ? 'spacefill' : 'ball-and-stick']);
    for (const [key, type] of plan) {
      const component = item.refs.components[key];
      if (!component) continue;
      const repr = await plugin.builders.structure.representation.addRepresentation(component, {
        type,
        typeParams: modelTypeParams(item, type),
        ...modelColor(item, type),
      });
      item.refs.reprs.push(repr.ref);
      if (SURFACES.includes(type)) item.refs.surfaces.push(repr.ref);
    }
  });
  if (!item.visible) setHidden(item.refs.structure, true);
}

// Opacity of the molecular surface of a model, without computing the surface again; false when it has none.
export async function setSurfaceAlpha(item) {
  const refs = (item.refs.surfaces || []).filter(hasCell);
  if (!refs.length) return false;
  const alpha = modelTypeParams(item, 'molecular-surface').alpha;
  const b = plugin.build();
  for (const ref of refs) b.to(ref).update((old) => ({ ...old, type: { ...old.type, params: { ...old.type.params, alpha } } }));
  await b.commit();
  return true;
}

// ---------------------------------------------------------- measurements
export function atomDistance(a, b) {
  const { StructureElement: SE, StructureProperties: SP } = lib.structure;
  const la = SE.Loci.getFirstLocation(a);
  const lb = SE.Loci.getFirstLocation(b);
  if (!la || !lb) return NaN;
  return Math.hypot(SP.atom.x(la) - SP.atom.x(lb), SP.atom.y(la) - SP.atom.y(lb), SP.atom.z(la) - SP.atom.z(lb));
}

// A dashed line between two atoms with its length (a Mol* measurement, removed with its model).
export async function addDistance(a, b) {
  return !!(await plugin.managers.structure.measurement.addDistance(a, b));
}

export async function clearDistances() {
  const cells = plugin.managers.structure.measurement.state.distances || [];
  if (!cells.length) return 0;
  const b = plugin.build();
  cells.forEach((c) => b.delete(c.transform.ref));
  await b.commit();
  return cells.length;
}

export function residueLoci(item, chain, resno) {
  const structure = shownStructure(item);
  if (!structure) return null;
  const { Queries, StructureSelection, StructureProperties: SP, QueryContext } = lib.structure;
  const query = Queries.generators.residues({
    chainTest: chain ? (ctx) => SP.chain.auth_asym_id(ctx.element) === chain : undefined,
    residueTest: Number.isFinite(resno) ? (ctx) => SP.residue.auth_seq_id(ctx.element) === resno : undefined,
  });
  const loci = StructureSelection.toLociWithSourceUnits(query(new QueryContext(structure)));
  return lib.loci.Loci.isEmpty(loci) ? null : loci;
}

export function wholeResidue(loci) {
  return lib.structure.StructureElement.Loci.extendToWholeResidues(loci);
}

// --------------------------------------------------------------- generic
export function setHidden(ref, hidden) {
  const state = plugin.state.data;
  if (!state.cells.has(ref)) return;
  let resync = false;
  const walk = (r) => {
    state.updateCellState(r, { isHidden: hidden });
    // An update that rebuilds a representation (an isosurface switching between GPU and CPU, e.g. when it gets
    // coloured by another map) keeps the hidden flag of its cell but brings the rebuilt parts back visible.
    const repr = state.cells.get(r)?.obj?.data?.repr;
    if (repr?.renderObjects?.some((ro) => ro.state.visible === hidden)) {
      repr.setState({ visible: !hidden });
      resync = true;
    }
    state.tree.children.get(r).forEach(walk);
  };
  walk(ref);
  if (resync) plugin.canvas3d?.syncVisibility();
}

export async function remove(ref) {
  if (hasCell(ref)) await plugin.build().delete(ref).commit();
}

// What is under the mouse: an atom of a model or a map surface.
export function describeLoci(loci) {
  if (!loci) return null;
  if (loci.kind === 'element-loci') {
    const { StructureElement: SE, StructureProperties: SP } = lib.structure;
    const l = SE.Loci.getFirstLocation(loci);
    if (!l) return null;
    try {
      return {
        kind: 'atom', root: loci.structure.root, loci,
        chain: SP.chain.auth_asym_id(l), resn: SP.atom.label_comp_id(l), resi: SP.residue.auth_seq_id(l),
        ins: SP.residue.pdbx_PDB_ins_code(l) || '', atom: SP.atom.label_atom_id(l), element: SP.atom.type_symbol(l),
        b: SP.atom.B_iso_or_equiv(l),
      };
    } catch {
      return { kind: 'atom', root: loci.structure.root, loci };
    }
  }
  if (loci.volume) return { kind: 'volume', volume: loci.volume };
  return null;
}

// --------------------------------------------------------------- positions
// Items moved in space (ChimeraX's move, turn and fitmap): a transform of the data under the representations, so that
// picking, distances, centring and colouring follow. Maps: Mol*'s VolumeTransform above their isosurface (also above
// the map colouring them); models: TransformStructureConformation above their components (only the instance
// matrices of the drawn geometry change, nothing is recomputed). `m`: 16 numbers row by row, or null.
const transformParams = (m) => ({ transform: { name: 'matrix', params: { data: columnMajor(m || [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]), transpose: false } } });

async function placeVolume(holder, m) {
  if (hasCell(holder.refs.xform)) {
    await plugin.build().to(holder.refs.xform).update(transformParams(m)).commit();
    return;
  }
  const update = plugin.build().to(holder.refs.volume).apply(lib.plugin.StateTransforms.Volume.VolumeTransform, transformParams(m));
  holder.refs.xform = update.ref;
  await update.commit();
}

export async function setTransform(item, m) {
  item.transform = isIdentity(m) ? null : [...m];
  if (item.kind === 'map') {
    if (item.colorBy) await placeVolume(item.colorBy, item.transform);
    if (hasCell(item.refs.volume)) await placeVolume(item, item.transform);
    if (item.colorBy) await showMap(item); // the colour theme then reads the moved colouring map
    return;
  }
  if (hasCell(item.refs.xform)) {
    await plugin.build().to(item.refs.xform).update(transformParams(item.transform)).commit();
  } else if (hasCell(item.refs.structure)) {
    const update = plugin.build().to(item.refs.structure).apply(lib.plugin.StateTransforms.Model.TransformStructureConformation, transformParams(item.transform));
    item.refs.xform = update.ref;
    await update.commit();
  }
}

// The structure drawn (moved or not) and the volume drawn: what picking returns for this item.
export const shownStructure = (item) => (hasCell(item.refs?.xform) ? cellData(item.refs.xform) : cellData(item.refs?.structure)) || item.structure;
export const shownVolume = (item) => (hasCell(item.refs?.xform) ? cellData(item.refs.xform) : item.volume);

// Centre (Å) of an item as it is drawn: the centre of its atoms, or of the voxels above its level.
export function itemCenter(item) {
  const sphere = sphereOf(item);
  return sphere ? [...sphere.center] : null;
}

// Mouse drags move items instead of the camera (on: true), or the camera again.
let cameraBindings = null;
export function setCameraDrag(on) {
  const trackball = plugin.canvas3d.attribs.trackball;
  if (!on) {
    cameraBindings ||= { ...trackball.bindings };
    const off = { ...trackball.bindings };
    for (const key of ['dragRotate', 'dragRotateZ', 'dragPan', 'dragZoom', 'dragFocus', 'dragFocusZoom']) off[key] = { triggers: [], action: '', description: '' };
    plugin.canvas3d.setAttribs({ trackball: { bindings: off } });
  } else if (cameraBindings) {
    plugin.canvas3d.setAttribs({ trackball: { bindings: cameraBindings } });
    cameraBindings = null;
  }
}

// The axes of the screen in the scene (right, up, towards the viewer) and the size of a pixel (Å) at a depth.
export function screenFrame(at) {
  const c = plugin.canvas3d.camera.getSnapshot();
  const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
  const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  const norm = (v) => { const n = Math.hypot(...v) || 1; return v.map((x) => x / n); };
  const dir = norm(sub([...c.target], [...c.position])); // into the screen
  const right = norm(cross(dir, [...c.up]));
  const up = cross(right, dir);
  const depth = at ? Math.max(1, (at[0] - c.position[0]) * dir[0] + (at[1] - c.position[1]) * dir[1] + (at[2] - c.position[2]) * dir[2])
    : Math.hypot(...sub([...c.target], [...c.position]));
  const height = canvas().clientHeight || 600;
  return { right, up, toward: dir.map((v) => -v), pixel: (2 * depth * Math.tan(c.fov / 2)) / height };
}

export function onHover(fn) {
  return plugin.behaviors.interaction.hover.subscribe((e) => fn(describeLoci(e.current.loci)));
}

export function onClick(fn) {
  return plugin.behaviors.interaction.click.subscribe((e) => fn(describeLoci(e.current.loci), e));
}

// ---------------------------------------------------------------- camera
export function resetView({ whenReady = false } = {}) {
  // Right after loading, the geometry is still being built: let Mol* reset once it is drawn.
  if (whenReady) plugin.canvas3d.requestCameraReset();
  else plugin.managers.camera.reset();
}

// Coot-like recentring: move the rotation centre to the loci without changing the zoom.
export function centerOn(loci, { zoom = false } = {}) {
  if (!loci) return;
  if (zoom) {
    plugin.managers.camera.focusLoci(loci, { minRadius: 7, extraRadius: 4, durationMs: 300 });
    return;
  }
  const sphere = lib.loci.Loci.getBoundingSphere(loci);
  if (!sphere) return;
  const V = lib.math.LinearAlgebra.Vec3;
  const camera = plugin.canvas3d.camera;
  const snap = camera.getSnapshot();
  const shift = V.sub(V(), sphere.center, snap.target);
  camera.setState({ target: V.clone(sphere.center), position: V.add(V(), snap.position, shift) }, 300);
}

// Grid indices (i along x) to Cartesian coordinates, as Mol* computes it.
function gridToCartesian(grid) {
  const { Mat4: M, Vec3: V } = lib.math.LinearAlgebra;
  const t = grid.transform;
  if (t.kind === 'matrix') return t.matrix;
  if (t.kind !== 'spacegroup') return M.identity();
  const dims = grid.cells.space.dimensions;
  const size = V.sub(V(), t.fractionalBox.max, t.fractionalBox.min);
  const scale = M.fromScaling(M(), V.create(size[0] / dims[0], size[1] / dims[1], size[2] / dims[2]));
  return M.mul(M(), M.mul(M(), t.cell.fromFractional, M.fromTranslation(M(), t.fractionalBox.min)), scale);
}

// Voxel (i, j, k) of a map to Cartesian Å as Mol* places it. EM maps are axis-aligned: a scale and an offset per axis
// (x = offset[0] + scale[0] * i, ...).
export function voxelAxes(volume) {
  const T = gridToCartesian(volume.grid);
  return { scale: [T[0], T[5], T[10]], offset: [T[12], T[13], T[14]] };
}

// Sphere around the voxels above the contour level: isosurfaces computed on the GPU only know their box,
// which would frame a small molecule in a large box from far away. Cached per volume and level.
const spheres = new WeakMap();
function isoSphere(item) {
  const cached = spheres.get(item.volume);
  if (cached && cached.level === item.level) return cached.sphere;
  const sphere = computeIsoSphere(item);
  spheres.set(item.volume, { level: item.level, sphere });
  return sphere;
}

function computeIsoSphere(item) {
  const V = lib.math.LinearAlgebra.Vec3;
  const grid = item.volume.grid;
  const { data, space } = grid.cells;
  const dims = space.dimensions;
  const hist = dims.map((d) => new Float64Array(d));
  const step = Math.max(1, Math.round(Math.cbrt((dims[0] * dims[1] * dims[2]) / 2e6)));
  let count = 0;
  for (let k = 0; k < dims[2]; k += step) {
    for (let j = 0; j < dims[1]; j += step) {
      for (let i = 0; i < dims[0]; i += step) {
        if (space.get(data, i, j, k) >= item.level) { hist[0][i] += 1; hist[1][j] += 1; hist[2][k] += 1; count += 1; }
      }
    }
  }
  if (!count) return null;
  const bounds = hist.map((hh) => {
    let lo = 0;
    let hi = hh.length - 1;
    while (lo < hi && !hh[lo]) lo++;
    while (hi > lo && !hh[hi]) hi--;
    return [Math.max(0, lo - step), Math.min(hh.length - 1, hi + step)];
  });
  const T = gridToCartesian(grid);
  const corners = [];
  for (const a of bounds[0]) for (const b of bounds[1]) for (const c of bounds[2]) corners.push(V.transformMat4(V(), V.create(a, b, c), T));
  const center = V.scale(V(), corners.reduce((acc, q) => V.add(acc, acc, q), V()), 1 / corners.length);
  const radius = Math.max(...corners.map((q) => V.distance(q, center)));
  return radius > 0 ? { center, radius } : null;
}

function union(spheres) {
  const V = lib.math.LinearAlgebra.Vec3;
  let c = V.clone(spheres[0].center);
  let r = spheres[0].radius;
  for (const s of spheres.slice(1)) {
    const d = V.distance(c, s.center);
    if (d + s.radius <= r) continue;
    if (d + r <= s.radius) { c = V.clone(s.center); r = s.radius; continue; }
    const nr = (d + r + s.radius) / 2;
    c = V.add(V(), c, V.scale(V(), V.sub(V(), s.center, c), (nr - r) / d));
    r = nr;
  }
  return { center: c, radius: r };
}

function sphereOf(item) {
  if (item.kind !== 'map') {
    const structure = shownStructure(item);
    return structure && lib.loci.Loci.getBoundingSphere(lib.structure.Structure.toStructureElementLoci(structure));
  }
  const sphere = item.volume && isoSphere(item);
  if (!sphere || isIdentity(item.transform)) return sphere;
  return { center: applyRigid(item.transform, [...sphere.center]), radius: sphere.radius }; // a moved map
}

// Sphere around what is shown: the models and the isosurfaces of the maps.
export function contentSphere(items) {
  const found = items.filter((i) => i.visible).map(sphereOf).filter((sp) => sp && sp.radius > 0);
  return found.length ? union(found) : null;
}

// ------------------------------------------------------------------ slab
// Coot-like slab: only what lies within ±thickness/2 of the centre of rotation, along the view, is drawn (the cut
// density filled). Mol* clips at the camera distance ± the radius of its focus, which the slab sets; framing or
// zooming on something resets that radius, hence reapplySlab.
let slab = null; // thickness in Å, or null

export function setSlab(thickness, items = []) {
  slab = thickness;
  const camera = plugin.canvas3d.camera;
  if (thickness !== null) {
    camera.setState({ radius: Math.max(0.5, thickness / 2) });
    return;
  }
  const sphere = contentSphere(items);
  const need = sphere ? lib.math.LinearAlgebra.Vec3.distance(camera.state.target, sphere.center) + sphere.radius + 2 : camera.state.radiusMax;
  camera.setState({ radius: Math.max(1, need), radiusMax: Math.max(camera.state.radiusMax, need) });
}

export function reapplySlab(delayMs = 0) {
  if (slab === null) return;
  setTimeout(() => { if (slab !== null) plugin.canvas3d.camera.setState({ radius: Math.max(0.5, slab / 2) }); }, delayMs);
}

// Frame the isosurfaces of these maps; false when none is above its level.
export function frameMaps(items, durationMs = 250) {
  const found = items.filter((i) => i.kind === 'map' && i.visible && i.volume).map(sphereOf).filter(Boolean);
  if (!found.length) return false;
  const sphere = union(found);
  plugin.managers.camera.focusSphere(sphere, { durationMs, extraRadius: 2 + 0.1 * sphere.radius });
  return true;
}

// The clipping planes and the fog follow the radius of the camera focus: widen it (without moving the camera)
// when an item, e.g. another volume of a series or a map opened later, reaches beyond it.
export function keepInView(item) {
  if (slab !== null) return; // the slab decides what is clipped
  const sphere = sphereOf(item);
  if (!sphere || !(sphere.radius > 0)) return;
  const camera = plugin.canvas3d.camera;
  const need = lib.math.LinearAlgebra.Vec3.distance(camera.state.target, sphere.center) + sphere.radius + 2;
  if (need > camera.state.radius) camera.setState({ radius: need, radiusMax: Math.max(camera.state.radiusMax, need) });
}

export function focusItem(item) {
  if (item.kind === 'model') {
    const structure = shownStructure(item);
    if (structure) plugin.managers.camera.focusLoci(lib.structure.Structure.toStructureElementLoci(structure), { durationMs: 300 });
    return;
  }
  if (frameMaps([{ ...item, visible: true }], 300)) return;
  const repr = plugin.state.data.cells.get(item.refs.repr)?.obj?.data?.repr;
  if (repr) plugin.managers.camera.focusRenderObjects(repr.renderObjects, { durationMs: 300 });
}

export function select(loci) {
  const selects = plugin.managers.interactivity.lociSelects;
  if (loci) selects.selectOnly({ loci });
  else selects.deselectAll();
}

// ---------------------------------------------------------------- canvas
function canvasProps(key, value) {
  const c = plugin.canvas3d;
  c.setProps({ [key]: { ...c.props[key], ...value } });
}

export function setBackground(color) { canvasProps('renderer', { backgroundColor: color }); }
export function setProjection(mode) { canvasProps('camera', { mode }); }

export function setSpin(on, speed = 0.12) {
  const V = lib.math.LinearAlgebra.Vec3;
  plugin.canvas3d.setProps({ trackball: { animate: on ? { name: 'spin', params: { speed, axis: V.create(0, -1, 0) } } : { name: 'off', params: {} } } });
}

export function setSoftLighting(on) {
  if (on && !softOcclusion) return false;
  canvasProps('postprocessing', { occlusion: on ? softOcclusion : { name: 'off', params: {} } });
  return true;
}

// The 3D view changed size (split with the slices): Mol* resizes its drawing buffer.
export function resize() { plugin.handleResize?.(); }

export const canvas = () => plugin.canvas3d.webgl.gl.canvas;

// ---------------------------------------------------------------- camera
export function onCamera(fn) {
  return plugin.canvas3d.camera.stateChanged.subscribe(() => fn());
}

const round = (v, d = 3) => Math.round(v * 10 ** d) / 10 ** d;
export function cameraState() {
  const c = plugin.canvas3d.camera.getSnapshot();
  return { position: [...c.position].map((v) => round(v)), target: [...c.target].map((v) => round(v)), up: [...c.up].map((v) => round(v, 5)),
    radius: round(c.radius), radiusMax: round(c.radiusMax), fov: round(c.fov, 5), mode: c.mode };
}

export function setCameraState(v, durationMs = 0) {
  if (!v || !Array.isArray(v.position) || !Array.isArray(v.target)) return false;
  const V = lib.math.LinearAlgebra.Vec3;
  const state = { position: V.create(...v.position), target: V.create(...v.target) };
  if (Array.isArray(v.up)) state.up = V.create(...v.up);
  for (const k of ['radius', 'radiusMax', 'fov']) if (Number.isFinite(v[k])) state[k] = v[k];
  plugin.canvas3d.camera.setState(state, durationMs);
  return true;
}

// ----------------------------------------------------------------- images
function screenshotHelper(scale, transparent) {
  const helper = plugin.helpers.viewportScreenshot;
  const c = canvas();
  const width = Math.round(c.clientWidth * scale);
  const height = Math.round(c.clientHeight * scale);
  helper.behaviors.values.next({ ...helper.values, transparent, resolution: { name: 'custom', params: { width, height } } });
  return { helper, width, height };
}

// PNG of the view at `scale` × the window size; overlay(ctx, width, height, scale) draws on top of it (a colour
// scale), in which case the image is composed here instead of by Mol*.
export async function saveImage(filename, { scale = 2, transparent = false, overlay = null } = {}) {
  const { helper, width, height } = screenshotHelper(scale, transparent);
  if (!overlay) {
    await helper.download(filename);
    return { width, height };
  }
  const uri = await helper.getImageDataUri();
  const img = new Image();
  await new Promise((resolve, reject) => { img.onload = resolve; img.onerror = () => reject(new Error('Could not compose the image')); img.src = uri; });
  const out = document.createElement('canvas');
  out.width = img.width;
  out.height = img.height;
  const ctx = out.getContext('2d');
  ctx.drawImage(img, 0, 0);
  overlay(ctx, out.width, out.height, out.width / Math.max(1, canvas().clientWidth));
  const blob = await new Promise((resolve) => out.toBlob(resolve, 'image/png'));
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  return { width: out.width, height: out.height };
}
