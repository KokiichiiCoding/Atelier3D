"""The inference engine: a pinned ComfyUI checkout that Atelier3D starts, feeds and stops.

Atelier3D never imports ComfyUI. It runs it as a child process on a private port and talks
to its HTTP + WebSocket API, so a crash or out-of-memory in the engine never takes the
studio down, and the engine's heavy Python stack stays out of the app's own environment.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx
import websockets

from .config import CFG, ROOT

ENGINE_DIRS = ("custom_nodes", "input", "output", "temp", "user")
NODES_SRC = ROOT / "engine_nodes"   # Atelier3D's own engine nodes, copied in on every start


def install_nodes() -> None:
    for pkg in NODES_SRC.iterdir():
        if pkg.is_dir():
            shutil.copytree(pkg, CFG.engine_data / "custom_nodes" / pkg.name, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__"))


class EngineError(RuntimeError):
    pass


class Cancelled(EngineError):
    pass


@dataclass
class Event:
    kind: str                 # executing | progress | executed | cached
    node: str | None = None
    value: float = 0.0        # progress 0..1 inside the node
    files: list[dict] | None = None
    nodes: list[str] | None = None


class EngineClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.ws_url = self.base_url.replace("http", "ws", 1)

    async def system_stats(self) -> dict | None:
        try:
            async with httpx.AsyncClient(timeout=4) as c:
                r = await c.get(f"{self.base_url}/system_stats")
                return r.json() if r.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            return None

    async def upload(self, path: Path, subfolder: str = "atelier") -> str:
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(f"{self.base_url}/upload/image",
                             files={"image": (path.name, path.read_bytes(), "image/png")},
                             data={"overwrite": "true", "type": "input", "subfolder": subfolder})
            r.raise_for_status()
            j = r.json()
        return f"{j['subfolder']}/{j['name']}" if j.get("subfolder") else j["name"]

    async def download(self, item: dict) -> bytes:
        async with httpx.AsyncClient(timeout=300) as c:
            r = await c.get(f"{self.base_url}/view", params={
                "filename": item["filename"], "subfolder": item.get("subfolder", ""),
                "type": item.get("type", "output")})
            r.raise_for_status()
            return r.content

    async def interrupt(self):
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                await c.post(f"{self.base_url}/interrupt")
        except httpx.HTTPError:
            pass

    async def run(self, prompt: dict, on_event: Callable[[Event], Awaitable[None]],
                  cancelled: Callable[[], bool] = lambda: False) -> dict:
        """Queue ``prompt`` and stream its events until it finishes; returns its outputs."""
        client_id = uuid.uuid4().hex
        async with websockets.connect(f"{self.ws_url}/ws?clientId={client_id}", max_size=None) as ws:
            async with httpx.AsyncClient(timeout=60) as c:
                r = await c.post(f"{self.base_url}/prompt", json={"prompt": prompt, "client_id": client_id})
            if r.status_code != 200:
                raise EngineError(describe_rejection(r))
            pid = r.json()["prompt_id"]
            while True:
                if cancelled():
                    await self.interrupt()
                    raise Cancelled("cancelled")
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                except websockets.ConnectionClosed:
                    raise EngineError("the engine stopped in the middle of the job (out of memory?); "
                                      "the Models tab has its log") from None
                if isinstance(raw, bytes):
                    continue  # latent preview frames
                msg = json.loads(raw)
                t, d = msg.get("type"), msg.get("data", {})
                if d.get("prompt_id") not in (None, pid):
                    continue
                if t == "executing":
                    if d.get("node") is None:
                        break
                    await on_event(Event("executing", node=str(d["node"])))
                elif t == "progress":
                    await on_event(Event("progress", node=str(d.get("node")),
                                         value=d.get("value", 0) / max(1, d.get("max", 1))))
                elif t == "executed":
                    out = d.get("output") or {}
                    files = [f for k in ("3d", "images") for f in out.get(k, []) if isinstance(f, dict)]
                    await on_event(Event("executed", node=str(d.get("node")), files=files))
                elif t == "execution_cached":
                    await on_event(Event("cached", nodes=[str(n) for n in d.get("nodes", [])]))
                elif t == "execution_error":
                    raise EngineError(f"{d.get('node_type', 'node')} failed: "
                                      f"{d.get('exception_message', '').strip() or d}")
                elif t == "execution_interrupted":
                    raise Cancelled("interrupted")
                elif t == "execution_success":
                    break
        async with httpx.AsyncClient(timeout=30) as c:
            hist = (await c.get(f"{self.base_url}/history/{pid}")).json()
        return hist.get(pid, {}).get("outputs", {})


def describe_rejection(r: httpx.Response) -> str:
    """Turn ComfyUI's validation error JSON into one readable line."""
    try:
        j = r.json()
    except ValueError:
        return f"engine rejected the job ({r.status_code}): {r.text[:300]}"
    msgs = []
    for nid, err in (j.get("node_errors") or {}).items():
        for e in err.get("errors", []):
            msgs.append(f"{err.get('class_type', nid)}: {e.get('message')} {e.get('details', '')}".strip())
    head = (j.get("error") or {}).get("message", "engine rejected the job")
    return head + (" - " + "; ".join(msgs) if msgs else "")


class EngineProcess:
    """Start/stop the pinned ComfyUI. State is published so the UI can show it."""

    def __init__(self, publish: Callable[[dict], Awaitable[None]] | None = None):
        self.publish = publish
        self.proc: subprocess.Popen | None = None
        self.state = "stopped"      # stopped | starting | ready | error | missing
        self.error: str | None = None
        self.stats: dict | None = None
        self.client = EngineClient(CFG.engine_url)
        self._lock = asyncio.Lock()

    @staticmethod
    def installed() -> bool:
        return CFG.engine_main.is_file() and CFG.engine_python.is_file()

    def info(self) -> dict:
        dev = (self.stats or {}).get("devices") or [{}]
        d = dev[0]
        return {"state": self.state, "error": self.error, "url": CFG.engine_url,
                "version": (self.stats or {}).get("system", {}).get("comfyui_version"),
                "device": d.get("name"), "device_type": d.get("type"),
                "vram_total": d.get("vram_total"), "vram_free": d.get("vram_free")}

    async def _set(self, state: str, error: str | None = None):
        self.state, self.error = state, error
        if self.publish:
            await self.publish({"type": "engine", **self.info()})

    async def ensure(self, timeout: float = 420) -> None:
        """Make sure the engine is serving; start it if needed. Raises EngineError."""
        async with self._lock:
            if self.state == "ready" and await self.client.system_stats():
                return
            stats = await self.client.system_stats()
            if stats:  # left over from a previous run of the studio: adopt it
                self.stats = stats
                return await self._set("ready")
            if not self.installed():
                await self._set("missing", "engine not installed: run Atelier3D.bat once")
                raise EngineError(self.error)
            await self._set("starting")
            for d in ENGINE_DIRS:
                (CFG.engine_data / d).mkdir(parents=True, exist_ok=True)
            install_nodes()
            CFG.models_dir.mkdir(parents=True, exist_ok=True)
            log = open(CFG.data_dir / "engine.log", "w", encoding="utf-8", errors="replace")
            args = [str(CFG.engine_python), str(CFG.engine_main), "--listen", "127.0.0.1",
                    "--port", str(CFG.engine_port), "--base-directory", str(CFG.engine_data),
                    "--models-directory", str(CFG.models_dir), "--disable-auto-launch",
                    *CFG.engine_args]
            env = {**os.environ, "PYTHONUNBUFFERED": "1"}
            flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            self.proc = subprocess.Popen(args, cwd=str(CFG.engine_main.parent), stdout=log,
                                         stderr=subprocess.STDOUT, env=env, creationflags=flags)
            t0 = time.monotonic()
            while time.monotonic() - t0 < timeout:
                if self.proc.poll() is not None:
                    await self._set("error", f"engine exited (code {self.proc.returncode}); "
                                    f"see {CFG.data_dir / 'engine.log'}")
                    raise EngineError(self.error)
                stats = await self.client.system_stats()
                if stats:
                    self.stats = stats
                    return await self._set("ready")
                await asyncio.sleep(1)
            await self._set("error", f"engine did not start within {int(timeout)} s")
            raise EngineError(self.error)

    async def refresh(self):
        stats = await self.client.system_stats()
        if stats:
            self.stats = stats
            if self.state != "ready":
                await self._set("ready")
        elif self.state == "ready":
            await self._set("stopped")

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
        self.state = "stopped"


def log_tail(lines: int = 60) -> str:
    p = CFG.data_dir / "engine.log"
    if not p.exists():
        return ""
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        return "".join(f.readlines()[-lines:])


def as_dict(outputs: dict[str, Any]) -> dict[str, list[dict]]:
    """History outputs -> {node id: [file dicts]} for 3D models and images."""
    return {nid: [f for k in ("3d", "images") for f in o.get(k, []) if isinstance(f, dict)]
            for nid, o in outputs.items()}
