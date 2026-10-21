"""Marca como resueltas TODAS las alertas pendientes (para dejar la lista limpia después de pruebas).

No borra nada: cada alerta queda como FALSO_POSITIVO con el motivo indicado, así el historial se conserva
y las cámaras y usuarios no se tocan. La contraseña se pide por teclado (no se guarda ni se imprime).

Uso (desde firewatch-project-backend, con el entorno activado y el backend corriendo):
    python tools/limpiar_pendientes.py
    python tools/limpiar_pendientes.py --usuario admin --motivo "limpieza de pruebas"
"""
import argparse
import getpass
import sys

import requests


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:8100")
    ap.add_argument("--usuario", default="admin")
    ap.add_argument("--motivo", default="limpieza de pruebas")
    ap.add_argument("--si", action="store_true", help="no pedir confirmación")
    a = ap.parse_args()
    base = a.url.rstrip("/")

    pwd = getpass.getpass(f"Contraseña de {a.usuario}: ")
    try:
        r = requests.post(f"{base}/login", json={"username": a.usuario, "password": pwd}, timeout=10)
    except requests.RequestException as e:
        sys.exit(f"No se pudo conectar con el backend en {base}: {e}")
    if r.status_code != 200:
        sys.exit(f"Login rechazado ({r.status_code}): {r.text[:150]}")
    h = {"Authorization": f"Bearer {r.json()['token']}"}

    total = requests.get(f"{base}/alertas", params={"estado": "PENDIENTE", "limit": 1}, headers=h, timeout=10).json()["total"]
    if total == 0:
        print("No hay alertas pendientes.")
        return
    if not a.si and input(f"Hay {total} alerta(s) pendiente(s). Marcarlas todas como resueltas (motivo: «{a.motivo}»)? Escribe SI: ").strip() != "SI":
        sys.exit("Cancelado, no se tocó nada.")

    hechas = fallos = 0
    while True:
        items = requests.get(f"{base}/alertas", params={"estado": "PENDIENTE", "limit": 200}, headers=h, timeout=20).json()["items"]
        if not items:
            break
        progreso = 0
        for al in items:
            p = requests.patch(f"{base}/alertas/{al['id_alerta']}", headers=h, timeout=10,
                               json={"estado": "FALSO_POSITIVO", "motivo_descarte": a.motivo})
            if p.status_code == 200:
                hechas += 1
                progreso += 1
            else:
                fallos += 1
                print(f"  no se pudo resolver {al['id_alerta']}: {p.status_code} {p.text[:100]}")
        if progreso == 0:      # nada avanzó: evitar un bucle infinito
            break
    print(f"Listo: {hechas} alerta(s) resuelta(s), {fallos} con error.")


if __name__ == "__main__":
    main()
