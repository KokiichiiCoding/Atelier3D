"""Install (or repair) Atelier3D's private inference engine. Standard library only.

    python scripts/setup_engine.py

Fetches the pinned ComfyUI release into engine/ComfyUI and builds engine/.venv with a CUDA 13
PyTorch. Safe to re-run: finished steps are skipped. Uses uv when available (fast, and it
hard-links wheels it already has instead of storing another copy), otherwise venv + pip.
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

COMFY_REF = "v0.42.1"
COMFY_REPO = "https://github.com/Comfy-Org/ComfyUI"
PYTHON = "3.12"
# comfy-kitchen only enables its fast int8/fp8 kernels on CUDA 13 builds of torch
TORCH = ["torch==2.14.1+cu130", "torchvision==0.29.1+cu130"]
TORCH_INDEX = "https://download.pytorch.org/whl/cu130"

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / "engine"
COMFY = ENGINE / "ComfyUI"
VENV = ENGINE / ".venv"
PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
STAMP = VENV / f".atelier-{COMFY_REF}-{TORCH[0].replace('==', '-')}"


def say(msg: str):
    print(f"[Atelier3D] {msg}", flush=True)


def run(*cmd: str):
    say(" ".join(cmd))
    subprocess.run(cmd, check=True)


def find_uv() -> str | None:
    found = shutil.which("uv")
    local = Path.home() / ".local" / "bin" / ("uv.exe" if os.name == "nt" else "uv")
    return found or (str(local) if local.is_file() else None)


def fetch_comfy():
    if (COMFY / "main.py").is_file():
        return
    ENGINE.mkdir(exist_ok=True)
    if shutil.which("git"):
        run("git", "clone", "--depth", "1", "--branch", COMFY_REF, COMFY_REPO, str(COMFY))
        return
    url = f"{COMFY_REPO}/archive/refs/tags/{COMFY_REF}.zip"
    say(f"downloading {url}")
    data = urllib.request.urlopen(url, timeout=120).read()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        top = z.namelist()[0].split("/")[0]
        z.extractall(ENGINE)
    (ENGINE / top).rename(COMFY)


def build_venv(uv: str | None):
    if STAMP.exists():
        return
    if not PY.is_file():
        if uv:
            run(uv, "venv", "--python", PYTHON, str(VENV))
        else:
            run(sys.executable, "-m", "venv", str(VENV))
    if uv:
        pip = [uv, "pip", "install", "--python", str(PY)]
        os.environ.setdefault("UV_LINK_MODE", "hardlink")
        run(*pip, *TORCH, "--index-url", TORCH_INDEX, "--extra-index-url", "https://pypi.org/simple",
            "--index-strategy", "unsafe-best-match")
    else:
        pip = [str(PY), "-m", "pip", "install"]
        run(*pip, *TORCH, "--extra-index-url", TORCH_INDEX)
    run(*pip, "-r", str(COMFY / "requirements.txt"), "numba")
    for old in VENV.glob(".atelier-*"):
        old.unlink()
    STAMP.write_text("ok")


def main() -> int:
    if sys.version_info < (3, 10):
        say("Python 3.10+ is required")
        return 1
    fetch_comfy()
    build_venv(find_uv())
    for d in ("custom_nodes", "input", "output", "temp", "user"):
        (ENGINE / "data" / d).mkdir(parents=True, exist_ok=True)
    say(f"engine ready: ComfyUI {COMFY_REF}, {TORCH[0]}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as e:
        say(f"setup failed: {e}")
        sys.exit(1)
