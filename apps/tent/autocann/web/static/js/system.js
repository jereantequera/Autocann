/**
 * Sistema: calibration, the installation's sensors, the database and the screen.
 *
 * Calibration has had a validated endpoint on both verbs since the backend
 * audit and no way to reach it from the browser. The offsets correct every
 * reading the rest of the app shows, so editing them with curl meant the one
 * knob nobody could see was the one that moves all the numbers.
 *
 * The sensor table is two writers in one row: health.js writes the readings
 * every ten seconds, and this module writes the correction cells once per
 * calibration version. The second never happens on the poll, so a value that
 * is being typed is not wiped by a reading arriving.
 */

import { apiGet, apiPost } from './api.js';
import { escapeHtml, fmtNum, fmtSigned, formatAge, icon } from './util.js';
import { fetchHealth, latestHealth } from './health.js';
import { applyTheme, readTheme } from './theme.js';

/**
 * Stored offsets by sensor id.
 *
 * /api/calibration answers with a list of rows, not a map; keying it here is
 * what lets a row find its own sensor instead of whatever row happened to be
 * in that position.
 */
let calibration = {};
/** Bumped on every calibration fetch: a row is rebuilt when its version is stale. */
let calibrationVersion = 0;
/**
 * Sensors saved in this page session.
 *
 * The caveat that matters - saved is not applied until the loop restarts - is
 * said once, where the save button is, and stays until the page is reloaded,
 * which is what restarting the loop leads to anyway.
 */
const savedThisSession = new Set();

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

/** "a" reads as a label, "dht22_outdoor" reads as itself. */
export function sensorLabel(sensorId) {
    return sensorId.length <= 2 ? `Sensor ${sensorId.toUpperCase()}` : sensorId;
}

/**
 * Which of the rows differ from what the server holds.
 *
 * Compared as numbers and trimmed text: the field is a string, the stored value
 * is a number, and "0" against 0 is not a change.
 */
export function changedCalibrations(rows, stored) {
    return rows.filter(row => {
        const saved = stored[row.sensorId] || {};
        return Number(row.temperature) !== (Number(saved.temperature_offset) || 0)
            || Number(row.humidity) !== (Number(saved.humidity_offset) || 0)
            || (row.notes || null) !== (saved.notes || null);
    });
}

/** Ids a calibration row could be about: the posting sensors, then the ones only the table knows. */
export function calibrationIds(live, stored) {
    const orphans = Object.keys(stored).filter(id => !live.includes(id)).sort();
    return { live: [...live].sort(), orphans };
}

// ---------------------------------------------------------------------------
// The table
// ---------------------------------------------------------------------------

function sensorRows() {
    return [...(document.getElementById('indoor-sensors-grid')?.rows || [])];
}

/** The rows health.js writes: the sensors, not the outdoor reference or an orphan. */
function liveSensorIds() {
    return sensorRows()
        .filter(row => row.dataset.sensorRow && row.dataset.sensorRow !== 'outdoor'
            && !row.hasAttribute('data-orphan'))
        .map(row => row.dataset.sensorRow);
}

function calibrationCellsHtml(sensorId, current) {
    const id = escapeHtml(sensorId);
    const temperature = Number(current.temperature_offset) || 0;
    const humidity = Number(current.humidity_offset) || 0;
    return {
        temperature: `<input class="ac-field__input ac-field__input--num" type="number" step="0.05"
            min="-20" max="20" inputmode="decimal" data-calib="temperature"
            aria-label="Corrección de temperatura del sensor ${id}"
            value="${fmtNum(temperature, 2, '0')}">`,
        humidity: `<input class="ac-field__input ac-field__input--num" type="number" step="0.5"
            min="-50" max="50" inputmode="decimal" data-calib="humidity"
            aria-label="Corrección de humedad del sensor ${id}"
            value="${fmtNum(humidity, 2, '0')}">`,
        notes: `<div class="calib-note">
            <input class="ac-field__input" type="text" data-calib="notes" maxlength="200"
                aria-label="Nota de la corrección del sensor ${id}"
                placeholder="Contra el termómetro de referencia"
                value="${escapeHtml(current.notes || '')}">
            <button type="button" class="ac-btn ac-btn--ghost" data-calib-zero="${id}">Poner en 0</button>
        </div>`,
    };
}

/** Fills the three correction cells of a row, once per calibration version. */
function fillCalibrationRow(row, sensorId) {
    const cells = calibrationCellsHtml(sensorId, calibration[sensorId] || {});
    for (const [field, html] of Object.entries(cells)) {
        const cell = row.querySelector(`[data-calib-cell="${field}"]`);
        if (cell) cell.innerHTML = html;
    }
    row.dataset.calibVersion = String(calibrationVersion);
    row.classList.toggle('is-set', (Number(calibration[sensorId]?.temperature_offset) || 0) !== 0
        || (Number(calibration[sensorId]?.humidity_offset) || 0) !== 0);
    row.classList.remove('is-dirty');
}

function orphanRowHtml(sensorId) {
    // An offset stored against an id nothing posts under any more corrects
    // nothing, and looks exactly like one that works until it is said. The row
    // keeps the table's shape, and says so in the place a reading would be.
    return `
        <td class="sensor-name">
            <span class="sensor-name-line"><span class="ac-dot ac-dot--idle"></span><span>${escapeHtml(sensorLabel(sensorId))}</span></span>
            <span class="sensor-why"><span>Ningún sensor está posteando con este id: esta corrección no se le aplica a nada.</span></span>
        </td>
        <td data-label="Rol">—</td>
        <td class="ac-num" data-label="Temp.">—</td>
        <td class="ac-num" data-label="Humedad">—</td>
        <td class="ac-num" data-label="Última">—</td>
        <td data-calib-cell="temperature" data-label="Corrección °C"></td>
        <td data-calib-cell="humidity" data-label="Corrección %"></td>
        <td data-calib-cell="notes" data-label="Nota"></td>`;
}

/**
 * Brings every correction cell up to the current calibration.
 *
 * Rows the user is editing (`is-dirty`) are left alone: a fetch that lands
 * mid-typing must not erase what was typed.
 */
function renderCalibrationRows() {
    const tbody = document.getElementById('indoor-sensors-grid');
    if (!tbody) return;

    const health = latestHealth();
    for (const row of sensorRows()) {
        const id = row.dataset.sensorRow;
        if (!id || id === 'outdoor') continue;
        if (row.classList.contains('is-dirty')) continue;
        if (row.dataset.calibVersion === String(calibrationVersion)) continue;
        fillCalibrationRow(row, id);
    }

    // An orphan is only a claim once health has said which sensors exist: before
    // that, every stored id would look like one.
    if (!health) return;
    const live = liveSensorIds();
    const { orphans } = calibrationIds(live, calibration);
    for (const id of orphans) {
        let row = sensorRows().find(other => other.dataset.sensorRow === id);
        if (!row) {
            row = document.createElement('tr');
            row.dataset.sensorRow = id;
            row.dataset.orphan = '';
            row.innerHTML = orphanRowHtml(id);
            tbody.appendChild(row);
        }
        if (row.dataset.calibVersion !== String(calibrationVersion)
            && !row.classList.contains('is-dirty')) {
            fillCalibrationRow(row, id);
        }
    }
}

function renderCalibrationBadge() {
    const badge = document.getElementById('calibration-badge');
    if (!badge) return;
    const corrected = Object.values(calibration).filter(row =>
        Number(row.temperature_offset) || Number(row.humidity_offset)).length;
    badge.className = corrected ? 'sensor-badge ok' : 'sensor-badge';
    badge.textContent = corrected ? `${corrected} con corrección` : 'todos en 0';
}

/** The saved values of one row, read from its inputs. */
function readRow(row) {
    const field = name => row.querySelector(`[data-calib="${name}"]`);
    const number = input => Number(input?.value) || 0;
    return {
        sensorId: row.dataset.sensorRow,
        temperature: number(field('temperature')),
        humidity: number(field('humidity')),
        notes: field('notes')?.value.trim() || null,
    };
}

// ---------------------------------------------------------------------------
// Saving
// ---------------------------------------------------------------------------

/**
 * Saves the rows that differ from the server, one request each.
 *
 * Rows that did not change are not sent: a save that rewrites every sensor
 * says "saved" about values nobody touched.
 */
async function saveChanged(rows, message) {
    if (!rows.length) {
        message.textContent = 'Nada cambió desde la última vez.';
        message.className = 'sys-note-inline';
        return;
    }
    message.textContent = 'Guardando…';
    message.className = 'sys-note-inline';

    for (const row of rows) {
        const body = {
            sensor_id: row.sensorId,
            temperature_offset: row.temperature,
            humidity_offset: row.humidity,
            notes: row.notes,
        };
        const result = await apiPost('/api/calibration', body);
        if (!result.ok) {
            message.textContent = `${sensorLabel(row.sensorId)}: ${result.error}`;
            message.className = 'sys-note-inline is-error';
            return;
        }
        savedThisSession.add(row.sensorId);
    }

    // Said out loud and left there: the loop reads the offsets once at startup,
    // so a saved value that is not yet in effect would otherwise look applied.
    message.textContent = 'Guardado · se aplica cuando reinicies el lazo de control';
    message.className = 'sys-note-inline is-ok';
    fetchCalibration();
}

function saveAll() {
    const rows = sensorRows()
        .filter(row => row.querySelector('[data-calib="temperature"]'))
        .map(readRow);
    saveChanged(changedCalibrations(rows, calibration), document.getElementById('calibration-msg'));
}

/** "Poner en 0" saves that one sensor at zero, as it always did: the button says what it does. */
function zeroRow(sensorId) {
    const row = sensorRows().find(other => other.dataset.sensorRow === sensorId);
    if (!row) return;
    row.querySelector('[data-calib="temperature"]').value = '0';
    row.querySelector('[data-calib="humidity"]').value = '0';
    saveChanged(changedCalibrations([readRow(row)], calibration),
        document.getElementById('calibration-msg'));
}

function fetchCalibration() {
    apiGet('/api/calibration', { key: 'calibration' }).then(data => {
        if (!data || !Array.isArray(data.calibration)) return;
        calibration = Object.fromEntries(
            data.calibration.filter(row => row && row.sensor_id).map(row => [row.sensor_id, row]));
        calibrationVersion += 1;
        renderCalibrationRows();
        renderCalibrationBadge();
        if (savedThisSession.size) {
            const message = document.getElementById('calibration-msg');
            if (message && !message.textContent) {
                message.textContent = 'Guardado · se aplica cuando reinicies el lazo de control';
                message.className = 'sys-note-inline is-ok';
            }
        }
    });
}

// ---------------------------------------------------------------------------
// Database
// ---------------------------------------------------------------------------

function renderDatabase(stats) {
    const set = (id, value) => {
        const el = document.getElementById(id);
        if (el) el.textContent = value;
    };
    const count = value => (Number.isFinite(value) ? value.toLocaleString('es-AR') : '--');

    set('stat-db-size', stats.database_size_mb !== undefined ? `${stats.database_size_mb} MB` : '--');
    set('stat-grows', count(stats.grow_count));
    set('stat-readings', count(stats.sensor_data_count));
    set('stat-events', count(stats.control_events_count));
    set('stat-span', stats.oldest_record
        ? `${stats.oldest_record.split(' ')[0]} → ${(stats.newest_record || '').split(' ')[0]}`
        : 'sin lecturas');
}

function fetchDatabase() {
    // Not polled: the file grows by kilobytes an hour, and nobody watches this
    // card waiting for it to move.
    apiGet('/api/database-stats', { key: 'database-stats' })
        .then(data => { if (data) renderDatabase(data); });
}

// ---------------------------------------------------------------------------
// Session
// ---------------------------------------------------------------------------
// Only shown where there is a PIN to log out of. `/api/auth` answers whether
// one is configured even when it is not, so the dashboard reads the
// configuration instead of inferring it from a 401 it would never see while
// logged in.

function fetchSession() {
    apiGet('/api/auth', { key: 'auth' }).then(data => {
        const box = document.getElementById('session-section');
        if (box && data) box.hidden = !data.pin_required;
    });
}

async function logout() {
    const result = await apiPost('/api/logout', {});
    // Reload either way: on success the next request answers 401 and lands on
    // the login page, and on failure the page should show whatever is actually
    // true rather than a button that appeared to do nothing.
    window.location.reload();
    return result;
}

// ---------------------------------------------------------------------------
// The screen: index, theme
// ---------------------------------------------------------------------------

/** The in-page index: scrolls to the section and marks the link, without touching the hash. */
function jumpTo(sectionId, view) {
    const target = document.getElementById(sectionId);
    if (!target) return;
    target.scrollIntoView({
        behavior: window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
        block: 'start',
    });
    view.querySelectorAll('[data-sys-jump]').forEach(link => {
        const current = link.dataset.sysJump === sectionId;
        link.classList.toggle('is-current', current);
        if (current) link.setAttribute('aria-current', 'location');
        else link.removeAttribute('aria-current');
    });
}

function paintThemeChoice(name) {
    document.querySelectorAll('[data-theme-choice]').forEach(button => {
        button.setAttribute('aria-pressed', String(button.dataset.themeChoice === name));
    });
}

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------

function refreshSystem() {
    fetchHealth();
    fetchCalibration();
    fetchDatabase();
    fetchSession();
}

function wireSystem() {
    // `.view` is not decoration here: the nav tab carries the same data-view and
    // comes first in the document, so a bare attribute selector binds every one
    // of these buttons to the anchor instead of the section.
    const view = document.querySelector('.view[data-view="sistema"]');
    if (!view) return;

    paintThemeChoice(readTheme());

    view.addEventListener('click', event => {
        const jump = event.target.closest('[data-sys-jump]');
        if (jump) {
            // Preventing the default is the point: an anchor here would change
            // the hash, and the router would read it as a view and leave Sistema.
            event.preventDefault();
            jumpTo(jump.dataset.sysJump, view);
            return;
        }
        const theme = event.target.closest('[data-theme-choice]');
        if (theme) {
            paintThemeChoice(applyTheme(theme.dataset.themeChoice));
            return;
        }
        if (event.target.closest('[data-sys="refresh"]')) {
            refreshSystem();
            return;
        }
        if (event.target.closest('[data-sys="logout"]')) {
            logout();
            return;
        }
        if (event.target.closest('#calibration-save')) {
            saveAll();
            return;
        }
        const zero = event.target.closest('[data-calib-zero]');
        if (zero) zeroRow(zero.dataset.calibZero);
    });

    // Marks a row as being typed in, so a calibration fetch leaves it alone.
    view.addEventListener('input', event => {
        const row = event.target.closest('#indoor-sensors-grid tr[data-sensor-row]');
        if (row) row.classList.add('is-dirty');
    });

    // Only on entering: none of this moves on its own, and the view is opened
    // to change something rather than to watch it.
    document.addEventListener('view:enter', event => {
        if (event.detail?.view === 'sistema') refreshSystem();
    });
    // The rows exist once health has answered; the correction cells follow it.
    document.addEventListener('health:updated', renderCalibrationRows);
}

export { fetchCalibration, fetchSession, logout, refreshSystem, wireSystem };
