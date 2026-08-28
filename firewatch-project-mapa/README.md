# FireWatch — Mapa 3D

Subproyecto nuevo, independiente de `firewatch-project-web/`, `-desktop/` y
`-camara/`. Es la base real del mapa 3D: calles/edificios reales, terreno 3D
real, viento y temperatura en vivo. No incluye todavía la simulación de
propagación de incendio — eso queda para la siguiente iteración, una vez que
esta base esté validada.

## Cómo correrlo

No requiere build ni instalación. Desde esta carpeta:

```bash
python -m http.server 8080
```

Abrir `http://localhost:8080` en el navegador. Todo corre 100% en el cliente
(HTML + JS vanilla), sin backend propio — cada capa consulta directamente a
servicios externos gratuitos.

## Qué es real y qué no

| Elemento | Estado |
|---|---|
| Mapa (calles, edificios) | **Real** — OpenStreetMap vía OpenFreeMap, sin API key |
| Terreno 3D (elevación) | **Real** — Mapterhorn, datos de elevación reales |
| Viento (velocidad + dirección) | **Real, en vivo** — Open-Meteo, se actualiza cada 10 min o al presionar "Actualizar" |
| Temperatura / mapa de calor | **Real, en vivo** — Open-Meteo, misma grilla que el viento |
| Sensor de cámara en el mapa | **Real** — coordenadas y azimut tomados de `firewatch-project-camara/camera_config.json`, soporta varias cámaras (`CONFIG.cameras`) |
| Cobertura vegetal (riesgo de combustible) | **Real** — ESA WorldCover (satélites Sentinel-1/2, 10m, CC BY 4.0), vía Microsoft Planetary Computer, sin API key ni contacto institucional |
| Ubicación del centro del mapa | **Fija por ahora** — hoy apunta a las coordenadas de esa cámara. En producción debería venir del backend (última alerta activa), no estar hardcodeada en `config.js` |
| Velocidad visual de las partículas de viento | **Exagerada a propósito** — el viento real (15-30 km/h) es demasiado lento para verse fluir en pantalla a esta escala. Ver `CONFIG.windVisualSpeedFactor` |

## Estructura

```
firewatch-project-mapa/
├── index.html          → punto de entrada
├── css/style.css        → estilos (reusa la paleta de firewatch-project-web)
└── js/
    ├── config.js         → TODO lo ajustable: centro, estilo de mapa, tamaño de grilla, etc.
    ├── weather-grid.js    → construye la grilla, consulta Open-Meteo, interpola viento/temp
    ├── map-init.js        → arranca MapLibre, terreno 3D, marcador de cámara
    ├── wind-layer.js       → partículas de viento animadas (canvas sobre el mapa)
    ├── heat-layer.js       → capa de calor nativa de MapLibre, alimentada con temperatura real
    └── main.js             → conecta todo + controles de la interfaz
```

## Sobre la grilla de viento/temperatura

Open-Meteo no ofrece (todavía) un endpoint de "área" — hay que pedir puntos
específicos. `weather-grid.js` arma una grilla de `gridSize x gridSize` puntos
(6×6 por defecto) alrededor del centro, cubriendo `gridRadiusKm` km a la
redonda, y pide **todos los puntos en una sola llamada HTTP** (Open-Meteo
soporta coordenadas separadas por coma en un solo request — no hace 36
llamadas separadas).

Entre los puntos de la grilla, el viento y la temperatura se interpolan
(`WeatherGrid.sample()`) para que las partículas se muevan de forma continua
y no salten entre 36 valores fijos. El viento se interpola convirtiendo
velocidad+dirección a componentes vectoriales (u/v) antes de promediar —
promediar el ángulo directamente es un error común (el promedio entre 350°
y 10° no da 180°).

Si suben `gridSize`, el mapa queda más preciso pero cada actualización pide
más puntos en la misma llamada — no debería ser un problema para uso normal,
pero no lo dejen en un número absurdamente alto sin necesidad.

## Si el mapa no carga

- **`tiles.openfreemap.org` no responde**: cambiar `mapStyle` en `config.js`
  a `bright` o `positron` (mismo dominio, otro estilo), o conseguir una key
  gratis de MapTiler como alternativa (ver comentarios en `config.js`).
- **El terreno 3D no aparece**: `tiles.mapterhorn.com` puede estar caído
  ocasionalmente por ser un servicio pequeño gratuito. Alternativa sin key:
  AWS Open Data Terrarium tiles (URL comentada en `config.js`), un poco más
  lenta pero muy estable.
- **"Error consultando Open-Meteo"**: revisar la consola del navegador — casi
  siempre es un parámetro mal formado en la URL o que se superó algún límite
  temporal de la grilla (rangos de lat/lon inválidos si `gridRadiusKm` es
  absurdamente grande).
- **"⚠️ No se pudo cargar la cobertura vegetal"**: esta capa depende de
  Microsoft Planetary Computer. Si falla, revisen la consola (F12) — el
  mensaje de error queda ahí. El resto del mapa (viento, temperatura,
  cámaras) sigue funcionando igual aunque esta capa falle; está aislada
  a propósito para que un servicio externo caído no rompa todo lo demás.
  (Nota histórica: la primera versión de esta capa usaba el WMS de
  Terrascope, que empezó a fallar de forma persistente —
  `net::ERR_HTTP2_PROTOCOL_ERROR` incluso pegando la URL directo en el
  navegador desde dos redes distintas— así que se migró a Planetary
  Computer.)

## Próximos pasos (no incluidos todavía)

1. **Conectar el centro del mapa al backend real** — hoy es fijo en
   `config.js`, debería recibir la coordenada de la alerta/cámara activa.
2. **Traer de vuelta la simulación de propagación de incendio** (la del
   playground anterior) pero corriendo sobre este terreno real en vez de uno
   ficticio — ahora que tienen elevación real, el factor de pendiente del
   autómata celular puede usar datos reales en vez de la función de ruido.
3. **Capa de fuentes de agua reales** (Overpass API) en vez de las 4
   fuentes inventadas del playground.
4. ~~**Capa de riesgo de combustible real**~~ — ✅ ya está: `js/fuel-layer.js`
   usa ESA WorldCover (cobertura vegetal satelital real, sin necesidad de
   CONAF/CENIA). Es una simplificación de primer orden — clasifica por tipo
   de cobertura, no por humedad/carga de combustible real — pero es dato
   satelital verificable, no inventado. Documentar esta limitación
   explícitamente en la memoria.

## Nota sobre acceso a cámaras institucionales (CONAF/CENIA)

CONAF y el CENIA ya operan una red real de cámaras de teledetección de
incendios en Chile (incluida una en Sky Costanera, Santiago), pero el feed
es gestionado internamente — no encontramos una API pública para
conectarse. Si en algún momento pueden conversar con ellos, vale la pena
intentarlo (colaboración académica), pero el proyecto NO depende de eso:
tanto el mapa de calor (Open-Meteo) como la cobertura vegetal (ESA
WorldCover) son fuentes públicas independientes que no requieren ningún
contacto institucional.

## Atribuciones requeridas

- Datos de mapa: © OpenStreetMap contributors, vía OpenFreeMap
- Terreno: Mapterhorn
- Viento/temperatura: Open-Meteo.com (CC BY 4.0)

Mantengan estos créditos visibles en la versión final que presenten — es
parte de las condiciones de uso gratuito de estos servicios.
