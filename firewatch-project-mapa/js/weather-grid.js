// ============================================================
// FireWatch · Mapa 3D — grilla de datos meteorológicos reales
//
// Construye una grilla NxN de puntos alrededor de un centro, consulta
// Open-Meteo (gratis, sin API key) y permite interpolar viento,
// temperatura y humedad para cualquier lat/lon dentro de la grilla.
//
// v2 — UNA sola petición con todas las coordenadas (antes: 64 peticiones
// en paralelo). Verificado en vivo (28-09-2026): 64 puntos, 1 respuesta,
// ~0.6 s, 0 datos nulos. Se agrega humedad relativa (mismo costo).
// Con el proxy local la respuesta queda cacheada 30 min; sin proxy se
// cachea en localStorage 60 min. Si Open-Meteo falla se usa el último
// dato guardado, marcado como viejo.
//
// El viento se interpola convirtiendo velocidad+dirección a componentes
// u/v ANTES de interpolar y se reconvierte después (interpolar el ángulo
// directamente es un error común: el promedio entre 350° y 10° NO es 180°).
// ============================================================

function clamp(x, min, max) { return Math.min(max, Math.max(min, x)); }
function bilerp(v00, v10, v01, v11, fr, fc) {
  const a = v00 + (v10 - v00) * fr;
  const b = v01 + (v11 - v01) * fr;
  return a + (b - a) * fc;
}
function windToUV(speed, dirDeg) {
  const rad = ((dirDeg + 180) % 360) * Math.PI / 180;
  return { u: Math.sin(rad) * speed, v: Math.cos(rad) * speed };
}
function uvToWind(u, v) {
  const speed = Math.hypot(u, v);
  const dirDeg = (Math.atan2(u, v) * 180 / Math.PI + 180 + 360) % 360;
  return { speed, dirDeg };
}

const OPEN_METEO_DIRECT = 'https://api.open-meteo.com/v1/forecast';
const OPEN_METEO_ARCHIVE_DIRECT = 'https://archive-api.open-meteo.com/v1/archive';
const OPEN_METEO_CACHE_MS = 60 * 60 * 1000;

const isNum = (x) => typeof x === 'number' && Number.isFinite(x);

class WeatherGrid {
  /**
   * `opts.at` (Date, opcional): en vez del clima actual, usa el clima de esa
   * HORA (UTC) tomado del archivo histórico de Open-Meteo. Sirve para el
   * replay y para "ayer": el clima del momento en que el satélite vio el foco.
   */
  constructor(center, size, radiusKm, opts = {}) {
    this.at = opts.at ? new Date(Math.round(opts.at.getTime() / 3600000) * 3600000) : null;
    this.center = center;
    this.size = size;
    this.radiusKm = radiusKm;
    this.points = this._buildPoints();
    this.loaded = false;
    this.fetchedAt = null;      // cuándo se pidió el dato
    this.modelTime = null;      // hora (UTC) del dato según Open-Meteo
    this.missingPoints = 0;
    this.fromCache = false;     // true si el dato salió de caché
    this.stale = false;         // true si Open-Meteo falló y se usó un dato viejo
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
          temp: null, rh: null, windSpeed: null, windDir: null, elev: null,
        });
      }
    }
    return pts;
  }

  _cacheKey() {
    const c = this.center;
    return `fw:om:${c.lat.toFixed(3)},${c.lon.toFixed(3)}:${this.size}:${this.radiusKm.toFixed(1)}` +
      (this.at ? `:${this.at.toISOString().slice(0, 13)}` : '');
  }

  _buildQuery() {
    const coords = `latitude=${this.points.map((p) => p.lat.toFixed(4)).join(',')}` +
      `&longitude=${this.points.map((p) => p.lon.toFixed(4)).join(',')}`;
    const vars = 'temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m';
    if (this.at) {
      const day = this.at.toISOString().slice(0, 10);
      return `${coords}&hourly=${vars}&start_date=${day}&end_date=${day}&wind_speed_unit=kmh&timezone=UTC`;
    }
    return `${coords}&current=${vars}&wind_speed_unit=kmh&timezone=UTC`;
  }

  /** Pide todos los puntos en UNA sola petición (con un reintento). */
  async _requestRaw() {
    const qs = this._buildQuery();
    const url = this.at
      ? (FW.proxy ? `/api/openmeteo-archive?${qs}` : `${OPEN_METEO_ARCHIVE_DIRECT}?${qs}`)
      : (FW.proxy ? `/api/openmeteo?${qs}` : `${OPEN_METEO_DIRECT}?${qs}`);
    let lastErr = null;
    for (let attempt = 0; attempt < 2; attempt++) {
      if (attempt) await new Promise((r) => setTimeout(r, 1500));
      try {
        const res = await fetch(url);
        if (res.status === 429) throw new Error('Open-Meteo: límite de uso alcanzado (HTTP 429)');
        if (!res.ok) throw new Error(`Open-Meteo: HTTP ${res.status}`);
        const cacheAge = parseInt(res.headers.get('X-Cache-Age') || '0', 10);
        return { data: await res.json(), cacheAge, stale: res.headers.get('X-Stale') === 'true' };
      } catch (err) {
        lastErr = err;
        if (/429/.test(String(err.message))) break; // reintentar no sirve si es límite de uso
      }
    }
    throw lastErr;
  }

  _applyResponse(data) {
    const list = Array.isArray(data) ? data : [data];
    let latest = '';
    const hourKey = this.at ? this.at.toISOString().slice(0, 13) + ':00' : null;
    this.points.forEach((p, i) => {
      let c = (list[i] && list[i].current) || {};
      if (hourKey && list[i] && list[i].hourly) {
        const h = list[i].hourly, k = h.time.indexOf(hourKey);
        c = k < 0 ? {} : {
          time: hourKey, temperature_2m: h.temperature_2m[k], relative_humidity_2m: h.relative_humidity_2m[k],
          wind_speed_10m: h.wind_speed_10m[k], wind_direction_10m: h.wind_direction_10m[k],
        };
      }
      p.elev = list[i] && isNum(list[i].elevation) ? list[i].elevation : null;
      const ok = isNum(c.temperature_2m) && isNum(c.wind_speed_10m) && isNum(c.wind_direction_10m);
      p.temp = ok ? c.temperature_2m : null;
      p.rh = isNum(c.relative_humidity_2m) ? c.relative_humidity_2m : null;
      p.windSpeed = ok ? c.wind_speed_10m : null;
      p.windDir = ok ? c.wind_direction_10m : null;
      if (ok && c.time > latest) latest = c.time;
    });
    if (list.length !== this.points.length) {
      console.warn(`[WeatherGrid] Open-Meteo devolvió ${list.length} de ${this.points.length} puntos.`);
    }
    this.modelTime = latest ? new Date(latest + 'Z') : null;
    this.missingPoints = this.points.filter((p) => p.temp === null).length;
  }

  async fetchWeather() {
    let fromCache = false, stale = false;

    // Sin proxy: caché del navegador para no gastar cuota al recargar.
    if (!FW.proxy) {
      const hit = lsGet(this._cacheKey(), OPEN_METEO_CACHE_MS);
      if (hit) { this._applyResponse(hit.value); fromCache = true; }
    }

    if (!fromCache) {
      try {
        const { data, cacheAge, stale: proxyStale } = await this._requestRaw();
        this._applyResponse(data);
        fromCache = FW.proxy && cacheAge > 5;
        stale = proxyStale;
        if (!FW.proxy) lsSet(this._cacheKey(), data);
      } catch (err) {
        const old = !FW.proxy ? lsGet(this._cacheKey(), 0) : null; // sin límite de edad
        if (!old) throw err;
        console.warn('[WeatherGrid] Open-Meteo falló, usando dato viejo:', err.message);
        this._applyResponse(old.value);
        fromCache = true; stale = true;
      }
    }

    if (this.missingPoints > 0) {
      console.warn(`[WeatherGrid] ${this.missingPoints} de ${this.points.length} puntos sin datos.`);
    }
    if (this.missingPoints === this.points.length) {
      throw new Error('Open-Meteo no devolvió datos válidos para ningún punto.');
    }

    this.loaded = true;
    this.fetchedAt = new Date();
    this.fromCache = fromCache;
    this.stale = stale;
    return this.points;
  }

  _idx(row, col) { return row * this.size + col; }

  /** Interpola viento, temperatura y humedad para cualquier lat/lon dentro de la grilla. */
  sample(lat, lon) {
    if (!this.loaded) return null;
    const first = this.points[0];
    const last = this.points[this.points.length - 1];

    const rowT = (lat - first.lat) / (last.lat - first.lat || 1);
    const colT = (lon - first.lon) / (last.lon - first.lon || 1);
    if (rowT < -0.15 || rowT > 1.15 || colT < -0.15 || colT > 1.15) return null;

    const rf = clamp(rowT, 0, 1) * (this.size - 1);
    const cf = clamp(colT, 0, 1) * (this.size - 1);
    const r0 = Math.floor(rf), c0 = Math.floor(cf);
    const r1 = Math.min(this.size - 1, r0 + 1), c1 = Math.min(this.size - 1, c0 + 1);
    const fr = rf - r0, fc = cf - c0;

    const p00 = this.points[this._idx(r0, c0)], p10 = this.points[this._idx(r1, c0)];
    const p01 = this.points[this._idx(r0, c1)], p11 = this.points[this._idx(r1, c1)];
    if ([p00, p10, p01, p11].some((p) => p.temp === null || p.windSpeed === null)) return null;

    const temp = bilerp(p00.temp, p10.temp, p01.temp, p11.temp, fr, fc);

    const uv00 = windToUV(p00.windSpeed, p00.windDir), uv10 = windToUV(p10.windSpeed, p10.windDir);
    const uv01 = windToUV(p01.windSpeed, p01.windDir), uv11 = windToUV(p11.windSpeed, p11.windDir);
    const u = bilerp(uv00.u, uv10.u, uv01.u, uv11.u, fr, fc);
    const v = bilerp(uv00.v, uv10.v, uv01.v, uv11.v, fr, fc);
    const { speed, dirDeg } = uvToWind(u, v);

    const rh = [p00, p10, p01, p11].every((p) => isNum(p.rh))
      ? bilerp(p00.rh, p10.rh, p01.rh, p11.rh, fr, fc)
      : null;

    const elev = [p00, p10, p01, p11].every((p) => isNum(p.elev))
      ? bilerp(p00.elev, p10.elev, p01.elev, p11.elev, fr, fc)
      : null;

    return { temp, speed, dirDeg, rh, elev };
  }

  averages() {
    const valid = this.points.filter((p) => p.temp !== null && p.windSpeed !== null);
    if (!valid.length) return null;
    const rhs = valid.map((p) => p.rh).filter(isNum);
    return {
      temp: valid.reduce((a, p) => a + p.temp, 0) / valid.length,
      windSpeed: valid.reduce((a, p) => a + p.windSpeed, 0) / valid.length,
      rh: rhs.length ? rhs.reduce((a, x) => a + x, 0) / rhs.length : null,
    };
  }
}
