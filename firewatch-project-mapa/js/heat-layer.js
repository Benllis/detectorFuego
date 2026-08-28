// ============================================================
// FireWatch · Mapa 3D — capa de calor (temperatura real, continua)
//
// v2 — arquitectura corregida por rendimiento.
// La primera versión recalculaba toda la superficie (miles de celdas)
// en CADA evento 'move' del mapa, que se dispara muchas veces por
// segundo mientras se arrastra/hace zoom -> caída notoria de FPS.
//
// Solución correcta: se renderiza la superficie interpolada UNA sola
// vez (o cada vez que llegan datos nuevos) en un canvas chico, anclado
// a las 4 esquinas geográficas reales de la grilla, y se registra como
// una fuente de tipo 'image' en MapLibre. A partir de ahí, MapLibre
// mueve/rota/escala esa imagen por GPU junto con el resto del mapa —
// igual que cualquier otra capa raster — sin que este código tenga que
// recalcular nada mientras el usuario navega.
// ============================================================

function tempToColor(temp) {
  const stops = [
    { v: 0, c: [59, 130, 246] },   // frío — azul
    { v: 15, c: [47, 174, 109] },  // templado — verde
    { v: 25, c: [232, 185, 61] },  // cálido — amarillo
    { v: 35, c: [229, 72, 77] },   // caluroso — rojo
  ];
  let a = stops[0], b = stops[stops.length - 1];
  for (let i = 0; i < stops.length - 1; i++) {
    if (temp >= stops[i].v && temp <= stops[i + 1].v) { a = stops[i]; b = stops[i + 1]; break; }
  }
  if (temp > stops[stops.length - 1].v) { a = b = stops[stops.length - 1]; }
  if (temp < stops[0].v) { a = b = stops[0]; }
  const t = b.v > a.v ? Math.min(1, Math.max(0, (temp - a.v) / (b.v - a.v))) : 1;
  return [
    Math.round(a.c[0] + (b.c[0] - a.c[0]) * t),
    Math.round(a.c[1] + (b.c[1] - a.c[1]) * t),
    Math.round(a.c[2] + (b.c[2] - a.c[2]) * t),
  ];
}

class TemperatureHeatLayer {
  constructor(map, weatherGrid, resolution = 160) {
    this.map = map;
    this.grid = weatherGrid;
    this.resolution = resolution; // resolución del canvas FUENTE en px — fija, no depende del zoom de pantalla
    this.sourceId = 'temp-heat-image';
    this.layerId = 'temp-heat-layer';
    this._added = false;

    this.canvas = document.createElement('canvas');
    this.canvas.width = resolution;
    this.canvas.height = resolution;
    this.ctx = this.canvas.getContext('2d');
  }

  /** Esquinas geográficas de la grilla, en el orden que espera una fuente 'image' de MapLibre: [NO, NE, SE, SO]. */
  _bboxCoordinates() {
    const first = this.grid.points[0];                              // esquina suroeste (lat/lon mínimos)
    const last = this.grid.points[this.grid.points.length - 1];      // esquina noreste (lat/lon máximos)
    return [
      [first.lon, last.lat],
      [last.lon, last.lat],
      [last.lon, first.lat],
      [first.lon, first.lat],
    ];
  }

  /** Pinta la superficie interpolada en el canvas fuente (se llama solo al cargar/actualizar datos). */
  _renderCanvas() {
    const size = this.resolution;
    const ctx = this.ctx;
    const imgData = ctx.createImageData(size, size);
    const first = this.grid.points[0];
    const last = this.grid.points[this.grid.points.length - 1];

    // Recorte circular: el cuadrado de datos cubre exactamente el radio de
    // cobertura en cada dirección, así que el círculo inscrito en ese
    // cuadrado coincide con el borde punteado real dibujado en el mapa.
    const cx = size / 2, cy = size / 2, r = size / 2;

    for (let py = 0; py < size; py++) {
      const lat = last.lat - (py / (size - 1)) * (last.lat - first.lat); // fila 0 = borde norte
      for (let px = 0; px < size; px++) {
        const idx = (py * size + px) * 4;
        const dx = px + 0.5 - cx, dy = py + 0.5 - cy;
        if (dx * dx + dy * dy > r * r) { imgData.data[idx + 3] = 0; continue; } // fuera del círculo: transparente
        const lon = first.lon + (px / (size - 1)) * (last.lon - first.lon);
        const w = this.grid.sample(lat, lon);
        if (!w) { imgData.data[idx + 3] = 0; continue; } // fuera del área con datos: transparente
        const [r2, g, b] = tempToColor(w.temp);
        imgData.data[idx] = r2;
        imgData.data[idx + 1] = g;
        imgData.data[idx + 2] = b;
        imgData.data[idx + 3] = 140; // ~55% opacidad
      }
    }
    ctx.putImageData(imgData, 0, 0);
  }

  /** Renderiza y (re)publica la imagen en el mapa. Llamar solo cuando cambian los datos, no en cada frame. */
  render() {
    if (!this.grid.loaded) return;
    this._renderCanvas();
    const dataUrl = this.canvas.toDataURL();

    if (!this._added) {
      this.map.addSource(this.sourceId, {
        type: 'image',
        url: dataUrl,
        coordinates: this._bboxCoordinates(),
      });
      this.map.addLayer({
        id: this.layerId, type: 'raster', source: this.sourceId,
        paint: { 'raster-fade-duration': 0 },
      });
      this._added = true;
    } else {
      this.map.getSource(this.sourceId).updateImage({ url: dataUrl });
    }
  }

  setVisible(visible) {
    if (this.map.getLayer(this.layerId)) {
      this.map.setLayoutProperty(this.layerId, 'visibility', visible ? 'visible' : 'none');
    }
  }

  destroy() {
    if (this.map.getLayer(this.layerId)) this.map.removeLayer(this.layerId);
    if (this.map.getSource(this.sourceId)) this.map.removeSource(this.sourceId);
  }
}