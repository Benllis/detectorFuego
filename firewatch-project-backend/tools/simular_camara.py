"""Simula una cámara: envía una alerta de prueba al backend.

Uso:
  python tools/simular_camara.py --clave <CLAVE_DE_LA_CAMARA> [--url http://localhost:8100]
         [--camara cam_1] [--tipo FIRE|SMOKE|CRITICAL|NO_DETECTION] [--imagen foto.jpg]
         [--lat -33.50 --lon -70.60]
Sin --imagen usa un JPG mínimo generado en memoria.
"""
import argparse
import json
import time

import requests

# JPG mínimo válido de 1x1 píxel
JPG_MINIMO = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707070909080a0c140d0c0b0b0c1912130f14"
    "1d1a1f1e1d1a1c1c20242e2720222c231c1c2837292c30313434341f27393d38323c2e333432ffc0000b080001000101011100"
    "ffc4001f0000010501010101010100000000000000000102030405060708090a0bffc400b5100002010303020403050504040000"
    "017d01020300041105122131410613516107227114328191a1082342b1c11552d1f02433627282090a161718191a25262728292a"
    "3435363738393a434445464748494a535455565758595a636465666768696a737475767778797a838485868788898a92939495"
    "969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1f2"
    "f3f4f5f6f7f8f9faffda0008010100003f00fbfcffd9"
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8100")
    ap.add_argument("--clave", required=True)
    ap.add_argument("--camara", default="cam_1")
    ap.add_argument("--tipo", default="FIRE", choices=["FIRE", "SMOKE", "CRITICAL", "NO_DETECTION"])
    ap.add_argument("--imagen")
    ap.add_argument("--lat", type=float, default=-33.5227)
    ap.add_argument("--lon", type=float, default=-70.5859)
    a = ap.parse_args()

    img = open(a.imagen, "rb").read() if a.imagen else JPG_MINIMO
    ahora = time.time()
    meta = {
        "id_alerta": f"{a.camara}_{a.tipo.lower()}_{time.strftime('%Y%m%d_%H%M%S')}_{int(ahora * 1000) % 1000:03d}",
        "camara_id": a.camara, "camara_nombre": "Cámara simulada", "ubicacion": "Prueba",
        "gps": [a.lat, a.lon], "tipo": a.tipo, "timestamp": ahora,
        "fecha_hora": time.strftime("%Y-%m-%d %H:%M:%S"),
        "fuego_detectado": a.tipo in ("FIRE", "CRITICAL"), "humo_detectado": a.tipo in ("SMOKE", "CRITICAL"),
        "confianza": 0.85,
    }
    r = requests.post(f"{a.url}/alertas", data={"metadata": json.dumps(meta)},
                      files={"imagen": ("alerta.jpg", img, "image/jpeg")},
                      headers={"X-Camera-Key": a.clave}, timeout=10)
    print(r.status_code, r.text)


if __name__ == "__main__":
    main()
