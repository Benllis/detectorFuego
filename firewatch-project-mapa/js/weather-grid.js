// ============================================================
// FireWatch · Mapa 3D — grilla de datos meteorológicos reales
//
// Construye una grilla NxN de puntos alrededor de un centro,
// consulta Open-Meteo (gratis, sin API key, 1 sola llamada HTTP
// para toda la grilla vía coordenadas separadas por coma) y
// permite interpolar viento/temperatura para cualquier lat/lon
// dentro de la grilla (usado por la capa de viento para animar
// partículas de forma continua, no solo en los puntos exactos).
//
// Nota técnica: el viento se interpola convirtiendo velocidad+dirección
// a componentes u/v (vectores) ANTES de interpolar, y se reconvierte
// a velocidad+dirección después. Interpolar el ángulo directamente
// es un error común (el promedio entre 350° y 10° NO es 180°).
// ============================================================

function clamp(x, min, max) { return Math.min(max, Math.max(min, x)); }
function bilerp(v00, v10, v01, v11, fr, fc) {
  const a = v00 + (v10 - v00) * fr;
  const b = v01 + (v11 - v01) * fr;
  return a + (b - a) * fc;
}
function windToUV(speed, dirDeg) {
  // dirDeg = dirección DE DONDE viene el viento (convención meteorológica).
  // El vector de movimiento apunta hacia el lado opuesto (+180°).
  const rad = ((dirDeg + 180) % 360) * Math.PI / 180;
  return { u: Math.sin(rad) * speed, v: Math.cos(rad) * speed };
}
function uvToWind(u, v) {
  const speed = Math.hypot(u, v);
  const dirDeg = (Math.atan2(u, v) * 180 / Math.PI + 180 + 360) % 360;
  return { speed, dirDeg };
}

class WeatherGrid {
  constructor(center, size, radiusKm) {
    this.center = center;
    this.size = size;
    this.radiusKm = radiusKm;
    this.points = this._buildPoints();
    this.loaded = false;
    this.fetchedAt = null;
  }

  _buildPoints() {
    const latKmPerDeg = 111.32;
    const lonKmPerDeg = 111.32 * Math.cos(this.center.lat * Math.PI / 180);
    const step = (this.radiusKm * 2) / (this.size - 1);
    const pts = [];
    for (let row = 0; row < this.size; row++) {
      for (let col = 0; col < this.size; col++) {
        const dLatKm = -this.radiusKm + row * step;
        const dLonKm = -this.radiusKm + col * step;
        pts.push({
          row, col,
          lat: this.center.lat + dLatKm / latKmPerDeg,
          lon: this.center.lon + dLonKm / lonKmPerDeg,
          temp: null, windSpeed: null, windDir: null,
        });
      }
    }
    return pts;
  }

  /**
   * Consulta Open-Meteo para cada punto de la grilla POR SEPARADO, en paralelo
   * (Promise.all — no es una fila secuencial, todas las llamadas salen a la vez).
   *
   * Nota: Open-Meteo también permite pedir varias coordenadas separadas por
   * coma en una sola llamada HTTP, pero en la práctica la respuesta agrupada
   * puede volver incompleta o desalineada con los puntos pedidos (varios
   * puntos quedaban con temp:null, generando huecos en la interpolación que
   * se veían como manchas sueltas en vez de una superficie continua).
   * Pedir cada punto por separado es más lento en cantidad de requests, pero
   * cada resultado se asigna directo a su punto — sin adivinar por orden.
   */
  async fetchWeather() {
    const results = await Promise.all(this.points.map((p) => this._fetchOne(p)));

    results.forEach((r, i) => {
      this.points[i].temp = r ? r.temp : null;
      this.points[i].windSpeed = r ? r.windSpeed : 0;
      this.points[i].windDir = r ? r.windDir : 0;
    });

    const missing = this.points.filter((p) => p.temp === null).length;
    if (missing > 0) {
      console.warn(`[WeatherGrid] ${missing} de ${this.points.length} puntos sin datos tras la consulta.`);
    }

    this.loaded = true;
    this.fetchedAt = new Date();
    this.missingPoints = missing;
    return this.points;
  }

  async _fetchOne(p) {
    const url = `https://api.open-meteo.com/v1/forecast` +
      `?latitude=${p.lat.toFixed(4)}&longitude=${p.lon.toFixed(4)}` +
      `&current=temperature_2m,wind_speed_10m,wind_direction_10m` +
      `&wind_speed_unit=kmh&timezone=auto`;
    try {
      const res = await fetch(url);
      if (!res.ok) return null;
      const data = await res.json();
      const c = data.current || {};
      return {
        temp: typeof c.temperature_2m === 'number' ? c.temperature_2m : null,
        windSpeed: typeof c.wind_speed_10m === 'number' ? c.wind_speed_10m : 0,
        windDir: typeof c.wind_direction_10m === 'number' ? c.wind_direction_10m : 0,
      };
    } catch (err) {
      console.warn('[WeatherGrid] Falló la consulta de un punto:', err);
      return null;
    }
  }

  _idx(row, col) { return row * this.size + col; }

  /** Interpola viento (velocidad+dirección) y temperatura para cualquier lat/lon dentro de la grilla. */
  sample(lat, lon) {
    if (!this.loaded) return null;
    const first = this.points[0];
    const last = this.points[this.points.length - 1];

    const rowT = (lat - first.lat) / (last.lat - first.lat || 1);
    const colT = (lon - first.lon) / (last.lon - first.lon || 1);
    if (rowT < -0.15 || rowT > 1.15 || colT < -0.15 || colT > 1.15) return null; // fuera del área cubierta

    const rf = clamp(rowT, 0, 1) * (this.size - 1);
    const cf = clamp(colT, 0, 1) * (this.size - 1);
    const r0 = Math.floor(rf), c0 = Math.floor(cf);
    const r1 = Math.min(this.size - 1, r0 + 1), c1 = Math.min(this.size - 1, c0 + 1);
    const fr = rf - r0, fc = cf - c0;

    const p00 = this.points[this._idx(r0, c0)], p10 = this.points[this._idx(r1, c0)];
    const p01 = this.points[this._idx(r0, c1)], p11 = this.points[this._idx(r1, c1)];
    if ([p00, p10, p01, p11].some(p => p.temp === null)) return null;

    const temp = bilerp(p00.temp, p10.temp, p01.temp, p11.temp, fr, fc);

    const uv00 = windToUV(p00.windSpeed, p00.windDir), uv10 = windToUV(p10.windSpeed, p10.windDir);
    const uv01 = windToUV(p01.windSpeed, p01.windDir), uv11 = windToUV(p11.windSpeed, p11.windDir);
    const u = bilerp(uv00.u, uv10.u, uv01.u, uv11.u, fr, fc);
    const v = bilerp(uv00.v, uv10.v, uv01.v, uv11.v, fr, fc);
    const { speed, dirDeg } = uvToWind(u, v);

    return { temp, speed, dirDeg };
  }

  averages() {
    const valid = this.points.filter(p => p.temp !== null);
    if (!valid.length) return null;
    return {
      temp: valid.reduce((a, p) => a + p.temp, 0) / valid.length,
      windSpeed: valid.reduce((a, p) => a + p.windSpeed, 0) / valid.length,
    };
  }
}
