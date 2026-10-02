import { config, state } from './state.js';

/** A marker further than this from any plotted point is not drawn. */
const STAGE_MARKER_TOLERANCE_SECONDS = 12 * 3600;

const STAGE_SHORT_NAMES = {
    early_veg: 'Veg. temprano',
    late_veg: 'Veg. tardío',
    flowering: 'Floración',
    dry: 'Secado',
};
import { formatDate, parseLocalDatetime } from './util.js';

/**
 * The three history charts.
 *
 * Chart instances stay module-private; other modules reach them through the
 * accessors at the bottom instead of sharing globals.
 */

const chartColors = {
    temp: { border: '#f97316', bg: 'rgba(249, 115, 22, 0.1)' },
    humidity: { border: '#3b82f6', bg: 'rgba(59, 130, 246, 0.1)' },
    targetHumidity: { border: '#22c55e', bg: 'transparent' },
    vpd: { border: '#a855f7', bg: 'rgba(168, 85, 247, 0.1)' },
    outsideTemp: { border: '#fbbf24', bg: 'transparent' },
    outsideHumidity: { border: '#14b8a6', bg: 'transparent' }
};

const chartDefaults = {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
        legend: {
            display: true,
            position: 'top',
            labels: {
                // The envelope's lower bound exists only to fill against; it is
                // not a series anyone wants to toggle.
                filter: item => item.text !== 'Mín. temperatura',
                color: '#8fa396',
                padding: 16,
                font: { size: 12, family: 'Outfit' },
                usePointStyle: true,
                pointStyle: 'circle'
            }
        },
        tooltip: {
            backgroundColor: '#151d19',
            borderColor: '#243028',
            borderWidth: 1,
            titleColor: '#e8f0eb',
            bodyColor: '#8fa396',
            padding: 12,
            titleFont: { size: 13, weight: 'bold', family: 'Outfit' },
            bodyFont: { size: 12, family: 'JetBrains Mono' }
        }
    },
    scales: {
        x: {
            grid: { color: 'rgba(36, 48, 40, 0.5)' },
            ticks: { color: '#5a6b5f', font: { size: 11 }, maxRotation: 45 }
        },
        y: {
            grid: { color: 'rgba(36, 48, 40, 0.5)' },
            ticks: { color: '#5a6b5f', font: { size: 11, family: 'JetBrains Mono' } }
        }
    }
};

let tempHumidityChart, vpdChart, comparisonChart;

/** What is currently plotted, so markers can be placed on the same axis. */
let lastPlotted = { labels: [], timestamps: [] };

// VPD ranges per stage

function initCharts() {
    // Temperature & Humidity Chart
    const ctx1 = document.getElementById('tempHumidityChart').getContext('2d');
    tempHumidityChart = new Chart(ctx1, {
        type: 'line',
        data: {
            labels: [],
            datasets: [
                {
                    label: 'Temperatura (°C)',
                    data: [],
                    borderColor: chartColors.temp.border,
                    backgroundColor: chartColors.temp.bg,
                    borderWidth: 2,
                    tension: 0.4,
                    fill: true,
                    yAxisID: 'y',
                    pointRadius: 0,
                    pointHoverRadius: 4
                },
                {
                    label: 'Humedad (%)',
                    data: [],
                    borderColor: chartColors.humidity.border,
                    backgroundColor: chartColors.humidity.bg,
                    borderWidth: 2,
                    tension: 0.4,
                    fill: true,
                    yAxisID: 'y1',
                    pointRadius: 0,
                    pointHoverRadius: 4
                },
                {
                    label: 'Humedad Objetivo (%)',
                    data: [],
                    borderColor: chartColors.targetHumidity.border,
                    backgroundColor: chartColors.targetHumidity.bg,
                    borderWidth: 2,
                    borderDash: [5, 5],
                    tension: 0.4,
                    fill: false,
                    yAxisID: 'y1',
                    pointRadius: 0,
                    pointHoverRadius: 4
                },
                // Min/max envelope. Each stored row summarises ~100 readings, so
                // the average alone hides the swing — and the swing is what
                // stresses the plants. The lower bound is drawn invisibly and
                // the upper one fills down to it to shade the band.
                {
                    label: 'Mín. temperatura',
                    data: [],
                    borderColor: 'transparent',
                    backgroundColor: 'transparent',
                    borderWidth: 0,
                    pointRadius: 0,
                    tension: 0.4,
                    fill: false,
                    yAxisID: 'y',
                    order: 10,
                },
                {
                    label: 'Rango de temperatura',
                    data: [],
                    borderColor: 'transparent',
                    backgroundColor: 'rgba(249, 115, 22, 0.13)',
                    borderWidth: 0,
                    pointRadius: 0,
                    tension: 0.4,
                    fill: '-1',
                    yAxisID: 'y',
                    order: 10,
                }
            ]
        },
        options: {
            ...chartDefaults,
            scales: {
                ...chartDefaults.scales,
                y: {
                    type: 'linear',
                    display: true,
                    position: 'left',
                    grid: { color: 'rgba(36, 48, 40, 0.5)' },
                    ticks: { color: chartColors.temp.border, font: { size: 11, family: 'JetBrains Mono' } },
                    title: { display: true, text: '°C', color: chartColors.temp.border }
                },
                y1: {
                    type: 'linear',
                    display: true,
                    position: 'right',
                    grid: { drawOnChartArea: false },
                    ticks: { color: chartColors.humidity.border, font: { size: 11, family: 'JetBrains Mono' } },
                    title: { display: true, text: '%', color: chartColors.humidity.border }
                }
            }
        }
    });

    // VPD Chart with optimal range band
    const ctx2 = document.getElementById('vpdChart').getContext('2d');
    vpdChart = new Chart(ctx2, {
        type: 'line',
        data: {
            labels: [],
            datasets: [{
                label: 'VPD (kPa)',
                data: [],
                borderColor: chartColors.vpd.border,
                backgroundColor: chartColors.vpd.bg,
                borderWidth: 2,
                tension: 0.4,
                fill: true,
                pointRadius: 0,
                pointHoverRadius: 4
            }]
        },
        options: {
            ...chartDefaults,
            plugins: {
                ...chartDefaults.plugins,
                annotation: {
                    annotations: {
                        optimalZone: {
                            type: 'box',
                            yMin: config.vpdRanges[state.stage].min,
                            yMax: config.vpdRanges[state.stage].max,
                            backgroundColor: 'rgba(34, 197, 94, 0.1)',
                            borderColor: 'rgba(34, 197, 94, 0.3)',
                            borderWidth: 1,
                            label: {
                                display: true,
                                content: 'Rango Óptimo',
                                position: 'start',
                                color: '#22c55e',
                                font: { size: 10 }
                            }
                        }
                    }
                }
            },
            scales: {
                ...chartDefaults.scales,
                y: {
                    ...chartDefaults.scales.y,
                    min: 0,
                    max: 2.0,
                    title: { display: true, text: 'kPa', color: chartColors.vpd.border }
                }
            }
        }
    });

    // Comparison Chart
    const ctx3 = document.getElementById('comparisonChart').getContext('2d');
    comparisonChart = new Chart(ctx3, {
        type: 'line',
        data: {
            labels: [],
            datasets: [
                {
                    label: 'Temp. Interior (°C)',
                    data: [],
                    borderColor: chartColors.temp.border,
                    backgroundColor: 'transparent',
                    borderWidth: 2,
                    tension: 0.4,
                    yAxisID: 'y',
                    pointRadius: 0,
                    pointHoverRadius: 4
                },
                {
                    label: 'Temp. Exterior (°C)',
                    data: [],
                    borderColor: chartColors.outsideTemp.border,
                    backgroundColor: 'transparent',
                    borderWidth: 2,
                    tension: 0.4,
                    yAxisID: 'y',
                    pointRadius: 0,
                    pointHoverRadius: 4
                },
                {
                    label: 'Hum. Interior (%)',
                    data: [],
                    borderColor: chartColors.humidity.border,
                    backgroundColor: 'transparent',
                    borderWidth: 2,
                    tension: 0.4,
                    yAxisID: 'y1',
                    pointRadius: 0,
                    pointHoverRadius: 4
                },
                {
                    label: 'Hum. Exterior (%)',
                    data: [],
                    borderColor: chartColors.outsideHumidity.border,
                    backgroundColor: 'transparent',
                    borderWidth: 2,
                    tension: 0.4,
                    yAxisID: 'y1',
                    pointRadius: 0,
                    pointHoverRadius: 4
                }
            ]
        },
        options: {
            ...chartDefaults,
            scales: {
                ...chartDefaults.scales,
                y: {
                    type: 'linear',
                    display: true,
                    position: 'left',
                    grid: { color: 'rgba(36, 48, 40, 0.5)' },
                    ticks: { color: '#5a6b5f', font: { size: 11, family: 'JetBrains Mono' } },
                    title: { display: true, text: '°C', color: '#5a6b5f' }
                },
                y1: {
                    type: 'linear',
                    display: true,
                    position: 'right',
                    grid: { drawOnChartArea: false },
                    ticks: { color: '#5a6b5f', font: { size: 11, family: 'JetBrains Mono' } },
                    title: { display: true, text: '%', color: '#5a6b5f' }
                }
            }
        }
    });
}

// "YYYY-MM-DD HH:MM:SS" is not a format the Date constructor has to
// accept; Safari returns Invalid Date for it. Parse it as local ISO.

function updateHistoricalCharts(data) {
    if (!data || !data.data || data.data.length === 0) return;

    const sortedData = [...data.data].sort(
        (a, b) => parseLocalDatetime(a.datetime) - parseLocalDatetime(b.datetime));
    
    const labels = sortedData.map(d => formatDate(d.datetime,
        { hours: state.hours, interval: state.interval }));
    const temperatures = sortedData.map(d => d.temperature);
    const humidities = sortedData.map(d => d.humidity);
    const targetHumidities = sortedData.map(d => d.target_humidity || null);
    const vpds = sortedData.map(d => d.vpd);
    const outsideTemps = sortedData.map(d => d.outside_temperature);
    const outsideHumidities = sortedData.map(d => d.outside_humidity);

    // Update charts
    // Kept so stage markers can be positioned against the same x-axis.
    lastPlotted = {
        labels,
        timestamps: sortedData.map(d => Math.floor(parseLocalDatetime(d.datetime).getTime() / 1000)),
    };

    // Rows written before the interval summary existed carry no min/max; the
    // band is hidden rather than drawn as a flat line on top of the average.
    const tempMins = sortedData.map(d => d.temperature_min ?? null);
    const tempMaxs = sortedData.map(d => d.temperature_max ?? null);
    const hasBand = tempMins.some(v => v !== null);

    tempHumidityChart.data.labels = labels;
    tempHumidityChart.data.datasets[0].data = temperatures;
    tempHumidityChart.data.datasets[1].data = humidities;
    tempHumidityChart.data.datasets[2].data = targetHumidities;
    tempHumidityChart.data.datasets[3].data = hasBand ? tempMins : [];
    tempHumidityChart.data.datasets[4].data = hasBand ? tempMaxs : [];
    tempHumidityChart.data.datasets[3].hidden = !hasBand;
    tempHumidityChart.data.datasets[4].hidden = !hasBand;
    tempHumidityChart.update();

    vpdChart.data.labels = labels;
    vpdChart.data.datasets[0].data = vpds;
    vpdChart.update();

    comparisonChart.data.labels = labels;
    comparisonChart.data.datasets[0].data = temperatures;
    comparisonChart.data.datasets[1].data = outsideTemps;
    comparisonChart.data.datasets[2].data = humidities;
    comparisonChart.data.datasets[3].data = outsideHumidities;
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
            borderColor: 'rgba(192, 132, 252, 0.7)',
            borderWidth: 2,
            borderDash: [5, 4],
            label: {
                display: true,
                content: STAGE_SHORT_NAMES[period.stage] || period.stage,
                position: 'start',
                backgroundColor: 'rgba(168, 85, 247, 0.85)',
                color: '#fff',
                font: { size: 10, family: 'Outfit' },
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

export { initCharts, updateHistoricalCharts, getVpdChart, renderStageMarkers };
