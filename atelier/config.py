"""Paths and settings. Everything can be overridden with ATELIER_* environment variables."""
from __future__ import annotations

import os
import shlex
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _path(env: str, default: Path) -> Path:
    return Path(os.environ[env]).resolve() if os.environ.get(env) else default


@dataclass
class Settings:
    host: str = os.environ.get("ATELIER_HOST", "127.0.0.1")
    port: int = int(os.environ.get("ATELIER_PORT", "7870"))
    engine_port: int = int(os.environ.get("ATELIER_ENGINE_PORT", "8199"))
    engine_dir: Path = _path("ATELIER_ENGINE_DIR", ROOT / "engine")
    models_dir: Path = _path("ATELIER_MODELS", ROOT / "models")
    outputs_dir: Path = _path("ATELIER_OUTPUTS", ROOT / "outputs")
    data_dir: Path = _path("ATELIER_DATA", ROOT / "data")
    # extra ComfyUI flags, e.g. "--novram" when a big model will not fit
    engine_args: list[str] = field(
        default_factory=lambda: shlex.split(os.environ.get("ATELIER_ENGINE_ARGS", "")))
    demo: bool = os.environ.get("ATELIER_DEMO", "") == "1"
    max_upload_mb: int = 40

    @property
    def engine_url(self) -> str:
        return f"http://127.0.0.1:{self.engine_port}"

    @property
    def engine_python(self) -> Path:
        sub = "Scripts/python.exe" if os.name == "nt" else "bin/python"
        return self.engine_dir / ".venv" / sub

    @property
    def engine_main(self) -> Path:
        return self.engine_dir / "ComfyUI" / "main.py"

    @property
    def engine_data(self) -> Path:
        return self.engine_dir / "data"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "atelier.db"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"


CFG = Settings()
