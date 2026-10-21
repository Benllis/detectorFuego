# FireWatch — Cámara

Programa de escritorio que mira una o varias cámaras, analiza cada imagen con dos modelos (fuego y humo) y, cuando
**confirma** una detección, envía una alerta con foto al backend de FireWatch para que un operador la revise.
No decide nada por sí solo: la alerta llega como `PENDIENTE` y una persona la confirma o la descarta en el escritorio.

## Cómo funciona

1. **Captura.** Cada cámara tiene su propio hilo; si la señal se corta, reconecta sola con pausas de 1, 2, 4, 8, 15 y 30 s y muestra el estado ("Sin señal · reintento N").
2. **Análisis.** Cada ~0,8 s se analiza una cámara (van por turnos) con `models/modelo_a_fire.onnx` (fuego, umbral 0,35) y `models/modelo_b_smoke.onnx` (humo, umbral 0,55), a 416 px. Las imágenes alargadas se analizan en recuadros con solape para que las llamas lejanas no se vuelvan diminutas.
3. **Confirmación.** Un destello suelto no alerta: el fuego debe verse en **3 de las últimas 5** revisiones y el humo en **2 de 5**, todas de esa cámara y de los últimos 30 s. Fuego y humo se confirman por separado; los dos juntos dan `CRITICAL`.
4. **Alerta.** Se guarda el cuadro (JPG) y sus datos (JSON) en `alertas_pendientes/`. Esa carpeta es una **cola**: un hilo la envía a `POST /alertas` del backend y, si sale bien, mueve los archivos a `alertas_enviadas/`; si el backend no responde, los deja y reintenta cada 10 s (no se pierde nada sin red); si el backend rechaza la alerta por inválida, van a `alertas_rechazadas/`.
5. **Sin repetir.** Dentro de un mismo incendio solo se avisa de nuevo si **sube de nivel** (humo → fuego → crítico) o pasa el tiempo de espera: humo 10 min, fuego 5 min, crítico 10 min (`FIREWATCH_COOLDOWN_CRITICAL` lo cambia, en segundos). Un incendio se da por terminado tras 3 min sin ver nada.

Lo que informa la alerta es lo **confirmado**: `fuego_detectado`, `humo_detectado` y `confianza` salen del tipo confirmado en la ventana, no del último cuadro suelto.

## Instalar y ejecutar

```
cd firewatch-project-camara
& "$HOME\.venv-camara\Scripts\Activate.ps1"
pip install -r requirements.txt
copy .env.example .env        # y completar (ver abajo)
python main_multicamera.py
```

Los modelos se leen de `..\models\` (están en el repositorio).

## Conectar con el backend (`.env`)

```
FIREWATCH_API_URL=http://localhost:8100
FIREWATCH_KEY_CAM_1=<clave de cam_1>
```

La clave la entrega `POST /camaras` del backend (se muestra **una sola vez**). El nombre es `FIREWATCH_KEY_` + el id de la cámara en mayúsculas (`cam_2` → `FIREWATCH_KEY_CAM_2`). Sin URL o sin la clave de una cámara, esa cámara solo guarda en la carpeta y no envía. El estado del envío aparece en el panel ("Backend: conectado / sin conexión / clave rechazada…").
`.env` está en `.gitignore`: nunca se sube.

**Con HTTPS** (backend en otro equipo): `FIREWATCH_API_URL=https://IP-DEL-SERVIDOR:8100` y, si el certificado es propio, `FIREWATCH_CA_BUNDLE=C:\ruta\cert.pem`. La verificación nunca se desactiva: si el certificado no es de confianza, la alerta queda en cola y el panel lo dice. Ver el README del backend, sección HTTPS.

## Agregar una cámara

Botón **➕ Agregar**. Tres tipos:

- **Webcam / USB**: el número (0 = la primera).
- **Cámara IP (Wi-Fi)**: marca (Tapo, Hikvision, Dahua/Imou, Reolink, Foscam, Axis u "Otra" con ruta manual), IP, usuario y clave **de la cuenta que tú creaste en tu cámara**. La clave se escribe oculta; al editar, dejarla en blanco conserva la anterior.
- **Archivo / URL**: un video de prueba (`C:\videos\prueba.mp4`) o una URL completa (`http://IP:8080/video` de un celular con la app *IP Webcam*, que sirve para probar sin cámara).

**🔌 Probar conexión** muestra una imagen real antes de guardar. Desde la terminal: `python tools\probar_camara.py --ip 192.168.1.50 --usuario micuenta` (pide la clave oculta).
Las rutas por marca son las habituales del fabricante, no una garantía: según modelo y firmware pueden variar (y algunas cámaras con app propia en la nube no ofrecen RTSP; no vale la pena forzarlas).

### Ubicación de la cámara

La alerta lleva las coordenadas de la cámara. Se pueden escribir o buscar con tres vías, de más a menos precisa: **ubicación de Windows** (Wi-Fi; no envía nada fuera del equipo), **dirección escrita** (OpenStreetMap/Nominatim recibe el texto de la dirección) y **IP pública** (aproximada, error de kilómetros; `ipwho.is` ve tu IP). Revisa siempre el punto en el mapa antes de guardar.

## Foto manual "sin detección"

Si la persona que está frente a la cámara ve algo y los modelos no (pasa: ver límites), el botón **📷 Enviar foto al operador (sin detección)** envía el cuadro actual como alerta `NO_DETECTION`; el operador la ve como "SIN DETECCIÓN — verificar la foto" y decide. Pide confirmación, no se envía sola nunca, hay 30 s de espera entre envíos por cámara y se rechaza si la cámara ya tiene una alerta activa (la central ya fue avisada). No cambia el estado del incidente: no apaga ni reemplaza una alerta automática.

## Pruebas

Sin cámara, sin modelos y sin red; desde esta carpeta:

```
python tools\test_logica_alertas.py     # confirmación, cooldowns, qué informa la alerta, foto manual
python tools\test_backend_client.py     # envío al backend, HTTPS y errores de certificado
python tools\test_ubicacion.py          # ubicación por Windows / dirección / IP
python tools\medir_falsas_alarmas.py "..\Videos de prueba\sin_fuego"   # videos SIN incendio: cuántas alertas falsas dan (necesita los modelos)
```
Cada una imprime `OK` o `FAIL` por caso. La interfaz (diálogos, botones) no tiene prueba automática: se verifica a mano.

## Carpetas y archivos

| | |
|---|---|
| `main_multicamera.py` | Panel multi-cámara (el programa). `main.py` es la versión anterior, de una sola cámara. |
| `camera_manager.py`, `camera_sources.py` | Hilos de captura con reconexión; armado de la URL por marca. |
| `backend_client.py` | Cola y envío al backend. |
| `camera_location.py` | Ubicación (Windows, dirección, IP). |
| `cameras_config.json` | Las cámaras agregadas. **Contiene la fuente completa, incluida la clave de una cámara IP en texto plano**; está en `.gitignore`. |
| `alertas_pendientes/`, `alertas_enviadas/`, `alertas_rechazadas/` | Cola y copias (JPG + JSON). Las copias **no se borran solas**. |

## Seguridad y privacidad

- No se graba video: los cuadros se analizan en memoria y solo se guarda una foto al generar una alerta (automática o manual). Esa foto puede mostrar lo que haya a la vista (personas, vehículos, casas): apuntar la cámara hacia el cerro o el bosque, no hacia espacios privados. No hay desenfoque de personas.
- La clave de la cámara IP vive en `cameras_config.json` de ese equipo; usar una cuenta propia de la cámara, con clave exclusiva. La clave del backend (`FIREWATCH_KEY_*`) vive en `.env`.
- Para poner una cámara ajena en el sistema, ver [GUIA_DUENO_CAMARA.md](GUIA_DUENO_CAMARA.md).

## Límites conocidos (para el informe)

- **Los modelos son débiles.** Con los umbrales del panel, sobre imágenes de prueba de D-Fire y FASDD: fuego detecta ~50 % y ~28 % con ~12 % de falsas; humo detecta ~15 % y ~26 % con ~0,7 % de falsas (`evaluacion_modelos/`, METODOLOGIA 5i). La confirmación 3 de 5 / 2 de 5 baja las falsas, pero también retrasa y puede perder un fuego breve.
- **Falsas alarmas:** en una medición con 23 videos sin incendio real hubo 6 alertas falsas en ~8 min; la niebla y las nubes puntuaron más alto que un fuego real. Está pendiente el reentrenamiento (depende del material del compañero).
- Probada de punta a punta con videos y con un celular como cámara IP; **no** con una cámara IP de marca ni con la webcam en uso prolongado.
- El análisis es a 416 px; evaluar 640 px requiere re-exportar los modelos (`evaluacion_modelos/exportar_640.py`).
