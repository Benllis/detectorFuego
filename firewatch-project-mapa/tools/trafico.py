#!/usr/bin/env python3
"""
FireWatch · Mapa — foto del tráfico guardada en el archivo (SQLite).

Descarga los tiles VECTORIALES de flujo de TomTom que cubren el área del mapa, los decodifica
(formato Mapbox Vector Tile, sin librerías externas) y guarda, para ese instante:
  · traffic_segments: cada tramo de vía (tipo, nivel de tráfico 0-1, punto medio, largo)
  · traffic_summary : km y nivel medio por tipo de vía (autopista, principal…)

Uso (desde recolector.py):
  python tools/recolector.py --trafico                 una foto
  python tools/recolector.py --trafico-cada 30         una foto cada 30 min
  opciones: --zoom 11  --detalle principales|secundarias|todas  --conservar-dias 30

Cuota: cada foto pide N tiles (ver el mensaje al correr). El plan gratis de TomTom da ~200.000
tiles al mes y el mapa abierto también gasta; con zoom 11 (~16 tiles por foto) y una foto cada 30 min son ~23.000/mes.
Nada de esto se probó aún contra los tiles reales: los nombres de propiedades (road_type,
traffic_level) salen de la documentación de TomTom.
"""
import gzip
import math
import time

try:
    import storage
except ImportError:  # pragma: no cover  (se ejecuta como tools/trafico.py)
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import storage

LAYER = "Traffic flow"

# Mismas clases que js/routing.js (fwTrafficClass).
TYPE_NAMES = {
    "motorway": "autopista", "international road": "troncal", "major road": "principal",
    "secondary road": "secundaria", "connecting road": "colectora", "major local road": "colectora",
    "local road": "calle", "minor local road": "calle",
}
CATEGORY_NAMES = {
    "motorway": "autopista", "trunk": "troncal", "primary": "principal", "secondary": "secundaria",
    "tertiary": "colectora", "street": "calle",
}
DETAIL = {
    "principales": ("autopista", "troncal", "principal"),
    "secundarias": ("autopista", "troncal", "principal", "secundaria"),
    "todas": None,
}
SLOW, JAM = 0.6, 0.3


def road_class(props):
    cat = str(props.get("road_category", "")).lower()
    if cat in CATEGORY_NAMES:
        return CATEGORY_NAMES[cat]
    return TYPE_NAMES.get(str(props.get("road_type", "")).lower(), "otra")


# ---------------------------------------------------------------- decodificador MVT
def _varint(buf, i):
    shift = result = 0
    while True:
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7


def _fields(buf):
    """Itera (número_de_campo, tipo_de_cable, valor) de un mensaje protobuf."""
    i, n = 0, len(buf)
    while i < n:
        key, i = _varint(buf, i)
        num, wire = key >> 3, key & 7
        if wire == 0:
            val, i = _varint(buf, i)
        elif wire == 2:
            ln, i = _varint(buf, i)
            val, i = buf[i:i + ln], i + ln
        elif wire == 1:
            val, i = buf[i:i + 8], i + 8
        elif wire == 5:
            val, i = buf[i:i + 4], i + 4
        else:
            raise ValueError(f"tipo de cable protobuf no soportado: {wire}")
        yield num, wire, val


def _unzigzag(v):
    return (v >> 1) ^ -(v & 1)


def _packed(buf):
    i, out = 0, []
    while i < len(buf):
        v, i = _varint(buf, i)
        out.append(v)
    return out


def _value(buf):
    import struct
    for num, wire, val in _fields(buf):
        if num == 1:
            return val.decode("utf-8", "replace")
        if num == 2:
            return struct.unpack("<f", val)[0]
        if num == 3:
            return struct.unpack("<d", val)[0]
        if num in (4, 5):
            return val
        if num == 6:
            return _unzigzag(val)
        if num == 7:
            return bool(val)
    return None


def _lines(geom):
    """Decodifica la geometría de una feature: lista de líneas [(x, y), ...] en coordenadas del tile."""
    out, cur, x, y, i = [], [], 0, 0, 0
    while i < len(geom):
        cmd, count = geom[i] & 7, geom[i] >> 3
        i += 1
        if cmd == 7:
            continue
        for _ in range(count):
            x += _unzigzag(geom[i])
            y += _unzigzag(geom[i + 1])
            i += 2
            if cmd == 1:
                if len(cur) > 1:
                    out.append(cur)
                cur = [(x, y)]
            else:
                cur.append((x, y))
    if len(cur) > 1:
        out.append(cur)
    return out


def decode_tile(data):
    """bytes (posiblemente gzip) → {nombre_capa: [{'props': {...}, 'type': 1|2|3, 'lines': [...]}, ...], 'extent': n}"""
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    layers = {}
    for num, _, lay in _fields(data):
        if num != 3:
            continue
        name, keys, values, feats, extent = "", [], [], [], 4096
        for n2, _, v in _fields(lay):
            if n2 == 1:
                name = v.decode("utf-8", "replace")
            elif n2 == 2:
                feats.append(v)
            elif n2 == 3:
                keys.append(v.decode("utf-8", "replace"))
            elif n2 == 4:
                values.append(_value(v))
            elif n2 == 5:
                extent = v
        out = []
        for f in feats:
            tags, gtype, geom = [], 0, []
            for n3, w3, v in _fields(f):
                if n3 == 2:
                    tags = _packed(v) if w3 == 2 else tags
                elif n3 == 3:
                    gtype = v
                elif n3 == 4:
                    geom = _packed(v)
            props = {keys[tags[j]]: values[tags[j + 1]] for j in range(0, len(tags) - 1, 2)}
            out.append({"props": props, "type": gtype, "lines": _lines(geom) if gtype == 2 else []})
        layers[name] = {"features": out, "extent": extent}
    return layers


# ---------------------------------------------------------------- geometría
def tile_to_lonlat(z, x, y, px, py, extent):
    n = 2.0 ** z
    lon = (x + px / extent) / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + py / extent) / n))))
    return lat, lon


def line_length_m(pts):
    total = 0.0
    for (la1, lo1), (la2, lo2) in zip(pts, pts[1:]):
        dy = (la2 - la1) * 110574.0
        dx = (lo2 - lo1) * 111320.0 * math.cos(math.radians((la1 + la2) / 2))
        total += math.hypot(dx, dy)
    return total


def tiles_for_bbox(south, west, north, east, z):
    n = 2 ** z

    def xy(lat, lon):
        x = int((lon + 180.0) / 360.0 * n)
        y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
        return min(max(x, 0), n - 1), min(max(y, 0), n - 1)
    x0, y1 = xy(south, west)
    x1, y0 = xy(north, east)
    return [(z, x, y) for x in range(min(x0, x1), max(x0, x1) + 1) for y in range(min(y0, y1), max(y0, y1) + 1)]


def area_bbox(a):
    lat_km, lon_km = 111.32, 111.32 * math.cos(math.radians(a["lat"]))
    dlat, dlon = a["radius_km"] / lat_km, a["radius_km"] / lon_km
    return a["lat"] - dlat, a["lon"] - dlon, a["lat"] + dlat, a["lon"] + dlon


# ---------------------------------------------------------------- foto
def segments_from_tile(z, x, y, data):
    """Tramos de un tile: dicts con clase, nivel, punto medio y largo."""
    lay = decode_tile(data).get(LAYER)
    if not lay:
        return []
    segs, idx = [], 0
    for f in lay["features"]:
        p = f["props"]
        for line in f["lines"]:
            pts = [tile_to_lonlat(z, x, y, px, py, lay["extent"]) for px, py in line]
            mid = pts[len(pts) // 2]
            try:
                level = float(p.get("traffic_level"))
            except (TypeError, ValueError):
                level = None
            segs.append({"z": z, "x": x, "y": y, "idx": idx, "road_class": road_class(p), "road_type": str(p.get("road_type", "")) or None,
                         "level": level, "coverage": p.get("traffic_road_coverage"), "closure": 1 if p.get("road_closure") else 0,
                         "lat": round(mid[0], 5), "lon": round(mid[1], 5), "length_m": round(line_length_m(pts), 1)})
            idx += 1
    return segs


def summarize(segments, n_tiles, zoom):
    by = {}
    for g in segments:
        by.setdefault(g["road_class"], []).append(g)
    out = []
    for cls, rows in sorted(by.items()):
        km = sum(r["length_m"] for r in rows) / 1000.0
        known = [r for r in rows if r["level"] is not None]
        kk = sum(r["length_m"] for r in known)
        mean = sum(r["level"] * r["length_m"] for r in known) / kk if kk else None
        out.append({"road_class": cls, "n_segments": len(rows), "km": round(km, 2),
                    "mean_level": None if mean is None else round(mean, 3),
                    "km_slow": round(sum(r["length_m"] for r in known if r["level"] < SLOW) / 1000.0, 2),
                    "km_jam": round(sum(r["length_m"] for r in known if r["level"] < JAM) / 1000.0, 2),
                    "n_tiles": n_tiles, "zoom": zoom})
    return out


def snapshot(con, area, fetch, zoom=11, detail="principales", keep_days=30, log=print):
    """Una foto del tráfico del área. `fetch(z, x, y)` → bytes del tile (server.traffic_vtile)."""
    if detail not in DETAIL:
        raise ValueError("detalle inválido: " + detail)
    tiles = tiles_for_bbox(*area_bbox(area), zoom)
    ts = storage.now_iso()
    log(f"  tráfico: {len(tiles)} tiles a zoom {zoom} (cuenta contra la cuota de TomTom)")
    t0 = time.time()
    all_segs, bad, last_err = [], 0, None
    for z, x, y in tiles:
        try:
            all_segs += segments_from_tile(z, x, y, fetch(z, x, y))
        except PermissionError:
            raise
        except Exception as err:  # noqa: BLE001 — un tile malo no tumba la foto entera
            bad += 1
            last_err = err
            storage.log_event("trafico", "warn", variant=f"{z}/{x}/{y}", phase="descarga", err=err)
    if not all_segs:
        if last_err is not None and bad == len(tiles):
            storage.log_event("trafico", "error", phase="descarga", err=last_err, rows=0, ms=(time.time() - t0) * 1000)
            raise RuntimeError(f"ningún tile se pudo descargar: {last_err}")
        storage.log_event("trafico", "error", phase="guardado", error_type="formato",
                          error_msg=f"los {len(tiles)} tiles llegaron sin la capa '{LAYER}' (¿cambió el formato o la key no incluye Traffic API?)",
                          rows=0, ms=(time.time() - t0) * 1000)
        raise RuntimeError("ningún tile devolvió tramos")
    allowed = DETAIL[detail]
    keep = [g for g in all_segs if allowed is None or g["road_class"] in allowed]
    storage.save_traffic(con, ts, summarize(all_segs, len(tiles), zoom), keep, keep_days)
    storage.log_event("trafico", "warn" if bad else "ok", variant=detail, phase="guardado", rows=len(all_segs), new=len(keep),
                      ms=(time.time() - t0) * 1000, detail=f"{len(tiles)} tiles, {bad} con problemas")
    return {"ts": ts, "tiles": len(tiles), "bad_tiles": bad, "segments": len(all_segs), "stored": len(keep)}


def format_summary(con, last=1):
    """Texto con el resumen de las últimas `last` fotos."""
    stamps = [r[0] for r in con.execute("SELECT DISTINCT ts_utc FROM traffic_summary ORDER BY ts_utc DESC LIMIT ?", (last,))]
    if not stamps:
        return "Aún no hay fotos de tráfico (usa --trafico)."
    lines = []
    for ts in stamps:
        lines.append(f"Tráfico {ts}")
        for r in con.execute("SELECT * FROM traffic_summary WHERE ts_utc=? ORDER BY km DESC", (ts,)):
            mean = "s/d" if r["mean_level"] is None else f"{r['mean_level']:.2f}"
            lines.append(f"  {r['road_class']:<10} {r['km']:>8.1f} km · fluidez media {mean} · lento {r['km_slow']:.1f} km · congestión {r['km_jam']:.1f} km")
    return "\n".join(lines)
