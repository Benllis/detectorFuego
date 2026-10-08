# FireWatch — Mapa 3D

Subproyecto independiente de `firewatch-project-web/`, `-desktop/` y
`-camara/`. Es la base real del mapa 3D: calles y edificios reales,
terreno 3D real, viento animado con dirección real, temperatura, humedad,
cobertura vegetal, fuentes de agua, grifos, estaciones de bomberos,
focos de calor satelitales y estaciones meteorológicas oficiales —
centrado en el Gran Santiago. No incluye todavía la simulación de
propagación de incendio.

## Cómo correrlo

Solo hace falta Python 3.8+ (sin instalar paquetes). Desde esta carpeta:

```bash
python server.py
```

Abrir `http://localhost:8080`. `server.py` reemplaza a
`python -m http.server`: sirve el mapa y además hace de **proxy local con
caché** para las fuentes que piden credenciales.

### Credenciales (opcional pero recomendado)

1. Copiar `.env.example` como `.env` (está en `.gitignore`, no se sube).
2. Completar:
   - `FIRMS_MAP_KEY` — gratis: <https://firms.modaps.eosdis.nasa.gov/api/map_key/>
   - `TOMTOM_KEY` — opcional (tráfico en vivo y rutas con tráfico), gratis y sin tarjeta: <https://developer.tomtom.com>
   - `DMC_USUARIO` + `DMC_TOKEN` — gratis, registro en el portal de la
     Dirección Meteorológica de Chile (<https://climatologia.meteochile.gob.cl>,
     sección de web services).

Sin `.env` el mapa igual funciona: se pierden los focos FIRMS y las
mediciones de la DMC (las 11 estaciones del área se muestran en gris).
Sin el proxy (por ejemplo si se sirve como sitio estático) pasa lo mismo.
Ninguna key va en el código del navegador.

## Qué es real y qué no

| Elemento | Estado |
|---|---|
| Mapa (calles, edificios) | **Real** — OpenStreetMap vía OpenFreeMap |
| Terreno 3D | **Real** — Mapterhorn |
| Viento, temperatura, humedad | **Real, en vivo** — Open-Meteo (modelo, no medición instrumental). 1 petición para los 64 puntos, refresco cada 60 min |
| Estaciones DMC (Red EMA) | **Real, medición instrumental** — 11 estaciones en el área. Requiere token (gratis). Se compara contra el modelo (panel "Modelo vs. DMC") |
| Mapa de calor | **Real, en vivo** — Open-Meteo. Tres modos: **Riesgo de incendio** (índice 0-100 propio: humedad, temperatura, viento; escala absoluta, ver METODOLOGIA §5b), **Temperatura real** (escala relativa) y **Temperatura ajustada a 600 m** (quita el efecto de la altura, §5d) |
| Zona de calor local (2.5 km) | **Real** — se genera sola alrededor de cada foco NASA FIRMS; si no hay focos, no aparece nada (normal). También se genera en replay / día anterior, con el clima histórico de la hora del foco |
| Sensor(es) de cámara | **Real** — `camera_config.json` |
| Cobertura vegetal | **Real** — ESA WorldCover vía Planetary Computer (año 2021). Imagen recoloreada por riesgo por clase (matorral > bosque > pastizal > cultivo), todavía no un dato consultable por celda |
| Fuentes de agua / grifos / estaciones de bomberos | **Real** — OpenStreetMap vía Overpass (crowdsourced, cobertura desigual). Ríos con trazado real. Caché de 24 h |
| Focos de calor satelitales | **Real** — NASA FIRMS (VIIRS NOAA-20, NOAA-21 y Suomi-NPP), vía el proxy. Incluye **replay** de incendios históricos y **"ayer"**, con el clima real de ese momento (Open-Meteo archivo, METODOLOGIA §5c) |
| Área de cobertura | **Fija** — Gran Santiago (28 km), no se mueve al navegar |

El panel **"Estado de las fuentes"** (arriba a la derecha) muestra, para
cada fuente, si cargó, cuántos datos trajo, si vienen de caché o si son
un dato viejo porque el servicio estaba caído.

## Documentación

- [`METODOLOGIA.md`](./METODOLOGIA.md) — cómo se obtiene el dato de cada
  API (endpoint, parámetros, qué devuelve, frecuencia, licencia y
  limitaciones) y resultados de las pruebas en vivo del 28-09-2026.
  Pensado para la memoria y para responder a la comisión.

## Decisiones de diseño importantes

- **Proxy local en vez de keys en el navegador**: el token de la DMC no
  puede ir en un JS que corre en el cliente. El mismo proxy cachea
  Open-Meteo (límite gratuito de 10.000 llamadas/día por IP) y FIRMS.
  Pendiente si se necesita publicarlo online: migrar `server.py` a un
  serverless gratuito (p. ej. Cloudflare Workers) con los mismos
  endpoints `/api/*`.
- **Open-Meteo en una sola petición** (64 puntos, verificado en vivo) y
  refresco cada 60 min, porque el modelo se recalcula cada hora.
- **Overpass se consulta capa por capa**, con reintento, tope de tiempo,
  mirror de respaldo (`maps.mail.ru`; `overpass.kumi.systems` y
  `overpass.private.coffee` no respondieron el 28-09-2026) y caché de 24 h
  (`overpass-utils.js`).
- **Área fija en Santiago, no "todo Chile"**: se probó con tiles de
  OpenWeatherMap y se descartó porque esos tiles no muestran dirección de
  viento (bitácora, Sesión 07).
- **Mapa de calor con borde suave**: llena el cuadrado del área pero
  desvanece los bordes (sin esquinas duras). La cobertura vegetal se pide
  para el área exacta, recoloreada por riesgo y sin desenfoque.
- **Focos FIRMS y zona de calor local, un solo interruptor**: la zona
  (relleno amarillo→rojo, contorno y rótulo) se genera alrededor de cada
  foco y se enciende/apaga junto con los puntos.

## Estructura

```
firewatch-project-mapa/
├── index.html
├── server.py                  → servidor local + proxy con caché (DMC, FIRMS, Open-Meteo)
├── storage.py                 → archivo histórico en SQLite (data/firewatch.db, fuera de git)
├── .env.example               → plantilla de credenciales (copiar a .env)
├── css/style.css
├── METODOLOGIA.md
├── tools/recolector.py        → guarda clima/focos/estaciones sin el mapa abierto (`--una-vez`, `--cada 60`, `--terreno`, `--stats`, `--resumen`, `--log`)
├── tools/historico.py         → respaldo histórico: focos FIRMS pasados, eventos de incendio y clima (`--historico`, `--eventos`)
├── tools/trafico.py           → foto del tráfico (tiles vectoriales de TomTom) guardada en SQLite (`--trafico`, `--trafico-cada`, `--trafico-resumen`)
├── tests/test_trafico.py      → pruebas del tráfico guardado (`python tests/test_trafico.py`; usa `tests/mvt_encode.py`)
├── tests/test_historico.py    → pruebas del histórico (`python tests/test_historico.py`)
├── tests/test_storage.py      → archivo histórico y recolector (`python tests/test_storage.py`)
├── tools/calibrar_indice.js   → reproduce la comprobación del índice con incendios reales (Node 18+)
├── tests/routing.test.js      → lógica pura de rutas (`node tests/routing.test.js`)
├── tests/test_route.py        → endpoint /api/route del proxy (`python tests/test_route.py`)
├── tests/fuel-grid.test.js    → pruebas de la grilla de combustible y pendiente (`node tests/fuel-grid.test.js`)
└── js/
    ├── config.js              → cámaras, área, estilo de mapa, satélites y presets de replay
    ├── api.js                 → detección del proxy, caché local y registro de estado de fuentes
    ├── weather-grid.js        → grilla de viento/temperatura/humedad + interpolación
    ├── map-init.js            → MapLibre, terreno 3D, cámaras, círculo de referencia
    ├── wind-layer.js          → partículas de viento animadas
    ├── fuel-grid.js           → grilla de combustible CONSULTABLE por celda de 250 m (sin DOM, probable en Node)
    ├── heat-layer.js          → mapa de calor (reutilizable: ciudad y zonas locales)
    ├── fuel-layer.js          → cobertura vegetal (ESA WorldCover): imagen + construye la grilla
    ├── overpass-utils.js      → fetch a Overpass con reintento, mirror y caché
    ├── water-layer.js         → fuentes de agua y ríos (Overpass)
    ├── hydrants-layer.js      → grifos (Overpass)
    ├── firestations-layer.js  → estaciones de bomberos (Overpass)
    ├── nasa-firms-layer.js    → focos de calor satelitales + replay (NASA FIRMS)
    ├── local-fire-heat.js     → zonas de calor locales alrededor de focos FIRMS
    ├── inspector.js           → "Consultar un punto": altura, pendiente, vegetación, clima y riesgo
    ├── routing.js             → rutas de emergencia (estación → agua → foco) y capa de tráfico
    ├── chile-weather-layer.js → estaciones DMC + comparación modelo vs. medición
    ├── owm-layers.js          → OBSOLETO (vacío) — borrar el archivo (nada lo usa)
    └── main.js                → orquesta todo + controles
```

## Si algo no carga

- Mirar el panel **"Estado de las fuentes"**: dice cuál falló y por qué.
- **Cualquier capa de Overpass falla**: botón "🔄 Actualizar datos ahora"
  reintenta solo lo que falló. Si hay un dato guardado de hasta 24 h, se
  usa mientras tanto (aparece como "dato guardado").
- **NASA FIRMS dice que falta la key o el proxy**: crear `.env` y correr
  con `python server.py`.
- **Estaciones DMC en gris**: falta `DMC_TOKEN` en `.env`, o el token fue
  rechazado (el mensaje exacto sale en el panel). El crudo de la última
  respuesta queda en `cache/` para revisar el esquema real.
- **`tiles.openfreemap.org` / `tiles.mapterhorn.com` caídos**: cambiar
  `mapStyle` en `config.js` a `bright` o `positron`.

## Próximos pasos

1. Registrar el token DMC y confirmar el esquema real de
   `getDatosRecientesRedEma` (el normalizador de `server.py` es tolerante
   pero no se pudo probar contra datos reales sin token).
2. ~~Grilla de combustible consultable por celda~~ (hecho: `fuel-grid.js` +
   "Consultar un punto", ver METODOLOGIA.md §2b).
3. Simulación de propagación de incendio sobre este terreno real, con
   `findNearestWaterSource` y `findNearestFireStation` ya listos.
4. Rutas: ver METODOLOGIA.md §5e. Falta probarlas contra OSRM/TomTom reales, evitar el humo y, más adelante, OpenRouteService o un OSRM/Valhalla propio.
5. Archivo histórico: dejar corriendo el recolector varios días (METODOLOGIA.md §5f) y correr `--historico` para traer incendios pasados (§5f); falta área quemada real (MODIS MCD64A1/CONAF) para validar.
6. Backend real cámara↔central (la prioridad más alta de todo FireWatch).

## Atribuciones requeridas

- Mapa: © OpenStreetMap contributors, vía OpenFreeMap
- Terreno: Mapterhorn
- Viento/temperatura/humedad: Open-Meteo.com (CC BY 4.0)
- Estaciones meteorológicas: Dirección Meteorológica de Chile (DMC)
- Cobertura vegetal: ESA WorldCover, vía Microsoft Planetary Computer
- Agua, grifos y bomberos: © OpenStreetMap contributors, vía Overpass API
- Focos de calor: NASA FIRMS
