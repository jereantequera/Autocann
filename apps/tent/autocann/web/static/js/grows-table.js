/**
 * The list of all grows in the Cultivo view.
 */

import { config } from './state.js';
import { escapeHtml } from './util.js';
import { durationLabel, formatShortDate, growDays } from './grow-model.js';

export async function loadGrowsList() {
    const growListElement = document.getElementById('growList');
    growListElement.innerHTML = '<div class="loading">Cargando cultivos</div>';

    try {
        const response = await fetch('/api/grows');
        const data = await response.json();

        const badge = document.getElementById('grow-count-badge');

        if (data.grows && data.grows.length > 0) {
            // The names come from /api/config like everywhere else; this used to
            // keep a fourth copy of the same four strings.
            const stageNames = config.stageNames;

            if (badge) {
                badge.textContent = `${data.grows.length} `
                    + `${data.grows.length === 1 ? 'cultivo' : 'cultivos'}`;
            }

            // Every interpolated value is escaped: names, dates and the
            // stage come straight from the database. Riegos is not in the
            // grow list, so the table has no column for it.
            const rows = data.grows.map(grow => {
                const id = Number(grow.id);
                const samples = Number.isFinite(grow.sample_count)
                    ? grow.sample_count.toLocaleString('es-AR') : '--';
                const days = growDays(grow.start_date, grow.end_date);
                // The row is the same control as its "Activar" button, so a
                // click anywhere on an inactive grow still activates it.
                return `
                <tr class="${grow.is_active ? 'is-active' : ''}"
                    data-grow-id="${id}"
                    ${grow.is_active ? '' : 'data-action="activate"'}>
                    <td>
                        <span class="grow-name-cell">
                            <span class="ac-dot ${grow.is_active ? 'ac-dot--ok' : 'ac-dot--idle'}"></span>
                            ${escapeHtml(grow.name)}
                        </span>
                    </td>
                    <td>${escapeHtml(stageNames[grow.stage] || grow.stage)}</td>
                    <td class="ac-num">${escapeHtml(formatShortDate(grow.start_date))}</td>
                    <td class="ac-num">${escapeHtml(durationLabel(days))}</td>
                    <td class="ac-num">${escapeHtml(samples)}</td>
                    <td class="grow-row-actions">
                        ${grow.is_active
                            ? '<span class="ac-field__hint">activo</span>'
                            : `<button class="ac-btn ac-btn--ghost" data-action="activate"
                                       data-grow-id="${id}">Activar</button>`}
                        ${!grow.end_date && !grow.is_active ? `
                            <button class="ac-btn ac-btn--ghost grow-end" data-action="end"
                                    data-grow-id="${id}">Finalizar</button>
                        ` : ''}
                    </td>
                </tr>`;
            }).join('');

            growListElement.innerHTML = `
                <table class="ac-table grow-table">
                    <thead>
                        <tr>
                            <th>Nombre</th><th>Etapa</th><th>Inicio</th>
                            <th>Duración</th><th>Muestras</th><th></th>
                        </tr>
                    </thead>
                    <tbody>${rows}</tbody>
                </table>`;
        } else {
            if (badge) badge.textContent = 'ninguno';
            growListElement.innerHTML =
                '<div class="ac-empty">No hay cultivos registrados.</div>';
        }
    } catch (error) {
        console.error('Error loading grows:', error);
        growListElement.innerHTML =
            '<div class="ac-empty">No se pudieron cargar los cultivos.</div>';
    }
}
