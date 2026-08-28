// ============================================================
// FireWatch · Mapa 3D — configuración central
//
// v3 — el área de cobertura ya no depende solo de las cámaras: hay un
// "área de la ciudad" fija (CONFIG.city) que garantiza que TODO el Gran
// Santiago quede cubierto desde el principio, sin importar cuántas
// cámaras haya configuradas. Si en el futuro agregan una cámara fuera de
// ese radio (otra región, por ejemplo), el área crece sola para
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
  // Centro aproximado (cerca de Plaza de Armas) + radio que alcanza a
  // cubrir de Puente Alto/Pirque (sur) a Quilicura/Colina (norte) y de
  // Pudahuel (oeste) a las estribaciones cordilleranas (este).
  city: {
    center: { lat: -33.45, lon: -70.65 },
    radiusKm: 28,
  },

  pitch: 55,
  bearing: -20,

  mapStyle: 'https://tiles.openfreemap.org/styles/liberty',
  terrainTileJSON: 'https://tiles.mapterhorn.com/tilejson.json',
  terrainExaggeration: 1.4,

  // Puntos por lado de la grilla de viento/temperatura. Subido de 6 a 8
  // (8x8=64 puntos) porque ahora el área a cubrir es más grande (todo
  // Santiago) — con 6x6 la grilla habría quedado demasiado espaciada.
  gridSize: 8,

  // Margen extra (km) que se le suma a la distancia de una cámara lejana
  // antes de decidir si el área de la ciudad ya no alcanza a cubrirla.
  areaMarginKm: 10,

  autoRefreshMs: 10 * 60 * 1000, // 10 minutos
  windVisualSpeedFactor: 1.0,
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