// ============================================================
// FireWatch · Mapa 3D — orquestador principal
//
// v7 — capa de datos revisada (sesión de auditoría 28-09-2026):
//  - Detecta el proxy local (api.js) antes de pedir nada.
//  - Open-Meteo: una sola petición, refresco cada 60 min (el modelo se
//    recalcula cada hora).
//  - Overpass (agua, grifos, bomberos): se piden UNA POR UNA (en paralelo
//    saturaban el servidor), con caché de 24 h.
//  - NASA FIRMS: varios satélites, refresco cada 30 min, modo replay.
//  - DMC: estaciones reales + comparación modelo vs. medición.
//  - Panel "Estado de las fuentes": cada fuente informa si cargó, cuántos
//    datos trajo o por qué falló (antes un error tapaba a los demás).
// ============================================================

document.addEventListener('DOMContentLoaded', async () => {
  await initDataAccess();
  renderSourcePanel();
  refreshArchiveStatus();

  const map = initMap();
  const grid = new WeatherGrid(AREA.center, CONFIG.gridSize, AREA.radiusKm);
  // Clima de todo Chile (grilla gruesa) + compuesto: grilla fina de Santiago donde exista, nacional en el resto.
  const national = new NationalWeatherGrid();
  const liveWeather = new WeatherComposite(grid, national);
  let windLayer = null;
  let heatLayer = null;
  let natHeat = null; // mapa de calor / riesgo a escala de todo Chile (grilla gruesa)
  window.FW_DEBUG = { get natHeat() { return natHeat; }, get scenario() { return scenario; }, national, liveWeather }; // para pruebas y para mirar desde la consola

  // Escenario: focos de un día pasado (replay o "ayer") + el clima de ese momento.
  // Mientras está activo, su mapa de calor y su viento reemplazan a los del clima actual.
  let scenario = null; // { preset, grid, heat, eventTime }
  const activeGrid = () => (scenario ? scenario.grid : grid);
  const activeHeat = () => (scenario ? scenario.heat : heatLayer);
  // Para consultas, simulador y viento: el escenario si hay uno; si no, el clima en vivo de todo el país.
  const activeSampler = () => (scenario ? scenario.grid : liveWeather);

  function setStatus(msg, isError) {
    const el = document.getElementById('status-text');
    el.textContent = msg;
    el.style.color = isError ? '#e5484d' : '';
  }

  const fmtTime = (d) => d.toLocaleTimeString('es-CL', { hour: '2-digit', minute: '2-digit' });

  function updateInfoPanel() {
    const g = activeGrid();
    const avg = g.averages();
    if (!avg) return;
    const v = scenario ? null : chileValidation; // la comparación con la DMC es solo del clima actual
    const modeLine = FW.proxy
      ? '<div class="info-line hi"><b>Datos</b> · proxy local con caché</div>'
      : '<div class="info-line"><b>Datos</b> · directo (sin proxy: sin FIRMS ni mediciones DMC)</div>';
    const modelLine = scenario
      ? `<div class="info-line hi"><b>Clima histórico</b> · ${g.at ? formatFireTimeLocal(g.at.getTime()) : '—'}</div>`
      : `<div class="info-line ${g.stale ? 'warn' : ''}"><b>Modelo Open-Meteo</b> · ${g.modelTime ? fmtTime(g.modelTime) + ' (hora local)' : '—'}${g.stale ? ' · dato viejo' : g.fromCache ? ' · caché' : ''}</div>` +
        `<div class="info-line"><b>Última consulta</b> · ${fmtTime(g.fetchedAt)}</div>`;
    document.getElementById('info-body').innerHTML = `
      ${scenario ? `<div class="info-line hi"><b>Escenario</b> · ${scenario.preset.label}</div>` : ''}
      <div class="info-line"><b>Viento promedio</b> · ${avg.windSpeed.toFixed(1)} km/h</div>
      <div class="info-line"><b>Temperatura promedio</b> · ${avg.temp.toFixed(1)} °C</div>
      <div class="info-line"><b>Humedad promedio</b> · ${avg.rh !== null ? avg.rh.toFixed(0) + ' %' : '—'}</div>
      <div class="info-line"><b>Grilla</b> · ${CONFIG.gridSize}×${CONFIG.gridSize} puntos, radio ${g.radiusKm.toFixed(0)} km</div>
      <div class="info-line"><b>Cámaras</b> · ${CONFIG.cameras.length}</div>
      <div class="info-line"><b>Focos NASA FIRMS</b> · ${firePixels.length}${FIRE_STATE.replay ? ' (escenario)' : ' (24 h)'}</div>
      ${modeLine}
      ${modelLine}
      ${v ? `<div class="info-line hi"><b>Modelo vs. DMC</b> · ${v.n} estaciones · error temp ±${v.tempMAE.toFixed(1)} °C (sesgo ${v.tempBias >= 0 ? '+' : ''}${v.tempBias.toFixed(1)})${v.windMAE !== null ? ' · viento ±' + v.windMAE.toFixed(1) + ' km/h' : ''}</div>` : ''}
    `;
  }

  function ensureWind() {
    if (windLayer) return;
    windLayer = new WindParticleLayer(map, scenario ? scenario.grid : liveWeather);
    windLayer.setVisible(document.getElementById('toggle-wind').checked);
    windLayer.start();
  }

  async function loadNationalWeather() {
    try {
      await national.fetchWeather();
      ensureWind();
      if (!natHeat) natHeat = new NationalHeatLayer(map, national, { mode: document.getElementById('heat-mode').value });
      natHeat.render();
      natHeat.setVisible(document.getElementById('toggle-heat').checked && !scenario);
      const n = national.points.length;
      reportSource('weatherNational', national.stale || national.missingPoints > 0 ? 'warn' : 'ok',
        `${n - national.missingPoints}/${n} puntos · ${national.modelTime ? 'modelo de ' + fmtTime(national.modelTime) : ''}${national.stale ? ' · DATO VIEJO' : national.fromCache ? ' · desde caché' : ''}`);
    } catch (err) {
      console.error(err);
      reportSource('weatherNational', 'error', err.message);
    }
  }

  async function loadWeather() {
    setStatus('Consultando Open-Meteo…');
    try {
      await grid.fetchWeather();

      if (!heatLayer) {
        heatLayer = new TemperatureHeatLayer(map, grid, { resolution: 256, opacity: 120, beforeId: 'fire-pixels-glow', mode: document.getElementById('heat-mode').value });
        heatLayer.fuelGrid = fuelGridFor(AREA.center.lat, AREA.center.lon); // si ya cargó
      }
      heatLayer.render();
      if (!scenario) heatLayer.setVisible(document.getElementById('toggle-heat').checked);

      ensureWind();
      computeModelValidation(grid);
      updateInfoPanel();

      const bits = [`${grid.points.length - grid.missingPoints}/${grid.points.length} puntos`];
      if (grid.modelTime) bits.push('modelo de ' + fmtTime(grid.modelTime));
      if (grid.stale) bits.push('DATO VIEJO: Open-Meteo no respondió');
      else if (grid.fromCache) bits.push('desde caché');
      reportSource('weather', grid.stale || grid.missingPoints > 0 ? 'warn' : 'ok', bits.join(' · '));
      setTimeout(refreshArchiveStatus, 1500); // el proxy ya guardó esta lectura
      setStatus('Datos actualizados: ' + fmtTime(grid.fetchedAt) + (grid.missingPoints > 0 ? ` (${grid.missingPoints} puntos sin datos)` : ''),
        grid.missingPoints > 0 || grid.stale);
    } catch (err) {
      console.error(err);
      reportSource('weather', 'error', err.message);
      setStatus('Error consultando Open-Meteo: ' + err.message, true);
    }
  }

  async function loadFire(replay) {
    setStatus(replay ? `Cargando replay de FIRMS (${replay.date})…` : 'Cargando focos de calor satelitales (NASA FIRMS)…');
    // En vivo se consulta todo Chile; un replay usa el bbox de su escenario.
    await loadFirePixels(map, replay ? AREA : NATION_AREA, replay);
    await addLocalFireHeatZones(map);
    updateInfoPanel();
  }

  async function loadStations() {
    await addChileWeatherLayer(map, NATION_AREA);
    if (grid.loaded) computeModelValidation(grid);
    updateInfoPanel();
  }

  // Las operaciones que cambian focos/escenario se ejecutan de a una (en cola) y los
  // botones se bloquean mientras tanto: dos escenarios cargando a la vez pisaban los datos.
  let opChain = Promise.resolve();
  let pendingOps = 0;
  const opButtons = () => document.querySelectorAll('#btn-replay, #btn-live, #btn-refresh');
  function queueOp(fn) {
    pendingOps++;
    opButtons().forEach((b) => { b.disabled = true; });
    opChain = opChain.then(fn).catch((e) => console.error('[main] operación falló:', e)).then(() => {
      if (--pendingOps === 0) opButtons().forEach((b) => { b.disabled = false; });
    });
    return opChain;
  }

  function clearScenario() {
    if (scenario && scenario.heat) scenario.heat.destroy();
    scenario = null;
    if (windLayer) windLayer.grid = liveWeather;
    if (heatLayer) {
      heatLayer._updateLegend();
      heatLayer.setVisible(document.getElementById('toggle-heat').checked);
    }
    if (natHeat) natHeat.setVisible(document.getElementById('toggle-heat').checked);
    delete sourceStatus.weatherHist;
    renderSourcePanel();
  }

  /** Carga los focos FIRMS de ese día y el clima histórico de la hora del foco más intenso. */
  async function enterScenario(preset) {
    setStatus(`Cargando escenario: ${preset.label}…`);
    clearScenario();
    await loadFirePixels(map, AREA, preset);

    const evTs = strongestFireTime(firePixels);
    const eventTime = new Date(evTs || Date.parse(preset.date + 'T18:00:00Z')); // sin focos: ~15 h de Chile
    const sgrid = new WeatherGrid(preset.center, CONFIG.gridSize, preset.radiusKm, { at: eventTime });
    let weatherError = null;
    try {
      await sgrid.fetchWeather();
      reportSource('weatherHist', sgrid.missingPoints ? 'warn' : 'ok',
        `${sgrid.points.length - sgrid.missingPoints}/${sgrid.points.length} puntos · ${formatFireTimeLocal(sgrid.at.getTime())}`);
    } catch (err) {
      weatherError = err.message;
      reportSource('weatherHist', 'error', err.message);
    }

    if (!weatherError) {
      if (heatLayer) heatLayer.setVisible(false); // primero apagar el actual (comparten la leyenda)
      if (natHeat) natHeat.setVisible(false);     // el escenario es histórico: no mezclarlo con el clima de hoy
      let fuel = null; // vegetación del área del escenario (para el modo combinado y la consulta de puntos)
      try { fuel = await loadFuelGrid(boundsFromCenterKm(preset.center, preset.radiusKm)); } catch (err) {
        console.warn('[main] Sin vegetación para el escenario:', err.message);
      }
      const heat = new TemperatureHeatLayer(map, sgrid, {
        resolution: 256, opacity: 120, beforeId: 'fire-pixels-glow', idSuffix: '-scenario',
        mode: document.getElementById('heat-mode').value,
      });
      heat.fuelGrid = fuel;
      heat.render();
      heat.setVisible(document.getElementById('toggle-heat').checked);
      scenario = { preset, grid: sgrid, heat, eventTime };
      if (windLayer) windLayer.grid = sgrid;
    }

    await addLocalFireHeatZones(map);
    updateInfoPanel();

    const when = formatFireTimeLocal(eventTime.getTime());
    const note = weatherError
      ? `Escenario: ${preset.label}. Focos FIRMS cargados, pero el clima histórico falló: ${weatherError}`
      : firePixels.length
        ? `Escenario: ${preset.label}. ${firePixels.length} foco(s) FIRMS reales · clima histórico de Open-Meteo a esa hora (${when}, la del foco más intenso).`
        : `Escenario: ${preset.label}. FIRMS no detectó focos ese día (¿buena noticia?). Se muestra el clima de ${when} como referencia.`;
    document.getElementById('replay-note').textContent = note;
    setStatus(`Escenario: ${preset.label}`, !!weatherError);
  }

  // La grilla de combustible llega de forma asíncrona: se la damos al mapa de calor
  // (modo "Riesgo × combustible") apenas esté lista.
  document.addEventListener('fuelgrid', (e) => {
    if (heatLayer && !heatLayer.fuelGrid && e.detail.contains(AREA.center.lat, AREA.center.lon)) heatLayer.setFuelGrid(e.detail);
  });

  const inspector = initInspector(map, {
    enabled: () => document.getElementById('toggle-inspect').checked,
    weather: () => ({ grid: activeSampler(), label: scenario ? scenario.preset.label : '' }),
  });
  document.getElementById('toggle-inspect').addEventListener('change', (e) => { if (!e.target.checked) inspector.close(); });

  initRouting(map);
  initAlerts(map);
  initSpread(map, { weather: () => ({ grid: activeSampler(), label: scenario ? scenario.preset.label : '' }) });
  initM2(map, { weather: () => ({ grid: activeSampler(), label: scenario ? scenario.preset.label : '' }) }); // «continuación probable» (modelo M2 en el backend)
  initZones(map); // agua, grifos, cuarteles y vegetación de cualquier zona de Chile al acercar el mapa

  // Una alerta nueva de la central aparece también en la barra de estado.
  document.addEventListener('fw-new-alert', (e) => {
    const a = e.detail;
    setStatus(`🚨 Alerta nueva: ${a.camara_nombre || a.camara_id} · ${a.fecha_hora}`, true);
  });

  map.on('load', async () => {
    loadWeather();
    loadNationalWeather();
    addFuelCoverLayer(map, AREA); // Planetary Computer — servidor distinto, no compite con Overpass

    // Cuarteles y grifos: primero la copia local del SIG de Bomberos (todo el país, sin Overpass);
    // si no existe, se piden a OpenStreetMap como antes.
    setStatus('Cargando cuarteles y grifos (SIG Bomberos)…');
    const bomberosOk = await bomberosLoadLocal();

    // Overpass: una consulta por vez (en paralelo saturaba el servidor)
    setStatus('Cargando fuentes de agua…');
    await addWaterSourcesLayer(map, AREA);

    if (!bomberosOk) {
      setStatus('Cargando grifos…');
      await addHydrantsLayer(map, AREA);

      setStatus('Cargando estaciones de bomberos…');
      await addFireStationsLayer(map, AREA);
    }

    await loadFire(null);

    setStatus('Cargando estaciones meteorológicas DMC…');
    await loadStations();

    const errors = Object.keys(sourceStatus).filter((k) => sourceStatus[k].state === 'error');
    if (errors.length) {
      setStatus(`⚠️ Fuentes con error: ${errors.map((k) => SOURCE_LABELS[k]).join(', ')} (ver panel de fuentes)`, true);
    } else {
      setStatus('Datos actualizados: ' + fmtTime(new Date()));
    }

    setInterval(loadWeather, CONFIG.autoRefreshMs);
    setInterval(loadNationalWeather, CONFIG.autoRefreshMs);
    setInterval(() => { if (!FIRE_STATE.replay) loadFire(null); }, CONFIG.firmsRefreshMs);
    setInterval(loadStations, 15 * 60 * 1000);
  });

  // ---- botón "Actualizar datos ahora": reintenta lo que falló y refresca lo vivo ----
  document.getElementById('btn-refresh').addEventListener('click', () => queueOp(async () => {
    loadWeather();
    loadNationalWeather();
    if (!map.getSource('water-sources')) { setStatus('Reintentando fuentes de agua…'); await addWaterSourcesLayer(map, AREA); }
    if (!bomberosLocal.available && !map.getSource('hydrants')) { setStatus('Reintentando grifos…'); await addHydrantsLayer(map, AREA); }
    if (!bomberosLocal.available && !map.getSource('fire-stations')) { setStatus('Reintentando estaciones de bomberos…'); await addFireStationsLayer(map, AREA); }
    if (!map.getSource('worldcover-image')) { setStatus('Reintentando cobertura vegetal…'); await addFuelCoverLayer(map, AREA); }
    if (FIRE_STATE.replay) await enterScenario(FIRE_STATE.replay); else await loadFire(null);
    await loadStations();
    setStatus('Datos actualizados: ' + fmtTime(new Date()));
  }));

  // ---- replay: focos históricos / ayer / cualquier fecha, con el clima de ese momento ----
  const replaySelect = document.getElementById('replay-select');
  const replayDate = document.getElementById('replay-date');
  const chileDate = (d) => d.toLocaleDateString('en-CA', { timeZone: 'America/Santiago' });

  CONFIG.replayPresets.forEach((p, i) => {
    const opt = document.createElement('option');
    opt.value = String(i);
    opt.textContent = p.label;
    replaySelect.appendChild(opt);
  });
  [['yesterday', 'Ayer · Santiago'], ['custom', 'Elegir fecha… · Santiago']].forEach(([value, text]) => {
    const opt = document.createElement('option');
    opt.value = value; opt.textContent = text;
    replaySelect.appendChild(opt);
  });
  replayDate.max = chileDate(new Date(Date.now() - 864e5));
  replaySelect.addEventListener('change', () => {
    replayDate.style.display = replaySelect.value === 'custom' ? 'block' : 'none';
  });

  function replayPresetFromUI() {
    const v = replaySelect.value;
    if (v !== 'yesterday' && v !== 'custom') return CONFIG.replayPresets[Number(v)];
    const date = v === 'yesterday' ? chileDate(new Date(Date.now() - 864e5)) : replayDate.value;
    if (!date) return null;
    const b = boundsFromCenterKm(AREA.center, AREA.radiusKm);
    return {
      label: (v === 'yesterday' ? `Ayer (${date})` : date) + ' · Santiago',
      date, days: 1, center: AREA.center, radiusKm: AREA.radiusKm,
      bbox: [b[0][0], b[0][1], b[1][0], b[1][1]], santiago: true,
    };
  }

  function fitSantiago() {
    map.fitBounds(boundsFromCenterKm(AREA.center, AREA.radiusKm * 1.1), {
      pitch: CONFIG.pitch, bearing: CONFIG.bearing,
      padding: { top: 90, bottom: 60, left: 300, right: 280 },
    });
  }

  document.getElementById('btn-replay').addEventListener('click', () => {
    const preset = replayPresetFromUI();
    if (!preset) { document.getElementById('replay-note').textContent = 'Elige una fecha primero.'; return; }
    queueOp(async () => {
      if (preset.santiago) fitSantiago();
      else map.flyTo({ center: [preset.center.lon, preset.center.lat], zoom: 10.5, pitch: CONFIG.pitch, bearing: CONFIG.bearing });
      await enterScenario(preset);
    });
  });

  document.getElementById('btn-nation').addEventListener('click', () => {
    map.flyTo({ center: [-71.2, -37.5], zoom: 4.1, pitch: 0, bearing: 0 });
  });

  document.getElementById('btn-live').addEventListener('click', () => {
    queueOp(async () => {
      document.getElementById('replay-note').textContent = '';
      clearScenario();
      fitSantiago();
      await loadFire(null);
      setStatus('Focos en vivo: ' + fmtTime(new Date()));
    });
  });

  // ---- interruptores de capas ----
  document.getElementById('toggle-heat').addEventListener('change', (e) => {
    const l = activeHeat();
    if (l) l.setVisible(e.target.checked);
    if (natHeat) natHeat.setVisible(e.target.checked && !scenario);
  });

  document.getElementById('heat-mode').addEventListener('change', (e) => {
    // Primero el actual y al final el activo: la leyenda es compartida y gana el último.
    if (heatLayer) heatLayer.setMode(e.target.value);
    if (natHeat) natHeat.setMode(e.target.value);
    if (scenario && scenario.heat) scenario.heat.setMode(e.target.value);
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

  document.getElementById('toggle-water').addEventListener('change', (e) => {
    setWaterSourcesVisible(map, e.target.checked);
  });

  document.getElementById('toggle-hydrants').addEventListener('change', (e) => {
    setHydrantsVisible(map, e.target.checked);
  });

  document.getElementById('toggle-firestations').addEventListener('change', (e) => {
    setFireStationsVisible(map, e.target.checked);
  });

  document.getElementById('toggle-firms').addEventListener('change', (e) => {
    setFirePixelsVisible(map, e.target.checked);
  });

  document.getElementById('toggle-localzones').addEventListener('change', (e) => {
    setLocalFireHeatVisible(e.target.checked);
  });

  document.getElementById('toggle-chile').addEventListener('change', (e) => {
    setChileWeatherVisible(map, e.target.checked);
  });
});
