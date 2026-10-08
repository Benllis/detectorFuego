"""
FireWatch · descarga del pasado: focos históricos, incendios reconstruidos y clima de cada incendio.

Se usa desde recolector.py:

    python tools/recolector.py --historico                       # los 3 pasos, zona "centro", últimos 3 años
    python tools/recolector.py --historico --desde 2023-01-01 --zona valparaiso
    python tools/recolector.py --historico --solo focos          # solo un paso (focos | eventos | clima)
    python tools/recolector.py --eventos 15                      # los 15 incendios más grandes ya reconstruidos

Los tres pasos:
  1. focos   → NASA FIRMS (VIIRS) por tramos de 5 días → tabla fire_detections.
  2. eventos → agrupa detecciones cercanas en espacio y tiempo → fire_events / fire_event_detections.
  3. clima   → Open-Meteo (archivo) en el punto de cada incendio, desde 7 días antes hasta 1 día después
               → weather_obs (fuente om-hourly). 7 días antes sirven para saber cuánto llovió.

Se puede interrumpir con Ctrl+C y volver a correr: lo ya descargado (tabla backfill_progress) no se repite.
"""
import datetime as dt
import sys
import time

import server
import storage

# Zonas predefinidas: (oeste, sur, este, norte). "santiago" sale de js/config.js (área del mapa).
ZONES = {
    "centro": (-72.2, -34.6, -69.8, -32.2),       # Valparaíso + Región Metropolitana + norte de O'Higgins
    "valparaiso": (-72.0, -33.8, -70.9, -32.5),
}
CHUNK_DAYS = 5                                      # máximo que permite la API de FIRMS por consulta
SP_SOURCES = ["VIIRS_SNPP_SP", "VIIRS_NOAA20_SP"]  # procesamiento estándar: histórico definitivo (con meses de retraso)
NRT_SOURCES = ["VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT"]   # casi tiempo real: últimos meses
SP_AFTER_DAYS = 150     # más viejo que esto: solo SP
NRT_WITHIN_DAYS = 45    # más nuevo que esto: solo NRT; entre medio se piden ambos y se descartan duplicados
ARCHIVE_LAG_DAYS = 6    # el archivo de Open-Meteo llega con unos 5 días de retraso

WEATHER_VARS = [
    "temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,wind_gusts_10m,precipitation,"
    "vapour_pressure_deficit,shortwave_radiation,et0_fao_evapotranspiration",
    "temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,wind_gusts_10m,precipitation",
    "temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m",
]


def resolve_zone(name, area):
    """Devuelve (nombre, (oeste, sur, este, norte)). `name` puede ser una zona, 'santiago' o 'w,s,e,n'."""
    import math
    if name in ZONES:
        return name, ZONES[name]
    if name == "santiago":
        d_lat = area["radius_km"] / 111.32
        d_lon = area["radius_km"] / (111.32 * math.cos(math.radians(area["lat"])))
        return name, (area["lon"] - d_lon, area["lat"] - d_lat, area["lon"] + d_lon, area["lat"] + d_lat)
    try:
        w, s, e, n = (float(x) for x in name.split(","))
        if not (-180 <= w < e <= 180 and -90 <= s < n <= 90):
            raise ValueError
        return "custom", (w, s, e, n)
    except ValueError:
        raise SystemExit(f"--zona: usa una de {sorted(list(ZONES) + ['santiago'])} o oeste,sur,este,norte (ej. -72,-34,-70,-33)")


def plan_chunks(desde, hasta, days=CHUNK_DAYS):
    """Tramos consecutivos [(inicio, n_dias)] que cubren desde..hasta inclusive."""
    out, d = [], desde
    while d <= hasta:
        n = min(days, (hasta - d).days + 1)
        out.append((d, n))
        d += dt.timedelta(days=n)
    return out


def sources_for(chunk_start, n_days, today):
    """Qué productos FIRMS pedir según la antigüedad del tramo."""
    age = (today - (chunk_start + dt.timedelta(days=n_days - 1))).days
    if age > SP_AFTER_DAYS:
        return list(SP_SOURCES)
    if age <= NRT_WITHIN_DAYS:
        return [s for s in NRT_SOURCES if not (s.endswith("NOAA21_NRT") and chunk_start < dt.date(2024, 2, 1))]
    return list(SP_SOURCES) + [s for s in NRT_SOURCES if not s.endswith("NOAA21_NRT")]


def _count_rows(csv_text):
    return max(0, len([ln for ln in csv_text.splitlines() if ln.strip()]) - 1)


def backfill_fires(con, zone_name, bbox, desde, hasta, today=None, sleep=0.2, log=print, firms=None):
    """Descarga focos FIRMS por tramos. Devuelve {'chunks', 'done', 'skipped', 'rows', 'errors', 'aborted'}."""
    firms = firms or server.firms
    today = today or dt.date.today()
    bbox_s = ",".join(f"{v:.4f}" for v in bbox)
    scope = f"{zone_name}:{bbox_s}"
    chunks = plan_chunks(desde, hasta)
    res = {"chunks": 0, "done": 0, "skipped": 0, "rows": 0, "errors": 0, "aborted": False}
    consecutive = 0
    for idx, (start, n) in enumerate(chunks, 1):
        for source in sources_for(start, n, today):
            res["chunks"] += 1
            key = f"{source}|{start.isoformat()}|{n}"
            if storage.progress_done("fires", scope, key, con):
                res["skipped"] += 1
                continue
            text, err = None, None
            for attempt in (1, 2):
                try:
                    text, _, _ = firms({"source": [source], "bbox": [bbox_s], "days": [str(n)], "date": [start.isoformat()]})
                    err = None
                    break
                except Exception as e:  # noqa: BLE001
                    err = e
                    if attempt == 1 and any(w in str(e).lower() for w in ("transaction", "limit", "exceed")):
                        log("  FIRMS pide esperar (límite de consultas); pauso 60 s…")
                        time.sleep(60)
                        continue
                    break
            if err is not None:
                res["errors"] += 1
                consecutive += 1
                storage.progress_set("fires", scope, key, "error", None, f"{type(err).__name__}: {err}", con)
                log(f"  ERROR {source} {start}: {type(err).__name__}: {err}")
                if consecutive >= 3:
                    res["aborted"] = True
                    log("  Tres errores seguidos: me detengo. Revisa `--resumen` y vuelve a correr (retoma desde aquí).")
                    return res
                continue
            consecutive = 0
            rows = _count_rows(text)
            res["rows"] += rows
            res["done"] += 1
            storage.progress_set("fires", scope, key, "ok", rows, None, con)
            time.sleep(sleep)
        if idx % 10 == 0 or idx == len(chunks):
            log(f"  focos: tramo {idx}/{len(chunks)} ({start}) · {res['rows']} detecciones recibidas · {res['errors']} errores")
    return res


def backfill_weather(con, min_detections=3, today=None, sleep=0.3, log=print, archive=None):
    """Clima por hora en el punto de cada incendio (de 7 días antes a 1 día después). Devuelve un resumen."""
    archive = archive or server.open_meteo_archive
    today = today or dt.date.today()
    last_ok = today - dt.timedelta(days=ARCHIVE_LAG_DAYS)
    events = con.execute("SELECT event_id, start_utc, end_utc, lat, lon FROM fire_events WHERE n_detections >= ? ORDER BY start_utc",
                         (min_detections,)).fetchall()
    res = {"events": len(events), "done": 0, "skipped": 0, "too_recent": 0, "errors": 0, "aborted": False}
    var_idx, consecutive = 0, 0
    for i, ev in enumerate(events, 1):
        w0 = dt.date.fromisoformat(ev["start_utc"][:10]) - dt.timedelta(days=7)
        end_d = dt.date.fromisoformat(ev["end_utc"][:10])
        w1 = end_d + dt.timedelta(days=1)
        if w1 > last_ok:   # el archivo aún no cubre todo el incendio: se reintenta en otra corrida
            res["too_recent"] += 1
            continue
        key = f"{ev['lat']:.4f},{ev['lon']:.4f}|{w0}|{w1}"
        if storage.progress_done("weather", "evento", key, con):
            res["skipped"] += 1
            continue
        done, err = False, None
        while True:
            query = (f"latitude={ev['lat']:.4f}&longitude={ev['lon']:.4f}&hourly={WEATHER_VARS[var_idx]}"
                     f"&start_date={w0}&end_date={w1}&wind_speed_unit=kmh&timezone=UTC")
            try:
                archive(query)
                done = True
                break
            except Exception as e:  # noqa: BLE001
                err = e
                msg = str(e)
                if "429" not in msg and "400" in msg and var_idx + 1 < len(WEATHER_VARS):
                    var_idx += 1          # variable rechazada: se prueba un juego más básico (y se mantiene para los siguientes)
                    log(f"  clima: Open-Meteo rechazó alguna variable ({e}); uso un juego más básico")
                    continue
                break
        if done:
            consecutive = 0
            res["done"] += 1
            storage.progress_set("weather", "evento", key, "ok", None, f"incendio {ev['event_id']}", con)
            time.sleep(sleep)
        else:
            res["errors"] += 1
            consecutive += 1
            storage.progress_set("weather", "evento", key, "error", None, f"{type(err).__name__}: {err}", con)
            log(f"  ERROR clima incendio {ev['event_id']}: {type(err).__name__}: {err}")
            if consecutive >= 3 or (err is not None and "429" in str(err)):
                res["aborted"] = True
                log("  Me detengo (errores seguidos o límite de Open-Meteo). Vuelve a correr más tarde: retoma desde aquí.")
                return res
        if i % 25 == 0 or i == len(events):
            log(f"  clima: {i}/{len(events)} incendios · {res['done']} descargados · {res['skipped']} ya estaban · {res['errors']} errores")
    return res


def run(args, area, con):
    """Orquesta los pasos pedidos. `args`: desde, hasta, zona, solo, min_detecciones."""
    storage.QUIET = True       # el avance se muestra en un resumen; cada evento queda igual en el registro
    today = dt.date.today()
    hasta = dt.date.fromisoformat(args.hasta) if args.hasta else today - dt.timedelta(days=3)
    desde = dt.date.fromisoformat(args.desde) if args.desde else today - dt.timedelta(days=3 * 365)
    if desde > hasta:
        raise SystemExit("--desde debe ser anterior a --hasta")
    zone_name, bbox = resolve_zone(args.zona, area)
    steps = [args.solo] if args.solo else ["focos", "eventos", "clima"]
    print(f"Histórico · zona {zone_name} {tuple(round(v, 2) for v in bbox)} · {desde} → {hasta} · pasos: {', '.join(steps)}")
    if "focos" in steps:
        if not server.secret("FIRMS_MAP_KEY"):
            raise SystemExit("Falta FIRMS_MAP_KEY en .env (es gratis: https://firms.modaps.eosdis.nasa.gov/api/map_key/)")
        n = len(plan_chunks(desde, hasta))
        print(f"[1/3] Focos FIRMS: {n} tramos de {CHUNK_DAYS} días (cada uno con 2-3 satélites). Puede tardar varios minutos.")
        r = backfill_fires(con, zone_name, bbox, desde, hasta)
        print(f"      listo: {r['done']} consultas nuevas, {r['skipped']} ya estaban, {r['rows']} detecciones recibidas, {r['errors']} errores")
        if r["aborted"]:
            return
    if "eventos" in steps:
        r = storage.build_fire_events(con)
        print(f"[2/3] Incendios reconstruidos: {r['events']} a partir de {r['detections']} detecciones ({r['multi']} con 3 o más detecciones).")
    if "clima" in steps:
        ev = con.execute("SELECT COUNT(*) FROM fire_events WHERE n_detections >= ?", (args.min_detecciones,)).fetchone()[0]
        print(f"[3/3] Clima de {ev} incendios con ≥ {args.min_detecciones} detecciones (de 7 días antes a 1 día después).")
        r = backfill_weather(con, args.min_detecciones)
        print(f"      listo: {r['done']} descargados, {r['skipped']} ya estaban, {r['too_recent']} muy recientes para el archivo, {r['errors']} errores")
    s = storage.stats(con)
    print(f"Archivo: {s['fire_detections']} focos · {s['fire_events']} incendios · {s['weather_obs']} lecturas de clima · {s['size_mb']} MB")
    print("Revisa errores con: python tools/recolector.py --resumen")


def format_events(events):
    if not events:
        return "Todavía no hay incendios reconstruidos. Corre: python tools/recolector.py --historico"
    lines = [f"{'ID':>4}  {'INICIO (UTC)':<16} {'DÍAS':>4} {'DET.':>5} {'ÁREA km²':>9} {'FRP máx':>8} {'EXTENSIÓN':>10}  CENTRO"]
    for e in events:
        lines.append(f"{e['event_id']:>4}  {e['start_utc']:<16} {e['n_days']:>4} {e['n_detections']:>5} {e['area_km2_approx']:>9.2f} "
                     f"{(e['max_frp'] or 0):>8.1f} {e['extent_km']:>8.1f}km  {e['lat']:.3f}, {e['lon']:.3f}")
    lines.append("\nÁREA = suma aproximada de pixeles con detección (cota del área con fuego, NO el área quemada).")
    return "\n".join(lines)
