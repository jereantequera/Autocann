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
 *
 * Este módulo también decide qué cartel de arriba se muestra: el de "roto" y el
 * de anomalías son uno solo, para que no haya dos cajas rojas pegadas diciendo
 * lo mismo.
 */

import { apiGet, connection } from './api.js';
import {
    escapeHtml, fmtNum, fmtSigned, formatAge, icon, iconText, stampLastUpdate,
} from './util.js';

// ===============================
// Vocabulario
// ===============================
// The tent has two DHT22 on one node: a primary that drives control and a
// witness that drives nothing. The witness exists to catch the primary
// reporting a plausible wrong number, so it is only worth having if somebody
// can see it - which is what this section is.

const SENSOR_ROLES = {
    primary: { icon: 'crosshair', label: 'Primario, controla' },
    witness: { icon: 'eye', label: 'Testigo, sólo vigila' },
    // Shown and nothing else. The sensor above the lamp is worth reading and
    // useless as a witness: its offset against the primary moves with the
    // photoperiod, not with the sensors' health.
    observer: { icon: 'chart-line', label: 'Observación, sólo informa' },
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
    posting: { dot: 'ac-dot--ok', badge: 'ok' },
    suspect: { dot: 'ac-dot--warn', badge: 'stale' },
    stale: { dot: 'ac-dot--alert', badge: 'error' },
    missing: { dot: 'ac-dot--alert', badge: 'error' },
};

/** El último estado conocido, para que otros módulos no vuelvan a pedirlo. */
let snapshot = null;
/** Anomalías de la última consulta de analytics.js, y si el usuario las descartó. */
let anomalyItems = [];
let anomaliesDismissed = false;

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

/**
 * Los cuatro chequeos de la sección Estado, cada uno con su nivel.
 *
 * Backend viene del propio cliente HTTP, no de /api/health: si el backend no
 * contesta, el veredicto tampoco puede llegar, y el chequeo tiene que decirlo.
 */
function healthChecks(health, { offline = false } = {}) {
    const sensors = health?.sensors || [];
    const sensorsSummary = sensorsBadge(sensors);
    return [
        {
            name: 'Backend',
            level: offline ? 'error' : 'ok',
            detail: offline ? 'sin respuesta' : 'responde',
        },
        {
            name: 'Lazo de control',
            level: health?.loop?.level || 'idle',
            detail: health?.loop?.label || 'sin datos',
        },
        {
            name: 'Sensores',
            level: sensorsSummary.state === 'ok' ? 'ok' : sensorsSummary.state === 'stale' ? 'warn'
                : sensorsSummary.state === 'error' ? 'error' : 'idle',
            detail: sensorsSummary.text,
        },
        {
            name: 'Histórico',
            level: health?.history?.level || 'idle',
            detail: health?.history?.label || 'sin datos',
        },
    ];
}

/**
 * La frase grande de arriba de Sistema.
 *
 * "Todo anda" sólo cuando los cuatro chequeos están en ok. Si no, lo que dice
 * el backend sobre lo peor que está pasando: nombrarlo, no contar problemas.
 */
function verdictFor(health, checks) {
    if (!health) return 'Consultando el estado';
    if (health.error) return 'No se pudo calcular el estado';
    if (checks.every(check => check.level === 'ok')) return 'Todo anda';
    return health.headline || 'Hay algo para revisar';
}

/**
 * Si el cartel de "roto" tiene que estar arriba de todo.
 *
 * Sólo lo que está roto ahora y es del lazo o del sensor que controla. Una
 * pausa es intencional y la anuncia el header; un hueco en el histórico lo
 * cuentan las anomalías. Un cartel que aparece por algo que alguien hizo a
 * propósito es el que enseña a ignorar los carteles.
 */
function isBroken(health) {
    if (!health || health.error) return false;
    if (health.loop?.state === 'down' || health.loop?.level === 'error') return true;
    return (health.sensors || []).some(
        sensor => sensor.role === 'primary' && (sensor.state === 'missing' || sensor.state === 'stale'));
}

/**
 * Qué cartel de arriba se muestra, dados el veredicto y las anomalías.
 *
 * - Roto: sólo el cartel de roto. Las anomalías no tienen caja propia: se
 *   suman como una línea "+N advertencias", para que el que lee vea que hay
 *   más sin tener dos carteles pegados.
 * - Sano con anomalías: el cartel de advertencia, más calmo, con hasta tres.
 * - Nada: ningún cartel.
 *
 * Pura, así que lo que se ve queda probado sin el DOM.
 */
function alertDecision({ health, anomalies = [], dismissed = false }) {
    const issues = dismissed ? [] : anomalies;
    if (isBroken(health)) {
        return {
            mode: 'broken',
            title: health.headline || 'Problema con sensores',
            text: health.detail || '',
            more: issues.length,
        };
    }
    if (issues.length) {
        return {
            mode: 'warnings',
            total: issues.length,
            critical: issues.some(issue => issue.severity === 'critical'),
            items: issues.slice(0, 3),
            hidden: Math.max(0, issues.length - 3),
        };
    }
    return { mode: 'none' };
}

/**
 * The "+N" line of the broken-state alert: singular for one, plural for more,
 * empty for none so the element stays hidden.
 */
function moreWarningsLabel(n) {
    if (!n) return '';
    return n === 1 ? '+1 advertencia' : `+${n} advertencias`;
}

// ===============================
// Tabla de sensores
// ===============================

/**
 * Celdas de lectura de una fila de sensor, sin las de calibración.
 *
 * Compartidas con system.js, que agrega las celdas de corrección a las mismas
 * filas: así la tabla tiene una sola forma aunque la escriban dos módulos.
 */
const SENSOR_READ_CELLS = `
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
const CALIB_CELLS = `
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

function setBadge(id, badge) {
    const el = document.getElementById(id);
    if (!el) return;
    el.className = `sensor-badge ${badge.state}`.trim();
    el.innerHTML = iconText(badge);
}

function renderSensors(health) {
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

function renderChecks(health) {
    const row = document.getElementById('health-checks');
    const verdict = document.getElementById('health-verdict');
    const age = document.getElementById('health-age');
    const checks = healthChecks(health, { offline: connection.offline });

    if (row) {
        row.innerHTML = checks.map(check => `
            <div class="health-check is-${check.level}">
                <div class="health-check-name">
                    <span class="ac-dot ${check.level === 'ok' ? 'ac-dot--ok' : check.level === 'warn' ? 'ac-dot--warn'
                        : check.level === 'error' ? 'ac-dot--alert' : 'ac-dot--idle'}"></span>
                    ${escapeHtml(check.name)}
                </div>
                <div class="health-check-detail">${escapeHtml(check.detail)}</div>
            </div>`).join('');
    }
    if (verdict) verdict.textContent = verdictFor(health, checks);
    if (age) {
        age.textContent = health?.generated_at
            ? `Revisado ${formatAge(Math.max(0, Date.now() / 1000 - health.generated_at))}`
            : '';
    }
}

/**
 * Los carteles de arriba de todo, decididos en un solo lugar.
 *
 * El de conexión lo maneja api.js; éste es el de sensores y el de anomalías,
 * y los dos salen de alertDecision para que nunca se muestren juntos diciendo
 * lo mismo.
 */
function renderAlerts() {
    const decision = alertDecision({
        health: snapshot,
        anomalies: anomalyItems,
        dismissed: anomaliesDismissed,
    });

    const broken = document.getElementById('sensor-alert');
    const brokenMessage = document.getElementById('sensor-alert-message');
    const brokenTitle = document.getElementById('sensor-alert-title');
    const brokenMore = document.getElementById('sensor-alert-more');
    if (broken) {
        const show = decision.mode === 'broken';
        broken.classList.toggle('show', show);
        if (show) {
            if (brokenTitle) brokenTitle.textContent = decision.title;
            if (brokenMessage) brokenMessage.textContent = decision.text;
            if (brokenMore) {
                brokenMore.hidden = !decision.more;
                brokenMore.textContent = moreWarningsLabel(decision.more);
            }
        }
    }

    const warnings = document.getElementById('anomalies-alert');
    const list = document.getElementById('anomalies-list');
    const title = document.getElementById('anomalies-title-text');
    const iconEl = document.getElementById('anomalies-icon');
    const showWarnings = decision.mode === 'warnings';
    if (warnings) warnings.classList.toggle('show', showWarnings);
    if (showWarnings) {
        if (title) {
            title.textContent = decision.critical
                ? 'Problemas críticos detectados' : 'Advertencias detectadas';
        }
        if (iconEl) iconEl.innerHTML = icon(decision.critical ? 'siren' : 'triangle-alert');
        if (list) {
            list.innerHTML = decision.items.map(issue => `
                <div class="anomaly-item ${escapeHtml(issue.severity || 'warning')}">
                    ${icon(issue.severity === 'critical' ? 'circle-alert' : 'triangle-alert', 'anomaly-icon')}
                    <span class="anomaly-text">${escapeHtml(issue.message)}</span>
                    <span class="anomaly-time">${escapeHtml(issue.timestamp ? String(issue.timestamp).split(' ')[1] || '' : '')}</span>
                </div>`).join('')
                + (decision.hidden ? `<div class="anomaly-more">+ ${decision.hidden} más</div>` : '');
        }
    }
}

/**
 * Lo que analytics.js trae de /api/anomalies, para decidir junto con la salud.
 *
 * `dismissed` llega de analytics.js, que es quien sabe si el usuario lo
 * descartó y hasta cuándo.
 */
function setAnomalies(items, { dismissed = false } = {}) {
    anomalyItems = Array.isArray(items) ? items : [];
    anomaliesDismissed = !!dismissed;
    renderAlerts();
}

function renderHealth(health) {
    snapshot = health;

    renderSensors(health);
    renderChecks(health);

    // The section badge over the cards in Sistema, the only place sensors are
    // shown now that the panel reads the loop and the sensors from its status.
    setBadge('sensors-badge', sensorsBadge(health.sensors));

    const outdoor = health.outdoor || {};
    setOutdoorRow(outdoor);
    setBadge('sensor-status-outdoor',
        outdoor.ok === true ? { icon: 'circle-check', text: 'OK', state: 'ok' }
            : outdoor.ok === false ? { icon: 'circle-x', text: 'Error', state: 'error' }
                : { icon: 'hourglass', text: '', state: '' });

    renderAlerts();

    // The panel's status line reads the loop's state from this same answer,
    // so it is told when a new one lands rather than polling for it.
    document.dispatchEvent(new CustomEvent('health:updated'));
}

/**
 * El exterior no tiene lectura en /api/health, sólo si su DHT responde: la
 * fila dice eso y nada más, para no inventar un número que no llegó.
 */
function setOutdoorRow(outdoor) {
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

function fetchHealth() {
    apiGet('/api/health', { key: 'health' }).then(data => {
        if (!data || data.error) return;
        renderHealth(data);
        // This poller runs on every view, so it is the one that keeps the
        // sidebar's "última actualización" honest where current data does not run.
        stampLastUpdate();
    });
}

// The header of Sistema says whether the backend answers; the checks have to
// change with it, not on the next health poll ten seconds later.
// Guarded because the unit tests import this module into Node, which has no
// document; in the page there always is one.
if (typeof document !== 'undefined') {
    document.addEventListener('connection:changed', () => {
        renderChecks(snapshot);
        renderAlerts();
    });
}

export {
    alertDecision, fetchHealth, healthChecks, isBroken, latestHealth, moreWarningsLabel, renderAlerts,
    sensorsBadge, setAnomalies, verdictFor, SENSOR_READ_CELLS, CALIB_CELLS,
    SENSOR_ROLES, DRIFT_FLAG_LABELS,
};
