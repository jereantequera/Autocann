/**
 * La tarjeta de salidas del Panel: qué relé está en qué, quién lo conmuta, y
 * los botones de a mano cuando el lazo no está a cargo.
 *
 * Aparte de dashboard.js porque es una pieza entera: las tres filas, el
 * renglón que nombra la placa y los controles manuales se leen juntos y
 * cambian juntos. Y porque todos comparten una misma regla, que es la razón
 * de este archivo: **el estado de una salida es lo que el lazo pidió, nunca lo
 * que hizo un contacto.** Mientras el nodo de relés consulta cada dos
 * segundos, la diferencia no se nota y no vale una palabra en pantalla; cuando
 * deja de consultar, las dos cosas no tienen nada que ver y todo lo que se
 * dibuja acá tiene que decirlo.
 */

import { outputRowState, outputShortName, relayNodeSilent, silentFor } from './status.js';
import { escapeHtml, formatDuration, icon, relaySourceLabel } from './util.js';

// Recordado por relé: cuándo esta página lo vio prendido por primera vez. El
// backend no dice cuándo conmutó, así que después de un reload los minutos
// cuentan desde el reload y no desde el cambio — la fila lo dice mostrando
// menos, no equivocándose.
const onSince = {};

/** Las salidas con `since` puesto en las que están prendidas. */
function outputsWithSince(outputs) {
    const nowSeconds = Date.now() / 1000;
    return outputs.map(output => {
        if (output.state !== true) {
            delete onSince[output.name];
            return output;
        }
        if (!(output.name in onSince)) onSince[output.name] = nowSeconds;
        return { ...output, since: onSince[output.name] };
    });
}

function outputRowsHtml(outputs, relayNode) {
    const silent = relayNodeSilent(relayNode);
    return outputs.map(output => {
        const row = outputRowState(output, Date.now(), relayNode);
        // Tres aspectos, no dos. Una fila que el lazo pidió prendida mientras
        // nadie conmuta no está ni prendida (ámbar) ni apagada (gris), y
        // pintarla de cualquiera de las dos es el error entero: el ámbar dice
        // que el deshumidificador está andando y el gris dice que el lazo se
        // olvidó de él, y las dos mandan a cualquier lado menos a la placa.
        const cls = row.state === 'on' ? ' is-on'
            : (silent && output.state === true) ? ' is-unconfirmed' : '';
        return `
            <div class="ac-output${cls}">
                <span class="ac-output__name">${escapeHtml(outputShortName(output))}</span>
                <span class="ac-output__state">${escapeHtml(row.text)}</span>
            </div>`;
    }).join('');
}

/**
 * Los controles manuales, debajo de las filas, y sólo mientras el lazo no está
 * a cargo: los botones son la forma de mover un relé a mano, y no significan
 * nada mientras el lazo decide.
 */
function manualControlsHtml(outputs, mode) {
    return outputs.filter(output => output.name).map(output => {
        const requested = mode.requested_outputs?.[output.name];
        // Un relé que no se mueve tiene que decir por qué, o el botón parece
        // roto. Los mínimos de encendido y apagado valen también para una
        // persona.
        const held = (typeof output.held_seconds === 'number' && output.held_seconds > 0)
            ? `<span class="panel-manual__held">${icon('hourglass')}esperando ${formatDuration(output.held_seconds)} (mínimo de seguridad)</span>`
            : '';
        const name = escapeHtml(output.name);
        return `
            <div class="panel-manual__row">
                <span class="ac-output__name">${escapeHtml(outputShortName(output))}</span>
                <span class="panel-manual__buttons">
                    <button type="button" class="ac-btn ac-btn--secondary" aria-pressed="${requested === true}"
                            data-action="test-output" data-name="${name}" data-state="true">Prender</button>
                    <button type="button" class="ac-btn ac-btn--secondary" aria-pressed="${requested === false}"
                            data-action="test-output" data-name="${name}" data-state="false">Apagar</button>
                </span>
                ${held}
            </div>`;
    }).join('');
}

/**
 * El renglón que nombra la placa que conmuta — y, si dejó de pedir órdenes, lo
 * dice ahí mismo.
 *
 * Ahí y no en un cartel nuevo al lado: ese renglón ya nombra la placa, así que
 * reforzarlo es más barato que abrir una caja que repita lo que la línea de
 * estado de arriba ya está diciendo.
 */
function renderSourceLine(outputs, relayNode) {
    const source = document.getElementById('outputs-source');
    if (!source) return;
    const label = relaySourceLabel(outputs[0]?.pin);
    const silent = relayNodeSilent(relayNode);
    source.textContent = silent
        ? `${label.text} · sin contacto${silentFor(relayNode)}`
        : label.text;
    source.title = silent ? relayNode.detail : label.title;
    source.classList.toggle('is-alert', silent);
}

/**
 * Dibuja la tarjeta entera con lo que vino de `/api/output-status`.
 *
 * @returns {Array} las salidas con `since`, para quien tenga que repartirlas.
 */
function renderOutputsCard(payload, testMode) {
    const outputs = outputsWithSince(payload?.outputs || []);
    const relayNode = payload?.relay_node || null;

    renderSourceLine(outputs, relayNode);

    const list = document.getElementById('outputs-list');
    if (list) {
        list.innerHTML = outputs.length
            ? outputRowsHtml(outputs, relayNode)
            : '<div class="ac-output"><span class="ac-output__name">No configuradas</span>'
                + '<span class="ac-output__state">--</span></div>';
    }

    const manual = document.getElementById('outputs-manual');
    if (manual) {
        const show = testMode.active && outputs.length > 0;
        manual.innerHTML = show ? manualControlsHtml(outputs, testMode) : '';
        manual.hidden = !show;
    }

    return outputs;
}

export { manualControlsHtml, outputRowsHtml, outputsWithSince, renderOutputsCard };
