/**
 * La decisión de qué cartel de arriba se muestra: el de "roto" con las
 * anomalías como una línea, o el de advertencia solo, o ninguno.
 *
 * Lo que se prueba es el defecto que originó la ranura única: dos cajas rojas
 * pegadas, una de sensores y otra de anomalías, diciendo lo mismo dos veces.
 */

import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
    alertDecision, anomalyIcon, isBroken, noGrowNotice, ownedByBanner, worstSeverity,
} from '../../autocann/web/static/js/alerts.js';
import { healthChecks, verdictFor } from '../../autocann/web/static/js/health.js';

const posting = { role: 'primary', state: 'posting', level: 'ok', label: 'A' };
const healthy = {
    level: 'ok',
    headline: 'Todo en orden',
    detail: 'Última decisión hace 3 s.',
    loop: { state: 'running', level: 'ok', label: 'Controlando' },
    history: { state: 'writing', level: 'ok', label: 'Guardando' },
    sensors: [posting],
};
const anomaly = (severity, message) => ({ severity, message, timestamp: '2026-10-15 12:00:00' });

test('a loop that is down is broken, and so is a controlling sensor that went quiet', () => {
    assert.equal(isBroken({ ...healthy, loop: { state: 'down', level: 'error' } }), true);
    const missing = { ...posting, state: 'missing', role: 'primary' };
    assert.equal(isBroken({ ...healthy, sensors: [missing] }), true);
    const stale = { ...posting, state: 'stale' };
    assert.equal(isBroken({ ...healthy, sensors: [stale] }), true);
});

test('a witness going quiet, or a paused tent, is not broken', () => {
    // El testigo no controla: que no postee es un problema para mirar, no un
    // cartel de arriba.
    const witnessDown = { role: 'witness', state: 'missing' };
    assert.equal(isBroken({ ...healthy, sensors: [posting, witnessDown] }), false);
    // Una pausa es intencional y la anuncia el header.
    assert.equal(isBroken({ ...healthy, loop: { state: 'paused', level: 'warn' } }), false);
    assert.equal(isBroken(undefined), false);
});

test('when broken, the anomalies fold into the one alert as a count, not a second box', () => {
    const broken = { ...healthy, loop: { state: 'down', level: 'error' }, headline: 'El lazo de control no está corriendo', detail: 'Hace 9 min.' };
    const decision = alertDecision({
        health: broken,
        anomalies: [anomaly('critical', 'a'), anomaly('warning', 'b'), anomaly('warning', 'c'), anomaly('warning', 'd')],
    });
    assert.equal(decision.mode, 'broken');
    assert.equal(decision.title, 'El lazo de control no está corriendo');
    assert.equal(decision.text, 'Hace 9 min.');
    assert.equal(decision.more, 4);
    // No second box: the warnings list is not part of the decision at all.
    assert.equal('items' in decision, false);
});

test('when fine, the anomalies get their own quieter box, at most three of them', () => {
    const decision = alertDecision({
        health: healthy,
        anomalies: [anomaly('critical', 'a'), anomaly('warning', 'b'), anomaly('warning', 'c'), anomaly('warning', 'd')],
    });
    assert.equal(decision.mode, 'warnings');
    assert.equal(decision.total, 4);
    assert.equal(decision.critical, true);
    assert.equal(decision.items.length, 3);
    assert.equal(decision.hidden, 1);
});

test('a dismissed list shows nothing when fine, and only the count when broken', () => {
    const anomalies = [anomaly('warning', 'a')];
    assert.equal(alertDecision({ health: healthy, anomalies, dismissed: true }).mode, 'none');
    const broken = { ...healthy, loop: { state: 'down', level: 'error' } };
    const decision = alertDecision({ health: broken, anomalies, dismissed: true });
    assert.equal(decision.mode, 'broken');
    assert.equal(decision.more, 0);
});

test('nothing wrong and nothing found is no alert at all', () => {
    assert.deepEqual(alertDecision({ health: healthy, anomalies: [] }), { mode: 'none', notice: null });
    assert.deepEqual(alertDecision({ health: null, anomalies: [] }), { mode: 'none', notice: null });
});

test('a critical item makes the box red and says so', () => {
    const decision = alertDecision({
        health: healthy, anomalies: [anomaly('warning', 'a'), anomaly('critical', 'b')],
    });
    assert.equal(decision.severity, 'critical');
    assert.equal(decision.title, 'Problemas críticos detectados');
    assert.equal(decision.tone, '');
    assert.equal(decision.icon, 'siren');
});

test('warnings alone are amber and never called critical', () => {
    // El defecto: una advertencia releída por el backend seguía saliendo como
    // "Problemas críticos detectados".
    const decision = alertDecision({ health: healthy, anomalies: [anomaly('warning', 'a')] });
    assert.equal(decision.severity, 'warning');
    assert.equal(decision.critical, false);
    assert.equal(decision.title, 'Advertencias detectadas');
    assert.equal(decision.tone, 'warn');
});

test('information alone is neutral', () => {
    const decision = alertDecision({ health: healthy, anomalies: [anomaly('info', 'a')] });
    assert.equal(decision.severity, 'info');
    assert.equal(decision.tone, 'info');
    assert.notEqual(decision.title, 'Advertencias detectadas');
});

test('an item without a known severity counts as a warning, not as nothing', () => {
    assert.equal(worstSeverity([{ message: 'x' }]), 'warning');
    assert.equal(worstSeverity([anomaly('info', 'a'), anomaly('bogus', 'b')]), 'warning');
    assert.equal(worstSeverity([anomaly('info', 'a')]), 'info');
});

const noGrow = {
    ...healthy,
    no_active_grow: true,
    headline: 'Sin cultivo activo',
    history: { state: 'no_grow', level: 'warn', label: 'Sin cultivo activo' },
};

test('no active grow is a notice, not an alert, and nothing in it is critical', () => {
    const decision = alertDecision({ health: noGrow, anomalies: [] });
    assert.equal(decision.mode, 'none');
    assert.equal(decision.notice.kind, 'no_grow');
    assert.match(decision.notice.text, /^No hay cultivo activo: no se guardan datos\./);
    assert.match(decision.notice.text, /Creá un cultivo/);
});

test('the no-grow notice is shown paused and monitoring too, worded for monitoring', () => {
    const paused = { ...noGrow, loop: { state: 'paused', level: 'warn' } };
    assert.equal(noGrowNotice(paused).kind, 'no_grow');
    const monitoring = { ...noGrow, loop: { state: 'monitor', level: 'ok' } };
    assert.match(noGrowNotice(monitoring).text, /el monitoreo no guarda datos/);
});

test('dismissing the anomalies does not hide the no-grow notice', () => {
    const decision = alertDecision({ health: noGrow, anomalies: [anomaly('warning', 'a')], dismissed: true });
    assert.equal(decision.mode, 'none');
    assert.equal(decision.notice.kind, 'no_grow');
});

test('the notice goes as soon as health reports a grow', () => {
    assert.equal(alertDecision({ health: { ...noGrow, no_active_grow: false } }).notice, null);
    assert.equal(noGrowNotice({ ...noGrow, error: 'x' }), null);
    assert.equal(noGrowNotice(null), null);
});

test('the verdict is "Todo anda" only when every check is ok, else the worst headline', () => {
    const allOk = healthChecks(healthy, { offline: false });
    assert.equal(verdictFor(healthy, allOk), 'Todo anda');

    const paused = { ...healthy, headline: 'Sistema en pausa', loop: { state: 'paused', level: 'warn', label: 'En pausa' } };
    const checks = healthChecks(paused, { offline: false });
    assert.equal(verdictFor(paused, checks), 'Sistema en pausa');
});

test('a backend that does not answer is a failed check, whatever health last said', () => {
    const checks = healthChecks(healthy, { offline: true });
    assert.equal(checks[0].name, 'Backend');
    assert.equal(checks[0].level, 'error');
    assert.notEqual(verdictFor(healthy, checks), 'Todo anda');
});

test('before the first answer the page says it is checking, not that all is well', () => {
    assert.equal(verdictFor(null, healthChecks(null, { offline: false })), 'Consultando el estado');
});

// ---------------------------------------------------------------------------
// Un dueño por hecho (#65)
// ---------------------------------------------------------------------------

test('a history that stopped saving with the loop running is not the red banner, it stays in the box', () => {
    // Nada queda sin controlar: lo dice la caja de anomalías, con qué revisar.
    const stalled = {
        ...healthy,
        level: 'error',
        headline: 'No se está guardando el histórico',
        history: { state: 'stalled', level: 'error', label: 'No se está guardando' },
    };
    assert.equal(isBroken(stalled), false);
    const stale = { type: 'stale_data', severity: 'warning', message: 'Último dato guardado hace 40 min, pero…' };
    const decision = alertDecision({ health: stalled, anomalies: [stale] });
    assert.equal(decision.mode, 'warnings');
    assert.equal(decision.items[0].type, 'stale_data');
});

test('a dead primary is one alert: the banner owns "nothing is being stored", even in the count', () => {
    const dead = {
        ...healthy,
        headline: 'Sensor A (el que controla) no está posteando',
        sensors: [{ ...posting, state: 'stale' }],
    };
    const stale = { type: 'stale_data', severity: 'critical', message: 'Último dato guardado hace 40 min.' };
    const none = { type: 'no_data', severity: 'critical', message: 'No hay datos guardados.' };
    const decision = alertDecision({ health: dead, anomalies: [stale, none] });
    assert.equal(decision.mode, 'broken');
    assert.equal(decision.more, 0);
});

test('when broken, the history items it does not own still count as "+N advertencias"', () => {
    const dead = { ...healthy, sensors: [{ ...posting, state: 'missing' }] };
    const spike = { type: 'temperature_spike', severity: 'warning', message: 'Cambio brusco' };
    const gap = { type: 'data_gap', severity: 'warning', message: 'Hueco' };
    const decision = alertDecision({ health: dead, anomalies: [spike, gap] });
    assert.equal(decision.more, 2);
});

test('without a banner, the box keeps what health could not explain', () => {
    const stale = { type: 'stale_data', severity: 'warning', message: 'Último dato guardado hace 20 min' };
    const decision = alertDecision({ health: healthy, anomalies: [stale] });
    assert.equal(decision.mode, 'warnings');
    assert.equal(decision.items.length, 1);
});

test('ownedByBanner names only the facts the banner says', () => {
    assert.equal(ownedByBanner({ type: 'no_data' }), true);
    assert.equal(ownedByBanner({ type: 'stale_data' }), true);
    assert.equal(ownedByBanner({ type: 'data_gap' }), false);
    assert.equal(ownedByBanner({ type: 'temperature_spike' }), false);
    assert.equal(ownedByBanner(null), false);
});

test('an info notice gets the calm circle, not the warning triangle', () => {
    assert.equal(anomalyIcon('info'), 'circle-alert');
    assert.equal(anomalyIcon('critical'), 'circle-alert');
    assert.equal(anomalyIcon('warning'), 'triangle-alert');
    assert.equal(anomalyIcon(undefined), 'triangle-alert');
});

test('a lone awaiting-first-sample notice is an info box, not a warning one', () => {
    const issue = { type: 'awaiting_first_sample', severity: 'info', message: 'Todavía no se guardó nada.' };
    const decision = alertDecision({ health: healthy, anomalies: [issue] });
    assert.equal(decision.mode, 'warnings');
    assert.equal(decision.severity, 'info');
    assert.equal(decision.tone, 'info');
    assert.equal(decision.critical, false);
});
