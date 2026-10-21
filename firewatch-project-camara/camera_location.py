"""
FireWatch · Cámara — cómo averiguar dónde está la cámara sin escribir las coordenadas a mano.

Tres vías, de la más precisa a la más burda (la interfaz las ofrece en este orden):
  1. ubicacion_windows()  → servicio de ubicación de Windows (Wi-Fi). Precisión típica: decenas a cientos
                            de metros. No necesita librerías: llama a PowerShell.
  2. buscar_direccion()   → dirección escrita → coordenadas con OpenStreetMap (Nominatim). Es la más
                            confiable para una cámara fija.
  3. ubicacion_por_ip()   → ciudad aproximada según la IP pública (error de kilómetros). Solo respaldo.

Todas devuelven un dict {"lat", "lon", "fuente", "detalle"} o lanzan UbicacionError con un mensaje
pensado para mostrarse tal cual al usuario. Solo usa la biblioteca estándar.
Privacidad: (2) envía el texto de la dirección a nominatim.openstreetmap.org y (3) hace que ipwho.is
vea tu IP pública; (1) no envía nada fuera del equipo.
"""
import base64
import json
import subprocess
import sys
import urllib.parse
import urllib.request

USER_AGENT = "FireWatch-Camara/1.0 (proyecto academico Duoc UC)"   # Nominatim exige identificar la app
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
IP_URL = "https://ipwho.is/"

_PS_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
try {
  Add-Type -AssemblyName System.Device
  $w = New-Object System.Device.Location.GeoCoordinateWatcher([System.Device.Location.GeoPositionAccuracy]::Default)
  [void]$w.TryStart($false, [TimeSpan]::FromSeconds(10))
  $t = 0
  while ($w.Status -ne 'Ready' -and $w.Permission -ne 'Denied' -and $t -lt 120) { Start-Sleep -Milliseconds 100; $t++ }
  if ($w.Permission -eq 'Denied') { '{"error":"denegado"}' }
  elseif ($w.Position.Location.IsUnknown) { '{"error":"desconocida"}' }
  else {
    $l = $w.Position.Location
    (@{ lat = $l.Latitude; lon = $l.Longitude; acc = $l.HorizontalAccuracy } | ConvertTo-Json -Compress)
  }
} catch { '{"error":"' + ($_.Exception.Message -replace '"', "'") + '"}' }
"""


UMBRAL_IMPRECISA_M = 2000   # sobre esto, la ubicación de Windows es solo "la zona", no el punto


def _fmt_metros(m):
    return f"{m / 1000:.0f} km" if m >= 1000 else f"{int(round(m))} m"


class UbicacionError(Exception):
    """Mensaje listo para mostrar al usuario."""


def _coords_validas(lat, lon):
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if lat == lon == 0 or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon


# ---------------------------------------------------------------- 1. Windows
def interpretar_salida_windows(texto):
    """Convierte lo que imprime el script de PowerShell en un resultado o un error legible."""
    try:
        d = json.loads((texto or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise UbicacionError("Windows no devolvió una ubicación legible.")
    err = d.get("error")
    if err == "denegado":
        raise UbicacionError("Windows no deja a esta aplicación usar la ubicación. Actívala en Configuración › "
                             "Privacidad y seguridad › Ubicación (y permite el acceso a aplicaciones de escritorio).")
    if err == "desconocida":
        raise UbicacionError("Windows no pudo estimar la ubicación (sin Wi-Fi o servicio apagado).")
    if err:
        raise UbicacionError("Windows no pudo dar la ubicación: " + str(err)[:120])
    c = _coords_validas(d.get("lat"), d.get("lon"))
    if not c:
        raise UbicacionError("Windows devolvió una ubicación inválida.")
    acc = d.get("acc")
    detalle = "Ubicación de Windows (Wi-Fi)"
    aproximada = False
    if isinstance(acc, (int, float)) and acc == acc and acc > 0:
        detalle += f", precisión de unos {_fmt_metros(acc)}"
        if acc > UMBRAL_IMPRECISA_M:
            aproximada = True
            detalle += ": demasiado imprecisa para una cámara fija. Escribe la dirección y pulsa Buscar"
    return {"lat": c[0], "lon": c[1], "fuente": "windows", "detalle": detalle, "aproximada": aproximada}


def ubicacion_windows(timeout=25):
    if not sys.platform.startswith("win"):
        raise UbicacionError("La ubicación de Windows solo existe en Windows.")
    cod = base64.b64encode(_PS_SCRIPT.encode("utf-16-le")).decode("ascii")
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", cod],
            capture_output=True, text=True, timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except FileNotFoundError:
        raise UbicacionError("No se encontró PowerShell en este equipo.")
    except subprocess.TimeoutExpired:
        raise UbicacionError("Windows tardó demasiado en responder la ubicación.")
    return interpretar_salida_windows(r.stdout)


# ---------------------------------------------------------------- 2. dirección
def _get_json(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def buscar_direccion(texto, pais="cl"):
    texto = (texto or "").strip()
    if len(texto) < 4:
        raise UbicacionError("Escribe la dirección completa (calle, número y comuna).")
    q = {"q": texto, "format": "jsonv2", "limit": 1, "addressdetails": 1}
    if pais:
        q["countrycodes"] = pais
    try:
        datos = _get_json(NOMINATIM_URL + "?" + urllib.parse.urlencode(q))
    except Exception:
        raise UbicacionError("No pude consultar el buscador de direcciones (¿hay internet?).")
    if not datos:
        raise UbicacionError("No encontré esa dirección. Prueba con calle, número y comuna (ej: Av. Libertad 123, Viña del Mar).")
    c = _coords_validas(datos[0].get("lat"), datos[0].get("lon"))
    if not c:
        raise UbicacionError("El buscador devolvió coordenadas inválidas.")
    nombre = str(datos[0].get("display_name", ""))[:140]
    detalle = "Dirección encontrada: " + nombre
    aproximada = False
    pide_numero = any(ch.isdigit() for ch in texto)
    if pide_numero and not (datos[0].get("address") or {}).get("house_number"):
        aproximada = True
        detalle += " (solo encontró la calle, no el número exacto: ajusta el punto si hace falta)"
    return {"lat": c[0], "lon": c[1], "fuente": "direccion", "detalle": detalle, "aproximada": aproximada}


# ---------------------------------------------------------------- 3. IP
def ubicacion_por_ip():
    try:
        d = _get_json(IP_URL)
    except Exception:
        raise UbicacionError("No pude consultar la ubicación por IP (¿hay internet?).")
    if not d.get("success", True):
        raise UbicacionError("El servicio de IP no pudo ubicarte.")
    c = _coords_validas(d.get("latitude"), d.get("longitude"))
    if not c:
        raise UbicacionError("El servicio de IP no devolvió coordenadas.")
    lugar = ", ".join(x for x in (d.get("city"), d.get("region")) if x)
    return {"lat": c[0], "lon": c[1], "fuente": "ip",
            "detalle": f"Aproximada por IP ({lugar or 'ciudad'}): puede errar varios kilómetros, ajústala",
            "aproximada": True}


def url_mapa(lat, lon, zoom=17):
    return f"https://www.openstreetmap.org/?mlat={lat:.6f}&mlon={lon:.6f}#map={zoom}/{lat:.6f}/{lon:.6f}"
