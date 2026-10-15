/**
 * The one sentence the panel opens with: is the tent fine, and if not, which
 * way is it drifting and what is already doing something about it.
 *
 * Pure on purpose - no DOM, no network - so every branch is a test in
 * tests/js/status.test.mjs rather than something found by looking at the wall.
 */

import { MODE_MONITOR } from './state.js';
import { fmtComma } from './util.js';

/** The short names the relay rows use. Unknown outputs fall back to their label. */
const OUTPUT_NAMES = {
    humidity_up: 'Humidificador',
    humidity_down: 'Deshumidificador',
    ventilation: 'Extracción',
};

/** The same names with their article, for sentences that start with them. */
const OUTPUT_ARTICLED = {
    humidity_up: 'El humidificador',
    humidity_down: 'El deshumidificador',
};

/**
 * Outputs the control loop drives by itself. Mirrors ACTION_OUTPUTS in
 * autocann/control/quiet.py, and /api/quiet-hours serves the same list; the
 * ventilation is the one a person has to switch.
 */
const AUTOMATIC_OUTPUTS = ['humidity_up', 'humidity_down'];

const LOOK_AT_SISTEMA = 'Mirá Sistema para ver qué sensor o qué parte del lazo no contesta.';

/** 61 -> "61", 61.5 -> "61,5". Band edges are whole numbers in practice. */
function fmtBand(value) {
    return Number.isInteger(value) ? String(value) : fmtComma(value, 1);
}

function outputNamed(outputs, name) {
    return (outputs || []).find(output => output.name === name) || null;
}

/** The output has been on since `since` (epoch seconds), so how many minutes. */
function minutesSince(since, now) {
    if (!Number.isFinite(since)) return null;
    return Math.max(0, Math.floor((now / 1000 - since) / 60));
}

/** Whether a relay name is one the loop switches on its own. */
function isAutomaticOutput(name) {
    return AUTOMATIC_OUTPUTS.includes(name);
}

/** The short name of an output: "Humidificador", never the internal key. */
function outputShortName(output) {
    return OUTPUT_NAMES[output?.name] || output?.label || 'Salida';
}

/**
 * What one relay row says about itself, in the words the panel uses.
 *
 * `held_seconds` is the backend refusing to switch yet (minimum on/off times),
 * which is the one reason a relay that was asked to move did not. `since` is
 * epoch seconds, when the panel saw the relay come on; without it the row
 * says "prendido" and nothing about how long.
 *
 * @returns {{state: 'on'|'off'|'unknown', text: string}}
 */
function outputRowState(output, now = Date.now()) {
    if (output?.state === true) {
        const minutes = minutesSince(output.since, now);
        return {
            state: 'on',
            text: minutes === null ? 'prendido' : `prendido · ${minutes} min`,
        };
    }
    if (typeof output?.held_seconds === 'number' && output.held_seconds > 0) {
        return {
            state: 'off',
            text: `apagado · espera ${Math.ceil(output.held_seconds / 60)} min`,
        };
    }
    if (output?.state === false) {
        return {
            state: 'off',
            text: isAutomaticOutput(output.name) ? 'apagado' : 'sólo manual',
        };
    }
    return { state: 'unknown', text: 'sin estado' };
}

/**
 * The status line: { level, title, text }.
 *
 * `level` is one of ok, warn, alert, idle. `idle` is not an error: it says the
 * panel has nothing to judge (no readings, paused, monitoring), which is a
 * different thing from the tent being out of range.
 *
 * @param {?object} current   /api/current-data, or null when there is none
 * @param {?object} health    /api/health, for the loop's own state
 * @param {?object} mode      test_mode from /api/output-status
 * @param {Array}  outputs    outputs from /api/output-status
 * @param {object} stageNames stage key -> label, from /api/config
 * @param {number} now        epoch ms, injectable for the tests
 */
function panelStatus(current, health, mode, outputs = [], stageNames = {}, now = Date.now()) {
    const idle = (title, text) => ({ level: 'idle', title, text });

    const hasReadings = !!current
        && Number.isFinite(current.temperature) && Number.isFinite(current.humidity);

    // The health verdict wins over the reading. A reading can be on screen
    // while the loop is down, or while the sensor that controls the tent has
    // stopped posting; the alerts already say so, and the status line must not
    // say the opposite.
    const controlling = (health?.sensors || []).find(sensor => sensor.controls);
    const sensorSilent = controlling?.state === 'missing' || controlling?.state === 'stale';
    if (!hasReadings || health?.loop?.state === 'down' || sensorSilent) {
        const text = sensorSilent && hasReadings && health?.loop?.state !== 'down'
            ? 'El sensor que controla la carpa no está posteando. Mirá Sistema para ver cuál y por qué.'
            : LOOK_AT_SISTEMA;
        return idle('Sin lecturas del lazo', text);
    }

    if (mode?.active) {
        return mode.mode === MODE_MONITOR
            ? idle('Monitoreando', 'Nada se enciende: el lazo sólo mira y guarda el histórico.')
            : idle('En pausa', 'El lazo no controla la carpa: los relés quedan donde los pongas a mano.');
    }

    const stage = stageNames[current.stage] || current.stage || 'esta etapa';

    if (current.vpd_in_range === true) {
        return {
            level: 'ok',
            title: 'En rango',
            text: `Temperatura y humedad dentro de lo que pide ${stage}.`,
        };
    }

    if (current.vpd_in_range !== false) {
        return idle('Sin evaluación', 'El lazo todavía no dice si esto está en rango.');
    }

    const band = current.humidity_band;
    if (!Array.isArray(band) || band.length !== 2) {
        return {
            level: 'warn',
            title: 'Fuera de rango',
            text: `Temperatura y humedad fuera de lo que pide ${stage}.`,
        };
    }

    const [low, high] = band;
    const dry = current.humidity < low;
    const humid = current.humidity > high;
    if (!dry && !humid) {
        return {
            level: 'warn',
            title: 'Fuera de rango',
            text: `Temperatura y humedad fuera de lo que pide ${stage}.`,
        };
    }

    // The relay that is already doing something about it, if any: the reason
    // the title says so, and the first sentence of the text.
    const relayName = dry ? 'humidity_up' : 'humidity_down';
    const relay = outputNamed(outputs, relayName);
    const relayOn = relay?.state === true;
    const verb = dry ? 'humidificando' : 'deshumidificando';

    const minutes = minutesSince(relay?.since, now);
    const relaySentence = relayOn
        ? `${OUTPUT_ARTICLED[relayName]} ${minutes === null
            ? 'está prendido'
            : `arrancó hace ${minutes < 1 ? 'un momento' : `${minutes} min`}`}. `
        : '';

    return {
        level: 'warn',
        title: `${dry ? 'Un poco seco' : 'Un poco húmedo'}${relayOn ? ` · ${verb}` : ''}`,
        text: `${relaySentence}A ${fmtComma(current.temperature, 1)} °C la humedad tiene que `
            + `estar entre ${fmtBand(low)} y ${fmtBand(high)} % para esta etapa.`,
    };
}

export {
    AUTOMATIC_OUTPUTS, OUTPUT_NAMES, outputRowState, outputShortName, isAutomaticOutput,
    panelStatus,
};
