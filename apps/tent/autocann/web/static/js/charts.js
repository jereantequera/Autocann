import { config, state } from './state.js';
import { formatDate, parseLocalDatetime } from './util.js';
import { withAlpha } from './stats-math.js';
import { applyPlan, gapPlan } from './chartgaps.js';

/** A marker further than this from any plotted point is not drawn. */
const STAGE_MARKER_TOLERANCE_SECONDS = 12 * 3600;

const STAGE_SHORT_NAMES = {
    seedling: 'Plántula',
    early_veg: 'Veg. temprano',
    late_veg: 'Veg. tardío',
    flowering: 'Floración',
    dry: 'Secado',
};

/**
 * The history charts.
 *
 * Chart instances stay module-private; other modules reach them through the
 * accessors at the bottom instead of sharing globals.
 *
 * Colours come from the design tokens, not from literals here, and they are
 * read again whenever data-theme changes. Chart.js bakes the colour into the
 * canvas, so a theme switch that only restyled the page would leave the charts
 * in the day palette on the night tablet, which is the one screen that must
 * not light the tent.
 */

let tempHumidityChart, vpdChart, comparisonChart;

/** What is currently plotted, so markers can be placed on the same axis. */
let lastPlotted = { labels: [], timestamps: [] };

/** Colours as the current theme spells them; null until first read. */
let palette = null;

/** Canvases in the order the history view lists them. */
const HISTORY_CANVASES = ['tempHumidityChart', 'vpdChart', 'comparisonChart'];

function readPalette() {
    const style = getComputedStyle(document.documentElement);
    const token = (name, fallback) => style.getPropertyValue(name).trim() || fallback;
    return {
        temp: token('--data-temp', '#E8B07A'),
        water: token('--data-water', '#7FA7D9'),
        outside: token('--data-outside', '#9C9B94'),
        ok: token('--state-ok', '#8FC79A'),
        warn: token('--state-warn', '#E3A24F'),
        grid: token('--hairline-faint', '#232623'),
        tick: token('--fg-subtle', '#878A82'),
        hairline: token('--hairline', '#2B2E2B'),
        fg: token('--fg', '#ECEBE6'),
        fgSecondary: token('--fg-secondary', '#C9C8C1'),
        fgMuted: token('--fg-muted', '#9C9B94'),
        overlay: token('--surface-overlay', '#1C1F1C'),
        mono: token('--font-mono', 'monospace'),
        sans: token('--font-sans', 'sans-serif'),
    };
}

function currentPalette() {
    if (!palette) palette = readPalette();
    return palette;
}

/**
 * Tooltip and axis styling shared by all three charts.
 *
 * Legends are not Chart.js legends: they are HTML in the page, so they can be
 * styled as the rest of the legend and so a hidden series is simply not listed.
 */
function baseOptions(p) {
    const axis = {
        grid: { color: p.grid },
        ticks: { color: p.tick, font: { size: 12, family: p.mono } },
    };
    return {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        parsing: false, normalized: true, // points are {x: index, y}
        interaction: { mode: 'index', intersect: false },
        plugins: {
            legend: { display: false },
            decimation: { enabled: true, algorithm: 'min-max', samples: 200 }, // keeps each bucket's extremes
            tooltip: {
                backgroundColor: p.overlay,
                borderColor: p.hairline,
                borderWidth: 1,
                cornerRadius: 4,
                titleColor: p.fg,
                bodyColor: p.fgMuted,
                padding: 12,
                titleFont: { size: 13, weight: '500', family: p.sans },
                bodyFont: { size: 13, family: p.mono },
            },
        },
        scales: {
            x: { ...axis, ticks: { ...axis.ticks, maxRotation: 0, autoSkipPadding: 16 } },
            y: axis,
        },
    };
}

/** A line series in a token colour: no fill, no smoothing, no glow. */
function line(label, color, yAxisID, extra = {}) {
    return {
        label,
        data: [],
        borderColor: color,
        backgroundColor: 'transparent',
        borderWidth: 2,
        tension: 0,
        fill: false,
        spanGaps: false,
        yAxisID,
        pointRadius: 0,
        pointHoverRadius: 4,
        ...extra,
    };
}

function initCharts() {
    const p = currentPalette();

    tempHumidityChart = new Chart(document.getElementById('tempHumidityChart').getContext('2d'), {
        type: 'line',
        data: {
            labels: [],
            datasets: [
                line('Temperatura (°C)', p.temp, 'y'),
                line('Humedad (%)', p.water, 'y1'),
                line('Humedad objetivo (%)', p.ok, 'y1', { borderDash: [4, 4] }),
                // Min/max envelope. Each stored row summarises ~100 readings, so
                // the average alone hides the swing, and the swing is what stresses
                // the plants. The lower bound is drawn invisibly and the upper one
                // fills down to it, which is the only fill on this chart.
                line('Mín. temperatura', 'transparent', 'y', { borderWidth: 0, order: 10 }),
                line('Rango de temperatura', 'transparent', 'y', {
                    borderWidth: 0, order: 10, fill: '-1',
                    backgroundColor: withAlpha(p.temp, 0.12),
                }),
            ],
        },
        options: {
            ...baseOptions(p),
            scales: {
                ...baseOptions(p).scales,
                y: { ...baseOptions(p).scales.y, position: 'left',
                     ticks: { ...baseOptions(p).scales.y.ticks, callback: v => `${v}°` } },
                y1: { ...baseOptions(p).scales.y, position: 'right', grid: { drawOnChartArea: false },
                      ticks: { ...baseOptions(p).scales.y.ticks, callback: v => `${v}%` } },
            },
        },
    });

    vpdChart = new Chart(document.getElementById('vpdChart').getContext('2d'), {
        type: 'line',
        data: {
            labels: [],
            datasets: [line('VPD de hoja (kPa)', p.warn, 'y')],
        },
        options: {
            ...baseOptions(p),
            plugins: {
                ...baseOptions(p).plugins,
                annotation: {
                    annotations: {
                        // grows.js moves this box when the stage changes, so its
                        // key is part of the contract, not a detail of this file.
                        optimalZone: {
                            type: 'box',
                            yMin: config.vpdRanges[state.stage].min,
                            yMax: config.vpdRanges[state.stage].max,
                            backgroundColor: withAlpha(p.ok, 0.13),
                            borderWidth: 0,
                        },
                    },
                },
            },
            scales: {
                ...baseOptions(p).scales,
                y: { ...baseOptions(p).scales.y, min: 0, max: 2.0,
                     ticks: { ...baseOptions(p).scales.y.ticks, callback: v => `${v}` } },
            },
        },
    });

    // Indoor against outdoor temperature. Humidity is not drawn here: it is on
    // the chart above, and the outdoor humidity is in the readout.
    comparisonChart = new Chart(document.getElementById('comparisonChart').getContext('2d'), {
        type: 'line',
        data: {
            labels: [],
            datasets: [
                line('Adentro (°C)', p.temp, 'y'),
                line('Afuera (°C)', p.outside, 'y', { borderDash: [4, 4] }),
            ],
        },
        options: {
            ...baseOptions(p),
            scales: {
                ...baseOptions(p).scales,
                y: { ...baseOptions(p).scales.y, ticks: { ...baseOptions(p).scales.y.ticks, callback: v => `${v}°` } },
            },
        },
    });

    // The palette is read once per theme change, not once per chart.
    new MutationObserver(() => {
        palette = readPalette();
        paintCharts();
    }).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
}

/**
 * Put the current palette into every chart that exists.
 *
 * Only the colours are written. Writing the whole options object would drop
 * the stage markers and the optimal zone, which live in it.
 */
function paintCharts() {
    const p = currentPalette();
    const paintAxis = (axis, color) => {
        if (!axis) return;
        axis.grid.color = p.grid;
        axis.ticks.color = p.tick;
    };

    if (tempHumidityChart) {
        const o = tempHumidityChart.options;
        paintAxis(o.scales.x); paintAxis(o.scales.y); paintAxis(o.scales.y1);
        const ds = tempHumidityChart.data.datasets;
        ds[0].borderColor = p.temp;
        ds[1].borderColor = p.water;
        ds[2].borderColor = p.ok;
        ds[4].backgroundColor = withAlpha(p.temp, 0.12);
    }
    if (vpdChart) {
        paintAxis(vpdChart.options.scales.x); paintAxis(vpdChart.options.scales.y);
        vpdChart.data.datasets[0].borderColor = p.warn;
        const zone = vpdChart.options.plugins.annotation?.annotations?.optimalZone;
        if (zone) zone.backgroundColor = withAlpha(p.ok, 0.13);
    }
    if (comparisonChart) {
        const ds = comparisonChart.data.datasets;
        ds[0].borderColor = p.temp;
        ds[1].borderColor = p.outside;
        paintAxis(comparisonChart.options.scales.x); paintAxis(comparisonChart.options.scales.y);
    }
    [tempHumidityChart, vpdChart, comparisonChart].filter(Boolean).forEach(chart => chart.update('none'));
}

/**
 * Re-measure the charts of whichever view just became visible.
 *
 * A canvas built inside a `display: none` section has no size, and Chart.js
 * only notices once something tells it to look again.
 */
function resizeCharts() {
    [tempHumidityChart, vpdChart, comparisonChart].filter(Boolean).forEach(chart => chart.resize());
}

/**
 * Say why a chart is blank instead of drawing an empty grid, or clear it.
 *
 * @param {?string} message  null once there is something to plot
 */
function setChartEmpty(canvasId, message) {
    const wrapper = document.getElementById(canvasId)?.closest('.ac-chart');
    if (!wrapper) return;
    wrapper.classList.toggle('is-empty', !!message);
    if (message) wrapper.dataset.empty = message;
    else delete wrapper.dataset.empty;
}

/**
 * Show or hide a legend entry along with the series it names.
 *
 * A series with no values (the envelope on rows written before it existed, the
 * target on aggregated rows) is not in the legend either, so the legend never
 * names a line that is not on the chart.
 */
function setLegendItem(key, present) {
    document.querySelectorAll(`[data-series="${key}"]`).forEach(item => {
        item.hidden = !present;
    });
}

function updateHistoricalCharts(data) {
    const empty = !data || !data.data || data.data.length === 0;
    // It used to return here and leave the previous range plotted: picking a
    // week with no samples kept showing the day before, under the new title.
    HISTORY_CANVASES.forEach(id => setChartEmpty(id,
        empty ? 'Sin muestras en este período' : null));
    if (empty) {
        ['band', 'target'].forEach(key => setLegendItem(key, false));
        return;
    }

    const sortedData = [...data.data].sort(
        (a, b) => parseLocalDatetime(a.datetime) - parseLocalDatetime(b.datetime));

    // A hole in the samples becomes a null row, so every line breaks there.
    const stamps = sortedData.map(d => Math.floor(parseLocalDatetime(d.datetime).getTime() / 1000));
    const plan = gapPlan(stamps);
    const points = values => applyPlan(plan, values).map((y, x) => ({ x, y }));
    const labels = applyPlan(plan, sortedData.map(d => formatDate(d.datetime,
        { hours: state.hours, interval: state.interval })));
    const temperatures = points(sortedData.map(d => d.temperature));
    const humidities = points(sortedData.map(d => d.humidity));
    // The band is about the leaf, so the curve is too; the air value stands in
    // only for rows that have no leaf sensor, as the VPD score does.
    const vpds = points(sortedData.map(d => d.leaf_vpd ?? d.vpd));
    const outsideTemps = points(sortedData.map(d => d.outside_temperature));

    lastPlotted = { labels, timestamps: applyPlan(plan, stamps, NaN) };

    // Rows written before the interval summary existed carry no min/max; the
    // band is hidden rather than drawn as a flat line on top of the average.
    const hasBand = sortedData.some(d => d.temperature_min != null);
    const hasTarget = sortedData.some(d => d.target_humidity != null);

    const top = tempHumidityChart.data;
    top.labels = labels;
    top.datasets[0].data = temperatures;
    top.datasets[1].data = humidities;
    top.datasets[2].data = hasTarget ? points(sortedData.map(d => d.target_humidity)) : [];
    top.datasets[3].data = hasBand ? points(sortedData.map(d => d.temperature_min)) : [];
    top.datasets[4].data = hasBand ? points(sortedData.map(d => d.temperature_max)) : [];
    top.datasets[2].hidden = !hasTarget;
    top.datasets[3].hidden = !hasBand;
    top.datasets[4].hidden = !hasBand;
    tempHumidityChart.update();
    setLegendItem('band', hasBand);
    setLegendItem('target', hasTarget);

    vpdChart.data.labels = labels;
    vpdChart.data.datasets[0].data = vpds;
    vpdChart.update();

    comparisonChart.data.labels = labels;
    comparisonChart.data.datasets[0].data = temperatures;
    comparisonChart.data.datasets[1].data = outsideTemps;
    comparisonChart.update();
}

/**
 * The VPD chart, for callers that need to restyle its target-band annotation
 * when the stage changes. Returns null before initCharts() has run.
 */
function getVpdChart() {
    return vpdChart || null;
}

/**
 * Draw a vertical marker on each chart wherever the stage changed.
 *
 * Seeing a stage change next to the VPD curve explains jumps that otherwise
 * look inexplicable: the target band moves, so the controller starts aiming
 * somewhere else.
 *
 * @param {Array} timeline  periods from /api/grows/<id>/timeline
 * @param {Array<string>} labels  the x-axis labels currently plotted
 * @param {Array<number>} timestamps  epoch seconds matching those labels
 */
function renderStageMarkers(timeline, labels = lastPlotted.labels,
                            timestamps = lastPlotted.timestamps) {
    const charts = [tempHumidityChart, vpdChart, comparisonChart].filter(Boolean);
    if (!charts.length || !labels || !labels.length) return;
    const p = currentPalette();

    const markers = {};
    (timeline || []).forEach((period, index) => {
        // Skip the first period: its start is the start of the grow, not a change.
        if (index === 0 || !Number.isFinite(period.started_at)) return;

        // Snap to the nearest plotted point; a change outside the visible range
        // has no place to sit, so it is left out.
        let nearest = -1;
        let best = Infinity;
        timestamps.forEach((ts, i) => {
            const distance = Math.abs(ts - period.started_at);
            if (distance < best) { best = distance; nearest = i; }
        });
        if (nearest < 0 || best > STAGE_MARKER_TOLERANCE_SECONDS) return;

        markers[`stage_${index}`] = {
            type: 'line',
            xMin: nearest,
            xMax: nearest,
            borderColor: withAlpha(p.fgSecondary, 0.5),
            borderWidth: 1,
            borderDash: [4, 4],
            label: {
                display: true,
                content: STAGE_SHORT_NAMES[period.stage] || period.stage,
                position: 'start',
                backgroundColor: p.overlay,
                color: p.fgSecondary,
                font: { size: 11, family: p.sans },
                padding: { x: 6, y: 3 },
            },
        };
    });

    charts.forEach(chart => {
        const plugins = chart.options.plugins;
        // chart.options is a resolver proxy in Chart.js v4. Writing a value the
        // proxy just resolved back into it (`x.a = x.a || {}`) makes the setter
        // recurse into itself until the stack blows up, so only ever assign when
        // the key is genuinely missing.
        if (!plugins.annotation) {
            plugins.annotation = { annotations: {} };
        } else if (!plugins.annotation.annotations) {
            plugins.annotation.annotations = {};
        }
        const annotations = plugins.annotation.annotations;
        // Replace previous markers without disturbing the VPD optimal zone.
        Object.keys(annotations)
            .filter(key => key.startsWith('stage_'))
            .forEach(key => delete annotations[key]);
        Object.assign(annotations, markers);
        chart.update('none');
    });
}

export {
    initCharts, updateHistoricalCharts, resizeCharts, getVpdChart,
    renderStageMarkers, setChartEmpty,
};
