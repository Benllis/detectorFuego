// ============================================================
// FireWatch · Mapa 3D — capa de calor (temperatura real, continua)
//
// v3 — escala de color RELATIVA (se adapta al mínimo/máximo real del
// momento, así siempre hay contraste visible aunque el día sea frío),
// leyenda con grados reales, sin recorte circular (llena todo el
// cuadrado del área), y reutilizable: acepta idSuffix/opacity/
// showLegend para poder crear varias instancias (la de toda la ciudad
// y las zonas locales de 2.5 km alrededor de focos NASA FIRMS).
//
// Se renderiza la superficie interpolada UNA sola vez (o cada vez que
// llegan datos nuevos) en un canvas chico anclado a las 4 esquinas
// geográficas de la grilla y se registra como fuente 'image' en
// MapLibre — sin recalcular nada mientras el usuario navega.
// ============================================================

const HEAT_COLOR_STOPS = [
  [59, 130, 246],   // frío relativo — azul
  [47, 174, 109],   // templado — verde
  [232, 185, 61],   // cálido — amarillo
  [229, 72, 77],    // caluroso relativo — rojo
];

function relativeTempToColor(t) { // t = 0..1, posición dentro del rango real del momento
  const n = HEAT_COLOR_STOPS.length - 1;
  const scaled = Math.min(1, Math.max(0, t)) * n;
  const i = Math.min(n - 1, Math.floor(scaled));
  const f = scaled - i;
  const a = HEAT_COLOR_STOPS[i], b = HEAT_COLOR_STOPS[i + 1];
  return [
    Math.round(a[0] + (b[0] - a[0]) * f),
    Math.round(a[1] + (b[1] - a[1]) * f),
    Math.round(a[2] + (b[2] - a[2]) * f),
  ];
}

const FIRE_COLOR_STOPS = [
  [255, 214, 90],   // tibio — amarillo
  [255, 122, 40],   // caliente — naranja
  [214, 40, 40],    // muy caliente — rojo
];

function fireTempToColor(t) {
  const scaled = Math.min(1, Math.max(0, t)) * (FIRE_COLOR_STOPS.length - 1);
  const i = Math.min(FIRE_COLOR_STOPS.length - 2, Math.floor(scaled));
  const f = scaled - i, a = FIRE_COLOR_STOPS[i], b = FIRE_COLOR_STOPS[i + 1];
  return [0, 1, 2].map((k) => Math.round(a[k] + (b[k] - a[k]) * f));
}

// ---------- Temperatura ajustada por altura ----------
// La temperatura baja ~6.5 °C por cada 1000 m (gradiente estándar). Con el
// mapa en temperatura real, la cordillera sale muy fría y ese contraste de
// altura tapa el patrón meteorológico real (brisa, calor urbano). El modo
// "ajustada" lleva cada punto a la altura de referencia (Santiago, 600 m):
//   T_ajustada = T + 0.0065 × (altura_del_punto − 600)
// La altura de cada punto viene de Open-Meteo (campo `elevation`). El índice
// de riesgo NO usa este ajuste: para el fuego importa la temperatura real.
const REF_ELEVATION_M = 600;
const LAPSE_RATE_C_PER_M = 0.0065;
function adjustTempToReference(tempC, elevM) {
  return (elevM === null || elevM === undefined) ? tempC : tempC + LAPSE_RATE_C_PER_M * (elevM - REF_ELEVATION_M);
}

// ---------- Índice de riesgo de incendio (0-100) ----------
// Índice SIMPLE del proyecto (no es un índice oficial como FFDI o FWI):
// combina tres factores meteorológicos con pesos fijos y umbrales fijos, de
// modo que el color significa siempre lo mismo (escala absoluta).
//   humedad relativa baja  → 50 %  (0 con RH ≥ 60 %, máximo con RH ≤ 10 %)
//   temperatura alta       → 30 %  (0 con T ≤ 5 °C,   máximo con T ≥ 35 °C)
//   viento fuerte          → 20 %  (0 con calma,      máximo con ≥ 40 km/h)
// No incluye lluvia previa ni sequedad del combustible (los índices
// oficiales sí): es una señal de condiciones, no un pronóstico de incendio.
const RISK_WEIGHTS = { humidity: 0.5, temp: 0.3, wind: 0.2 };
const RISK_CLASSES = [
  { max: 20,  label: 'Bajo',     color: [47, 174, 109] },
  { max: 40,  label: 'Moderado', color: [232, 185, 61] },
  { max: 60,  label: 'Alto',     color: [255, 122, 40] },
  { max: 80,  label: 'Muy alto', color: [229, 72, 77] },
  { max: 100, label: 'Extremo',  color: [142, 27, 107] },
];
const RISK_STOPS = [[0, [47, 174, 109]], [25, [232, 185, 61]], [50, [255, 122, 40]], [75, [229, 72, 77]], [100, [142, 27, 107]]];

function fireRiskIndex(tempC, rhPct, windKmh) {
  if (tempC === null || tempC === undefined || rhPct === null || rhPct === undefined || windKmh === null || windKmh === undefined) return null;
  const c = (x) => Math.min(1, Math.max(0, x));
  const h = c((60 - rhPct) / 50);
  const t = c((tempC - 5) / 30);
  const w = c(windKmh / 40);
  return 100 * (RISK_WEIGHTS.humidity * h + RISK_WEIGHTS.temp * t + RISK_WEIGHTS.wind * w);
}

// Riesgo combinado: el índice meteorológico modulado por la propensión de
// combustible de la celda (fuel-grid.js, 0-1). Donde no hay con qué arder
// (agua, nieve) queda en el 25 % del índice; sobre matorral, el 100 %.
// Es una regla del proyecto, no un modelo validado.
function combinedFireRisk(meteoIndex, fuel) {
  return meteoIndex * (0.25 + 0.75 * fuel);
}

function fireRiskClass(idx) {
  return RISK_CLASSES.find((c) => idx <= c.max) || RISK_CLASSES[RISK_CLASSES.length - 1];
}

function riskIndexToColor(idx) {
  const v = Math.min(100, Math.max(0, idx));
  for (let i = 0; i < RISK_STOPS.length - 1; i++) {
    const [a, ca] = RISK_STOPS[i], [b, cb] = RISK_STOPS[i + 1];
    if (v <= b) {
      const f = (v - a) / (b - a);
      return [0, 1, 2].map((k) => Math.round(ca[k] + (cb[k] - ca[k]) * f));
    }
  }
  return RISK_STOPS[RISK_STOPS.length - 1][1];
}

function idx4(py, px, size) { return (py * size + px) * 4; }

class TemperatureHeatLayer {
  constructor(map, weatherGrid, { resolution = 160, idSuffix = '', opacity = 140, showLegend = true, circular = false, beforeId = null, ramp = 'relative', feather = 0.12, mode = 'temp' } = {}) {
    this.map = map;
    this.grid = weatherGrid;
    this.resolution = resolution;
    this.sourceId = 'temp-heat-image' + idSuffix;
    this.layerId = 'temp-heat-layer' + idSuffix;
    this.opacity = opacity;       // 0-255, transparencia del relleno
    this.showLegend = showLegend; // las zonas locales no pisan la leyenda principal
    this.circular = circular;     // true: borde difuso circular en vez de cuadrado (zonas locales)
    this.fuelGrid = null;         // FuelGrid (fuel-grid.js) para el modo 'combined'
    this.mode = mode;             // 'temp' (escala relativa) | 'risk' (índice de riesgo, escala absoluta)
    this.ramp = ramp;             // 'relative' (azul→rojo) | 'fire' (transparente→naranja→rojo, para zonas de foco)
    this.feather = feather;       // fracción del borde que se desvanece en la capa cuadrada
    this.beforeId = beforeId;     // capa bajo la cual insertar (para no tapar puntos y popups)
    this._added = false;
    this.scale = { min: 0, max: 30 };

    this.canvas = document.createElement('canvas');
    this.canvas.width = resolution;
    this.canvas.height = resolution;
    this.ctx = this.canvas.getContext('2d');
  }

  _bboxCoordinates() {
    const first = this.grid.points[0];
    const last = this.grid.points[this.grid.points.length - 1];
    return [
      [first.lon, last.lat],
      [last.lon, last.lat],
      [last.lon, first.lat],
      [first.lon, first.lat],
    ];
  }

  /** Mínimo/máximo real entre los puntos de la grilla — define la escala del momento. */
  _computeScale() {
    const temps = this.grid.points.filter((p) => p.temp !== null)
      .map((p) => (this.mode === 'tempadj' ? adjustTempToReference(p.temp, p.elev) : p.temp));
    if (!temps.length) return { min: 0, max: 30 };
    let min = Math.min(...temps), max = Math.max(...temps);
    if (max - min < 2) { min -= 1; max += 1; } // evita una escala de 0° de rango
    return { min, max };
  }

  /** Latitud de la fila `py` de la imagen (lineal). La capa nacional la sobrescribe con la proyección Mercator. */
  _rowLat(py, size, first, last) { return last.lat - (py / (size - 1)) * (last.lat - first.lat); }

  /** Factor 0-1 extra de transparencia por punto (la capa nacional enmascara lo que no es Chile). */
  _extraAlpha(lat, lon) { return 1; }

  _renderCanvas() {
    const size = this.resolution;
    const ctx = this.ctx;
    const imgData = ctx.createImageData(size, size);
    const first = this.grid.points[0];
    const last = this.grid.points[this.grid.points.length - 1];
    this.scale = this._computeScale();

    for (let py = 0; py < size; py++) {
      const lat = this._rowLat(py, size, first, last);
      for (let px = 0; px < size; px++) {
        const idx = (py * size + px) * 4;
        const lon = first.lon + (px / (size - 1)) * (last.lon - first.lon);
        const w = this.grid.sample(lat, lon);
        if (!w) { imgData.data[idx + 3] = 0; continue; }
        const tv = this.mode === 'tempadj' ? adjustTempToReference(w.temp, w.elev) : w.temp;
        const t = (tv - this.scale.min) / (this.scale.max - this.scale.min);
        let r0, g0, b0, alpha;
        if ((this.mode === 'risk' || this.mode === 'combined') && this.ramp !== 'fire') {
          let ri = fireRiskIndex(w.temp, w.rh, w.speed);
          if (ri !== null && this.mode === 'combined') {
            const f = this.fuelGrid ? this.fuelGrid.fuelAt(lat, lon) : null;
            ri = f === null ? null : combinedFireRisk(ri, f);
          }
          if (ri === null) { imgData.data[idx4(py, px, size) + 3] = 0; continue; }
          [r0, g0, b0] = riskIndexToColor(ri);
          alpha = this.opacity;
        } else if (this.ramp === 'fire') {
          // Solo lo más caliente de la zona se pinta; lo frío queda transparente.
          const tt = Math.min(1, Math.max(0, t));
          [r0, g0, b0] = fireTempToColor(tt);
          alpha = Math.round(this.opacity * (0.25 + 0.75 * tt));
        } else {
          [r0, g0, b0] = relativeTempToColor(t);
          alpha = this.opacity;
        }
        imgData.data[idx] = r0;
        imgData.data[idx + 1] = g0;
        imgData.data[idx + 2] = b0;
        const smooth = (a, b, x) => { const k = Math.min(1, Math.max(0, (x - a) / (b - a))); return k * k * (3 - 2 * k); };
        if (this.circular) {
          // 1 en el centro, baja suavemente desde el 55% del radio hasta 0 en el borde.
          const rad = Math.hypot((2 * px) / (size - 1) - 1, (2 * py) / (size - 1) - 1);
          alpha = Math.round(alpha * (1 - smooth(0.55, 1, rad)));
        } else if (this.feather > 0) {
          // Borde cuadrado desvanecido: sin esquinas duras.
          const ex = Math.min(px, size - 1 - px) / (size - 1);
          const ey = Math.min(py, size - 1 - py) / (size - 1);
          alpha = Math.round(alpha * smooth(0, this.feather, Math.min(ex, ey)));
        }
        imgData.data[idx + 3] = Math.round(alpha * this._extraAlpha(lat, lon));
      }
    }
    ctx.putImageData(imgData, 0, 0);
  }

  render() {
    if (!this.grid.loaded) return;
    this._renderCanvas();
    const dataUrl = this.canvas.toDataURL();

    if (!this._added) {
      this.map.addSource(this.sourceId, {
        type: 'image',
        url: dataUrl,
        coordinates: this._bboxCoordinates(),
      });
      const before = this.beforeId && this.map.getLayer(this.beforeId) ? this.beforeId : undefined;
      this.map.addLayer({
        id: this.layerId, type: 'raster', source: this.sourceId,
        paint: { 'raster-fade-duration': 0 },
      }, before);
      this._added = true;
    } else {
      this.map.getSource(this.sourceId).updateImage({ url: dataUrl });
    }
    this._updateLegend();
  }

  _updateLegend() {
    if (!this.showLegend) return;
    const el = document.getElementById('heat-legend');
    if (!el) return;
    if (this.mode === 'risk' || this.mode === 'combined') {
      const cap = this.mode === 'combined'
        ? (this.fuelGrid ? 'Riesgo meteorológico × propensión de combustible (vegetación ESA WorldCover).' : 'Esperando la cobertura vegetal…')
        : 'Índice 0-100: humedad baja, temperatura alta y viento fuerte.';
      el.innerHTML = `
        <div class="heat-legend-bar" style="background:linear-gradient(to right,${RISK_CLASSES.map((c, i) => `rgb(${c.color}) ${i * 20}% ${(i + 1) * 20}%`).join(',')});"></div>
        <div class="heat-legend-labels" style="font-size:.62rem;">${RISK_CLASSES.map((c) => `<span>${c.label}</span>`).join('')}</div>
        <div class="heat-legend-labels" style="font-size:.6rem;opacity:.7;display:block;margin-top:2px;">${cap}</div>`;
      return;
    }
    el.innerHTML = `
      <div class="heat-legend-bar"></div>
      <div class="heat-legend-labels">
        <span>${this.scale.min.toFixed(0)}°C</span>
        <span>${((this.scale.min + this.scale.max) / 2).toFixed(0)}°C</span>
        <span>${this.scale.max.toFixed(0)}°C</span>
      </div>
      ${this.mode === 'tempadj' ? `<div class="heat-legend-labels" style="font-size:.6rem;opacity:.7;display:block;margin-top:2px;">Ajustada a ${REF_ELEVATION_M} m (−6.5 °C por cada 1000 m) para quitar el efecto de la altura.</div>` : ''}
    `;
  }

  /** Entrega la grilla de combustible (modo 'combined'); redibuja si hace falta. */
  setFuelGrid(grid) {
    this.fuelGrid = grid;
    if (this.mode === 'combined') this.render();
  }

  /** Cambia el modo ('risk' | 'combined' | 'temp' | 'tempadj') y vuelve a dibujar. */
  setMode(mode) {
    if (mode === this.mode) return;
    this.mode = mode;
    this.render();
  }

  setVisible(visible) {
    if (this.map.getLayer(this.layerId)) {
      this.map.setLayoutProperty(this.layerId, 'visibility', visible ? 'visible' : 'none');
    }
    if (this.showLegend) {
      const legend = document.getElementById('heat-legend-wrap');
      if (legend) legend.style.display = visible ? 'block' : 'none';
    }
  }

  destroy() {
    if (this.map.getLayer(this.layerId)) this.map.removeLayer(this.layerId);
    if (this.map.getSource(this.sourceId)) this.map.removeSource(this.sourceId);
  }
}
