/**
 * The one sentence the panel opens with: is the tent fine, and if not, which
 * way is it drifting and what is already doing something about it.
 *
 * Pure on purpose - no DOM, no network - so every branch is a test in
 * tests/js/status.test.mjs rather than something found by looking at the wall.
 */

import { MODE_MONITOR } from './state.js';
import { fmtComma, formatDuration } from './util.js';

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

/**
 * Whether the board that carries out the decisions has gone quiet.
 *
 * `relay_node` rides along with both the outputs and the health answer, from
 * the same computation on the backend, and is null when the Raspberry switches
 * its own pins — then there is nothing between a decision and a relay.
 *
 * Everything the panel knows about a relay is what the loop *asked for*. While
 * the node is polling, two seconds later that is also what the relay is doing,
 * and the distinction is not worth a word on screen. While it is silent the
 * two have nothing to do with each other, and every sentence below that names
 * a relay has to say so instead of narrating equipment nobody is switching.
 */
function relayNodeSilent(relayNode) {
    return !!relayNode && relayNode.level !== 'ok';
}

/** "hace 15 h 23 min", or "" when the node never polled at all. */
function silentFor(relayNode) {
    const age = relayNode?.age_seconds;
    return typeof age === 'number' && age > 0 ? ` hace ${formatDuration(age)}` : '';
}

/**
 * What an in-range tent with an output on is doing, for the subtitle. The
 * humidity relays come first: they are the ones that move the reading, the
 * extractor only runs alongside them. Null when nothing is on — and null,
 * too, when nothing is switching, which is a different thing that must not
 * read on screen as activity.
 */
function rangeActivity(outputs, target, relayNode) {
    if (relayNodeSilent(relayNode)) return null;
    const on = name => outputNamed(outputs, name)?.state === true;
    const aim = Number.isFinite(target) ? ` hacia ${fmtBand(target)} %` : '';
    if (on('humidity_up')) return `humidificando${aim}`;
    if (on('humidity_down')) return `deshumidificando${aim}`;
    if (on('ventilation')) return 'ventilando';
    return null;
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
 * `relayNode` is the board that applies all of this. While it is silent a row
 * the loop asked ON is the one that lies loudest — it is the row somebody
 * reads as "the dehumidifier is running" while standing in front of an open
 * relay — so it stops claiming to be on and says what it really is: a request
 * with nobody on the other end. A row asked OFF needs no such warning: off is
 * both what was asked and where an unattended relay ends up.
 *
 * @returns {{state: 'on'|'off'|'unknown', text: string}}
 */
function outputRowState(output, now = Date.now(), relayNode = null) {
    if (output?.state === true && relayNodeSilent(relayNode)) {
        return { state: 'unknown', text: 'pedido · sin conmutar' };
    }
    if (output?.state === true) {
        const minutes = minutesSince(output.since, now);
        return {
            state: 'on',
            text: minutes === null ? 'prendido'
                : minutes < 1 ? 'prendido · recién' : `prendido · ${minutes} min`,
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
 * The tent itself in one sentence, for a status line whose title is about
 * something else. Says exactly what the out-of-range branch below says, from
 * the same inputs, so the panel never grows a second way of describing one
 * reading.
 */
function environmentSentence(current, stage) {
    if (current.vpd_in_range === true) {
        return `Temperatura y humedad están dentro de lo que pide ${stage}.`;
    }
    if (current.vpd_in_range !== false) {
        return 'El lazo todavía no dice si esto está en rango.';
    }
    const band = current.humidity_band;
    if (!Array.isArray(band) || band.length !== 2) {
        return `Temperatura y humedad fuera de lo que pide ${stage}.`;
    }
    const [low, high] = band;
    return `A ${fmtComma(current.temperature, 1)} °C la humedad tiene que estar `
        + `entre ${fmtBand(low)} y ${fmtBand(high)} % para esta etapa.`;
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
 *
 * The relay node is read off `health.relay_node` rather than passed in: the
 * headline in Sistema is computed from the same field on the backend, so the
 * two cannot end up disagreeing about whether anything is switching.
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

    const relayNode = health?.relay_node;

    // Checked before the reading is judged, and it outranks it. "Un poco
    // húmedo · deshumidificando" was true of the loop's intentions and false
    // of the tent: on 2026-11-10 the relay node went off the air at 18:14 and
    // the panel spent the night narrating a dehumidifier that had no power,
    // over a relay anybody could see was open. Which way the humidity is
    // drifting still matters, so it stays in the text — but the headline is
    // that nothing is acting on it.
    if (relayNodeSilent(relayNode)) {
        return {
            level: 'alert',
            title: 'Nadie conmuta los relés',
            text: `${relayNode.name || 'El nodo de relés'} no pide órdenes`
                + `${silentFor(relayNode)}, así que las salidas de abajo son lo que `
                + `el lazo pide y no lo que está pasando. `
                + environmentSentence(current, stage),
        };
    }

    if (current.vpd_in_range === true) {
        const activity = rangeActivity(outputs, current.target_humidity, relayNode);
        return {
            level: 'ok',
            title: 'En rango',
            text: activity
                ? `Dentro de la franja · ${activity}`
                : `Temperatura y humedad dentro de lo que pide ${stage}.`,
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
        text: relaySentence + environmentSentence(current, stage),
    };
}

export {
    AUTOMATIC_OUTPUTS, OUTPUT_NAMES, outputRowState, outputShortName, isAutomaticOutput,
    panelStatus, relayNodeSilent, silentFor,
};
