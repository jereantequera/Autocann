"""
Things the app builds once and every blueprint reads: the Redis client, the
cache-busting token and the icon helper.
"""

from __future__ import annotations

from flask import current_app
from markupsafe import Markup, escape

from autocann import __version__
from autocann.paths import STATIC_DIR


def get_redis():
    """
    The Redis client create_app() built for this process.

    Routes read it per request from the app they are serving, instead of
    closing over it: a blueprint module is imported once, before any app
    exists, so it has nothing to close over.
    """
    return current_app.extensions["redis"]


def static_asset_version() -> str:
    """
    A cache-busting token that changes when the files behind it do.

    It used to be `__version__`, which has read "0.1.0" since the first commit
    and has never been bumped once. Every stylesheet and every module shipped
    since then went out behind the same query string, so the promise in
    index.html - that a deploy busts the browser cache - had never been kept:
    the tablet on the tent wall kept its old app.css against freshly deployed
    markup, which is worse than no cache-busting, because it looks like the
    deploy broke the page.

    The newest mtime under static/ answers it exactly, and without anyone
    having to remember anything: `git pull` stamps every file it rewrites, so
    the token moves on the deploys that changed an asset and stays put on the
    ones that did not. The version stays in front of it so the string is still
    recognisable as this app's.
    """
    try:
        newest = max(
            (path.stat().st_mtime for path in STATIC_DIR.rglob("*") if path.is_file()),
            default=0.0,
        )
    except OSError:
        # An unreadable static dir is a problem, but not this function's to
        # report: serving a stale token is better than failing to build the app.
        return __version__
    return f"{__version__}-{int(newest):x}" if newest else __version__


def icon(name: str, extra_class: str = "") -> Markup:
    """
    An icon from the sprite in partials/icons.svg, for the templates.

    The same markup icon() in static/js/util.js builds, so an icon rendered by
    the server and one rendered by a poller look the same.
    """
    classes = f"icon {extra_class}".strip()
    return Markup(
        f'<svg class="{escape(classes)}" aria-hidden="true" focusable="false">'
        f'<use href="#i-{escape(name)}"></use></svg>'
    )
