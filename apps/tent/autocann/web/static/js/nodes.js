/**
 * Los nodos ESP32: su diagnóstico y su log, en Sistema.
 *
 * El nodo imprime por el puerto serie exactamente lo que está mal — el DHT22
 * que no contesta, el POST que volvió 400, el WiFi que se cayó — y eso era
 * invisible desde la web. Leerlo de verdad significa entrar a la carpa con una
 * notebook y desenchufar el nodo de su fuente para enchufarlo al USB, lo que lo
 * reinicia y borra justo el estado que se quería mirar. Así que el nodo lo
 * postea y esto lo muestra.
 */

import { apiGet } from './api.js';
import { escapeHtml, fmtNum, formatAge, formatDuration, icon } from './util.js';

const LEVEL_ICONS = { ok: 'circle-check', warn: 'triangle-alert', error: 'circle-x' };
// Text marks, not icons: the console is a column of monospace, and a glyph that
// is one character wide keeps the lines aligned.
const LINE_LEVELS = { info: '·', warn: '!', error: '×' };

/** Qué nodo se está mirando; null hasta que el backend diga cuáles hay. */
let selected = null;
// Node names from the last /api/nodes answer. With two or more nodes the log
// endpoint refuses to guess, so the first one is asked for by name.
let knownNodes = [];
/** Si la consola se pega al final sola. Se apaga al scrollear para atrás. */
let follow = true;

// ===============================
// Helpers puros
// ===============================

/**
 * La calidad del WiFi en palabras.
 *
 * -62 dBm no le dice nada a nadie, y es el número que decide si vale la pena
 * mover el nodo o poner un repetidor.
 */
function signalLabel(rssi) {
    if (!Number.isFinite(rssi)) return 'sin dato';
    if (rssi >= -60) return `${rssi} dBm · buena`;
    if (rssi >= -70) return `${rssi} dBm · aceptable`;
    if (rssi >= -80) return `${rssi} dBm · justa`;
    return `${rssi} dBm · mala`;
}

/**
 * Qué proporción de lecturas falló, que es lo que distingue un DHT22 con un
 * cable flojo de uno sano: una falla suelta cada tanto es normal en estos
 * sensores, una de cada tres es un problema físico.
 */
function readRate(sensor) {
    const ok = Number(sensor.reads_ok) || 0;
    const failed = Number(sensor.reads_failed) || 0;
    const total = ok + failed;
    if (!total) return { text: 'sin lecturas', bad: true };
    const pct = (failed / total) * 100;
    return {
        text: `${ok} ok · ${failed} ${failed === 1 ? 'fallida' : 'fallidas'} (${fmtNum(pct, 1)} %)`,
        bad: pct >= 20 || Number(sensor.consecutive_failed) >= 3,
    };
}

// ===============================
// Render
// ===============================

function nodeSensorHtml(sensor) {
    const rate = readRate(sensor);
    const posts = Number(sensor.posts_failed) || 0;
    return `
        <div class="node-sensor${rate.bad ? ' is-bad' : ''}">
            <span class="node-sensor-id">${escapeHtml(String(sensor.id || '?').toUpperCase())}</span>
            <span class="node-sensor-pin">GPIO ${escapeHtml(String(sensor.pin ?? '--'))}</span>
            <span class="node-sensor-rate">${escapeHtml(rate.text)}</span>
            ${posts ? `<span class="node-sensor-posts">${posts} ${posts === 1 ? 'POST fallido' : 'POST fallidos'}</span>` : ''}
        </div>`;
}

function factsHtml(rows) {
    return `<dl class="node-facts">${rows.map(([name, value]) => `
        <dt>${escapeHtml(name)}</dt>
        <dd>${escapeHtml(String(value))}</dd>`).join('')}</dl>`;
}

function nodeCardHtml(node) {
    const levelIcon = icon(LEVEL_ICONS[node.level] || 'hourglass');
    const sensors = (node.sensors || []).map(nodeSensorHtml).join('');

    // Cada dato está acá porque contesta una pregunta concreta cuando algo
    // falla: el uptime y el motivo del arranque distinguen un corte de luz de
    // un brownout de la fuente, las reconexiones delatan un WiFi al límite, y
    // el heap libre delata una pérdida de memoria antes de que el nodo se
    // reinicie solo.
    const rows = [
        ['Firmware', node.firmware || 'desconocido'],
        ['IP', node.ip || '--'],
        ['Señal', signalLabel(node.rssi)],
        ['Encendido hace', node.uptime_seconds ? formatDuration(node.uptime_seconds) : '--'],
        ['Último arranque', node.boot_reason || '--'],
        ['Reconexiones WiFi', node.wifi_reconnects ?? '--'],
        ['Memoria libre', Number.isFinite(node.free_heap)
            ? `${Math.round(node.free_heap / 1024)} kB` : '--'],
    ];
    // Two columns of facts, so a node reads as one block instead of a list
    // that runs the length of the screen.
    const half = Math.ceil(rows.length / 2);

    return `
        <article class="node-card is-${escapeHtml(node.level || 'warn')}">
            <div class="node-card-head">
                <span class="ac-dot ${node.level === 'ok' ? 'ac-dot--ok' : 'ac-dot--alert'}" aria-hidden="true"></span>
                <span class="node-card-name">${escapeHtml(node.node)}</span>
                <span class="node-card-state">${levelIcon}${escapeHtml(node.label || '')}</span>
            </div>
            <div class="sys-detail">${escapeHtml(node.detail || '')}</div>
            <div class="node-card-rows">
                ${factsHtml(rows.slice(0, half))}
                ${factsHtml(rows.slice(half))}
            </div>
            ${sensors ? `<div class="node-sensors">${sensors}</div>` : ''}
            <div class="node-card-foot">Último reporte ${escapeHtml(formatAge(node.age_seconds))}</div>
        </article>`;
}

function renderNodes(payload) {
    const grid = document.getElementById('nodes-grid');
    const badge = document.getElementById('nodes-badge');
    if (!grid) return;

    const nodes = payload?.nodes || [];
    knownNodes = nodes.map(node => node.node);
    if (!nodes.length) {
        // Un nodo con firmware viejo no reporta nada, y eso se ve igual que un
        // nodo muerto. Vale la pena decir cuál de las dos cosas es.
        grid.innerHTML = `
            <div class="sys-note">
                Ningún nodo reportó su estado todavía. El firmware anterior no lo
                hacía: regrabá <code>firmware/sensor_node</code> para ver acá su
                log y sus contadores.
            </div>`;
        if (badge) { badge.className = 'sensor-badge'; badge.textContent = 'sin reportes'; }
        return;
    }

    grid.innerHTML = nodes.map(nodeCardHtml).join('');

    if (badge) {
        // Estar en línea y estar sano no son lo mismo: un nodo que reporta con
        // un DHT22 que no lee sigue estando en línea, y contarlo como caído
        // manda a revisar la red en vez del cable.
        const online = nodes.filter(node => node.state === 'online' || node.state === 'degraded');
        const healthy = online.length === nodes.length && online.every(node => node.level === 'ok');
        badge.className = `sensor-badge ${healthy ? 'ok' : online.length ? 'stale' : 'error'}`;
        badge.textContent = `${online.length} de ${nodes.length} en línea`;
    }
}

function renderPicker(names) {
    const picker = document.getElementById('node-log-picker');
    if (!picker) return;

    // Con un solo nodo el selector es ruido: no hay nada que elegir.
    picker.hidden = names.length < 2;
    if (picker.options.length === names.length
        && [...picker.options].every((option, i) => option.value === names[i])) {
        picker.value = selected || names[0] || '';
        return;
    }
    picker.innerHTML = names
        .map(name => `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`)
        .join('');
    picker.value = selected || names[0] || '';
}

function renderLog(payload) {
    const box = document.getElementById('node-log');
    if (!box) return;

    const lines = payload?.lines || [];
    if (!lines.length) {
        box.innerHTML = '<div class="node-log-empty">El nodo todavía no mandó ninguna línea.</div>';
        return;
    }

    box.innerHTML = lines.map(line => `
        <div class="node-log-line is-${escapeHtml(line.level || 'info')}">
            <span class="node-log-time">${escapeHtml(line.datetime || '')}</span>
            <span class="node-log-mark">${LINE_LEVELS[line.level] || '·'}</span>
            <span class="node-log-text">${escapeHtml(line.text || '')}</span>
        </div>`).join('');

    // Pegado al final sólo si nadie se fue a leer para arriba: arrastrar la
    // vista mientras alguien lee una línea vieja es perder esa línea.
    if (follow) box.scrollTop = box.scrollHeight;
}

// ===============================
// Fetch
// ===============================

function fetchNodes() {
    apiGet('/api/nodes', { key: 'nodes' }).then(data => {
        if (!data) return;
        renderNodes(data);
        // The log was asked for before the names were known; ask again by name.
        if (!selected) fetchNodeLog();
    });
}

function fetchNodeLog() {
    selected = selected || knownNodes[0] || null;
    const url = selected ? `/api/node/log?node=${encodeURIComponent(selected)}` : '/api/node/log';
    apiGet(url, { key: 'node-log' }).then(data => {
        if (!data) return;
        const names = data.nodes || [];
        renderPicker(names);
        if (data.error) {
            // Sin nodos el backend contesta 400 con la lista vacía; eso no es
            // un error que haya que gritar, es un nodo que todavía no habló.
            renderLog({ lines: [] });
            return;
        }
        selected = data.node || selected;
        renderLog(data);
    });
}

function refreshNodes() {
    fetchNodes();
    fetchNodeLog();
}

function wireNodes() {
    const view = document.querySelector('.view[data-view="sistema"]');
    if (!view) return;

    view.addEventListener('click', event => {
        if (event.target.closest('[data-sys="refresh-nodes"]')) refreshNodes();
    });

    const picker = document.getElementById('node-log-picker');
    if (picker) {
        picker.addEventListener('change', () => {
            selected = picker.value;
            fetchNodeLog();
        });
    }

    const followBox = document.getElementById('node-log-follow');
    if (followBox) {
        follow = followBox.checked;
        followBox.addEventListener('change', () => {
            follow = followBox.checked;
            if (follow) fetchNodeLog();
        });
    }

    const box = document.getElementById('node-log');
    if (box) {
        box.addEventListener('scroll', () => {
            const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 24;
            if (atBottom !== follow && followBox) {
                follow = atBottom;
                followBox.checked = atBottom;
            }
        });
    }

    document.addEventListener('view:enter', event => {
        if (event.detail?.view === 'sistema') refreshNodes();
    });
}

export { fetchNodeLog, fetchNodes, refreshNodes, signalLabel, readRate, wireNodes };
