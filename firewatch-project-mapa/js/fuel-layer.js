// ============================================================
// FireWatch · Mapa 3D — capa de cobertura vegetal / riesgo de combustible
//
// v3 — migrado de Terrascope (WMS, servidor caído/poco confiable) a
// Microsoft Planetary Computer (Azure, mismo dataset ESA WorldCover).
//
// A diferencia de un WMS clásico, Planetary Computer organiza los datos
// como un catálogo STAC dividido en tiles de 3x3 grados. El flujo es:
//   1. Se busca en el catálogo STAC qué tile de WorldCover cubre el área
//      de interés (POST a /api/stac/v1/search).
//   2. Se pide la imagen ya renderizada de ese tile completo, con la
//      paleta de colores OFICIAL de ESA aplicada automáticamente
//      (colormap_name=esa-worldcover).
//   3. Se recorta esa imagen client-side (canvas) solo al área de interés
//      real (~30km, igual que la capa de calor) — el tile completo cubre
//      ~330km de lado y mostrarlo entero se ve desproporcionado frente al
//      resto de las capas.
//
// Fuente: ESA WorldCover (Copernicus Sentinel-1 + Sentinel-2, 10m,
// CC BY 4.0) vía Microsoft Planetary Computer, sin API key para este
// nivel de acceso.
// ============================================================

const PC_STAC_SEARCH_URL = 'https://planetarycomputer.microsoft.com/api/stac/v1/search';
const PC_PREVIEW_BASE_URL = 'https://planetarycomputer.microsoft.com/api/data/v1/item/preview.png';

// Clasificación ESA (11 clases oficiales) -> interpretación de riesgo de
// combustible. Solo se listan acá las relevantes para la zona central de
// Chile; el resto (nieve, manglar, musgo) prácticamente no aplican.
const WORLDCOVER_LEGEND = [
  { color: '#006400', label: 'Bosque / arbolado', risk: 'Alto' },
  { color: '#ffbb22', label: 'Matorral', risk: 'Alto' },
  { color: '#ffff4c', label: 'Pastizal', risk: 'Medio' },
  { color: '#f096ff', label: 'Cultivo agrícola', risk: 'Medio-bajo' },
  { color: '#fa0000', label: 'Urbano / construido', risk: 'Bajo' },
  { color: '#b4b4b4', label: 'Suelo desnudo / vegetación escasa', risk: 'Bajo' },
  { color: '#0064c8', label: 'Cuerpos de agua', risk: 'Nulo' },
];

async function addFuelCoverLayer(map, area) {
  if (map.getSource('worldcover-image')) return; // ya agregada

  const bounds = boundsFromCenterKm(area.center, area.radiusKm * 1.1); // [[west,south],[east,north]]
  const [west, south] = bounds[0];
  const [east, north] = bounds[1];

  try {
    // 1. Buscar qué tile de WorldCover cubre esta área
    const searchRes = await fetch(PC_STAC_SEARCH_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        collections: ['esa-worldcover'],
        bbox: [west, south, east, north],
        limit: 1,
      }),
    });
    if (!searchRes.ok) throw new Error(`Búsqueda STAC falló (HTTP ${searchRes.status})`);
    const searchData = await searchRes.json();
    const item = searchData.features && searchData.features[0];
    if (!item) throw new Error('No se encontró ningún tile de WorldCover para esta zona.');

    const itemBbox = item.bbox; // [west, south, east, north] del tile completo (~3x3 grados)

    // 2. Pedir la imagen ya renderizada de ese tile, con la paleta oficial de ESA.
    // Se pide en buena resolución porque después se recorta solo la zona de
    // interés (mucho más chica que el tile completo) — si se pidiera a baja
    // resolución, el recorte final se vería borroso/pixelado.
    const previewUrl = `${PC_PREVIEW_BASE_URL}?collection=esa-worldcover&item=${item.id}` +
      `&assets=map&colormap_name=esa-worldcover&format=png&width=2048&height=2048&max_size=2048`;

    // 3. Recortar client-side solo el área de interés (mismo tamaño que la
    // capa de calor), en vez de mostrar el tile completo de ~330 km.
    // Se recorta con canvas a partir de la imagen ya descargada — sin pedir
    // nada nuevo a Planetary Computer, sin arriesgar otro endpoint distinto.
    const imgRes = await fetch(previewUrl);
    if (!imgRes.ok) throw new Error(`No se pudo descargar la imagen (HTTP ${imgRes.status})`);
    const blob = await imgRes.blob();
    const bitmap = await createImageBitmap(blob);

    const [iWest, iSouth, iEast, iNorth] = itemBbox;
    const px0 = Math.max(0, Math.round((west - iWest) / (iEast - iWest) * bitmap.width));
    const px1 = Math.min(bitmap.width, Math.round((east - iWest) / (iEast - iWest) * bitmap.width));
    const py0 = Math.max(0, Math.round((iNorth - north) / (iNorth - iSouth) * bitmap.height));
    const py1 = Math.min(bitmap.height, Math.round((iNorth - south) / (iNorth - iSouth) * bitmap.height));
    const cropW = Math.max(1, px1 - px0);
    const cropH = Math.max(1, py1 - py0);

    const canvas = document.createElement('canvas');
    canvas.width = cropW;
    canvas.height = cropH;
    const ctx = canvas.getContext('2d');
    // Desenfoque suave: la cobertura vegetal es un dato categórico (cada
    // pixel es "esto ES bosque" o "esto ES urbano", sin términos medios),
    // así que se ve como mosaico de bloques sólidos. El blur lo hace sentir
    // más como una capa continua/suave, en la línea del mapa de calor,
    // aunque el dato de fondo siga siendo categórico.
    ctx.filter = 'blur(6px)';
    ctx.drawImage(bitmap, px0, py0, cropW, cropH, 0, 0, cropW, cropH);
    ctx.filter = 'none';

    // Recorte circular, para que coincida con el borde punteado real de
    // cobertura. El recorte de imagen (cropW/cropH) se pidió con un margen
    // extra (radiusKm*1.1) para que el blur no corte feo en los bordes —
    // por eso el círculo real es más chico que el canvas completo.
    const circleRx = (cropW / 2) / 1.1;
    const circleRy = (cropH / 2) / 1.1;
    ctx.globalCompositeOperation = 'destination-in';
    ctx.beginPath();
    ctx.ellipse(cropW / 2, cropH / 2, circleRx, circleRy, 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.globalCompositeOperation = 'source-over';

    const croppedUrl = canvas.toDataURL();

    map.addSource('worldcover-image', {
      type: 'image',
      url: croppedUrl,
      coordinates: [
        [west, north], // NO
        [east, north], // NE
        [east, south], // SE
        [west, south], // SO
      ],
    });
    map.addLayer({
      id: 'worldcover-layer',
      type: 'raster',
      source: 'worldcover-image',
      paint: { 'raster-opacity': 0.4 },
    });
  } catch (err) {
    // Si esta capa externa falla, no debe romper el resto del mapa ni
    // fallar en silencio — se avisa en la interfaz y el resto (viento,
    // temperatura, cámaras) sigue funcionando igual.
    console.warn('[FuelLayer] No se pudo cargar ESA WorldCover (Planetary Computer):', err);
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