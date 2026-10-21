"""Quita una cámara del backend para poder registrarla de nuevo y obtener una clave nueva.

Uso (con el backend detenido o en marcha):
  python tools/quitar_camara.py cam_1 [--db data/backend.db]

Si la cámara tiene alertas, estas se conservan (solo se borra el registro de la cámara).
"""
import argparse
import os
import sqlite3
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("camara_id")
    ap.add_argument("--db", default="data/backend.db")
    a = ap.parse_args()

    if not os.path.exists(a.db):
        sys.exit(f"No encuentro la base de datos: {a.db}. Revisa FIREWATCH_DB en el .env del backend.")
    con = sqlite3.connect(a.db)
    existe = con.execute("SELECT COUNT(*) FROM camaras WHERE camara_id = ?", (a.camara_id,)).fetchone()[0]
    if not existe:
        sys.exit(f"La cámara {a.camara_id} no existe en {a.db}.")
    n_alertas = con.execute("SELECT COUNT(*) FROM alertas WHERE camara_id = ?", (a.camara_id,)).fetchone()[0]
    try:
        con.execute("DELETE FROM camaras WHERE camara_id = ?", (a.camara_id,))
        con.commit()
    except sqlite3.IntegrityError as e:
        sys.exit(f"No se pudo quitar (la cámara tiene datos asociados): {e}")
    print(f"Cámara {a.camara_id} eliminada. Sus {n_alertas} alerta(s) siguen guardadas.")


if __name__ == "__main__":
    main()
