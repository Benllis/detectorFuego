# FireWatch — Backend (v0.1)

API central que reemplaza la carpeta compartida `alertas_pendientes`. El detalle de rutas y campos está en [CONTRATO_API.md](CONTRATO_API.md).

## Estado

| Bloque | Estado |
|---|---|
| `/salud`, `/login`, `/usuarios` | listo |
| `/camaras` (crear, listar, editar) | listo |
| `/alertas` (enviar, listar, ver, imagen, confirmar/descartar) | listo |
| `/propagacion/m2` (continuación de un foco, modelo M2) | listo (capa en el mapa verificada en vivo) |
| `/reportes` (formularios de bomberos y usuarios, con límite de envíos) | listo (falta verificar/descartar con `PATCH`) |
| HTTPS o red cerrada | listo (certificado propio + salvaguarda de arranque); falta decidir cuál usar en la demo |
| Conexión real de la cámara y del escritorio | listo (probado el 15-10-2026) |

Probado con pruebas automáticas (79, todas pasan) y en vivo el 15-10-2026: cámara (con videos de prueba) → backend → escritorio y mapa, incluyendo confirmar/descartar, reintento con el backend caído y recuperación del escritorio. Aún no se ha probado con una cámara IP o webcam real.

## Instalar y ejecutar

```
cd firewatch-project-backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python -m app.servidor
```

`python -m app.servidor` arranca el backend en `http://127.0.0.1:8100` (solo este equipo) y se niega a escuchar en la red sin HTTPS (ver más abajo). El arranque de siempre, `uvicorn app.main:create_app --factory --port 8100`, sigue funcionando pero **no** tiene esa salvaguarda.

La primera vez imprime la contraseña del usuario `admin` (o usa `ADMIN_PASSWORD` del `.env`). Documentación interactiva en `http://localhost:8100/docs`.

## Primer uso

1. Iniciar sesión: `POST /login` con `admin` y su contraseña → copia el `token`.
2. Registrar una cámara: `POST /camaras` con `Authorization: Bearer <token>` y `{"camara_id": "cam_1", "nombre": "Webcam"}` → devuelve la `clave` de la cámara (se muestra una sola vez).
3. Probar una alerta: `python tools/simular_camara.py --clave <clave> --camara cam_1`
4. Verla: `GET /alertas` con el token.

## Pruebas

```
python -m pytest -q tests
```

Se ejecuta con `python -m pytest` (desde la carpeta del backend y con su `.venv` activo) para que Python encuentre el paquete `app`.

## Modelo M2 (propagación)

`POST /propagacion/m2` usa `models/m2/modelo_M2_final.joblib` (scikit-learn **1.9.1**, fijo en `requirements.txt`).
Al cargarlo se verifica su SHA-256 contra `modelo_M2_final.joblib.sha256`: un `.joblib` es un pickle y abrir uno alterado ejecuta código ajeno.
Si cambias el modelo, regenera el hash (`python -c "import hashlib;print(hashlib.sha256(open('models/m2/modelo_M2_final.joblib','rb').read()).hexdigest())"`).
Otra ubicación: variable `FIREWATCH_M2_MODELO`. Qué hace y qué no (solo continuación de un foco ya detectado; niveles relativos, no porcentajes): ver `app/m2.py` y CONTRATO_API.md §5b.

## HTTPS o red cerrada

El backend maneja contraseñas, tokens de sesión y claves de cámara: en una red compartida **no** debe hablar HTTP plano. Hay dos formas de cuidarlo; para la demo basta una.

**A. Todo en el mismo equipo (lo más simple).** Backend, mapa y escritorio en un solo computador: se deja `FIREWATCH_HOST=127.0.0.1` (por defecto) y nada sale a la red. Si la cámara está en otro equipo, no sirve; usa la B.

**B. HTTPS con certificado propio en la red local.**

1. En el equipo del backend, con el `.venv` activo: `pip install -r requirements.txt` (trae `cryptography`).
2. Generar el certificado con la IP que tiene ese equipo en la red (`ipconfig` → "Dirección IPv4"):
   ```
   python tools/generar_certificado.py --ip 192.168.1.50
   ```
   Crea `certs/cert.pem` (público) y `certs/key.pem` (**privado**: no se sube a git ni se comparte; `certs/` y `*.pem` ya están en `.gitignore`). El certificado solo vale para `localhost`, `127.0.0.1` y las IP o nombres que le pases; si la IP del equipo cambia, hay que generarlo de nuevo (`--forzar`).
3. En `.env` del backend:
   ```
   FIREWATCH_HOST=0.0.0.0
   FIREWATCH_SSL_CERT=certs/cert.pem
   FIREWATCH_SSL_KEY=certs/key.pem
   ```
   y arrancar con `python -m app.servidor` (dirá `Escuchando en https://...`). Abrir el puerto 8100 en el Firewall de Windows solo para la red privada.
4. Cada cliente debe **confiar en ese certificado**, porque no lo firmó una autoridad conocida (el cliente lo rechaza y avisa, nunca se desactiva la verificación):
   - **Cámara** (`firewatch-project-camara/.env`): `FIREWATCH_API_URL=https://192.168.1.50:8100` y `FIREWATCH_CA_BUNDLE=C:\ruta\cert.pem`.
   - **Escritorio**: variables de entorno `FIREWATCH_API=https://192.168.1.50:8100` y `FIREWATCH_CA_BUNDLE=C:\ruta\cert.pem` antes de abrir el programa.
   - **Mapa en el navegador**: abrir una vez `https://192.168.1.50:8100/salud` y aceptar el aviso del certificado, o instalar `cert.pem` en "Entidades de certificación raíz de confianza" del equipo. Además, el origen del mapa debe estar en `FIREWATCH_CORS`.
5. Copiar solo `cert.pem` (nunca `key.pem`) a los otros equipos.

Si el backend queda detrás de un proxy propio (Caddy, nginx) que ya pone el HTTPS, el backend puede escuchar en `127.0.0.1` y se activa `FIREWATCH_PROXY_CONFIAR=1` para que el límite de `/reportes` use la IP real del visitante.

Salvaguarda: `python -m app.servidor` **no arranca** con `FIREWATCH_HOST` distinto de `127.0.0.1` si no hay certificado, salvo que pongas `FIREWATCH_PERMITIR_HTTP=1` (solo para pruebas en una red que controlas). Un certificado propio protege del espionaje en la red, no de alguien que ya tenga acceso al equipo del servidor.

## Reportes (formularios)

`POST /reportes` recibe reportes de ciudadanos (sin iniciar sesión) y de bomberos (con sesión). Al ser público, tiene límite por IP (5 por hora) y un tope total (100 por hora), `Retry-After` en el `429`, tamaño máximo de cuerpo y foto (3 MB, JPG verificado por contenido) y validación de todos los campos. El texto se guarda como dato: los formularios y pantallas que lo muestren deben escaparlo. Detalle y errores en [CONTRATO_API.md](CONTRATO_API.md) §5; variables en `.env.example`.

## Seguridad

- Contraseñas con bcrypt; tokens y claves de cámara guardados solo como hash.
- Bloqueo de 1 minuto tras 5 intentos de login fallidos.
- Solo se aceptan imágenes JPG de hasta 5 MB; el nombre del archivo sale del `id_alerta`, validado.
- `.env` y `data/` están en `.gitignore`.
- `/reportes` público con límite por IP y tope global, validación estricta y foto verificada.
- HTTPS con certificado propio y arranque que se niega a exponerse sin cifrado (sección anterior).
- Pendiente antes de la demo: elegir A o B y probarlo con la cámara en otro equipo; `PATCH` de reportes; un navegador que verifique el certificado de punta a punta (probado solo con `requests`).
