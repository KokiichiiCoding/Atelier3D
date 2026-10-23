"""Self-test of Atelier3D's engine nodes (engine_nodes/) on the CPU, no models needed.

    engine\\.venv\\Scripts\\python.exe scripts\\test_engine_nodes.py

Builds a lumpy, asymmetric test object, renders a "photo" of it with the Pixal3D front camera,
bakes a deliberately soft texture, and checks that AtelierProjectImage restores the fine detail
on what the camera sees, leaves the back alone, copes with a flipped photo and refuses a photo
that does not match.
"""
from __future__ import annotations

import logging
import math
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / "engine" / "ComfyUI"
sys.argv = [sys.argv[0], "--cpu", "--base-directory", str(ROOT / "engine" / "data"),
            "--disable-all-custom-nodes"]
sys.path[:0] = [str(ENGINE), str(ROOT / "engine_nodes")]
os.chdir(ENGINE)
logging.basicConfig(level=logging.INFO, format="%(message)s")

import comfy.options  # noqa: E402

comfy.options.enable_args_parsing()
import numpy as np  # noqa: E402
import torch  # noqa: E402
from comfy_api.latest import Types  # noqa: E402
from comfy_extras import nodes_mesh_postprocess as mp  # noqa: E402

import atelier3d as an  # noqa: E402

FOV, TEX, PHOTO = 40.0, 1024, 512


def icosphere(level):
    t = (1 + 5 ** 0.5) / 2
    v = np.array([(-1, t, 0), (1, t, 0), (-1, -t, 0), (1, -t, 0), (0, -1, t), (0, 1, t), (0, -1, -t),
                  (0, 1, -t), (t, 0, -1), (t, 0, 1), (-t, 0, -1), (-t, 0, 1)], float)
    f = np.array([(0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11), (1, 5, 9), (5, 11, 4),
                  (11, 10, 2), (10, 7, 6), (7, 1, 8), (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8),
                  (3, 8, 9), (4, 9, 5), (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1)])
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    for _ in range(level):
        e = np.sort(np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]]), axis=1)
        uniq, inv = np.unique(e, axis=0, return_inverse=True)
        mid = v[uniq].mean(1)
        mid /= np.linalg.norm(mid, axis=1, keepdims=True)
        m = inv.reshape(3, -1).T + len(v)
        v = np.concatenate([v, mid])
        a, b, c = f.T
        ab, bc, ca = m.T
        f = np.concatenate([np.stack(s, 1) for s in ((a, ab, ca), (b, bc, ab), (c, ca, bc), (ab, bc, ca))])
    return v, f


def low(p):   # what a soft voxel bake keeps
    return torch.stack([0.5 + 0.4 * p[:, 0], 0.5 + 0.4 * p[:, 1], 0.4 + 0.0 * p[:, 2]], -1)


def high(p):  # what it loses: a fine pattern (~0.03 units)
    return (0.18 * torch.sin(200 * p[:, 0]) * torch.sin(200 * p[:, 1]))[:, None].expand(-1, 3)


def texture_of(fn, v, f, uv):
    face_idx, bary, cov = mp._rasterize_uv_barycentric(f.numpy(), uv.numpy(), TEX)
    pos = mp._interp_vertex_attr(v, f.long(), face_idx, bary, cov)
    out = torch.zeros(TEX, TEX, 3)
    out[cov] = fn(pos[cov]).clamp(0, 1)
    return out, cov, pos


def main() -> int:
    t0 = time.time()
    v, f = icosphere(5)
    x, y, z = v.T
    r = 1 + 0.35 * np.exp(-((x - 0.0) ** 2 + (y + 0.1) ** 2 + (z - 1) ** 2) / 0.08)  # a nose (front)
    r += 0.3 * np.exp(-((x - 0.7) ** 2 + (y - 0.7) ** 2 + z ** 2) / 0.1)              # an ear (top right)
    v = torch.from_numpy(v * r[:, None] * 0.33).float()
    f = torch.from_numpy(f).int()
    mesh = Types.MESH(vertices=v[None], faces=f[None])
    mesh, = mp.UnwrapMesh.PREPARE_CLASS_CLONE(None).execute(
        mesh=mesh, segmenter="pec", resolution=TEX, padding=2, weld_distance=0.0).result
    v, f = mesh.vertices[0], mesh.faces[0]
    uv = torch.from_numpy(mp._normalize_uvs_to_unit(mesh.uvs[0].numpy()))
    print(f"test mesh: {f.shape[0]} faces, unwrapped in {time.time() - t0:.1f}s")

    truth, cov, pos = texture_of(lambda p: low(p) + high(p), v, f, uv)
    soft, _, _ = texture_of(low, v, f, uv)
    eye, fwd, right, up, focal = an.camera(0.0, FOV, 1.0, torch.device("cpu"))
    tri = v[f.long()]
    bvh = mp._build_triangle_bvh(tri)
    photo, hit, _ = mp._render_view(tri, bvh, uv, f.long(), truth, eye, fwd, right, up,
                                    math.radians(FOV), PHOTO, PHOTO)
    mask = hit.float()

    vn = torch.from_numpy(mp._smooth_vertex_normals(v.numpy(), f.numpy()))
    face_idx, bary, _ = mp._rasterize_uv_barycentric(f.numpy(), uv.numpy(), TEX)
    nrm = torch.nn.functional.normalize(mp._interp_vertex_attr(vn, f.long(), face_idx, bary, cov), dim=-1)
    facing = (nrm * torch.nn.functional.normalize(eye - pos, dim=-1)).sum(-1)
    front = cov & (facing > 0.85) & (pos[..., 1] < 0.15)   # squarely seen, below the ear
    back = cov & (facing < -0.2)

    def err(tex, sel):
        return (tex[sel] - truth[sel]).abs().mean().item()

    failures = 0

    def check(name, ok, detail):
        nonlocal failures
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {name}: {detail}")

    for label, img, msk in (("photo", photo, mask), ("upside-down photo", photo.flip(0), mask.flip(0))):
        out, report = an.project_image(v, f, uv, soft, img, msk, FOV, detail_sigma=16.0)
        before, after = err(soft, front), err(out, front)
        check(f"{label} sharpens the front", report.startswith("painted") and after < 0.55 * before,
              f"error {before:.4f} -> {after:.4f}; {report}")
        check(f"{label} leaves the back alone", (out[back] - soft[back]).abs().max().item() < 1e-5,
              f"max change {(out[back] - soft[back]).abs().max().item():.2e}")

    # the node's cameras must agree with Pixal3D's own projection (voxel grid -> its world -> pixel)
    from comfy.ldm.trellis2.model import _coords_to_proj_world, _project_points_to_image, build_proj_transform_matrix
    from comfy_extras.nodes_trellis2 import _VIEW_AZIMUTHS, _VIEW_PAD, _orbit_camera_to_world
    res = 64
    coords = torch.cat([torch.zeros(500, 1), torch.randint(8, res - 8, (500, 3)).float()], 1)
    world, _ = _coords_to_proj_world(coords, res, torch.ones(1))
    mesh_pts = coords[:, 1:] / (res - 1) - 0.5        # the decoder's mesh frame (Y up, no swap)
    rigs = [("single image", 0.0, 49.0, 1.0,
             build_proj_transform_matrix(torch.tensor([0.5 / math.tan(math.radians(49.0) / 2)]), 1, "cpu"))]
    for name, az in _VIEW_AZIMUTHS.items():
        rigs.append((f"rig {name}", az, 20.0, _VIEW_PAD,
                     _orbit_camera_to_world([az], [0.0], _VIEW_PAD * 0.5 / math.tan(math.radians(10.0)))))
    for name, az, fov, scale, c2w in rigs:
        px, _, _ = _project_points_to_image(world[None], c2w, torch.tensor([math.radians(fov)]), 1024)
        xy, _ = an.project(mesh_pts, *an.camera(az, fov, scale, torch.device("cpu")))
        gap = (xy * 1024 - px[0]).abs().max().item()
        check(f"camera matches Pixal3D ({name})", gap < 0.5, f"max {gap:.3f} px apart")

    wrong = torch.zeros(PHOTO, PHOTO)
    wrong[:120, :120] = 1
    out, report = an.project_image(v, f, uv, soft, photo, wrong, FOV)
    check("a mismatched photo is refused", report.startswith("skipped") and torch.equal(out, soft), report)
    print(f"{'all passed' if not failures else f'{failures} FAILED'} in {time.time() - t0:.0f}s")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
