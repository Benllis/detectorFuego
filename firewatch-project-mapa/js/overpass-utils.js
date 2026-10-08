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
// Usado por water-layer.js, hydrants-layer.js y firestations-layer.js.
// ============================================================

const OVERPASS_SERVERS = [
  'https://overpass-api.de/api/interpreter',
  'https://maps.mail.ru/osm/tools/overpass/api/interpreter',
];
const OVERPASS_ATTEMPT_TIMEOUT_MS = 35000;
const OVERPASS_CACHE_MS = 24 * 60 * 60 * 1000;

function sleep(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }

async function overpassAttempt(url, query) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), OVERPASS_ATTEMPT_TIMEOUT_MS);
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
    if (err.name === 'AbortError') throw new Error(`Sin respuesta en ${OVERPASS_ATTEMPT_TIMEOUT_MS / 1000}s (${new URL(url).host})`);
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Ejecuta una consulta Overpass QL. `cacheKey` (opcional) activa el caché de 24 h.
 * Devuelve el JSON de Overpass; agrega la propiedad no enumerable `_fromCache`
 * ('fresh' | 'stale' | undefined) para que la capa pueda informarlo.
 */
async function fetchOverpass(query, cacheKey) {
  const ck = cacheKey ? `fw:ovp:${cacheKey}` : null;
  if (ck) {
    const hit = lsGet(ck, OVERPASS_CACHE_MS);
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
  for (const attempt of attempts) {
    if (attempt.delayBefore) await sleep(attempt.delayBefore);
    try {
      const json = await overpassAttempt(attempt.url, query);
      if (ck) lsSet(ck, json);
      return json;
    } catch (err) {
      lastError = err;
    }
  }

  if (ck) {
    const old = lsGet(ck, 0);
    if (old) {
      console.warn('[Overpass] todos los servidores fallaron; usando dato guardado:', lastError && lastError.message);
      Object.defineProperty(old.value, '_fromCache', { value: 'stale', enumerable: false });
      return old.value;
    }
  }
  throw lastError || new Error('Overpass no respondió en ningún servidor.');
}
