// ============================================================
// FireWatch · calibración del índice de riesgo de incendio
//
// Reproduce la comprobación de METODOLOGIA.md §5b. Para cada incendio real
// de la lista baja el clima horario (archivo de Open-Meteo) de TODA su
// temporada (dic → mar, o hasta abril), calcula el índice en las tardes
// (16-21 UTC ≈ 13-18 h de Chile) y dice en qué percentil de esa temporada
// cae el día del incendio, más la distribución por categoría.
//
// Uso (Node 18 o superior, con internet):
//     node tools/calibrar_indice.js
//
// Qué NO demuestra: solo hay días CON incendio; sin días sin incendio no se
// puede medir cuántas falsas alarmas da el índice. Es una comprobación de
// coherencia, no una validación estadística.
// ============================================================

// Debe coincidir con js/heat-layer.js
const WEIGHTS = { humidity: 0.5, temp: 0.3, wind: 0.2 };
const CLASSES = [['Bajo', 20], ['Moderado', 40], ['Alto', 60], ['Muy alto', 80], ['Extremo', 100]];
const clamp01 = (x) => Math.min(1, Math.max(0, x));
function fireRiskIndex(t, rh, wind, w = WEIGHTS) {
  return 100 * (w.humidity * clamp01((60 - rh) / 50) + w.temp * clamp01((t - 5) / 30) + w.wind * clamp01(wind / 40));
}

// Coordenadas aproximadas (el modelo de Open-Meteo tiene ~10 km de resolución).
const EVENTS = [
  { name: 'Viña del Mar / Valparaíso, 2 feb 2024', lat: -33.07, lon: -71.5, day: '2024-02-02', from: '2023-12-01', to: '2024-03-31' },
  { name: 'Viña del Mar / Valparaíso, 3 feb 2024', lat: -33.07, lon: -71.5, day: '2024-02-03', from: '2023-12-01', to: '2024-03-31' },
  { name: 'Santa Juana (Biobío), 3 feb 2023', lat: -37.17, lon: -72.94, day: '2023-02-03', from: '2022-12-01', to: '2023-03-31' },
  { name: 'Constitución / Santa Olga (Maule), 25 ene 2017', lat: -35.30, lon: -72.20, day: '2017-01-25', from: '2016-12-01', to: '2017-03-31' },
  { name: 'Valparaíso, La Pólvora, 12 abr 2014', lat: -33.06, lon: -71.63, day: '2014-04-12', from: '2013-12-01', to: '2014-04-30' },
];

const mean = (a) => a.reduce((x, y) => x + y, 0) / a.length;

function analyze(hourly, day, fetchedElevation) {
  const rows = [];
  hourly.time.forEach((tm, i) => {
    const hr = Number(tm.slice(11, 13));
    const t = hourly.temperature_2m[i], rh = hourly.relative_humidity_2m[i], w = hourly.wind_speed_10m[i];
    if (hr >= 16 && hr <= 21 && t != null && rh != null && w != null) rows.push({ tm, idx: fireRiskIndex(t, rh, w), t, rh, w });
  });
  const ev = rows.filter((r) => r.tm.startsWith(day));
  const evMean = mean(ev.map((r) => r.idx));
  const pct = (100 * rows.filter((r) => r.idx <= evMean).length) / rows.length;
  const dist = CLASSES.map(([, max], k) => Math.round((100 * rows.filter((r) => r.idx <= max && r.idx > (k ? CLASSES[k - 1][1] : -1)).length) / rows.length));
  return { evMean, pct, dist, evT: mean(ev.map((r) => r.t)), evRH: mean(ev.map((r) => r.rh)), evW: mean(ev.map((r) => r.w)), elev: fetchedElevation, n: rows.length };
}

async function main() {
  const vars = 'temperature_2m,relative_humidity_2m,wind_speed_10m';
  console.log('Evento | índice tarde | categoría | percentil en su temporada | T / HR / viento | % de tardes de la temporada por categoría (Bajo/Mod/Alto/M.alto/Extremo)');
  for (const e of EVENTS) {
    const url = `https://archive-api.open-meteo.com/v1/archive?latitude=${e.lat}&longitude=${e.lon}` +
      `&start_date=${e.from}&end_date=${e.to}&hourly=${vars}&wind_speed_unit=kmh&timezone=UTC`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`${e.name}: HTTP ${res.status}`);
    const j = await res.json();
    const r = analyze(j.hourly, e.day, j.elevation);
    const cls = CLASSES.find(([, max]) => r.evMean <= max)[0];
    console.log(`${e.name} | ${r.evMean.toFixed(0)} | ${cls} | p${r.pct.toFixed(0)} | ${r.evT.toFixed(0)} °C / ${r.evRH.toFixed(0)} % / ${r.evW.toFixed(0)} km/h | ${r.dist.join('/')}`);
  }
}

if (require.main === module) main().catch((err) => { console.error(err.message); process.exit(1); });
module.exports = { fireRiskIndex, analyze };
