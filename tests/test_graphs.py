"""Graphs are checked offline against the engine's own node schemas (tests/fixtures/
object_info.json, dumped from the pinned ComfyUI). scripts/validate_graphs.py does the same
inside the real engine."""
import json
from pathlib import Path

import pytest

from atelier import graphs

OI = json.loads((Path(__file__).parent / "fixtures" / "object_info.json").read_text())


def check(g: graphs.Graph):
    """Every input exists, links point at real outputs of the right type, required inputs set."""
    for nid, node in g.nodes.items():
        ct = node["class_type"]
        assert ct in OI, f"unknown node {ct}"
        spec = OI[ct]["input"]
        req, opt = spec.get("required", {}), spec.get("optional", {})
        allowed = dict(req, **opt)
        for name, s in list(allowed.items()):  # dynamic combo sub-inputs are "<combo>.<input>"
            if s[0] == "COMFY_DYNAMICCOMBO_V3":
                for o in s[1]["options"]:
                    for sub, sub_spec in o["inputs"].get("required", {}).items():
                        allowed[f"{name}.{sub}"] = sub_spec
        for name, value in node["inputs"].items():
            assert name in allowed, f"{ct} has no input {name}"
            want = allowed[name][0]
            if isinstance(value, list):  # a link
                src = g.nodes[value[0]]
                outs = OI[src["class_type"]]["output"]
                assert value[1] < len(outs), f"{ct}.{name}: bad slot {value}"
                got = outs[value[1]]
                assert got in (want if isinstance(want, list) else want.split(",")) or want == "*", \
                    f"{ct}.{name} wants {want}, gets {got} from {src['class_type']}"
            elif want == "COMBO" or isinstance(want, list):
                options = want if isinstance(want, list) else allowed[name][1].get("options", [])
                if options and name not in ("unet_name", "ckpt_name", "clip_name", "vae_name",
                                            "model_name", "bg_removal_name", "image"):
                    assert value in options, f"{ct}.{name}={value!r} not in {options}"
            elif want == "COMFY_DYNAMICCOMBO_V3":
                keys = [o["key"] for o in allowed[name][1]["options"]]
                assert value in keys
        for name in req:
            assert name in node["inputs"], f"{ct} is missing required input {name}"


@pytest.mark.parametrize("kw", [
    dict(),
    dict(quality="draft", texture=1024, faces=30_000),
    dict(quality="high", texture=4096, faces=700_000),
    dict(engine="trellis2"),
])
def test_image_graph_matches_engine_schema(kw):
    g = graphs.image_to_3d("atelier/x.png", seed=7, **kw)
    check(g)
    assert set(g.outputs.values()) >= {"model", "input", "preview_structure", "preview_painted",
                                        "map_base_color", "map_normal", "map_ao"}
    stages = {s for s, _, _ in graphs.STAGES_3D}
    assert set(g.stage_of.values()) <= stages


def test_trellis_skips_moge_and_pixal_conditioning():
    kinds = {n["class_type"] for n in graphs.image_to_3d("x.png", seed=1, engine="trellis2").nodes.values()}
    assert "Trellis2Conditioning" in kinds and "MoGeInference" not in kinds
    kinds = {n["class_type"] for n in graphs.image_to_3d("x.png", seed=1).nodes.values()}
    assert "Pixal3DConditioning" in kinds and "MoGeInference" in kinds


def test_multiview_graph():
    g = graphs.image_to_3d(views={"front": "a.png", "back": "b.png"}, seed=3)
    check(g)
    cond = next(n for n in g.nodes.values() if n["class_type"] == "Pixal3DMultiViewConditioning")
    assert set(cond["inputs"]) == {"clip_vision_model", "fov", "front", "back"}
    unet = next(n for n in g.nodes.values() if n["class_type"] == "UNETLoader")
    assert unet["inputs"]["unet_name"] == graphs.MULTIVIEW_UNET


def test_seeds_and_settings_reach_the_graph():
    g = graphs.image_to_3d("x.png", seed=100, faces=42_000, texture=1024, quality="draft")
    seeds = sorted(n["inputs"]["seed"] for n in g.nodes.values() if n["class_type"] == "KSampler")
    assert seeds == [100, 101, 101, 102]
    dec = next(n for n in g.nodes.values() if n["class_type"] == "DecimateMesh")
    assert dec["inputs"]["target_face_count"] == 42_000
    bake = next(n for n in g.nodes.values() if n["class_type"] == "BakeTextureFromVoxel")
    assert bake["inputs"]["texture_size"] == 1024
    steps = [n["inputs"]["steps"] for n in g.nodes.values() if n["class_type"] == "KSampler"]
    assert max(steps) == graphs.QUALITY["draft"]["shape_steps"]


@pytest.mark.parametrize("bad", [
    dict(quality="ultra"), dict(faces=10), dict(faces=5_000_000), dict(texture=3000), dict(engine="tripo"),
])
def test_bad_settings_are_rejected(bad):
    with pytest.raises(ValueError):
        graphs.image_to_3d("x.png", seed=1, **bad)


def test_image_xor_views():
    with pytest.raises(ValueError):
        graphs.image_to_3d(seed=1)
    with pytest.raises(ValueError):
        graphs.image_to_3d("x.png", views={"front": "y.png"}, seed=1)
    with pytest.raises(ValueError):
        graphs.image_to_3d(views={"top": "y.png"}, seed=1)


@pytest.mark.parametrize("model", ["zimage", "netayume", "sdxl:my_model.safetensors"])
def test_concept_graphs(model):
    p, n = graphs.shape_prompt("a red teapot", "toy", character=False)
    g = graphs.concepts(model, p, n, seed=5, count=3)
    check(g)
    assert list(g.outputs.values()) == ["concept"]
    latent = next(n for n in g.nodes.values() if "batch_size" in n["inputs"])
    assert latent["inputs"]["batch_size"] == 3
    text = " ".join(n["inputs"].get("text", "") for n in g.nodes.values())
    assert "a red teapot" in text


def test_concepts_reject_unknown_model_and_count():
    with pytest.raises(ValueError):
        graphs.concepts("dalle", "x", "y", seed=1)
    with pytest.raises(ValueError):
        graphs.concepts("zimage", "x", "y", seed=1, count=9)


def test_shape_prompt():
    p, n = graphs.shape_prompt("  A brass owl. ", "realistic", character=True)
    assert p.startswith("A brass owl, photorealistic")
    assert "A-pose" in p and "white background" in p
    assert "cropped" in n
    with pytest.raises(ValueError):
        graphs.shape_prompt("x", "vaporwave")
