"""FBX and OBJ (+MTL+textures) export through a local Blender, when one is installed.

GLB, STL, PLY and USDZ are exported in the browser by three.js; FBX and textured OBJ need a
real DCC exporter, and Blender's are the best free ones.
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

_SCRIPT = r"""
import os, sys, bpy
src, dst, fmt = sys.argv[sys.argv.index("--") + 1:]
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=src)
if fmt == "fbx":
    bpy.ops.export_scene.fbx(filepath=dst, path_mode="COPY", embed_textures=True,
                             axis_forward="-Z", axis_up="Y")
else:
    # GLB textures are embedded: write them next to the .obj so the .mtl can point at them
    folder = os.path.dirname(dst)
    for img in bpy.data.images:
        if img.size[0]:
            img.filepath_raw = os.path.join(folder, bpy.path.clean_name(img.name) + ".png")
            img.file_format = "PNG"
            img.save()
    bpy.ops.wm.obj_export(filepath=dst, export_materials=True, path_mode="STRIP",
                          export_pbr_extensions=True)
"""


def find_blender() -> str | None:
    if os.environ.get("ATELIER_BLENDER"):
        return os.environ["ATELIER_BLENDER"]
    found = shutil.which("blender")
    if found:
        return found
    hits = sorted(glob.glob(r"C:\Program Files\Blender Foundation\Blender *\blender.exe"))
    return hits[-1] if hits else None


def export(glb_path: Path, fmt: str, timeout: int = 300) -> Path:
    """Convert ``glb_path`` to ``fmt`` ('fbx' or 'obj'); returns the file to download
    (a zip for OBJ, which needs its .mtl and textures). Results are cached next to the GLB."""
    if fmt not in ("fbx", "obj"):
        raise ValueError("fmt must be fbx or obj")
    out_dir = glb_path.parent / "exports"
    target = out_dir / ("model.fbx" if fmt == "fbx" else "model_obj.zip")
    if target.exists() and target.stat().st_mtime >= glb_path.stat().st_mtime:
        return target
    blender = find_blender()
    if not blender:
        raise FileNotFoundError("Blender is not installed (needed for FBX and textured OBJ)")
    work = out_dir / fmt
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    dst = work / f"model.{fmt}"
    r = subprocess.run([blender, "-b", "--factory-startup", "-noaudio", "--python-expr", _SCRIPT,
                        "--", str(glb_path), str(dst), fmt],
                       capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0 or not dst.exists():
        tail = (r.stderr or r.stdout or "").strip().splitlines()[-3:]
        raise RuntimeError("Blender export failed: " + " / ".join(tail))
    if fmt == "fbx":
        shutil.move(dst, target)
    else:
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
            for f in work.rglob("*"):
                if f.is_file():
                    z.write(f, f.relative_to(work))
    shutil.rmtree(work, ignore_errors=True)
    return target


def textures_zip(asset_dir: Path) -> Path:
    target = asset_dir / "exports" / "textures.zip"
    target.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted((asset_dir / "maps").glob("*.png")):
            z.write(f, f.name)
    return target
