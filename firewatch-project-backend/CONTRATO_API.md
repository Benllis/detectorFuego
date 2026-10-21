# FireWatch — Contrato de la API (v0.1, borrador)

Objetivo: reemplazar la carpeta compartida `alertas_pendientes` por una API, para que cámara, escritorio, web y app móvil hablen con el mismo backend.
Los nombres de campo salen de lo que ya envía `main_multicamera.py`, para que Benja no tenga que rehacer su parte.

- Backend: FastAPI + SQLite. Base URL de desarrollo: `http://localhost:8100`.
- Formato: JSON en UTF-8, fechas `YYYY-MM-DD HH:MM:SS` (hora local de la cámara) más `timestamp` (epoch, segundos).
- Errores: código HTTP correcto y cuerpo `{"error": "mensaje corto"}`.

## 1. Autenticación

| Quién | Cómo |
|---|---|
| Cámara | Cabecera `X-Camera-Key: <clave>`. Una clave por cámara, entregada por el administrador. |
| Operadores (escritorio, móvil) | `POST /login` devuelve un token. Luego `Authorization: Bearer <token>`. |
| Público (formulario de usuarios) | Sin login, solo `POST /reportes`, con límite de envíos por IP. |

Las claves y contraseñas viven en `.env`, nunca en el repositorio. Las contraseñas se guardan con bcrypt.

## 2. Alertas

### POST /alertas  (cámara)
`multipart/form-data` con dos partes:
- `metadata`: JSON (abajo).
- `imagen`: archivo JPG.

```json
{
  "id_alerta": "cam_1_fire_20261012_153045",
  "camara_id": "cam_1",
  "camara_nombre": "Webcam Principal",
  "ubicacion": "Sector B - Planta Alta",
  "gps": [-33.5227, -70.5859],
  "tipo": "FIRE",
  "timestamp": 1791471504.12,
  "fecha_hora": "2026-10-12 15:30:45",
  "fuego_detectado": true,
  "humo_detectado": false,
  "confianza": 0.81,
  "imagen_asociada": "cam_1_fire_20261012_153045.jpg"
}
```

- `tipo`: `FIRE`, `SMOKE`, `CRITICAL` (fuego y humo juntos) o `NO_DETECTION`.
- `NO_DETECTION`: la cámara envía la foto sin detección y el mensaje "no se logró detectar el origen"; el operador decide si es falsa.
- `gps` es `[lat, lon]` y puede ser `null`. `confianza` es opcional (0 a 1).
- `id_alerta` lo genera la cámara. Si ya existe, el backend responde `200` con la alerta existente (así un reintento no duplica).

Respuesta `201`: `{"id_alerta": "...", "estado": "PENDIENTE"}`.
Si el backend no responde, la cámara guarda la alerta en disco y reintenta (cola local).

### GET /alertas  (operador)
Query opcional: `estado` (`PENDIENTE`, `CONFIRMADA`, `FALSO_POSITIVO`), `camara_id`, `desde`, `hasta`, `limit` (por defecto 50), `offset`.
Devuelve `{"total": 12, "items": [ <alerta>, ... ]}`. Cada item es el metadata de arriba más `estado`, `operador_resolucion`, `fecha_resolucion` y `motivo_descarte`.

### GET /alertas/{id_alerta}  (operador)
Una alerta completa.

### GET /alertas/{id_alerta}/imagen  (operador)
Devuelve el JPG.

### PATCH /alertas/{id_alerta}  (operador)
Confirmar o descartar:
```json
{"estado": "CONFIRMADA"}
{"estado": "FALSO_POSITIVO", "motivo_descarte": "Reflejo del sol"}
```
`motivo_descarte` es obligatorio para `FALSO_POSITIVO` (igual que hoy en el escritorio). El backend registra el operador y la fecha de resolución.

## 3. Cámaras

| Método | Ruta | Quién | Para qué |
|---|---|---|---|
| GET | `/camaras` | operador | Listar cámaras con su ubicación. |
| POST | `/camaras` | administrador | Registrar una cámara y generar su clave. |
| PUT | `/camaras/{camara_id}` | administrador | Cambiar nombre, ubicación o `gps`. |

Una cámara: `{"camara_id", "nombre", "ubicacion", "gps": [lat, lon], "azimut", "orientacion"}`.

## 4. Usuarios

- `POST /login`: `{"username", "password"}` → `{"token", "nombre_completo", "rol"}`. Roles: `ADMIN`, `OPERADOR`, `BOMBERO`.
- `POST /usuarios` (solo `ADMIN`): crear operadores y bomberos.

## 5. Reportes manuales (formularios)

### POST /reportes
Para bomberos y ciudadanos. JSON (`Content-Type: application/json`):
```json
{
  "origen": "USUARIO",
  "descripcion": "Humo en el cerro detrás de la plaza",
  "gps": [-33.52, -70.58],
  "contacto": "opcional"
}
```
Con foto opcional: `multipart/form-data` con `metadata` (el JSON de arriba como texto) e `imagen` (JPG), igual que `POST /alertas`.

- `origen`: `USUARIO` o `BOMBERO`. **`USUARIO` no necesita sesión** (formulario público) y está limitado por IP; **`BOMBERO` exige `Authorization: Bearer <token>`** de un usuario con sesión (bombero, operador o admin). Un token inválido responde `401`; no se trata como anónimo.
- `descripcion`: 5 a 1000 caracteres. `gps`: `[lat, lon]` **obligatorio** (un reporte sin lugar no sirve), lat entre -90 y 90, lon entre -180 y 180. `contacto`: opcional, hasta 120 caracteres. Los caracteres de control se eliminan; el resto del texto se guarda tal cual, así que **quien lo muestre debe escaparlo** (no insertarlo como HTML).
- Imagen: solo JPG (se revisa el contenido, no solo la extensión), hasta 3 MB.
- Estado inicial: `SIN_VERIFICAR` para `USUARIO`, `PENDIENTE` para `BOMBERO`.

Respuesta `201`: `{"id_reporte": "rep_ab12cd34ef", "estado": "SIN_VERIFICAR"}`.

Errores: `401` (bombero sin sesión o token inválido), `413` (cuerpo o imagen demasiado grandes), `415` (ni JSON ni multipart), `422` (dato inválido: `{"error": "dato inválido (gps): ..."}`), `429` (límite de envíos; trae la cabecera `Retry-After` en segundos).

**Límite de envíos** (solo para quien no inició sesión): 5 reportes por IP cada hora y 100 en total cada hora entre todas las IP, para frenar a quien cambia de IP. Se configura en `.env` (`FIREWATCH_REPORTES_*`). La IP se guarda junto al reporte para poder rastrear abusos; **no** sale por la API. Detrás de un proxy propio, ver `FIREWATCH_PROXY_CONFIAR` en el README.

### GET /reportes  (con sesión)
Filtros: `origen`, `estado`, `desde`, `hasta` (sobre `fecha_hora`), `limit` (por defecto 50, máximo 200) y `offset`. Devuelve `{"total": 3, "items": [ ... ]}`; cada item trae `id_reporte`, `origen`, `descripcion`, `gps`, `contacto`, `estado`, `fecha_hora` e `imagen_asociada`. Para el rol `BOMBERO`, `contacto` viene siempre `null` (es un dato personal del ciudadano: solo operadores y admin lo ven).

### GET /reportes/{id_reporte}  y  GET /reportes/{id_reporte}/imagen  (con sesión)
Un reporte, y su foto en JPG (la foto no es pública).

Pendiente: verificar o descartar un reporte (`PATCH`), igual que las alertas.

## 5b. Propagación M2

### POST /propagacion/m2  (cualquier usuario con sesión)
Estima en qué celdas de ~1 km es más probable que haya fuego al día siguiente, **a partir de un foco ya detectado**.
No predice incendios nuevos. El mapa arma las entradas y el backend corre el modelo (`models/m2/`).
```json
{
  "lat": -33.05, "lon": -71.60,
  "focos": [[-33.05, -71.60], [-33.04, -71.60]],
  "viento_kmh": 20, "viento_dir_deg": 270,
  "temp_max": 28, "humedad": 35,
  "cobertura": {"n": 16, "valores": [30, 30, "... n*n clases ESA WorldCover ..."]},
  "elevacion": {"n": 16, "valores": ["... n*n metros, opcional ..."]}
}
```
- `lat/lon`: centro del parche (el foco de la alerta). El parche es de 64 x 64 celdas de 0,01° (≈ 71 km) y la celda central es la del foco.
- `focos`: detecciones previas (máx. 500). Los que caen fuera del parche, o en su anillo del borde, no cuentan. Si no queda ninguno → `422`.
- `viento_dir_deg`: meteorológica, **de dónde** sopla. `cobertura` y `elevacion` son mallas `n x n` (2 ≤ n ≤ 64) que cubren **todo** el parche; fila 0 = sur, columna 0 = oeste; `null` = sin dato. Sin `elevacion` se supone terreno plano.

Respuesta `200`:
```json
{
  "modelo": "M2", "version": "2026-10-11T20:40:41",
  "parche": {"centro": [-33.05, -71.6], "celda_grados": 0.01, "lado": 64,
             "bbox": {"sur": -33.375, "norte": -32.735, "oeste": -71.925, "este": -71.285}},
  "focos_usados": 2,
  "grilla": {"filas": 64, "columnas": 64, "orden": "fila 0 = sur, columna 0 = oeste", "niveles": [0, 0, 1, "... 4096 valores 0-3"]},
  "niveles": [{"nivel": 1, "etiqueta": "muy probable", "celdas": 18, "area_km2": 22.1, "alcance_km": 4.2}, "..."],
  "advertencias": [],
  "nota": "Orden relativo de celdas, no un porcentaje ...",
  "limites": "solo continuacion de un incendio ya detectado ..."
}
```
- `niveles` de la grilla: 0 = nada, 1 = muy probable, 2 = probable, 3 = posible (una celda entra al nivel si su salida supera 0,10 / 0,03 / 0,01 **y** está entre las 30 / 100 / 250 mejor ordenadas del parche; el tope evita pintar medio parche en terreno montañoso).
  **No son porcentajes**: el modelo se entrenó con el 5 % de los negativos y sus valores están inflados; sirven para ordenar celdas.
- `alcance_km`: distancia máxima desde el foco más cercano de las celdas de ese nivel o más probables.
- Errores: `401` sin sesión, `422` datos inválidos o sin focos dentro del parche, `503` modelo no instalado o con SHA-256 que no coincide.
- Fuera de la zona de entrenamiento (centro de Chile, aprox. 33°–40° S) responde igual, con una `advertencia`.

## 6. Utilidad

- `GET /salud` → `{"ok": true, "version": "0.1"}`. Sirve para que la cámara sepa si hay conexión.

## 7. Base de datos (resumen)

Reutiliza `registro_alertas` del escritorio, con estas columnas nuevas: `camara_nombre`, `tipo` ampliado, `confianza`, `imagen_path`. Tablas nuevas: `camaras`, `usuarios` (bcrypt), `reportes`.

## 8. Pendientes de decidir con Benja

1. ¿La cámara envía una foto por alerta o también un clip corto?
2. ¿El `gps` lo ingresa el instalador a mano (como hoy) o se elige en un mapa? Eso no cambia la API.
3. ¿Quién genera las claves de las cámaras? Propongo el administrador desde el escritorio.
4. Valores finales de `tipo`: hoy el código usa `FIRE`/`SMOKE`/`CRITICAL` y el escritorio antiguo mostraba otros nombres.

## 9. Orden de implementación

1. `GET /salud`, `POST /login`.
2. `POST /alertas` y `GET /alertas` (con imagen). Con esto la cámara y el escritorio ya pueden conectarse.
3. `PATCH /alertas/{id}`.
4. `/camaras`, `/reportes` y `/usuarios`. (Hechos; falta solo el `PATCH` de reportes.)
