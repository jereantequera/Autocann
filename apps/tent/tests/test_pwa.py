"""
The installable dashboard: manifest, service worker, icons and head tags.

The offline behaviour itself (what is cached, what is never cached) is in
tests/js/pwa.test.mjs, which runs the worker. These tests check what the server
hands the browser.
"""

from __future__ import annotations

import json
import re
import struct

import pytest

from autocann.paths import STATIC_DIR
from autocann.web.pwa import MANIFEST, shell_urls
from tests.conftest import FakeRedis


def _static_file(url: str):
    assert url.startswith("/static/"), url
    return STATIC_DIR / url.removeprefix("/static/")


def _png_size(path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{path.name} is not a PNG"
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def _manifest(client) -> dict:
    response = client.get("/manifest.webmanifest")
    assert response.status_code == 200
    return json.loads(response.get_data(as_text=True))


def test_manifest_has_the_fields_an_install_needs(client):
    response = client.get("/manifest.webmanifest")
    assert response.mimetype == "application/manifest+json"
    data = _manifest(client)
    for key in ("name", "short_name", "start_url", "display", "background_color", "theme_color", "icons"):
        assert key in data, key
    assert data["name"] == "Autocann"
    assert data["short_name"] == "Autocann"
    assert data["start_url"] == "/"
    assert data["display"] == "standalone"


def test_manifest_icons_exist_and_have_the_size_they_claim(client):
    icons = _manifest(client)["icons"]
    for icon in icons:
        path = _static_file(icon["src"])
        assert path.is_file(), icon["src"]
        width, height = _png_size(path)
        expected = tuple(int(n) for n in icon["sizes"].split("x"))
        assert (width, height) == expected, icon["src"]
        assert icon["type"] == "image/png"
    # Android installs need a 192 and a 512; the maskable one keeps the launcher's mask off the leaf.
    sizes = {icon["sizes"] for icon in icons if icon["purpose"] == "any"}
    assert {"192x192", "512x512"} <= sizes
    assert any(icon["purpose"] == "maskable" for icon in icons)


def test_manifest_colours_are_the_grafito_surface():
    tokens = (STATIC_DIR / "css" / "tokens.css").read_text(encoding="utf-8")
    surface = re.search(r"--surface-0:\s*(#[0-9A-Fa-f]{6});", tokens).group(1)
    assert MANIFEST["background_color"].lower() == surface.lower()
    assert MANIFEST["theme_color"].lower() == surface.lower()


def test_service_worker_is_javascript_served_from_the_root(client):
    response = client.get("/sw.js")
    assert response.status_code == 200
    assert response.content_type.startswith("application/javascript")
    assert response.headers["Service-Worker-Allowed"] == "/"
    assert response.headers["Cache-Control"] == "no-cache"
    body = response.get_data(as_text=True)
    assert body.startswith("const SHELL = {")
    assert "addEventListener('fetch'" in body


def test_service_worker_precaches_the_shell_and_never_the_api(client):
    body = client.get("/sw.js").get_data(as_text=True)
    shell = json.loads(re.match(r"const SHELL = (.*?);\n", body).group(1))
    assert shell["version"]
    urls = shell["urls"]
    assert "/" in urls
    assert "/static/js/main.js" in urls
    assert "/static/css/tokens.css" in urls
    assert "/static/icons/icon-192.png" in urls
    assert "/static/vendor/fonts/ibm-plex-sans-latin.woff2" in urls
    assert not any(url.startswith("/api") for url in urls)
    for url in urls:
        if url != "/":
            assert _static_file(url).is_file(), url


def test_shell_list_is_the_whole_static_tree():
    urls = shell_urls()
    on_disk = {f"/static/{p.relative_to(STATIC_DIR).as_posix()}" for p in STATIC_DIR.rglob("*") if p.is_file()}
    assert set(urls) == on_disk | {"/"}


def test_index_links_the_manifest_and_the_icons(client):
    html = client.get("/").get_data(as_text=True)
    assert '<link rel="manifest" href="/manifest.webmanifest">' in html
    assert '<meta name="theme-color" content="#121412">' in html
    assert 'name="apple-mobile-web-app-capable" content="yes"' in html
    assert re.search(r'<link rel="apple-touch-icon" href="/static/icons/apple-touch-icon\.png', html)


def test_the_pwa_files_load_before_the_pin_is_typed(locked):
    """A 401 on either one means no install and no offline shell, so they stay open."""
    assert locked.get("/manifest.webmanifest").status_code == 200
    assert locked.get("/sw.js").status_code == 200
    # The gate itself is still in place for the page.
    assert locked.get("/").status_code == 401


@pytest.fixture
def locked(monkeypatch, temp_db):
    """A client whose backend has a PIN configured."""
    import autocann.web.app as web

    fake = FakeRedis()
    monkeypatch.setattr(web, "pooled_client", lambda *args, **kwargs: fake)
    monkeypatch.setenv("AUTOCANN_PIN", "4821")
    app = web.create_app()
    app.config.update(TESTING=True)
    return app.test_client()
