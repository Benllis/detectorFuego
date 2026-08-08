import sys
import os
import cv2
import time
import json
import threading
import numpy as np
import onnxruntime as ort
import customtkinter as ctk
from PIL import Image

def resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)

class CameraThread(threading.Thread):
    def __init__(self, src=0):
        super().__init__()
        self.cap = cv2.VideoCapture(src, cv2.CAP_DSHOW)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        
        self.frame = None
        self.ret = False
        self.running = True
        self.lock = threading.Lock()

    def run(self):
        while self.running:
            ret, frame = self.cap.read()
            if ret:
                with self.lock:
                    self.frame = frame
                    self.ret = ret
            time.sleep(0.01)

    def get_frame(self):
        with self.lock:
            if self.ret and self.frame is not None:
                return True, self.frame.copy()
            return False, None

    def stop(self):
        self.running = False
        time.sleep(0.1)
        if self.cap.isOpened():
            self.cap.release()

class FireWatchApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("FireWatch — Emisor de Alertas para Central")
        self.geometry("900x750")

        # Cargar Modelos ONNX
        path_a = resource_path(os.path.join("..", "models", "modelo_a_fire.onnx"))
        path_b = resource_path(os.path.join("..", "models", "modelo_b_smoke.onnx"))
        
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL

        self.session_a = ort.InferenceSession(path_a, opts, providers=['CPUExecutionProvider'])
        self.session_b = ort.InferenceSession(path_b, opts, providers=['CPUExecutionProvider'])

        self.SZ = 416  
        self.CONF_A = 0.35
        self.CONF_B = 0.55

        self.frame_count = 0
        self.process_every_n = 15
        self.last_fire_dets = []
        self.last_smoke_dets = []

        # Configuración del Sistema de Alertas y Envíos
        self.alert_folder = "alertas_pendientes"
        os.makedirs(self.alert_folder, exist_ok=True)

        self.last_alert_time = 0
        self.last_alert_state = None  # Almacena "SMOKE", "FIRE" o "CRITICAL"

        # Tiempos de Cooldown en segundos
        self.COOLDOWN_SMOKE = 600      # 10 Minutos
        self.COOLDOWN_FIRE = 300       # 5 Minutos
        self.COOLDOWN_CRITICAL = 180   # 3 Minutos

        # Interfaz
        self.label_video = ctk.CTkLabel(self, text="")
        self.label_video.pack(pady=10, fill="both", expand=True)

        self.label_status = ctk.CTkLabel(self, text="Cámara desactivada", font=("Arial", 16, "bold"))
        self.label_status.pack(pady=5)

        self.btn_frame = ctk.CTkFrame(self)
        self.btn_frame.pack(pady=10, fill="x")

        self.btn_start = ctk.CTkButton(self.btn_frame, text="📷 Iniciar Cámara", command=self.start_cam)
        self.btn_start.pack(side="left", padx=10)

        self.btn_stop = ctk.CTkButton(self.btn_frame, text="⏹ Detener", command=self.stop_cam, state="disabled")
        self.btn_stop.pack(side="left", padx=10)

        self.cam_thread = None
        self.is_running = False

    def start_cam(self):
        self.cam_thread = CameraThread(0)
        self.cam_thread.start()
        self.is_running = True
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.update_frame()

    def stop_cam(self):
        self.is_running = False
        if self.cam_thread:
            self.cam_thread.stop()
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        self.label_status.configure(text="Cámara detenida", text_color="black")

    def run_model(self, session, tensor, conf_thresh):
        input_name = session.get_inputs()[0].name
        output_name = session.get_outputs()[0].name
        out = session.run([output_name], {input_name: tensor})[0]
        
        raw = out[0]
        if raw.shape[0] < raw.shape[1]:
            raw = raw.T
            
        dets = []
        for row in raw:
            score = row[4]
            if score >= conf_thresh:
                cx, cy, w, h = row[0], row[1], row[2], row[3]
                dets.append([cx, cy, w, h, score])
                
        return self.nms(dets, 0.45)

    def nms(self, dets, thresh):
        if not dets: return []
        dets = sorted(dets, key=lambda x: x[4], reverse=True)
        keep = []
        while dets:
            curr = dets.pop(0)
            keep.append(curr)
            dets = [d for d in dets if self.iou(curr, d) < thresh]
        return keep

    def iou(self, box1, box2):
        ax1, ay1 = box1[0]-box1[2]/2, box1[1]-box1[3]/2
        ax2, ay2 = box1[0]+box1[2]/2, box1[1]+box1[3]/2
        bx1, by1 = box2[0]-box2[2]/2, box2[1]-box2[3]/2
        bx2, by2 = box2[0]+box2[2]/2, box2[1]+box2[3]/2
        
        inter_x = max(0, min(ax2, bx2) - max(ax1, bx1))
        inter_y = max(0, min(ay2, by2) - max(ay1, by1))
        inter_area = inter_x * inter_y
        
        area1 = (ax2 - ax1) * (ay2 - ay1)
        area2 = (bx2 - bx1) * (by2 - by1)
        union = area1 + area2 - inter_area
        return inter_area / union if union > 0 else 0

    def draw_detections(self, frame, dets, color, label_text):
        h_img, w_img, _ = frame.shape
        for d in dets:
            cx, cy, w, h, conf = d
            x1 = int((cx - w/2) * w_img)
            y1 = int((cy - h/2) * h_img)
            x2 = int((cx + w/2) * w_img)
            y2 = int((cy + h/2) * h_img)
            
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            lbl = f"{label_text} {int(conf*100)}%"
            cv2.putText(frame, lbl, (x1, max(y1-10, 20)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

    def process_alert_logic(self, frame, current_state):
        """Gestiona la lógica de cooldowns y escalamiento dinámico"""
        now = time.time()
        elapsed = now - self.last_alert_time

        # Definir nivel de prioridad: SMOKE (1) < FIRE (2) < CRITICAL (3)
        priority_map = {"SMOKE": 1, "FIRE": 2, "CRITICAL": 3}
        current_priority = priority_map.get(current_state, 0)
        last_priority = priority_map.get(self.last_alert_state, 0)

        # Determinar el cooldown aplicable
        cooldown = self.COOLDOWN_CRITICAL
        if current_state == "SMOKE":
            cooldown = self.COOLDOWN_SMOKE
        elif current_state == "FIRE":
            cooldown = self.COOLDOWN_FIRE

        # Regla 1: Si subió el nivel de amenaza (ej. de solo Humo a Fuego), envía Inmediato.
        # Regla 2: Si es el mismo nivel o inferior, respeta el tiempo de Cooldown.
        should_send = False
        if current_priority > last_priority:
            should_send = True
        elif elapsed >= cooldown:
            should_send = True

        if should_send:
            self.last_alert_time = now
            self.last_alert_state = current_state
            self.dispatch_alert(frame, current_state)

    def dispatch_alert(self, frame, alert_type):
        """Genera el paquete de datos (Foto JPG + Metadata JSON) listo para consumo externo"""
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        base_name = f"{alert_type.lower()}_{timestamp}"

        img_path = os.path.join(self.alert_folder, f"{base_name}.jpg")
        json_path = os.path.join(self.alert_folder, f"{base_name}.json")

        # 1. Guardar Fotografía
        cv2.imwrite(img_path, frame)

        # 2. Guardar Metadata para la central de bomberos
        metadata = {
            "id_alerta": base_name,
            "tipo": alert_type,
            "timestamp": time.time(),
            "fecha_hora": time.strftime("%Y-%m-%d %H:%M:%S"),
            "fuego_detectado": len(self.last_fire_dets) > 0,
            "humo_detectado": len(self.last_smoke_dets) > 0,
            "imagen_asociada": f"{base_name}.jpg"
        }

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=4)

        print(f"[PAQUETE DE ALERTA GENERADO] Tipo: {alert_type} | Archivo: {base_name}")

    def update_frame(self):
        if not self.is_running:
            return

        ret, frame = self.cam_thread.get_frame()
        if ret:
            self.frame_count += 1

            if self.frame_count % self.process_every_n == 0:
                img_resized = cv2.resize(frame, (self.SZ, self.SZ))
                img_rgb = cv2.cvtColor(img_resized, cv2.COLOR_BGR2RGB)
                tensor = img_rgb.astype(np.float32) / 255.0
                tensor = np.transpose(tensor, (2, 0, 1))
                tensor = np.expand_dims(tensor, axis=0)

                # Alterna: fuego en ciclos pares, humo en ciclos impares
                if (self.frame_count // self.process_every_n) % 2 == 0:
                    self.last_fire_dets = self.run_model(self.session_a, tensor, self.CONF_A)
                else:
                    self.last_smoke_dets = self.run_model(self.session_b, tensor, self.CONF_B)

            # Dibujar detecciones sobre la transmisión
            self.draw_detections(frame, self.last_fire_dets, (0, 0, 255), "Fuego")
            self.draw_detections(frame, self.last_smoke_dets, (0, 165, 255), "Humo")

            # Evaluar Estados y Cooldowns
            if self.last_fire_dets and self.last_smoke_dets:
                self.label_status.configure(text="⬛ ALERTA MÁXIMA: FUEGO Y HUMO", text_color="black")
                self.process_alert_logic(frame, "CRITICAL")

            elif self.last_fire_dets:
                self.label_status.configure(text="⬛ ALERTA: FUEGO DETECTADO", text_color="black")
                self.process_alert_logic(frame, "FIRE")

            elif self.last_smoke_dets:
                self.label_status.configure(text="⬛ ALERTA: HUMO DETECTADO", text_color="black")
                self.process_alert_logic(frame, "SMOKE")

            else:
                self.label_status.configure(text="✅ ESTADO NORMAL", text_color="black")
                if time.time() - self.last_alert_time > 30:
                    self.last_alert_state = None

            # Actualizar visualización
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img_pil = Image.fromarray(frame_rgb)
            ctk_img = ctk.CTkImage(light_image=img_pil, dark_image=img_pil, size=(640, 480))
            self.label_video.configure(image=ctk_img)

        self.after(20, self.update_frame)


if __name__ == "__main__":
    app = FireWatchApp()
    app.mainloop()