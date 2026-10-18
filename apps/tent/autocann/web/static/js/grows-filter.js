/**
 * The grow scope selector in Estadísticas.
 */

import { apiGet } from './api.js';
import { fetchHistoricalData, fetchPeriodSummary } from './dashboard.js';
import { fetchVpdScore } from './analytics.js';
import { state } from './state.js';
import { escapeHtml } from './util.js';

// ---------------------------------------------------------------------------
// The grow scope selector in Estadísticas
// ---------------------------------------------------------------------------
// The history is filtered by grow, not only by time. A 30- or 90-day window
// used to reach back past the end of the previous crop and draw ten months of
// an empty tent and a real grow as one continuous curve.
//
// The default is the active grow — everything else on screen is about that
// grow, from the stage to the day counter — and "todos los cultivos" is still
// there, as something you pick.

const ALL_GROWS_OPTION = 'all';

/** Fill the selector from the grow list, keeping whatever is selected. */
export function renderGrowFilter(grows) {
    const select = document.getElementById('grow-filter');
    if (!select) return;

    const options = [
        '<option value="">Cultivo activo</option>',
        ...(grows || []).map(grow => {
            const id = Number(grow.id);
            const samples = Number.isFinite(grow.sample_count)
                ? ` · ${grow.sample_count.toLocaleString('es-AR')} muestras` : '';
            return `<option value="${id}">${escapeHtml(grow.name)}`
                + `${grow.is_active ? ' (activo)' : ''}${samples}</option>`;
        }),
        `<option value="${ALL_GROWS_OPTION}">Todos los cultivos</option>`,
    ];

    select.innerHTML = options.join('');
    // Re-selecting after innerHTML: the list is refetched on every visit, and
    // losing the choice would quietly take the charts back to the active grow.
    select.value = state.growId === null ? '' : String(state.growId);
    if (select.value !== (state.growId === null ? '' : String(state.growId))) {
        // The chosen grow is gone from the list; fall back rather than show a
        // selector that disagrees with the charts.
        state.growId = null;
        select.value = '';
    }
}

export function fetchGrowFilter() {
    apiGet('/api/grows', { key: 'grow-filter' }).then(data => {
        if (data && data.grows) renderGrowFilter(data.grows);
    });
}

export function wireGrowFilter() {
    const select = document.getElementById('grow-filter');
    if (!select) return;

    select.addEventListener('change', () => {
        const value = select.value;
        state.growId = value === '' ? null
            : value === ALL_GROWS_OPTION ? ALL_GROWS_OPTION : Number(value);
        // Everything in this view reads the same scope, so all of it is
        // refetched together: two charts disagreeing about which grow they are
        // drawing is the exact confusion this selector exists to end.
        fetchHistoricalData(state.period, state.interval, state.hours);
        fetchPeriodSummary();
        fetchVpdScore();
    });

    document.addEventListener('view:enter', event => {
        if (event.detail?.view === 'stats') fetchGrowFilter();
    });
    fetchGrowFilter();
}
