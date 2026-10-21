// ============================================================
// FireWatch · Mapa 3D — capa de focos de calor satelitales (NASA FIRMS)
//
// Fire Information for Resource Management System (FIRMS) de la NASA:
// detecciones reales de incendios activos vía satélite (sensor VIIRS a
// bordo de NOAA-20, NOAA-21 y Suomi-NPP), casi en tiempo real (NRT).
//
// v2:
//  - Consulta VARIOS satélites (cada uno pasa a distinta hora → más
//    pasadas por día) y elimina duplicados.
//  - Pasa por el proxy local: la MAP_KEY ya no está en el navegador.
//  - MODO REPLAY: muestra incendios reales históricos (fuentes "SP") para
//    poder demostrar el mapa cuando en vivo no hay focos.
//  - Actualiza la capa en vez de ignorar las recargas (antes, si la capa
//    ya existía, nunca se refrescaba).
//
// Verificado en vivo (28-09-2026): la API responde con CORS abierto y la
// misma URL con fecha devuelve datos históricos (Valparaíso, 3-feb-2024).
// Un foco FIRMS NO es necesariamente un incendio: puede ser quema
// agrícola, industria u otra fuente de calor intensa.
// ============================================================

const FIRE_STATE = { replay: null, loading: false };
let firePixels = []; // { lat, lon, brightness, confidence, acqDate, acqTime, frp, satellite, ts }

function parseCsv(text) {
  const lines = text.trim().split(/\r?\n/);
  if (lines.length < 2) return [];
  const headers = lines[0].split(',').map((h) => h.trim());
  return lines.slice(1).filter(Boolean).map((line) => {
    const cols = line.split(',');
    const row = {};
    headers.forEach((h, i) => { row[h] = cols[i] !== undefined ? cols[i].trim() : ''; });
    return row;
  });
}

/** VIIRS entrega l/n/h; MODIS entrega un número 0-100. Se normaliza a l/n/h. */
function normalizeConfidence(c) {
  if (c === 'l' || c === 'n' || c === 'h') return c;
  const n = parseFloat(c);
  if (Number.isNaN(n)) return 'n';
  return n >= 80 ? 'h' : n >= 30 ? 'n' : 'l';
}

function firePixelTimestamp(acqDate, acqTime) {
  const t = String(acqTime || '0').padStart(4, '0');
  const ms = Date.parse(`${acqDate}T${t.slice(0, 2)}:${t.slice(2)}:00Z`);
  return Number.isNaN(ms) ? null : ms;
}

function formatFireTimeLocal(ts) {
  if (!ts) return 'hora desconocida';
  return new Date(ts).toLocaleString('es-CL', {
    timeZone: 'America/Santiago', day: '2-digit', month: '2-digit', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  }) + ' (hora de Chile)';
}

async function fetchFirmsSource(source, bbox, days, date) {
  if (!FW.proxy) throw new Error('requiere el proxy local (python server.py)');
  if (!FW.firms) throw new Error('falta FIRMS_MAP_KEY en .env');
  const qs = new URLSearchParams({ source, bbox: bbox.join(','), days: String(days) });
  if (date) qs.set('date', date);
  const res = await fetch(`/api/firms?${qs}`);
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try { msg = (await res.json()).error || msg; } catch (_) { /* no JSON */ }
    throw new Error(msg);
  }
  return parseCsv(await res.text());
}

function ensureFireLayers(map) {
  if (map.getSource('fire-pixels')) return;
  map.addSource('fire-pixels', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({
    id: 'fire-pixels-glow', type: 'circle', source: 'fire-pixels',
    paint: { 'circle-radius': ['interpolate', ['linear'], ['zoom'], 8, 16, 13, 30], 'circle-color': '#e5484d', 'circle-opacity': 0.3, 'circle-blur': 0.6 },
  });
  map.addLayer({
    id: 'fire-pixels-point', type: 'circle', source: 'fire-pixels',
    paint: {
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 8, 5, 13, 9],
      'circle-color': ['match', ['get', 'confidence'], 'h', '#e5484d', 'n', '#ff6a3d', 'l', '#e8b93d', '#e5484d'],
      'circle-stroke-color': '#ffffff', 'circle-stroke-width': 2,
    },
  });

  // Las capas de fondo (calor, vegetación, zonas) quedan siempre debajo de los focos.
  ['worldcover-layer', 'temp-heat-layer'].forEach((id) => {
    if (map.getLayer(id)) map.moveLayer(id, 'fire-pixels-glow');
  });

  map.on('click', 'fire-pixels-point', (e) => {
    const p = e.features[0].properties;
    const conf = { h: 'alta', n: 'nominal', l: 'baja' }[p.confidence] || p.confidence;
    new maplibregl.Popup({ offset: 8 })
      .setLngLat(e.lngLat)
      .setHTML(
        `<b>🛰️ Foco de calor (NASA FIRMS)</b>` +
        `<br>Detectado: ${formatFireTimeLocal(Number(p.ts))}` +
        `<br>Satélite: ${p.satellite}` +
        `<br>Confianza: ${conf}` +
        (p.frp ? `<br>Potencia radiativa: ${p.frp} MW` : '') +
        `<br><span style="opacity:.7">Puede ser incendio, quema agrícola u otra fuente de calor intensa.</span>` +
        `<div class="fw-zone">${routeButtonHTML(e.lngLat.lat, e.lngLat.lng, '🚒 Ruta de bomberos a este foco')}${spreadButtonHTML(e.lngLat.lat, e.lngLat.lng)}${typeof m2ButtonHTML === 'function' ? m2ButtonHTML(e.lngLat.lat, e.lngLat.lng) : ''}</div>`
      )
      .addTo(map);
  });
  map.on('mouseenter', 'fire-pixels-point', () => map.getCanvas().style.cursor = 'pointer');
  map.on('mouseleave', 'fire-pixels-point', () => map.getCanvas().style.cursor = '');
}

function firePixelsToGeoJSON() {
  return {
    type: 'FeatureCollection',
    features: firePixels.map((p) => ({
      type: 'Feature',
      properties: {
        confidence: p.confidence, ts: p.ts, satellite: p.satellite,
        frp: p.frp, brightness: p.brightness,
      },
      geometry: { type: 'Point', coordinates: [p.lon, p.lat] },
    })),
  };
}

/**
 * Carga (o recarga) los focos. Sin `replay`: en vivo sobre el área de cobertura.
 * Con `replay` (un preset de CONFIG.replayPresets): incendio histórico en su bbox.
 */
async function loadFirePixels(map, area, replay) {
  ensureFireLayers(map);
  FIRE_STATE.replay = replay || null;
  FIRE_STATE.loading = true;

  const toggle = document.getElementById('toggle-firms');
  let bbox, days, date, sourceSets;
  if (replay) {
    bbox = replay.bbox; days = replay.days || 1; date = replay.date;
    // Fechas recientes están en las fuentes NRT; las antiguas en las "SP" (procesamiento
    // estándar, con ~3 meses de retraso). Si la primera familia no trae nada, se prueba la otra.
    const ageDays = (Date.now() - Date.parse(replay.date + 'T00:00:00Z')) / 86400000;
    sourceSets = ageDays <= 90
      ? [CONFIG.firmsLiveSources, CONFIG.firmsReplaySources]
      : [CONFIG.firmsReplaySources, CONFIG.firmsLiveSources];
  } else {
    const b = area.bbox ? [[area.bbox[0], area.bbox[1]], [area.bbox[2], area.bbox[3]]] : boundsFromCenterKm(area.center, area.radiusKm);
    bbox = [b[0][0], b[0][1], b[1][0], b[1][1]];
    days = CONFIG.firmsLiveDays; date = ''; sourceSets = [CONFIG.firmsLiveSources];
  }

  // Un satélite por vez: pocas peticiones y errores individuales identificables.
  const rows = [];
  const failures = [];
  let attempted = 0;
  for (const set of sourceSets) {
    for (const source of set) {
      attempted++;
      try {
        const r = await fetchFirmsSource(source, bbox, days, date);
        r.forEach((row) => rows.push({ row, source }));
      } catch (err) {
        failures.push(`${source}: ${err.message}`);
      }
    }
    if (rows.length) break;
  }
  FIRE_STATE.loading = false;

  if (failures.length === attempted) {
    console.warn('[FirmsLayer] No se pudo cargar NASA FIRMS:', failures);
    reportSource('firms', 'error', failures[0]);
    if (toggle) toggle.disabled = !FW.proxy || !FW.firms;
    firePixels = [];
    map.getSource('fire-pixels').setData(firePixelsToGeoJSON());
    return;
  }
  if (failures.length) console.warn('[FirmsLayer] Fuentes con error:', failures);

  const seen = new Set();
  firePixels = [];
  rows.forEach(({ row, source }) => {
    const lat = parseFloat(row.latitude), lon = parseFloat(row.longitude);
    if (Number.isNaN(lat) || Number.isNaN(lon)) return;
    const key = `${row.latitude},${row.longitude},${row.acq_date},${row.acq_time},${row.satellite || source}`;
    if (seen.has(key)) return;
    seen.add(key);
    firePixels.push({
      lat, lon,
      brightness: parseFloat(row.bright_ti4 || row.brightness),
      confidence: normalizeConfidence(row.confidence),
      acqDate: row.acq_date, acqTime: row.acq_time,
      frp: parseFloat(row.frp),
      satellite: row.satellite || source,
      ts: firePixelTimestamp(row.acq_date, row.acq_time),
    });
  });

  map.getSource('fire-pixels').setData(firePixelsToGeoJSON());
  if (toggle) toggle.disabled = false;

  const what = replay ? `replay ${replay.date}` : `últimas ${days * 24} h`;
  const partial = failures.length ? ` · ${failures.length} satélite(s) sin respuesta` : '';
  if (!firePixels.length) {
    console.info('[FirmsLayer] Sin focos activos en esta zona (buena noticia, no es un error).');
    reportSource('firms', failures.length ? 'warn' : 'ok', `sin focos (${what})${partial}`);
  } else {
    reportSource('firms', failures.length ? 'warn' : 'ok', `${firePixels.length} foco(s) · ${what}${partial}`);
  }
}

function setFirePixelsVisible(map, visible) {
  ['fire-pixels-point', 'fire-pixels-glow'].forEach((id) => {
    if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', visible ? 'visible' : 'none');
  });
  // Las zonas de calor local (círculo, contorno y rótulo) tienen su propio interruptor: toggle-localzones.
}

/** Instante del foco más intenso (mayor potencia radiativa). Sin FRP: la mediana de las horas. */
function strongestFireTime(pixels) {
  const withTs = pixels.filter((p) => p.ts);
  if (!withTs.length) return null;
  const withFrp = withTs.filter((p) => Number.isFinite(p.frp));
  if (withFrp.length) return withFrp.reduce((a, p) => (p.frp > a.frp ? p : a)).ts;
  const sorted = withTs.map((p) => p.ts).sort((a, b) => a - b);
  return sorted[Math.floor(sorted.length / 2)];
}
