// Capture Splat integration. Spark and Three.js are pinned, unmodified dependencies.
// See UPSTREAM.md for source, licenses and the reproducible offline bundle command.
import * as THREE from "three";
import { SparkRenderer, SplatMesh, PointerControls } from "@sparkjsdev/spark";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const $ = (id) => document.getElementById(id);
const canvas = $("canvas");
const keys = new Set();
const heightPointers = new Map();
const stick = new THREE.Vector2();
let renderer, spark, mesh, camera, scene, pointer, orbit;
let loaded = false;
let disposed = false;
let mode = "walk";
let quality = "detail";
let vertical = 0;
let stickPointer = null;
let baseSpeed = 0.65;
let speedIndex = 1;
let lastFrame = 0;
let dirty = true;
let settleUntil = 0;
const speeds = [0.35, 1, 2.2];
const speedNames = ["Slow", "Normal", "Fast"];
const homePosition = new THREE.Vector3();
const homeQuaternion = new THREE.Quaternion();
const worldUp = new THREE.Vector3(0, 1, 0);
const lookForward = new THREE.Vector3();
const moveRight = new THREE.Vector3();
const movement = new THREE.Vector3();
const orbitTarget = new THREE.Vector3();
let focusDistance = 2;
let hintTimer;

function requestRender() {
  dirty = true;
  settleUntil = performance.now() + 200;
}

function showHint(text) {
  clearTimeout(hintTimer);
  $("hint").textContent = text;
  $("hint").style.opacity = "1";
  hintTimer = setTimeout(() => { $("hint").style.opacity = ".28"; }, 6500);
}

function clearInput() {
  keys.clear();
  heightPointers.clear();
  stick.set(0, 0);
  stickPointer = null;
  vertical = 0;
  $("thumb").style.transform = "translate(0, 0)";
  if (pointer) {
    pointer.rotateVelocity.set(0, 0, 0);
    pointer.moveVelocity.set(0, 0, 0);
    pointer.scroll.set(0, 0, 0);
    pointer.rotating = null;
    pointer.sliding = null;
    pointer.lastDown = null;
    pointer.dualPress = false;
    pointer.doublePressed = undefined;
    pointer.triplePressed = false;
  }
}

function resize() {
  if (!renderer || !camera) return;
  const width = Math.max(1, canvas.clientWidth);
  const height = Math.max(1, canvas.clientHeight);
  // Device-pixel detail, bounded at the GPU limit, 4K long edge and 8 MP.
  // Smooth uses fewer pixels. Neither setting invents missing scene detail.
  const longEdge = quality === "detail" ? 3840 : 1920;
  const pixelBudget = quality === "detail" ? 8_294_400 : 2_073_600;
  const ratio = Math.max(0.25, Math.min(window.devicePixelRatio || 1,
    quality === "detail" ? 3 : 1.25, longEdge / Math.max(width, height),
    Math.sqrt(pixelBudget / (width * height)),
    renderer.capabilities.maxTextureSize / Math.max(width, height)));
  renderer.setPixelRatio(ratio);
  renderer.setSize(width, height, false);
  camera.aspect = width / height;
  camera.updateProjectionMatrix();
  requestRender();
}

function makeOrbit() {
  const savedPosition = camera.position.clone();
  const savedQuaternion = camera.quaternion.clone();
  const controls = new OrbitControls(camera, canvas);
  controls.enabled = false;
  controls.enableDamping = true;
  controls.dampingFactor = 0.12;
  controls.rotateSpeed = 0.55;
  controls.panSpeed = 0.7;
  controls.zoomSpeed = 0.7;
  controls.minDistance = 0.03;
  controls.maxDistance = 1000;
  controls.screenSpacePanning = true;
  controls.addEventListener("change", requestRender);
  // OrbitControls' constructor updates a default origin target. Preserve the
  // current view and put its pivot ahead of the camera before enabling it.
  camera.position.copy(savedPosition);
  camera.quaternion.copy(savedQuaternion);
  camera.getWorldDirection(lookForward);
  controls.target.copy(savedPosition).addScaledVector(lookForward, focusDistance);
  return controls;
}

function setMode(next) {
  if (!loaded || next === mode) return;
  clearInput();
  // Recreate controls to discard their private damping velocity on each switch.
  orbit.dispose();
  orbit = makeOrbit();
  if (next === "orbit") {
    camera.getWorldDirection(lookForward);
    orbitTarget.copy(camera.position).addScaledVector(lookForward, focusDistance);
    orbit.target.copy(orbitTarget);
  }
  mode = next;
  pointer.enable = mode === "walk";
  orbit.enabled = mode === "orbit";
  document.body.dataset.mode = mode;
  $("walk").setAttribute("aria-pressed", String(mode === "walk"));
  $("orbit").setAttribute("aria-pressed", String(mode === "orbit"));
  showHint(mode === "walk" ? "Drag to look · Use the pad to move" : "Drag to orbit · Pinch to zoom · Two fingers to pan");
  requestRender();
}

function reset() {
  if (!loaded) return;
  clearInput();
  camera.position.copy(homePosition);
  camera.quaternion.copy(homeQuaternion);
  orbit.dispose();
  orbit = makeOrbit();
  camera.getWorldDirection(lookForward);
  orbit.target.copy(camera.position).addScaledVector(lookForward, focusDistance);
  orbit.enabled = mode === "orbit";
  requestRender();
  showHint("Back at your starting view");
}

function bindInput() {
  const pad = $("joystick");
  const updateStick = (event) => {
    const rect = pad.getBoundingClientRect();
    const radius = rect.width * 0.31;
    stick.set((event.clientX - rect.left - rect.width / 2) / radius,
      (event.clientY - rect.top - rect.height / 2) / radius);
    if (stick.length() > 1) stick.normalize();
    if (stick.length() < .1) stick.set(0, 0);
    $("thumb").style.transform = `translate(${stick.x * radius}px, ${stick.y * radius}px)`;
    requestRender();
  };
  pad.addEventListener("pointerdown", (event) => {
    if (!loaded || stickPointer !== null) return;
    event.preventDefault();
    stickPointer = event.pointerId;
    pad.setPointerCapture(event.pointerId);
    updateStick(event);
  });
  pad.addEventListener("pointermove", (event) => {
    if (event.pointerId === stickPointer) { event.preventDefault(); updateStick(event); }
  });
  const releaseStick = (event) => {
    if (event.pointerId !== stickPointer) return;
    stickPointer = null;
    stick.set(0, 0);
    $("thumb").style.transform = "translate(0, 0)";
  };
  for (const name of ["pointerup", "pointercancel", "lostpointercapture"]) pad.addEventListener(name, releaseStick);
  for (const [id, direction] of [["up", 1], ["down", -1]]) {
    const button = $(id);
    button.addEventListener("pointerdown", (event) => {
      if (!loaded) return;
      event.preventDefault();
      heightPointers.set(event.pointerId, direction);
      vertical = Math.max(-1, Math.min(1, [...heightPointers.values()].reduce((a, b) => a + b, 0)));
      button.setPointerCapture(event.pointerId);
      requestRender();
    });
    for (const name of ["pointerup", "pointercancel", "lostpointercapture"]) button.addEventListener(name, (event) => {
      heightPointers.delete(event.pointerId);
      vertical = Math.max(-1, Math.min(1, [...heightPointers.values()].reduce((a, b) => a + b, 0)));
    });
  }
  window.addEventListener("keydown", (event) => {
    if (["KeyW", "KeyA", "KeyS", "KeyD", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "KeyQ", "KeyE"].includes(event.code)) {
      event.preventDefault(); keys.add(event.code); requestRender();
    }
    if (event.code === "KeyR") reset();
  });
  window.addEventListener("keyup", (event) => keys.delete(event.code));
  window.addEventListener("blur", clearInput);
  canvas.addEventListener("lostpointercapture", () => {
    if (pointer && !pointer.rotating && !pointer.sliding) pointer.rotateVelocity.set(0, 0, 0);
  });
  $("walk").addEventListener("click", () => setMode("walk"));
  $("orbit").addEventListener("click", () => setMode("orbit"));
  $("reset").addEventListener("click", reset);
  $("quality").addEventListener("click", () => {
    quality = quality === "detail" ? "smooth" : "detail";
    $("quality").textContent = quality === "detail" ? "Detail" : "Smooth";
    $("quality").setAttribute("aria-label", quality === "detail" ? "Rendering detail. Tap for smoother movement." : "Smoother movement. Tap for more rendering detail.");
    resize();
  });
  $("speed").addEventListener("click", () => {
    speedIndex = (speedIndex + 1) % speeds.length;
    $("speed").textContent = `${speedNames[speedIndex]} speed`;
  });
}

function updateWalk(delta) {
  let updated = pointer.update(delta, camera, camera);
  if (updated) {
    // Remove accidental roll while retaining the look direction and real gravity.
    camera.getWorldDirection(lookForward);
    camera.lookAt(orbitTarget.copy(camera.position).add(lookForward));
  }
  const x = stick.x + Number(keys.has("KeyD") || keys.has("ArrowRight")) - Number(keys.has("KeyA") || keys.has("ArrowLeft"));
  const z = -stick.y + Number(keys.has("KeyW") || keys.has("ArrowUp")) - Number(keys.has("KeyS") || keys.has("ArrowDown"));
  const y = vertical + Number(keys.has("KeyE")) - Number(keys.has("KeyQ"));
  if (x || z || y) {
    camera.getWorldDirection(lookForward).projectOnPlane(worldUp);
    if (lookForward.lengthSq() < .0001) lookForward.set(0, 0, -1).applyQuaternion(homeQuaternion).projectOnPlane(worldUp);
    lookForward.normalize();
    moveRight.crossVectors(lookForward, worldUp).normalize();
    movement.copy(moveRight).multiplyScalar(x).addScaledVector(lookForward, z).addScaledVector(worldUp, y);
    if (movement.length() > 1) movement.normalize();
    camera.position.addScaledVector(movement, baseSpeed * speeds[speedIndex] * delta);
    updated = true;
  }
  return updated;
}

function frame(now) {
  if (disposed || document.hidden) return;
  const delta = Math.min(.05, Math.max(.001, (now - (lastFrame || now)) / 1000));
  lastFrame = now;
  if (loaded) {
    const changed = mode === "walk" ? updateWalk(delta) : orbit.update(delta);
    if (dirty || changed || now < settleUntil) {
      dirty = false;
      renderer.render(scene, camera);
    }
  }
}

function estimateSceneScale(bytes, format) {
  // Robust sampled distances prevent isolated floaters setting movement speed.
  const view = new DataView(bytes);
  let count = bytes.byteLength / 32;
  let dataOffset = 0;
  let stride = 32;
  let positionOffsets = [0, 4, 8];
  if (format === "ply") {
    const prefix = new TextDecoder("ascii").decode(new Uint8Array(bytes, 0, Math.min(bytes.byteLength, 16 * 1024)));
    const marker = /(?:^|\n)end_header\r?\n/.exec(prefix);
    if (!marker) throw new Error("Invalid Gaussian PLY header.");
    dataOffset = marker.index + marker[0].length;
    const lines = prefix.slice(0, dataOffset).split(/\r?\n/);
    if (lines[0] !== "ply" || !lines.includes("format binary_little_endian 1.0")) throw new Error("Unsupported Gaussian PLY.");
    const vertex = lines.find((line) => /^element vertex \d+$/.test(line));
    count = vertex ? Number(vertex.split(" ")[2]) : 0;
    const properties = lines.filter((line) => line.startsWith("property ")).map((line) => line.trim().split(/\s+/));
    if (properties.some((fields) => fields.length !== 3 || !["float", "float32"].includes(fields[1]))) throw new Error("Unsupported PLY property.");
    stride = properties.length * 4;
    positionOffsets = ["x", "y", "z"].map((name) => properties.findIndex((fields) => fields[2] === name) * 4);
    if (!Number.isInteger(count) || count < 1 || count > 2_000_000 || positionOffsets.some((offset) => offset < 0) || bytes.byteLength !== dataOffset + count * stride) throw new Error("Invalid PLY vertex data.");
  } else if (!Number.isInteger(count)) {
    throw new Error("Invalid compact splat data.");
  }
  const distances = [];
  for (let i = 0; i < count; i += Math.max(1, Math.ceil(count / 2048))) {
    const offset = dataOffset + i * stride;
    const distance = Math.hypot(...positionOffsets.map((field) => view.getFloat32(offset + field, true)));
    if (Number.isFinite(distance) && distance > .01) distances.push(distance);
  }
  distances.sort((a, b) => a - b);
  const radius = distances[Math.floor(distances.length / 2)] || 2;
  focusDistance = Math.max(.2, Math.min(20, radius));
  baseSpeed = Math.max(.1, Math.min(3, radius * .25));
  return count;
}

function dispose() {
  if (disposed) return;
  disposed = true;
  clearInput();
  clearTimeout(hintTimer);
  renderer?.setAnimationLoop(null);
  orbit?.dispose();
  mesh?.dispose();
  spark?.dispose();
  renderer?.dispose();
}

async function openRoom() {
  try {
    renderer = new THREE.WebGLRenderer({ canvas, antialias: false, alpha: false, powerPreference: "high-performance" });
    renderer.setClearColor(0x101216, 1);
    scene = new THREE.Scene();
    camera = new THREE.PerspectiveCamera(60, 1, .02, 1000);
    // Desktop jobs are in the context camera's CV frame: +X right,+Y down,+Z forward.
    // Three uses +Y up,-Z forward. Rotate only the camera, never the model twice.
    const suppliedCamera = window.CAPTURE_SPLAT_CAMERA;
    if (suppliedCamera?.convention === "opengl") {
      const r = suppliedCamera.rotation;
      camera.position.fromArray(suppliedCamera.position);
      camera.quaternion.setFromRotationMatrix(new THREE.Matrix4().set(
        r[0][0], r[0][1], r[0][2], 0,
        r[1][0], r[1][1], r[1][2], 0,
        r[2][0], r[2][1], r[2][2], 0, 0, 0, 0, 1));
    } else if (suppliedCamera) {
      worldUp.set(0, -1, 0);
      camera.quaternion.setFromAxisAngle(new THREE.Vector3(1, 0, 0), Math.PI);
      const gravity = window.CAPTURE_SPLAT_CAMERA.gravity_up;
      if (Array.isArray(gravity) && gravity.length === 3 && gravity.every(Number.isFinite)) {
        const candidate = new THREE.Vector3(...gravity);
        if (candidate.lengthSq() > 1e-12 && candidate.lengthSq() < 1e12) worldUp.copy(candidate.normalize());
      }
    }
    camera.up.copy(worldUp);
    camera.getWorldDirection(lookForward);
    camera.lookAt(orbitTarget.copy(camera.position).add(lookForward));
    homePosition.copy(camera.position);
    homeQuaternion.copy(camera.quaternion);
    spark = new SparkRenderer({ renderer, onDirty: requestRender, enableLod: false, sortRadial: true, minSortIntervalMs: 24 });
    scene.add(spark);
    pointer = new PointerControls({ canvas, rotateSpeed: .0025, reverseRotate: false,
      slideSpeed: .002, scrollSpeed: .001, moveInertia: .08, rotateInertia: .07,
      pointerRollScale: 0, pressMoveSpeed: 0, doublePressMoveSpeed: 0, triplePressMoveSpeed: 0 });
    orbit = makeOrbit();
    bindInput();
    resize();
    const format = window.CAPTURE_SPLAT_MODEL?.format === "ply" ? "ply" : "splat";
    const response = await fetch(new URL(`model.${format}`, window.location.href));
    if (!response.ok) throw new Error("The room file could not be opened.");
    const bytes = await response.arrayBuffer();
    if (!bytes.byteLength || bytes.byteLength > 64 * 1024 * 1024) throw new Error("This room file is empty or exceeds the mobile limit.");
    const count = estimateSceneScale(bytes, format);
    mesh = new SplatMesh({ fileBytes: bytes, fileType: format, fileName: `model.${format}`, enableLod: false, raycastable: false });
    await mesh.initialized;
    if (disposed) { mesh.dispose(); return; }
    scene.add(mesh);
    loaded = true;
    $("loading").hidden = true;
    showHint("Drag to look · Use the pad to move");
    renderer.setAnimationLoop(frame);
    requestRender();
    // Read-only, local diagnostic snapshot used for integration acceptance.
    Object.defineProperty(window, "captureSplatViewer", { value: Object.freeze({
      getState: () => ({ ready: loaded, mode, quality, count, format, maxSh: mesh.maxSh,
        position: camera.position.toArray(), quaternion: camera.quaternion.toArray(), up: camera.up.toArray(),
        renderSize: renderer.getDrawingBufferSize(new THREE.Vector2()).toArray(),
        homePosition: homePosition.toArray(), homeQuaternion: homeQuaternion.toArray() })
    }) });
  } catch (error) {
    console.error("Room viewer:", error);
    $("loading").hidden = false;
    $("loading").querySelector(".spinner").hidden = true;
    $("message").textContent = renderer ? "This room could not be opened. Close the viewer and try importing it again." : "This device could not start the 3D viewer. Close it and try again.";
    dispose();
  }
}

window.addEventListener("resize", resize);
document.addEventListener("visibilitychange", () => {
  clearInput();
  lastFrame = 0;
  if (!disposed && renderer) {
    renderer.setAnimationLoop(document.hidden ? null : frame);
    requestRender();
  }
});
canvas.addEventListener("webglcontextlost", (event) => {
  event.preventDefault();
  $("loading").hidden = false;
  $("loading").querySelector(".spinner").hidden = true;
  $("message").textContent = "The 3D viewer was paused by your iPhone. Close and reopen the room.";
  dispose();
});
window.addEventListener("pagehide", dispose, { once: true });
openRoom();
