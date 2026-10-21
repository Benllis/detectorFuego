// ============================================================
// FireWatch · Mapa 3D — zonas detalladas por todo Chile (carga al acercar)
//
// Lo que NO se puede traer de todo el país de una vez (agua, grifos, cuarteles
// de OpenStreetMap y la grilla de vegetación) se carga por TESELAS de 0,25°
// (~27 x 22 km) solo cuando el mapa está acercado (zoom >= ZONES.minZoom) y
// cada teselas se carga una vez; lo ya cargado nunca se reemplaza ni salta.
//
//  - Overpass (agua, grifos, cuarteles): una teselas por vez, en cola, con el
//    helper compartido fetchOverpass (caché 24 h, mirror, reintentos).
//  - Vegetación (ESA WorldCover): solo si la capa está encendida, o cuando la
//    pide el simulador / la consulta de punto (zonesEnsureFuel).
//  - Lo cargado alimenta las mismas listas globales que ya usan las rutas
//    (fireStations, hydrants, waterSources, waterRivers), así que "ruta al
//    foco" y "cuartel más cercano" funcionan fuera de Santiago.
//  - Las teselas dentro del área de Santiago (AREA) se omiten: ya las cargan
//    las capas originales.
//
// Cargar después de las capas originales y antes de main.js.
// ============================================================

const ZONES = {
  tileDeg: 0.25,
  minZoom: 9.5,       // por debajo solo se ven focos, DMC, alertas y clima nacional
  maxTiles: 40,       // tope de teselas con Overpass por sesión (memoria y cortesía con el servidor)
  maxPerView: 6,      // teselas más cercanas al centro que se piden por movimiento
  debounceMs: 900,
  retryMs: 60000,
};

const zonesState = {
  map: null,
  tiles: new Map(),   // clave -> { ix, iy, bounds, done:{ovp,fuel}, retryAt, inside }
  queue: { ovp: [], fuel: [] }, running: { ovp: false, fuel: false }, timer: null, // dos colas: la vegetación no espera a Overpass
  feat: { rivers: [], waters: [], hydrants: [], stations: [] },
  ids: { rivers: new Set(), waters: new Set(), hydrants: new Set(), stations: new Set() },
  layersReady: false, seeded: false,
};

const zonesTileKey = (ix, iy) => `${ix}:${iy}`;
const zonesSafeId = (key) => key.replace(/-/g, 'm').replace(':', '_');
function zonesTileBounds(ix, iy) {
  const s = ZONES.tileDeg;
  return [[ix * s, iy * s], [(ix + 1) * s, (iy + 1) * s]];
}
function zonesTilesIn(west, south, east, north) {
  const s = ZONES.tileDeg, out = [];
  for (let ix = Math.floor(west / s); ix <= Math.floor(east / s); ix++) {
    for (let iy = Math.floor(south / s); iy <= Math.floor(north / s); iy++) out.push({ ix, iy });
  }
  return out;
}
function zonesInsideArea(b) {
  const a = boundsFromCenterKm(AREA.center, AREA.radiusKm);
  return b[0][0] >= a[0][0] && b[1][0] <= a[1][0] && b[0][1] >= a[0][1] && b[1][1] <= a[1][1];
}
function zonesTile(ix, iy) {
  const key = zonesTileKey(ix, iy);
  if (!zonesState.tiles.has(key)) {
    const bounds = zonesTileBounds(ix, iy);
    zonesState.tiles.set(key, { key, ix, iy, bounds, done: { ovp: false, fuel: false }, retryAt: 0, inside: zonesInsideArea(bounds), failed: [] });
  }
  return zonesState.tiles.get(key);
}

const zonesVisible = (id) => { const el = document.getElementById(id); return !el || el.checked; };

function zonesSeedIds() {
  if (zonesState.seeded) return;
  zonesState.seeded = true;
  waterSources.forEach((x) => zonesState.ids.waters.add(x.id));
  waterRivers.forEach((x) => zonesState.ids.rivers.add(x.id));
  hydrants.forEach((x) => zonesState.ids.hydrants.add(x.id));
  fireStations.forEach((x) => zonesState.ids.stations.add(x.id));
}


/** Agrega un elemento Overpass de agua (lago/embalse o río) a las listas globales y a las capas de zona. */
function zonesAddWater(el) {
  const tags = el.tags || {}, f = zonesState.feat, ids = zonesState.ids;
  const name = tags.name || tags['name:es'] || 'Sin nombre';
  if (el.type === 'way' && Array.isArray(el.geometry) && tags.waterway === 'river') {
    if (ids.rivers.has(el.id)) return;
    ids.rivers.add(el.id);
    const coords = el.geometry.map((g) => [g.lon, g.lat]);
    waterRivers.push({ id: el.id, name, coords });
    if (coords.length > 1) f.rivers.push({ type: 'Feature', properties: { id: el.id, name }, geometry: { type: 'LineString', coordinates: coords } });
  } else if (el.center) {
    if (ids.waters.has(el.id)) return;
    ids.waters.add(el.id);
    const type = tags.water || 'agua';
    waterSources.push({ id: el.id, name, type, lat: el.center.lat, lon: el.center.lon });
    f.waters.push({ type: 'Feature', properties: { id: el.id, name, type }, geometry: { type: 'Point', coordinates: [el.center.lon, el.center.lat] } });
  }
}

function zonesAddHydrant(el) {
  if (el.type !== 'node' || zonesState.ids.hydrants.has(el.id)) return;
  zonesState.ids.hydrants.add(el.id);
  const tags = el.tags || {};
  const h = { id: el.id, lat: el.lat, lon: el.lon, tipo: tags['fire_hydrant:type'] || 'desconocido', color: tags['fire_hydrant:color'] || null, diametro: tags['fire_hydrant:diameter'] || null };
  hydrants.push(h);
  zonesState.feat.hydrants.push({ type: 'Feature', properties: { id: h.id, tipo: h.tipo, diametro: h.diametro }, geometry: { type: 'Point', coordinates: [h.lon, h.lat] } });
}

function zonesAddStation(el) {
  const c = el.center || (el.type === 'node' ? { lat: el.lat, lon: el.lon } : null);
  if (!c || zonesState.ids.stations.has(el.id)) return;
  zonesState.ids.stations.add(el.id);
  const tags = el.tags || {};
  const st = { id: el.id, name: tags.name || tags['name:es'] || 'Cuartel sin nombre en OSM', operator: tags.operator || null, phone: tags.phone || tags['contact:phone'] || null, lat: c.lat, lon: c.lon };
  fireStations.push(st);
  zonesState.feat.stations.push({ type: 'Feature', properties: { id: st.id, name: st.name, operator: st.operator, phone: st.phone }, geometry: { type: 'Point', coordinates: [st.lon, st.lat] } });
}

function zonesEnsureLayers(map) {
  if (zonesState.layersReady) return;
  zonesState.layersReady = true;
  const empty = { type: 'FeatureCollection', features: [] };
  const vis = (id) => ({ visibility: zonesVisible(id) ? 'visible' : 'none' });
  map.addSource('zone-water-rivers', { type: 'geojson', data: empty });
  map.addLayer({
    id: 'zone-water-rivers-line', type: 'line', source: 'zone-water-rivers',
    layout: { 'line-cap': 'round', 'line-join': 'round', ...vis('toggle-water') },
    paint: { 'line-color': '#3b82f6', 'line-width': ['interpolate', ['linear'], ['zoom'], 9, 1.2, 14, 3], 'line-opacity': 0.85 },
  });
  map.addSource('zone-water-sources', { type: 'geojson', data: empty });
  map.addLayer({ id: 'zone-water-sources-glow', type: 'circle', source: 'zone-water-sources', layout: vis('toggle-water'),
    paint: { 'circle-radius': 12, 'circle-color': '#3b82f6', 'circle-opacity': 0.2 } });
  map.addLayer({ id: 'zone-water-sources-point', type: 'circle', source: 'zone-water-sources', layout: vis('toggle-water'),
    paint: { 'circle-radius': 5, 'circle-color': '#3b82f6', 'circle-stroke-color': '#ffffff', 'circle-stroke-width': 1.5 } });
  map.addSource('zone-hydrants', { type: 'geojson', data: empty });
  map.addLayer({ id: 'zone-hydrants-point', type: 'circle', source: 'zone-hydrants', minzoom: 12, layout: vis('toggle-hydrants'),
    paint: { 'circle-radius': ['interpolate', ['linear'], ['zoom'], 12, 2.5, 16, 6], 'circle-color': '#a855f7', 'circle-stroke-color': '#ffffff', 'circle-stroke-width': 1 } });
  map.addSource('zone-fire-stations', { type: 'geojson', data: empty });
  map.addLayer({ id: 'zone-fire-stations-glow', type: 'circle', source: 'zone-fire-stations', layout: vis('toggle-firestations'),
    paint: { 'circle-radius': 14, 'circle-color': '#ff6a3d', 'circle-opacity': 0.22 } });
  map.addLayer({ id: 'zone-fire-stations-point', type: 'circle', source: 'zone-fire-stations', layout: vis('toggle-firestations'),
    paint: { 'circle-radius': 6, 'circle-color': '#ff6a3d', 'circle-stroke-color': '#ffffff', 'circle-stroke-width': 1.5 } });

  const popup = (layer, html) => map.on('click', layer, (e) => {
    new maplibregl.Popup({ offset: 10 }).setLngLat(e.lngLat).setHTML(html(e.features[0].properties)).addTo(map);
  });
  popup('zone-water-sources-point', (p) => `<b>${p.name}</b><br>Tipo: ${p.type}`);
  popup('zone-water-rivers-line', (p) => `<b>${p.name}</b><br>Tipo: río`);
  const esc = (t) => String(t == null ? '' : t).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const src = () => (zonesState.localBomberos ? `<br><small>Fuente: ${esc(zonesState.localBomberos.label)}</small>` : '');
  popup('zone-hydrants-point', (p) => `<b>🚰 Grifo</b><br>Tipo: ${esc(p.tipo)}${p.diametro ? '<br>Diámetro: ' + esc(p.diametro) : ''}${p.comuna && p.comuna !== 'null' ? '<br>' + esc(p.comuna) : ''}${p.anio && p.anio !== 'null' ? '<br>Catastro: ' + esc(p.anio) : ''}${src()}`);
  popup('zone-fire-stations-point', (p) => {
    let extra = '';
    try { extra = p.extra ? Object.entries(JSON.parse(p.extra)).map(([k, v]) => `<br>${esc(k)}: ${esc(v)}`).join('') : ''; } catch (e) { /* sin extras */ }
    return `<b>🚒 ${esc(p.name)}</b>${p.operator ? '<br>Operador: ' + esc(p.operator) : ''}${p.phone ? '<br>Teléfono: ' + esc(p.phone) : ''}${extra}${src()}`;
  });
  ['zone-water-sources-point', 'zone-water-rivers-line', 'zone-hydrants-point', 'zone-fire-stations-point'].forEach((id) => {
    map.on('mouseenter', id, () => { map.getCanvas().style.cursor = 'pointer'; });
    map.on('mouseleave', id, () => { map.getCanvas().style.cursor = ''; });
  });
}

/** Carga en las mismas listas y capas de zona los cuarteles y grifos de la copia local del SIG de Bomberos. */
function zonesApplyBomberos(data) {
  const map = zonesState.map;
  zonesSeedIds();
  zonesEnsureLayers(map);
  const f = zonesState.feat, ids = zonesState.ids;
  data.stations.forEach((s) => {
    if (ids.stations.has(s.id)) return;
    ids.stations.add(s.id);
    fireStations.push({ id: s.id, name: s.name, operator: s.operator, phone: s.phone, lat: s.lat, lon: s.lon });
    f.stations.push({ type: 'Feature', properties: { id: s.id, name: s.name, operator: s.operator, phone: s.phone, kind: s.kind, extra: s.extra ? JSON.stringify(s.extra) : '' },
      geometry: { type: 'Point', coordinates: [s.lon, s.lat] } });
  });
  data.hydrants.forEach((h) => {
    if (ids.hydrants.has(h.id)) return;
    ids.hydrants.add(h.id);
    hydrants.push({ id: h.id, lat: h.lat, lon: h.lon, tipo: h.tipo, color: null, diametro: h.diametro });
    f.hydrants.push({ type: 'Feature', properties: { id: h.id, tipo: h.tipo, diametro: h.diametro, anio: h.anio, comuna: h.comuna },
      geometry: { type: 'Point', coordinates: [h.lon, h.lat] } });
  });
  zonesState.localBomberos = { label: data.label };
  zonesState.hydrantsDone = true;
  zonesFlush(map);
  zonesReport();
}

function zonesFlush(map) {
  const f = zonesState.feat;
  const set = (id, features) => { const s = map.getSource(id); if (s) s.setData({ type: 'FeatureCollection', features }); };
  set('zone-water-rivers', f.rivers); set('zone-water-sources', f.waters);
  set('zone-hydrants', f.hydrants); set('zone-fire-stations', f.stations);
}

function zonesLogFail(part, key, err) {
  console.warn('[Zones]', part, key, err.message);
  if (typeof fwLog === 'function') fwLog('zonas', 'warn', `${part} de la tesela ${key}: ${err.message}`, { phase: 'overpass', variant: key });
}

const zonesRound = (b) => ({ s: b[0][1].toFixed(2), w: b[0][0].toFixed(2), n: b[1][1].toFixed(2), e: b[1][0].toFixed(2) });

/** Agua, grifos y cuarteles de una tesela (3 consultas Overpass, una tras otra). */
async function zonesLoadOverpass(tile) {
  zonesSeedIds();
  const [west, south] = tile.bounds[0], [east, north] = tile.bounds[1];
  const r = zonesRound(tile.bounds), k = `${r.s},${r.w},${r.n},${r.e}`;
  const failed = [];
  const f = zonesState.feat, ids = zonesState.ids;

  try {
    const data = await fetchOverpass(buildOverpassQuery(south, west, north, east), `water:${k}`);
    (data.elements || []).forEach(zonesAddWater);
  } catch (err) { failed.push('agua'); zonesLogFail('agua', tile.key, err); }

  if (!zonesState.hydrantsDone && !zonesState.localBomberos) { // si los grifos de todo el país ya cargaron, la tesela no los vuelve a pedir
    try {
      const data = await fetchOverpass(buildHydrantsQuery(south, west, north, east), `hydrants:${k}`);
      (data.elements || []).forEach(zonesAddHydrant);
    } catch (err) { failed.push('grifos'); zonesLogFail('grifos', tile.key, err); }
  }

  if (!zonesState.localBomberos) { // con la copia local del SIG de Bomberos no hace falta OSM para cuarteles
    try {
      const data = await fetchOverpass(buildFireStationsQuery(south, west, north, east), `firestations:${k}`);
      (data.elements || []).forEach(zonesAddStation);
    } catch (err) { failed.push('cuarteles'); zonesLogFail('cuarteles', tile.key, err); }
  }

  return failed;
}

/** Vegetación de una tesela: grilla consultable + capa de imagen (visible según el interruptor). */
async function zonesLoadFuel(tile, map) {
  const grid = await loadFuelGrid(tile.bounds);
  if (zonesState.fuelMode !== 'images') return grid; // en modo teselas del servidor solo se necesita la grilla consultable (simulador, consulta de punto)
  const id = `zone-fuel-${zonesSafeId(tile.key)}`;
  if (!map.getSource(id)) {
    const [west, south] = tile.bounds[0], [east, north] = tile.bounds[1];
    map.addSource(id, { type: 'image', url: paintFuelGrid(grid).toDataURL(), coordinates: [[west, north], [east, north], [east, south], [west, south]] });
    const before = map.getLayer('temp-heat-layer') ? 'temp-heat-layer' : (map.getLayer('fire-pixels-glow') ? 'fire-pixels-glow' : undefined);
    map.addLayer({ id: `${id}-layer`, type: 'raster', source: id, layout: { visibility: zonesVisible('toggle-fuel') ? 'visible' : 'none' },
      paint: { 'raster-opacity': 0.6, 'raster-fade-duration': 0 } }, before);
  }
  return grid;
}

function zonesReport(natBad) {
  const all = [...zonesState.tiles.values()];
  const ovp = all.filter((t) => !t.inside && t.done.ovp).length;
  const bad = all.filter((t) => t.failed.length).length;
  const z = zonesState.map ? zonesState.map.getZoom() : 0;
  const hint = z < ZONES.minZoom ? ` · acerca el mapa (zoom ≥ ${ZONES.minZoom}) para ver ríos y teselas de detalle` : '';
  const f = zonesState.feat;
  const lb = zonesState.localBomberos ? ` · cuarteles y grifos: ${zonesState.localBomberos.label}` : '';
  const nat = zonesState.nationalDone ? 'país completo' : `país ${zonesState.nationalBands || 0}/${ZONES_BANDS.length} franjas`;
  const fuelTxt = { tiles: 'teselas del servidor (paleta ' + (zonesState.fuelPalette === 'esa' ? 'ESA' : 'propia') + ')', images: 'imágenes por zona (respaldo)' }[zonesState.fuelMode] || 'sin iniciar';
  reportSource('zones', bad || natBad || zonesState.fuelError ? 'warn' : 'ok',
    `${nat}: ${f.stations.length} cuarteles, ${f.waters.length} lagos/embalses, ${f.hydrants.length} grifos · detalle: ${ovp} teselas con ${f.rivers.length} ríos · vegetación: ${fuelTxt}` +
    `${bad ? ` · ${bad} teselas con fallas (se reintenta)` : ''}${zonesState.fuelError ? ' · ' + zonesState.fuelError : ''}${lb}${hint}`);
}

async function zonesRunQueue(kind) {
  if (zonesState.running[kind]) return;
  zonesState.running[kind] = true;
  const map = zonesState.map, q = zonesState.queue[kind];
  try {
    while (q.length) {
      const job = q.shift(), tile = job.tile;
      if (kind === 'ovp' && !tile.done.ovp && !tile.inside) {
        zonesEnsureLayers(map);
        const failed = await zonesLoadOverpass(tile);
        tile.failed = failed;
        if (failed.length) tile.retryAt = Date.now() + ZONES.retryMs; else tile.done.ovp = true;
        zonesFlush(map);
        zonesReport();
      } else if (kind === 'fuel' && !tile.done.fuel && !tile.inside) {
        try { await zonesLoadFuel(tile, map); tile.done.fuel = true; } catch (err) {
          console.warn('[Zones] vegetación', tile.key, err.message);
          if (typeof fwLog === 'function') fwLog('zonas', 'warn', `vegetación de la tesela ${tile.key}: ${err.message}`, { phase: 'vegetación', variant: tile.key });
          tile.fuelRetryAt = Date.now() + ZONES.retryMs;
        }
        zonesReport();
      }
      if (job.resolve) job.resolve();
    }
  } finally { zonesState.running[kind] = false; }
}

function zonesEnqueue(tile, kind, resolve) {
  const q = zonesState.queue[kind];
  if (!resolve && q.some((j) => j.tile === tile)) return;
  q.push({ tile, kind, resolve });
  zonesRunQueue(kind);
}

/** Teselas visibles (las más cercanas al centro primero), sin pasarse del tope. */
function zonesWantedTiles(map) {
  const b = map.getBounds(), c = map.getCenter();
  return zonesTilesIn(b.getWest(), b.getSouth(), b.getEast(), b.getNorth())
    .map((t) => ({ ...t, d: Math.hypot((t.ix + 0.5) * ZONES.tileDeg - c.lng, (t.iy + 0.5) * ZONES.tileDeg - c.lat) }))
    .sort((a, b2) => a.d - b2.d).slice(0, ZONES.maxPerView);
}

function zonesOnMove() {
  const map = zonesState.map;
  if (!map) return;
  if (map.getZoom() < ZONES.minZoom) { zonesReport(); return; }
  const now = Date.now();
  let loadedOvp = [...zonesState.tiles.values()].filter((t) => !t.inside && t.done.ovp).length;
  zonesWantedTiles(map).forEach(({ ix, iy }) => {
    const t = zonesTile(ix, iy);
    if (t.inside) return;
    if (!t.done.ovp && now >= t.retryAt && loadedOvp < ZONES.maxTiles) { zonesEnqueue(t, 'ovp'); loadedOvp++; }
    if (zonesState.fuelMode === 'images' && zonesVisible('toggle-fuel') && !t.done.fuel && now >= (t.fuelRetryAt || 0)) zonesEnqueue(t, 'fuel');
  });
}

/**
 * Garantiza la grilla de vegetación alrededor de un punto (para simular o consultar fuera de Santiago).
 * Devuelve cuando están cargadas las teselas de la ventana; los fallos no lanzan (se verá como "sin datos").
 */
function zonesEnsureFuel(lat, lon, radiusKm) {
  const dLat = radiusKm / 111.32, dLon = radiusKm / (111.32 * Math.cos(lat * Math.PI / 180));
  const jobs = zonesTilesIn(lon - dLon, lat - dLat, lon + dLon, lat + dLat).map(({ ix, iy }) => zonesTile(ix, iy))
    .filter((t) => !t.inside && !t.done.fuel);
  return Promise.all(jobs.map((t) => new Promise((resolve) => zonesEnqueue(t, 'fuel', resolve))));
}


// ---------- nivel país: cuarteles, lagos/embalses y grifos (una vez, por franjas de latitud) ----------
// Cada franja se pide con un polígono alrededor del contorno de Chile (filtro `poly` de Overpass):
// es mucho más rápido que filtrar por área administrativa y deja fuera Argentina/Bolivia/Perú.
// Franjas más finas donde hay más datos (centro del país). Si una franja falla, se parte en dos y se reintenta.
const ZONES_BANDS = [[-56.5, -44], [-44, -40], [-40, -37], [-37, -35], [-35, -33.2], [-33.2, -30], [-30, -24], [-24, -17.3]];
const ZONES_OVP_OPTS = { timeoutMs: 90000, bigCache: true };

/** Polígono "lat lon lat lon…" de la franja [s, n]: costa oeste (N→S) y límite este (S→N), con 0,3° de margen. */
function zonesBandPoly(s, n) {
  const lats = [];
  for (let la = n; la > s; la -= 0.5) lats.push(la);
  lats.push(s);
  const west = [], east = [];
  lats.forEach((la) => {
    const e = chileEdgesAt(Math.min(-17.5, Math.max(-56, la)));
    west.push(`${la.toFixed(2)} ${(e[0] - 0.3).toFixed(2)}`);
    east.unshift(`${la.toFixed(2)} ${(e[1] + 0.3).toFixed(2)}`);
  });
  return west.concat(east).join(' ');
}

const zonesQueries = {
  stations: (s, n) => { const p = `(poly:"${zonesBandPoly(s, n)}")`; return `[out:json][timeout:90];(${['node', 'way', 'relation'].map((t) => `${t}["amenity"="fire_station"]${p};`).join('')});out center tags;`; },
  lakes: (s, n) => { const p = `(poly:"${zonesBandPoly(s, n)}")`; return `[out:json][timeout:90];(${['way', 'relation'].map((t) => `${t}["natural"="water"]["water"~"^(lake|reservoir)$"]["name"]${p};`).join('')});out center tags;`; },
  hydrants: (s, n) => `[out:json][timeout:120];node["emergency"="fire_hydrant"](poly:"${zonesBandPoly(s, n)}");out;`,
};

/** Consulta una franja [s, n]; si falla y es lo bastante ancha, la parte en dos. Devuelve cuántas franjas fallaron. */
async function zonesBand(label, s, n, kind, onElements, depth = 0) {
  try {
    const data = await fetchOverpass(zonesQueries[kind](s, n), `nat2:${kind}:${s}:${n}`, ZONES_OVP_OPTS);
    (data.elements || []).forEach(onElements);
    return 0;
  } catch (err) {
    console.warn('[Zones]', label, s, n, err.message);
    if (typeof fwLog === 'function') fwLog('zonas', 'warn', `${label} ${s}…${n}: ${err.message}`, { phase: depth ? `franja partida (nivel ${depth})` : 'franja nacional', variant: `${s}:${n}` });
    if (depth >= 2 || n - s < 2) return 1;
    const mid = (s + n) / 2;
    return (await zonesBand(label, s, mid, kind, onElements, depth + 1)) + (await zonesBand(label, mid, n, kind, onElements, depth + 1));
  }
}

async function zonesLoadNational() {
  const map = zonesState.map;
  zonesSeedIds();
  zonesEnsureLayers(map);
  let bad = 0, hbad = 0;
  for (const [s, n] of ZONES_BANDS) {
    if (!zonesState.localBomberos) bad += await zonesBand('cuarteles', s, n, 'stations', zonesAddStation);
    bad += await zonesBand('lagos y embalses', s, n, 'lakes', zonesAddWater);
    zonesFlush(map);
    if (!zonesState.localBomberos) hbad += await zonesBand('grifos', s, n, 'hydrants', zonesAddHydrant); // se dibujan solo desde zoom 12
    zonesFlush(map);
    zonesState.nationalBands = (zonesState.nationalBands || 0) + 1;
    zonesReport(bad + hbad);
  }
  zonesState.nationalDone = true;
  zonesState.hydrantsDone = hbad === 0; // si alguna franja falló, las teselas siguen pidiendo sus grifos
  zonesReport(bad + hbad);
}

/** Teselas WorldCover (3°) que tocan Chile; una sola búsqueda STAC por sesión. */
function zonesFuelItems() {
  if (!zonesState.fuelItems) {
    zonesState.fuelItems = (async () => {
      const res = await fetch(PC_STAC_SEARCH_URL, { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ collections: ['esa-worldcover'], bbox: NATION.bbox, limit: 400 }) });
      if (!res.ok) throw new Error(`Búsqueda STAC falló (HTTP ${res.status})`);
      // Solo teselas de 3° que tocan Chile (el bbox del país también trae Argentina, Bolivia y Perú).
      const touchesChile = (b) => { for (let i = 0; i <= 4; i++) for (let j = 0; j <= 4; j++) { if (chileMaskAlpha(b[1] + (b[3] - b[1]) * j / 4, b[0] + (b[2] - b[0]) * i / 4) > 0.5) return true; } return false; };
      return ((await res.json()).features || []).filter((it) => touchesChile(it.bbox));
    })();
    zonesState.fuelItems.catch(() => { zonesState.fuelItems = null; });
  }
  return zonesState.fuelItems;
}

// ---------- vegetación en TESELAS del servidor (modo principal) ----------
// En vez de bajar y clasificar imágenes en el navegador, cada tesela WorldCover (3°) se muestra como una
// capa raster por XYZ del servidor de Planetary Computer, ya coloreada con la paleta de riesgo del proyecto
// (parámetro `colormap`). Se carga solo lo que se ve, a cualquier zoom, con una sola paleta en todo Chile
// (incluida Santiago). Si el servidor rechaza esa paleta, prueba la paleta ESA estándar (`colormap_name`) y,
// si tampoco, vuelve al modo de imágenes por zona de antes.
const PC_ITEM_TILES = 'https://planetarycomputer.microsoft.com/api/data/v1/item/tiles/WebMercatorQuad';
const FUEL_RASTER_VALUES = { 10: 'bosque', 20: 'matorral', 30: 'pastizal', 40: 'cultivo', 50: 'urbano', 60: 'desnudo', 70: 'nieve', 80: 'agua', 90: 'humedal', 95: 'manglar', 100: 'musgo' };

function zonesFuelColormap() {
  const out = {};
  Object.entries(FUEL_RASTER_VALUES).forEach(([v, key]) => { out[v] = WORLDCOVER_CLASSES.find((c) => c.key === key).rgba; });
  return out;
}
function zonesFuelTileUrl(itemId, palette) {
  const pal = palette === 'esa' ? 'colormap_name=esa-worldcover' : `colormap=${encodeURIComponent(JSON.stringify(zonesFuelColormap()))}`;
  return `${PC_ITEM_TILES}/{z}/{x}/{y}@1x?collection=esa-worldcover&item=${itemId}&assets=map&format=png&${pal}`;
}
const zonesFuelTilesActive = () => zonesState.fuelMode === 'tiles';

async function zonesInitFuelTiles(map) {
  if (zonesState.fuelTilesInit) return zonesState.fuelTilesInit;
  zonesState.fuelTilesInit = (async () => {
    const items = await zonesFuelItems();
    const before = map.getLayer('temp-heat-layer-nacional') ? 'temp-heat-layer-nacional' : (map.getLayer('fire-pixels-glow') ? 'fire-pixels-glow' : undefined);
    const vis = zonesVisible('toggle-fuel') ? 'visible' : 'none';
    zonesState.fuelTileIds = [];
    items.forEach((it, i) => {
      const id = `zone-fuelt-${i}`;
      map.addSource(id, { type: 'raster', tiles: [zonesFuelTileUrl(it.id, 'own')], tileSize: 256, minzoom: 0, maxzoom: 14, bounds: it.bbox, attribution: 'ESA WorldCover 2021 (Planetary Computer)' });
      map.addLayer({ id: `${id}-layer`, type: 'raster', source: id, layout: { visibility: vis }, paint: { 'raster-opacity': 0.6, 'raster-fade-duration': 150 } }, before);
      zonesState.fuelTileIds.push({ id, item: it.id });
    });
    zonesState.fuelMode = 'tiles'; zonesState.fuelPalette = 'own';
    zonesState.fuelTileErr = 0; zonesState.fuelTileOk = 0;
    if (map.getLayer('worldcover-layer')) map.setLayoutProperty('worldcover-layer', 'visibility', 'none'); // la capa antigua de Santiago sobraría
    map.on('data', (e) => { if (zonesState.fuelMode === 'tiles' && e.dataType === 'source' && e.tile && String(e.sourceId).startsWith('zone-fuelt-')) zonesState.fuelTileOk++; });
    map.on('error', (e) => {
      if (zonesState.fuelMode !== 'tiles' || !String(e.sourceId || '').startsWith('zone-fuelt-')) return;
      // Tras cambiar de paleta siguen llegando errores de las peticiones antiguas: no cuentan.
      const url = String((e.error && e.error.url) || '');
      if (url && ((zonesState.fuelPalette === 'esa') !== url.includes('colormap_name='))) return;
      zonesState.fuelTileErr++;
      if (typeof fwLog === 'function') fwLog('vegetacion', 'warn', (e.error && e.error.message) || 'error de tesela', { phase: `teselas paleta ${zonesState.fuelPalette}`, variant: e.sourceId, detail: `ok=${zonesState.fuelTileOk} err=${zonesState.fuelTileErr}` });
      if (zonesState.fuelTileErr >= 6 && zonesState.fuelTileOk === 0) zonesFuelTilesFallback(map);
    });
    zonesReport();
  })();
  zonesState.fuelTilesInit.catch((err) => {
    console.warn('[Zones] teselas de vegetación', err);
    if (typeof fwLog === 'function') fwLog('vegetacion', 'warn', err.message, { phase: 'inicio de teselas' });
    zonesState.fuelTilesInit = null;
    zonesState.fuelMode = 'images'; zonesState.fuelError = `teselas no disponibles (${err.message})`;
    zonesReport();
  });
  return zonesState.fuelTilesInit;
}

/** Paleta propia rechazada → paleta ESA; ESA rechazada → imágenes por zona (el método anterior). */
function zonesFuelTilesFallback(map) {
  if (zonesState.fuelPalette === 'own') {
    zonesState.fuelPalette = 'esa'; zonesState.fuelTileErr = 0; zonesState.fuelTileOk = 0;
    zonesState.fuelTileIds.forEach(({ id, item }) => { const src = map.getSource(id); if (src && src.setTiles) src.setTiles([zonesFuelTileUrl(item, 'esa')]); });
    zonesState.fuelError = 'la paleta propia fue rechazada: se usa la paleta ESA estándar';
  } else {
    zonesState.fuelMode = 'images';
    zonesState.fuelTileIds.forEach(({ id }) => { if (map.getLayer(`${id}-layer`)) map.removeLayer(`${id}-layer`); if (map.getSource(id)) map.removeSource(id); });
    zonesState.fuelTileIds = [];
    zonesState.fuelError = 'teselas no disponibles: vegetación por imágenes de zona';
    if (typeof setFuelCoverVisible === 'function') setFuelCoverVisible(map, zonesVisible('toggle-fuel'));
    if (zonesVisible('toggle-fuel')) { zonesLoadFuelNational(); zonesOnMove(); }
  }
  if (typeof fwLog === 'function') fwLog('vegetacion', 'warn', zonesState.fuelError, { phase: 'respaldo' });
  zonesReport();
}

// ---------- respaldo: vegetación a escala país con imágenes gruesas (solo si fallan las teselas) ----------
const ZONES_FUEL_NAT_PX = 512; // por tile de 3° ≈ 650 m/px

async function zonesLoadFuelNational() {
  const map = zonesState.map;
  if (zonesState.fuelNat) return zonesState.fuelNat;
  zonesState.fuelNat = (async () => {
    const items = await zonesFuelItems();
    let ok = 0, bad = 0;
    for (const item of items) {
      try {
        const url = `${PC_PREVIEW_BASE_URL}?collection=esa-worldcover&item=${item.id}&assets=map&colormap_name=esa-worldcover&format=png&width=${ZONES_FUEL_NAT_PX}&height=${ZONES_FUEL_NAT_PX}&max_size=${ZONES_FUEL_NAT_PX}`;
        const r = await fetch(url);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const bmp = await createImageBitmap(await r.blob());
        const c = classifyWorldCover(bmp, 0, 0, bmp.width, bmp.height, bmp.width, bmp.height);
        const [w, so, e, n] = item.bbox;
        const id = `zone-fuelnat-${ok + bad}`;
        map.addSource(id, { type: 'image', url: paintFuelGrid({ cls: c.cls, w: c.w, h: c.h }).toDataURL(), coordinates: [[w, n], [e, n], [e, so], [w, so]] });
        map.addLayer({ id: `${id}-layer`, type: 'raster', source: id,
          layout: { visibility: zonesVisible('toggle-fuel') ? 'visible' : 'none' },
          paint: { 'raster-opacity': ['interpolate', ['linear'], ['zoom'], 8, 0.6, 11, 0.3], 'raster-fade-duration': 0 } },
          map.getLayer('temp-heat-layer-nacional') ? 'temp-heat-layer-nacional' : (map.getLayer('fire-pixels-glow') ? 'fire-pixels-glow' : undefined));
        ok++;
      } catch (err) { bad++; console.warn('[Zones] vegetación nacional', item.id, err.message); }
    }
    return { ok, bad };
  })();
  zonesState.fuelNat.catch((err) => { zonesState.fuelNat = null; zonesState.fuelError = err.message; zonesReport(); });
  return zonesState.fuelNat;
}

/** Mosaico de todas las grillas de vegetación cargadas: misma interfaz que FuelGrid.cell para el simulador. */
const fuelMosaic = {
  contains(lat, lon) { return !!fuelGridFor(lat, lon); },
  cell(lat, lon) { const g = fuelGridFor(lat, lon); return g ? g.cell(lat, lon) : null; },
};

function initZones(map) {
  zonesState.map = map;
  const schedule = () => { clearTimeout(zonesState.timer); zonesState.timer = setTimeout(zonesOnMove, ZONES.debounceMs); };
  map.on('moveend', schedule);
  // Los interruptores existentes también gobiernan las capas de zona.
  const bind = (id, layers) => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('change', () => {
      layers.forEach((l) => { if (map.getLayer(l)) map.setLayoutProperty(l, 'visibility', el.checked ? 'visible' : 'none'); });
    });
  };
  bind('toggle-water', ['zone-water-sources-point', 'zone-water-sources-glow', 'zone-water-rivers-line']);
  bind('toggle-hydrants', ['zone-hydrants-point']);
  bind('toggle-firestations', ['zone-fire-stations-point', 'zone-fire-stations-glow']);
  const fuelEl = document.getElementById('toggle-fuel');
  if (fuelEl) fuelEl.addEventListener('change', () => {
    map.getStyle().layers.filter((l) => l.id.startsWith('zone-fuel')).forEach((l) => {
      map.setLayoutProperty(l.id, 'visibility', fuelEl.checked ? 'visible' : 'none');
    });
    if (fuelEl.checked && zonesState.fuelMode === 'images') { schedule(); zonesLoadFuelNational(); }
  });
  zonesReport();
  schedule();
  setTimeout(() => zonesLoadNational().catch((e) => console.warn('[Zones] nacional', e)), 6000); // después de las capas de Santiago
  setTimeout(() => zonesInitFuelTiles(map).catch(() => {}), 2500);
}

if (typeof module !== 'undefined') module.exports = { ZONES, zonesTilesIn, zonesTileBounds };
