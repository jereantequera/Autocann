"""
The Flask app: wiring only.

The routes live in autocann/web/api, one blueprint per area. This module builds
the app, attaches what the blueprints share, and registers them, the
diagnostics and the PIN gate.
"""

from __future__ import annotations

from flask import Flask

from autocann.config import redis_config_from_env
from autocann.paths import STATIC_DIR, TEMPLATES_DIR
from autocann.redis_pool import pooled_client
from autocann.web.api import ALL_BLUEPRINTS
from autocann.web.auth import register_auth
from autocann.web.deps import icon, static_asset_version
from autocann.web.diagnostics import register_diagnostics


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=str(TEMPLATES_DIR),
        static_folder=str(STATIC_DIR),
        static_url_path="/static",
    )

    # Computed once, here: the services restart on every deploy, so there is
    # nothing a per-request stat would catch that this misses, and walking
    # static/ on every page load would be a directory scan per visit.
    asset_version = static_asset_version()

    @app.context_processor
    def inject_version():
        # Appended to static URLs so a deploy busts the browser cache without
        # having to serve the assets with no-cache.
        return {"asset_version": asset_version, "icon": icon}

    redis_cfg = redis_config_from_env()
    # One client for the life of the process, with a bounded pool and a socket
    # that gives up: see autocann/redis_pool.py for what the defaults cost.
    # The blueprints read it per request through autocann/web/deps.get_redis().
    redis_client = pooled_client(redis_cfg)
    app.extensions["redis"] = redis_client

    for blueprint in ALL_BLUEPRINTS:
        app.register_blueprint(blueprint)

    # ¿Anda la instalación?, y el log de los nodos. En su propio módulo
    # porque es lo único acá que no contesta sobre la carpa sino sobre el
    # equipamiento que la mide.
    register_diagnostics(app, redis_client)

    # El PIN va último, después de que existan todas las rutas: el gate es un
    # before_request sobre la app entera, así que no puede quedar ninguna ruta
    # registrada después que se le escape. Sin AUTOCANN_PIN no instala nada y
    # todo se comporta exactamente como antes.
    register_auth(app)

    return app


# Convenience for WSGI servers
app = create_app()
