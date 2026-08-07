import sys
import os
import cv2
import numpy as np
import onnxruntime as ort
import customtkinter as ctk
from PIL import Image, ImageTk

# Función clave para encontrar los modelos dentro del .exe compilado
def resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)

class FireWatchApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("FireWatch — Detector Ensemble")
        self.geometry("900x700")

        # Carga de modelos ONNX
        path_a = resource_path(os.path.join("models", "modelo_fire.onnx"))
        path_b = resource_path(os.path.join("models", "modelo_smoke.onnx"))
        
        self.session_a = ort.InferenceSession(path_a, providers=['CPUExecutionProvider'])
        self.session_b = ort.InferenceSession(path_b, providers=['CPUExecutionProvider'])

        # Componentes de Interfaz
        self.label_video = ctk.CTkLabel(self, text="")
        self.label_video.pack(pady=10, fill="both", expand=True)

        self.btn_frame = ctk.CTkFrame(self)
        self.btn_frame.pack(pady=10, fill="x")

        self.btn_start = ctk.CTkButton(self.btn_frame, text="📷 Iniciar Cámara", command=self.start_cam)
        self.btn_start.pack(side="left", padx=10)

        self.btn_stop = ctk.CTkButton(self.btn_frame, text="⏹ Detener", command=self.stop_cam, state="disabled")
        self.btn_stop.pack(side="left", padx=10)

        self.cap = None
        self.running = False

    def start_cam(self):
        self.cap = cv2.VideoCapture(0)
        self.running = True
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.update_frame()

    def stop_cam(self):
        self.running = False
        if self.cap:
            self.cap.release()
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")

    def update_frame(self):
        if not self.running:
            return

        ret, frame = self.cap.read()
        if ret:
            # Procesamiento de imagen para la UI
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(frame_rgb)
            img_tk = ImageTk.PhotoImage(image=img)
            self.label_video.img_tk = img_tk
            self.label_video.configure(image=img_tk)

        self.after(15, self.update_frame)

if __name__ == "__main__":
    app = FireWatchApp()
    app.mainloop()