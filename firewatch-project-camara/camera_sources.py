"""
camera_sources.py — Cómo se describe y se prueba una fuente de video (sin interfaz gráfica).

Tipos de fuente:
  · webcam / USB:     "0", "1", …
  · cámara IP (RTSP): rtsp://usuario:clave@192.168.1.50:554/ruta   (la mayoría de las marcas lo ofrece)
  · archivo o URL:    una ruta de video (pruebas) o una URL http(s)/rtsp que el usuario ya tiene armada

La cámara es SIEMPRE de quien instala el programa: se usa el usuario y la clave que el propio dueño creó en su
cámara. Este módulo no busca cámaras en la red ni prueba claves: solo arma la URL con lo que el dueño escribió.
"""
import os
import re
import time
from urllib.parse import quote, unquote, urlsplit

# Marcas frecuentes. Son rutas por defecto de cada fabricante, NO una garantía: según el modelo o el firmware
# pueden cambiar; por eso existe "Probar conexión" y la opción "Otra (ruta manual)".
#   port: puerto RTSP habitual · main/sub: ruta del flujo principal y del secundario ({ch} = canal)
BRANDS = {
    "tapo":     {"label": "TP-Link Tapo / Kasa", "port": 554, "main": "/stream1", "sub": "/stream2",
                 "hint": "En la app Tapo: Ajustes de la cámara > Avanzado > Cuenta de la cámara. Ese usuario y clave son los que se usan aquí."},
    "hikvision": {"label": "Hikvision", "port": 554, "main": "/Streaming/Channels/{ch}01", "sub": "/Streaming/Channels/{ch}02",
                  "hint": "Usuario y clave del equipo (admin o el que creaste). RTSP suele venir activado."},
    "dahua":    {"label": "Dahua / Amcrest / Imou", "port": 554, "main": "/cam/realmonitor?channel={ch}&subtype=0", "sub": "/cam/realmonitor?channel={ch}&subtype=1",
                 "hint": "Usuario y clave del equipo. En Imou puede requerir activar RTSP/ONVIF en la app."},
    "reolink":  {"label": "Reolink", "port": 554, "main": "/h264Preview_{ch:02d}_main", "sub": "/h264Preview_{ch:02d}_sub",
                 "hint": "Usuario y clave del equipo. En algunos modelos hay que activar RTSP en la configuración."},
    "foscam":   {"label": "Foscam", "port": 88, "main": "/videoMain", "sub": "/videoSub",
                 "hint": "Puerto 88 en muchos modelos; usuario y clave del equipo."},
    "axis":     {"label": "Axis", "port": 554, "main": "/axis-media/media.amp", "sub": "/axis-media/media.amp?resolution=640x480",
                 "hint": "Usuario y clave del equipo."},
    "otra":     {"label": "Otra (ruta manual)", "port": 554, "main": "", "sub": "",
                 "hint": "Escribe la ruta que indica el manual de tu cámara (lo que va después del puerto), por ejemplo /stream1."},
}
BRAND_ORDER = ["tapo", "hikvision", "dahua", "reolink", "foscam", "axis", "otra"]

_URL_CRED = re.compile(r"^(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*://)(?P<user>[^:@/\s]*)(?::(?P<pwd>[^@/\s]*))?@")


def brand_label(key):
    return BRANDS[key]["label"]


def brand_from_label(label):
    for k, v in BRANDS.items():
        if v["label"] == label:
            return k
    return "otra"


def build_rtsp_url(brand, host, port=None, user="", password="", stream="main", channel=1, path=""):
    """Arma rtsp://usuario:clave@host:puerto/ruta. Usuario y clave se codifican (aceptan @, :, / y espacios)."""
    host = (host or "").strip()
    if not host:
        raise ValueError("Falta la dirección IP o el nombre de la cámara.")
    host = re.sub(r"^[a-zA-Z]+://", "", host).split("/")[0]  # por si pegaron la URL completa
    if ":" in host and not host.startswith("["):
        host, _, maybe_port = host.partition(":")
        if maybe_port.isdigit() and not port:
            port = int(maybe_port)
    spec = BRANDS.get(brand, BRANDS["otra"])
    port = int(port) if port not in (None, "") else spec["port"]
    if not 1 <= port <= 65535:
        raise ValueError("El puerto debe estar entre 1 y 65535.")
    route = (path or "").strip() or spec["sub" if stream == "sub" else "main"]
    if not route:
        raise ValueError("Para esta marca debes indicar la ruta del video (la entrega el manual de la cámara).")
    try:
        route = route.format(ch=int(channel))
    except (KeyError, IndexError, ValueError):
        pass  # una ruta manual con llaves propias se deja tal cual
    if not route.startswith("/"):
        route = "/" + route
    cred = ""
    if user:
        cred = quote(user, safe="")
        if password:
            cred += ":" + quote(password, safe="")
        cred += "@"
    return f"rtsp://{cred}{host}:{port}{route}"


def mask_source(source):
    """La misma fuente con la clave oculta: es lo único que se muestra en pantalla y en los registros."""
    s = str(source)
    m = _URL_CRED.match(s)
    if not m or m.group("pwd") is None:
        return s
    return s[:m.start("pwd")] + "••••" + s[m.end("pwd"):]


def source_kind(source):
    s = str(source).strip()
    if s.isdigit():
        return "webcam"
    low = s.lower()
    if low.startswith(("rtsp://", "rtsps://")):
        return "ip"
    if low.startswith(("http://", "https://", "rtmp://")):
        return "url"
    return "archivo"


def split_rtsp(source):
    """Descompone una URL rtsp en sus partes (para volver a mostrarla en el formulario). La clave NO se devuelve."""
    u = urlsplit(str(source))
    return {
        "host": u.hostname or "", "port": u.port or 554, "user": unquote(u.username) if u.username else "",
        "path": (u.path or "") + (("?" + u.query) if u.query else ""),
    }


def parse_gps(lat_text, lon_text):
    """('-33.45', '-70.66') → (−33.45, −70.66); acepta coma decimal y 'lat, lon' pegado de Google Maps en el primer campo."""
    lat_text, lon_text = (lat_text or "").strip(), (lon_text or "").strip()
    if lat_text and not lon_text and "," in lat_text:
        lat_text, _, lon_text = lat_text.partition(",")
        lat_text, lon_text = lat_text.strip(), lon_text.strip()
    if not lat_text and not lon_text:
        return None
    try:
        lat = float(lat_text.replace(",", ".")); lon = float(lon_text.replace(",", "."))
    except ValueError:
        raise ValueError("Coordenadas inválidas: usa números como -33.4489 y -70.6693.")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("Coordenadas fuera de rango (latitud entre -90 y 90, longitud entre -180 y 180).")
    return (lat, lon)


# ---------------------------------------------------------------- abrir y probar
def configure_ffmpeg_for_streams():
    """RTSP por TCP (más estable que UDP en redes domésticas y por Wi-Fi). Debe ejecutarse antes de abrir el primer video."""
    os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")


def open_capture(source, timeout_ms=8000):
    """cv2.VideoCapture con tiempos de espera (apertura y lectura) para que una cámara caída no deje colgado el programa."""
    import cv2
    configure_ffmpeg_for_streams()
    src = int(source) if str(source).strip().isdigit() else str(source).strip()
    if isinstance(src, int):
        return cv2.VideoCapture(src, cv2.CAP_DSHOW if os.name == "nt" else cv2.CAP_ANY)
    params = []
    if hasattr(cv2, "CAP_PROP_OPEN_TIMEOUT_MSEC") and hasattr(cv2, "CAP_PROP_READ_TIMEOUT_MSEC"):
        params = [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, int(timeout_ms), cv2.CAP_PROP_READ_TIMEOUT_MSEC, int(timeout_ms)]
    try:
        return cv2.VideoCapture(src, cv2.CAP_FFMPEG, params) if params else cv2.VideoCapture(src, cv2.CAP_FFMPEG)
    except TypeError:  # OpenCV viejo sin parámetros
        return cv2.VideoCapture(src, cv2.CAP_FFMPEG)


_FRIENDLY = [
    ("401", "La cámara rechazó el usuario o la clave. Revisa la \"cuenta de la cámara\" (no es la clave de tu cuenta de la app)."),
    ("unauthorized", "La cámara rechazó el usuario o la clave. Revisa la \"cuenta de la cámara\" (no es la clave de tu cuenta de la app)."),
    ("404", "La cámara respondió pero esa ruta no existe: prueba otra marca u \"Otra (ruta manual)\"."),
    ("timed out", "La cámara no respondió a tiempo. Revisa la IP, que esté encendida y en la misma red."),
    ("refused", "La cámara rechazó la conexión: revisa el puerto y que RTSP esté activado en su configuración."),
]


def probe_source(source, timeout_ms=8000, frames=3):
    """
    Intenta abrir la fuente y leer un cuadro. Devuelve (ok, mensaje_en_español, cuadro_o_None, segundos).
    Nunca incluye la clave en el mensaje.
    """
    t0 = time.time()
    cap = None
    try:
        cap = open_capture(source, timeout_ms)
        if not cap.isOpened():
            return False, "No se pudo abrir la fuente. Revisa IP, puerto, usuario, clave y que RTSP esté activado en la cámara.", None, time.time() - t0
        frame = None
        for _ in range(max(1, frames)):
            ok, f = cap.read()
            if ok and f is not None:
                frame = f
        if frame is None:
            return False, "La cámara se abrió pero no entregó imagen (puede estar ocupada por otra aplicación o usar un formato no compatible).", None, time.time() - t0
        h, w = frame.shape[:2]
        return True, f"Conexión correcta: imagen de {w}x{h} px.", frame, time.time() - t0
    except Exception as err:  # noqa: BLE001
        msg = str(err)
        for key, text in _FRIENDLY:
            if key in msg.lower():
                return False, text, None, time.time() - t0
        return False, f"No se pudo probar la fuente: {type(err).__name__}.", None, time.time() - t0
    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception:  # noqa: BLE001
                pass
