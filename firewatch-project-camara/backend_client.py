"""
backend_client.py — Envía las alertas de la cámara al backend de FireWatch.

Cómo funciona
-------------
La cámara sigue guardando cada alerta (JPG + JSON) en la carpeta `alertas_pendientes`.
Esa carpeta pasa a ser la COLA: un hilo en segundo plano envía lo que haya a
`POST /alertas`. Si el envío funciona, mueve los dos archivos a `alertas_enviadas`.
Si el backend no responde, los deja donde están y reintenta más tarde, así no se
pierde ninguna alerta aunque no haya red.

Configuración (archivo `.env` junto a este módulo, nunca en el repositorio)
---------------------------------------------------------------------------
    FIREWATCH_API_URL=http://localhost:8100
    FIREWATCH_KEY_CAM_1=<clave que entregó POST /camaras para cam_1>
    FIREWATCH_KEY_CAM_2=<clave de cam_2, si hay otra cámara>
    FIREWATCH_CA_BUNDLE=<ruta del cert.pem del backend>   (solo si el backend usa HTTPS con certificado propio)

Si falta la URL o la clave de una cámara, esa cámara simplemente no envía
(el comportamiento queda como antes: solo guarda en la carpeta).
"""
import json
import os
import threading
import time

try:
    import requests
except ImportError:  # el cliente se desactiva con un aviso claro
    requests = None


def load_env(path=None):
    """Lee un .env sencillo (CLAVE=valor) sin dependencias extra."""
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    values = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                values[k.strip()] = v.strip().strip('"').strip("'")
    return values


class BackendClient:
    RETRY_SECONDS = 10      # cada cuánto revisa la cola
    TIMEOUT = 10            # segundos de espera por envío

    def __init__(self, queue_dir, env=None):
        env = env if env is not None else {**load_env(), **os.environ}
        self.queue_dir = queue_dir
        self.sent_dir = os.path.join(os.path.dirname(os.path.abspath(queue_dir)) or ".", "alertas_enviadas")
        self.rejected_dir = os.path.join(os.path.dirname(os.path.abspath(queue_dir)) or ".", "alertas_rechazadas")
        self.base_url = (env.get("FIREWATCH_API_URL") or "").rstrip("/")
        # HTTPS con certificado propio: FIREWATCH_CA_BUNDLE = cert.pem del backend. La verificación nunca se apaga.
        self.verify = (env.get("FIREWATCH_CA_BUNDLE") or "").strip() or True
        self.keys = {k[len("FIREWATCH_KEY_"):].lower(): v
                     for k, v in env.items() if k.startswith("FIREWATCH_KEY_") and v}
        self.status = "desactivado"
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = None

        if requests is None:
            self.status = "desactivado (falta instalar 'requests')"
        elif not self.base_url or not self.keys:
            self.status = "desactivado (falta FIREWATCH_API_URL o FIREWATCH_KEY_* en .env)"
        else:
            os.makedirs(self.sent_dir, exist_ok=True)
            os.makedirs(self.rejected_dir, exist_ok=True)
            self.status = "iniciando"
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    @property
    def enabled(self):
        return self._thread is not None

    def notify(self):
        """Avisa que hay una alerta nueva en la cola (envío inmediato)."""
        self._wake.set()

    def stop(self):
        self._stop.set()
        self._wake.set()

    # ── interno ──
    def _loop(self):
        while not self._stop.is_set():
            try:
                self._flush()
            except Exception as e:  # nunca debe romper la cámara
                self.status = f"error inesperado: {e}"
            self._wake.wait(self.RETRY_SECONDS)
            self._wake.clear()

    def _flush(self):
        pendientes = sorted(f for f in os.listdir(self.queue_dir) if f.endswith(".json"))
        if not pendientes:
            if self.status.startswith(("iniciando", "sin conexión")):
                self.status = "conectado"
            return
        for nombre in pendientes:
            if self._stop.is_set():
                return
            resultado = self._send_one(os.path.join(self.queue_dir, nombre))
            if resultado == "red":      # sin conexión: dejar el resto para después
                return

    def _move(self, json_path, meta, dest):
        base = os.path.splitext(os.path.basename(json_path))[0]
        img = os.path.join(self.queue_dir, meta.get("imagen_asociada") or f"{base}.jpg")
        for p in (img, json_path):
            if os.path.exists(p):
                os.replace(p, os.path.join(dest, os.path.basename(p)))  # sobrescribe también en Windows

    def _send_one(self, json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except (OSError, ValueError):
            return "ignorada"           # JSON a medio escribir: se reintenta luego
        cam_id = str(meta.get("camara_id", "")).lower()
        clave = self.keys.get(cam_id)
        if not clave:
            self.status = f"sin clave para {cam_id} (agrega FIREWATCH_KEY_{cam_id.upper()})"
            return "sin_clave"
        img_path = os.path.join(self.queue_dir, meta.get("imagen_asociada", ""))
        if not os.path.isfile(img_path):
            return "ignorada"
        try:
            with open(img_path, "rb") as f:
                jpg = f.read()
            r = requests.post(
                f"{self.base_url}/alertas",
                data={"metadata": json.dumps(meta)},
                files={"imagen": (os.path.basename(img_path), jpg, "image/jpeg")},
                headers={"X-Camera-Key": clave},
                timeout=self.TIMEOUT,
                verify=self.verify,
            )
        except requests.RequestException as e:
            if type(e).__name__ == "SSLError":
                self.status = "certificado HTTPS del backend no confiable (define FIREWATCH_CA_BUNDLE con su cert.pem)"
            else:
                self.status = "sin conexión con el backend (reintentando)"
            return "red"

        if r.status_code in (200, 201):
            self._move(json_path, meta, self.sent_dir)
            self.status = "conectado"
            return "ok"
        if r.status_code in (401, 403):
            self.status = f"clave rechazada para {cam_id} ({r.status_code}); revisa .env y POST /camaras"
            return "clave"
        if r.status_code in (413, 422):  # la alerta en sí es inválida: no tiene sentido reintentar
            self._move(json_path, meta, self.rejected_dir)
            self.status = f"alerta rechazada ({r.status_code}): {r.text[:120]}"
            return "rechazada"
        self.status = f"backend respondió {r.status_code}"
        return "red"
