"""Progress tracking, GLB helpers, engine error messages and the Blender exporter guard."""
import random

import httpx
import pytest

from atelier import exporters, glb, graphs
from atelier.demo import demo_glb
from atelier.engine import Event, describe_rejection
from atelier.pipeline import Tracker


def test_tracker_walks_every_stage_in_order():
    g = graphs.image_to_3d("x.png", seed=1)
    t = Tracker(g, graphs.STAGES_3D)
    last, seen = 0.0, []
    for nid, node in g.nodes.items():
        t.feed(Event("executing", node=nid))
        if node["class_type"] == "KSampler":
            for v in (0.25, 0.5, 1.0):
                t.feed(Event("progress", node=nid, value=v))
                assert t.snapshot()["overall"] >= last
        snap = t.snapshot()
        assert snap["overall"] >= last
        last = snap["overall"]
        if snap["stage"] not in seen:
            seen.append(snap["stage"])
    t.feed(Event("executed", node=list(g.nodes)[-1]))
    snap = t.snapshot()
    assert seen == [s for s, _, _ in graphs.STAGES_3D]
    assert snap["overall"] == pytest.approx(1.0) and all(s["state"] == "done" for s in snap["stages"])


def test_tracker_counts_cached_nodes_as_done():
    g = graphs.image_to_3d("x.png", seed=1)
    t = Tracker(g, graphs.STAGES_3D)
    t.feed(Event("cached", nodes=[n for n, s in g.stage_of.items() if s == "prepare"]))
    assert t.stage_fraction("prepare") == 1.0 and t.snapshot()["overall"] == pytest.approx(0.08)


def test_glb_roundtrip_stats(tmp_path):
    p = tmp_path / "m.glb"
    p.write_bytes(demo_glb(random.Random(1)))
    s = glb.stats(p)
    assert s["faces"] > 1000 and s["vertices"] > 500 and s["maps"] == ["base_color"]
    assert s["size"][1] == pytest.approx(1.2, abs=1e-3)
    report = glb.inspect(p, target_faces=10)
    assert report["ready"] and report["checks"]["base_color"] and report["checks"]["uv"]
    assert any("above" in note for note in report["notes"])
    with pytest.raises(ValueError):
        (tmp_path / "bad.glb").write_bytes(b"nope" * 10)
        glb.stats(tmp_path / "bad.glb")


def test_inspection_rejects_an_untextured_mesh(tmp_path):
    import json
    import struct

    original = tmp_path / "original.glb"
    original.write_bytes(demo_glb(random.Random(4)))
    raw = original.read_bytes()
    json_len = struct.unpack_from("<I", raw, 12)[0]
    doc = json.loads(raw[20:20 + json_len])
    doc["materials"][0]["pbrMetallicRoughness"].pop("baseColorTexture")
    encoded = json.dumps(doc).encode()
    encoded += b" " * (-len(encoded) % 4)
    binary = raw[20 + json_len:]
    broken = tmp_path / "grey.glb"
    broken.write_bytes(struct.pack("<III", 0x46546C67, 2, 20 + len(encoded) + len(binary))
                       + struct.pack("<II", len(encoded), 0x4E4F534A) + encoded + binary)
    report = glb.inspect(broken)
    assert not report["ready"]
    assert any("base-color texture" in problem for problem in report["problems"])


def test_lathe_faces_point_outwards():
    pos, nrm, uv, idx = glb.lathe([(0.5, 0.0), (0.5, 1.0)], segments=4)
    a, b, c = (pos[3 * i: 3 * i + 3] for i in idx[:3])
    e1 = [b[k] - a[k] for k in range(3)]
    e2 = [c[k] - a[k] for k in range(3)]
    n = [e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2], e1[0] * e2[1] - e1[1] * e2[0]]
    centroid = [(a[k] + b[k] + c[k]) / 3 for k in range(3)]
    assert n[0] * centroid[0] + n[2] * centroid[2] > 0  # same direction as the radius


def test_engine_rejections_are_readable():
    r = httpx.Response(400, json={"error": {"message": "Prompt outputs failed validation"},
                                  "node_errors": {"5": {"class_type": "UNETLoader", "errors": [
                                      {"message": "Value not in list", "details": "unet_name: 'x' not in []"}]}}})
    msg = describe_rejection(r)
    assert msg.startswith("Prompt outputs failed validation") and "UNETLoader: Value not in list" in msg
    assert "500" in describe_rejection(httpx.Response(500, text="boom"))


def test_engine_crash_on_a_blocked_gpu_is_explained():
    from atelier.engine import GPU_BLOCKED, explain_exit

    log = ("RuntimeError: Unexpected error from cudaGetDeviceCount(). Did you run some cuda "
           "functions before calling NumCudaDevices() that might have already set an error? "
           "Error 1: invalid argument\n")
    assert explain_exit(log, 1) == GPU_BLOCKED and "Restart the PC" in GPU_BLOCKED
    assert "code 1" in explain_exit("ImportError: no module named x\n", 1)


def test_fbx_export_without_blender(tmp_path, monkeypatch):
    monkeypatch.setattr(exporters, "find_blender", lambda: None)
    p = tmp_path / "model.glb"
    p.write_bytes(demo_glb(random.Random(2)))
    with pytest.raises(FileNotFoundError):
        exporters.export(p, "fbx")
    with pytest.raises(ValueError):
        exporters.export(p, "max")


@pytest.mark.skipif(not exporters.find_blender(), reason="Blender not installed")
def test_blender_exports_carry_textures(tmp_path):
    import zipfile

    p = tmp_path / "model.glb"
    p.write_bytes(demo_glb(random.Random(3)))
    assert b"\x89PNG" in exporters.export(p, "fbx").read_bytes()  # texture embedded
    names = zipfile.ZipFile(exporters.export(p, "obj")).namelist()
    assert {"model.obj", "model.mtl"} <= set(names) and any(n.endswith(".png") for n in names)
    small = exporters.export(p, "draco").read_bytes()
    assert small[:4] == b"glTF" and b"KHR_draco_mesh_compression" in small and b"\x89PNG" in small


def test_engine_stop_kills_the_whole_process_tree():
    """The engine runs under a venv python.exe, which on Windows is a launcher with the real
    interpreter as its child; stop() must not leave that child holding RAM and VRAM."""
    import socket
    import subprocess
    import sys
    import time

    from atelier.engine import EngineProcess

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    child = (f"import socket,time; s=socket.socket(); s.bind(('127.0.0.1',{port})); s.listen(); "
             "time.sleep(120)")
    e = EngineProcess()
    e.proc = subprocess.Popen([sys.executable, "-c", child])
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    e.stop()
    time.sleep(0.5)
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
