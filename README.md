# Atelier3D

**Text or image in, a textured, game-ready 3D model out, made entirely on your own PC.**

Atelier3D is a local web studio in the spirit of Meshy, Tripo, Rodin and Hunyuan 3D Studio,
built on the newest open models. Drop in a picture (or describe something and pick a concept),
and it sculpts the shape, paints it with PBR materials, retopologises it, unwraps UVs, bakes
colour / metal / roughness / normal / AO maps and hands you a GLB, FBX, OBJ, USDZ, STL or PLY.
No accounts, no credits, no uploads.

```
text ──► concept art ──┐
                       ├──► cut-out + camera ──► structure ──► shape ──► detail ──► PBR paint ──► remesh · UVs · bake ──► GLB / FBX / OBJ / USDZ
image ─────────────────┘     (BiRefNet, MoGe)     └──────── Pixal3D or TRELLIS.2 ────────┘
```

## Highlights

- **State-of-the-art open models.** [Pixal3D](https://huggingface.co/TencentARC/Pixal3D)
  (Tencent ARC, SIGGRAPH 2026) by default, which keeps flat colours, logos and faces true to the
  picture, and Microsoft's [TRELLIS.2](https://huggingface.co/microsoft/TRELLIS.2-4B) as a
  second engine for photos and soft 3D renders. Both run as int8 builds sized for 8 GB GPUs.
- **Real PBR assets, not vertex-coloured blobs.** Every model is remeshed, decimated to the
  polycount you choose (30k game-ready to 700k film), UV-unwrapped and baked to 1K/2K/4K
  base colour, metallic, roughness, normal and ambient-occlusion maps.
- **Text to 3D like the big studios.** Describe it, pick a style (stylised, realistic, anime,
  toy, low poly, sculpture), get up to four concepts, then build the one you like. The prompt is
  shaped for 3D (one centred subject, plain background, even light) and an A-pose switch makes
  characters ready for rigging.
- **Multi-view.** Front, side and back views (a turnaround sheet works) become one model.
- **Watch it being made.** Live stage tracker with ETA, the blocked-out structure appears in
  the viewport within seconds, then the painted mesh, then the final baked model.
- **A proper viewport.** Lit PBR, base colour, clay, wireframe, normals and UV-checker views;
  studio / sunset / overcast / night lighting; turntable; screenshots. Keys `1`-`6`, `T`, `G`, `F`.
- **Exports for every tool.** GLB (original), FBX and textured OBJ through your Blender
  install, USDZ (AR Quick Look), STL (printing) and PLY in the browser, plus a zip of all maps.
- **Library.** Search, favourites, rename, delete, remix with a new seed, or redo in High
  quality with the same seed. Variations: build up to four seeds of one input in one click.
- **Queue.** Line up as many jobs as you like and keep working; jobs survive a page reload,
  queued ones survive a restart.
- **Model manager.** Download packs with resume and progress, or let "Find models on this PC"
  hard-link copies you already have (ComfyUI, Ka-myii...) without using any extra disk space.
- **Demo mode.** The whole studio runs on a fake engine with procedural models: no GPU, no
  downloads. Great for trying the UI or developing it.

## Status

Version 0.1. The studio, queue, library, exports and model manager are tested end to end on the
demo engine, and the Pixal3D graphs pass the real engine's validation. Real-GPU timings will be
added after the first runs on the target laptop (RTX 4070, 8 GB).

## Requirements

| | Generation | Demo mode |
|---|---|---|
| OS | Windows 10/11 (Linux: manual steps below, untested) | any |
| GPU | NVIDIA, **8 GB+ VRAM**, driver for CUDA 13 (R580+) | none |
| RAM | 16 GB | 2 GB |
| Disk | ~5 GB engine + **10 GB Pixal3D core**, optional packs below | ~100 MB |

## Quick start

1. Double-click **`Atelier3D.bat`**. The first run creates two Python environments, fetches the
   pinned engine (ComfyUI 0.42.1, PyTorch 2.14 CUDA 13) and opens the studio at
   <http://127.0.0.1:7870>. Later runs start in seconds.
2. On the **Models** tab, download **Pixal3D core** (10 GB), or press **Find models on this PC**
   if you already have the files in another app's folder on the same drive.
3. **Create**: drop an image (or paste one with Ctrl+V) and press **Generate 3D model**.

`Atelier3D.bat --demo` opens the studio without a GPU. The bat uses [uv](https://astral.sh/uv)
when it is installed (faster, and it shares wheels between environments) and falls back to
Python 3.12's venv + pip.

Manual / Linux:

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/setup_engine.py      # engine/ComfyUI + engine/.venv
.venv/bin/python -m atelier --open           # add --demo to skip the engine
```

## Model packs

| Pack | What it adds | Size | License |
|---|---|---|---|
| **Pixal3D core** (required) | image to 3D with PBR, BiRefNet cut-out, MoGe camera | 10.0 GB | MIT; DINOv3 encoder under Meta's DINOv3 License |
| Pixal3D multi-view | front/left/back/right views to one model | 5.6 GB | MIT |
| TRELLIS.2 engine | second image-to-3D engine | 5.3 GB | MIT |
| Z-Image Turbo | general-purpose concept art (8 steps) | 12.2 GB | Apache-2.0 |
| NetaYume Lumina | anime / illustration concept art | 10.6 GB | Apache-2.0 |

Any SDXL checkpoint dropped into `models/checkpoints/` also shows up as a concept model.
Weights come from Comfy-Org's repacks on Hugging Face (int8 builds made for ComfyUI).

## How it works

```
Atelier3D.bat
 └─ studio  (.venv: FastAPI, no torch)            http://127.0.0.1:7870
     ├─ web/            single-page studio, three.js 0.186 vendored (works offline)
     ├─ atelier/api.py  REST + server-sent events, uploads, library, exports
     ├─ atelier/jobs.py SQLite queue + library, one GPU worker
     ├─ atelier/pipeline.py  graph -> engine -> stage progress, live previews, files
     ├─ atelier/graphs.py    the ComfyUI graphs, built in code (Pixal3D/TRELLIS.2, concepts)
     └─ engine  (engine/.venv: torch 2.14 cu130)  private port 8199
         └─ ComfyUI 0.42.1, pinned, started and stopped by the studio
```

The engine is a stock ComfyUI release that Atelier3D installs into its own folder and drives
headless through its API: its native TRELLIS.2 / Pixal3D, MoGe, remesh, UV-unwrap and bake nodes
need no compiled extensions, and its memory manager is built to fit big models onto small GPUs. Your own ComfyUI install, if you have one, is never touched.

## Development

```bash
.venv\Scripts\python -m pytest tests -q          # 45 tests, GPU-free (demo engine)
engine\.venv\Scripts\python scripts\validate_graphs.py   # graphs vs the real engine, no GPU
.venv\Scripts\python -m atelier --demo --port 7871
```

`tests/e2e` drives the UI in Chromium (install `playwright`, then `playwright install chromium`
or set `ATELIER_E2E_BROWSER` to a Chromium executable). CI runs everything on Windows and
Ubuntu without a GPU. Read `AGENTS.md` before changing things.

## Credits

[Pixal3D](https://github.com/TencentARC/Pixal3D) (Tencent ARC / Tsinghua),
[TRELLIS.2](https://github.com/microsoft/TRELLIS.2) (Microsoft),
[MoGe](https://github.com/microsoft/MoGe), [BiRefNet](https://github.com/ZhengPeng7/BiRefNet),
[DINOv3](https://github.com/facebookresearch/dinov3) (Meta),
[Z-Image](https://github.com/Tongyi-MAI/Z-Image) (Alibaba Tongyi),
[NetaYume Lumina](https://huggingface.co/duongve/NetaYume-Lumina-Image-2.0),
[ComfyUI](https://github.com/Comfy-Org/ComfyUI) (GPL-3.0; installed separately, not bundled) and
Comfy-Org's model repacks and workflow templates, [three.js](https://threejs.org) (MIT),
[Blender](https://www.blender.org) for FBX/OBJ export.

Atelier3D's own code is MIT. Check each model's license before using its output commercially.
