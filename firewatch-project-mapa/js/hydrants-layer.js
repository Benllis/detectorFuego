// ============================================================
// FireWatch · Mapa 3D — capa de grifos (hidrantes) reales
//
// Overpass API (OpenStreetMap): nodos emergency=fire_hydrant dentro del
// área de cobertura. Apoyo terrestre (camiones de bomberos), a
// diferencia de water-layer.js que es para apoyo aéreo.
//
// Aviso honesto: la cobertura de grifos en OSM es CROWDSOURCED y muy
// desigual entre comunas — que no aparezca uno NO significa que no
// exista en la realidad, solo que nadie lo cargó a OSM todavía.
// ============================================================

let hydrants = []; // { id, lat, lon, tipo, color, diametro }

// Tope de resultados. Antes era 3000 y cortaba en silencio si había más;
// ahora es alto y, si igual se alcanza, se avisa en el estado de la fuente.
const HYDRANTS_LIMIT = 20000;

function buildHydrantsQuery(south, west, north, east) {
  const bbox = `${south},${west},${north},${east}`;
  return `
    [out:json][timeout:25];
    node["emergency"="fire_hydrant"](${bbox});
    out ${HYDRANTS_LIMIT};
  `;
}

async function addHydrantsLayer(map, area) {
  if (map.getSource('hydrants')) return;

  const bounds = boundsFromCenterKm(area.center, area.radiusKm);
  const [west, south] = bounds[0];
  const [east, north] = bounds[1];

  try {
    const data = await fetchOverpass(
      buildHydrantsQuery(south, west, north, east),
      `hydrants:${south.toFixed(2)},${west.toFixed(2)},${north.toFixed(2)},${east.toFixed(2)}`
    );

    hydrants = (data.elements || [])
      .filter((el) => el.type === 'node')
      .map((el) => ({
        id: el.id,
        lat: el.lat,
        lon: el.lon,
        tipo: (el.tags && el.tags['fire_hydrant:type']) || 'desconocido',
        color: (el.tags && el.tags['fire_hydrant:color']) || null,
        diametro: (el.tags && el.tags['fire_hydrant:diameter']) || null,
      }));

    if (!hydrants.length) {
      throw new Error('No hay grifos mapeados en OSM para esta zona todavía — cobertura crowdsourced desigual, no significa que no existan en la realidad.');
    }

    const geojson = {
      type: 'FeatureCollection',
      features: hydrants.map((h) => ({
        type: 'Feature',
        properties: { id: h.id, tipo: h.tipo, diametro: h.diametro },
        geometry: { type: 'Point', coordinates: [h.lon, h.lat] },
      })),
    };

    map.addSource('hydrants', { type: 'geojson', data: geojson });
    map.addLayer({
      id: 'hydrants-point', type: 'circle', source: 'hydrants',
      // Más de 2.500 grifos: a zoom de ciudad son una mancha, solo se dibujan de cerca.
      minzoom: 12,
      paint: {
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 12, 2.5, 16, 6],
        // Morado: el rojo se confundía con los focos FIRMS.
        'circle-color': '#a855f7',
        'circle-stroke-color': '#ffffff',
        'circle-stroke-width': 1,
      },
    });

    map.on('click', 'hydrants-point', (e) => {
      const p = e.features[0].properties;
      new maplibregl.Popup({ offset: 8 })
        .setLngLat(e.lngLat)
        .setHTML(`<b>🚰 Grifo</b><br>Tipo: ${p.tipo}${p.diametro ? '<br>Diámetro: ' + p.diametro + 'mm' : ''}`)
        .addTo(map);
    });
    map.on('mouseenter', 'hydrants-point', () => map.getCanvas().style.cursor = 'pointer');
    map.on('mouseleave', 'hydrants-point', () => map.getCanvas().style.cursor = '');

    const stale = data._fromCache === 'stale';
    const capped = hydrants.length >= HYDRANTS_LIMIT;
    reportSource('hydrants', stale || capped ? 'warn' : 'ok',
      `${hydrants.length} grifos` + (capped ? ' (tope alcanzado: puede haber más)' : '') +
      (stale ? ' (dato guardado, servidor caído)' : data._fromCache ? ' (caché)' : '') +
      ' · cobertura OSM desigual');
  } catch (err) {
    console.warn('[HydrantsLayer] No se pudo cargar grifos (Overpass):', err);
    reportSource('hydrants', 'error', err.message);
    const el = document.getElementById('status-text');
    if (el) {
      el.textContent = '⚠️ Grifos: ' + err.message;
      el.style.color = '#e5484d';
    }
  }
}

function setHydrantsVisible(map, visible) {
  if (map.getLayer('hydrants-point')) {
    map.setLayoutProperty('hydrants-point', 'visibility', visible ? 'visible' : 'none');
  }
}
