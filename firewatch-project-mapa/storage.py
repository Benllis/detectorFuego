"""
FireWatch · Mapa 3D — archivo histórico en SQLite (solo librería estándar).

Guarda lo que el proyecto necesita para, más adelante, estudiar y predecir la
propagación del fuego: clima por hora, focos de calor satelitales, mediciones
de estaciones DMC y la grilla de combustible/terreno por celda.

  clima     → weather_obs   (una fila por fuente, hora UTC y punto de la grilla)
  focos     → fire_detections (una fila por detección; sin duplicados)
  estación  → station_obs   (DMC: una fila por estación y hora UTC)
  terreno   → fuel_areas + fuel_cells (celdas de 250 m con vegetación, altura, pendiente)

Todas las horas están en UTC ('YYYY-MM-DDTHH:MM'). Las filas repetidas se
actualizan sin duplicarse, así que guardar lo mismo dos veces es inofensivo.

El archivo vive en data/firewatch.db (está en .gitignore por `*.db`; se puede
copiar tal cual al compañero de equipo). Si algo falla al escribir, el servidor
sigue funcionando: `safe()` solo avisa por consola.
"""
import calendar
import csv
import io
import json
import os
import sqlite3
import sys
import socket
import time
import urllib.error
import urllib.parse

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("FIREWATCH_DB") or os.path.join(ROOT, "data", "firewatch.db")
LOG_PATH = os.path.join(os.path.dirname(DB_PATH), "ingest.log")
ORIGIN = "server"          # el recolector lo cambia a "recolector"
QUIET = False              # los tests lo apagan para no llenar la consola

SCHEMA_VERSION = 4
SCHEMA = """
CREATE TABLE IF NOT EXISTS weather_obs (
  source TEXT NOT NULL,          -- 'om-current' (modelo, valor actual) | 'om-hourly' (horario/histórico)
  ts_utc TEXT NOT NULL,
  lat REAL NOT NULL, lon REAL NOT NULL,      -- punto pedido (4 decimales)
  temp_c REAL, rh_pct REAL, wind_kmh REAL, wind_dir_deg REAL, gust_kmh REAL,
  precip_mm REAL, vpd_kpa REAL, radiation_wm2 REAL, soil_moist REAL, et0_mm REAL,
  elevation_m REAL,
  fetched_at TEXT,
  PRIMARY KEY (source, ts_utc, lat, lon)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS fire_detections (
  lat REAL NOT NULL, lon REAL NOT NULL, acq_utc TEXT NOT NULL, satellite TEXT NOT NULL,
  instrument TEXT, confidence TEXT, frp REAL, brightness REAL, daynight TEXT,
  firms_source TEXT, first_seen_at TEXT,
  PRIMARY KEY (lat, lon, acq_utc, satellite)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS station_obs (
  station_id TEXT NOT NULL, ts_utc TEXT NOT NULL,
  name TEXT, lat REAL, lon REAL,
  temp_c REAL, rh_pct REAL, wind_kmh REAL, wind_dir_deg REAL,
  fetched_at TEXT,
  PRIMARY KEY (station_id, ts_utc)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS fuel_areas (
  area_key TEXT PRIMARY KEY,     -- 'oeste,sur,este,norte' (4 decimales)
  west REAL, south REAL, east REAL, north REAL,
  cell_m INTEGER, source TEXT, n_cells INTEGER, created_at TEXT
);

CREATE TABLE IF NOT EXISTS fuel_cells (
  area_key TEXT NOT NULL, r INTEGER NOT NULL, c INTEGER NOT NULL,   -- r: 0 = borde sur; c: 0 = borde oeste
  lat REAL NOT NULL, lon REAL NOT NULL,
  dominant TEXT, fuel REAL, fractions TEXT,         -- JSON {"matorral": 62, "urbano": 20} en %
  elevation_m REAL, slope_pct REAL, aspect_deg REAL,  -- se completan con `recolector.py --terreno`
  PRIMARY KEY (area_key, r, c)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS ingest_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts_utc TEXT NOT NULL,
  source TEXT NOT NULL,          -- clima | focos | dmc | combustible | terreno
  variant TEXT,                  -- clima: actual/horario/historico · focos: fuente FIRMS · combustible: clave del área
  origin TEXT,                   -- server (mapa abierto) | recolector | browser
  phase TEXT,                    -- solicitud | descarga | guardado
  status TEXT NOT NULL,          -- ok | warn | error | skipped
  rows_in INTEGER, rows_new INTEGER, ms INTEGER,
  error_type TEXT, error_msg TEXT, detail TEXT
);
CREATE INDEX IF NOT EXISTS ingest_log_ts ON ingest_log (ts_utc);

CREATE TABLE IF NOT EXISTS fire_events (        -- incendios reconstruidos: detecciones cercanas en espacio y tiempo
  event_id INTEGER PRIMARY KEY,                -- se renumera cada vez que se reconstruyen
  start_utc TEXT, end_utc TEXT, duration_h REAL, n_days INTEGER,
  n_detections INTEGER, n_pixels INTEGER, area_km2_approx REAL,
  lat REAL, lon REAL, lat_min REAL, lat_max REAL, lon_min REAL, lon_max REAL, extent_km REAL,
  max_frp REAL, sum_frp REAL, max_confidence TEXT, satellites TEXT,
  params TEXT, built_at TEXT
);

CREATE TABLE IF NOT EXISTS fire_event_detections (  -- a qué incendio pertenece cada detección
  lat REAL NOT NULL, lon REAL NOT NULL, acq_utc TEXT NOT NULL, satellite TEXT NOT NULL,
  event_id INTEGER NOT NULL,
  PRIMARY KEY (lat, lon, acq_utc, satellite)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS fire_event_detections_ev ON fire_event_detections (event_id);

CREATE TABLE IF NOT EXISTS backfill_progress (   -- para reanudar las descargas históricas sin repetir
  kind TEXT NOT NULL, scope TEXT NOT NULL, key TEXT NOT NULL,
  status TEXT NOT NULL, rows INTEGER, detail TEXT, updated_at TEXT,
  PRIMARY KEY (kind, scope, key)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS traffic_segments (   -- foto del tráfico: un tramo de vía en un instante
  ts_utc TEXT NOT NULL,
  tile_z INTEGER NOT NULL, tile_x INTEGER NOT NULL, tile_y INTEGER NOT NULL, seg_idx INTEGER NOT NULL,
  road_class TEXT, road_type TEXT,                 -- autopista | troncal | principal | secundaria | colectora | calle
  traffic_level REAL,                              -- relativo: 1 = fluido, 0 = detenido
  coverage TEXT, closure INTEGER,
  lat REAL, lon REAL, length_m REAL,               -- punto medio y largo del tramo (recortado al tile)
  PRIMARY KEY (ts_utc, tile_z, tile_x, tile_y, seg_idx)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS traffic_summary (    -- resumen de cada foto por tipo de vía (se conserva siempre)
  ts_utc TEXT NOT NULL, road_class TEXT NOT NULL,
  n_segments INTEGER, km REAL, mean_level REAL,   -- mean_level ponderado por largo
  km_slow REAL, km_jam REAL,                      -- km con nivel < 0,6 (lento) y < 0,3 (congestión)
  n_tiles INTEGER, zoom INTEGER,
  PRIMARY KEY (ts_utc, road_class)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS collector_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, started_at TEXT, ok INTEGER, detail TEXT
);
"""

_FIELD_MAP = {
    "temperature_2m": "temp_c",
    "relative_humidity_2m": "rh_pct",
    "wind_speed_10m": "wind_kmh",
    "wind_direction_10m": "wind_dir_deg",
    "wind_gusts_10m": "gust_kmh",
    "precipitation": "precip_mm",
    "vapour_pressure_deficit": "vpd_kpa",
    "shortwave_radiation": "radiation_wm2",
    "soil_moisture_0_to_1cm": "soil_moist",
    "et0_fao_evapotranspiration": "et0_mm",
}
_WEATHER_COLS = list(_FIELD_MAP.values()) + ["elevation_m"]
TABLES = ("weather_obs", "fire_detections", "station_obs", "fuel_areas", "fuel_cells", "collector_runs", "ingest_log", "fire_events", "fire_event_detections", "backfill_progress", "traffic_segments", "traffic_summary")


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def connect(path=None):
    path = path or DB_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    con = sqlite3.connect(path, timeout=15)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    return con


def safe(fn, *args, **kwargs):
    """Ejecuta una escritura sin dejar que un fallo de disco tumbe el servidor."""
    try:
        return fn(*args, **kwargs)
    except Exception as err:  # noqa: BLE001
        print(f"[storage] no se pudo guardar ({fn.__name__}): {type(err).__name__}: {err}", file=sys.stderr)
        return None


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # NaN → NULL


# ---------------------------------------------------------------- clima (Open-Meteo)
def parse_open_meteo(query, text):
    """Filas de weather_obs a partir de la consulta (latitude/longitude pedidas) y la respuesta JSON."""
    params = urllib.parse.parse_qs(query)
    if params.get("timezone", ["GMT"])[0].upper() not in ("UTC", "GMT"):
        raise ValueError("solo se archivan consultas con timezone=UTC")
    lats = [round(float(x), 4) for x in params["latitude"][0].split(",")]
    lons = [round(float(x), 4) for x in params["longitude"][0].split(",")]
    payload = json.loads(text)
    items = payload if isinstance(payload, list) else [payload]
    rows = []
    for i, item in enumerate(items):
        if i >= len(lats):
            break
        elev = _num(item.get("elevation"))
        if isinstance(item.get("current"), dict):
            cur = item["current"]
            rows.append(("om-current", str(cur.get("time")), lats[i], lons[i], cur, None, elev))
        if isinstance(item.get("hourly"), dict):
            h = item["hourly"]
            for k, t in enumerate(h.get("time", [])):
                rows.append(("om-hourly", str(t), lats[i], lons[i], h, k, elev))
    out = []
    for source, ts, lat, lon, block, k, elev in rows:
        vals = {}
        for field, col in _FIELD_MAP.items():
            if field in block:
                v = block[field] if k is None else (block[field][k] if k < len(block[field]) else None)
                vals[col] = _num(v)
        out.append((source, ts, lat, lon, vals, elev))
    return out


def save_open_meteo(query, text, con=None):
    rows = parse_open_meteo(query, text)
    own = con is None
    con = con or connect()
    try:
        fetched = now_iso()
        cols = ["source", "ts_utc", "lat", "lon"] + _WEATHER_COLS + ["fetched_at"]
        upd = ", ".join(f"{c}=COALESCE(excluded.{c}, {c})" for c in _WEATHER_COLS + ["fetched_at"])
        sql = (f"INSERT INTO weather_obs ({','.join(cols)}) VALUES ({','.join('?' * len(cols))}) "
               f"ON CONFLICT(source, ts_utc, lat, lon) DO UPDATE SET {upd}")
        data = [(s, ts, la, lo, *[vals.get(c) for c in _FIELD_MAP.values()], elev, fetched) for s, ts, la, lo, vals, elev in rows]
        with con:
            con.executemany(sql, data)
        return len(data)
    finally:
        if own:
            con.close()


# ---------------------------------------------------------------- focos (NASA FIRMS)
def parse_firms_csv(text):
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        try:
            date, hhmm = r["acq_date"], str(r["acq_time"]).zfill(4)
            lat, lon = round(float(r["latitude"]), 5), round(float(r["longitude"]), 5)
        except (KeyError, ValueError):
            continue
        out.append({
            "lat": lat, "lon": lon, "acq_utc": f"{date}T{hhmm[:2]}:{hhmm[2:]}",
            "satellite": r.get("satellite") or "?", "instrument": r.get("instrument"),
            "confidence": r.get("confidence"), "frp": _num(r.get("frp")),
            "brightness": _num(r.get("bright_ti4") or r.get("brightness")), "daynight": r.get("daynight"),
        })
    return out


def save_firms_csv(text, firms_source, con=None):
    rows = parse_firms_csv(text)
    own = con is None
    con = con or connect()
    try:
        seen = now_iso()
        with con:
            before = con.total_changes
            con.executemany(
                "INSERT OR IGNORE INTO fire_detections (lat,lon,acq_utc,satellite,instrument,confidence,frp,brightness,daynight,firms_source,first_seen_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [(r["lat"], r["lon"], r["acq_utc"], r["satellite"], r["instrument"], r["confidence"], r["frp"],
                  r["brightness"], r["daynight"], firms_source, seen) for r in rows])
            return con.total_changes - before  # solo las nuevas
    finally:
        if own:
            con.close()


# ---------------------------------------------------------------- estaciones DMC
def save_station_obs(stations, con=None):
    own = con is None
    con = con or connect()
    try:
        fetched = now_iso()
        data = []
        for s in stations:
            if not s.get("time"):
                continue
            ts = str(s["time"]).replace(" ", "T")[:16]
            data.append((s["id"], ts, s.get("name"), s.get("lat"), s.get("lon"), s.get("temp"), s.get("rh"),
                         s.get("windSpeedKmh"), s.get("windDir"), fetched))
        with con:
            con.executemany(
                "INSERT OR REPLACE INTO station_obs (station_id,ts_utc,name,lat,lon,temp_c,rh_pct,wind_kmh,wind_dir_deg,fetched_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)", data)
        return len(data)
    finally:
        if own:
            con.close()


# ---------------------------------------------------------------- grilla de combustible
def has_fuel_area(area_key, con=None):
    own = con is None
    con = con or connect()
    try:
        return con.execute("SELECT 1 FROM fuel_areas WHERE area_key=?", (area_key,)).fetchone() is not None
    finally:
        if own:
            con.close()


def save_fuel_grid(payload, con=None):
    """payload: {key, bounds:[w,s,e,n], cellM, source, cells:[[r,c,lat,lon,dominante,fuel,{clase:%}], …]}"""
    key = str(payload["key"])
    w, s, e, n = (float(x) for x in payload["bounds"])
    cells = payload["cells"]
    if not cells or len(cells) > 400000:
        raise ValueError("cantidad de celdas inválida")
    own = con is None
    con = con or connect()
    try:
        with con:
            con.execute("DELETE FROM fuel_cells WHERE area_key=?", (key,))
            con.executemany(
                "INSERT INTO fuel_cells (area_key,r,c,lat,lon,dominant,fuel,fractions) VALUES (?,?,?,?,?,?,?,?)",
                [(key, int(c[0]), int(c[1]), float(c[2]), float(c[3]), c[4], float(c[5]),
                  json.dumps(c[6], separators=(",", ":"))) for c in cells])
            con.execute(
                "INSERT OR REPLACE INTO fuel_areas (area_key,west,south,east,north,cell_m,source,n_cells,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (key, w, s, e, n, int(payload.get("cellM", 250)), str(payload.get("source", ""))[:120], len(cells), now_iso()))
        return len(cells)
    finally:
        if own:
            con.close()


def slope_aspect_from_grid(elev, r, c, step_m):
    """Pendiente (%) y exposición (° desde el norte, hacia donde mira la ladera) con diferencias centrales de las celdas vecinas."""
    def z(rr, cc):
        return elev.get((rr, cc))
    e, w, nn, ss = z(r, c + 1), z(r, c - 1), z(r + 1, c), z(r - 1, c)  # r crece hacia el norte
    if None in (e, w, nn, ss):
        return None, None
    import math
    dzdx = (e - w) / (2 * step_m)
    dzdy = (nn - ss) / (2 * step_m)
    rise = math.hypot(dzdx, dzdy)
    aspect = None if rise < 0.01 else (math.degrees(math.atan2(-dzdx, -dzdy)) + 360) % 360
    return rise * 100, aspect


# ---------------------------------------------------------------- registro de lo que se guarda
# Tipos de error y qué hacer en cada caso (se muestran en el registro y en el resumen).
ERROR_HINTS = {
    "red": "No hubo conexión o el servicio tardó demasiado. Revisa internet/VPN o reintenta; si se repite, el servicio puede estar caído.",
    "credenciales": "Falta o es inválida la key/token. Revisa el archivo .env (FIRMS_MAP_KEY, DMC_USUARIO/DMC_TOKEN) y reinicia server.py.",
    "limite_de_uso": "El servicio respondió 429 (demasiadas consultas). Espera o sube el intervalo del recolector (--cada).",
    "http_4xx": "El servicio rechazó la consulta (error del cliente). Revisa los parámetros o las variables pedidas.",
    "http_5xx": "Falla del servicio externo. Normalmente se resuelve solo; se sirve el último dato guardado si existe.",
    "formato": "La respuesta llegó pero no tiene la forma esperada (el servicio cambió o devolvió un mensaje de error). Revisa el detalle.",
    "datos_incompletos": "Llegó respuesta pero con muchos valores vacíos. Puede ser un problema temporal del proveedor.",
    "validacion": "Los datos enviados no pasaron las validaciones (tamaño, rango o formato).",
    "base_bloqueada": "La base de datos está en uso por otro proceso. Reintenta; evita abrir el .db con otro programa mientras se escribe.",
    "disco": "No se pudo escribir en disco o en la base de datos (espacio, permisos o archivo dañado).",
    "desconocido": "Error no clasificado; mira el mensaje.",
}


def classify_error(err):
    """Clasifica una excepción en un tipo estable de ERROR_HINTS."""
    if isinstance(err, urllib.error.HTTPError):
        if err.code in (401, 403):
            return "credenciales"
        if err.code == 429:
            return "limite_de_uso"
        return "http_5xx" if err.code >= 500 else "http_4xx"
    if isinstance(err, (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError)):
        return "red"
    if isinstance(err, PermissionError):
        return "credenciales"
    if isinstance(err, sqlite3.OperationalError) and "locked" in str(err).lower():
        return "base_bloqueada"
    if isinstance(err, (sqlite3.Error, OSError)):
        return "disco"
    if isinstance(err, (json.JSONDecodeError, KeyError, IndexError, TypeError, AttributeError)):
        return "formato"
    if isinstance(err, ValueError):
        return "validacion"
    return "desconocido"


def _local(ts_utc):
    try:
        t = calendar.timegm(time.strptime(ts_utc, "%Y-%m-%dT%H:%M:%SZ"))
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))
    except (ValueError, TypeError):
        return str(ts_utc)


def format_event(e):
    """Una línea legible de un evento (fila de ingest_log como dict)."""
    tag = {"ok": "OK   ", "warn": "AVISO", "error": "ERROR", "skipped": "OMITIDO"}.get(e["status"], e["status"])
    what = e["source"] + (f":{e['variant']}" if e.get("variant") else "")
    bits = []
    if e.get("rows_in") is not None:
        bits.append(f"{e['rows_in']} filas" + (f", {e['rows_new']} nuevas" if e.get("rows_new") is not None else ""))
    if e.get("ms") is not None:
        bits.append(f"{e['ms']} ms")
    if e.get("error_type"):
        bits.append(f"{e['error_type']}" + (f" [{e['phase']}]" if e.get("phase") else ""))
    line = f"{_local(e['ts_utc'])}  {tag:<7} {what:<34} {e.get('origin') or '':<9} " + " · ".join(bits)
    if e.get("error_msg"):
        line += f"\n{'':>21}↳ {e['error_msg']}"
    return line


def _rotate_text_log():
    try:
        if os.path.getsize(LOG_PATH) > 1_000_000:
            os.replace(LOG_PATH, LOG_PATH + ".1")
    except OSError:
        pass


def log_event(source, status, variant=None, phase=None, rows=None, new=None, ms=None, err=None,
              error_type=None, error_msg=None, detail=None, origin=None):
    """
    Anota un evento en la tabla ingest_log y en data/ingest.log. NUNCA lanza: si la base
    falla, al menos queda la línea de texto y el aviso en consola.
    """
    if err is not None:
        error_type = error_type or classify_error(err)
        error_msg = error_msg or f"{type(err).__name__}: {err}"
    ev = {"ts_utc": now_iso(), "source": source, "variant": variant, "origin": origin or ORIGIN, "phase": phase, "status": status,
          "rows_in": rows, "rows_new": new, "ms": None if ms is None else int(ms), "error_type": error_type,
          "error_msg": (error_msg or "")[:400] or None, "detail": (detail or "")[:300] or None}
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        _rotate_text_log()
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(format_event(ev) + "\n")
    except OSError:
        pass
    if not QUIET:
        print("[archivo] " + format_event(ev).replace("\n", " "), file=sys.stderr if status == "error" else sys.stdout)
    try:
        con = connect()
        try:
            with con:
                cols = list(ev)
                con.execute(f"INSERT INTO ingest_log ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [ev[c] for c in cols])
                n = con.execute("SELECT COUNT(*) FROM ingest_log").fetchone()[0]
                if n > 40000:  # mantiene solo los últimos 20.000 eventos
                    con.execute("DELETE FROM ingest_log WHERE id <= (SELECT MAX(id) - 20000 FROM ingest_log)")
        finally:
            con.close()
    except Exception as db_err:  # noqa: BLE001
        print(f"[archivo] no se pudo anotar el evento en la base: {db_err}", file=sys.stderr)
    return ev


def recent_events(limit=30, only_errors=False, con=None):
    own = con is None
    con = con or connect()
    try:
        where = "WHERE status IN ('error','warn','skipped')" if only_errors else ""
        rows = con.execute(f"SELECT * FROM ingest_log {where} ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
        return [dict(r) for r in rows][::-1]
    finally:
        if own:
            con.close()


def summary_by_source(con=None):
    """Una fila por (fuente, variante): cuántas veces salió bien o mal, último éxito y último error."""
    own = con is None
    con = con or connect()
    try:
        out = []
        for r in con.execute("SELECT source, variant, COUNT(*) n, SUM(status='ok') ok, SUM(status='warn') warn, SUM(status='error') err, "
                             "SUM(status='skipped') skipped, SUM(COALESCE(rows_in,0)) rows, MAX(CASE WHEN status='ok' THEN ts_utc END) last_ok "
                             "FROM ingest_log GROUP BY source, variant ORDER BY source, variant").fetchall():
            d = dict(r)
            last = con.execute("SELECT ts_utc, error_type, error_msg FROM ingest_log WHERE source=? AND COALESCE(variant,'')=COALESCE(?,'') "
                               "AND status IN ('error','warn','skipped') ORDER BY id DESC LIMIT 1", (d["source"], d["variant"])).fetchone()
            d["last_problem"] = dict(last) if last else None
            out.append(d)
        return out
    finally:
        if own:
            con.close()


def format_summary(con=None):
    rows = summary_by_source(con)
    if not rows:
        return "Todavía no hay eventos registrados."
    lines = [f"{'FUENTE':<34} {'OK':>5} {'AVISO':>6} {'ERROR':>6} {'OMIT.':>6} {'FILAS':>9}  ÚLTIMO PROBLEMA"]
    for d in rows:
        what = d["source"] + (f":{d['variant']}" if d["variant"] else "")
        lp = d["last_problem"]
        prob = f"{_local(lp['ts_utc'])} · {lp['error_type']}" if lp else "—"
        lines.append(f"{what:<34} {d['ok'] or 0:>5} {d['warn'] or 0:>6} {d['err'] or 0:>6} {d['skipped'] or 0:>6} {d['rows'] or 0:>9}  {prob}")
    kinds = {d["last_problem"]["error_type"] for d in rows if d["last_problem"] and d["last_problem"]["error_type"]}
    if kinds:
        lines.append("\nQué hacer según el tipo de error:")
        lines += [f"  · {k}: {ERROR_HINTS.get(k, '')}" for k in sorted(kinds)]
    return "\n".join(lines)


def format_log_text(limit=200, only_errors=False, con=None):
    return (format_summary(con) + "\n\n" + f"Últimos {limit} eventos" + (" con problemas" if only_errors else "") + " (hora local):\n" +
            "\n".join(format_event(e) for e in recent_events(limit, only_errors, con)))


# ---------------------------------------------------------------- incendios reconstruidos (fire_events)
_CONF_RANK = {"l": 0, "n": 1, "h": 2}


def _is_low_confidence(c):
    if c is None or c == "":
        return False
    c = str(c).strip().lower()
    if c in _CONF_RANK:
        return c == "l"
    try:
        return float(c) < 30        # MODIS: porcentaje
    except ValueError:
        return False


def _epoch(acq_utc):
    return calendar.timegm(time.strptime(acq_utc, "%Y-%m-%dT%H:%M"))


def cluster_detections(dets, radius_km=2.0, gap_h=48.0):
    """
    Agrupa detecciones en incendios: dos detecciones quedan unidas si están a ≤ radius_km y a ≤ gap_h horas;
    un incendio es una cadena de uniones (enlace simple). dets: lista de dicts con lat, lon, t (epoch s).
    Devuelve una lista de listas de índices.
    """
    import math
    n = len(dets)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    kx0 = 111.32 * math.cos(math.radians(sum(d["lat"] for d in dets) / n)) if n else 111.32
    cells = {}
    for i, d in enumerate(dets):
        cells.setdefault((math.floor(d["lat"] * 111.32 / radius_km), math.floor(d["lon"] * kx0 / radius_km)), []).append(i)
    gap = gap_h * 3600
    for (cy, cx), members in cells.items():
        near = [j for dy in (-1, 0, 1) for dx in (-1, 0, 1) for j in cells.get((cy + dy, cx + dx), ())]
        for i in members:
            di = dets[i]
            for j in near:
                if j <= i:
                    continue
                dj = dets[j]
                if abs(di["t"] - dj["t"]) <= gap and math.hypot((di["lat"] - dj["lat"]) * 111.32, (di["lon"] - dj["lon"]) * kx0) <= radius_km:
                    ri, rj = find(i), find(j)
                    if ri != rj:
                        parent[rj] = ri
    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def build_fire_events(con=None, radius_km=2.0, gap_h=48.0, include_low=False):
    """
    Reconstruye fire_events y fire_event_detections desde fire_detections (se borra y se rehace entera,
    así que se puede repetir cada vez que llegan detecciones nuevas). Por defecto ignora las de confianza baja.
    Devuelve {'detections', 'events', 'multi'} (multi = incendios con ≥ 3 detecciones).
    """
    import math
    own = con is None
    con = con or connect()
    try:
        rows = con.execute("SELECT lat, lon, acq_utc, satellite, confidence, frp FROM fire_detections ORDER BY acq_utc").fetchall()
        dets = []
        for r in rows:
            if not include_low and _is_low_confidence(r["confidence"]):
                continue
            try:
                dets.append({"lat": r["lat"], "lon": r["lon"], "t": _epoch(r["acq_utc"]), "acq": r["acq_utc"], "sat": r["satellite"],
                             "conf": str(r["confidence"] or "").lower(), "frp": r["frp"]})
            except ValueError:
                continue
        groups = cluster_detections(dets, radius_km, gap_h) if dets else []
        groups.sort(key=lambda g: min(dets[i]["t"] for i in g))
        params = json.dumps({"radius_km": radius_km, "gap_h": gap_h, "include_low": include_low})
        built = now_iso()
        ev_rows, mem_rows = [], []
        for eid, g in enumerate(groups, 1):
            ds = [dets[i] for i in g]
            lats, lons = [d["lat"] for d in ds], [d["lon"] for d in ds]
            lat0, lon0 = sum(lats) / len(ds), sum(lons) / len(ds)
            kx = 111.32 * math.cos(math.radians(lat0))
            t0, t1 = min(d["t"] for d in ds), max(d["t"] for d in ds)
            cells = {(math.floor(d["lat"] * 111.32 / 0.375), math.floor(d["lon"] * kx / 0.375)) for d in ds}
            frps = [d["frp"] for d in ds if d["frp"] is not None]
            conf = max((d["conf"] for d in ds if d["conf"] in _CONF_RANK), key=lambda c: _CONF_RANK[c], default=None)
            ev_rows.append((eid, time.strftime("%Y-%m-%dT%H:%M", time.gmtime(t0)), time.strftime("%Y-%m-%dT%H:%M", time.gmtime(t1)),
                            round((t1 - t0) / 3600, 2), len({d["acq"][:10] for d in ds}), len(ds), len(cells), round(len(cells) * 0.375 ** 2, 3),
                            lat0, lon0, min(lats), max(lats), min(lons), max(lons),
                            round(math.hypot((max(lats) - min(lats)) * 111.32, (max(lons) - min(lons)) * kx), 2),
                            max(frps) if frps else None, round(sum(frps), 2) if frps else None, conf,
                            ",".join(sorted({d["sat"] for d in ds})), params, built))
            mem_rows += [(d["lat"], d["lon"], d["acq"], d["sat"], eid) for d in ds]
        with con:
            con.execute("DELETE FROM fire_event_detections")
            con.execute("DELETE FROM fire_events")
            con.executemany("INSERT INTO fire_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ev_rows)
            con.executemany("INSERT OR REPLACE INTO fire_event_detections (lat,lon,acq_utc,satellite,event_id) VALUES (?,?,?,?,?)", mem_rows)
        return {"detections": len(dets), "events": len(ev_rows), "multi": sum(1 for r in ev_rows if r[5] >= 3)}
    finally:
        if own:
            con.close()


def top_events(limit=15, con=None):
    own = con is None
    con = con or connect()
    try:
        return [dict(r) for r in con.execute("SELECT * FROM fire_events ORDER BY n_detections DESC, sum_frp DESC LIMIT ?", (int(limit),))]
    finally:
        if own:
            con.close()


# ---------------------------------------------------------------- avance de descargas históricas
def progress_done(kind, scope, key, con):
    r = con.execute("SELECT status FROM backfill_progress WHERE kind=? AND scope=? AND key=?", (kind, scope, key)).fetchone()
    return bool(r and r[0] == "ok")


def progress_set(kind, scope, key, status, rows, detail, con):
    with con:
        con.execute("INSERT OR REPLACE INTO backfill_progress (kind,scope,key,status,rows,detail,updated_at) VALUES (?,?,?,?,?,?,?)",
                    (kind, scope, key, status, rows, (detail or "")[:300], now_iso()))


# ---------------------------------------------------------------- consultas
def save_traffic(con, ts_utc, summary, segments, keep_days=30):
    """Guarda una foto del tráfico. `summary`: lista de dicts por clase; `segments`: lista de dicts.
    Los tramos se borran pasados `keep_days` días (el resumen se conserva siempre)."""
    with con:
        con.executemany(
            "INSERT OR REPLACE INTO traffic_summary (ts_utc, road_class, n_segments, km, mean_level, km_slow, km_jam, n_tiles, zoom) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            [(ts_utc, r["road_class"], r["n_segments"], r["km"], r["mean_level"], r["km_slow"], r["km_jam"], r["n_tiles"], r["zoom"]) for r in summary])
        con.executemany(
            "INSERT OR REPLACE INTO traffic_segments (ts_utc, tile_z, tile_x, tile_y, seg_idx, road_class, road_type, traffic_level, "
            "coverage, closure, lat, lon, length_m) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(ts_utc, g["z"], g["x"], g["y"], g["idx"], g["road_class"], g["road_type"], g["level"], g["coverage"], g["closure"],
              g["lat"], g["lon"], g["length_m"]) for g in segments])
        if keep_days:
            cut = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - keep_days * 86400))
            con.execute("DELETE FROM traffic_segments WHERE ts_utc < ?", (cut,))
    return len(segments)


def stats(con=None):
    own = con is None
    con = con or connect()
    try:
        out = {"path": DB_PATH, "size_mb": round(os.path.getsize(DB_PATH) / 1e6, 2) if os.path.exists(DB_PATH) else 0}
        for t in TABLES:
            out[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        rng = con.execute("SELECT MIN(ts_utc), MAX(ts_utc) FROM weather_obs").fetchone()
        out["weather_range"] = [rng[0], rng[1]]
        rng = con.execute("SELECT MIN(acq_utc), MAX(acq_utc) FROM fire_detections").fetchone()
        out["fires_range"] = [rng[0], rng[1]]
        since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 86400))
        out["errors_24h"] = con.execute("SELECT COUNT(*) FROM ingest_log WHERE status='error' AND ts_utc>=?", (since,)).fetchone()[0]
        out["warnings_24h"] = con.execute("SELECT COUNT(*) FROM ingest_log WHERE status IN ('warn','skipped') AND ts_utc>=?", (since,)).fetchone()[0]
        last = con.execute("SELECT ts_utc, source, variant, error_type FROM ingest_log WHERE status='error' ORDER BY id DESC LIMIT 1").fetchone()
        out["last_error"] = dict(last) if last else None
        return out
    finally:
        if own:
            con.close()


def export_csv(table, path, con=None):
    if table not in TABLES:
        raise ValueError("tabla desconocida: " + table)
    own = con is None
    con = con or connect()
    try:
        cur = con.execute(f"SELECT * FROM {table}")
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow([d[0] for d in cur.description])
            n = 0
            for row in cur:
                w.writerow(list(row))
                n += 1
        return n
    finally:
        if own:
            con.close()
