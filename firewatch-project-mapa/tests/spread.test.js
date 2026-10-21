// Pruebas de la lógica pura del simulador de propagación. Ejecutar: node tests/spread.test.js
const assert = require('assert');
const S = require('../js/spread-model.js');
let n = 0;
const test = (name, fn) => { fn(); n++; console.log('ok -', name); };

const CELL = 250;
/** Ventana de prueba: `size` x `size` celdas, ignición al centro, todo configurable. */
function make({ size = 41, fuel = 1, windKmh = 0, windFrom = 270, rh = 50, temp = 25, elevFn = null } = {}) {
  const N = size * size, mid = (size - 1) / 2;
  const f = new Float32Array(N), ws = new Float32Array(N), wd = new Float32Array(N), dr = new Float32Array(N);
  const z = elevFn ? new Float32Array(N) : null;
  for (let r = 0; r < size; r++) for (let c = 0; c < size; c++) {
    const i = r * size + c;
    f[i] = typeof fuel === 'function' ? fuel(r, c) : fuel;
    ws[i] = windKmh; wd[i] = windFrom; dr[i] = S.fwDryness(temp, rh);
    if (z) z[i] = elevFn(r, c);
  }
  return { rows: size, cols: size, cellM: CELL, fuel: f, elev: z, windSpeed: ws, windFromDeg: wd, dryness: dr, ignitions: [{ row: mid, col: mid }], mid };
}
const at = (res, dr, dc) => res.arrival[(res.ignitions[0].row + dr) * res.cols + res.ignitions[0].col + dc];

test('hay 16 vecinos por celda', () => assert.strictEqual(S.SPREAD_NEIGHBORS.length, 16));

test('sin viento, plano y combustible 1: avanza como un círculo a la velocidad base (±6 %)', () => {
  const res = S.fwSimulate(make());
  const expected = (6 * CELL) / S.SPREAD_DEFAULTS.r0;           // 6 celdas = 1,5 km
  [at(res, 6, 0), at(res, -6, 0), at(res, 0, 6), at(res, 0, -6), at(res, 4, 4)].forEach((t, k) => {
    const exp = k === 4 ? (Math.hypot(4, 4) * CELL) / S.SPREAD_DEFAULTS.r0 : expected;
    assert.ok(Math.abs(t - exp) / exp < 0.06, `dirección ${k}: ${t.toFixed(1)} vs ${exp.toFixed(1)}`);
  });
});

test('con viento del oeste el fuego va hacia el este: cabeza ≪ flanco ≪ cola (a la misma distancia, una celda)', () => {
  const res = S.fwSimulate(make({ windKmh: 20, windFrom: 270 }));
  const head = at(res, 0, 1), flank = at(res, 1, 0), back = at(res, 0, -1);
  assert.ok(head < flank && flank < back, `cabeza ${head.toFixed(0)} < flanco ${flank.toFixed(0)} < cola ${back.toFixed(0)} min`);
  assert.ok(flank / head > 5 && back / flank > 1.5);
  assert.ok(Math.abs(at(res, 1, 0) - at(res, -1, 0)) / flank < 0.01, 'los dos flancos son simétricos');
});

test('el viento no achica la mancha: con viento el área a las 3 h es mayor que sin viento', () => {
  const area = (kmh) => S.fwSpreadStats(S.fwSimulate(make({ size: 81, windKmh: kmh })), [180])[0].areaHa;
  assert.ok(area(20) > area(0) * 1.5, `${area(20).toFixed(0)} ha vs ${area(0).toFixed(0)} ha`);
});

test('un viento más fuerte alarga más la mancha (mayor alcance a las 3 h)', () => {
  const reach = (kmh) => S.fwSpreadStats(S.fwSimulate(make({ size: 81, windKmh: kmh })), [180])[0].maxKm;
  assert.ok(reach(10) > reach(0) && reach(30) > reach(10));
});

test('el alcance máximo apunta hacia donde sopla el viento', () => {
  const west = S.fwSpreadStats(S.fwSimulate(make({ size: 61, windKmh: 25, windFrom: 270 })), [120])[0];
  assert.strictEqual(west.compass, 'E');
  const north = S.fwSpreadStats(S.fwSimulate(make({ size: 61, windKmh: 25, windFrom: 0 })), [120])[0];
  assert.strictEqual(north.compass, 'S');
});

test('una franja sin combustible (agua) frena el fuego por completo', () => {
  const res = S.fwSimulate(make({ fuel: (r, c) => (c === 25 ? 0 : 1) }));   // columna 25 sin combustible, de lado a lado
  assert.ok(Number.isFinite(at(res, 0, 3)));
  assert.strictEqual(at(res, 0, 10), Infinity);
});

test('combustible pobre avanza más lento que combustible rico', () => {
  const rich = S.fwSimulate(make({ fuel: 1 })), poor = S.fwSimulate(make({ fuel: 0.4 }));
  assert.ok(at(poor, 0, 6) > at(rich, 0, 6) * 2);
});

test('sube más rápido de lo que baja: pendiente hacia el norte (altura crece con la fila)', () => {
  const res = S.fwSimulate(make({ elevFn: (r) => r * 25 }));   // 25 m por celda de 250 m = 10 %
  assert.ok(at(res, 6, 0) < at(res, -6, 0), `cuesta arriba ${at(res, 6, 0).toFixed(0)} < cuesta abajo ${at(res, -6, 0).toFixed(0)}`);
});

test('aire seco y caliente propaga más rápido que aire húmedo y frío', () => {
  const dry = S.fwSimulate(make({ rh: 15, temp: 35 })), wet = S.fwSimulate(make({ rh: 85, temp: 12 }));
  assert.ok(at(dry, 0, 6) < at(wet, 0, 6) / 2);
});

test('el área quemada crece con el tiempo y nunca pasa del límite de 6 h', () => {
  const res = S.fwSimulate(make({ size: 101, windKmh: 15 }));
  const st = S.fwSpreadStats(res, [60, 180, 360]);
  assert.ok(st[0].areaHa < st[1].areaHa && st[1].areaHa < st[2].areaHa);
  assert.ok(res.arrival.every((t) => t === Infinity || t <= 360 + 1e-3));
});

test('la ignición sobre una celda sin vegetación mapeada igual arde (piso de combustible)', () => {
  const inp = make({ fuel: (r, c) => (r === 20 && c === 20 ? NaN : 1) });
  const res = S.fwSimulate(inp);
  assert.strictEqual(at(res, 0, 0), 0);
  assert.ok(Number.isFinite(at(res, 0, 4)));
});

test('es determinista: dos corridas iguales dan exactamente lo mismo', () => {
  const a = S.fwSimulate(make({ windKmh: 12, windFrom: 200, elevFn: (r, c) => r * 8 + c * 3 }));
  const b = S.fwSimulate(make({ windKmh: 12, windFrom: 200, elevFn: (r, c) => r * 8 + c * 3 }));
  assert.deepStrictEqual(Array.from(a.arrival), Array.from(b.arrival));
});

test('sin ignición lanza un error claro', () => {
  const inp = make(); inp.ignitions = [];
  assert.throws(() => S.fwSimulate(inp), /ignición/);
});

test('reglas básicas: sequedad acotada, LB acotada, círculo sin viento', () => {
  assert.strictEqual(S.fwDryness(25, 50), 1);
  assert.ok(S.fwDryness(45, 5) <= 1.7 && S.fwDryness(0, 100) >= 0.3);
  assert.strictEqual(S.fwLengthBreadth(0), 1);
  assert.strictEqual(S.fwLengthBreadth(500), S.SPREAD_DEFAULTS.lbMax);
  assert.strictEqual(S.fwEccentricity(1), 0);
  assert.ok(Math.abs(S.fwDirRate(2, 1, 1.234) - 2) < 1e-9);
});

test('viento efectivo: pendiente sola empuja hacia arriba; viento a favor y pendiente se suman', () => {
  const up = S.fwEffectiveWind(0, 0, 0, 0.2);                 // sube hacia el norte
  assert.ok(Math.abs(up.toRad) < 1e-9 && up.speed > 0);
  const both = S.fwEffectiveWind(0, 10, 0, 0.2);              // viento hacia el norte y cuesta arriba al norte
  assert.ok(both.speed > up.speed + 9.9);
  const against = S.fwEffectiveWind(180, 10, 0, 0.2);         // viento hacia el sur, cuesta arriba al norte
  assert.ok(against.speed < both.speed);
});

test('retícula de altura y su interpolación: reproduce un plano inclinado', () => {
  const center = { lat: -33.45, lon: -70.65 };
  const lat = S.fwElevationLattice(center, 12, 1);
  assert.ok(lat.coords.length === lat.nLat * lat.nLon && lat.nLat > 24);
  const z = Float32Array.from(lat.coords.map(([la, lo]) => 500 + (la - center.lat) * 110574 * 0.1 + (lo - center.lon) * 93000 * 0.05));
  const fn = S.fwElevationFn(lat, z);
  const la = center.lat + 0.03, lo = center.lon - 0.02;
  const exact = 500 + (la - center.lat) * 110574 * 0.1 + (lo - center.lon) * 93000 * 0.05;
  assert.ok(Math.abs(fn(la, lo) - exact) < 1.5, `${fn(la, lo)} vs ${exact}`);
});

test('fwBuildInput: arma la ventana con celda central y resumen; falla claro sin vegetación o sin clima', () => {
  const fuelGrid = { cell: () => ({ fuel: 0.8 }) };
  const weatherGrid = { sample: () => ({ temp: 30, rh: 20, speed: 18, dirDeg: 225 }) };
  const b = S.fwBuildInput({ center: { lat: -33.5, lon: -70.6 }, radiusKm: 6, cellM: 250, fuelGrid, weatherGrid, elevAt: (la) => la * 0 + 600 });
  assert.ok(b.input.rows % 2 === 1 && b.input.rows >= 49);
  assert.strictEqual(b.input.ignitions[0].row, (b.input.rows - 1) / 2);
  assert.strictEqual(b.summary.fuelCoverage, 1);
  assert.strictEqual(b.summary.windFromDeg, 225);
  assert.ok(b.geo.east > b.geo.west && b.geo.north > b.geo.south);
  assert.throws(() => S.fwBuildInput({ center: { lat: 0, lon: 0 }, radiusKm: 6, fuelGrid: null, weatherGrid }), /vegetación/);
  assert.throws(() => S.fwBuildInput({ center: { lat: 0, lon: 0 }, radiusKm: 6, fuelGrid, weatherGrid: { sample: () => null } }), /clima/);
  const res = S.fwSimulate(b.input);
  assert.ok(S.fwSpreadStats(res)[2].areaHa > 0);
});
console.log(`\n${n} pruebas OK`);
