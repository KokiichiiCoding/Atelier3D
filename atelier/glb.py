"""Minimal GLB reading (stats) and writing (demo models, tests). No numpy, no trimesh."""
from __future__ import annotations

import io
import json
import math
import struct
from pathlib import Path

from PIL import Image

_GLB_MAGIC = 0x46546C67
_JSON, _BIN = 0x4E4F534A, 0x004E4942


def read_json(path: Path) -> dict:
    with open(path, "rb") as f:
        magic, _version, _length = struct.unpack("<III", f.read(12))
        if magic != _GLB_MAGIC:
            raise ValueError(f"{path.name} is not a GLB file")
        clen, ctype = struct.unpack("<II", f.read(8))
        if ctype != _JSON:
            raise ValueError("GLB has no JSON chunk")
        return json.loads(f.read(clen))


def stats(path: Path) -> dict:
    """Face/vertex counts, texture sizes and bounds from the GLB header, without loading it."""
    g = read_json(path)
    acc = g.get("accessors", [])
    faces = verts = 0
    lo, hi = [math.inf] * 3, [-math.inf] * 3
    for mesh in g.get("meshes", []):
        for prim in mesh.get("primitives", []):
            pos = acc[prim["attributes"]["POSITION"]]
            verts += pos["count"]
            idx = prim.get("indices")
            faces += (acc[idx]["count"] if idx is not None else pos["count"]) // 3
            for i in range(3):
                if "min" in pos:
                    lo[i] = min(lo[i], pos["min"][i])
                    hi[i] = max(hi[i], pos["max"][i])
    maps = sorted({k for m in g.get("materials", []) for k in _material_maps(m)})
    return {"faces": faces, "vertices": verts, "maps": maps,
            "textures": len(g.get("images", [])), "bytes": path.stat().st_size,
            "size": [round(h - l, 4) for l, h in zip(lo, hi)] if lo[0] != math.inf else None}


def inspect(path: Path, target_faces: int | None = None) -> dict:
    """Check the exported GLB's usable geometry and material links.

    This is a structural check, not a visual-quality or watertightness claim. It only reads
    the JSON chunk, so even a large 4K textured model is inexpensive to inspect.
    """
    g = read_json(path)
    accessors = g.get("accessors", [])
    materials = g.get("materials", [])
    textures = g.get("textures", [])
    images = g.get("images", [])
    views = g.get("bufferViews", [])
    primitives = [p for m in g.get("meshes", []) for p in m.get("primitives", [])]
    problems, notes = [], []
    if not primitives:
        problems.append("The GLB contains no mesh.")
    for number, prim in enumerate(primitives, 1):
        label = f"Mesh part {number}"
        attrs = prim.get("attributes", {})
        pos_id = attrs.get("POSITION")
        if not isinstance(pos_id, int) or not 0 <= pos_id < len(accessors) or accessors[pos_id].get("count", 0) < 3:
            problems.append(f"{label} has no usable vertices.")
        if prim.get("mode", 4) != 4:
            problems.append(f"{label} is not a triangle mesh.")
        if "TEXCOORD_0" not in attrs:
            problems.append(f"{label} has no UV coordinates for its texture.")
        if "NORMAL" not in attrs:
            notes.append(f"{label} has no vertex normals; shading may be wrong.")
        mat_id = prim.get("material")
        mat = materials[mat_id] if isinstance(mat_id, int) and 0 <= mat_id < len(materials) else {}
        base = mat.get("pbrMetallicRoughness", {}).get("baseColorTexture", {})
        tex_id = base.get("index")
        texture = textures[tex_id] if isinstance(tex_id, int) and 0 <= tex_id < len(textures) else {}
        image_id = texture.get("source")
        image = images[image_id] if isinstance(image_id, int) and 0 <= image_id < len(images) else {}
        view_id = image.get("bufferView")
        embedded = (isinstance(view_id, int) and 0 <= view_id < len(views)
                    and views[view_id].get("byteLength", 0) > 0)
        if not embedded:
            problems.append(f"{label} has no embedded base-color texture.")
    s = stats(path)
    if not s["faces"]:
        problems.append("The GLB contains no triangles.")
    if s["size"] and any(v <= 0 for v in s["size"]):
        notes.append("The model is flat on at least one axis; check it in the viewport.")
    if target_faces and s["faces"] > target_faces * 1.1:
        notes.append(f"The mesh has {s['faces']:,} triangles, above the {target_faces:,} target.")
    if "normal" not in s["maps"]:
        notes.append("No normal map is linked in the GLB; fine surface detail may be lost.")
    if "metallic_roughness" not in s["maps"]:
        notes.append("No metallic-roughness map is linked in the GLB; material variation may be limited.")
    return {"ready": not problems, "parts": len(primitives), "problems": problems, "notes": notes,
            "checks": {"triangles": bool(s["faces"]) and all(p.get("mode", 4) == 4 for p in primitives),
                       "uv": bool(primitives) and all("TEXCOORD_0" in p.get("attributes", {}) for p in primitives),
                       "base_color": bool(primitives) and not any("base-color texture" in p for p in problems),
                       "normal_map": "normal" in s["maps"],
                       "metallic_roughness": "metallic_roughness" in s["maps"]}}


def _material_maps(m: dict):
    pbr = m.get("pbrMetallicRoughness", {})
    if "baseColorTexture" in pbr:
        yield "base_color"
    if "metallicRoughnessTexture" in pbr:
        yield "metallic_roughness"
    for k, name in (("normalTexture", "normal"), ("occlusionTexture", "ao"),
                    ("emissiveTexture", "emissive")):
        if k in m:
            yield name


# ---------------------------------------------------------------- writing (demo)

def write_textured(path: Path, positions, normals, uvs, indices, texture: Image.Image,
                   metallic: float = 0.0, roughness: float = 0.6):
    """Write one textured triangle mesh as a GLB (positions/normals/uvs are flat float lists)."""
    png = io.BytesIO()
    texture.save(png, "PNG")
    blobs = [struct.pack(f"<{len(positions)}f", *positions),
             struct.pack(f"<{len(normals)}f", *normals),
             struct.pack(f"<{len(uvs)}f", *uvs),
             struct.pack(f"<{len(indices)}I", *indices),
             png.getvalue()]
    views, offset, binary = [], 0, b""
    for i, b in enumerate(blobs):
        pad = (-len(b)) % 4
        view = {"buffer": 0, "byteOffset": offset, "byteLength": len(b)}
        if i < 3:
            view["target"] = 34962
        elif i == 3:
            view["target"] = 34963
        views.append(view)
        binary += b + b"\0" * pad
        offset += len(b) + pad
    n = len(positions) // 3
    xs, ys, zs = positions[0::3], positions[1::3], positions[2::3]
    doc = {
        "asset": {"version": "2.0", "generator": "Atelier3D demo"},
        "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2},
                                    "indices": 3, "material": 0}]}],
        "materials": [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0},
                                                "metallicFactor": metallic,
                                                "roughnessFactor": roughness}}],
        "textures": [{"source": 0, "sampler": 0}],
        "samplers": [{"magFilter": 9729, "minFilter": 9987}],
        "images": [{"bufferView": 4, "mimeType": "image/png"}],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": n, "type": "VEC3",
             "min": [min(xs), min(ys), min(zs)], "max": [max(xs), max(ys), max(zs)]},
            {"bufferView": 1, "componentType": 5126, "count": n, "type": "VEC3"},
            {"bufferView": 2, "componentType": 5126, "count": n, "type": "VEC2"},
            {"bufferView": 3, "componentType": 5125, "count": len(indices), "type": "SCALAR"},
        ],
        "bufferViews": views,
        "buffers": [{"byteLength": len(binary)}],
    }
    js = json.dumps(doc, separators=(",", ":")).encode()
    js += b" " * ((-len(js)) % 4)
    total = 12 + 8 + len(js) + 8 + len(binary)
    with open(path, "wb") as f:
        f.write(struct.pack("<III", _GLB_MAGIC, 2, total))
        f.write(struct.pack("<II", len(js), _JSON) + js)
        f.write(struct.pack("<II", len(binary), _BIN) + binary)


def lathe(profile: list[tuple[float, float]], segments: int = 48):
    """Revolve (radius, height) points around Y: positions, normals, uvs, indices."""
    pos, nrm, uv, idx = [], [], [], []
    rows = len(profile)
    for j, (r, y) in enumerate(profile):
        # profile tangent gives the normal's slope
        r0, y0 = profile[max(j - 1, 0)]
        r1, y1 = profile[min(j + 1, rows - 1)]
        tr, ty = r1 - r0, y1 - y0
        nr, ny = ty, -tr
        ln = math.hypot(nr, ny) or 1.0
        for i in range(segments + 1):
            a = 2 * math.pi * i / segments
            c, s = math.cos(a), math.sin(a)
            pos += [r * c, y, r * s]
            nrm += [nr / ln * c, ny / ln, nr / ln * s]
            uv += [i / segments, 1 - j / (rows - 1)]
    for j in range(rows - 1):
        for i in range(segments):
            a = j * (segments + 1) + i
            b = a + segments + 1
            idx += [a, b, a + 1, a + 1, b, b + 1]  # counter-clockwise seen from outside
    return pos, nrm, uv, idx
