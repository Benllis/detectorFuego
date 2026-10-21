// ============================================================
// FireWatch · Mapa 3D — consulta de un punto ("¿qué hay aquí?")
//
// Con el modo consulta activo, un clic en el mapa muestra los datos de ESE
// punto juntos: altura, pendiente y exposición, vegetación de su celda de
// 250 m y propensión de combustible, clima (actual o del escenario) y el
// riesgo de incendio meteorológico y con combustible. Es la vía para leer
// la grilla de combustible y la base de datos que usará una simulación.
//
// Altura: servicio de elevación de Open-Meteo (DEM Copernicus 90 m), una
// sola petición con 5 puntos (centro y ±200 m) para calcular pendiente.
// Verificado en vivo (28-09-2026): HTTP 200, ~0.25 s. Si falla, el popup
// sale igual, sin altura ni pendiente.
// ============================================================

const INSPECT_STEP_M = 200;

async function fetchElevations(lat, lon) {
  const dLat = INSPECT_STEP_M / 110574;
  const dLon = INSPECT_STEP_M / (111320 * Math.cos(lat * Math.PI / 180));
  const lats = [lat, lat + dLat, lat - dLat, lat, lat];
  const lons = [lon, lon, lon, lon + dLon, lon - dLon];
  const url = 'https://api.open-meteo.com/v1/elevation' +
    `?latitude=${lats.map((x) => x.toFixed(5)).join(',')}&longitude=${lons.map((x) => x.toFixed(5)).join(',')}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const z = (await res.json()).elevation;
  if (!Array.isArray(z) || z.length !== 5 || z.some((v) => typeof v !== 'number')) throw new Error('respuesta inesperada');
  return z; // [centro, norte, sur, este, oeste]
}

/** Pendiente (%, °) y exposición (hacia dónde mira la ladera, grados desde el norte). */
function slopeAspect(z, stepM) {
  const dzdx = (z[3] - z[4]) / (2 * stepM); // este - oeste
  const dzdy = (z[1] - z[2]) / (2 * stepM); // norte - sur
  const rise = Math.hypot(dzdx, dzdy);
  const deg = Math.atan(rise) * 180 / Math.PI;
  const aspect = rise < 0.01 ? null : (Math.atan2(-dzdx, -dzdy) * 180 / Math.PI + 360) % 360; // cuesta abajo
  return { percent: rise * 100, deg, aspect };
}

const fmtCoord = (lat, lon) => `${Math.abs(lat).toFixed(4)}° ${lat < 0 ? 'S' : 'N'}, ${Math.abs(lon).toFixed(4)}° ${lon < 0 ? 'O' : 'E'}`;

async function buildInspectorHTML(lat, lon, weatherGrid, scenarioLabel) {
  const rows = [`<div><b>📍 Punto consultado</b></div><div class="fw-dim">${fmtCoord(lat, lon)}</div>`];

  // Altura y pendiente
  try {
    const z = await fetchElevations(lat, lon);
    const sa = slopeAspect(z, INSPECT_STEP_M);
    rows.push(`<div><b>Altura:</b> ${z[0]} m · <b>Pendiente:</b> ${sa.percent.toFixed(0)} % (${sa.deg.toFixed(0)}°)` +
      `${sa.aspect === null ? ' · plano' : ` · mira al ${degToCompass(sa.aspect)}`}</div>`);
  } catch (err) {
    rows.push('<div class="fw-dim">Altura y pendiente no disponibles.</div>');
  }

  // Vegetación / combustible
  // Fuera de Santiago la grilla de la zona se baja bajo demanda (zones.js).
  if (!fuelGridFor(lat, lon) && typeof zonesEnsureFuel === 'function') { try { await zonesEnsureFuel(lat, lon, 0.3); } catch (_) { /* sin datos */ } }
  const fg = fuelGridFor(lat, lon);
  const cell = fg ? fg.cell(lat, lon) : null;
  if (cell) {
    rows.push(`<div><b>Vegetación</b> <span class="fw-dim">(celda de ${FUEL_CELL_M} m)</span><b>:</b> ${fuelFractionsText(cell.fractions, 3)}</div>` +
      `<div><b>Propensión de combustible:</b> ${cell.fuel.toFixed(2)} / 1 · ${cell.dominant.label} (${cell.dominant.risk.toLowerCase()})</div>`);
  } else {
    rows.push('<div class="fw-dim">Sin datos de vegetación en este punto (fuera del área cargada).</div>');
  }

  // Clima y riesgo
  const w = weatherGrid ? weatherGrid.sample(lat, lon) : null;
  if (w) {
    const src = scenarioLabel ? `histórico (${scenarioLabel})` : 'actual';
    rows.push(`<div><b>Clima ${src}:</b> ${w.temp.toFixed(1)} °C` +
      `${w.rh !== null ? ` · humedad ${w.rh.toFixed(0)} %` : ''} · viento ${w.speed.toFixed(0)} km/h desde el ${degToCompass(w.dirDeg)}</div>`);
    const risk = fireRiskIndex(w.temp, w.rh, w.speed);
    if (risk !== null) {
      let line = `<div><b>Riesgo meteorológico:</b> ${risk.toFixed(0)}/100 · ${fireRiskClass(risk).label}`;
      if (cell) {
        const comb = combinedFireRisk(risk, cell.fuel);
        line += `<br><b>Riesgo con combustible:</b> ${comb.toFixed(0)}/100 · ${fireRiskClass(comb).label}`;
      }
      rows.push(line + '</div>');
    }
  } else {
    rows.push('<div class="fw-dim">Sin clima para este punto (fuera de la grilla de datos).</div>');
  }
  rows.push(routeButtonHTML(lat.toFixed(5), lon.toFixed(5)));
  rows.push(spreadButtonHTML(lat.toFixed(5), lon.toFixed(5)));
  return `<div class="fw-zone">${rows.join('')}</div>`;
}

/**
 * Activa la consulta por clic. `ctx.enabled()` dice si el modo está encendido;
 * `ctx.weather()` devuelve { grid, label } del clima activo (actual o escenario).
 */
function initInspector(map, ctx) {
  let popup = null;
  map.on('click', async (e) => {
    if (!ctx.enabled()) return;
    // Si el clic cae sobre un elemento con su propio popup (grifo, foco, cámara…), no se duplica.
    const own = ['fire-pixels-point', 'hydrants-point', 'fire-stations-point', 'water-sources-point',
      'water-rivers-line', 'chile-weather-point', 'camera-sensor-point',
      'zone-hydrants-point', 'zone-fire-stations-point', 'zone-water-sources-point', 'zone-water-rivers-line'].filter((id) => map.getLayer(id));
    if (own.length && map.queryRenderedFeatures(e.point, { layers: own }).length) return;

    const { lat, lng } = e.lngLat;
    if (popup) popup.remove();
    popup = new maplibregl.Popup({ offset: 10, maxWidth: '300px', className: 'fw-zone-popup fw-inspect' })
      .setLngLat([lng, lat]).setHTML('<div class="fw-zone fw-dim">Consultando el punto…</div>').addTo(map);
    const { grid, label } = ctx.weather();
    const html = await buildInspectorHTML(lat, lng, grid, label);
    if (popup) popup.setHTML(html);
  });
  map.on('mousemove', (e) => { map.getCanvas().style.cursor = ctx.enabled() ? 'crosshair' : ''; });
  return { close() { if (popup) { popup.remove(); popup = null; } } };
}
