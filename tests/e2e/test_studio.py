"""Browser journey through the real UI on the demo engine: image to 3D, text to 3D via
concepts, library, models page. Skips when Playwright or a browser is missing.

Locally: pip install playwright, then `playwright install chromium`, or point
ATELIER_E2E_BROWSER at any Chromium executable (or ATELIER_E2E_CHANNEL=msedge)."""
import os
import socket
import threading
import time

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from conftest import png_bytes  # noqa: E402


@pytest.fixture
def studio(private_dirs):
    import uvicorn

    from atelier.api import create_app

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    while not server.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    t.join(10)


@pytest.fixture
def page(studio):
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel=os.environ.get("ATELIER_E2E_CHANNEL") or None,
                                        executable_path=os.environ.get("ATELIER_E2E_BROWSER") or None)
        except Exception as e:  # no browser installed
            pytest.skip(f"no browser for e2e: {e}")
        pg = browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        pg.on("console", lambda m: m.type == "error" and errors.append(m.text))
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(studio + "/#/create")
        yield pg
        browser.close()
        assert not errors, errors


def test_image_and_text_journeys(page, private_dirs):
    img = private_dirs / "teapot.png"
    img.write_bytes(png_bytes((400, 400)))

    # Image -> 3D
    page.click(".modes button[data-mode=image]")
    page.set_input_files("#file", str(img))
    page.click("#generate")
    page.wait_for_selector("#asset-panel .asset-title input", timeout=30_000)
    assert page.input_value("#asset-panel .asset-title input") == "teapot"
    page.wait_for_selector("#vp-stats span")
    for mode in ("clay", "wire", "uv", "lit"):
        page.click(f"#view-mode button[data-v={mode}]")
    assert page.locator("#asset-panel .maps button").count() == 5

    # Text -> concepts -> 3D
    page.click(".modes button[data-mode=text]")
    page.fill("#prompt", "a brass owl")
    page.click("#generate")
    page.wait_for_selector(".concept img", timeout=30_000)
    assert page.locator(".concept img").count() == 4
    page.hover(".concept >> nth=0")
    page.click(".concept >> nth=0 >> .make")
    page.wait_for_function("document.querySelectorAll('#recent .thumb-btn').length === 2", timeout=30_000)
    assert page.input_value("#asset-panel .asset-title input") == "a brass owl"

    # Library and Models
    page.click(".tabs a[data-route=library]")
    page.wait_for_selector(".lib-card")
    assert page.locator(".lib-card").count() == 2
    page.click(".tabs a[data-route=models]")
    page.wait_for_selector(".pack")
    assert page.locator(".pack").count() == 5
