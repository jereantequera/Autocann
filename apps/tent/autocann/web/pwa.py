"""
Installable dashboard: the web app manifest and the service worker.

The manifest lets the phone install the dashboard to its home screen and open
it full screen. The service worker caches the static shell (the page, CSS, JS,
fonts and icons), so the page still opens when the Raspberry cannot be reached
and the dashboard shows its own "Sin conexión" state. It never caches /api/:
the numbers on screen are the backend's, and a stale copy of them would look
like a live reading.

Browsers only run service workers on HTTPS or localhost. On plain HTTP over
the LAN the manifest still makes the dashboard installable, but no shell is
cached. How the Raspberry is served on the LAN decides which of these apply.
"""

from __future__ import annotations

import hashlib
import json

from flask import Flask, Response

from autocann.paths import STATIC_DIR, WEB_DIR

#: The worker's source. Served from the root, with the shell list put in front
#: of it by `register_pwa`, so the file itself stays plain JavaScript.
WORKER_PATH = WEB_DIR / "sw_worker.js"

#: Grafito's surface-0 (static/css/tokens.css). Both the status bar and the
#: splash screen take the dashboard's background, not a brand colour.
THEME_COLOR = "#121412"

MANIFEST = {
    "name": "Autocann",
    "short_name": "Autocann",
    "description": "Control de la carpa de cultivo",
    "lang": "es",
    "start_url": "/",
    "scope": "/",
    "display": "standalone",
    "background_color": THEME_COLOR,
    "theme_color": THEME_COLOR,
    "icons": [
        {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
        {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
        {"src": "/static/icons/icon-maskable-512.png", "sizes": "512x512", "type": "image/png",
         "purpose": "maskable"},
    ],
}


def shell_urls() -> list[str]:
    """The page and every file under static/, the things the offline shell needs."""
    files = sorted(path.relative_to(STATIC_DIR).as_posix() for path in STATIC_DIR.rglob("*") if path.is_file())
    return ["/"] + [f"/static/{name}" for name in files]


def register_pwa(app: Flask, asset_version: str) -> None:
    """
    Serve the manifest and the service worker.

    The shell's cache name is the asset version plus a digest of the worker's
    source: a deploy that changes only the worker still gets a new cache, and
    one that changes only the assets gets a new cache too.
    """
    worker_source = WORKER_PATH.read_text(encoding="utf-8")
    digest = hashlib.sha256(worker_source.encode("utf-8")).hexdigest()[:8]
    shell = {"version": f"{asset_version}-{digest}", "urls": shell_urls()}
    worker = f"const SHELL = {json.dumps(shell)};\n\n{worker_source}"

    @app.route("/manifest.webmanifest", methods=["GET"])
    def manifest():
        return Response(json.dumps(MANIFEST, ensure_ascii=False, indent=2), mimetype="application/manifest+json")

    @app.route("/sw.js", methods=["GET"])
    def service_worker():
        response = Response(worker, mimetype="application/javascript")
        # The worker's scope is the whole site, so it can serve the root page.
        response.headers["Service-Worker-Allowed"] = "/"
        # Browsers re-fetch the worker on navigation anyway; never let an
        # intermediate cache hold on to an old one.
        response.headers["Cache-Control"] = "no-cache"
        return response
