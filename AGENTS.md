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
| Tests (no GPU) | `.venv\Scripts\python.exe -m pytest tests -q` (44 unit/API + 1 browser e2e) |
| e2e locally | set `ATELIER_E2E_BROWSER=C:\Users\Kokii\AppData\Local\ms-playwright\chromium-1243\chrome-win64\chrome.exe` |
| Graphs vs real engine | `engine\.venv\Scripts\python.exe scripts\validate_graphs.py` (no GPU, ~2-10 min: imports all nodes) |
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

## 7. Status (2026-10-22) and what is NOT verified yet

Done and verified:
- Studio, queue, library, model manager, exports, demo engine: 44 pytest + browser e2e pass.
- All three Pixal3D quality presets pass the **real engine's** prompt validation
  (`validate_graphs.py`); TRELLIS.2 / multi-view / concept graphs are checked against the
  engine's node schemas offline (their models are not installed).
- Pixal3D core models downloaded and size-verified.

**Not verified: a real generation on the GPU.** The owner's RTX 4070 dropped off the bus
("GPU is lost", nvlddmkm event 153, 2026-10-21 19:05) during a Diff-BGM run; it needs a reboot.
First thing next session after a reboot:
1. `Atelier3D.bat`, check the engine pill shows the 4070, generate one Draft and one Standard
   model from an image; record times and peak VRAM in the README.
2. If it runs out of memory: try `ATELIER_ENGINE_ARGS=--novram` or lower `upsample`/`remesh` in
   `graphs.QUALITY`; Comfy's own template uses upsample 1536 (our "high").
3. Link NetaYume from the owner's ComfyUI ("Find models on this PC") and run Text to 3D.
4. Try FBX/OBJ export through Blender 4.5 on a real model.

## 8. Roadmap ideas (owner has not prioritised these yet)

- Auto-rigging + animation presets for characters (Polii had a distance-weight rigger; look at
  UniRig / SkinTokens / Puppeteer), then VRM export for VTubers (ties into Ka-myii).
- Retexture an uploaded mesh (needs a mesh->latent encoder or a multi-view paint model).
- Post-hoc LOD: re-run only the remesh/bake tail at a new polycount (ComfyUI caches upstream).
- Hunyuan3D 2.1 / Hunyuan Part when they have native, compile-free ComfyUI nodes.
- Optional free cloud fallback (Hugging Face Spaces) when the local GPU is unavailable.
