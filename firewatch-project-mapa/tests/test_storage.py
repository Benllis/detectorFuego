"""Pruebas del archivo histórico (SQLite) y del recolector. Ejecutar: python tests/test_storage.py"""
import json, math, os, sys, tempfile, threading, unittest, urllib.request
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import storage
import server

storage.QUIET = True  # sin eco en consola durante las pruebas
import urllib.error
import sqlite3
import socket

Q_CURRENT = ("latitude=-33.4500,-33.5000&longitude=-70.6500,-70.6000&current=temperature_2m,relative_humidity_2m,"
             "wind_speed_10m,wind_direction_10m&wind_speed_unit=kmh&timezone=UTC")
R_CURRENT = [{"elevation": 560, "current": {"time": "2026-09-29T20:00", "temperature_2m": 18.5, "relative_humidity_2m": 40,
                                            "wind_speed_10m": 12.0, "wind_direction_10m": 250}},
             {"elevation": 600, "current": {"time": "2026-09-29T20:00", "temperature_2m": 17.0, "relative_humidity_2m": None,
                                            "wind_speed_10m": 9.0, "wind_direction_10m": 240}}]
Q_HOURLY = ("latitude=-33.4500&longitude=-70.6500&hourly=temperature_2m,precipitation&start_date=2026-09-29&end_date=2026-09-29"
            "&wind_speed_unit=kmh&timezone=UTC")
R_HOURLY = {"elevation": 560, "hourly": {"time": ["2026-09-29T00:00", "2026-09-29T01:00"], "temperature_2m": [10, 9.5], "precipitation": [0, 1.2]}}
FIRMS = ("latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight\n"
         "-33.52,-70.87,296.9,0.4,0.4,2026-09-28,0830,N20,VIIRS,n,2.0NRT,280,1.2,N\n"
         "-33.29,-70.73,320.2,0.4,0.4,2026-09-28,1712,N,VIIRS,h,2.0NRT,290,3.0,D\n")


class StorageTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db"); os.close(fd)
        self.con = storage.connect(self.path)

    def tearDown(self):
        self.con.close(); os.remove(self.path)

    def test_clima_actual_y_sin_duplicados(self):
        n = storage.save_open_meteo(Q_CURRENT, json.dumps(R_CURRENT), self.con)
        self.assertEqual(n, 2)
        storage.save_open_meteo(Q_CURRENT, json.dumps(R_CURRENT), self.con)  # repetido: no duplica
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM weather_obs").fetchone()[0], 2)
        r = self.con.execute("SELECT * FROM weather_obs WHERE lat=-33.45").fetchone()
        self.assertEqual((r["source"], r["ts_utc"], r["temp_c"], r["wind_kmh"], r["elevation_m"]), ("om-current", "2026-09-29T20:00", 18.5, 12.0, 560))
        self.assertIsNone(self.con.execute("SELECT rh_pct FROM weather_obs WHERE lat=-33.5").fetchone()[0])

    def test_clima_horario_y_merge_de_campos(self):
        storage.save_open_meteo(Q_HOURLY, json.dumps(R_HOURLY), self.con)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM weather_obs WHERE source='om-hourly'").fetchone()[0], 2)
        # una segunda consulta con OTRAS variables completa la misma fila sin borrar lo anterior
        q2 = Q_HOURLY.replace("temperature_2m,precipitation", "relative_humidity_2m")
        storage.save_open_meteo(q2, json.dumps({"elevation": 560, "hourly": {"time": ["2026-09-29T00:00"], "relative_humidity_2m": [55]}}), self.con)
        r = self.con.execute("SELECT temp_c, precip_mm, rh_pct FROM weather_obs WHERE ts_utc='2026-09-29T00:00'").fetchone()
        self.assertEqual((r[0], r[1], r[2]), (10, 0, 55))

    def test_rechaza_zona_horaria_que_no_es_utc(self):
        with self.assertRaises(ValueError):
            storage.save_open_meteo(Q_CURRENT.replace("timezone=UTC", "timezone=auto"), json.dumps(R_CURRENT), self.con)

    def test_focos_sin_duplicados_y_hora_utc(self):
        self.assertEqual(storage.save_firms_csv(FIRMS, "VIIRS_NOAA20_NRT", self.con), 2)
        self.assertEqual(storage.save_firms_csv(FIRMS, "VIIRS_NOAA20_NRT", self.con), 0)   # ya estaban
        r = self.con.execute("SELECT acq_utc, frp, satellite FROM fire_detections ORDER BY acq_utc").fetchone()
        self.assertEqual((r[0], r[1], r[2]), ("2026-09-28T08:30", 1.2, "N20"))

    def test_estaciones(self):
        st = [{"id": "330020", "name": "Quinta Normal", "lat": -33.44, "lon": -70.68, "time": "2026-09-28 21:00:00", "temp": 15, "rh": 60, "windDir": 250, "windSpeedKmh": 14.8},
              {"id": "x", "name": "sin hora", "lat": 1, "lon": 1, "time": None}]
        self.assertEqual(storage.save_station_obs(st, self.con), 1)
        self.assertEqual(self.con.execute("SELECT ts_utc, wind_kmh FROM station_obs").fetchone()[:], ("2026-09-28T21:00", 14.8))

    def test_grilla_de_combustible(self):
        payload = {"key": "-70.9000,-33.7000,-70.4000,-33.2000", "bounds": [-70.9, -33.7, -70.4, -33.2], "cellM": 250, "source": "ESA",
                   "cells": [[0, 0, -33.699, -70.899, "matorral", 1.0, {"matorral": 100}], [0, 1, -33.699, -70.897, "urbano", 0.1, {"urbano": 90, "agua": 10}]]}
        self.assertFalse(storage.has_fuel_area(payload["key"], self.con))
        self.assertEqual(storage.save_fuel_grid(payload, self.con), 2)
        self.assertTrue(storage.has_fuel_area(payload["key"], self.con))
        self.assertEqual(json.loads(self.con.execute("SELECT fractions FROM fuel_cells WHERE c=1").fetchone()[0]), {"urbano": 90, "agua": 10})
        storage.save_fuel_grid(payload, self.con)  # reemplaza, no duplica
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM fuel_cells").fetchone()[0], 2)
        with self.assertRaises(ValueError):
            storage.save_fuel_grid({**payload, "cells": []}, self.con)

    def test_pendiente_por_vecinos(self):
        # plano inclinado: sube 10 m por cada celda hacia el este (columna) → 10/250 = 4 %, mira al oeste
        elev = {(r, c): 100 + 10 * c for r in range(3) for c in range(3)}
        slope, aspect = storage.slope_aspect_from_grid(elev, 1, 1, 250)
        self.assertAlmostEqual(slope, 4.0, places=6)
        self.assertAlmostEqual(aspect, 270.0, places=6)
        self.assertEqual(storage.slope_aspect_from_grid(elev, 0, 0, 250), (None, None))  # borde
        flat = {(r, c): 100 for r in range(3) for c in range(3)}
        self.assertEqual(storage.slope_aspect_from_grid(flat, 1, 1, 250), (0.0, None))

    def test_stats_y_export(self):
        storage.save_firms_csv(FIRMS, "X", self.con)
        s = storage.stats(self.con)
        self.assertEqual(s["fire_detections"], 2)
        out = self.path + ".csv"
        self.assertEqual(storage.export_csv("fire_detections", out, self.con), 2)
        self.assertIn("acq_utc", open(out, encoding="utf-8").readline()); os.remove(out)
        with self.assertRaises(ValueError):
            storage.export_csv("sqlite_master; DROP TABLE x", out, self.con)


class ServerTests(unittest.TestCase):
    """Los endpoints de /api/store y el guardado automático al consultar Open-Meteo/FIRMS."""
    @classmethod
    def setUpClass(cls):
        fd, cls.path = tempfile.mkstemp(suffix=".db"); os.close(fd)
        storage.DB_PATH = cls.path
        storage.LOG_PATH = cls.path + ".log"
        server.CACHE_DIR = tempfile.mkdtemp()
        server._mem.clear()
        server.http_get = lambda url: json.dumps(R_CURRENT).encode() if "open-meteo" in url else FIRMS.encode()
        os.environ["FIRMS_MAP_KEY"] = "k"
        cls.srv = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown(); os.remove(cls.path); os.environ.pop("FIRMS_MAP_KEY")

    def get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}") as r:
            return json.loads(r.read())

    def post(self, path, obj, headers=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", json.dumps(obj).encode(), {"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_flujo(self):
        self.get("/api/openmeteo?" + Q_CURRENT)
        self.get("/api/openmeteo?" + Q_CURRENT)  # 2ª: sale del caché y no vuelve a guardar
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/firms?source=VIIRS_NOAA20_NRT&bbox=-71,-34,-70,-33&days=1") as r:
            r.read()
        s = self.get("/api/store/stats")
        self.assertEqual((s["weather_obs"], s["fire_detections"]), (2, 2))
        key = "-70.9000,-33.7000,-70.4000,-33.2000"
        self.assertFalse(self.get("/api/store/has?key=" + key)["exists"])
        code, out = self.post("/api/store/fuelgrid", {"key": key, "bounds": [-70.9, -33.7, -70.4, -33.2], "cells": [[0, 0, -33.69, -70.89, "matorral", 1, {"matorral": 100}]]})
        self.assertEqual((code, out["cells"]), (200, 1))
        self.assertTrue(self.get("/api/store/has?key=" + key)["exists"])
        self.assertTrue(self.get("/api/status")["archive"])

    def test_seguridad(self):
        code, _ = self.post("/api/store/fuelgrid", {"key": "k", "bounds": [0, 0, 1, 1], "cells": [[0, 0, 0, 0, "a", 1, {}]]}, {"Origin": "http://evil.example"})
        self.assertEqual(code, 403)
        code, _ = self.post("/api/store/fuelgrid", {"nada": 1})
        self.assertEqual(code, 400)
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.get("/api/store/has?key=%27%20OR%201%3D1")
        self.assertEqual(e.exception.code, 400)
        for blocked in ("/storage.py", "/data/firewatch.db", "/DATA/x"):
            with self.assertRaises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(f"http://127.0.0.1:{self.port}{blocked}")
            self.assertEqual(e.exception.code, 404)


class LogTests(unittest.TestCase):
    """Registro de lo que se guarda: tipos de error, avisos y omitidos."""
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db"); os.close(fd)
        storage.DB_PATH = self.path; storage.LOG_PATH = self.path + ".log"; storage.ORIGIN = "server"
        server.CACHE_DIR = tempfile.mkdtemp(); server._mem.clear()
        for k in ("FIRMS_MAP_KEY", "DMC_USUARIO", "DMC_TOKEN"):
            os.environ.pop(k, None); server.ENV.pop(k, None)

    def tearDown(self):
        os.remove(self.path)
        if os.path.exists(self.path + ".log"): os.remove(self.path + ".log")

    def events(self):
        return storage.recent_events(100)

    def test_clasificacion_de_errores(self):
        c = storage.classify_error
        self.assertEqual(c(urllib.error.HTTPError("u", 403, "x", {}, None)), "credenciales")
        self.assertEqual(c(urllib.error.HTTPError("u", 429, "x", {}, None)), "limite_de_uso")
        self.assertEqual(c(urllib.error.HTTPError("u", 400, "x", {}, None)), "http_4xx")
        self.assertEqual(c(urllib.error.HTTPError("u", 503, "x", {}, None)), "http_5xx")
        self.assertEqual(c(urllib.error.URLError("dns")), "red")
        self.assertEqual(c(socket.timeout("t")), "red")
        self.assertEqual(c(PermissionError("k")), "credenciales")
        self.assertEqual(c(sqlite3.OperationalError("database is locked")), "base_bloqueada")
        self.assertEqual(c(sqlite3.OperationalError("disk I/O error")), "disco")
        self.assertEqual(c(json.JSONDecodeError("m", "d", 0)), "formato")
        self.assertEqual(c(KeyError("x")), "formato")
        self.assertEqual(c(ValueError("v")), "validacion")
        self.assertEqual(c(RuntimeError("?")), "desconocido")
        for k in ("red", "credenciales", "limite_de_uso", "http_4xx", "http_5xx", "formato", "datos_incompletos", "validacion", "base_bloqueada", "disco", "desconocido"):
            self.assertIn(k, storage.ERROR_HINTS)

    def test_ok_se_registra_con_filas_y_duracion(self):
        server.http_get = lambda url: json.dumps(R_CURRENT).encode()
        server.open_meteo(Q_CURRENT)
        (e,) = self.events()
        self.assertEqual((e["source"], e["variant"], e["status"], e["rows_in"], e["phase"]), ("clima", "actual", "ok", 2, "guardado"))
        self.assertIsNotNone(e["ms"]); self.assertEqual(e["origin"], "server")

    def test_error_de_red_en_descarga(self):
        def boom(url): raise urllib.error.URLError("timed out")
        server.http_get = boom
        with self.assertRaises(urllib.error.URLError):
            server.open_meteo(Q_CURRENT)
        (e,) = self.events()
        self.assertEqual((e["status"], e["error_type"], e["phase"]), ("error", "red", "descarga"))
        self.assertIn("timed out", e["error_msg"])

    def test_http_400_y_formato_roto_al_guardar_no_tumban_el_servicio(self):
        def bad(url): raise urllib.error.HTTPError(url, 400, "Bad", {}, None)
        server.http_get = bad
        with self.assertRaises(urllib.error.HTTPError):
            server.open_meteo(Q_CURRENT)
        server._mem.clear()
        server.http_get = lambda url: b'{"error": true, "reason": "Cannot initialize"}'   # 200 pero sin datos
        text, _, _ = server.open_meteo(Q_CURRENT)   # el dato igual se sirve
        self.assertIn("error", text)
        ev = self.events()
        self.assertEqual([(x["status"], x["error_type"], x["phase"]) for x in ev], [("error", "http_4xx", "descarga"), ("warn", "formato", "guardado")])

    def test_clima_con_muchos_vacios_da_aviso(self):
        empty = [{"elevation": 1, "current": {"time": "2026-09-29T20:00", "temperature_2m": None, "relative_humidity_2m": None, "wind_speed_10m": None, "wind_direction_10m": None}}] * 2
        server.http_get = lambda url: json.dumps(empty).encode()
        server.open_meteo(Q_CURRENT)
        (e,) = self.events()
        self.assertEqual((e["status"], e["error_type"]), ("warn", "datos_incompletos"))

    def test_firms_key_invalida_y_sin_key(self):
        with self.assertRaises(PermissionError):
            server.firms({"bbox": ["-71,-34,-70,-33"]})
        os.environ["FIRMS_MAP_KEY"] = "mala"
        server.http_get = lambda url: b"Invalid MAP_KEY."     # FIRMS responde 200 con texto
        with self.assertRaises(PermissionError):
            server.firms({"bbox": ["-71,-34,-70,-33"]})
        server._mem.clear()
        server.http_get = lambda url: FIRMS.encode()
        server.firms({"bbox": ["-71,-34,-70,-33"], "source": ["VIIRS_SNPP_NRT"]})
        os.environ.pop("FIRMS_MAP_KEY")
        ev = self.events()
        self.assertEqual([(x["status"], x["error_type"], x["phase"]) for x in ev],
                         [("skipped", "credenciales", "solicitud"), ("error", "credenciales", "descarga"), ("ok", None, "guardado")])
        self.assertEqual((ev[2]["rows_in"], ev[2]["rows_new"], ev[2]["variant"]), (2, 2, "VIIRS_SNPP_NRT"))

    def test_resumen_y_texto(self):
        server.http_get = lambda url: json.dumps(R_CURRENT).encode()
        server.open_meteo(Q_CURRENT)
        def boom(url): raise urllib.error.URLError("sin red")
        server.http_get = boom
        with self.assertRaises(urllib.error.URLError):
            server.open_meteo(Q_CURRENT.replace("-33.4500", "-33.4400"))   # otra consulta: no sale del caché
        (row,) = storage.summary_by_source()
        self.assertEqual((row["source"], row["ok"], row["err"], row["rows"]), ("clima", 1, 1, 2))
        self.assertEqual(row["last_problem"]["error_type"], "red")
        txt = storage.format_log_text(10)
        self.assertIn("clima:actual", txt); self.assertIn("ERROR", txt); self.assertIn("Qué hacer", txt); self.assertIn("red:", txt)
        self.assertIn("ERROR", open(storage.LOG_PATH, encoding="utf-8").read())    # también en el archivo de texto
        s = storage.stats()
        self.assertEqual((s["errors_24h"], s["last_error"]["error_type"]), (1, "red"))

    def test_registrar_nunca_lanza_aunque_la_base_falle(self):
        storage.DB_PATH = "/ruta/que/no/existe/\0/x.db"
        ev = storage.log_event("clima", "error", err=RuntimeError("x"))   # no debe lanzar
        self.assertEqual(ev["error_type"], "desconocido")
        storage.DB_PATH = self.path

    def test_endpoints_del_registro(self):
        server.http_get = lambda url: json.dumps(R_CURRENT).encode()
        srv = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            base = f"http://127.0.0.1:{srv.server_address[1]}"
            urllib.request.urlopen(base + "/api/openmeteo?" + Q_CURRENT).read()
            ev = json.loads(urllib.request.urlopen(base + "/api/store/log?limit=5").read())["events"]
            self.assertEqual(ev[0]["status"], "ok")
            sm = json.loads(urllib.request.urlopen(base + "/api/store/summary").read())
            self.assertEqual(sm["sources"][0]["source"], "clima"); self.assertIn("red", sm["hints"])
            txt = urllib.request.urlopen(base + "/api/store/log.txt").read().decode()
            self.assertIn("clima:actual", txt)
            self.assertEqual(json.loads(urllib.request.urlopen(base + "/api/store/log?errors=1").read())["events"], [])
        finally:
            srv.shutdown()


class CollectorTests(unittest.TestCase):
    def test_puntos_y_area_coinciden_con_el_mapa(self):
        import recolector
        a = recolector.area_from_config()
        self.assertEqual((a["lat"], a["lon"], a["radius_km"], a["size"]), (-33.45, -70.65, 28.0, 8))
        pts = recolector.grid_points(a)
        self.assertEqual(len(pts), 64)
        # esquina suroeste: -radio en lat y en lon
        self.assertAlmostEqual(pts[0][0], -33.45 - 28 / 111.32, places=6)
        self.assertAlmostEqual(pts[0][1], -70.65 - 28 / (111.32 * math.cos(math.radians(-33.45))), places=6)
        self.assertEqual(recolector.coords_query(pts).count(","), 2 * 63)


if __name__ == "__main__":
    unittest.main(verbosity=1)
