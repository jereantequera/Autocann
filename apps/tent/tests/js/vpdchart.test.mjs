/**
 * Tests for the VPD chart's geometry and tooltip (static/js/vpdchart.js).
 *
 * The chart is drawn from numbers that the control loop and the history
 * produce, so these tests pin the mapping from a reading to a point on the
 * plot, the clamping at the edges, and the text the tooltip shows.
 */

import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
    airVpd, bandEdges, bandLabels, pointAnchor, renderChartSvg, tooltipModel,
} from '../../autocann/web/static/js/vpdchart.js';

const band = {
    stage: 'early_veg',
    kind: 'vpd',
    vpd_min: 0.6,
    vpd_max: 1.0,
    leaf_offset_c: 1.5,
    temp_min: 18,
    temp_max: 32,
    points: [
        { temperature: 18, humidity_low: 60, humidity_high: 80 },
        { temperature: 32, humidity_low: 50, humidity_high: 70 },
    ],
};

const current = {
    temperature: 25,
    humidity: 50,
    leaf_temperature: 23.5,
    leaf_vpd: 1.21,
    vpd_in_range: true,
    datetime: '2026-10-15 15:42:00',
};

test('the air VPD is the Tetens formula the rest of the code uses', () => {
    assert.equal(airVpd(25, 50).toFixed(2), '1.58');
    assert.equal(airVpd(25, 100), 0);
});

test('a reading maps to its place on the plot, in percent of the figure', () => {
    // 25 °C is 7 of 14 degrees across the plot; 50 % is 40 of 60 down it.
    const anchor = pointAnchor(current);
    assert.equal(anchor.left, 50.6);
    assert.equal(anchor.top, 60.1);
});

test('a reading outside the plot is drawn on its edge, not off the chart', () => {
    const anchor = pointAnchor({ ...current, humidity: 120 });
    assert.equal(anchor.top, 2.2);
    assert.equal(pointAnchor(null), null);
});

test('without a reading the chart shows the band and says so', () => {
    const svg = renderChartSvg({ band, current: null, trail: [] });
    assert.match(svg, /ac-chart__band/);
    assert.match(svg, /Sin lectura actual/);
    assert.doesNotMatch(svg, /ac-chart__point/);
});

test('the point is marked as in range only when the loop says so', () => {
    const inside = renderChartSvg({ band, current, trail: [] });
    assert.match(inside, /class="ac-chart__point is-ok"/);
    assert.match(inside, /ac-chart__halo/);

    const outside = renderChartSvg({ band, current: { ...current, vpd_in_range: false }, trail: [] });
    assert.match(outside, /class="ac-chart__point"/);
    assert.doesNotMatch(outside, /is-ok/);
});

test('the band is a shape between its two edges, with its labels at the right end', () => {
    const svg = renderChartSvg({ band, current, trail: [] });
    assert.match(svg, /<polygon class="ac-chart__band"/);
    assert.equal((svg.match(/class="ac-chart__edge"/g) || []).length, 2);
    assert.match(svg, />0,6</);
    assert.match(svg, />1,0 kPa</);
});

test('a humidity band is labelled in humidity, not in kPa', () => {
    const svg = renderChartSvg({ band: { ...band, kind: 'humidity' }, current, trail: [] });
    assert.match(svg, />50 %</);
    assert.match(svg, />70 %</);
});

test('the trail follows the last hourly readings, oldest first, and ends at the dot', () => {
    const rows = [
        { datetime: '2026-10-15 12:00:00', temperature: 22, humidity: 60 },
        { datetime: '2026-10-15 13:00:00', temperature: 23, humidity: 62 },
        { datetime: '2026-10-15 14:00:00', temperature: 24, humidity: 64 },
    ];
    const svg = renderChartSvg({ band, current, trail: rows });
    const trail = svg.match(/<polyline class="ac-chart__trail" points="([^"]+)"/);
    assert.ok(trail, 'no trail drawn');
    // Three history points and the current one.
    assert.equal(trail[1].trim().split(' ').length, 4);
});

test('the tooltip reads the reading, its air and leaf VPD, and the target', () => {
    const model = tooltipModel(current, band);
    assert.equal(model.time, 'Ahora · 15:42');
    assert.equal(model.main, '25,0 °C · 50,0 %');
    assert.equal(model.air, '1,58 kPa');
    assert.equal(model.leaf, '≈ 1,21 kPa');
    assert.equal(model.leafOk, true);
    assert.equal(model.target, '0,6 – 1,0');
    assert.equal(model.note, 'Hoja estimada en 23,5 °C (aire − 1,5 °C)');
});

test('a leaf VPD out of range is flagged, and no reading means no tooltip', () => {
    assert.equal(tooltipModel({ ...current, vpd_in_range: false }, band).leafOk, false);
    assert.equal(tooltipModel(null, band), null);
});

test('the vpd_min label sits on the upper edge, the vpd_max label on the lower one', () => {
    // Lower VPD is the wetter side: the upper curve is vpd_min, the lower is vpd_max.
    const [top, bottom] = bandLabels(band);
    assert.equal(top.text, '0,6');
    assert.equal(bottom.text, '1,0 kPa');
    assert.ok(top.y < bottom.y, 'the top label must be the higher one on the plot');
});

test('the upper edge is drawn from humidity_high, the lower from humidity_low', () => {
    const { upper, lower } = bandEdges(band.points);
    // At 18 °C the high edge is 80 %, the low edge 60 %: higher humidity is higher on the plot.
    assert.ok(upper[0].y < lower[0].y);
    const svg = renderChartSvg({ band, current: null, trail: [] });
    const [first, second] = [...svg.matchAll(/<polyline class="ac-chart__edge" points="([^"]+)"/g)]
        .map(match => match[1].split(' ')[0].split(',')[1]);
    assert.ok(Number(first) < Number(second), 'the first edge drawn is the upper one');
});

test('on a narrow screen the labels land at about 11 px and the ticks are thinned', () => {
    const narrow = renderChartSvg({ band, current, trail: [] }, 358);
    const size = Number(narrow.match(/font-size:([\d.]+)px/)[1]);
    assert.ok(Math.abs(size * 358 / 640 - 11) < 0.1, `rendered at ${size * 358 / 640} px`);
    assert.equal((narrow.match(/text-anchor="middle"/g) || []).length, 3);
    assert.equal((narrow.match(/text-anchor="end"/g) || []).length >= 4, true);

    const wide = renderChartSvg({ band, current, trail: [] }, 640);
    assert.doesNotMatch(wide, /font-size:/);
    assert.equal((wide.match(/text-anchor="middle"/g) || []).length, 8);
});
