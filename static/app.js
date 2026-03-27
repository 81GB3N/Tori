import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

const loader = new THREE.ObjectLoader();
const gltfLoader = new GLTFLoader();
const comparePriority = { modified: 0, added: 1, removed: 2, unchanged: 3 };
const uploadEndpoint = "/api/models/import";

const els = {
  baseFile: document.querySelector("#base-file"),
  revisionFile: document.querySelector("#revision-file"),
  loadDemo: document.querySelector("#load-demo"),
  swapModels: document.querySelector("#swap-models"),
  baseName: document.querySelector("#base-name"),
  revisionName: document.querySelector("#revision-name"),
  diffName: document.querySelector("#diff-name"),
  changeList: document.querySelector("#change-list"),
  counts: {
    added: document.querySelector("#count-added"),
    removed: document.querySelector("#count-removed"),
    modified: document.querySelector("#count-modified"),
    unchanged: document.querySelector("#count-unchanged"),
  },
  geometryDelta: document.querySelector("#geometry-delta"),
};

const state = {
  base: null,
  revision: null,
  syncSource: null,
  usingDemo: true,
};

class ViewerPane {
  constructor(canvas, label) {
    this.canvas = canvas;
    this.label = label;
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x06131f);

    this.camera = new THREE.PerspectiveCamera(45, 1, 0.1, 2000);
    this.camera.position.set(12, 9, 12);

    this.renderer = new THREE.WebGLRenderer({
      canvas,
      antialias: true,
      alpha: true,
    });
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));

    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true;
    this.controls.target.set(0, 0, 0);

    this.content = new THREE.Group();
    this.scene.add(this.content);

    const ambient = new THREE.AmbientLight(0xffffff, 1.5);
    const key = new THREE.DirectionalLight(0xd7ecff, 1.8);
    key.position.set(8, 12, 10);
    const fill = new THREE.DirectionalLight(0x7fd8ff, 0.65);
    fill.position.set(-12, 6, -8);
    const grid = new THREE.GridHelper(30, 30, 0x35627f, 0x183245);
    grid.position.y = -2.5;
    const gridMaterials = Array.isArray(grid.material) ? grid.material : [grid.material];
    gridMaterials.forEach((material) => {
      material.transparent = true;
      material.opacity = 0.3;
    });

    this.scene.add(ambient, key, fill, grid);

    this.controls.addEventListener("change", () => {
      state.syncSource = this;
    });
  }

  setContent(object) {
    this.scene.remove(this.content);
    this.content = object ?? new THREE.Group();
    this.scene.add(this.content);
  }

  resize() {
    const frame = this.canvas.parentElement;
    const width = frame.clientWidth;
    const height = frame.clientHeight;
    if (width === 0 || height === 0) {
      return;
    }

    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(width, height, false);
  }

  render() {
    this.controls.update();
    this.renderer.render(this.scene, this.camera);
  }
}

const viewers = {
  base: new ViewerPane(document.querySelector("#base-canvas"), "base"),
  revision: new ViewerPane(document.querySelector("#revision-canvas"), "revision"),
  diff: new ViewerPane(document.querySelector("#diff-canvas"), "diff"),
};

function animate() {
  requestAnimationFrame(animate);

  if (state.syncSource) {
    for (const pane of Object.values(viewers)) {
      if (pane === state.syncSource) {
        continue;
      }
      pane.camera.position.copy(state.syncSource.camera.position);
      pane.camera.quaternion.copy(state.syncSource.camera.quaternion);
      pane.controls.target.copy(state.syncSource.controls.target);
    }
    state.syncSource = null;
  }

  Object.values(viewers).forEach((pane) => pane.render());
}

function resizeAll() {
  Object.values(viewers).forEach((pane) => pane.resize());
}

function clearViewerPane(pane) {
  pane.setContent(new THREE.Group());
}

function roundValue(value) {
  return Number(value.toFixed(4));
}

function roundVector(vector) {
  return [roundValue(vector.x), roundValue(vector.y), roundValue(vector.z)];
}

function renderableChildren(root) {
  const nodes = [];
  root.traverse((child) => {
    if (child.isMesh || child.isLine || child.isPoints) {
      nodes.push(child);
    }
  });
  return nodes;
}

function sampleTypedArray(array, sampleCount = 16) {
  if (!array || array.length === 0) {
    return "none";
  }

  const step = Math.max(1, Math.floor(array.length / sampleCount));
  const sampled = [];
  for (let index = 0; index < array.length && sampled.length < sampleCount; index += step) {
    sampled.push(roundValue(Number(array[index])));
  }
  return `${array.length}:${sampled.join(",")}`;
}

function geometrySignature(geometry) {
  if (!geometry) {
    return "no-geometry";
  }

  geometry.computeBoundingBox();
  const position = geometry.getAttribute("position");
  const bbox = geometry.boundingBox;
  const bounds = bbox
    ? `${roundVector(bbox.min).join("/")}:${roundVector(bbox.max).join("/")}`
    : "no-bounds";

  return JSON.stringify({
    type: geometry.type,
    vertexCount: position?.count ?? 0,
    indexCount: geometry.index?.count ?? 0,
    bounds,
    positions: sampleTypedArray(position?.array),
    indices: sampleTypedArray(geometry.index?.array),
  });
}

function materialSignature(material) {
  const materials = Array.isArray(material) ? material : [material];
  return JSON.stringify(
    materials.map((item) => ({
      type: item?.type ?? "none",
      name: item?.name ?? "",
      color: item?.color?.getHexString?.() ?? null,
      emissive: item?.emissive?.getHexString?.() ?? null,
      opacity: item?.opacity ?? 1,
      metalness: item?.metalness ?? null,
      roughness: item?.roughness ?? null,
      wireframe: item?.wireframe ?? false,
    })),
  );
}

function inspectObject(object, path) {
  return {
    path,
    name: object.name || object.type,
    type: object.type,
    object,
    renderable: Boolean(object.isMesh || object.isLine || object.isPoints),
    transform: JSON.stringify({
      position: roundVector(object.position),
      rotation: [
        roundValue(object.rotation.x),
        roundValue(object.rotation.y),
        roundValue(object.rotation.z),
      ],
      scale: roundVector(object.scale),
    }),
    geometry: geometrySignature(object.geometry),
    material: materialSignature(object.material),
  };
}

function buildObjectIndex(root) {
  const index = new Map();

  function walk(object, parentPath) {
    const childCounts = new Map();
    for (const child of object.children) {
      const label = child.userData?.diffId || child.name || child.type;
      const occurrence = childCounts.get(label) ?? 0;
      childCounts.set(label, occurrence + 1);
      const path = `${parentPath}/${label}[${occurrence}]`;
      index.set(path, inspectObject(child, path));
      walk(child, path);
    }
  }

  const rootPath = "root[0]";
  index.set(rootPath, inspectObject(root, rootPath));
  walk(root, rootPath);

  return index;
}

function collectReasons(baseEntry, revisionEntry) {
  const reasons = [];
  if (baseEntry.type !== revisionEntry.type) {
    reasons.push("type");
  }
  if (baseEntry.transform !== revisionEntry.transform) {
    reasons.push("transform");
  }
  if (baseEntry.geometry !== revisionEntry.geometry) {
    reasons.push("geometry");
  }
  if (baseEntry.material !== revisionEntry.material) {
    reasons.push("material");
  }
  return reasons;
}

function countVertices(root) {
  return renderableChildren(root).reduce((total, node) => {
    const position = node.geometry?.getAttribute?.("position");
    return total + (position?.count ?? 0);
  }, 0);
}

function createDisplayClone(root) {
  const clone = root.clone(true);
  clone.traverse((child) => {
    if (child.isMesh) {
      child.castShadow = true;
      child.receiveShadow = true;
    }
  });
  return clone;
}

function applyTint(material, style) {
  if (!material) {
    return material;
  }

  const clone = material.clone();
  if ("color" in clone && clone.color) {
    clone.color.set(style.color);
  }
  if ("emissive" in clone && clone.emissive) {
    clone.emissive.set(style.emissive ?? style.color);
    clone.emissiveIntensity = style.emissiveIntensity ?? 0.25;
  }
  clone.opacity = style.opacity;
  clone.transparent = style.opacity < 1;
  clone.wireframe = style.wireframe ?? false;
  clone.depthWrite = style.opacity >= 0.35;
  return clone;
}

function tintObject(root, style) {
  const clone = root.clone(true);
  clone.traverse((child) => {
    if (child.isMesh) {
      if (Array.isArray(child.material)) {
        child.material = child.material.map((material) => applyTint(material, style));
      } else {
        child.material = applyTint(child.material, style);
      }
    }
  });
  return clone;
}

function createHighlightedWireframe(root, style) {
  const group = new THREE.Group();
  const scaleMultipliers = style.scaleMultipliers ?? [1];

  for (const scaleMultiplier of scaleMultipliers) {
    const clone = root.clone(true);
    clone.traverse((child) => {
      if (!child.isMesh) {
        return;
      }

      child.material = new THREE.MeshBasicMaterial({
        color: style.color,
        wireframe: true,
        transparent: true,
        opacity: style.opacity,
        depthWrite: false,
      });
      child.scale.multiplyScalar(scaleMultiplier);
    });
    group.add(clone);
  }

  return group;
}

function buildDiffScene(baseRoot, revisionRoot, changes) {
  const group = new THREE.Group();

  if (!baseRoot || !revisionRoot) {
    return group;
  }

  const baseIndex = buildObjectIndex(baseRoot);
  const revisionIndex = buildObjectIndex(revisionRoot);
  const diffByPath = new Map(changes.map((change) => [change.path, change]));

  for (const [path, entry] of revisionIndex.entries()) {
    if (!entry.renderable) {
      continue;
    }

    const change = diffByPath.get(path);
    if (!change) {
      continue;
    }

    if (change.status === "unchanged") {
      group.add(
        tintObject(entry.object, {
          color: 0x7d94a8,
          emissive: 0x3d5871,
          emissiveIntensity: 0.12,
          opacity: 0.16,
        }),
      );
    }

    if (change.status === "added") {
      group.add(
        tintObject(entry.object, {
          color: 0x4ade80,
          emissive: 0x0f7d4b,
          emissiveIntensity: 0.24,
          opacity: 0.82,
        }),
      );
    }

    if (change.status === "modified") {
      group.add(
        tintObject(entry.object, {
          color: 0xfbbf24,
          emissive: 0xa16207,
          emissiveIntensity: 0.28,
          opacity: 0.84,
        }),
      );
    }
  }

  for (const [path, entry] of baseIndex.entries()) {
    if (!entry.renderable) {
      continue;
    }

    const change = diffByPath.get(path);
    if (!change) {
      continue;
    }

    if (change.status === "removed") {
      group.add(
        createHighlightedWireframe(entry.object, {
          color: 0xfb7185,
          opacity: 0.82,
          scaleMultipliers: [1, 1.008],
        }),
      );
    }

    if (change.status === "modified") {
      group.add(
        createHighlightedWireframe(entry.object, {
          color: 0xfb7185,
          opacity: 0.56,
          scaleMultipliers: [1, 1.006],
        }),
      );
    }
  }

  return group;
}

function computeDiff(baseRoot, revisionRoot) {
  const baseIndex = buildObjectIndex(baseRoot);
  const revisionIndex = buildObjectIndex(revisionRoot);
  const allPaths = new Set([...baseIndex.keys(), ...revisionIndex.keys()]);
  const changes = [];

  for (const path of allPaths) {
    const baseEntry = baseIndex.get(path);
    const revisionEntry = revisionIndex.get(path);

    if (!baseEntry) {
      changes.push({
        path,
        name: revisionEntry.name,
        status: "added",
        reasons: ["new node"],
        renderable: revisionEntry.renderable,
      });
      continue;
    }

    if (!revisionEntry) {
      changes.push({
        path,
        name: baseEntry.name,
        status: "removed",
        reasons: ["missing in candidate revision"],
        renderable: baseEntry.renderable,
      });
      continue;
    }

    const reasons = collectReasons(baseEntry, revisionEntry);
    changes.push({
      path,
      name: revisionEntry.name,
      status: reasons.length === 0 ? "unchanged" : "modified",
      reasons: reasons.length === 0 ? ["no material, geometry, or transform delta"] : reasons,
      renderable: baseEntry.renderable || revisionEntry.renderable,
    });
  }

  changes.sort((left, right) => {
    const priorityDelta = comparePriority[left.status] - comparePriority[right.status];
    if (priorityDelta !== 0) {
      return priorityDelta;
    }
    return left.path.localeCompare(right.path);
  });

  return {
    changes,
    summary: {
      added: changes.filter((item) => item.status === "added" && item.renderable).length,
      removed: changes.filter((item) => item.status === "removed" && item.renderable).length,
      modified: changes.filter((item) => item.status === "modified" && item.renderable).length,
      unchanged: changes.filter((item) => item.status === "unchanged" && item.renderable).length,
      baseMeshes: renderableChildren(baseRoot).length,
      revisionMeshes: renderableChildren(revisionRoot).length,
      baseVertices: countVertices(baseRoot),
      revisionVertices: countVertices(revisionRoot),
    },
  };
}

function fitViewersToObjects(objects) {
  const bounds = new THREE.Box3();
  let hasGeometry = false;

  for (const object of objects) {
    if (!object) {
      continue;
    }
    const box = new THREE.Box3().setFromObject(object);
    if (!Number.isFinite(box.min.lengthSq()) || box.isEmpty()) {
      continue;
    }
    bounds.union(box);
    hasGeometry = true;
  }

  if (!hasGeometry) {
    return;
  }

  const size = bounds.getSize(new THREE.Vector3());
  const center = bounds.getCenter(new THREE.Vector3());
  const maxDimension = Math.max(size.x, size.y, size.z, 1);
  const distance = maxDimension * 1.75;

  for (const pane of Object.values(viewers)) {
    pane.controls.target.copy(center);
    pane.camera.position.set(center.x + distance, center.y + distance * 0.7, center.z + distance);
    pane.camera.near = Math.max(0.1, maxDimension / 100);
    pane.camera.far = maxDimension * 30;
    pane.camera.updateProjectionMatrix();
    pane.controls.update();
  }
}

function updateSummary(diff) {
  els.counts.added.textContent = diff.summary.added;
  els.counts.removed.textContent = diff.summary.removed;
  els.counts.modified.textContent = diff.summary.modified;
  els.counts.unchanged.textContent = diff.summary.unchanged;

  const meshDelta = diff.summary.revisionMeshes - diff.summary.baseMeshes;
  const vertexDelta = diff.summary.revisionVertices - diff.summary.baseVertices;
  const meshLabel = `${meshDelta >= 0 ? "+" : ""}${meshDelta} meshes`;
  const vertexLabel = `${vertexDelta >= 0 ? "+" : ""}${vertexDelta} verts`;
  els.geometryDelta.textContent = `${meshLabel} / ${vertexLabel}`;
}

function escapeHtml(value) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function showMessage(message) {
  els.changeList.innerHTML = `<div class="empty-state">${escapeHtml(message)}</div>`;
}

function renderChangeList(diff) {
  const interesting = diff.changes.filter((change) => change.status !== "unchanged" && change.renderable);
  if (interesting.length === 0) {
    els.changeList.innerHTML = `
      <div class="empty-state">
        No renderable deltas were detected. Upload two different revisions or load the demo data.
      </div>
    `;
    return;
  }

  els.changeList.innerHTML = interesting
    .map(
      (change) => `
        <article class="change-card">
          <header>
            <h3>${escapeHtml(change.name)}</h3>
            <span class="pill ${change.status}">${change.status}</span>
          </header>
          <div class="change-path">${escapeHtml(change.path)}</div>
          <div class="change-meta">${escapeHtml(change.reasons.join(", "))}</div>
        </article>
      `,
    )
    .join("");
}

function parseThreeJson(text) {
  const data = JSON.parse(text);
  return loader.parse(data);
}

async function uploadSceneFile(file) {
  const formData = new FormData();
  formData.append("file", file);

  const response = await fetch(uploadEndpoint, {
    method: "POST",
    body: formData,
    headers: {
      Accept: "application/json",
    },
  });

  const rawBody = await response.text();
  let payload = {};
  if (rawBody) {
    try {
      payload = JSON.parse(rawBody);
    } catch {
      payload = {};
    }
  }
  if (!response.ok) {
    const detail =
      payload.detail ??
      (rawBody ? rawBody.slice(0, 180) : `Upload failed with status ${response.status}`);
    throw new Error(detail);
  }

  return payload;
}

function cloneSceneGraph(root) {
  const clone = root.clone(true);
  clone.traverse((child) => {
    child.userData = { ...child.userData };
  });
  return clone;
}

function normalizeImportedScene(scene, sceneName) {
  let meshIndex = 0;
  let nodeIndex = 0;

  scene.name = sceneName;
  scene.userData = { ...scene.userData, diffId: "scene-root" };

  scene.traverse((child) => {
    if (child === scene) {
      return;
    }

    child.userData = { ...child.userData };

    if (child.isMesh) {
      meshIndex += 1;
      child.userData.diffId = `mesh-${meshIndex}`;
      child.name = child.name && !/^[a-f0-9]{20,}$/i.test(child.name) ? child.name : `Part ${meshIndex}`;
      return;
    }

    nodeIndex += 1;
    child.userData.diffId = `node-${nodeIndex}`;
    if (!child.name || /^[a-f0-9]{20,}$/i.test(child.name)) {
      child.name = `Node ${nodeIndex}`;
    }
  });

  return scene;
}

async function loadGltfScene(url, sceneName) {
  const gltf = await gltfLoader.loadAsync(url);
  const scene = cloneSceneGraph(gltf.scene);
  return normalizeImportedScene(scene, sceneName);
}

async function loadUploadedModel(model) {
  if (model.asset_kind === "glb") {
    return loadGltfScene(model.asset_url, model.scene_name);
  }

  if (model.asset_kind === "three-json") {
    const response = await fetch(model.asset_url);
    if (!response.ok) {
      throw new Error(`Failed to load ${model.original_filename}`);
    }
    const parsed = parseThreeJson(await response.text());
    parsed.name = parsed.name || model.scene_name;
    return parsed;
  }

  throw new Error(`Unsupported asset kind: ${model.asset_kind}`);
}

async function loadSceneFromUrl(url) {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Failed to load ${url}`);
  }
  return parseThreeJson(await response.text());
}

function updateSceneLabels() {
  els.baseName.textContent = state.base?.name ?? "Waiting for model";
  els.revisionName.textContent = state.revision?.name ?? "Waiting for model";
  if (state.base && state.revision) {
    els.diffName.textContent = `${state.base.name} vs ${state.revision.name}`;
  } else {
    els.diffName.textContent = "Overlay of both revisions";
  }
}

function resetSummary() {
  els.counts.added.textContent = "0";
  els.counts.removed.textContent = "0";
  els.counts.modified.textContent = "0";
  els.counts.unchanged.textContent = "0";
  els.geometryDelta.textContent = "0 meshes / 0 verts";
}

function renderPartialState() {
  viewers.base.setContent(state.base ? createDisplayClone(state.base) : new THREE.Group());
  viewers.revision.setContent(state.revision ? createDisplayClone(state.revision) : new THREE.Group());
  viewers.diff.setContent(new THREE.Group());

  const visibleObjects = [viewers.base.content, viewers.revision.content].filter(Boolean);
  fitViewersToObjects(visibleObjects);
  resetSummary();
  updateSceneLabels();

  if (state.base || state.revision) {
    showMessage("Upload the other model to generate the diff view.");
  } else {
    showMessage("Upload an original model and a changed model to begin.");
  }

  resizeAll();
}

function refreshViewer() {
  if (!state.base || !state.revision) {
    renderPartialState();
    return;
  }

  const diff = computeDiff(state.base, state.revision);

  viewers.base.setContent(createDisplayClone(state.base));
  viewers.revision.setContent(createDisplayClone(state.revision));
  viewers.diff.setContent(buildDiffScene(state.base, state.revision, diff.changes));

  fitViewersToObjects([viewers.base.content, viewers.revision.content, viewers.diff.content]);
  updateSummary(diff);
  renderChangeList(diff);
  updateSceneLabels();
  resizeAll();
}

async function handleFileInput(target, file) {
  if (!file) {
    return;
  }

  try {
    if (state.usingDemo) {
      state.usingDemo = false;
      state.base = null;
      state.revision = null;
      renderPartialState();
    }

    showMessage(`Uploading ${file.name} to the backend for conversion.`);
    const uploadedModel = await uploadSceneFile(file);
    const parsed = await loadUploadedModel(uploadedModel);
    state[target] = parsed;
    updateSceneLabels();
    refreshViewer();
  } catch (error) {
    showMessage(`Failed to import ${file.name}. ${error.message}`);
    console.error(error);
  }
}

async function loadDemoScenes() {
  const [base, revision] = await Promise.all([
    loadSceneFromUrl("/static/samples/aircraft-baseline.json"),
    loadSceneFromUrl("/static/samples/aircraft-revision.json"),
  ]);

  base.name = base.name || "Baseline";
  revision.name = revision.name || "Revision";
  state.base = base;
  state.revision = revision;
  state.usingDemo = true;
  refreshViewer();
}

function swapScenes() {
  [state.base, state.revision] = [state.revision, state.base];
  updateSceneLabels();
  refreshViewer();
}

els.baseFile.addEventListener("change", async (event) => {
  const [file] = event.target.files;
  await handleFileInput("base", file);
});

els.revisionFile.addEventListener("change", async (event) => {
  const [file] = event.target.files;
  await handleFileInput("revision", file);
});

els.loadDemo.addEventListener("click", async () => {
  try {
    await loadDemoScenes();
  } catch (error) {
    els.changeList.innerHTML = `<div class="empty-state">${error.message}</div>`;
  }
});

els.swapModels.addEventListener("click", () => {
  swapScenes();
});

window.addEventListener("resize", resizeAll);

resizeAll();
animate();
loadDemoScenes().catch((error) => {
  els.changeList.innerHTML = `<div class="empty-state">${error.message}</div>`;
});
