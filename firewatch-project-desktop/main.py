import os
import json
import time
import sqlite3
import queue
import customtkinter as ctk
from PIL import Image

import io
import threading
from tkinter import messagebox

import api_client
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
        self.geometry("1320x760")

        self.token = current_user["token"]
        self.incoming = queue.Queue()      # listas de pendientes traídas por el hilo de consulta
        self.polling = False
        self.api_error = None
        self.pending = {}                  # id_alerta -> alerta (lo que el servidor dice que está pendiente)
        self.seen_ids = set()              # ids ya vistos, para detectar las nuevas
        self.new_ids = set()               # nuevas aún no abiertas por el operador
        self.resolved_ids = set()          # resueltas aquí (evita que una consulta atrasada las reviva)
        self.first_load = True
        self.last_poll = None
        self.active_alert = None
        self._list_sig = None

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
        self.view_frame.grid_columnconfigure(0, weight=0)
        self.view_frame.grid_columnconfigure(1, weight=3)
        self.view_frame.grid_columnconfigure(2, weight=2)
        self.view_frame.grid_rowconfigure(0, weight=1)

        # Lista de alertas pendientes (más graves primero, y dentro de cada tipo las más recientes)
        self.list_frame = ctk.CTkFrame(self.view_frame, fg_color="#262626", width=250)
        self.list_frame.grid(row=0, column=0, padx=(15, 0), pady=15, sticky="ns")
        self.list_frame.grid_propagate(False)
        ctk.CTkLabel(self.list_frame, text="Alertas pendientes", font=("Arial", 14, "bold")).pack(pady=(10, 0))
        self.lbl_list_count = ctk.CTkLabel(self.list_frame, text="0 en total", font=("Arial", 10), text_color="gray")
        self.lbl_list_count.pack(pady=(0, 4))
        self.filtro_tipo = "Todas"
        self.seg_filtro = ctk.CTkSegmentedButton(self.list_frame, values=["Todas", "CRITICAL", "FIRE", "SMOKE", "FOTO"],
                                                 command=self._on_filter, font=("Arial", 9))
        self.seg_filtro.set("Todas")
        self.seg_filtro.pack(fill="x", padx=6, pady=(0, 6))
        self.scroll_pending = ctk.CTkScrollableFrame(self.list_frame, fg_color="transparent", width=214)
        self.scroll_pending.pack(fill="both", expand=True, padx=4, pady=(0, 6))

        self.lbl_image = ctk.CTkLabel(self.view_frame, text="Esperando transmisiones...", font=("Arial", 14))
        self.lbl_image.grid(row=0, column=1, padx=15, pady=15, sticky="nsew")

        self.info_panel = ctk.CTkFrame(self.view_frame, fg_color="#262626")
        self.info_panel.grid(row=0, column=2, padx=(0, 15), pady=15, sticky="nsew")

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

        try:
            records = api_client.alertas_resueltas(self.token)
        except api_client.ApiError as e:
            lbl_err = ctk.CTkLabel(self.scroll_history, text=f"No se pudo cargar el historial: {e}", font=("Arial", 14))
            lbl_err.pack(pady=30)
            return

        if not records:
            lbl_empty = ctk.CTkLabel(self.scroll_history, text="No hay alertas registradas en el historial.", font=("Arial", 14))
            lbl_empty.pack(pady=30)
            return

        for a in records:
            id_alerta, tipo, fecha = a["id_alerta"], a["tipo"], a["fecha_hora"]
            cam_id, direccion = a["camara_id"], a.get("ubicacion") or "sin dirección"
            estado, op_res, motivo = a["estado"], a.get("operador_resolucion"), a.get("motivo_descarte")

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

    def _show_image(self, ctk_img=None, text=""):
        """Muestra una imagen o un texto en el visor.

        Nunca se usa image=None: en customtkinter deja al widget apuntando a una
        imagen de Tk ya liberada y la siguiente alerta falla con
        'image "pyimageN" doesn't exist'. En su lugar se pone una imagen
        transparente y se guarda una referencia a la imagen activa."""
        if ctk_img is None:
            if getattr(self, "_blank_img", None) is None:
                self._blank_img = ctk.CTkImage(light_image=Image.new("RGBA", (1, 1), (0, 0, 0, 0)),
                                               dark_image=Image.new("RGBA", (1, 1), (0, 0, 0, 0)),
                                               size=(1, 1))
            ctk_img = self._blank_img
        self._current_img = ctk_img  # evita que se libere mientras se muestra
        self.lbl_image.configure(image=ctk_img, text=text)

    def _poll_api(self):
        """Hilo: pide las alertas pendientes al servidor sin congelar la ventana."""
        try:
            self.incoming.put(api_client.alertas(self.token, estado="PENDIENTE", limit=200))
            self.api_error = None
            self.last_poll = time.strftime("%H:%M:%S")
        except api_client.ApiError as e:
            self.api_error = str(e)
        except Exception as e:   # cualquier otro fallo también debe verse, no morir en silencio
            self.api_error = f"error inesperado: {e}"
        finally:
            self.polling = False

    SEVERIDAD = {"CRITICAL": 0, "FIRE": 1, "SMOKE": 2}
    COLOR_TIPO = {"CRITICAL": "#8E0000", "FIRE": "#B34700", "SMOKE": "#6B5B00"}
    # Nombre corto del selector -> tipo real de la alerta (las fotos manuales de la cámara llegan como NO_DETECTION)
    FILTRO_A_TIPO = {"FOTO": "NO_DETECTION"}
    MAX_LISTA = 60

    def _sorted_pending(self):
        """Primero las NUEVAS (que el operador aún no abrió); luego las más recientes.
        La gravedad se distingue por el color y se puede filtrar por tipo."""
        return sorted(self.pending.values(),
                      key=lambda a: (0 if a.get("id_alerta") in self.new_ids else 1,
                                     -(a.get("timestamp") or 0)))

    def _visible_pending(self):
        """Lista ordenada y filtrada por el tipo elegido en el selector."""
        f = getattr(self, "filtro_tipo", "Todas")
        orden = self._sorted_pending()
        if f == "Todas":
            return orden
        tipo = self.FILTRO_A_TIPO.get(f, f)
        return [a for a in orden if a.get("tipo") == tipo]

    def _on_filter(self, valor):
        self.filtro_tipo = valor
        self._list_sig = None
        self._refresh_list()

    def _apply_pending(self, items):
        """Sincroniza la lista local con lo que dice el servidor."""
        items = [a for a in items if a.get("id_alerta") and a["id_alerta"] not in self.resolved_ids]
        fresh = [a["id_alerta"] for a in items if a["id_alerta"] not in self.seen_ids]
        self.pending = {a["id_alerta"]: a for a in items}
        self.seen_ids.update(self.pending)
        if fresh and not self.first_load:
            self.new_ids.update(fresh)
            try:
                self.bell()
            except Exception:
                pass
        self.first_load = False
        self.new_ids &= set(self.pending)

        # si la alerta abierta ya no está pendiente (la resolvió otro puesto), se suelta
        if self.active_alert and self.active_alert["id_alerta"] not in self.pending:
            self.active_alert = None
            self._reset_view()
        # sin alerta abierta: se abre la más grave
        if not self.active_alert and self.pending:
            self.display_alert(self._sorted_pending()[0])
        self._refresh_list()

    def _refresh_list(self):
        """Redibuja la lista solo si cambió algo (evita parpadeo y trabajo inútil cada 3 s)."""
        orden = self._visible_pending()
        active_id = self.active_alert["id_alerta"] if self.active_alert else None
        sig = (tuple(a["id_alerta"] for a in orden[:self.MAX_LISTA]), active_id, tuple(sorted(self.new_ids)),
               len(orden), len(self.pending), self.filtro_tipo)
        if sig == self._list_sig:
            return
        self._list_sig = sig

        for w in self.scroll_pending.winfo_children():
            w.destroy()
        total = len(self.pending)
        self.lbl_list_count.configure(
            text=f"{total} en total" if len(orden) == total else f"{len(orden)} de {total} ({self.filtro_tipo})")
        for a in orden[:self.MAX_LISTA]:
            tipo = a.get("tipo", "?")
            cam = (a.get("camara_nombre") or a.get("camara_id") or "?")[:18]
            hora = (a.get("fecha_hora") or "")[5:16]
            marca = "● NUEVA  " if a["id_alerta"] in self.new_ids else ""
            activa = a["id_alerta"] == active_id
            ctk.CTkButton(
                self.scroll_pending, text=f"{marca}{tipo} · {cam}\n{hora}", anchor="w", height=44,
                fg_color=self.COLOR_TIPO.get(tipo, "#444444"), hover_color="#555555",
                border_width=3 if activa else 0, border_color="#FFFFFF",
                command=lambda al=a: self.select_alert(al)
            ).pack(fill="x", pady=2, padx=2)
        if len(orden) > self.MAX_LISTA:
            ctk.CTkLabel(self.scroll_pending, text=f"+{len(orden) - self.MAX_LISTA} más (resuelve las de arriba)",
                         font=("Arial", 10), text_color="gray").pack(pady=6)

    def select_alert(self, alerta):
        """El operador elige una alerta de la lista."""
        if alerta["id_alerta"] in self.pending:
            self.display_alert(alerta)
            self._refresh_list()

    def check_pending_alerts(self):
        if not self.polling:
            self.polling = True
            threading.Thread(target=self._poll_api, daemon=True).start()

        latest = None
        while not self.incoming.empty():
            latest = self.incoming.get()
        if latest is not None:
            self._apply_pending(latest)

        n = len(self.pending)
        self.lbl_scan_status.configure(
            text=f"🟢 Consultado {self.last_poll}\n{n} pendiente(s) · {len(self.new_ids)} nueva(s)" if self.last_poll
            else "⏳ Consultando servidor...", text_color="#4CAF50")
        if self.api_error:
            self.lbl_queue_count.configure(text=f"🔌 {self.api_error}", text_color="#FFB020")
        elif n > 0:
            self.lbl_queue_count.configure(text=f"⚠️ {n} alerta(s) pendiente(s)", text_color="#FF4D4D")
        else:
            self.lbl_queue_count.configure(text="En cola: 0 alertas", text_color="gray")

        self.after(3000, self.check_pending_alerts)

    def _reset_view(self):
        self.lbl_alert_title.configure(text="✅ Sistema Normal — Sin Alertas Pendientes", text_color="#4CAF50")
        self._show_image(None, "Esperando transmisiones...")
        self.lbl_info_text.configure(text="No hay datos de alerta activos.")
        self.btn_confirm.configure(state="disabled")
        self.btn_reject.configure(state="disabled")

    def display_alert(self, alert_data):
        self.active_alert = alert_data
        self.new_ids.discard(alert_data.get("id_alerta"))
        tipo = alert_data.get("tipo", "DESCONOCIDO")
        remaining = max(len(self.pending) - 1, 0)

        queue_text = f" ({remaining} más pendientes)" if remaining > 0 else ""
        self.lbl_alert_title.configure(text=f"🚨 ALERTA DETECTADA: {tipo}{queue_text}", text_color="#FF3333")

        if tipo == "NO_DETECTION":
            self.lbl_alert_title.configure(
                text=f"⚠️ SIN DETECCIÓN — verificar la foto{queue_text}", text_color="#FFB020")

        try:
            pil_img = Image.open(io.BytesIO(api_client.imagen(self.token, alert_data["id_alerta"])))
            ctk_img = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=(440, 340))
            self._show_image(ctk_img)
        except Exception:
            self._show_image(None, "Imagen no disponible")

        gps = alert_data.get("gps")
        coords = f"Lat: {gps[0]} | Lon: {gps[1]}" if gps else "sin coordenadas"
        conf = alert_data.get("confianza")

        info_str = (
            f"• ID Alerta: {alert_data.get('id_alerta')}\n\n"
            f"• Fecha/Hora: {alert_data.get('fecha_hora')}\n\n"
            f"• Cámara: {alert_data.get('camara_nombre') or alert_data.get('camara_id')}"
            f" ({alert_data.get('camara_id')})\n\n"
            f"• Ubicación:\n  {alert_data.get('ubicacion') or 'sin dirección'}\n\n"
            f"• Coordenadas GPS:\n  {coords}\n\n"
            f"• Confianza del modelo: {f'{conf:.0%}' if conf is not None else 'n/d'}"
        )
        self.lbl_info_text.configure(text=info_str)

        self.btn_confirm.configure(state="normal")
        self.btn_reject.configure(state="normal")

    def save_alert_to_db(self, estado, motivo_descarte=""):
        """Envía la resolución al servidor. Devuelve True si se guardó."""
        if not self.active_alert:
            return False
        try:
            api_client.resolver(self.token, self.active_alert["id_alerta"], estado, motivo_descarte or None)
            return True
        except api_client.ApiError as e:
            messagebox.showerror("FireWatch", f"No se pudo guardar la resolución:\n{e}\n\nLa alerta sigue pendiente.")
            return False

    def clear_active_alert_files(self):
        """Ya resuelta la alerta abierta: se quita de la lista y se abre la siguiente más grave."""
        if not self.active_alert:
            return
        aid = self.active_alert["id_alerta"]
        self.resolved_ids.add(aid)
        self.pending.pop(aid, None)
        self.new_ids.discard(aid)
        self.active_alert = None

        siguiente = self._sorted_pending()
        if siguiente:
            self.display_alert(siguiente[0])
        else:
            self._reset_view()
        self._refresh_list()

    def confirm_alert(self):
        if self.save_alert_to_db(estado="CONFIRMADA"):
            print(f"[ALERTA CONFIRMADA] {self.active_alert.get('id_alerta')} procesada por {self.current_user['username']}")
            self.clear_active_alert_files()

    def reject_alert_dialog(self):
        RejectModal(self, on_confirm_callback=self.process_rejection)

    def process_rejection(self, motivo):
        if self.save_alert_to_db(estado="FALSO_POSITIVO", motivo_descarte=motivo):
            print(f"[FALSO POSITIVO] {self.active_alert.get('id_alerta')} descartada. Motivo: {motivo}")
            self.clear_active_alert_files()

    def logout(self):
        self.destroy()
        self.on_logout_callback()


def run_app():
    def start_main_dashboard(user_data):
        app = FireWatchCentralApp(current_user=user_data, on_logout_callback=run_app)
        app.mainloop()

    login = LoginWindow(on_login_success=start_main_dashboard)
    login.mainloop()


if __name__ == "__main__":
    run_app()