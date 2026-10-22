"""SQLite job queue + asset library, one GPU worker, and an event bus for the UI's SSE stream."""
from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

from .config import CFG

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
    params TEXT NOT NULL, progress TEXT, result TEXT, error TEXT,
    created REAL, started REAL, finished REAL
);
CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY, job_id TEXT, name TEXT, kind TEXT, favorite INTEGER DEFAULT 0,
    meta TEXT, created REAL
);
"""
FINAL = ("done", "failed", "cancelled")


def new_id() -> str:
    return uuid.uuid4().hex[:12]


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(_SCHEMA)
        # a job that was running when the studio stopped cannot resume mid-graph
        self.db.execute("UPDATE jobs SET status='failed', error='interrupted: the studio was "
                        "closed while this was running' WHERE status='running'")
        self.db.commit()

    # jobs ------------------------------------------------------------------
    def add_job(self, kind: str, params: dict) -> dict:
        jid = new_id()
        self.db.execute("INSERT INTO jobs (id,kind,status,params,created) VALUES (?,?,?,?,?)",
                        (jid, kind, "queued", json.dumps(params), time.time()))
        self.db.commit()
        return self.job(jid)

    def update_job(self, jid: str, **fields: Any):
        for k in ("params", "progress", "result"):
            if k in fields and not isinstance(fields[k], str):
                fields[k] = json.dumps(fields[k])
        cols = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), jid))
        self.db.commit()

    def job(self, jid: str) -> dict | None:
        r = self.db.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
        return _job(r) if r else None

    def jobs(self, limit: int = 50) -> list[dict]:
        rows = self.db.execute("SELECT * FROM jobs ORDER BY created DESC LIMIT ?", (limit,))
        return [_job(r) for r in rows]

    def queued_ids(self) -> list[str]:
        rows = self.db.execute("SELECT id FROM jobs WHERE status='queued' ORDER BY created")
        return [r["id"] for r in rows]

    def delete_job(self, jid: str):
        self.db.execute("DELETE FROM jobs WHERE id=?", (jid,))
        self.db.commit()

    # assets ----------------------------------------------------------------
    def add_asset(self, aid: str, job_id: str, name: str, kind: str, meta: dict) -> dict:
        self.db.execute("INSERT INTO assets (id,job_id,name,kind,meta,created) VALUES (?,?,?,?,?,?)",
                        (aid, job_id, name, kind, json.dumps(meta), time.time()))
        self.db.commit()
        return self.asset(aid)

    def asset(self, aid: str) -> dict | None:
        r = self.db.execute("SELECT * FROM assets WHERE id=?", (aid,)).fetchone()
        return _asset(r) if r else None

    def assets(self, q: str = "", favorites: bool = False, limit: int = 500) -> list[dict]:
        sql, args = "SELECT * FROM assets WHERE 1=1", []
        if q:
            sql += " AND name LIKE ?"
            args.append(f"%{q}%")
        if favorites:
            sql += " AND favorite=1"
        rows = self.db.execute(sql + " ORDER BY created DESC LIMIT ?", (*args, limit))
        return [_asset(r) for r in rows]

    def patch_meta(self, aid: str, **fields: Any):
        r = self.db.execute("SELECT meta FROM assets WHERE id=?", (aid,)).fetchone()
        meta = {**json.loads(r["meta"] or "{}"), **fields}
        self.db.execute("UPDATE assets SET meta=? WHERE id=?", (json.dumps(meta), aid))
        self.db.commit()

    def update_asset(self, aid: str, name: str | None = None, favorite: bool | None = None):
        if name is not None:
            self.db.execute("UPDATE assets SET name=? WHERE id=?", (name, aid))
        if favorite is not None:
            self.db.execute("UPDATE assets SET favorite=? WHERE id=?", (int(favorite), aid))
        self.db.commit()

    def delete_asset(self, aid: str):
        self.db.execute("DELETE FROM assets WHERE id=?", (aid,))
        self.db.commit()
        shutil.rmtree(CFG.outputs_dir / aid, ignore_errors=True)


def _job(r: sqlite3.Row) -> dict:
    return {"id": r["id"], "kind": r["kind"], "status": r["status"],
            "params": json.loads(r["params"]), "progress": json.loads(r["progress"] or "null"),
            "result": json.loads(r["result"] or "null"), "error": r["error"],
            "created": r["created"], "started": r["started"], "finished": r["finished"]}


def _asset(r: sqlite3.Row) -> dict:
    return {"id": r["id"], "job_id": r["job_id"], "name": r["name"], "kind": r["kind"],
            "favorite": bool(r["favorite"]), "created": r["created"], **json.loads(r["meta"] or "{}")}


class Bus:
    """Fan-out of every event to every open SSE stream."""

    def __init__(self):
        self._subs: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        self._subs.discard(q)

    async def publish(self, event: dict):
        for q in list(self._subs):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:  # a stalled tab must not block generation
                pass


Runner = Callable[[dict, "Context"], Awaitable[dict]]


class Context:
    """What a running job can do: report progress, check for cancel."""

    def __init__(self, worker: "Worker", job: dict):
        self.worker, self.job = worker, job

    @property
    def cancelled(self) -> bool:
        return self.job["id"] in self.worker.cancel_requested

    async def progress(self, **p: Any):
        self.worker.store.update_job(self.job["id"], progress=p)
        await self.worker.bus.publish({"type": "job", "id": self.job["id"], "status": "running",
                                       "progress": p})

    async def preview(self, kind: str, url: str):
        await self.worker.bus.publish({"type": "preview", "id": self.job["id"], "kind": kind,
                                       "url": url})


class Worker:
    """Runs jobs one at a time (one GPU), in submission order."""

    def __init__(self, store: Store, bus: Bus, runner: Runner):
        self.store, self.bus, self.runner = store, bus, runner
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.cancel_requested: set[str] = set()
        self.current: str | None = None
        self._task: asyncio.Task | None = None

    def start(self):
        for jid in self.store.queued_ids():  # jobs queued before a restart run again
            self.queue.put_nowait(jid)
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        if self._task:
            self._task.cancel()

    async def submit(self, kind: str, params: dict) -> dict:
        job = self.store.add_job(kind, params)
        await self.queue.put(job["id"])
        await self.bus.publish({"type": "job", **job})
        return job

    async def cancel(self, jid: str) -> bool:
        job = self.store.job(jid)
        if not job or job["status"] in FINAL:
            return False
        self.cancel_requested.add(jid)
        if job["status"] == "queued":
            self._finish(jid, "cancelled")
            await self.bus.publish({"type": "job", **self.store.job(jid)})
        return True

    def _finish(self, jid: str, status: str, **fields: Any):
        self.store.update_job(jid, status=status, finished=time.time(), **fields)

    async def _loop(self):
        while True:
            jid = await self.queue.get()
            job = self.store.job(jid)
            if not job or job["status"] != "queued":
                continue
            self.current = jid
            self.store.update_job(jid, status="running", started=time.time())
            await self.bus.publish({"type": "job", **self.store.job(jid)})
            try:
                result = await self.runner(job, Context(self, job))
                self._finish(jid, "done", result=result)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - every failure is reported to the UI
                if jid in self.cancel_requested:
                    self._finish(jid, "cancelled")
                else:
                    self._finish(jid, "failed", error=str(e) or type(e).__name__)
            finally:
                self.current = None
                self.cancel_requested.discard(jid)
            await self.bus.publish({"type": "job", **self.store.job(jid)})
