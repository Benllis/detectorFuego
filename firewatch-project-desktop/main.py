import os
import json
import time
import sqlite3
import queue
import customtkinter as ctk
from PIL import Image

from database import init_db, DB_NAME
from login_view import LoginWindow

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")


class RejectModal(ctk.CTkToplevel):
    """Ventana modal obligatoria para pedir el motivo de descarte de una alerta."""
    def __init__(self, parent, on_confirm_callback):
        super().__init__(parent)
        self.title("Descartar Alerta - Falso Positivo")
        self.geometry("450x300")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self.on_confirm_callback = on_confirm_callback

        lbl_title = ctk.CTkLabel(
            self, 
            text="⚠️ Registro de Falso Positivo", 
            font=("Arial", 18, "bold"), 
            text_color="#FF4D4D"
        )
        lbl_title.pack(pady=(20, 5))

        lbl_sub = ctk.CTkLabel(
            self, 
            text="Por favor ingrese el motivo o justificación del descarte:", 
            font=("Arial", 12)
        )
        lbl_sub.pack(pady=(0, 10))

        self.txt_reason = ctk.CTkTextbox(self, width=380, height=100)
        self.txt_reason.pack(pady=10)

        self.lbl_error = ctk.CTkLabel(self, text="", text_color="#FF3333", font=("Arial", 11))
        self.lbl_error.pack(pady=2)

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(pady=10)

        btn_cancel = ctk.CTkButton(
            btn_frame, 
            text="Cancelar", 
            fg_color="gray", 
            hover_color="#555555",
            command=self.destroy
        )
        btn_cancel.pack(side="left", padx=10)

        btn_submit = ctk.CTkButton(
            btn_frame, 
            text="Confirmar Descarte", 
            fg_color="#D32F2F", 
            hover_color="#9A0007",
            command=self.submit
        )
        btn_submit.pack(side="left", padx=10)

    def submit(self):
        reason = self.txt_reason.get("1.0", "end-1c").strip()
        if not reason:
            self.lbl_error.configure(text="El motivo del descarte es obligatorio.")
            return
        self.destroy()
        self.on_confirm_callback(reason)


class FireWatchCentralApp(ctk.CTk):
    def __init__(self, current_user, on_logout_callback):
        super().__init__()

        self.current_user = current_user
        self.on_logout_callback = on_logout_callback

        self.title("FireWatch Central — Panel de Monitoreo de Incendios")
        self.geometry("1150x720")

        self.alert_folder = os.path.abspath(os.path.join("..", "firewatch-project-camara", "alertas_pendientes"))
        os.makedirs(self.alert_folder, exist_ok=True)

        self.alert_queue = queue.Queue()
        self.processed_ids = set()
        self.active_alert = None

        self.setup_ui()
        self.check_pending_alerts()

    def setup_ui(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # ------------------- BARRA LATERAL / MENÚ -------------------
        self.sidebar = ctk.CTkFrame(self, width=220, corner_radius=0)
        self.sidebar.grid(row=0, column=0, sticky="nsew")

        title_lbl = ctk.CTkLabel(
            self.sidebar, 
            text="🔥 FireWatch", 
            font=("Arial", 22, "bold"),
            text_color="#FF4D4D"
        )
        title_lbl.pack(pady=(20, 5), padx=20)

        sub_lbl = ctk.CTkLabel(self.sidebar, text="Central de Control", font=("Arial", 12))
        sub_lbl.pack(pady=(0, 15), padx=20)

        # Información de Operador
        user_card = ctk.CTkFrame(self.sidebar, fg_color="#2A2A2A", corner_radius=8)
        user_card.pack(fill="x", padx=15, pady=5)

        op_title = ctk.CTkLabel(user_card, text="Operador Activo:", font=("Arial", 10), text_color="gray")
        op_title.pack(anchor="w", padx=10, pady=(5, 0))

        op_name = ctk.CTkLabel(
            user_card, 
            text=self.current_user['nombre_completo'], 
            font=("Arial", 12, "bold")
        )
        op_name.pack(anchor="w", padx=10, pady=(0, 5))

        # Botones de Navegación
        self.btn_nav_live = ctk.CTkButton(
            self.sidebar, 
            text="🚨 Monitor en Vivo", 
            fg_color="#1F6AA5",
            command=self.show_live_view
        )
        self.btn_nav_live.pack(fill="x", padx=15, pady=(20, 5))

        self.btn_nav_history = ctk.CTkButton(
            self.sidebar, 
            text="📋 Historial de Alertas", 
            fg_color="#333333",
            hover_color="#555555",
            command=self.show_history_view
        )
        self.btn_nav_history.pack(fill="x", padx=15, pady=5)

        # Estado del Scanner
        self.lbl_scan_status = ctk.CTkLabel(
            self.sidebar, 
            text="🟢 Monitoreando red...", 
            font=("Arial", 11),
            text_color="#4CAF50"
        )
        self.lbl_scan_status.pack(pady=(15, 0))

        self.lbl_queue_count = ctk.CTkLabel(
            self.sidebar, 
            text="En cola: 0 alertas", 
            font=("Arial", 12, "bold"),
            text_color="gray"
        )
        self.lbl_queue_count.pack(pady=5)

        # Botón Cerrar Sesión
        btn_logout = ctk.CTkButton(
            self.sidebar, 
            text="🚪 Cerrar Sesión", 
            fg_color="#333333", 
            hover_color="#555555",
            command=self.logout
        )
        btn_logout.pack(side="bottom", pady=20, padx=15)

        # ------------------- VISTA 1: MONITOR EN VIVO -------------------
        self.main_content = ctk.CTkFrame(self, corner_radius=10)
        self.main_content.grid(row=0, column=1, padx=20, pady=20, sticky="nsew")
        self.main_content.grid_columnconfigure(0, weight=1)
        self.main_content.grid_rowconfigure(1, weight=1)

        self.header_frame = ctk.CTkFrame(self.main_content, fg_color="transparent")
        self.header_frame.grid(row=0, column=0, padx=20, pady=10, sticky="ew")

        self.lbl_alert_title = ctk.CTkLabel(
            self.header_frame, 
            text="✅ Sistema Normal — Sin Alertas Pendientes", 
            font=("Arial", 20, "bold"),
            text_color="#4CAF50"
        )
        self.lbl_alert_title.pack(side="left")

        self.view_frame = ctk.CTkFrame(self.main_content, fg_color="#1E1E1E")
        self.view_frame.grid(row=1, column=0, padx=20, pady=10, sticky="nsew")
        self.view_frame.grid_columnconfigure(0, weight=3)
        self.view_frame.grid_columnconfigure(1, weight=2)
        self.view_frame.grid_rowconfigure(0, weight=1)

        self.lbl_image = ctk.CTkLabel(self.view_frame, text="Esperando transmisiones...", font=("Arial", 14))
        self.lbl_image.grid(row=0, column=0, padx=15, pady=15, sticky="nsew")

        self.info_panel = ctk.CTkFrame(self.view_frame, fg_color="#262626")
        self.info_panel.grid(row=0, column=1, padx=15, pady=15, sticky="nsew")

        info_header = ctk.CTkLabel(self.info_panel, text="Detalles de la Captura", font=("Arial", 16, "bold"))
        info_header.pack(pady=10, padx=10, anchor="w")

        self.lbl_info_text = ctk.CTkLabel(self.info_panel, text="No hay datos de alerta activos.", justify="left", font=("Arial", 12))
        self.lbl_info_text.pack(pady=10, padx=10, anchor="w", fill="x")

        self.action_bar = ctk.CTkFrame(self.main_content, fg_color="transparent")
        self.action_bar.grid(row=2, column=0, padx=20, pady=15, sticky="ew")

        self.btn_confirm = ctk.CTkButton(
            self.action_bar, text="🟢 CONFIRMAR ALERTA (Despachar)", font=("Arial", 14, "bold"),
            fg_color="#2E7D32", hover_color="#1B5E20", height=45, state="disabled", command=self.confirm_alert
        )
        self.btn_confirm.pack(side="right", padx=10)

        self.btn_reject = ctk.CTkButton(
            self.action_bar, text="🔴 FALSO POSITIVO (Descartar)", font=("Arial", 14, "bold"),
            fg_color="#C62828", hover_color="#8E0000", height=45, state="disabled", command=self.reject_alert_dialog
        )
        self.btn_reject.pack(side="right", padx=10)

        # ------------------- VISTA 2: HISTORIAL DE ALERTAS -------------------
        self.history_content = ctk.CTkFrame(self, corner_radius=10)
        self.history_content.grid_columnconfigure(0, weight=1)
        self.history_content.grid_rowconfigure(1, weight=1)

        hist_header = ctk.CTkLabel(self.history_content, text="📋 Historial de Alertas Procesadas", font=("Arial", 20, "bold"))
        hist_header.grid(row=0, column=0, padx=20, pady=15, sticky="w")

        # Contenedor con scroll para la tabla
        self.scroll_history = ctk.CTkScrollableFrame(self.history_content, fg_color="#1A1A1A")
        self.scroll_history.grid(row=1, column=0, padx=20, pady=(0, 20), sticky="nsew")

    def show_live_view(self):
        """Muestra el monitor en vivo y oculta el historial."""
        self.history_content.grid_forget()
        self.main_content.grid(row=0, column=1, padx=20, pady=20, sticky="nsew")
        self.btn_nav_live.configure(fg_color="#1F6AA5")
        self.btn_nav_history.configure(fg_color="#333333")

    def show_history_view(self):
        """Muestra el historial y carga los datos desde SQLite."""
        self.main_content.grid_forget()
        self.history_content.grid(row=0, column=1, padx=20, pady=20, sticky="nsew")
        self.btn_nav_history.configure(fg_color="#1F6AA5")
        self.btn_nav_live.configure(fg_color="#333333")
        self.load_history_data()

    def load_history_data(self):
        """Consulta la base de datos y despliega la lista de alertas resueltas."""
        # Limpiar elementos previos en el scroll
        for widget in self.scroll_history.winfo_children():
            widget.destroy()

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id_alerta, tipo, fecha_hora, camara_id, direccion, estado, operador_resolucion, motivo_descarte
            FROM registro_alertas ORDER BY fecha_resolucion DESC
        """)
        records = cursor.fetchall()
        conn.close()

        if not records:
            lbl_empty = ctk.CTkLabel(self.scroll_history, text="No hay alertas registradas en el historial.", font=("Arial", 14))
            lbl_empty.pack(pady=30)
            return

        for row in records:
            id_alerta, tipo, fecha, cam_id, direccion, estado, op_res, motivo = row

            card_color = "#1E3A1E" if estado == "CONFIRMADA" else "#3A1E1E"
            status_text = "🟢 CONFIRMADA" if estado == "CONFIRMADA" else "🔴 FALSO POSITIVO"

            card = ctk.CTkFrame(self.scroll_history, fg_color=card_color, corner_radius=8)
            card.pack(fill="x", padx=10, pady=6)

            txt_info = (
                f"[{status_text}]  ID: {id_alerta}  |  Tipo: {tipo}  |  Fecha: {fecha}\n"
                f"Cámara: {cam_id}  |  Dirección: {direccion}\n"
                f"Resuelto por: {op_res}"
            )
            if estado == "FALSO_POSITIVO" and motivo:
                txt_info += f"  |  Motivo: {motivo}"

            lbl_item = ctk.CTkLabel(card, text=txt_info, justify="left", font=("Arial", 11))
            lbl_item.pack(anchor="w", padx=12, pady=10)

    def check_pending_alerts(self):
        if os.path.exists(self.alert_folder):
            files = [f for f in os.listdir(self.alert_folder) if f.endswith(".json")]
            
            for file_name in files:
                json_path = os.path.join(self.alert_folder, file_name)
                try:
                    with open(json_path, "r", encoding="utf-8") as f:
                        alert_data = json.load(f)
                        alert_id = alert_data.get("id_alerta")

                        if alert_id and alert_id not in self.processed_ids:
                            self.alert_queue.put(alert_data)
                            self.processed_ids.add(alert_id)
                except Exception as e:
                    print(f"[ERROR] No se pudo leer {json_path}: {e}")

        in_queue = self.alert_queue.qsize()
        if self.active_alert:
            in_queue += 1

        if in_queue > 0:
            self.lbl_queue_count.configure(text=f"⚠️ {in_queue} alerta(s) pendiente(s)", text_color="#FF4D4D")
        else:
            self.lbl_queue_count.configure(text="En cola: 0 alertas", text_color="gray")

        if not self.active_alert and not self.alert_queue.empty():
            next_alert = self.alert_queue.get()
            self.display_alert(next_alert)

        self.after(2000, self.check_pending_alerts)

    def display_alert(self, alert_data):
        self.active_alert = alert_data
        tipo = alert_data.get("tipo", "DESCONOCIDO")
        remaining = self.alert_queue.qsize()
        
        queue_text = f" ({remaining} más en espera)" if remaining > 0 else ""
        self.lbl_alert_title.configure(text=f"🚨 ALERTA DETECTADA: {tipo}{queue_text}", text_color="#FF3333")

        img_filename = alert_data.get("imagen_asociada", "")
        img_path = os.path.join(self.alert_folder, img_filename)

        if os.path.exists(img_path):
            pil_img = Image.open(img_path)
            ctk_img = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=(500, 380))
            self.lbl_image.configure(image=ctk_img, text="")
        else:
            self.lbl_image.configure(image=None, text="Imagen no encontrada")

        cam = alert_data.get("camara", {})
        ubic = alert_data.get("ubicacion", {})

        info_str = (
            f"• ID Alerta: {alert_data.get('id_alerta')}\n\n"
            f"• Fecha/Hora: {alert_data.get('fecha_hora')}\n\n"
            f"• Cámara: {cam.get('id')} ({cam.get('orientacion')}, Azimut {cam.get('azimut')}°)\n\n"
            f"• Dirección:\n  {ubic.get('direccion')}\n\n"
            f"• Coordenadas GPS:\n  Lat: {ubic.get('latitud')} | Lon: {ubic.get('longitud')}\n\n"
            f"• Fuente Ubicación: {ubic.get('fuente')}"
        )
        self.lbl_info_text.configure(text=info_str)

        self.btn_confirm.configure(state="normal")
        self.btn_reject.configure(state="normal")

    def save_alert_to_db(self, estado, motivo_descarte=""):
        if not self.active_alert:
            return

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()

        cam = self.active_alert.get("camara", {})
        ubic = self.active_alert.get("ubicacion", {})

        cursor.execute("""
            INSERT OR REPLACE INTO registro_alertas (
                id_alerta, tipo, fecha_hora, camara_id, orientacion, azimut,
                direccion, latitud, longitud, imagen_path, estado,
                operador_resolucion, fecha_resolucion, motivo_descarte
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            self.active_alert.get("id_alerta"),
            self.active_alert.get("tipo"),
            self.active_alert.get("fecha_hora"),
            cam.get("id"),
            cam.get("orientacion"),
            cam.get("azimut"),
            ubic.get("direccion"),
            ubic.get("latitud"),
            ubic.get("longitud"),
            self.active_alert.get("imagen_asociada"),
            estado,
            self.current_user["username"],
            time.strftime("%Y-%m-%d %H:%M:%S"),
            motivo_descarte
        ))

        conn.commit()
        conn.close()

    def clear_active_alert_files(self):
        if not self.active_alert:
            return

        id_alerta = self.active_alert.get("id_alerta")
        json_path = os.path.join(self.alert_folder, f"{id_alerta}.json")
        img_path = os.path.join(self.alert_folder, f"{id_alerta}.jpg")

        if os.path.exists(json_path):
            os.remove(json_path)
        if os.path.exists(img_path):
            os.remove(img_path)

        self.active_alert = None

        if not self.alert_queue.empty():
            next_alert = self.alert_queue.get()
            self.display_alert(next_alert)
        else:
            self.lbl_alert_title.configure(text="✅ Sistema Normal — Sin Alertas Pendientes", text_color="#4CAF50")
            self.lbl_image.configure(image=None, text="Esperando transmisiones...")
            self.lbl_info_text.configure(text="No hay datos de alerta activos.")
            self.btn_confirm.configure(state="disabled")
            self.btn_reject.configure(state="disabled")

    def confirm_alert(self):
        self.save_alert_to_db(estado="CONFIRMADA")
        print(f"[ALERTA CONFIRMADA] {self.active_alert.get('id_alerta')} procesada por {self.current_user['username']}")
        self.clear_active_alert_files()

    def reject_alert_dialog(self):
        RejectModal(self, on_confirm_callback=self.process_rejection)

    def process_rejection(self, motivo):
        self.save_alert_to_db(estado="FALSO_POSITIVO", motivo_descarte=motivo)
        print(f"[FALSO POSITIVO] {self.active_alert.get('id_alerta')} descartada. Motivo: {motivo}")
        self.clear_active_alert_files()

    def logout(self):
        self.destroy()
        self.on_logout_callback()


def run_app():
    init_db()

    def start_main_dashboard(user_data):
        app = FireWatchCentralApp(current_user=user_data, on_logout_callback=run_app)
        app.mainloop()

    login = LoginWindow(on_login_success=start_main_dashboard)
    login.mainloop()


if __name__ == "__main__":
    run_app()