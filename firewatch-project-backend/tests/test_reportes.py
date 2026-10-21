"""Pruebas de /reportes: validación, permisos por rol, privacidad del contacto y límite de envíos."""
import io
import json

import pytest
from fastapi.testclient import TestClient

from app import reportes
from app.main import create_app

JPG = b"\xff\xd8\xff\xe0" + b"0" * 200
BASE = {"origen": "USUARIO", "descripcion": "Humo en el cerro detrás de la plaza", "gps": [-33.52, -70.58]}


def crear(tmp_path, monkeypatch, **extra_env):
    monkeypatch.setenv("ADMIN_PASSWORD", "clave-admin-123")
    for k, v in extra_env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.chdir(tmp_path)  # que load_env no lea un .env real
    app = create_app(str(tmp_path / "t.db"), str(tmp_path / "fotos"))
    c = TestClient(app)
    tok = c.post("/login", json={"username": "admin", "password": "clave-admin-123"}).json()["token"]
    return c, {"Authorization": f"Bearer {tok}"}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    return crear(tmp_path, monkeypatch, FIREWATCH_REPORTES_MAX="3")


def con_foto(c, meta, img=JPG, headers=None):
    return c.post("/reportes", data={"metadata": json.dumps(meta)},
                  files={"imagen": ("f.jpg", io.BytesIO(img), "image/jpeg")}, headers=headers or {})


def usuario(c, admin, nombre, rol):
    c.post("/usuarios", json={"username": nombre, "password": "clave-larga-1", "nombre_completo": nombre, "rol": rol},
           headers=admin)
    tok = c.post("/login", json={"username": nombre, "password": "clave-larga-1"}).json()["token"]
    return {"Authorization": f"Bearer {tok}"}


# ── envío público ──────────────────────────────────────────────────────
def test_ciudadano_envia_sin_login_y_queda_sin_verificar(env):
    c, admin = env
    r = c.post("/reportes", json={**BASE, "contacto": "  +56 9 1234 5678 "})
    assert r.status_code == 201 and r.json()["estado"] == "SIN_VERIFICAR"
    it = c.get("/reportes", headers=admin).json()["items"][0]
    assert it["origen"] == "USUARIO" and it["gps"] == [-33.52, -70.58]
    assert it["contacto"] == "+56 9 1234 5678"          # se recorta
    assert it["id_reporte"] == r.json()["id_reporte"]
    assert "ip" not in it and "usuario" not in it       # datos internos no salen por la API


def test_listar_exige_sesion(env):
    c, _ = env
    assert c.get("/reportes").status_code == 401
    assert c.get("/reportes", headers={"Authorization": "Bearer falso"}).status_code == 401


def test_token_invalido_en_el_envio_no_se_trata_como_anonimo(env):
    c, _ = env
    assert c.post("/reportes", json=BASE, headers={"Authorization": "Bearer falso"}).status_code == 401


def test_bombero_exige_sesion_y_queda_pendiente(env):
    c, admin = env
    assert c.post("/reportes", json={**BASE, "origen": "BOMBERO"}).status_code == 401
    b = usuario(c, admin, "bombero1", "BOMBERO")
    r = c.post("/reportes", json={**BASE, "origen": "BOMBERO"}, headers=b)
    assert r.status_code == 201 and r.json()["estado"] == "PENDIENTE"


def test_contacto_oculto_para_bomberos(env):
    c, admin = env
    c.post("/reportes", json={**BASE, "contacto": "ana@correo.cl"})
    b = usuario(c, admin, "bombero1", "BOMBERO")
    assert c.get("/reportes", headers=b).json()["items"][0]["contacto"] is None
    assert c.get("/reportes", headers=admin).json()["items"][0]["contacto"] == "ana@correo.cl"
    rid = c.get("/reportes", headers=admin).json()["items"][0]["id_reporte"]
    assert c.get(f"/reportes/{rid}", headers=b).json()["contacto"] is None


# ── validación ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("cambio", [
    {"origen": "OTRO"},
    {"descripcion": "hi"},
    {"descripcion": "x" * 1001},
    {"descripcion": "\x00\x01\x02\x03\x04\x05"},       # solo caracteres de control
    {"gps": [-33.5]},
    {"gps": [-133.5, -70.5]},
    {"gps": [-33.5, -270.5]},
    {"gps": None},
    {"contacto": "c" * 121},
])
def test_validaciones_rechazan(env, cambio):
    c, _ = env
    r = c.post("/reportes", json={**BASE, **cambio})
    assert r.status_code == 422 and r.json()["error"].startswith("dato inválido")


def test_gps_es_obligatorio(env):
    c, _ = env
    sin = {k: v for k, v in BASE.items() if k != "gps"}
    assert c.post("/reportes", json=sin).status_code == 422


def test_cuerpo_invalido_y_tipo_no_soportado(env):
    c, _ = env
    assert c.post("/reportes", content=b"{no es json", headers={"Content-Type": "application/json"}).status_code == 422
    assert c.post("/reportes", content=b"hola", headers={"Content-Type": "text/plain"}).status_code == 415
    assert c.post("/reportes", json=[1, 2, 3]).status_code == 422


def test_limpia_caracteres_de_control(env):
    c, admin = env
    c.post("/reportes", json={**BASE, "descripcion": "Humo\x00 negro\x07 al fondo"})
    assert c.get("/reportes", headers=admin).json()["items"][0]["descripcion"] == "Humo negro al fondo"


def test_el_texto_se_guarda_tal_cual_sin_ejecutarse(env):
    """La API devuelve el texto como dato; quien lo muestre debe escaparlo (ver README)."""
    c, admin = env
    c.post("/reportes", json={**BASE, "descripcion": "<script>alert(1)</script> humo"})
    assert "<script>" in c.get("/reportes", headers=admin).json()["items"][0]["descripcion"]


# ── imagen ─────────────────────────────────────────────────────────────
def test_reporte_con_foto(env):
    c, admin = env
    r = con_foto(c, BASE)
    assert r.status_code == 201
    rid = r.json()["id_reporte"]
    it = c.get(f"/reportes/{rid}", headers=admin).json()
    assert it["imagen_asociada"] == f"{rid}.jpg"
    img = c.get(f"/reportes/{rid}/imagen", headers=admin)
    assert img.status_code == 200 and img.content == JPG
    assert c.get(f"/reportes/{rid}/imagen").status_code == 401   # la foto no es pública


def test_foto_invalida_o_muy_grande(env):
    c, admin = env
    assert con_foto(c, BASE, img=b"no es jpg").status_code == 422
    assert con_foto(c, BASE, img=JPG + b"0" * reportes.MAX_IMG_REPORTE).status_code == 413
    assert c.get("/reportes", headers=admin).json()["total"] == 0   # lo rechazado no se guarda


def test_cuerpo_gigante_se_rechaza_antes_de_leerlo(env):
    c, _ = env
    r = c.post("/reportes", content=b"x", headers={"Content-Type": "application/json",
                                                   "Content-Length": str(10 * 1024 * 1024)})
    assert r.status_code in (413, 400)


def test_multipart_con_metadata_mala(env):
    c, _ = env
    r = c.post("/reportes", data={"metadata": "{no"}, files={"imagen": ("f.jpg", io.BytesIO(JPG), "image/jpeg")})
    assert r.status_code == 422


def test_reporte_sin_foto_no_tiene_imagen(env):
    c, admin = env
    rid = c.post("/reportes", json=BASE).json()["id_reporte"]
    assert c.get(f"/reportes/{rid}", headers=admin).json()["imagen_asociada"] is None
    assert c.get(f"/reportes/{rid}/imagen", headers=admin).status_code == 404
    assert c.get("/reportes/no_existe", headers=admin).status_code == 404


# ── filtros ────────────────────────────────────────────────────────────
def test_filtros_y_paginacion(env):
    c, admin = env
    b = usuario(c, admin, "bombero1", "BOMBERO")
    c.post("/reportes", json=BASE)
    c.post("/reportes", json={**BASE, "origen": "BOMBERO"}, headers=b)
    assert c.get("/reportes", headers=admin).json()["total"] == 2
    assert c.get("/reportes?origen=BOMBERO", headers=admin).json()["total"] == 1
    assert c.get("/reportes?estado=SIN_VERIFICAR", headers=admin).json()["total"] == 1
    assert len(c.get("/reportes?limit=1", headers=admin).json()["items"]) == 1
    assert c.get("/reportes?limit=0", headers=admin).status_code == 422
    assert c.get("/reportes?origen=XX", headers=admin).status_code == 422


# ── límite de envíos ───────────────────────────────────────────────────
def test_limite_por_ip_responde_429_con_retry_after(env):
    c, _ = env                                   # límite: 3 por ventana
    for _ in range(3):
        assert c.post("/reportes", json=BASE).status_code == 201
    r = c.post("/reportes", json=BASE)
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) > 0 and "demasiados" in r.json()["error"]


def test_el_limite_no_afecta_a_quien_tiene_sesion(env):
    c, admin = env
    b = usuario(c, admin, "bombero1", "BOMBERO")
    for _ in range(6):
        assert c.post("/reportes", json={**BASE, "origen": "BOMBERO"}, headers=b).status_code == 201


def test_x_forwarded_for_se_ignora_por_defecto(env):
    """Sin proxy propio, cambiar X-Forwarded-For no permite saltarse el límite."""
    c, _ = env
    for i in range(3):
        assert c.post("/reportes", json=BASE, headers={"X-Forwarded-For": f"10.0.0.{i}"}).status_code == 201
    assert c.post("/reportes", json=BASE, headers={"X-Forwarded-For": "10.0.0.99"}).status_code == 429


def test_con_proxy_de_confianza_se_usa_la_ultima_ip(tmp_path, monkeypatch):
    c, _ = crear(tmp_path, monkeypatch, FIREWATCH_REPORTES_MAX="2", FIREWATCH_PROXY_CONFIAR="1")
    # el cliente puede inventar la primera IP; la última la agrega el proxy y es la que cuenta
    for i in range(2):
        h = {"X-Forwarded-For": f"1.1.1.{i}, 9.9.9.9"}
        assert c.post("/reportes", json=BASE, headers=h).status_code == 201
    assert c.post("/reportes", json=BASE, headers={"X-Forwarded-For": "2.2.2.2, 9.9.9.9"}).status_code == 429
    assert c.post("/reportes", json=BASE, headers={"X-Forwarded-For": "2.2.2.2, 8.8.8.8"}).status_code == 201


def test_tope_global_frena_a_quien_rota_de_ip(tmp_path, monkeypatch):
    c, _ = crear(tmp_path, monkeypatch, FIREWATCH_REPORTES_MAX="5", FIREWATCH_REPORTES_MAX_GLOBAL="4",
                 FIREWATCH_PROXY_CONFIAR="1")
    for i in range(4):
        assert c.post("/reportes", json=BASE, headers={"X-Forwarded-For": f"7.7.7.{i}"}).status_code == 201
    assert c.post("/reportes", json=BASE, headers={"X-Forwarded-For": "7.7.7.200"}).status_code == 429


# ── el limitador, sin servidor ─────────────────────────────────────────
def test_limitador_ventana_deslizante():
    lim = reportes.LimiteEnvios(2, 100, 10)
    assert lim.intentar("a", 0) is None and lim.intentar("a", 10) is None
    espera = lim.intentar("a", 20)
    assert espera is not None and 80 <= espera <= 82       # el primero vence a los 100 s
    assert lim.intentar("b", 20) is None                    # otra IP no se ve afectada
    assert lim.intentar("a", 101) is None                   # ya venció el primero


def test_limitador_no_crece_sin_limite():
    lim = reportes.LimiteEnvios(1, 10, 10**9)
    for i in range(6000):
        lim.intentar(f"ip{i}", 0)
    lim.intentar("nueva", 100)                              # dispara la limpieza (todo lo anterior venció)
    assert len(lim._por_clave) < 10
