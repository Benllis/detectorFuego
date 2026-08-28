// ============================================================
// FireWatch · Mapa 3D — orquestador principal
// ============================================================

document.addEventListener('DOMContentLoaded', () => {
  const map = initMap();
  const grid = new WeatherGrid(AREA.center, CONFIG.gridSize, AREA.radiusKm);
  let windLayer = null;
  let heatLayer = null;

  function setStatus(msg, isError) {
    const el = document.getElementById('status-text');
    el.textContent = msg;
    el.style.color = isError ? '#e5484d' : '';
  }

  function updateInfoPanel() {
    const avg = grid.averages();
    if (!avg) return;
    document.getElementById('info-body').innerHTML = `
      <div class="info-line"><b>Viento promedio</b> · ${avg.windSpeed.toFixed(1)} km/h</div>
      <div class="info-line"><b>Temperatura promedio</b> · ${avg.temp.toFixed(1)} °C</div>
      <div class="info-line"><b>Grilla</b> · ${CONFIG.gridSize}×${CONFIG.gridSize} puntos, radio ${AREA.radiusKm.toFixed(0)} km</div>
      <div class="info-line"><b>Cámaras</b> · ${CONFIG.cameras.length}</div>
      <div class="info-line hi"><b>Fuente</b> · Open-Meteo, datos en vivo</div>
      <div class="info-line"><b>Última actualización</b> · ${grid.fetchedAt.toLocaleTimeString()}</div>
    `;
  }

  async function loadWeather() {
    setStatus('Consultando Open-Meteo…');
    try {
      await grid.fetchWeather();

      if (!heatLayer) {
        heatLayer = new TemperatureHeatLayer(map, grid);
      }
      heatLayer.render();
      heatLayer.setVisible(document.getElementById('toggle-heat').checked);

      if (!windLayer) {
        windLayer = new WindParticleLayer(map, grid);
        windLayer.setVisible(document.getElementById('toggle-wind').checked);
        windLayer.start();
      }
      updateInfoPanel();
      const warn = grid.missingPoints > 0 ? ` (${grid.missingPoints} puntos sin datos)` : '';
      setStatus('Datos actualizados: ' + grid.fetchedAt.toLocaleTimeString() + warn, grid.missingPoints > 0);
    } catch (err) {
      console.error(err);
      setStatus('Error consultando Open-Meteo — reintenta en unos segundos.', true);
    }
  }

  map.on('load', () => {
    loadWeather();
    addFuelCoverLayer(map, AREA);
    setInterval(loadWeather, CONFIG.autoRefreshMs);
  });

  document.getElementById('btn-refresh').addEventListener('click', loadWeather);

  document.getElementById('toggle-heat').addEventListener('change', (e) => {
    if (heatLayer) heatLayer.setVisible(e.target.checked);
  });

  document.getElementById('toggle-wind').addEventListener('change', (e) => {
    if (windLayer) windLayer.setVisible(e.target.checked);
  });

  document.getElementById('toggle-cam').addEventListener('change', (e) => {
    ['camera-sensor-point', 'camera-sensor-glow'].forEach((id) => {
      if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', e.target.checked ? 'visible' : 'none');
    });
  });

  document.getElementById('toggle-fuel').addEventListener('change', (e) => {
    setFuelCoverVisible(map, e.target.checked);
  });
});
