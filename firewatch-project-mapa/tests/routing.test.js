// Pruebas de la lógica pura de rutas. Ejecutar: node tests/routing.test.js
const assert = require('assert');
const R = require('../js/routing.js');
let n = 0;
const test = (name, fn) => { fn(); n++; console.log('ok -', name); };

test('fwDistKm: 1° de latitud ≈ 111 km', () => {
  assert.ok(Math.abs(R.fwDistKm(-33, -70, -34, -70) - 111.32) < 0.01);
});
test('fwNearestK ordena por distancia y limita k', () => {
  const list = [{ id: 'c', lat: -33.9, lon: -70 }, { id: 'a', lat: -33.01, lon: -70 }, { id: 'b', lat: -33.3, lon: -70 }];
  const r = R.fwNearestK(list, -33, -70, 2);
  assert.deepStrictEqual(r.map((x) => x.id), ['a', 'b']);
  assert.ok(r[0].distKm < r[1].distKm);
  assert.strictEqual(list[0].distKm, undefined, 'no muta la lista original');
});
test('fwPickFastest elige por tiempo, no por orden; ignora fallos', () => {
  const res = [{ station: 'A', route: { duration_s: 900 } }, null, { station: 'B', route: { duration_s: 600 } }];
  assert.strictEqual(R.fwPickFastest(res).station, 'B');
  assert.strictEqual(R.fwPickFastest([null, null]), null);
});
test('fwPickWaterPoint: grifo cercano gana; si no, fuente de agua; si no hay nada, null', () => {
  const h = [{ lat: 1, lon: 1, distKm: 0.5 }];
  const w = { lat: 2, lon: 2, distKm: 0.2, name: 'Río Mapocho', type: 'río' };
  assert.strictEqual(R.fwPickWaterPoint(h, w).kind, 'grifo');
  assert.strictEqual(R.fwPickWaterPoint([{ lat: 1, lon: 1, distKm: 9 }], w).kind, 'río');
  assert.strictEqual(R.fwPickWaterPoint([{ lat: 1, lon: 1, distKm: 9 }], null).kind, 'grifo'); // mejor un grifo lejano que nada
  assert.strictEqual(R.fwPickWaterPoint([], null), null);
});
test('formatos de tiempo, distancia y retraso', () => {
  assert.strictEqual(R.fwFmtDuration(20), '1 min');
  assert.strictEqual(R.fwFmtDuration(14 * 60), '14 min');
  assert.strictEqual(R.fwFmtDuration(65 * 60), '1 h 05 min');
  assert.strictEqual(R.fwFmtKm(850), '850 m');
  assert.strictEqual(R.fwFmtKm(8400), '8.4 km');
  assert.strictEqual(R.fwRouteText({ duration_s: 600, distance_m: 5000, delay_s: null }), '10 min · 5.0 km');
  assert.ok(R.fwRouteText({ duration_s: 600, distance_m: 5000, delay_s: 180 }).includes('+3 min por tráfico'));
  assert.ok(R.fwRouteText({ duration_s: 600, distance_m: 5000, delay_s: 10 }).includes('sin demoras'));
});
test('tráfico: clases y filtro de vías principales', () => {
  assert.strictEqual(R.fwTrafficClass({ road_type: 'Motorway' }), 'autopista');
  assert.strictEqual(R.fwTrafficClass({ road_type: 'Major road' }), 'principal');
  assert.strictEqual(R.fwTrafficClass({ road_category: 'Primary', road_type: 'x' }), 'principal');
  assert.strictEqual(R.fwTrafficClass({ road_type: 'Local road' }), 'calle');
  assert.strictEqual(R.fwTrafficClass({}), null);
  assert.ok(R.fwTrafficVisible({ road_type: 'Motorway' }, 'principales'));
  assert.ok(R.fwTrafficVisible({ road_type: 'Major road' }, 'principales'));
  assert.ok(!R.fwTrafficVisible({ road_type: 'Secondary road' }, 'principales'));
  assert.ok(R.fwTrafficVisible({ road_type: 'Secondary road' }, 'secundarias'));
  assert.ok(!R.fwTrafficVisible({ road_type: 'Local road' }, 'secundarias'));
  assert.ok(R.fwTrafficVisible({ road_type: 'Local road' }, 'todas') && R.fwTrafficVisible({}, 'todas'));
  assert.ok(!R.fwTrafficVisible({}, 'principales'), 'un tramo sin tipo no se muestra en modo principales');
  assert.strictEqual(R.fwTrafficFilter('todas'), null);
  const f = JSON.stringify(R.fwTrafficFilter('principales'));
  assert.ok(f.includes('motorway') && f.includes('major road') && !f.includes('secondary road') && !f.includes('local road'));
  assert.ok(R.fwTrafficFilter('desconocido'), 'un valor raro cae a principales');
});
test('tráfico: expresiones de estilo bien formadas', () => {
  const c = R.fwTrafficColorExpr();
  assert.strictEqual(c[0], 'interpolate');
  const o = R.fwTrafficOpacityExpr(0.5);
  assert.strictEqual(o[0], 'let');
  assert.ok(R.fwTrafficAlpha(0.2, 0.9) > R.fwTrafficAlpha(1, 0.9), 'lo congestionado se ve más opaco que lo fluido');
  assert.ok(R.fwTrafficAlpha(0.2, 0.9) >= 0.99);
  assert.ok(Math.abs(R.fwTrafficAlpha(1, 0.9) - 0.54) < 1e-9);
  assert.strictEqual(R.fwTrafficWidthExpr()[2][0], 'zoom');
});

console.log(`\n${n} pruebas OK`);
