import sys
import os
import cv2
import time
import json
import threading
import numpy as np
import onnxruntime as ort
import customtkinter as ctk
from tkinter import messagebox
from PIL import Image

from camera_manager import CameraManager
from backend_client import BackendClient
from camera_sources import BRANDS, BRAND_ORDER, brand_label, brand_from_label, build_rtsp_url, split_rtsp, source_kind, parse_gps, probe_source
from urllib.parse import unquote, urlsplit
import webbrowser
import camera_location
from camera_location import UbicacionError


def resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)


class CameraFormDialog(ctk.CTkToplevel):
    """
    Agregar / editar una cámara. Tres tipos de fuente:
      · Webcam / USB      → número (0, 1…)
      · Cámara IP (Wi-Fi) → marca + IP + usuario + clave (la clave se muestra oculta y nunca se imprime)
      · Archivo / URL     → video de prueba o una URL que el usuario ya tiene armada
    "Probar conexión" abre la fuente en segundo plano y muestra una imagen real antes de guardar.
    """
    TYPES = ["Webcam / USB", "Cámara IP (Wi-Fi)", "Archivo / URL"]

    def __init__(self, master, title="Cámara", name="", source="", location="", gps=None, on_save=None):
        super().__init__(master)
        self.title(title)
        self.geometry("480x880")
        self.on_save = on_save
        self._orig_password = ""
        self._testing = False
        self._locating = False
        self._last_loc = None
        self.after(100, self.grab_set)

        ctk.CTkLabel(self, text="Nombre de la cámara", anchor="w").pack(fill="x", padx=20, pady=(12, 0))
        self.entry_name = ctk.CTkEntry(self, placeholder_text="Ej: Patio / Cámara 1")
        self.entry_name.insert(0, name)
        self.entry_name.pack(fill="x", padx=20, pady=(0, 8))

        ctk.CTkLabel(self, text="Tipo de cámara", anchor="w").pack(fill="x", padx=20)
        self.seg_type = ctk.CTkSegmentedButton(self, values=self.TYPES, command=self._on_type)
        self.seg_type.pack(fill="x", padx=20, pady=(0, 6))

        self.host_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.host_frame.pack(fill="x", padx=20)

        # --- webcam ---
        self.f_webcam = ctk.CTkFrame(self.host_frame, fg_color="transparent")
        ctk.CTkLabel(self.f_webcam, text="Número de la cámara (0 = la primera, 1 = la segunda…)", anchor="w").pack(fill="x")
        self.entry_webcam = ctk.CTkEntry(self.f_webcam)
        self.entry_webcam.pack(fill="x", pady=(0, 4))

        # --- cámara IP ---
        self.f_ip = ctk.CTkFrame(self.host_frame, fg_color="transparent")
        ctk.CTkLabel(self.f_ip, text="Marca", anchor="w").pack(fill="x")
        self.opt_brand = ctk.CTkOptionMenu(self.f_ip, values=[brand_label(k) for k in BRAND_ORDER], command=self._on_brand)
        self.opt_brand.pack(fill="x")
        self.lbl_hint = ctk.CTkLabel(self.f_ip, text="", anchor="w", justify="left", wraplength=430, text_color="gray70")
        self.lbl_hint.pack(fill="x", pady=(2, 4))
        row = ctk.CTkFrame(self.f_ip, fg_color="transparent"); row.pack(fill="x")
        self.entry_ip = ctk.CTkEntry(row, placeholder_text="IP de la cámara (ej: 192.168.1.50)")
        self.entry_ip.pack(side="left", expand=True, fill="x", padx=(0, 5))
        self.entry_port = ctk.CTkEntry(row, width=70, placeholder_text="Puerto")
        self.entry_port.pack(side="left")
        row2 = ctk.CTkFrame(self.f_ip, fg_color="transparent"); row2.pack(fill="x", pady=(6, 0))
        self.entry_user = ctk.CTkEntry(row2, placeholder_text="Usuario de la cámara")
        self.entry_user.pack(side="left", expand=True, fill="x", padx=(0, 5))
        self.entry_pass = ctk.CTkEntry(row2, placeholder_text="Clave", show="•")
        self.entry_pass.pack(side="left", expand=True, fill="x")
        row3 = ctk.CTkFrame(self.f_ip, fg_color="transparent"); row3.pack(fill="x", pady=(6, 0))
        self.opt_stream = ctk.CTkOptionMenu(row3, values=["Calidad normal", "Calidad baja (más liviano)"], width=190)
        self.opt_stream.pack(side="left", padx=(0, 5))
        self.entry_channel = ctk.CTkEntry(row3, width=80, placeholder_text="Canal")
        self.entry_channel.insert(0, "1")
        self.entry_channel.pack(side="left", padx=(0, 5))
        self.entry_path = ctk.CTkEntry(row3, placeholder_text="Ruta (solo 'Otra')")
        self.entry_path.pack(side="left", expand=True, fill="x")
        ctk.CTkLabel(self.f_ip, text="Se usa la cuenta que TÚ creaste en tu cámara. La clave queda guardada solo en este equipo.",
                     anchor="w", justify="left", wraplength=430, text_color="gray60").pack(fill="x", pady=(4, 0))

        # --- archivo / URL ---
        self.f_file = ctk.CTkFrame(self.host_frame, fg_color="transparent")
        ctk.CTkLabel(self.f_file, text="Ruta del video de prueba o URL completa", anchor="w").pack(fill="x")
        self.entry_file = ctk.CTkEntry(self.f_file, placeholder_text="C:\\videos\\prueba.mp4 o rtsp://...")
        self.entry_file.pack(fill="x", pady=(0, 4))

        # --- probar conexión ---
        test_row = ctk.CTkFrame(self, fg_color="transparent"); test_row.pack(fill="x", padx=20, pady=(8, 0))
        self.btn_test = ctk.CTkButton(test_row, text="🔌 Probar conexión", width=150, command=self._test)
        self.btn_test.pack(side="left")
        self.lbl_test = ctk.CTkLabel(test_row, text="", anchor="w", justify="left", wraplength=290)
        self.lbl_test.pack(side="left", padx=10, fill="x", expand=True)
        self.lbl_thumb = ctk.CTkLabel(self, text="", height=1)
        self.lbl_thumb.pack(padx=20, pady=(4, 0))

        ctk.CTkLabel(self, text="Ubicación / Descripción", anchor="w").pack(fill="x", padx=20, pady=(6, 0))
        self.entry_location = ctk.CTkEntry(self, placeholder_text="Ej: Sector B - Planta Alta")
        self.entry_location.insert(0, location)
        self.entry_location.pack(fill="x", padx=20, pady=(0, 6))

        ctk.CTkLabel(self, text="Coordenadas GPS (opcional; puedes pegar «lat, lon» de Google Maps en Latitud)", anchor="w",
                     wraplength=430, justify="left").pack(fill="x", padx=20)
        gps_frame = ctk.CTkFrame(self, fg_color="transparent")
        gps_frame.pack(fill="x", padx=20, pady=(0, 6))
        self.entry_lat = ctk.CTkEntry(gps_frame, placeholder_text="Latitud")
        self.entry_lon = ctk.CTkEntry(gps_frame, placeholder_text="Longitud")
        if gps:
            self.entry_lat.insert(0, str(gps[0]))
            self.entry_lon.insert(0, str(gps[1]))
        self.entry_lat.pack(side="left", expand=True, fill="x", padx=(0, 5))
        self.entry_lon.pack(side="left", expand=True, fill="x")

        # --- ubicación automática: Windows (Wi-Fi) → dirección escrita → IP (respaldo) ---
        auto = ctk.CTkFrame(self, fg_color="transparent")
        auto.pack(fill="x", padx=20, pady=(0, 6))
        self.btn_loc_win = ctk.CTkButton(auto, text="📍 Usar mi ubicación (Wi-Fi)", command=lambda: self._loc_run(camera_location.ubicacion_windows))
        self.btn_loc_win.pack(fill="x")
        addr_row = ctk.CTkFrame(auto, fg_color="transparent"); addr_row.pack(fill="x", pady=(6, 0))
        self.entry_address = ctk.CTkEntry(addr_row, placeholder_text="O escribe la dirección: Av. Libertad 123, Viña del Mar")
        self.entry_address.pack(side="left", expand=True, fill="x", padx=(0, 5))
        self.entry_address.bind("<Return>", lambda _e: self._loc_from_address())
        self.btn_loc_addr = ctk.CTkButton(addr_row, text="🔎 Buscar", width=80, command=self._loc_from_address)
        self.btn_loc_addr.pack(side="left")
        extra = ctk.CTkFrame(auto, fg_color="transparent"); extra.pack(fill="x", pady=(6, 0))
        self.btn_loc_ip = ctk.CTkButton(extra, text="🌐 Aproximada por IP", fg_color="gray40", command=lambda: self._loc_run(camera_location.ubicacion_por_ip))
        self.btn_loc_ip.pack(side="left", expand=True, fill="x", padx=(0, 5))
        self.btn_loc_map = ctk.CTkButton(extra, text="🗺 Ver en el mapa", fg_color="gray40", state="disabled", command=self._loc_open_map)
        self.btn_loc_map.pack(side="left", expand=True, fill="x")
        self._loc_buttons = [self.btn_loc_win, self.btn_loc_addr, self.btn_loc_ip]
        self.lbl_loc = ctk.CTkLabel(auto, text="Orden recomendado: ubicación de Windows, y si falla, la dirección. La IP es solo una referencia.",
                                    anchor="w", justify="left", wraplength=430, text_color="gray60")
        self.lbl_loc.pack(fill="x", pady=(4, 0))

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20, pady=10)
        ctk.CTkButton(btn_frame, text="Cancelar", fg_color="gray40", command=self.destroy).pack(side="left", expand=True, padx=(0, 5))
        ctk.CTkButton(btn_frame, text="Guardar", command=self._submit).pack(side="left", expand=True, padx=(5, 0))

        self._load(source)

    # ---- tipos ----
    def _load(self, source):
        s = str(source).strip()
        kind = source_kind(s) if s else "webcam"
        self.opt_brand.set(brand_label("otra" if s.lower().startswith("rtsp") else "tapo"))
        if kind == "webcam":
            self.seg_type.set(self.TYPES[0]); self.entry_webcam.insert(0, s or "0")
        elif kind == "ip":
            self.seg_type.set(self.TYPES[1])
            parts = split_rtsp(s)
            self.opt_brand.set(brand_label("otra"))
            self.entry_ip.insert(0, parts["host"]); self.entry_port.insert(0, str(parts["port"]))
            self.entry_user.insert(0, parts["user"]); self.entry_path.insert(0, parts["path"])
            try:
                self._orig_password = unquote(urlsplit(s).password or "")
            except ValueError:
                self._orig_password = ""
            if self._orig_password:
                self.entry_pass.configure(placeholder_text="(sin cambios)")
        else:
            self.seg_type.set(self.TYPES[2]); self.entry_file.insert(0, s)
        self._on_brand(self.opt_brand.get())
        self._on_type(self.seg_type.get())

    def _on_type(self, value):
        for f in (self.f_webcam, self.f_ip, self.f_file):
            f.pack_forget()
        {self.TYPES[0]: self.f_webcam, self.TYPES[1]: self.f_ip, self.TYPES[2]: self.f_file}[value].pack(fill="x")

    def _on_brand(self, label):
        key = brand_from_label(label)
        self.lbl_hint.configure(text=BRANDS[key]["hint"])
        if not self.entry_port.get().strip() or self.entry_port.get().strip() in {str(b["port"]) for b in BRANDS.values()}:
            self.entry_port.delete(0, "end"); self.entry_port.insert(0, str(BRANDS[key]["port"]))

    # ---- armar la fuente ----
    def _build_source(self):
        """Devuelve el texto de la fuente (puede contener la clave: NO se muestra). Lanza ValueError con un mensaje claro."""
        t = self.seg_type.get()
        if t == self.TYPES[0]:
            n = self.entry_webcam.get().strip() or "0"
            if not n.isdigit():
                raise ValueError("El número de la webcam debe ser 0, 1, 2…")
            return n
        if t == self.TYPES[2]:
            s = self.entry_file.get().strip().strip('"')
            if not s:
                raise ValueError("Indica la ruta del video o la URL.")
            return s
        key = brand_from_label(self.opt_brand.get())
        user = self.entry_user.get().strip()
        pwd = self.entry_pass.get() or self._orig_password
        ch = self.entry_channel.get().strip()
        return build_rtsp_url(
            key, self.entry_ip.get(), self.entry_port.get().strip() or None, user, pwd,
            stream="sub" if self.opt_stream.get().startswith("Calidad baja") else "main",
            channel=int(ch) if ch.isdigit() else 1, path=self.entry_path.get())

    # ---- probar ----
    def _test(self):
        if self._testing:
            return
        try:
            source = self._build_source()
        except ValueError as err:
            self.lbl_test.configure(text=f"⚠️ {err}", text_color="#ff9f43"); return
        self._testing = True
        self.btn_test.configure(state="disabled")
        self.lbl_test.configure(text="Probando… (puede tardar hasta 10 s)", text_color="gray70")
        self.lbl_thumb.configure(image=None, text="", height=1)

        def work():
            res = probe_source(source, timeout_ms=8000)
            try:
                self.after(0, lambda: self._test_done(*res))
            except Exception:  # noqa: BLE001  (el diálogo se cerró mientras probaba)
                pass
        threading.Thread(target=work, daemon=True).start()

    def _test_done(self, ok, msg, frame, secs):
        self._testing = False
        self.btn_test.configure(state="normal")
        self.lbl_test.configure(text=("✅ " if ok else "❌ ") + msg, text_color="#2ecc71" if ok else "#ff6b6b")
        if ok and frame is not None:
            h, w = frame.shape[:2]
            tw = 320; th = max(1, int(tw * h / w))
            img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            self._thumb = ctk.CTkImage(light_image=img, dark_image=img, size=(tw, th))
            self.lbl_thumb.configure(image=self._thumb, text="", height=th)

    # ---- ubicación automática ----
    def _loc_status(self, text, ok=None):
        color = "#2ecc71" if ok else ("#ff6b6b" if ok is False else "gray70")
        self.lbl_loc.configure(text=text, text_color=color)

    def _loc_from_address(self):
        self._loc_run(camera_location.buscar_direccion, self.entry_address.get())

    def _loc_run(self, fn, *args):
        if self._locating:
            return
        self._locating = True
        for b in self._loc_buttons:
            b.configure(state="disabled")
        self._loc_status("Buscando la ubicación… (Windows puede tardar hasta 20 s)")

        def work():
            try:
                res, err = fn(*args), None
            except UbicacionError as e:
                res, err = None, str(e)
            except Exception as e:  # noqa: BLE001  (no dejar el botón bloqueado ante un error imprevisto)
                res, err = None, f"Error inesperado: {str(e)[:100]}"
            try:
                self.after(0, lambda: self._loc_done(res, err))
            except Exception:  # noqa: BLE001  (el diálogo se cerró mientras buscaba)
                pass
        threading.Thread(target=work, daemon=True).start()

    def _loc_done(self, res, err):
        self._locating = False
        for b in self._loc_buttons:
            b.configure(state="normal")
        if err:
            hint = ""
            if "Windows" in err:
                hint = "  Prueba escribiendo la dirección."
            self._loc_status("⚠️ " + err + hint, False)
            return
        self.entry_lat.delete(0, "end"); self.entry_lat.insert(0, f"{res['lat']:.6f}")
        self.entry_lon.delete(0, "end"); self.entry_lon.insert(0, f"{res['lon']:.6f}")
        if res["fuente"] == "direccion" and not self.entry_location.get().strip():
            self.entry_location.insert(0, self.entry_address.get().strip())
        self._last_loc = res
        self.btn_loc_map.configure(state="normal")
        self._loc_status("✅ " + res["detalle"] + ". Revisa el punto en el mapa antes de guardar.", not res.get("aproximada"))

    def _loc_open_map(self):
        try:
            lat, lon = parse_gps(self.entry_lat.get(), self.entry_lon.get()) or (None, None)
        except ValueError as err:
            self._loc_status("⚠️ " + str(err), False); return
        if lat is None:
            self._loc_status("Aún no hay coordenadas que mostrar.", False); return
        webbrowser.open(camera_location.url_mapa(lat, lon))

    # ---- guardar ----
    def _submit(self):
        name = self.entry_name.get().strip() or "Cámara sin nombre"
        location = self.entry_location.get().strip()
        try:
            source = self._build_source()
            gps = parse_gps(self.entry_lat.get(), self.entry_lon.get())
        except ValueError as err:
            messagebox.showerror("Revisa los datos", str(err))
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
        # Imágenes alargadas (16:9, vertical...) se analizan en recuadros cuadrados con
        # solape: reducir todo el cuadro a 416x416 vuelve diminutas las llamas lejanas.
        self.TILE_MIN_ASPECT = 1.5
        self._infer_busy = False

        self.COOLDOWN_SMOKE = 600
        self.COOLDOWN_FIRE = 300
        # Mientras dure un incendio CRITICAL se repite el aviso cada COOLDOWN_CRITICAL s (la central ya tiene
        # la alerta pendiente; el recordatorio es para casos reales). Para la demo con un video en bucle son
        # 10 min; en producción se puede bajar sin tocar el código: set FIREWATCH_COOLDOWN_CRITICAL=180
        self.COOLDOWN_CRITICAL = int(os.environ.get("FIREWATCH_COOLDOWN_CRITICAL", "600"))
        # Un destello suelto no dispara alerta: fuego (o humo) debe verse en CONFIRM_MIN de las
        # últimas CONFIRM_WINDOW inferencias DE ESA CÁMARA, y esas inferencias no pueden ser más
        # viejas que CONFIRM_MAX_AGE s (si no, un destello de hace una hora se sumaría al de ahora).
        # Un incendio se considera terminado (y una detección nueva vuelve a avisar) solo tras
        # INCIDENT_RESET s sin ver nada.
        # El fuego da más falsos positivos (luces, atardeceres) y exige 3 de 5; el humo casi no
        # (medido ~0,7 %) pero es intermitente y tenue, así que basta con 2 de 5.
        self.CONFIRM_MIN_FIRE = 3
        self.CONFIRM_MIN_SMOKE = 2
        self.CONFIRM_WINDOW = 5
        self.CONFIRM_MAX_AGE = 30
        self.INCIDENT_RESET = 180

        # Foto manual "sin detección": el operador de la cámara la envía a mano cuando ve algo y los modelos no
        # (los modelos de fuego/humo fallan en parte de los casos). Nunca sale sola, y hay una espera mínima
        # entre envíos de la misma cámara para que un clic repetido no llene la bandeja de la central.
        self.MANUAL_COOLDOWN = 30

        self.alert_folder = "alertas_pendientes"
        os.makedirs(self.alert_folder, exist_ok=True)

        # Envío de alertas al backend (la carpeta de arriba funciona como cola con reintentos)
        self.backend = BackendClient(self.alert_folder)

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

        self.btn_snapshot = ctk.CTkButton(
            main, text="📷 Enviar foto al operador (sin detección)", fg_color="gray35",
            command=self._snapshot_clicked)
        self.btn_snapshot.pack(pady=(0, 10))

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
        self.lbl_latency.configure(text=f"{n} cámara(s) — chequeo cada ~{latency:.1f}s\nBackend: {self.backend.status}")

    def _update_connection_indicators(self):
        n = len(self.cam_manager.cameras)
        latency = self.cam_manager.estimated_latency_seconds(self.INFERENCE_INTERVAL_MS / 1000)
        texto = f"{n} cámara(s) — chequeo cada ~{latency:.1f}s\nBackend: {self.backend.status}"
        if texto != getattr(self, "_last_latency_text", None):
            self._last_latency_text = texto
            self.lbl_latency.configure(text=texto)
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
        if not self._infer_busy:
            cam = self.cam_manager.next_camera_for_inference()
            if cam and cam.thread:
                ret, frame = cam.thread.get_frame()
                if ret:
                    self._infer_busy = True
                    threading.Thread(target=self._infer_job, args=(cam, frame), daemon=True).start()
        self.after(self.INFERENCE_INTERVAL_MS, self._inference_tick)

    def _infer_job(self, cam, frame):
        """Corre los modelos fuera del hilo de la interfaz para que la ventana no se congele."""
        try:
            dets_fire, dets_smoke = self._run_both_models(frame)
            cam.last_fire_dets = dets_fire
            cam.last_smoke_dets = dets_smoke
            cam.last_inference_time = time.time()
            self._evaluate_alert(cam, frame)
        except Exception as e:
            print(f"[inferencia] error en {cam.id}: {e}")
        finally:
            self._infer_busy = False

    def _tiles(self, frame):
        """Recuadros (x0, y0, ancho, alto). Imagen casi cuadrada: un solo recuadro (el cuadro entero)."""
        h, w = frame.shape[:2]
        lado, mayor = min(h, w), max(h, w)
        if mayor / lado < self.TILE_MIN_ASPECT:
            return [(0, 0, w, h)]
        n = int(np.ceil((mayor - lado) / (lado * 0.5))) + 1      # ~50 % de solape
        pos = np.linspace(0, mayor - lado, n).astype(int)
        if h > w:
            return [(0, int(p), w, lado) for p in pos]
        return [(int(p), 0, lado, h) for p in pos]

    def _run_both_models(self, frame):
        """Devuelve cajas [cx, cy, w, h, conf] NORMALIZADAS (0-1) respecto del cuadro completo."""
        H, W = frame.shape[:2]
        dets_fire, dets_smoke = [], []
        for x0, y0, tw, th in self._tiles(frame):
            crop = frame[y0:y0 + th, x0:x0 + tw]
            img_rgb = cv2.cvtColor(cv2.resize(crop, (self.SZ, self.SZ)), cv2.COLOR_BGR2RGB)
            tensor = np.expand_dims(np.transpose(img_rgb.astype(np.float32) / 255.0, (2, 0, 1)), axis=0)
            for session, conf, acc in ((self.session_a, self.CONF_A, dets_fire),
                                       (self.session_b, self.CONF_B, dets_smoke)):
                for cx, cy, w, h, c in self._run_model(session, tensor, conf, nms=False):
                    # el modelo entrega píxeles de 416x416 del recuadro -> fracción del cuadro completo
                    acc.append([(x0 + cx / self.SZ * tw) / W, (y0 + cy / self.SZ * th) / H,
                                (w / self.SZ * tw) / W, (h / self.SZ * th) / H, float(c)])
        return self._nms(dets_fire, 0.45), self._nms(dets_smoke, 0.45)

    def _run_model(self, session, tensor, conf_thresh, nms=True):
        input_name = session.get_inputs()[0].name
        output_name = session.get_outputs()[0].name
        out = session.run([output_name], {input_name: tensor})[0]
        raw = out[0]
        if raw.shape[0] < raw.shape[1]:
            raw = raw.T
        dets = [[r[0], r[1], r[2], r[3], r[4]] for r in raw if r[4] >= conf_thresh]
        return self._nms(dets, 0.45) if nms else dets

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

    @staticmethod
    def _max_conf(dets):
        """Mayor confianza de una lista de cajas [cx, cy, w, h, conf] (0 si no hay ninguna)."""
        try:
            return max((float(d[4]) for d in dets), default=0.0)
        except (TypeError, IndexError, ValueError):
            return 0.0

    def _alert_summary(self, cam, alert_type):
        """Qué se informa en la alerta: lo CONFIRMADO (el tipo), no solo lo que se vio en el último cuadro.
        Devuelve (fuego_detectado, humo_detectado, confianza máxima de lo confirmado en la ventana)."""
        fire = alert_type in ("FIRE", "CRITICAL")
        smoke = alert_type in ("SMOKE", "CRITICAL")
        hist = list(getattr(cam, "conf_history", []))
        hist.append((0, self._max_conf(cam.last_fire_dets), self._max_conf(cam.last_smoke_dets)))
        confs = [h[1] for h in hist if fire] + [h[2] for h in hist if smoke]
        best = max(confs, default=0.0)
        return fire, smoke, (round(best, 3) if best > 0 else None)

    def _evaluate_alert(self, cam, frame):
        has_fire = len(cam.last_fire_dets) > 0
        has_smoke = len(cam.last_smoke_dets) > 0
        now = time.time()

        # Historial de esta cámara: (momento, hubo fuego, hubo humo). Se descartan las muestras
        # viejas y se mantienen solo las últimas CONFIRM_WINDOW.
        cam.history.append((now, has_fire, has_smoke))
        cam.conf_history.append((now, self._max_conf(cam.last_fire_dets), self._max_conf(cam.last_smoke_dets)))
        cam.conf_history[:] = [h for h in cam.conf_history if now - h[0] <= self.CONFIRM_MAX_AGE][-self.CONFIRM_WINDOW:]
        cam.history[:] = [h for h in cam.history if now - h[0] <= self.CONFIRM_MAX_AGE][-self.CONFIRM_WINDOW:]

        if has_fire or has_smoke:
            nf = sum(1 for h in cam.history if h[1]); ns = sum(1 for h in cam.history if h[2])
            mx = max([d[4] for d in list(cam.last_fire_dets) + list(cam.last_smoke_dets)] or [0])
            print(f"[confirm] {cam.id} fuego={nf}/{self.CONFIRM_MIN_FIRE} humo={ns}/{self.CONFIRM_MIN_SMOKE} "
                  f"muestras={len(cam.history)} confmax={mx:.2f} ultimo_estado={cam.last_alert_state}")

        if not (has_fire or has_smoke):
            if cam.last_alert_state and now - cam.last_detection_time > self.INCIDENT_RESET:
                cam.last_alert_state = None   # el incidente terminó: la próxima detección avisa de nuevo
            return

        cam.last_detection_time = now

        # Fuego y humo se confirman por separado (un destello de cada uno no suma).
        fire_ok = sum(1 for h in cam.history if h[1]) >= self.CONFIRM_MIN_FIRE
        smoke_ok = sum(1 for h in cam.history if h[2]) >= self.CONFIRM_MIN_SMOKE
        if fire_ok and smoke_ok:
            state = "CRITICAL"
        elif fire_ok:
            state = "FIRE"
        elif smoke_ok:
            state = "SMOKE"
        else:
            return                            # aún no está confirmado

        priority_map = {"SMOKE": 1, "FIRE": 2, "CRITICAL": 3}
        current_priority = priority_map[state]
        last_priority = priority_map.get(cam.last_alert_state, 0)

        # Dentro de un mismo incidente:
        #   · si sube de nivel (humo -> fuego -> crítico) se avisa de inmediato;
        #   · si es del mismo nivel, se vuelve a avisar solo pasado el cooldown;
        #   · si baja de nivel (p. ej. humo suelto después de un FIRE/CRITICAL) NO se avisa:
        #     es el mismo incendio y ya está informado.
        cooldown = {"SMOKE": self.COOLDOWN_SMOKE, "FIRE": self.COOLDOWN_FIRE,
                    "CRITICAL": self.COOLDOWN_CRITICAL}[state]
        elapsed = now - cam.last_alert_time

        if current_priority > last_priority:
            should_send = True
        elif current_priority == last_priority:
            should_send = elapsed >= cooldown
        else:
            should_send = False
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
        fuego, humo, confianza = self._alert_summary(cam, alert_type)
        metadata = {
            "id_alerta": base_name,
            "camara_id": cam.id,
            "camara_nombre": cam.name,
            "ubicacion": cam.location,
            "gps": cam.gps,
            "tipo": alert_type,
            "timestamp": time.time(),
            "fecha_hora": time.strftime("%Y-%m-%d %H:%M:%S"),
            "fuego_detectado": fuego,
            "humo_detectado": humo,
            "confianza": confianza,
            "imagen_asociada": f"{base_name}.jpg",
        }
        # Escritura atómica: el envío nunca ve un JSON a medias.
        tmp_path = json_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=4, ensure_ascii=False)
        os.replace(tmp_path, json_path)
        self.backend.notify()

    def send_snapshot(self, cam, frame, now=None):
        """Envía a la central la foto actual de `cam` como alerta NO_DETECTION (el operador decide si es real).
        Devuelve (enviada, mensaje). No toca el estado de incidente: una foto manual no apaga ni reemplaza
        una alerta automática."""
        now = time.time() if now is None else now
        if frame is None:
            return False, "La cámara no tiene imagen en este momento."
        if cam.last_alert_state:
            return False, "Esta cámara ya tiene una alerta activa; la central ya fue avisada."
        espera = self.MANUAL_COOLDOWN - (now - getattr(cam, "last_manual_time", 0.0))
        if espera > 0:
            return False, f"Espera {int(espera) + 1} s antes de enviar otra foto de esta cámara."
        cam.last_manual_time = now
        self._dispatch_alert(cam, frame, "NO_DETECTION")
        return True, "Foto enviada a la central como alerta sin detección."

    def _snapshot_clicked(self):
        cam = self.cam_manager.cameras.get(self.selected_cam_id) if self.selected_cam_id else None
        if not cam or not cam.thread:
            messagebox.showinfo("Enviar foto", "Selecciona una cámara primero.")
            return
        if not messagebox.askyesno(
                "Enviar foto al operador",
                f"Se enviará una foto de «{cam.name}» a la central como alerta SIN detección "
                "(el operador decidirá si es real). ¿Enviar?"):
            return
        ret, frame = cam.thread.get_frame()
        ok, msg = self.send_snapshot(cam, frame if ret else None)
        (messagebox.showinfo if ok else messagebox.showwarning)("Enviar foto", msg)

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
                else:
                    self.label_status.configure(text=f"📡 {cam.name}: {cam.thread.status}")

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
        self.backend.stop()
        self.cam_manager.stop_all()
        self.destroy()


if __name__ == "__main__":
    app = FireWatchApp()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()