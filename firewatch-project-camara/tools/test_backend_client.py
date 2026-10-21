# Pruebas del envío al backend sin red (requests va simulado): python tools/test_backend_client.py
import json, os, sys, tempfile, types
AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))

rq = types.ModuleType("requests")
class RE(Exception): pass
class SSLError(RE): pass
rq.RequestException = RE
sys.modules["requests"] = rq
import backend_client as bc

fallos = []
def check(nombre, cond, extra=""):
    print("OK  " if cond else "FAIL", nombre, "" if cond else extra)
    if not cond: fallos.append(nombre)

def cliente(extra_env=None):
    d = tempfile.mkdtemp()
    cola = os.path.join(d, "alertas_pendientes"); os.makedirs(cola)
    with open(os.path.join(cola, "a.jpg"), "wb") as f: f.write(b"\xff\xd8\xff0")
    with open(os.path.join(cola, "a.json"), "w") as f:
        json.dump({"id_alerta": "a", "camara_id": "cam_1", "imagen_asociada": "a.jpg"}, f)
    env = {"FIREWATCH_API_URL": "https://srv:8100", "FIREWATCH_KEY_CAM_1": "k", **(extra_env or {})}
    c = bc.BackendClient.__new__(bc.BackendClient)
    # se arma sin hilo: solo se prueba el envío de una alerta
    c.queue_dir = cola; c.sent_dir = os.path.join(d, "enviadas"); c.rejected_dir = os.path.join(d, "rechazadas")
    os.makedirs(c.sent_dir); os.makedirs(c.rejected_dir)
    c.base_url = env["FIREWATCH_API_URL"]; c.keys = {"cam_1": "k"}; c.status = ""
    c.verify = (env.get("FIREWATCH_CA_BUNDLE") or "").strip() or True
    return c, os.path.join(cola, "a.json")

visto = {}
def post_ok(url, **kw):
    visto.update(kw); return types.SimpleNamespace(status_code=201, text="")
rq.post = post_ok
c, j = cliente()
check("envío normal", c._send_one(j) == "ok")
check("verifica el certificado por defecto", visto["verify"] is True)
c, j = cliente({"FIREWATCH_CA_BUNDLE": "C:/certs/cert.pem"})
c._send_one(j)
check("usa el cert.pem configurado", visto["verify"] == "C:/certs/cert.pem")

def post_ssl(url, **kw): raise SSLError("CERTIFICATE_VERIFY_FAILED")
rq.post = post_ssl
c, j = cliente()
check("certificado no confiable: la alerta queda en cola", c._send_one(j) == "red" and os.path.exists(j))
check("y el estado lo explica", "certificado" in c.status and "FIREWATCH_CA_BUNDLE" in c.status, c.status)

def post_red(url, **kw): raise RE("sin red")
rq.post = post_red
c, j = cliente()
check("sin red reintenta", c._send_one(j) == "red" and "sin conexión" in c.status, c.status)

print("\nFALLOS:", len(fallos)); sys.exit(1 if fallos else 0)
