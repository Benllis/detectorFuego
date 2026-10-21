#!/usr/bin/env python3
"""
FireWatch · Mapa — descarga ÚNICA de los datos del SIG de Bomberos de Chile (sig.bomberos.cl).

Qué hace
  1. Baja una sola vez cuerpos.json, compannias.json y grifos_2019.json (los mismos archivos que
     usa el mapa público de sig.bomberos.cl, sin login) y los guarda tal cual en data/bomberos/raw/.
  2. Los normaliza (coordenadas a número, nombres, teléfono, diámetro del grifo) y escribe
     data/bomberos/stations.json, data/bomberos/hydrants.json y data/bomberos/meta.json.
  3. El servidor del mapa (server.py) sirve esos archivos en /api/bomberos/*. Nada se vuelve a pedir
     a sig.bomberos.cl: es una copia local.

Condiciones de uso que nos fijamos (el sitio no declara licencia)
  - Una descarga, con identificación clara, sin repetir peticiones. Si ya existe la copia, no vuelve a bajar
    (usa --force solo si hace falta, y con criterio).
  - data/bomberos/ está en .gitignore: los datos NO se suben al repositorio.
  - El mapa muestra siempre la fuente y el año ("SIG Bomberos de Chile, grifos 2019").
  - Se contrastan con OpenStreetMap; no se presentan como información oficial en tiempo real.

Uso (desde la carpeta firewatch-project-mapa)
  python tools/ingest_bomberos.py                 # descarga y normaliza
  python tools/ingest_bomberos.py --inspect       # muestra campos y un registro de ejemplo (no escribe)
  python tools/ingest_bomberos.py --from-dir RUTA # normaliza archivos que guardaste a mano (cuerpos.json, compannias.json, grifos_2019.json)
  python tools/ingest_bomberos.py --renormalize   # vuelve a normalizar lo ya descargado, sin tocar la red

Solo usa la librería estándar.
"""
import argparse
import hashlib
import json
import os
import sys
import time
import unicodedata
import urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "bomberos")
RAW = os.path.join(OUT, "raw")
BASE = "https://sig.bomberos.cl"
FILES = {"cuerpos": "cuerpos.json", "companias": "compannias.json", "grifos": "grifos_2019.json"}
UA = "FireWatch-Mapa/1.0 (proyecto academico Duoc UC; descarga unica de cuarteles y grifos)"
SOURCE_LABEL = "SIG Bomberos de Chile (sig.bomberos.cl)"
HYDRANT_YEAR = 2019

# Chile continental + insular (Juan Fernández, Rapa Nui): sirve para descartar coordenadas rotas.
LAT_RANGE = (-56.8, -17.0)
LON_RANGE = (-110.0, -65.5)


# ------------------------------------------------------------------ utilidades
def norm_key(k):
    k = unicodedata.normalize("NFKD", str(k)).encode("ascii", "ignore").decode().lower()
    return "".join(c for c in k if c.isalnum())


def to_num(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def text(v, maxlen=120):
    if v is None:
        return None
    s = " ".join(str(v).split())
    return s[:maxlen] if s and s.lower() not in ("null", "none", "nan", "-", "s/i") else None


def records(raw):
    """Lista de registros planos {campo: valor} venga como lista, GeoJSON o dict envoltorio."""
    if isinstance(raw, dict):
        if isinstance(raw.get("features"), list):
            out = []
            for f in raw["features"]:
                rec = dict(f.get("properties") or {})
                g = f.get("geometry") or {}
                if g.get("type") == "Point" and len(g.get("coordinates") or []) >= 2:
                    rec["__lon"], rec["__lat"] = g["coordinates"][0], g["coordinates"][1]
                out.append(rec)
            return out
        for v in raw.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
        return []
    return [r for r in raw if isinstance(r, dict)] if isinstance(raw, list) else []


class Rec:
    """Acceso a un registro por nombre de campo tolerante (sin tildes, mayúsculas ni símbolos)."""
    def __init__(self, d):
        self.d = d
        self.k = {norm_key(k): k for k in d}

    def get(self, *names):
        for n in names:
            key = self.k.get(norm_key(n))
            if key is not None:
                v = self.d[key]
                if v not in (None, "", " "):
                    return v
        return None


LAT_NAMES = ("lat", "latitud", "latitude", "y", "lat_y", "coord_y", "ycoord")
LON_NAMES = ("lng", "lon", "long", "longitud", "longitude", "x", "lon_x", "coord_x", "xcoord")


def coords(r):
    lat = to_num(r.d.get("__lat")) if "__lat" in r.d else to_num(r.get(*LAT_NAMES))
    lon = to_num(r.d.get("__lon")) if "__lon" in r.d else to_num(r.get(*LON_NAMES))
    if lat is None or lon is None:
        return None
    if not (LAT_RANGE[0] <= lat <= LAT_RANGE[1]) and (LAT_RANGE[0] <= lon <= LAT_RANGE[1]):
        lat, lon = lon, lat  # vienen invertidas
    if not (LAT_RANGE[0] <= lat <= LAT_RANGE[1] and LON_RANGE[0] <= lon <= LON_RANGE[1]):
        return None
    return round(lat, 6), round(lon, 6)


USED = set(map(norm_key, LAT_NAMES + LON_NAMES + ("__lat", "__lon")))


def extras(r, skip, limit=6):
    """Otros campos de texto cortos (dirección, comuna, etc.) para mostrar en el popup."""
    out = {}
    for k, v in r.d.items():
        nk = norm_key(k)
        if nk in USED or nk in skip or k.startswith("__") or isinstance(v, (dict, list)):
            continue
        t = text(v, 80)
        if t:
            out[str(k)[:30]] = t
        if len(out) >= limit:
            break
    return out


# ------------------------------------------------------------------ normalización
def norm_stations(cuerpos_raw, companias_raw):
    items, dropped = [], {"cuerpos": 0, "companias": 0}
    seen = set()
    for kind, raw, label in (("cuerpo", cuerpos_raw, "cuerpos"), ("compania", companias_raw, "companias")):
        for i, d in enumerate(records(raw)):
            r = Rec(d)
            c = coords(r)
            if not c:
                dropped[label] += 1
                continue
            cuerpo = text(r.get("cuerpo", "nombre_cuerpo", "cuerpo_bomberos", "nombrecuerpo", "bomberos"))
            numero = text(r.get("numero", "n_compania", "num_compania", "ncompania", "compania_numero", "numerocompania"))
            nombre = text(r.get("nombre", "name", "nombre_compania", "compania", "nombre_cuerpo", "razon_social", "titulo"))
            if kind == "compania":
                if nombre and numero and numero not in nombre:
                    nombre = f"{numero}ª Compañía · {nombre}"
                elif not nombre:
                    nombre = f"{numero}ª Compañía" if numero else "Compañía de bomberos"
                operador = cuerpo
                if cuerpo and cuerpo not in nombre:
                    nombre = f"{nombre} · {cuerpo}"
            else:
                nombre = nombre or cuerpo or "Cuerpo de Bomberos"
                operador = cuerpo or nombre
            rid = text(r.get("id", "id_cuerpo", "id_compania", "codigo", "cod", "objectid", "fid", "gid"), 30) or str(i)
            uid = f"sig-{kind[0]}-{rid}"
            if uid in seen:
                uid = f"{uid}-{i}"
            seen.add(uid)
            items.append({
                "id": uid, "kind": kind, "name": nombre, "operator": operador,
                "phone": text(r.get("telefono", "fono", "phone", "tel", "celular", "fono_cuartel"), 40),
                "lat": c[0], "lon": c[1],
                "extra": extras(r, set(map(norm_key, ("nombre", "name", "cuerpo", "telefono", "fono", "id", "numero")))),
            })
    return items, dropped


def norm_hydrants(raw):
    items, dropped = [], 0
    for i, d in enumerate(records(raw)):
        r = Rec(d)
        c = coords(r)
        if not c:
            dropped += 1
            continue
        diam = r.get("diametro", "diam", "diametro_mm", "diametro_pulgadas", "pulgadas", "dn")
        tipo = text(r.get("tipo", "tipo_grifo", "type", "modelo", "clase"), 40)
        rid = text(r.get("id", "codigo", "cod", "objectid", "fid", "gid", "n_grifo"), 30) or str(i)
        items.append([f"sig-g-{rid}-{i}", c[0], c[1], tipo or "desconocido", text(diam, 20),
                      text(r.get("anio", "ano", "year", "fecha"), 12) or str(HYDRANT_YEAR),
                      text(r.get("comuna", "ciudad", "localidad"), 40)])
    return items, dropped


# ------------------------------------------------------------------ descarga / disco
def sha256(b):
    return hashlib.sha256(b).hexdigest()


def fetch(name):
    req = urllib.request.Request(f"{BASE}/{name}", headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def read_json(path):
    with open(path, "rb") as fh:
        return json.loads(fh.read().decode("utf-8-sig"))


def describe(label, raw):
    recs = records(raw)
    print(f"\n[{label}] {len(recs)} registros")
    if recs:
        print("  campos:", ", ".join(map(str, recs[0].keys())))
        print("  ejemplo:", json.dumps(recs[0], ensure_ascii=False)[:500])


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="vuelve a descargar aunque ya exista la copia")
    ap.add_argument("--inspect", action="store_true", help="muestra campos y un ejemplo; no escribe nada")
    ap.add_argument("--from-dir", help="carpeta con los 3 JSON guardados a mano (no usa la red)")
    ap.add_argument("--renormalize", action="store_true", help="vuelve a normalizar lo ya descargado (sin red)")
    a = ap.parse_args()

    os.makedirs(RAW, exist_ok=True)
    raws, meta_files = {}, {}
    for key, fname in FILES.items():
        dest = os.path.join(RAW, fname)
        if a.from_dir:
            src = os.path.join(a.from_dir, fname)
            if not os.path.exists(src):
                sys.exit(f"Falta {src}")
            body = open(src, "rb").read()
            origin = "archivo local"
        elif os.path.exists(dest) and not a.force:
            body = open(dest, "rb").read()
            origin = "copia local previa"
        elif a.renormalize:
            sys.exit(f"No hay copia local de {fname}; ejecuta sin --renormalize.")
        else:
            print(f"Descargando {BASE}/{fname} ...", flush=True)
            try:
                body = fetch(fname)
            except Exception as err:  # noqa: BLE001
                sys.exit(f"No se pudo descargar {fname}: {type(err).__name__}: {err}\n"
                         f"Alternativa: abre {BASE}/{fname} en el navegador, guárdalo con ese nombre en una carpeta "
                         f"y ejecuta: python tools/ingest_bomberos.py --from-dir <carpeta>")
            origin = "descarga"
            time.sleep(1.5)  # cortesía entre archivos
        try:
            raws[key] = json.loads(body.decode("utf-8-sig"))
        except ValueError as err:
            sys.exit(f"{fname} no es JSON válido ({err}). Primeros bytes: {body[:120]!r}")
        meta_files[fname] = {"bytes": len(body), "sha256": sha256(body), "origen": origin}
        if not a.inspect and origin != "copia local previa":
            with open(dest, "wb") as fh:
                fh.write(body)
        print(f"  {fname}: {len(body) / 1e6:.2f} MB ({origin})")

    if a.inspect:
        for k, raw in raws.items():
            describe(k, raw)
        return

    stations, dropped_s = norm_stations(raws["cuerpos"], raws["companias"])
    hydrants, dropped_h = norm_hydrants(raws["grifos"])
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    fuente = {"fuente": SOURCE_LABEL, "descargado": now}
    write_json(os.path.join(OUT, "stations.json"), {**fuente, "anio": None, "items": stations})
    write_json(os.path.join(OUT, "hydrants.json"), {
        **fuente, "anio": HYDRANT_YEAR,
        "campos": ["id", "lat", "lon", "tipo", "diametro", "anio", "comuna"], "items": hydrants})
    n_c = sum(1 for s in stations if s["kind"] == "cuerpo")
    meta = {
        **fuente, "archivos": meta_files, "cuerpos": n_c, "companias": len(stations) - n_c, "grifos": len(hydrants),
        "descartados": {"cuerpos_sin_coordenadas": dropped_s["cuerpos"], "companias_sin_coordenadas": dropped_s["companias"],
                        "grifos_sin_coordenadas": dropped_h},
        "licencia": "El sitio no declara licencia: uso academico, copia local, sin redistribuir; citar la fuente.",
    }
    write_json(os.path.join(OUT, "meta.json"), meta)
    print(f"\nListo: {n_c} cuerpos, {len(stations) - n_c} compañías, {len(hydrants)} grifos "
          f"(descartados por coordenadas inválidas: {dropped_s['cuerpos'] + dropped_s['companias']} cuarteles, {dropped_h} grifos).")
    if stations:
        print("  ejemplo cuartel:", json.dumps(stations[-1], ensure_ascii=False)[:300])
    if hydrants:
        print("  ejemplo grifo  :", json.dumps(hydrants[0], ensure_ascii=False))
    print(f"Archivos en {OUT}")


if __name__ == "__main__":
    main()
