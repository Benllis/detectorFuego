# Prueba de la logica anti-alertas-repetidas (sin camara ni modelos): python tools/test_logica_alertas.py
import sys, types, time, importlib.util
for m in ["cv2","onnxruntime","customtkinter","PIL","numpy","camera_manager","backend_client","camera_sources","camera_location","tkinter"]:
    sys.modules[m]=types.ModuleType(m)
sys.modules["customtkinter"].CTk=object; sys.modules["customtkinter"].CTkToplevel=object
sys.modules["tkinter"].messagebox=None; sys.modules["PIL"].Image=None
for n in ["CameraManager"]: setattr(sys.modules["camera_manager"],n,object)
setattr(sys.modules["backend_client"],"BackendClient",object)
class _UE(Exception): pass
sys.modules["camera_location"].UbicacionError=_UE
cs=sys.modules["camera_sources"]
for n in "BRANDS BRAND_ORDER brand_label brand_from_label build_rtsp_url split_rtsp source_kind parse_gps probe_source".split(): setattr(cs,n,None)
sys.modules["tkinter"].messagebox=object
spec=importlib.util.spec_from_file_location("mm",__import__("os").path.join(__import__("os").path.dirname(__import__("os").path.abspath(__file__)),"..","main_multicamera.py")); mm=importlib.util.module_from_spec(spec); spec.loader.exec_module(mm)
App=mm.FireWatchApp
class Cam: 
    def __init__(s): s.last_fire_dets=[];s.last_smoke_dets=[];s.last_alert_time=0.0;s.last_alert_state=None;s.last_detection_time=0.0;s.id="cam_1";s.history=[];s.conf_history=[]
class Fake:
    CONFIRM_MIN_FIRE=3;CONFIRM_MIN_SMOKE=2;CONFIRM_WINDOW=5;CONFIRM_MAX_AGE=30;INCIDENT_RESET=180
    COOLDOWN_SMOKE=600;COOLDOWN_FIRE=300;COOLDOWN_CRITICAL=600
    def __init__(s): s.sent=[]
    _max_conf=staticmethod(App._max_conf)
    def _dispatch_alert(s,cam,f,t): s.sent.append(t)
t=[1000.0]; mm.time=types.SimpleNamespace(time=lambda:t[0])
def run(name, steps, expect):
    a=Fake(); c=Cam()
    for dt,f,sm in steps:
        t[0]+=dt; c.last_fire_dets=[[0,0,0,0,0.7]] if f else []; c.last_smoke_dets=[[0,0,0,0,0.6]] if sm else []
        App._evaluate_alert(a,c,None)
    print("OK " if a.sent==expect else "FAIL", name, a.sent)
F=(1,1,0);S=(1,0,1);N=(1,0,0);FS=(1,1,1)
run("destello suelto no alerta",[F,N,N,N],[])
run("2 de 5 de fuego no alerta",[F,N,F,N,N],[])
run("2 de 5 de humo si alerta",[S,N,S,N,N],["SMOKE"])
run("1 destello de humo no alerta",[S,N,N,N,N],[])
run("3 fuego confirma",[F,F,F],["FIRE"])
run("fuego y humo no se suman entre si",[F,S,F],[])
run("un destello de humo tras FIRE no alerta",[F,F,F,S,N,N,N,N],["FIRE"])
run("humo sostenido tras FIRE sube a CRITICAL",[F,F,F,S,S],["FIRE","CRITICAL"])
run("sube a CRITICAL",[F,F,F,FS,FS,FS],["FIRE","CRITICAL"])
run("destello viejo no suma",[F,(3600,1,0),F],[])
run("no repite antes del cooldown",[F]*3+[F]*100,["FIRE"])
run("repite tras cooldown",[F]*3+[(10,1,0)]*35,["FIRE","FIRE"])
run("incidente nuevo tras 180s",[F]*3+[(200,0,0)]+[F]*3,["FIRE","FIRE"])
# --- lo que se informa en la alerta (fuego/humo/confianza) ---
def resumen(tipo, hist, fire_dets, smoke_dets):
    c=Cam(); c.conf_history=hist; c.last_fire_dets=fire_dets; c.last_smoke_dets=smoke_dets
    return App._alert_summary(Fake(), c, tipo)
def check(name, got, exp): print("OK " if got==exp else "FAIL", name, got)
check("CRITICAL informa fuego y humo aunque el ultimo cuadro solo tenga humo",
      resumen("CRITICAL",[(1,0.8,0.0),(2,0.0,0.6)],[],[[0,0,0,0,0.6]]), (True,True,0.8))
check("SMOKE no dice fuego aunque el ultimo cuadro lo tuviera",
      resumen("SMOKE",[(1,0.0,0.7),(2,0.5,0.6)],[[0,0,0,0,0.5]],[[0,0,0,0,0.6]]), (False,True,0.7))
check("FIRE ignora la confianza del humo",
      resumen("FIRE",[(1,0.4,0.9)],[[0,0,0,0,0.5]],[]), (True,False,0.5))
check("sin detecciones no inventa confianza", resumen("SMOKE",[],[],[]), (False,True,None))
# repite CRITICAL cada 10 min, no cada 3
run("CRITICAL no repite a los 3 min",[F,F,F,FS,FS,FS]+[(10,1,1)]*17,["FIRE","CRITICAL"])
run("CRITICAL repite tras 10 min",[F,F,F,FS,FS,FS]+[(10,1,1)]*62,["FIRE","CRITICAL","CRITICAL"])

# --- foto manual sin detección ---
class FakeM:
    MANUAL_COOLDOWN=30
    def __init__(s): s.sent=[]
    _max_conf=staticmethod(App._max_conf)
    def _dispatch_alert(s,cam,f,t): s.sent.append(t)
def manual(name, pasos, esperado):
    a=FakeM(); c=Cam(); res=[]
    for ahora, frame, estado in pasos:
        c.last_alert_state=estado
        res.append(App.send_snapshot(a,c,frame,now=ahora)[0])
    check(name, (res,a.sent), esperado)
manual("foto manual se envia como NO_DETECTION", [(1000,"img",None)], ([True],["NO_DETECTION"]))
manual("sin imagen no envia", [(1000,None,None)], ([False],[]))
manual("con alerta activa no duplica", [(1000,"img","FIRE")], ([False],[]))
manual("segundo clic antes de 30 s no envia", [(1000,"img",None),(1010,"img",None)], ([True,False],["NO_DETECTION"]))
manual("pasados 30 s vuelve a enviar", [(1000,"img",None),(1031,"img",None)], ([True,True],["NO_DETECTION","NO_DETECTION"]))
check("la foto manual no inventa fuego ni confianza", resumen("NO_DETECTION",[(1,0.8,0.6)],[[0,0,0,0,0.8]],[[0,0,0,0,0.6]]), (False,False,None))
a=FakeM(); c=Cam(); App.send_snapshot(a,c,"img",now=5000)
check("la foto manual no cambia el estado del incidente", (c.last_alert_state,), (None,))
