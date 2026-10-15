/**
 * ¿Anda? — el estado de los sensores, del lazo y del histórico, en un solo lugar.
 *
 * Antes esto estaba repartido en tres fuentes que podían contradecirse, y se
 * contradecían: el badge del hero salía de `sensor_status` (la opinión del lazo
 * sobre el primario), cada tarjeta de la antigüedad de su propia lectura en
 * Redis, y el cartel de anomalías de la antigüedad de la última fila en la base
 * de datos. Con el sistema en pausa los tres decían cosas distintas a la vez:
 * un cartel rojo de "sensor posiblemente desconectado" arriba de dos tarjetas
 * verdes, con los sensores posteando perfecto.
 *
 * Todo lo que esta pantalla dice sobre si algo anda sale ahora de /api/health,
 * que lo calcula una vez mirando todas las fuentes juntas. Dos elementos de la
 * pantalla no pueden discrepar si leen del mismo objeto.
 */

import { apiGet } from './api.js';
import { escapeHtml, fmtNum, fmtSigned, formatAge, icon, iconText } from './util.js';

// ===============================
// Vocabulario
// ===============================
// The tent has two DHT22 on one node: a primary that drives control and a
// witness that drives nothing. The witness exists to catch the primary
// reporting a plausible wrong number, so it is only worth having if somebody
// can see it - which is what this section is.

const SENSOR_ROLES = {
    primary: { icon: 'crosshair', label: 'Primario · controla' },
    witness: { icon: 'eye', label: 'Testigo · sólo vigila' },
    // Shown and nothing else. The sensor above the lamp is worth reading and
    // useless as a witness: its offset against the primary moves with the
    // photoperiod, not with the sensors' health.
    observer: { icon: 'chart-line', label: 'Observación · sólo informa' },
};

/** Same wording the control loop logs, so the two are recognisably the same fault. */
const DRIFT_FLAG_LABELS = {
    stuck: 'valor congelado',
    railed: 'pegado al tope de escala',
};

/**
 * Cómo se ve cada estado.
 *
 * Cuatro estados, no dos. "Nunca posteó" y "dejó de postear" se ven iguales en
 * una tarjeta que sólo sabe ok/error, y mandan a lugares distintos: la primera
 * al id del firmware y al cableado, la segunda a la red y al log del nodo. Y
 * "postea pero el detector lo marcó" es el que más esconde un badge verde.
 */
const SENSOR_STATES = {
    posting: { icon: 'circle-check', css: 'is-ok', badge: 'ok' },
    suspect: { icon: 'triangle-alert', css: 'is-suspect', badge: 'stale' },
    stale: { icon: 'clock', css: 'is-stale', badge: 'error' },
    missing: { icon: 'circle-x', css: 'is-missing', badge: 'error' },
};

/** El último estado conocido, para que otros módulos no vuelvan a pedirlo. */
let snapshot = null;

const latestHealth = () => snapshot;

// ===============================
// Helpers puros
// ===============================

/**
 * El badge de la sección: cuántos sensores están posteando, de cuántos.
 *
 * Un número, no un tilde: "2 de 2 posteando" sigue siendo verdad cuando alguien
 * agrega un tercer sensor y se olvida de configurarlo, y un tilde verde no.
 */
function sensorsBadge(sensors) {
    if (!sensors || !sensors.length) return { text: 'sin sensores', state: '' };
    const live = sensors.filter(s => s.state === 'posting' || s.state === 'suspect').length;
    return {
        icon: live === sensors.length ? 'circle-check' : live ? 'triangle-alert' : 'circle-x',
        text: `${live} de ${sensors.length} posteando`,
        state: live === sensors.length ? 'ok' : live ? 'stale' : 'error',
    };
}

// ===============================
// Tarjetas
// ===============================

function sensorCardHtml(sensor) {
    const role = SENSOR_ROLES[sensor.role] || { icon: 'map-pin', label: sensor.role || '' };
    const look = SENSOR_STATES[sensor.state] || SENSOR_STATES.missing;
    // Una lectura vieja se muestra apagada. Mostrar 21.6 °C en naranja fuerte
    // cuando es de hace cuatro horas es, en sí mismo, decir algo falso.
    const cold = sensor.state === 'stale' || sensor.state === 'missing';

    const flags = (sensor.flags || [])
        .map(flag => `<div class="sensor-flag">${icon('triangle-alert')}${escapeHtml(DRIFT_FLAG_LABELS[flag] || flag)}</div>`)
        .join('');

    return `
        <div class="sensor-card ${look.css}">
            <div class="sensor-card-header">
                <div class="sensor-card-name">${escapeHtml(sensor.label || 'Sensor')}</div>
                <span class="sensor-badge ${look.badge}">${iconText({ icon: look.icon, text: sensor.state_label })}</span>
            </div>
            <div class="sensor-card-role">${iconText({ icon: role.icon, text: role.label })}</div>
            <div class="sensor-card-readings${cold ? ' is-cold' : ''}">
                <div>
                    <div class="metric-label">Temperatura</div>
                    <div class="metric-value sensor-temp">${fmtNum(sensor.temperature, 1)}<span class="metric-unit">°C</span></div>
                </div>
                <div>
                    <div class="metric-label">Humedad</div>
                    <div class="metric-value sensor-humidity">${fmtNum(sensor.humidity, 1)}<span class="metric-unit">%</span></div>
                </div>
            </div>
            <div class="sensor-card-footer">
                <span title="VPD del aire a la temperatura del sensor, no de la hoja">VPD aire ${fmtNum(sensor.vpd, 2)} kPa</span>
                <span>${escapeHtml(formatAge(sensor.age_seconds))}</span>
            </div>
            ${flags}
            ${sensor.state === 'posting' ? '' : `
                <div class="sensor-why">${escapeHtml(sensor.state_detail || '')}</div>`}
            ${sensor.action ? `<div class="sensor-action">→ ${escapeHtml(sensor.action)}</div>` : ''}
        </div>
    `;
}

function driftVerdictHtml(drift) {
    if (!drift) {
        // sensor_status expires with the rest of the dashboard keys, so no
        // verdict means the control loop is not publishing one.
        return `<div class="sensor-flag">${icon('hourglass')}El lazo de control no publicó un veredicto</div>`;
    }
    if (drift.ok) {
        return `<div class="drift-verdict ok">${icon('circle-check')}Sin deriva</div>`;
    }
    const messages = (drift.messages || [])
        .map(message => `<div class="sensor-flag">${icon('triangle-alert')}${escapeHtml(message)}</div>`)
        .join('');
    return messages || `<div class="sensor-flag">${icon('triangle-alert')}Revisar los sensores</div>`;
}

function comparisonCardHtml(payload) {
    const delta = payload.delta;
    const drift = payload.drift || null;
    const baseline = drift && drift.baseline;

    // The offset between two sensors in the same tent proves nothing on its own
    // - position and airflow are real. It is the *change* from the learned
    // baseline that means something, so the baseline is shown next to it.
    const baselineText = baseline
        ? `Base: ${fmtSigned(baseline.temperature, 2)} °C / ${fmtSigned(baseline.humidity, 2)} %`
        : 'Base: aprendiendo (~200 muestras)';

    // Styled as a value only when it is one: "sin par de lecturas frescas" set
    // in the big lime numerals reads as a measurement at a glance.
    const deltaText = delta
        ? `${fmtSigned(delta.temperature, 1)} °C · ${fmtSigned(delta.humidity, 1)} %`
        : 'sin par de lecturas frescas';
    const deltaClass = delta ? 'metric-value sensor-delta' : 'sensor-delta-absent';

    return `
        <div class="sensor-card sensor-card-compare">
            <div class="sensor-card-header">
                <div class="sensor-card-name">Diferencia</div>
            </div>
            <div class="sensor-card-role">${icon('arrow-left-right')}testigo − primario</div>
            <div class="${deltaClass}">${escapeHtml(deltaText)}</div>
            <div class="sensor-card-footer"><span>${escapeHtml(baselineText)}</span></div>
            ${driftVerdictHtml(drift)}
        </div>
    `;
}

// ===============================
// Render
// ===============================

function setBadge(id, badge) {
    const el = document.getElementById(id);
    if (!el) return;
    el.className = `sensor-badge ${badge.state}`.trim();
    el.innerHTML = iconText(badge);
}

function renderSensors(health) {
    const grid = document.getElementById('indoor-sensors-grid');
    if (!grid) return;

    const sensors = health.sensors || [];
    if (!sensors.length) {
        grid.innerHTML = `
            <div class="sensor-card is-missing">
                <div class="sensor-card-header">
                    <div class="sensor-card-name">Sin sensores configurados</div>
                </div>
                <div class="sensor-why">${escapeHtml(health.detail || '')}</div>
            </div>
        `;
        return;
    }

    const cards = sensors.map(sensorCardHtml);
    // Gated on a witness existing, not on the number of sensors: an observer is
    // not something to compare against, and a difference card next to one would
    // claim a cross-check the installation does not have.
    if (health.has_witness) cards.push(comparisonCardHtml(health));
    grid.innerHTML = cards.join('');
}

/**
 * El cartel rojo de arriba de todo.
 *
 * Sólo para lo que está roto de verdad y ahora: una pausa es intencional y ya
 * la anuncia el header, así que acá no entra. Un cartel que aparece por algo
 * que alguien hizo a propósito es el que enseña a ignorar los carteles.
 */
function renderAlert(health) {
    const alert = document.getElementById('sensor-alert');
    const message = document.getElementById('sensor-alert-message');
    if (!alert || !message) return;

    // Sólo lo que está roto ahora y es de los sensores o del lazo. Un hueco en
    // el histórico ya lo cuenta el cartel de anomalías, y decir lo mismo dos
    // veces, en dos cajas rojas pegadas, es la forma más rápida de que alguien
    // deje de leer las dos.
    const broken = (health.sensors || []).some(sensor => sensor.level === 'error')
        || health.loop?.level === 'error';

    if (broken) {
        message.textContent = `${health.headline}. ${health.detail}`;
        alert.style.display = 'flex';
    } else {
        alert.style.display = 'none';
    }
}

function renderHealth(health) {
    snapshot = health;

    renderSensors(health);

    // The section badge over the cards in Sistema, the only place sensors are
    // shown now that the panel reads the loop and the sensors from its status.
    setBadge('sensors-badge', sensorsBadge(health.sensors));

    const outdoor = health.outdoor || {};
    setBadge('sensor-status-outdoor',
        outdoor.ok === true ? { icon: 'circle-check', text: 'OK', state: 'ok' }
            : outdoor.ok === false ? { icon: 'circle-x', text: 'Error', state: 'error' }
                : { icon: 'hourglass', text: '', state: '' });

    renderAlert(health);

    // The panel's status line reads the loop's state from this same answer,
    // so it is told when a new one lands rather than polling for it.
    document.dispatchEvent(new CustomEvent('health:updated'));
}

function fetchHealth() {
    apiGet('/api/health', { key: 'health' }).then(data => {
        if (data && !data.error) renderHealth(data);
    });
}

export { fetchHealth, latestHealth, sensorsBadge, SENSOR_ROLES, DRIFT_FLAG_LABELS };
