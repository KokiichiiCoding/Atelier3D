// The studio viewport: three.js scene, display modes, lighting presets, turntable, exports.
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';

const SKIES = {
  sunset: { top: '#3b4a7a', horizon: '#f2a46b', bottom: '#2a2220', sun: [1, 0.72, 0.45], sunDir: [-0.6, 0.18, 0.5], sunSize: 0.09, sunPower: 40 },
  overcast: { top: '#b9c2cc', horizon: '#d9dde2', bottom: '#5a5d61', sun: [1, 1, 1], sunDir: [0.2, 1, 0.3], sunSize: 0.9, sunPower: 1.2 },
  night: { top: '#05070d', horizon: '#1d2740', bottom: '#050506', sun: [0.55, 0.7, 1], sunDir: [0.5, 0.45, -0.4], sunSize: 0.06, sunPower: 30 },
};

export class Viewer {
  constructor(container) {
    this.container = container;
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.toneMapping = THREE.NeutralToneMapping;
    this.renderer.toneMappingExposure = 1.0;
    container.append(this.renderer.domElement);

    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(35, 1, 0.01, 100);
    this.camera.position.set(1.6, 1.1, 2.4);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    this.controls.autoRotateSpeed = 1.6;
    this.controls.target.set(0, 0.45, 0);
    this.controls.addEventListener('change', () => this.dirty = true);

    this.pmrem = new THREE.PMREMGenerator(this.renderer);
    this.envs = {};
    this.key = new THREE.DirectionalLight(0xffffff, 0.6);
    this.key.position.set(2, 3, 2);
    this.scene.add(this.key);

    this.grid = new THREE.GridHelper(4, 40, 0x3a3e48, 0x23262e);
    this.grid.material.transparent = true;
    this.grid.material.opacity = 0.55;
    this.scene.add(this.grid);
    this.shadow = blobShadow();
    this.scene.add(this.shadow);

    this.pivot = new THREE.Group();
    this.scene.add(this.pivot);
    this.model = null;
    this.mode = 'lit';
    this.loader = new GLTFLoader();
    this.loadToken = 0;
    this.shared = {
      clay: new THREE.MeshStandardMaterial({ color: 0xc4bdb2, roughness: 0.78, metalness: 0, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 }),
      normals: new THREE.MeshNormalMaterial(),
      wire: new THREE.MeshBasicMaterial({ color: 0xffc58a, wireframe: true, transparent: true, opacity: 0.28 }),
      uv: new THREE.MeshBasicMaterial({ map: checkerTexture() }),
    };
    this.setEnv('studio');

    new ResizeObserver(() => this.resize()).observe(container);
    this.resize();
    this.timer = new THREE.Timer();
    this.renderer.setAnimationLoop(() => this.tick());
  }

  resize() {
    const { clientWidth: w, clientHeight: h } = this.container;
    if (!w || !h) return;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.dirty = true;
  }

  tick() {
    this.timer.update();
    const moved = this.controls.update(this.timer.getDelta());
    // render on demand: the GPU is usually busy generating the next model
    if (moved || this.dirty || this.controls.autoRotate) {
      this.renderer.render(this.scene, this.camera);
      this.dirty = false;
    }
  }

  setEnv(name) {
    if (!this.envs[name]) {
      this.envs[name] = name === 'studio'
        ? this.pmrem.fromScene(new RoomEnvironment(), 0.04).texture
        : skyEnvironment(this.pmrem, SKIES[name]);
    }
    this.scene.environment = this.envs[name];
    this.key.intensity = name === 'night' ? 0.25 : name === 'overcast' ? 0.3 : 0.6;
    this.dirty = true;
  }

  setTurntable(on) { this.controls.autoRotate = on; this.dirty = true; }
  setGrid(on) { this.grid.visible = on; this.dirty = true; }

  /** Load a GLB; keepView leaves the camera alone (previews of the same job). */
  async load(url, { keepView = false } = {}) {
    const token = ++this.loadToken;
    const gltf = await this.loader.loadAsync(url);
    if (token !== this.loadToken) { disposeTree(gltf.scene); return null; }  // a newer load won
    this.clear();
    const root = gltf.scene;
    root.traverse((o) => {
      if (o.isMesh) {
        o.userData.orig = o.material;
        o.geometry.computeBoundingBox();
      }
    });
    // normalise: 1 unit tall/wide, standing on the grid, centred
    const box = new THREE.Box3().setFromObject(root);
    const size = box.getSize(new THREE.Vector3());
    const scale = 1 / Math.max(size.x, size.y, size.z, 1e-6);
    root.scale.setScalar(scale);
    const center = box.getCenter(new THREE.Vector3()).multiplyScalar(scale);
    root.position.set(-center.x, -box.min.y * scale, -center.z);
    this.pivot.add(root);
    this.model = root;
    this.dims = size;
    this.shadow.scale.set(size.x * scale * 1.5 + 0.1, size.z * scale * 1.5 + 0.1, 1);
    this.shadow.visible = true;
    this.applyMode();
    if (!keepView || !this.framed) this.frame();
    this.dirty = true;
    return { size };
  }

  clear() {
    if (this.model) {
      this.pivot.remove(this.model);
      disposeTree(this.model, new Set(Object.values(this.shared)));
    }
    this.model = null;
    this.shadow.visible = false;
    this.dirty = true;
  }

  frame() {
    const h = this.model ? new THREE.Box3().setFromObject(this.model).getSize(new THREE.Vector3()).y : 1;
    const dist = 1.15 / Math.sin(THREE.MathUtils.degToRad(this.camera.fov / 2)) * 0.62;
    const dir = new THREE.Vector3(0.62, 0.42, 1).normalize();
    this.controls.target.set(0, h / 2, 0);
    this.camera.position.copy(dir.multiplyScalar(dist)).add(this.controls.target);
    this.controls.update();
    this.framed = true;
    this.dirty = true;
  }

  setMode(mode) { this.mode = mode; this.applyMode(); }

  applyMode() {
    if (!this.model) return;
    this.model.traverse((o) => {
      if (!o.isMesh || o.userData.overlay) return;
      for (const c of [...o.children]) if (c.userData.overlay) o.remove(c);
      const orig = o.userData.orig;
      switch (this.mode) {
        case 'base':
          o.userData.basic ??= new THREE.MeshBasicMaterial({ map: orig.map || null, color: orig.map ? 0xffffff : orig.color, vertexColors: orig.vertexColors });
          o.material = o.userData.basic;
          break;
        case 'clay': o.material = this.shared.clay; break;
        case 'normals': o.material = this.shared.normals; break;
        case 'uv': o.material = this.shared.uv; break;
        case 'wire': {
          o.material = this.shared.clay;
          const overlay = new THREE.Mesh(o.geometry, this.shared.wire);
          overlay.userData.overlay = true;
          o.add(overlay);
          break;
        }
        default: o.material = orig;
      }
    });
    this.dirty = true;
  }

  /** PNG of the current view; square=true crops the centre (library thumbnails). */
  async snapshot({ square = false, size = 512 } = {}) {
    const grid = this.grid.visible;
    this.grid.visible = !square && grid;  // thumbnails float on the card, no floor grid
    this.renderer.render(this.scene, this.camera);
    this.grid.visible = grid;
    this.dirty = true;
    const src = this.renderer.domElement;
    let canvas = src;
    if (square) {
      const s = Math.min(src.width, src.height);
      canvas = document.createElement('canvas');
      canvas.width = canvas.height = size;
      canvas.getContext('2d').drawImage(src, (src.width - s) / 2, (src.height - s) / 2, s, s, 0, 0, size, size);
    }
    return new Promise((resolve) => canvas.toBlob(resolve, 'image/png'));
  }

  /** Record one full turntable orbit of the viewport as a WebM video. */
  async recordTurntable(seconds = 6) {
    if (!this.model || !window.MediaRecorder) throw new Error('video recording is not supported here');
    const stream = this.renderer.domElement.captureStream(30);
    const type = ['video/webm;codecs=vp9', 'video/webm'].find((t) => MediaRecorder.isTypeSupported(t));
    const rec = new MediaRecorder(stream, { mimeType: type, videoBitsPerSecond: 8e6 });
    const chunks = [];
    rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    const was = { on: this.controls.autoRotate, speed: this.controls.autoRotateSpeed };
    this.controls.autoRotateSpeed = 60 / seconds;  // OrbitControls: speed 2 = one orbit per 30 s
    this.controls.autoRotate = true;
    this.scene.background = studioBackdrop();  // the canvas is transparent; video has no alpha
    rec.start();
    await new Promise((r) => setTimeout(r, seconds * 1000));
    await new Promise((r) => { rec.onstop = r; rec.stop(); });
    Object.assign(this.controls, { autoRotate: was.on, autoRotateSpeed: was.speed });
    this.scene.background.dispose();
    this.scene.background = null;
    stream.getTracks().forEach((t) => t.stop());
    return new Blob(chunks, { type: 'video/webm' });
  }

  /** Export the loaded model in the browser: stl | ply | usdz | obj. Returns a Blob. */
  async exportAs(fmt) {
    if (!this.model) throw new Error('nothing loaded');
    const saved = this.mode;
    if (saved !== 'lit') this.setMode('lit');  // export the real materials, not the display ones
    try {
      if (fmt === 'stl') {
        const { STLExporter } = await import('three/addons/exporters/STLExporter.js');
        return new Blob([new STLExporter().parse(this.model, { binary: true })], { type: 'model/stl' });
      }
      if (fmt === 'ply') {
        const { PLYExporter } = await import('three/addons/exporters/PLYExporter.js');
        const data = await new Promise((res) => new PLYExporter().parse(this.model, res, { binary: true }));
        return new Blob([data], { type: 'application/octet-stream' });
      }
      if (fmt === 'usdz') {
        const { USDZExporter } = await import('three/addons/exporters/USDZExporter.js');
        const data = await new USDZExporter().parseAsync(this.model);
        return new Blob([data], { type: 'model/vnd.usdz+zip' });
      }
      if (fmt === 'obj') {
        const { OBJExporter } = await import('three/addons/exporters/OBJExporter.js');
        return new Blob([new OBJExporter().parse(this.model)], { type: 'text/plain' });
      }
      throw new Error(`unknown format ${fmt}`);
    } finally {
      if (saved !== 'lit') this.setMode(saved);
    }
  }
}

function disposeTree(root, keep = new Set()) {
  root.traverse((o) => {
    if (!o.isMesh || o.userData.overlay) return;
    o.geometry?.dispose();
    for (const m of [o.userData.orig, o.userData.basic, o.material]) {
      if (!m || keep.has(m)) continue;
      for (const v of Object.values(m)) if (v?.isTexture) v.dispose();
      m.dispose();
    }
  });
}

function studioBackdrop() {
  const c = document.createElement('canvas');
  c.width = c.height = 512;
  const g = c.getContext('2d');
  const grad = g.createRadialGradient(256, 200, 10, 256, 256, 380);
  grad.addColorStop(0, '#2a2d36');
  grad.addColorStop(0.5, '#1a1c22');
  grad.addColorStop(1, '#0f1013');
  g.fillStyle = grad;
  g.fillRect(0, 0, 512, 512);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

function blobShadow() {
  const c = document.createElement('canvas');
  c.width = c.height = 128;
  const g = c.getContext('2d');
  const grad = g.createRadialGradient(64, 64, 4, 64, 64, 64);
  grad.addColorStop(0, 'rgba(0,0,0,0.55)');
  grad.addColorStop(1, 'rgba(0,0,0,0)');
  g.fillStyle = grad;
  g.fillRect(0, 0, 128, 128);
  const mesh = new THREE.Mesh(new THREE.PlaneGeometry(1, 1),
    new THREE.MeshBasicMaterial({ map: new THREE.CanvasTexture(c), transparent: true, depthWrite: false }));
  mesh.rotation.x = -Math.PI / 2;
  mesh.position.y = 0.001;
  mesh.visible = false;
  return mesh;
}

function checkerTexture() {
  const c = document.createElement('canvas');
  c.width = c.height = 512;
  const g = c.getContext('2d');
  const n = 8, s = 512 / n;
  for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) {
    const hue = (x / n) * 300;
    g.fillStyle = (x + y) % 2 ? `hsl(${hue} 55% 62%)` : `hsl(${hue} 30% 28%)`;
    g.fillRect(x * s, y * s, s, s);
  }
  g.fillStyle = 'rgba(255,255,255,.85)';
  g.font = 'bold 22px sans-serif';
  for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) g.fillText(`${String.fromCharCode(65 + x)}${y + 1}`, x * s + 8, y * s + 28);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.repeat.set(4, 4);
  t.anisotropy = 8;
  return t;
}

function skyEnvironment(pmrem, sky) {
  const scene = new THREE.Scene();
  const mat = new THREE.ShaderMaterial({
    side: THREE.BackSide, depthWrite: false,
    uniforms: {
      top: { value: new THREE.Color(sky.top) }, horizon: { value: new THREE.Color(sky.horizon) },
      bottom: { value: new THREE.Color(sky.bottom) },
      sun: { value: new THREE.Vector3(...sky.sun).multiplyScalar(sky.sunPower) },
      sunDir: { value: new THREE.Vector3(...sky.sunDir).normalize() }, sunSize: { value: sky.sunSize },
    },
    vertexShader: 'varying vec3 vDir; void main(){ vDir = normalize(position); gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }',
    fragmentShader: `uniform vec3 top, horizon, bottom, sun, sunDir; uniform float sunSize; varying vec3 vDir;
      void main(){
        vec3 d = normalize(vDir); float h = d.y;
        vec3 c = h > 0.0 ? mix(horizon, top, pow(h, 0.55)) : mix(horizon, bottom, pow(-h, 0.35));
        float s = smoothstep(cos(sunSize), cos(sunSize * 0.4), dot(d, sunDir));
        gl_FragColor = vec4(c + sun * s, 1.0);
      }`,
  });
  scene.add(new THREE.Mesh(new THREE.SphereGeometry(10, 64, 32), mat));
  return pmrem.fromScene(scene, 0.02).texture;
}
