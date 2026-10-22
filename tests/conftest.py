"""Every test runs against throwaway folders in demo mode: no GPU, no engine, no downloads,
and never the owner's real models/, outputs/ or data/."""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["ATELIER_DEMO_SPEED"] = "0"


@pytest.fixture(autouse=True)
def private_dirs(tmp_path, monkeypatch):
    from atelier.config import CFG

    monkeypatch.setattr(CFG, "outputs_dir", tmp_path / "outputs")
    monkeypatch.setattr(CFG, "data_dir", tmp_path / "data")
    monkeypatch.setattr(CFG, "models_dir", tmp_path / "models")
    monkeypatch.setattr(CFG, "engine_dir", tmp_path / "engine")
    monkeypatch.setattr(CFG, "demo", True)
    return tmp_path


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from atelier.api import create_app

    with TestClient(create_app(), base_url="http://127.0.0.1:7870") as c:
        yield c


def png_bytes(size=(320, 240), color=(200, 60, 40), mode="RGB") -> bytes:
    import io

    from PIL import Image

    b = io.BytesIO()
    Image.new(mode, size, color).save(b, "PNG")
    return b.getvalue()


def wait_jobs(client, ids, timeout=20.0):
    import time

    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        jobs = [client.get(f"/api/jobs/{i}").json() for i in ids]
        if all(j["status"] in ("done", "failed", "cancelled") for j in jobs):
            return jobs
        time.sleep(0.05)
    raise AssertionError(f"jobs did not finish: {[j['status'] for j in jobs]}")
