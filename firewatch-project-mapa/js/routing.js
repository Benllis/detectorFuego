// ============================================================
// FireWatch · Mapa 3D — rutas de emergencia y tráfico en vivo
//
// Al pulsar "Ruta de bomberos" en un foco (o en un punto consultado):
//   1. Toma las 3 estaciones de bomberos más cercanas en línea recta y calcula
//      el camino real desde cada una: gana la que llega antes (no la más cercana
//      "a vuelo de pájaro", que puede estar al otro lado de un río o autopista).
//   2. Dibuja esa ruta (roja) y hasta 2 alternativas (grises).
//   3. Busca el grifo (o, si no hay cerca, la fuente de agua) más cercano al
//      foco y dibuja la ruta estación → agua → foco (azul punteada), con el
//      tiempo extra que cuesta pasar por ahí.
//
// Motor de rutas (server.py, /api/route): OSRM público (datos OSM, SIN tráfico)
// por defecto; con TOMTOM_KEY en .env usa TomTom, que sí incluye el tráfico en
// vivo y entrega el retraso estimado. La capa "Tráfico en vivo" son los tiles
// de flujo de TomTom, servidos por el proxy (la key no llega al navegador).
//
// Las funciones "fw*" de arriba no tocan el DOM: se prueban en Node (tests/).
// ============================================================

// ---------------------------------------------------------------- lógica pura
function fwDistKm(aLat, aLon, bLat, bLon) {
  const kx = 111.32 * Math.cos(((aLat + bLat) / 2) * Math.PI / 180);
  return Math.hypot((bLat - aLat) * 111.32, (bLon - aLon) * kx);
}

/** Los k elementos más cercanos (línea recta) a un punto, con `distKm`. */
function fwNearestK(list, lat, lon, k) {
  return list
    .map((it) => ({ ...it, distKm: fwDistKm(lat, lon, it.lat, it.lon) }))
    .sort((a, b) => a.distKm - b.distKm)
    .slice(0, k);
}

function fwFmtDuration(sec) {
  const min = Math.max(1, Math.round(sec / 60));
  return min < 60 ? `${min} min` : `${Math.floor(min / 60)} h ${String(min % 60).padStart(2, '0')} min`;
}

const fwFmtKm = (m) => (m < 1000 ? `${Math.round(m)} m` : `${(m / 1000).toFixed(1)} km`);

/** De varios resultados { station, route } elige el de menor tiempo de viaje. */
function fwPickFastest(results) {
  let best = null;
  results.forEach((r) => { if (r && r.route && (!best || r.route.duration_s < best.route.duration_s)) best = r; });
  return best;
}

/**
 * Punto de agua para reabastecer cerca del foco: el grifo más cercano si está
 * a ≤ maxHydrantKm; si no, la fuente de agua (lago/río) más cercana.
 */
function fwPickWaterPoint(hydrantsNear, waterNear, maxHydrantKm = 3) {
  const h = hydrantsNear && hydrantsNear[0];
  if (h && h.distKm <= maxHydrantKm) return { kind: 'grifo', lat: h.lat, lon: h.lon, distKm: h.distKm, name: 'Grifo' };
  if (waterNear) return { kind: waterNear.type || 'agua', lat: waterNear.lat, lon: waterNear.lon, distKm: waterNear.distKm, name: waterNear.name || 'Fuente de agua' };
  if (h) return { kind: 'grifo', lat: h.lat, lon: h.lon, distKm: h.distKm, name: 'Grifo' };
  return null;
}

/** "Tiempo · distancia" con el retraso por tráfico cuando el proveedor lo entrega. */
function fwRouteText(r) {
  const delay = r.delay_s;
  return `${fwFmtDuration(r.duration_s)} · ${fwFmtKm(r.distance_m)}` +
    (delay !== null && delay !== undefined ? (delay >= 60 ? ` (+${fwFmtDuration(delay)} por tráfico)` : ' (sin demoras por tráfico)') : '');
}

// ---- Tráfico: qué vías mostrar y con qué colores (lógica pura, probada en Node) ----
// Los tiles vectoriales de TomTom traen, por tramo, `road_type` (texto) y, opcional,
// `road_category`. Se traducen a clases simples; "principales" son las que importan para
// llegar a un incendio sin llenar el mapa de calles.
const FW_TRAFFIC_TYPE_NAMES = {   // road_type → clase
  'motorway': 'autopista', 'international road': 'troncal', 'major road': 'principal',
  'secondary road': 'secundaria', 'connecting road': 'colectora', 'major local road': 'colectora',
  'local road': 'calle', 'minor local road': 'calle',
};
const FW_TRAFFIC_CATEGORY_NAMES = {   // road_category → clase
  'motorway': 'autopista', 'trunk': 'troncal', 'primary': 'principal', 'secondary': 'secundaria',
  'tertiary': 'colectora', 'street': 'calle',
};
const FW_TRAFFIC_DETAIL = {   // nivel elegido en el panel → clases visibles (null = todas)
  principales: ['autopista', 'troncal', 'principal'],
  secundarias: ['autopista', 'troncal', 'principal', 'secundaria'],
  todas: null,
};

/** Clase de vía de un tramo ('autopista' | 'troncal' | 'principal' | 'secundaria' | 'colectora' | 'calle' | null). */
function fwTrafficClass(props) {
  const p = props || {};
  const cat = String(p.road_category == null ? '' : p.road_category).toLowerCase();
  if (FW_TRAFFIC_CATEGORY_NAMES[cat]) return FW_TRAFFIC_CATEGORY_NAMES[cat];
  const type = String(p.road_type == null ? '' : p.road_type).toLowerCase();
  return FW_TRAFFIC_TYPE_NAMES[type] || null;
}

/** ¿Se muestra este tramo con el nivel de detalle dado? */
function fwTrafficVisible(props, detail) {
  const allowed = FW_TRAFFIC_DETAIL[detail] === undefined ? FW_TRAFFIC_DETAIL.principales : FW_TRAFFIC_DETAIL[detail];
  if (allowed === null) return true;
  return allowed.includes(fwTrafficClass(props));
}

/** Filtro de MapLibre equivalente a fwTrafficVisible (se evalúa en la GPU, sin recargar tiles). */
function fwTrafficFilter(detail) {
  const allowed = FW_TRAFFIC_DETAIL[detail] === undefined ? FW_TRAFFIC_DETAIL.principales : FW_TRAFFIC_DETAIL[detail];
  if (allowed === null) return null;
  const types = Object.keys(FW_TRAFFIC_TYPE_NAMES).filter((k) => allowed.includes(FW_TRAFFIC_TYPE_NAMES[k]));
  const cats = Object.keys(FW_TRAFFIC_CATEGORY_NAMES).filter((k) => allowed.includes(FW_TRAFFIC_CATEGORY_NAMES[k]));
  const low = (key) => ['downcase', ['to-string', ['coalesce', ['get', key], '']]];
  return ['any', ['in', low('road_type'), ['literal', types]], ['in', low('road_category'), ['literal', cats]]];
}

// traffic_level (relativo): 1 = fluido, 0 = detenido. Colores algo apagados, pero el verde debe verse sobre el mapa base beige.
const FW_TRAFFIC_COLORS = [[0, '#b5403f'], [0.3, '#d27a45'], [0.6, '#d9b85a'], [0.85, '#5aa87a'], [1, '#5aa87a']];
const fwLevel = ['coalesce', ['to-number', ['get', 'traffic_level']], 1];

function fwTrafficColorExpr() {
  return ['interpolate', ['linear'], fwLevel, ...FW_TRAFFIC_COLORS.flat()];
}
/**
 * `factor` (0-1) es el control de opacidad del panel. Lo lento/congestionado (naranja, rojo)
 * casi siempre se ve opaco; lo fluido (verde) queda más tenue para no tapar el mapa.
 */
function fwTrafficAlpha(level, factor) {
  const g = Math.max(0, Math.min(1, (level - 0.6) / 0.25));   // 0 = lento, 1 = fluido
  return (1 - g) * Math.min(1, factor + 0.2) + g * factor * 0.6;
}
function fwTrafficOpacityExpr(factor) {
  const g = ['max', 0, ['min', 1, ['/', ['-', fwLevel, 0.6], 0.25]]];
  return ['let', 'g', g, ['+', ['*', ['-', 1, ['var', 'g']], Math.min(1, factor + 0.2)], ['*', ['var', 'g'], factor * 0.6]]];
}
function fwTrafficWidthExpr() {
  const big = ['in', ['downcase', ['to-string', ['coalesce', ['get', 'road_type'], '']]], ['literal', ['motorway', 'international road']]];
  const w = (px) => ['case', big, px * 1.5, px];
  return ['interpolate', ['exponential', 1.4], ['zoom'], 8, w(1), 11, w(2), 14, w(3.8), 17, w(8)];
}

if (typeof module !== 'undefined') {
  module.exports = { fwTrafficClass, fwTrafficVisible, fwTrafficFilter, fwTrafficColorExpr, fwTrafficOpacityExpr, fwTrafficAlpha, fwTrafficWidthExpr, FW_TRAFFIC_DETAIL, fwDistKm, fwNearestK, fwFmtDuration, fwFmtKm, fwPickFastest, fwPickWaterPoint, fwRouteText };
}

// ---------------------------------------------------------------- navegador
let routeMap = null;
let routeMarkers = [];
let routeSeq = 0; // descarta respuestas de una ruta anterior si el usuario pidió otra

function routeButtonHTML(lat, lon, label = '🚒 Ruta de bomberos hasta aquí') {
  return `<button class="fw-zoom fw-route-btn" type="button" data-lat="${lat}" data-lon="${lon}">${label}</button>`;
}

async function fetchRoute(pts, allowTomTom = true) {
  const coords = pts.map((p) => `${p[0].toFixed(5)},${p[1].toFixed(5)}`).join(';');
  const providers = allowTomTom && FW.tomtom ? ['tomtom', 'osrm'] : ['osrm'];
  let lastErr = null;
  for (const provider of providers) {
    try {
      const res = await fetch(`/api/route?coords=${coords}&provider=${provider}`);
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.error || `HTTP ${res.status}`);
      }
      const data = await res.json();
      if (!data.routes || !data.routes.length) throw new Error('sin rutas');
      data.fellBack = provider !== providers[0];
      return data;
    } catch (err) { lastErr = err; }
  }
  throw lastErr;
}

function routeSetNote(html, isError) {
  const el = document.getElementById('route-note');
  if (!el) return;
  el.innerHTML = html;
  el.style.color = isError ? '#e5484d' : '';
  const card = document.getElementById('route-card');
  if (card) card.style.display = html ? 'block' : 'none';
}

function ensureRouteLayers() {
  const map = routeMap;
  if (map.getSource('emergency-routes')) return;
  map.addSource('emergency-routes', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  const add = (id, kind, paint, extra = {}) => map.addLayer({
    id, type: 'line', source: 'emergency-routes', filter: ['==', ['get', 'kind'], kind],
    layout: { 'line-cap': 'round', 'line-join': 'round' }, paint, ...extra,
  });
  add('route-alt', 'alt', { 'line-color': '#9aa4b2', 'line-width': 3, 'line-opacity': 0.8 });
  add('route-water-casing', 'water', { 'line-color': '#0b1220', 'line-width': 7, 'line-opacity': 0.7 });
  add('route-water', 'water', { 'line-color': '#4aa3ff', 'line-width': 4, 'line-dasharray': [2, 1.4] });
  add('route-direct-casing', 'direct', { 'line-color': '#0b1220', 'line-width': 8, 'line-opacity': 0.75 });
  add('route-direct', 'direct', { 'line-color': '#ff4d3d', 'line-width': 5 });
}

function clearEmergencyRoute() {
  routeSeq++;
  routeMarkers.forEach((m) => m.remove());
  routeMarkers = [];
  if (routeMap && routeMap.getSource('emergency-routes')) {
    routeMap.getSource('emergency-routes').setData({ type: 'FeatureCollection', features: [] });
  }
  routeSetNote('');
}

function routeMarker(lngLat, emoji, title) {
  const el = document.createElement('div');
  el.textContent = emoji;
  el.title = title;
  el.style.cssText = 'font-size:22px;line-height:1;filter:drop-shadow(0 1px 2px #000);cursor:default;';
  const m = new maplibregl.Marker({ element: el }).setLngLat(lngLat).addTo(routeMap);
  routeMarkers.push(m);
}

async function planEmergencyRoute(lat, lon) {
  if (!routeMap) return;
  if (!FW.proxy) {
    routeSetNote('Las rutas necesitan el servidor local (<code>python server.py</code>).', true);
    reportSource('routing', 'error', 'sin proxy local');
    return;
  }
  if (!fireStations.length) {
    routeSetNote('No hay estaciones de bomberos cargadas todavía (Overpass).', true);
    return;
  }
  clearEmergencyRoute();
  const seq = routeSeq;
  routeSetNote('Calculando rutas desde las 3 estaciones más cercanas…');

  try {
    const candidates = fwNearestK(fireStations, lat, lon, 3);
    const settled = await Promise.allSettled(candidates.map((s) => fetchRoute([[s.lon, s.lat], [lon, lat]])));
    if (seq !== routeSeq) return; // el usuario pidió otra ruta o limpió
    const results = settled.map((r, i) => (r.status === 'fulfilled' ? { station: candidates[i], data: r.value, route: r.value.routes[0] } : null));
    const best = fwPickFastest(results);
    if (!best) throw settled.find((r) => r.status === 'rejected').reason;

    const fellBack = best.data.fellBack;
    const traffic = best.data.traffic;
    const features = [];
    const line = (coords, kind) => ({ type: 'Feature', properties: { kind }, geometry: { type: 'LineString', coordinates: coords } });
    best.data.routes.slice(1).forEach((r) => features.push(line(r.coords, 'alt')));

    // Ruta pasando por agua: estación → agua → foco.
    let waterInfo = '';
    const water = fwPickWaterPoint(
      typeof hydrants !== 'undefined' ? fwNearestK(hydrants, lat, lon, 1) : [],
      typeof findNearestWaterSource === 'function' ? findNearestWaterSource(lat, lon) : null);
    let waterRoute = null;
    if (water) {
      try {
        const wd = await fetchRoute([[best.station.lon, best.station.lat], [water.lon, water.lat], [lon, lat]]);
        if (seq !== routeSeq) return;
        waterRoute = wd.routes[0];
        features.push(line(waterRoute.coords, 'water'));
        const extra = waterRoute.duration_s - best.route.duration_s;
        waterInfo = `<div><b style="color:#4aa3ff">┄ Pasando por ${water.kind === 'grifo' ? 'el grifo más cercano' : 'la fuente de agua más cercana'}</b> ` +
          `(${fwFmtKm(water.distKm * 1000)} del foco${water.kind !== 'grifo' ? ` · ${water.name}` : ''}): ${fwRouteText(waterRoute)}` +
          `${extra > 30 ? ` · +${fwFmtDuration(extra)} sobre la directa` : ''}</div>`;
        routeMarker([water.lon, water.lat], '💧', `${water.name} (${water.kind})`);
      } catch (err) {
        waterInfo = '<div class="fw-dim">No se pudo calcular la ruta por el punto de agua.</div>';
      }
    } else {
      waterInfo = '<div class="fw-dim">Sin grifos ni fuentes de agua cargadas para sugerir.</div>';
    }
    features.push(line(best.route.coords, 'direct'));

    ensureRouteLayers();
    routeMap.getSource('emergency-routes').setData({ type: 'FeatureCollection', features });
    routeMarker([best.station.lon, best.station.lat], '🚒', best.station.name || 'Estación de bomberos');
    routeMarker([lon, lat], '🔥', 'Destino');

    const all = [best.route.coords, ...(waterRoute ? [waterRoute.coords] : [])].flat();
    const b = all.reduce((bb, c) => bb.extend(c), new maplibregl.LngLatBounds(all[0], all[0]));
    routeMap.fitBounds(b, { padding: { top: 90, bottom: 90, left: 340, right: 340 }, maxZoom: 15, duration: 900 });

    const alts = best.data.routes.length - 1;
    routeSetNote(
      `<div><b>🚒 ${best.station.name || 'Estación de bomberos'}</b> ` +
      `<span class="fw-dim">(${candidates.length > 1 ? 'la más rápida de las ' + candidates.length + ' más cercanas' : 'la más cercana'})</span></div>` +
      `<div><b style="color:#ff4d3d">━ Ruta directa:</b> ${fwRouteText(best.route)}${alts > 0 ? ` · ${alts} alternativa(s) en gris` : ''}</div>` +
      waterInfo +
      `<div class="fw-dim">${traffic ? 'Con tráfico en vivo (TomTom).' : 'Sin tráfico en vivo: tiempos por la red vial de OSM (OSRM), un vehículo liviano.' +
        (FW.tomtom ? '' : ' Agrega TOMTOM_KEY a .env para incluirlo.')}${fellBack ? ' TomTom falló; se usó OSRM.' : ''}</div>`);
    reportSource('routing', fellBack ? 'warn' : 'ok', (traffic ? 'TomTom (con tráfico)' : 'OSRM (sin tráfico)') + (fellBack ? ' · TomTom falló' : ''));
  } catch (err) {
    if (seq !== routeSeq) return;
    console.warn('[Routing]', err);
    routeSetNote('No se pudo calcular la ruta: ' + err.message, true);
    reportSource('routing', 'error', err.message);
  }
}

// ---------------------------------------------------------------- capa de tráfico
let trafficReported = false;
let trafficMode = null; // 'vector' | 'raster' (respaldo si los tiles vectoriales no cargan)

const trafficSettings = () => {
  const sel = document.getElementById('traffic-detail');
  const op = document.getElementById('traffic-opacity');
  return { detail: sel ? sel.value : 'principales', factor: op ? Number(op.value) / 100 : 0.6 };
};

function applyTrafficStyle(map) {
  const { detail, factor } = trafficSettings();
  if (trafficMode === 'vector' && map.getLayer('traffic-flow-layer')) {
    map.setFilter('traffic-flow-layer', fwTrafficFilter(detail));
    map.setPaintProperty('traffic-flow-layer', 'line-opacity', fwTrafficOpacityExpr(factor));
  } else if (trafficMode === 'raster' && map.getLayer('traffic-flow-layer')) {
    map.setPaintProperty('traffic-flow-layer', 'raster-opacity', factor);
  }
}

function addTrafficRaster(map, before) {
  trafficMode = 'raster';
  map.addSource('traffic-flow', {
    type: 'raster', tileSize: 256, maxzoom: 17, attribution: 'Tráfico © TomTom',
    tiles: [`${location.origin}/api/traffic/{z}/{x}/{y}.png`],
  });
  map.addLayer({
    id: 'traffic-flow-layer', type: 'raster', source: 'traffic-flow',
    paint: { 'raster-opacity': trafficSettings().factor, 'raster-saturation': -0.45 },
  }, before);
}

function setTrafficVisible(map, visible) {
  const before = map.getLayer('fire-pixels-glow') ? 'fire-pixels-glow' : undefined;
  if (visible && !map.getSource('traffic-flow')) {
    trafficMode = 'vector';
    map.addSource('traffic-flow', {
      type: 'vector', minzoom: 6, maxzoom: 14, attribution: 'Tráfico © TomTom',
      tiles: [`${location.origin}/api/traffic-v/{z}/{x}/{y}.pbf`],
    });
    map.addLayer({
      id: 'traffic-flow-layer', type: 'line', source: 'traffic-flow', 'source-layer': 'Traffic flow',
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': fwTrafficColorExpr(), 'line-width': fwTrafficWidthExpr(), 'line-opacity': fwTrafficOpacityExpr(trafficSettings().factor) },
    }, before);
    applyTrafficStyle(map);
    map.on('error', (e) => {
      if (!e || e.sourceId !== 'traffic-flow') return;
      if (trafficMode === 'vector' && !trafficReported) {
        // Los tiles vectoriales no cargaron: se cae al raster (sin filtro por tipo de vía, pero con la opacidad suave).
        map.removeLayer('traffic-flow-layer'); map.removeSource('traffic-flow');
        addTrafficRaster(map, before);
        reportSource('traffic', 'warn', 'tiles vectoriales no disponibles: se usa el raster (sin filtro de vías)');
      } else if (!trafficReported) {
        trafficReported = true;
        reportSource('traffic', 'error', (e.error && e.error.message) || 'no cargaron los tiles');
      }
    });
    map.on('data', (e) => {
      if (e.sourceId === 'traffic-flow' && e.tile && !trafficReported) {
        trafficReported = true;
        reportSource('traffic', 'ok', (trafficMode === 'vector' ? 'flujo vectorial de TomTom' : 'flujo raster de TomTom') + ' (caché de 5 min)');
      }
    });
  }
  if (map.getLayer('traffic-flow-layer')) map.setLayoutProperty('traffic-flow-layer', 'visibility', visible ? 'visible' : 'none');
}

function initRouting(map) {
  routeMap = map;
  // Botones "Ruta de bomberos" dentro de popups (se crean y destruyen dinámicamente).
  document.addEventListener('click', (e) => {
    const btn = e.target.closest && e.target.closest('.fw-route-btn');
    if (!btn) return;
    planEmergencyRoute(Number(btn.dataset.lat), Number(btn.dataset.lon));
  });

  const clear = document.getElementById('btn-route-clear');
  if (clear) clear.addEventListener('click', clearEmergencyRoute);

  const tog = document.getElementById('toggle-traffic');
  if (tog) {
    if (!FW.tomtom) {
      tog.disabled = true;
      tog.title = 'Falta TOMTOM_KEY en .env (y el servidor local)';
      reportSource('traffic', 'off', FW.proxy ? 'falta TOMTOM_KEY en .env' : 'requiere el servidor local');
    }
    tog.addEventListener('change', (e) => setTrafficVisible(map, e.target.checked));
    ['traffic-detail', 'traffic-opacity'].forEach((id) => {
      const el = document.getElementById(id);
      if (!el) return;
      el.disabled = !FW.tomtom;
      el.addEventListener('input', () => applyTrafficStyle(map));
    });
  }
}
