/**
 * Tests for the pure helpers in static/js/health.js and static/js/nodes.js.
 *
 * Lo que se prueba acá es el defecto que originó los dos módulos: la pantalla
 * decía "✅ OK" al lado de un "--", y un cartel rojo de "sensor posiblemente
 * desconectado" arriba de dos tarjetas verdes. El badge no puede volver a
 * afirmar que algo anda cuando lo que está mostrando está vacío.
 */

import assert from 'node:assert/strict';
import { test } from 'node:test';

import { heroBadge, sensorsBadge } from '../../autocann/web/static/js/health.js';
import { readRate, signalLabel } from '../../autocann/web/static/js/nodes.js';

const posting = { controls: true, state: 'posting' };

test('the hero badge does not say OK while the card it sits on has nothing to show', () => {
    // Con el lazo en pausa no hay VPD que mostrar: el badge decía "OK" en verde
    // arriba de un "--", que es exactamente lo que no se entendía.
    const paused = heroBadge({ level: 'warn', loop: { state: 'paused' }, sensors: [posting] });
    assert.equal(paused.text, 'en pausa');
    assert.equal(paused.icon, 'circle-pause');
    assert.equal(paused.state, 'stale');

    const down = heroBadge({ level: 'error', loop: { state: 'down' }, sensors: [posting] });
    assert.equal(down.text, 'lazo caído');
    assert.equal(down.icon, 'circle-x');
});

test('the hero badge reports the sensor that controls, not the installation average', () => {
    const health = {
        level: 'error',
        loop: { state: 'running' },
        sensors: [{ controls: true, state: 'stale' }, { controls: false, state: 'posting' }],
    };
    assert.equal(heroBadge(health).state, 'error');
});

test('the hero badge is green only when everything it depends on is', () => {
    const health = { level: 'ok', loop: { state: 'running' }, sensors: [posting] };
    assert.deepEqual(heroBadge(health), { icon: 'circle-check', text: 'OK', state: 'ok' });
});

test('the hero badge stays green when what is broken is not its business', () => {
    // El histórico puede estar sin guardarse con el VPD perfectamente bien
    // medido y mostrado. Pintar el badge de rojo por eso es el mismo defecto
    // de antes dado vuelta: un badge que no habla de lo que tiene al lado.
    const health = {
        level: 'error',
        loop: { state: 'running', level: 'ok' },
        history: { state: 'stalled', level: 'error' },
        sensors: [posting],
    };
    assert.equal(heroBadge(health).state, 'ok');
});

test('the hero badge waits rather than guessing before the first answer', () => {
    assert.equal(heroBadge(null).icon, 'hourglass');
    assert.equal(heroBadge(undefined).state, '');
});

test('the section badge counts, so a sensor nobody configured still shows up', () => {
    // Un tilde verde sigue diciendo "todo bien" cuando alguien suma un tercer
    // sensor y se olvida de configurarlo; "2 de 3" no.
    assert.equal(
        sensorsBadge([posting, posting, { controls: false, state: 'missing' }]).text,
        '2 de 3 posteando',
    );
    assert.equal(sensorsBadge([posting, posting]).text, '2 de 2 posteando');
    assert.equal(sensorsBadge([posting, posting]).icon, 'circle-check');
    assert.equal(sensorsBadge([{ state: 'missing' }]).state, 'error');
    assert.equal(sensorsBadge([]).text, 'sin sensores');
});

test('a sensor the drift monitor flagged still counts as posting', () => {
    // Postea, contesta y está equivocado: es un problema del valor, no del
    // enlace, y contarlo como caído mandaría a revisar el cable equivocado.
    assert.equal(sensorsBadge([{ state: 'suspect' }]).text, '1 de 1 posteando');
});

test('the WiFi signal is reported in words, not only in dBm', () => {
    assert.match(signalLabel(-45), /buena/);
    assert.match(signalLabel(-65), /aceptable/);
    assert.match(signalLabel(-88), /mala/);
    assert.equal(signalLabel(null), 'sin dato');
});

test('a DHT22 that fails one read in a hundred is not reported like one that fails a third', () => {
    // Una falla suelta es normal en estos sensores; una racha es un cable flojo.
    assert.equal(readRate({ reads_ok: 99, reads_failed: 1 }).bad, false);
    assert.equal(readRate({ reads_ok: 60, reads_failed: 40 }).bad, true);
    assert.equal(readRate({ reads_ok: 99, reads_failed: 1, consecutive_failed: 5 }).bad, true);
    assert.equal(readRate({}).text, 'sin lecturas');
});
