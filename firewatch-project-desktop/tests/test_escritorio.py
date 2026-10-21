"""Pruebas del escritorio sin ventana ni red (customtkinter, PIL, requests y login_view van simulados).
Ejecutar desde firewatch-project-desktop:  python tests/test_escritorio.py"""
import os, sys, types, queue
from unittest.mock import MagicMock

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))

# --- simulacros de lo que necesita la interfaz ---
ctk = types.ModuleType("customtkinter")
ctk.CTk = object; ctk.CTkToplevel = object
ctk.set_appearance_mode = ctk.set_default_color_theme = lambda *a, **k: None
ctk.CTkButton = MagicMock(); ctk.CTkLabel = MagicMock(); ctk.CTkImage = MagicMock()
sys.modules["customtkinter"] = ctk
pil = types.ModuleType("PIL"); pil.Image = MagicMock(); sys.modules["PIL"] = pil
tk = types.ModuleType("tkinter"); tk.messagebox = MagicMock(); sys.modules["tkinter"] = tk
sys.modules["tkinter.messagebox"] = tk.messagebox
lv = types.ModuleType("login_view"); lv.LoginWindow = object; sys.modules["login_view"] = lv
rq = types.ModuleType("requests")
class _RE(Exception): pass
rq.RequestException = _RE
sys.modules["requests"] = rq

import api_client
import main as M
App = M.FireWatchCentralApp

fallos = []
def check(nombre, cond, extra=""):
    print("OK  " if cond else "FAIL", nombre, "" if cond else extra)
    if not cond: fallos.append(nombre)

def nueva_app():
    a = object.__new__(App)
    a.current_user = {"username": "op", "token": "T"}; a.token = "T"
    a.incoming = queue.Queue(); a.polling = False; a.api_error = None
    a.pending = {}; a.seen_ids = set(); a.new_ids = set(); a.resolved_ids = set()
    a.first_load = True; a.last_poll = None; a.active_alert = None; a._list_sig = None
    a.filtro_tipo = "Todas"; a.bell = MagicMock()
    for w in ("scroll_pending", "lbl_list_count", "lbl_alert_title", "lbl_info_text",
              "btn_confirm", "btn_reject", "lbl_scan_status", "lbl_queue_count"):
        setattr(a, w, MagicMock())
    a.scroll_pending.winfo_children.return_value = []
    a._show_image = MagicMock()
    return a

def al(i, tipo="FIRE", ts=0, **kw):
    d = {"id_alerta": i, "tipo": tipo, "timestamp": ts, "camara_id": "cam_1", "estado": "PENDIENTE"}
    d.update(kw); return d

# ---------- api_client ----------
resp = lambda code, js=None, txt="": types.SimpleNamespace(
    status_code=code, text=txt, content=b"IMG", json=(lambda: js) if js is not None else (lambda: (_ for _ in ()).throw(ValueError())))

llamadas = []
def fake_req(m, url, headers=None, timeout=None, **kw):
    llamadas.append((m, url, headers, kw)); return fake_req.r
rq.request = fake_req

fake_req.r = resp(200, {"nombre_completo": "Op Uno", "rol": "operador", "token": "tok"})
u = api_client.login("op", "x")
check("login devuelve token y rol", u["token"] == "tok" and u["rol"] == "operador")
check("login no manda cabecera de autorización", llamadas[-1][2] == {})

fake_req.r = resp(200, {"items": [al("a")]})
api_client.alertas("T", estado="PENDIENTE", limit=50)
check("alertas manda token y filtros", llamadas[-1][2] == {"Authorization": "Bearer T"}
      and llamadas[-1][3]["params"] == {"limit": 50, "estado": "PENDIENTE"})

fake_req.r = resp(200, {"items": [al("a"), al("b", estado="CONFIRMADA"), al("c", estado="FALSO_POSITIVO")]})
check("historial excluye pendientes", [a["id_alerta"] for a in api_client.alertas_resueltas("T")] == ["b", "c"])

fake_req.r = resp(200)
check("imagen devuelve bytes", api_client.imagen("T", "a") == b"IMG")

# --- HTTPS: verificación siempre activa, certificado propio por variable de entorno ---
os.environ.pop("FIREWATCH_CA_BUNDLE", None)
fake_req.r = resp(200, {"items": []}); api_client.alertas("T")
check("por defecto verifica el certificado (verify=True)", llamadas[-1][3]["verify"] is True)
os.environ["FIREWATCH_CA_BUNDLE"] = "C:/certs/cert.pem"
api_client.alertas("T")
check("FIREWATCH_CA_BUNDLE se usa como verify", llamadas[-1][3]["verify"] == "C:/certs/cert.pem")
os.environ.pop("FIREWATCH_CA_BUNDLE")
class SSLError(_RE): pass
def req_ssl(*a, **k): raise SSLError("CERTIFICATE_VERIFY_FAILED")
rq.request = req_ssl
try:
    api_client.alertas("T"); msg = ""
except api_client.ApiError as e:
    msg = str(e)
check("error de certificado da un mensaje claro", "certificado" in msg and "FIREWATCH_CA_BUNDLE" in msg, msg)
def req_red(*a, **k): raise _RE("sin red")
rq.request = req_red
try:
    api_client.alertas("T"); msg = ""
except api_client.ApiError as e:
    msg = str(e)
check("sin red sigue diciendo que no hay conexión", "No hay conexión" in msg, msg)
rq.request = fake_req

fake_req.r = resp(200, {"ok": 1})
api_client.resolver("T", "a", "FALSO_POSITIVO", "niebla")
check("resolver manda motivo", llamadas[-1][3]["json"] == {"estado": "FALSO_POSITIVO", "motivo_descarte": "niebla"})
api_client.resolver("T", "a", "CONFIRMADA")
check("resolver sin motivo no lo manda", llamadas[-1][3]["json"] == {"estado": "CONFIRMADA"})

fake_req.r = resp(401, {"error": "credenciales"})
try: api_client.login("op", "mal"); check("error HTTP lanza ApiError", False)
except api_client.ApiError as e: check("error HTTP lanza ApiError con mensaje", str(e) == "credenciales")
fake_req.r = resp(500, txt="boom")
try: api_client.alertas("T"); check("texto no JSON", False)
except api_client.ApiError as e: check("error sin JSON usa el texto", str(e) == "boom")
def caida(*a, **k): raise _RE()
rq.request = caida
try: api_client.alertas("T"); check("sin red lanza ApiError", False)
except api_client.ApiError as e: check("sin red: mensaje legible", "conexión" in str(e))
rq.request = fake_req

# ---------- orden y filtro ----------
a = nueva_app()
a.pending = {x["id_alerta"]: x for x in [al("v", "SMOKE", 100), al("n", "FIRE", 50), al("r", "CRITICAL", 200)]}
a.new_ids = {"v"}
check("las NUEVAS van primero, luego las recientes", [x["id_alerta"] for x in a._sorted_pending()] == ["v", "r", "n"])
a.filtro_tipo = "FIRE"
check("filtro por tipo", [x["id_alerta"] for x in a._visible_pending()] == ["n"])
a.filtro_tipo = "Todas"
check("filtro Todas muestra todo", len(a._visible_pending()) == 3)
a.pending["f"] = al("f", "NO_DETECTION", 300)
a.filtro_tipo = "FOTO"
check("filtro FOTO muestra solo las fotos sin detección", [x["id_alerta"] for x in a._visible_pending()] == ["f"])
a.filtro_tipo = "FIRE"
check("las fotos sin detección no aparecen bajo FIRE", [x["id_alerta"] for x in a._visible_pending()] == ["n"])
a.filtro_tipo = "Todas"
check("Todas incluye también las fotos", len(a._visible_pending()) == 4)
del a.pending["f"]

# ---------- sincronización con el servidor ----------
a = nueva_app(); a.display_alert = MagicMock(side_effect=lambda x: setattr(a, "active_alert", x))
a._apply_pending([al("a", ts=1), al("b", ts=2)])
check("primera carga no marca nuevas ni suena", a.new_ids == set() and not a.bell.called)
check("primera carga abre una alerta", a.active_alert is not None)
a._apply_pending([al("a", ts=1), al("b", ts=2), al("c", ts=3)])
check("alerta que aparece después es NUEVA y suena", a.new_ids == {"c"} and a.bell.called)
a.resolved_ids.add("c")
a._apply_pending([al("a", ts=1), al("b", ts=2), al("c", ts=3)])
check("una resuelta aquí no revive por consulta atrasada", "c" not in a.pending and "c" not in a.new_ids)
a._apply_pending([al(None), {"tipo": "FIRE"}])
check("alertas sin id se ignoran", a.pending == {})
check("si la abierta ya no está pendiente se suelta", a.active_alert is None)

# ---------- confirmar / descartar ----------
def con_activa():
    a = nueva_app(); a.pending = {"a": al("a", "CRITICAL", 5), "b": al("b", "SMOKE", 4)}
    a.active_alert = a.pending["a"]; a.display_alert = MagicMock(); return a
guardadas = []
api_client.resolver = lambda t, i, e, m=None: guardadas.append((t, i, e, m)) or {}
a = con_activa(); a.confirm_alert()
check("confirmar manda CONFIRMADA al servidor", guardadas[-1] == ("T", "a", "CONFIRMADA", None))
check("tras confirmar sale de la lista y abre la siguiente",
      "a" not in a.pending and "a" in a.resolved_ids and a.display_alert.call_args[0][0]["id_alerta"] == "b")
a = con_activa(); a.process_rejection("niebla")
check("descartar manda FALSO_POSITIVO con motivo", guardadas[-1] == ("T", "a", "FALSO_POSITIVO", "niebla"))
a = con_activa(); a.pending = {"a": a.pending["a"]}; a.active_alert = a.pending["a"]; a.confirm_alert()
check("sin más pendientes vuelve a 'Sistema Normal'", a.lbl_alert_title.configure.called and a.btn_confirm.configure.call_args[1] == {"state": "disabled"})

def falla(*x, **k): raise api_client.ApiError("servidor caído")
api_client.resolver = falla
a = con_activa(); a.confirm_alert()
check("si el servidor falla la alerta sigue pendiente", "a" in a.pending and a.active_alert is not None and "a" not in a.resolved_ids)
check("si falla se avisa al operador", tk.messagebox.showerror.called)
a = nueva_app(); check("guardar sin alerta abierta no hace nada", a.save_alert_to_db("CONFIRMADA") is False)

# ---------- consulta periódica ----------
a = nueva_app(); a.after = MagicMock()
api_client.alertas = lambda t, estado=None, limit=100: [al("z")]
a._poll_api()
check("el hilo de consulta deja la lista en la cola", a.incoming.get_nowait()[0]["id_alerta"] == "z" and a.api_error is None and not a.polling)
def caida2(*x, **k): raise api_client.ApiError("No hay conexión con el servidor FireWatch.")
api_client.alertas = caida2
a._poll_api()
check("sin servidor se anota el error y no se rompe", "conexión" in a.api_error)
api_client.alertas = lambda *x, **k: 1/0
a._poll_api()
check("error inesperado también se muestra", a.api_error.startswith("error inesperado") and not a.polling)
a.check_pending_alerts = App.check_pending_alerts.__get__(a)
M.threading = types.SimpleNamespace(Thread=lambda target, daemon: types.SimpleNamespace(start=lambda: None))
a.check_pending_alerts()
check("el aviso de conexión sale en la barra", "🔌" in a.lbl_queue_count.configure.call_args[1]["text"])
check("se reprograma cada 3 s", a.after.call_args[0][0] == 3000)

# ---------- mostrar alerta ----------
a = nueva_app(); a.pending = {"a": al("a")}
api_client.imagen = lambda t, i: (_ for _ in ()).throw(api_client.ApiError("x"))
a.display_alert(al("a", "FIRE", confianza=0.8123, gps=[-33.0, -71.6], ubicacion="Calle 1"))
txt = a.lbl_info_text.configure.call_args[1]["text"]
check("muestra confianza y coordenadas", "81%" in txt and "-33.0" in txt and "Calle 1" in txt)
check("imagen caída no rompe la ficha", a._show_image.call_args[0][1] == "Imagen no disponible")
a.display_alert(al("a", "FIRE"))
txt = a.lbl_info_text.configure.call_args[1]["text"]
check("sin datos muestra n/d y sin coordenadas", "n/d" in txt and "sin coordenadas" in txt and "sin dirección" in txt)
a.display_alert(al("a", "NO_DETECTION"))
check("NO_DETECTION pide verificar la foto", "SIN DETECCIÓN" in a.lbl_alert_title.configure.call_args[1]["text"])

print("\nFALLOS:", len(fallos)); sys.exit(1 if fallos else 0)
