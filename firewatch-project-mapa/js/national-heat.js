// ============================================================
// FireWatch · Mapa 3D — mapa de calor / riesgo a escala de todo Chile
//
// Misma capa que la de Santiago (TemperatureHeatLayer) pero alimentada con la
// grilla nacional gruesa (1,25°). Dos diferencias:
//  - las filas de la imagen se reparten en Mercator (la imagen del mapa se
//    estira en Mercator; con filas lineales en latitud saldría corrida);
//  - una máscara aproximada (chileMaskAlpha) la deja solo sobre el país.
// Va DEBAJO de la capa fina de Santiago; no usa vegetación (el modo "riesgo ×
// combustible" se muestra aquí como riesgo meteorológico) ni la leyenda.
// Cargar después de heat-layer.js y national.js.
// ============================================================

const mercY = (latDeg) => Math.log(Math.tan(Math.PI / 4 + (latDeg * Math.PI) / 360));
const mercLat = (y) => (2 * Math.atan(Math.exp(y)) - Math.PI / 2) * 180 / Math.PI;

/** Modo de la capa nacional según el selector del panel (sin elevación ni vegetación a esta escala). */
function nationalHeatMode(mode) { return mode === 'combined' ? 'risk' : mode === 'tempadj' ? 'temp' : mode; }

class NationalHeatLayer extends TemperatureHeatLayer {
  constructor(map, grid, opts = {}) {
    super(map, grid, {
      resolution: 512, opacity: 110, showLegend: false, feather: 0, idSuffix: '-nacional',
      beforeId: map.getLayer('temp-heat-layer') ? 'temp-heat-layer' : 'fire-pixels-glow',
      ...opts, mode: nationalHeatMode(opts.mode || 'risk'),
    });
  }
  _rowLat(py, size, first, last) {
    const yTop = mercY(last.lat), yBot = mercY(first.lat);
    return mercLat(yTop + (py / (size - 1)) * (yBot - yTop));
  }
  _extraAlpha(lat, lon) { return chileMaskAlpha(lat, lon); }
  setMode(mode) { super.setMode(nationalHeatMode(mode)); }
}

if (typeof module !== 'undefined') module.exports = { mercY, mercLat, nationalHeatMode };
