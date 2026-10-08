# FireWatch — Metodología de datos (Mapa 3D)

Este documento explica **cómo** se obtiene el dato de cada fuente externa
usada en `firewatch-project-mapa/` — no solo qué servicio se usa, sino el
mecanismo real: endpoint, parámetros, qué devuelve, cada cuánto se
actualiza y bajo qué licencia. Pensado para responder en la defensa a
"¿de dónde sale este dato exactamente?" sin improvisar.

> **Actualización 28-09-2026:** las fuentes fueron probadas en vivo (ver
> sección 9, con resultados, tiempos y problemas encontrados) y la capa
> de datos se reorganizó: las credenciales viven en un proxy local
> (`server.py`) y ninguna key está en el código del navegador.

---

## 1. Viento, temperatura y humedad — Open-Meteo

**Qué es:** servicio meteorológico europeo que agrega modelos numéricos de
predicción (incluye GFS de NOAA, ICON de Alemania, etc.), interpolados a
cualquier coordenada del mundo. **Es un modelo, no una medición
instrumental** en cada punto. Por eso se contrasta con las estaciones
reales de la DMC (sección 6).

**Cómo se obtiene:** UNA llamada HTTP GET con las 64 coordenadas de la
grilla separadas por coma (verificado el 28-09-2026: responde un arreglo
con un objeto por punto, ~0.6 s, sin datos nulos):

```
GET https://api.open-meteo.com/v1/forecast
    ?latitude={lat1,lat2,...}&longitude={lon1,lon2,...}
    &current=temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m
    &wind_speed_unit=kmh&timezone=UTC
```

(Antes se hacían 64 llamadas separadas. El uso de peticiones múltiples se
había descartado por una experiencia previa con datos incompletos; en la
prueba de 64 puntos vinieron completos, y el código igual valida que la
cantidad de resultados coincida con la de puntos y marca los faltantes.)

**Qué devuelve** (JSON): objeto `current` con `temperature_2m` (°C),
`relative_humidity_2m` (%), `wind_speed_10m` (km/h, a 10 m de altura,
estándar meteorológico) y `wind_direction_10m` (grados; convención
meteorológica: dirección **de donde viene** el viento, no hacia dónde va).

**Frecuencia y límites:** el modelo se recalcula cada hora, así que la
app pide datos cada 60 minutos (`CONFIG.autoRefreshMs`) o al presionar
"Actualizar datos ahora". Límites del plan gratuito (según los términos
del servicio): 600 llamadas/min, 5.000/hora, 10.000/día, 300.000/mes,
por IP. Con 64 puntos por hora son ~1.536 llamadas/día por usuario
(antes, con refresco cada 10 min, eran ~9.200/día). No está documentado
cómo cuenta una petición con varias coordenadas; se asume la peor
hipótesis (una llamada por coordenada). **Sin API key.** Uso gratuito solo
para proyectos sin suscripción ni publicidad; requiere atribución
(CC BY 4.0).

**Caché:** el proxy local guarda la respuesta 30 min (en memoria y
disco); sin proxy, el navegador la guarda 60 min (localStorage). Si
Open-Meteo falla, se muestra el último dato guardado marcado como
"dato viejo".

**Procesamiento propio** (`weather-grid.js`): los 64 puntos de la grilla
(8×8) se interpolan bilinealmente. El viento se interpola convirtiendo
velocidad+dirección a componentes vectoriales (u, v) antes de promediar,
porque promediar el ángulo directamente es incorrecto (350° y 10°
promedian a 0°, no a 180°). Un punto sin dato ya no cuenta como "viento
0" (antes distorsionaba los promedios).

**Mapa de calor:** la escala de color es **relativa** al mínimo/máximo
real del momento (no fija), con leyenda en °C, para que siempre haya
contraste visible aunque el día sea frío o cálido. Por lo mismo, no es
un índice de riesgo de incendio.

---

## 2. Cobertura vegetal / riesgo de combustible — ESA WorldCover vía Microsoft Planetary Computer

**Qué es:** clasificación satelital global de cobertura de suelo (11
categorías: bosque, matorral, pastizal, cultivo, urbano, agua…), 10 m de
resolución, de la Agencia Espacial Europea (Sentinel-1 radar +
Sentinel-2 óptico), año 2021.

**Cómo se obtiene** (dos pasos):
1. Búsqueda en el catálogo STAC de qué tile (3×3 grados) cubre el área:
   ```
   POST https://planetarycomputer.microsoft.com/api/stac/v1/search
   Body: { "collections": ["esa-worldcover"], "bbox": [...], "limit": 1 }
   ```
2. Imagen del área exacta con la paleta oficial de ESA (verificado en
   vivo el 28-09-2026: 1024 px, HTTP 200, 0.6 s, colores exactos de ESA):
   ```
   GET https://planetarycomputer.microsoft.com/api/data/v1/item/bbox/
       {oeste},{sur},{este},{norte}/1280x1280.png
       ?collection=esa-worldcover&item={id}&assets=map&colormap_name=esa-worldcover
   ```
   Respaldo si ese endpoint falla: `item/preview.png` del tile completo
   (2048 px sobre ~330 km, ~160 m/px) recortado en el navegador.

**Procesamiento propio:** cada clase de ESA se recolorea en el navegador
a un color de **riesgo de combustible** (matorral muy alto → bosque alto →
pastizal medio-alto → cultivo medio-bajo; urbano y suelo desnudo casi
transparentes; agua sin color). Sin desenfoque ni mezcla entre clases:
el dato es categórico y el blur anterior lo convertía en una mancha. La
asignación de riesgo por clase es una regla simple del proyecto, no un
dato de ESA.

**Limitaciones (decirlas así en la defensa):** es cobertura de suelo, no
un modelo de combustible real (no considera humedad, carga ni disposición
del material vegetal); y es del año 2021, sin actualización en vivo.
**Sin API key.** CC BY 4.0.

---

## 2b. Grilla de combustible consultable por celda

La cobertura vegetal ya no es solo una imagen: `js/fuel-grid.js` guarda la
clasificación de ESA WorldCover como **datos** en una grilla de celdas de
250 m alineada a la esquina suroeste del área. Cada celda resume los pixeles
(~45 m) que contiene: fracciones por clase, clase dominante y propensión de
combustible. Se puede preguntar por punto (`cell(lat, lon)`), por radio
(`around(lat, lon, km)`) o pedir todas las celdas (`toCells()`), que es la
entrada prevista para la simulación de propagación.

**Propensión de combustible (`fuel`, 0-1)** por clase: matorral 1.0, bosque
0.8, pastizal 0.75, cultivo 0.4, urbano 0.1, suelo desnudo 0.05, humedal y
manglar 0.2, musgo 0.15, nieve y agua 0. **Es una regla del proyecto, no un
dato medido**: una clase de cobertura no dice carga, humedad ni continuidad
del combustible (los modelos reales, como Anderson o Scott & Burgan, sí).
Debe validarse contra incendios pasados antes de usarla para algo más que
priorizar.

**Riesgo con combustible** (modo "Riesgo × combustible"):
`índice_meteorológico × (0.25 + 0.75 × fuel)`. Sin combustible queda en el
25 % del índice; sobre matorral, el 100 %.

**Consultar un punto** (casilla en el panel, apagada por defecto): un clic
muestra altura, pendiente y exposición (5 alturas de la API de elevación de
Open-Meteo, DEM de 90 m, en el punto y a ±200 m; pendiente por diferencias
centrales, exposición = hacia dónde mira la ladera), vegetación de la celda,
clima (actual o del escenario) y riesgo con y sin combustible. Si la API de
elevación falla, el resto del popup se muestra igual. La pendiente de un DEM
de 90 m suaviza los cerros pequeños.

Prueba en vivo (29-09-2026): la API de elevación devolvió 200 en ~250 ms.
Pruebas automáticas: `node tests/fuel-grid.test.js`.

## 3. Fuentes de agua, grifos y estaciones de bomberos — OpenStreetMap vía Overpass API

**Qué es:** OpenStreetMap es un mapa colaborativo editado por
voluntarios; Overpass es el motor de consultas sobre esa base.

**Cómo se obtiene:** consulta en Overpass QL por POST:
```
POST https://overpass-api.de/api/interpreter
Body: data=<query>
```
Ejemplo (grifos, `hydrants-layer.js`):
```
[out:json][timeout:25];
node["emergency"="fire_hydrant"](south,west,north,east);
out 3000;
```
Fuentes de agua filtran `natural=water` + `water=lake|reservoir` o
`waterway=river`; estaciones de bomberos, `amenity=fire_station`.

**Confiabilidad del dato:** es **crowdsourced** — la cobertura varía
mucho por comuna. Que algo no aparezca no significa que no exista. Es
especialmente relevante para grifos; las estaciones de bomberos suelen
estar mejor mapeadas. No usar como única fuente para logística real sin
verificar contra el catastro oficial.

**Resiliencia del servicio:** el servidor público se satura (probado:
HTTP 504 y 429). Por eso (`overpass-utils.js`) las consultas se hacen
**una por una**, con reintento automático y un mirror de respaldo
(`overpass.kumi.systems`). **Sin API key.** ODbL.

---

## 4. Focos de calor satelitales — NASA FIRMS

**Qué es:** Fire Information for Resource Management System de la NASA:
detecciones reales de incendios activos vía satélite (sensor VIIRS de
los satélites NOAA-20, NOAA-21 y Suomi-NPP), casi en tiempo real.

**Cómo se obtiene** (a través del proxy local, que agrega la MAP_KEY):
```
GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/{MAP_KEY}
    /{SOURCE}/{west},{south},{east},{north}/{DAY_RANGE}[/{YYYY-MM-DD}]
```
- `SOURCE` en vivo: `VIIRS_NOAA20_NRT`, `VIIRS_SNPP_NRT`, `VIIRS_NOAA21_NRT`.
  Cada satélite pasa a distinta hora, por lo que consultar varios
  aumenta las pasadas por día. Los duplicados exactos se eliminan.
- `DAY_RANGE`: 1 a 5 días. La fecha opcional permite pedir el pasado.
- **Modo replay:** para fechas antiguas se usan las fuentes "SP" (standard
  processing: `VIIRS_NOAA20_SP`, `VIIRS_SNPP_SP`); las "NRT" solo cubren lo
  reciente. Verificado el 28-09-2026 con Valparaíso, 3-feb-2024: la API
  devolvió ~32 KB de detecciones. El replay existe para poder demostrar el
  mapa cuando en vivo no hay focos.

**Qué devuelve:** CSV con una fila por "pixel de fuego": coordenadas,
brillo, confianza (`l` baja, `n` nominal, `h` alta), fecha/hora UTC,
satélite y potencia radiativa (FRP, MW — proxy de intensidad). El mapa
muestra la hora convertida a hora de Chile.

**Frecuencia:** el satélite sobrevuela cada zona 1–2 veces al día por
satélite; "NRT" significa dato disponible ~60 minutos tras el sobrevuelo,
no actualización continua. La app refresca cada 30 minutos.

**Requiere MAP_KEY gratuita** (solo correo). Límite 5000
transacciones/10 min. El proxy cachea 30 min (en vivo) o 7 días
(replay). **CORS:** verificado el 28-09-2026 que la API responde con CORS
abierto desde el navegador; aun así se usa el proxy para no exponer la key.

**Limitaciones (decirlas así en la defensa):** un foco FIRMS **no es
necesariamente un incendio forestal** — puede ser quema agrícola
controlada, industria u otra fuente de calor intensa. Es una señal que
cobra sentido al cruzarla con cobertura vegetal, cercanía urbana y
alertas de cámara. En el replay el clima es el histórico de la hora del foco (§5c), no el de hoy.

---

## 5. Zonas de calor locales (cerca de focos FIRMS)

**Qué es:** un mini mapa de calor de **2.5 km de radio** centrado
automáticamente en cada foco real que detecte NASA FIRMS (`local-fire-heat.js`).

**Cómo funciona:** los focos FIRMS muy cercanos (< 1.5 km) se agrupan
como un mismo incendio; por cada grupo (máx. 5) se crea una grilla de
6×6 puntos de Open-Meteo alrededor de ese punto y se renderiza con la
misma clase del mapa de calor de la ciudad, pero más opaco para que
resalte. Así el "punto de inicio del fuego" sale de un dato real (el
satélite), sin clic manual ni backend.

**Si no hay focos activos** (lo normal), no se genera nada — no es un
error.

---

## 5b. Índice de riesgo de incendio (mapa de calor, modo "Riesgo")

**Qué es:** un índice de 0 a 100 **propio del proyecto** (no es un índice
oficial como el FFDI australiano o el FWI canadiense), calculado en el
navegador en cada punto de la grilla de Open-Meteo (sección 1):

```
riesgo = 100 × ( 0.5 × H + 0.3 × T + 0.2 × V )
H = (60 − humedad relativa %) / 50      → 0 con HR ≥ 60 %,  1 con HR ≤ 10 %
T = (temperatura °C − 5) / 30           → 0 con T ≤ 5 °C,   1 con T ≥ 35 °C
V = viento km/h / 40                    → 0 en calma,       1 con ≥ 40 km/h
(cada factor se limita a 0–1)
```

**Categorías (umbrales fijos):** Bajo 0–20, Moderado 20–40, Alto 40–60,
Muy alto 60–80, Extremo 80–100. La escala es **absoluta**: el mismo color
significa lo mismo cualquier día (a diferencia del modo Temperatura, que
estira los colores entre el mínimo y el máximo del momento).

**Pesos:** la humedad pesa más porque es el factor que más condiciona que
el material vegetal encienda; temperatura y viento la acompañan. Los
pesos y umbrales son una decisión de diseño del proyecto.

**Comprobación con incendios reales (28-09-2026):** se bajó el clima
horario (archivo de Open-Meteo, ERA5) de la temporada completa de cinco
incendios chilenos y se calculó el índice en las tardes (16-21 UTC ≈ 13-18 h
de Chile). Resultado, con los pesos 50/30/20:

| Incendio (coordenadas aproximadas) | Índice esa tarde | Categoría | Percentil en su temporada | T / HR / viento |
|---|---|---|---|---|
| Viña del Mar / Valparaíso, 2 feb 2024 | 62 | Muy alto | 93 | 30 °C / 38 % / 29 km/h |
| Viña del Mar / Valparaíso, 3 feb 2024 | 64 | Muy alto | 95 | 28 °C / 34 % / 31 km/h |
| Santa Juana (Biobío), 3 feb 2023 | 85 | Extremo | 98 | 39 °C / 17 % / 24 km/h |
| Constitución / Santa Olga (Maule), 25 ene 2017 | 85 | Extremo | 98 | 36 °C / 17 % / 23 km/h |
| Valparaíso, La Pólvora, 12 abr 2014 | 45 | Alto | 100 | 18 °C / 43 % / 30 km/h |

En una tarde de verano típica el índice cae en **Moderado** (en Viña del
Mar: 3 % Bajo, 55 % Moderado, 34 % Alto, 9 % Muy alto; en el interior más
caliente, hasta 25 % Muy alto/Extremo), y los cuatro incendios de verano
caen en **Muy alto o Extremo**. Se probaron pesos alternativos (40/30/30,
60/25/15, 45/25/30): el percentil de los eventos casi no cambia (91-100),
así que **no hay evidencia para modificar los pesos** y se mantuvieron.
Reproducible con `node tools/calibrar_indice.js`.

**Lo que esta comprobación NO demuestra:** solo se miraron días CON
incendio. Sin días sin incendio no se puede medir cuántas falsas alarmas
da el índice (hay muchas tardes calurosas y secas sin ignición), ni su
capacidad predictiva. Cinco casos con coordenadas aproximadas son una
comprobación de coherencia, no una validación estadística. El incendio de
abril de 2014 (viento fuerte, día fresco) sale solo en Alto: el índice
subestima incendios impulsados por el viento con humedad media.

**Limitaciones (decirlas así en la defensa):** no incluye lluvia de días
anteriores ni sequedad del combustible (los índices oficiales sí), ni la
vegetación ni la pendiente. Es una señal de condiciones meteorológicas,
no un pronóstico de que habrá un incendio. Si el proyecto lo requiere, el
siguiente paso es adaptar un índice publicado (FWI) y citarlo. Las zonas
locales (sección 5) muestran además el índice en el punto del foco.

---

## 5c. Escenarios: replay y "día anterior" con el clima del momento

**Qué es:** el selector "Replay: focos + clima del momento" muestra los
focos FIRMS de un día pasado **junto con el clima real de esa hora**, y
con eso el mapa de calor (riesgo o temperatura), el viento y las zonas
locales corresponden a ese día y no al de hoy. Opciones: el incendio de
Viña del Mar / Valparaíso (3-feb-2024), "Ayer" en Santiago, o una fecha a
elección en Santiago.

**Cómo se obtiene:**
1. Focos: `GET /api/firms?source=...&bbox=...&days=...&date=YYYY-MM-DD` (§4).
   Fechas de los últimos ~90 días: fuentes NRT primero; más antiguas: "SP".
   Si la primera familia no trae nada se prueba la otra.
2. Hora del clima: la del **foco más intenso** (mayor potencia radiativa) del
   día; sin focos, las 18:00 UTC (~15 h de Chile). Cada zona local usa la
   hora de su propio foco más intenso.
3. Clima de esa hora: archivo de Open-Meteo, una petición para los 64 puntos:
   ```
   GET https://archive-api.open-meteo.com/v1/archive
       ?latitude={64 lat}&longitude={64 lon}&start_date=D&end_date=D
       &hourly=temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m
       &wind_speed_unit=kmh&timezone=UTC
   ```
   Verificado en vivo el 28-09-2026: HTTP 200 en ~0.25 s, 24 valores por
   punto sin nulos, campo `elevation` incluido, y **sirve también para
   "ayer"** (no hace falta esperar días). El proxy lo expone como
   `/api/openmeteo-archive` con caché de 3 h (últimos 7 días) o 7 días.

**Límites:** el clima es el de un modelo de reanálisis (ERA5/Open-Meteo,
~10 km), no una medición en el foco; la hora se redondea a la hora más
cercana. Los focos FIRMS no son necesariamente incendios (§4).

---

## 5d. Temperatura ajustada por altura

La temperatura baja ~6.5 °C por cada 1000 m. En modo "Temperatura real" la
cordillera sale muy fría (llegó a -5 °C) y ese contraste de altura tapa el
patrón meteorológico real. El modo **"Temperatura ajustada a 600 m"** lleva
cada punto a la altura de referencia (Santiago):

`T_ajustada = T + 0.0065 × (altura del punto − 600)`

La altura de cada punto la entrega Open-Meteo (campo `elevation`, ya
usado para bajar la temperatura del modelo a la altura real del punto).
El **índice de riesgo no usa este ajuste**: para el fuego importa la
temperatura real. El gradiente de 6.5 °C/km es el estándar de la
atmósfera; el real varía con la hora y la estación.

---

## 5e. Rutas de emergencia y tráfico en vivo

Objetivo: que quien despache a los bomberos vea la mejor ruta al foco.

**Cómo funciona** (`js/routing.js`, endpoint `/api/route` de `server.py`):

1. Toma las 3 estaciones de bomberos (OSM) más cercanas en línea recta y calcula
   el camino real desde cada una; gana la que llega antes. La estación más
   cercana "a vuelo de pájaro" no siempre es la más rápida (ríos, autopistas).
2. Dibuja la ruta más rápida (roja) y hasta 2 alternativas (grises).
3. Elige el punto de agua para reabastecer: el grifo más cercano al foco si está
   a ≤ 3 km; si no, la fuente de agua (lago o río) más cercana. Dibuja la ruta
   estación → agua → foco (azul punteada) y dice cuánto tiempo extra cuesta.

Se dispara con el botón «Ruta de bomberos» del popup de un foco, de una zona de
calor local o de «Consultar un punto».

**Motores de ruta** (el navegador siempre habla con el proxy; la key no sale de `.env`):

| Motor | Tráfico | Clave | Estado |
|---|---|---|---|
| OSRM público (`router.project-osrm.org`) | No | No | **Usado en la demo.** Servidor de demo: uso liviano, sin garantía. |
| TomTom Routing (con `TOMTOM_KEY`) | Sí, en vivo (entrega el retraso) | Gratis, sin tarjeta | Se usa solo si hay key; si falla cae a OSRM y lo avisa. |
| OpenRouteService | No | Gratis, con registro | **Opción a implementar después** (permite evitar áreas y perfiles de vehículo). |
| Valhalla / OSRM propio | No | No | **Opción a implementar después**: autohospedar con datos de Chile de OSM para no depender del servidor demo. |

**Capa «Tráfico en vivo»** (ajustada tras el feedback del profesor: colores muy saturados y
demasiadas calles): ahora se dibuja con los tiles **vectoriales** de flujo de TomTom
(`/api/traffic-v/{z}/{x}/{y}.pbf`, capa «Traffic flow»), que traen el tipo de vía y el nivel de
tráfico de cada tramo. Con eso:
- **Solo vías principales** (autopistas, troncales y avenidas principales) por defecto; el panel
  permite «+ secundarias» o «Todas». El filtro corre en el navegador, sin volver a pedir tiles.
- **Colores apagados**: verde medio (`#5aa87a`) para lo fluido, ámbar → naranja → rojo ladrillo para
  lo lento. El control de opacidad (90 % por defecto) afecta más a lo fluido: con 90 % lo lento y la
  congestión se ven opacos (100 %) y el verde al ~54 %, para que no tape el mapa base.
- Si los tiles vectoriales no cargan, cae solo al raster anterior (`.png`), igual con
  opacidad y saturación reducidas, pero sin filtro de vías (la fuente queda en «aviso»).
- Mismo caché de 5 min en el proxy para cuidar la cuota gratuita (según su página de precios:
  200.000 tiles y 20.000 rutas al mes; cada ruta de emergencia usa hasta 4 llamadas).
- El mapeo `road_type` → clase sale de la documentación de TomTom (Motorway, International road,
  Major road, Secondary road, Connecting road, Major local road, Local road, Minor local road).
  **No se pudo probar con los tiles reales desde el entorno de desarrollo**: la prueba usó tiles
  falsos con ese formato. Si «Solo principales» no muestra nada, revisar el nombre real de las
  propiedades (el panel de fuentes y `--trafico-resumen` lo delatan).

**Foto del tráfico guardada (`--trafico`)**: `python tools/recolector.py --trafico` (una vez) o
`--trafico-cada 30` (mínimo 10 min) baja los tiles vectoriales que cubren el área, los decodifica
(`tools/trafico.py`, formato Mapbox Vector Tile sin librerías) y guarda en SQLite:
`traffic_segments` (cada tramo: clase, tipo, nivel 0-1, punto medio, largo) y `traffic_summary`
(por tipo de vía: km, fluidez media ponderada por largo, km lentos <0,6 y km en congestión <0,3).
`--detalle principales|secundarias|todas` decide qué tramos se guardan uno a uno (el resumen
siempre incluye todas las clases), `--zoom` el nivel de los tiles (11 por defecto: unos 16 tiles
por foto, ≈23.000 tiles al mes con una foto cada 30 min, que cuentan contra los 200.000 gratis)
y `--conservar-dias 30` borra los tramos viejos (el resumen se conserva siempre).
`--trafico-resumen` muestra la última foto. Los tramos que cruzan el borde de un tile aparecen
una vez por tile, así que los conteos son aproximados.

**Limitaciones honestas**
- Los tiempos son para un auto liviano; un carro bomba es más lento y puede usar
  vías y prioridades que el motor no conoce.
- OSRM/OSM no sabe de cortes de calle, obras ni incendios que bloqueen una vía.
  La ruta no evita el humo ni el frente de fuego (se dejó para después).
- El punto de agua es el más cercano al foco en línea recta; un río puede no ser
  accesible en camión en ese punto. Los grifos son los mapeados en OSM (cobertura desigual).
- La ruta va hasta el punto del foco satelital (pixel de ~375 m), no hasta el frente real.
- Prueba con servicios reales: **pendiente** (desde el entorno de desarrollo esas APIs
  están bloqueadas). Lo probado con respuestas simuladas: contrato del proxy,
  validaciones, caché, elección de estación, dibujo de rutas y capa de tráfico.
  Falta correrlo en tu equipo con `python server.py`.

**Registro TomTom**: https://developer.tomtom.com → crear cuenta → «Get a free API key»,
copiar la key en `.env` como `TOMTOM_KEY=…` y reiniciar `server.py`.

## 5f. Archivo histórico (SQLite) para predecir la propagación

Para estudiar y predecir cómo se propaga un incendio hacen falta datos **de antes
y de durante** el fuego. Como casi ninguna fuente guarda el pasado por nosotros
(FIRMS sí da histórico, pero el clima "de ese momento" y la vegetación hay que
reunirlos), desde ahora el proyecto los acumula en `data/firewatch.db`.

**Cómo se llena** (dos caminos, que no se pisan: las filas repetidas se actualizan):

1. **Automático con el mapa.** `server.py` guarda lo que consulta (clima
   Open-Meteo actual/horario/histórico, focos FIRMS, estaciones DMC) y el navegador
   envía una sola vez la grilla de combustible de cada área (`/api/store/fuelgrid`).
2. **Recolector aparte, sin mapa abierto**: `tools/recolector.py`.
   - `python tools/recolector.py --una-vez`: una pasada (clima actual y de los últimos
     2 días de los 64 puntos, focos FIRMS de 3 satélites, DMC si hay token).
   - `python tools/recolector.py --cada 60`: repite cada 60 min (mínimo 30).
   - En Windows, para que corra solo: Programador de tareas → nueva tarea → acción
     "Iniciar programa" `python`, argumentos `tools\recolector.py --una-vez`, carpeta
     `C:\proyectos\FireWatch\firewatch-project-mapa`, repetir cada 1 hora. El PC
     debe estar encendido; los huecos se notan en `--stats` (`weather_range`).
   - `python tools/recolector.py --terreno`: completa altura y pendiente/exposición de
     cada celda (una altura por celda de 250 m con la API de elevación de Open-Meteo,
     ~500 llamadas para el área de Santiago; se puede interrumpir y continuar; la
     pendiente sale de las celdas vecinas).
   - `--stats` resume lo guardado; `--exportar TABLA archivo.csv` saca una tabla a CSV.

**Tablas** (horas siempre en UTC, `YYYY-MM-DDTHH:MM`):

| Tabla | Una fila por… | Campos principales |
|---|---|---|
| `weather_obs` | fuente (`om-current`/`om-hourly`), hora y punto de la grilla 8×8 | temperatura, humedad, viento y dirección, ráfaga, precipitación, déficit de presión de vapor, radiación, humedad de suelo, ET0, altura |
| `fire_detections` | detección satelital (sin duplicados) | lat/lon, hora, satélite, confianza, potencia radiativa (FRP), brillo, día/noche |
| `station_obs` | estación DMC y hora | temperatura, humedad, viento |
| `fuel_areas` / `fuel_cells` | área / celda de 250 m (fila 0 = sur, columna 0 = oeste) | clase dominante, propensión de combustible, fracciones por clase, altura, pendiente, exposición |
| `traffic_segments` | tramo de vía en un instante (tile + índice) | clase y tipo de vía, nivel de tráfico (1 = fluido), punto medio, largo |
| `traffic_summary` | instante y clase de vía | tramos, km, fluidez media, km lentos y en congestión |
| `collector_runs` | ejecución del recolector | tipo, éxito, detalle |

**Registro de lo que se guarda (qué entra, qué falla y por qué)**

Cada intento de guardar algo deja un evento en la tabla `ingest_log` y una línea en
`data/ingest.log` (texto, se rota a 1 MB; hora local). También se imprime en la consola
de `server.py`/`recolector.py` con el prefijo `[archivo]`.

| Campo | Qué dice |
|---|---|
| fuente / variante | `clima` (actual, horario, historico), `focos` (satélite FIRMS), `dmc`, `combustible` (área), `terreno` |
| origen | `server` (mapa abierto), `recolector`, `browser` |
| fase | `solicitud` (antes de pedir), `descarga` (falló la fuente externa) o `guardado` (falló la base) |
| estado | `ok`, `warn` (se guardó pero algo raro), `error`, `skipped` (omitido, p. ej. falta una key) |
| filas | recibidas y, en focos, cuántas eran nuevas |
| error | tipo + mensaje original |

**Tipos de error** (y qué hacer): `red` (sin conexión o timeout), `credenciales`
(key/token faltante o inválida: FIRMS responde 200 con texto cuando la key es mala),
`limite_de_uso` (429), `http_4xx` (consulta rechazada, p. ej. una variable no válida),
`http_5xx` (falla del servicio), `formato` (respuesta inesperada), `datos_incompletos`
(más de la mitad de las lecturas sin temperatura), `validacion`, `base_bloqueada`,
`disco`, `desconocido`. El texto con la recomendación de cada uno sale en `--resumen`.

Dónde verlo:
- En el navegador: enlace «Ver registro del archivo histórico» del panel de estado
  (`/api/store/log.txt`; `?errors=1` solo problemas). La fila «Archivo histórico» se pone
  amarilla si hubo errores en las últimas 24 h.
- `python tools/recolector.py --resumen`: una fila por fuente con veces OK/aviso/error y el último problema.
- `python tools/recolector.py --log 50 --solo-errores`: los últimos eventos con problemas.
- `data/ingest.log`: el mismo historial como texto.
- JSON: `/api/store/log`, `/api/store/summary`.

Un fallo al **guardar** nunca impide que el mapa reciba el dato. Un 0 en filas de focos es
normal cuando no hay incendios; un 0 en clima se marca como aviso. Se conservan los últimos
20.000 eventos.

**Qué hay que saber**
- Las variables extra del clima (ráfaga, precipitación, VPD, radiación, humedad de suelo,
  ET0) **no se pudieron probar en vivo** desde el entorno de desarrollo. Si la API
  rechaza alguna, el recolector prueba un juego más básico y lo avisa por consola.
- El archivo es crudo: no interpola, no rellena huecos ni corrige. Quien lo use para
  modelar debe decidir cómo tratar los huecos (el PC apagado, servicios caídos).
- Las grillas de combustible se guardan **por versión de área**; si cambia el radio
  del mapa se crea otra área, no se pisa la anterior.
- Tamaño esperado: el clima de una grilla de 64 puntos cada hora son ~6.000 filas al
  día (pocos MB al mes); la grilla de combustible de Santiago son ~50.000 celdas (~5 MB).
- El `.db` no se sube a git. Para dárselo al compañero: copiar el archivo (con el
  servidor y el recolector detenidos).

**Datos que todavía faltan para predecir propagación** (ordenados por utilidad):
1. **Verdad terreno de incendios pasados**: perímetros o áreas quemadas para validar
   predicciones. Candidatos: área quemada MODIS (MCD64A1, en Planetary Computer, 500 m)
   y las estadísticas de CONAF (hay que revisar qué publican y con qué licencia).
2. **Humedad del combustible vivo y muerto**: no existe dato directo. Se aproxima con
   precipitación de los últimos días, humedad de suelo, VPD y un índice de vegetación
   (NDVI de Sentinel-2 o MODIS, también en Planetary Computer) que mide qué tan seca
   está la vegetación.
3. **Modelo de combustible más fino** que la clase de cobertura (carga y tipo por clase,
   como los modelos de Anderson o Scott & Burgan), validado con incendios locales.
4. **Barreras y accesos**: caminos, ríos y zonas urbanas que frenan o permiten el
   avance (OSM; ya se consulta para agua y estaciones).
5. **Detecciones satelitales de baja latencia** (GOES-19, cada 10 min) para ver cómo
   avanza el frente hora a hora.
6. **Un viento más fino sobre el terreno**: el modelo de Open-Meteo es de ~11 km; en
   quebradas el viento real cambia mucho.

### Histórico y eventos de incendio (`--historico`)

`python tools/recolector.py --historico` arma, para la zona elegida, el material que
necesita un modelo de propagación: focos pasados agrupados en incendios y el clima de
cada uno. Requiere `FIRMS_MAP_KEY` en `.env`. Es reanudable (tabla `backfill_progress`):
si se corta, se vuelve a ejecutar y sigue donde quedó.

1. **Focos FIRMS** en tramos de 5 días (el máximo de la API). Usa el producto *SP*
   (procesado, definitivo) para fechas de hace más de ~150 días y *NRT* para lo reciente.
   El SP tiene meses de retraso, así que el tramo intermedio puede quedar con NRT.
2. **Eventos** (`fire_events`, `fire_event_detections`): agrupa detecciones con
   encadenamiento (single-link): a ≤2 km y con ≤48 h de separación son el mismo incendio.
   Ignora detecciones de confianza baja. `area_km2_approx` = celdas únicas de 375 m
   × 0,375² km²: es una **cota superior del área detectada, no el área quemada**.
3. **Clima** (Open-Meteo archivo, por hora, UTC) en el centroide de cada evento, desde
   7 días antes (para lluvia previa) hasta 1 día después. El archivo tiene ~6 días de
   retraso: los eventos más recientes se saltan ("muy recientes") y se completan en otra
   corrida. Si la API rechaza alguna variable, prueba un juego más básico.

Opciones: `--zona`, `--desde/--hasta` (por defecto 3 años), `--solo {focos,eventos,clima}`,
`--min-detecciones` (por defecto 3), `--eventos 15` (lista los mayores), `--resumen`
(errores). Cuidado: calor industrial/agrícola puede aparecer como "incendio"; el clima
es puntual (centroide), no por celda; y respeta la cuota de Open-Meteo (con 429 se detiene).

## 6. Estaciones meteorológicas — Dirección Meteorológica de Chile (Red EMA)

**Qué es:** la Red de Estaciones Meteorológicas Automáticas (EMA) de la
DMC: mediciones **instrumentales** reales de temperatura, humedad y
viento. Es la fuente "local y oficial" que pidió el profesor, y reemplaza
a la de MinCiencia/CEAZA (servidor caído, ver sección 9).

**Cómo se obtiene:** servicios web JSON del portal de Servicios
Climáticos (<https://climatologia.meteochile.gob.cl>, producto RE8011):
```
GET .../application/servicios/getEstacionesRedEma
GET .../application/servicios/getDatosRecientesRedEma?usuario={correo}&token={token}
```
- **Catálogo (`getEstacionesRedEma`):** verificado el 28-09-2026 que
  responde SIN credenciales y con CORS abierto: 148 estaciones en Chile,
  11 dentro del área de Santiago (Quinta Normal, Pudahuel, Tobalaba,
  La Florida, Talagante, Lo Prado, Lo Pinto, Colina, Río Clarillo, San
  José de Guayacán, San Pablo). Los nombres llegan con la codificación
  rota ("SÃ¡nchez") y se reparan.
- **Mediciones (`getDatosRecientesRedEma`):** requieren `usuario` + `token`
  personal (gratis, registro en el portal). Verificado que sin
  credenciales responde "servicio bloqueado para el usuario que consulta".
  Según la documentación entrega las últimas 12 h con: temperatura (°C),
  humedad relativa (%), dirección del viento (°), **fuerza del viento en
  nudos** (se convierte a km/h × 1.852), presión y precipitación, con hora
  en UTC.

**Frecuencia:** las estaciones reportan cada ~15 minutos. El proxy cachea
10 min y el mapa refresca cada 15 min. Un dato de más de 90 minutos se
marca como "atrasado".

**Por qué pasa por el proxy:** el token no puede ir en JavaScript que
corre en el navegador. Está en `.env` y solo lo lee `server.py`.

**Validación del modelo:** con mediciones frescas, el panel calcula el
error medio absoluto de temperatura (y su sesgo) y de viento entre lo que
predice Open-Meteo, interpolado en la ubicación de cada estación, y lo
que midió la estación. Permite decir en la defensa qué tan confiable es
el dato con números.

**⚠️ Pendiente:** el esquema exacto de `getDatosRecientesRedEma` no se
pudo verificar sin token. El proxy lo normaliza de forma tolerante y guarda
la respuesta cruda en `cache/dmc_recientes_raw.json`; hay que revisarla la
primera vez que se use con un token real.

---

## 7. Sensor(es) de cámara

Coordenadas y azimut tomados de `firewatch-project-camara/camera_config.json`.
No es una API externa, es dato propio del proyecto.

---

## 8. Por qué una grilla de puntos y no un área continua

Open-Meteo solo acepta consultas por coordenada puntual. Por eso se arma
una grilla uniforme (8×8 en la ciudad, 6×6 en las zonas locales), se
pide cada punto en paralelo y se interpola entre ellos. Es una
aproximación, no una medición en cada píxel — decirlo si preguntan por la
precisión entre puntos de muestreo.

---

## 9. Pruebas en vivo de las fuentes (28-09-2026)

Ejecutadas desde el navegador integrado del computador del equipo
(mismas condiciones que un usuario real):

| Fuente | Resultado |
|---|---|
| Open-Meteo (1 punto y 64 puntos en una petición) | OK. 64 puntos en ~0.6 s, 0 nulos, con humedad. |
| NASA FIRMS (NOAA-20 y SNPP, 1 y 5 días) | OK, con CORS abierto. Ese día había 1 foco real cerca de Talagante. |
| NASA FIRMS, replay Valparaíso 03-02-2024 (`VIIRS_NOAA20_SP`) | OK, ~32 KB de detecciones. |
| Open-Meteo archivo (`archive-api`), 2 puntos, 03-02-2024 y "ayer" | OK, HTTP 200 en ~0.25 s; 24 h sin nulos; incluye `elevation`; sirve para ayer. |
| Open-Meteo forecast con `start_date`/`end_date` (fecha pasada) | OK, HTTP 200. |
| Calibración del índice (5 incendios, 5 temporadas horarias) | OK, ver §5b y `tools/calibrar_indice.js`. |
| Planetary Computer (búsqueda STAC de WorldCover) | OK, ~0.6 s. |
| OpenFreeMap (estilo) y Mapterhorn (terreno) | OK. |
| Overpass `overpass-api.de` | OK, ~6 s por consulta simple. |
| Overpass `overpass.kumi.systems` (mirror que usaba el código) | **Caído** (timeout 15 s). Reemplazado. |
| Overpass `overpass.private.coffee` | **Caído** (timeout). |
| Overpass `maps.mail.ru` | OK (~7 s). Es el mirror actual. |
| MinCiencia / CEAZA (`datos.occ.minciencia.gob.cl`) | **Caído** (timeout). Descartado. |
| DMC catálogo de estaciones | OK sin credenciales, CORS abierto. |
| DMC datos recientes | Bloqueado sin credenciales (esperado). |

Desde el entorno de desarrollo en la nube estas APIs están bloqueadas por
política de red, por lo que las pruebas reales se hicieron desde el
computador del equipo.

---

## Resumen de fuentes y licencias

| Capa | Fuente | Institución | Licencia | API key |
|---|---|---|---|---|
| Viento / temperatura / humedad | Open-Meteo | Open-Meteo (Europa) | CC BY 4.0; gratis solo sin suscripción ni publicidad | No |
| Cobertura vegetal | ESA WorldCover | ESA, vía Microsoft | CC BY 4.0 | No |
| Agua / grifos / bomberos | OpenStreetMap | Comunidad OSM, vía Overpass | ODbL | No |
| Focos de calor satelitales | NASA FIRMS | NASA (VIIRS NOAA-20/NOAA-21/SNPP) | Dominio público | Sí, gratis (en `.env`) |
| Zonas de calor locales | Open-Meteo + FIRMS | — | — | Igual que sus fuentes |
| Estaciones meteorológicas | Red EMA | Dirección Meteorológica de Chile | Datos públicos, citar a la DMC | Token gratis (en `.env`); catálogo sin token |
| Mapa base / terreno | OpenFreeMap / Mapterhorn | Comunidad OSM / Mapterhorn | ODbL / CC | No |
