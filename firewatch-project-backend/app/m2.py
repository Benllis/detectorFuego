"""Modelo M2 de FireWatch: «continuación de un foco ya detectado».

Qué hace: dado un foco confirmado (la alerta) y los focos recientes de alrededor, estima en qué celdas de ~1 km
de un parche de 64 x 64 celdas (≈ 71 km de lado) es más probable que haya fuego al día siguiente.
Qué NO hace: no predice incendios nuevos, no da una dirección de avance fina (el viento pesa poco en este modelo;
para eso está la simulación por reglas del mapa) y sus valores NO son probabilidades calibradas: se entrenó
con todos los positivos y solo el 5 % de los negativos, así que sirven para ORDENAR celdas, no como porcentaje.
Por eso la salida son tres niveles relativos (muy probable / probable / posible), nunca un porcentaje.

El mapa arma las entradas (clima activo, vegetación y altura gruesa) y este módulo las valida, las lleva a la
malla del entrenamiento y corre el modelo. Las variables se calculan con fire_features.py (misma lógica que el
entrenamiento; paridad comprobada con tools de Datas/verificar_features_v2.py).
"""
import hashlib
import json
import math
import os
import threading
import warnings
from pathlib import Path
from typing import Optional

import numpy as np
from pydantic import BaseModel, Field, model_validator
from scipy.ndimage import distance_transform_edt, zoom

from . import fire_features as ff

# (nivel, etiqueta, umbral de la salida del modelo, tope de celdas acumulado). Una celda entra a un nivel si
# supera el umbral Y está entre las `tope` mejor ordenadas del parche. El tope evita que, en terreno
# montañoso, el modelo pinte medio parche: la altura absoluta le sube la salida en celdas lejanas al foco.
# 30 / 100 / 250 celdas ≈ 0,8 % / 2,6 % / 6,5 % del parche (en parches con fuego previo arde ~0,4 %).
NIVELES = ((1, "muy probable", 0.10, 30), (2, "probable", 0.03, 100), (3, "posible", 0.01, 250))
ZONA_ENTRENAMIENTO = (-41.0, -32.0, -74.0, -70.0)  # lat mín, lat máx, lon mín, lon máx (entrenado en -40..-33 / -73.5..-70.5)
MAX_FOCOS = 500
M_LAT_KM, M_LON_KM_EQ = 110.574, 111.320
NOTA = ("Orden relativo de celdas, no un porcentaje: el modelo se entrenó con el 5 % de los negativos, así que "
        "sus valores están inflados. Solo vale para la continuación de un foco ya detectado.")
_CARGA = {"lock": threading.Lock(), "modelo": None, "meta": None, "ruta": None}


class Grilla(BaseModel):
    """Malla n x n que cubre TODO el parche. Fila 0 = sur, columna 0 = oeste. `null` = sin dato."""
    n: int = Field(ge=2, le=64)
    valores: list[Optional[float]]

    @model_validator(mode="after")
    def _largo(self):
        if len(self.valores) != self.n * self.n:
            raise ValueError(f"se esperaban {self.n * self.n} valores (n x n) y llegaron {len(self.valores)}")
        return self


class M2In(BaseModel):
    lat: float = Field(ge=-90, le=90, description="centro del parche (el foco de la alerta)")
    lon: float = Field(ge=-180, le=180)
    focos: list[list[float]] = Field(max_length=MAX_FOCOS, description="[[lat, lon], ...] focos previos; incluye el de la alerta")
    viento_kmh: float = Field(ge=0, le=300)
    viento_dir_deg: float = Field(ge=-360, le=720, description="meteorológica: DE DÓNDE sopla")
    temp_max: float = Field(ge=-40, le=60)
    humedad: float = Field(ge=0, le=100)
    cobertura: Grilla  # clases ESA WorldCover (10, 20, 30, 40, 50, 60, 70, 80, 90, 95, 100)
    elevacion: Optional[Grilla] = None  # metros; si falta se supone terreno plano

    @model_validator(mode="after")
    def _focos(self):
        for f in self.focos:
            if len(f) != 2 or not all(math.isfinite(x) for x in f):
                raise ValueError("cada foco debe ser [lat, lon]")
        return self


class M2NoDisponible(Exception):
    """El modelo no está instalado o no pasa la verificación de integridad (se responde 503)."""


def ruta_modelo() -> Path:
    env = os.environ.get("FIREWATCH_M2_MODELO")
    return Path(env) if env else Path(__file__).resolve().parent.parent / "models" / "m2" / "modelo_M2_final.joblib"


def cargar(ruta: Optional[Path] = None):
    """Carga el modelo una sola vez (por ruta). Verifica su SHA-256 contra el archivo .sha256 vecino: un .joblib es un
    pickle y abrir uno cambiado equivale a ejecutar código ajeno."""
    ruta = Path(ruta) if ruta else ruta_modelo()
    with _CARGA["lock"]:
        if _CARGA["modelo"] is not None and _CARGA["ruta"] == str(ruta):
            return _CARGA["modelo"], _CARGA["meta"]
        if not ruta.is_file():
            raise M2NoDisponible("el modelo M2 no está instalado en el servidor")
        sha = ruta.with_name(ruta.name + ".sha256")
        if not sha.is_file():
            raise M2NoDisponible("falta el archivo .sha256 del modelo M2")
        real = hashlib.sha256(ruta.read_bytes()).hexdigest()
        if real != sha.read_text().strip().lower():
            raise M2NoDisponible("el modelo M2 no coincide con su SHA-256 (archivo alterado o dañado)")
        import joblib  # import tardío: el resto de la API no necesita scikit-learn para arrancar
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # aviso de versión de scikit-learn: se fija en requirements.txt
            modelo = joblib.load(ruta)
        metaf = ruta.with_suffix(".json")
        meta = json.loads(metaf.read_text(encoding="utf-8")) if metaf.is_file() else {}
        if meta.get("variables_en_orden") and list(meta["variables_en_orden"]) != ff.M2_VARIABLES:
            raise M2NoDisponible("las variables del modelo no coinciden con fire_features.py")
        _CARGA.update(modelo=modelo, meta=meta, ruta=str(ruta))
        return modelo, meta


def clases_a_malla(g: Grilla) -> np.ndarray:
    """Clases n x n -> 64 x 64 por vecino más cercano (alineado a los centros de celda). `null` -> clase desconocida (0)."""
    a = np.array([0 if v is None else v for v in g.valores], np.float32).reshape(g.n, g.n)
    idx = np.minimum(((np.arange(ff.SIDE) + 0.5) * g.n / ff.SIDE).astype(int), g.n - 1)
    return a[np.ix_(idx, idx)]


def altura_a_malla(g: Grilla) -> tuple[np.ndarray, bool]:
    """Altura n x n -> 64 x 64 por interpolación bilineal. Devuelve (malla, hubo_datos). Los `null` se rellenan con el promedio."""
    v = np.array([np.nan if x is None else x for x in g.valores], np.float64).reshape(g.n, g.n)
    if not np.isfinite(v).any():
        return np.zeros((ff.SIDE, ff.SIDE), np.float32), False
    v = np.where(np.isfinite(v), v, np.nanmean(v))
    if g.n == ff.SIDE:
        return v.astype(np.float32), True
    z = zoom(v, ff.SIDE / g.n, order=1, mode="nearest", grid_mode=True)
    return z.astype(np.float32), True


def predecir(data: M2In) -> dict:
    modelo, meta = cargar()
    prev = ff.rasterize_detections([f[0] for f in data.focos], [f[1] for f in data.focos], data.lat, data.lon)
    interior = int(prev[~ff.RING].sum())
    if interior == 0:
        raise ValueError("ningún foco cae dentro del parche de 71 km alrededor del punto (el borde no cuenta)")

    advertencias = []
    la0, la1, lo0, lo1 = ZONA_ENTRENAMIENTO
    if not (la0 <= data.lat <= la1 and lo0 <= data.lon <= lo1):
        advertencias.append("El punto está fuera de la zona con la que se entrenó M2 (centro de Chile): "
                            "el resultado puede ser poco fiable.")
    cls = clases_a_malla(data.cobertura)
    fuel = ff.worldcover_to_fuel(cls)
    desconocido = float(np.mean(~np.isin(cls, list(ff.WORLDCOVER_FUEL))))
    if desconocido > 0.5:
        advertencias.append(f"Sin tipo de vegetación en el {desconocido:.0%} del parche: se supuso combustible medio ahí.")
    if data.elevacion is not None:
        elev, hay = altura_a_malla(data.elevacion)
    else:
        elev, hay = np.zeros((ff.SIDE, ff.SIDE), np.float32), False
    if not hay:
        advertencias.append("Sin altura del terreno: se supuso terreno plano (la pendiente cambia poco el resultado).")

    X = ff.build_m2_matrix(elev, fuel, prev, data.viento_kmh, data.viento_dir_deg % 360, data.temp_max,
                           data.humedad, data.lat)
    p = ff.predict_grid(modelo, X)

    niveles = np.zeros(p.shape, np.uint8)
    orden = np.argsort(-p.reshape(-1), kind="stable")
    puesto = np.empty(p.size, np.int64)
    puesto[orden] = np.arange(p.size)  # 0 = la celda mejor ordenada
    puesto = puesto.reshape(p.shape)
    for n, _, umbral, tope in reversed(NIVELES):  # del más amplio al más estricto: el más estricto pisa
        niveles[(p >= umbral) & (puesto < tope)] = n
    niveles[ff.RING] = 0

    # distancias en km desde el foco más cercano
    prev_ok = prev.copy(); prev_ok[ff.RING] = 0
    dy = M_LAT_KM * ff.CELL
    dx = M_LON_KM_EQ * math.cos(math.radians(data.lat)) * ff.CELL
    dist = distance_transform_edt(prev_ok == 0, sampling=(dy, dx))
    resumen = []
    for n, etiqueta, _, _ in NIVELES:
        m = niveles == n
        acum = (niveles > 0) & (niveles <= n)
        resumen.append({
            "nivel": n, "etiqueta": etiqueta, "celdas": int(m.sum()), "area_km2": round(float(m.sum() * dy * dx), 1),
            "alcance_km": round(float(dist[acum].max()), 1) if acum.any() else 0.0,
        })

    h = ff.CELL / 2
    c = ff.SIDE // 2
    return {
        "modelo": "M2", "version": meta.get("creado"),
        "parche": {
            "centro": [data.lat, data.lon], "celda_grados": ff.CELL, "lado": ff.SIDE,
            "bbox": {"sur": round(data.lat - c * ff.CELL - h, 6), "norte": round(data.lat + (ff.SIDE - 1 - c) * ff.CELL + h, 6),
                     "oeste": round(data.lon - c * ff.CELL - h, 6), "este": round(data.lon + (ff.SIDE - 1 - c) * ff.CELL + h, 6)},
        },
        "focos_usados": interior,
        "grilla": {"filas": ff.SIDE, "columnas": ff.SIDE, "orden": "fila 0 = sur, columna 0 = oeste",
                   "niveles": niveles.reshape(-1).tolist()},
        "niveles": resumen,
        "advertencias": advertencias,
        "nota": NOTA,
        "limites": meta.get("limites"),
    }
