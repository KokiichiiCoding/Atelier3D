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
    with pytest.raises(ValueError):
        (tmp_path / "bad.glb").write_bytes(b"nope" * 10)
        glb.stats(tmp_path / "bad.glb")


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


def test_fbx_export_without_blender(tmp_path, monkeypatch):
    monkeypatch.setattr(exporters, "find_blender", lambda: None)
    p = tmp_path / "model.glb"
    p.write_bytes(demo_glb(random.Random(2)))
    with pytest.raises(FileNotFoundError):
        exporters.export(p, "fbx")
    with pytest.raises(ValueError):
        exporters.export(p, "max")
