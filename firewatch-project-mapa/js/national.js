// ============================================================
// FireWatch · Mapa 3D — cobertura de todo Chile (clima)
//
// La grilla fina de Santiago (8x8, radio 28 km) no escala a un país. Esta
// capa agrega una grilla GRUESA nacional (1,25° ≈ 140 km, 33 x 9 = 297
// puntos) pedida a Open-Meteo en tandas de 80 puntos, y un "compuesto":
// el punto se mide con la grilla fina si cae dentro de ella y, si no, con
// la nacional. El viento se interpola en componentes u/v (conserva la
// dirección; es lo que se perdió en el intento de la Sesión 07 con tiles
// de OpenWeatherMap).
//
// LÍMITE HONESTO: 1,25° no ve brisas de valle ni efectos de la cordillera;
// sirve para el viento sinóptico de la región. Para una zona concreta, el
// clima fino se pide aparte (zona local / consulta de punto).
//
// Cargar después de weather-grid.js (extiende WeatherGrid).
// ============================================================

const NATION = {
  bbox: [-76, -56.5, -66, -17.3],        // oeste, sur, este, norte
  center: { lat: -37, lon: -71 },
  lat0: -56.5, lon0: -76, step: 1.25,
  rows: 33, cols: 9,
  chunk: 80,                              // puntos por petición
};
const NATION_AREA = { center: NATION.center, radiusKm: 2200, bbox: NATION.bbox };

// Contorno APROXIMADO de Chile: por latitud, [lat, longitud de la costa oeste, longitud del límite este].
// (Digitado a mano de mapas; incluye islas cercanas y el archipiélago austral de forma gruesa.)
const CHILE_EDGES = [
  [-17.5, -70.5, -69.4], [-19, -70.3, -68.9], [-21, -70.3, -68.3], [-23, -70.5, -67.0], [-25, -70.6, -68.3],
  [-27, -71.0, -68.5], [-29, -71.6, -69.8], [-31, -71.7, -70.2], [-33, -71.8, -69.9], [-35, -72.4, -70.3],
  [-37, -73.3, -70.9], [-39, -73.5, -71.2], [-41, -74.0, -71.6], [-43, -74.5, -71.7], [-45, -75.2, -71.5],
  [-47, -75.6, -72.6], [-49, -75.7, -72.9], [-51, -75.6, -71.8], [-52.5, -75.2, -69.2], [-54, -73.8, -68.4],
  [-55, -71.5, -66.6], [-56, -69.5, -66.8],
];
/**
 * 1 dentro de Chile, bajando a 0 en ~0,5° fuera de su contorno aproximado. Sirve para no pintar el mapa
 * de calor sobre el océano, Argentina y Bolivia, ni lanzar partículas de viento fuera del país.
 * NO es la frontera real.
 */
/** [oeste, este] del contorno aproximado a esa latitud, o null si queda fuera del país. */
function chileEdgesAt(lat) {
  const E = CHILE_EDGES;
  if (lat > E[0][0] || lat < E[E.length - 1][0]) return null;
  let i = 0;
  while (i < E.length - 2 && lat < E[i + 1][0]) i++;
  const a = E[i], b = E[i + 1], f = (lat - a[0]) / (b[0] - a[0]);
  return [a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f];
}
function chileMaskAlpha(lat, lon) {
  const ed = chileEdgesAt(lat);
  if (!ed) return 0;
  const [west, east] = ed;
  const d = lon < west ? west - lon : lon > east ? lon - east : 0;
  if (d <= 0) return 1;
  if (d >= 0.5) return 0;
  const k = 1 - d / 0.5;
  return k * k * (3 - 2 * k);
}

class NationalWeatherGrid extends WeatherGrid {
  constructor(opts = {}) {
    super(NATION.center, 2, 2200, opts);
    this.size = NATION.rows; // (referencia; el muestreo usa rows/cols propios)
  }

  _buildPoints() {
    const pts = [];
    for (let row = 0; row < NATION.rows; row++) {
      for (let col = 0; col < NATION.cols; col++) {
        pts.push({
          row, col,
          lat: NATION.lat0 + row * NATION.step,
          lon: NATION.lon0 + col * NATION.step,
          temp: null, rh: null, windSpeed: null, windDir: null, elev: null,
        });
      }
    }
    return pts;
  }

  _cacheKey() { return `fw:om:nacional:v1:${NATION.rows}x${NATION.cols}`; }
  _idx(row, col) { return row * NATION.cols + col; }

  _queryFor(pts) {
    return `latitude=${pts.map((p) => p.lat.toFixed(3)).join(',')}&longitude=${pts.map((p) => p.lon.toFixed(3)).join(',')}` +
      '&current=temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m&wind_speed_unit=kmh&timezone=UTC';
  }

  /** Tandas secuenciales con pausa y reintentos: así no se pasa del límite por minuto de Open-Meteo. */
  async _requestRaw() {
    const out = [];
    let cacheAge = 0, stale = false;
    for (let i = 0; i < this.points.length; i += NATION.chunk) {
      const qs = this._queryFor(this.points.slice(i, i + NATION.chunk));
      const url = FW.proxy ? `/api/openmeteo?${qs}` : `${OPEN_METEO_DIRECT}?${qs}`;
      let data = null, lastErr = null;
      for (let attempt = 0; attempt < 3 && !data; attempt++) {
        if (attempt) await sleep(attempt === 1 ? 4000 : 15000);
        try {
          const res = await fetch(url);
          if (!res.ok) throw new Error(`Open-Meteo: HTTP ${res.status}`);
          cacheAge = Math.max(cacheAge, parseInt(res.headers.get('X-Cache-Age') || '0', 10));
          stale = stale || res.headers.get('X-Stale') === 'true';
          data = await res.json();
        } catch (err) {
          lastErr = err;
          if (typeof fwLog === 'function') fwLog('clima-nacional', 'warn', err.message, { phase: `tanda ${i / NATION.chunk + 1}, intento ${attempt + 1}`, variant: 'open-meteo' });
        }
      }
      if (!data) throw lastErr || new Error('Open-Meteo no respondió');
      (Array.isArray(data) ? data : [data]).forEach((d) => out.push(d));
      await sleep(400);
    }
    return { data: out, cacheAge, stale };
  }

  sample(lat, lon) {
    if (!this.loaded) return null;
    const rf0 = (lat - NATION.lat0) / NATION.step, cf0 = (lon - NATION.lon0) / NATION.step;
    const m = 0.15;
    if (rf0 < -m || rf0 > NATION.rows - 1 + m || cf0 < -m || cf0 > NATION.cols - 1 + m) return null;
    const rf = clamp(rf0, 0, NATION.rows - 1), cf = clamp(cf0, 0, NATION.cols - 1);
    const r0 = Math.floor(rf), c0 = Math.floor(cf);
    const r1 = Math.min(NATION.rows - 1, r0 + 1), c1 = Math.min(NATION.cols - 1, c0 + 1);
    const fr = rf - r0, fc = cf - c0;
    const p00 = this.points[this._idx(r0, c0)], p10 = this.points[this._idx(r1, c0)];
    const p01 = this.points[this._idx(r0, c1)], p11 = this.points[this._idx(r1, c1)];
    const q = [p00, p10, p01, p11];
    if (q.some((p) => p.temp === null || p.windSpeed === null)) return null;
    const temp = bilerp(p00.temp, p10.temp, p01.temp, p11.temp, fr, fc);
    const uv = q.map((p) => windToUV(p.windSpeed, p.windDir));
    const u = bilerp(uv[0].u, uv[1].u, uv[2].u, uv[3].u, fr, fc);
    const v = bilerp(uv[0].v, uv[1].v, uv[2].v, uv[3].v, fr, fc);
    const { speed, dirDeg } = uvToWind(u, v);
    const rh = q.every((p) => isNum(p.rh)) ? bilerp(p00.rh, p10.rh, p01.rh, p11.rh, fr, fc) : null;
    return { temp, speed, dirDeg, rh, elev: null, scale: 'national' };
  }
}

/** Clima "en vivo" para consultas y viento: grilla fina si el punto cae en ella; si no, la nacional. */
class WeatherComposite {
  constructor(local, national) { this.local = local; this.national = national; }
  get loaded() { return !!((this.local && this.local.loaded) || (this.national && this.national.loaded)); }
  sample(lat, lon) {
    if (this.local && this.local.loaded) {
      const a = this.local.sample(lat, lon);
      if (a) return { ...a, scale: 'local' };
    }
    return this.national && this.national.loaded ? this.national.sample(lat, lon) : null;
  }
}

if (typeof module !== 'undefined') module.exports = { NATION, NATION_AREA, chileMaskAlpha, chileEdgesAt };
