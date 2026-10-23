# Atelier3D: agent handoff & working guide

Atelier3D is a local web studio that turns text or images into textured, PBR, game-ready 3D
models, like Meshy / Tripo / Rodin / Hunyuan 3D Studio but on the owner's own GPU. It is the
rebrand and rebuild of two earlier attempts: "Stunning-Modeler / Polii" (a Flask feature list
with placeholder models: Shap-E, MiDaS depth) and "Forge3D" (`C:\Users\Kokii\Forge3D`, never
pushed: FastAPI + the owner's ComfyUI Desktop, Hunyuan3D v2 shape, front-only projection paint).
Read this whole file before changing anything, and keep it current when you finish work.

## 1. Owner decisions (don't re-litigate)

- **Local first, high quality.** Open models only, no paid APIs, no per-generation cost.
- **Same standard as Ka-myii** (`C:\Users\Kokii\Ka-myii`): one-click bat, demo mode, GPU-free
  tests + CI, browser e2e, honest README, this handoff file.
- Target hardware: **RTX 4070 Laptop, 8 GB VRAM, 16 GB RAM, Windows 11**, C: has ~18 GB free
  (disk is tight: hard-link instead of copying, offer deletes, check space before downloads).
- Outputs must always be **textured** (Forge3D's rule): never hand the user a grey mesh.

## 2. Architecture decisions made in this rebuild (2026-10-22)

- **Engine = a pinned, private ComfyUI** (`engine/ComfyUI`, v0.42.1, own venv `engine/.venv`,
  port 8199), started/stopped by the studio and driven over HTTP + WebSocket. Reason: ComfyUI
  core now ships native, pure-PyTorch nodes for TRELLIS.2 / Pixal3D, MoGe, remesh, UV unwrap and
  PBR bakes (no nvdiffrast / custom_rasterizer / spconv builds), int8 model support and a memory
  manager for small GPUs. It is *not* the owner's ComfyUI Desktop (`Documents\ComfyUI`), which
  is old (0.3.x), used by other tools, and must not be modified.
- **Studio = FastAPI** (`atelier/`) in its own light venv (`.venv`, no torch), vanilla-JS UI in
  `web/` with three.js 0.186.1 vendored (offline, no build step).
- **Default model: Pixal3D int8** (Comfy-Org repack). TRELLIS.2 int8 is an optional second
  engine; Pixal3D multi-view optional. Hunyuan3D 2.x was dropped from the main path: ComfyUI's
  native Hunyuan nodes are shape-only (grey) and Hunyuan Paint needs compiled CUDA extensions.
- **Text to 3D = concepts first** (like Meshy/Tripo): text model draws 1-4 images with a
  3D-friendly prompt wrapper (`graphs.shape_prompt`), the user picks one, it becomes an image job.

## 3. Repository state

- Path `C:\Users\Kokii\Atelier3D`, local git repo. **Not on GitHub yet**: creating/pushing the
  repo is the owner's call. If they ask: public repos commit as
  `git -c user.name=KokiichiiCoding -c user.email=130875715+KokiichiiCoding@users.noreply.github.com commit`
  (never the personal gmail); push from PowerShell (Git Credential Manager), not the Bash tool.
- Gitignored and never committed: `.venv/`, `engine/`, `models/`, `outputs/`, `data/`.
- `models/` holds the Pixal3D core pack (verified complete by byte size, 9.95 GB).

## 4. Environment & commands

| What | How |
|---|---|
| Run (real) | `Atelier3D.bat` or `.venv\Scripts\python.exe -m atelier --open` (port 7870) |
| Run (demo, no GPU) | `.venv\Scripts\python.exe -m atelier --demo --port 7871` |
| Tests (no GPU) | `.venv\Scripts\python.exe -m pytest tests -q` (59 unit/API + 1 browser e2e) |
| Engine node self-test | `engine\.venv\Scripts\python.exe scripts\test_engine_nodes.py` (CPU, ~1 min: photo paint + camera vs Pixal3D's own projection) |
| e2e locally | set `ATELIER_E2E_BROWSER=C:\Users\Kokii\AppData\Local\ms-playwright\chromium-1243\chrome-win64\chrome.exe` |
| Graphs vs real engine | `engine\.venv\Scripts\python.exe scripts\validate_graphs.py` (no GPU, ~2-10 min: imports all nodes; `--write-fixture` adds schemas of new nodes to the test fixture) |
| (Re)install engine | `.venv\Scripts\python.exe scripts\setup_engine.py` (idempotent, stamp in engine/.venv) |
| Engine log | `data\engine.log`, or the Models tab > Log |
| Settings | env vars `ATELIER_PORT`, `ATELIER_ENGINE_PORT`, `ATELIER_MODELS`, `ATELIER_OUTPUTS`, `ATELIER_DATA`, `ATELIER_ENGINE_ARGS` (e.g. `--novram`), `ATELIER_BLENDER` |

`uv` is at `C:\Users\Kokii\.local\bin\uv.exe`; with `UV_LINK_MODE=hardlink` wheels already in
the uv cache cost no disk (that is how torch was installed).

## 5. Code map

```
atelier/config.py     paths/ports (env overridable)            atelier/models.py   packs, sizes, downloader (resume), hard-link finder
atelier/graphs.py     ComfyUI API graphs + quality presets      atelier/engine.py   ComfyUI process + client (ws events, upload, view)
atelier/pipeline.py   job runner: graph -> progress/previews     atelier/jobs.py     SQLite jobs/assets, worker, SSE bus
atelier/api.py        FastAPI routes, uploads, security          atelier/demo.py     fake backend: real graphs, fake outputs
atelier/exporters.py  Blender FBX / OBJ+MTL, textures zip       atelier/glb.py      GLB stats reader + tiny writer (demo)
web/js/app.js         create view, live events, asset panel      web/js/viewer.js    three.js viewport (modes, envs, exports)
web/js/pages.js       library + models pages                     web/js/ui.js, api.js helpers
scripts/setup_engine.py   engine install (pins live here)        scripts/validate_graphs.py   engine-side graph check
tests/fixtures/object_info.json   node schemas of the nodes we use, dumped from the pinned engine
engine_nodes/atelier3d/   our own ComfyUI node (AtelierProjectImage = photo paint); EngineProcess copies it
                          into engine/data/custom_nodes at every engine start. scripts/test_engine_nodes.py tests it
```

## 6. Hard-won facts

- **CUDA 13 torch is required for speed**: ComfyUI's `comfy_kitchen` disables its CUDA int8/fp8
  kernels when `torch.version.cuda < 13` (quant_ops.py), falling back to slow paths. The engine
  pins torch 2.14.1+cu130 (driver 596 supports it).
- **three.module.min.js imports `./three.core.js`** (not `.min`), and GLTFLoader imports
  `utils/SkeletonUtils.js`; both are vendored under those exact names.
- **Dynamic combo inputs** (RemeshMesh `sign_mode`, DecimateMesh `placement_mode`) are sent as
  `"sign_mode": "solid", "sign_mode.fill": 20.0, "sign_mode.qef": false`.
- The official Pixal3D template wires through `PreviewImage` passthrough outputs; in API graphs
  connect to the real source node instead. SaveGLB reports files under the `"3d"` ui key,
  SaveImage under `"images"`. Cached output nodes send no `executed` event: the pipeline also
  saves anything in the final history outputs.
- ComfyUI `LoadImage` drops alpha, so uploads with transparency are flattened onto neutral grey
  (128) before BiRefNet; uploads are re-encoded to PNG (≤2048 px, no metadata).
- The engine needs `custom_nodes/ input/ output/ temp/ user/` under its base dir or it crashes on
  start; `EngineProcess.ensure` creates them. The studio deletes engine output files after copying.
- With the GPU lost (see 7) torch reports no CUDA and ComfyUI runs on CPU: the pipeline refuses
  3D jobs with a clear message instead of running for hours.
- Windows' registry can map `.js` to text/plain; `api.py` registers JS and GLB MIME types.
- **Pixal3D camera (photo paint relies on it):** mesh frame is glTF (Y up); the single-image camera
  sits on +Z at `0.5 / tan(fov/2)` looking at the origin, horizontal fov = MoGe's; the multi-view
  rig uses fov 20 and 1.1x that distance, azimuth 90 = camera on +X ("left"), relative to the
  first connected view. `scripts/test_engine_nodes.py` checks `atelier3d.camera` against
  `comfy/ldm/trellis2/model.py::_project_points_to_image` to 0.000 px. TRELLIS.2 is not
  camera-conditioned, so it never gets photo paint.
- Photo paint blends `photo - blur(photo) + blur(bake rendered from that camera)` (sigma 12 px at
  1024) so the photo adds detail but not its lighting. It scores 4 camera hypotheses (front/back
  x upright/flipped) by silhouette IoU after a small 2D similarity fit and skips below 0.85: a
  blobby shape matched an upside-down photo at 0.88, so "first above the bar" was not enough.
- **Remesh density is ~4x the engine tooltip's "512 ~ 1M faces"** for big rounded shapes: a
  radius-0.42 sphere gave 8.28M faces at grid 1024. `graphs.remesh_resolution` still uses the
  tooltip rule (conservative, so 2M is always reachable on thin objects too).
- CPU timings of the 2M tail (this laptop's CPU, GPU lost, 2026-10-22): remesh 1024 6.2 min, decimate
  8.3M->2M 3.3 min, unwrap pec 4K 1.4 min (1,586 charts), normal bake 2K 3.6 min, peak 2.9 GB.
- FastAPI 0.159 reads a *missing* bool `Form` field as False (HTML checkbox rule), so switches
  that default to on (`photo_paint`) are taken as strings and parsed by `api._flag`.
- Editing files through bash heredocs: the tool call un-escapes `\\`, so `"\x89"` or `"\\n"`
  become real bytes / newlines. A failed `write_text` truncated a file once; use Edit for those.

## 7. Status (2026-10-23) and what is NOT verified yet

Done and verified:
- Studio, queue, library, model manager, exports (incl. Draco GLB via Blender), demo engine:
  59 pytest + browser e2e pass.
- All Pixal3D presets, the 2M-face + 4K + photo-paint graph and the NetaYume / novaAnimeXL concept
  graphs pass the **real engine's** prompt validation (`validate_graphs.py`); TRELLIS.2 /
  multi-view / Z-Image graphs are checked against node schemas offline (models not installed).
- 0.2 (2026-10-23): polycount up to 2M (remesh grid follows the target), photo paint
  (`engine_nodes/atelier3d`, CPU self-test passes: front error -84%, back untouched, bad photo
  refused, camera = Pixal3D's projection), UV padding 2-4 px, Rebuild dialog, Draco download.
- The 2M-face remesh/decimate/unwrap/normal-bake chain ran on the CPU with the real engine nodes
  (see section 6 for timings).
- Pixal3D core models downloaded and size-verified.
- Engine on torch 2.14.1+cu130: the comfy_kitchen CUDA backend is enabled (no "cu130" warning).
- The real **prepare stage ran on CPU** through the studio's own EngineClient (upload, ws events,
  outputs): BiRefNet cut-out + crop of a Ka-myii character was clean and correctly framed (~10 min
  on the starved CPU), MoGe finished (~13 min). DINOv3/NAF conditioning loaded and was still running
  after 50 min when it was stopped to spare the CPU, so Pixal3DConditioning has not completed yet.

**Not verified: a real generation on the GPU.** The owner's RTX 4070 dropped off the bus
("GPU is lost", nvlddmkm event 153, 2026-10-21 19:05) during a Diff-BGM run; it needs a reboot.
First thing next session after a reboot:
1. `Atelier3D.bat`, check the engine pill shows the 4070, generate one Draft and one Standard
   model from an image; record times and peak VRAM in the README.
2. If it runs out of memory: try `ATELIER_ENGINE_ARGS=--novram` or lower `upsample`/`remesh` in
   `graphs.QUALITY`; Comfy's own template uses upsample 1536 (our "high").
3. Link NetaYume from the owner's ComfyUI ("Find models on this PC") and run Text to 3D.
4. Try FBX/OBJ export through Blender 4.5 on a real model.
5. Run one model with photo paint on and one with it off (same seed: Rebuild keeps it) and compare
   the front; check `data/engine.log` for the `AtelierProjectImage painted ...` line (silhouette
   match should be > 0.9). Tune `detail_sigma` (graphs.py) if the photo's shading shows through.
6. Run a 2M / 4K High job; record time and VRAM. If the 1024 remesh or the bakes OOM, cap
   `REMESH_STEPS` at 768 (still ~4M faces on rounded shapes, see section 6).

## 8. Roadmap ideas (owner has not prioritised these yet)

- Auto-rigging + animation presets for characters (Polii had a distance-weight rigger; look at
  UniRig / SkinTokens / Puppeteer), then VRM export for VTubers (ties into Ka-myii).
- Retexture an uploaded mesh (needs a mesh->latent encoder or a multi-view paint model).
- Post-hoc LOD is done as "Rebuild" (same seed); it is only fast while ComfyUI's cache still
  holds the upstream nodes. A real tail-only job would need to save the shape latent / voxels.
- Quad remesh for DCC users (Tripo offers it); no compile-free option yet.
- Hunyuan3D 2.1 / Hunyuan Part when they have native, compile-free ComfyUI nodes.
- Optional free cloud fallback (Hugging Face Spaces) when the local GPU is unavailable.
