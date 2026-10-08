// ============================================================
// FireWatch · Mapa 3D — capa de cobertura vegetal / riesgo de combustible
//
// ESA WorldCover vía Microsoft Planetary Computer (Azure). Flujo:
//   1. Búsqueda STAC de qué tile (3x3 grados) cubre el área.
//   2. Imagen del área EXACTA (endpoint bbox, 1280 px ≈ 45 m/px) con la
//      paleta oficial de ESA. Si ese endpoint falla, se recorta la vista
//      previa del tile completo (más gruesa, ~160 m/px).
//   3. Se RECOLOREA en el navegador: cada clase de ESA pasa a un color de
//      riesgo de combustible (escala única de rojo oscuro a verde claro).
//      Lo no inflamable (urbano, agua, suelo desnudo) queda casi
//      transparente para que se lea el mapa base.
//
// v6: la clasificación se guarda como grilla consultable (FuelGrid, fuel-grid.js):
//     clase, propensión de combustible y resumen por celda de 250 m o por radio.
// v5.1: filtro de moda 3x3 (sin sal y pimienta), colores más suaves, por
// debajo del mapa de calor y apagada por defecto (es una capa de referencia).
// v5 — legible: sin desenfoque (el dato es categórico y el blur lo
// convertía en una mancha), imagen más nítida y colores por riesgo (antes
// el rojo de ESA marcaba "urbano", que se confundía con riesgo alto).
//
// Fuente: ESA WorldCover (Copernicus Sentinel-1 + Sentinel-2, 10m,
// CC BY 4.0), año 2021. Sin API key. Es una clasificación de cobertura,
// no una medición de combustible: el riesgo por clase es una regla simple.
// ============================================================

const PC_STAC_SEARCH_URL = 'https://planetarycomputer.microsoft.com/api/stac/v1/search';
const PC_PREVIEW_BASE_URL = 'https://planetarycomputer.microsoft.com/api/data/v1/item/preview.png';
const PC_BBOX_BASE_URL = 'https://planetarycomputer.microsoft.com/api/data/v1/item/bbox';
const FUEL_IMAGE_PX = 1280;

/**
 * Clasifica un bitmap de ESA: índice de clase por pixel (WORLDCOVER_CLASSES, en fuel-grid.js),
 * con un filtro de moda 3x3 que quita el "sal y pimienta" sin inventar clases.
 */
function classifyWorldCover(bitmap, sx, sy, sw, sh, outW, outH) {
  const canvas = document.createElement('canvas');
  canvas.width = outW; canvas.height = outH;
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  ctx.imageSmoothingEnabled = false; // categórico: nunca mezclar clases
  ctx.drawImage(bitmap, sx, sy, sw, sh, 0, 0, outW, outH);
  const d = ctx.getImageData(0, 0, outW, outH).data;

  const cache = new Map();
  const cls = new Uint8Array(outW * outH).fill(FUEL_NO_DATA);
  for (let p = 0, i = 0; p < cls.length; p++, i += 4) {
    if (d[i + 3] === 0) continue;
    const key = (d[i] << 16) | (d[i + 1] << 8) | d[i + 2];
    let k = cache.get(key);
    if (k === undefined) {
      let bd = Infinity; k = 0;
      WORLDCOVER_CLASSES.forEach((c, n) => {
        const dist = (c.esa[0] - d[i]) ** 2 + (c.esa[1] - d[i + 1]) ** 2 + (c.esa[2] - d[i + 2]) ** 2;
        if (dist < bd) { bd = dist; k = n; }
      });
      cache.set(key, k);
    }
    cls[p] = k;
  }

  const out = new Uint8Array(cls);
  const counts = new Uint8Array(WORLDCOVER_CLASSES.length);
  for (let y = 1; y < outH - 1; y++) {
    for (let x = 1; x < outW - 1; x++) {
      counts.fill(0);
      let best = FUEL_NO_DATA, bc = 0;
      for (let dy = -1; dy <= 1; dy++) {
        for (let dx = -1; dx <= 1; dx++) {
          const k = cls[(y + dy) * outW + x + dx];
          if (k === FUEL_NO_DATA) continue;
          if (++counts[k] > bc) { bc = counts[k]; best = k; }
        }
      }
      if (best !== FUEL_NO_DATA && bc >= 3) out[y * outW + x] = best;
    }
  }
  return { cls: out, w: outW, h: outH };
}

/** Dibuja la grilla como imagen a colores de riesgo (para la capa del mapa). */
function paintFuelGrid(grid) {
  const canvas = document.createElement('canvas');
  canvas.width = grid.w; canvas.height = grid.h;
  const ctx = canvas.getContext('2d');
  const img = ctx.createImageData(grid.w, grid.h);
  for (let p = 0, i = 0; p < grid.cls.length; p++, i += 4) {
    const k = grid.cls[p];
    if (k === FUEL_NO_DATA) continue;
    const c = WORLDCOVER_CLASSES[k].rgba;
    img.data[i] = c[0]; img.data[i + 1] = c[1]; img.data[i + 2] = c[2]; img.data[i + 3] = c[3];
  }
  ctx.putImageData(img, 0, 0);
  return canvas;
}

async function fetchWorldCoverBitmap(item, west, south, east, north) {
  // 1) Área exacta, alta resolución
  try {
    const url = `${PC_BBOX_BASE_URL}/${west},${south},${east},${north}/${FUEL_IMAGE_PX}x${FUEL_IMAGE_PX}.png` +
      `?collection=esa-worldcover&item=${item.id}&assets=map&colormap_name=esa-worldcover`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const bitmap = await createImageBitmap(await res.blob());
    return { bitmap, crop: [0, 0, bitmap.width, bitmap.height], detail: 'alta resolución' };
  } catch (err) {
    console.warn('[FuelLayer] Endpoint bbox falló, se usa la vista previa del tile:', err.message);
  }

  // 2) Respaldo: vista previa del tile completo, recortada
  const previewUrl = `${PC_PREVIEW_BASE_URL}?collection=esa-worldcover&item=${item.id}` +
    `&assets=map&colormap_name=esa-worldcover&format=png&width=2048&height=2048&max_size=2048`;
  const imgRes = await fetch(previewUrl);
  if (!imgRes.ok) throw new Error(`No se pudo descargar la imagen (HTTP ${imgRes.status})`);
  const bitmap = await createImageBitmap(await imgRes.blob());
  const [iWest, iSouth, iEast, iNorth] = item.bbox;
  const px0 = Math.max(0, Math.round((west - iWest) / (iEast - iWest) * bitmap.width));
  const px1 = Math.min(bitmap.width, Math.round((east - iWest) / (iEast - iWest) * bitmap.width));
  const py0 = Math.max(0, Math.round((iNorth - north) / (iNorth - iSouth) * bitmap.height));
  const py1 = Math.min(bitmap.height, Math.round((iNorth - south) / (iNorth - iSouth) * bitmap.height));
  return { bitmap, crop: [px0, py0, Math.max(1, px1 - px0), Math.max(1, py1 - py0)], detail: 'resolución reducida' };
}

// ---------- grillas de combustible cargadas (una por área) ----------
const FUEL_GRID_PROMISES = new Map(); // clave de bounds -> Promise<FuelGrid>
const FUEL_GRIDS_READY = [];          // FuelGrid ya cargadas

/** Grilla que contiene el punto (la más reciente si hay varias), o null. */
function fuelGridFor(lat, lon) {
  for (let i = FUEL_GRIDS_READY.length - 1; i >= 0; i--) {
    if (FUEL_GRIDS_READY[i].contains(lat, lon)) return FUEL_GRIDS_READY[i];
  }
  return null;
}

/**
 * Guarda la grilla en el archivo histórico (SQLite, vía server.py) una sola vez por área.
 * Sin proxy no hace nada. Nunca lanza: el archivo es un extra, no debe romper el mapa.
 */
async function archiveFuelGrid(grid) {
  if (!FW.proxy || !FW.archive) return;
  try {
    const key = grid.areaKey();
    const has = await (await fetch(`/api/store/has?key=${encodeURIComponent(key)}`)).json();
    if (has.exists) return;
    const res = await fetch('/api/store/fuelgrid', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(grid.toArchivePayload()),
    });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const out = await res.json();
    console.info(`[Archivo] grilla de combustible guardada: ${out.cells} celdas`);
    refreshArchiveStatus();
  } catch (err) {
    console.warn('[Archivo] no se pudo guardar la grilla de combustible:', err);
  }
}

/** Descarga y clasifica WorldCover para un área. `bounds` = [[oeste,sur],[este,norte]]. */
function loadFuelGrid(bounds) {
  const key = bounds.flat().map((x) => x.toFixed(3)).join(',');
  if (FUEL_GRID_PROMISES.has(key)) return FUEL_GRID_PROMISES.get(key);
  const p = (async () => {
    const [west, south] = bounds[0];
    const [east, north] = bounds[1];
    const searchRes = await fetch(PC_STAC_SEARCH_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ collections: ['esa-worldcover'], bbox: [west, south, east, north], limit: 1 }),
    });
    if (!searchRes.ok) throw new Error(`Búsqueda STAC falló (HTTP ${searchRes.status})`);
    const item = ((await searchRes.json()).features || [])[0];
    if (!item) throw new Error('No se encontró ningún tile de WorldCover para esta zona.');

    const { bitmap, crop, detail } = await fetchWorldCoverBitmap(item, west, south, east, north);
    const c = classifyWorldCover(bitmap, crop[0], crop[1], crop[2], crop[3], crop[2], crop[3]);
    const grid = new FuelGrid(c.cls, c.w, c.h, bounds, { detail });
    FUEL_GRIDS_READY.push(grid);
    if (detail !== 'resolución reducida') setTimeout(() => archiveFuelGrid(grid), 1500); // no frena el mapa
    return grid;
  })();
  FUEL_GRID_PROMISES.set(key, p);
  p.catch(() => FUEL_GRID_PROMISES.delete(key)); // permite reintentar
  return p;
}

async function addFuelCoverLayer(map, area) {
  if (map.getSource('worldcover-image')) return; // ya agregada

  const bounds = boundsFromCenterKm(area.center, area.radiusKm);
  const [west, south] = bounds[0];
  const [east, north] = bounds[1];

  try {
    const grid = await loadFuelGrid(bounds);
    if (map.getSource('worldcover-image')) return;

    map.addSource('worldcover-image', {
      type: 'image',
      url: paintFuelGrid(grid).toDataURL(),
      coordinates: [[west, north], [east, north], [east, south], [west, south]],
    });
    // Orden (de abajo hacia arriba): vegetación < mapa de calor < zonas locales < focos.
    const before = map.getLayer('temp-heat-layer') ? 'temp-heat-layer'
      : (map.getLayer('fire-pixels-glow') ? 'fire-pixels-glow' : undefined);
    map.addLayer({
      id: 'worldcover-layer', type: 'raster', source: 'worldcover-image',
      paint: { 'raster-opacity': 0.6, 'raster-fade-duration': 0 },
    }, before);
    const toggle = document.getElementById('toggle-fuel');
    if (toggle && !toggle.checked) setFuelCoverVisible(map, false);
    reportSource('fuel', 'ok', `ESA WorldCover 2021 · ${grid.meta.detail} · grilla de celdas de ${FUEL_CELL_M} m`);
    document.dispatchEvent(new CustomEvent('fuelgrid', { detail: grid }));
  } catch (err) {
    console.warn('[FuelLayer] No se pudo cargar ESA WorldCover (Planetary Computer):', err);
    reportSource('fuel', 'error', err.message);
    const el = document.getElementById('status-text');
    if (el) {
      el.textContent = '⚠️ No se pudo cargar la cobertura vegetal: ' + err.message;
      el.style.color = '#e5484d';
    }
    const toggle = document.getElementById('toggle-fuel');
    if (toggle) toggle.disabled = true;
  }
}

function setFuelCoverVisible(map, visible) {
  if (map.getLayer('worldcover-layer')) {
    map.setLayoutProperty('worldcover-layer', 'visibility', visible ? 'visible' : 'none');
  }
}
