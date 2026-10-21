"""
FireWatch · Cámara — medición de falsas alarmas con videos SIN fuego.

Pasa cada video por los dos modelos con la MISMA lógica de main_multicamera.py (teselas para
video vertical/panorámico, umbrales por modelo, confirmación por ventana) y cuenta:
  · % de muestras con una detección suelta de fuego / de humo
  · % de muestras en que la regla de confirmación se cumpliría (fuego 3/5, humo 2/5)
  · alertas que se habrían enviado (con la regla de prioridad y cooldown reales)

Uso (desde firewatch-project-camara, con .venv-camara activo):
  python tools/medir_falsas_alarmas.py "..\\Videos de prueba\\sin_fuego"
  opciones: --cada 2.4 (segundos de video entre muestras: ~0.8 s x nº de cámaras)
            --modelos ..\\models  --csv resultados.csv  --max-seg 120
"""
import argparse
import csv
import glob
import os
import sys
import time

import cv2
import numpy as np
import onnxruntime as ort

SZ = 416
CONF_A, CONF_B = 0.35, 0.55
TILE_MIN_ASPECT = 1.5
COOLDOWN = {"SMOKE": 600, "FIRE": 300, "CRITICAL": 180}
PRIO = {"SMOKE": 1, "FIRE": 2, "CRITICAL": 3}
MIN_FIRE, MIN_SMOKE, WINDOW, MAX_AGE, RESET = 3, 2, 5, 30, 180


def tiles(h, w):
    lado, mayor = min(h, w), max(h, w)
    if mayor / lado < TILE_MIN_ASPECT:
        return [(0, 0, w, h)]
    n = int(np.ceil((mayor - lado) / (lado * 0.5))) + 1
    pos = np.linspace(0, mayor - lado, n).astype(int)
    if h > w:
        return [(0, int(p), w, lado) for p in pos]
    return [(int(p), 0, lado, h) for p in pos]


def run(session, tensor, conf):
    out = session.run([session.get_outputs()[0].name], {session.get_inputs()[0].name: tensor})[0][0]
    if out.shape[0] < out.shape[1]:
        out = out.T
    return float(out[:, 4].max()), int((out[:, 4] >= conf).sum())


def analizar(frame, sa, sb):
    h, w = frame.shape[:2]
    fire_max = smoke_max = 0.0
    fire_n = smoke_n = 0
    for x0, y0, tw, th in tiles(h, w):
        crop = frame[y0:y0 + th, x0:x0 + tw]
        rgb = cv2.cvtColor(cv2.resize(crop, (SZ, SZ)), cv2.COLOR_BGR2RGB)
        t = np.expand_dims(np.transpose(rgb.astype(np.float32) / 255.0, (2, 0, 1)), axis=0)
        m, n = run(sa, t, CONF_A); fire_max = max(fire_max, m); fire_n += n
        m, n = run(sb, t, CONF_B); smoke_max = max(smoke_max, m); smoke_n += n
    return fire_max, fire_n > 0, smoke_max, smoke_n > 0


def medir(path, sa, sb, cada, max_seg):
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    total = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    dur = total / fps if total else 0
    dur = min(dur, max_seg) if max_seg else dur
    hist, estado, t_alerta, t_det = [], None, -1e9, -1e9
    res = dict(video=os.path.basename(path), seg=round(dur, 1), muestras=0, fuego_suelto=0, humo_suelto=0,
               fuego_conf=0, humo_conf=0, alertas=0, tipos="", max_fuego=0.0, max_humo=0.0)
    tipos = []
    t = 0.0
    while t < dur:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        if not ok:
            break
        fm, hf, sm, hs = analizar(frame, sa, sb)
        res["muestras"] += 1
        res["fuego_suelto"] += hf
        res["humo_suelto"] += hs
        res["max_fuego"] = max(res["max_fuego"], fm)
        res["max_humo"] = max(res["max_humo"], sm)
        hist.append((t, hf, hs))
        hist = [x for x in hist if t - x[0] <= MAX_AGE][-WINDOW:]
        fire_ok = sum(1 for x in hist if x[1]) >= MIN_FIRE
        smoke_ok = sum(1 for x in hist if x[2]) >= MIN_SMOKE
        res["fuego_conf"] += fire_ok and hf
        res["humo_conf"] += smoke_ok and hs
        if not (hf or hs):
            if estado and t - t_det > RESET:
                estado = None
        else:
            t_det = t
            st = "CRITICAL" if (fire_ok and smoke_ok) else "FIRE" if fire_ok else "SMOKE" if smoke_ok else None
            if st:
                p, lp = PRIO[st], PRIO.get(estado, 0)
                if p > lp or (p == lp and t - t_alerta >= COOLDOWN[st]):
                    t_alerta, estado = t, st
                    res["alertas"] += 1
                    tipos.append(f"{st}@{int(t)}s")
        t += cada
    cap.release()
    res["tipos"] = " ".join(tipos)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("carpeta")
    ap.add_argument("--cada", type=float, default=2.4)
    ap.add_argument("--modelos", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "models"))
    ap.add_argument("--csv", default="falsas_alarmas.csv")
    ap.add_argument("--max-seg", type=float, default=120)
    a = ap.parse_args()
    sa = ort.InferenceSession(os.path.join(a.modelos, "modelo_a_fire.onnx"), providers=["CPUExecutionProvider"])
    sb = ort.InferenceSession(os.path.join(a.modelos, "modelo_b_smoke.onnx"), providers=["CPUExecutionProvider"])
    vids = sorted(glob.glob(os.path.join(a.carpeta, "*.mp4")))
    if not vids:
        sys.exit("No hay .mp4 en " + a.carpeta)
    filas = []
    for v in vids:
        t0 = time.time()
        r = medir(v, sa, sb, a.cada, a.max_seg)
        filas.append(r)
        print(f"{r['video'][:55]:55} {r['seg']:6.1f}s muestras={r['muestras']:3d} "
              f"fuego={r['fuego_suelto']:2d}({r['fuego_conf']}) humo={r['humo_suelto']:2d}({r['humo_conf']}) "
              f"alertas={r['alertas']} {r['tipos']}  [{time.time()-t0:.0f}s]", flush=True)
    with open(a.csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(filas[0].keys()))
        w.writeheader(); w.writerows(filas)
    tm = sum(r["muestras"] for r in filas); ts = sum(r["seg"] for r in filas)
    print(f"\nVideos: {len(filas)} · {ts/60:.1f} min de video · {tm} muestras")
    print(f"Detección suelta de fuego: {sum(r['fuego_suelto'] for r in filas)} ({100*sum(r['fuego_suelto'] for r in filas)/tm:.1f} % de las muestras)")
    print(f"Detección suelta de humo : {sum(r['humo_suelto'] for r in filas)} ({100*sum(r['humo_suelto'] for r in filas)/tm:.1f} %)")
    print(f"Alertas que se habrían enviado: {sum(r['alertas'] for r in filas)} en {ts/60:.1f} min de video")
    print(f"Resultados guardados en {a.csv}")


if __name__ == "__main__":
    main()
