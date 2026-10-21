"""
Variables del modelo M2 calculadas EN VIVO (para el backend). Replica, sin Earth Engine ni nada del notebook,
las variables con las que se entrenó M2, con las mismas convenciones:

  * Parche de 64 x 64 celdas de 0.01 grados centrado en (lat, lon). Fila 0 = SUR, columna 0 = OESTE.
  * Los focos que caen FUERA del parche se descartan (en el dataset original quedaban pegados al borde: ese era
    el "artefacto del borde"). El anillo de 1 píxel del borde se ignora (fuego previo en 0 y salida en 0).
  * Viento en km/h; dirección meteorológica (de dónde sopla), en grados. Temperatura en °C, humedad en %.
  * Elevación en metros (en el entrenamiento: Earth Engine a ~1.1 km). Combustible 0-1 desde ESA WorldCover.

Uso:
    X = build_m2_matrix(elevation, landcover_fuel, prev_mask, wind_kmh, wind_dir_deg, temp_max, humidity, lat_center)
    p = predict_grid(model, X)          # (64, 64), anillo del borde en 0
"""
import numpy as np
from scipy.ndimage import maximum_filter, uniform_filter, distance_transform_edt

SIDE, CELL = 64, 0.01
RING = np.zeros((SIDE, SIDE), bool)
RING[0, :] = RING[-1, :] = RING[:, 0] = RING[:, -1] = True

# orden EXACTO de columnas del modelo M2 (coincide con modelo_desplegable_M2.json)
M2_VARIABLES = ["prev", "prev_max3", "prev_max7", "prev_max15", "prev_max31",
                "prev_dens7", "prev_dens15", "prev_dens31", "dist_prev", "log_n_prev",
                "elevation", "slope_deg", "aspect_sin", "aspect_cos", "landcover_fuel",
                "wind_speed", "wind_sin", "wind_cos", "temp_max", "humidity"]

# mismo mapeo que el notebook (_WORLDCOVER_FUEL_MAP); clase desconocida -> 0.3
WORLDCOVER_FUEL = {10: 0.9, 20: 0.7, 30: 0.5, 40: 0.3, 50: 0.0, 60: 0.05, 70: 0.0, 80: 0.0, 90: 0.2, 95: 0.6, 100: 0.3}


def grid_coords(lat_center, lon_center):
    off = (np.arange(SIDE) - SIDE // 2) * CELL
    return np.meshgrid(lat_center + off, lon_center + off, indexing="ij")


def rasterize_detections(lats, lons, lat_center, lon_center):
    """Máscara 64x64 con los focos DENTRO del parche (celda más cercana). Fuera del parche se descartan."""
    m = np.zeros((SIDE, SIDE), np.float32)
    r = np.rint((np.asarray(lats, float) - lat_center) / CELL).astype(int) + SIDE // 2
    c = np.rint((np.asarray(lons, float) - lon_center) / CELL).astype(int) + SIDE // 2
    ok = (r >= 0) & (r < SIDE) & (c >= 0) & (c < SIDE)
    m[r[ok], c[ok]] = 1.0
    return m


def worldcover_to_fuel(classes):
    f = np.vectorize(lambda c: WORLDCOVER_FUEL.get(int(round(c)), 0.3))(np.asarray(classes, float))
    return f.astype(np.float32)


def slope_aspect(elevation, lat_center, cell_size_deg=CELL):
    """Copia de get_slope_aspect del notebook."""
    lat_rad = np.radians(lat_center)
    m_lat = 111_320.0
    m_lon = 111_320.0 * max(np.cos(lat_rad), 1e-6)
    dz_dlat, dz_dlon = np.gradient(elevation, cell_size_deg * m_lat, cell_size_deg * m_lon)
    slope = np.degrees(np.arctan(np.sqrt(dz_dlon ** 2 + dz_dlat ** 2)))
    asp = np.arctan2(dz_dlat, -dz_dlon)
    return slope.astype(np.float32), np.sin(asp).astype(np.float32), np.cos(asp).astype(np.float32)


def proximity(prev):
    """Variables de cercanía al fuego previo (iguales a baseline_simple_v2.features)."""
    prev = (prev > 0.5).astype(np.float32)
    out = {"prev": prev}
    for k in (3, 7, 15, 31):
        out[f"prev_max{k}"] = maximum_filter(prev, size=k)
    for k in (7, 15, 31):
        out[f"prev_dens{k}"] = uniform_filter(prev, size=k, mode="constant")
    d = distance_transform_edt(prev == 0) if prev.any() else np.full(prev.shape, 99.0, np.float32)
    out["dist_prev"] = np.minimum(d, 99).astype(np.float32)
    out["log_n_prev"] = np.full(prev.shape, np.log1p(prev.sum()), np.float32)
    return out


def build_m2_matrix(elevation, landcover_fuel, prev_mask, wind_kmh, wind_dir_deg, temp_max, humidity, lat_center):
    """(4096, 20) float32 en el orden de M2_VARIABLES."""
    prev = np.array(prev_mask, np.float32).reshape(SIDE, SIDE)
    prev[RING] = 0
    el = np.nan_to_num(np.asarray(elevation, np.float32).reshape(SIDE, SIDE))
    slope, asin, acos = slope_aspect(el, lat_center)
    f = proximity(prev)
    wd = np.deg2rad(float(wind_dir_deg))
    full = lambda v: np.full((SIDE, SIDE), v, np.float32)
    f.update({"elevation": el, "slope_deg": slope, "aspect_sin": asin, "aspect_cos": acos,
              "landcover_fuel": np.asarray(landcover_fuel, np.float32).reshape(SIDE, SIDE),
              "wind_speed": full(wind_kmh), "wind_sin": full(np.sin(wd)), "wind_cos": full(np.cos(wd)),
              "temp_max": full(temp_max), "humidity": full(humidity)})
    return np.stack([f[k].reshape(-1) for k in M2_VARIABLES], axis=1).astype(np.float32)


def predict_grid(model, matrix):
    p = model.predict_proba(matrix)[:, 1].reshape(SIDE, SIDE).astype(np.float32)
    p[RING] = 0.0
    return p
