import sys
import os
import cv2
import time
import json
import numpy as np
import onnxruntime as ort
import customtkinter as ctk
from tkinter import messagebox
from PIL import Image

from camera_manager import CameraManager


def resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)


class CameraFormDialog(ctk.CTkToplevel):
    def __init__(self, master, title="Cámara", name="", source="", location="", gps=None, on_save=None):
        super().__init__(master)
        self.title(title)
        self.geometry("440x420")
        self.on_save = on_save
        self.grab_set()

        ctk.CTkLabel(self, text="Nombre de la cámara", anchor="w").pack(fill="x", padx=20, pady=(15, 0))
        self.entry_name = ctk.CTkEntry(self, placeholder_text="Ej: Pasillo Norte / Cámara 1")
        self.entry_name.insert(0, name)
        self.entry_name.pack(fill="x", padx=20, pady=(0, 10))

        ctk.CTkLabel(self, text="Fuente de video (0, 1... o RTSP)", anchor="w").pack(fill="x", padx=20)
        self.entry_source = ctk.CTkEntry(self, placeholder_text="0 para webcam o rtsp://...")
        self.entry_source.insert(0, source)
        self.entry_source.pack(fill="x", padx=20, pady=(0, 10))

        ctk.CTkLabel(self, text="Ubicación / Descripción", anchor="w").pack(fill="x", padx=20)
        self.entry_location = ctk.CTkEntry(self, placeholder_text="Ej: Sector B - Planta Alta")
        self.entry_location.insert(0, location)
        self.entry_location.pack(fill="x", padx=20, pady=(0, 10))

        ctk.CTkLabel(self, text="Coordenadas GPS (opcional)", anchor="w").pack(fill="x", padx=20)
        gps_frame = ctk.CTkFrame(self, fg_color="transparent")
        gps_frame.pack(fill="x", padx=20, pady=(0, 10))

        self.entry_lat = ctk.CTkEntry(gps_frame, placeholder_text="Latitud")
        self.entry_lon = ctk.CTkEntry(gps_frame, placeholder_text="Longitud")
        if gps:
            self.entry_lat.insert(0, str(gps[0]))
            self.entry_lon.insert(0, str(gps[1]))

        self.entry_lat.pack(side="left", expand=True, fill="x", padx=(0, 5))
        self.entry_lon.pack(side="left", expand=True, fill="x")

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20, pady=15)
        ctk.CTkButton(btn_frame, text="Cancelar", fg_color="gray40", command=self.destroy).pack(side="left", expand=True, padx=(0, 5))
        ctk.CTkButton(btn_frame, text="Guardar", command=self._submit).pack(side="left", expand=True, padx=(5, 0))

    def _submit(self):
        name = self.entry_name.get().strip() or "Cámara sin nombre"
        source = self.entry_source.get().strip()
        location = self.entry_location.get().strip()

        if not source:
            messagebox.showerror("Error", "Debes indicar una fuente de video (0 o URL RTSP)")
            return

        gps = None
        lat, lon = self.entry_lat.get().strip(), self.entry_lon.get().strip()
        if lat and lon:
            try:
                gps = (float(lat), float(lon))
            except ValueError:
                messagebox.showerror("Error", "Coordenadas GPS inválidas")
                return

        if self.on_save:
            self.on_save(name, source, location, gps)
        self.destroy()


class FireWatchApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("FireWatch — Panel Multi-Cámara")
        self.geometry("1200x760")

        # ── Modelos ONNX ──
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
        self.INFERENCE_INTERVAL_MS = 800

        self.COOLDOWN_SMOKE = 600
        self.COOLDOWN_FIRE = 300
        self.COOLDOWN_CRITICAL = 180

        self.alert_folder = "alertas_pendientes"
        os.makedirs(self.alert_folder, exist_ok=True)

        self.cam_manager = CameraManager()
        self.selected_cam_id = None
        self.cam_buttons = {}

        self._build_ui()
        self.cam_manager.auto_detect_and_add()
        
        # Selecciona la primera cámara disponible por defecto
        if self.cam_manager.cameras:
            self.selected_cam_id = list(self.cam_manager.cameras.keys())[0]

        self._refresh_camera_list()

        self.after(self.INFERENCE_INTERVAL_MS, self._inference_tick)
        self.after(30, self._display_tick)

    def _build_ui(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        sidebar = ctk.CTkFrame(self, width=320)
        sidebar.grid(row=0, column=0, sticky="ns", padx=(10, 5), pady=10)
        sidebar.grid_propagate(False)

        ctk.CTkLabel(sidebar, text="Cámaras", font=("Arial", 16, "bold")).pack(pady=(10, 2))

        self.lbl_latency = ctk.CTkLabel(sidebar, text="", font=("Arial", 11), text_color="gray")
        self.lbl_latency.pack(pady=(0, 5))

        self.camera_list_frame = ctk.CTkScrollableFrame(sidebar, width=300, height=480)
        self.camera_list_frame.pack(fill="both", expand=True, padx=5)

        btn_box = ctk.CTkFrame(sidebar, fg_color="transparent")
        btn_box.pack(fill="x", pady=10, padx=5)

        ctk.CTkButton(btn_box, text="🔍 Auto-detectar", command=self._auto_detect).pack(side="left", expand=True, padx=(0, 2))
        ctk.CTkButton(btn_box, text="➕ Agregar", command=self._open_add_dialog).pack(side="right", expand=True, padx=(2, 0))

        main = ctk.CTkFrame(self)
        main.grid(row=0, column=1, sticky="nsew", padx=(5, 10), pady=10)

        self.label_video = ctk.CTkLabel(main, text="Selecciona una cámara para ver su transmisión")
        self.label_video.pack(pady=10, fill="both", expand=True)

        self.label_status = ctk.CTkLabel(main, text="Sin cámara seleccionada", font=("Arial", 16, "bold"))
        self.label_status.pack(pady=5)

    def _refresh_camera_list(self):
        for widget in self.camera_list_frame.winfo_children():
            widget.destroy()

        self.cam_buttons.clear()

        for cam_id, cam in self.cam_manager.cameras.items():
            row = ctk.CTkFrame(self.camera_list_frame)
            row.pack(fill="x", pady=3)

            connected = cam.thread.connected if cam.thread else False
            dot = "🟢" if connected else "🔴"

            btn_select = ctk.CTkButton(
                row, text=f"{dot} {cam.name}", anchor="w",
                fg_color="#1f3864" if cam_id == self.selected_cam_id else "gray25",
                command=lambda cid=cam_id: self._select_camera(cid)
            )
            btn_select.pack(side="left", fill="x", expand=True, padx=(0, 2))
            self.cam_buttons[cam_id] = btn_select

            ctk.CTkButton(
                row, text="✏️", width=30, fg_color="gray35",
                command=lambda cid=cam_id: self._open_edit_dialog(cid)
            ).pack(side="right", padx=(0, 2))

            ctk.CTkButton(
                row, text="✕", width=30, fg_color="gray40",
                command=lambda cid=cam_id: self._remove_camera(cid)
            ).pack(side="right")

        n = len(self.cam_manager.cameras)
        latency = self.cam_manager.estimated_latency_seconds(self.INFERENCE_INTERVAL_MS / 1000)
        self.lbl_latency.configure(text=f"{n} cámara(s) — chequeo cada ~{latency:.1f}s")

    def _update_connection_indicators(self):
        for cam_id, cam in self.cam_manager.cameras.items():
            if cam_id in self.cam_buttons:
                connected = cam.thread.connected if cam.thread else False
                dot = "🟢" if connected else "🔴"
                self.cam_buttons[cam_id].configure(text=f"{dot} {cam.name}")

    def _auto_detect(self):
        added = self.cam_manager.auto_detect_and_add()
        self._refresh_camera_list()
        if added > 0:
            messagebox.showinfo("Auto-detección", f"Se agregaron {added} cámara(s) nueva(s).")
        else:
            messagebox.showinfo("Auto-detección", "No se encontraron nuevas cámaras locales.")

    def _open_add_dialog(self):
        CameraFormDialog(
            self, title="Agregar cámara",
            on_save=lambda name, src, loc, gps: self._add_camera(name, src, loc, gps)
        )

    def _open_edit_dialog(self, cam_id):
        cam = self.cam_manager.cameras.get(cam_id)
        if not cam:
            return
        CameraFormDialog(
            self, title=f"Editar {cam.name}",
            name=cam.name, source=cam.source, location=cam.location, gps=cam.gps,
            on_save=lambda name, src, loc, gps: self._edit_camera(cam_id, name, src, loc, gps)
        )

    def _add_camera(self, name, source, location, gps):
        cam_id = self.cam_manager.add_camera(name, source, location=location, gps=gps)
        self._select_camera(cam_id)

    def _edit_camera(self, cam_id, name, source, location, gps):
        self.cam_manager.edit_camera(cam_id, name, source, location=location, gps=gps)
        self._refresh_camera_list()

    def _remove_camera(self, cam_id):
        if messagebox.askyesno("Confirmar", "¿Eliminar esta cámara?"):
            self.cam_manager.remove_camera(cam_id)
            if self.selected_cam_id == cam_id:
                self.selected_cam_id = list(self.cam_manager.cameras.keys())[0] if self.cam_manager.cameras else None
            self._refresh_camera_list()

    def _select_camera(self, cam_id):
        self.selected_cam_id = cam_id
        self._refresh_camera_list()

    def _inference_tick(self):
        cam = self.cam_manager.next_camera_for_inference()
        if cam and cam.thread:
            ret, frame = cam.thread.get_frame()
            if ret:
                dets_fire, dets_smoke = self._run_both_models(frame)
                cam.last_fire_dets = dets_fire
                cam.last_smoke_dets = dets_smoke
                cam.last_inference_time = time.time()
                self._evaluate_alert(cam, frame)

        self.after(self.INFERENCE_INTERVAL_MS, self._inference_tick)

    def _run_both_models(self, frame):
        img_resized = cv2.resize(frame, (self.SZ, self.SZ))
        img_rgb = cv2.cvtColor(img_resized, cv2.COLOR_BGR2RGB)
        tensor = img_rgb.astype(np.float32) / 255.0
        tensor = np.transpose(tensor, (2, 0, 1))
        tensor = np.expand_dims(tensor, axis=0)

        dets_fire = self._run_model(self.session_a, tensor, self.CONF_A)
        dets_smoke = self._run_model(self.session_b, tensor, self.CONF_B)
        return dets_fire, dets_smoke

    def _run_model(self, session, tensor, conf_thresh):
        input_name = session.get_inputs()[0].name
        output_name = session.get_outputs()[0].name
        out = session.run([output_name], {input_name: tensor})[0]
        raw = out[0]
        if raw.shape[0] < raw.shape[1]:
            raw = raw.T
        dets = [[r[0], r[1], r[2], r[3], r[4]] for r in raw if r[4] >= conf_thresh]
        return self._nms(dets, 0.45)

    def _nms(self, dets, thresh):
        if not dets:
            return []
        dets = sorted(dets, key=lambda x: x[4], reverse=True)
        keep = []
        while dets:
            curr = dets.pop(0)
            keep.append(curr)
            dets = [d for d in dets if self._iou(curr, d) < thresh]
        return keep

    def _iou(self, box1, box2):
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

    def _evaluate_alert(self, cam, frame):
        has_fire = len(cam.last_fire_dets) > 0
        has_smoke = len(cam.last_smoke_dets) > 0

        if has_fire and has_smoke:
            state = "CRITICAL"
        elif has_fire:
            state = "FIRE"
        elif has_smoke:
            state = "SMOKE"
        else:
            state = None
            if time.time() - cam.last_alert_time > 30:
                cam.last_alert_state = None
            return

        now = time.time()
        priority_map = {"SMOKE": 1, "FIRE": 2, "CRITICAL": 3}
        current_priority = priority_map.get(state, 0)
        last_priority = priority_map.get(cam.last_alert_state, 0)

        cooldown = {"SMOKE": self.COOLDOWN_SMOKE, "FIRE": self.COOLDOWN_FIRE,
                    "CRITICAL": self.COOLDOWN_CRITICAL}[state]
        elapsed = now - cam.last_alert_time

        should_send = current_priority > last_priority or elapsed >= cooldown
        if should_send:
            cam.last_alert_time = now
            cam.last_alert_state = state
            self._dispatch_alert(cam, frame, state)

    def _dispatch_alert(self, cam, frame, alert_type):
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        base_name = f"{cam.id}_{alert_type.lower()}_{timestamp}"
        img_path = os.path.join(self.alert_folder, f"{base_name}.jpg")
        json_path = os.path.join(self.alert_folder, f"{base_name}.json")

        cv2.imwrite(img_path, frame)
        metadata = {
            "id_alerta": base_name,
            "camara_id": cam.id,
            "camara_nombre": cam.name,
            "ubicacion": cam.location,
            "gps": cam.gps,
            "tipo": alert_type,
            "timestamp": time.time(),
            "fecha_hora": time.strftime("%Y-%m-%d %H:%M:%S"),
            "fuego_detectado": len(cam.last_fire_dets) > 0,
            "humo_detectado": len(cam.last_smoke_dets) > 0,
            "imagen_asociada": f"{base_name}.jpg",
        }
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=4, ensure_ascii=False)

    def _display_tick(self):
        if self.selected_cam_id:
            cam = self.cam_manager.cameras.get(self.selected_cam_id)
            if cam and cam.thread:
                ret, frame = cam.thread.get_frame()
                if ret:
                    self._draw_detections(frame, cam.last_fire_dets, (0, 0, 255), "Fuego")
                    self._draw_detections(frame, cam.last_smoke_dets, (0, 165, 255), "Humo")

                    if cam.last_fire_dets and cam.last_smoke_dets:
                        self.label_status.configure(text=f"⬛ {cam.name}: ALERTA MÁXIMA")
                    elif cam.last_fire_dets:
                        self.label_status.configure(text=f"⬛ {cam.name}: FUEGO DETECTADO")
                    elif cam.last_smoke_dets:
                        self.label_status.configure(text=f"⬛ {cam.name}: HUMO DETECTADO")
                    else:
                        self.label_status.configure(text=f"✅ {cam.name}: NORMAL")

                    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    img_pil = Image.fromarray(frame_rgb)
                    ctk_img = ctk.CTkImage(light_image=img_pil, dark_image=img_pil, size=(640, 480))
                    self.label_video.configure(image=ctk_img, text="")

        self._update_connection_indicators()
        self.after(30, self._display_tick)

    def _draw_detections(self, frame, dets, color, label_text):
        h_img, w_img, _ = frame.shape
        for d in dets:
            cx, cy, w, h, conf = d
            x1 = int((cx - w/2) * w_img)
            y1 = int((cy - h/2) * h_img)
            x2 = int((cx + w/2) * w_img)
            y2 = int((cy + h/2) * h_img)
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            cv2.putText(frame, f"{label_text} {int(conf*100)}%", (x1, max(y1-10, 20)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

    def on_close(self):
        self.cam_manager.stop_all()
        self.destroy()


if __name__ == "__main__":
    app = FireWatchApp()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()