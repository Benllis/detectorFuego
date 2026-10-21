// ============================================================
// FireWatch · Mapa 3D — simulador de propagación POR REGLAS
//
// Dado un punto de ignición, calcula en cuánto tiempo llegaría el fuego a cada
// celda de 250 m alrededor, usando tres datos que el mapa ya tiene:
//   · combustible: propensión 0-1 de la grilla (fuel-grid.js)
//   · viento: velocidad y dirección del clima activo (weather-grid.js)
//   · pendiente: a partir de la altura (DEM de Open-Meteo, 90 m)
// y la humedad / temperatura, que acelera o frena el avance.
//
// MÉTODO (determinista, explicable y repetible):
//   1. Cada celda tiene una velocidad base  R0·combustible·sequedad  (m/min).
//   2. Viento y pendiente se suman como un "viento efectivo" (la pendiente
//      equivale a un viento hacia arriba, como en Rothermel). De él sale la
//      relación largo/ancho (LB) de la mancha y su dirección de avance.
//   3. En cada dirección θ respecto de la cabeza del fuego, la velocidad es la de
//      una elipse con el origen en su foco trasero (Richards, 1990):
//          R(θ) = (R0ef / LB) / (1 − e·cos θ),   e = √(LB² − 1) / LB
//      Con LB = 1 es un círculo (sin viento): R(θ) = R0ef. Con viento, la cabeza
//      va mucho más rápido y la cola casi no avanza.
//   4. Se busca el camino de MENOR TIEMPO desde la ignición (Dijkstra, 16 vecinos
//      por celda para que la mancha no salga con forma de octágono).
//
// ES UN MODELO POR REGLAS, NO UN MODELO FÍSICO CALIBRADO. Los parámetros de
// SPREAD_DEFAULTS son supuestos razonables del proyecto; hay que ajustarlos con
// incendios pasados (tools/evaluar_propagacion.py). El viento se supone constante.
// No considera humo, cortafuegos, la acción de bomberos ni saltos de brasas.
//
// Sin dependencias del navegador: se puede probar en Node (tests/spread.test.js).
// ============================================================

const SPREAD_DEFAULTS = {
  r0: 4.0,               // m/min: avance base (combustible 1, sin viento, plano, aire normal). 5 → 4 tras la calibración con Valparaíso 2024 (ver METODOLOGIA 5g)
  fuelMin: 0.04,         // por debajo de esta propensión la celda no arde (agua, nieve, suelo desnudo)
  lbPerKmh: 0.12,        // (0.08 → 0.12, ídem) LB = 1 + lbPerKmh · viento efectivo (km/h)
  lbMax: 6,              // la mancha no se estira más que esto
  slopeKmhPerPct: 0.5,   // km/h de viento equivalente por cada 1 % de pendiente
  tMaxMin: 360,          // se simula hasta 6 h
  ignitionFuelFloor: 0.3 // el punto de ignición arde aunque la vegetación mapeada ahí sea pobre
};
const SPREAD_D2R = Math.PI / 180;
const SPREAD_M_LAT = 110574;
const SPREAD_M_LON_EQ = 111320;
const SPREAD_NEIGHBORS = [];
for (let dr = -2; dr <= 2; dr++) {
  for (let dc = -2; dc <= 2; dc++) {
    if ((dr || dc) && Math.abs(dr) + Math.abs(dc) <= 3 && (Math.abs(dr) !== 2 || Math.abs(dc) !== 2)) {
      const g = (a, b) => (b ? g(b, a % b) : a);
      if (g(Math.abs(dr), Math.abs(dc)) === 1) SPREAD_NEIGHBORS.push({ dr, dc, dist: Math.hypot(dr, dc), bearing: Math.atan2(dc, dr) });
    }
  }
}

const fwClamp = (x, a, b) => Math.min(b, Math.max(a, x));
const fwIsNum = (x) => typeof x === 'number' && Number.isFinite(x);

// ---------------------------------------------------------------- reglas puras
/** Factor de sequedad (0.3 - 1.7): aire seco y caliente = fuego más rápido. 1 = condiciones normales. */
function fwDryness(tempC, rhPct) {
  let f = 1;
  if (fwIsNum(rhPct)) f += 0.5 * fwClamp((50 - rhPct) / 35, -1, 1);   // 85 % → 0.5 · 50 % → 1 · 15 % → 1.5
  if (fwIsNum(tempC)) f += 0.2 * fwClamp((tempC - 25) / 15, -1, 1);   // ±0.2 entre 10 y 40 °C
  return fwClamp(f, 0.3, 1.7);
}

/** Relación largo/ancho de la mancha según el viento efectivo (km/h). */
function fwLengthBreadth(effWindKmh, p = SPREAD_DEFAULTS) {
  return fwClamp(1 + p.lbPerKmh * Math.max(0, effWindKmh), 1, p.lbMax);
}

function fwEccentricity(lb) {
  return lb <= 1 ? 0 : Math.sqrt(lb * lb - 1) / lb;
}

/** Velocidad (m/min) en la dirección que forma θ (rad) con la cabeza del fuego. */
function fwDirRate(r0ef, lb, theta) {
  return (r0ef / lb) / (1 - fwEccentricity(lb) * Math.cos(theta));
}

/**
 * Viento efectivo = viento + pendiente (como vectores). `windToDeg` es hacia dónde SOPLA;
 * (dzdx, dzdy) es el gradiente de altura (m/m) hacia el este y el norte; la pendiente
 * "empuja" hacia arriba, o sea en la dirección del gradiente.
 * Devuelve { speed km/h, toRad: rumbo de la cabeza del fuego en rad desde el norte }.
 */
function fwEffectiveWind(windToDeg, windKmh, dzdx, dzdy, p = SPREAD_DEFAULTS) {
  const wr = windToDeg * SPREAD_D2R;
  let e = Math.sin(wr) * windKmh, n = Math.cos(wr) * windKmh;
  const slope = Math.hypot(dzdx, dzdy);
  if (slope > 0) {
    const s = p.slopeKmhPerPct * slope * 100;
    e += (dzdx / slope) * s; n += (dzdy / slope) * s;
  }
  return { speed: Math.hypot(e, n), toRad: Math.atan2(e, n) };
}

class FwHeap { // cola de prioridad mínima (tiempo, índice)
  constructor() { this.t = []; this.i = []; }
  get size() { return this.t.length; }
  push(t, i) {
    let k = this.t.length;
    this.t.push(t); this.i.push(i);
    while (k > 0) {
      const up = (k - 1) >> 1;
      if (this.t[up] <= t) break;
      this.t[k] = this.t[up]; this.i[k] = this.i[up]; k = up;
    }
    this.t[k] = t; this.i[k] = i;
  }
  pop() {
    const topT = this.t[0], topI = this.i[0];
    const lastT = this.t.pop(), lastI = this.i.pop();
    const n = this.t.length;
    if (n) {
      let k = 0;
      for (;;) {
        let c = 2 * k + 1;
        if (c >= n) break;
        if (c + 1 < n && this.t[c + 1] < this.t[c]) c++;
        if (this.t[c] >= lastT) break;
        this.t[k] = this.t[c]; this.i[k] = this.i[c]; k = c;
      }
      this.t[k] = lastT; this.i[k] = lastI;
    }
    return [topT, topI];
  }
}

/**
 * Simula la propagación.
 * @param {object} inp
 *   rows, cols, cellM            tamaño de la ventana y de cada celda (fila 0 = sur, columna 0 = oeste)
 *   fuel       Float32Array      propensión 0-1 (NaN = sin dato: no arde)
 *   elev       Float32Array|null altura (m); null = terreno plano
 *   windSpeed  Float32Array      km/h
 *   windFromDeg Float32Array     de dónde viene el viento (grados)
 *   dryness    Float32Array|null factor de sequedad (null = 1)
 *   ignitions  [{row, col}]
 * @returns {{arrival: Float32Array, rows, cols, cellM, ignitions, params}} arrival en minutos (Infinity = no llega)
 */
function fwSimulate(inp, params = {}) {
  const p = { ...SPREAD_DEFAULTS, ...params };
  const { rows, cols, cellM } = inp;
  const N = rows * cols;
  const ign = new Set((inp.ignitions || []).map((g) => g.row * cols + g.col));
  if (!ign.size) throw new Error('falta el punto de ignición');

  const r0ef = new Float32Array(N), lb = new Float32Array(N), ecc = new Float32Array(N), head = new Float32Array(N);
  const burnable = new Uint8Array(N);
  const z = inp.elev || null;
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const i = r * cols + c;
      let f = inp.fuel[i];
      if (ign.has(i)) f = Math.max(fwIsNum(f) ? f : 0, p.ignitionFuelFloor);
      if (!fwIsNum(f) || f < p.fuelMin) continue;
      burnable[i] = 1;
      let dzdx = 0, dzdy = 0;
      if (z) {
        const cw = Math.max(0, c - 1), ce = Math.min(cols - 1, c + 1), rs = Math.max(0, r - 1), rn = Math.min(rows - 1, r + 1);
        dzdx = (z[r * cols + ce] - z[r * cols + cw]) / ((ce - cw) * cellM || 1);
        dzdy = (z[rn * cols + c] - z[rs * cols + c]) / ((rn - rs) * cellM || 1);
      }
      const ws = fwIsNum(inp.windSpeed[i]) ? inp.windSpeed[i] : 0;
      const wf = fwIsNum(inp.windFromDeg[i]) ? inp.windFromDeg[i] : 0;
      const eff = fwEffectiveWind((wf + 180) % 360, ws, dzdx, dzdy, p);
      lb[i] = fwLengthBreadth(eff.speed, p);
      ecc[i] = fwEccentricity(lb[i]);
      head[i] = eff.toRad;
      r0ef[i] = p.r0 * f * (inp.dryness ? inp.dryness[i] : 1);
    }
  }

  const dist = new Float64Array(N).fill(Infinity); // doble precisión: con Float32 el tiempo del heap no coincide y se descartan celdas
  const heap = new FwHeap();
  ign.forEach((i) => { dist[i] = 0; heap.push(0, i); });
  while (heap.size) {
    const [t, i] = heap.pop();
    if (t > dist[i]) continue;
    if (t > p.tMaxMin) break;
    const r = (i / cols) | 0, c = i - r * cols;
    for (const nb of SPREAD_NEIGHBORS) {
      const nr = r + nb.dr, nc = c + nb.dc;
      if (nr < 0 || nr >= rows || nc < 0 || nc >= cols) continue;
      const j = nr * cols + nc;
      if (!burnable[j]) continue;
      const rate = (k) => (r0ef[k] / lb[k]) / (1 - ecc[k] * Math.cos(nb.bearing - head[k]));
      const nt = t + (nb.dist * cellM / 2) * (1 / rate(i) + 1 / rate(j));
      if (nt < dist[j] && nt <= p.tMaxMin) { dist[j] = nt; heap.push(nt, j); }
    }
  }
  return { arrival: Float32Array.from(dist), rows, cols, cellM, ignitions: inp.ignitions, params: p };
}

const FW_COMPASS16 = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSO', 'SO', 'OSO', 'O', 'ONO', 'NO', 'NNO'];
function fwCompass16(deg) {
  return FW_COMPASS16[Math.round((((deg % 360) + 360) % 360) / 22.5) % 16];
}

/**
 * Resumen por horizonte (minutos): área quemada (ha), alcance máximo (km) y hacia dónde
 * (rumbo y punto cardinal) desde la primera ignición.
 */
function fwSpreadStats(res, horizonsMin = [60, 180, 360]) {
  const { arrival, rows, cols, cellM } = res;
  const o = res.ignitions[0];
  return horizonsMin.map((m) => {
    let cells = 0, far = 0, farR = o.row, farC = o.col;
    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        if (arrival[r * cols + c] > m) continue;
        cells++;
        const d = Math.hypot(r - o.row, c - o.col);
        if (d > far) { far = d; farR = r; farC = c; }
      }
    }
    const bearing = far ? ((Math.atan2(farC - o.col, farR - o.row) / SPREAD_D2R) + 360) % 360 : null;
    return { minutes: m, cells, areaHa: cells * cellM * cellM / 10000, maxKm: far * cellM / 1000, bearing, compass: bearing === null ? null : fwCompass16(bearing) };
  });
}

// ---------------------------------------------------------------- entradas desde las grillas del mapa
/** Retícula de puntos de altura que cubre la ventana (con un margen), para pedir el DEM por tandas. */
function fwElevationLattice(center, radiusKm, spacingKm) {
  const mLon = SPREAD_M_LON_EQ * Math.cos(center.lat * SPREAD_D2R);
  const half = radiusKm + 1.5 * spacingKm; // un poco más de margen: el anclaje a la cuadrícula puede desplazar hasta medio paso
  const nLat = Math.ceil((2 * half) / spacingKm) + 1, nLon = nLat;
  const dLat = (spacingKm * 1000) / SPREAD_M_LAT, dLon = (spacingKm * 1000) / mLon;
  // La retícula se ancla a una cuadrícula fija (múltiplos de dLat/dLon): dos ignaciones cercanas comparten puntos
  // y la altura ya guardada (navegador y servidor) se reutiliza. El margen de un paso sigue cubriendo la ventana.
  const lat0 = Math.round((center.lat - ((nLat - 1) / 2) * dLat) / dLat) * dLat;
  const lon0 = Math.round((center.lon - ((nLon - 1) / 2) * dLon) / dLon) * dLon;
  const coords = [];
  for (let a = 0; a < nLat; a++) for (let b = 0; b < nLon; b++) coords.push([lat0 + a * dLat, lon0 + b * dLon]);
  return { lat0, lon0, dLat, dLon, nLat, nLon, coords };
}

/** Función (lat, lon) → altura, por interpolación bilineal de la retícula (z en el orden de `coords`). */
function fwElevationFn(lat, z) {
  return (la, lo) => {
    const fa = fwClamp((la - lat.lat0) / lat.dLat, 0, lat.nLat - 1), fb = fwClamp((lo - lat.lon0) / lat.dLon, 0, lat.nLon - 1);
    const a0 = Math.min(lat.nLat - 2, Math.floor(fa)), b0 = Math.min(lat.nLon - 2, Math.floor(fb));
    const ta = fa - a0, tb = fb - b0;
    const v = (a, b) => z[a * lat.nLon + b];
    return (v(a0, b0) * (1 - tb) + v(a0, b0 + 1) * tb) * (1 - ta) + (v(a0 + 1, b0) * (1 - tb) + v(a0 + 1, b0 + 1) * tb) * ta;
  };
}

/**
 * Arma la entrada del simulador muestreando las grillas del mapa (se les pide solo
 * `fuelGrid.cell(lat, lon).fuel` y `weatherGrid.sample(lat, lon)`).
 * La ventana es cuadrada, centrada en la ignición, y la celda de ignición cae al centro.
 */
function fwBuildInput({ center, radiusKm, cellM = 250, fuelGrid, weatherGrid, elevAt = null }) {
  if (!fuelGrid) throw new Error('No hay datos de vegetación en este punto.');
  const n = Math.max(3, 2 * Math.ceil((radiusKm * 1000) / cellM) + 1); // impar: hay celda central; radiusKm = medio lado
  const rows = n, cols = n;
  const mLon = SPREAD_M_LON_EQ * Math.cos(center.lat * SPREAD_D2R);
  const lat0 = center.lat - ((rows * cellM) / 2) / SPREAD_M_LAT, lon0 = center.lon - ((cols * cellM) / 2) / mLon;
  const N = rows * cols;
  const fuel = new Float32Array(N), windSpeed = new Float32Array(N), windFromDeg = new Float32Array(N), dryness = new Float32Array(N);
  const elev = elevAt ? new Float32Array(N) : null;
  const ctr = weatherGrid.sample(center.lat, center.lon);
  if (!ctr) throw new Error('No hay clima para este punto (fuera de la grilla de datos).');
  let withFuel = 0, sumSpeed = 0, sumRh = 0, nRh = 0, sumTemp = 0;
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const i = r * cols + c;
      const la = lat0 + ((r + 0.5) * cellM) / SPREAD_M_LAT, lo = lon0 + ((c + 0.5) * cellM) / mLon;
      const cell = fuelGrid.cell(la, lo);
      fuel[i] = cell ? cell.fuel : NaN;
      if (cell) withFuel++;
      const w = weatherGrid.sample(la, lo) || ctr;
      windSpeed[i] = w.speed; windFromDeg[i] = w.dirDeg; dryness[i] = fwDryness(w.temp, w.rh);
      sumSpeed += w.speed; sumTemp += w.temp;
      if (fwIsNum(w.rh)) { sumRh += w.rh; nRh++; }
      if (elev) elev[i] = elevAt(la, lo);
    }
  }
  const mid = (n - 1) / 2;
  return {
    input: { rows, cols, cellM, fuel, elev, windSpeed, windFromDeg, dryness, ignitions: [{ row: mid, col: mid }] },
    geo: { lat0, lon0, mLon, west: lon0, south: lat0, east: lon0 + (cols * cellM) / mLon, north: lat0 + (rows * cellM) / SPREAD_M_LAT },
    summary: {
      fuelCoverage: withFuel / N, windKmh: sumSpeed / N, windFromDeg: ctr.dirDeg, tempC: sumTemp / N,
      rh: nRh ? sumRh / nRh : null, hasElevation: !!elev, ignitionFuel: fuel[mid * cols + mid],
    },
  };
}

if (typeof module !== 'undefined') {
  module.exports = {
    SPREAD_DEFAULTS, SPREAD_NEIGHBORS, fwDryness, fwLengthBreadth, fwEccentricity, fwDirRate, fwEffectiveWind,
    fwSimulate, fwSpreadStats, fwCompass16, fwElevationLattice, fwElevationFn, fwBuildInput,
  };
}
