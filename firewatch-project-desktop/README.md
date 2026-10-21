# FireWatch — Escritorio (central de monitoreo)

Programa para el operador: inicia sesión en el backend, ve las alertas que llegan de las cámaras, mira la foto y las **confirma** o **descarta** (con motivo). Todo pasa por la API del backend (`../firewatch-project-backend`); el escritorio ya no tiene base de datos propia.

## Qué hace

- **Inicio de sesión** con una cuenta del backend (`POST /login`). Los roles `ADMIN` y `OPERADOR` pueden resolver alertas; `BOMBERO` solo puede mirarlas (el servidor rechaza su resolución).
- **Alertas pendientes**, consultadas cada 3 s. Las nuevas suenan y se marcan "● NUEVA". La lista se puede filtrar por tipo (`CRITICAL`, `FIRE`, `SMOKE`, `FOTO`); `FOTO` muestra las alertas `NO_DETECTION` (foto enviada a mano desde la cámara), que también aparecen en "Todas" y se ven como "SIN DETECCIÓN — verificar la foto".
- **Ficha de la alerta**: foto, fecha, cámara, ubicación, coordenadas y confianza del modelo (o `n/d`).
- **Confirmar / descartar.** Descartar exige un motivo. Si el servidor no alcanza a guardar la resolución, **la alerta sigue pendiente** y se avisa; si otro puesto la resolvió antes, desaparece de la lista.
- **Historial** de las ya resueltas (quién y cuándo, y el motivo del descarte).
- Si se cae la conexión, la barra lo dice y se reintenta sola.

## Instalar y ejecutar

```
cd firewatch-project-desktop
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

El backend debe estar corriendo (por defecto `http://127.0.0.1:8100`). Para otro servidor, variables de entorno **antes** de abrir el programa:

```
$env:FIREWATCH_API = "https://192.168.1.50:8100"
$env:FIREWATCH_CA_BUNDLE = "C:\ruta\cert.pem"    # solo si el backend usa HTTPS con certificado propio
python main.py
```

La verificación del certificado nunca se desactiva: si no es de confianza, el programa lo dice ("No se pudo verificar el certificado HTTPS…"). Ver el README del backend, sección HTTPS.

## Pruebas

Sin ventana, sin red y sin servidor (la interfaz, `requests` y `login_view` van simulados):

```
python tests\test_escritorio.py
```

Imprime `OK` o `FAIL` por caso y termina con `FALLOS: 0` si todo pasa. Cubre el cliente de la API (token, filtros, motivo, errores, certificado), orden y filtro de la lista, sincronización con el servidor, confirmar/descartar, consulta periódica y la ficha. **Lo visual (colores, tamaños, sonido) no tiene prueba automática: se mira a mano.**

## Archivos

| | |
|---|---|
| `main.py` | Ventana principal (lista, ficha, historial). |
| `login_view.py` | Ventana de inicio de sesión. |
| `api_client.py` | Todas las llamadas al backend. |
| `tests/test_escritorio.py` | Pruebas. |
| `database.py`, `firewatch_central.db` | **Restos de la versión anterior** (base local con contraseñas en SHA-256 sin sal). **Nada los usa** (`main.py` y `login_view.py` ya van por la API). Borrarlos en la limpieza final; `*.db` ya está en `.gitignore`. |

## Límites conocidos

- La lista muestra hasta 60 alertas (avisa cuántas más hay); el historial toma las últimas 200 alertas y muestra las ya resueltas.
- No hay aún pantalla para los **reportes** de bomberos y ciudadanos (`/reportes`): el backend ya los recibe, falta la vista y el `PATCH` para verificarlos.
- Con la sesión vencida (12 h) las consultas fallan con el aviso del servidor ("sesión inválida o vencida"): hay que cerrar sesión y volver a entrar; no lo hace solo.
