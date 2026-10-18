/**
 * The sensor table in Sistema: one row per sensor, the diagnostic row under a
 * sensor that needs one, the drift line, the outdoor reference row and the
 * section badge. Reading cells are written here; the correction cells belong
 * to system-calibration.js.
 */

import { DRIFT_FLAG_LABELS, SENSOR_ROLES, SENSOR_STATES } from './health-model.js';
import { escapeHtml, fmtNum, fmtSigned, formatAge, icon, iconText } from './util.js';

// ===============================
// Tabla de sensores
// ===============================

/**
 * Celdas de lectura de una fila de sensor, sin las de calibración.
 *
 * Compartidas con system.js, que agrega las celdas de corrección a las mismas
 * filas: así la tabla tiene una sola forma aunque la escriban dos módulos.
 */
export const SENSOR_READ_CELLS = `
    <td class="sensor-name">
        <span class="sensor-name-line"><span class="ac-dot" data-f="dot"></span><span data-f="name"></span></span>
    </td>
    <td data-f="role" data-label="Rol"></td>
    <td class="ac-num" data-f="temp" data-label="Temp."></td>
    <td class="ac-num" data-f="hum" data-label="Humedad"></td>
    <td class="ac-num" data-f="age" data-label="Última"></td>`;

function fillCells(row, values) {
    for (const [field, value] of Object.entries(values)) {
        const cell = row.querySelector(`[data-f="${field}"]`);
        if (!cell) continue;
        if (field === 'dot') {
            cell.className = `ac-dot ${value || ''}`.trim();
        } else {
            cell.textContent = value ?? '';
        }
    }
}

/**
 * El diagnóstico de un sensor, para su fila de abajo: qué pasa y qué hacer.
 *
 * Va en una fila aparte y no en la celda del nombre porque el texto largo
 * estiraba cada fila y apretaba las columnas de lecturas. Vacío cuando no hay
 * nada que decir, que es lo normal con un sensor que posta sin deriva.
 */
function sensorWhyHtml(sensor) {
    const flags = (sensor.flags || []).map(flag => DRIFT_FLAG_LABELS[flag] || flag);
    const parts = [];
    if (sensor.state !== 'posting') {
        parts.push(`<span>${escapeHtml(sensor.state_label || '')}</span>`);
        if (sensor.state_detail) parts.push(`<span>${escapeHtml(sensor.state_detail)}</span>`);
    }
    if (flags.length) parts.push(`<span>${escapeHtml(flags.join(' · '))}</span>`);
    if (sensor.action) parts.push(`<span class="sensor-why-action">→ ${escapeHtml(sensor.action)}</span>`);
    return parts.join('');
}

function sensorRowValues(sensor) {
    const look = SENSOR_STATES[sensor.state] || SENSOR_STATES.missing;
    const role = SENSOR_ROLES[sensor.role] || { label: sensor.role || '' };
    // Una lectura vieja se muestra apagada. Mostrar 21.6 °C en tono fuerte
    // cuando es de hace cuatro horas es, en sí mismo, decir algo falso.
    return {
        dot: look.dot,
        name: sensor.label || 'Sensor',
        role: role.label,
        temp: fmtNum(sensor.temperature, 1),
        hum: fmtNum(sensor.humidity, 1),
        age: formatAge(sensor.age_seconds),
    };
}

function rowFor(tbody, key) {
    return [...tbody.rows].find(row => row.dataset.sensorRow === key) || null;
}

function whyRowFor(tbody, key) {
    return [...tbody.rows].find(row => row.classList.contains('sensor-why-row')
        && row.dataset.whyFor === key) || null;
}

/**
 * La fila de diagnóstico debajo de `row`, o ninguna si `html` está vacío.
 *
 * Se crea y se quita según haga falta: un sensor que vuelve a postear pierde
 * su fila de diagnóstico y no deja un renglón vacío atrás.
 */
function setWhyRow(tbody, row, html) {
    const key = row.dataset.sensorRow;
    let why = whyRowFor(tbody, key);
    if (!html) {
        if (why) why.remove();
        return;
    }
    if (!why) {
        why = document.createElement('tr');
        why.className = 'sensor-why-row';
        why.dataset.whyFor = key;
        why.innerHTML = `<td colspan="${row.cells.length}"><div class="sensor-why"></div></td>`;
        row.after(why);
    }
    why.querySelector('.sensor-why').innerHTML = html;
}

/**
 * Una fila por sensor, actualizada en su lugar.
 *
 * Las celdas de lectura se reescriben cada consulta, pero las de corrección
 * no se tocan: system.js las llena una vez y las deja, porque reescribirlas
 * cada diez segundos borraría lo que alguien está tipeando.
 */
function renderSensorRows(tbody, sensors) {
    const wanted = new Set(sensors.map(sensor => sensor.sensor_id));
    // The loading placeholder has no id, and goes the first time there is an answer.
    // The diagnostic rows have none either, and are handled with their sensor below.
    [...tbody.rows].forEach(row => {
        if (!row.dataset.sensorRow && !row.classList.contains('sensor-why-row')) row.remove();
    });
    // Sólo las filas que esta pantalla creó. Las de corrección sin sensor
    // (data-orphan) las marca system.js y no las borra nadie más.
    [...tbody.rows].forEach(row => {
        if (row.dataset.sensorRow && !row.hasAttribute('data-orphan')
            && row.dataset.sensorRow !== 'outdoor' && !wanted.has(row.dataset.sensorRow)) {
            row.remove();
        }
    });
    // A diagnostic row whose sensor row went away goes with it.
    [...tbody.rows].forEach(row => {
        if (row.classList.contains('sensor-why-row') && !rowFor(tbody, row.dataset.whyFor)) row.remove();
    });

    for (const sensor of sensors) {
        let row = rowFor(tbody, sensor.sensor_id);
        if (!row) {
            row = document.createElement('tr');
            row.dataset.sensorRow = sensor.sensor_id;
            row.innerHTML = SENSOR_READ_CELLS + CALIB_CELLS;
            const orphan = [...tbody.rows].find(other => other.hasAttribute('data-orphan'));
            tbody.insertBefore(row, orphan || null);
        }
        row.removeAttribute('data-orphan');
        fillCells(row, sensorRowValues(sensor));
        // Only a sensor that is not posting (or has a drift flag) has anything to say under it.
        const why = sensor.state !== 'posting' || (sensor.flags || []).length;
        setWhyRow(tbody, row, why ? sensorWhyHtml(sensor) : '');
    }
}

/** Las tres celdas de corrección; system.js las llena. Acá sólo existen. */
export const CALIB_CELLS = `
    <td class="ac-num" data-calib-cell="temperature" data-label="Corrección °C"></td>
    <td class="ac-num" data-calib-cell="humidity" data-label="Corrección %"></td>
    <td data-calib-cell="notes" data-label="Nota"></td>`;

/** La línea de deriva bajo la tabla: sólo cuando hay testigo que comparar. */
function driftLineHtml(payload) {
    const delta = payload.delta;
    const drift = payload.drift || null;
    const baseline = drift && drift.baseline;

    // The offset between two sensors in the same tent proves nothing on its own
    // - position and airflow are real. It is the *change* from the learned
    // baseline that means something, so the baseline is shown next to it.
    const deltaText = delta
        ? `Testigo − primario: ${fmtSigned(delta.temperature, 1)} °C · ${fmtSigned(delta.humidity, 1)} %`
        : 'Testigo − primario: sin par de lecturas frescas';
    const baselineText = baseline
        ? `base ${fmtSigned(baseline.temperature, 2)} °C / ${fmtSigned(baseline.humidity, 2)} %`
        : 'base: aprendiendo (~200 muestras)';

    let verdict;
    if (!drift) {
        // sensor_status expires with the rest of the dashboard keys, so no
        // verdict means the control loop is not publishing one.
        verdict = `${icon('hourglass')}El lazo de control no publicó un veredicto`;
    } else if (drift.ok) {
        verdict = `${icon('circle-check')}Sin deriva`;
    } else {
        const messages = (drift.messages || []).map(message => escapeHtml(message));
        verdict = `${icon('triangle-alert')}${messages.join(' · ') || 'Revisar los sensores'}`;
    }
    return `<span>${escapeHtml(deltaText)}</span><span>${escapeHtml(baselineText)}</span>`
        + `<span class="sensors-drift-verdict">${verdict}</span>`;
}

// ===============================
// Render
// ===============================

export function setBadge(id, badge) {
    const el = document.getElementById(id);
    if (!el) return;
    el.className = `sensor-badge ${badge.state}`.trim();
    el.innerHTML = iconText(badge);
}

export function renderSensors(health) {
    const tbody = document.getElementById('indoor-sensors-grid');
    if (!tbody) return;

    const sensors = health.sensors || [];
    if (!sensors.length) {
        // Nothing configured is a state of its own, not an empty table.
        tbody.innerHTML = `<tr class="sensors-empty"><td colspan="8">Sin sensores configurados. ${escapeHtml(health.detail || '')}</td></tr>`;
    } else {
        renderSensorRows(tbody, sensors);
    }

    const drift = document.getElementById('drift-line');
    if (drift) {
        // Gated on a witness existing, not on the number of sensors: an observer is
        // not something to compare against, and a difference line next to one would
        // claim a cross-check the installation does not have.
        drift.hidden = !health.has_witness;
        if (health.has_witness) drift.innerHTML = driftLineHtml(health);
    }
}

/**
 * El exterior no tiene lectura en /api/health, sólo si su DHT responde: la
 * fila dice eso y nada más, para no inventar un número que no llegó.
 */
export function setOutdoorRow(outdoor) {
    const tbody = document.getElementById('indoor-sensors-grid');
    if (!tbody) return;
    let row = rowFor(tbody, 'outdoor');
    if (!row) {
        row = document.createElement('tr');
        row.dataset.sensorRow = 'outdoor';
        row.innerHTML = SENSOR_READ_CELLS + CALIB_CELLS;
        tbody.appendChild(row);
    }
    const ok = outdoor.ok;
    fillCells(row, {
        dot: ok === true ? 'ac-dot--ok' : ok === false ? 'ac-dot--alert' : 'ac-dot--idle',
        name: 'Exterior',
        role: 'Afuera, sólo se grafica',
        temp: '—',
        hum: '—',
        age: '—',
    });
    setWhyRow(tbody, row, ok === false && outdoor.error
        ? `<span>${escapeHtml(outdoor.error)}</span>` : '');
    row.querySelectorAll('[data-calib-cell]').forEach(cell => { cell.textContent = '—'; });
}
