// ============================================================
// FireWatch · Mapa 3D — estaciones meteorológicas REALES de la DMC
// (Dirección Meteorológica de Chile, Red EMA — estaciones automáticas)
//
// Reemplaza a la fuente MinCiencia/CEAZA (servidor caído, esquema nunca
// verificado). Fuente oficial: https://climatologia.meteochile.gob.cl
//
// Verificado en vivo (28-09-2026):
//   - getEstacionesRedEma  → catálogo, SIN credenciales, CORS abierto.
//     148 estaciones en Chile, 11 dentro del área de Santiago.
//   - getDatosRecientesRedEma → mediciones, REQUIERE usuario+token
//     (gratis, registro en el portal). Sin token responde "servicio
//     bloqueado para el usuario que consulta".
//
// Por eso:
//   - Con proxy + token: se muestran las mediciones reales (temperatura,
//     humedad, viento) y se comparan contra el modelo Open-Meteo.
//   - Sin token: se muestran igual las estaciones (en gris, "sin datos").
//
// El token nunca llega al navegador: lo usa server.py (.env).
// Unidades: la DMC entrega el viento en NUDOS; el proxy lo convierte a km/h.
// ============================================================

const DMC_CATALOG_DIRECT = 'https://climatologia.meteochile.gob.cl/application/servicios/getEstacionesRedEma';
const DMC_MAX_AGE_MIN = 90; // más viejo que esto se marca como dato atrasado

let chileStations = []; // { id, name, lat, lon, elevation, hasData, time, temp, rh, windDir, windSpeedKmh, ageMin }
let chileValidation = null; // { n, tempMAE, tempBias, windMAE } — modelo vs. medición

async function fetchDmcCatalog() {
  if (FW.proxy) {
    const res = await fetch('/api/dmc/estaciones');
    if (!res.ok) throw new Error(`catálogo DMC: HTTP ${res.status}`);
    return res.json();
  }
  const res = await fetch(DMC_CATALOG_DIRECT);
  if (!res.ok) throw new Error(`catálogo DMC: HTTP ${res.status}`);
  const raw = await res.json();
  return (raw.datosEstacion || []).map((r) => ({
    id: String(r.codigoNacional),
    name: fixMojibake(String(r.nombreEstacion || '').trim()),
    lat: parseFloat(r.latitud),
    lon: parseFloat(r.longitud),
    elevation: r.altura,
  })).filter((s) => Number.isFinite(s.lat) && Number.isFinite(s.lon));
}

async function fetchDmcMeasurements() {
  const res = await fetch('/api/dmc/recientes');
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try { msg = (await res.json()).error || msg; } catch (_) { /* no JSON */ }
    throw new Error(msg);
  }
  return res.json();
}

function stationColor(s) {
  if (!s.hasData) return '#7d8a9a';            // sin mediciones
  if (s.ageMin > DMC_MAX_AGE_MIN) return '#e8b93d'; // dato atrasado
  return '#2fae6d';                            // dato fresco
}

function stationsToGeoJSON() {
  return {
    type: 'FeatureCollection',
    features: chileStations.map((s) => ({
      type: 'Feature',
      properties: {
        id: s.id, name: s.name, elevation: s.elevation ?? null, hasData: s.hasData,
        temp: s.temp ?? null, rh: s.rh ?? null, windDir: s.windDir ?? null,
        windSpeedKmh: s.windSpeedKmh ?? null, time: s.time ?? null, ageMin: s.ageMin ?? null,
        color: stationColor(s),
      },
      geometry: { type: 'Point', coordinates: [s.lon, s.lat] },
    })),
  };
}

async function addChileWeatherLayer(map, area) {
  const bounds = boundsFromCenterKm(area.center, area.radiusKm);
  const [west, south] = bounds[0];
  const [east, north] = bounds[1];
  const inBounds = (s) => s.lat >= south && s.lat <= north && s.lon >= west && s.lon <= east;

  try {
    const catalog = (await fetchDmcCatalog()).filter(inBounds);
    if (!catalog.length) throw new Error('No hay estaciones DMC dentro del área de cobertura.');

    let measures = {};
    let measureNote = '';
    if (FW.proxy && FW.dmc) {
      try {
        (await fetchDmcMeasurements()).forEach((m) => { measures[m.id] = m; });
      } catch (err) {
        measureNote = `sin mediciones: ${err.message}`;
      }
    } else if (!FW.proxy) {
      measureNote = 'sin mediciones (requiere el proxy local con token DMC)';
    } else {
      measureNote = 'sin mediciones (falta DMC_TOKEN en .env)';
    }

    const now = Date.now();
    chileStations = catalog.map((c) => {
      const m = measures[c.id];
      const t = m && m.time ? Date.parse(String(m.time).replace(' ', 'T') + 'Z') : NaN;
      const ageMin = Number.isFinite(t) ? Math.round((now - t) / 60000) : null;
      return {
        ...c,
        hasData: !!m && Number.isFinite(m.temp),
        time: m ? m.time : null,
        temp: m ? m.temp : null,
        rh: m ? m.rh : null,
        windDir: m ? m.windDir : null,
        windSpeedKmh: m ? m.windSpeedKmh : null,
        ageMin,
      };
    });

    const geojson = stationsToGeoJSON();
    if (map.getSource('chile-weather')) {
      map.getSource('chile-weather').setData(geojson);
    } else {
      map.addSource('chile-weather', { type: 'geojson', data: geojson });
      map.addLayer({
        id: 'chile-weather-point', type: 'circle', source: 'chile-weather',
        paint: {
          'circle-radius': 8, 'circle-color': ['get', 'color'],
          'circle-stroke-color': '#ffffff', 'circle-stroke-width': 2,
        },
      });
      map.on('click', 'chile-weather-point', (e) => {
        const p = e.features[0].properties;
        const has = p.hasData === true || p.hasData === 'true';
        const num = (v, unit, d = 1) => (v === null || v === 'null' || v === undefined ? '—' : `${Number(v).toFixed(d)}${unit}`);
        new maplibregl.Popup({ offset: 10 })
          .setLngLat(e.lngLat)
          .setHTML(
            `<b>🇨🇱 ${p.name}</b> <span style="opacity:.7">(DMC · código ${p.id})</span>` +
            (has
              ? `<br>Temperatura: ${num(p.temp, ' °C')}` +
                `<br>Humedad: ${num(p.rh, ' %', 0)}` +
                `<br>Viento: ${num(p.windSpeedKmh, ' km/h')} desde ${num(p.windDir, '°', 0)}` +
                `<br><span style="opacity:.7">Medido ${p.time} UTC (hace ${p.ageMin} min)</span>`
              : `<br><span style="opacity:.7">Sin mediciones cargadas (ver estado de fuentes).</span>`)
          )
          .addTo(map);
      });
      map.on('mouseenter', 'chile-weather-point', () => map.getCanvas().style.cursor = 'pointer');
      map.on('mouseleave', 'chile-weather-point', () => map.getCanvas().style.cursor = '');
    }

    const withData = chileStations.filter((s) => s.hasData).length;
    const stale = chileStations.filter((s) => s.hasData && s.ageMin > DMC_MAX_AGE_MIN).length;
    if (withData) {
      reportSource('dmc', stale ? 'warn' : 'ok',
        `${withData}/${chileStations.length} estaciones con datos` + (stale ? ` · ${stale} atrasadas` : ''));
    } else {
      reportSource('dmc', 'warn', `${chileStations.length} estaciones · ${measureNote}`);
    }
    console.info(`[DMC] ${chileStations.length} estaciones en el área, ${withData} con mediciones.`);
  } catch (err) {
    console.warn('[DMC] No se pudo cargar la red de estaciones:', err);
    reportSource('dmc', 'error', err.message);
    const toggle = document.getElementById('toggle-chile');
    if (toggle && !map.getSource('chile-weather')) toggle.disabled = true;
  }
}

function setChileWeatherVisible(map, visible) {
  if (map.getLayer('chile-weather-point')) {
    map.setLayoutProperty('chile-weather-point', 'visibility', visible ? 'visible' : 'none');
  }
}

/**
 * Compara el modelo (Open-Meteo, interpolado en la ubicación de cada estación)
 * contra la medición real de la DMC. Sirve para decir en la defensa qué tan
 * confiable es el viento/temperatura que se muestra, con números y no a ojo.
 * Devuelve null si no hay estaciones con datos frescos dentro de la grilla.
 */
function computeModelValidation(grid) {
  const pairs = chileStations
    .filter((s) => s.hasData && s.ageMin <= DMC_MAX_AGE_MIN)
    .map((s) => ({ s, m: grid.sample(s.lat, s.lon) }))
    .filter((x) => x.m);
  if (!pairs.length) { chileValidation = null; return null; }

  const dT = pairs.map(({ s, m }) => m.temp - s.temp);
  const dW = pairs.filter(({ s }) => Number.isFinite(s.windSpeedKmh)).map(({ s, m }) => m.speed - s.windSpeedKmh);
  const mean = (a) => a.reduce((x, y) => x + y, 0) / a.length;
  chileValidation = {
    n: pairs.length,
    tempBias: mean(dT),
    tempMAE: mean(dT.map(Math.abs)),
    windMAE: dW.length ? mean(dW.map(Math.abs)) : null,
  };
  return chileValidation;
}
