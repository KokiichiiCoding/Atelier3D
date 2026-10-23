"""Job execution: build the graph, feed the engine, turn its events into stage progress and
live previews, and file the results into the asset library."""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from . import glb, graphs
from .config import CFG
from .engine import EngineClient, EngineProcess, Event, as_dict
from .jobs import Context

# relative cost of a node inside its stage (samplers dominate)
_NODE_COST = {"KSampler": 10, "RemeshMesh": 3, "UnwrapMesh": 3, "BakeTextureFromVoxel": 3,
              "BakeAmbientOcclusion": 3, "BakeNormalMapFromMesh": 2, "MoGeInference": 2,
              "RemoveBackground": 2, "UNETLoader": 2, "CheckpointLoaderSimple": 3,
              "VaeDecodeShapeTrellis": 2, "VaeDecodeTextureTrellis": 2, "VAEDecode": 2,
              "AtelierProjectImage": 2}
PREVIEWS = {"input": "input", "preview_structure": "structure", "preview_painted": "painted"}
MAP_FILES = {"map_base_color": "maps/base_color.png", "map_metallic": "maps/metallic.png",
             "map_roughness": "maps/roughness.png", "map_normal": "maps/normal.png",
             "map_ao": "maps/ao.png"}
NO_GPU = ("The engine is running on the CPU because no CUDA GPU is available, so one model would "
          "take many hours. If this PC has an NVIDIA GPU, reboot it (Windows can lose the GPU after "
          "a driver crash) or update the NVIDIA driver, then try again.")


class PipelineError(RuntimeError):
    pass


class EngineBackend:
    """The real engine behind the same small interface as ``demo.DemoBackend``."""

    def __init__(self, proc: EngineProcess):
        self.proc = proc
        self.client: EngineClient = proc.client

    async def ensure(self):
        await self.proc.ensure()
        if (self.proc.info().get("device_type") or "").lower() == "cpu":
            raise PipelineError(NO_GPU)

    async def upload(self, path: Path) -> str:
        return await self.client.upload(path)

    async def run(self, graph: graphs.Graph, on_event, cancelled) -> dict[str, list[dict]]:
        return as_dict(await self.client.run(graph.prompt(), on_event, cancelled))

    async def fetch(self, item: dict) -> bytes:
        data = await self.client.download(item)
        # the studio keeps its own copy; don't let the engine's output folder fill the disk
        p = CFG.engine_data / item.get("type", "output") / item.get("subfolder", "") / item["filename"]
        p.unlink(missing_ok=True)
        return data


class Tracker:
    """Map engine node events to weighted stage progress and an ETA."""

    def __init__(self, graph: graphs.Graph, stages: list[tuple[str, str, float]]):
        self.stages = stages
        self.graph = graph
        self.cost = {nid: _NODE_COST.get(n["class_type"], 1) for nid, n in graph.nodes.items()}
        self.total = {s: 0.0 for s, _, _ in stages}
        for nid, s in graph.stage_of.items():
            self.total[s] += self.cost[nid]
        self.done: set[str] = set()
        self.current: str | None = None
        self.frac = 0.0
        self.t0 = time.monotonic()

    def feed(self, ev: Event):
        if ev.kind == "executing":
            if self.current:
                self.done.add(self.current)
            self.current, self.frac = ev.node, 0.0
        elif ev.kind == "progress" and ev.node == self.current:
            self.frac = ev.value
        elif ev.kind == "cached":
            self.done.update(ev.nodes or [])
        elif ev.kind == "executed" and ev.node:
            self.done.add(ev.node)

    def stage_fraction(self, stage: str) -> float:
        got = sum(self.cost[n] for n in self.done if self.graph.stage_of.get(n) == stage)
        if self.current and self.current not in self.done and self.graph.stage_of.get(self.current) == stage:
            got += self.cost[self.current] * self.frac
        return min(1.0, got / self.total[stage]) if self.total[stage] else 1.0

    def snapshot(self) -> dict:
        cur = self.graph.stage_of.get(self.current or "", self.stages[0][0])
        rows, overall = [], 0.0
        for sid, label, w in self.stages:
            f = self.stage_fraction(sid)
            overall += w * f
            state = "done" if f >= 1.0 else "active" if sid == cur else "pending"
            rows.append({"id": sid, "label": label, "state": state, "fraction": round(f, 3)})
        elapsed = time.monotonic() - self.t0
        eta = elapsed * (1 - overall) / overall if overall > 0.05 else None
        label = next(l for s, l, _ in self.stages if s == cur)
        return {"stage": cur, "label": label, "overall": round(overall, 4), "stages": rows,
                "elapsed": round(elapsed, 1), "eta": round(eta, 1) if eta is not None else None}


def file_url(rel: str) -> str:
    return f"/files/{rel.replace(chr(92), '/')}"


async def _drive(graph: graphs.Graph, stages, ctx: Context, backend, save) -> None:
    """Run ``graph``, publishing throttled progress and saving outputs as they appear."""
    tracker = Tracker(graph, stages)
    saved: set[str] = set()
    last = {"t": 0.0, "stage": None}

    async def store(nid: str, files: list[dict]):
        if nid in saved or not files or nid not in graph.outputs:
            return
        saved.add(nid)
        await save(graph.outputs[nid], [await backend.fetch(f) for f in files])

    async def on_event(ev: Event):
        tracker.feed(ev)
        if ev.kind == "executed":
            await store(ev.node, ev.files or [])
        snap = tracker.snapshot()
        now = time.monotonic()
        if now - last["t"] > 0.3 or snap["stage"] != last["stage"]:
            last.update(t=now, stage=snap["stage"])
            await ctx.progress(**snap)

    outputs = await backend.run(graph, on_event, lambda: ctx.cancelled)
    for nid, files in outputs.items():  # cached output nodes send no "executed" event
        await store(nid, files)


async def run_job(job: dict, ctx: Context, backend) -> dict:
    if job["kind"] == "concepts":
        return await _concepts(job, ctx, backend)
    if job["kind"] in ("image3d", "multiview3d"):
        return await _model(job, ctx, backend)
    raise PipelineError(f"unknown job kind {job['kind']}")


async def _model(job: dict, ctx: Context, backend) -> dict:
    p, aid = job["params"], job["id"]
    adir = CFG.outputs_dir / aid
    (adir / "maps").mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    await ctx.progress(stage="prepare", label="Starting the engine", overall=0.0, stages=[], eta=None)
    await backend.ensure()
    common = dict(seed=int(p["seed"]), quality=p["quality"], faces=int(p["faces"]),
                  texture=int(p["texture"]), photo_paint=bool(p.get("photo_paint", True)))
    if job["kind"] == "multiview3d":
        views = {v: await backend.upload(Path(path)) for v, path in p["views"].items()}
        graph = graphs.image_to_3d(views=views, **common)
    else:
        graph = graphs.image_to_3d(await backend.upload(Path(p["image"])), engine=p["engine"], **common)

    async def save(name: str, blobs: list[bytes]):
        rel = MAP_FILES.get(name) or (f"{name}.png" if name.startswith("input") else f"{name}.glb")
        (adir / rel).write_bytes(blobs[0])
        if name in PREVIEWS or name.startswith("input_front"):
            await ctx.preview(PREVIEWS.get(name, "input"), file_url(f"{aid}/{rel}"))

    await _drive(graph, graphs.STAGES_3D, ctx, backend, save)
    model = adir / "model.glb"
    if not model.exists():
        raise PipelineError("the engine finished without producing a model")
    thumb_src = adir / ("input.png" if job["kind"] == "image3d" else "input_front.png")
    if not thumb_src.exists():
        thumb_src = next(adir.glob("input*.png"), None)
    if thumb_src:
        shutil.copyfile(thumb_src, adir / "thumb.png")
    meta = {"params": public_params(p), "stats": glb.stats(model), "seconds": round(time.time() - t0),
            "maps": [Path(r).stem for r in MAP_FILES.values() if (adir / r).exists()],
            "source": p.get("source")}
    (adir / "asset.json").write_text(json.dumps(meta, indent=1))
    name = p.get("name") or _default_name(p)
    asset = ctx.worker.store.add_asset(aid, job["id"], name, job["kind"], meta)
    await ctx.worker.bus.publish({"type": "asset", **public_asset(asset)})
    return {"asset": aid}


async def _concepts(job: dict, ctx: Context, backend) -> dict:
    p, jid = job["params"], job["id"]
    out = CFG.outputs_dir / "_concepts" / jid
    out.mkdir(parents=True, exist_ok=True)
    await ctx.progress(stage="load", label="Starting the engine", overall=0.0, stages=[], eta=None)
    await backend.ensure()
    prompt, negative = graphs.shape_prompt(p["prompt"], p.get("style", "stylized"),
                                           bool(p.get("character")))
    graph = graphs.concepts(p["model"], prompt, negative, seed=int(p["seed"]), count=int(p["count"]))
    urls: list[str] = []

    async def save(name: str, blobs: list[bytes]):
        for i, b in enumerate(blobs):
            (out / f"concept_{i}.png").write_bytes(b)
            urls.append(file_url(f"_concepts/{jid}/concept_{i}.png"))

    await _drive(graph, graphs.STAGES_CONCEPTS, ctx, backend, save)
    if not urls:
        raise PipelineError("the engine finished without drawing anything")
    return {"images": urls, "prompt": prompt}


def concept_path(job: dict, index: int) -> Path:
    p = CFG.outputs_dir / "_concepts" / job["id"] / f"concept_{int(index)}.png"
    if not p.is_file():
        raise FileNotFoundError(p.name)
    return p


def public_asset(a: dict) -> dict:
    """Asset as the UI sees it: URLs for the model, thumbnail, maps and previews."""
    base = f"/files/{a['id']}"
    thumb = CFG.outputs_dir / a["id"] / "thumb.png"
    stamp = int(thumb.stat().st_mtime) if thumb.exists() else 0
    return {**a, "url": f"{base}/model.glb", "thumb": f"{base}/thumb.png?v={stamp}",
            "maps": {m: f"{base}/maps/{m}.png" for m in a.get("maps", [])},
            "previews": {k: f"{base}/{f}" for k, f in (("structure", "preview_structure.glb"),
                                                      ("painted", "preview_painted.glb"))
                         if (CFG.outputs_dir / a["id"] / f).exists()}}


def public_params(p: dict) -> dict:
    """Params worth showing/remixing (no local file paths)."""
    return {k: v for k, v in p.items() if k not in ("image", "views")}


def _default_name(p: dict) -> str:
    src = p.get("source") or {}
    if src.get("prompt"):
        words = src["prompt"].strip().split()
        return " ".join(words[:6])[:48] or "Untitled"
    if src.get("filename"):
        return Path(src["filename"]).stem[:48]
    return "Untitled"
