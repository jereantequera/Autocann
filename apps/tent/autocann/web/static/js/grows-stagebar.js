/**
 * Cultivo, the header and the stage bar: the day counters, the stage name, and
 * the bar of every stage with its real days, drawn from the timeline.
 */

import { apiGet } from './api.js';
import { renderStageMarkers } from './charts.js';
import { config, state } from './state.js';
import { escapeHtml } from './util.js';
import { formatLongDate, growLine, nextStage, stageBarModel } from './grow-model.js';
import { growState } from './grows-state.js';

function loadStageConfig() {
    if (growState.stageConfig) return Promise.resolve(growState.stageConfig);
    if (!growState.stageConfigRequest) {
        growState.stageConfigRequest = apiGet('/api/config').then(data => {
            if (!data || !Array.isArray(data.stages)) {
                // Not cached on failure: the next render asks again.
                growState.stageConfigRequest = null;
                return null;
            }
            growState.stageConfig = {
                order: data.stages,
                expected: data.stage_expected_days || null,
            };
            return growState.stageConfig;
        });
    }
    return growState.stageConfigRequest;
}

/**
 * The Cultivo header: which grow, since when, where it is in its stage, and
 * what the primary button will do next.
 */
export function renderCultivo() {
    const grow = growState.activeGrow;
    if (!grow) return;

    const kicker = document.getElementById('grow-since');
    const line = document.getElementById('grow-line');
    if (kicker) kicker.textContent = `Cultivo activo · desde el ${formatLongDate(grow.start_date)}`;
    if (line) {
        line.textContent = growLine({
            stageName: config.stageNames[grow.stage] || grow.stage,
            day: grow.day_in_stage,
            expected: grow.stage_expected_days,
            vpd: config.vpdRanges[grow.stage],
        });
    }

    // The stage order arrives with /api/config; until then the bar and the
    // next-stage button have nothing to draw from.
    loadStageConfig().then(stages => {
        if (!stages) return;
        const next = nextStage(stages.order, grow.stage);
        const button = document.getElementById('grow-next-stage');
        if (button) {
            button.hidden = !next;
            if (next) {
                button.dataset.stage = next;
                const name = config.stageNames[next] || next;
                button.textContent = `Pasar a ${name.toLocaleLowerCase('es-AR')}`;
            }
        }
        drawStageBar();
    });
}

/**
 * "Floración · día 12 de ~56" plus the progress bar.
 *
 * Days are calendar days counted by the server; the start date is day 1.
 */
export function renderDayCounters(grow) {
    const stageDay = document.getElementById('stage-day');
    const growDay = document.getElementById('grow-day');
    const progress = document.getElementById('stage-progress');
    const fill = document.getElementById('stage-progress-fill');
    const label = document.getElementById('stage-progress-label');
    if (!stageDay) return;

    const day = grow.day_in_stage;
    const expected = grow.stage_expected_days;

    if (!Number.isFinite(day)) {
        // A grow with no recorded history: say nothing rather than show "día --".
        stageDay.textContent = '';
        growDay.textContent = '';
        progress.hidden = true;
        return;
    }

    const overdue = Number.isFinite(expected) && day > expected;
    stageDay.textContent = expected ? `día ${day} de ~${expected}` : `día ${day}`;
    stageDay.classList.toggle('overdue', overdue);
    stageDay.title = overdue
        ? `Lleva ${day - expected} día(s) más que la duración típica de la etapa`
        : 'Día dentro de la etapa actual';

    growDay.textContent = Number.isFinite(grow.day_of_grow)
        ? `cultivo: día ${grow.day_of_grow}` : '';

    if (!Number.isFinite(expected)) {
        progress.hidden = true;
        return;
    }

    progress.hidden = false;
    const pct = Math.min((day / expected) * 100, 100);
    fill.style.width = `${pct}%`;
    fill.classList.toggle('overdue', overdue);

    if (Number.isFinite(grow.estimated_end)) {
        const end = new Date(grow.estimated_end * 1000);
        label.textContent = `fin estimado ${end.toLocaleDateString('es-AR', { day: '2-digit', month: 'short' })}`;
        label.title = 'Estimación: asume que las etapas restantes duran lo típico';
    } else {
        label.textContent = `${Math.round(pct)}%`;
        label.title = '';
    }
}

/** Stage history for the active grow: the chart markers and the stage bar. */
export function fetchStageTimeline(growId) {
    if (!Number.isFinite(growId)) return;
    apiGet(`/api/grows/${growId}/timeline`, { key: 'timeline' }).then(data => {
        if (!data || !data.timeline) return;
        state.timeline = data.timeline;
        growState.timelineData = data.timeline;
        renderStageMarkers(data.timeline);
        drawStageBar();
    });
}

/**
 * The stage bar: every stage of the cycle, its real days where the grow went
 * through it, and how far the current one has got.
 *
 * Drawn from whatever has arrived; it redraws when the timeline or the stage
 * order lands, so a late answer only fills in cells that were waiting.
 */
function drawStageBar() {
    const box = document.getElementById('grow-timeline');
    if (!box || !growState.activeGrow || !growState.stageConfig) return;

    const expected = { ...(growState.stageConfig.expected || {}) };
    if (Number.isFinite(growState.activeGrow.stage_expected_days)) {
        expected[growState.activeGrow.stage] = growState.activeGrow.stage_expected_days;
    }
    const cells = stageBarModel({
        order: growState.stageConfig.order,
        current: growState.activeGrow.stage,
        dayInStage: growState.activeGrow.day_in_stage,
        expected,
        timeline: growState.timelineData,
        names: config.stageNames,
    });

    box.style.gridTemplateColumns = cells.map(cell => `${cell.flex}fr`).join(' ');
    // --col places the bar segment; --row places the label row under the bar
    // on narrow screens (see cultivo.css). Desktop ignores both.
    box.innerHTML = cells.map((cell, index) => `
        <div class="ac-stage is-${cell.state}" style="--col: ${index + 1}; --row: ${index + 2}">
            <div class="ac-stage__bar"><div class="ac-stage__fill" style="width: ${cell.fill.toFixed(1)}%"></div></div>
            <div class="ac-stage__name">${escapeHtml(cell.name)}</div>
            <div class="ac-stage__meta">${escapeHtml(cell.meta)}</div>
        </div>`).join('');
}
