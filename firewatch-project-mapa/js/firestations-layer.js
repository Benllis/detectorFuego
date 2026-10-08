// ============================================================
// FireWatch · Mapa 3D — capa de estaciones de bomberos reales
//
// Overpass API (OpenStreetMap): amenity=fire_station dentro del área de
// cobertura. Calza con la idea original del proyecto (FireWatch.md):
// "se le enviará la imagen a bomberos de la comuna y más cercanos".
// Se piden nodes, ways y relations con "out center" (algunas estaciones
// están mapeadas como el contorno del edificio).
// ============================================================

let fireStations = []; // { id, name, operator, phone, lat, lon }

function buildFireStationsQuery(south, west, north, east) {
  const bbox = `${south},${west},${north},${east}`;
  return `
    [out:json][timeout:25];
    (
      node["amenity"="fire_station"](${bbox});
      way["amenity"="fire_station"](${bbox});
      relation["amenity"="fire_station"](${bbox});
    );
    out center tags;
  `;
}

async function addFireStationsLayer(map, area) {
  if (map.getSource('fire-stations')) return;

  const bounds = boundsFromCenterKm(area.center, area.radiusKm);
  const [west, south] = bounds[0];
  const [east, north] = bounds[1];

  try {
    const data = await fetchOverpass(
      buildFireStationsQuery(south, west, north, east),
      `firestations:${south.toFixed(2)},${west.toFixed(2)},${north.toFixed(2)},${east.toFixed(2)}`
    );

    fireStations = (data.elements || [])
      .map((el) => {
        const center = el.center || (el.type === 'node' ? { lat: el.lat, lon: el.lon } : null);
        if (!center) return null;
        const tags = el.tags || {};
        return {
          id: el.id,
          name: tags.name || tags['name:es'] || 'Cuartel sin nombre en OSM',
          operator: tags.operator || null,
          phone: tags.phone || tags['contact:phone'] || null,
          lat: center.lat,
          lon: center.lon,
        };
      })
      .filter(Boolean);

    if (!fireStations.length) throw new Error('No se encontraron estaciones de bomberos mapeadas en OSM para esta zona.');

    const geojson = {
      type: 'FeatureCollection',
      features: fireStations.map((s) => ({
        type: 'Feature',
        properties: { id: s.id, name: s.name, operator: s.operator, phone: s.phone },
        geometry: { type: 'Point', coordinates: [s.lon, s.lat] },
      })),
    };

    map.addSource('fire-stations', { type: 'geojson', data: geojson });
    map.addLayer({
      id: 'fire-stations-glow', type: 'circle', source: 'fire-stations',
      paint: { 'circle-radius': 14, 'circle-color': '#ff6a3d', 'circle-opacity': 0.22 },
    });
    map.addLayer({
      id: 'fire-stations-point', type: 'circle', source: 'fire-stations',
      paint: {
        'circle-radius': 6, 'circle-color': '#ff6a3d',
        'circle-stroke-color': '#ffffff', 'circle-stroke-width': 1.5,
      },
    });

    map.on('click', 'fire-stations-point', (e) => {
      const p = e.features[0].properties;
      new maplibregl.Popup({ offset: 10 })
        .setLngLat(e.lngLat)
        .setHTML(
          `<b>🚒 ${p.name}</b>` +
          (p.operator ? `<br>Operador: ${p.operator}` : '') +
          (p.phone ? `<br>Teléfono: ${p.phone}` : '')
        )
        .addTo(map);
    });
    map.on('mouseenter', 'fire-stations-point', () => map.getCanvas().style.cursor = 'pointer');
    map.on('mouseleave', 'fire-stations-point', () => map.getCanvas().style.cursor = '');

    const stale = data._fromCache === 'stale';
    reportSource('firestations', stale ? 'warn' : 'ok',
      `${fireStations.length} cuarteles` +
      (stale ? ' (dato guardado, servidor caído)' : data._fromCache ? ' (caché)' : ''));
  } catch (err) {
    console.warn('[FireStationsLayer] No se pudo cargar estaciones de bomberos (Overpass):', err);
    reportSource('firestations', 'error', err.message);
    const el = document.getElementById('status-text');
    if (el) {
      el.textContent = '⚠️ Estaciones de bomberos: ' + err.message;
      el.style.color = '#e5484d';
    }
  }
}

function setFireStationsVisible(map, visible) {
  ['fire-stations-point', 'fire-stations-glow'].forEach((id) => {
    if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', visible ? 'visible' : 'none');
  });
}

/** Estación de bomberos real más cercana a un punto — para conectar con la lógica de alertas. */
function findNearestFireStation(lat, lon) {
  if (!fireStations.length) return null;
  const latKmPerDeg = 111.32;
  const lonKmPerDeg = 111.32 * Math.cos(lat * Math.PI / 180);
  let nearest = null, nearestKm = Infinity;
  fireStations.forEach((s) => {
    const distKm = Math.hypot((s.lat - lat) * latKmPerDeg, (s.lon - lon) * lonKmPerDeg);
    if (distKm < nearestKm) { nearestKm = distKm; nearest = s; }
  });
  return { ...nearest, distKm: nearestKm };
}
