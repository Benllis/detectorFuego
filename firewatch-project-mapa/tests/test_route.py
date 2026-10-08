"""Pruebas del endpoint de rutas del proxy. Ejecutar: python tests/test_route.py"""
import json, os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import server

OSRM = {"code": "Ok", "routes": [{"distance": 5000, "duration": 600, "geometry": {"coordinates": [[-70.6, -33.4], [-70.5, -33.5]]}}]}
TOMTOM = {"routes": [{"summary": {"lengthInMeters": 5200, "travelTimeInSeconds": 800, "trafficDelayInSeconds": 200},
                      "legs": [{"points": [{"latitude": -33.4, "longitude": -70.6}, {"latitude": -33.5, "longitude": -70.5}]}]}]}


class RouteTests(unittest.TestCase):
    def setUp(self):
        server._mem.clear()
        self.urls = []
        server.CACHE_DIR = os.path.join(os.path.dirname(__file__), "_cache_test")
        server.cache_put = lambda k, v: server._mem.__setitem__(k, {"t": __import__("time").time(), "v": v})
        server.cache_get = lambda k, ttl, allow_stale=False: ((server._mem[k]["v"], 0) if k in server._mem else (None, None))

        def fake(url):
            self.urls.append(url)
            return json.dumps(TOMTOM if "tomtom" in url else OSRM).encode()
        server.http_get = fake
        os.environ.pop("TOMTOM_KEY", None)
        server.ENV.pop("TOMTOM_KEY", None)

    def test_osrm_normaliza(self):
        v, _, _ = server.route({"coords": ["-70.6,-33.4;-70.5,-33.5"]})
        r = json.loads(v)
        self.assertEqual(r["provider"], "osrm"); self.assertFalse(r["traffic"])
        self.assertEqual(r["routes"][0]["duration_s"], 600); self.assertIsNone(r["routes"][0]["delay_s"])
        self.assertIn("-70.6,-33.4;-70.5,-33.5", self.urls[0])

    def test_cache_no_repite_llamada(self):
        server.route({"coords": ["-70.6,-33.4;-70.5,-33.5"]}); server.route({"coords": ["-70.6,-33.4;-70.5,-33.5"]})
        self.assertEqual(len(self.urls), 1)

    def test_tomtom_sin_key_da_permiso(self):
        with self.assertRaises(PermissionError):
            server.route({"coords": ["-70.6,-33.4;-70.5,-33.5"], "provider": ["tomtom"]})

    def test_tomtom_con_key_normaliza_y_pone_lat_lon(self):
        os.environ["TOMTOM_KEY"] = "k"
        v, _, _ = server.route({"coords": ["-70.6,-33.4;-70.5,-33.5"], "provider": ["tomtom"]})
        r = json.loads(v)
        self.assertTrue(r["traffic"]); self.assertEqual(r["routes"][0]["delay_s"], 200)
        self.assertEqual(r["routes"][0]["coords"][0], [-70.6, -33.4])           # [lon, lat]
        self.assertIn("-33.4,-70.6:-33.5,-70.5", self.urls[0])                   # TomTom pide lat,lon
        os.environ.pop("TOMTOM_KEY")

    def test_validaciones(self):
        for bad in ["", "1,2", "a,b;c,d", "-70,-33;-70,-95", "1,2;3,4;5,6;7,8;9,10"]:
            with self.assertRaises(ValueError, msg=bad):
                server.route({"coords": [bad]})
        with self.assertRaises(ValueError):
            server.route({"coords": ["1,2;3,4"], "provider": ["google"]})

    def test_tile_valida_y_cachea(self):
        os.environ["TOMTOM_KEY"] = "k"
        server.http_get = lambda url: (self.urls.append(url) or b"png")
        self.assertEqual(server.traffic_tile(12, 1300, 2400), b"png")
        server.traffic_tile(12, 1300, 2400)
        self.assertEqual(len(self.urls), 1)
        with self.assertRaises(ValueError):
            server.traffic_tile(2, 9, 9)
        os.environ.pop("TOMTOM_KEY")
        server._tiles.clear()
        with self.assertRaises(PermissionError):
            server.traffic_tile(12, 1, 1)


if __name__ == "__main__":
    unittest.main()
