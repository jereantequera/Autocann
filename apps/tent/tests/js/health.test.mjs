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

import { moreWarningsLabel, sensorsBadge } from '../../autocann/web/static/js/health.js';
import { readRate, signalLabel } from '../../autocann/web/static/js/nodes.js';

const posting = { controls: true, state: 'posting' };

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

test('the folded-in warning count agrees with its noun', () => {
    // Issue #31: "+1 advertencias" — one takes the singular.
    assert.equal(moreWarningsLabel(0), '');
    assert.equal(moreWarningsLabel(1), '+1 advertencia');
    assert.equal(moreWarningsLabel(2), '+2 advertencias');
});

test('the node counters agree with their nouns at one', () => {
    assert.equal(readRate({ reads_ok: 9, reads_failed: 1 }).text, '9 ok · 1 fallida (10.0 %)');
    assert.equal(readRate({ reads_ok: 9, reads_failed: 2 }).text, '9 ok · 2 fallidas (18.2 %)');
});
