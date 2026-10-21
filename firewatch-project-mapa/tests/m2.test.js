// Pruebas de la lógica pura de la capa M2 del mapa. Ejecutar: node tests/m2.test.js
const assert = require('assert');
const M = require('../js/m2-layer.js');
let n = 0;
const test = (name, fn) => { fn(); n++; console.log('ok -', name); };
const near = (a, b, e = 1e-9) => assert.ok(Math.abs(a - b) < e, `${a} vs ${b}`);

const LAT = -33.05, LON = -71.6;

test('caja del parche: 64 celdas de 0,01° y la celda 32 centrada en el foco', () => {
  const b = M.fwM2Bbox(LAT, LON);
  near(b.north - b.south, 0.64); near(b.east - b.west, 0.64);
  near(b.south + 32.5 * 0.01, LAT); near(b.west + 32.5 * 0.01, LON);
  near(b.south, -33.375); near(b.north, -32.735); // mismos valores que devuelve el backend (tests/test_m2.py)
});

test('malla de bloques: fila 0 = sur, columna 0 = oeste, cubre todo el parche', () => {
  const g = M.fwM2BlockCenters(LAT, LON, 16);
  assert.strictEqual(g.coords.length, 256);
  assert.ok(g.coords[0][0] < g.coords[16][0], 'la fila 1 está al norte de la 0');
  assert.ok(g.coords[0][1] < g.coords[1][1], 'la columna 1 está al este de la 0');
  const b = M.fwM2Bbox(LAT, LON);
  near(g.coords[0][0], b.south + 0.02); near(g.coords[255][1], b.east - 0.02);
  near(g.lat0, g.coords[0][0]); assert.strictEqual(g.nLat, 16);
});

const T = Date.parse('2026-10-15T18:00:00Z');
const px = (la, lo, hAgo) => ({ lat: la, lon: lo, ts: T - hAgo * 3600e3 });

test('focos: el de la alerta va primero y los satelitales viejos o lejanos se descartan', () => {
  const f = M.fwM2Focos([px(LAT + 0.05, LON, 2), px(LAT + 0.1, LON, 40), px(LAT + 3, LON, 1), px(LAT - 0.1, LON + 0.1, 30)], LAT, LON, T);
  assert.deepStrictEqual(f[0], [LAT, LON]);
  assert.strictEqual(f.length, 3); // alerta + 2h + 30h; fuera: 40h y 3° al norte
});

test('focos: uno por celda y sin repetir el de la alerta', () => {
  const f = M.fwM2Focos([px(LAT + 0.001, LON, 1), px(LAT + 0.0015, LON, 3), px(LAT + 0.02, LON, 1)], LAT, LON, T);
  assert.strictEqual(f.length, 2);
});

test('focos: ignora datos sin hora o con coordenadas inválidas; tope de 500 con los más cercanos', () => {
  assert.strictEqual(M.fwM2Focos([{ lat: LAT + 0.1, lon: LON, ts: NaN }, { lat: NaN, lon: LON, ts: T }, null], LAT, LON, T).length, 1);
  const muchos = [];
  for (let i = -30; i <= 30; i++) for (let j = -30; j <= 30; j++) muchos.push(px(LAT + i * 0.01, LON + j * 0.01, 1));
  const f = M.fwM2Focos(muchos, LAT, LON, T);
  assert.strictEqual(f.length, 500);
  assert.deepStrictEqual(f[0], [LAT, LON]);
  assert.ok(f.every(([la, lo]) => Math.hypot(la - LAT, lo - LON) < 0.20), 'quedan los más cercanos');
});

test('focos: un foco "del futuro" lejano (más de 3 h) no cuenta', () => {
  assert.strictEqual(M.fwM2Focos([px(LAT + 0.1, LON, -10)], LAT, LON, T).length, 1);
});

test('clases: usa los códigos ESA y marca null donde no hay grilla', () => {
  const claves = ['bosque', 'matorral', 'pastizal', 'cultivo', 'urbano', 'desnudo', 'nieve', 'agua', 'humedal', 'manglar', 'musgo'];
  assert.deepStrictEqual(claves.map((k) => M.FW_M2_ESA[k]), [10, 20, 30, 40, 50, 60, 70, 80, 90, 95, 100]);
  const { grid, coverage } = M.fwM2ClassGrid((la, lo) => (lo < LON ? { dominant: { key: 'matorral' } } : null), LAT, LON, 8);
  assert.strictEqual(grid.n, 8); assert.strictEqual(grid.valores.length, 64);
  assert.strictEqual(grid.valores[0], 20); assert.strictEqual(grid.valores[7], null);
  near(coverage, 0.5);
});

test('payload: viento meteorológico, temperatura máxima y humedad acotada', () => {
  const w = { speed: 18.4, dirDeg: -90, temp: 21, rh: 130 };
  const b = M.fwM2Payload({ lat: LAT, lon: LON, focos: [[LAT, LON]], w, tempMax: 33.2, cobertura: { n: 2, valores: [10, 10, 10, 10] } });
  assert.strictEqual(b.viento_dir_deg, 270); assert.strictEqual(b.temp_max, 33.2); assert.strictEqual(b.humedad, 100);
  assert.ok(!('elevacion' in b));
  const c = M.fwM2Payload({ lat: LAT, lon: LON, focos: [], w: { speed: 5, dirDeg: 10, temp: 21, rh: null }, tempMax: null, cobertura: {}, elevacion: { n: 2, valores: [1, 2, 3, 4] } });
  assert.strictEqual(c.temp_max, 21); assert.strictEqual(c.humedad, 40); assert.deepStrictEqual(c.elevacion.valores, [1, 2, 3, 4]);
});

test('temperatura máxima: solo las próximas 24 h y tolera datos nulos', () => {
  const now = Date.parse('2026-10-15T12:00:00Z');
  const h = { time: ['2026-10-15T10:00', '2026-10-15T13:00', '2026-10-15T15:00', '2026-10-16T09:00', '2026-10-17T15:00'],
    temperature_2m: [40, 27.5, 31.1, null, 45] };
  assert.strictEqual(M.fwM2TempMax(h, now), 31.1);
  assert.strictEqual(M.fwM2TempMax({ time: [], temperature_2m: [] }, now), null);
  assert.strictEqual(M.fwM2TempMax(null, now), null);
});

test('celdas: un polígono por celda con nivel, en el lugar y tamaño correctos (fila 0 = sur)', () => {
  const bbox = { sur: 0, norte: 2, oeste: 10, este: 12 };
  const g = M.fwM2Cells([1, 0, 0, 3], 2, 2, bbox); // sur: [1,0]; norte: [0,3]
  assert.strictEqual(g.features.length, 2);
  const [a, b] = g.features;
  assert.strictEqual(a.properties.nivel, 1); assert.strictEqual(b.properties.nivel, 3);
  assert.strictEqual(JSON.stringify(a.geometry.coordinates[0]), '[[10,0],[11,0],[11,1],[10,1],[10,0]]'); // sur-oeste
  assert.strictEqual(JSON.stringify(b.geometry.coordinates[0]), '[[11,1],[12,1],[12,2],[11,2],[11,1]]'); // norte-este
  assert.strictEqual(M.fwM2Cells([0, 4, -1, NaN], 2, 2, bbox).features.length, 0, 'niveles fuera de 1-3 se ignoran');
});

console.log(`\n${n} pruebas OK`);
