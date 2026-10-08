"""
Frontend checks.

The JavaScript tests run under node when it is available and are skipped
otherwise: the Raspberry needs no JS toolchain to run the dashboard, so it must
not need one to run the suite either.
"""

from __future__ import annotations

import re
import shutil
import subprocess

import pytest

from autocann.paths import STATIC_DIR, TEMPLATES_DIR

JS_DIR = STATIC_DIR / "js"


def _strip_js_comments(source: str) -> str:
    """Drop // and /* */ comments so a pattern named in prose is not flagged."""
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"(?m)//.*$", "", source)


@pytest.mark.skipif(shutil.which("node") is None, reason="node no está instalado")
def test_javascript_unit_tests_pass():
    result = subprocess.run(
        ["node", "--test", "tests/js/util.test.mjs"],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_no_inline_onclick_handlers_remain():
    """
    Handlers built as strings out of database values are what produced the XSS
    in the grow list; they are also unreachable from ES modules.
    """
    offenders = [
        path.name
        for path in TEMPLATES_DIR.rglob("*.html")
        if "onclick=" in path.read_text()
    ]
    assert offenders == []


def test_the_page_carries_no_inline_style_or_script_block():
    """CSS and JS live in cacheable static files, not in the template."""
    index = (TEMPLATES_DIR / "index.html").read_text()
    assert "<style>" not in index
    assert not re.search(r"<script(?![^>]*\bsrc=)", index)


def test_chart_library_is_vendored_not_loaded_from_a_cdn():
    """A tent network may have no internet, and a failed CDN load is silent."""
    for path in TEMPLATES_DIR.rglob("*.html"):
        text = path.read_text()
        assert "cdn.jsdelivr.net" not in text, path.name
        assert "cdnjs.cloudflare.com" not in text, path.name
    assert (STATIC_DIR / "vendor" / "chart.umd.min.js").exists()
    assert (STATIC_DIR / "vendor" / "chartjs-plugin-annotation.min.js").exists()


def test_every_module_import_resolves_to_a_real_export():
    """A missing export is a blank dashboard, and only shows up at runtime."""
    exports: dict[str, set[str]] = {}
    for path in JS_DIR.glob("*.js"):
        text = path.read_text()
        names: set[str] = set()
        for match in re.finditer(r"^export \{([^}]*)\};", text, re.M):
            names |= {n.strip() for n in match.group(1).split(",") if n.strip()}
        names |= set(re.findall(r"^export (?:const|function|let)\s+(\w+)", text, re.M))
        exports[path.name] = names

    problems = []
    for path in JS_DIR.glob("*.js"):
        for match in re.finditer(r"import \{([^}]*)\} from '\./(\w+\.js)'", path.read_text(), re.S):
            for name in (n.strip() for n in match.group(1).split(",")):
                if name and name not in exports.get(match.group(2), set()):
                    problems.append(f"{path.name} importa '{name}' de {match.group(2)}")
    assert problems == []


def test_stage_ranges_are_not_hardcoded_in_the_frontend():
    """They come from /api/config so vpd_math.py stays the only source."""
    for path in list(JS_DIR.glob("*.js")) + list(TEMPLATES_DIR.rglob("*.html")):
        if path.name == "state.js":
            continue  # holds only the pre-first-response fallback
        text = path.read_text()
        assert "early_veg: { min:" not in text, path.name
        assert "'early_veg': {" not in text, path.name


def test_chart_options_are_never_self_assigned():
    """
    `chart.options` is a resolver proxy in Chart.js v4. Writing back a value the
    proxy just resolved (`x.a = x.a || {}`) makes its setter recurse until the
    stack overflows, and the chart silently stops updating.
    """
    code = _strip_js_comments((JS_DIR / "charts.js").read_text())
    offenders = re.findall(r"(\w+(?:\.\w+)*)\s*=\s*\1\s*\|\|", code)
    assert offenders == [], f"auto-asignación sobre el proxy de Chart.js: {offenders}"


def test_the_stage_day_counter_is_wired_to_the_api_fields():
    """The counter reads what /api/grows/active actually returns."""
    grows_js = (JS_DIR / "grows.js").read_text()
    for field in ("day_in_stage", "day_of_grow", "stage_expected_days", "estimated_end"):
        assert field in grows_js, field


def test_the_temperature_chart_draws_the_min_max_envelope():
    """
    Each stored row summarises ~100 readings; the average alone hides the swing,
    which is the thing that actually stresses the plants.
    """
    charts_js = (JS_DIR / "charts.js").read_text()
    assert "temperature_min" in charts_js
    assert "temperature_max" in charts_js
    # The upper bound fills down to the lower one to shade the band.
    assert "fill: '-1'" in charts_js


def test_the_envelopes_helper_series_is_kept_out_of_the_legend():
    charts_js = (JS_DIR / "charts.js").read_text()
    assert "filter: item => item.text !== 'Mín. temperatura'" in charts_js


def test_the_uv_wrapper_is_not_counted_as_a_second_control_loop(monkeypatch):
    """
    Production launches the loop as `uv run python -m autocann.cli.vpd`, which
    appears in `ps` twice: the uv wrapper and the interpreter it spawns.
    Counting both raises a false alarm about the very problem this detects.
    """
    import subprocess

    from autocann.cli import check_system

    fake = (
        "  PID COMMAND\n"
        " 1657 uv run python -u -m autocann.cli.vpd\n"
        " 1662 /home/autocann/Autocann/.venv/bin/python3 -u -m autocann.cli.vpd\n"
        " 1656 uv run python -m autocann.cli.backend\n"
    )
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=fake, stderr=""))
    assert check_system.count_control_loops() == 1


def test_two_real_control_loops_are_still_reported(monkeypatch):
    import subprocess

    from autocann.cli import check_system

    fake = (
        "  PID COMMAND\n"
        " 100 /home/autocann/Autocann/.venv/bin/python3 -u -m autocann.cli.vpd\n"
        " 200 /home/autocann/Autocann/.venv/bin/python3 -u -m autocann.cli.vpd\n"
    )
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=fake, stderr=""))
    assert check_system.count_control_loops() == 2
