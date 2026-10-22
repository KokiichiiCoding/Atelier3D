"""Demo backend: walks a real job graph node by node with fake timings and fake outputs.

Used by ``--demo`` (try the studio without a GPU or downloads), by the tests and by the
browser end-to-end checks. Everything above the backend (graphs, progress, files, library)
is the real code path.
"""
from __future__ import annotations

import asyncio
import io
import math
import os
import random
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from . import glb
from .engine import Cancelled, Event
from .graphs import Graph

SPEED = float(os.environ.get("ATELIER_DEMO_SPEED", "1"))  # 0 = instant (tests)
_SLOW = {"KSampler": 1.2, "RemeshMesh": 0.5, "UnwrapMesh": 0.5, "BakeTextureFromVoxel": 0.5,
         "BakeAmbientOcclusion": 0.4, "RemoveBackground": 0.3, "MoGeInference": 0.3}


class DemoBackend:
    device = {"state": "ready", "device": "Demo mode (no GPU)", "device_type": "demo"}

    def __init__(self):
        self._files: dict[str, bytes] = {}
        self._uploads: dict[str, Path] = {}

    async def ensure(self):
        return None

    async def upload(self, path: Path) -> str:
        name = f"atelier/{path.name}"
        self._uploads[name] = path
        return name

    async def fetch(self, item: dict) -> bytes:
        return self._files[item["filename"]]

    async def run(self, graph: Graph, on_event, cancelled) -> dict[str, list[dict]]:
        outputs: dict[str, list[dict]] = {}
        seed = next((n["inputs"]["seed"] for n in graph.nodes.values()
                     if n["class_type"] == "KSampler"), 0)
        rng = random.Random(seed)
        batch = next((n["inputs"]["batch_size"] for n in graph.nodes.values()
                      if n["class_type"].startswith("Empty") and "Trellis2" not in n["class_type"]), 1)
        for nid, node in graph.nodes.items():
            if cancelled():
                raise Cancelled("cancelled")
            await on_event(Event("executing", node=nid))
            ct = node["class_type"]
            if ct == "KSampler":
                for i in range(1, 9):
                    await asyncio.sleep(0.12 * SPEED)
                    await on_event(Event("progress", node=nid, value=i / 8))
            else:
                await asyncio.sleep(_SLOW.get(ct, 0.04) * SPEED)
            name = graph.outputs.get(nid)
            if name:
                files = self._make(name, rng, batch)
                outputs[nid] = files
                await on_event(Event("executed", node=nid, files=files))
        return outputs

    def _store(self, data: bytes, ext: str) -> dict:
        fname = f"demo_{len(self._files):04d}.{ext}"
        self._files[fname] = data
        return {"filename": fname, "subfolder": "atelier", "type": "output"}

    def _make(self, name: str, rng: random.Random, batch: int) -> list[dict]:
        if name == "concept":
            return [self._store(_png(concept_image(rng)), "png") for _ in range(batch)]
        if name.startswith("input"):
            src = next(iter(self._uploads.values()), None)
            img = Image.open(src).convert("RGB") if src else concept_image(rng)
            return [self._store(_png(img.resize((1024, 1024))), "png")]
        if name.startswith("map_"):
            return [self._store(_png(_map_image(name, rng)), "png")]
        return [self._store(demo_glb(rng, coarse=name == "preview_structure"), "glb")]


def _png(img: Image.Image) -> bytes:
    b = io.BytesIO()
    img.save(b, "PNG")
    return b.getvalue()


def concept_image(rng: random.Random, size: int = 512) -> Image.Image:
    """A soft, centred 'object' on white, like the concept art Text to 3D asks for."""
    hue = rng.random()
    img = Image.new("RGB", (size, size), "white")
    d = ImageDraw.Draw(img)
    c = _hsv(hue, 0.55, 0.85)
    w = rng.uniform(0.25, 0.4) * size
    h = rng.uniform(0.3, 0.42) * size
    cx, cy = size / 2, size / 2
    d.ellipse([cx - w, cy - h, cx + w, cy + h], fill=c)
    d.ellipse([cx - w * 0.5, cy - h * 1.15, cx + w * 0.5, cy - h * 0.45], fill=_hsv(hue + 0.08, 0.5, 0.95))
    d.ellipse([cx - w * 0.6, cy - h * 0.6, cx - w * 0.1, cy - h * 0.1], fill=(255, 255, 255))
    return img.filter(ImageFilter.GaussianBlur(1.5))


def _hsv(h: float, s: float, v: float) -> tuple[int, int, int]:
    import colorsys
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
    return int(r * 255), int(g * 255), int(b * 255)


def _map_image(name: str, rng: random.Random, size: int = 256) -> Image.Image:
    if name == "map_base_color":
        return _texture(rng, size)
    if name == "map_normal":
        return Image.new("RGB", (size, size), (128, 128, 255))
    level = {"map_metallic": 20, "map_roughness": 160, "map_ao": 230}.get(name, 128)
    return Image.new("RGB", (size, size), (level,) * 3)


def _texture(rng: random.Random, size: int = 512) -> Image.Image:
    hue = rng.random()
    img = Image.new("RGB", (size, size))
    d = ImageDraw.Draw(img)
    bands = 9
    for i in range(bands):
        d.rectangle([0, i * size // bands, size, (i + 1) * size // bands],
                    fill=_hsv(hue + 0.04 * math.sin(i), 0.5 + 0.2 * (i % 2), 0.55 + 0.04 * i))
    for _ in range(40):
        x, y, r = rng.uniform(0, size), rng.uniform(0, size), rng.uniform(4, 18)
        d.ellipse([x - r, y - r, x + r, y + r], fill=_hsv(hue + 0.5, 0.4, 0.9))
    return img.filter(ImageFilter.GaussianBlur(0.8))


def demo_glb(rng: random.Random, coarse: bool = False) -> bytes:
    """A textured vase-like figure. ``coarse`` gives the blocky 'structure' preview."""
    rows = 9 if coarse else 40
    bulge = rng.uniform(0.25, 0.4)
    profile = []
    for j in range(rows):
        t = j / (rows - 1)
        r = 0.18 + bulge * math.sin(math.pi * t) ** 1.5 + 0.06 * math.sin(6 * math.pi * t)
        profile.append((max(r, 0.02) if 0 < j < rows - 1 else 0.001, t * 1.2 - 0.6))
    pos, nrm, uv, idx = glb.lathe(profile, segments=8 if coarse else 64)
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "m.glb"
        glb.write_textured(p, pos, nrm, uv, idx, _texture(rng), roughness=0.55)
        return p.read_bytes()
