// ============================================================
// FireWatch · Mapa 3D — configuración central
//
// v6 — foco fijo en Santiago (se probó cobertura de todo Chile vía
// tiles de OpenWeatherMap, pero se descartó: esos tiles solo muestran
// intensidad de viento por color, no dirección — se perdía justo la
// parte más útil de la capa de viento. Ver bitácora, Sesión 07.)
//
// SIN KEYS EN EL CÓDIGO: la MAP_KEY de NASA FIRMS y el token de la DMC
// viven en `.env` y solo los usa el proxy local (server.py). Ver
// .env.example.
//
// El área de cobertura no depende solo de las cámaras: hay un "área de
// la ciudad" fija (CONFIG.city) que garantiza que TODO el Gran Santiago
// quede cubierto, sin importar cuántas cámaras haya configuradas. Si
// agregan una cámara fuera de ese radio, el área crece sola para
// alcanzarla — ver computeAreaOfInterest más abajo.
// ============================================================

const CONFIG = {
  // Cámaras reales. Hoy solo existe CAM_SECTOR_01 (de camera_config.json,
  // La Florida). Para sumar otra real, agreguen otro objeto con el mismo
  // formato — no hay que tocar nada más, el mapa se actualiza solo.
  cameras: [
    {
      id: 'CAM_SECTOR_01',
      lat: -33.5227,
      lon: -70.5859,
      orientacion: 'Noreste (NE)',
      azimutGrados: 45,
    },
    // Ejemplo de cómo se vería una segunda cámara (comentado — no es real,
    // bórrenlo/reemplácenlo cuando tengan la ubicación real):
    // {
    //   id: 'CAM_SECTOR_02',
    //   lat: -33.4489,
    //   lon: -70.6693,
    //   orientacion: 'Norte (N)',
    //   azimutGrados: 0,
    // },
  ],

  // Área mínima garantizada de cobertura: todo el Gran Santiago.
  city: {
    center: { lat: -33.45, lon: -70.65 },
    radiusKm: 28,
  },

  pitch: 55,
  bearing: -20,

  mapStyle: 'https://tiles.openfreemap.org/styles/liberty',
  terrainTileJSON: 'https://tiles.mapterhorn.com/tilejson.json',
  terrainExaggeration: 1.4,

  // Puntos por lado de la grilla de viento/temperatura (8x8 = 64, en UNA petición).
  gridSize: 8,

  // Margen extra (km) que se le suma a la distancia de una cámara lejana
  // antes de decidir si el área de la ciudad ya no alcanza a cubrirla.
  areaMarginKm: 10,

  // Open-Meteo recalcula su modelo cada hora: pedir más seguido no aporta
  // datos nuevos y gasta cuota (límite gratuito: 10.000 llamadas/día por IP).
  // 64 puntos/hora = 1.536 llamadas/día.
  autoRefreshMs: 60 * 60 * 1000,

  // FIRMS NRT se actualiza con cada pasada de satélite (pocas por día).
  firmsRefreshMs: 30 * 60 * 1000,

  // Focos de calor en vivo: satélites consultados (cada uno pasa a distinta hora).
  firmsLiveSources: ['VIIRS_NOAA20_NRT', 'VIIRS_SNPP_NRT', 'VIIRS_NOAA21_NRT'],
  firmsLiveDays: 1, // 1..5

  // Replay: incendios reales históricos para demostrar el mapa cuando en vivo no hay focos.
  // FIRMS "SP" (standard processing) cubre fechas antiguas; "NRT" solo lo reciente.
  // (Valparaíso 03-02-2024 verificado: FIRMS devolvió detecciones.)
  firmsReplaySources: ['VIIRS_NOAA20_SP', 'VIIRS_SNPP_SP'],
  replayPresets: [
    {
      label: 'Viña del Mar / Valparaíso — 3 feb 2024',
      date: '2024-02-03',
      days: 2,
      center: { lat: -33.07, lon: -71.5 },
      bbox: [-71.75, -33.25, -71.25, -32.85], // west, south, east, north
      radiusKm: 25,                            // radio de la grilla de clima del escenario
    },
  ],
};

/**
 * Centro + radio (km) que garantiza cubrir TODO el Gran Santiago
 * (CONFIG.city), y además crece si alguna cámara configurada queda fuera
 * de ese radio (útil si en el futuro suman cámaras en otra zona/región).
 */
function computeAreaOfInterest(cameras, marginKm) {
  const center = { ...CONFIG.city.center };
  let radiusKm = CONFIG.city.radiusKm;

  if (cameras && cameras.length) {
    const latKmPerDeg = 111.32;
    const lonKmPerDeg = 111.32 * Math.cos(center.lat * Math.PI / 180);
    cameras.forEach((c) => {
      const dLatKm = (c.lat - center.lat) * latKmPerDeg;
      const dLonKm = (c.lon - center.lon) * lonKmPerDeg;
      const distKm = Math.hypot(dLatKm, dLonKm) + marginKm;
      radiusKm = Math.max(radiusKm, distKm);
    });
  }

  return { center, radiusKm };
}
