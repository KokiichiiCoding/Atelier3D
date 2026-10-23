"""The studio API end to end in demo mode (real graphs, fake engine)."""
from pathlib import Path

from conftest import png_bytes, wait_jobs

from atelier.config import CFG


def _image(client, **data):
    files = {"image": ("teapot.png", png_bytes(), "image/png")}
    r = client.post("/api/generate/image", files=files, data={"quality": "draft", **data})
    assert r.status_code == 200, r.text
    return r.json()["jobs"]


def test_status(client):
    s = client.get("/api/status").json()
    assert s["demo"] and s["capabilities"]["image3d"]
    assert "standard" in s["options"]["quality"]


def test_image_to_3d_builds_a_library_asset(client):
    jobs = _image(client, faces="60000", texture="1024", seed="11", name="Teapot")
    (job,) = wait_jobs(client, [j["id"] for j in jobs])
    assert job["status"] == "done", job["error"]
    a = client.get(f"/api/assets/{job['result']['asset']}").json()
    assert a["name"] == "Teapot" and a["kind"] == "image3d"
    assert a["params"]["seed"] == 11 and a["params"]["faces"] == 60000
    assert "image" not in a["params"] and "image" not in job["params"]  # no local paths leak
    assert a["stats"]["faces"] > 0 and set(a["maps"]) == {"base_color", "metallic", "roughness", "normal", "ao"}
    assert set(a["previews"]) == {"structure", "painted"}
    r = client.get(a["url"])
    assert r.status_code == 200 and r.content[:4] == b"glTF"
    assert r.headers["content-type"] == "model/gltf-binary"
    assert client.get(a["maps"]["normal"]).status_code == 200
    d = client.get(f"/api/assets/{a['id']}/download/glb")
    assert d.status_code == 200 and "Teapot.glb" in d.headers["content-disposition"]
    z = client.get(f"/api/assets/{a['id']}/download/textures")
    assert z.status_code == 200 and z.content[:2] == b"PK"


def test_variations_get_consecutive_seeds(client):
    jobs = _image(client, seed="500", variations="3")
    assert [j["params"]["seed"] for j in jobs] == [500, 501, 502]
    done = wait_jobs(client, [j["id"] for j in jobs])
    assert all(j["status"] == "done" for j in done)
    assert len(client.get("/api/assets").json()) == 3


def test_upload_is_cleaned_and_validated(client):
    jobs = _image(client)
    up = sorted(CFG.uploads_dir.glob("*.png"))
    assert len(up) == 1
    r = client.post("/api/generate/image", files={"image": ("x.png", b"not an image", "image/png")})
    assert r.status_code == 400 and "not a readable image" in r.json()["detail"]
    r = client.post("/api/generate/image", files={"image": ("x.png", png_bytes()), }, data={"texture": "3000"})
    assert r.status_code == 400
    r = client.post("/api/generate/image", files={"image": ("x.png", png_bytes())}, data={"variations": "9"})
    assert r.status_code == 400
    wait_jobs(client, [j["id"] for j in jobs])


def test_transparent_upload_is_flattened_on_grey(client):
    from PIL import Image

    client.post("/api/generate/image", files={"image": ("cut.png", png_bytes(mode="RGBA", color=(255, 0, 0, 0)))})
    (p,) = CFG.uploads_dir.glob("*.png")
    img = Image.open(p)
    assert img.mode == "RGB" and img.getpixel((0, 0)) == (128, 128, 128)


def test_multiview(client):
    files = {"front": ("f.png", png_bytes(), "image/png"), "back": ("b.png", png_bytes(), "image/png")}
    r = client.post("/api/generate/multiview", files=files, data={"quality": "draft"})
    assert r.status_code == 200, r.text
    (job,) = wait_jobs(client, [r.json()["jobs"][0]["id"]])
    assert job["status"] == "done", job["error"]
    a = client.get(f"/api/assets/{job['result']['asset']}").json()
    assert a["kind"] == "multiview3d" and a["source"]["views"] == ["front", "back"]
    assert client.post("/api/generate/multiview", data={"quality": "draft"}).status_code == 422


def test_text_to_3d_via_concepts(client):
    r = client.post("/api/generate/concepts", json={"prompt": "a brass owl", "count": 2, "style": "toy"})
    assert r.status_code == 200, r.text
    (cj,) = wait_jobs(client, [r.json()["job"]["id"]])
    assert cj["status"] == "done" and len(cj["result"]["images"]) == 2
    assert "vinyl toy" in cj["result"]["prompt"]
    assert client.get(cj["result"]["images"][1]).status_code == 200
    r = client.post("/api/generate/from-concept", json={"job_id": cj["id"], "index": 1, "quality": "draft"})
    assert r.status_code == 200, r.text
    (job,) = wait_jobs(client, [r.json()["jobs"][0]["id"]])
    a = client.get(f"/api/assets/{job['result']['asset']}").json()
    assert a["source"]["mode"] == "text" and a["name"] == "a brass owl"
    assert client.post("/api/generate/from-concept", json={"job_id": cj["id"], "index": 7}).status_code == 404
    assert client.post("/api/generate/concepts", json={"prompt": "  "}).status_code == 400
    assert client.post("/api/generate/concepts", json={"prompt": "x", "style": "nope"}).status_code == 400


def test_remix_reuses_the_input(client):
    (job,) = wait_jobs(client, [_image(client, seed="3")[0]["id"]])
    aid = job["result"]["asset"]
    r = client.post(f"/api/assets/{aid}/remix", json={"quality": "high", "seed": 3})
    assert r.status_code == 200, r.text
    new = r.json()["jobs"][0]
    assert new["params"]["quality"] == "high" and new["params"]["seed"] == 3
    (done,) = wait_jobs(client, [new["id"]])
    assert done["status"] == "done"


def test_ultra_polycount_and_photo_paint_switch(client):
    (job,) = _image(client, faces="2000000", texture="4096", photo_paint="false", seed="8")
    assert job["params"]["faces"] == 2_000_000 and job["params"]["photo_paint"] is False
    (done,) = wait_jobs(client, [job["id"]])
    aid = done["result"]["asset"]
    # a rebuild keeps the seed and the paint choice, with a new polycount
    new = client.post(f"/api/assets/{aid}/remix", json={"seed": 8, "faces": 30000}).json()["jobs"][0]
    assert (new["params"]["seed"], new["params"]["faces"], new["params"]["photo_paint"]) == (8, 30000, False)
    assert _image(client)[0]["params"]["photo_paint"] is True
    r = client.post("/api/generate/image", files={"image": ("t.png", png_bytes(), "image/png")},
                    data={"faces": "2000001"})
    assert r.status_code == 400 and "faces" in r.json()["detail"]


def test_library_edit_thumb_and_delete(client):
    (job,) = wait_jobs(client, [_image(client)[0]["id"]])
    aid = job["result"]["asset"]
    a = client.patch(f"/api/assets/{aid}", json={"name": "  Lamp ", "favorite": True}).json()
    assert a["name"] == "Lamp" and a["favorite"]
    assert [x["id"] for x in client.get("/api/assets?favorites=true").json()] == [aid]
    assert client.get("/api/assets?q=zzz").json() == []
    assert client.patch(f"/api/assets/{aid}", json={"name": " "}).status_code == 400
    r = client.put(f"/api/assets/{aid}/thumb", content=png_bytes((900, 900)), headers={"content-type": "image/png"})
    assert r.status_code == 200
    assert client.get(f"/api/assets/{aid}").json()["thumb_rendered"] is True
    assert client.put(f"/api/assets/{aid}/thumb", content=b"junk").status_code == 400
    assert client.delete(f"/api/assets/{aid}").status_code == 200
    assert not (CFG.outputs_dir / aid).exists()
    assert client.get(f"/api/assets/{aid}").status_code == 404


def test_cancel_a_queued_job(client, monkeypatch):
    import atelier.demo as demo

    monkeypatch.setattr(demo, "SPEED", 2.0)  # keep the first job busy
    first = _image(client)[0]
    second = _image(client)[0]
    assert client.post(f"/api/jobs/{second['id']}/cancel").json()["ok"]
    assert client.get(f"/api/jobs/{second['id']}").json()["status"] == "cancelled"
    client.post(f"/api/jobs/{first['id']}/cancel")
    (j,) = wait_jobs(client, [first["id"]])
    assert j["status"] == "cancelled"
    assert client.delete(f"/api/jobs/{first['id']}").status_code == 200


def test_files_route_is_confined(client):
    CFG.outputs_dir.mkdir(parents=True, exist_ok=True)
    (CFG.outputs_dir.parent / "secret.png").write_bytes(b"x")
    assert client.get("/files/../secret.png").status_code == 404
    assert client.get("/files/%2e%2e/secret.png").status_code == 404
    assert client.get("/api/assets/../../etc").status_code == 404


def test_cross_site_and_foreign_host_are_refused(client):
    r = client.post("/api/engine/start", headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    r = client.get("/api/status", headers={"host": "attacker.example"})
    assert r.status_code == 403
    assert client.post("/api/engine/start", headers={"origin": "http://127.0.0.1:7870"}).status_code == 200


def test_real_mode_needs_the_core_pack(client, monkeypatch):
    monkeypatch.setattr(CFG, "demo", False)
    r = client.post("/api/generate/image", files={"image": ("x.png", png_bytes())})
    assert r.status_code == 409 and "Pixal3D core" in r.json()["detail"]
    st = client.get("/api/status").json()
    assert st["capabilities"] == {"image3d": False, "multiview": False, "trellis2": False, "text3d": False}
    assert st["engine"]["state"] in ("stopped", "missing")


def test_studio_page_is_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "Atelier" in r.text
    assert client.get("/js/app.js").headers["content-type"].startswith("text/javascript")
    assert Path(CFG.models_dir).exists()
