#!/usr/bin/env node
/**
 * FireWatch · Mapa — calibración del simulador de propagación con un incendio real (FIRMS).
 *
 * Qué hace
 *   1. Toma las detecciones satelitales VIIRS (FIRMS) de un incendio pasado y las ordena por pasada del satélite.
 *   2. La PRIMERA pasada es el punto de partida (ignición, varios puntos). Las siguientes son lo que realmente pasó.
 *   3. Descarga UNA vez (y guarda en data/calibracion/<evento>/) la vegetación (ESA WorldCover), la altura del terreno
 *      y el clima por hora (Open-Meteo archivo) de esa zona y fecha.
 *   4. Corre el simulador del mapa (js/spread-model.js, el mismo código) con los parámetros actuales ("línea base"),
 *      mide qué tanto calza con cada pasada y busca los parámetros (r0, lbPerKmh, slopeKmhPerPct) que mejor calzan
 *      con las primeras pasadas. La ÚLTIMA pasada se guarda aparte para validar (no se usa para ajustar).
 *   5. Escribe data/calibracion/<evento>/resultado.json e informe.md.
 *
 * Uso (desde la carpeta firewatch-project-mapa; Node 18 o superior, sin instalar nada):
 *   node tools/calibrar_propagacion.js --firms "C:\proyectos\FireWatch\Datas\firms\fire_archive_SV-C2_806978.csv"
 *   node tools/calibrar_propagacion.js --firms ... --solo-datos      # solo descarga y guarda los datos
 *   node tools/calibrar_propagacion.js --firms ... --ventana 12      # horas de clima que se promedian (24 por defecto)
 *
 * LÍMITES HONESTOS (también quedan escritos en el informe)
 *   - Un solo incendio y 2-3 pasadas: sirve para ver el orden de magnitud y si el modelo exagera o se queda corto;
 *     NO es una calibración estadística. Se reporta qué tan bien quedan determinados los parámetros.
 *   - VIIRS mira el frente activo en el instante de cada pasada (≈ 2 por día), no el área quemada total. Lo "observado"
 *     es la unión de las detecciones hasta esa pasada, ensanchada --dilatar celdas (500 m por defecto) para cubrir los huecos.
 *   - El modelo supone viento constante y no tiene bomberos: contra un incendio combatido tenderá a sobreestimar con las horas.
 */
'use strict';
const fs = require('fs');
const path = require('path');
const zlib = require('zlib');

const ROOT = path.resolve(__dirname, '..');
const { FuelGrid, WORLDCOVER_CLASSES } = require(path.join(ROOT, 'js', 'fuel-grid.js'));
const M = require(path.join(ROOT, 'js', 'spread-model.js'));

// ------------------------------------------------------------------ eventos
const EVENTOS = {
  valparaiso2024: {
    nombre: 'Incendios de Valparaíso (Viña del Mar, Quilpué, Villa Alemana), 2-4 de febrero de 2024',
    bbox: [-71.76, -33.35, -71.12, -32.81], // oeste, sur, este, norte
    desde: '2024-02-02', hasta: '2024-02-05',
    inicio: '2024-02-02 1838',            // pasada de partida (fecha hora UTC, HHMM)
    clima: ['2024-02-02', '2024-02-04'],
  },
};

// ------------------------------------------------------------------ argumentos
const args = process.argv.slice(2);
const opt = (name, def) => { const i = args.indexOf('--' + name); return i < 0 ? def : (args[i + 1] && !args[i + 1].startsWith('--') ? args[i + 1] : true); };
const has = (name) => args.includes('--' + name);
const EVENT_ID = opt('evento', 'valparaiso2024');
const EV = EVENTOS[EVENT_ID];
if (!EV) { console.error('Evento desconocido. Disponibles: ' + Object.keys(EVENTOS).join(', ')); process.exit(1); }
const FIRMS_CSV = opt('firms', null);
const VENTANA_H = Number(opt('ventana', 24));
const DILATAR = Number(opt('dilatar', 2));
const MIN_PIX = Number(opt('min-pixeles', 20));
const CELL_M = 250;
const DIR = path.join(ROOT, 'data', 'calibracion', EVENT_ID);
fs.mkdirSync(DIR, { recursive: true });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const UA = { 'User-Agent': 'FireWatch-Mapa/1.0 (proyecto academico Duoc UC; calibracion con FIRMS)' };

// ------------------------------------------------------------------ FIRMS
function leerFirms(file) {
  const lines = fs.readFileSync(file, 'utf8').split(/\r?\n/).filter(Boolean);
  const head = lines[0].split(',');
  const ix = (n) => head.indexOf(n);
  const [iLat, iLon, iDate, iTime, iConf, iFrp] = ['latitude', 'longitude', 'acq_date', 'acq_time', 'confidence', 'frp'].map(ix);
  if ([iLat, iLon, iDate, iTime].some((i) => i < 0)) throw new Error('El CSV no parece de FIRMS (faltan columnas latitude/longitude/acq_date/acq_time).');
  const [w, s, e, n] = EV.bbox;
  const out = [];
  for (let i = 1; i < lines.length; i++) {
    const c = lines[i].split(',');
    const date = c[iDate];
    if (date < EV.desde || date > EV.hasta) continue;
    const lat = +c[iLat], lon = +c[iLon];
    if (lat < s || lat > n || lon < w || lon > e) continue;
    const conf = (c[iConf] || '').trim().toLowerCase();
    if (conf === 'l' || conf === 'low') continue; // baja confianza: ruido de sol y suelo caliente
    const hhmm = String(c[iTime]).padStart(4, '0');
    const t = Date.parse(`${date}T${hhmm.slice(0, 2)}:${hhmm.slice(2)}:00Z`);
    out.push({ lat, lon, t, key: `${date} ${hhmm}`, frp: iFrp >= 0 ? +c[iFrp] : 0 });
  }
  return out;
}

function pasadas(pix) {
  const by = new Map();
  pix.forEach((p) => { if (!by.has(p.key)) by.set(p.key, []); by.get(p.key).push(p); });
  // Las pasadas del mismo satélite llegan con segundos/minutos de diferencia: se agrupan en ventanas de 30 min.
  const sorted = [...by.entries()].map(([key, arr]) => ({ key, t: arr[0].t, pix: arr })).sort((a, b) => a.t - b.t);
  const out = [];
  sorted.forEach((p) => {
    const last = out[out.length - 1];
    if (last && p.t - last.t < 30 * 60000) { last.pix.push(...p.pix); } else out.push({ ...p });
  });
  return out;
}

// ------------------------------------------------------------------ PNG (mínimo: 8 bits, sin entrelazado)
function decodePNG(buf) {
  if (buf.readUInt32BE(0) !== 0x89504e47) throw new Error('la respuesta no es un PNG');
  let off = 8, w = 0, h = 0, depth = 0, ctype = 0, inter = 0, plte = null, trns = null;
  const idat = [];
  while (off < buf.length) {
    const len = buf.readUInt32BE(off), type = buf.toString('latin1', off + 4, off + 8), data = buf.subarray(off + 8, off + 8 + len);
    if (type === 'IHDR') { w = data.readUInt32BE(0); h = data.readUInt32BE(4); depth = data[8]; ctype = data[9]; inter = data[12]; }
    else if (type === 'PLTE') plte = data;
    else if (type === 'tRNS') trns = data;
    else if (type === 'IDAT') idat.push(data);
    else if (type === 'IEND') break;
    off += 12 + len;
  }
  if (depth !== 8 || inter !== 0) throw new Error(`PNG no soportado (profundidad ${depth}, entrelazado ${inter})`);
  const ch = { 0: 1, 2: 3, 3: 1, 4: 2, 6: 4 }[ctype];
  if (!ch) throw new Error('tipo de color PNG no soportado: ' + ctype);
  const raw = zlib.inflateSync(Buffer.concat(idat));
  const stride = w * ch, px = new Uint8Array(w * h * stride / w);
  let prev = new Uint8Array(stride), p = 0;
  for (let y = 0; y < h; y++) {
    const f = raw[p++]; const cur = new Uint8Array(raw.subarray(p, p + stride)); p += stride;
    for (let i = 0; i < stride; i++) {
      const a = i >= ch ? cur[i - ch] : 0, b = prev[i], c = i >= ch ? prev[i - ch] : 0;
      let v = cur[i];
      if (f === 1) v += a; else if (f === 2) v += b; else if (f === 3) v += (a + b) >> 1;
      else if (f === 4) { const pp = a + b - c, pa = Math.abs(pp - a), pb = Math.abs(pp - b), pc = Math.abs(pp - c); v += (pa <= pb && pa <= pc) ? a : (pb <= pc ? b : c); }
      cur[i] = v & 255;
    }
    px.set(cur, y * stride); prev = cur;
  }
  const rgba = new Uint8Array(w * h * 4);
  for (let i = 0; i < w * h; i++) {
    let r, g, b, a = 255;
    if (ctype === 6) { r = px[i * 4]; g = px[i * 4 + 1]; b = px[i * 4 + 2]; a = px[i * 4 + 3]; }
    else if (ctype === 2) { r = px[i * 3]; g = px[i * 3 + 1]; b = px[i * 3 + 2]; }
    else if (ctype === 3) { const k = px[i]; r = plte[k * 3]; g = plte[k * 3 + 1]; b = plte[k * 3 + 2]; a = trns && k < trns.length ? trns[k] : 255; }
    else if (ctype === 0) { r = g = b = px[i]; } else { r = g = b = px[i * 2]; a = px[i * 2 + 1]; }
    rgba[i * 4] = r; rgba[i * 4 + 1] = g; rgba[i * 4 + 2] = b; rgba[i * 4 + 3] = a;
  }
  return { w, h, rgba };
}

// ------------------------------------------------------------------ descargas con caché en disco
async function getJSON(url, init, intentos = 4) {
  let last;
  for (let a = 0; a < intentos; a++) {
    if (a) await sleep([0, 3000, 10000, 25000][a]);
    try {
      const r = await fetch(url, { headers: UA, ...init });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return await r.json();
    } catch (e) { last = e; console.warn(`  reintento ${a + 1}/${intentos - 1}: ${e.message}`); }
  }
  throw last;
}
async function getBuf(url, intentos = 3) {
  let last;
  for (let a = 0; a < intentos; a++) {
    if (a) await sleep(4000 * a);
    try {
      const r = await fetch(url, { headers: UA });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return Buffer.from(await r.arrayBuffer());
    } catch (e) { last = e; console.warn(`  reintento ${a + 1}/${intentos - 1}: ${e.message}`); }
  }
  throw last;
}
const cachePath = (n) => path.join(DIR, n);
const readCache = (n) => (fs.existsSync(cachePath(n)) ? JSON.parse(fs.readFileSync(cachePath(n), 'utf8')) : null);
const writeCache = (n, o) => fs.writeFileSync(cachePath(n), JSON.stringify(o));

/** Vegetación: WorldCover clasificado a las clases del proyecto, 50 m/px, cubriendo todo el bbox (puede cruzar tiles de 3°). */
async function datosVegetacion() {
  const c = readCache('vegetacion.json');
  if (c) return { cls: Uint8Array.from(Buffer.from(c.cls, 'base64')), w: c.w, h: c.h };
  console.log('Descargando vegetación (ESA WorldCover, Planetary Computer)…');
  const [W, S, E, N] = EV.bbox;
  const midLat = (S + N) / 2, mLon = 111320 * Math.cos(midLat * Math.PI / 180);
  const PX = 50;
  const w = Math.round(((E - W) * mLon) / PX), h = Math.round(((N - S) * 110574) / PX);
  const cls = new Uint8Array(w * h).fill(255);
  const encontrados = (await getJSON('https://planetarycomputer.microsoft.com/api/stac/v1/search', {
    method: 'POST', headers: { ...UA, 'Content-Type': 'application/json' },
    body: JSON.stringify({ collections: ['esa-worldcover'], bbox: EV.bbox, limit: 8 }),
  })).features || [];
  // WorldCover trae dos versiones (2020 v100 y 2021 v200) de cada tile: solo la 2021, la misma que usa el mapa.
  const items = encontrados.filter((it) => /2021_v200/.test(it.id));
  if (!items.length) throw new Error('STAC no devolvió tiles de WorldCover 2021 (v200); recibí: ' + encontrados.map((it) => it.id).join(', '));
  for (const it of items) {
    const [iw, is, ie, inn] = it.bbox;
    const bw = Math.max(W, iw), bs = Math.max(S, is), be = Math.min(E, ie), bn = Math.min(N, inn);
    if (be <= bw || bn <= bs) continue;
    const x0 = Math.round((bw - W) / (E - W) * w), x1 = Math.round((be - W) / (E - W) * w);
    const y0 = Math.round((N - bn) / (N - S) * h), y1 = Math.round((N - bs) / (N - S) * h);
    const tw = x1 - x0, th = y1 - y0;
    if (tw < 1 || th < 1) continue;
    console.log(`  tile ${it.id}: ${tw}x${th} px`);
    const url = `https://planetarycomputer.microsoft.com/api/data/v1/item/bbox/${bw},${bs},${be},${bn}/${tw}x${th}.png?collection=esa-worldcover&item=${it.id}&assets=map&colormap_name=esa-worldcover`;
    const png = decodePNG(await getBuf(url));
    for (let y = 0; y < Math.min(th, png.h); y++) {
      for (let x = 0; x < Math.min(tw, png.w); x++) {
        const i = (y * png.w + x) * 4;
        if (png.rgba[i + 3] === 0) continue;
        let best = 0, bd = Infinity;
        WORLDCOVER_CLASSES.forEach((k, n) => { const d = (k.esa[0] - png.rgba[i]) ** 2 + (k.esa[1] - png.rgba[i + 1]) ** 2 + (k.esa[2] - png.rgba[i + 2]) ** 2; if (d < bd) { bd = d; best = n; } });
        cls[(y0 + y) * w + x0 + x] = best;
      }
    }
  }
  const sinDato = cls.reduce((s, v) => s + (v === 255 ? 1 : 0), 0) / cls.length;
  console.log(`  vegetación lista (${(100 - sinDato * 100).toFixed(0)} % con dato)`);
  writeCache('vegetacion.json', { w, h, cls: Buffer.from(cls).toString('base64') });
  return { cls, w, h };
}

async function datosAltura(center) {
  const lat = M.fwElevationLattice(center, 30, 2.0);
  const c = readCache('altura.json');
  if (c && c.z.length === lat.coords.length) return { lat, z: Float32Array.from(c.z) };
  console.log(`Descargando altura del terreno (Open-Meteo, ${lat.coords.length} puntos)…`);
  const parts = [];
  for (let i = 0; i < lat.coords.length; i += 100) {
    const ch = lat.coords.slice(i, i + 100);
    const url = `https://api.open-meteo.com/v1/elevation?latitude=${ch.map((p) => p[0].toFixed(5)).join(',')}&longitude=${ch.map((p) => p[1].toFixed(5)).join(',')}`;
    parts.push((await getJSON(url)).elevation);
    process.stdout.write(`  ${Math.min(i + 100, lat.coords.length)}/${lat.coords.length}\r`);
    await sleep(800);
  }
  const z = parts.flat();
  writeCache('altura.json', { z });
  console.log('');
  return { lat, z: Float32Array.from(z) };
}

async function datosClima() {
  const c = readCache('clima.json');
  if (c) return c;
  console.log('Descargando clima por hora (Open-Meteo archivo, 9 puntos)…');
  const [W, S, E, N] = EV.bbox, pts = [];
  for (let a = 0; a < 3; a++) for (let b = 0; b < 3; b++) pts.push({ lat: S + (N - S) * (a / 2), lon: W + (E - W) * (b / 2) });
  const url = 'https://archive-api.open-meteo.com/v1/archive?' +
    `latitude=${pts.map((p) => p.lat.toFixed(3)).join(',')}&longitude=${pts.map((p) => p.lon.toFixed(3)).join(',')}` +
    `&start_date=${EV.clima[0]}&end_date=${EV.clima[1]}&hourly=temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m&wind_speed_unit=kmh&timezone=UTC`;
  const raw = await getJSON(url);
  const arr = Array.isArray(raw) ? raw : [raw];
  const out = { pts: pts.map((p, i) => ({ ...p, time: arr[i].hourly.time, temp: arr[i].hourly.temperature_2m, rh: arr[i].hourly.relative_humidity_2m, ws: arr[i].hourly.wind_speed_10m, wd: arr[i].hourly.wind_direction_10m })) };
  writeCache('clima.json', out);
  return out;
}

/** Clima constante: promedio (vectorial para el viento) de las horas [t0, t0+ventana] en cada punto; interpolación por distancia inversa. */
function climaPromedio(clima, t0, ventanaH) {
  const D2R = Math.PI / 180;
  const pts = clima.pts.map((p) => {
    let u = 0, v = 0, T = 0, R = 0, n = 0;
    p.time.forEach((ts, i) => {
      const t = Date.parse(ts + ':00Z');
      if (t < t0 - 3600000 || t > t0 + ventanaH * 3600000) return;
      if (![p.ws[i], p.wd[i], p.temp[i], p.rh[i]].every(Number.isFinite)) return;
      const to = (p.wd[i] + 180) * D2R; // hacia dónde sopla
      u += Math.sin(to) * p.ws[i]; v += Math.cos(to) * p.ws[i]; T += p.temp[i]; R += p.rh[i]; n++;
    });
    if (!n) throw new Error('No hay clima en la ventana pedida');
    u /= n; v /= n;
    const speed = Math.hypot(u, v), toDeg = ((Math.atan2(u, v) / D2R) + 360) % 360;
    return { lat: p.lat, lon: p.lon, speed, dirDeg: (toDeg + 180) % 360, temp: T / n, rh: R / n };
  });
  return {
    loaded: true,
    sample(lat, lon) {
      let sw = 0, su = 0, sv = 0, sT = 0, sR = 0;
      pts.forEach((p) => {
        const d = Math.hypot((p.lat - lat) * 110.57, (p.lon - lon) * 111.32 * Math.cos(lat * D2R)) + 0.5;
        const wgt = 1 / (d * d), to = (p.dirDeg + 180) * D2R;
        sw += wgt; su += wgt * Math.sin(to) * p.speed; sv += wgt * Math.cos(to) * p.speed; sT += wgt * p.temp; sR += wgt * p.rh;
      });
      const u = su / sw, v = sv / sw;
      return { speed: Math.hypot(u, v), dirDeg: (((Math.atan2(u, v) / D2R) + 360) % 360 + 180) % 360, temp: sT / sw, rh: sR / sw, scale: 'local' };
    },
    resumen: pts,
  };
}

// ------------------------------------------------------------------ evaluación
function celdasDe(pix, geo, rows, cols) {
  const set = new Set();
  pix.forEach((p) => {
    const r = Math.floor((p.lat - geo.lat0) * 110574 / CELL_M), c = Math.floor((p.lon - geo.lon0) * geo.mLon / CELL_M);
    if (r >= 0 && r < rows && c >= 0 && c < cols) set.add(r * cols + c);
  });
  return set;
}
function dilatar(set, rows, cols, k) {
  if (!k) return new Set(set);
  const out = new Set();
  set.forEach((i) => {
    const r = (i / cols) | 0, c = i - r * cols;
    for (let dr = -k; dr <= k; dr++) for (let dc = -k; dc <= k; dc++) {
      const nr = r + dr, nc = c + dc;
      if (nr >= 0 && nr < rows && nc >= 0 && nc < cols) out.add(nr * cols + nc);
    }
  });
  return out;
}

function evaluar(res, objetivos) {
  // objetivos: [{ min, obsExacto:Set, obsAncho:Set }]
  return objetivos.map((o) => {
    const arr = res.arrival;
    let pred = 0, inter = 0, hitExacto = 0;
    for (let i = 0; i < arr.length; i++) if (arr[i] <= o.min) { pred++; if (o.obsAncho.has(i)) inter++; }
    o.obsExacto.forEach((i) => { if (arr[i] <= o.min) hitExacto++; });
    const uni = pred + o.obsAncho.size - inter;
    return {
      iou: uni ? inter / uni : 0,
      recall: o.obsExacto.size ? hitExacto / o.obsExacto.size : 0,
      precision: pred ? inter / pred : 0,
      predHa: pred * CELL_M * CELL_M / 1e4,
      obsHa: o.obsAncho.size * CELL_M * CELL_M / 1e4,
      ratio: o.obsAncho.size ? pred / o.obsAncho.size : 0,
    };
  });
}

const fmt = (x, d = 2) => (Number.isFinite(x) ? x.toFixed(d) : '—');

// ------------------------------------------------------------------ principal
(async () => {
  if (!FIRMS_CSV) { console.error('Falta --firms <ruta al CSV de FIRMS VIIRS>.'); process.exit(1); }
  const [W, S, E, N] = EV.bbox;
  const center = { lat: (S + N) / 2, lon: (W + E) / 2 };
  console.log(`Evento: ${EV.nombre}`);

  const veg = await datosVegetacion();
  const alt = await datosAltura(center);
  const clima = await datosClima();
  if (has('solo-datos')) { console.log('Datos guardados en ' + DIR); return; }

  const pix = leerFirms(FIRMS_CSV);
  const ps = pasadas(pix);
  const iStart = ps.findIndex((p) => p.key.slice(0, 15) === EV.inicio || p.key === EV.inicio);
  if (iStart < 0) throw new Error(`No encuentro la pasada de partida ${EV.inicio}. Pasadas: ${ps.slice(0, 12).map((p) => p.key).join(', ')}`);
  const t0 = ps[iStart].t;
  const siguientes = ps.slice(iStart + 1).filter((p) => p.pix.length >= MIN_PIX);
  if (siguientes.length < 2) throw new Error('Hay menos de 2 pasadas posteriores con suficientes detecciones.');
  console.log(`Pasada de partida: ${ps[iStart].key} UTC (${ps[iStart].pix.length} detecciones). Pasadas siguientes: ` +
    siguientes.map((p) => `${p.key} (+${((p.t - t0) / 3600000).toFixed(1)} h, ${p.pix.length})`).join(' · '));

  // Entrada del simulador con los mismos datos y el mismo código que usa el mapa.
  const fuelGrid = new FuelGrid(veg.cls, veg.w, veg.h, [[W, S], [E, N]], { source: 'ESA WorldCover' });
  const weather = climaPromedio(clima, t0, VENTANA_H);
  const elevAt = M.fwElevationFn(alt.lat, alt.z);
  const built = M.fwBuildInput({ center, radiusKm: 30, cellM: CELL_M, fuelGrid, weatherGrid: weather, elevAt });
  const { rows, cols } = built.input;
  const ign = [...celdasDe(ps[iStart].pix, built.geo, rows, cols)].map((i) => ({ row: (i / cols) | 0, col: i % cols }));
  built.input.ignitions = ign;
  console.log(`Ventana ${cols}x${rows} celdas de ${CELL_M} m · ${ign.length} celdas de ignición · clima promedio de ${VENTANA_H} h: ` +
    `viento ${fmt(built.summary.windKmh, 0)} km/h desde ${fmt(built.summary.windFromDeg, 0)}° · ${fmt(built.summary.tempC, 0)} °C · HR ${fmt(built.summary.rh, 0)} % · vegetación en ${fmt(built.summary.fuelCoverage * 100, 0)} % de la zona`);

  // Objetivos: unión acumulada de detecciones hasta cada pasada (incluida la de partida).
  let acum = celdasDe(ps[iStart].pix, built.geo, rows, cols);
  const objetivos = siguientes.map((p) => {
    celdasDe(p.pix, built.geo, rows, cols).forEach((i) => acum.add(i));
    return { key: p.key, min: (p.t - t0) / 60000, obsExacto: new Set(acum), obsAncho: dilatar(acum, rows, cols, DILATAR) };
  });
  const nCal = objetivos.length - 1; // la última pasada se reserva para validar
  const idxCal = objetivos.slice(0, nCal).map((_, i) => i), idxVal = [objetivos.length - 1];
  const tMax = Math.ceil(objetivos[objetivos.length - 1].min + 60);

  const correr = (p) => M.fwSimulate(built.input, { ...p, tMaxMin: tMax });
  const media = (ev, idx) => idx.reduce((s, i) => s + ev[i].iou, 0) / idx.length;

  // Línea base: parámetros actuales del mapa.
  const base = M.SPREAD_DEFAULTS;
  const evBase = evaluar(correr({}), objetivos);
  console.log('\nLínea base (parámetros actuales): r0=' + base.r0 + ' lbPerKmh=' + base.lbPerKmh + ' slopeKmhPerPct=' + base.slopeKmhPerPct);
  evBase.forEach((e, i) => console.log(`  ${objetivos[i].key} (+${fmt(objetivos[i].min / 60, 1)} h): IoU ${fmt(e.iou)} · recall ${fmt(e.recall)} · simulado ${fmt(e.predHa, 0)} ha vs observado ${fmt(e.obsHa, 0)} ha`));

  // Búsqueda en malla.
  const mallaR0 = [0.5, 1, 1.5, 2, 3, 4, 5, 6, 8, 10, 12, 15, 20], mallaLB = [0.01, 0.02, 0.04, 0.06, 0.08, 0.12, 0.16], mallaSl = [0, 0.25, 0.5, 1];
  console.log(`\nBuscando parámetros (${mallaR0.length * mallaLB.length * mallaSl.length} combinaciones)…`);
  const filas = [];
  for (const r0 of mallaR0) for (const lbPerKmh of mallaLB) for (const slopeKmhPerPct of mallaSl) {
    const ev = evaluar(correr({ r0, lbPerKmh, slopeKmhPerPct }), objetivos);
    filas.push({ r0, lbPerKmh, slopeKmhPerPct, cal: media(ev, idxCal), val: media(ev, idxVal), ev });
  }
  filas.sort((a, b) => b.cal - a.cal);
  const mejor = filas[0];
  const cercanas = filas.filter((f) => f.cal >= mejor.cal - 0.02);
  const rango = (k) => [Math.min(...cercanas.map((f) => f[k])), Math.max(...cercanas.map((f) => f[k]))];

  console.log(`\nMejor ajuste con las pasadas de calibración: r0=${mejor.r0} lbPerKmh=${mejor.lbPerKmh} slopeKmhPerPct=${mejor.slopeKmhPerPct}  (IoU ${fmt(mejor.cal)})`);
  mejor.ev.forEach((e, i) => console.log(`  ${objetivos[i].key} (+${fmt(objetivos[i].min / 60, 1)} h)${idxVal.includes(i) ? ' [VALIDACIÓN]' : ''}: IoU ${fmt(e.iou)} · recall ${fmt(e.recall)} · simulado ${fmt(e.predHa, 0)} ha vs observado ${fmt(e.obsHa, 0)} ha`));
  console.log(`\nCombinaciones dentro de 0,02 de IoU del mejor: ${cercanas.length}. Rangos: r0 ${rango('r0').join('–')}, lbPerKmh ${rango('lbPerKmh').join('–')}, pendiente ${rango('slopeKmhPerPct').join('–')}`);
  const enBorde = [['r0', mallaR0], ['lbPerKmh', mallaLB], ['slopeKmhPerPct', mallaSl]].filter(([k, m]) => mejor[k] === m[0] || mejor[k] === m[m.length - 1]).map(([k]) => k);
  if (enBorde.length) console.log(`→ ATENCIÓN: el mejor valor de ${enBorde.join(', ')} cae en el borde de la malla; el óptimo real podría estar más allá (no confiar en ese número).`);
  console.log(cercanas.length > 12 ? '→ Los parámetros están poco determinados con un solo incendio: mejor reportar un RANGO que un número exacto.' : '→ Los parámetros quedan razonablemente acotados por este incendio.');

  const salida = {
    evento: EVENT_ID, nombre: EV.nombre, ventanaClimaH: VENTANA_H, dilatarCeldas: DILATAR, celdaM: CELL_M,
    clima: built.summary, pasadas: objetivos.map((o) => ({ pasada: o.key, horas: +(o.min / 60).toFixed(2), celdasObservadas: o.obsExacto.size })),
    lineaBase: { params: { r0: base.r0, lbPerKmh: base.lbPerKmh, slopeKmhPerPct: base.slopeKmhPerPct }, resultados: evBase },
    mejor: { params: { r0: mejor.r0, lbPerKmh: mejor.lbPerKmh, slopeKmhPerPct: mejor.slopeKmhPerPct }, iouCalibracion: mejor.cal, resultados: mejor.ev, validacion: idxVal },
    cercanas: cercanas.slice(0, 40).map((f) => ({ r0: f.r0, lbPerKmh: f.lbPerKmh, slopeKmhPerPct: f.slopeKmhPerPct, iouCal: +f.cal.toFixed(3), iouVal: +f.val.toFixed(3) })),
    nCercanas: cercanas.length, rangos: { r0: rango('r0'), lbPerKmh: rango('lbPerKmh'), slopeKmhPerPct: rango('slopeKmhPerPct') },
  };
  fs.writeFileSync(cachePath('resultado.json'), JSON.stringify(salida, null, 2));

  const fila = (n, e, k) => `| ${n} | +${fmt(objetivos[k].min / 60, 1)} h | ${fmt(e.iou)} | ${fmt(e.recall)} | ${fmt(e.predHa, 0)} | ${fmt(e.obsHa, 0)} |`;
  const md = `# Calibración del simulador de propagación — ${EV.nombre}

Generado por \`tools/calibrar_propagacion.js\`. Datos: FIRMS VIIRS (pasada de partida ${EV.inicio} UTC), ESA WorldCover 2021, DEM y clima por hora de Open-Meteo.

Clima supuesto (promedio de ${VENTANA_H} h desde la partida): viento ${fmt(built.summary.windKmh, 0)} km/h desde ${fmt(built.summary.windFromDeg, 0)}°, ${fmt(built.summary.tempC, 0)} °C, humedad ${fmt(built.summary.rh, 0)} %.

## Línea base (parámetros actuales del mapa)

Parámetros: r0=${base.r0}, lbPerKmh=${base.lbPerKmh}, slopeKmhPerPct=${base.slopeKmhPerPct}.

| Pasada | Tiempo | IoU | Recall | Simulado (ha) | Observado (ha) |
|---|---|---|---|---|---|
${evBase.map((e, i) => fila(objetivos[i].key, e, i)).join('\n')}

## Mejor ajuste con las pasadas de calibración

Parámetros: r0=${mejor.r0}, lbPerKmh=${mejor.lbPerKmh}, slopeKmhPerPct=${mejor.slopeKmhPerPct}. La última pasada se reservó para validar.

| Pasada | Tiempo | IoU | Recall | Simulado (ha) | Observado (ha) |
|---|---|---|---|---|---|
${mejor.ev.map((e, i) => fila(objetivos[i].key + (idxVal.includes(i) ? ' (validación)' : ''), e, i)).join('\n')}

${cercanas.length} de ${filas.length} combinaciones quedan a menos de 0,02 de IoU del mejor. Rangos: r0 ${rango('r0').join('–')}, lbPerKmh ${rango('lbPerKmh').join('–')}, pendiente ${rango('slopeKmhPerPct').join('–')}.

## Cómo leerlo

- IoU: solapamiento entre el área simulada y el área observada (1 = idéntica). Recall: qué parte de las celdas con detección real quedó dentro de lo simulado.
- Lo observado es la unión de detecciones satelitales hasta esa pasada, ensanchada ${DILATAR} celda(s) de ${CELL_M} m. VIIRS ve el frente activo en el instante de la pasada, no el área quemada total.
- Un solo incendio y pocas pasadas: es una verificación del orden de magnitud, no una calibración estadística. El modelo no incluye bomberos, así que con las horas tiende a sobreestimar.
`;
  fs.writeFileSync(cachePath('informe.md'), md);
  console.log('\nResultados guardados en ' + DIR + ' (resultado.json, informe.md)');
})().catch((e) => { console.error('\nError: ' + e.message); process.exit(1); });
