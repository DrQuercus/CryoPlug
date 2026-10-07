// Mol* adapter: the rendering engine behind the CryoPlug viewer. Every Mol*-specific call lives here,
// so the interface (models panel, command line, slices) only handles plain item objects.

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

// Atoms drawn for each model display style: [component, Mol* representation].
const DISPLAY_PLAN = {
  cartoon: [['polymer', 'cartoon'], ['ligand', 'ball-and-stick'], ['branched', 'ball-and-stick'], ['ion', 'ball-and-stick']],
  sticks: [['polymer', 'ball-and-stick'], ['ligand', 'ball-and-stick'], ['branched', 'ball-and-stick'], ['ion', 'ball-and-stick']],
  both: [['polymer', 'cartoon'], ['sidechain', 'ball-and-stick'], ['ligand', 'ball-and-stick'], ['branched', 'ball-and-stick'], ['ion', 'ball-and-stick']],
};

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
  canvasProps('camera', { helper: { ...plugin.canvas3d.props.camera.helper, axes: { name: 'off', params: {} } } });
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
export async function loadVolume(url, label) {
  const data = await plugin.builders.data.download({ url, isBinary: true, label }, { state: { isGhost: true } });
  try {
    const parsed = await plugin.dataFormats.get('ccp4').parse(plugin, data, { entryId: label });
    const volume = parsed.volume || parsed.volumes?.[0];
    if (!volume?.isOk) throw new Error('not a readable MRC/CCP4 map');
    return { dataRef: data.ref, volumeRef: volume.ref, volume: volume.cell.obj.data };
  } catch (err) {
    await remove(data.ref);
    throw err;
  }
}

function isosurfaceParams(volume, s) {
  const { registry, themes } = plugin.representation.volume;
  const repr = registry.get('isosurface');
  const color = themes.colorThemeRegistry.get('uniform');
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
      },
    },
    colorTheme: { name: 'uniform', params: { ...defaultsOf(color.getParams({ volume })), value: s.color } },
    sizeTheme: { name: size.name, params: { ...defaultsOf(size.getParams({ volume })), ...repr.defaultSizeTheme.props } },
  };
}

// Create or update the isosurface of a map item ({ volume, level, style, opacity, color, refs }).
export async function showMap(item) {
  const params = isosurfaceParams(item.volume, item);
  if (hasCell(item.refs.repr)) {
    await plugin.build().to(item.refs.repr).update(params).commit();
  } else {
    const update = plugin.build().to(item.refs.volume).apply(lib.plugin.StateTransforms.Representation.VolumeRepresentation3D, params);
    item.refs.repr = update.ref;
    await update.commit();
  }
  if (!item.visible) setHidden(item.refs.repr, true);
}

// Latest-wins update queue per item: dragging a threshold or a colour picker sends many requests,
// only the last state matters.
const queues = new WeakMap();
export function schedule(item, onError) {
  let q = queues.get(item);
  if (!q) queues.set(item, q = { running: false, dirty: false });
  if (q.running) { q.dirty = true; return; }
  q.running = true;
  (async () => {
    do {
      q.dirty = false;
      try { await (item.kind === 'map' ? showMap(item) : showModel(item)); } catch (err) { onError?.(err); }
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

function modelColor(item, type) {
  const cartoon = type === 'cartoon';
  const uniform = { value: item.color, saturation: 0, lightness: 0 };
  switch (item.colorMode) {
    case 'ss': return cartoon ? { color: 'secondary-structure' } : { color: 'element-symbol' };
    case 'element': return { color: 'element-symbol' };
    case 'bfactor': return { color: 'uncertainty' };
    case 'rainbow': return { color: 'sequence-id' };
    case 'uniform':
      return cartoon ? { color: 'uniform', colorParams: uniform }
        : { color: 'element-symbol', colorParams: { carbonColor: { name: 'uniform', params: uniform } } };
    default: return cartoon ? { color: 'chain-id' } : { color: 'element-symbol' };
  }
}

// (Re)build the representations of a model item ({ display, colorMode, color, waters, refs }).
export async function showModel(item) {
  await plugin.dataTransaction(async () => {
    const old = (item.refs.reprs || []).filter(hasCell);
    if (old.length) {
      const b = plugin.build();
      old.forEach((r) => b.delete(r));
      await b.commit();
    }
    item.refs.reprs = [];
    const plan = DISPLAY_PLAN[item.display].map((step) => (step[0] === 'sidechain' && !item.refs.components.sidechain ? ['polymer', 'ball-and-stick'] : step));
    if (item.waters) plan.push(['water', 'ball-and-stick']);
    for (const [key, type] of plan) {
      const component = item.refs.components[key];
      if (!component) continue;
      const repr = await plugin.builders.structure.representation.addRepresentation(component, {
        type,
        typeParams: type === 'ball-and-stick' ? { sizeFactor: item.display === 'sticks' ? 0.17 : 0.2 } : {},
        ...modelColor(item, type),
      });
      item.refs.reprs.push(repr.ref);
    }
  });
  if (!item.visible) setHidden(item.refs.structure, true);
}

export function residueLoci(item, chain, resno) {
  const structure = cellData(item.refs.structure);
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
  const walk = (r) => {
    state.updateCellState(r, { isHidden: hidden });
    state.tree.children.get(r).forEach(walk);
  };
  walk(ref);
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

export function focusItem(item) {
  if (item.kind === 'model') {
    const structure = cellData(item.refs.structure);
    if (structure) plugin.managers.camera.focusLoci(lib.structure.Structure.toStructureElementLoci(structure), { durationMs: 300 });
    return;
  }
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
export function setSlab(percent) { canvasProps('cameraClipping', { radius: percent === null ? 100 : Math.max(1, Math.min(99, percent)), far: true }); }

export function setSpin(on) {
  const V = lib.math.LinearAlgebra.Vec3;
  plugin.canvas3d.setProps({ trackball: { animate: on ? { name: 'spin', params: { speed: 0.12, axis: V.create(0, -1, 0) } } : { name: 'off', params: {} } } });
}

export function setSoftLighting(on) {
  if (on && !softOcclusion) return false;
  canvasProps('postprocessing', { occlusion: on ? softOcclusion : { name: 'off', params: {} } });
  return true;
}

export async function saveImage(filename, { scale = 2, transparent = false } = {}) {
  const helper = plugin.helpers.viewportScreenshot;
  const canvas = plugin.canvas3d.webgl.gl.canvas;
  const width = Math.round(canvas.clientWidth * scale);
  const height = Math.round(canvas.clientHeight * scale);
  helper.behaviors.values.next({ ...helper.values, transparent, resolution: { name: 'custom', params: { width, height } } });
  await helper.download(filename);
  return { width, height };
}
