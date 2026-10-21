import io
import json

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

JPG = b"\xff\xd8\xff\xe0" + b"0" * 200


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "clave-admin-123")
    monkeypatch.chdir(tmp_path)  # que load_env no lea un .env real
    app = create_app(str(tmp_path / "t.db"), str(tmp_path / "fotos"))
    c = TestClient(app)
    tok = c.post("/login", json={"username": "admin", "password": "clave-admin-123"}).json()["token"]
    admin = {"Authorization": f"Bearer {tok}"}
    cam = c.post("/camaras", json={"camara_id": "cam_1", "nombre": "Webcam", "gps": [-33.5, -70.5]}, headers=admin)
    return c, admin, cam.json()["clave"]


def meta(id_="cam_1_fire_1", tipo="FIRE", cam="cam_1"):
    return {"id_alerta": id_, "camara_id": cam, "camara_nombre": "Webcam", "ubicacion": "Sector B",
            "gps": [-33.5, -70.5], "tipo": tipo, "timestamp": 1.0, "fecha_hora": "2026-10-12 15:30:45",
            "fuego_detectado": tipo in ("FIRE", "CRITICAL"), "humo_detectado": tipo in ("SMOKE", "CRITICAL"),
            "confianza": 0.8}


def enviar(c, clave, m, img=JPG):
    return c.post("/alertas", data={"metadata": json.dumps(m)},
                  files={"imagen": ("a.jpg", io.BytesIO(img), "image/jpeg")}, headers={"X-Camera-Key": clave})


def test_salud(env):
    c, _, _ = env
    assert c.get("/salud").json() == {"ok": True, "version": "0.1"}


def test_raiz_redirige_a_docs(env):
    c, _, _ = env
    r = c.get("/", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"] == "/docs"


def test_login_incorrecto_y_bloqueo(env):
    c, _, _ = env
    for _ in range(5):
        assert c.post("/login", json={"username": "admin", "password": "mala"}).status_code == 401
    assert c.post("/login", json={"username": "admin", "password": "clave-admin-123"}).status_code == 429


def test_sin_token_401(env):
    c, _, _ = env
    assert c.get("/alertas").status_code == 401
    assert c.get("/alertas", headers={"Authorization": "Bearer falso"}).status_code == 401


def test_flujo_alerta_completo(env):
    c, admin, clave = env
    r = enviar(c, clave, meta())
    assert r.status_code == 201 and r.json()["estado"] == "PENDIENTE"
    lista = c.get("/alertas?estado=PENDIENTE", headers=admin).json()
    assert lista["total"] == 1
    it = lista["items"][0]
    assert it["gps"] == [-33.5, -70.5] and it["tipo"] == "FIRE" and it["fuego_detectado"] is True
    assert it["imagen_asociada"] == "cam_1_fire_1.jpg"
    img = c.get("/alertas/cam_1_fire_1/imagen", headers=admin)
    assert img.status_code == 200 and img.content == JPG
    ok = c.patch("/alertas/cam_1_fire_1", json={"estado": "CONFIRMADA", "motivo_descarte": "ignorado"},
                 headers=admin).json()
    assert ok["estado"] == "CONFIRMADA" and ok["operador_resolucion"] == "admin"
    assert ok["motivo_descarte"] is None
    assert c.get("/alertas?estado=PENDIENTE", headers=admin).json()["total"] == 0


def test_reintento_no_duplica(env):
    c, admin, clave = env
    assert enviar(c, clave, meta()).status_code == 201
    assert enviar(c, clave, meta()).status_code == 200
    assert c.get("/alertas", headers=admin).json()["total"] == 1


def test_falso_positivo_exige_motivo(env):
    c, admin, clave = env
    enviar(c, clave, meta())
    r = c.patch("/alertas/cam_1_fire_1", json={"estado": "FALSO_POSITIVO"}, headers=admin)
    assert r.status_code == 422 and "motivo" in r.json()["error"]
    r = c.patch("/alertas/cam_1_fire_1", json={"estado": "FALSO_POSITIVO", "motivo_descarte": "Reflejo"}, headers=admin)
    assert r.json()["motivo_descarte"] == "Reflejo"


def test_no_detection_sin_gps(env):
    c, admin, clave = env
    m = meta("cam_1_nd_1", "NO_DETECTION"); m["gps"] = None
    assert enviar(c, clave, m).status_code == 201
    assert c.get("/alertas/cam_1_nd_1", headers=admin).json()["gps"] is None


def test_clave_de_camara(env):
    c, _, clave = env
    assert enviar(c, "mala", meta()).status_code == 401
    r = c.post("/alertas", data={"metadata": json.dumps(meta())},
               files={"imagen": ("a.jpg", io.BytesIO(JPG), "image/jpeg")})
    assert r.status_code == 401
    # una cámara no puede enviar alertas a nombre de otra
    assert enviar(c, clave, meta(cam="cam_2")).status_code == 403


def test_validaciones(env):
    c, _, clave = env
    assert enviar(c, clave, meta(tipo="OTRO")).status_code == 422
    assert enviar(c, clave, meta(), img=b"no es jpg").status_code == 422
    assert enviar(c, clave, meta(id_="../../etc/passwd")).status_code == 422
    assert enviar(c, clave, meta(), img=JPG + b"0" * (5 * 1024 * 1024)).status_code == 413


def test_roles(env):
    c, admin, clave = env
    assert c.post("/usuarios", json={"username": "bombero1", "password": "bomb-clave-1",
                                     "nombre_completo": "B Uno", "rol": "BOMBERO"}, headers=admin).status_code == 201
    tok = c.post("/login", json={"username": "bombero1", "password": "bomb-clave-1"}).json()["token"]
    b = {"Authorization": f"Bearer {tok}"}
    enviar(c, clave, meta())
    assert c.get("/alertas", headers=b).status_code == 200            # puede ver
    assert c.patch("/alertas/cam_1_fire_1", json={"estado": "CONFIRMADA"}, headers=b).status_code == 403
    assert c.post("/camaras", json={"nombre": "x"}, headers=b).status_code == 403
    assert c.post("/usuarios", json={"username": "otro", "password": "12345678",
                                     "nombre_completo": "x"}, headers=b).status_code == 403


def test_camaras_no_exponen_clave(env):
    c, admin, clave = env
    lista = c.get("/camaras", headers=admin).json()
    assert lista[0]["camara_id"] == "cam_1" and lista[0]["gps"] == [-33.5, -70.5]
    assert "clave" not in json.dumps(lista) and clave not in json.dumps(lista)
    r = c.put("/camaras/cam_1", json={"nombre": "Webcam 2", "ubicacion": "Techo", "gps": [-33.4, -70.4]}, headers=admin)
    assert r.json()["nombre"] == "Webcam 2"


def test_cors_permite_al_mapa_y_no_a_otros(env):
    c, admin, _ = env
    ok = c.options("/alertas", headers={"Origin": "http://localhost:8080", "Access-Control-Request-Method": "GET",
                                        "Access-Control-Request-Headers": "authorization"})
    assert ok.status_code == 200
    assert ok.headers["access-control-allow-origin"] == "http://localhost:8080"
    r = c.get("/alertas", headers={**admin, "Origin": "http://localhost:8080"})
    assert r.headers["access-control-allow-origin"] == "http://localhost:8080"
    otro = c.get("/alertas", headers={**admin, "Origin": "http://sitio-malo.example"})
    assert "access-control-allow-origin" not in otro.headers


def test_cambiar_mi_clave(env):
    c, admin, _ = env
    assert c.post("/usuarios/yo/clave", json={"actual": "mala", "nueva": "otra-clave-1"}, headers=admin).status_code == 401
    assert c.post("/usuarios/yo/clave", json={"actual": "clave-admin-123", "nueva": "corta"}, headers=admin).status_code == 422
    assert c.post("/usuarios/yo/clave", json={"actual": "clave-admin-123", "nueva": "clave-admin-123"}, headers=admin).status_code == 422
    assert c.post("/usuarios/yo/clave", json={"actual": "clave-admin-123", "nueva": "otra-clave-1"}, headers=admin).status_code == 200
    assert c.get("/camaras", headers=admin).status_code == 401  # la sesión anterior se cerró
    assert c.post("/login", json={"username": "admin", "password": "clave-admin-123"}).status_code == 401
    assert c.post("/login", json={"username": "admin", "password": "otra-clave-1"}).status_code == 200
