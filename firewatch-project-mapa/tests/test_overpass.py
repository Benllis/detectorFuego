"""Pruebas del proxy de Overpass (sin red: el POST al servidor público va simulado). Ejecutar: python tests/test_overpass.py"""
import json, os, sys, threading, time, unittest, urllib.error, urllib.request
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import server

Q = '[out:json][timeout:60];node["emergency"="fire_hydrant"](-33.5,-70.7,-33.4,-70.6);out;'
OK = {"elements": [{"type": "node", "id": 1, "lat": -33.45, "lon": -70.65}]}


class OverpassTests(unittest.TestCase):
    def setUp(self):
        server._mem.clear()
        self.calls = []
        server.cache_put = lambda k, v: server._mem.__setitem__(k, {"t": time.time(), "v": v})
        server.cache_get = lambda k, ttl, allow_stale=False: ((server._mem[k]["v"], 0) if k in server._mem else (None, None))
        self._sleep, server.time.sleep = time.sleep, lambda s: None     # sin esperas reales entre reintentos
        self.respuestas = []

        def fake(url, query, timeout):
            self.calls.append((url, query, timeout))
            r = self.respuestas.pop(0) if self.respuestas else OK
            if isinstance(r, Exception):
                raise r
            return r
        server._overpass_post = fake

    def tearDown(self):
        server.time.sleep = self._sleep

    def test_devuelve_el_json_de_overpass(self):
        v, age, stale = server.overpass(Q)
        self.assertEqual(v, OK); self.assertFalse(stale)
        self.assertEqual(self.calls[0][0], server.OVERPASS_URLS[0])
        self.assertEqual(self.calls[0][2], 75)                      # timeout de la consulta (60) + 15 s de margen

    def test_cache_no_repite_la_llamada(self):
        server.overpass(Q); server.overpass("  " + Q + "  ")        # mismo texto tras recortar espacios
        self.assertEqual(len(self.calls), 1)

    def test_reintenta_y_cae_al_mirror(self):
        self.respuestas = [OSError("timeout"), OSError("timeout"), OK]
        v, _, _ = server.overpass(Q)
        self.assertEqual(v, OK)
        self.assertEqual([c[0] for c in self.calls], [server.OVERPASS_URLS[0], server.OVERPASS_URLS[0], server.OVERPASS_URLS[1]])

    def test_remark_de_timeout_no_se_acepta_ni_se_guarda(self):
        self.respuestas = [{"elements": [], "remark": "runtime error: Query timed out"}, OK]
        v, _, _ = server.overpass(Q)
        self.assertEqual(v, OK)                                      # el segundo intento sí
        self.assertEqual(len(self.calls), 2)

    def test_si_todo_falla_lanza_el_ultimo_error(self):
        self.respuestas = [OSError("a"), OSError("b"), OSError("c")]
        with self.assertRaises(OSError) as cm:
            server.overpass(Q)
        self.assertEqual(str(cm.exception), "c")
        self.assertEqual(server._mem, {})                            # un fallo no se guarda como si fuera dato

    def test_si_falla_y_hay_dato_viejo_lo_devuelve_marcado(self):
        server.overpass(Q)
        server.cache_get = lambda k, ttl, allow_stale=False: ((server._mem[k]["v"], 99999) if allow_stale and k in server._mem else (None, None))
        self.respuestas = [OSError("x")] * 3
        v, age, stale = server.overpass(Q)
        self.assertTrue(stale); self.assertEqual(v, OK)

    def test_validaciones(self):
        for mala in ("", "node(1);out;", "[out:xml];node(1);out;", '[timeout:60];node(1);out;',
                     Q + " " * (server.MAX_OVERPASS_BODY + 1) + "x"):
            with self.assertRaises(ValueError, msg=mala[:30]):
                server.overpass(mala)
        self.assertEqual(self.calls, [])                             # nada malo llega a Overpass

    def test_consulta_sin_timeout_usa_el_de_por_defecto_y_el_tope_se_respeta(self):
        server.overpass('[out:json];node(1);out;')
        server.overpass('[out:json][timeout:999];node(2);out;')
        self.assertEqual([c[2] for c in self.calls], [40, 135])


class HttpTests(unittest.TestCase):
    """El endpoint de verdad, con un servidor local en un puerto libre."""
    @classmethod
    def setUpClass(cls):
        cls.srv = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.eventos = []
        server.storage.log_event = lambda *a, **k: cls.eventos.append((a, k))   # no escribir en la base real

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        OverpassTests.setUp(self)

    def tearDown(self):
        OverpassTests.tearDown(self)

    def post(self, cuerpo, headers=None, raw=None):
        data = raw if raw is not None else json.dumps(cuerpo).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/overpass", data=data, method="POST",
                                     headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read()), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read()), dict(e.headers)

    def test_flujo_completo(self):
        st, body, h = self.post({"query": Q})
        self.assertEqual(st, 200); self.assertEqual(body, OK); self.assertEqual(h["X-Stale"], "false")

    def test_consulta_mala_es_400(self):
        self.assertEqual(self.post({"query": "node(1);out;"})[0], 400)
        self.assertEqual(self.post({"otra": 1})[0], 400)
        self.assertEqual(self.post(None, raw=b"{no es json")[0], 400)
        self.assertEqual(self.post({"query": 123})[0], 400)

    def test_origen_de_otro_sitio_se_rechaza(self):
        st, body, _ = self.post({"query": Q}, headers={"Origin": "http://sitio-malo.example"})
        self.assertEqual(st, 403); self.assertEqual(self.calls, [])

    def test_cuerpo_gigante_es_413(self):
        st, _, _ = self.post(None, raw=b"x" * (server.MAX_OVERPASS_BODY + 2048))
        self.assertEqual(st, 413)

    def test_si_overpass_cae_responde_502_y_lo_anota(self):
        self.respuestas = [OSError("caído")] * 3
        st, body, _ = self.post({"query": '[out:json];node(77);out;'})
        self.assertEqual(st, 502); self.assertEqual(body["kind"], "upstream")
        self.assertTrue(any(a[0] == "overpass" for a, _ in self.eventos))

    def test_get_no_existe(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/overpass", timeout=5)
        self.assertEqual(cm.exception.code, 404)                      # solo se acepta POST


if __name__ == "__main__":
    unittest.main(verbosity=1)
