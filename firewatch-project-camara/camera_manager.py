"""
camera_manager.py — Gestión eficiente de cámaras sin bloqueos de hardware.
"""

import cv2
import time
import json
import os
import threading
from dataclasses import dataclass, field

from camera_sources import open_capture


class CameraThread(threading.Thread):
    """
    Hilo de captura de UNA cámara. Mantiene la fuente abierta y, si se corta (cámara IP que pierde Wi-Fi,
    se reinicia o cambia de IP), la vuelve a abrir sola con pausas crecientes (1, 2, 4, 8, 15 y 30 s) sin
    que el resto del programa se entere más que por el estado visible (`status`).
    """
    BACKOFF_S = (1, 2, 4, 8, 15, 30)
    STALL_S = 8.0          # sin cuadros nuevos durante este tiempo → se considera caída y se reabre
    OPEN_TIMEOUT_MS = 8000

    def __init__(self, source):
        super().__init__(daemon=True)
        self.source_clean = self._parse_source(source)
        self.cap = None
        self.frame = None
        self.ret = False
        self.running = True
        self.lock = threading.Lock()
        self.connected = False
        self.status = "Conectando…"
        self.attempt = 0                # reintentos desde el último cuadro bueno
        self.last_frame_time = 0.0

    def _parse_source(self, source):
        if isinstance(source, int):
            return source
        if isinstance(source, str) and source.strip().isdigit():
            return int(source.strip())
        return source

    def _set(self, connected, status):
        with self.lock:
            self.connected = connected
            if not connected:
                self.ret = False
            self.status = status

    def _sleep(self, seconds):
        """Espera en tramos cortos para poder detenerse rápido."""
        end = time.time() + seconds
        while self.running and time.time() < end:
            time.sleep(0.1)

    def _wait_before_retry(self, why):
        self.attempt += 1
        wait = self.BACKOFF_S[min(self.attempt - 1, len(self.BACKOFF_S) - 1)]
        self._set(False, f"Sin señal ({why}) · reintento {self.attempt} en {wait} s")
        self._sleep(wait)

    def run(self):
        is_file = isinstance(self.source_clean, str) and os.path.isfile(self.source_clean)
        while self.running:
            # ── abrir ──
            self._set(False, "Conectando…" if not self.attempt else self.status)
            self.cap = open_capture(self.source_clean, self.OPEN_TIMEOUT_MS)
            if not self.cap or not self.cap.isOpened():
                self._release()
                self._wait_before_retry("no se pudo abrir")
                continue

            if isinstance(self.source_clean, int):
                self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                self.cap.set(cv2.CAP_PROP_FPS, 30)

            # Archivo de video (pruebas): en bucle y a su velocidad natural.
            frame_delay = 0.01
            if is_file:
                fps = self.cap.get(cv2.CAP_PROP_FPS)
                frame_delay = 1.0 / fps if fps and 1 < fps < 120 else 1.0 / 25
            fallos = 0
            self.last_frame_time = time.time()

            # ── leer ──
            while self.running:
                ret, frame = self.cap.read()
                if not ret and is_file:
                    self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)  # volver al inicio del video
                    fallos += 1
                    if fallos >= 3:  # el archivo no se puede leer
                        self._set(False, "Archivo de video ilegible")
                    time.sleep(0.05)
                    continue
                if ret and frame is not None:
                    fallos = 0
                    self.attempt = 0
                    self.last_frame_time = time.time()
                    with self.lock:
                        self.frame = frame
                        self.ret = True
                        self.connected = True
                        self.status = "Conectada"
                    time.sleep(frame_delay)  # respeta el ritmo del video / evita gastar CPU
                    continue
                fallos += 1
                if time.time() - self.last_frame_time > self.STALL_S or fallos >= 50:
                    break  # transmisión caída: cerrar y reconectar
                time.sleep(0.05)

            self._release()
            if self.running:
                self._wait_before_retry("transmisión interrumpida")

        self._release()

    def _release(self):
        try:
            if self.cap is not None:
                self.cap.release()
        except Exception:  # noqa: BLE001
            pass
        self.cap = None

    def get_frame(self):
        with self.lock:
            if self.ret and self.frame is not None:
                return True, self.frame.copy()
            return False, None

    def stop(self):
        self.running = False
        time.sleep(0.1)


@dataclass
class CameraState:
    id: str
    name: str
    source: str                     
    location: str = ""              
    gps: tuple = None               
    thread: CameraThread = None
    last_fire_dets: list = field(default_factory=list)
    last_smoke_dets: list = field(default_factory=list)
    last_alert_time: float = 0.0
    last_alert_state: str = None
    last_inference_time: float = 0.0
    last_detection_time: float = 0.0      # última vez que se vio fuego o humo
    history: list = field(default_factory=list)   # últimas inferencias (True = hubo detección)
    conf_history: list = field(default_factory=list)   # (momento, confianza máx. fuego, confianza máx. humo)


class CameraManager:
    CONFIG_PATH = "cameras_config.json"

    def __init__(self):
        self.cameras: dict[str, CameraState] = {}
        self._rotation_order: list[str] = []
        self._rotation_idx = 0
        self._next_id = 1
        self.load_config()

    def auto_detect_and_add(self):
        """Escaneo rápido sin bloquear dispositivos en uso."""
        existing_sources = [str(c.source) for c in self.cameras.values()]
        added_count = 0

        # Si no hay cámaras agregadas, registra la webcam principal (0) por defecto
        if "0" not in existing_sources:
            cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
            if cap.isOpened():
                ret, _ = cap.read()
                cap.release()
                if ret:
                    self.add_camera(name="Webcam Principal", source="0", location="Integrada local")
                    added_count += 1

        return added_count

    def add_camera(self, name, source, location="", gps=None, start=True):
        cam_id = f"cam_{self._next_id}"
        self._next_id += 1
        cam = CameraState(id=cam_id, name=name, source=str(source), location=location, gps=gps)
        if start:
            cam.thread = CameraThread(source)
            cam.thread.start()
        self.cameras[cam_id] = cam
        self._rotation_order.append(cam_id)
        self.save_config()
        return cam_id

    def edit_camera(self, cam_id, name, source, location="", gps=None):
        if cam_id not in self.cameras:
            return False
        cam = self.cameras[cam_id]
        
        if str(cam.source) != str(source):
            if cam.thread:
                cam.thread.stop()
            cam.source = str(source)
            cam.thread = CameraThread(source)
            cam.thread.start()

        cam.name = name
        cam.location = location
        cam.gps = gps
        self.save_config()
        return True

    def remove_camera(self, cam_id):
        cam = self.cameras.pop(cam_id, None)
        if cam and cam.thread:
            cam.thread.stop()
        if cam_id in self._rotation_order:
            self._rotation_order.remove(cam_id)
        self.save_config()

    def stop_all(self):
        for cam in self.cameras.values():
            if cam.thread:
                cam.thread.stop()

    def save_config(self):
        data = [
            {"name": c.name, "source": c.source, "location": c.location, "gps": c.gps}
            for c in self.cameras.values()
        ]
        with open(self.CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def load_config(self):
        if not os.path.exists(self.CONFIG_PATH):
            return
        with open(self.CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        for entry in data:
            self.add_camera(
                entry["name"], 
                entry["source"], 
                entry.get("location", ""),
                tuple(entry["gps"]) if entry.get("gps") else None
            )

    def next_camera_for_inference(self):
        if not self._rotation_order:
            return None
        n = len(self._rotation_order)
        for _ in range(n):
            self._rotation_idx = (self._rotation_idx + 1) % n
            cam_id = self._rotation_order[self._rotation_idx]
            cam = self.cameras.get(cam_id)
            if cam and cam.thread and cam.thread.connected:
                return cam
        return None

    def estimated_latency_seconds(self, seconds_per_inference=0.8):
        n = len(self._rotation_order)
        return n * seconds_per_inference if n else 0