// ============================================================
// FireWatch · Mapa 3D — acceso a datos y estado de las fuentes
//
// Dos modos de operación:
//   - CON proxy local (python server.py): las credenciales (token DMC,
//     MAP_KEY de FIRMS) viven en .env, las respuestas se cachean en disco
//     y el navegador nunca ve una key.
//   - SIN proxy (hosting estático, p. ej. GitHub Pages): solo funcionan
//     las fuentes que no piden credenciales (Open-Meteo, Overpass,
//     Planetary Computer y el catálogo de estaciones DMC). FIRMS y las
//     mediciones DMC quedan desactivadas con un aviso claro.
//     (Para producción: pasar el proxy a un serverless gratuito.)
// ============================================================

const FW = {
  proxy: false,   // ¿hay proxy local respondiendo?
  dmc: false,     // ¿el proxy tiene token DMC?
  firms: false,   // ¿el proxy tiene MAP_KEY de FIRMS?
  tomtom: false,  // ¿el proxy tiene TOMTOM_KEY (tráfico en vivo)?
  archive: false, // ¿el proxy guarda un archivo histórico (SQLite)?
};

async function initDataAccess() {
  try {
    const res = await fetch('/api/status', { cache: 'no-store' });
    if (res.ok) {
      const s = await res.json();
      FW.proxy = s.ok === true;
      FW.dmc = !!s.dmc;
      FW.firms = !!s.firms;
      FW.tomtom = !!s.tomtom;
      FW.archive = !!s.archive;
    }
  } catch (_) { /* sin proxy: modo estático */ }
  console.info('[FW] modo de datos:', FW);
  return FW;
}

/** Muestra en el panel cuántos datos hay guardados en el archivo histórico. */
async function refreshArchiveStatus() {
  if (!FW.proxy || !FW.archive) { reportSource('archive', 'off', FW.proxy ? 'desactivado' : 'requiere el servidor local'); return; }
  try {
    const s = await (await fetch('/api/store/stats', { cache: 'no-store' })).json();
    const counts = `${s.weather_obs} lecturas de clima · ${s.fire_detections} focos · ${s.station_obs} de estaciones · ${s.fuel_cells} celdas · ${s.size_mb} MB`;
    if (s.errors_24h > 0) {
      const le = s.last_error;
      reportSource('archive', 'warn', `${counts} · ${s.errors_24h} error(es) en 24 h` +
        (le ? ` (último: ${le.source}${le.variant ? ':' + le.variant : ''} · ${le.error_type})` : '') + ' — ver registro');
    } else if (s.warnings_24h > 0) {
      reportSource('archive', 'ok', `${counts} · ${s.warnings_24h} aviso(s) en 24 h — ver registro`);
    } else {
      reportSource('archive', 'ok', counts);
    }
  } catch (err) { reportSource('archive', 'error', err.message); }
}

/** Repara UTF-8 mal codificado (la DMC devuelve 'SÃ¡nchez' en vez de 'Sánchez'). */
function fixMojibake(s) {
  if (typeof s !== 'string' || !/[ÃÂ]/.test(s)) return s;
  try { return decodeURIComponent(escape(s)); } catch (_) { return s; }
}

// ---- almacenamiento local seguro (puede no estar disponible) ----
function lsGet(key, maxAgeMs) {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return null;
    const { t, v } = JSON.parse(raw);
    if (maxAgeMs && Date.now() - t > maxAgeMs) return null;
    return { value: v, age: Date.now() - t };
  } catch (_) { return null; }
}
function lsSet(key, value) {
  try { localStorage.setItem(key, JSON.stringify({ t: Date.now(), v: value })); } catch (_) { /* lleno o bloqueado */ }
}

// ---- caché grande en IndexedDB (localStorage se llena con ~5 MB; los grifos de todo Chile pesan varios MB) ----
let _fwIdb = null;
function fwIdb() {
  if (!_fwIdb) {
    _fwIdb = new Promise((resolve) => {
      try {
        const rq = indexedDB.open('firewatch-cache', 1);
        rq.onupgradeneeded = () => rq.result.createObjectStore('kv');
        rq.onsuccess = () => resolve(rq.result);
        rq.onerror = () => resolve(null);
      } catch (_) { resolve(null); }
    });
  }
  return _fwIdb;
}
/** Lee { value, age } si existe y no pasó `maxAgeMs` (0 = sin límite de edad); si no, null. Nunca lanza. */
async function fwCacheGet(key, maxAgeMs) {
  try {
    const db = await fwIdb(); if (!db) return null;
    const rec = await new Promise((resolve) => { const r = db.transaction('kv').objectStore('kv').get(key); r.onsuccess = () => resolve(r.result); r.onerror = () => resolve(null); });
    if (!rec || (maxAgeMs && Date.now() - rec.t > maxAgeMs)) return null;
    return { value: rec.v, age: Date.now() - rec.t };
  } catch (_) { return null; }
}
async function fwCacheSet(key, value) {
  try {
    const db = await fwIdb(); if (!db) return;
    await new Promise((resolve) => { const tx = db.transaction('kv', 'readwrite'); tx.objectStore('kv').put({ t: Date.now(), v: value }, key); tx.oncomplete = () => resolve(); tx.onerror = () => resolve(); tx.onabort = () => resolve(); });
  } catch (_) { /* sin caché: se vuelve a pedir */ }
}

// ---- registro de estado de cada fuente (lo muestra el panel "Fuentes") ----
const SOURCE_LABELS = {
  weather: 'Viento y temperatura (Open-Meteo)',
  weatherHist: 'Clima histórico del escenario (Open-Meteo)',
  weatherNational: 'Clima de todo Chile (Open-Meteo, grilla de 1,25°)',
  zones: 'Zonas detalladas al acercar (OSM + vegetación)',
  fuel: 'Cobertura vegetal (ESA WorldCover)',
  water: 'Fuentes de agua (OSM)',
  hydrants: 'Grifos (OSM)',
  firestations: 'Estaciones de bomberos (OSM)',
  firms: 'Focos de calor (NASA FIRMS)',
  dmc: 'Estaciones DMC (Meteochile)',
  routing: 'Rutas de emergencia (OSRM / TomTom)',
  alerts: 'Alertas de la central (API FireWatch)',
  spread: 'Simulación de propagación (reglas)',
  traffic: 'Tráfico en vivo (TomTom)',
  archive: 'Archivo histórico (SQLite)',
};
const sourceStatus = {}; // key -> { state: 'ok'|'warn'|'error'|'off', detail, at }

function reportSource(key, state, detail) {
  sourceStatus[key] = { state, detail: detail || '', at: new Date() };
  renderSourcePanel();
  // Cada error/aviso de una fuente queda también en el registro del servidor (salvo el propio archivo: sería un bucle).
  if ((state === 'error' || state === 'warn') && key !== 'archive') {
    fwLog('fuente:' + key, state, detail || '(sin detalle)', { phase: 'estado', dedupe: `fuente:${key}:${state}`, windowMs: 5 * 60 * 1000 });
  }
}

// ---- registro de errores del navegador ----
// Los errores y avisos (fuente caída, 429, fallas de Overpass, errores de JavaScript…) se mandan al
// servidor local, que los anota en data/ingest.log y en la base SQLite con origen «browser».
// Se leen en  http://localhost:8080/api/store/log.txt?errors=1  (o en la consola: FW_LOG.recent).
// Sin proxy solo quedan en memoria. Se evita el spam: mismo error → una vez por minuto, tope de 300.
const FW_LOG = { sent: new Map(), count: 0, max: 300, recent: [] };
function fwLog(source, level, message, extra = {}) {
  try {
    const msg = String((message && message.message) || message || '').slice(0, 400);
    const lvl = level === 'warn' ? 'warn' : 'error';
    const key = extra.dedupe || `${source}|${lvl}|${extra.variant || ''}|${msg}`;
    const now = Date.now(), last = FW_LOG.sent.get(key);
    if (last && now - last < (extra.windowMs || 60000)) return;
    FW_LOG.sent.set(key, now);
    if (FW_LOG.count >= FW_LOG.max) return;
    FW_LOG.count++;
    FW_LOG.recent.push({ at: new Date().toISOString(), source, level: lvl, message: msg, variant: extra.variant || '', phase: extra.phase || '', detail: extra.detail || '' });
    if (FW_LOG.recent.length > 100) FW_LOG.recent.shift();
    if (!FW.proxy) return;
    fetch('/api/clientlog', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, keepalive: true,
      body: JSON.stringify({ source, level: lvl, message: msg, variant: extra.variant || '', phase: extra.phase || '', detail: String(extra.detail || '') }),
    }).catch(() => { /* el registro nunca debe romper el mapa */ });
  } catch (_) { /* idem */ }
}
window.addEventListener('error', (e) => fwLog('javascript', 'error', e.message, { phase: 'excepción', detail: `${(e.filename || '').split('/').pop()}:${e.lineno}` }));
window.addEventListener('unhandledrejection', (e) => fwLog('javascript', 'error', (e.reason && e.reason.message) || e.reason || 'promesa rechazada', { phase: 'promesa' }));

function renderSourcePanel() {
  const el = document.getElementById('source-status');
  if (!el) return;
  const icon = { ok: '●', warn: '●', error: '●', off: '○' };
  el.innerHTML = Object.keys(SOURCE_LABELS).filter((k) => k !== 'weatherHist' || sourceStatus.weatherHist).map((k) => {
    const s = sourceStatus[k] || { state: 'off', detail: 'pendiente' };
    return `<div class="src-row src-${s.state}"><span class="src-dot">${icon[s.state]}</span>` +
      `<span class="src-name">${SOURCE_LABELS[k]}</span>` +
      `<span class="src-detail">${s.detail}</span></div>`;
  }).join('');
}

/** Distancia aproximada en km entre dos puntos (suficiente a escala ciudad). */
function approxKm(lat1, lon1, lat2, lon2) {
  const dLat = (lat2 - lat1) * 111.32;
  const dLon = (lon2 - lon1) * 111.32 * Math.cos(((lat1 + lat2) / 2) * Math.PI / 180);
  return Math.hypot(dLat, dLon);
}
