/**
 * Qué cartel de arriba se muestra: el de "roto", el de anomalías o ninguno.
 *
 * Una regla: cada cosa la dice un solo cartel. Que el sensor que controla no
 * postea o que el lazo no corre lo dice el cartel rojo de arriba (sale de
 * /api/health); la caja de anomalías queda para el histórico — saltos, huecos,
 * y el lazo que corre pero no guarda. El backend ya saca de /api/anomalies lo que la salud
 * explica (`web/alert_owners.py`); acá se vuelve a filtrar porque las dos
 * respuestas llegan en momentos distintos.
 *
 * Puro, así que lo que se ve queda probado sin el DOM.
 */

import { MODE_MONITOR } from './state.js';

/**
 * Si el cartel de "roto" tiene que estar arriba de todo.
 *
 * Sólo lo que está roto ahora y es del lazo o del sensor que controla. Una
 * pausa es intencional y la anuncia el header; un hueco en el histórico, o el
 * lazo que corre sin guardar, lo cuentan las anomalías. Un cartel que aparece
 * por algo que alguien hizo a propósito es el que enseña a ignorar los
 * carteles.
 */
function isBroken(health) {
    if (!health || health.error) return false;
    if (health.loop?.state === 'down' || health.loop?.level === 'error') return true;
    return (health.sensors || []).some(
        sensor => sensor.role === 'primary' && (sensor.state === 'missing' || sensor.state === 'stale'));
}

/**
 * Las anomalías que dicen lo mismo que el cartel de roto: "no se está guardando
 * nada ahora". Con el cartel arriba, ni siquiera suman al "+N advertencias".
 */
const OWNED_BY_BANNER = new Set(['no_data', 'stale_data']);

function ownedByBanner(issue) {
    return OWNED_BY_BANNER.has(issue?.type);
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
 * Lo que ya se vio (o se pospuso) no se muestra: queda contado en `seen`, que
 * es el "N vistas" que lo vuelve a abrir. Con `showSeen` (la campanita, o ese
 * botón) se muestran todas, las vistas también, y sin el tope de tres.
 *
 * Aparte, `notice`: el aviso de que no hay cultivo, que no es una alerta y no
 * se marca como vista. Sale de la salud y no de las anomalías, así que "Visto"
 * no lo esconde y se va en cuanto hay un cultivo.
 */
function alertDecision({ health, anomalies = [], showSeen = false }) {
    const unread = anomalies.filter(isUnread);
    const seen = anomalies.length - unread.length;
    const issues = showSeen ? anomalies : unread;
    const notice = noGrowNotice(health);
    if (isBroken(health)) {
        return {
            mode: 'broken',
            title: health.headline || 'Problema con sensores',
            text: health.detail || '',
            more: unread.filter(issue => !ownedByBanner(issue)).length,
            notice,
        };
    }
    if (issues.length) {
        // The tone is the news's: a red alert already seen does not keep the
        // box red over a new amber one.
        const severity = worstSeverity(unread.length ? unread : issues);
        const limit = showSeen ? issues.length : 3;
        return {
            mode: 'warnings',
            total: issues.length,
            severity,
            critical: severity === 'critical',
            ...ANOMALY_LOOKS[severity],
            items: issues.slice(0, limit),
            hidden: Math.max(0, issues.length - limit),
            seen,
            showSeen,
            notice,
        };
    }
    return { mode: 'none', notice };
}

/**
 * Whether an alert is news. The backend says so (`unread`); an item that does
 * not carry it is news, so nothing is hidden by a missing field.
 */
function isUnread(issue) {
    return issue?.unread !== false;
}

/** How many alerts are news: the bell's number. */
function unreadCount(anomalies = []) {
    return anomalies.filter(isUnread).length;
}

/** The line that opens and closes the seen alerts: "1 vista", "3 vistas". */
function seenLabel(n, showSeen) {
    if (showSeen) return 'Ocultar vistas';
    if (!n) return '';
    return n === 1 ? '1 vista' : `${n} vistas`;
}

/**
 * What a seen alert says instead of its buttons: when it comes back if it was
 * snoozed, or just that it was seen.
 */
function seenStateLabel(issue, now = Date.now()) {
    const until = issue?.snoozed_until;
    if (issue?.acknowledged_at == null && Number.isFinite(until) && until * 1000 > now) {
        const at = new Date(until * 1000);
        const hh = String(at.getHours()).padStart(2, '0');
        const mm = String(at.getMinutes()).padStart(2, '0');
        return `pospuesta hasta las ${hh}:${mm}`;
    }
    return 'vista';
}

/**
 * The icon of one item in the anomalies box. Only a warning gets the triangle;
 * a critical item and an info notice get the circle, so a notice does not look
 * like an alarm. A missing severity counts as a warning, as everywhere else.
 */
function anomalyIcon(severity) {
    return (severity || 'warning') === 'warning' ? 'triangle-alert' : 'circle-alert';
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
    alertDecision, anomalyIcon, isBroken, isUnread, moreWarningsLabel, noGrowNotice, ownedByBanner,
    seenLabel, seenStateLabel, unreadCount, worstSeverity,
};
