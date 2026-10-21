// ============================================================
// FireWatch · Mapa 3D — alertas de la central (API de FireWatch)
//
// Muestra en el mapa las alertas que las cámaras envían al backend
// (firewatch-project-backend, puerto 8100): rojo = pendiente, naranja =
// confirmada, gris = falso positivo. Al pulsar una alerta se ve la foto,
// el estado y los botones "Acercar al foco", "Ruta de bomberos" y "Simular propagación".
//
// Inicio de sesión: el mapa pide usuario y contraseña de un operador y guarda
// solo el TOKEN en la pestaña (sessionStorage). La contraseña no se guarda.
// El backend debe permitir el origen del mapa (CORS, variable FIREWATCH_CORS).
//
// Las funciones "fwAlert*" de arriba no tocan el DOM: se prueban en Node
// (tests/alerts.test.js).
// ============================================================

// ---------------------------------------------------------------- lógica pura
const FW_ALERT_COLORS = { PENDIENTE: '#ff2d2d', CONFIRMADA: '#ff8a00', FALSO_POSITIVO: '#7d8a9a' };
const FW_ALERT_TIPOS = { FIRE: 'Fuego', SMOKE: 'Humo', CRITICAL: 'Fuego y humo', NO_DETECTION: 'Sin detección (revisar la foto)' };

/** Solo las alertas con coordenadas válidas; `showClosed` agrega los falsos positivos. */
function fwAlertsToGeoJSON(items, showClosed) {
  const features = [];
  (items || []).forEach((a) => {
    const g = a && a.gps;
    if (!Array.isArray(g) || g.length !== 2 || !Number.isFinite(g[0]) || !Number.isFinite(g[1])) return;
    if (!showClosed && a.estado === 'FALSO_POSITIVO') return;
    features.push({
      type: 'Feature',
      properties: {
        id: a.id_alerta, estado: a.estado || 'PENDIENTE', tipo: a.tipo || '', camara: a.camara_nombre || a.camara_id || '',
        fecha: a.fecha_hora || '', confianza: a.confianza == null ? '' : a.confianza, ubicacion: a.ubicacion || '',
        motivo: a.motivo_descarte || '',
      },
      geometry: { type: 'Point', coordinates: [g[1], g[0]] }, // GeoJSON: [lon, lat]
    });
  });
  return { type: 'FeatureCollection', features };
}

/** Conteos por estado y cuántas no se pueden dibujar por no tener ubicación. */
function fwAlertSummary(items) {
  const s = { PENDIENTE: 0, CONFIRMADA: 0, FALSO_POSITIVO: 0, sinUbicacion: 0, total: 0 };
  (items || []).forEach((a) => {
    s.total++;
    s[a.estado] = (s[a.estado] || 0) + 1;
    const g = a.gps;
    if (!Array.isArray(g) || g.length !== 2) s.sinUbicacion++;
  });
  return s;
}

/** Alertas pendientes con ubicación que no estaban en `seen` (ids ya vistos). */
function fwNewPending(items, seen) {
  const ok = (g) => Array.isArray(g) && g.length === 2 && Number.isFinite(g[0]) && Number.isFinite(g[1]);
  return (items || []).filter((a) => a.estado === 'PENDIENTE' && !seen.has(a.id_alerta) && ok(a.gps));
}

function fwEscape(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/** Limpia la URL escrita por la persona: sin espacios ni barra final. */
function fwApiBase(url) {
  return String(url || '').trim().replace(/\/+$/, '');
}

if (typeof module !== 'undefined') {
  module.exports = { fwAlertsToGeoJSON, fwAlertSummary, fwNewPending, fwEscape, fwApiBase, FW_ALERT_COLORS, FW_ALERT_TIPOS };
}

// ---------------------------------------------------------------- navegador
const ALERTS_POLL_MS = 5000;
const alertsState = { ready: false, map: null, base: '', token: '', items: [], seen: null, timer: null, busy: false, popupUrl: null };

function alertsStore(key, value) {
  try {
    if (value === undefined) return sessionStorage.getItem(key);
    if (value === null) sessionStorage.removeItem(key); else sessionStorage.setItem(key, value);
  } catch (_) { /* almacenamiento bloqueado: la sesión dura hasta cerrar la pestaña */ }
  return null;
}

function alertsNote(text, isError) {
  const el = document.getElementById('alerts-note');
  if (!el) return;
  el.textContent = text;
  el.style.color = isError ? '#e5484d' : '';
}

async function alertsFetch(path, opts) {
  const res = await fetch(alertsState.base + path, {
    ...opts, headers: { ...(opts && opts.headers), ...(alertsState.token ? { Authorization: `Bearer ${alertsState.token}` } : {}) },
  });
  if (res.status === 401 && alertsState.token) { alertsLogout('La sesión venció. Inicia sesión otra vez.'); throw new Error('sesión vencida'); }
  return res;
}

function ensureAlertLayers(map) {
  if (map.getSource('central-alerts')) return;
  map.addSource('central-alerts', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  const color = ['match', ['get', 'estado'], 'PENDIENTE', FW_ALERT_COLORS.PENDIENTE, 'CONFIRMADA', FW_ALERT_COLORS.CONFIRMADA, FW_ALERT_COLORS.FALSO_POSITIVO];
  map.addLayer({
    id: 'central-alerts-halo', type: 'circle', source: 'central-alerts',
    paint: { 'circle-radius': ['interpolate', ['linear'], ['zoom'], 8, 14, 14, 30], 'circle-color': color, 'circle-opacity': 0.25, 'circle-blur': 0.5 },
  });
  map.addLayer({
    id: 'central-alerts-point', type: 'circle', source: 'central-alerts',
    paint: {
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 8, 7, 14, 12], 'circle-color': color,
      'circle-stroke-color': '#ffffff', 'circle-stroke-width': 3,
    },
  });
  map.on('click', 'central-alerts-point', (e) => openAlertPopup(e.features[0].properties, e.lngLat));
  map.on('mouseenter', 'central-alerts-point', () => { map.getCanvas().style.cursor = 'pointer'; });
  map.on('mouseleave', 'central-alerts-point', () => { map.getCanvas().style.cursor = ''; });
}

function openAlertPopup(p, lngLat) {
  const estado = { PENDIENTE: 'Pendiente de revisión', CONFIRMADA: 'Confirmada', FALSO_POSITIVO: 'Falso positivo' }[p.estado] || p.estado;
  const conf = p.confianza === '' ? '' : ` · ${Math.round(Number(p.confianza) * 100)} %`;
  const abierta = p.estado !== 'FALSO_POSITIVO';
  const spread = typeof spreadButtonHTML === 'function' && abierta ? spreadButtonHTML(lngLat.lat, lngLat.lng, '🔥 Simular propagación') : '';
  const html =
    `<div class="fw-zone fw-alert">` +
    `<b style="color:${FW_ALERT_COLORS[p.estado] || '#ff2d2d'}">🚨 ${fwEscape(FW_ALERT_TIPOS[p.tipo] || p.tipo)}</b>` +
    `<div class="fw-dim">Cámara ${fwEscape(p.camara)} · ${fwEscape(p.fecha)}</div>` +
    (p.ubicacion ? `<div class="fw-dim">${fwEscape(p.ubicacion)}</div>` : '') +
    `<div>Estado: <b>${fwEscape(estado)}</b>${conf ? `<span class="fw-dim"> · confianza${conf}</span>` : ''}</div>` +
    (p.motivo ? `<div class="fw-dim">Motivo del descarte: ${fwEscape(p.motivo)}</div>` : '') +
    `<div class="fw-alert-img" id="alert-img-box"><span class="fw-dim">Cargando foto…</span></div>` +
    `<div class="fw-alert-actions">` +
    `<button class="fw-zoom fw-alert-zoom" type="button">🔎 Acercar al foco</button>` +
    (abierta ? routeButtonHTML(lngLat.lat, lngLat.lng, '🚒 Ruta de bomberos') : '') + spread +
    `</div></div>`;
  // El globo se abre a la derecha del punto para no taparlo, y el mapa se corre para dejarle sitio.
  const popup = new maplibregl.Popup({ anchor: 'left', offset: 18, maxWidth: '260px', className: 'fw-zone-popup fw-alert-popup' })
    .setLngLat(lngLat).setHTML(html).addTo(alertsState.map);
  popup.on('close', () => { if (alertsState.popupUrl) { URL.revokeObjectURL(alertsState.popupUrl); alertsState.popupUrl = null; } });
  const map = alertsState.map;
  const pad = { top: 0, bottom: 0, left: 0, right: Math.min(320, Math.round(map.getContainer().clientWidth * 0.4)) };
  map.easeTo({ center: lngLat, padding: pad, duration: 600 });
  const zoomBtn = popup.getElement() && popup.getElement().querySelector('.fw-alert-zoom');
  if (zoomBtn) zoomBtn.addEventListener('click', () => {
    map.flyTo({ center: lngLat, zoom: Math.max(map.getZoom(), 16.5), padding: pad, duration: 1200 });
  });
  loadAlertImage(p.id);
}

/** La foto exige el token: se descarga con fetch y se muestra como imagen local. */
async function loadAlertImage(id) {
  try {
    const res = await alertsFetch(`/alertas/${encodeURIComponent(id)}/imagen`);
    const box = document.getElementById('alert-img-box');
    if (!box) return;
    if (!res.ok) { box.innerHTML = '<span class="fw-dim">Sin foto disponible.</span>'; return; }
    if (alertsState.popupUrl) URL.revokeObjectURL(alertsState.popupUrl);
    alertsState.popupUrl = URL.createObjectURL(await res.blob());
    box.innerHTML = '';
    const img = document.createElement('img');
    img.src = alertsState.popupUrl; img.alt = 'Foto de la cámara'; img.style.cssText = 'width:100%;max-height:150px;object-fit:cover;border-radius:6px;margin-top:6px;';
    box.appendChild(img);
  } catch (_) {
    const box = document.getElementById('alert-img-box');
    if (box) box.innerHTML = '<span class="fw-dim">No se pudo cargar la foto.</span>';
  }
}

function alertsRender() {
  if (!alertsState.ready) return; // el estilo del mapa aún no cargó; se dibuja al cargar
  const map = alertsState.map;
  const showClosed = document.getElementById('toggle-alerts-closed').checked;
  ensureAlertLayers(map);
  map.getSource('central-alerts').setData(fwAlertsToGeoJSON(alertsState.items, showClosed));
  const s = fwAlertSummary(alertsState.items);
  const extra = s.sinUbicacion ? ` · ${s.sinUbicacion} sin ubicación (no se pueden dibujar)` : '';
  alertsNote(`${s.PENDIENTE} pendiente(s) · ${s.CONFIRMADA} confirmada(s) · ${s.FALSO_POSITIVO} descartada(s)${extra}`);
  reportSource('alerts', 'ok', `${s.total} alerta(s) · ${s.PENDIENTE} pendiente(s)`);
}

async function alertsPoll() {
  if (!alertsState.token || alertsState.busy) return;
  alertsState.busy = true;
  try {
    const res = await alertsFetch('/alertas?limit=200');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    alertsState.items = data.items || [];
    const fresh = alertsState.seen ? fwNewPending(alertsState.items, alertsState.seen) : [];
    alertsState.seen = new Set(alertsState.items.map((a) => a.id_alerta));
    alertsRender();
    document.dispatchEvent(new CustomEvent('fw-alerts-updated', { detail: alertsState.items })); // p. ej. spread-layer precarga los datos de la zona
    if (fresh.length) {
      const a = fresh[0];
      document.dispatchEvent(new CustomEvent('fw-new-alert', { detail: a }));
      if (document.getElementById('toggle-alerts-follow').checked) {
        alertsState.map.flyTo({ center: [a.gps[1], a.gps[0]], zoom: Math.max(alertsState.map.getZoom(), 13.5), duration: 1200 });
      }
    }
  } catch (err) {
    if (err.message !== 'sesión vencida') {
      alertsNote('No se pudo consultar la central: ' + err.message, true);
      reportSource('alerts', 'error', err.message);
    }
  } finally { alertsState.busy = false; }
}

function alertsStart() {
  clearInterval(alertsState.timer);
  alertsState.seen = null;
  alertsPoll();
  alertsState.timer = setInterval(alertsPoll, ALERTS_POLL_MS);
}

function alertsLogout(msg) {
  clearInterval(alertsState.timer);
  alertsState.token = ''; alertsState.items = []; alertsState.seen = null;
  alertsStore('fw-alerts-token', null);
  const map = alertsState.map;
  if (map && map.getSource('central-alerts')) map.getSource('central-alerts').setData({ type: 'FeatureCollection', features: [] });
  document.getElementById('alerts-login').style.display = '';
  document.getElementById('alerts-session').style.display = 'none';
  alertsNote(msg || '');
  reportSource('alerts', 'off', 'sin sesión');
}

async function alertsLogin() {
  const base = fwApiBase(document.getElementById('alerts-url').value);
  const username = document.getElementById('alerts-user').value.trim();
  const password = document.getElementById('alerts-pass').value;
  if (!base || !username || !password) { alertsNote('Completa la dirección, el usuario y la contraseña.', true); return; }
  alertsNote('Entrando…');
  try {
    const res = await fetch(base + '/login', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username, password }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
    alertsState.base = base; alertsState.token = body.token;
    document.getElementById('alerts-pass').value = '';
    alertsStore('fw-alerts-token', body.token); alertsStore('fw-alerts-base', base);
    document.getElementById('alerts-login').style.display = 'none';
    document.getElementById('alerts-session').style.display = '';
    document.getElementById('alerts-who').textContent = `${body.nombre_completo} (${body.rol})`;
    alertsStart();
  } catch (err) {
    const net = err instanceof TypeError; // fetch sin respuesta: servidor apagado o CORS
    alertsNote(net ? 'No se pudo conectar. ¿Está corriendo el backend en esa dirección y permite este origen (CORS)?' : err.message, true);
    reportSource('alerts', 'error', net ? 'sin conexión con la central' : err.message);
  }
}

function initAlerts(map) {
  alertsState.map = map;
  map.on('load', () => { alertsState.ready = true; if (alertsState.items.length) alertsRender(); });
  const url = document.getElementById('alerts-url');
  url.value = alertsStore('fw-alerts-base') || 'http://localhost:8100';
  document.getElementById('btn-alerts-login').addEventListener('click', alertsLogin);
  document.getElementById('alerts-pass').addEventListener('keydown', (e) => { if (e.key === 'Enter') alertsLogin(); });
  document.getElementById('btn-alerts-logout').addEventListener('click', () => alertsLogout('Sesión cerrada.'));
  document.getElementById('toggle-alerts-closed').addEventListener('change', () => { if (alertsState.token) alertsRender(); });
  document.getElementById('toggle-alerts').addEventListener('change', (e) => {
    ['central-alerts-halo', 'central-alerts-point'].forEach((id) => {
      if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', e.target.checked ? 'visible' : 'none');
    });
  });
  reportSource('alerts', 'off', 'sin sesión');
  // Si la pestaña ya tenía una sesión, se retoma (el token vive solo en esta pestaña).
  const tok = alertsStore('fw-alerts-token');
  if (tok) {
    alertsState.base = fwApiBase(url.value); alertsState.token = tok;
    document.getElementById('alerts-login').style.display = 'none';
    document.getElementById('alerts-session').style.display = '';
    document.getElementById('alerts-who').textContent = 'sesión anterior';
    alertsStart();
  }
}
