"""Atelier3D web API (FastAPI): generation, jobs, library, models, engine, live events."""
from __future__ import annotations

import asyncio
import io
import json
import mimetypes
import random
import re
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps, UnidentifiedImageError

from . import __version__, exporters, graphs, models
from .config import CFG, ROOT
from .demo import DemoBackend
from .engine import EngineProcess, log_tail
from .jobs import FINAL, Bus, Store, Worker, new_id
from .pipeline import EngineBackend, concept_path, public_asset, run_job

WEB = ROOT / "web"
mimetypes.add_type("model/gltf-binary", ".glb")
mimetypes.add_type("text/javascript", ".js")  # Windows registries sometimes map .js to text/plain
_ID = re.compile(r"^[0-9a-f]{12}$")
_LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]"}
MAX_SEED = 2**31 - 1


def _flag(v) -> bool:
    """A form/JSON switch. (FastAPI reads a *missing* bool form field as False, like an HTML
    checkbox, so switches that default to on arrive as strings.)"""
    return v.strip().lower() not in ("false", "0", "off", "no", "") if isinstance(v, str) else bool(v)


def _bad(msg: str, code: int = 400):
    raise HTTPException(code, msg)


def create_app() -> FastAPI:
    store = Store(CFG.db_path)
    bus = Bus()
    engine = EngineProcess(publish=bus.publish)
    downloads = models.Downloader(publish=bus.publish)
    backend = DemoBackend() if CFG.demo else EngineBackend(engine)
    worker = Worker(store, bus, lambda job, ctx: run_job(job, ctx, backend))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        for d in (CFG.outputs_dir, CFG.uploads_dir, CFG.models_dir):
            d.mkdir(parents=True, exist_ok=True)
        worker.start()
        warm = None
        if not CFG.demo and EngineProcess.installed():
            warm = asyncio.create_task(_quiet(engine.ensure()))  # warm up while the UI loads
        yield
        if warm:
            warm.cancel()
        await worker.stop()
        engine.stop()

    app = FastAPI(title="Atelier3D", version=__version__, lifespan=lifespan)
    app.state.store, app.state.worker, app.state.engine = store, worker, engine

    @app.middleware("http")
    async def same_origin_only(request: Request, call_next):
        # The studio binds to loopback: refuse other Host names (DNS rebinding) and
        # state-changing requests sent by other websites (CSRF).
        if CFG.host in _LOOPBACK:
            host = (request.headers.get("host") or "").rsplit(":", 1)[0]
            if host not in _LOOPBACK:
                return JSONResponse({"detail": "forbidden host"}, status_code=403)
            origin = request.headers.get("origin")
            if request.method not in ("GET", "HEAD") and origin and origin != "null":
                if urlsplit(origin).hostname not in _LOOPBACK:
                    return JSONResponse({"detail": "cross-site request refused"}, status_code=403)
        response = await call_next(request)
        # revalidate the UI's files (ETag) so an updated studio never runs stale JS/CSS
        response.headers.setdefault("Cache-Control", "no-cache")
        return response

    # ------------------------------------------------------------ status & events

    def capabilities() -> dict:
        if CFG.demo:
            return {"image3d": True, "multiview": True, "trellis2": True, "text3d": True}
        core = models.installed("pixal3d")
        return {"image3d": core, "multiview": core and models.installed("multiview"),
                "trellis2": core and models.installed("trellis2"),
                "text3d": core and bool(models.text_models())}

    def text_models() -> list[dict]:
        if CFG.demo:
            return [{"id": "netayume", "label": "Demo sketcher", "family": "netayume"}]
        return models.text_models()

    def status() -> dict:
        info = engine.info() if not CFG.demo else DemoBackend.device
        return {"version": __version__, "demo": CFG.demo, "engine": info,
                "capabilities": capabilities(), "text_models": text_models(),
                "blender": bool(exporters.find_blender()), "disk_free": models.disk_free(),
                "queue": {"current": worker.current, "waiting": worker.queue.qsize()},
                "options": {"quality": list(graphs.QUALITY), "texture": list(graphs.TEXTURE_SIZES),
                            "faces": list(graphs.FACE_LIMITS), "styles": list(graphs.STYLES),
                            "engines": list(graphs.ENGINES)}}

    @app.get("/api/status")
    async def get_status():
        if not CFG.demo:
            await engine.refresh()
        return status()

    @app.get("/api/events")
    async def events(request: Request):
        q = bus.subscribe()

        async def stream():
            try:
                yield _sse({"type": "hello", **status()})
                while not await request.is_disconnected():
                    try:
                        ev = await asyncio.wait_for(q.get(), timeout=15)
                        yield _sse(ev)
                    except asyncio.TimeoutError:
                        yield ": ping\n\n"
            finally:
                bus.unsubscribe(q)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ------------------------------------------------------------ engine

    @app.post("/api/engine/start")
    async def engine_start():
        if CFG.demo:
            return {"ok": True}
        asyncio.create_task(_quiet(engine.ensure()))
        return {"ok": True}

    @app.post("/api/engine/restart")
    async def engine_restart():
        if worker.current:
            _bad("wait for the running job to finish (or cancel it) first", 409)
        if not CFG.demo:
            engine.stop()
            asyncio.create_task(_quiet(engine.ensure()))
        return {"ok": True}

    @app.get("/api/engine/log")
    async def engine_log():
        return Response(log_tail(200), media_type="text/plain")

    # ------------------------------------------------------------ models

    @app.get("/api/models")
    async def list_models():
        return {"packs": models.status(downloads), "disk_free": models.disk_free(),
                "text_models": text_models()}

    @app.post("/api/models/{pack}/download")
    async def download(pack: str):
        if pack not in models.PACKS:
            _bad("no such pack", 404)
        await downloads.request(pack)
        return {"ok": True}

    @app.post("/api/models/{pack}/cancel")
    async def cancel_download(pack: str):
        downloads.cancel(pack)
        return {"ok": True}

    @app.delete("/api/models/{pack}")
    async def delete_pack(pack: str):
        if pack not in models.PACKS:
            _bad("no such pack", 404)
        if worker.current:
            _bad("wait for the running job to finish first", 409)
        return {"freed": models.delete_pack(pack)}

    @app.post("/api/models/link")
    async def link_models():
        return {"linked": await asyncio.to_thread(models.link_existing)}

    # ------------------------------------------------------------ generation

    def model_params(quality: str, faces: int, texture: int, engine_name: str, seed: int | None,
                     name: str = "", photo_paint: bool = True) -> dict:
        try:
            graphs.check_params(quality, int(faces), int(texture))
        except ValueError as e:
            _bad(str(e))
        if engine_name not in graphs.ENGINES:
            _bad("unknown engine")
        if engine_name == "trellis2" and not capabilities()["trellis2"]:
            _bad("the TRELLIS.2 engine is not installed (Models tab)", 409)
        if seed is not None and not 0 <= int(seed) <= MAX_SEED:
            _bad("seed out of range")
        return {"quality": quality, "faces": int(faces), "texture": int(texture),
                "engine": engine_name, "seed": int(seed) if seed is not None else None,
                "name": name.strip()[:80], "photo_paint": _flag(photo_paint)}

    async def submit_variations(kind: str, params: dict, variations: int) -> list[dict]:
        if not 1 <= variations <= 4:
            _bad("variations must be 1-4")
        base = params["seed"] if params.get("seed") is not None else random.randint(0, MAX_SEED - 4)
        return [await worker.submit(kind, {**params, "seed": base + i}) for i in range(variations)]

    def need(cap: str):
        if not capabilities()[cap]:
            msg = {"image3d": "Download the Pixal3D core pack first (Models tab).",
                   "multiview": "Download the Pixal3D multi-view pack first (Models tab).",
                   "text3d": "Text to 3D needs the Pixal3D core pack and a text-to-image pack "
                             "(Models tab)."}[cap]
            _bad(msg, 409)

    @app.post("/api/generate/image")
    async def generate_image(image: UploadFile = File(...), quality: str = Form("standard"),
                             faces: int = Form(150_000), texture: int = Form(2048),
                             engine_name: str = Form("pixal3d", alias="engine"),
                             seed: int | None = Form(None), variations: int = Form(1),
                             name: str = Form(""), photo_paint: str = Form("true")):
        need("image3d")
        params = model_params(quality, faces, texture, engine_name, seed, name, photo_paint)
        path = await _save_upload(image)
        params.update(image=str(path), source={"mode": "image", "filename": image.filename})
        return {"jobs": await submit_variations("image3d", params, variations)}

    @app.post("/api/generate/multiview")
    async def generate_multiview(front: UploadFile = File(...), left: UploadFile | None = File(None),
                                 back: UploadFile | None = File(None),
                                 right: UploadFile | None = File(None),
                                 quality: str = Form("standard"), faces: int = Form(150_000),
                                 texture: int = Form(2048), seed: int | None = Form(None),
                                 variations: int = Form(1), name: str = Form(""),
                                 photo_paint: str = Form("true")):
        need("multiview")
        params = model_params(quality, faces, texture, "pixal3d", seed, name, photo_paint)
        views = {"front": str(await _save_upload(front))}
        for v, up in (("left", left), ("back", back), ("right", right)):
            if up is not None and up.filename:
                views[v] = str(await _save_upload(up))
        params.update(views=views, source={"mode": "multiview", "filename": front.filename,
                                           "views": list(views)})
        return {"jobs": await submit_variations("multiview3d", params, variations)}

    @app.post("/api/generate/concepts")
    async def generate_concepts(body: dict = Body(...)):
        need("text3d")
        prompt = str(body.get("prompt", "")).strip()
        if not prompt:
            _bad("describe what to make")
        if len(prompt) > 1000:
            _bad("prompt is too long (1000 characters max)")
        ids = {m["id"] for m in text_models()}
        model = body.get("model") or next(iter(ids), None)
        if model not in ids:
            _bad("that text-to-image model is not installed")
        style = body.get("style", "stylized")
        if style not in graphs.STYLES:
            _bad("unknown style")
        count = int(body.get("count", 4))
        if not 1 <= count <= 4:
            _bad("count must be 1-4")
        seed = body.get("seed")
        seed = random.randint(0, MAX_SEED) if seed in (None, "") else int(seed)
        job = await worker.submit("concepts", {"prompt": prompt, "style": style, "model": model,
                                               "character": bool(body.get("character")),
                                               "count": count, "seed": seed})
        return {"job": job}

    @app.post("/api/generate/from-concept")
    async def from_concept(body: dict = Body(...)):
        need("image3d")
        cjob = store.job(str(body.get("job_id", "")))
        if not cjob or cjob["kind"] != "concepts" or cjob["status"] != "done":
            _bad("no finished concept job with that id", 404)
        try:
            src = concept_path(cjob, int(body.get("index", 0)))
        except (FileNotFoundError, ValueError):
            _bad("no such concept image", 404)
        params = model_params(body.get("quality", "standard"), body.get("faces", 150_000),
                              body.get("texture", 2048), body.get("engine", "pixal3d"),
                              body.get("seed"), body.get("name", ""), body.get("photo_paint", True))
        dest = CFG.uploads_dir / f"{new_id()}.png"
        shutil.copyfile(src, dest)
        params.update(image=str(dest), source={"mode": "text", "prompt": cjob["params"]["prompt"],
                                               "style": cjob["params"]["style"],
                                               "concept": [cjob["id"], int(body.get("index", 0))]})
        return {"jobs": await submit_variations("image3d", params, int(body.get("variations", 1)))}

    @app.post("/api/assets/{aid}/remix")
    async def remix(aid: str, body: dict = Body(default={})):
        asset = _asset_or_404(aid)
        job = store.job(asset["job_id"])
        if not job:
            _bad("the original job is gone, so its input image is too", 410)
        old = job["params"]
        inputs = [old["image"]] if "image" in old else list(old["views"].values())
        if not all(Path(p).is_file() for p in inputs):
            _bad("the original input image was deleted", 410)
        params = model_params(body.get("quality", old["quality"]), body.get("faces", old["faces"]),
                              body.get("texture", old["texture"]), body.get("engine", old["engine"]),
                              body.get("seed"), body.get("name", asset["name"]),
                              body.get("photo_paint", old.get("photo_paint", True)))
        params.update({k: old[k] for k in ("image", "views", "source") if k in old})
        return {"jobs": await submit_variations(job["kind"], params, int(body.get("variations", 1)))}

    # ------------------------------------------------------------ jobs

    @app.get("/api/jobs")
    async def list_jobs(limit: int = 30):
        return [_public_job(j) for j in store.jobs(min(max(limit, 1), 200))]

    @app.get("/api/jobs/{jid}")
    async def get_job(jid: str):
        return _public_job(_job_or_404(jid))

    @app.post("/api/jobs/{jid}/cancel")
    async def cancel_job(jid: str):
        _job_or_404(jid)
        return {"ok": await worker.cancel(jid)}

    @app.delete("/api/jobs/{jid}")
    async def delete_job(jid: str):
        job = _job_or_404(jid)
        if job["status"] not in FINAL:
            _bad("cancel the job before removing it", 409)
        store.delete_job(jid)
        shutil.rmtree(CFG.outputs_dir / "_concepts" / jid, ignore_errors=True)
        return {"ok": True}

    def _job_or_404(jid: str) -> dict:
        job = store.job(jid) if _ID.match(jid) else None
        if not job:
            _bad("no such job", 404)
        return job

    # ------------------------------------------------------------ library

    @app.get("/api/assets")
    async def list_assets(q: str = "", favorites: bool = False):
        return [public_asset(a) for a in store.assets(q.strip()[:80], favorites)]

    @app.get("/api/assets/{aid}")
    async def get_asset(aid: str):
        return public_asset(_asset_or_404(aid))

    @app.patch("/api/assets/{aid}")
    async def patch_asset(aid: str, body: dict = Body(...)):
        _asset_or_404(aid)
        name = body.get("name")
        if name is not None and not str(name).strip():
            _bad("name cannot be empty")
        store.update_asset(aid, name=str(name).strip()[:80] if name is not None else None,
                           favorite=body.get("favorite"))
        a = public_asset(store.asset(aid))
        await bus.publish({"type": "asset", **a})
        return a

    @app.delete("/api/assets/{aid}")
    async def delete_asset(aid: str):
        _asset_or_404(aid)
        store.delete_asset(aid)
        await bus.publish({"type": "asset_deleted", "id": aid})
        return {"ok": True}

    @app.put("/api/assets/{aid}/thumb")
    async def put_thumb(aid: str, request: Request):
        _asset_or_404(aid)
        data = await request.body()
        if len(data) > 4 << 20:
            _bad("thumbnail too large", 413)
        try:
            img = Image.open(io.BytesIO(data))
            img.load()
        except (UnidentifiedImageError, OSError):
            _bad("not an image")
        img.thumbnail((640, 640))
        img.save(CFG.outputs_dir / aid / "thumb.png")
        store.patch_meta(aid, thumb_rendered=True)
        return {"ok": True}

    @app.get("/api/assets/{aid}/download/{fmt}")
    async def download_asset(aid: str, fmt: str):
        a = _asset_or_404(aid)
        adir = CFG.outputs_dir / aid
        stem = re.sub(r"[^\w\- ]+", "", a["name"]).strip().replace(" ", "_") or aid
        if fmt == "glb":
            return FileResponse(adir / "model.glb", filename=f"{stem}.glb")
        if fmt == "textures":
            return FileResponse(exporters.textures_zip(adir), filename=f"{stem}_textures.zip")
        if fmt in exporters.TARGETS:
            try:
                out = await asyncio.to_thread(exporters.export, adir / "model.glb", fmt)
            except FileNotFoundError as e:
                _bad(str(e), 501)
            except RuntimeError as e:
                _bad(str(e), 500)
            names = {"fbx": f"{stem}.fbx", "obj": f"{stem}_obj.zip", "draco": f"{stem}_draco.glb"}
            return FileResponse(out, filename=names[fmt])
        _bad("unknown format", 404)

    def _asset_or_404(aid: str) -> dict:
        a = store.asset(aid) if _ID.match(aid) else None
        if not a:
            _bad("no such asset", 404)
        return a

    # ------------------------------------------------------------ files & UI

    @app.get("/files/{rel:path}")
    async def files(rel: str):
        root = CFG.outputs_dir.resolve()
        p = (root / rel).resolve()
        if root not in p.parents or not p.is_file() or p.suffix not in (".glb", ".png"):
            _bad("not found", 404)
        return FileResponse(p, headers={"Cache-Control": "no-cache"})

    app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
    return app


async def _save_upload(up: UploadFile) -> Path:
    """Validate an uploaded picture and store it as a clean PNG (no metadata, max 2048 px)."""
    data = await up.read(CFG.max_upload_mb * 1_000_000 + 1)
    if len(data) > CFG.max_upload_mb * 1_000_000:
        _bad(f"image too large ({CFG.max_upload_mb} MB max)", 413)
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        _bad(f"{up.filename or 'upload'} is not a readable image")
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        # the engine drops alpha; put cut-outs on neutral grey so background removal still works
        rgba = img.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (128, 128, 128, 255))
        img = Image.alpha_composite(bg, rgba)
    img = img.convert("RGB")
    img.thumbnail((2048, 2048), Image.LANCZOS)
    path = CFG.uploads_dir / f"{new_id()}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return path


def _public_job(j: dict) -> dict:
    p = {k: v for k, v in j["params"].items() if k not in ("image", "views")}
    return {**j, "params": p}


def _sse(ev: dict) -> str:
    return f"data: {json.dumps(ev, default=str)}\n\n"


async def _quiet(coro):
    try:
        await coro
    except Exception:  # noqa: BLE001 - state and error are already published by the engine
        pass
