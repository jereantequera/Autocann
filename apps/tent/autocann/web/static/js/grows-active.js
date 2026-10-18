/**
 * The active grow: fetched from the server, then painted in the sidebar, the
 * Cultivo header, and the VPD chart's optimal zone.
 */

import { apiGet } from './api.js';
import { getVpdChart } from './charts.js';
import { config, state } from './state.js';
import { paintTent } from './sidebar.js';
import { growState } from './grows-state.js';
import { fetchStageTimeline, renderCultivo, renderDayCounters } from './grows-stagebar.js';

// Grow management

function updateActiveGrow(grow) {
    if (!grow) return;
    
    document.getElementById('grow-name').textContent = grow.name;
    const detailName = document.getElementById('grow-detail-name');
    if (detailName) detailName.textContent = grow.name;
    state.stage = grow.stage;
    
    const stageElement = document.getElementById('grow-stage');
    const stageNames = config.stageNames;
    
    stageElement.textContent = stageNames[grow.stage] || grow.stage;
    stageElement.className = `grow-stage ${grow.stage}`;
    
    renderDayCounters(grow);
    // The collapsed bar shows the tent as a monogram, and its title and
    // accessible name carry the same words as the expanded block.
    paintTent({
        name: grow.name,
        stage: stageNames[grow.stage] || grow.stage,
        day: grow.day_in_stage,
        expected: grow.stage_expected_days,
    });
    growState.activeGrow = grow;
    renderCultivo();

    // Update VPD chart optimal zone
    const vpdChart = getVpdChart();
    if (vpdChart && config.vpdRanges[state.stage]) {
        const range = config.vpdRanges[state.stage];
        vpdChart.options.plugins.annotation.annotations.optimalZone.yMin = range.min;
        vpdChart.options.plugins.annotation.annotations.optimalZone.yMax = range.max;
        vpdChart.update();
    }
}

export function fetchActiveGrow() {
    apiGet('/api/grows/active').then(data => {
        if (!data) return;
        updateActiveGrow(data);
        fetchStageTimeline(data.id);
    });
}
