// ============================================================
// FireWatch · Mapa 3D — inicialización del mapa base
// Mapa real (calles/edificios de OpenStreetMap vía OpenFreeMap)
// + terreno 3D real (elevación vía Mapterhorn) + marcadores de
// TODAS las cámaras configuradas (no solo una).
// ============================================================

const AREA = computeAreaOfInterest(CONFIG.cameras, CONFIG.areaMarginKm);

function initMap() {
  const map = new maplibregl.Map({
    container: 'map',
    style: CONFIG.mapStyle,
    center: [AREA.center.lon, AREA.center.lat],
    zoom: 11,
    pitch: CONFIG.pitch,
    bearing: CONFIG.bearing,
    antialias: true,
  });

  map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'top-right');
  map.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-left');

  map.on('load', () => {
    // Terreno 3D real
    map.addSource('terrainSource', { type: 'raster-dem', url: CONFIG.terrainTileJSON });
    map.setTerrain({ source: 'terrainSource', exaggeration: CONFIG.terrainExaggeration });

    // Cielo/atmósfera — propiedad de estilo aparte, no una capa (addLayer no aplica acá)
    map.setSky({
      'sky-color': '#1b2838',
      'sky-horizon-blend': 0.5,
      'horizon-color': '#a9a394',
      'horizon-fog-blend': 0.5,
      'fog-color': '#e9e3d4',
      'fog-ground-blend': 0.6,
    });

    // Marcadores de TODAS las cámaras configuradas
    map.addSource('camera-sensors', { type: 'geojson', data: camerasGeoJSON() });
    map.addLayer({
      id: 'camera-sensor-glow', type: 'circle', source: 'camera-sensors',
      paint: { 'circle-radius': 16, 'circle-color': '#ff6a3d', 'circle-opacity': 0.18 },
    });
    map.addLayer({
      id: 'camera-sensor-point', type: 'circle', source: 'camera-sensors',
      paint: {
        'circle-radius': 7, 'circle-color': '#ff6a3d',
        'circle-stroke-color': '#ffffff', 'circle-stroke-width': 2,
      },
    });

    map.on('click', 'camera-sensor-point', (e) => {
      const p = e.features[0].properties;
      new maplibregl.Popup({ offset: 12 })
        .setLngLat(e.lngLat)
        .setHTML(`<b>${p.id}</b><br>Orientación: ${p.orientacion}<br>Azimut: ${p.azimut}°`)
        .addTo(map);
    });
    map.on('mouseenter', 'camera-sensor-point', () => map.getCanvas().style.cursor = 'pointer');
    map.on('mouseleave', 'camera-sensor-point', () => map.getCanvas().style.cursor = '');

    // Borde que marca el área real que cubre la grilla de viento/temperatura
    // (calculada a partir de TODAS las cámaras — ver computeAreaOfInterest en config.js).
    map.addSource('coverage-boundary', {
      type: 'geojson',
      data: buildCircleGeoJSON(AREA.center, AREA.radiusKm),
    });
    map.addLayer({
      id: 'coverage-boundary-line', type: 'line', source: 'coverage-boundary',
      paint: { 'line-color': '#7d8a9a', 'line-width': 1.5, 'line-dasharray': [2, 2], 'line-opacity': 0.6 },
    });

    // Encuadrar el mapa en el área que cubren las cámaras + margen.
    // padding: le avisamos a MapLibre que los paneles de la interfaz tapan
    // parte de la pantalla, para que centre el área visible real.
    const bounds = boundsFromCenterKm(AREA.center, AREA.radiusKm * 1.1);
    map.fitBounds(bounds, {
      pitch: CONFIG.pitch,
      bearing: CONFIG.bearing,
      padding: { top: 90, bottom: 60, left: 300, right: 280 },
      duration: 0,
    });
  });

  return map;
}

function camerasGeoJSON() {
  return {
    type: 'FeatureCollection',
    features: CONFIG.cameras.map((cam) => ({
      type: 'Feature',
      properties: { id: cam.id, orientacion: cam.orientacion, azimut: cam.azimutGrados },
      geometry: { type: 'Point', coordinates: [cam.lon, cam.lat] },
    })),
  };
}

/** Construye un polígono circular aproximado (GeoJSON) alrededor de un centro, radio en km. */
function buildCircleGeoJSON(center, radiusKm, steps = 64) {
  const latKmPerDeg = 111.32;
  const lonKmPerDeg = 111.32 * Math.cos(center.lat * Math.PI / 180);
  const coords = [];
  for (let i = 0; i <= steps; i++) {
    const angle = (i / steps) * 2 * Math.PI;
    const dLatKm = Math.sin(angle) * radiusKm;
    const dLonKm = Math.cos(angle) * radiusKm;
    coords.push([center.lon + dLonKm / lonKmPerDeg, center.lat + dLatKm / latKmPerDeg]);
  }
  return { type: 'Feature', geometry: { type: 'LineString', coordinates: coords }, properties: {} };
}

/** Bounding box [[west,south],[east,north]] alrededor de un centro, dado un radio en km. */
function boundsFromCenterKm(center, radiusKm) {
  const latKmPerDeg = 111.32;
  const lonKmPerDeg = 111.32 * Math.cos(center.lat * Math.PI / 180);
  const dLat = radiusKm / latKmPerDeg;
  const dLon = radiusKm / lonKmPerDeg;
  return [
    [center.lon - dLon, center.lat - dLat],
    [center.lon + dLon, center.lat + dLat],
  ];
}