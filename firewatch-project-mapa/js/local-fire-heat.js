// ============================================================
// FireWatch · Mapa 3D — zonas de calor locales cerca de focos reales
//
// En vez de pedirle al usuario que marque a mano "el punto de inicio del
// fuego", se usan los focos que YA detecta NASA FIRMS como disparador:
// por cada foco detectado se genera una zona de 2.5 km de radio centrada
// ahí, con el clima real de ese punto — más detalle que la grilla de toda
// la ciudad.
//
// v5: también genera zonas en replay / día anterior, con el clima histórico
//     de la hora del foco (archivo de Open-Meteo); prioriza los focos más intensos.
// v4: comparte interruptor con los focos FIRMS (una sola capa "Focos de calor").
// v3 — legible:
//  - La zona es un CÍRCULO de borde difuso (antes un cuadrado duro) con un
//    contorno naranja punteado que marca el radio exacto.
//  - Se dibuja DEBAJO de los puntos FIRMS: antes los tapaba y no se veía
//    ni el foco ni su descripción.
//  - Cada zona lleva un rótulo fijo con lo esencial (detecciones, hora,
//    temperatura, viento, humedad) y un botón "Acercar".
//  - La escala de color es relativa a la propia zona (en 2.5 km la
//    temperatura varía poco); el rótulo dice el rango real en °C.
//
// Si NO hay ningún foco activo (lo normal la mayoría de los días), no se
// genera nada — es el comportamiento correcto, no un error.
//
// En modo REPLAY / día anterior el clima NO es el de hoy: se pide al archivo
// histórico de Open-Meteo para la hora del foco más intenso de cada zona, y
// el rótulo lo dice. (Antes no se generaban zonas para no mostrar el clima
// de hoy como si fuera el del incendio.)
//
// Reusa WeatherGrid (weather-grid.js), TemperatureHeatLayer
// (heat-layer.js) y buildCircleGeoJSON (map-init.js), que deben cargarse
// ANTES que este script.
// ============================================================

const LOCAL_HEAT_RADIUS_KM = 2.5;
const LOCAL_HEAT_GRID_SIZE = 6;      // 6x6 = 36 puntos (una sola petición por zona)
const LOCAL_HEAT_MAX_ZONES = 5;      // tope de zonas simultáneas, por si hay muchos focos
const LOCAL_HEAT_CLUSTER_KM = 1.5;   // focos más cercanos que esto cuentan como "el mismo" incendio
const LOCAL_ZONE_BEFORE_LAYER = 'fire-pixels-glow'; // las zonas se dibujan debajo de los focos

let localHeatZones = []; // { grid, layer, center, outlineId, outlineSourceId, popup }
let localZonesVisible = true;
let localZonesMap = null;

/** Agrupa focos FIRMS muy cercanos entre sí (mismo incendio, varios pixeles) en un solo punto. */
function clusterFirePixels(pixels, clusterKm) {
  const latKmPerDeg = 111.32;
  const clusters = [];
  pixels.forEach((p) => {
    const lonKmPerDeg = 111.32 * Math.cos(p.lat * Math.PI / 180);
    const near = clusters.find((c) => {
      const dLatKm = (c.lat - p.lat) * latKmPerDeg;
      const dLonKm = (c.lon - p.lon) * lonKmPerDeg;
      return Math.hypot(dLatKm, dLonKm) < clusterKm;
    });
    if (near) { near.count++; near.pixels.push(p); }
    else clusters.push({ lat: p.lat, lon: p.lon, count: 1, pixels: [p] });
  });
  return clusters;
}

/** Grados (de dónde viene el viento) → punto cardinal en español. */
function degToCompass(deg) {
  const names = ['N', 'NE', 'E', 'SE', 'S', 'SO', 'O', 'NO'];
  return names[Math.round((((deg % 360) + 360) % 360) / 45) % 8];
}

function localZoneSummaryHTML(cluster, grid) {
  const temps = grid.points.map((p) => p.temp).filter((t) => t !== null);
  const tMin = Math.min(...temps), tMax = Math.max(...temps);
  const c = grid.sample(cluster.lat, cluster.lon);

  const latest = cluster.pixels.reduce((a, p) => ((p.ts || 0) > (a.ts || 0) ? p : a), cluster.pixels[0]);
  const confRank = { l: 1, n: 2, h: 3 };
  const best = cluster.pixels.reduce((a, p) => (confRank[p.confidence] > confRank[a.confidence] ? p : a), cluster.pixels[0]);
  const confName = { h: 'alta', n: 'nominal', l: 'baja' }[best.confidence] || best.confidence;
  const frp = cluster.pixels.reduce((s, p) => s + (Number.isFinite(p.frp) ? p.frp : 0), 0);

  const risk = c ? fireRiskIndex(c.temp, c.rh, c.speed) : null;
  const riskCls = risk !== null ? fireRiskClass(risk) : null;
  const fg = fuelGridFor(cluster.lat, cluster.lon);
  const veg = fg ? fg.around(cluster.lat, cluster.lon, LOCAL_HEAT_RADIUS_KM) : null;
  const cellFuel = fg ? fg.fuelAt(cluster.lat, cluster.lon) : null;
  const combined = risk !== null && cellFuel !== null ? combinedFireRisk(risk, cellFuel) : null;
  return `
    <div class="fw-zone">
      <b>🔥 Zona de calor local</b> <span class="fw-dim">(radio ${LOCAL_HEAT_RADIUS_KM} km)</span>
      ${riskCls ? `<div><b>Riesgo de incendio:</b> ${risk.toFixed(0)}/100 · ${riskCls.label}${combined !== null ? ` <span class="fw-dim">(con vegetación: ${combined.toFixed(0)}/100)</span>` : ''}</div>` : ''}
      ${veg ? `<div class="fw-detail"><b>Vegetación en ${LOCAL_HEAT_RADIUS_KM} km:</b> ${fuelFractionsText(veg.fractions, 3)} · propensión ${veg.fuel.toFixed(2)}</div>` : ''}
      <div class="fw-detail"><b>Foco NASA FIRMS:</b> ${cluster.count} detección(es), confianza ${confName}${frp ? `, ${frp.toFixed(1)} MW` : ''}</div>
      <div class="fw-detail"><b>Última detección:</b> ${formatFireTimeLocal(latest.ts)}</div>
      <div class="fw-detail"><b>Temperatura en la zona:</b> ${tMin.toFixed(1)} – ${tMax.toFixed(1)} °C</div>
      ${c ? `<div class="fw-detail"><b>Viento:</b> ${c.speed.toFixed(0)} km/h desde el ${degToCompass(c.dirDeg)}${c.rh !== null ? ` · <b>Humedad:</b> ${c.rh.toFixed(0)} %` : ''}</div>` : ''}
      ${grid.at ? `<div class="fw-dim fw-detail">Clima histórico de las ${formatFireTimeLocal(grid.at.getTime())} (Open-Meteo, hora del foco).</div>` : ''}
      <div class="fw-dim fw-detail">Más rojo = más caliente dentro de la zona.</div>
      <button class="fw-zoom" type="button">Acercar al foco</button>
      ${routeButtonHTML(cluster.lat, cluster.lon, '🚒 Ruta de bomberos')}
    </div>`;
}

const LOCAL_ZONE_COMPACT_ZOOM = 11.5; // por debajo de este zoom, el rótulo se reduce

/** A poca distancia los rótulos se apilan: se muestra solo lo esencial. */
function updateLocalZonePopupDensity() {
  if (!localZonesMap) return;
  const compact = localZonesMap.getZoom() < LOCAL_ZONE_COMPACT_ZOOM;
  localHeatZones.forEach((z) => {
    const el = z.popup && z.popup.getElement();
    if (el) el.classList.toggle('fw-compact', compact);
  });
}

function removeLocalZone(map, z) {
  z.layer.destroy();
  if (map.getLayer(z.outlineId)) map.removeLayer(z.outlineId);
  if (map.getSource(z.outlineSourceId)) map.removeSource(z.outlineSourceId);
  if (z.popup) z.popup.remove();
}

async function addLocalFireHeatZones(map) {
  localZonesMap = map;
  localHeatZones.forEach((z) => removeLocalZone(map, z));
  localHeatZones = [];

  // En replay / día anterior el clima se pide al ARCHIVO histórico, a la hora del foco
  // (antes no se generaban zonas porque el clima disponible era el de hoy).
  const historical = !!FIRE_STATE.replay;
  if (!firePixels || !firePixels.length) {
    console.info('[LocalFireHeat] Sin focos activos — no se genera ninguna zona local (no es un error).');
    return;
  }

  const frpOf = (c) => c.pixels.reduce((s, p) => s + (Number.isFinite(p.frp) ? p.frp : 0), 0);
  const clusters = clusterFirePixels(firePixels, LOCAL_HEAT_CLUSTER_KM)
    .sort((a, b) => frpOf(b) - frpOf(a) || b.count - a.count)   // las zonas más intensas primero
    .slice(0, LOCAL_HEAT_MAX_ZONES);
  const toggle = document.getElementById('toggle-localzones'); // interruptor propio, separado del de los focos
  localZonesVisible = toggle ? toggle.checked : true;
  const before = map.getLayer(LOCAL_ZONE_BEFORE_LAYER) ? LOCAL_ZONE_BEFORE_LAYER : undefined;

  for (let i = 0; i < clusters.length; i++) {
    const cluster = clusters[i];
    const center = { lat: cluster.lat, lon: cluster.lon };
    const at = historical ? strongestFireTime(cluster.pixels) : null;
    const grid = new WeatherGrid(center, LOCAL_HEAT_GRID_SIZE, LOCAL_HEAT_RADIUS_KM, at ? { at: new Date(at) } : {});
    try {
      await grid.fetchWeather();
    } catch (err) {
      console.warn('[LocalFireHeat] No se pudo obtener clima para una zona local:', err);
      continue;
    }

    const layer = new TemperatureHeatLayer(map, grid, {
      resolution: 128,
      idSuffix: `-local-${i}`,
      opacity: 200,
      ramp: 'fire',        // solo lo más caliente se pinta (amarillo→rojo); lo frío es transparente
      showLegend: false,   // no pisa la leyenda de la capa principal
      circular: true,      // borde difuso, sin esquinas
      beforeId: LOCAL_ZONE_BEFORE_LAYER,
    });
    layer.render();
    layer.setVisible(localZonesVisible);

    // Contorno punteado: marca el radio exacto de la zona.
    const outlineSourceId = `local-zone-outline-src-${i}`;
    const outlineId = `local-zone-outline-${i}`;
    if (map.getLayer(outlineId)) map.removeLayer(outlineId);
    if (map.getSource(outlineSourceId)) map.removeSource(outlineSourceId);
    map.addSource(outlineSourceId, { type: 'geojson', data: buildCircleGeoJSON(center, LOCAL_HEAT_RADIUS_KM) });
    map.addLayer({
      id: outlineId, type: 'line', source: outlineSourceId,
      layout: { visibility: localZonesVisible ? 'visible' : 'none' },
      paint: { 'line-color': '#ff6a3d', 'line-width': 2, 'line-dasharray': [2, 2], 'line-opacity': 0.9 },
    }, before);

    // Rótulo fijo con lo esencial + botón para acercar.
    const popup = new maplibregl.Popup({
      closeButton: false, closeOnClick: false, closeOnMove: false,
      offset: 16, maxWidth: '270px', className: 'fw-zone-popup',
    }).setLngLat([center.lon, center.lat]).setHTML(localZoneSummaryHTML(cluster, grid));
    if (localZonesVisible) popup.addTo(map);
    const btn = popup.getElement() && popup.getElement().querySelector('.fw-zoom');
    if (btn) {
      btn.addEventListener('click', () => {
        map.flyTo({ center: [center.lon, center.lat], zoom: 13, pitch: CONFIG.pitch, bearing: CONFIG.bearing });
      });
    }

    localHeatZones.push({ grid, layer, center, outlineId, outlineSourceId, popup });
  }

  if (!map._fwZoneZoomBound) { map.on('zoom', updateLocalZonePopupDensity); map._fwZoneZoomBound = true; }
  updateLocalZonePopupDensity();

  console.info(`[LocalFireHeat] ${localHeatZones.length} zona(s) de calor local generada(s) cerca de focos FIRMS.`);
}

function setLocalFireHeatVisible(visible) {
  localZonesVisible = visible;
  localHeatZones.forEach((z) => {
    z.layer.setVisible(visible);
    if (localZonesMap && localZonesMap.getLayer(z.outlineId)) {
      localZonesMap.setLayoutProperty(z.outlineId, 'visibility', visible ? 'visible' : 'none');
    }
    if (z.popup && localZonesMap) {
      if (visible) { z.popup.addTo(localZonesMap); } else { z.popup.remove(); }
    }
  });
  if (visible) updateLocalZonePopupDensity();
}
