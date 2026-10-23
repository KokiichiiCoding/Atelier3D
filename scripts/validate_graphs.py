"""Check every Atelier3D graph against the pinned engine, without running anything.

Loads ComfyUI's node definitions (no models, no GPU) and runs its own prompt validation on
each graph the studio can build. Run it after changing atelier/graphs.py or the engine pin:

    engine\\.venv\\Scripts\\python.exe scripts\\validate_graphs.py

Model files that are not installed are reported as "skipped (missing model)" rather than
failures; any other validation error fails the script. ``--write-fixture`` also adds the schema
of any node the graphs use that tests/fixtures/object_info.json lacks (new V3 nodes).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / "engine" / "ComfyUI"
DATA = ROOT / "engine" / "data"
FIXTURE = ROOT / "tests" / "fixtures" / "object_info.json"
WRITE_FIXTURE = "--write-fixture" in sys.argv

sys.argv = [sys.argv[0], "--cpu", "--base-directory", str(DATA), "--models-directory",
            str(ROOT / "models"), "--disable-all-custom-nodes"]
sys.path[:0] = [str(ENGINE), str(ROOT)]
os.chdir(ENGINE)
logging.basicConfig(level=logging.WARNING)

import comfy.options  # noqa: E402

comfy.options.enable_args_parsing()
import execution  # noqa: E402
import nodes  # noqa: E402
from PIL import Image  # noqa: E402

from atelier import graphs  # noqa: E402

MODEL_INPUTS = {"unet_name", "ckpt_name", "clip_name", "vae_name", "model_name", "bg_removal_name"}


def cases():
    img = "atelier/_validate.png"
    yield "image pixal3d standard", graphs.image_to_3d(img, seed=1)
    yield "image pixal3d high 4k", graphs.image_to_3d(img, seed=1, quality="high", texture=4096, faces=700_000)
    yield "image pixal3d draft 1k", graphs.image_to_3d(img, seed=1, quality="draft", texture=1024, faces=30_000)
    yield "image pixal3d high 4k 2M", graphs.image_to_3d(img, seed=1, quality="high", texture=4096, faces=2_000_000)
    yield "image trellis2", graphs.image_to_3d(img, seed=1, engine="trellis2")
    yield "multiview 4 views", graphs.image_to_3d(views={v: img for v in graphs.VIEWS}, seed=1)
    yield "multiview front+back", graphs.image_to_3d(views={"front": img, "back": img}, seed=1)
    for model in ("zimage", "netayume", "sdxl:novaAnimeXL_ilV140.safetensors"):
        p, n = graphs.shape_prompt("a red teapot", "stylized")
        yield f"concepts {model}", graphs.concepts(model, p, n, seed=1, count=4)


async def main() -> int:
    await nodes.init_extra_nodes(init_custom_nodes=False, init_api_nodes=False)
    assert await nodes.load_custom_node(str(ROOT / "engine_nodes" / "atelier3d")), "engine_nodes failed to load"
    if WRITE_FIXTURE:
        write_fixture()
    (DATA / "input" / "atelier").mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 64), "white").save(DATA / "input" / "atelier" / "_validate.png")
    failed = 0
    for name, g in cases():
        ok, err, good, node_errors = await execution.validate_prompt("validate", g.prompt(), None)
        problems = [(nid, e) for nid, ne in node_errors.items() for e in ne.get("errors", [])]
        missing = [e for _, e in problems if e.get("type") == "value_not_in_list"
                   and e.get("extra_info", {}).get("input_name") in MODEL_INPUTS]
        real = [(nid, e) for nid, e in problems if e not in missing]
        if real or (not ok and not missing):
            failed += 1
            print(f"FAIL {name}: {err.get('message') if err else ''}")
            for nid, e in real:
                print(f"     node {nid} {g.nodes[nid]['class_type']}: {e.get('message')} {e.get('details')}")
        elif missing:
            names = sorted({e['extra_info'].get('received_value') for e in missing})
            print(f"skip {name} (missing model: {', '.join(map(str, names))})")
        else:
            assert set(good) == set(g.outputs), (name, set(g.outputs) - set(good))
            print(f"ok   {name} ({len(g.nodes)} nodes, {len(good)} outputs)")
    return failed


def write_fixture() -> None:
    info = json.loads(FIXTURE.read_text())
    used = {n["class_type"] for _, g in cases() for n in g.nodes.values()}
    for ct in sorted(used - set(info)):
        info[ct] = nodes.NODE_CLASS_MAPPINGS[ct].GET_NODE_INFO_V1()  # V1 nodes come from /object_info
        print(f"fixture: added {ct}")
    FIXTURE.write_text(json.dumps(info, indent=1, sort_keys=True) + "\n", newline="\n")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
