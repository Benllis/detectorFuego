#!/usr/bin/env python3
"""
FireWatch · Mapa 3D — servidor local + proxy mínimo de datos.

Reemplaza a `python -m http.server`: sirve los archivos estáticos del mapa
y, además, expone unos pocos endpoints /api/* que:

  1. guardan las credenciales FUERA del código del navegador (token de la
     DMC y MAP_KEY de NASA FIRMS, leídos de `.env`),
  2. cachean las respuestas (memoria + disco en ./cache) para no gastar la
     cuota de Open-Meteo (10.000 llamadas/día por IP) ni la de FIRMS al
     recargar la página o abrir varias pestañas,
  3. normalizan los datos de la DMC (nombres con codificación rota, nudos
     a km/h, último dato por estación).

Solo usa la librería estándar de Python (3.8+). No hay que instalar nada.

    python server.py            # http://localhost:8080
    python server.py 9000       # otro puerto

Escucha solo en 127.0.0.1 (nada expuesto a la red). Ver .env.example.
"""

import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import storage
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(ROOT, "cache")

DMC_BASE = "https://climatologia.meteochile.gob.cl/application/servicios"
FIRMS_BASE = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
OSRM_URL = "https://router.project-osrm.org/route/v1/driving"      # servidor de demo: uso liviano
TOMTOM_ROUTE_URL = "https://api.tomtom.com/routing/1/calculateRoute"
TOMTOM_FLOW_URL = "https://api.tomtom.com/traffic/map/4/tile/flow/relative"   # tiles raster (.png)
TOMTOM_FLOW_VECTOR_URL = "https://api.tomtom.com/traffic/map/4/tile/flow/relative"  # mismos datos en vectores (.pbf)

FIRMS_SOURCES = {
    "VIIRS_NOAA20_NRT", "VIIRS_NOAA20_SP",
    "VIIRS_NOAA21_NRT",
    "VIIRS_SNPP_NRT", "VIIRS_SNPP_SP",
    "MODIS_NRT", "MODIS_SP",
}

TTL = {
    "dmc_estaciones": 24 * 3600,   # el catálogo casi no cambia
    "dmc_recientes": 10 * 60,      # las EMA reportan cada ~15 min
    "firms_live": 30 * 60,         # FIRMS NRT: 1-2 pasadas por día
    "firms_replay": 7 * 24 * 3600, # histórico: no cambia
    "open_meteo": 30 * 60,         # el modelo se recalcula cada hora
    "om_archive_recent": 3 * 3600, # últimos días: Open-Meteo aún puede ajustarlos
    "om_archive_old": 7 * 24 * 3600,  # histórico: no cambia
    "route_osrm": 3600,            # la red vial cambia lento
    "route_tomtom": 2 * 60,        # incluye tráfico en vivo
    "traffic_tile": 5 * 60,        # el flujo se actualiza cada ~minuto; 5 min cuida la cuota gratis
}

HTTP_TIMEOUT = 25
MAX_POST_BYTES = 30 * 1024 * 1024  # grilla de combustible de un área de ~56 km: ~4-6 MB
KNOTS_TO_KMH = 1.852

# Rutas que JAMÁS se sirven como archivo estático (secretos y código del servidor).
BLOCKED_PREFIXES = ("/.", "/cache", "/server.py", "/storage.py", "/data", "/__pycache__")


# ---------------------------------------------------------------- .env
def load_env(path):
    env = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return env


ENV = load_env(os.path.join(ROOT, ".env"))


def secret(name):
    return os.environ.get(name) or ENV.get(name) or ""


# ---------------------------------------------------------------- caché
_mem = {}


def _cache_path(key):
    return os.path.join(CACHE_DIR, hashlib.sha256(key.encode()).hexdigest()[:24] + ".json")


def cache_get(key, ttl, allow_stale=False):
    now = time.time()
    hit = _mem.get(key)
    if not hit:
        try:
            with open(_cache_path(key), encoding="utf-8") as fh:
                hit = json.load(fh)
            _mem[key] = hit
        except (OSError, ValueError):
            hit = None
    if hit and (allow_stale or now - hit["t"] < ttl):
        return hit["v"], now - hit["t"]
    return None, None


def cache_put(key, value):
    entry = {"t": time.time(), "v": value}
    _mem[key] = entry
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(_cache_path(key), "w", encoding="utf-8") as fh:
            json.dump(entry, fh)
    except OSError as err:
        print(f"[cache] no se pudo escribir en disco: {err}", file=sys.stderr)


def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "FireWatch-Mapa/1.0 (proyecto academico)"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return resp.read()


def cached_fetch(key, ttl, producer):
    """Devuelve (valor, edad_seg, stale). Si el upstream falla y hay dato viejo, lo devuelve marcado stale."""
    value, age = cache_get(key, ttl)
    if value is not None:
        return value, age, False
    try:
        value = producer()
    except Exception as err:  # noqa: BLE001 — cualquier fallo de red cae al caché viejo
        stale, stale_age = cache_get(key, ttl, allow_stale=True)
        if stale is not None:
            print(f"[cache] upstream falló ({err}); sirviendo dato viejo de {int(stale_age)}s", file=sys.stderr)
            return stale, stale_age, True
        raise
    cache_put(key, value)
    return value, 0, False


# ---------------------------------------------------------------- archivo histórico: descarga + guardado con registro
def _ingest(source, variant, detail, fetch, save):
    """
    Descarga (fetch) y guarda (save) anotando en el registro qué pasó: ok, aviso o error, con el
    tipo de error y la fase (descarga o guardado). Un fallo al GUARDAR nunca impide servir el dato.
    `save(text)` devuelve (filas_recibidas, filas_nuevas o None, problema o None).
    """
    t0 = time.time()
    try:
        text = fetch()
    except Exception as err:  # noqa: BLE001
        storage.log_event(source, "error", variant=variant, phase="descarga", err=err, detail=detail, ms=(time.time() - t0) * 1000)
        raise
    try:
        rows, new, problem = save(text)
        ms = (time.time() - t0) * 1000
        if problem:
            storage.log_event(source, "warn", variant=variant, phase="guardado", rows=rows, new=new, ms=ms,
                              error_type=problem[0], error_msg=problem[1], detail=detail)
        else:
            storage.log_event(source, "ok", variant=variant, phase="guardado", rows=rows, new=new, ms=ms, detail=detail)
    except Exception as err:  # noqa: BLE001
        storage.log_event(source, "error", variant=variant, phase="guardado", err=err, detail=detail, ms=(time.time() - t0) * 1000)
    return text


def _save_weather(query):
    def save(text):
        rows = storage.parse_open_meteo(query, text)
        n = len(rows)
        null_t = sum(1 for r in rows if r[4].get("temp_c") is None)
        storage.save_open_meteo(query, text)
        if n == 0:
            return 0, None, ("formato", "la respuesta no trajo ninguna lectura de clima")
        if null_t / n > 0.5:
            return n, None, ("datos_incompletos", f"{null_t} de {n} lecturas sin temperatura")
        return n, None, None
    return save


def _weather_variant(query):
    q = urllib.parse.parse_qs(query)
    return "actual" if "current" in q else "horario"


def _weather_detail(query):
    q = urllib.parse.parse_qs(query)
    variables = (q.get("current") or q.get("hourly") or [""])[0]
    return f"{len(q['latitude'][0].split(','))} puntos · {variables.count(',') + 1} variables ({variables[:90]})"


def _skip(source, variant, err):
    storage.log_event(source, "skipped", variant=variant, phase="solicitud", err=err)


# ---------------------------------------------------------------- utilidades DMC
def fix_mojibake(text):
    """La DMC devuelve UTF-8 mal codificado ('SÃ¡nchez'). Se repara si aplica."""
    if not isinstance(text, str):
        return text
    try:
        fixed = text.encode("latin-1").decode("utf-8")
        return fixed
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def to_float(x):
    try:
        if x is None or x == "":
            return None
        return float(str(x).replace(",", "."))
    except ValueError:
        return None


def dmc_catalog():
    def producer():
        raw = json.loads(http_get(f"{DMC_BASE}/getEstacionesRedEma").decode("utf-8"))
        rows = raw.get("datosEstacion") or []
        return [
            {
                "id": str(r.get("codigoNacional")),
                "name": fix_mojibake(str(r.get("nombreEstacion", "")).strip()),
                "lat": to_float(r.get("latitud")),
                "lon": to_float(r.get("longitud")),
                "elevation": r.get("altura"),
                "region": fix_mojibake(r.get("NombreRegion")),
                "zone": fix_mojibake(r.get("zonaGeografica")),
            }
            for r in rows
            if to_float(r.get("latitud")) is not None and to_float(r.get("longitud")) is not None
        ]

    return cached_fetch("dmc_estaciones", TTL["dmc_estaciones"], producer)


def _latest_measure(entry):
    """Toma el último registro de una estación, tolerando estructuras distintas (esquema sin verificar)."""
    data = entry.get("datos")
    if isinstance(data, list) and data:
        return max(data, key=lambda d: str(d.get("momento", "")))
    if isinstance(data, dict):
        return data
    return entry  # ya viene plano


def normalize_dmc_recientes(raw, catalog):
    """
    Convierte la respuesta de getDatosRecientesRedEma a una lista plana. El
    esquema exacto no se pudo verificar (requiere token), así que se buscan
    las estaciones en las claves más probables y se guarda el crudo en caché
    para inspección (cache/dmc_recientes_raw.json).
    """
    by_id = {c["id"]: c for c in catalog}
    entries = None
    for key in ("datosEstacion", "estaciones", "datos"):
        if isinstance(raw.get(key), list):
            entries = raw[key]
            break
    if entries is None:
        raise ValueError("Respuesta DMC con estructura desconocida (claves: %s)" % ", ".join(raw.keys()))

    out = []
    for e in entries:
        meta = e.get("estacion") if isinstance(e.get("estacion"), dict) else e
        sid = str(meta.get("codigoNacional", ""))
        m = _latest_measure(e)
        base = by_id.get(sid, {})
        lat = to_float(meta.get("latitud")) if meta.get("latitud") is not None else base.get("lat")
        lon = to_float(meta.get("longitud")) if meta.get("longitud") is not None else base.get("lon")
        if lat is None or lon is None:
            continue
        knots = to_float(m.get("fuerzaDelViento"))
        out.append({
            "id": sid,
            "name": fix_mojibake(str(meta.get("nombreEstacion") or base.get("name") or sid).strip()),
            "lat": lat,
            "lon": lon,
            "time": m.get("momento"),  # UTC
            "temp": to_float(m.get("temperatura")),
            "rh": to_float(m.get("humedadRelativa")),
            "windDir": to_float(m.get("direccionDelViento")),
            "windSpeedKmh": round(knots * KNOTS_TO_KMH, 1) if knots is not None else None,
        })
    return out


def dmc_recientes():
    user, token = secret("DMC_USUARIO"), secret("DMC_TOKEN")
    if not user or not token:
        err = PermissionError("Faltan DMC_USUARIO y DMC_TOKEN en .env")
        _skip("dmc", "recientes", err)
        raise err
    catalog, _, _ = dmc_catalog()

    def producer():
        qs = urllib.parse.urlencode({"usuario": user, "token": token})
        t0 = time.time()
        try:
            raw = json.loads(http_get(f"{DMC_BASE}/getDatosRecientesRedEma?{qs}").decode("utf-8"))
            if "mensaje" in raw and "datosEstacion" not in raw and "estaciones" not in raw:
                raise PermissionError("DMC respondió: " + fix_mojibake(str(raw["mensaje"])))
        except Exception as err:  # noqa: BLE001
            storage.log_event("dmc", "error", variant="recientes", phase="descarga", err=err, ms=(time.time() - t0) * 1000)
            raise
        cache_put("dmc_recientes_raw", raw)  # para diagnosticar el esquema real
        try:
            stations = normalize_dmc_recientes(raw, catalog)
        except Exception as err:  # noqa: BLE001
            storage.log_event("dmc", "error", variant="recientes", phase="guardado", err=err, ms=(time.time() - t0) * 1000,
                              error_type="formato", detail="el esquema de la respuesta no es el esperado; ver cache/dmc_recientes_raw.json")
            raise
        try:
            n = storage.save_station_obs(stations)
            fresh = sum(1 for s in stations if s.get("time"))
            if fresh == 0:
                storage.log_event("dmc", "warn", variant="recientes", phase="guardado", rows=n, ms=(time.time() - t0) * 1000,
                                  error_type="datos_incompletos", error_msg="ninguna estación trajo hora de medición")
            else:
                storage.log_event("dmc", "ok", variant="recientes", phase="guardado", rows=n, ms=(time.time() - t0) * 1000,
                                  detail=f"{len(stations)} estaciones")
        except Exception as err:  # noqa: BLE001
            storage.log_event("dmc", "error", variant="recientes", phase="guardado", err=err, ms=(time.time() - t0) * 1000)
        return stations

    return cached_fetch("dmc_recientes", TTL["dmc_recientes"], producer)


# ---------------------------------------------------------------- FIRMS / Open-Meteo
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def firms(params):
    key = secret("FIRMS_MAP_KEY")
    source = params.get("source", ["VIIRS_NOAA20_NRT"])[0]
    if not key:
        err = PermissionError("Falta FIRMS_MAP_KEY en .env")
        _skip("focos", source, err)
        raise err
    if source not in FIRMS_SOURCES:
        raise ValueError("source inválido")
    days = int(params.get("days", ["1"])[0])
    if not 1 <= days <= 5:
        raise ValueError("days debe estar entre 1 y 5")
    date = params.get("date", [""])[0]
    if date and not DATE_RE.match(date):
        raise ValueError("date debe ser YYYY-MM-DD")
    bbox = params.get("bbox", [""])[0]
    parts = bbox.split(",")
    if len(parts) != 4 or any(to_float(p) is None for p in parts):
        raise ValueError("bbox debe ser west,south,east,north")
    bbox = ",".join(f"{float(p):.4f}" for p in parts)

    path = f"{FIRMS_BASE}/{key}/{source}/{bbox}/{days}" + (f"/{date}" if date else "")
    ck = f"firms|{source}|{bbox}|{days}|{date}"

    def fetch():
        text = http_get(path).decode("utf-8")
        head = text.lstrip()[:200].lower()
        if "latitude" not in head:  # FIRMS responde 200 con texto plano si la key es inválida
            raise PermissionError("FIRMS no devolvió CSV: " + text.strip()[:120])
        return text

    def save(text):
        rows = len(storage.parse_firms_csv(text))
        new = storage.save_firms_csv(text, source)
        return rows, new, None  # 0 filas es normal cuando no hay incendios

    def producer():
        return _ingest("focos", source, f"{days} día(s)" + (f" desde {date}" if date else ""), fetch, save)

    return cached_fetch(ck, TTL["firms_replay"] if date else TTL["firms_live"], producer)


def open_meteo(query):
    ck = "om|" + query

    def producer():
        return _ingest("clima", _weather_variant(query), _weather_detail(query),
                       lambda: http_get(f"{OPEN_METEO_URL}?{query}").decode("utf-8"), _save_weather(query))

    return cached_fetch(ck, TTL["open_meteo"], producer)


def open_meteo_archive(query):
    """Clima histórico por hora (también sirve para ayer: verificado en vivo 28-09-2026)."""
    params = urllib.parse.parse_qs(query)
    for k in ("start_date", "end_date"):
        v = params.get(k, [""])[0]
        if not DATE_RE.match(v):
            raise ValueError(f"{k} debe ser YYYY-MM-DD")
    end = time.strptime(params["end_date"][0], "%Y-%m-%d")
    age_days = (time.time() - time.mktime(end)) / 86400
    ttl = TTL["om_archive_recent"] if age_days < 7 else TTL["om_archive_old"]
    ck = "oma|" + query

    def producer():
        return _ingest("clima", "historico", f"{params['start_date'][0]} → {params['end_date'][0]}",
                       lambda: http_get(f"{OPEN_METEO_ARCHIVE_URL}?{query}").decode("utf-8"), _save_weather(query))

    return cached_fetch(ck, ttl, producer)


# ---------------------------------------------------------------- rutas y tráfico
def _parse_coords(raw):
    """'lon,lat;lon,lat[;lon,lat…]' → [(lon, lat), …] validado (2 a 4 puntos)."""
    pts = []
    for part in raw.split(";"):
        xy = part.split(",")
        if len(xy) != 2:
            raise ValueError("coords debe ser lon,lat;lon,lat")
        lon, lat = to_float(xy[0]), to_float(xy[1])
        if lon is None or lat is None or not (-180 <= lon <= 180 and -90 <= lat <= 90):
            raise ValueError("coordenadas fuera de rango")
        pts.append((round(lon, 5), round(lat, 5)))
    if not 2 <= len(pts) <= 4:
        raise ValueError("se aceptan entre 2 y 4 puntos")
    return pts


def _route_osrm(pts):
    coords = ";".join(f"{lon},{lat}" for lon, lat in pts)
    url = f"{OSRM_URL}/{coords}?overview=full&geometries=geojson&alternatives=true&steps=false"
    raw = json.loads(http_get(url).decode("utf-8"))
    if raw.get("code") != "Ok":
        raise RuntimeError("OSRM: " + str(raw.get("code") or raw.get("message")))
    routes = [{
        "distance_m": r["distance"],
        "duration_s": r["duration"],
        "delay_s": None,                       # OSRM no conoce el tráfico
        "coords": r["geometry"]["coordinates"],
    } for r in raw["routes"]]
    return {"provider": "osrm", "traffic": False, "routes": routes}


def _route_tomtom(pts):
    key = secret("TOMTOM_KEY")
    if not key:
        raise PermissionError("Falta TOMTOM_KEY en .env")
    loc = ":".join(f"{lat},{lon}" for lon, lat in pts)
    qs = urllib.parse.urlencode({
        "key": key, "traffic": "true", "routeType": "fastest", "travelMode": "car",
        "maxAlternatives": 2, "routeRepresentation": "polyline", "computeTravelTimeFor": "all",
    })
    raw = json.loads(http_get(f"{TOMTOM_ROUTE_URL}/{loc}/json?{qs}").decode("utf-8"))
    routes = []
    for r in raw.get("routes", []):
        sm = r["summary"]
        coords = [[p["longitude"], p["latitude"]] for leg in r["legs"] for p in leg["points"]]
        routes.append({
            "distance_m": sm["lengthInMeters"],
            "duration_s": sm["travelTimeInSeconds"],
            "delay_s": sm.get("trafficDelayInSeconds", 0),
            "coords": coords,
        })
    if not routes:
        raise RuntimeError("TomTom no devolvió rutas")
    return {"provider": "tomtom", "traffic": True, "routes": routes}


def route(params):
    pts = _parse_coords(params.get("coords", [""])[0])
    provider = params.get("provider", ["osrm"])[0]
    if provider not in ("osrm", "tomtom"):
        raise ValueError("provider inválido")
    fn = _route_tomtom if provider == "tomtom" else _route_osrm
    ttl = TTL["route_tomtom"] if provider == "tomtom" else TTL["route_osrm"]
    ck = f"route|{provider}|" + ";".join(f"{a},{b}" for a, b in pts)
    return cached_fetch(ck, ttl, lambda: json.dumps(fn(pts)))


_tiles = {}  # (z,x,y) → (t, bytes); solo memoria


def traffic_tile(z, x, y):
    key = secret("TOMTOM_KEY")
    if not key:
        raise PermissionError("Falta TOMTOM_KEY en .env")
    if not (0 <= z <= 22 and 0 <= x < 2 ** z and 0 <= y < 2 ** z):
        raise ValueError("tile fuera de rango")
    hit = _tiles.get((z, x, y))
    if hit and time.time() - hit[0] < TTL["traffic_tile"]:
        return hit[1]
    body = http_get(f"{TOMTOM_FLOW_URL}/{z}/{x}/{y}.png?key={key}&thickness=8")
    if len(_tiles) > 800:
        _tiles.clear()
    _tiles[(z, x, y)] = (time.time(), body)
    return body


_vtiles = {}  # (z,x,y) → (t, bytes); solo memoria


def traffic_vtile(z, x, y):
    """Tile VECTORIAL de flujo (.pbf): trae el tipo de vía y el nivel de tráfico de cada tramo.
    Lo usa el mapa (para filtrar vías principales y pintar suave) y el recolector (para guardar)."""
    key = secret("TOMTOM_KEY")
    if not key:
        raise PermissionError("Falta TOMTOM_KEY en .env")
    if not (0 <= z <= 22 and 0 <= x < 2 ** z and 0 <= y < 2 ** z):
        raise ValueError("tile fuera de rango")
    hit = _vtiles.get((z, x, y))
    if hit and time.time() - hit[0] < TTL["traffic_tile"]:
        return hit[1]
    body = http_get(f"{TOMTOM_FLOW_VECTOR_URL}/{z}/{x}/{y}.pbf?key={key}")
    if len(_vtiles) > 800:
        _vtiles.clear()
    _vtiles[(z, x, y)] = (time.time(), body)
    return body


# ---------------------------------------------------------------- HTTP
class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), fmt % args))

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if "Cache-Control" not in (extra or {}):
            self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code, obj, extra=None):
        self._send(code, json.dumps(obj, ensure_ascii=False), extra=extra)

    def do_GET(self):  # noqa: N802
        url = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(url.path)
        if path.startswith("/api/"):
            return self._api(path, url.query)
        low = path.lower()  # Windows no distingue mayúsculas: /SERVER.PY no debe esquivar el bloqueo
        if any(low == p or low.startswith(p) for p in BLOCKED_PREFIXES):
            return self._send(404, "No encontrado", "text/plain; charset=utf-8")
        return super().do_GET()

    def do_POST(self):  # noqa: N802
        url = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(url.path)
        t0 = time.time()
        variant = None
        try:
            if path != "/api/store/fuelgrid":
                return self._json(404, {"error": "endpoint desconocido"})
            origin = self.headers.get("Origin")
            if origin and urllib.parse.urlparse(origin).netloc != self.headers.get("Host"):
                storage.log_event("combustible", "error", phase="solicitud", origin="browser", error_type="validacion",
                                  error_msg=f"origen no permitido: {origin}")
                return self._json(403, {"error": "origen no permitido"})
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length <= MAX_POST_BYTES:
                storage.log_event("combustible", "error", phase="solicitud", origin="browser", error_type="validacion",
                                  error_msg=f"cuerpo de {length} bytes (máximo {MAX_POST_BYTES})")
                return self._json(413, {"error": "cuerpo vacío o demasiado grande"})
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            variant = str(payload.get("key", ""))[:60]
            n = storage.save_fuel_grid(payload)
            storage.log_event("combustible", "ok", variant=variant, phase="guardado", origin="browser", rows=n,
                              ms=(time.time() - t0) * 1000, detail=f"{length / 1e6:.1f} MB recibidos")
            return self._json(200, {"ok": True, "cells": n})
        except (ValueError, KeyError, TypeError) as err:
            storage.log_event("combustible", "error", variant=variant, phase="guardado", origin="browser", err=err,
                              ms=(time.time() - t0) * 1000)
            return self._json(400, {"error": f"{type(err).__name__}: {err}"})
        except Exception as err:  # noqa: BLE001
            storage.log_event("combustible", "error", variant=variant, phase="guardado", origin="browser", err=err,
                              ms=(time.time() - t0) * 1000)
            return self._json(500, {"error": f"{type(err).__name__}: {err}"})

    def _api(self, path, query):
        try:
            if path == "/api/status":
                return self._json(200, {
                    "ok": True,
                    "dmc": bool(secret("DMC_USUARIO") and secret("DMC_TOKEN")),
                    "firms": bool(secret("FIRMS_MAP_KEY")),
                    "tomtom": bool(secret("TOMTOM_KEY")),
                    "archive": True,
                })
            if path == "/api/store/stats":
                return self._json(200, storage.stats())
            if path == "/api/store/log":
                q = urllib.parse.parse_qs(query)
                limit = max(1, min(500, int(q.get("limit", ["50"])[0])))
                return self._json(200, {"events": storage.recent_events(limit, q.get("errors", ["0"])[0] == "1")})
            if path == "/api/store/summary":
                return self._json(200, {"sources": storage.summary_by_source(), "hints": storage.ERROR_HINTS})
            if path == "/api/store/log.txt":
                q = urllib.parse.parse_qs(query)
                text = storage.format_log_text(200, q.get("errors", ["0"])[0] == "1")
                return self._send(200, text, "text/plain; charset=utf-8")
            if path == "/api/store/has":
                key = urllib.parse.parse_qs(query).get("key", [""])[0]
                if not re.match(r"^-?\d+\.\d{4}(,-?\d+\.\d{4}){3}$", key):
                    raise ValueError("key inválida")
                return self._json(200, {"exists": storage.has_fuel_area(key)})
            if path == "/api/dmc/estaciones":
                value, age, stale = dmc_catalog()
                return self._json(200, value, {"X-Cache-Age": str(int(age)), "X-Stale": str(stale).lower()})
            if path == "/api/dmc/recientes":
                value, age, stale = dmc_recientes()
                return self._json(200, value, {"X-Cache-Age": str(int(age)), "X-Stale": str(stale).lower()})
            if path == "/api/firms":
                value, age, stale = firms(urllib.parse.parse_qs(query))
                return self._send(200, value, "text/csv; charset=utf-8",
                                  {"X-Cache-Age": str(int(age)), "X-Stale": str(stale).lower()})
            if path == "/api/openmeteo":
                value, age, stale = open_meteo(query)
                return self._send(200, value, "application/json; charset=utf-8",
                                  {"X-Cache-Age": str(int(age)), "X-Stale": str(stale).lower()})
            if path == "/api/openmeteo-archive":
                value, age, stale = open_meteo_archive(query)
                return self._send(200, value, "application/json; charset=utf-8",
                                  {"X-Cache-Age": str(int(age)), "X-Stale": str(stale).lower()})
            if path == "/api/route":
                value, age, stale = route(urllib.parse.parse_qs(query))
                return self._send(200, value, "application/json; charset=utf-8",
                                  {"X-Cache-Age": str(int(age)), "X-Stale": str(stale).lower()})
            m = re.match(r"^/api/traffic-v/(\d+)/(\d+)/(\d+)\.pbf$", path)
            if m:
                body = traffic_vtile(*(int(g) for g in m.groups()))
                extra = {"Cache-Control": "public, max-age=300"}
                if body[:2] == b"\x1f\x8b":   # TomTom a veces entrega el tile ya comprimido
                    extra["Content-Encoding"] = "gzip"
                return self._send(200, body, "application/x-protobuf", extra)
            m = re.match(r"^/api/traffic/(\d+)/(\d+)/(\d+)\.png$", path)
            if m:
                body = traffic_tile(*(int(g) for g in m.groups()))
                return self._send(200, body, "image/png", {"Cache-Control": "public, max-age=300"})
            return self._json(404, {"error": "endpoint desconocido"})
        except PermissionError as err:
            return self._json(503, {"error": str(err), "kind": "credenciales"})
        except ValueError as err:
            return self._json(400, {"error": str(err)})
        except urllib.error.HTTPError as err:
            return self._json(502, {"error": f"upstream HTTP {err.code}", "kind": "upstream"})
        except Exception as err:  # noqa: BLE001
            return self._json(502, {"error": f"{type(err).__name__}: {err}", "kind": "upstream"})


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(secret("PORT") or 8080)
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"FireWatch mapa: http://localhost:{port}")
    print(f"  DMC token : {'OK' if secret('DMC_TOKEN') else 'FALTA (solo catálogo de estaciones)'}")
    print(f"  Archivo   : {storage.DB_PATH}")
    print(f"  TomTom key: {'OK' if secret('TOMTOM_KEY') else 'FALTA (sin tráfico en vivo; rutas por OSRM)'}")
    print(f"  FIRMS key : {'OK' if secret('FIRMS_MAP_KEY') else 'FALTA (capa de focos desactivada)'}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor detenido.")


if __name__ == "__main__":
    main()
