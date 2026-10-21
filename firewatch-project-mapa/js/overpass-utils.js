// ============================================================
// FireWatch · Mapa 3D — helper compartido para consultas a Overpass
//
// El servidor público principal (overpass-api.de) es gratis pero real:
// se satura y devuelve 504/429 bajo carga. Este helper:
//   - reintenta solo (2 intentos en el principal, 1 en el mirror),
//   - pone un tope de tiempo a cada intento (antes un servidor colgado
//     dejaba la carga esperando indefinidamente),
//   - cae a un mirror que SÍ responde (verificado 28-09-2026:
//     maps.mail.ru OK; overpass.kumi.systems y overpass.private.coffee
//     dieron timeout, por eso se reemplazó el mirror anterior),
//   - cachea el resultado 24 h en localStorage: agua, grifos y cuarteles
//     casi no cambian, y así recargar la página no vuelve a golpear
//     al servidor público. Si todo falla, usa el último dato guardado.
//
// Por dónde sale la consulta: primero por el servidor local del mapa (POST /api/overpass, ver server.py).
// El navegador NO puede leer la respuesta directa de los servidores públicos cuando no mandan cabeceras
// CORS (error "blocked by CORS policy"); el servidor local no tiene esa restricción, además reintenta,
// cae al mirror y guarda 24 h en disco. Solo si el mapa se sirve con otro servidor (sin /api/overpass)
// o el servidor local no responde, se prueba directo como antes.
//
// Usado por water-layer.js, hydrants-layer.js y firestations-layer.js.
// ============================================================

const OVERPASS_SERVERS = [
  'https://overpass-api.de/api/interpreter',
  'https://maps.mail.ru/osm/tools/overpass/api/interpreter',
];
const OVERPASS_ATTEMPT_TIMEOUT_MS = 35000;
const OVERPASS_CACHE_MS = 24 * 60 * 60 * 1000;
const OVERPASS_PROXY_URL = '/api/overpass';
let overpassProxyOk = null; // null = aún no se sabe; false = no existe o no responde (se usa la vía directa)

function sleep(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }

async function overpassAttempt(url, query, timeoutMs = OVERPASS_ATTEMPT_TIMEOUT_MS) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(url, {
      method: 'POST',
      body: 'data=' + encodeURIComponent(query),
      signal: ctrl.signal,
    });
    if (!res.ok) throw new Error(`HTTP ${res.status} (${new URL(url).host})`);
    const json = await res.json();
    // Overpass a veces responde 200 con un "remark" de timeout/memoria y datos incompletos.
    if (json.remark && /timed out|out of memory|runtime error/i.test(json.remark)) {
      throw new Error(`Overpass: ${json.remark} (${new URL(url).host})`);
    }
    return json;
  } catch (err) {
    if (err.name === 'AbortError') throw new Error(`Sin respuesta en ${timeoutMs / 1000}s (${new URL(url).host})`);
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

/** Consulta por el servidor local. Lanza con `noProxy` si el proxy no existe o no se puede alcanzar. */
async function overpassViaProxy(query, timeoutMs = OVERPASS_ATTEMPT_TIMEOUT_MS) {
  const ctrl = new AbortController();
  // el servidor ya hace hasta 3 intentos (principal, principal, mirror): se le da tiempo para todos
  const timer = setTimeout(() => ctrl.abort(), timeoutMs * 3 + 10000);
  let res;
  try {
    res = await fetch(OVERPASS_PROXY_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query }),
      signal: ctrl.signal,
    });
  } catch (err) {
    if (err.name === 'AbortError') throw new Error('Sin respuesta del proxy local de Overpass');
    throw Object.assign(new Error('proxy local de Overpass inalcanzable'), { noProxy: true });
  } finally {
    clearTimeout(timer);
  }
  if (res.status === 404 || res.status === 405) throw Object.assign(new Error('este servidor no tiene /api/overpass'), { noProxy: true });
  if (!res.ok) {
    let detail = '';
    try { detail = (await res.json()).error || ''; } catch (_) { /* sin cuerpo JSON */ }
    throw new Error(`HTTP ${res.status} (proxy local de Overpass)${detail ? ': ' + detail : ''}`);
  }
  const json = await res.json();
  if (json.remark && /timed out|out of memory|runtime error/i.test(json.remark)) {
    throw new Error(`Overpass: ${json.remark} (proxy local)`);
  }
  return json;
}

/**
 * Ejecuta una consulta Overpass QL. `cacheKey` (opcional) activa el caché de 24 h.
 * Devuelve el JSON de Overpass; agrega la propiedad no enumerable `_fromCache`
 * ('fresh' | 'stale' | undefined) para que la capa pueda informarlo.
 */
async function fetchOverpass(query, cacheKey, opts = {}) {
  const ck = cacheKey ? `fw:ovp:${cacheKey}` : null;
  if (ck) {
    const hit = lsGet(ck, OVERPASS_CACHE_MS) || await fwCacheGet(ck, OVERPASS_CACHE_MS);
    if (hit) {
      Object.defineProperty(hit.value, '_fromCache', { value: 'fresh', enumerable: false });
      return hit.value;
    }
  }

  const attempts = [
    { url: OVERPASS_SERVERS[0], delayBefore: 0 },
    { url: OVERPASS_SERVERS[0], delayBefore: 2500 },
    { url: OVERPASS_SERVERS[1], delayBefore: 1000 },
  ];

  let lastError = null;
  let solved = null;
  if (overpassProxyOk !== false) {
    try {
      solved = await overpassViaProxy(query, opts.timeoutMs);
      overpassProxyOk = true;
    } catch (err) {
      if (err.noProxy) overpassProxyOk = false;          // sin proxy: se sigue con la vía directa
      else lastError = err;                              // el proxy sí respondió y Overpass falló: ya probó ambos servidores
      if (typeof fwLog === 'function') fwLog('overpass', 'warn', err.message, { phase: 'proxy', variant: 'local', detail: query.replace(/\s+/g, ' ').slice(0, 200) });
    }
    if (solved) {
      if (ck) { if (!opts.bigCache) lsSet(ck, solved); fwCacheSet(ck, solved); }
      return solved;
    }
  }

  for (const attempt of (overpassProxyOk === false ? attempts : [])) {
    if (attempt.delayBefore) await sleep(attempt.delayBefore);
    try {
      const json = await overpassAttempt(attempt.url, query, opts.timeoutMs);
      if (ck) { if (!opts.bigCache) lsSet(ck, json); fwCacheSet(ck, json); } // localStorage (si cabe) + IndexedDB; lo grande, solo IndexedDB
      return json;
    } catch (err) {
      lastError = err;
      if (typeof fwLog === 'function') fwLog('overpass', 'warn', err.message, { phase: 'intento', variant: new URL(attempt.url).host, detail: query.replace(/\s+/g, ' ').slice(0, 200) });
    }
  }

  if (ck) {
    const old = lsGet(ck, 0) || await fwCacheGet(ck, 0);
    if (old) {
      console.warn('[Overpass] todos los servidores fallaron; usando dato guardado:', lastError && lastError.message);
      Object.defineProperty(old.value, '_fromCache', { value: 'stale', enumerable: false });
      return old.value;
    }
  }
  throw lastError || new Error('Overpass no respondió en ningún servidor.');
}
