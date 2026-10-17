"""
The URL map: the same rules and methods as before the split into blueprints.

tests/fixtures/routes.json was dumped from the single-file app, before
autocann/web/api existed. Endpoint names are left out on purpose: they follow
the blueprints, and the URLs and methods are what clients depend on.
"""

from __future__ import annotations

import json
from pathlib import Path

from autocann.web.app import create_app

FIXTURE = Path(__file__).parent / "fixtures" / "routes.json"


def _url_map() -> list[dict]:
    app = create_app()
    rows = [
        {"rule": str(rule), "methods": sorted(set(rule.methods) - {"HEAD", "OPTIONS"})}
        for rule in app.url_map.iter_rules()
    ]
    return sorted(rows, key=lambda row: (row["rule"], row["methods"]))


def test_the_url_map_is_the_one_in_the_fixture():
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert _url_map() == expected
