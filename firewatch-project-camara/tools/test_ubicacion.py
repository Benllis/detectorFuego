"""Pruebas de camera_location sin red ni Windows:  python tools/test_ubicacion.py"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import camera_location as cl


def lanza(fn, *a):
    try:
        fn(*a)
    except cl.UbicacionError as e:
        return str(e)
    raise AssertionError("debió lanzar UbicacionError")


def test_windows():
    r = cl.interpretar_salida_windows('{"lat":-33.0245,"lon":-71.5518,"acc":120.0}')
    assert r["fuente"] == "windows" and abs(r["lat"] + 33.0245) < 1e-9 and "120 m" in r["detalle"]
    assert r["aproximada"] is False
    r = cl.interpretar_salida_windows('{"lat":-33.53,"lon":-70.64,"acc":50000}')   # lo que dio tu PC: 50 km
    assert r["aproximada"] is True and "50 km" in r["detalle"] and "dirección" in r["detalle"]
    assert "Privacidad" in lanza(cl.interpretar_salida_windows, '{"error":"denegado"}')
    assert "no pudo estimar" in lanza(cl.interpretar_salida_windows, '{"error":"desconocida"}')
    assert "legible" in lanza(cl.interpretar_salida_windows, "basura")
    lanza(cl.interpretar_salida_windows, "")
    lanza(cl.interpretar_salida_windows, '{"lat":0,"lon":0,"acc":1}')          # 0,0 = "sin dato"
    lanza(cl.interpretar_salida_windows, '{"lat":123,"lon":-71,"acc":1}')      # fuera de rango
    r = cl.interpretar_salida_windows("aviso\n" + '{"lat":-33.0,"lon":-71.5,"acc":"NaN"}')  # ruido antes del JSON
    assert r["detalle"] == "Ubicación de Windows (Wi-Fi)"


def test_direccion(monkeypatch=None):
    orig = cl._get_json
    try:
        cl._get_json = lambda url, timeout=10: [{"lat": "-33.0153", "lon": "-71.5500", "display_name": "Av. Libertad, Viña del Mar, Chile"}]
        r = cl.buscar_direccion("Av. Libertad 123, Viña del Mar")
        assert r["fuente"] == "direccion" and abs(r["lon"] + 71.55) < 1e-9 and r["aproximada"] is True  # sin house_number
        cl._get_json = lambda url, timeout=10: [{"lat": "-33.0", "lon": "-71.5", "display_name": "X", "address": {"house_number": "123"}}]
        assert cl.buscar_direccion("Av. Libertad 123")["aproximada"] is False
        cl._get_json = lambda url, timeout=10: [{"lat": "-33.0", "lon": "-71.5", "display_name": "X", "address": {}}]
        assert cl.buscar_direccion("Viña del Mar centro")["aproximada"] is False   # sin número pedido, no hay nada que avisar
        cl._get_json = lambda url, timeout=10: [{"lat": "-33.0153", "lon": "-71.5500", "display_name": "Av. Libertad, Viña del Mar, Chile"}]
        cl._get_json = lambda url, timeout=10: []
        assert "No encontré" in lanza(cl.buscar_direccion, "calle que no existe 999")
        assert "dirección completa" in lanza(cl.buscar_direccion, "ab")
        def boom(url, timeout=10): raise OSError("sin red")
        cl._get_json = boom
        assert "internet" in lanza(cl.buscar_direccion, "Av. Libertad 123")
        urls = []
        cl._get_json = lambda url, timeout=10: urls.append(url) or []
        lanza(cl.buscar_direccion, "Av. Libertad 123, Viña del Mar")
        assert "countrycodes=cl" in urls[0] and "Vi%C3%B1a" in urls[0]
    finally:
        cl._get_json = orig


def test_ip():
    orig = cl._get_json
    try:
        cl._get_json = lambda url, timeout=10: {"success": True, "latitude": -33.45, "longitude": -70.66, "city": "Santiago", "region": "RM"}
        r = cl.ubicacion_por_ip()
        assert r["fuente"] == "ip" and "Santiago" in r["detalle"]
        cl._get_json = lambda url, timeout=10: {"success": False}
        lanza(cl.ubicacion_por_ip)
    finally:
        cl._get_json = orig


def test_url_mapa():
    assert "mlat=-33.015300" in cl.url_mapa(-33.0153, -71.55)


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f(); print("OK ", n)
