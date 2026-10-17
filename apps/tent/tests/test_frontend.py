"""
Frontend checks.

The JavaScript tests run under node when it is available and are skipped
otherwise: the Raspberry needs no JS toolchain to run the dashboard, so it must
not need one to run the suite either.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from autocann.paths import STATIC_DIR, TEMPLATES_DIR

JS_DIR = STATIC_DIR / "js"
JS_TEST_DIR = Path(__file__).parent / "js"
#: The two modules the control-mode tests read as text.
VPD_PY = Path(__file__).parent.parent / "autocann" / "cli" / "vpd.py"
TEST_MODE_PY = Path(__file__).parent.parent / "autocann" / "web" / "api" / "test_mode.py"
#: Partials kept on disk on purpose with no page including them yet. None now:
#: the Exterior card moved into Estadísticas, and its partial went with it. The
#: list stays so that the next exception has to be decided here, not slipped in.
PENDING_PARTIALS: set[str] = set()


def _strip_js_comments(source: str) -> str:
    """Drop // and /* */ comments so a pattern named in prose is not flagged."""
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"(?m)//.*$", "", source)


@pytest.mark.skipif(shutil.which("node") is None, reason="node no está instalado")
def test_javascript_unit_tests_pass():
    """
    Every file in tests/js/, not a hardcoded list: a suite nobody runs is worse
    than no suite. `node --test` is given the files rather than the directory,
    which it reads as a module to execute.
    """
    suites = sorted(str(path) for path in JS_TEST_DIR.glob("*.test.mjs"))
    assert suites, "tests/js/ quedó sin suites"
    result = subprocess.run(
        ["node", "--test", *suites],
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


def test_every_stage_in_the_model_is_named_and_coloured_in_the_frontend():
    """
    The stage list lives once in vpd_math.py, but the browser needs four things
    per stage that no kPa band implies: a fallback display name, a short name
    for the chart markers, an option in each of the two stage selects and a
    badge colour.

    Every one of them is forgotten silently. A stage with no name renders as
    `seedling` in the badge. The feed grid's header is the stage's display name
    now, so it needs no letter of its own.
    """
    from autocann.control.vpd_math import STAGES

    state_js = (JS_DIR / "state.js").read_text()
    css = (STATIC_DIR / "css" / "app.css").read_text()
    modals = (TEMPLATES_DIR / "partials" / "modals.html").read_text()

    def block(path_text, opener, source):
        match = re.search(re.escape(opener) + r"(.*?)\n(?:    )?\}", path_text, re.S)
        assert match, f"{opener} desapareció de {source}"
        return match.group(1)

    blocks = {
        "state.js (stageNames)": block(state_js, "stageNames: {", "state.js"),
        "state.js (vpdRanges)": block(state_js, "vpdRanges: {", "state.js"),
        "charts.js (STAGE_SHORT_NAMES)": block(
            (JS_DIR / "charts.js").read_text(), "const STAGE_SHORT_NAMES = {", "charts.js"),
    }

    missing = []
    for stage in STAGES:
        for where, text in blocks.items():
            if f"{stage}:" not in text:
                missing.append(f"{stage} falta en {where}")
        if modals.count(f'value="{stage}"') != 2:
            missing.append(f"{stage} no está en los dos selects de etapa")
        if f".grow-stage.{stage}" not in css:
            missing.append(f"{stage} no tiene color de chapita en app.css")
    assert missing == [], missing

    # The grid's stage row is named from the same table as the badge, not
    # from a private copy.
    feeding = (JS_DIR / "feeding.js").read_text()
    assert "stageName(group.stage)" in feeding
    assert "STAGE_INITIALS" not in feeding


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
    """
    The lower bound of the envelope is drawn invisibly to fill against, so it
    must not be a legend entry. The legends are HTML now: Chart.js draws none,
    and the HTML names the band as one item, shown only when there is a band.
    """
    charts_js = (JS_DIR / "charts.js").read_text()
    assert "legend: { display: false }" in charts_js
    partial = (TEMPLATES_DIR / "partials" / "charts.html").read_text()
    assert "Mín. temperatura" not in partial
    assert 'data-series="band" hidden' in partial


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


#: Templates that are pages in their own right rather than partials. The PIN
#: screen is the second one: it cannot be a modal over the dashboard, because
#: the dashboard fires a dozen requests the moment it loads and every one of
#: them would answer 401 behind the dialog.
ENTRY_POINTS = ("index.html", "login.html")


def test_every_template_is_reachable_from_a_page():
    """
    A partial nobody includes renders nothing and fails silently. The views sit
    between index.html and the partials now, so reachability has to be followed
    through the chain rather than looked for in one file.
    """
    reachable = set(ENTRY_POINTS)
    for entry in ENTRY_POINTS:
        reachable |= _included(TEMPLATES_DIR / entry)
    on_disk = {
        str(path.relative_to(TEMPLATES_DIR)) for path in TEMPLATES_DIR.rglob("*.html")
    }
    assert on_disk - reachable - PENDING_PARTIALS == set()


def test_every_entry_point_is_actually_served():
    """A page template nothing renders is a 500 waiting for the right moment."""
    import autocann.web.app as web

    web_dir = Path(web.__file__).parent
    app_py = "".join(path.read_text() for path in [web_dir / "app.py", *(web_dir / "api").glob("*.py")])
    auth_py = (web_dir / "auth.py").read_text()
    for entry in ENTRY_POINTS:
        assert entry in app_py or entry in auth_py, entry


def test_the_humidity_readout_cannot_render_a_difference_without_readings():
    """
    /api/current-data answers 404 with a JSON body, and apiGet only raises on
    5xx, so the dashboard renders that body with every field `undefined`. A
    guard written `humidity !== null` lets `undefined` through, and the readout
    announced "NaN% por encima". The difference now lives in renderReadouts,
    which checks the reading itself before it compares anything.
    """
    dashboard = _strip_js_comments((JS_DIR / "dashboard.js").read_text())
    match = re.search(r"function renderReadouts\(.*?\n\}", dashboard, re.S)
    assert match, "renderReadouts desapareció de dashboard.js"
    body = match.group(0)

    assert "humidityOk &&" in body
    assert "humidity - targetHumidity" not in body
    # `undefined !== null` is true, which is how the NaN got on screen.
    assert "!== null" not in body


def test_the_indoor_sensors_section_is_reachable_from_the_page():
    assert "partials/sensors.html" in _included(TEMPLATES_DIR / "index.html")
    assert 'id="indoor-sensors-grid"' in (TEMPLATES_DIR / "partials" / "sensors.html").read_text()
    assert "indoor-sensors-grid" in (JS_DIR / "health.js").read_text()


def test_every_nav_tab_points_at_a_view_that_exists():
    """A tab for a view that is not in the page just shows an empty screen."""
    nav = (TEMPLATES_DIR / "partials" / "sidebar.html").read_text()
    tabs = set(re.findall(r'class="nav-tab[^"]*"[^>]*data-view="(\w+)"', nav))
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


def test_the_sensor_cells_name_their_column_for_the_phone_cards():
    """
    On a phone the header row is hidden and each sensor is a card, so every
    value gets its column name from data-label. The correction cells are
    written by system.js, the reading cells by health.js: both must carry it.
    """
    health = (JS_DIR / "health.js").read_text()
    system_js = (JS_DIR / "system.js").read_text()
    for label in ("Rol", "Temp.", "Humedad", "Última", "Corrección °C", "Corrección %", "Nota"):
        assert f'data-label="{label}"' in health
    assert 'data-label="Corrección °C"' in system_js
    assert 'data-label="Corrección %"' in system_js

    css = (STATIC_DIR / "css" / "views" / "sistema.css").read_text()
    phone = css.split("@media (max-width: 599px)", 1)[1]
    assert "td[data-label]::before" in phone
    assert "content: attr(data-label)" in phone


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


def test_the_quiet_hours_editor_is_wired_to_the_endpoint():
    """
    Both verbs have been routed and validated since quiet hours were added,
    with nothing calling them: the setting that keeps a loud fan from starting
    at four in the morning was reachable only with curl.
    """
    import autocann.web.app as web

    routes = {str(rule) for rule in web.app.url_map.iter_rules()}
    assert "/api/quiet-hours" in routes
    assert "partials/quiet.html" in _included(TEMPLATES_DIR / "index.html")

    quiet_js = (JS_DIR / "quiet.js").read_text()
    assert "/api/quiet-hours" in quiet_js


def test_the_quiet_hours_editor_says_a_window_crossing_midnight_does():
    """
    23:00 → 07:00 is two intervals on a clock face and one continuous night.
    A pair of time fields alone does not say which one the backend means.
    """
    assert "cruza la medianoche" in (JS_DIR / "quiet.js").read_text()


def test_a_window_on_an_output_nothing_drives_is_shown_as_inert():
    """
    `automatic_outputs` is served precisely so the dashboard can say so instead
    of offering a setting that silently blocks nothing.
    """
    quiet_js = (JS_DIR / "quiet.js").read_text()
    assert "automatic_outputs" in quiet_js
    assert "inertCardHtml" in quiet_js


def test_the_quiet_hours_editor_never_offers_to_switch_a_relay():
    """
    The window constrains the controller's *decision*, not the contact. Forcing
    a relay behind the controller's back would leave its minimum-time
    bookkeeping describing a device that is not running.
    """
    quiet_js = _strip_js_comments((JS_DIR / "quiet.js").read_text())
    assert "/api/output-control" not in quiet_js
    assert "/api/test-mode/outputs" not in quiet_js


def test_the_statistics_view_lets_you_choose_which_grow():
    """
    Filtering by time alone drew ten months of an empty tent and a real crop as
    one curve as soon as the window reached past the changeover. The default is
    the active grow; "todos los cultivos" has to be reachable, and asked for.
    """
    page = _page_html()
    assert 'id="grow-filter"' in page
    assert 'id="grow-scope-note"' in page

    grows_js = (JS_DIR / "grows.js").read_text()
    assert "grow-filter" in grows_js
    assert "Todos los cultivos" in grows_js


def test_every_history_request_carries_the_grow_scope():
    """
    One of these left out is one chart answering a different question from the
    others on the same screen.
    """
    for module, endpoints in (
        # The time-in-range readout asks for its score from dashboard.js now,
        # next to the readout it fills, so that is where its scope is checked.
        ("dashboard.js", ("/api/sensor-history", "/api/history/aggregated",
                          "/api/period-summary", "/api/vpd-score")),
    ):
        code = _strip_js_comments((JS_DIR / module).read_text())
        assert "withGrow" in code, module
        for endpoint in endpoints:
            assert endpoint in code, f"{module}: {endpoint}"


def test_the_grow_scope_is_never_hardcoded_into_a_url():
    """
    `grow_id=${...}` built by hand is how a selector set to "todos" turns into
    `grow_id=null` in a URL, which the backend rejects — so the chart just goes
    blank. withGrow() is the one place that decides.
    """
    offenders = []
    for path in JS_DIR.glob("*.js"):
        if path.name == "util.js":
            continue  # where withGrow itself lives
        code = _strip_js_comments(path.read_text())
        if re.search(r"grow_id=\$\{", code) or "grow_id=' +" in code:
            offenders.append(path.name)
    assert offenders == []


def test_no_native_confirm_or_alert_remains():
    """
    They freeze the whole tab — every poller included — until somebody clicks,
    they cannot be styled, and the confirm button says "OK" on a dialog about
    ending a grow.
    """
    offenders = []
    for path in JS_DIR.glob("*.js"):
        code = _strip_js_comments(path.read_text())
        # `alertDialog(` and `confirmDialog(` are ours; the bare globals are not.
        for call in re.findall(r"(?<![\w.])(alert|confirm|prompt)\s*\(", code):
            offenders.append(f"{path.name}: {call}()")
    assert offenders == []


def test_the_focus_is_trapped_in_the_modal_and_given_back_on_close():
    """
    Backlog #9 plus what was left of Fase 0. Tabbing out of an open modal lands
    on the buttons behind it, which a keyboard or screen-reader user cannot see
    are covered; and closing one dropped focus on <body>, so the next Tab
    started from the top of the document.
    """
    modals = _strip_js_comments((JS_DIR / "modals.js").read_text())

    # Both directions. Fixing only Tab leaves a trap you walk out of with
    # Shift+Tab, which is the half that usually gets forgotten.
    assert "shiftKey" in modals
    assert "nextFocus" in modals
    assert "'Tab'" in modals

    # And the way back: stored on open, focused on close.
    assert "openers.set" in modals
    assert "openers.get" in modals
    assert "opener.focus()" in modals


def test_only_modals_js_opens_or_closes_a_modal():
    """
    Escape, the backdrop and the close button are three ways out of the same
    dialog. One of them flipping the class by hand is one of them skipping the
    part that puts focus back — which is how closing by backdrop used to leave
    focus on <body>.

    The `.show` class is not modals-only (the anomaly strip uses it too), so
    what is checked is that nothing else reaches for a `.modal` at all.
    """
    offenders = []
    for path in JS_DIR.glob("*.js"):
        if path.name == "modals.js":
            continue
        code = _strip_js_comments(path.read_text())
        # An import of the module, and `data-action="close-modal"` routing to
        # closeModal(), are the two legitimate mentions.
        code = re.sub(r"^import .*?from '\./modals\.js';$", "", code, flags=re.M)
        code = code.replace("'close-modal'", "").replace("dataset.modal", "")
        for match in re.finditer(r"['\"`][^'\"`]*\.modal[^'\"`]*['\"`]", code):
            offenders.append(f"{path.name}: {match.group(0)}")
    assert offenders == []


def test_a_destructive_confirmation_names_what_it_does():
    """
    "OK" is what the native dialog said. A button on a destructive action has
    to say which act it is about to perform.
    """
    modals = _strip_js_comments((JS_DIR / "modals.js").read_text())
    # No default for confirmLabel: forgetting it has to be a visible mistake,
    # not a dialog that quietly says "Confirmar".
    assert re.search(r"confirmLabel\s*=", modals) is None

    labels = []
    for name in ("grows.js", "feeding.js", "dashboard.js"):
        code = (JS_DIR / name).read_text()
        labels += re.findall(r"confirmLabel:\s*'([^']+)'", code)

    assert "Finalizar cultivo" in labels
    assert "Borrar riego" in labels
    for label in labels:
        assert label.lower() not in ("ok", "aceptar", "sí", "confirmar"), label

    # Stopping the tent used to be one of these, with the label "Pausar el
    # sistema". It is a fork now - monitoring or pause - which a one-button
    # dialog cannot ask, so it has its own modal. The rule still holds there:
    # each button names the state it puts the tent in, and says what that does
    # to the relays and to the history.
    modal = (TEMPLATES_DIR / "partials" / "modals.html").read_text()
    stop = modal[modal.index('id="stopModeModal"'):]
    stop = stop[: stop.index("</div>\n\n")]
    for action, name in (("enter-monitor", "Monitoreo"), ("enter-pause", "Pausa")):
        assert f'data-action="{action}"' in stop, action
        assert f">{name}<" in stop, name
    assert "el histórico se sigue\n                            guardando" in stop \
        or "histórico se sigue" in stop
    assert "no se guarda nada" in stop


def test_the_dialog_shell_is_in_the_page_and_wired():
    """Built from markup like every other modal, so it looks like one."""
    page = _page_html()
    for element in ('id="appDialog"', 'id="dialog-title"', 'id="dialog-message"',
                    'id="dialog-confirm"', 'id="dialog-cancel"', 'id="toast-tray"'):
        assert element in page, element
    assert 'aria-modal="true"' in page

    modals = (JS_DIR / "modals.js").read_text()
    # textContent, never innerHTML: some of what goes in is a server error.
    assert "innerHTML" not in _strip_js_comments(modals)
    assert "textContent" in modals


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
    health = (JS_DIR / "health.js").read_text()
    assert "sensor-status-indoor-2" not in health
    assert "/api/health" in health


def test_only_one_module_decides_whether_a_sensor_is_working():
    """
    El badge del hero, las tarjetas y el cartel de arriba salían de tres fuentes
    distintas, y con el sistema en pausa decían tres cosas a la vez: "sensor
    posiblemente desconectado" en rojo arriba de dos "OK" en verde. Ahora los
    tres los pinta health.js con una sola respuesta; dashboard.js no puede
    volver a tocar esos elementos sin que esto se entere.
    """
    dashboard = _strip_js_comments((JS_DIR / "dashboard.js").read_text())
    for element in ("sensor-status-indoor", "sensor-status-outdoor",
                    "indoor-sensors-grid", "sensor-alert"):
        assert element not in dashboard, element


def _js_object(name: str, module: str = "health.js") -> str:
    match = re.search(
        rf"const {name} = \{{(.*?)\n\}};", (JS_DIR / module).read_text(), re.S)
    assert match, f"{name} desapareció de {module}"
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


def test_the_pause_is_announced_on_screen_while_it_lasts():
    """
    The one failure this feature can produce is somebody walking away without
    noticing the tent is uncontrolled, so the announcement is not optional, and
    it matters more now than when the pause expired after eight hours: nothing
    but a person ends it, so saying so is the whole safety story.

    What changed is where it is said, not whether. It used to be a full-width
    amber banner under the header *and* an amber chip inside it, three
    sentences apart, saying the same three things - and two elements saying one
    thing is how a reader learns to skip both. The chip is the announcement
    now: it is in the sidebar, so it is on every view, it goes amber and keeps
    its dot pulsing, it says how long it has been, and the verb that ends it is
    on it.
    """
    sidebar = (TEMPLATES_DIR / "partials" / "sidebar.html").read_text()
    assert 'id="system-state"' in sidebar
    assert 'data-action="toggle-pause"' in sidebar
    # How long it has been - the one thing the banner carried that a chip did not.
    assert 'id="pause-elapsed"' in sidebar

    dashboard = _strip_js_comments((JS_DIR / "dashboard.js").read_text())
    assert "renderSystemState" in dashboard
    # The classes the stylesheet hangs the fill, the border and the glow on.
    # Without them the chip is a grey label, which is not an announcement. Two
    # of them now: a tent that is monitoring is unattended just as surely as a
    # paused one, and has to look it without looking like the same thing.
    assert "'is-paused', stopped && !monitoring" in dashboard
    assert "'is-monitor', monitoring" in dashboard
    css = (STATIC_DIR / "css" / "app.css").read_text()
    assert ".system-state.is-paused {" in css
    assert ".system-state.is-monitor {" in css
    # Says out loud that it does not end on its own; a state that only reports
    # itself reads like something that will clear up by itself.
    assert "No se reanuda solo" in dashboard


def test_the_pause_is_announced_in_exactly_one_place():
    """
    The banner under the header repeated the chip above it word for word. The
    grower's reaction to the panel - "ese banner duplica info" - is the whole
    reason it is gone, and nothing may quietly grow back in its place.
    """
    page = _page_html()
    assert "pause-bar" not in page
    assert page.count('data-action="toggle-pause"') == 1
    assert page.count('id="pause-elapsed"') == 1

    for name in ("pause-bar", "resume-system"):
        assert name not in _strip_js_comments((JS_DIR / "dashboard.js").read_text()), name
        assert name not in _strip_js_comments((JS_DIR / "main.js").read_text()), name
    assert ".pause-bar" not in (STATIC_DIR / "css" / "app.css").read_text()



def test_the_panel_answers_its_questions_in_order():
    """
    The Ahora tab is read for three things, in this order: how the tent is
    (the two readings), which relays are on, and what to pour today. They are
    bands top to bottom, so each is found by looking at one part of the tab.
    The status line and the chart sit beside them, not in front of them.
    """
    panel = (TEMPLATES_DIR / "views" / "panel.html").read_text()
    readings = panel.index('class="ac-readout-row"')
    order = [panel.index(f'{{% include "partials/{name}.html" %}}')
             for name in ("outputs", "today-feed")]
    assert readings < order[0] < order[1], "el orden de las bandas del panel cambió"
    assert 'include "partials/hero.html"' not in panel



def test_the_sensor_diagnostics_are_not_on_the_panel():
    """
    Six cards of raw readings, roles, ages and drift verdicts were taking half
    of the screen that exists to be read from across the room. They belong next
    to the calibration, the connectivity and the node log, in Sistema.

    The panel no longer has a sensors row either: its status line says whether
    to believe the readings, and it reads the loop from the same /api/health
    response the Sistema cards use, so the two cannot disagree.
    """
    assert 'include "partials/sensors.html"' in (
        TEMPLATES_DIR / "views" / "system.html").read_text()
    panel = (TEMPLATES_DIR / "views" / "panel.html").read_text()
    assert "sensors.html" not in panel
    assert "health-strip" not in panel

    health = _strip_js_comments((JS_DIR / "health.js").read_text())
    assert "health-sensors-state" not in health
    assert "sensorsBadge(health.sensors)" in health

    # And the poller that feeds both has to run on both views, or whichever one
    # is not listed sits at the loading ellipsis forever. It also feeds the
    # sidebar and the alert slot, which every view shows, so it has no `views`.
    pollers = re.search(r"const POLLERS = \[(.*?)\n\];",
                        _strip_js_comments((JS_DIR / "main.js").read_text()), re.S)
    fetch_health = re.search(r"\{ fn: fetchHealth[^}]*\}", pollers.group(1))
    assert fetch_health, "el poller de /api/health desapareció"
    assert "views" not in fetch_health.group(0)


def test_the_panel_watering_tile_never_fetches_its_own_plan():
    """
    A previous version of this tile was deleted for drifting out of step with
    the Riego tab. It renders the payload the feeding module already fetched,
    handed over by `feeding:updated`, and the arithmetic that turns a dose per
    litre into what goes in the jug is one shared helper - so "40 ml" on the
    panel and "40 ml" in the form are the same multiplication.
    """
    panel_js = _strip_js_comments((JS_DIR / "panel.js").read_text())
    assert "feeding:updated" in panel_js
    assert "/api/feeding/today" not in panel_js
    assert "/api/irrigations" not in panel_js

    for module in ("panel.js", "feeding.js"):
        code = _strip_js_comments((JS_DIR / module).read_text())
        assert "doseAmount(" in code, module
        assert "lastWateringLabel(" in code, module
        # Neither may keep a private copy of either.
        assert "function doseAmount" not in code, module
        assert "function lastWateringLabel" not in code, module


def test_the_pause_is_reachable_from_every_view():
    """
    Pausing is the state of the whole system, not a property of the relay
    section it used to be buried in - and the view showing that section is not
    the one you are on when you need to stop the tent.
    """
    index = (TEMPLATES_DIR / "index.html").read_text()
    # Included outside <main class="views">, so it is not inside any one view.
    assert index.index('partials/sidebar.html') < index.index('<main class="views">')

    sidebar = (TEMPLATES_DIR / "partials" / "sidebar.html").read_text()
    assert 'data-action="toggle-pause"' in sidebar
    assert 'href="#/riego"' in sidebar, "el CTA de riego también vive en la barra"


def test_the_pause_does_not_expire_on_its_own():
    """
    A pause that resumes by itself closes relays at a moment nobody chose, and
    if it was taken to rewire something, while somebody's hands are in there.
    """
    import autocann.config as config

    assert not hasattr(config, "TEST_MODE_DEFAULT_SECONDS")
    assert not hasattr(config, "TEST_MODE_MAX_SECONDS")

    web_dir = Path(config.__file__).parent / "web"
    app_py = "".join(path.read_text() for path in [web_dir / "app.py", *(web_dir / "api").glob("*.py")])
    # No TTL on the pause keys: `ex=` on either is an expiry by another name.
    test_mode_py = (web_dir / "api" / "test_mode.py").read_text()
    pause_write = test_mode_py[test_mode_py.index("def set_test_mode"):]
    pause_write = pause_write[: pause_write.index("@bp.route", 10)]
    assert "ex=" not in pause_write

    # And it outlives Redis, which is memory: a reboot must not resume the tent.
    assert "TEST_MODE_SETTING" in app_py
    assert "restore_pause_mirror" in (Path(config.__file__).parent / "cli" / "vpd.py").read_text()


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


def test_the_asset_version_changes_when_the_assets_do():
    """
    index.html says the query string on every stylesheet and module busts the
    browser cache on a deploy. It was `__version__`, frozen at "0.1.0" since
    the first commit, so that had never once been true: the tablet on the tent
    wall kept its old app.css against freshly deployed markup, which looks
    exactly like the deploy having broken the page.

    Nobody has to remember to bump anything now - the token is the newest mtime
    under static/, which `git pull` moves for us.
    """
    from autocann.web.deps import static_asset_version

    before = static_asset_version()
    assert before, "el token quedó vacío"

    # The token is the newest mtime under static/, so push app.css past the
    # newest file, not just past its own old mtime: after a `git pull` another
    # file can be newer than app.css + 1 h, and then the token would not move.
    asset = STATIC_DIR / "css" / "app.css"
    stamp = asset.stat()
    newest = max(p.stat().st_mtime for p in STATIC_DIR.rglob("*") if p.is_file())
    try:
        os.utime(asset, (stamp.st_atime, newest + 3600))
        assert static_asset_version() != before
    finally:
        os.utime(asset, (stamp.st_atime, stamp.st_mtime))

    assert static_asset_version() == before


# ---------------------------------------------------------------------------
# The third state
# ---------------------------------------------------------------------------
# "No decidas" and "no guardes" were one code path: the paused branch of the
# loop `continue`s before the accumulator is ever reached. That is right for
# bench work and exactly wrong for setup, where what the room does on its own
# is the baseline worth having before there is anything alive to protect.


def test_monitoring_records_history_and_the_pause_still_does_not():
    """
    The whole difference between the two stopped states, in the one place it is
    decided. A monitoring branch that forgot to flush would look identical to a
    pause and silently throw away the data it exists to collect.
    """
    vpd = _strip_js_comments(VPD_PY.read_text())

    monitor = vpd[vpd.index("def run_monitor_cycle("):]
    monitor = monitor[: monitor.index("\ndef ", 1)]
    assert "accumulator.push" in monitor
    # Nothing switches, and the desired state keeps being published: the remote
    # node de-energises 60 s after the last poll.
    assert "relays.all_off()" in monitor

    paused = vpd[vpd.index("def run_test_mode_cycle("):]
    paused = paused[: paused.index("\ndef ", 1)]
    assert "accumulator" not in paused, "la pausa volvió a guardar histórico"


def test_monitoring_records_no_decision_it_did_not_make():
    """
    A row carrying a stage and a target would describe a controller that was
    aiming at something. Nothing is. That is also what makes a monitoring
    stretch recognisable afterwards without inventing a flag for it.
    """
    vpd = VPD_PY.read_text()
    monitor = vpd[vpd.index("def run_monitor_cycle("):]
    monitor = monitor[: monitor.index("\ndef ", 1)]
    for field in ('"stage": None', '"target_humidity": None', '"control_action": None'):
        assert field in monitor, field


def test_one_writer_for_the_history_rather_than_two():
    """
    Both regimes store; two copies of the interval arithmetic is how one of
    them quietly starts summarising differently from the other.
    """
    vpd = _strip_js_comments(
        VPD_PY.read_text())
    assert vpd.count("store_sensor_sample(") == 1
    assert vpd.count("flush_history(") >= 3  # the def, and both call sites


def test_a_tent_stopped_before_monitoring_existed_stays_stopped():
    """
    The failure this must not have: the deploy that adds the third state
    reading an old record as "active" and handing a tent somebody deliberately
    stopped back to the controller, energising relays at a moment nobody chose.

    Every record written before this carries no `mode`, and every one of them
    meant a pause. So does the bare timestamp the Redis mirror used to hold.
    """
    from autocann.cli.vpd import parse_mode_mirror
    from autocann.config import MODE_ACTIVE, MODE_MONITOR, MODE_PAUSED

    # The format the tent's Redis is holding right now.
    assert parse_mode_mirror("1791849247") == (MODE_PAUSED, 1791849247)
    assert parse_mode_mirror(b"1791849247") == (MODE_PAUSED, 1791849247)

    assert parse_mode_mirror('{"mode": "monitor", "since": 7}') == (MODE_MONITOR, 7)
    assert parse_mode_mirror('{"mode": "paused", "since": 7}') == (MODE_PAUSED, 7)

    # Nothing stored means the loop is in charge; a mode this build does not
    # know does not, because that is not a reason to start driving a tent.
    assert parse_mode_mirror(None)[0] == MODE_ACTIVE
    assert parse_mode_mirror('{"mode": "something-newer", "since": 7}')[0] in (
        MODE_MONITOR, MODE_PAUSED)


def test_the_relays_are_only_movable_by_hand_in_the_pause():
    """
    Monitoring switching a relay would make its own history worthless: those
    rows are worth having precisely because nothing was running in the room.
    """
    test_mode_py = TEST_MODE_PY.read_text()
    guard = test_mode_py[test_mode_py.index("def set_test_mode_output("):]
    assert "control_mode()[0] != MODE_PAUSED" in guard


def test_the_header_offers_both_ways_to_stop_and_one_way_back():
    """
    Three states need a way into each. The chip alone cannot do it - it has one
    action - so stopping opens the fork and the modal's two buttons are the
    only doors into monitoring and into the pause.
    """
    page = _page_html()
    assert 'id="stopModeModal"' in page
    assert page.count('data-action="enter-monitor"') == 1
    assert page.count('data-action="enter-pause"') == 1
    assert page.count('data-action="toggle-pause"') == 1

    main_js = _strip_js_comments((JS_DIR / "main.js").read_text())
    for action in ("'enter-monitor'", "'enter-pause'", "'toggle-pause'"):
        assert action in main_js, action


def test_monitoring_is_not_exempt_from_the_history_check():
    """
    The pause is allowed a gap in the history and says so. Monitoring is not:
    it writes, so a gap is as real a fault there as under control - and calling
    it expected would hide exactly the failure the mode exists to prevent.
    """
    from autocann.config import MODE_MONITOR
    from autocann.web.diagnostics import history_verdict

    assert history_verdict(999999, "paused")["level"] == "ok"
    assert history_verdict(999999, MODE_MONITOR)["level"] == "error"
    assert history_verdict(10, MODE_MONITOR)["state"] == "writing"


# Pictographic emoji, plus the variation selector that turns a plain glyph into one.
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿⌚-⏿️]")


def _ui_sources():
    yield from TEMPLATES_DIR.rglob("*.html")
    yield from (STATIC_DIR / "js").glob("*.js")
    yield STATIC_DIR / "css" / "app.css"


def _code_only(path: Path) -> str:
    """The file without its comments, which may quote what the screen used to say."""
    text = path.read_text()
    text = re.sub(r"{#.*?#}|<!--.*?-->|/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"^\s*(//|\*).*$", "", text, flags=re.M)


def test_the_dashboard_draws_no_emoji():
    """
    The Raspberry has no emoji font: on the tablet on the tent wall every emoji
    rendered as an empty box, the ✅ and ❌ of the status badges included.
    """
    offenders = {
        path.name: sorted(set(_EMOJI.findall(_code_only(path))))
        for path in _ui_sources()
        if _EMOJI.search(_code_only(path))
    }
    assert offenders == {}


def test_every_icon_the_dashboard_asks_for_is_in_the_sprite():
    """A missing symbol renders nothing at all, with no error anywhere."""
    sprite = (TEMPLATES_DIR / "partials" / "icons.html").read_text()
    available = set(re.findall(r'<symbol id="i-([a-z0-9-]+)"', sprite))

    wanted = set()
    for path in TEMPLATES_DIR.rglob("*.html"):
        wanted |= set(re.findall(r'icon\("([a-z0-9-]+)"', path.read_text()))
    for path in (STATIC_DIR / "js").glob("*.js"):
        code = _code_only(path)
        wanted |= set(re.findall(r"\bicon\(\s*'([a-z0-9-]+)'", code))
        wanted |= set(re.findall(r"\bicon: '([a-z0-9-]+)'", code))
        # Lookup tables of state -> icon name.
        for table in re.findall(r"const [A-Z_]+_ICONS = \{([^}]*)\}", code):
            wanted |= set(re.findall(r"'([a-z0-9-]+)'", table))

    assert wanted, "the scan found no icons at all, so it is not looking"
    assert wanted - available == set()


def test_fonts_are_served_from_here_not_from_google():
    """The stylesheet request to Google is what a page with no internet hangs on."""
    for entry in ENTRY_POINTS:
        assert "fonts.googleapis.com" not in (TEMPLATES_DIR / entry).read_text(), entry
    css = (STATIC_DIR / "css" / "app.css").read_text()
    for font in re.findall(r"url\('\.\./(vendor/fonts/[^']+)'\)", css):
        assert (STATIC_DIR / font).exists(), font
    assert "vendor/fonts/" in css


#: Every colour token of the design system. The values live in tokens.css and
#: come from the designer's tokens.json; this is the list the stylesheet must
#: define in both themes, so a token added to the design and forgotten here fails.
COLOR_TOKENS = (
    "surface-0", "surface-sunken", "surface-raised", "surface-active", "surface-overlay",
    "fg", "fg-secondary", "fg-muted", "fg-subtle", "fg-inverse",
    "fill-inverse", "hairline", "hairline-faint", "border-control", "focus",
    "state-ok", "state-warn", "state-alert", "state-idle",
    "data-water", "data-temp", "data-outside",
)


def _theme_block(css: str, selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{(.*?)\n\}", css, re.S)
    assert match, f"{selector} desapareció de tokens.css"
    return match.group(1)


def test_the_tokens_define_every_colour_in_both_themes():
    """Grafito is the default and Noche is the wall tablet's; a token missing from one is a blank colour there."""
    css = (STATIC_DIR / "css" / "tokens.css").read_text()
    themes = {
        "Grafito (:root)": _theme_block(css, ":root"),
        "Noche (data-theme=night)": _theme_block(css, ':root[data-theme="night"]'),
    }
    missing = [
        f"--{name} en {theme}"
        for theme, block in themes.items()
        for name in COLOR_TOKENS
        if not re.search(rf"--{re.escape(name)}:\s*#[0-9A-Fa-f]{{6}};", block)
    ]
    assert missing == []
    assert "--font-sans:" in themes["Grafito (:root)"]
    assert "--font-mono:" in themes["Grafito (:root)"]


def test_the_page_links_the_tokens_and_components_before_the_app_stylesheet():
    """
    The tokens and the component classes come first so app.css, which maps its
    old variables onto them, has the last word. components.css must not import
    anything: the page cannot depend on the internet.
    """
    assert "@import" not in (STATIC_DIR / "css" / "components.css").read_text()
    for entry in ("index.html", "login.html"):
        html = (TEMPLATES_DIR / entry).read_text()
        positions = [html.find(f"css/{name}") for name in ("tokens.css", "components.css", "app.css")]
        assert all(position >= 0 for position in positions), entry
        assert positions == sorted(positions), entry


def test_the_page_loads_behind_one_skeleton_and_always_comes_out_of_it():
    """
    The page starts as skeletons and is revealed once, when the first answers
    are in - not piece by piece, with the alerts landing last and shoving the
    panel down. And a startup that throws must still take the skeleton off,
    or the page looks like it is loading forever.
    """
    index = (TEMPLATES_DIR / "index.html").read_text()
    assert '<body class="is-booting">' in index

    main = _strip_js_comments((JS_DIR / "main.js").read_text())
    assert "whenSettled" in main
    assert re.search(r"\.catch\(\s*\w+\s*=>\s*\{[^}]*classList\.remove\('is-booting'\)", main)

    css = (STATIC_DIR / "css" / "app.css").read_text()
    assert ".is-booting :is(#sensor-alert, #anomalies-alert, #connection-alert)" in css



def test_a_chart_with_nothing_to_plot_says_so():
    """An empty grid with axes from 0 to 1 reads as a broken chart, not as no data."""
    charts = _strip_js_comments((JS_DIR / "charts.js").read_text())
    panel = _strip_js_comments((JS_DIR / "panel.js").read_text())
    vpd = _strip_js_comments((JS_DIR / "vpdchart.js").read_text())
    # Estadísticas still has charts that can be empty.
    assert "setChartEmpty" in charts
    # The panel's chart has no grid to be empty with: without a reading it
    # draws the band and says so, instead of a dot at a made-up place.
    assert "Sin lectura actual" in vpd
    assert "setChartEmpty" not in panel
    css = (STATIC_DIR / "css" / "app.css").read_text()
    assert ".chart-wrapper.is-empty::after" in css


def test_the_sidebar_tent_holds_no_space_for_a_hidden_progress_bar():
    """
    The progress block is `hidden` when a stage has no expected length, but a
    `display: flex` rule beats the attribute, and in the tent's column layout
    its flex-basis was a 140 px height: an empty gap between the stage line and
    "día N de ~M". The rule that displays it has to yield to `hidden`.
    """
    css = (STATIC_DIR / "css" / "app.css").read_text()
    assert ".ac-sidebar .stage-progress[hidden] {" in css
    block = re.search(r"\.ac-sidebar \.stage-progress \{(.*?)\}", css, re.S)
    assert block, "la regla de .stage-progress desapareció"
    assert "flex: none" in block.group(1)
    assert "140px" not in block.group(1)

