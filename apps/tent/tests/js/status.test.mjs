/**
 * Tests for the status line of the panel and the relay rows (static/js/status.js).
 *
 * The verdict is the first thing read from the doorway, so each branch is
 * pinned here: a wrong sentence on that line is a wrong decision about the
 * tent, not a cosmetic bug.
 */

import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
    outputRowState, outputShortName, panelStatus,
} from '../../autocann/web/static/js/status.js';

const NOW = 1_000_000_000_000; // epoch ms
const STAGES = { early_veg: 'Vegetativo Temprano' };
const HEALTHY = { loop: { state: 'running' }, sensors: [] };
const RUNNING = { active: false, mode: 'active' };

/** A reading in the band: 25.8 °C, 68 % RH, band 61–74. */
const inRange = {
    temperature: 25.8,
    humidity: 68,
    stage: 'early_veg',
    humidity_band: [61, 74],
    vpd_in_range: true,
};

/** Dry: 55 % against the same band. */
const dry = { ...inRange, humidity: 55, vpd_in_range: false };

/** Humid: 80 % against the same band. */
const humid = { ...inRange, humidity: 80, vpd_in_range: false };

test('no reading, or the loop is down, is idle and points at Sistema', () => {
    for (const [current, health] of [
        [null, HEALTHY],
        [{ error: 'No current data available' }, HEALTHY],
        [inRange, { loop: { state: 'down' } }],
    ]) {
        const status = panelStatus(current, health, RUNNING, [], STAGES, NOW);
        assert.equal(status.level, 'idle');
        assert.equal(status.title, 'Sin lecturas del lazo');
        assert.match(status.text, /Sistema/);
    }
});

test('a reading on screen does not beat the loop being down', () => {
    const status = panelStatus(inRange, { loop: { state: 'down' }, sensors: [] }, RUNNING, [], STAGES, NOW);
    assert.equal(status.level, 'idle');
    assert.equal(status.title, 'Sin lecturas del lazo');
});

test('a reading on screen does not beat the controlling sensor being silent', () => {
    for (const state of ['missing', 'stale']) {
        const health = { loop: { state: 'running' }, sensors: [{ controls: true, state }] };
        const status = panelStatus(inRange, health, RUNNING, [], STAGES, NOW);
        assert.equal(status.level, 'idle', state);
        assert.match(status.text, /sensor que controla/, state);
    }
});

test('a witness that is silent does not change the verdict', () => {
    const health = { loop: { state: 'running' }, sensors: [
        { controls: true, state: 'posting' }, { controls: false, state: 'stale' }] };
    assert.equal(panelStatus(inRange, health, RUNNING, [], STAGES, NOW).level, 'ok');
});

test('paused and monitoring are idle, and say what the relays are doing', () => {
    const paused = panelStatus(inRange, HEALTHY, { active: true, mode: 'paused' }, [], STAGES, NOW);
    assert.equal(paused.level, 'idle');
    assert.equal(paused.title, 'En pausa');

    const monitor = panelStatus(inRange, HEALTHY, { active: true, mode: 'monitor' }, [], STAGES, NOW);
    assert.equal(monitor.level, 'idle');
    assert.equal(monitor.title, 'Monitoreando');
});

test('inside the band is ok, and the sentence names the stage', () => {
    const status = panelStatus(inRange, HEALTHY, RUNNING, [], STAGES, NOW);
    assert.equal(status.level, 'ok');
    assert.equal(status.title, 'En rango');
    assert.equal(status.text, 'Temperatura y humedad dentro de lo que pide Vegetativo Temprano.');
});

test('dry with the humidifier running says so, and when it started', () => {
    const outputs = [{ name: 'humidity_up', state: true, since: NOW / 1000 - 240 }];
    const status = panelStatus(dry, HEALTHY, RUNNING, outputs, STAGES, NOW);
    assert.equal(status.level, 'warn');
    assert.equal(status.title, 'Un poco seco · humidificando');
    assert.equal(
        status.text,
        'El humidificador arrancó hace 4 min. A 25,8 °C la humedad tiene que estar entre 61 y 74 % para esta etapa.',
    );
});

test('dry with the humidifier off drops the relay sentence', () => {
    const status = panelStatus(dry, HEALTHY, RUNNING, [{ name: 'humidity_up', state: false }], STAGES, NOW);
    assert.equal(status.title, 'Un poco seco');
    assert.equal(
        status.text,
        'A 25,8 °C la humedad tiene que estar entre 61 y 74 % para esta etapa.',
    );
});

test('humid with the dehumidifier on, without a start time, says it is on', () => {
    const outputs = [{ name: 'humidity_down', state: true }];
    const status = panelStatus(humid, HEALTHY, RUNNING, outputs, STAGES, NOW);
    assert.equal(status.title, 'Un poco húmedo · deshumidificando');
    assert.equal(
        status.text,
        'El deshumidificador está prendido. A 25,8 °C la humedad tiene que estar entre 61 y 74 % para esta etapa.',
    );
});

test('out of range without a band says so instead of guessing a direction', () => {
    const status = panelStatus({ ...inRange, vpd_in_range: false, humidity_band: undefined },
        HEALTHY, RUNNING, [], STAGES, NOW);
    assert.equal(status.level, 'warn');
    assert.equal(status.title, 'Fuera de rango');
});

test('a reading the loop has not judged is idle, not ok', () => {
    const status = panelStatus({ ...inRange, vpd_in_range: null }, HEALTHY, RUNNING, [], STAGES, NOW);
    assert.equal(status.level, 'idle');
});

test('a relay row says what the relay is doing in plain words', () => {
    assert.deepEqual(
        outputRowState({ name: 'humidity_up', state: true, since: NOW / 1000 - 600 }, NOW),
        { state: 'on', text: 'prendido · 10 min' },
    );
    assert.deepEqual(outputRowState({ name: 'humidity_up', state: true }, NOW),
        { state: 'on', text: 'prendido' });
    assert.deepEqual(outputRowState({ name: 'humidity_down', state: false, held_seconds: 90 }, NOW),
        { state: 'off', text: 'apagado · espera 2 min' });
    assert.deepEqual(outputRowState({ name: 'ventilation', state: false }, NOW),
        { state: 'off', text: 'sólo manual' });
    assert.deepEqual(outputRowState({ name: 'humidity_down', state: false }, NOW),
        { state: 'off', text: 'apagado' });
    assert.deepEqual(outputRowState({ name: 'humidity_down', state: null }, NOW),
        { state: 'unknown', text: 'sin estado' });
});

test('the relays are named for the grower, not by their internal keys', () => {
    assert.equal(outputShortName({ name: 'humidity_up', label: 'Humedad arriba (humidificador)' }), 'Humidificador');
    assert.equal(outputShortName({ name: 'ventilation' }), 'Extracción');
    assert.equal(outputShortName({ name: 'new_relay', label: 'Bomba' }), 'Bomba');
});
