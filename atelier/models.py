"""Model packs: what each feature needs, where it lives, and how it gets onto the disk.

Files keep ComfyUI's folder layout under ``models/`` because the engine reads that folder
directly (``--models-directory``). A pack can be downloaded from Hugging Face or, when the
same file already sits in another app's folder on this drive, hard-linked for free.
"""
from __future__ import annotations

import asyncio
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

import httpx

from .config import CFG

HF = "https://huggingface.co"


@dataclass(frozen=True)
class ModelFile:
    folder: str     # ComfyUI models sub-folder
    name: str
    repo: str
    path: str       # path inside the Hugging Face repo
    size: int       # bytes, used to verify downloads and links

    @property
    def url(self) -> str:
        return f"{HF}/{self.repo}/resolve/main/{self.path}"

    def dest(self, models_dir: Path | None = None) -> Path:
        return (models_dir or CFG.models_dir) / self.folder / self.name


@dataclass(frozen=True)
class Pack:
    id: str
    title: str
    blurb: str
    license: str
    files: tuple[ModelFile, ...]
    required: bool = False

    @property
    def size(self) -> int:
        return sum(f.size for f in self.files)


_DINO = ModelFile("clip_vision", "dino_v3_L_naf_fp32.safetensors", "Comfy-Org/Pixal3D",
                  "clip_vision/dino_v3_L_naf_fp32.safetensors", 1215214176)
_SHAPE_VAE = ModelFile("vae", "trellis_2_shape_vae_bf16.safetensors", "Comfy-Org/Pixal3D",
                       "vae/trellis_2_shape_vae_bf16.safetensors", 1095844024)
_TEX_VAE = ModelFile("vae", "trellis_2_texture_vae_bf16.safetensors", "Comfy-Org/Pixal3D",
                     "vae/trellis_2_texture_vae_bf16.safetensors", 948461364)

PACKS: dict[str, Pack] = {p.id: p for p in [
    Pack("pixal3d", "Pixal3D core",
         "Image to 3D with PBR textures (Tencent ARC, SIGGRAPH 2026), plus BiRefNet background "
         "removal and MoGe camera estimation. Required.",
         "MIT (DINOv3 encoder: Meta DINOv3 License)", (
             ModelFile("diffusion_models", "pixal3d_int8_convrot.safetensors", "Comfy-Org/Pixal3D",
                       "diffusion_models/pixal3d_int8_convrot.safetensors", 5584555824),
             _DINO, _SHAPE_VAE, _TEX_VAE,
             ModelFile("geometry_estimation", "moge_2_vitl_normal_fp16.safetensors", "Comfy-Org/MoGe",
                       "geometry_estimation/moge_2_vitl_normal_fp16.safetensors", 661859924),
             ModelFile("background_removal", "birefnet.safetensors", "Comfy-Org/BiRefNet",
                       "background_removal/birefnet.safetensors", 444473596),
         ), required=True),
    Pack("multiview", "Pixal3D multi-view",
         "Build one model from front, side and back views, such as a character turnaround sheet.",
         "MIT", (
             ModelFile("diffusion_models", "pixal3d_multiview_int8_convrot.safetensors",
                       "Comfy-Org/Pixal3D",
                       "diffusion_models/pixal3d_multiview_int8_convrot.safetensors", 5584555824),
         )),
    Pack("trellis2", "TRELLIS.2 engine",
         "Microsoft's TRELLIS.2 as a second image-to-3D engine. Often better on photos and "
         "softly lit 3D renders; Pixal3D holds flat colours and logos better.",
         "MIT", (
             ModelFile("diffusion_models", "trellis_2_int8_convrot.safetensors", "Comfy-Org/TRELLIS.2",
                       "diffusion_models/trellis_2_int8_convrot.safetensors", 5253048192),
         )),
    Pack("netayume", "NetaYume Lumina (text to image)",
         "Draws concept art for Text to 3D. Best at anime, illustration and stylised game art.",
         "Apache-2.0", (
             ModelFile("checkpoints", "NetaYumev35_pretrained_all_in_one.safetensors",
                       "duongve/NetaYume-Lumina-Image-2.0",
                       "NetaYumev35_pretrained_all_in_one.safetensors", 10620231237),
         )),
    Pack("zimage", "Z-Image Turbo (text to image)",
         "Fast, general-purpose concept art for Text to 3D: photoreal products, props and "
         "realistic characters in 8 steps.",
         "Apache-2.0", (
             ModelFile("diffusion_models", "z_image_turbo_int8_convrot.safetensors",
                       "Comfy-Org/z_image_turbo",
                       "split_files/diffusion_models/z_image_turbo_int8_convrot.safetensors", 6201001296),
             ModelFile("text_encoders", "qwen_3_4b_fp8_mixed.safetensors", "Comfy-Org/z_image_turbo",
                       "split_files/text_encoders/qwen_3_4b_fp8_mixed.safetensors", 5631994051),
             ModelFile("vae", "ae.safetensors", "Comfy-Org/z_image_turbo",
                       "split_files/vae/ae.safetensors", 335304388),
         )),
]}


def file_present(f: ModelFile) -> bool:
    p = f.dest()
    return p.is_file() and p.stat().st_size == f.size


def installed(pack_id: str) -> bool:
    return pack_id in PACKS and all(file_present(f) for f in PACKS[pack_id].files)


def sdxl_checkpoints() -> list[str]:
    """Any other checkpoint the user dropped into models/checkpoints is treated as SDXL."""
    known = {f.name for p in PACKS.values() for f in p.files}
    d = CFG.models_dir / "checkpoints"
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.glob("*.safetensors")
                  if p.name not in known and "hunyuan" not in p.name.lower())


def text_models() -> list[dict]:
    """Text-to-image models usable for concept art, best general-purpose first."""
    out = []
    if installed("zimage"):
        out.append({"id": "zimage", "label": "Z-Image Turbo", "family": "zimage"})
    if installed("netayume"):
        out.append({"id": "netayume", "label": "NetaYume Lumina", "family": "netayume"})
    out += [{"id": f"sdxl:{n}", "label": Path(n).stem, "family": "sdxl"} for n in sdxl_checkpoints()]
    return out


def disk_free(path: Path | None = None) -> int:
    p = path or CFG.models_dir
    while not p.exists():
        p = p.parent
    return shutil.disk_usage(p).free


def status(downloads: "Downloader | None" = None) -> list[dict]:
    rows = []
    for p in PACKS.values():
        have = sum(f.size for f in p.files if file_present(f))
        row = {"id": p.id, "title": p.title, "blurb": p.blurb, "license": p.license,
               "required": p.required, "size": p.size, "have": have,
               "installed": have == p.size, "files": [f.name for f in p.files]}
        if downloads:
            row.update(downloads.state_of(p.id))
        rows.append(row)
    return rows


# ---------------------------------------------------------------- linking local copies

def _default_search_roots() -> list[Path]:
    home = Path.home()
    return [home / "Documents" / "ComfyUI" / "models",
            home / "ComfyUI" / "models",
            home / "Ka-myii" / "models",
            home / "AppData" / "Local" / "Programs" / "ComfyUI" / "resources" / "ComfyUI" / "models"]


def link_existing(roots: list[Path] | None = None) -> list[dict]:
    """Hard-link pack files (and SDXL checkpoints) that already exist elsewhere on the drive.

    A hard link costs no disk space and leaves the original untouched. Files on another
    drive cannot be hard-linked and are reported instead of copied.
    """
    roots = [r for r in (roots or _default_search_roots()) if r.is_dir()]
    done: list[dict] = []
    wanted = {f.name: f for p in PACKS.values() for f in p.files}
    for root in roots:
        for src in root.rglob("*.safetensors"):
            f = wanted.get(src.name)
            if f is not None:
                if file_present(f) or src.stat().st_size != f.size:
                    continue
                dest = f.dest()
            elif src.parent.name == "checkpoints" and _looks_like_sdxl(src):
                dest = CFG.models_dir / "checkpoints" / src.name
                if dest.exists():
                    continue
            else:
                continue
            done.append(_link(src, dest))
    return done


def _looks_like_sdxl(p: Path) -> bool:
    name = p.name.lower()
    return (p.stat().st_size > 6_000_000_000 and "hunyuan" not in name
            and any(k in name for k in ("xl", "illustrious", "pony", "noob")))


def _link(src: Path, dest: Path) -> dict:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dest)
        return {"file": dest.name, "from": str(src), "linked": True}
    except OSError as e:
        return {"file": dest.name, "from": str(src), "linked": False, "error": str(e)}


def delete_pack(pack_id: str) -> int:
    """Remove a pack's files. Files another pack still needs are kept. Returns bytes freed
    (a hard-linked file frees nothing while its other copy exists, but is still removed)."""
    keep = {f.name for p in PACKS.values() if p.id != pack_id for f in p.files if installed(p.id)}
    freed = 0
    for f in PACKS[pack_id].files:
        p = f.dest()
        if p.exists() and f.name not in keep:
            freed += p.stat().st_size
            p.unlink()
    return freed


# ---------------------------------------------------------------- downloads

Progress = Callable[[dict], Awaitable[None]]


class Downloader:
    """One download at a time (the network, not the CPU, is the bottleneck), resumable."""

    def __init__(self, publish: Progress | None = None):
        self.publish = publish
        self._queue: list[str] = []
        self._task: asyncio.Task | None = None
        self._state: dict[str, dict] = {}
        self._cancel: set[str] = set()

    def state_of(self, pack_id: str) -> dict:
        return self._state.get(pack_id, {})

    async def request(self, pack_id: str):
        if pack_id not in PACKS:
            raise KeyError(pack_id)
        if installed(pack_id) or pack_id in self._queue:
            return
        self._cancel.discard(pack_id)
        self._queue.append(pack_id)
        self._state[pack_id] = {"downloading": True, "done": 0, "error": None}
        await self._emit(pack_id)
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    def cancel(self, pack_id: str):
        self._cancel.add(pack_id)
        if pack_id in self._queue[1:]:
            self._queue.remove(pack_id)
            self._state.pop(pack_id, None)

    async def _emit(self, pack_id: str):
        if self.publish:
            await self.publish({"type": "model", "pack": pack_id, **self._state.get(pack_id, {})})

    async def _run(self):
        while self._queue:
            pack_id = self._queue[0]
            try:
                await self._fetch_pack(pack_id)
                self._state[pack_id] = {"downloading": False, "done": PACKS[pack_id].size,
                                        "error": None}
            except _Cancelled:
                self._state[pack_id] = {"downloading": False, "error": None, "cancelled": True}
            except Exception as e:  # network, disk full, size mismatch
                self._state[pack_id] = {"downloading": False, "error": f"{type(e).__name__}: {e}"}
            self._queue.pop(0)
            await self._emit(pack_id)

    async def _fetch_pack(self, pack_id: str):
        pack = PACKS[pack_id]
        missing = [f for f in pack.files if not file_present(f)]
        need = sum(f.size - _part_size(f) for f in missing)
        if need > disk_free() - 2_000_000_000:  # keep 2 GB for the OS
            raise OSError(f"not enough disk space: {need / 1e9:.1f} GB needed, "
                          f"{disk_free() / 1e9:.1f} GB free")
        base = sum(f.size for f in pack.files if file_present(f))
        async with httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(30, read=120)) as c:
            for f in missing:
                async for got in _fetch_file(c, f):
                    if pack_id in self._cancel:
                        raise _Cancelled()
                    self._state[pack_id]["done"] = base + got
                    await self._emit(pack_id)
                base += f.size


class _Cancelled(Exception):
    pass


def _part_size(f: ModelFile) -> int:
    part = f.dest().with_name(f.name + ".part")
    return part.stat().st_size if part.exists() else 0


async def _fetch_file(client: httpx.AsyncClient, f: ModelFile):
    """Yield bytes-so-far while streaming ``f`` to ``<name>.part``; rename when complete."""
    dest = f.dest()
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(f.name + ".part")
    have = _part_size(f)
    headers = {"Range": f"bytes={have}-"} if have else {}
    last = 0
    async with client.stream("GET", f.url, headers=headers) as r:
        if r.status_code == 200:
            have = 0  # server ignored the range: start over
        elif r.status_code != 206:
            r.raise_for_status()
        with open(part, "ab" if have else "wb") as out:
            async for chunk in r.aiter_bytes(1 << 20):
                out.write(chunk)
                have += len(chunk)
                if have - last >= 8 << 20 or have == f.size:
                    last = have
                    yield have
    if have != f.size:
        raise IOError(f"{f.name}: got {have} bytes, expected {f.size}")
    os.replace(part, dest)
