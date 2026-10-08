// Pruebas de la grilla de combustible y de la pendiente. Ejecutar: node tests/fuel-grid.test.js
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { FuelGrid, WORLDCOVER_CLASSES, fuelFractionsText, FUEL_CELL_M } = require('../js/fuel-grid.js');

const idx = (k) => WORLDCOVER_CLASSES.findIndex((c) => c.key === k);
let n = 0;
const test = (name, fn) => { fn(); n++; console.log('ok -', name); };

// Área ~ 5 km x 5 km, 100x100 px (50 m/px). Mitad oeste matorral, mitad este urbano; esquina NE sin dato.
const W = 100, H = 100;
const cls = new Uint8Array(W * H);
for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) cls[y * W + x] = x < 50 ? idx('matorral') : idx('urbano');
for (let y = 0; y < 10; y++) for (let x = 90; x < 100; x++) cls[y * W + x] = 255;
const bounds = [[-70.70, -33.50], [-70.6547, -33.4550]];
const g = new FuelGrid(cls, W, H, bounds);

test('classAt devuelve la clase correcta y null fuera del área', () => {
  assert.strictEqual(g.classAt(-33.48, -70.69).key, 'matorral');
  assert.strictEqual(g.classAt(-33.48, -70.66).key, 'urbano');
  assert.strictEqual(g.classAt(0, 0), null);
  assert.strictEqual(g.classAt(-33.4551, -70.6548), null); // pixel sin dato
});
test('cell: fracciones suman 1 y el dominante manda', () => {
  const c = g.cell(-33.48, -70.69);
  assert.strictEqual(c.dominant.key, 'matorral');
  assert.ok(Math.abs(c.fractions.reduce((s, f) => s + f.fraction, 0) - 1) < 1e-9);
  assert.strictEqual(c.fuel, 1);
  assert.strictEqual(g.cell(0, 0), null);
});
test('cell: una celda en el borde matorral/urbano mezcla clases', () => {
  const lon = -70.70 + (bounds[1][0] - bounds[0][0]) * 0.5; // justo en el límite
  const c = g.cell(-33.48, lon);
  assert.ok(c.fractions.length >= 2, 'debe haber mezcla');
  assert.ok(c.fuel > 0.1 && c.fuel < 1);
});
test('la celda es de ~250 m (mismo resultado dentro de la celda, distinto en la vecina)', () => {
  const a = g.cell(-33.48, -70.69), b = g.cell(-33.48, -70.6901);
  assert.strictEqual(a.row, b.row); assert.strictEqual(a.col, b.col);
  const dLon = FUEL_CELL_M / g.mPerDegLon;
  const c = g.cell(-33.48, -70.69 + dLon * 1.2);
  assert.notStrictEqual(a.col, c.col);
});
test('fuelAt y around', () => {
  assert.strictEqual(g.fuelAt(-33.48, -70.69), 1);
  assert.strictEqual(g.fuelAt(-33.48, -70.66), 0.1);
  assert.strictEqual(g.fuelAt(0, 0), null);
  const r = g.around(-33.48, -70.68, 1);
  assert.ok(r.fractions.length >= 1);
  const s = r.fractions.reduce((a, f) => a + f.fraction, 0);
  assert.ok(Math.abs(s - 1) < 1e-9);
});
test('toCells cubre el área y fuelFractionsText formatea', () => {
  const t0 = g.toCells();
  assert.strictEqual(t0.cellM, 250);
  assert.ok(t0.rows * t0.cols > 300 && t0.rows * t0.cols < 600, 'celdas: ' + t0.rows * t0.cols);
  assert.ok(t0.cells.length <= t0.rows * t0.cols && t0.cells.length > 0);
  const t = fuelFractionsText([{ cls: WORLDCOVER_CLASSES[idx('matorral')], fraction: 0.62 }, { cls: WORLDCOVER_CLASSES[idx('urbano')], fraction: 0.2 }], 2);
  assert.ok(t.includes('62 %') && t.includes('matorral') && t.includes('urbano'));
});

test('toArchivePayload: formato compacto, fila 0 = sur, clave estable', () => {
  const p = g.toArchivePayload();
  assert.strictEqual(p.key, '-70.7000,-33.5000,-70.6547,-33.4550');
  assert.deepStrictEqual(p.bounds, [bounds[0][0], bounds[0][1], bounds[1][0], bounds[1][1]]);
  assert.ok(p.cells.length > 300);
  const [r, c, lat, lon, dom, fuel, fr] = p.cells[0];
  assert.strictEqual(r, 0); assert.strictEqual(c, 0);
  assert.ok(lat > bounds[0][1] && lat < bounds[0][1] + 0.01 && lon > bounds[0][0] && lon < bounds[0][0] + 0.01);
  assert.strictEqual(dom, 'matorral'); assert.strictEqual(fuel, 1); assert.deepStrictEqual(fr, { matorral: 100 });
  JSON.parse(JSON.stringify(p)); // serializable
});

// slopeAspect (inspector.js) sin navegador: se evalúa solo esa función.
const src = fs.readFileSync(path.join(__dirname, '../js/inspector.js'), 'utf8');
const m = src.match(/function slopeAspect[\s\S]*?\n}\n/);
const sa = vm.runInNewContext(m[0] + ';slopeAspect');
test('slopeAspect: plano, ladera al este y ladera al norte', () => {
  const flat = sa([100, 100, 100, 100, 100], 200);
  assert.strictEqual(flat.aspect, null);
  // z = [centro, N, S, E, O]; sube hacia el oeste => mira al este (90°)
  const east = sa([100, 100, 100, 80, 120], 200);
  assert.ok(Math.abs(east.aspect - 90) < 1e-6, 'aspect ' + east.aspect);
  assert.ok(Math.abs(east.percent - 10) < 1e-6);
  const north = sa([100, 80, 120, 100, 100], 200);
  assert.ok(Math.abs(north.aspect - 0) < 1e-6 || Math.abs(north.aspect - 360) < 1e-6);
});

// Riesgo combinado (heat-layer.js) también se evalúa aislado.
const hs = fs.readFileSync(path.join(__dirname, '../js/heat-layer.js'), 'utf8');
const mm = hs.match(/function combinedFireRisk[\s\S]*?\n}\n/);
const combined = vm.runInNewContext(mm[0] + ';combinedFireRisk');
test('combinedFireRisk: sin combustible queda en 25 %, con combustible máximo mantiene el índice', () => {
  assert.ok(Math.abs(combined(80, 0.4) - 44) < 1e-9);
  assert.ok(Math.abs(combined(80, 1) - 80) < 1e-9);
  assert.ok(Math.abs(combined(80, 0) - 20) < 1e-9);
});
console.log(`\n${n} pruebas OK`);
