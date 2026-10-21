"""
probar_camara.py — Prueba la conexión a TU cámara desde la terminal, sin abrir el panel.

Uso (PowerShell, dentro de firewatch-project-camara, con el entorno de la cámara):
    python tools\\probar_camara.py --ip 192.168.1.50 --usuario micuenta
    python tools\\probar_camara.py --ip 192.168.1.50 --usuario micuenta --marca hikvision
    python tools\\probar_camara.py --fuente 0                      (webcam)
    python tools\\probar_camara.py --fuente C:\\videos\\prueba.mp4  (archivo)

La clave se pide por teclado oculta (no queda en el historial ni en pantalla). Sin --marca prueba las marcas
conocidas una por una. Guarda una captura de la primera conexión correcta en probar_camara.jpg.
Solo para cámaras PROPIAS, con la cuenta que tú creaste en ellas.
"""
import argparse
import getpass
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from camera_sources import BRANDS, BRAND_ORDER, build_rtsp_url, mask_source, probe_source  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Prueba la conexión a una cámara propia.")
    ap.add_argument("--ip"); ap.add_argument("--puerto", type=int)
    ap.add_argument("--usuario", default=""); ap.add_argument("--marca", choices=BRAND_ORDER)
    ap.add_argument("--ruta", default="", help="ruta manual (lo que va después del puerto)")
    ap.add_argument("--canal", type=int, default=1)
    ap.add_argument("--fuente", help="0/1 (webcam), ruta de archivo o URL completa")
    ap.add_argument("--timeout", type=int, default=8, help="segundos de espera por intento")
    a = ap.parse_args()
    ms = a.timeout * 1000

    candidates = []
    if a.fuente:
        candidates.append(("fuente indicada", a.fuente))
    else:
        if not a.ip:
            ap.error("indica --ip (o --fuente)")
        pwd = getpass.getpass("Clave de la cuenta de la cámara (no se muestra): ") if a.usuario else ""
        brands = [a.marca] if a.marca else [b for b in BRAND_ORDER if b != "otra"]
        for b in brands:
            for stream in ("main", "sub"):
                try:
                    candidates.append((f"{BRANDS[b]['label']} ({'normal' if stream == 'main' else 'baja'})",
                                       build_rtsp_url(b, a.ip, a.puerto, a.usuario, pwd, stream, a.canal, a.ruta)))
                except ValueError as err:
                    print(f"  - {b}: {err}")
    ok_any = False
    for label, src in candidates:
        print(f"Probando {label}: {mask_source(src)} …", end=" ", flush=True)
        ok, msg, frame, secs = probe_source(src, ms)
        print(("OK" if ok else "falló") + f" ({secs:.1f} s) — {msg}")
        if ok:
            import cv2
            cv2.imwrite("probar_camara.jpg", frame)
            print("  Captura guardada en probar_camara.jpg")
            print(f"  Úsala en el panel: marca = {label.split(' (')[0]}, misma IP/usuario/clave.")
            ok_any = True
            break
    if not ok_any:
        print("\nNinguna prueba funcionó. Revisa: IP correcta, mismo Wi-Fi/red, RTSP u ONVIF activado en la app de la cámara,"
              " y usuario/clave de la \"cuenta de la cámara\" (no los de tu cuenta de la app).")
    return 0 if ok_any else 1


if __name__ == "__main__":
    sys.exit(main())
