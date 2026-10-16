/**
 * Qué cartel de arriba se muestra: el de "roto", el de anomalías o ninguno.
 *
 * Una regla: cada cosa la dice un solo cartel. Que el sensor que controla no
 * postea, que el lazo no corre o que el histórico no se guarda lo dice el
 * cartel rojo de arriba (sale de /api/health); la caja de anomalías queda para
 * la calidad de los datos guardados — lecturas imposibles, saltos, valores
 * clavados, huecos. El backend ya saca de /api/anomalies lo que la salud
 * explica (`web/alert_owners.py`); acá se vuelve a filtrar porque las dos
 * respuestas llegan en momentos distintos.
 *
 * Puro, así que lo que se ve queda probado sin el DOM.
 */

import { MODE_MONITOR } from './state.js';

/**
 * Si el cartel de "roto" tiene que estar arriba de todo.
 *
 * Sólo lo que está roto ahora: el lazo, el sensor que controla, o el histórico
 * que no se guarda con el lazo diciendo que corre. Una pausa es intencional y
 * la anuncia el header; un hueco que ya pasó lo cuentan las anomalías. Un
 * cartel que aparece por algo que alguien hizo a propósito es el que enseña a
 * ignorar los carteles.
 */
function isBroken(health) {
    if (!health || health.error) return false;
    if (health.loop?.state === 'down' || health.loop?.level === 'error') return true;
    if (health.history?.state === 'stalled') return true;
    return (health.sensors || []).some(
        sensor => sensor.role === 'primary' && (sensor.state === 'missing' || sensor.state === 'stale'));
}

/**
 * Las anomalías que dicen lo mismo que el cartel de roto: "no se está guardando
 * nada ahora" y un valor que sigue clavado. Con el cartel arriba, ni siquiera
 * suman al "+N advertencias".
 */
const OWNED_BY_BANNER = new Set(['no_data', 'stale_data']);

function ownedByBanner(issue) {
    if (OWNED_BY_BANNER.has(issue?.type)) return true;
    return String(issue?.type || '').startsWith('stuck_') && !!issue?.ongoing;
}

/** Severities, least serious first, so the box takes the tone of the worst one. */
const SEVERITIES = ['info', 'warning', 'critical'];

/**
 * The anomalies box's title, icon and tone for the most serious item in it.
 *
 * The colour follows the words: a red box titled "Advertencias" or an amber one
 * titled "Problemas críticos" says two things at once, and the reader believes
 * whichever is worse.
 */
const ANOMALY_LOOKS = {
    critical: { title: 'Problemas críticos detectados', icon: 'siren', tone: '' },
    warning: { title: 'Advertencias detectadas', icon: 'triangle-alert', tone: 'warn' },
    info: { title: 'Para tener en cuenta', icon: 'circle-alert', tone: 'info' },
};

/** The worst severity among `issues`; an unknown or missing one counts as a warning. */
function worstSeverity(issues) {
    return issues.reduce((worst, issue) => {
        const severity = SEVERITIES.includes(issue?.severity) ? issue.severity : 'warning';
        return SEVERITIES.indexOf(severity) > SEVERITIES.indexOf(worst) ? severity : worst;
    }, 'info');
}

/**
 * The "no hay cultivo activo" notice, or null when there is a grow.
 *
 * Not an anomaly: nothing is broken, the loop just has nowhere to file the
 * samples. It is shown in every mode — paused or monitoring, it is still true
 * that nothing is being stored — and worded for monitoring, where the grower
 * might think the mode itself was recording.
 */
function noGrowNotice(health) {
    if (!health || health.error || !health.no_active_grow) return null;
    const text = health.loop?.state === MODE_MONITOR
        ? 'No hay cultivo activo: el monitoreo no guarda datos. Creá un cultivo para empezar a registrar.'
        : 'No hay cultivo activo: no se guardan datos. Creá un cultivo para empezar a registrar.';
    return { kind: 'no_grow', text };
}

/**
 * Qué cartel de arriba se muestra, dados el veredicto y las anomalías.
 *
 * - Roto: sólo el cartel de roto. Las anomalías que quedan (las que no dicen
 *   lo mismo que el cartel) no tienen caja propia: se suman como una línea
 *   "+N advertencias", para que el que lee vea que hay más sin tener dos
 *   carteles pegados.
 * - Sano con anomalías: el cartel de anomalías, con hasta tres, con el título
 *   y el color del más grave que muestra.
 * - Nada: ningún cartel.
 *
 * Aparte, `notice`: el aviso de que no hay cultivo, que no es una alerta y no
 * se descarta. Sale de la salud y no de las anomalías, así que "Descartar" no
 * lo esconde y se va en cuanto hay un cultivo.
 */
function alertDecision({ health, anomalies = [], dismissed = false }) {
    const issues = dismissed ? [] : anomalies;
    const notice = noGrowNotice(health);
    if (isBroken(health)) {
        return {
            mode: 'broken',
            title: health.headline || 'Problema con sensores',
            text: health.detail || '',
            more: issues.filter(issue => !ownedByBanner(issue)).length,
            notice,
        };
    }
    if (issues.length) {
        const severity = worstSeverity(issues);
        return {
            mode: 'warnings',
            total: issues.length,
            severity,
            critical: severity === 'critical',
            ...ANOMALY_LOOKS[severity],
            items: issues.slice(0, 3),
            hidden: Math.max(0, issues.length - 3),
            notice,
        };
    }
    return { mode: 'none', notice };
}

/**
 * The "+N" line of the broken-state alert: singular for one, plural for more,
 * empty for none so the element stays hidden.
 */
function moreWarningsLabel(n) {
    if (!n) return '';
    return n === 1 ? '+1 advertencia' : `+${n} advertencias`;
}

export {
    alertDecision, isBroken, moreWarningsLabel, noGrowNotice, ownedByBanner, worstSeverity,
};
