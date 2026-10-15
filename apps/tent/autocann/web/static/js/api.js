/**
 * API client and connection state.
 *
 * Every request goes through apiGet so that timeouts, request de-duplication and
 * the online/offline indicator are handled in exactly one place.
 */

const connection = {
    consecutiveErrors: 0,
    lastSuccessAt: null,
    // Two misses in a row before complaining: one slow response on a busy
    // Raspberry is not an outage.
    get offline() { return this.consecutiveErrors >= 2; },
};

/**
 * Publish the connection state; the banner is this module's, the chip is not.
 *
 * The header chip now reports two things at once - whether the backend answers
 * and whether the control loop is paused - and only one place can own an
 * element. This announces, and whoever renders the chip decides which of the
 * two states wins.
 */
function renderConnectionState() {
    const banner = document.getElementById('connection-alert');
    if (banner) banner.classList.toggle('show', connection.offline);
    document.dispatchEvent(new CustomEvent('connection:changed', {
        detail: { offline: connection.offline },
    }));
}

const REQUEST_TIMEOUT_MS = 8000;
// One in-flight request per endpoint. Without this, a slow Raspberry makes
// the 3s pollers stack requests on top of each other.

const inFlight = new Map();

async function apiGet(url, { key = url } = {}) {
    if (inFlight.has(key)) return null;

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    inFlight.set(key, controller);

    try {
        const response = await fetch(url, { signal: controller.signal });
        if (response.status === 401) {
            // The session ran out, or the PIN changed under us. Every poller is
            // about to get the same answer, so go to the login page once
            // instead of filling the console and leaving the dashboard showing
            // numbers from before.
            window.location.reload();
            return null;
        }
        if (!response.ok && response.status >= 500) {
            throw new Error(`HTTP ${response.status}`);
        }
        const data = await response.json();
        connection.consecutiveErrors = 0;
        connection.lastSuccessAt = Date.now();
        renderConnectionState();
        return data;
    } catch (error) {
        connection.consecutiveErrors += 1;
        renderConnectionState();
        console.error(`Error fetching ${url}:`, error);
        return null;
    } finally {
        clearTimeout(timer);
        inFlight.delete(key);
    }
}

/**
 * POST con el error del servidor ya desempaquetado.
 *
 * No pasa por la de-duplicación de apiGet: dos clicks seguidos sobre un botón
 * son dos órdenes distintas, y descartar la segunda haría que la pantalla
 * mienta sobre lo que pidió el usuario.
 */
async function apiPost(url, body) {
    try {
        const response = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body ?? {}),
        });
        const data = await response.json().catch(() => ({}));
        if (response.status === 401 && data.auth_required) {
            // Same as in apiGet: the PIN is gone, so the page is, too. Said
            // out loud as well, because this one was a button press.
            window.location.reload();
            return { ok: false, error: 'La sesión expiró: hace falta el PIN otra vez' };
        }
        if (!response.ok) return { ok: false, error: data.error || `HTTP ${response.status}` };
        return { ok: true, data };
    } catch (error) {
        console.error(`Error posting ${url}:`, error);
        return { ok: false, error: 'No se pudo contactar al backend' };
    }
}

export { connection, renderConnectionState, apiGet, apiPost };
