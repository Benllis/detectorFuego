// Pruebas del cliente de Overpass: sale por el proxy local y solo cae a la vía directa si el proxy no existe.
// Ejecutar: node tests/overpass-proxy.test.js
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SRC = fs.readFileSync(path.join(__dirname, '..', 'js', 'overpass-utils.js'), 'utf8');
const OK = { elements: [{ type: 'node', id: 1 }] };
const clon = (x) => JSON.parse(JSON.stringify(x));   // como el navegador: cada lectura entrega un objeto nuevo
const jsonRes = (status, body) => ({ ok: status >= 200 && status < 300, status, json: async () => clon(body) });

/** Contexto limpio por prueba; `route` decide qué responde fetch según la URL. */
function entorno(route) {
  const llamadas = [], logs = [], cache = {};
  const ctx = {
    console, setTimeout, clearTimeout, URL, AbortController, encodeURIComponent, JSON, Object, Error, Promise,
    fetch: async (url, init) => { llamadas.push({ url, init }); return route(url, init); },
    lsGet: (k) => (cache[k] ? { value: clon(cache[k].value), age: 0 } : null), lsSet: (k, v) => { cache[k] = { value: clon(v), age: 0 }; },
    fwCacheGet: async () => null, fwCacheSet: () => {},
    fwLog: (...a) => logs.push(a),
  };
  vm.createContext(ctx);
  vm.runInContext(SRC, ctx);
  return { ctx, llamadas, logs, cache };
}
let n = 0;
const test = async (name, fn) => { await fn(); n++; console.log('ok -', name); };

(async () => {
  await test('sale por el proxy local con la consulta en JSON', async () => {
    const { ctx, llamadas } = entorno(() => jsonRes(200, OK));
    const r = await ctx.fetchOverpass('[out:json];node(1);out;');
    assert.deepStrictEqual(JSON.parse(JSON.stringify(r)), OK);
    assert.strictEqual(llamadas.length, 1);
    assert.strictEqual(llamadas[0].url, '/api/overpass');
    assert.strictEqual(llamadas[0].init.method, 'POST');
    assert.strictEqual(JSON.parse(llamadas[0].init.body).query, '[out:json];node(1);out;');
  });

  await test('no consulta directo a los servidores públicos cuando el proxy funciona', async () => {
    const { ctx, llamadas } = entorno(() => jsonRes(200, OK));
    await ctx.fetchOverpass('[out:json];node(1);out;');
    await ctx.fetchOverpass('[out:json];node(2);out;');
    assert.ok(llamadas.every((l) => l.url === '/api/overpass'));
  });

  await test('con caché guardado no llama a nada', async () => {
    const { ctx, llamadas } = entorno(() => jsonRes(200, OK));
    await ctx.fetchOverpass('[out:json];node(1);out;', 'k1');
    await ctx.fetchOverpass('[out:json];node(1);out;', 'k1');
    assert.strictEqual(llamadas.length, 1);
  });

  await test('si el servidor no tiene /api/overpass (404) cae a la vía directa y lo recuerda', async () => {
    const { ctx, llamadas } = entorno((url) => (url === '/api/overpass' ? jsonRes(404, {}) : jsonRes(200, OK)));
    const r = await ctx.fetchOverpass('[out:json];node(1);out;');
    assert.deepStrictEqual(JSON.parse(JSON.stringify(r)), OK);
    assert.deepStrictEqual(llamadas.map((l) => l.url), ['/api/overpass', 'https://overpass-api.de/api/interpreter']);
    await ctx.fetchOverpass('[out:json];node(2);out;');
    assert.strictEqual(llamadas.filter((l) => l.url === '/api/overpass').length, 1, 'no vuelve a probar el proxy');
  });

  await test('si el proxy no es alcanzable (servidor local caído) también usa la vía directa', async () => {
    const { ctx, llamadas } = entorno((url) => { if (url === '/api/overpass') throw new TypeError('Failed to fetch'); return jsonRes(200, OK); });
    await ctx.fetchOverpass('[out:json];node(1);out;');
    assert.strictEqual(llamadas[1].url, 'https://overpass-api.de/api/interpreter');
  });

  await test('si el proxy responde 502 NO repite directo: ya probó ambos servidores', async () => {
    const { ctx, llamadas } = entorno(() => jsonRes(502, { error: 'caído', kind: 'upstream' }));
    await assert.rejects(() => ctx.fetchOverpass('[out:json];node(1);out;'), /HTTP 502.*caído/);
    assert.strictEqual(llamadas.length, 1);
  });

  await test('si el proxy falla y hay dato viejo guardado, lo entrega como "stale"', async () => {
    const { ctx, cache } = entorno(() => jsonRes(502, { error: 'caído' }));
    cache['fw:ovp:k'] = { value: OK, age: 1e12 };           // lsGet(ck, 0) lo devuelve aunque sea viejo
    const orig = ctx.lsGet; ctx.lsGet = (k, max) => (max ? null : orig(k));   // vencido para el caché normal (maxAge > 0)
    const r = await ctx.fetchOverpass('[out:json];node(1);out;', 'k');
    assert.strictEqual(r._fromCache, 'stale');
  });

  await test('un "remark" de timeout dentro de una respuesta 200 del proxy es un error', async () => {
    const { ctx } = entorno(() => jsonRes(200, { elements: [], remark: 'runtime error: Query timed out' }));
    await assert.rejects(() => ctx.fetchOverpass('[out:json];node(1);out;'), /timed out/);
  });

  console.log(`\n${n} pruebas ok`);
})().catch((e) => { console.error(e); process.exit(1); });
