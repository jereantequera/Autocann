/**
 * La decisión de qué cartel de arriba se muestra: el de "roto" con las
 * anomalías como una línea, o el de advertencia solo, o ninguno.
 *
 * Lo que se prueba es el defecto que originó la ranura única: dos cajas rojas
 * pegadas, una de sensores y otra de anomalías, diciendo lo mismo dos veces.
 */

import assert from 'node:assert/strict';
import { test } from 'node:test';

import { alertDecision, healthChecks, isBroken, verdictFor } from '../../autocann/web/static/js/health.js';

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
    assert.deepEqual(alertDecision({ health: healthy, anomalies: [] }), { mode: 'none' });
    assert.deepEqual(alertDecision({ health: null, anomalies: [] }), { mode: 'none' });
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
