/**
 * Tests for the pure helpers in static/js/util.js.
 *
 * Run with: node --test tests/js/*.test.mjs
 * The Python suite also runs these via tests/test_frontend.py, and skips when
 * node is not installed — the Raspberry does not need a JS toolchain.
 */

import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
    doseAmount, escapeHtml, fmtDose, fmtNum, fmtSigned, formatAge, formatDate,
    formatDuration, getScoreClass, getScoreLabel, growParam, humidityDiffLabel,
    lastWateringLabel, outputPinLabel, parseLocalDatetime, relaySourceLabel, waterOnlyNote,
    withGrow,
} from '../../autocann/web/static/js/util.js';

test('escapeHtml neutralises markup that reaches innerHTML', () => {
    // A grow name is written by whoever can reach the API, and the grow list
    // renders it with innerHTML. This is the fix for that XSS.
    assert.equal(
        escapeHtml('<img src=x onerror="alert(1)">'),
        '&lt;img src=x onerror=&quot;alert(1)&quot;&gt;',
    );
    assert.equal(escapeHtml("it's & <b>"), 'it&#39;s &amp; &lt;b&gt;');
});

test('escapeHtml renders nullish as empty, not as the words null/undefined', () => {
    assert.equal(escapeHtml(null), '');
    assert.equal(escapeHtml(undefined), '');
    assert.equal(escapeHtml(0), '0');
});

test('fmtNum keeps a real zero instead of showing a dash', () => {
    // 0 °C of leaf temperature and 0% of target humidity are valid readings;
    // `x ? fmt(x) : '--'` used to hide both.
    assert.equal(fmtNum(0), '0.0');
    assert.equal(fmtNum(0, 2), '0.00');
    assert.equal(fmtNum(24.567, 1), '24.6');
});

test('fmtNum falls back for anything that is not a finite number', () => {
    for (const value of [null, undefined, NaN, Infinity, 'abc', {}]) {
        assert.equal(fmtNum(value), '--', `para ${String(value)}`);
    }
    assert.equal(fmtNum(null, 1, 's/d'), 's/d');
});

test('parseLocalDatetime accepts the API format Safari rejects', () => {
    // `new Date("2026-01-16 12:00:00")` is not a format the constructor must
    // accept, and Safari returns Invalid Date for it.
    const d = parseLocalDatetime('2026-01-16 12:30:45');
    assert.ok(!isNaN(d.getTime()));
    assert.equal(d.getFullYear(), 2026);
    assert.equal(d.getMonth(), 0);
    assert.equal(d.getDate(), 16);
    assert.equal(d.getHours(), 12);
    assert.equal(d.getMinutes(), 30);
});

test('parseLocalDatetime returns an invalid date for junk rather than throwing', () => {
    for (const value of [null, undefined, 42, 'no es una fecha']) {
        assert.ok(isNaN(parseLocalDatetime(value).getTime()), `para ${String(value)}`);
    }
});

test('formatDate granularity follows the selected view', () => {
    const ts = '2026-01-16 09:05:00';
    assert.equal(formatDate(ts, { hours: 6, interval: 'raw' }), '09:05');
    assert.equal(formatDate(ts, { hours: null, interval: 'daily' }), '16/01');
    assert.equal(formatDate(ts, { hours: null, interval: 'hourly' }), '16/01 09:05');
});

test('formatDate is pure: no shared state, so no view means the default', () => {
    assert.equal(formatDate('2026-01-16 09:05:00'), '16/01 09:05');
});

test('formatDate echoes an unparseable value instead of printing Invalid Date', () => {
    assert.equal(formatDate('qué fecha'), 'qué fecha');
    assert.equal(formatDate(null), '--');
});

test('score bands are contiguous and cover the whole 0-100 range', () => {
    const bands = [];
    for (let s = 0; s <= 100; s++) bands.push(getScoreClass(s));
    assert.ok(bands.every(Boolean), 'todo score debe tener una clase');
    assert.equal(getScoreClass(100), getScoreClass(95));
    assert.notEqual(getScoreClass(0), getScoreClass(100));
    assert.ok(getScoreLabel(90).length > 0);
    assert.ok(getScoreLabel(10).length > 0);
});

test('formatAge speaks in units a grower reads at a glance', () => {
    assert.equal(formatAge(4), 'hace 4 s');
    assert.equal(formatAge(59), 'hace 59 s');
    assert.equal(formatAge(60), 'hace 1 min');
    assert.equal(formatAge(3600), 'hace 1 h 0 min');
    assert.equal(formatAge(3 * 86400), 'hace 3 d');
});

test('formatAge never renders a negative age', () => {
    // The Raspberry has no real-time clock, so its timestamps can land ahead of
    // the browser's. "hace -3 min" is how that used to show up.
    assert.equal(formatAge(0), 'ahora');
    assert.equal(formatAge(-120), 'ahora');
});

test('formatAge says there is no reading rather than printing junk', () => {
    for (const value of [null, undefined, NaN, Infinity, 'viejo']) {
        assert.equal(formatAge(value), 'sin datos', `para ${String(value)}`);
    }
});

test('fmtSigned keeps the sign, which is the whole point of a difference', () => {
    assert.equal(fmtSigned(0.4), '+0.4');
    assert.equal(fmtSigned(-0.4), '-0.4');
    assert.equal(fmtSigned(0), '+0.0');
    assert.equal(fmtSigned(-1.234, 2), '-1.23');
    assert.equal(fmtSigned(null), '--');
});

test('formatDuration reads as time left, not as a number of seconds', () => {
    assert.equal(formatDuration(30), '30 s');
    assert.equal(formatDuration(90), '1 min');
    assert.equal(formatDuration(8 * 3600), '8 h 0 min');
    assert.equal(formatDuration(3 * 3600 + 25 * 60), '3 h 25 min');
});

test('formatDuration shows a dash rather than a countdown already past', () => {
    for (const value of [0, -5, null, undefined, NaN, 'pronto']) {
        assert.equal(formatDuration(value), '--', `para ${String(value)}`);
    }
});

test('humidityDiffLabel names the three states a grower acts on', () => {
    assert.deepEqual(humidityDiffLabel(60, 60), { state: 'on-target', text: '✓ En objetivo' });
    assert.deepEqual(humidityDiffLabel(63, 60), { state: 'on-target', text: '✓ En objetivo' });
    assert.deepEqual(humidityDiffLabel(50, 60), { state: 'too-low', text: '↓ 10.0% por debajo' });
    assert.deepEqual(humidityDiffLabel(70.5, 60), { state: 'too-high', text: '↑ 10.5% por encima' });
});

test('humidityDiffLabel returns nothing when a payload carries no readings', () => {
    // /api/current-data answers 404 with `{"error": "No current data available"}`,
    // and apiGet only throws on 5xx, so the dashboard renders that body: every
    // field is `undefined`. A guard written `humidity !== null` let it through
    // and the card announced "NaN% por encima".
    const empty = {};  // the 404 body, as far as updateCurrentData can tell
    assert.equal(humidityDiffLabel(empty.humidity, empty.target_humidity), null);

    for (const value of [null, undefined, NaN, Infinity, '60', {}]) {
        assert.equal(humidityDiffLabel(value, 60), null, `humedad ${String(value)}`);
        assert.equal(humidityDiffLabel(60, value), null, `objetivo ${String(value)}`);
    }
});

test('humidityDiffLabel keeps a real zero as a reading, not as a missing one', () => {
    assert.equal(humidityDiffLabel(0, 60).state, 'too-low');
    assert.equal(humidityDiffLabel(60, 0).state, 'too-high');
});

// ---------------------------------------------------------------------------
// Feeding
// ---------------------------------------------------------------------------
// These three used to live inside feeding.js, where the panel could not reach
// them. The panel tile and the Riego tab render the same plan, so a second copy
// of the arithmetic is a second answer to "¿cuánto le pongo?".

test('fmtDose writes a dose the way it is read aloud', () => {
    assert.equal(fmtDose(1), '1');
    assert.equal(fmtDose(1.5), '1,5');
    assert.equal(fmtDose(0), '0');
    assert.equal(fmtDose(undefined), '--');
    assert.equal(fmtDose(NaN), '--');
});

test('doseAmount multiplies the per-litre dose by the volume', () => {
    assert.equal(doseAmount({ dose: 4, unit: 'ml/L' }, 10), '40 ml');
    assert.equal(doseAmount({ dose: 0.5, unit: 'g/L' }, 7), '3,5 g');
});

test('doseAmount rounds to a tenth, like the backend does', () => {
    // scale_to_volume() stores round(dose * litres, 1); a locally recomputed
    // amount that rounds differently is the panel and the form disagreeing.
    assert.equal(doseAmount({ dose: 0.33, unit: 'ml/L' }, 10), '3,3 ml');
    assert.equal(doseAmount({ dose: 1.25, unit: 'ml/L' }, 3), '3,8 ml');
});

test('doseAmount survives a missing item or volume instead of rendering NaN', () => {
    assert.equal(doseAmount(undefined, 10), '0 ml');
    assert.equal(doseAmount({ dose: 4, unit: 'ml/L' }, undefined), '0 ml');
    assert.equal(doseAmount({ dose: 'x', unit: 'g/L' }, 10), '0 g');
});

/**
 * "YYYY-MM-DD" for `days` ago, in *local* time.
 *
 * Not toISOString(): that is UTC, and the log stores an Argentine date. Three
 * hours west of Greenwich, an evening test would build tomorrow's date and the
 * helper would correctly answer "-1 day" to a question nobody asked.
 */
function daysAgo(days) {
    const d = new Date();
    d.setHours(12, 0, 0, 0);
    d.setDate(d.getDate() - days);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
         + `-${String(d.getDate()).padStart(2, '0')}`;
}

test('lastWateringLabel counts midnight to midnight, not hour to hour', () => {
    // A watering logged this morning stays "Hoy" all day, and one logged last
    // night does not flip to "Hace 2 días" when the clock passes its hour.
    assert.equal(lastWateringLabel(daysAgo(0)).text, 'Hoy');
    assert.equal(lastWateringLabel(daysAgo(0)).days, 0);
    assert.equal(lastWateringLabel(daysAgo(1)).text, 'Ayer');
    assert.equal(lastWateringLabel(daysAgo(3)).text, 'Hace 3 días');
});

test('lastWateringLabel turns stale at the fourth day, on both screens', () => {
    // The tile's amber edge and the header badge read the same threshold, so
    // the panel and the Riego tab cannot disagree about when it has been a while.
    assert.equal(lastWateringLabel(daysAgo(3)).stale, false);
    assert.equal(lastWateringLabel(daysAgo(4)).stale, true);
});

test('lastWateringLabel crosses a month boundary without going negative', () => {
    // `setDate(0)` walks back into the previous month, which is the case a
    // subtraction on the day number alone gets wrong.
    assert.equal(lastWateringLabel('2026-02-28').days > 0, true);
});

test('lastWateringLabel returns nothing for a tent with no log yet', () => {
    // Not an alarm and not a zero: nobody has watered it, which the tile says
    // in its own words rather than as "Hace 0 días".
    for (const value of [undefined, null, '', 'ayer', {}]) {
        assert.equal(lastWateringLabel(value), null, `fecha ${String(value)}`);
    }
});

test('waterOnlyNote tells a seedling apart from a flush', () => {
    // The same watering - agua sola - at opposite ends of the grow. The tile
    // said "semana de lavado" for both, which about a two-week-old seedling
    // reads as an instruction to wash out a medium nobody fed.
    assert.match(waterOnlyNote('seedling').text, /^Plántula/);
    assert.match(waterOnlyNote('flush').text, /^Semana de lavado/);
    assert.notEqual(waterOnlyNote('seedling').badge, waterOnlyNote('flush').badge);
});

test('waterOnlyNote says nothing when the week does carry doses', () => {
    // null is what /api/feeding/today sends for a week with something due, and
    // the badge then counts products instead.
    for (const value of [null, undefined, '', 'raro', 0]) {
        assert.equal(waterOnlyNote(value), null, `motivo ${String(value)}`);
    }
});

test('growParam leaves the default unspoken so the backend picks the active grow', () => {
    // The selector's own "Cultivo activo" option has value "", and nothing
    // sets state.growId before the first fetch.
    for (const empty of [null, undefined, '']) {
        assert.equal(growParam(empty), '');
    }
});

test('growParam spells every grow and names one by id', () => {
    assert.equal(growParam('all'), 'grow_id=all');
    assert.equal(growParam(7), 'grow_id=7');
    assert.equal(growParam('7'), 'grow_id=7');
});

test('growParam refuses a scope that is not one instead of sending it', () => {
    // The backend answers 400 for these, which on screen is a blank chart with
    // no explanation. Nothing asked for is better than something invented.
    for (const bogus of [NaN, 'todos', 1.5, {}, [1, 2]]) {
        assert.equal(growParam(bogus), '', String(bogus));
    }
});

test('withGrow appends with whichever separator the url already needs', () => {
    assert.equal(withGrow('/api/vpd-score?days=7', 3), '/api/vpd-score?days=7&grow_id=3');
    assert.equal(withGrow('/api/weekly-report', 'all'), '/api/weekly-report?grow_id=all');
    assert.equal(withGrow('/api/weekly-report', null), '/api/weekly-report');
});

test('outputPinLabel names the node when the node is what switches the relay', () => {
    // El defecto: con los relés en el nodo, la pantalla decía "BCM 25" para la
    // ventilación. El 25 existe en las dos placas y en el ESP32 es el
    // humidificador, así que mandaba a medir el header correcto con la etiqueta
    // equivocada.
    const label = outputPinLabel({ scope: 'remote', node: 'ESP32 relés', number: 27 });
    assert.equal(label.text, 'GPIO 27');
    assert.match(label.title, /ESP32 relés/);
    assert.match(label.title, /no la Raspberry/);
});

test('outputPinLabel keeps saying BCM when the Raspberry is the one switching', () => {
    assert.equal(outputPinLabel({ scope: 'local', node: null, number: 7 }).text, 'BCM 7');
});

test('outputPinLabel shows nothing rather than a pin it was not given', () => {
    // Un "BCM NaN" o un "GPIO undefined" en la tarjeta es peor que el hueco:
    // parece un dato.
    for (const source of [null, undefined, {}, { scope: 'remote' },
                          { scope: 'local', number: null }, { scope: 'remote', number: 'IN1' }]) {
        assert.deepEqual(outputPinLabel(source), { text: '', title: '' }, JSON.stringify(source));
    }
});

test('outputPinLabel still names a remote pin when nobody named the node', () => {
    const label = outputPinLabel({ scope: 'remote', node: '', number: 25 });
    assert.equal(label.text, 'GPIO 25');
    assert.match(label.title, /nodo remoto/);
});

test('relaySourceLabel says which board the panel is talking about', () => {
    assert.equal(relaySourceLabel({ scope: 'remote', node: 'ESP32 relés', number: 25 }).text,
                 'ESP32 relés');
    assert.equal(relaySourceLabel({ scope: 'local', node: null, number: 7 }).text,
                 'GPIO de la Raspberry');
    // Sin payload el encabezado queda vacío: inventar una placa es el defecto.
    assert.deepEqual(relaySourceLabel(undefined), { text: '', title: '' });
    assert.deepEqual(relaySourceLabel({ scope: 'otra' }), { text: '', title: '' });
});
