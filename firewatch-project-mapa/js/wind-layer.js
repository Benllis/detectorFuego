// ============================================================
// FireWatch · Mapa 3D — capa de viento animada
//
// Un canvas transparente flota sobre el mapa. Cientos de partículas se
// mueven según el vector de viento interpolado en su posición
// (WeatherGrid.sample), dejando una estela que se desvanece. Color =
// velocidad del viento (azul=calma → rojo=fuerte). Misma idea que
// windy.com / earth.nullschool.net — muestra DIRECCIÓN real del viento.
// ============================================================

function clampWL(x, min, max) { return Math.min(max, Math.max(min, x)); }

function windSpeedToColor(speedKmh) {
  const stops = [
    { v: 0, c: [59, 130, 246] },
    { v: 15, c: [47, 174, 109] },
    { v: 30, c: [232, 185, 61] },
    { v: 50, c: [229, 72, 77] },
  ];
  let a = stops[0], b = stops[stops.length - 1];
  for (let i = 0; i < stops.length - 1; i++) {
    if (speedKmh >= stops[i].v && speedKmh <= stops[i + 1].v) { a = stops[i]; b = stops[i + 1]; break; }
  }
  if (speedKmh > stops[stops.length - 1].v) { a = b = stops[stops.length - 1]; }
  const t = b.v > a.v ? clampWL((speedKmh - a.v) / (b.v - a.v), 0, 1) : 1;
  const r = Math.round(a.c[0] + (b.c[0] - a.c[0]) * t);
  const g = Math.round(a.c[1] + (b.c[1] - a.c[1]) * t);
  const bl = Math.round(a.c[2] + (b.c[2] - a.c[2]) * t);
  return `rgb(${r},${g},${bl})`;
}

class WindParticleLayer {
  constructor(map, weatherGrid, particleCount = 700) {
    this.map = map;
    this.grid = weatherGrid;
    this.particleCount = particleCount;
    this.running = false;

    this.canvas = document.createElement('canvas');
    this.canvas.style.position = 'absolute';
    this.canvas.style.top = '0';
    this.canvas.style.left = '0';
    this.canvas.style.pointerEvents = 'none';
    map.getCanvasContainer().appendChild(this.canvas);
    this.ctx = this.canvas.getContext('2d');

    this._resize();
    this.particles = [];
    this._seedParticles();

    this._onMove = () => this._resize();
    map.on('move', this._onMove);
    window.addEventListener('resize', this._onMove);
  }

  _resize() {
    const c = this.map.getCanvas();
    this.canvas.width = c.clientWidth;
    this.canvas.height = c.clientHeight;
    this.canvas.style.width = c.clientWidth + 'px';
    this.canvas.style.height = c.clientHeight + 'px';
  }

  _randomPoint() {
    const b = this.map.getBounds();
    let pt = null;
    // Con la grilla nacional, solo nacen partículas donde hay dato y dentro de Chile (hasta 8 intentos).
    for (let i = 0; i < 8; i++) {
      pt = {
        lon: b.getWest() + Math.random() * (b.getEast() - b.getWest()),
        lat: b.getSouth() + Math.random() * (b.getNorth() - b.getSouth()),
        age: Math.random() * 80,
      };
      const inChile = typeof chileMaskAlpha !== 'function' || chileMaskAlpha(pt.lat, pt.lon) > 0.3;
      if (inChile && this.grid && this.grid.loaded && this.grid.sample(pt.lat, pt.lon)) break;
    }
    return pt;
  }

  _seedParticles() {
    this.particles = Array.from({ length: this.particleCount }, () => this._randomPoint());
  }

  start() {
    if (this.running) return;
    this.running = true;
    this._tick();
  }

  stop() {
    this.running = false;
    if (this._raf) cancelAnimationFrame(this._raf);
  }

  setVisible(visible) {
    this.canvas.style.display = visible ? 'block' : 'none';
  }

  destroy() {
    this.stop();
    this.map.off('move', this._onMove);
    window.removeEventListener('resize', this._onMove);
    this.canvas.remove();
  }

  _tick() {
    if (!this.running) return;
    const ctx = this.ctx;

    ctx.globalCompositeOperation = 'destination-in';
    ctx.fillStyle = 'rgba(0,0,0,0.90)';
    ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);
    ctx.globalCompositeOperation = 'source-over';

    const bounds = this.map.getBounds();
    // A escala de país el paso en grados sería invisible: crece al alejar el zoom.
    const zoomK = clampWL(Math.pow(2, 11 - this.map.getZoom()), 1, 80);

    for (const p of this.particles) {
      const w = this.grid.sample(p.lat, p.lon);
      if (!w) { Object.assign(p, this._randomPoint()); continue; }

      const bearing = ((w.dirDeg + 180) % 360) * Math.PI / 180;
      const step = 0.00022 * zoomK * (0.35 + w.speed / 35);
      const dLat = Math.cos(bearing) * step;
      const dLon = Math.sin(bearing) * step / Math.max(0.15, Math.cos(p.lat * Math.PI / 180));

      const prevScreen = this.map.project([p.lon, p.lat]);
      p.lon += dLon;
      p.lat += dLat;
      p.age += 1;
      const nextScreen = this.map.project([p.lon, p.lat]);

      ctx.strokeStyle = windSpeedToColor(w.speed);
      ctx.lineWidth = 1.4;
      ctx.beginPath();
      ctx.moveTo(prevScreen.x, prevScreen.y);
      ctx.lineTo(nextScreen.x, nextScreen.y);
      ctx.stroke();

      if (p.age > 110 || !bounds.contains([p.lon, p.lat]) || (typeof chileMaskAlpha === 'function' && chileMaskAlpha(p.lat, p.lon) < 0.05)) {
        Object.assign(p, this._randomPoint());
      }
    }

    this._raf = requestAnimationFrame(() => this._tick());
  }
}
