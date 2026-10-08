"""Pruebas de la descarga del pasado y de la reconstrucción de incendios. Ejecutar: python tests/test_historico.py"""
import datetime as dt, json, os, sys, tempfile, unittest, urllib.parse
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import storage
storage.QUIET = True
import historico

D = dt.date


def det(lat, lon, hhmm_utc, conf="n", frp=2.0, sat="N20"):
    return (lat, lon, hhmm_utc, sat, "VIIRS", conf, frp, 330.0, "D", "VIIRS_NOAA20_SP", "2026-10-01T00:00:00Z")


class Base(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db"); os.close(fd)
        storage.DB_PATH = self.path; storage.LOG_PATH = self.path + ".log"
        self.con = storage.connect(self.path)

    def tearDown(self):
        self.con.close(); os.remove(self.path)
        if os.path.exists(self.path + ".log"): os.remove(self.path + ".log")

    def insert(self, rows):
        with self.con:
            self.con.executemany("INSERT OR IGNORE INTO fire_detections (lat,lon,acq_utc,satellite,instrument,confidence,frp,brightness,daynight,firms_source,first_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)


class PlanTests(unittest.TestCase):
    def test_tramos_cubren_el_rango_sin_huecos(self):
        ch = historico.plan_chunks(D(2024, 1, 1), D(2024, 1, 12))
        self.assertEqual([(c[0].isoformat(), c[1]) for c in ch], [("2024-01-01", 5), ("2024-01-06", 5), ("2024-01-11", 2)])
        self.assertEqual(sum(n for _, n in ch), 12)
        self.assertEqual(historico.plan_chunks(D(2024, 1, 5), D(2024, 1, 5)), [(D(2024, 1, 5), 1)])
        self.assertEqual(historico.plan_chunks(D(2024, 1, 6), D(2024, 1, 5)), [])

    def test_productos_segun_antiguedad(self):
        today = D(2026, 10, 1)
        old = historico.sources_for(D(2024, 1, 1), 5, today)
        self.assertEqual(old, ["VIIRS_SNPP_SP", "VIIRS_NOAA20_SP"])
        recent = historico.sources_for(D(2026, 9, 20), 5, today)
        self.assertTrue(all(s.endswith("_NRT") for s in recent) and "VIIRS_NOAA21_NRT" in recent)
        mid = historico.sources_for(D(2026, 5, 1), 5, today)   # ~150 días: ambos
        self.assertIn("VIIRS_SNPP_SP", mid); self.assertIn("VIIRS_SNPP_NRT", mid)

    def test_zonas(self):
        area = {"lat": -33.45, "lon": -70.65, "radius_km": 28.0, "size": 8}
        self.assertEqual(historico.resolve_zone("centro", area)[0], "centro")
        w, s, e, n = historico.resolve_zone("santiago", area)[1]
        self.assertTrue(w < -70.65 < e and s < -33.45 < n)
        self.assertEqual(historico.resolve_zone("-72,-34,-70,-33", area), ("custom", (-72.0, -34.0, -70.0, -33.0)))
        with self.assertRaises(SystemExit):
            historico.resolve_zone("-70,-34,-72,-33", area)   # oeste > este
        with self.assertRaises(SystemExit):
            historico.resolve_zone("nada", area)


class EventTests(Base):
    def test_agrupa_por_espacio_y_tiempo_con_cadenas(self):
        self.insert([
            # incendio A: 3 detecciones en 10 h
            det(-33.000, -71.000, "2024-02-03T18:00"), det(-33.003, -71.002, "2024-02-03T21:00", frp=9.5), det(-33.001, -71.001, "2024-02-04T04:00"),
            # cadena: +40 h y +80 h (cada salto ≤ 48 h) → sigue siendo A
            det(-33.002, -71.001, "2024-02-05T20:00"), det(-33.002, -71.002, "2024-02-07T04:00"),
            # incendio B: mismo momento, a ~100 km
            det(-33.900, -71.800, "2024-02-03T19:00"),
            # incendio C: cerca de A pero 20 días después
            det(-33.001, -71.001, "2024-02-27T18:00"),
            # confianza baja cerca de A: se ignora por defecto
            det(-33.001, -71.000, "2024-02-03T18:30", conf="l"),
        ])
        r = storage.build_fire_events(self.con)
        self.assertEqual((r["detections"], r["events"], r["multi"]), (7, 3, 1))
        ev = {e["n_detections"]: e for e in storage.top_events(10, self.con)}
        a = ev[5]
        self.assertEqual((a["start_utc"], a["end_utc"], a["n_days"]), ("2024-02-03T18:00", "2024-02-07T04:00", 4))
        self.assertAlmostEqual(a["duration_h"], 82.0, places=1)
        self.assertEqual((a["max_frp"], a["max_confidence"], a["satellites"]), (9.5, "n", "N20"))
        self.assertGreater(a["area_km2_approx"], 0); self.assertLess(a["extent_km"], 1)
        # todas las detecciones quedan asignadas a un incendio
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM fire_event_detections").fetchone()[0], 7)
        self.assertEqual(self.con.execute("SELECT COUNT(DISTINCT event_id) FROM fire_event_detections").fetchone()[0], 3)

    def test_incluir_baja_y_reconstruccion_idempotente(self):
        self.insert([det(-33.0, -71.0, "2024-02-03T18:00"), det(-33.001, -71.0, "2024-02-03T18:30", conf="l")])
        self.assertEqual(storage.build_fire_events(self.con)["detections"], 1)
        r1 = storage.build_fire_events(self.con, include_low=True)
        self.assertEqual(r1["detections"], 2)
        r2 = storage.build_fire_events(self.con, include_low=True)
        self.assertEqual(r1, r2)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM fire_events").fetchone()[0], 1)   # se rehace, no se acumula
        self.assertIn('"include_low": true', self.con.execute("SELECT params FROM fire_events").fetchone()[0])

    def test_base_vacia_y_modis_numerico(self):
        self.assertEqual(storage.build_fire_events(self.con), {"detections": 0, "events": 0, "multi": 0})
        self.insert([det(-33.0, -71.0, "2024-02-03T18:00", conf="85"), det(-33.0, -71.001, "2024-02-03T18:10", conf="10")])
        self.assertEqual(storage.build_fire_events(self.con)["detections"], 1)   # 10 % es confianza baja

    def test_formato_de_lista(self):
        self.assertIn("Todavía no hay", historico.format_events([]))
        self.insert([det(-33.0, -71.0, "2024-02-03T18:00")])
        storage.build_fire_events(self.con)
        txt = historico.format_events(storage.top_events(5, self.con))
        self.assertIn("2024-02-03T18:00", txt); self.assertIn("NO el área quemada", txt)


class BackfillTests(Base):
    def test_focos_reanuda_sin_repetir_y_reintenta_errores(self):
        calls, fail = [], {"on": True}
        def fake_firms(p):
            calls.append((p["source"][0], p["date"][0], p["days"][0], p["bbox"][0]))
            if fail["on"] and p["date"][0] == "2024-01-06":
                raise ConnectionError("sin red")
            return ("latitude,longitude,acq_date\n1,1,2024-01-01\n2,2,2024-01-01\n", 0, False)
        bbox = (-72.0, -34.0, -70.0, -33.0)
        r = historico.backfill_fires(self.con, "centro", bbox, D(2024, 1, 1), D(2024, 1, 10), today=D(2026, 10, 1), sleep=0, log=lambda m: None, firms=fake_firms)
        self.assertEqual((r["chunks"], r["done"], r["errors"], r["rows"]), (4, 2, 2, 4))
        self.assertEqual(calls[0], ("VIIRS_SNPP_SP", "2024-01-01", "5", "-72.0000,-34.0000,-70.0000,-33.0000"))
        fail["on"] = False; calls.clear()
        r2 = historico.backfill_fires(self.con, "centro", bbox, D(2024, 1, 1), D(2024, 1, 10), today=D(2026, 10, 1), sleep=0, log=lambda m: None, firms=fake_firms)
        self.assertEqual((r2["skipped"], r2["done"], r2["errors"]), (2, 2, 0))
        self.assertEqual({c[1] for c in calls}, {"2024-01-06"})   # solo reintenta lo que falló

    def test_se_detiene_tras_tres_errores_seguidos(self):
        def boom(p): raise PermissionError("MAP_KEY inválida")
        r = historico.backfill_fires(self.con, "centro", (-72, -34, -70, -33), D(2024, 1, 1), D(2024, 3, 1), today=D(2026, 10, 1), sleep=0, log=lambda m: None, firms=boom)
        self.assertTrue(r["aborted"]); self.assertEqual(r["errors"], 3)

    def test_clima_ventana_variables_y_limite_de_uso(self):
        self.insert([det(-33.0, -71.0, "2024-02-03T18:00"), det(-33.001, -71.0, "2024-02-03T19:00"), det(-33.002, -71.0, "2024-02-04T01:00"),
                     det(-34.0, -72.0, "2026-09-30T18:00"), det(-34.001, -72.0, "2026-09-30T19:00"), det(-34.002, -72.0, "2026-09-30T20:00"),
                     det(-33.5, -71.5, "2024-03-01T18:00")])     # incendio de 1 sola detección
        storage.build_fire_events(self.con)
        queries = []
        def archive(q):
            queries.append(urllib.parse.parse_qs(q))
            if "wind_gusts_10m" in q:   # el primer juego (el más completo) se rechaza
                raise RuntimeError("HTTP Error 400: Bad Request")
        r = historico.backfill_weather(self.con, min_detections=3, today=D(2026, 10, 1), sleep=0, log=lambda m: None, archive=archive)
        self.assertEqual((r["events"], r["done"], r["too_recent"], r["errors"]), (2, 1, 1, 0))   # el de 2026 es muy reciente; el de 1 detección no entra
        q = queries[-1]
        self.assertEqual((q["start_date"][0], q["end_date"][0]), ("2024-01-27", "2024-02-05"))    # 7 días antes → 1 día después
        self.assertEqual(q["timezone"][0], "UTC"); self.assertNotIn("wind_gusts_10m", q["hourly"][0])
        self.assertEqual(len(queries), 3)   # 2 juegos rechazados (con ráfaga) y luego el básico
        # segunda corrida: ya estaba
        r2 = historico.backfill_weather(self.con, 3, today=D(2026, 10, 1), sleep=0, log=lambda m: None, archive=lambda q: None)
        self.assertEqual((r2["done"], r2["skipped"]), (0, 1))

    def test_clima_se_detiene_con_429(self):
        self.insert([det(-33.0, -71.0, "2024-02-03T18:00"), det(-33.001, -71.0, "2024-02-03T19:00"), det(-33.002, -71.0, "2024-02-04T01:00")])
        storage.build_fire_events(self.con)
        def limit(q): raise RuntimeError("HTTP Error 429: Too Many Requests")
        r = historico.backfill_weather(self.con, 3, today=D(2026, 10, 1), sleep=0, log=lambda m: None, archive=limit)
        self.assertTrue(r["aborted"]); self.assertEqual(r["errors"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=1)
