"""Model packs: presence checks, hard-linking local copies, deleting, resumable downloads."""
import asyncio

import httpx
import pytest

from atelier import models
from atelier.config import CFG


@pytest.fixture
def tiny_pack(monkeypatch):
    """Replace the real (multi-GB) packs with two tiny ones that share a file."""
    shared = models.ModelFile("vae", "shared.safetensors", "org/repo", "vae/shared.safetensors", 10)
    a = models.Pack("a", "A", "", "MIT", (models.ModelFile("diffusion_models", "a.safetensors", "org/repo",
                                                          "dm/a.safetensors", 25), shared), required=True)
    b = models.Pack("b", "B", "", "MIT", (shared,))
    monkeypatch.setattr(models, "PACKS", {"a": a, "b": b})
    return a, b


def _put(f: models.ModelFile, size=None):
    p = f.dest()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * (f.size if size is None else size))
    return p


def test_presence_is_checked_by_size(tiny_pack):
    a, _ = tiny_pack
    assert not models.installed("a")
    _put(a.files[0])
    _put(a.files[1], size=3)  # truncated download
    assert not models.installed("a")
    _put(a.files[1])
    assert models.installed("a")
    row = next(r for r in models.status() if r["id"] == "a")
    assert row["installed"] and row["have"] == row["size"] == 35


def test_delete_keeps_files_other_installed_packs_need(tiny_pack):
    a, b = tiny_pack
    for f in a.files:
        _put(f)
    assert models.installed("b")
    freed = models.delete_pack("a")
    assert freed == 25 and models.installed("b") and not models.installed("a")


def test_link_existing_hard_links_matches(tiny_pack, tmp_path):
    a, _ = tiny_pack
    other = tmp_path / "OtherApp" / "models"
    (other / "dm").mkdir(parents=True)
    (other / "dm" / "a.safetensors").write_bytes(b"y" * 25)
    (other / "dm" / "wrong_size_but_same_name.safetensors").write_bytes(b"y")
    (other / "shared.safetensors").write_bytes(b"y" * 9)  # size mismatch: skipped
    done = models.link_existing([other])
    assert [d["file"] for d in done] == ["a.safetensors"] and done[0]["linked"]
    dest = a.files[0].dest()
    assert dest.read_bytes() == b"y" * 25
    assert dest.stat().st_nlink == 2  # a hard link, not a copy
    assert models.link_existing([other]) == []  # idempotent


def test_text_models_lists_sdxl_drop_ins(tiny_pack):
    d = CFG.models_dir / "checkpoints"
    d.mkdir(parents=True)
    (d / "myStyle_xl.safetensors").write_bytes(b"")
    (d / "hunyuan3d-dit.safetensors").write_bytes(b"")
    ids = [m["id"] for m in models.text_models()]
    assert ids == ["sdxl:myStyle_xl.safetensors"]


class _Server:
    """A fake Hugging Face that honours Range requests and can drop the connection."""

    def __init__(self, blobs, fail_after=None):
        self.blobs, self.fail_after, self.calls = blobs, fail_after, []

    def __call__(self, request: httpx.Request):
        name = request.url.path.rsplit("/", 1)[-1]
        data = self.blobs[name]
        start = int(request.headers.get("range", "bytes=0-")[6:].split("-")[0])
        self.calls.append((name, start))
        body = data[start:]
        if self.fail_after is not None:
            body, self.fail_after = body[: self.fail_after], None
        return httpx.Response(206 if start else 200, content=body)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_download_resumes_and_verifies(tiny_pack, monkeypatch):
    a, _ = tiny_pack
    server = _Server({"a.safetensors": b"A" * 25, "shared.safetensors": b"S" * 10}, fail_after=7)
    real = httpx.AsyncClient
    monkeypatch.setattr(models.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(server), **kw))
    events = []

    async def publish(e):
        events.append(e)

    async def go():
        d = models.Downloader(publish)
        await d.request("a")
        await d._task
        assert d.state_of("a")["error"], "first attempt must fail on the short body"
        assert a.files[0].dest().with_name("a.safetensors.part").stat().st_size == 7
        await d.request("a")
        await d._task
        return d

    d = _run(go())
    assert models.installed("a"), d.state_of("a")
    assert ("a.safetensors", 7) in server.calls  # resumed from byte 7, not from scratch
    assert events[-1]["pack"] == "a" and not events[-1]["downloading"]


def test_download_refuses_when_the_disk_is_full(tiny_pack, monkeypatch):
    monkeypatch.setattr(models, "disk_free", lambda path=None: 1_000)

    async def go():
        d = models.Downloader()
        await d.request("a")
        await d._task
        return d.state_of("a")

    assert "disk space" in _run(go())["error"]
