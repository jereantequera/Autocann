/**
 * The panel's chart of where the tent is, in the plane the controller works in:
 * air temperature across, relative humidity up. The shaded band is where the
 * VPD of the leaf is inside this stage's range; the dot is the reading now.
 *
 * The geometry and the markup are pure functions (tests/js/vpdchart.test.mjs).
 * Only the wiring at the bottom touches the page.
 */

import { apiGet } from './api.js';
import { escapeHtml, fmtComma, parseLocalDatetime } from './util.js';

/** Plot area in chart units. The viewBox adds a margin for the axis labels. */
const PLOT = { width: 560, height: 400, tMin: 18, tMax: 32, hMin: 30, hMax: 90 };
const VIEW_BOX = { x: -44, y: -10, width: 640, height: 460 };
/** The dashed trail is the last few hours, not the whole day: it is context. */
const TRAIL_POINTS = 6;
/** Where the tooltip flips to the left of the dot, in percent of the plot. */
const TIP_FLIP_AT = 55;

function clamp(value, low, high) {
    return Math.min(Math.max(value, low), high);
}

/** Air temperature -> x, clamped to the plot so an outlier stays on the edge. */
function plotX(temperature) {
    return clamp((temperature - PLOT.tMin) / (PLOT.tMax - PLOT.tMin) * PLOT.width, 0, PLOT.width);
}

/** Relative humidity -> y, with 90 % at the top of the plot. */
function plotY(humidity) {
    return clamp((PLOT.hMax - humidity) / (PLOT.hMax - PLOT.hMin) * PLOT.height, 0, PLOT.height);
}

function round1(value) {
    return Math.round(value * 10) / 10;
}

function hasReading(row) {
    return !!row && Number.isFinite(row.temperature) && Number.isFinite(row.humidity);
}

/**
 * Air VPD in kPa, from air temperature (°C) and relative humidity (%).
 *
 * The Tetens form the rest of the code uses for VPD of the air; the leaf value
 * comes from the backend, so this is only for the reading the panel shows
 * next to it.
 */
function airVpd(temperature, humidity) {
    const saturation = 0.6108 * Math.exp((17.27 * temperature) / (temperature + 237.3));
    return saturation * (1 - humidity / 100);
}

/**
 * Both edges of the band, sorted left to right, in plot coordinates.
 *
 * `upper` follows humidity_high (the wet edge, drawn higher on the plot) and
 * `lower` follows humidity_low (the dry edge, drawn lower).
 */
function bandEdges(points) {
    const usable = (points || [])
        .filter(p => Number.isFinite(p?.temperature)
            && Number.isFinite(p.humidity_low) && Number.isFinite(p.humidity_high))
        .sort((a, b) => a.temperature - b.temperature);
    return {
        upper: usable.map(p => ({ x: plotX(p.temperature), y: plotY(p.humidity_high) })),
        lower: usable.map(p => ({ x: plotX(p.temperature), y: plotY(p.humidity_low) })),
    };
}

/**
 * The two band labels, top edge first, each at the height of the edge it names.
 *
 * Lower VPD is the wetter side, so the top edge is vpd_min and the bottom edge
 * is vpd_max. For a humidity band the labels are the humidities instead.
 */
function bandLabels(band) {
    const last = (band?.points || []).filter(p => Number.isFinite(p?.temperature)).at(-1);
    if (!last || !Number.isFinite(last.humidity_low) || !Number.isFinite(last.humidity_high)) return [];
    const humidityKind = band.kind === 'humidity';
    return [
        {
            text: humidityKind ? `${Math.round(last.humidity_high)} %` : fmtComma(band.vpd_min, 1),
            y: plotY(last.humidity_high),
        },
        {
            text: humidityKind ? `${Math.round(last.humidity_low)} %` : `${fmtComma(band.vpd_max, 1)} kPa`,
            y: plotY(last.humidity_low),
        },
    ];
}

function coords(points) {
    return points.map(p => `${round1(p.x)},${round1(p.y)}`).join(' ');
}

/** The last `TRAIL_POINTS` hourly readings, oldest first, as plot points. */
function trailPoints(rows, current) {
    const usable = (rows || [])
        .filter(hasReading)
        .sort((a, b) => parseLocalDatetime(a.datetime) - parseLocalDatetime(b.datetime))
        .slice(-TRAIL_POINTS)
        .map(row => ({ x: plotX(row.temperature), y: plotY(row.humidity) }));
    if (hasReading(current)) {
        usable.push({ x: plotX(current.temperature), y: plotY(current.humidity) });
    }
    return usable;
}

/** Where the dot sits, as percentages of the whole figure, for the tooltip. */
function pointAnchor(current) {
    if (!hasReading(current)) return null;
    const x = plotX(current.temperature) - VIEW_BOX.x;
    const y = plotY(current.humidity) - VIEW_BOX.y;
    return {
        left: round1(x / VIEW_BOX.width * 100),
        top: round1(y / VIEW_BOX.height * 100),
    };
}

/** Below this rendered width (CSS px) the text is scaled and the ticks thinned. */
const NARROW_PX = 480;
/** What the axis and band labels should measure on the screen, in CSS px. */
const LABEL_PX = 11;

/**
 * The SVG for the whole figure, as a string.
 *
 * `width` is the rendered width in CSS px. The viewBox is fixed, so on a narrow
 * screen the same text is scaled down with the plot: it is set to the size
 * that lands at LABEL_PX on screen, and the ticks are thinned so it fits.
 *
 * @param {{band: ?object, current: ?object, trail: ?Array}} view
 * @param {number} width
 */
function renderChartSvg({ band, current, trail }, width = VIEW_BOX.width) {
    const parts = [];
    const narrow = width < NARROW_PX;
    const labelUnits = narrow ? Math.round(LABEL_PX * (VIEW_BOX.width / width) * 10) / 10 : null;
    const labelStyle = labelUnits ? ` style="font-size:${labelUnits}px"` : '';

    for (let h = PLOT.hMin; h <= PLOT.hMax; h += 10) {
        const y = plotY(h).toFixed(1);
        parts.push(`<line class="ac-chart__grid" x1="0" y1="${y}" x2="${PLOT.width}" y2="${y}"></line>`);
    }
    for (let t = PLOT.tMin; t <= PLOT.tMax; t += 2) {
        const x = plotX(t).toFixed(1);
        parts.push(`<line class="ac-chart__grid" x1="${x}" y1="0" x2="${x}" y2="${PLOT.height}"></line>`);
    }

    const edges = band ? bandEdges(band.points) : { upper: [], lower: [] };
    if (edges.lower.length > 1) {
        // Upper edge left to right, then the lower edge back: one closed shape.
        const closed = [...edges.upper, ...[...edges.lower].reverse()];
        parts.push(`<polygon class="ac-chart__band" points="${coords(closed)}"></polygon>`);
        parts.push(`<polyline class="ac-chart__edge" points="${coords(edges.upper)}"></polyline>`);
        parts.push(`<polyline class="ac-chart__edge" points="${coords(edges.lower)}"></polyline>`);

        // Wide: the labels stand just past the right end, centred on their edge.
        // Narrow: they sit inside the band's right end, above the upper edge and
        // below the lower one, where the margin would be too small to hold them.
        bandLabels(band).forEach((label, index) => {
            const text = escapeHtml(label.text);
            if (narrow) {
                const y = index === 0 ? label.y - 5 : label.y + labelUnits + 3;
                parts.push(`<text class="ac-chart__axis ac-chart__axis--band" x="${PLOT.width - 6}" `
                    + `y="${y.toFixed(1)}" text-anchor="end"${labelStyle}>${text}</text>`);
            } else {
                parts.push(`<text class="ac-chart__axis ac-chart__axis--band" x="${PLOT.width + 6}" `
                    + `y="${(label.y + 4).toFixed(1)}"${labelStyle}>${text}</text>`);
            }
        });
    }

    const path = trailPoints(trail, current);
    if (path.length > 1) {
        parts.push(`<polyline class="ac-chart__trail" points="${coords(path)}"></polyline>`);
    }

    if (hasReading(current)) {
        const x = plotX(current.temperature).toFixed(1);
        const y = plotY(current.humidity).toFixed(1);
        const ok = current.vpd_in_range === true ? ' is-ok' : '';
        parts.push(`<circle class="ac-chart__halo" cx="${x}" cy="${y}" r="14"></circle>`);
        parts.push(`<circle class="ac-chart__point${ok}" cx="${x}" cy="${y}" r="7"></circle>`);
    } else {
        parts.push(`<text class="ac-chart__axis" x="${PLOT.width / 2}" y="${PLOT.height / 2}" `
            + `text-anchor="middle"${labelStyle}>Sin lectura actual</text>`);
    }

    // Narrow screens keep three temperatures and three humidities: enough to
    // read the axes by, without the labels running into each other.
    const xTicks = narrow ? [18, 25, 32] : [18, 20, 22, 24, 26, 28, 30, 32];
    for (const t of xTicks) {
        const label = t === PLOT.tMax ? `${t} °C` : `${t}`;
        parts.push(`<text class="ac-chart__axis" x="${plotX(t).toFixed(1)}" y="424" `
            + `text-anchor="middle"${labelStyle}>${label}</text>`);
    }
    const yTicks = narrow ? [30, 60, 90] : [30, 50, 70, 90];
    for (const h of yTicks) {
        const label = h === 90 ? '90 %' : `${h}`;
        parts.push(`<text class="ac-chart__axis" x="-10" y="${(plotY(h) + 4).toFixed(1)}" `
            + `text-anchor="end"${labelStyle}>${label}</text>`);
    }

    return `<svg viewBox="${VIEW_BOX.x} ${VIEW_BOX.y} ${VIEW_BOX.width} ${VIEW_BOX.height}" `
        + 'role="img" aria-label="Carta de temperatura contra humedad, con la franja donde el '
        + 'VPD de la hoja queda en rango para esta etapa">'
        + parts.join('') + '</svg>';
}

/**
 * What the tooltip says about the reading, as strings. Null without a reading.
 *
 * The leaf VPD and its temperature come from the control loop, which is the
 * one that decided the band; the air VPD is worked out here from the air.
 */
function tooltipModel(current, band) {
    if (!hasReading(current)) return null;

    const time = String(current.datetime || '').split(' ')[1] || '';
    const air = airVpd(current.temperature, current.humidity);
    const offset = band?.leaf_offset_c;
    const leafTemp = current.leaf_temperature;

    const hasTarget = Number.isFinite(band?.vpd_min) && Number.isFinite(band?.vpd_max);
    return {
        time: `Ahora · ${time ? time.slice(0, 5) : '--:--'}`,
        main: `${fmtComma(current.temperature, 1)} °C · ${fmtComma(current.humidity, 1)} %`,
        air: `${fmtComma(air, 2)} kPa`,
        leaf: Number.isFinite(current.leaf_vpd) ? `≈ ${fmtComma(current.leaf_vpd, 2)} kPa` : '--',
        leafOk: current.vpd_in_range === true,
        target: hasTarget ? `${fmtComma(band.vpd_min, 1)} – ${fmtComma(band.vpd_max, 1)}` : '--',
        note: Number.isFinite(leafTemp)
            ? `Hoja estimada en ${fmtComma(leafTemp, 1)} °C`
                + (Number.isFinite(offset) ? ` (aire − ${fmtComma(offset, 1)} °C)` : '')
            : '',
    };
}

// ===============================
// Page
// ===============================

const view = { band: null, current: null, trail: [], stage: null };
const dom = {};
/** Why the tooltip is open: hovering, focused, or pinned by a click. */
const open = { hover: false, focus: false, pinned: false };

function isOpen() {
    return open.hover || open.focus || open.pinned;
}

function syncTooltip() {
    const visible = isOpen() && !!view.current && hasReading(view.current);
    dom.tip.hidden = !visible;
    dom.point.setAttribute('aria-expanded', String(visible));
}

function closeTooltip() {
    open.hover = false;
    open.focus = false;
    open.pinned = false;
    syncTooltip();
}

function fillTooltip() {
    const model = tooltipModel(view.current, view.band);
    if (!model) return;
    dom.time.textContent = model.time;
    dom.main.textContent = model.main;
    dom.air.textContent = model.air;
    dom.leaf.textContent = model.leaf;
    dom.leaf.classList.toggle('is-warn', !model.leafOk);
    dom.target.textContent = model.target;
    dom.note.textContent = model.note;
}

function renderVpdChart() {
    if (!dom.plot) return;

    dom.plot.innerHTML = renderChartSvg(view, dom.plot.clientWidth || VIEW_BOX.width);

    const anchor = pointAnchor(view.current);
    dom.point.hidden = !anchor;
    if (anchor) {
        dom.point.style.left = `${anchor.left}%`;
        dom.point.style.top = `${anchor.top}%`;
        // The tooltip sits beside the dot, on the side with room for it.
        dom.tip.style.setProperty('--tip-x', `${anchor.left}%`);
        dom.tip.style.setProperty('--tip-y', `${anchor.top}%`);
        dom.tip.classList.toggle('is-left', anchor.left > TIP_FLIP_AT);
    }
    fillTooltip();
    syncTooltip();
}

/** The band for the stage the tent is in. */
function fetchVpdBand() {
    return apiGet('/api/vpd-band', { key: 'vpd-band' }).then(data => {
        // null is also what a request de-duplicated away returns: keep the band
        // already on screen rather than blanking the chart.
        if (!data || data.error) return;
        view.band = data;
        renderVpdChart();
    });
}

/**
 * A new reading. A change of stage also changes the band, so the old band is
 * dropped now and the new one asked for, rather than shown for the new stage.
 */
function setVpdCurrent(data) {
    view.current = data || null;
    if (data?.stage && data.stage !== view.stage) {
        view.stage = data.stage;
        view.band = null;
        fetchVpdBand();
    }
    renderVpdChart();
}

/** Hourly rows from /api/history/aggregated; only the last few are drawn. */
function setVpdTrail(rows) {
    view.trail = rows || [];
    renderVpdChart();
}

function wireVpdChart() {
    dom.figure = document.getElementById('vpd-chart');
    if (!dom.figure) return;
    dom.plot = document.getElementById('vpd-chart-plot');
    dom.point = document.getElementById('vpd-chart-point');
    dom.tip = document.getElementById('vpd-chart-tip');
    dom.time = document.getElementById('vpd-tip-time');
    dom.main = document.getElementById('vpd-tip-main');
    dom.air = document.getElementById('vpd-tip-air');
    dom.leaf = document.getElementById('vpd-tip-leaf');
    dom.target = document.getElementById('vpd-tip-target');
    dom.note = document.getElementById('vpd-tip-note');

    dom.point.addEventListener('mouseenter', () => { open.hover = true; syncTooltip(); });
    dom.point.addEventListener('mouseleave', () => { open.hover = false; syncTooltip(); });
    dom.point.addEventListener('focus', () => { open.focus = true; syncTooltip(); });
    dom.point.addEventListener('blur', () => { open.focus = false; syncTooltip(); });
    // A tap on a phone is a click; it pins the tooltip so it stays while the
    // finger is gone, and a second tap lets it go.
    dom.point.addEventListener('click', () => { open.pinned = !open.pinned; syncTooltip(); });

    dom.figure.addEventListener('keydown', event => {
        if (event.key !== 'Escape') return;
        closeTooltip();
        dom.point.focus();
    });
    document.getElementById('vpd-tip-close')?.addEventListener('click', () => {
        closeTooltip();
        dom.point.focus();
    });

    renderVpdChart();
    fetchVpdBand();
    // The plot is drawn at the width it has, so a turn of the phone redraws it.
    window.addEventListener('resize', renderVpdChart);
}

export {
    PLOT, VIEW_BOX, airVpd, bandLabels, bandEdges, renderChartSvg, tooltipModel, pointAnchor,
    fetchVpdBand, setVpdCurrent, setVpdTrail, wireVpdChart,
};
