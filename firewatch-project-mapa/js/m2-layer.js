// ============================================================
// FireWatch · Mapa 3D — capa «continuación probable» del modelo M2
//
// El botón «🛰️ Continuación probable (M2)» (en el popup de una alerta de la central o de un foco de
// calor) le pide al backend (POST /propagacion/m2) en qué celdas de ~1 km es más probable que haya
// fuego AL DÍA SIGUIENTE, a partir de un foco ya detectado. Se pinta en tres niveles relativos:
// rojo = muy probable, naranja = probable, amarillo = posible (celdas vectoriales, no imagen).
//
// QUÉ ES Y QUÉ NO ES (importa para la defensa del proyecto):
//   · Es la continuación de un incendio YA detectado. No predice incendios nuevos.
//   · No es una dirección de avance fina: el viento pesa poco en este modelo (la zona sale cerca del
//     foco). La dirección y el tiempo de llegada los da la simulación por reglas («🔥 Simular propagación»).
//   · Los niveles NO son porcentajes: el modelo se entrenó con el 5 % de los negativos, así que su salida
//     sirve para ordenar celdas, no como probabilidad.
//
// El mapa arma las entradas con lo que ya tiene (clima activo, focos FIRMS, vegetación y altura) y el
// backend las valida y corre el modelo. Las funciones «fwM2*» de arriba no tocan el DOM: se prueban en
// Node (tests/m2.test.js).
// ============================================================

// ---------------------------------------------------------------- lógica pura
const FW_M2_CELL = 0.01;               // grados por celda del modelo (≈ 1,1 km)
const FW_M2_SIDE = 64;                 // celdas por lado del parche (≈ 71 km)
const FW_M2_CENTER = 32;               // la celda del foco es (fila 32, columna 32)
const FW_M2_FOCO_MAX_HORAS = 36;       // el modelo se entrenó con el fuego del día anterior
const FW_M2_FOCOS_MAX = 500;
const FW_M2_FUEL_N = 32;               // vegetación: una muestra cada ~2,2 km
const FW_M2_ELEV_N = 16;               // altura: una muestra cada ~4,4 km (3 peticiones; el modelo tolera altura gruesa)
const FW_M2_ESA = { bosque: 10, matorral: 20, pastizal: 30, cultivo: 40, urbano: 50, desnudo: 60, nieve: 70, agua: 80, humedal: 90, manglar: 95, musgo: 100 };
const FW_M2_LEVELS = [                 // [nivel, etiqueta, rgba]
  [1, 'muy probable', [229, 72, 77, 190]],
  [2, 'probable', [255, 138, 0, 160]],
  [3, 'posible', [255, 214, 10, 120]],
];

/** Caja del parche. Igual que en el backend: la celda (32, 32) está centrada en el foco. */
function fwM2Bbox(lat, lon) {
  const lo = -FW_M2_CENTER * FW_M2_CELL - FW_M2_CELL / 2;
  const hi = (FW_M2_SIDE - 1 - FW_M2_CENTER) * FW_M2_CELL + FW_M2_CELL / 2;
  return { south: lat + lo, north: lat + hi, west: lon + lo, east: lon + hi };
}

/** Centros de una malla n x n que cubre todo el parche. Fila 0 = sur, columna 0 = oeste (como pide el backend). */
function fwM2BlockCenters(lat, lon, n) {
  const b = fwM2Bbox(lat, lon);
  const dLat = (b.north - b.south) / n, dLon = (b.east - b.west) / n;
  const coords = [];
  for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) coords.push([b.south + (r + 0.5) * dLat, b.west + (c + 0.5) * dLon]);
  return { lat0: b.south + dLat / 2, lon0: b.west + dLon / 2, dLat, dLon, nLat: n, nLon: n, coords };
}

/**
 * Focos que le damos al modelo: el de la alerta (siempre, primero) más los de satélite dentro del parche y de las
 * últimas 36 h respecto de `tRefMs`. Uno por celda de 0,01°. Si hay más de 500 quedan los más cercanos al foco.
 */
function fwM2Focos(pixels, lat, lon, tRefMs, maxHoras = FW_M2_FOCO_MAX_HORAS) {
  const b = fwM2Bbox(lat, lon), seen = new Set(), out = [];
  const key = (la, lo) => `${Math.round((la - lat) / FW_M2_CELL)}:${Math.round((lo - lon) / FW_M2_CELL)}`;
  const add = (la, lo) => { const k = key(la, lo); if (!seen.has(k)) { seen.add(k); out.push([la, lo]); } };
  add(lat, lon);
  (pixels || []).filter((p) => p && Number.isFinite(p.lat) && Number.isFinite(p.lon) && Number.isFinite(p.ts)
    && p.lat > b.south && p.lat < b.north && p.lon > b.west && p.lon < b.east
    && tRefMs - p.ts <= maxHoras * 3600e3 && p.ts - tRefMs <= 3 * 3600e3)
    .sort((a, c) => Math.hypot(a.lat - lat, a.lon - lon) - Math.hypot(c.lat - lat, c.lon - lon))
    .forEach((p) => { if (out.length < FW_M2_FOCOS_MAX) add(p.lat, p.lon); });
  return out;
}

/** Clases ESA (10, 20, …) en una malla n x n. `cellAt(lat, lon)` devuelve la celda de la grilla de combustible o null. */
function fwM2ClassGrid(cellAt, lat, lon, n = FW_M2_FUEL_N) {
  const { coords } = fwM2BlockCenters(lat, lon, n);
  let known = 0;
  const valores = coords.map(([la, lo]) => {
    const c = cellAt(la, lo);
    const code = c && c.dominant ? FW_M2_ESA[c.dominant.key] : null;
    if (code != null) known++;
    return code == null ? null : code;
  });
  return { grid: { n, valores }, coverage: known / valores.length };
}

/** Cuerpo del POST /propagacion/m2. `w` = {speed km/h, dirDeg (de dónde sopla), temp, rh}; `tempMax` pisa a w.temp si existe. */
function fwM2Payload({ lat, lon, focos, w, tempMax, cobertura, elevacion }) {
  const body = {
    lat, lon, focos,
    viento_kmh: Math.max(0, Number(w.speed) || 0),
    viento_dir_deg: Number.isFinite(w.dirDeg) ? ((w.dirDeg % 360) + 360) % 360 : 0,
    temp_max: Number.isFinite(tempMax) ? tempMax : Number(w.temp),
    humedad: Number.isFinite(w.rh) ? Math.min(100, Math.max(0, w.rh)) : 40,
    cobertura,
  };
  if (elevacion) body.elevacion = elevacion;
  return body;
}

/** Máximo de temperatura de las próximas 24 h (desde `nowMs`) en la respuesta horaria de Open-Meteo, o null. */
function fwM2TempMax(hourly, nowMs) {
  if (!hourly || !Array.isArray(hourly.time) || !Array.isArray(hourly.temperature_2m)) return null;
  let best = null;
  hourly.time.forEach((t, i) => {
    const ms = Date.parse(String(t).length === 16 ? `${t}:00Z` : t);
    const v = hourly.temperature_2m[i];
    if (Number.isFinite(ms) && Number.isFinite(v) && ms >= nowMs - 3600e3 && ms <= nowMs + 24 * 3600e3) best = best === null ? v : Math.max(best, v);
  });
  return best;
}

/**
 * Celdas del backend como polígonos (una por celda con nivel > 0). Se dibujan como capa vectorial y no como imagen
 * raster: un raster sobre el terreno 3D del mapa salía como un cuadrado negro opaco.
 * `niveles`: fila 0 = sur, columna 0 = oeste; `bbox`: {sur, norte, oeste, este}.
 */
function fwM2Cells(niveles, filas, columnas, bbox) {
  const dLat = (bbox.norte - bbox.sur) / filas, dLon = (bbox.este - bbox.oeste) / columnas;
  const features = [];
  for (let r = 0; r < filas; r++) {
    for (let c = 0; c < columnas; c++) {
      const nivel = niveles[r * columnas + c];
      if (!(nivel >= 1 && nivel <= 3)) continue;
      const s = bbox.sur + r * dLat, w = bbox.oeste + c * dLon, n = s + dLat, e = w + dLon;
      features.push({ type: 'Feature', properties: { nivel }, geometry: { type: 'Polygon', coordinates: [[[w, s], [e, s], [e, n], [w, n], [w, s]]] } });
    }
  }
  return { type: 'FeatureCollection', features };
}

if (typeof module !== 'undefined') {
  module.exports = { FW_M2_ESA, FW_M2_LEVELS, FW_M2_FUEL_N, FW_M2_ELEV_N, fwM2Bbox, fwM2BlockCenters, fwM2Focos, fwM2ClassGrid, fwM2Payload, fwM2TempMax, fwM2Cells };
}

// ---------------------------------------------------------------- navegador
const M2_RADIUS_KM = 36;   // medio lado del parche (≈ 71 km / 2)
const m2State = { map: null, ctx: null, seq: 0 };

function m2ButtonHTML(lat, lon, label = '🛰️ Continuación probable (M2)') {
  return `<button class="fw-zoom fw-m2-btn" type="button" data-lat="${lat}" data-lon="${lon}">${label}</button>`;
}

function m2Style() {
  if (document.getElementById('fw-m2-style')) return;
  const st = document.createElement('style');
  st.id = 'fw-m2-style';
  st.textContent = `.fw-m2-card{top:auto;bottom:112px}
    @media (max-width:1100px){.fw-m2-card{bottom:112px}}`;
  document.head.appendChild(st);
}

function m2Card() {
  let el = document.getElementById('m2-card');
  if (!el) {
    el = document.createElement('div');
    el.id = 'm2-card'; el.className = 'fw-spread-card fw-m2-card';
    document.body.appendChild(el);
  }
  return el;
}

function m2Note(html, withClear) {
  const el = m2Card();
  el.innerHTML = html + (withClear ? '<button class="btn" id="btn-m2-clear" type="button">Quitar capa M2</button>' : '');
  el.style.display = html ? 'block' : 'none';
  const b = document.getElementById('btn-m2-clear');
  if (b) b.addEventListener('click', clearM2);
}

function clearM2() {
  m2State.seq++;
  const map = m2State.map;
  if (map) {
    if (map.getLayer('m2-layer')) map.removeLayer('m2-layer');
    if (map.getSource('m2-cells')) map.removeSource('m2-cells');
  }
  m2Note('');
}

function m2Draw(map, res) {
  const { filas, columnas, niveles } = res.grilla;
  map.addSource('m2-cells', { type: 'geojson', data: fwM2Cells(niveles, filas, columnas, res.parche.bbox) });
  const color = ['match', ['get', 'nivel']];
  FW_M2_LEVELS.forEach((l) => color.push(l[0], `rgb(${l[2][0]},${l[2][1]},${l[2][2]})`));
  color.push('#888888');
  const before = map.getLayer('fire-pixels-glow') ? 'fire-pixels-glow' : undefined;
  map.addLayer({ id: 'm2-layer', type: 'fill', source: 'm2-cells', paint: { 'fill-color': color, 'fill-opacity': 0.5, 'fill-outline-color': 'rgba(255,255,255,0.35)' } }, before);
}

const m2Fmt = (x, d = 1) => Number(x).toLocaleString('es-CL', { minimumFractionDigits: d, maximumFractionDigits: d });

function m2CardHTML(res, info) {
  const rows = res.niveles.map((n) => {
    const def = FW_M2_LEVELS.find((l) => l[0] === n.nivel);
    return `<tr><td><span class="fw-spread-sw" style="background:rgba(${def[2][0]},${def[2][1]},${def[2][2]},1)"></span>${fwEscape(n.etiqueta)}</td>` +
      `<td>${n.celdas}</td><td>${m2Fmt(n.area_km2)} km²</td><td>${m2Fmt(n.alcance_km)} km</td></tr>`;
  }).join('');
  const warns = (res.advertencias || []).concat(info.avisos || []).map((a) => `<div style="color:#ff8a00">⚠ ${fwEscape(a)}</div>`).join('');
  return `<h3>🛰️ Continuación probable (M2)</h3>
    <div class="fw-dim">Dónde es más probable que haya fuego al día siguiente, a partir de un foco ya detectado. Celdas de ~1 km.</div>
    <table><tr><th>Nivel</th><th>Celdas</th><th>Área</th><th>Alcance*</th></tr>${rows}</table>
    <div class="fw-dim">* distancia máxima al foco más cercano, contando ese nivel y los más probables.</div>
    ${warns}
    <div>Entradas: ${res.focos_usados} foco(s) · viento ${m2Fmt(info.windKmh, 0)} km/h · ${m2Fmt(info.temp, 0)} °C${info.tempIsMax ? ' (máx. de las próximas 24 h)' : ' (actual)'} · humedad ${m2Fmt(info.rh, 0)} %</div>
    <div class="fw-dim">Vegetación conocida en el ${(info.coverage * 100).toFixed(0)} % del parche. ${info.hasElevation ? 'Con altura del terreno.' : 'Sin altura del terreno.'}</div>
    <div class="fw-dim">No predice incendios nuevos ni la dirección fina del avance (para eso, «Simular propagación»). Los niveles ordenan celdas: <b>no son porcentajes</b>. Validación: una sola partición, sin validación por fechas; con focos de MODIS y VIIRS mezclados.</div>`;
}

/** Temperatura máxima de las próximas 24 h en el punto (solo con clima actual y proxy local); null si no se puede. */
async function m2FetchTempMax(lat, lon) {
  if (typeof FW === 'undefined' || !FW.proxy) return null;
  try {
    const qs = `latitude=${lat.toFixed(4)}&longitude=${lon.toFixed(4)}&hourly=temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m` +
      '&forecast_days=2&wind_speed_unit=kmh&timezone=UTC';
    const res = await fetch(`/api/openmeteo?${qs}`);
    if (!res.ok) return null;
    const d = await res.json();
    return fwM2TempMax((Array.isArray(d) ? d[0] : d).hourly, Date.now());
  } catch (_) { return null; }
}

async function runM2(lat, lon) {
  const map = m2State.map;
  if (!map) return;
  clearM2();
  const seq = m2State.seq;
  const head = '<h3>🛰️ Continuación probable (M2)</h3>';
  const step = (t) => { if (seq === m2State.seq) m2Note(head + `<div class="fw-dim">${t}</div>`, true); };
  if (typeof alertsState === 'undefined' || !alertsState.token) {
    m2Note(head + '<div>Inicia sesión en la central (panel «Alertas») para usar M2: el modelo corre en el backend.</div>', true); return;
  }
  const { grid, label } = m2State.ctx.weather();
  if (!grid || !grid.loaded) { m2Note(head + '<div>Aún no hay datos de clima cargados.</div>', true); return; }
  const w = grid.sample(lat, lon);
  if (!w) { m2Note(head + '<div>No hay clima para este punto (fuera de la grilla de datos).</div>', true); return; }

  const avisos = [];
  step('Calculando… (vegetación, altura y clima de la zona, en paralelo)');
  try {
    const fuelTask = typeof zonesEnsureFuel === 'function' ? zonesEnsureFuel(lat, lon, M2_RADIUS_KM) : Promise.resolve();
    let elevacion = null;
    const elevTask = (async () => {
      try {
        const lattice = fwM2BlockCenters(lat, lon, FW_M2_ELEV_N);
        const z = await fetchElevationLattice(lattice, (i, n) => step(`Calculando… (altura del terreno ${i + 1}/${n})`));
        elevacion = { n: FW_M2_ELEV_N, valores: Array.from(z) };
      } catch (err) { avisos.push(`No se pudo traer la altura (${err.message}); el backend supone terreno plano.`); }
    })();
    const liveTask = m2IsLive() ? m2FetchTempMax(lat, lon) : Promise.resolve(null);
    const [, , tmax] = await Promise.all([fuelTask, elevTask, liveTask]);
    if (seq !== m2State.seq) return;

    const { grid: cobertura, coverage } = fwM2ClassGrid((la, lo) => (typeof fuelMosaic !== 'undefined' ? fuelMosaic.cell(la, lo) : null), lat, lon);
    if (coverage < 0.2) { m2Note(head + '<div>No hay datos de vegetación en esta zona (no se pudo cargar la grilla de combustible).</div>', true); return; }
    if (tmax === null) avisos.push('Se usó la temperatura actual: el modelo espera la máxima del día, así que puede subestimar.');

    const tRef = typeof FIRE_STATE !== 'undefined' && FIRE_STATE.replay
      ? Math.max(0, ...firePixels.filter((p) => Number.isFinite(p.ts)).map((p) => p.ts)) || Date.now() : Date.now();
    const focos = fwM2Focos(typeof firePixels !== 'undefined' ? firePixels : [], lat, lon, tRef);
    const body = fwM2Payload({ lat, lon, focos, w, tempMax: tmax, cobertura, elevacion });

    step('Consultando el modelo M2 en el backend…');
    const res = await alertsFetch('/propagacion/m2', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await res.json().catch(() => ({}));
    if (seq !== m2State.seq) return;
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    m2Draw(map, data);
    m2Note(m2CardHTML(data, {
      avisos, coverage, windKmh: body.viento_kmh, temp: body.temp_max, tempIsMax: tmax !== null, rh: body.humedad,
      hasElevation: !!elevacion,
    }) + (label ? `<div class="fw-dim">Clima: ${fwEscape(label)}</div>` : ''), true);
    if (typeof reportSource === 'function') reportSource('m2', 'ok', `${data.niveles.reduce((s, n) => s + n.celdas, 0)} celdas · ${data.focos_usados} foco(s)`);
  } catch (err) {
    if (err.message === 'sesión vencida') { m2Note(''); return; }
    const net = err instanceof TypeError;
    m2Note(head + `<div style="color:#e5484d">${net ? 'No se pudo conectar con el backend (¿está corriendo y permite este origen?).' : 'No se pudo calcular: ' + fwEscape(err.message)}</div>`, true);
    if (typeof reportSource === 'function') reportSource('m2', 'error', err.message);
  }
}

/** Con clima de escenario (histórico) no hay «próximas 24 h» reales: se usa el valor de la grilla. */
function m2IsLive() {
  const { label } = m2State.ctx.weather();
  return !label;
}

function initM2(map, ctx) {
  m2State.map = map; m2State.ctx = ctx;
  m2Style();
  document.addEventListener('click', (e) => {
    const btn = e.target.closest && e.target.closest('.fw-m2-btn');
    if (!btn) return;
    runM2(Number(btn.dataset.lat), Number(btn.dataset.lon));
  });
}
