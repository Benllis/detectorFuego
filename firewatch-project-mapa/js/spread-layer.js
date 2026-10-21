// ============================================================
// FireWatch · Mapa 3D — simulación de propagación en el mapa
//
// El botón «🔥 Simular propagación» (en el popup de un foco, de una alerta de
// la central o de un punto consultado) corre spread-model.js alrededor de ese
// punto y dibuja cuándo llegaría el fuego: rojo = primera hora, naranja =
// hasta 3 h, amarillo = hasta 6 h. Una tarjeta resume área y alcance.
//
// Datos: grilla de combustible (fuel-grid.js), clima activo (actual o del
// escenario) y altura (retícula de 1,5 km del DEM de Open-Meteo, ~4 peticiones en fila).
// Si la altura falla, se simula sobre terreno plano y se avisa en la tarjeta.
//
// ES UNA ESTIMACIÓN POR REGLAS con el viento actual supuesto constante, no un
// pronóstico. Ver spread-model.js y METODOLOGIA.md.
// ============================================================

const SPREAD_RADIUS_KM = 12;     // medio lado de la ventana simulada
const SPREAD_CELL_M = 250;
const SPREAD_ELEV_SPACING_KM = 1.5; // 1 km (625 puntos) pasaba el límite por minuto de Open-Meteo
const SPREAD_BANDS = [           // [hasta (min), color, etiqueta]
  [60, [229, 72, 77, 170], 'primera hora'],
  [180, [255, 138, 0, 140], 'hasta 3 h'],
  [360, [255, 214, 10, 110], 'hasta 6 h'],
];
const spreadState = { map: null, ctx: null, seq: 0, marker: null, elevCache: new Map() };

function spreadButtonHTML(lat, lon, label = '🔥 Simular propagación') {
  return `<button class="fw-zoom fw-spread-btn" type="button" data-lat="${lat}" data-lon="${lon}">${label}</button>`;
}

function spreadStyle() {
  if (document.getElementById('fw-spread-style')) return;
  const st = document.createElement('style');
  st.id = 'fw-spread-style';
  st.textContent = `
    .fw-spread-card{position:fixed;top:76px;left:304px;z-index:6;width:min(410px,calc(100vw - 620px));
      background:rgba(20,23,28,.92);backdrop-filter:blur(10px);border:1px solid var(--line);border-radius:12px;color:var(--paper);
      padding:.8rem .95rem;font-size:.76rem;line-height:1.45;display:none}
    .fw-spread-card h3{font-family:'Space Grotesk',sans-serif;font-size:.86rem;margin:0 0 .4rem}
    .fw-spread-card table{width:100%;border-collapse:collapse;margin:.35rem 0}
    .fw-spread-card td,.fw-spread-card th{padding:.18rem .3rem;text-align:left;font-weight:400}
    .fw-spread-card th{color:var(--paper-dim);font-size:.68rem}
    .fw-spread-card td:nth-child(n+2),.fw-spread-card th:nth-child(n+2){text-align:right;font-variant-numeric:tabular-nums}
    .fw-spread-sw{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;vertical-align:-1px}
    .fw-spread-card .fw-dim{color:var(--paper-dim)}
    @media (max-width:1100px){.fw-spread-card{width:calc(100vw - 32px);top:auto;bottom:112px}}`;
  document.head.appendChild(st);
}

function spreadCard() {
  let el = document.getElementById('spread-card');
  if (!el) {
    el = document.createElement('div');
    el.id = 'spread-card'; el.className = 'fw-spread-card';
    document.body.appendChild(el);
  }
  return el;
}

function spreadNote(html, withClear) {
  const el = spreadCard();
  el.innerHTML = html + (withClear ? '<button class="btn" id="btn-spread-clear" type="button">Quitar simulación</button>' : '');
  el.style.display = html ? 'block' : 'none';
  const b = document.getElementById('btn-spread-clear');
  if (b) b.addEventListener('click', clearSpread);
}

function clearSpread() {
  spreadState.seq++;
  const map = spreadState.map;
  if (map) {
    ['spread-layer'].forEach((id) => { if (map.getLayer(id)) map.removeLayer(id); });
    if (map.getSource('spread-image')) map.removeSource('spread-image');
  }
  if (spreadState.marker) { spreadState.marker.remove(); spreadState.marker = null; }
  spreadNote('');
}

/**
 * Pide la retícula de alturas en tandas de 100 puntos (límite del servicio), UNA TRAS OTRA
 * y con reintentos. Antes iban todas en paralelo y ~625 puntos de golpe pasaban el límite
 * de Open-Meteo (≈600 «llamadas» por minuto → HTTP 429). Ahora son ~360 puntos con pausa
 * entre tandas, y el resultado se guarda en el navegador (24 h) por si se repite el punto.
 * `onProgress(i, n)` avisa el avance para mostrarlo en la tarjeta.
 */
async function fetchElevationLattice(lat, onProgress) {
  const key = `${lat.lat0.toFixed(4)},${lat.lon0.toFixed(4)},${lat.nLat}`;
  if (spreadState.elevCache.has(key)) return spreadState.elevCache.get(key);
  const lsKey = `fw:elev:${key}`;
  const hit = typeof lsGet === 'function' ? lsGet(lsKey, 24 * 3600 * 1000) : null;
  if (hit && Array.isArray(hit.value) && hit.value.length === lat.coords.length) {
    const z = Float32Array.from(hit.value);
    spreadState.elevCache.set(key, z);
    return z;
  }
  const chunks = [];
  for (let i = 0; i < lat.coords.length; i += 100) chunks.push(lat.coords.slice(i, i + 100));
  const parts = [];
  for (let n = 0; n < chunks.length; n++) {
    const ch = chunks[n];
    if (onProgress) onProgress(n, chunks.length);
    const url = 'https://api.open-meteo.com/v1/elevation' +
      `?latitude=${ch.map((p) => p[0].toFixed(5)).join(',')}&longitude=${ch.map((p) => p[1].toFixed(5)).join(',')}`;
    const fetchUrl = (typeof FW !== 'undefined' && FW.proxy) ? url.replace('https://api.open-meteo.com/v1/elevation', '/api/elevation') : url; // por el servidor local: caché de 1 año
    let z = null, lastErr = null, fromCache = false;
    for (let attempt = 0; attempt < 4 && !z; attempt++) {
      if (attempt) await new Promise((r) => setTimeout(r, [0, 3000, 10000, 25000][attempt]));
      try {
        const res = await fetch(fetchUrl);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const arr = (await res.json()).elevation;
        if (!Array.isArray(arr) || arr.length !== ch.length || arr.some((v) => typeof v !== 'number')) throw new Error('respuesta inesperada');
        z = arr;
        fromCache = res.headers.get('X-Cache-Age') !== null && Number(res.headers.get('X-Cache-Age')) > 0;
      } catch (err) {
        lastErr = err;
        if (typeof fwLog === 'function') fwLog('elevacion', 'warn', err.message, { phase: `tanda ${n + 1}/${chunks.length}, intento ${attempt + 1}`, variant: 'open-meteo' });
      }
    }
    if (!z) throw lastErr || new Error('sin respuesta');
    parts.push(z);
    // Pausa solo si la tanda vino de Open-Meteo (con caché del servidor no hay nada que esperar).
    if (n < chunks.length - 1 && !fromCache) await new Promise((r) => setTimeout(r, 400));
  }
  const z = Float32Array.from(parts.flat());
  spreadState.elevCache.set(key, z);
  if (typeof lsSet === 'function') lsSet(lsKey, Array.from(z));
  return z;
}

function drawSpread(map, res, geo) {
  const { rows, cols, arrival } = res;
  const cv = document.createElement('canvas');
  cv.width = cols; cv.height = rows;
  const g = cv.getContext('2d');
  const img = g.createImageData(cols, rows);
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const t = arrival[r * cols + c];
      const band = SPREAD_BANDS.find((b) => t <= b[0]);
      if (!band) continue;
      const o = ((rows - 1 - r) * cols + c) * 4; // fila 0 = sur: en la imagen va abajo
      img.data[o] = band[1][0]; img.data[o + 1] = band[1][1]; img.data[o + 2] = band[1][2]; img.data[o + 3] = band[1][3];
    }
  }
  g.putImageData(img, 0, 0);
  map.addSource('spread-image', {
    type: 'image', url: cv.toDataURL('image/png'),
    coordinates: [[geo.west, geo.north], [geo.east, geo.north], [geo.east, geo.south], [geo.west, geo.south]],
  });
  const before = map.getLayer('fire-pixels-glow') ? 'fire-pixels-glow' : undefined;
  map.addLayer({ id: 'spread-layer', type: 'raster', source: 'spread-image', paint: { 'raster-opacity': 1, 'raster-fade-duration': 0 } }, before);
}

const spreadFmtHa = (ha) => (ha >= 100 ? `${Math.round(ha).toLocaleString('es-CL')} ha` : `${ha.toLocaleString('es-CL', { minimumFractionDigits: 1, maximumFractionDigits: 1 })} ha`);
const spreadFmtKm = (km) => `${km.toLocaleString('es-CL', { minimumFractionDigits: 1, maximumFractionDigits: 1 })} km`;

function spreadCardHTML(lat, lon, stats, sum, elevError, windLabel) {
  const rows = stats.map((s, i) => {
    const b = SPREAD_BANDS[i];
    return `<tr><td><span class="fw-spread-sw" style="background:rgba(${b[1][0]},${b[1][1]},${b[1][2]},1)"></span>${s.minutes / 60} h</td>` +
      `<td>${spreadFmtHa(s.areaHa)}</td><td>${spreadFmtKm(s.maxKm)}</td><td>${s.compass ? 'hacia el ' + s.compass : '—'}</td></tr>`;
  }).join('');
  const dir = typeof degToCompass === 'function' ? degToCompass(sum.windFromDeg) : Math.round(sum.windFromDeg) + '°';
  return `<h3>🔥 Propagación estimada</h3>
    <div class="fw-dim">Ignición en ${Math.abs(lat).toFixed(4)}° ${lat < 0 ? 'S' : 'N'}, ${Math.abs(lon).toFixed(4)}° ${lon < 0 ? 'O' : 'E'}</div>
    <table><tr><th>Tiempo</th><th>Área</th><th>Alcance</th><th>Dirección</th></tr>${rows}</table>
    <div>Viento supuesto: ${sum.windKmh.toFixed(0)} km/h desde el ${dir}${sum.rh !== null ? ` · humedad ${sum.rh.toFixed(0)} %` : ''} · ${sum.tempC.toFixed(0)} °C${windLabel ? ` (${windLabel})` : ''}</div>
    <div class="fw-dim">Vegetación en ${(sum.fuelCoverage * 100).toFixed(0)} % de la zona. ${sum.hasElevation ? 'Con pendiente del terreno.' : `Sin pendiente: ${elevError || 'altura no disponible'}; se simula terreno plano.`}</div>
    <div class="fw-dim">Estimación por reglas con viento constante; no es un pronóstico ni considera humo, cortafuegos ni la acción de bomberos. Pasadas las primeras horas tiende a SOBREESTIMAR (en Valparaíso 2024 simuló unas 5 veces el área observada a las 35 h): léela como un máximo.</div>`;
}

async function runSpread(lat, lon) {
  const map = spreadState.map;
  if (!map || typeof fwSimulate !== 'function') return;
  clearSpread();
  const seq = spreadState.seq;
  const { grid, label } = spreadState.ctx.weather();
  const T0 = performance.now(), timing = {};
  const head = '<h3>🔥 Propagación estimada</h3>';
  if (!grid || !grid.loaded) { spreadNote(head + '<div>Aún no hay datos de clima cargados.</div>', true); return; }
  spreadNote(head + '<div class="fw-dim">Calculando… (vegetación y altura del terreno, en paralelo)</div>', true);

  // Vegetación (fuera de Santiago se carga bajo demanda, zones.js) y altura del terreno son independientes:
  // se piden a la vez; antes iban una tras otra y los tiempos se sumaban.
  const center = { lat, lon };
  const fuelTask = (async () => {
    const t = performance.now();
    if (typeof zonesEnsureFuel === 'function') await zonesEnsureFuel(lat, lon, SPREAD_RADIUS_KM);
    timing.vegetacion = performance.now() - t;
  })();
  let elevAt = null, elevError = null;
  const elevTask = (async () => {
    const t = performance.now();
    try {
      const lattice = fwElevationLattice(center, SPREAD_RADIUS_KM, SPREAD_ELEV_SPACING_KM);
      const z = await fetchElevationLattice(lattice, (i, n) => {
        if (seq === spreadState.seq) spreadNote(head + `<div class="fw-dim">Calculando… (altura del terreno ${i + 1}/${n})</div>`, true);
      });
      elevAt = fwElevationFn(lattice, z);
    } catch (err) { elevError = err.message; }
    timing.altura = performance.now() - t;
  })();
  await Promise.all([fuelTask, elevTask]);
  if (seq !== spreadState.seq) return; // el usuario pidió otra simulación o la quitó

  const hasFuel = typeof fuelGridFor === 'function' && fuelGridFor(lat, lon);
  const fuelGrid = hasFuel ? (typeof fuelMosaic !== 'undefined' ? fuelMosaic : hasFuel) : null;
  if (!fuelGrid) { spreadNote(head + '<div>No hay datos de vegetación en este punto (no se pudo cargar la grilla de combustible de la zona).</div>', true); return; }

  try {
    const built = fwBuildInput({ center, radiusKm: SPREAD_RADIUS_KM, cellM: SPREAD_CELL_M, fuelGrid, weatherGrid: grid, elevAt });
    if (built.summary.fuelCoverage < 0.2) throw new Error('casi no hay vegetación mapeada alrededor de este punto');
    const tSim = performance.now();
    const res = fwSimulate(built.input);
    timing.calculo = performance.now() - tSim;
    const stats = fwSpreadStats(res, SPREAD_BANDS.map((b) => b[0]));
    const noSpread = stats[stats.length - 1].cells <= 1; // solo arde la celda de ignición
    if (noSpread) {
      spreadNote(head + `<div style="color:#ff8a00"><b>El fuego no se propaga desde este punto.</b></div>
        <div>No hay combustible alrededor: las celdas vecinas son roca, nieve, suelo desnudo, agua o zona urbana, que no arden en el modelo.</div>
        <div class="fw-dim">Vegetación en ${(built.summary.fuelCoverage * 100).toFixed(0)} % de la zona (ventana de ${SPREAD_RADIUS_KM * 2} km). Prueba en un punto con matorral, pastizal o bosque.</div>
        <div class="fw-dim">Tiempos: vegetación ${(timing.vegetacion / 1000).toFixed(1)} s · altura ${(timing.altura / 1000).toFixed(1)} s · cálculo ${timing.calculo.toFixed(0)} ms</div>`, true);
      if (typeof reportSource === 'function') reportSource('spread', 'ok', 'sin propagación: sin combustible alrededor del punto');
      return;
    }
    drawSpread(map, res, built.geo);
    const el = document.createElement('div');
    el.textContent = '🔥'; el.title = 'Punto de ignición';
    el.style.cssText = 'font-size:22px;line-height:1;filter:drop-shadow(0 1px 2px #000);cursor:default;';
    spreadState.marker = new maplibregl.Marker({ element: el }).setLngLat([lon, lat]).addTo(map);
    timing.total = performance.now() - T0;
    const wAtPoint = grid.sample(lat, lon);
    const coarse = wAtPoint && wAtPoint.scale === 'national';
    spreadNote(spreadCardHTML(lat, lon, stats, built.summary, elevError, label ? 'clima: ' + label : '') +
      `<div class="fw-dim">Tiempos: vegetación ${(timing.vegetacion / 1000).toFixed(1)} s · altura ${(timing.altura / 1000).toFixed(1)} s (en paralelo) · cálculo ${timing.calculo.toFixed(0)} ms · total ${(timing.total / 1000).toFixed(1)} s</div>` +
      (coarse ? '<div class="fw-dim">Clima de la grilla nacional (1,25°): no ve brisas locales ni efectos de la cordillera; el viento real puede diferir.</div>' : ''), true);
    if (typeof reportSource === 'function') reportSource('spread', 'ok', `${stats[2].areaHa.toFixed(0)} ha a 6 h · ${built.summary.hasElevation ? 'con pendiente' : 'sin pendiente'}`);
  } catch (err) {
    console.warn('[Spread]', err);
    spreadNote(`<h3>🔥 Propagación estimada</h3><div style="color:#e5484d">No se pudo simular: ${err.message}</div>`, true);
    if (typeof reportSource === 'function') reportSource('spread', 'error', err.message);
  }
}

// ---------- precarga: dejar lista la vegetación y la altura de las alertas de la central ----------
// Cuando llega (o ya hay) una alerta pendiente se descargan en segundo plano los datos de su zona; así, al pulsar
// «Simular propagación», la primera vez ya no espera la red. Una a la vez y sin pasar el límite de Open-Meteo.
const spreadPrefetch = { queue: [], running: false, done: new Set() };

function spreadPrefetchPoint(lat, lon) {
  const key = `${lat.toFixed(2)},${lon.toFixed(2)}`;
  if (!Number.isFinite(lat) || !Number.isFinite(lon) || spreadPrefetch.done.has(key)) return;
  spreadPrefetch.done.add(key);
  spreadPrefetch.queue.push({ lat, lon, key });
  spreadPrefetchRun();
}

async function spreadPrefetchRun() {
  if (spreadPrefetch.running) return;
  spreadPrefetch.running = true;
  try {
    while (spreadPrefetch.queue.length) {
      const { lat, lon, key } = spreadPrefetch.queue.shift();
      try {
        await Promise.all([
          typeof zonesEnsureFuel === 'function' ? zonesEnsureFuel(lat, lon, SPREAD_RADIUS_KM) : null,
          fetchElevationLattice(fwElevationLattice({ lat, lon }, SPREAD_RADIUS_KM, SPREAD_ELEV_SPACING_KM)),
        ]);
      } catch (err) {
        spreadPrefetch.done.delete(key); // se reintentará con la próxima alerta; no es grave
        console.warn('[Spread] precarga', key, err.message);
      }
    }
  } finally { spreadPrefetch.running = false; }
}

/** ctx.weather() devuelve { grid, label } del clima activo (actual o escenario), igual que el inspector. */
function initSpread(map, ctx) {
  spreadState.map = map; spreadState.ctx = ctx;
  spreadStyle();
  // Alertas pendientes de la central: precarga de las 3 más recientes (con ubicación).
  document.addEventListener('fw-alerts-updated', (e) => {
    (e.detail || []).filter((a) => a.estado === 'PENDIENTE' && Array.isArray(a.gps) && a.gps.length >= 2)
      .slice(0, 3).forEach((a) => spreadPrefetchPoint(Number(a.gps[0]), Number(a.gps[1])));
  });
  document.addEventListener('click', (e) => {
    const btn = e.target.closest && e.target.closest('.fw-spread-btn');
    if (!btn) return;
    runSpread(Number(btn.dataset.lat), Number(btn.dataset.lon));
  });
}
