"""Pruebas del endpoint POST /propagacion/m2 y de sus piezas (usa el modelo real en models/m2)."""
import shutil
import time
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import fire_features as ff
from app import m2
from app.main import create_app

LAT, LON = -33.05, -71.60


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "clave-admin-123")
    monkeypatch.delenv("FIREWATCH_M2_MODELO", raising=False)
    monkeypatch.chdir(tmp_path)
    m2._CARGA.update(modelo=None, meta=None, ruta=None)
    c = TestClient(create_app(str(tmp_path / "t.db"), str(tmp_path / "fotos")))
    tok = c.post("/login", json={"username": "admin", "password": "clave-admin-123"}).json()["token"]
    return c, {"Authorization": f"Bearer {tok}"}


def payload(**kw):
    n = 16
    d = {"lat": LAT, "lon": LON, "focos": [[LAT, LON], [LAT + 0.01, LON]],
         "viento_kmh": 20, "viento_dir_deg": 270, "temp_max": 28, "humedad": 35,
         "cobertura": {"n": n, "valores": [30.0] * (n * n)},
         "elevacion": {"n": n, "valores": [100.0 + 5 * (i % n) for i in range(n * n)]}}
    d.update(kw)
    return d


def test_prediccion_basica(env):
    c, h = env
    r = c.post("/propagacion/m2", json=payload(), headers=h)
    assert r.status_code == 200, r.text
    d = r.json()
    niv = np.array(d["grilla"]["niveles"]).reshape(64, 64)
    assert d["grilla"]["filas"] == 64 and niv.shape == (64, 64)
    assert set(np.unique(niv)) <= {0, 1, 2, 3}
    assert niv[32, 32] >= 1, "la celda del foco debe salir en la zona probable"
    assert niv[ff.RING].sum() == 0, "el anillo del borde nunca se marca"
    assert d["focos_usados"] == 2 and d["advertencias"] == []
    assert [x["nivel"] for x in d["niveles"]] == [1, 2, 3]
    assert d["niveles"][0]["celdas"] > 0 and d["niveles"][2]["alcance_km"] >= d["niveles"][0]["alcance_km"]
    assert "porcentaje" in d["nota"] or "no un porcentaje" in d["nota"]


def test_zona_probable_se_concentra_cerca_del_foco(env):
    c, h = env
    d = c.post("/propagacion/m2", json=payload(), headers=h).json()
    niv = np.array(d["grilla"]["niveles"]).reshape(64, 64)
    rr, cc = np.nonzero(niv)
    dist = np.hypot(rr - 32, cc - 32)
    assert dist.max() < 25, "el modelo no debe marcar celdas a decenas de km del foco"
    assert (niv == 1).sum() <= (niv > 0).sum()


def test_terreno_montanoso_no_pinta_medio_parche(env):
    """Con mucha altura la salida del modelo sube lejos del foco; los topes deben acotar la zona pintada."""
    c, h = env
    n = 16
    alto = {"n": n, "valores": [200.0 + 130.0 * (i % n) for i in range(n * n)]}  # sube ~2000 m hacia el este
    d = c.post("/propagacion/m2", json=payload(elevacion=alto), headers=h).json()
    niv = np.array(d["grilla"]["niveles"])
    topes = [(n_, tope) for n_, _, _, tope in m2.NIVELES]
    for nivel, tope in topes:
        assert ((niv > 0) & (niv <= nivel)).sum() <= tope
    assert (niv > 0).sum() <= 250


def test_niveles_son_anidados_por_ranking(env):
    c, h = env
    d = c.post("/propagacion/m2", json=payload(), headers=h).json()
    cel = [x["celdas"] for x in d["niveles"]]
    assert cel[0] <= 30 and cel[0] + cel[1] <= 100 and sum(cel) <= 250


def test_bbox_coincide_con_la_malla(env):
    c, h = env
    b = c.post("/propagacion/m2", json=payload(), headers=h).json()["parche"]["bbox"]
    assert round((b["norte"] - b["sur"]) / 0.01) == 64 and round((b["este"] - b["oeste"]) / 0.01) == 64
    assert b["sur"] < LAT < b["norte"] and b["oeste"] < LON < b["este"]
    # el centro del parche cae dentro de la celda (fila 32, columna 32)
    assert abs((b["sur"] + 32.5 * 0.01) - LAT) < 1e-6 and abs((b["oeste"] + 32.5 * 0.01) - LON) < 1e-6


def test_sin_token_401(env):
    c, _ = env
    assert c.post("/propagacion/m2", json=payload()).status_code == 401


def test_bombero_tambien_puede_consultar(env):
    c, h = env
    assert c.post("/usuarios", json={"username": "bomb1", "password": "clave-bombero-1", "nombre_completo": "B",
                                     "rol": "BOMBERO"}, headers=h).status_code == 201
    tok = c.post("/login", json={"username": "bomb1", "password": "clave-bombero-1"}).json()["token"]
    assert c.post("/propagacion/m2", json=payload(), headers={"Authorization": f"Bearer {tok}"}).status_code == 200


def test_focos_fuera_del_parche_422(env):
    c, h = env
    r = c.post("/propagacion/m2", json=payload(focos=[[LAT + 2, LON]]), headers=h)
    assert r.status_code == 422 and "ningún foco" in r.json()["error"]


def test_foco_solo_en_el_borde_no_cuenta(env):
    c, h = env
    # fila 0 del parche = 32 celdas al sur del centro
    r = c.post("/propagacion/m2", json=payload(focos=[[LAT - 0.32, LON]]), headers=h)
    assert r.status_code == 422


def test_sin_focos_422(env):
    c, h = env
    assert c.post("/propagacion/m2", json=payload(focos=[]), headers=h).status_code == 422


def test_datos_invalidos_422(env):
    c, h = env
    malo = payload(cobertura={"n": 16, "valores": [30.0] * 10})
    assert c.post("/propagacion/m2", json=malo, headers=h).status_code == 422
    assert c.post("/propagacion/m2", json=payload(humedad=150), headers=h).status_code == 422
    assert c.post("/propagacion/m2", json=payload(focos=[[1.0]]), headers=h).status_code == 422
    assert c.post("/propagacion/m2", json=payload(focos=[[LAT, LON]] * 501), headers=h).status_code == 422


def test_fuera_de_zona_avisa_pero_calcula(env):
    c, h = env
    lat, lon = -23.65, -70.40  # Antofagasta
    r = c.post("/propagacion/m2", json=payload(lat=lat, lon=lon, focos=[[lat, lon]]), headers=h)
    assert r.status_code == 200
    assert any("fuera de la zona" in a for a in r.json()["advertencias"])


def test_sin_elevacion_avisa_y_calcula(env):
    c, h = env
    p = payload(); p.pop("elevacion")
    r = c.post("/propagacion/m2", json=p, headers=h)
    assert r.status_code == 200 and any("terreno plano" in a for a in r.json()["advertencias"])


def test_vegetacion_desconocida_avisa(env):
    c, h = env
    p = payload(cobertura={"n": 4, "valores": [None] * 16})
    r = c.post("/propagacion/m2", json=p, headers=h)
    assert r.status_code == 200 and any("Sin tipo de vegetación" in a for a in r.json()["advertencias"])


def test_direccion_360_igual_a_0(env):
    c, h = env
    a = c.post("/propagacion/m2", json=payload(viento_dir_deg=0), headers=h).json()["grilla"]["niveles"]
    b = c.post("/propagacion/m2", json=payload(viento_dir_deg=360), headers=h).json()["grilla"]["niveles"]
    assert a == b


def test_mas_focos_no_achican_la_zona(env):
    c, h = env
    uno = c.post("/propagacion/m2", json=payload(focos=[[LAT, LON]]), headers=h).json()
    varios = c.post("/propagacion/m2", json=payload(focos=[[LAT + i * 0.01, LON + j * 0.01]
                                                           for i in range(-2, 3) for j in range(-2, 3)]),
                    headers=h).json()
    assert sum(x["celdas"] for x in varios["niveles"]) > sum(x["celdas"] for x in uno["niveles"])


def test_responde_rapido(env):
    c, h = env
    c.post("/propagacion/m2", json=payload(), headers=h)  # carga del modelo
    t = time.time()
    assert c.post("/propagacion/m2", json=payload(), headers=h).status_code == 200
    assert time.time() - t < 2.0


# ── instalación del modelo ────────────────────────────────────────────────
def test_modelo_ausente_503(env, monkeypatch, tmp_path):
    c, h = env
    monkeypatch.setenv("FIREWATCH_M2_MODELO", str(tmp_path / "no_existe.joblib"))
    r = c.post("/propagacion/m2", json=payload(), headers=h)
    assert r.status_code == 503 and "no está instalado" in r.json()["error"]


def test_modelo_alterado_503(env, monkeypatch, tmp_path):
    c, h = env
    src = m2.ruta_modelo()
    dst = tmp_path / "m.joblib"
    shutil.copy(src, dst)
    shutil.copy(src.with_name(src.name + ".sha256"), tmp_path / "m.joblib.sha256")
    with open(dst, "ab") as f:
        f.write(b"x")  # un byte de más
    monkeypatch.setenv("FIREWATCH_M2_MODELO", str(dst))
    r = c.post("/propagacion/m2", json=payload(), headers=h)
    assert r.status_code == 503 and "SHA-256" in r.json()["error"]


def test_modelo_sin_sha_503(env, monkeypatch, tmp_path):
    c, h = env
    dst = tmp_path / "m.joblib"
    shutil.copy(m2.ruta_modelo(), dst)
    monkeypatch.setenv("FIREWATCH_M2_MODELO", str(dst))
    assert c.post("/propagacion/m2", json=payload(), headers=h).status_code == 503


# ── piezas ────────────────────────────────────────────────────────────────
def test_clases_a_malla_cuadrantes():
    g = m2.Grilla(n=2, valores=[10, 20, 30, 40])  # fila 0 = sur: (sur-oeste, sur-este, norte-oeste, norte-este)
    m = m2.clases_a_malla(g)
    assert m.shape == (64, 64)
    assert m[0, 0] == 10 and m[0, 63] == 20 and m[63, 0] == 30 and m[63, 63] == 40
    assert m[31, 31] == 10 and m[32, 32] == 40


def test_altura_a_malla_constante_y_pendiente():
    z, hay = m2.altura_a_malla(m2.Grilla(n=4, valores=[50.0] * 16))
    assert hay and np.allclose(z, 50.0)
    g = m2.Grilla(n=4, valores=[float(c * 100) for r in range(4) for c in range(4)])  # sube hacia el este
    z, _ = m2.altura_a_malla(g)
    assert z[10, 5] < z[10, 60] and np.all(np.diff(z[10]) >= -1e-3)
    _, hay = m2.altura_a_malla(m2.Grilla(n=2, valores=[None] * 4))
    assert not hay


def test_celda_del_modelo_coincide_con_las_variables_de_entrenamiento():
    """La matriz de entrada sigue el orden exacto de columnas con que se entrenó M2."""
    meta = __import__("json").loads((m2.ruta_modelo().with_suffix(".json")).read_text(encoding="utf-8"))
    assert meta["variables_en_orden"] == ff.M2_VARIABLES
    prev = np.zeros((64, 64), np.float32); prev[32, 32] = 1
    X = ff.build_m2_matrix(np.zeros((64, 64)), np.full((64, 64), 0.5), prev, 10, 90, 25, 40, -33.0)
    assert X.shape == (4096, 20) and X[32 * 64 + 32, 0] == 1.0 and X[32 * 64 + 32, 8] == 0.0
