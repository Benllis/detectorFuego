#!/usr/bin/env python3
"""
FireWatch · recolector de datos históricos (corre SIN el mapa abierto).

Guarda en data/firewatch.db (SQLite) el clima, los focos de calor y las
mediciones de estaciones del área de Santiago, para estudiar y predecir la
propagación del fuego. Reutiliza el código de server.py (mismas keys de .env,
misma caché), así que no gasta más cuota que el mapa.

    python tools/recolector.py --una-vez          # una pasada y termina
    python tools/recolector.py --cada 60          # cada 60 min hasta Ctrl+C
    python tools/recolector.py --terreno          # altura y pendiente de cada celda de combustible
    python tools/recolector.py --stats            # resumen de lo guardado
    python tools/recolector.py --historico        # baja el PASADO: focos, incendios reconstruidos y su clima (ver tools/historico.py)
    python tools/recolector.py --eventos 15       # los 15 incendios reconstruidos más grandes
    python tools/recolector.py --resumen          # por fuente: cuántas veces OK / error y qué tipo de error
    python tools/recolector.py --log 50           # últimos 50 eventos (con --solo-errores: solo problemas)
    python tools/recolector.py --exportar weather_obs datos.csv

Para dejarlo corriendo solo: Programador de tareas de Windows → ejecutar
`python tools/recolector.py --una-vez` cada hora (ver METODOLOGIA.md §5f).
"""
import argparse
import json
import math
import os
import re
import sys
import time
import urllib.parse

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
import server    # noqa: E402  (reutiliza .env, caché y llamadas)
import storage   # noqa: E402

storage.ORIGIN = "recolector"

OPEN_METEO_ELEVATION = "https://api.open-meteo.com/v1/elevation"
FIRMS_SOURCES_NRT = ["VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT", "VIIRS_SNPP_NRT"]

# De lo más completo a lo básico: si la API rechaza una variable, se prueba el siguiente juego.
VAR_SETS = [
    "temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,wind_gusts_10m,precipitation,"
    "vapour_pressure_deficit,shortwave_radiation,soil_moisture_0_to_1cm,et0_fao_evapotranspiration",
    "temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,wind_gusts_10m,precipitation",
    "temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m",
]


def area_from_config():
    """Centro, radio y tamaño de la grilla: los mismos que usa el mapa (js/config.js)."""
    src = open(os.path.join(ROOT, "js", "config.js"), encoding="utf-8").read()
    city = re.search(r"city:\s*\{\s*center:\s*\{\s*lat:\s*(-?[\d.]+),\s*lon:\s*(-?[\d.]+)\s*\},\s*radiusKm:\s*([\d.]+)", src)
    size = re.search(r"gridSize:\s*(\d+)", src)
    if not city or not size:
        raise RuntimeError("No pude leer js/config.js (city.center, city.radiusKm, gridSize)")
    return {"lat": float(city.group(1)), "lon": float(city.group(2)), "radius_km": float(city.group(3)), "size": int(size.group(1))}


def grid_points(a):
    """Mismos puntos que WeatherGrid._buildPoints (js/weather-grid.js)."""
    lat_km, lon_km = 111.32, 111.32 * math.cos(math.radians(a["lat"]))
    step = a["radius_km"] * 2 / (a["size"] - 1)
    pts = []
    for row in range(a["size"]):
        for col in range(a["size"]):
            pts.append((a["lat"] + (-a["radius_km"] + row * step) / lat_km, a["lon"] + (-a["radius_km"] + col * step) / lon_km))
    return pts


def coords_query(pts):
    return "latitude=" + ",".join(f"{la:.4f}" for la, _ in pts) + "&longitude=" + ",".join(f"{lo:.4f}" for _, lo in pts)


def collect_weather(a):
    """Clima actual + horas recientes (2 días atrás y hoy) de los 64 puntos. Devuelve filas procesadas."""
    pts = grid_points(a)
    tail = "&wind_speed_unit=kmh&timezone=UTC"
    used, total = None, 0
    for variables in VAR_SETS:
        total = 0
        try:
            for block in (f"current={variables}", f"hourly={variables}&past_days=2&forecast_days=1"):
                text, _, _ = server.open_meteo(f"{coords_query(pts)}&{block}{tail}")
                total += len(storage.parse_open_meteo(f"{coords_query(pts)}&{block}{tail}", text))
            used = variables
            break
        except Exception as err:  # noqa: BLE001
            print(f"  clima: el juego de variables falló ({err}); pruebo uno más básico", file=sys.stderr)
    if used is None:
        raise RuntimeError("Open-Meteo no respondió con ningún juego de variables")
    return total, used.count(",") + 1


def collect_firms(a):
    if not server.secret("FIRMS_MAP_KEY"):
        return None
    lat_d = a["radius_km"] / 111.32
    lon_d = a["radius_km"] / (111.32 * math.cos(math.radians(a["lat"])))
    bbox = f"{a['lon'] - lon_d:.4f},{a['lat'] - lat_d:.4f},{a['lon'] + lon_d:.4f},{a['lat'] + lat_d:.4f}"
    n = 0
    for source in FIRMS_SOURCES_NRT:
        text, _, _ = server.firms({"source": [source], "bbox": [bbox], "days": ["2"]})
        n += max(0, text.count("\n") - 1)
    return n


def collect_dmc():
    if not (server.secret("DMC_USUARIO") and server.secret("DMC_TOKEN")):
        return None
    stations, _, _ = server.dmc_recientes()
    return len(stations)


def run_once():
    a = area_from_config()
    con = storage.connect()
    last_id = con.execute("SELECT COALESCE(MAX(id), 0) FROM ingest_log").fetchone()[0]
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] recolectando · centro {a['lat']}, {a['lon']} · radio {a['radius_km']} km · {a['size']}x{a['size']} puntos")
    for kind, fn in (("clima", lambda: collect_weather(a)), ("focos", lambda: collect_firms(a)), ("dmc", collect_dmc)):
        t0 = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        try:
            out = fn()
            if out is None:
                print(f"  {kind}: omitido (falta la key en .env)")
                storage.log_event(kind, "skipped", phase="solicitud", error_type="credenciales", error_msg="falta la key en .env")
                detail, ok = "omitido: falta la key", 1
            else:
                detail, ok = f"{out}", 1
                print(f"  {kind}: OK ({out})")
        except Exception as err:  # noqa: BLE001
            detail, ok = f"{type(err).__name__}: {err}", 0
            print(f"  {kind}: FALLÓ — {detail}", file=sys.stderr)
        with con:
            con.execute("INSERT INTO collector_runs (kind, started_at, ok, detail) VALUES (?,?,?,?)", (kind, t0, ok, detail))
    s = storage.stats(con)
    print(f"  archivo: {s['weather_obs']} lecturas de clima · {s['fire_detections']} focos · {s['station_obs']} de estaciones · {s['size_mb']} MB")
    mine = [dict(r) for r in con.execute("SELECT * FROM ingest_log WHERE id > ? ORDER BY id", (last_id,))]
    bad = [e for e in mine if e["status"] != "ok"]
    print(f"  esta pasada: {len(mine) - len(bad)} guardados bien, {len(bad)} con problemas (detalle en data/ingest.log o con --log)")
    kinds = sorted({e["error_type"] for e in bad if e["error_type"]})
    for k in kinds:
        print(f"    · {k}: {storage.ERROR_HINTS.get(k, '')}")
    con.close()


def fetch_elevations(batch):
    qs = urllib.parse.urlencode({"latitude": ",".join(f"{la:.5f}" for la, _ in batch), "longitude": ",".join(f"{lo:.5f}" for _, lo in batch)})
    data = json.loads(server.http_get(f"{OPEN_METEO_ELEVATION}?{qs}").decode("utf-8"))
    elev = data.get("elevation")
    if not isinstance(elev, list) or len(elev) != len(batch):
        raise RuntimeError("respuesta de elevación inesperada")
    return elev


def run_terrain():
    """Altura de cada celda de combustible (API de elevación, 100 por llamada) y pendiente/exposición por vecinos."""
    con = storage.connect()
    areas = [r["area_key"] for r in con.execute("SELECT area_key FROM fuel_areas")]
    if not areas:
        print("No hay grillas de combustible guardadas todavía: abre el mapa con `python server.py` (se guarda sola).")
        return
    for key in areas:
        cell_m = con.execute("SELECT cell_m FROM fuel_areas WHERE area_key=?", (key,)).fetchone()[0]
        todo = con.execute("SELECT r, c, lat, lon FROM fuel_cells WHERE area_key=? AND elevation_m IS NULL", (key,)).fetchall()
        print(f"área {key}: {len(todo)} celdas sin altura")
        done, t_area = 0, time.time()
        for i in range(0, len(todo), 100):
            batch = todo[i:i + 100]
            t0 = time.time()
            try:
                elev = fetch_elevations([(b["lat"], b["lon"]) for b in batch])
            except Exception as err:  # noqa: BLE001
                storage.log_event("terreno", "error", variant=key, phase="descarga", err=err, ms=(time.time() - t0) * 1000,
                                  detail=f"lote {i // 100 + 1} de {-(-len(todo) // 100)}; se guardó lo anterior, reanuda con --terreno")
                print(f"  falló el lote {i // 100 + 1}: {err}. Vuelve a correr --terreno para continuar.", file=sys.stderr)
                break
            done += len(batch)
            with con:
                con.executemany("UPDATE fuel_cells SET elevation_m=? WHERE area_key=? AND r=? AND c=?",
                                [(e, key, b["r"], b["c"]) for b, e in zip(batch, elev)])
            if (i // 100) % 50 == 49:
                print(f"  {i + len(batch)}/{len(todo)}")
            time.sleep(0.25)  # cuidar el límite gratuito (600 llamadas/min)
        grid = {(r["r"], r["c"]): r["elevation_m"] for r in con.execute("SELECT r, c, elevation_m FROM fuel_cells WHERE area_key=?", (key,))}
        upd = []
        for (r, c), z in grid.items():
            if z is None:
                continue
            slope, aspect = storage.slope_aspect_from_grid(grid, r, c, cell_m)
            if slope is not None:
                upd.append((slope, aspect, key, r, c))
        with con:
            con.executemany("UPDATE fuel_cells SET slope_pct=?, aspect_deg=? WHERE area_key=? AND r=? AND c=?", upd)
        print(f"  pendiente y exposición calculadas para {len(upd)} celdas (las del borde quedan sin pendiente)")
        if todo and done == len(todo):
            storage.log_event("terreno", "ok", variant=key, phase="guardado", rows=done, ms=(time.time() - t_area) * 1000,
                              detail=f"altura de {done} celdas; pendiente calculada en {len(upd)}")
        elif not todo:
            storage.log_event("terreno", "skipped", variant=key, phase="solicitud", error_type=None, detail="todas las celdas ya tenían altura")
    con.close()


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--una-vez", action="store_true", help="una pasada de recolección")
    g.add_argument("--cada", type=int, metavar="MIN", help="repetir cada MIN minutos (mínimo 30)")
    g.add_argument("--terreno", action="store_true", help="completar altura y pendiente de las celdas")
    g.add_argument("--stats", action="store_true", help="resumen de lo guardado")
    g.add_argument("--exportar", nargs=2, metavar=("TABLA", "ARCHIVO.csv"), help="exportar una tabla a CSV")
    g.add_argument("--historico", action="store_true", help="descargar el pasado (focos FIRMS, incendios y su clima)")
    g.add_argument("--eventos", nargs="?", const=15, type=int, metavar="N", help="listar los N incendios reconstruidos más grandes")
    g.add_argument("--trafico", action="store_true", help="guardar una foto del tráfico (TomTom, tiles vectoriales)")
    g.add_argument("--trafico-cada", type=int, metavar="MIN", help="una foto del tráfico cada MIN minutos (mínimo 10)")
    g.add_argument("--trafico-resumen", nargs="?", const=1, type=int, metavar="N", help="mostrar el resumen de las últimas N fotos de tráfico")
    g.add_argument("--resumen", action="store_true", help="por fuente: veces OK / aviso / error y último problema")
    g.add_argument("--log", nargs="?", const=30, type=int, metavar="N", help="últimos N eventos (por defecto 30)")
    p.add_argument("--zona", default="centro", help="con --historico: centro (defecto), valparaiso, santiago u oeste,sur,este,norte")
    p.add_argument("--desde", help="con --historico: YYYY-MM-DD (defecto: hace 3 años)")
    p.add_argument("--hasta", help="con --historico: YYYY-MM-DD (defecto: hace 3 días)")
    p.add_argument("--solo", choices=["focos", "eventos", "clima"], help="con --historico: ejecutar un solo paso")
    p.add_argument("--min-detecciones", type=int, default=3, help="con --historico: bajar clima solo de incendios con al menos N detecciones (defecto 3)")
    p.add_argument("--zoom", type=int, default=11, help="con --trafico: zoom de los tiles (defecto 11; más alto = más tiles = más cuota)")
    p.add_argument("--detalle", choices=["principales", "secundarias", "todas"], default="principales", help="con --trafico: qué vías guardar tramo a tramo (el resumen incluye todas)")
    p.add_argument("--conservar-dias", type=int, default=30, help="con --trafico: borrar los tramos con más de N días (el resumen se conserva; defecto 30)")
    p.add_argument("--solo-errores", action="store_true", help="con --log: mostrar solo avisos, errores y omitidos")
    args = p.parse_args()

    if args.una_vez:
        run_once()
    elif args.cada:
        if args.cada < 30:
            p.error("--cada: mínimo 30 minutos (Open-Meteo cambia cada hora; más seguido solo gasta cuota)")
        print(f"Recolectando cada {args.cada} min. Ctrl+C para detener.")
        try:
            while True:
                run_once()
                time.sleep(args.cada * 60)
        except KeyboardInterrupt:
            print("\nRecolector detenido.")
    elif args.trafico or args.trafico_cada:
        import trafico
        if args.trafico_cada and args.trafico_cada < 10:
            p.error("--trafico-cada: mínimo 10 minutos (los tiles se cachean 5 min y cada foto gasta cuota)")
        con = storage.connect()

        def once():
            try:
                r = trafico.snapshot(con, area_from_config(), server.traffic_vtile, args.zoom, args.detalle, args.conservar_dias)
                print(f"  tráfico guardado: {r['segments']} tramos ({r['stored']} guardados), {r['tiles']} tiles, {r['bad_tiles']} con problemas")
            except Exception as err:  # noqa: BLE001
                print(f"  tráfico FALLÓ — {type(err).__name__}: {err}", file=sys.stderr)
        try:
            once()
            while args.trafico_cada:
                time.sleep(args.trafico_cada * 60)
                once()
            print(trafico.format_summary(con))
        except KeyboardInterrupt:
            print("\nDetenido. Lo guardado queda en el archivo.")
        finally:
            con.close()
    elif args.trafico_resumen is not None:
        import trafico
        con = storage.connect()
        print(trafico.format_summary(con, args.trafico_resumen))
        con.close()
    elif args.terreno:
        run_terrain()
    elif args.historico:
        import historico
        con = storage.connect()
        try:
            historico.run(args, area_from_config(), con)
        except KeyboardInterrupt:
            print("\nInterrumpido. Lo descargado quedó guardado: vuelve a correr el mismo comando para continuar.")
        finally:
            con.close()
    elif args.eventos is not None:
        import historico
        print(historico.format_events(storage.top_events(args.eventos)))
    elif args.resumen:
        print(storage.format_summary())
    elif args.log is not None:
        events = storage.recent_events(args.log, args.solo_errores)
        print("\n".join(storage.format_event(e) for e in events) if events else "No hay eventos.")
    elif args.stats:
        print(json.dumps(storage.stats(), indent=2, ensure_ascii=False))
    else:
        n = storage.export_csv(args.exportar[0], args.exportar[1])
        print(f"{n} filas exportadas a {args.exportar[1]}")


if __name__ == "__main__":
    main()
