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

// `monitor` is its own icon, not the pause's: the two are different answers to
// "¿por qué no está controlando?" and the strip is the one line on the panel
// that says which.
const LOOP_ICONS = { running: 'circle-check', monitor: 'scan-eye', paused: 'circle-pause', down: 'circle-x' };
const HISTORY_ICONS = { writing: 'circle-check', paused: 'circle-pause', stalled: 'circle-x', empty: 'hourglass' };

/** El último estado conocido, para que otros módulos no vuelvan a pedirlo. */
let snapshot = null;

const latestHealth = () => snapshot;

// ===============================
// Helpers puros
// ===============================

/**
 * El badge del hero, que está sobre la tarjeta de VPD.
 *
 * Reporta lo que esa tarjeta puede mostrar, no sólo si el sensor contesta. Con
 * el lazo en pausa no hay VPD que mostrar, y un "OK" en verde al lado de un "--" es
 * exactamente la contradicción que hizo falta arreglar.
 *
 * @returns {{icon: string, text: string, state: string}}
 */
function heroBadge(health) {
    if (!health) return { icon: 'hourglass', text: '', state: '' };

    const loop = health.loop || {};
    if (loop.state === 'down') return { icon: 'circle-x', text: 'lazo caído', state: 'error' };
    if (loop.state === 'paused') return { icon: 'circle-pause', text: 'en pausa', state: 'stale' };
    // Monitoring says the same thing this badge is for - there is no VPD on
    // this card because nothing is aiming at one - and says which of the two
    // reasons it is, so the card is not read as broken.
    if (loop.state === 'monitor') return { icon: 'scan-eye', text: 'monitoreo', state: 'stale' };

    // Sólo el lazo y el sensor que controla: son los dos de los que depende el
    // número que esta tarjeta muestra. Un hueco en el histórico es un problema
    // real y no es de esta tarjeta, y pintarla de rojo por eso es volver a lo
    // de antes al revés — un badge que no se corresponde con lo que tiene al lado.
    const primary = (health.sensors || []).find(sensor => sensor.controls);
    if (!primary) return { icon: 'triangle-alert', text: 'sin primario', state: 'stale' };
    if (primary.state === 'missing') return { icon: 'circle-x', text: 'sin sensor', state: 'error' };
    if (primary.state === 'stale') return { icon: 'circle-x', text: 'sin datos', state: 'error' };
    if (primary.state === 'suspect') return { icon: 'triangle-alert', text: 'revisar', state: 'stale' };
    return { icon: 'circle-check', text: 'OK', state: 'ok' };
}

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

function renderRow(prefix, verdict, icons) {
    const row = document.getElementById(`health-${prefix}`);
    const state = document.getElementById(`health-${prefix}-state`);
    const detail = document.getElementById(`health-${prefix}-detail`);
    if (!row || !verdict) return;

    row.className = `health-row is-${verdict.level}`;
    state.innerHTML = iconText({ icon: icons[verdict.state] || 'hourglass', text: verdict.label });
    detail.textContent = verdict.detail || '';
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
    renderRow('loop', health.loop, LOOP_ICONS);
    renderRow('history', health.history, HISTORY_ICONS);

    // Two places, one computation: the section badge over the cards in Sistema
    // and the Sensores row of the panel's Estado strip. The cards moved off the
    // panel, so that row is all it says about them - it must not be able to say
    // something the cards contradict.
    const sensors = sensorsBadge(health.sensors);
    setBadge('sensors-badge', sensors);
    const strip = document.getElementById('health-sensors-state');
    if (strip) strip.innerHTML = iconText(sensors);

    setBadge('sensor-status-indoor', heroBadge(health));

    const outdoor = health.outdoor || {};
    setBadge('sensor-status-outdoor',
        outdoor.ok === true ? { icon: 'circle-check', text: 'OK', state: 'ok' }
            : outdoor.ok === false ? { icon: 'circle-x', text: 'Error', state: 'error' }
                : { icon: 'hourglass', text: '', state: '' });

    renderAlert(health);
}

function fetchHealth() {
    apiGet('/api/health', { key: 'health' }).then(data => {
        if (data && !data.error) renderHealth(data);
    });
}

export { fetchHealth, latestHealth, heroBadge, sensorsBadge, SENSOR_ROLES, DRIFT_FLAG_LABELS };
