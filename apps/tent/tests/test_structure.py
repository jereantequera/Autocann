"""
Module size guard.

Big modules are where unrelated changes collide and where reviews stop
reading, so no Python module under autocann/ and no dashboard script may pass
400 lines. The files that already do are listed in ALLOWLIST with their current
line count as a ceiling: they may shrink, never grow. Once a file is under the
limit it has to leave the list, so the list only ever gets shorter. The rules
behind the splits are in ARCHITECTURE.md, "Code structure".
"""

from __future__ import annotations

from pathlib import Path

import pytest

TENT_DIR = Path(__file__).parent.parent
PACKAGE_DIR = TENT_DIR / "autocann"
JS_DIR = PACKAGE_DIR / "web" / "static" / "js"

LIMIT = 400

#: Today's offenders, relative to apps/tent, with their line counts as ceilings.
ALLOWLIST: dict[str, int] = {
    "autocann/cli/simulate.py": 748,
    "autocann/config.py": 446,
    "autocann/web/app.py": 1756,
    "autocann/web/diagnostics.py": 912,
    "autocann/web/static/js/charts.js": 435,
    "autocann/web/static/js/dashboard.js": 616,
    "autocann/web/static/js/feeding.js": 644,
    "autocann/web/static/js/grows.js": 599,
    "autocann/web/static/js/health.js": 527,
    "autocann/web/static/js/system.js": 419,
}


def _sized_files() -> dict[str, int]:
    """Every guarded file, relative to apps/tent, mapped to its line count."""
    files = list(PACKAGE_DIR.rglob("*.py")) + list(JS_DIR.glob("*.js"))
    return {p.relative_to(TENT_DIR).as_posix(): p.read_bytes().count(b"\n") for p in sorted(files)}


SIZES = _sized_files()


def test_no_unlisted_file_is_over_the_limit():
    for path, lines in SIZES.items():
        if path in ALLOWLIST or lines <= LIMIT:
            continue
        pytest.fail(f"{path} has {lines} lines (limit {LIMIT}). Split it — see ARCHITECTURE.md, 'Code structure'.")


def test_allowlisted_files_do_not_grow():
    for path, ceiling in ALLOWLIST.items():
        lines = SIZES.get(path)
        assert lines is not None, f"{path} is in the allowlist but does not exist: remove it from the allowlist."
        if lines > ceiling:
            pytest.fail(f"{path} grew to {lines} lines (ceiling {ceiling}). It may only shrink; split it instead.")


def test_allowlisted_files_are_still_over_the_limit():
    for path in ALLOWLIST:
        lines = SIZES.get(path)
        assert lines is not None, f"{path} is in the allowlist but does not exist: remove it from the allowlist."
        if lines <= LIMIT:
            pytest.fail(f"{path} is down to {lines} lines: remove it from the allowlist.")
