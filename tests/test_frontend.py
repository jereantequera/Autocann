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
from pathlib import Path

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


def _included(path: Path, seen: set[str] | None = None) -> set[str]:
    """Every template `path` pulls in, directly or through another include."""
    seen = seen if seen is not None else set()
    for name in re.findall(r'{%\s*include\s+"([^"]+)"', path.read_text()):
        if name in seen:
            continue
        seen.add(name)
        _included(TEMPLATES_DIR / name, seen)
    return seen


def _page_html() -> str:
    """index.html with every include inlined, as one document."""
    parts = [(TEMPLATES_DIR / "index.html").read_text()]
    parts += [(TEMPLATES_DIR / name).read_text() for name in _included(TEMPLATES_DIR / "index.html")]
    return "\n".join(parts)


def test_every_template_is_reachable_from_the_page():
    """
    A partial nobody includes renders nothing and fails silently. The views sit
    between index.html and the partials now, so reachability has to be followed
    through the chain rather than looked for in one file.
    """
    reachable = _included(TEMPLATES_DIR / "index.html") | {"index.html"}
    on_disk = {
        str(path.relative_to(TEMPLATES_DIR)) for path in TEMPLATES_DIR.rglob("*.html")
    }
    assert on_disk - reachable == set()


def test_the_indoor_sensors_section_is_reachable_from_the_page():
    assert "partials/sensors.html" in _included(TEMPLATES_DIR / "index.html")
    assert 'id="indoor-sensors-grid"' in (TEMPLATES_DIR / "partials" / "sensors.html").read_text()
    assert "indoor-sensors-grid" in (JS_DIR / "dashboard.js").read_text()


def test_every_nav_tab_points_at_a_view_that_exists():
    """A tab for a view that is not in the page just shows an empty screen."""
    nav = (TEMPLATES_DIR / "partials" / "nav.html").read_text()
    tabs = set(re.findall(r'class="nav-tab"[^>]*data-view="(\w+)"', nav))
    views = set(re.findall(r'class="view [^"]*"\s+data-view="(\w+)"', _page_html()))
    assert tabs, "la barra de vistas quedó sin pestañas"
    assert tabs == views, f"pestañas sin vista o vistas sin pestaña: {tabs ^ views}"


def test_no_element_id_appears_twice_in_the_page():
    """
    Every view is in the DOM at once, only hidden, so two of them sharing an id
    means `getElementById` writes the reading into whichever came first and the
    other never updates.
    """
    ids = re.findall(r'\sid="([^"]+)"', _page_html())
    duplicates = sorted({name for name in ids if ids.count(name) > 1})
    assert duplicates == []


def test_the_time_range_is_chosen_in_exactly_one_place():
    """
    It used to exist twice, over the summary cards and over the charts, with
    JavaScript keeping the two in sync - two halves of one control separated by
    three unrelated sections.
    """
    assert _page_html().count('class="time-selector"') == 1


def test_every_poller_names_a_view_that_exists():
    """
    A poller scoped to a view that does not exist never runs, and the numbers it
    feeds sit at "--" forever.
    """
    main_js = _strip_js_comments((JS_DIR / "main.js").read_text())
    pollers = re.search(r"const POLLERS = \[(.*?)\n\];", main_js, re.S)
    assert pollers, "POLLERS desapareció de main.js"

    named = set(re.findall(r"'(\w+)'", pollers.group(1)))
    views = set(re.findall(r'data-view="(\w+)"', _page_html()))
    assert named <= views, f"pollers apuntando a vistas inexistentes: {named - views}"


def test_the_calibration_editor_is_wired_to_the_endpoint():
    """
    Both verbs have been routed and validated since the backend audit with no
    way to reach them from the browser.
    """
    import autocann.web.app as web

    routes = {str(rule) for rule in web.app.url_map.iter_rules()}
    assert "/api/calibration" in routes

    system_js = (JS_DIR / "system.js").read_text()
    assert "/api/calibration" in system_js
    # Saving does not take effect until the loop restarts, and a form that does
    # not say so reads as applied.
    assert "reinicies el lazo" in system_js


def test_a_view_is_never_selected_by_its_data_attribute_alone():
    """
    The nav tab carries the same `data-view` as the section it points at, and it
    comes first in the document: a bare `[data-view="x"]` binds the section's
    handlers to the anchor, and every button inside it stops working.
    """
    offenders = []
    for path in JS_DIR.glob("*.js"):
        code = _strip_js_comments(path.read_text())
        for match in re.finditer(r"querySelector(?:All)?\(\s*['\"]([^'\"]*)", code):
            selector = match.group(1)
            if "data-view=" in selector and ".view[" not in selector:
                offenders.append(f"{path.name}: {selector}")
    assert offenders == []


def test_the_weekly_report_is_a_view_rather_than_a_modal():
    """A report is read, scrolled and linked to; a modal can do none of those."""
    page = _page_html()
    assert 'data-view="reportes"' in page
    assert "weeklyReportModal" not in page
    assert "weeklyReportModal" not in (JS_DIR / "analytics.js").read_text()


def test_the_second_sensor_is_rendered_from_its_own_reading():
    """
    Its badge used to be driven by `data.indoor` - the control loop's view of
    the *primary* - through an element that did not exist in any template.
    """
    dashboard = (JS_DIR / "dashboard.js").read_text()
    assert "sensor-status-indoor-2" not in dashboard
    assert "/api/sensors/indoor" in dashboard


def _js_object(name: str) -> str:
    match = re.search(
        rf"const {name} = \{{(.*?)\n\}};", (JS_DIR / "dashboard.js").read_text(), re.S)
    assert match, f"{name} desapareció de dashboard.js"
    return match.group(1)


def test_the_sensor_role_names_match_the_backend():
    """A renamed role loses its label, and the card just says 'primary'."""
    from autocann.config import OBSERVER, PRIMARY, WITNESS

    roles = _js_object("SENSOR_ROLES")
    for role in (PRIMARY, WITNESS, OBSERVER):
        assert f"{role}:" in roles, role


def test_every_drift_flag_the_monitor_emits_has_a_label():
    """Otherwise a real fault renders as its raw internal name."""
    from autocann.control.drift import RAILED, STUCK

    labels = _js_object("DRIFT_FLAG_LABELS")
    for flag in (STUCK, RAILED):
        assert f"{flag}:" in labels, flag


def test_test_mode_is_announced_on_screen_while_it_is_on():
    """
    The one failure this feature can produce is somebody walking away without
    noticing the tent is uncontrolled, so the banner is not optional.
    """
    partial = (TEMPLATES_DIR / "partials" / "outputs.html").read_text()
    assert 'id="test-mode-banner"' in partial
    assert 'id="test-mode-countdown"' in partial
    assert 'data-action="exit-test-mode"' in partial

    dashboard = (JS_DIR / "dashboard.js").read_text()
    assert "renderTestModeBanner" in dashboard


def test_the_test_mode_endpoints_the_dashboard_calls_exist():
    """A renamed route is a button that silently does nothing."""
    import autocann.web.app as web

    routes = {str(rule) for rule in web.app.url_map.iter_rules()}
    dashboard = (JS_DIR / "dashboard.js").read_text()
    for route in ("/api/test-mode", "/api/test-mode/outputs"):
        assert route in dashboard, route
        assert route in routes, route


def test_the_action_dispatcher_hands_the_element_to_its_handler():
    """
    The relay buttons carry which output they belong to in data-*; a dispatcher
    that calls the handler with no arguments throws that away.
    """
    main_js = _strip_js_comments((JS_DIR / "main.js").read_text())
    assert "ACTIONS[action](trigger)" in main_js


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
