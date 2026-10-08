"""Pruebas de la foto de tráfico (decodificador MVT + guardado). Ejecutar: python tests/test_trafico.py"""
import gzip, os, sys, tempfile, unittest, urllib.error
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
sys.path.insert(0, os.path.dirname(__file__))
import storage
storage.QUIET = True
import trafico
from mvt_encode import encode_tile

Z, X, Y = 11, 622, 1226   # tile que contiene Santiago
AREA = {"lat": -33.45, "lon": -70.66, "radius_km": 3, "size": 5}


def fake_tile():
    return encode_tile([
        ({"road_type": "Motorway", "traffic_level": 0.9, "traffic_road_coverage": "full"}, [(0, 0), (1000, 0)]),
        ({"road_type": "Motorway", "traffic_level": 0.2, "traffic_road_coverage": "one_side"}, [(0, 100), (500, 100), (900, 400)]),
        ({"road_type": "Major road", "traffic_level": 0.5}, [(100, 800), (700, 800)]),
        ({"road_type": "Local road", "traffic_level": 1.0}, [(0, 2000), (300, 2000)]),
        ({"road_type": "Secondary road", "traffic_level": 0.7, "road_closure": True}, [(0, 3000), (200, 3000)]),
    ])


class Base(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db"); os.close(fd)
        storage.DB_PATH = self.path; storage.LOG_PATH = self.path + ".log"
        self.con = storage.connect(self.path)

    def tearDown(self):
        self.con.close(); os.remove(self.path)
        if os.path.exists(self.path + ".log"): os.remove(self.path + ".log")


class DecodeTests(unittest.TestCase):
    def test_ida_y_vuelta_del_tile(self):
        layers = trafico.decode_tile(fake_tile())
        feats = layers["Traffic flow"]["features"]
        self.assertEqual(len(feats), 5)
        self.assertEqual(layers["Traffic flow"]["extent"], 4096)
        self.assertEqual(feats[0]["props"]["road_type"], "Motorway")
        self.assertAlmostEqual(feats[0]["props"]["traffic_level"], 0.9, places=5)
        self.assertEqual(feats[1]["lines"], [[(0, 100), (500, 100), (900, 400)]])
        self.assertIs(feats[4]["props"]["road_closure"], True)

    def test_acepta_gzip(self):
        self.assertEqual(len(trafico.decode_tile(gzip.compress(fake_tile()))["Traffic flow"]["features"]), 5)

    def test_clases_de_via(self):
        c = trafico.road_class
        self.assertEqual(c({"road_type": "Motorway"}), "autopista")
        self.assertEqual(c({"road_type": "Major road"}), "principal")
        self.assertEqual(c({"road_type": "Minor local road"}), "calle")
        self.assertEqual(c({"road_category": "trunk", "road_type": "x"}), "troncal")
        self.assertEqual(c({}), "otra")

    def test_geometria_en_el_lugar_correcto(self):
        segs = trafico.segments_from_tile(Z, X, Y, fake_tile())
        self.assertEqual(len(segs), 5)
        lat0, lon0 = trafico.tile_to_lonlat(Z, X, Y, 0, 0, 4096)
        self.assertTrue(-33.9 < lat0 < -33.2 and -71.0 < lon0 < -70.3, (lat0, lon0))
        km = segs[0]["length_m"]   # 1000/4096 de un tile de ~19,6 km a z11 (en longitud ~16 km)
        self.assertTrue(2500 < km < 5000, km)

    def test_tiles_del_area(self):
        t = trafico.tiles_for_bbox(*trafico.area_bbox(AREA), 11)
        self.assertIn((11, X, Y), t)
        self.assertTrue(1 <= len(t) <= 4)


class SnapshotTests(Base):
    def test_foto_resumen_y_filtro_de_detalle(self):
        r = trafico.snapshot(self.con, AREA, lambda z, x, y: fake_tile(), zoom=11, detail="principales", log=lambda m: None)
        n_tiles = r["tiles"]
        self.assertEqual(r["segments"], 5 * n_tiles)
        self.assertEqual(r["stored"], 3 * n_tiles)    # 2 autopistas + 1 principal; la calle y la secundaria no entran
        classes = {row["road_class"] for row in self.con.execute("SELECT road_class FROM traffic_segments")}
        self.assertEqual(classes, {"autopista", "principal"})
        summ = {row["road_class"]: dict(row) for row in self.con.execute("SELECT * FROM traffic_summary")}
        self.assertEqual(set(summ), {"autopista", "principal", "calle", "secundaria"})   # el resumen incluye todas
        a = summ["autopista"]
        self.assertEqual(a["n_segments"], 2 * n_tiles)
        self.assertTrue(0.2 < a["mean_level"] < 0.9)
        self.assertGreater(a["km_jam"], 0)        # el tramo de nivel 0,2 es congestión
        self.assertGreater(a["km_slow"], a["km_jam"] - 1e-9)
        self.assertEqual(self.con.execute("SELECT closure FROM traffic_segments WHERE road_class='secundaria'").fetchall(), [])

    def test_detalle_todas_guarda_todo(self):
        r = trafico.snapshot(self.con, AREA, lambda z, x, y: fake_tile(), detail="todas", log=lambda m: None)
        self.assertEqual(r["stored"], r["segments"])

    def test_tile_malo_no_tumba_la_foto(self):
        calls = []
        def fetch(z, x, y):
            calls.append(1)
            if len(calls) == 1 and len(trafico.tiles_for_bbox(*trafico.area_bbox(AREA), 11)) > 1:
                raise urllib.error.URLError("timed out")
            return fake_tile()
        r = trafico.snapshot(self.con, AREA, fetch, log=lambda m: None)
        self.assertGreater(r["segments"], 0)

    def test_sin_capa_es_error_de_formato(self):
        empty = encode_tile([], layer="otra capa")
        with self.assertRaises(RuntimeError):
            trafico.snapshot(self.con, AREA, lambda z, x, y: empty, log=lambda m: None)
        row = self.con.execute("SELECT status, error_type FROM ingest_log WHERE source='trafico' ORDER BY id DESC").fetchone()
        self.assertEqual((row["status"], row["error_type"]), ("error", "formato"))

    def test_todos_los_tiles_caen_es_error_de_red(self):
        def fetch(z, x, y): raise urllib.error.URLError("timed out")
        with self.assertRaises(RuntimeError):
            trafico.snapshot(self.con, AREA, fetch, log=lambda m: None)
        row = self.con.execute("SELECT status, error_type FROM ingest_log WHERE source='trafico' AND status='error'").fetchone()
        self.assertEqual(row["error_type"], "red")

    def test_sin_key_se_propaga(self):
        def fetch(z, x, y): raise PermissionError("Falta TOMTOM_KEY")
        with self.assertRaises(PermissionError):
            trafico.snapshot(self.con, AREA, fetch, log=lambda m: None)

    def test_retencion_borra_tramos_viejos_pero_no_el_resumen(self):
        storage.save_traffic(self.con, "2020-01-01T00:00:00Z",
                             [{"road_class": "autopista", "n_segments": 1, "km": 1, "mean_level": .5, "km_slow": 0, "km_jam": 0, "n_tiles": 1, "zoom": 11}],
                             [{"z": 11, "x": 1, "y": 1, "idx": 0, "road_class": "autopista", "road_type": "Motorway", "level": .5, "coverage": None, "closure": 0, "lat": -33, "lon": -70, "length_m": 100}],
                             keep_days=0)
        storage.save_traffic(self.con, storage.now_iso(), [], [], keep_days=30)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM traffic_segments").fetchone()[0], 0)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM traffic_summary").fetchone()[0], 1)

    def test_resumen_en_texto(self):
        trafico.snapshot(self.con, AREA, lambda z, x, y: fake_tile(), log=lambda m: None)
        txt = trafico.format_summary(self.con)
        self.assertIn("autopista", txt); self.assertIn("congestión", txt)
        self.con.execute("DELETE FROM traffic_summary")
        self.assertIn("Aún no hay", trafico.format_summary(self.con))


if __name__ == "__main__":
    unittest.main(verbosity=1)
