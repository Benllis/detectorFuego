// Pruebas de la lógica pura de las alertas de la central. Ejecutar: node tests/alerts.test.js
const assert = require('assert');
const A = require('../js/alerts-layer.js');
let n = 0;
const test = (name, fn) => { fn(); n++; console.log('ok -', name); };

const alertas = [
  { id_alerta: 'a1', estado: 'PENDIENTE', tipo: 'FIRE', gps: [-33.52, -70.58], camara_id: 'cam_1', camara_nombre: 'Webcam', fecha_hora: '2026-10-12 15:30:45', confianza: 0.8 },
  { id_alerta: 'a2', estado: 'FALSO_POSITIVO', tipo: 'SMOKE', gps: [-33.4, -70.6], camara_id: 'cam_1', motivo_descarte: 'Reflejo' },
  { id_alerta: 'a3', estado: 'CONFIRMADA', tipo: 'CRITICAL', gps: null, camara_id: 'cam_2' },
  { id_alerta: 'a4', estado: 'PENDIENTE', tipo: 'NO_DETECTION', gps: [NaN, -70], camara_id: 'cam_3' },
];

test('GeoJSON: coordenadas en orden [lon, lat] y sin las que no tienen ubicación válida', () => {
  const g = A.fwAlertsToGeoJSON(alertas, true);
  assert.deepStrictEqual(g.features.map((f) => f.properties.id), ['a1', 'a2']);
  assert.deepStrictEqual(g.features[0].geometry.coordinates, [-70.58, -33.52]);
});
test('GeoJSON: los falsos positivos se ocultan salvo que se pidan', () => {
  assert.deepStrictEqual(A.fwAlertsToGeoJSON(alertas, false).features.map((f) => f.properties.id), ['a1']);
});
test('GeoJSON: lista vacía o nula no falla', () => {
  assert.strictEqual(A.fwAlertsToGeoJSON(null, false).features.length, 0);
});
test('resumen: cuenta por estado y las que no se pueden dibujar', () => {
  const s = A.fwAlertSummary(alertas);
  assert.strictEqual(s.PENDIENTE, 2); assert.strictEqual(s.CONFIRMADA, 1); assert.strictEqual(s.FALSO_POSITIVO, 1);
  assert.strictEqual(s.total, 4); assert.strictEqual(s.sinUbicacion, 1);
});
test('alertas nuevas: solo pendientes con ubicación que no se habían visto', () => {
  const vistas = new Set(['a2']);
  assert.deepStrictEqual(A.fwNewPending(alertas, vistas).map((a) => a.id_alerta), ['a1']);
  assert.strictEqual(A.fwNewPending(alertas, new Set(['a1'])).length, 0);
});
test('escape de HTML: el texto que viene de la API no inyecta etiquetas', () => {
  assert.strictEqual(A.fwEscape('<img src=x onerror=1>"\''), '&lt;img src=x onerror=1&gt;&quot;&#39;');
  assert.strictEqual(A.fwEscape(null), '');
});
test('dirección de la API: sin espacios ni barras finales', () => {
  assert.strictEqual(A.fwApiBase('  http://localhost:8100// '), 'http://localhost:8100');
});
console.log(`\n${n} pruebas OK`);
