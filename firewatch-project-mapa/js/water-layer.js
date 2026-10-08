// ============================================================
// FireWatch · Mapa 3D — capa de fuentes de agua reales
//
// Overpass API (OpenStreetMap, gratis, sin API key): lagos, embalses y
// ríos dentro del área de cobertura. Pensado para que aviones/
// helicópteros tengan una referencia de dónde recargar agua cerca de
// un foco. Usa fetchOverpass() (overpass-utils.js).
//
// v2 — los ríos se dibujan con su TRAZADO real (antes se reducían a un
// punto en el centro de cada tramo, lo que no dice dónde se puede tomar
// agua). Lagos y embalses siguen siendo un punto (su centro).
// findNearestWaterSource considera los vértices del río.
// ============================================================

let waterSources = []; // lagos/embalses: { id, name, type, lat, lon }
let waterRivers = [];  // ríos: { id, name, coords: [[lon, lat], ...] }

function buildOverpassQuery(south, west, north, east) {
  const bbox = `${south},${west},${north},${east}`;
  return `
    [out:json][timeout:60];
    (
      way["natural"="water"]["water"~"^(lake|reservoir)$"](${bbox});
      relation["natural"="water"]["water"~"^(lake|reservoir)$"](${bbox});
    );
    out center tags;
    way["waterway"="river"](${bbox});
    out geom tags;
  `;
}

async function addWaterSourcesLayer(map, area) {
  if (map.getSource('water-sources')) return;

  const bounds = boundsFromCenterKm(area.center, area.radiusKm);
  const [west, south] = bounds[0];
  const [east, north] = bounds[1];

  try {
    const data = await fetchOverpass(
      buildOverpassQuery(south, west, north, east),
      `water:${south.toFixed(2)},${west.toFixed(2)},${north.toFixed(2)},${east.toFixed(2)}`
    );

    waterSources = [];
    waterRivers = [];
    (data.elements || []).forEach((el) => {
      const tags = el.tags || {};
      const name = tags.name || tags['name:es'] || 'Sin nombre';
      if (el.type === 'way' && Array.isArray(el.geometry) && tags.waterway === 'river') {
        waterRivers.push({ id: el.id, name, coords: el.geometry.map((g) => [g.lon, g.lat]) });
      } else if (el.center) {
        waterSources.push({ id: el.id, name, type: tags.water || 'agua', lat: el.center.lat, lon: el.center.lon });
      }
    });

    if (!waterSources.length && !waterRivers.length) {
      throw new Error('No se encontraron fuentes de agua relevantes en esta zona.');
    }

    map.addSource('water-rivers', {
      type: 'geojson',
      data: {
        type: 'FeatureCollection',
        features: waterRivers
          .filter((r) => r.coords.length > 1)
          .map((r) => ({
            type: 'Feature',
            properties: { id: r.id, name: r.name },
            geometry: { type: 'LineString', coordinates: r.coords },
          })),
      },
    });
    map.addLayer({
      id: 'water-rivers-line', type: 'line', source: 'water-rivers',
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': '#3b82f6', 'line-width': ['interpolate', ['linear'], ['zoom'], 9, 1.2, 14, 3], 'line-opacity': 0.85 },
    });

    map.addSource('water-sources', {
      type: 'geojson',
      data: {
        type: 'FeatureCollection',
        features: waterSources.map((w) => ({
          type: 'Feature',
          properties: { id: w.id, name: w.name, type: w.type },
          geometry: { type: 'Point', coordinates: [w.lon, w.lat] },
        })),
      },
    });
    map.addLayer({
      id: 'water-sources-glow', type: 'circle', source: 'water-sources',
      paint: { 'circle-radius': 12, 'circle-color': '#3b82f6', 'circle-opacity': 0.2 },
    });
    map.addLayer({
      id: 'water-sources-point', type: 'circle', source: 'water-sources',
      paint: {
        'circle-radius': 5, 'circle-color': '#3b82f6',
        'circle-stroke-color': '#ffffff', 'circle-stroke-width': 1.5,
      },
    });

    map.on('click', 'water-sources-point', (e) => {
      const p = e.features[0].properties;
      new maplibregl.Popup({ offset: 10 })
        .setLngLat(e.lngLat)
        .setHTML(`<b>${p.name}</b><br>Tipo: ${p.type}`)
        .addTo(map);
    });
    map.on('click', 'water-rivers-line', (e) => {
      const p = e.features[0].properties;
      new maplibregl.Popup({ offset: 6 })
        .setLngLat(e.lngLat)
        .setHTML(`<b>${p.name}</b><br>Tipo: río`)
        .addTo(map);
    });
    ['water-sources-point', 'water-rivers-line'].forEach((id) => {
      map.on('mouseenter', id, () => map.getCanvas().style.cursor = 'pointer');
      map.on('mouseleave', id, () => map.getCanvas().style.cursor = '');
    });

    const note = data._fromCache === 'stale' ? ' (dato guardado, servidor caído)' : data._fromCache ? ' (caché)' : '';
    reportSource('water', data._fromCache === 'stale' ? 'warn' : 'ok',
      `${waterSources.length} cuerpos + ${waterRivers.length} tramos de río${note}`);
  } catch (err) {
    console.warn('[WaterLayer] No se pudo cargar fuentes de agua (Overpass):', err);
    reportSource('water', 'error', err.message);
    const el = document.getElementById('status-text');
    if (el) {
      el.textContent = '⚠️ No se pudo cargar fuentes de agua: ' + err.message;
      el.style.color = '#e5484d';
    }
  }
}

function setWaterSourcesVisible(map, visible) {
  ['water-sources-point', 'water-sources-glow', 'water-rivers-line'].forEach((id) => {
    if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', visible ? 'visible' : 'none');
  });
}

/**
 * Fuente de agua real más cercana a un punto — para conectar con la simulación más adelante.
 * Considera lagos/embalses (su centro) y ríos (el vértice más cercano del trazado).
 */
function findNearestWaterSource(lat, lon) {
  if (!waterSources.length && !waterRivers.length) return null;
  const latKmPerDeg = 111.32;
  const lonKmPerDeg = 111.32 * Math.cos(lat * Math.PI / 180);
  const dist = (pLat, pLon) => Math.hypot((pLat - lat) * latKmPerDeg, (pLon - lon) * lonKmPerDeg);
  let nearest = null, nearestKm = Infinity;

  waterSources.forEach((w) => {
    const d = dist(w.lat, w.lon);
    if (d < nearestKm) { nearestKm = d; nearest = { id: w.id, name: w.name, type: w.type, lat: w.lat, lon: w.lon }; }
  });
  waterRivers.forEach((r) => {
    r.coords.forEach(([vLon, vLat]) => {
      const d = dist(vLat, vLon);
      if (d < nearestKm) { nearestKm = d; nearest = { id: r.id, name: r.name, type: 'río', lat: vLat, lon: vLon }; }
    });
  });
  return { ...nearest, distKm: nearestKm };
}
