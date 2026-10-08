// ============================================================
// FireWatch · Mapa 3D — grilla de combustible CONSULTABLE por celda
//
// Hasta ahora la vegetación era solo una imagen recoloreada. Esta clase
// guarda la clasificación de cobertura (ESA WorldCover, ~45 m por pixel)
// como datos, para poder preguntar "¿qué hay en este punto / celda / radio?"
// y para alimentar después una simulación de propagación.
//
// Vocabulario:
//  - clase: una de las 11 categorías de ESA (bosque, matorral, pastizal…).
//  - propensión de combustible (`fuel`, 0-1): qué tan fácil arde y sostiene
//    fuego esa cobertura. ES UNA REGLA DEL PROYECTO, no un dato medido: una
//    clasificación de cobertura no dice carga, humedad ni continuidad del
//    combustible (los modelos reales, p. ej. Anderson/Scott & Burgan, sí).
//  - celda: cuadro de 250 m alineado a la esquina suroeste del área; cada
//    celda resume las clases de los pixeles que contiene.
//
// Sin dependencias del navegador: se puede probar en Node (tests/).
// ============================================================

const WORLDCOVER_CLASSES = [
  { key: 'bosque',    esa: [0, 100, 0],     label: 'Bosque / arbolado',                 risk: 'Alto',       fuel: 0.80, rgba: [230, 81, 0, 150] },
  { key: 'matorral',  esa: [255, 187, 34],  label: 'Matorral',                          risk: 'Muy alto',   fuel: 1.00, rgba: [183, 28, 28, 165] },
  { key: 'pastizal',  esa: [255, 255, 76],  label: 'Pastizal',                          risk: 'Medio-alto', fuel: 0.75, rgba: [251, 192, 45, 140] },
  { key: 'cultivo',   esa: [240, 150, 255], label: 'Cultivo agrícola',                  risk: 'Medio-bajo', fuel: 0.40, rgba: [174, 213, 129, 110] },
  { key: 'urbano',    esa: [250, 0, 0],     label: 'Urbano / construido',               risk: 'Bajo',       fuel: 0.10, rgba: [120, 120, 120, 45] },
  { key: 'desnudo',   esa: [180, 180, 180], label: 'Suelo desnudo / vegetación escasa', risk: 'Bajo',       fuel: 0.05, rgba: [150, 150, 150, 35] },
  { key: 'nieve',     esa: [240, 240, 240], label: 'Nieve / hielo',                     risk: 'Nulo',       fuel: 0.00, rgba: [0, 0, 0, 0] },
  { key: 'agua',      esa: [0, 100, 200],   label: 'Cuerpos de agua',                   risk: 'Nulo',       fuel: 0.00, rgba: [0, 0, 0, 0] },
  { key: 'humedal',   esa: [0, 150, 160],   label: 'Humedal',                           risk: 'Bajo',       fuel: 0.20, rgba: [0, 0, 0, 0] },
  { key: 'manglar',   esa: [0, 207, 117],   label: 'Manglar',                           risk: 'Bajo',       fuel: 0.20, rgba: [0, 0, 0, 0] },
  { key: 'musgo',     esa: [250, 230, 160], label: 'Musgo / líquenes',                  risk: 'Bajo',       fuel: 0.15, rgba: [150, 150, 150, 35] },
];
const FUEL_NO_DATA = 255;
const FUEL_CELL_M = 250;
const M_PER_DEG_LAT = 110574;
const M_PER_DEG_LON_EQ = 111320;

class FuelGrid {
  /**
   * @param {Uint8Array} cls   índice de clase (WORLDCOVER_CLASSES) por pixel, fila 0 = norte; 255 = sin dato
   * @param {number} w,h       tamaño de la imagen en pixeles
   * @param {number[][]} bounds [[oeste, sur],[este, norte]]
   */
  constructor(cls, w, h, bounds, meta = {}) {
    this.cls = cls; this.w = w; this.h = h;
    this.west = bounds[0][0]; this.south = bounds[0][1];
    this.east = bounds[1][0]; this.north = bounds[1][1];
    this.meta = meta;
    const latMid = (this.south + this.north) / 2;
    this.mPerDegLon = M_PER_DEG_LON_EQ * Math.cos(latMid * Math.PI / 180);
    this.widthM = (this.east - this.west) * this.mPerDegLon;
    this.heightM = (this.north - this.south) * M_PER_DEG_LAT;
    this.pxM = { x: this.widthM / w, y: this.heightM / h }; // metros por pixel
    this._cellCache = new Map();
  }

  contains(lat, lon) {
    return lat >= this.south && lat <= this.north && lon >= this.west && lon <= this.east;
  }

  /** Clase (entrada de WORLDCOVER_CLASSES) del pixel que contiene el punto, o null. */
  classAt(lat, lon) {
    if (!this.contains(lat, lon)) return null;
    const x = Math.min(this.w - 1, Math.floor(((lon - this.west) / (this.east - this.west)) * this.w));
    const y = Math.min(this.h - 1, Math.floor(((this.north - lat) / (this.north - this.south)) * this.h));
    const k = this.cls[y * this.w + x];
    return k === FUEL_NO_DATA ? null : WORLDCOVER_CLASSES[k];
  }

  _summarize(x0, x1, y0, y1) {
    const counts = new Array(WORLDCOVER_CLASSES.length).fill(0);
    let n = 0;
    for (let y = Math.max(0, y0); y < Math.min(this.h, y1); y++) {
      for (let x = Math.max(0, x0); x < Math.min(this.w, x1); x++) {
        const k = this.cls[y * this.w + x];
        if (k === FUEL_NO_DATA) continue;
        counts[k]++; n++;
      }
    }
    if (!n) return null;
    const fractions = counts.map((c, k) => ({ cls: WORLDCOVER_CLASSES[k], fraction: c / n }))
      .filter((f) => f.fraction > 0).sort((a, b) => b.fraction - a.fraction);
    const fuel = fractions.reduce((s, f) => s + f.fraction * f.cls.fuel, 0);
    return { fractions, dominant: fractions[0].cls, fuel, pixels: n };
  }

  /**
   * Celda de 250 m (alineada a la esquina suroeste del área) que contiene el punto:
   * { row, col, center:{lat,lon}, dominant, fractions:[{cls,fraction}], fuel, pixels }.
   */
  cell(lat, lon) {
    if (!this.contains(lat, lon)) return null;
    const mx = (lon - this.west) * this.mPerDegLon;
    const my = (lat - this.south) * M_PER_DEG_LAT;
    const col = Math.floor(mx / FUEL_CELL_M), row = Math.floor(my / FUEL_CELL_M);
    const key = row * 100000 + col;
    if (this._cellCache.has(key)) return this._cellCache.get(key);

    const x0 = Math.round((col * FUEL_CELL_M) / this.pxM.x), x1 = Math.max(x0 + 1, Math.round(((col + 1) * FUEL_CELL_M) / this.pxM.x));
    // filas de imagen: y = 0 es el norte
    const yTop = Math.round((this.heightM - (row + 1) * FUEL_CELL_M) / this.pxM.y);
    const yBot = Math.max(yTop + 1, Math.round((this.heightM - row * FUEL_CELL_M) / this.pxM.y));
    const s = this._summarize(x0, x1, yTop, yBot);
    const result = s && {
      row, col,
      center: {
        lat: this.south + ((row + 0.5) * FUEL_CELL_M) / M_PER_DEG_LAT,
        lon: this.west + ((col + 0.5) * FUEL_CELL_M) / this.mPerDegLon,
      },
      ...s,
    };
    this._cellCache.set(key, result);
    return result;
  }

  /** Propensión de combustible (0-1) de la celda del punto, o null si está fuera del área. */
  fuelAt(lat, lon) {
    const c = this.cell(lat, lon);
    return c ? c.fuel : null;
  }

  /** Resumen de las clases dentro de un radio (km) — p. ej. los 2.5 km de una zona local. */
  around(lat, lon, radiusKm) {
    if (!this.contains(lat, lon)) return null;
    const cx = ((lon - this.west) / (this.east - this.west)) * this.w;
    const cy = ((this.north - lat) / (this.north - this.south)) * this.h;
    const rx = (radiusKm * 1000) / this.pxM.x, ry = (radiusKm * 1000) / this.pxM.y;
    const counts = new Array(WORLDCOVER_CLASSES.length).fill(0);
    let n = 0;
    for (let y = Math.max(0, Math.floor(cy - ry)); y <= Math.min(this.h - 1, Math.ceil(cy + ry)); y++) {
      for (let x = Math.max(0, Math.floor(cx - rx)); x <= Math.min(this.w - 1, Math.ceil(cx + rx)); x++) {
        if (((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 > 1) continue;
        const k = this.cls[y * this.w + x];
        if (k === FUEL_NO_DATA) continue;
        counts[k]++; n++;
      }
    }
    if (!n) return null;
    const fractions = counts.map((c, k) => ({ cls: WORLDCOVER_CLASSES[k], fraction: c / n }))
      .filter((f) => f.fraction > 0).sort((a, b) => b.fraction - a.fraction);
    return { fractions, dominant: fractions[0].cls, fuel: fractions.reduce((s, f) => s + f.fraction * f.cls.fuel, 0), pixels: n };
  }

  /** Todas las celdas como arreglo plano (para una simulación futura). */
  toCells() {
    const cols = Math.ceil(this.widthM / FUEL_CELL_M), rows = Math.ceil(this.heightM / FUEL_CELL_M);
    const out = [];
    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        const lat = this.south + ((r + 0.5) * FUEL_CELL_M) / M_PER_DEG_LAT;
        const lon = this.west + ((c + 0.5) * FUEL_CELL_M) / this.mPerDegLon;
        const cell = this.cell(lat, lon);
        if (cell) out.push(cell);
      }
    }
    return { rows, cols, cellM: FUEL_CELL_M, cells: out };
  }

  /** Clave estable del área (4 decimales), para no guardar dos veces la misma grilla. */
  areaKey() {
    return [this.west, this.south, this.east, this.north].map((v) => v.toFixed(4)).join(',');
  }

  /**
   * Carga compacta para el archivo histórico (server.py → SQLite):
   * celdas como [fila, col, lat, lon, clase dominante, propensión, {clase: %}].
   * La fila 0 es el borde SUR y la columna 0 el borde OESTE.
   */
  toArchivePayload() {
    const t = this.toCells();
    return {
      key: this.areaKey(),
      bounds: [this.west, this.south, this.east, this.north],
      cellM: FUEL_CELL_M,
      source: (this.meta && this.meta.source) || 'ESA WorldCover',
      cells: t.cells.map((c) => [
        c.row, c.col, +c.center.lat.toFixed(5), +c.center.lon.toFixed(5), c.dominant.key, +c.fuel.toFixed(3),
        Object.fromEntries(c.fractions.filter((f) => f.fraction >= 0.02).map((f) => [f.cls.key, Math.round(f.fraction * 100)])),
      ]),
    };
  }
}

/** "62 % matorral · 20 % urbano · 10 % pastizal" (solo las 3 mayores, ≥ 3 %). */
function fuelFractionsText(fractions, max = 3) {
  return fractions.filter((f) => f.fraction >= 0.03).slice(0, max)
    .map((f) => `${Math.round(f.fraction * 100)} % ${f.cls.label.split(' / ')[0].toLowerCase()}`).join(' · ');
}

if (typeof module !== 'undefined') module.exports = { FuelGrid, WORLDCOVER_CLASSES, fuelFractionsText, FUEL_CELL_M };
