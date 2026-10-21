// Prueba de humo del flujo del navegador de m2-layer.js (runM2) con el DOM, el mapa y la red simulados.
// Ejecutar: node tests/m2-flow.test.js
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const els = {};
const mkEl = () => ({ style: {}, innerHTML: '', id: '', className: '', appendChild() {}, addEventListener() {}, set textContent(v) { this._t = v; },
  getContext: () => ({ createImageData: (w, h) => ({ data: new Uint8ClampedArray(w * h * 4) }), putImageData() {} }), toDataURL: () => 'data:image/png;base64,AAAA', width: 0, height: 0 });
const sources = {}, layers = {};
const map = {
  getLayer: (id) => layers[id], getSource: (id) => sources[id],
  addSource: (id, s) => { sources[id] = s; }, addLayer: (l) => { layers[l.id] = l; },
  removeLayer: (id) => { delete layers[id]; }, removeSource: (id) => { delete sources[id]; },
};
const posted = [];
const ctx = {
  console, Date, Math, Number, Array, Float32Array, Uint8ClampedArray, JSON, Promise, Object, String, Set, Map, setTimeout,
  document: {
    getElementById: (id) => els[id] || null,
    createElement: () => mkEl(),
    head: { appendChild() {} }, body: { appendChild: (e) => { els[e.id] = e; } },
    addEventListener() {},
  },
  FW: { proxy: false },
  alertsState: { token: 'T' },
  alertsFetch: async (url, opts) => { posted.push({ url, body: JSON.parse(opts.body) }); return { ok: true, status: 200, json: async () => RESPUESTA }; },
  fetchElevationLattice: async (lat) => Float32Array.from(lat.coords.map(() => 120)),
  zonesEnsureFuel: async () => {},
  fuelMosaic: { cell: () => ({ dominant: { key: 'matorral' } }) },
  firePixels: [], FIRE_STATE: { replay: null },
  fwEscape: (s) => String(s).replace(/</g, '&lt;'),
  reportSource: () => {},
};
let RESPUESTA;
const lat = -33.05, lon = -71.6;
const niveles = new Array(4096).fill(0); niveles[32 * 64 + 32] = 1; niveles[33 * 64 + 32] = 2; niveles[10 * 64 + 10] = 3;
RESPUESTA = {
  modelo: 'M2', focos_usados: 1, parche: { bbox: { sur: -33.375, norte: -32.735, oeste: -71.925, este: -71.285 } },
  grilla: { filas: 64, columnas: 64, niveles }, advertencias: ['Prueba <b>'],
  niveles: [{ nivel: 1, etiqueta: 'muy probable', celdas: 1, area_km2: 1.2, alcance_km: 0.5 }, { nivel: 2, etiqueta: 'probable', celdas: 1, area_km2: 1.2, alcance_km: 1.1 }, { nivel: 3, etiqueta: 'posible', celdas: 1, area_km2: 1.2, alcance_km: 30 }],
};
vm.createContext(ctx);
const src = fs.readFileSync(path.join(__dirname, '../js/m2-layer.js'), 'utf8').replace(/if \(typeof module[\s\S]*?\n}\n/, '');
vm.runInContext(src + '\nthis.__api = { initM2, runM2, clearM2, m2ButtonHTML };', ctx);
const api = ctx.__api;
const weather = (grid, label = '') => () => ({ grid, label });
const grid = { loaded: true, sample: () => ({ speed: 20, dirDeg: 250, temp: 26, rh: 35 }) };

(async () => {
  api.initM2(map, { weather: weather(grid) });
  await api.runM2(lat, lon);
  assert.strictEqual(posted.length, 1, 'se hizo un POST al backend');
  const b = posted[0].body;
  assert.strictEqual(posted[0].url, '/propagacion/m2');
  assert.strictEqual(JSON.stringify(b.focos[0]), JSON.stringify([lat, lon]));
  assert.strictEqual(b.cobertura.n, 32); assert.strictEqual(b.cobertura.valores.length, 1024); assert.strictEqual(b.cobertura.valores[0], 20);
  assert.strictEqual(b.elevacion.n, 16); assert.strictEqual(b.elevacion.valores.length, 256);
  assert.strictEqual(b.temp_max, 26, 'sin proxy usa la temperatura actual');
  assert.ok(layers['m2-layer'] && sources['m2-cells'], 'se dibujó la capa');
  assert.strictEqual(layers['m2-layer'].type, 'fill');
  assert.strictEqual(sources['m2-cells'].data.features.length, 3, 'una celda por nivel > 0');
  const card = els['m2-card'].innerHTML;
  assert.ok(card.includes('muy probable') && card.includes('no son porcentajes'));
  assert.ok(card.includes('Prueba &lt;b>'), 'las advertencias del servidor se escapan');
  assert.ok(card.includes('temperatura actual') || card.includes('Se usó la temperatura actual'));
  console.log('ok - flujo completo: arma el cuerpo, llama al backend, dibuja y muestra la tarjeta');

  api.clearM2();
  assert.ok(!layers['m2-layer'] && !sources['m2-cells'] && els['m2-card'].style.display === 'none');
  console.log('ok - quitar la capa limpia mapa y tarjeta');

  ctx.alertsState.token = '';
  await api.runM2(lat, lon);
  assert.strictEqual(posted.length, 1, 'sin sesión no consulta');
  assert.ok(els['m2-card'].innerHTML.includes('Inicia sesión'));
  console.log('ok - sin sesión pide iniciar sesión y no llama al backend');

  ctx.alertsState.token = 'T';
  ctx.fuelMosaic.cell = () => null;
  await api.runM2(lat, lon);
  assert.strictEqual(posted.length, 1);
  assert.ok(els['m2-card'].innerHTML.includes('No hay datos de vegetación'));
  console.log('ok - sin vegetación avisa y no llama al backend');

  ctx.fuelMosaic.cell = () => ({ dominant: { key: 'bosque' } });
  ctx.fetchElevationLattice = async () => { throw new Error('HTTP 429'); };
  await api.runM2(lat, lon);
  assert.strictEqual(posted.length, 2);
  assert.ok(!('elevacion' in posted[1].body), 'sin altura se manda sin elevacion');
  assert.ok(els['m2-card'].innerHTML.includes('terreno plano'));
  console.log('ok - si falla la altura sigue y lo avisa');

  ctx.alertsFetch = async () => ({ ok: false, status: 422, json: async () => ({ error: 'ningún foco cae dentro del parche' }) });
  await api.runM2(lat, lon);
  assert.ok(els['m2-card'].innerHTML.includes('ningún foco'));
  console.log('ok - muestra el error del backend');

  assert.ok(api.m2ButtonHTML(1.5, 2.5).includes('data-lat="1.5"'));
  console.log('\n6 pruebas OK');
})().catch((e) => { console.error(e); process.exit(1); });
