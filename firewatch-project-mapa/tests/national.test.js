// Pruebas de la cobertura nacional: clima (national.js) y teselas (zones.js). node tests/national.test.js
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const load = (f) => fs.readFileSync(path.join(__dirname, '..', 'js', f), 'utf8');
const ctx = vm.createContext({ console, Math, Number, Array, Float32Array, Map, Set, Date });
vm.runInContext(load('weather-grid.js') + '\n' + load('national.js') + '\n' + load('zones.js') +
  '\nthis.X = { NATION, NationalWeatherGrid, WeatherComposite, ZONES, zonesTilesIn, zonesTileBounds, zonesSafeId, WeatherGrid, chileMaskAlpha };', ctx);
const { NATION, NationalWeatherGrid, WeatherComposite, ZONES, zonesTilesIn, zonesTileBounds, zonesSafeId, WeatherGrid, chileMaskAlpha } = ctx.X;

let n = 0;
const t = (name, fn) => { try { fn(); n++; console.log('ok  ', name); } catch (e) { console.error('FAIL', name, '\n    ', e.message); process.exitCode = 1; } };

function nat(fill) {
  const g = new NationalWeatherGrid();
  g.points.forEach((p) => Object.assign(p, fill(p)));
  g.loaded = true;
  return g;
}
const uniform = (speed, dir) => () => ({ temp: 20, rh: 40, windSpeed: speed, windDir: dir });

t('la grilla nacional cubre Chile de Arica a Tierra del Fuego', () => {
  const g = new NationalWeatherGrid();
  assert.strictEqual(g.points.length, NATION.rows * NATION.cols);
  assert.ok(g.points[0].lat <= -56 && g.points.at(-1).lat >= -17.3);
  assert.ok(g.points[0].lon <= -75 && g.points.at(-1).lon >= -66);
});
t('muestrea en cualquier ciudad y devuelve null en alta mar / fuera', () => {
  const g = nat(uniform(20, 270));
  ['-33.45,-70.65', '-18.5,-70.3', '-41.5,-72.9', '-53.1,-70.9'].forEach((s) => assert.ok(g.sample(...s.split(',').map(Number))));
  assert.strictEqual(g.sample(-33, -90), null);
  assert.strictEqual(g.sample(-10, -70), null);
});
t('conserva la dirección entre 350° y 10° (no promedia el ángulo)', () => {
  const g = nat((p) => ({ temp: 20, rh: 40, windSpeed: 20, windDir: p.col % 2 ? 350 : 10 }));
  const w = g.sample(-36.8, NATION.lon0 + 0.5 * NATION.step);
  const d = Math.min(w.dirDeg, 360 - w.dirDeg);
  assert.ok(d < 5, `dirección ${w.dirDeg}`);
});
t('el viento cambia de dirección entre regiones', () => {
  const g = nat((p) => ({ temp: 20, rh: 40, windSpeed: 20, windDir: p.lat < -38 ? 180 : 270 }));
  assert.ok(Math.abs(g.sample(-45, -72).dirDeg - 180) < 1);
  assert.ok(Math.abs(g.sample(-30, -71).dirDeg - 270) < 1);
});
t('puntos sin dato anulan la muestra', () => {
  const g = nat(uniform(20, 270)); g.points[0].temp = null;
  assert.strictEqual(g.sample(NATION.lat0, NATION.lon0), null);
});
t('el compuesto usa la grilla fina donde existe y la nacional fuera de ella', () => {
  const local = new WeatherGrid({ lat: -33.45, lon: -70.65 }, 4, 28);
  local.points.forEach((p) => Object.assign(p, { temp: 30, rh: 20, windSpeed: 10, windDir: 90 }));
  local.loaded = true;
  const c = new WeatherComposite(local, nat(uniform(40, 270)));
  const a = c.sample(-33.45, -70.65), b = c.sample(-36.8, -73);
  assert.strictEqual(a.scale, 'local'); assert.ok(Math.abs(a.speed - 10) < 0.01);
  assert.strictEqual(b.scale, 'national'); assert.ok(Math.abs(b.speed - 40) < 0.01);
  assert.ok(c.loaded);
});
t('el compuesto sin grilla fina cargada usa solo la nacional', () => {
  const local = new WeatherGrid({ lat: -33.45, lon: -70.65 }, 4, 28);
  const c = new WeatherComposite(local, nat(uniform(40, 270)));
  assert.strictEqual(c.sample(-33.45, -70.65).scale, 'national');
});
t('las teselas de 0,25° cubren una ventana y no se repiten', () => {
  const tl = zonesTilesIn(-73.2, -36.9, -72.9, -36.7);
  const keys = new Set(tl.map((x) => `${x.ix}:${x.iy}`));
  assert.strictEqual(keys.size, tl.length);
  const b = zonesTileBounds(tl[0].ix, tl[0].iy);
  assert.ok(Math.abs(b[1][0] - b[0][0] - ZONES.tileDeg) < 1e-9);
  assert.ok(tl.every(({ ix, iy }) => { const bb = zonesTileBounds(ix, iy); return bb[1][0] >= -73.2 && bb[0][0] <= -72.9 && bb[1][1] >= -36.9 && bb[0][1] <= -36.7; }));
});
t('los ids de capa de una tesela con coordenadas negativas son válidos y únicos', () => {
  assert.strictEqual(zonesSafeId('-293:-148'), 'm293_m148');
  assert.notStrictEqual(zonesSafeId('-293:148'), zonesSafeId('293:-148'));
});
t('la máscara deja ciudades chilenas dentro y mar/Argentina/Bolivia fuera', () => {
  [[-33.45, -70.65], [-23.65, -70.4], [-18.48, -70.31], [-36.82, -73.05], [-41.47, -72.94], [-53.16, -70.91], [-29.9, -71.25]]
    .forEach(([la, lo]) => assert.ok(chileMaskAlpha(la, lo) > 0.9, `${la},${lo}`));
  [[-33, -80], [-33.0, -68.8], [-31.4, -64.2], [-17.0, -66.0], [-60, -70], [-10, -70], [-45, -68]]
    .forEach(([la, lo]) => assert.ok(chileMaskAlpha(la, lo) < 0.05, `${la},${lo}`));
});
console.log(`\n${n} pruebas OK`);
