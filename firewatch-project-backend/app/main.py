"""FireWatch backend — implementa CONTRATO_API.md: salud, login, usuarios, cámaras, alertas,
reportes manuales (/reportes, con límite de envíos) y la propagación M2 (POST /propagacion/m2)."""
import json
import os
import re
import time
from pathlib import Path
from typing import Literal, Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, ValidationError

from . import db, m2, reportes, security

VERSION = "0.1"
ROLES = ("ADMIN", "OPERADOR", "BOMBERO")
TIPOS = ("FIRE", "SMOKE", "CRITICAL", "NO_DETECTION")
ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,100}$")
MAX_IMG = 5 * 1024 * 1024
MAX_FALLOS, BLOQUEO_S = 5, 60
CORS_POR_DEFECTO = "http://localhost:8080,http://127.0.0.1:8080"  # el mapa (firewatch-project-mapa)


# ── modelos ────────────────────────────────────────────────────────────
class AlertaMeta(BaseModel):
    id_alerta: str
    camara_id: str
    camara_nombre: Optional[str] = None
    ubicacion: Optional[str] = ""
    gps: Optional[list[float]] = Field(default=None, min_length=2, max_length=2)
    tipo: Literal["FIRE", "SMOKE", "CRITICAL", "NO_DETECTION"]
    timestamp: Optional[float] = None
    fecha_hora: str
    fuego_detectado: bool = False
    humo_detectado: bool = False
    confianza: Optional[float] = Field(default=None, ge=0, le=1)


class Resolucion(BaseModel):
    estado: Literal["CONFIRMADA", "FALSO_POSITIVO"]
    motivo_descarte: Optional[str] = None


class LoginIn(BaseModel):
    username: str
    password: str


class CambioClaveIn(BaseModel):
    actual: str
    nueva: str = Field(min_length=8, max_length=128)


class UsuarioIn(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    password: str = Field(min_length=8, max_length=128)
    nombre_completo: str
    rol: Literal["ADMIN", "OPERADOR", "BOMBERO"] = "OPERADOR"


class CamaraIn(BaseModel):
    camara_id: Optional[str] = None
    nombre: str
    ubicacion: str = ""
    gps: Optional[list[float]] = Field(default=None, min_length=2, max_length=2)
    azimut: Optional[int] = None
    orientacion: Optional[str] = None


# ── helpers ────────────────────────────────────────────────────────────
# Declarados para que /docs muestre el botón "Authorize"
bearer_scheme = HTTPBearer(auto_error=False, description="Token devuelto por POST /login")
camera_key_scheme = APIKeyHeader(name="X-Camera-Key", auto_error=False, description="Clave de la cámara")


def alerta_dict(r) -> dict:
    d = dict(r)
    lat, lon = d.pop("lat"), d.pop("lon")
    d["gps"] = [lat, lon] if lat is not None and lon is not None else None
    d["fuego_detectado"] = bool(d["fuego_detectado"])
    d["humo_detectado"] = bool(d["humo_detectado"])
    d["imagen_asociada"] = Path(d["imagen_path"]).name if d.get("imagen_path") else None
    d.pop("imagen_path", None)
    d.pop("recibida", None)
    return d


def camara_dict(r) -> dict:
    d = dict(r)
    lat, lon = d.pop("lat"), d.pop("lon")
    d["gps"] = [lat, lon] if lat is not None and lon is not None else None
    d.pop("clave_hash", None)
    return d


def load_env(path: str = ".env") -> None:
    """Lee un .env simple (CLAVE=valor) sin pisar variables ya definidas."""
    p = Path(path)
    if not p.is_file():
        return
    for linea in p.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if linea and not linea.startswith("#") and "=" in linea:
            k, v = linea.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def create_app(db_path: Optional[str] = None, uploads_dir: Optional[str] = None) -> FastAPI:
    load_env()
    db_path = db_path or os.environ.get("FIREWATCH_DB", "data/backend.db")
    uploads = Path(uploads_dir or os.environ.get("FIREWATCH_UPLOADS", "data/alertas"))
    uploads.mkdir(parents=True, exist_ok=True)
    db.init(db_path)
    fallos: dict[str, list[float]] = {}  # username -> instantes de intentos fallidos

    app = FastAPI(title="FireWatch API", version=VERSION)

    # El mapa (otro puerto) consulta esta API desde el navegador: hay que permitir su origen.
    # Solo orígenes concretos, nunca "*": la API usa tokens de sesión.
    origenes = [o.strip() for o in os.environ.get("FIREWATCH_CORS", CORS_POR_DEFECTO).split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware, allow_origins=origenes, allow_methods=["GET", "POST", "PATCH", "PUT"],
        allow_headers=["Authorization", "Content-Type", "X-Camera-Key"],
    )

    # errores con el formato del contrato: {"error": "..."}
    @app.exception_handler(HTTPException)
    async def _http(_: Request, exc: HTTPException):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _val(_: Request, exc: RequestValidationError):
        e = exc.errors()[0]
        loc = ".".join(str(x) for x in e["loc"] if x not in ("body", "query"))
        return JSONResponse({"error": f"dato inválido ({loc}): {e['msg']}"}, status_code=422)

    def get_con():
        con = db.connect(db_path)
        try:
            yield con
        finally:
            con.close()

    # ── bootstrap del administrador ───────────────────────────────────
    con = db.connect(db_path)
    if con.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0] == 0:
        pw = os.environ.get("ADMIN_PASSWORD") or security.new_secret()[:16]
        con.execute(
            "INSERT INTO usuarios (username, password_hash, nombre_completo, rol) VALUES (?,?,?,?)",
            ("admin", security.hash_password(pw), "Administrador", "ADMIN"),
        )
        con.commit()
        if not os.environ.get("ADMIN_PASSWORD"):
            print(f"[FireWatch] Usuario 'admin' creado. Contraseña inicial (se muestra una sola vez): {pw}")
    con.close()

    # límite de envíos del formulario público (/reportes). Configurable por .env; ver README.
    limite_reportes = reportes.LimiteEnvios(
        int(os.environ.get("FIREWATCH_REPORTES_MAX", "5")),
        float(os.environ.get("FIREWATCH_REPORTES_VENTANA_S", "3600")),
        int(os.environ.get("FIREWATCH_REPORTES_MAX_GLOBAL", "100")),
    )
    confiar_proxy = os.environ.get("FIREWATCH_PROXY_CONFIAR") == "1"

    def ip_cliente(request: Request) -> str:
        """IP de quien llama. X-Forwarded-For solo se acepta si el servidor está detrás de un proxy propio
        (FIREWATCH_PROXY_CONFIAR=1): si no, cualquiera podría falsearla y saltarse el límite."""
        if confiar_proxy:
            xff = request.headers.get("x-forwarded-for", "")
            if xff:
                return xff.split(",")[-1].strip()[:64]  # la última la agrega nuestro proxy; las anteriores las manda el cliente
        return request.client.host if request.client else "desconocida"

    # ── autenticación ─────────────────────────────────────────────────
    def usuario_actual(cred: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
                       con=Depends(get_con)):
        token = cred.credentials if cred else ""
        if not token:
            raise HTTPException(401, "falta el token de sesión")
        row = con.execute(
            "SELECT u.username, u.nombre_completo, u.rol, s.expira FROM sesiones s "
            "JOIN usuarios u ON u.id = s.usuario_id WHERE s.token_hash = ?",
            (security.digest(token),),
        ).fetchone()
        if not row or row["expira"] < security.now():
            raise HTTPException(401, "sesión inválida o vencida")
        return dict(row)

    def usuario_opcional(cred: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
                         con=Depends(get_con)):
        """None si no hay token; 401 si hay token pero no sirve (no se trata como anónimo)."""
        if not cred or not cred.credentials:
            return None
        return usuario_actual(cred, con)

    def requiere(*roles):
        def dep(u=Depends(usuario_actual)):
            if u["rol"] not in roles:
                raise HTTPException(403, "no tienes permiso para esta acción")
            return u
        return dep

    def camara_actual(key: Optional[str] = Depends(camera_key_scheme), con=Depends(get_con)):
        if not key:
            raise HTTPException(401, "falta X-Camera-Key")
        row = con.execute("SELECT * FROM camaras WHERE clave_hash = ?", (security.digest(key),)).fetchone()
        if not row:
            raise HTTPException(401, "clave de cámara inválida")
        return dict(row)

    # ── rutas ─────────────────────────────────────────────────────────
    @app.get("/", include_in_schema=False)
    def raiz():
        return RedirectResponse("/docs")

    @app.get("/salud")
    def salud():
        return {"ok": True, "version": VERSION}

    @app.post("/login")
    def login(data: LoginIn, request: Request, con=Depends(get_con)):
        ahora = security.now()
        intentos = [t for t in fallos.get(data.username, []) if ahora - t < BLOQUEO_S]
        fallos[data.username] = intentos
        if len(intentos) >= MAX_FALLOS:
            raise HTTPException(429, "demasiados intentos, espera un minuto")
        u = con.execute("SELECT * FROM usuarios WHERE username = ?", (data.username,)).fetchone()
        if not u or not security.check_password(data.password, u["password_hash"]):
            fallos[data.username].append(ahora)
            raise HTTPException(401, "usuario o contraseña incorrectos")
        fallos.pop(data.username, None)
        token = security.new_secret()
        con.execute("DELETE FROM sesiones WHERE expira < ?", (ahora,))
        con.execute(
            "INSERT INTO sesiones (token_hash, usuario_id, expira) VALUES (?,?,?)",
            (security.digest(token), u["id"], ahora + security.SESSION_TTL),
        )
        con.commit()
        return {"token": token, "nombre_completo": u["nombre_completo"], "rol": u["rol"]}

    @app.post("/usuarios", status_code=201)
    def crear_usuario(data: UsuarioIn, con=Depends(get_con), _=Depends(requiere("ADMIN"))):
        if con.execute("SELECT 1 FROM usuarios WHERE username = ?", (data.username,)).fetchone():
            raise HTTPException(409, "ese usuario ya existe")
        con.execute(
            "INSERT INTO usuarios (username, password_hash, nombre_completo, rol) VALUES (?,?,?,?)",
            (data.username, security.hash_password(data.password), data.nombre_completo, data.rol),
        )
        con.commit()
        return {"username": data.username, "rol": data.rol}

    @app.post("/usuarios/yo/clave")
    def cambiar_mi_clave(data: CambioClaveIn, con=Depends(get_con), u=Depends(usuario_actual)):
        """Cambia la contraseña del usuario con sesión iniciada. Cierra las demás sesiones."""
        fila = con.execute("SELECT id, password_hash FROM usuarios WHERE username = ?", (u["username"],)).fetchone()
        if not fila or not security.check_password(data.actual, fila["password_hash"]):
            raise HTTPException(401, "la contraseña actual no es correcta")
        if data.nueva == data.actual:
            raise HTTPException(422, "la contraseña nueva debe ser distinta de la actual")
        con.execute("UPDATE usuarios SET password_hash = ? WHERE id = ?", (security.hash_password(data.nueva), fila["id"]))
        con.execute("DELETE FROM sesiones WHERE usuario_id = ?", (fila["id"],))
        con.commit()
        return {"ok": True}

    # cámaras
    @app.post("/camaras", status_code=201)
    def crear_camara(data: CamaraIn, con=Depends(get_con), _=Depends(requiere("ADMIN"))):
        cid = data.camara_id or "cam_" + security.new_secret()[:8].replace("-", "x").replace("_", "x")
        if not ID_RE.match(cid):
            raise HTTPException(422, "camara_id solo admite letras, números, - y _")
        if con.execute("SELECT 1 FROM camaras WHERE camara_id = ?", (cid,)).fetchone():
            raise HTTPException(409, "esa cámara ya existe")
        clave = security.new_secret()
        lat, lon = (data.gps or [None, None])
        con.execute(
            "INSERT INTO camaras (camara_id, nombre, ubicacion, lat, lon, azimut, orientacion, clave_hash) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (cid, data.nombre, data.ubicacion, lat, lon, data.azimut, data.orientacion, security.digest(clave)),
        )
        con.commit()
        # la clave solo se muestra ahora; en la base queda únicamente su hash
        return {"camara_id": cid, "clave": clave}

    @app.get("/camaras")
    def listar_camaras(con=Depends(get_con), _=Depends(usuario_actual)):
        return [camara_dict(r) for r in con.execute("SELECT * FROM camaras ORDER BY camara_id")]

    @app.put("/camaras/{camara_id}")
    def editar_camara(camara_id: str, data: CamaraIn, con=Depends(get_con), _=Depends(requiere("ADMIN"))):
        if not con.execute("SELECT 1 FROM camaras WHERE camara_id = ?", (camara_id,)).fetchone():
            raise HTTPException(404, "cámara no encontrada")
        lat, lon = (data.gps or [None, None])
        con.execute(
            "UPDATE camaras SET nombre=?, ubicacion=?, lat=?, lon=?, azimut=?, orientacion=? WHERE camara_id=?",
            (data.nombre, data.ubicacion, lat, lon, data.azimut, data.orientacion, camara_id),
        )
        con.commit()
        return camara_dict(con.execute("SELECT * FROM camaras WHERE camara_id = ?", (camara_id,)).fetchone())

    # alertas
    @app.post("/alertas")
    async def crear_alerta(
        metadata: str = Form(...),
        imagen: UploadFile = File(...),
        cam=Depends(camara_actual),
        con=Depends(get_con),
    ):
        try:
            meta = AlertaMeta.model_validate(json.loads(metadata))
        except (json.JSONDecodeError, ValidationError) as e:
            raise HTTPException(422, f"metadata inválida: {str(e)[:200]}")
        if not ID_RE.match(meta.id_alerta):
            raise HTTPException(422, "id_alerta solo admite letras, números, - y _")
        if meta.camara_id != cam["camara_id"]:
            raise HTTPException(403, "la clave no corresponde a esa cámara")

        existente = con.execute("SELECT * FROM alertas WHERE id_alerta = ?", (meta.id_alerta,)).fetchone()
        if existente:  # reintento de la cámara: no duplicar
            return JSONResponse({"id_alerta": meta.id_alerta, "estado": existente["estado"]}, status_code=200)

        datos = await imagen.read(MAX_IMG + 1)
        if len(datos) > MAX_IMG:
            raise HTTPException(413, "la imagen supera 5 MB")
        if not datos.startswith(b"\xff\xd8\xff"):
            raise HTTPException(422, "la imagen debe ser un JPG")
        ruta = uploads / f"{meta.id_alerta}.jpg"
        ruta.write_bytes(datos)

        lat, lon = (meta.gps or [None, None])
        con.execute(
            "INSERT INTO alertas (id_alerta, camara_id, camara_nombre, ubicacion, lat, lon, tipo, timestamp, "
            "fecha_hora, fuego_detectado, humo_detectado, confianza, imagen_path, recibida) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (meta.id_alerta, meta.camara_id, meta.camara_nombre or cam["nombre"], meta.ubicacion, lat, lon,
             meta.tipo, meta.timestamp, meta.fecha_hora, int(meta.fuego_detectado), int(meta.humo_detectado),
             meta.confianza, str(ruta), security.now()),
        )
        con.commit()
        return JSONResponse({"id_alerta": meta.id_alerta, "estado": "PENDIENTE"}, status_code=201)

    @app.get("/alertas")
    def listar_alertas(
        estado: Optional[Literal["PENDIENTE", "CONFIRMADA", "FALSO_POSITIVO"]] = None,
        camara_id: Optional[str] = None,
        desde: Optional[str] = None,
        hasta: Optional[str] = None,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        con=Depends(get_con),
        _=Depends(usuario_actual),
    ):
        cond, args = [], []
        if estado:
            cond.append("estado = ?"); args.append(estado)
        if camara_id:
            cond.append("camara_id = ?"); args.append(camara_id)
        if desde:
            cond.append("fecha_hora >= ?"); args.append(desde)
        if hasta:
            cond.append("fecha_hora <= ?"); args.append(hasta)
        where = ("WHERE " + " AND ".join(cond)) if cond else ""
        total = con.execute(f"SELECT COUNT(*) FROM alertas {where}", args).fetchone()[0]
        rows = con.execute(
            f"SELECT * FROM alertas {where} ORDER BY recibida DESC LIMIT ? OFFSET ?", [*args, limit, offset]
        ).fetchall()
        return {"total": total, "items": [alerta_dict(r) for r in rows]}

    def _alerta(con, id_alerta):
        r = con.execute("SELECT * FROM alertas WHERE id_alerta = ?", (id_alerta,)).fetchone()
        if not r:
            raise HTTPException(404, "alerta no encontrada")
        return r

    @app.get("/alertas/{id_alerta}")
    def ver_alerta(id_alerta: str, con=Depends(get_con), _=Depends(usuario_actual)):
        return alerta_dict(_alerta(con, id_alerta))

    @app.get("/alertas/{id_alerta}/imagen")
    def imagen_alerta(id_alerta: str, con=Depends(get_con), _=Depends(usuario_actual)):
        r = _alerta(con, id_alerta)
        if not r["imagen_path"] or not Path(r["imagen_path"]).is_file():
            raise HTTPException(404, "imagen no disponible")
        return FileResponse(r["imagen_path"], media_type="image/jpeg")

    @app.patch("/alertas/{id_alerta}")
    def resolver_alerta(
        id_alerta: str, data: Resolucion, con=Depends(get_con), u=Depends(requiere("ADMIN", "OPERADOR"))
    ):
        _alerta(con, id_alerta)
        if data.estado == "FALSO_POSITIVO" and not (data.motivo_descarte or "").strip():
            raise HTTPException(422, "el motivo del descarte es obligatorio")
        con.execute(
            "UPDATE alertas SET estado=?, motivo_descarte=?, operador_resolucion=?, fecha_resolucion=? "
            "WHERE id_alerta=?",
            (data.estado,
             ((data.motivo_descarte or "").strip() or None) if data.estado == "FALSO_POSITIVO" else None,
             u["username"],
             time.strftime("%Y-%m-%d %H:%M:%S"), id_alerta),
        )
        con.commit()
        return alerta_dict(_alerta(con, id_alerta))

    # reportes manuales (formularios). POST puede ser público (ciudadanos); GET exige sesión.
    def reporte_dict(r, rol: str) -> dict:
        d = dict(r)
        d["gps"] = [d.pop("lat"), d.pop("lon")]
        d["imagen_asociada"] = Path(d["imagen_path"]).name if d.get("imagen_path") else None
        for k in ("imagen_path", "recibida", "ip", "usuario"):
            d.pop(k, None)
        if rol == "BOMBERO":  # el contacto de un ciudadano es dato personal: solo operadores y admin
            d["contacto"] = None
        return d

    @app.post("/reportes", status_code=201)
    async def crear_reporte(request: Request, con=Depends(get_con), u=Depends(usuario_opcional)):
        # 1) límite de envíos para quien no inició sesión
        if u is None:
            espera = limite_reportes.intentar(ip_cliente(request), security.now())
            if espera is not None:
                raise HTTPException(429, f"demasiados reportes; intenta de nuevo en {espera} s",
                                    headers={"Retry-After": str(espera)})
        # 2) tamaño antes de leer el cuerpo
        try:
            largo = int(request.headers.get("content-length") or 0)
        except ValueError:
            raise HTTPException(400, "Content-Length inválido")
        if largo > reportes.MAX_IMG_REPORTE + reportes.MAX_CUERPO_EXTRA:
            raise HTTPException(413, "el envío supera el tamaño permitido")
        # 3) JSON, o multipart con `metadata` (JSON) e `imagen` opcional, igual que /alertas
        tipo = request.headers.get("content-type", "").split(";")[0].strip().lower()
        datos = None
        try:
            if tipo == "multipart/form-data":
                form = await request.form()
                crudo = json.loads(str(form.get("metadata", "")))
                arch = form.get("imagen")
                if arch is not None and hasattr(arch, "read"):
                    datos = await arch.read(reportes.MAX_IMG_REPORTE + 1)
            elif tipo == "application/json":
                crudo = await request.json()
            else:
                raise HTTPException(415, "usa application/json o multipart/form-data")
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPException(422, "el cuerpo no es un JSON válido")
        try:
            rep = reportes.ReporteIn.model_validate(crudo)
        except ValidationError as e:
            err = e.errors()[0]
            raise HTTPException(422, f"dato inválido ({'.'.join(str(x) for x in err['loc'])}): "
                                     f"{str(err['msg']).removeprefix('Value error, ')}")
        # 4) un reporte de bombero exige sesión: es lo que lo distingue de uno ciudadano
        if rep.origen == "BOMBERO" and u is None:
            raise HTTPException(401, "los reportes de bomberos requieren iniciar sesión")
        # 5) imagen opcional
        ruta = None
        id_rep = "rep_" + security.new_secret()[:10].replace("-", "x").replace("_", "x")
        if datos:
            if len(datos) > reportes.MAX_IMG_REPORTE:
                raise HTTPException(413, "la imagen supera 3 MB")
            if not datos.startswith(b"\xff\xd8\xff"):
                raise HTTPException(422, "la imagen debe ser un JPG")
            carpeta = uploads / "reportes"
            carpeta.mkdir(parents=True, exist_ok=True)
            ruta = carpeta / f"{id_rep}.jpg"
            ruta.write_bytes(datos)
        estado = "SIN_VERIFICAR" if rep.origen == "USUARIO" else "PENDIENTE"
        con.execute(
            "INSERT INTO reportes (id_reporte, origen, descripcion, lat, lon, contacto, estado, fecha_hora, "
            "imagen_path, usuario, ip, recibida) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (id_rep, rep.origen, rep.descripcion, rep.gps[0], rep.gps[1], rep.contacto, estado,
             time.strftime("%Y-%m-%d %H:%M:%S"), str(ruta) if ruta else None,
             u["username"] if u else None, ip_cliente(request), security.now()),
        )
        con.commit()
        return {"id_reporte": id_rep, "estado": estado}

    @app.get("/reportes")
    def listar_reportes(
        origen: Optional[Literal["USUARIO", "BOMBERO"]] = None,
        estado: Optional[str] = Query(None, max_length=30),
        desde: Optional[str] = Query(None, max_length=30),
        hasta: Optional[str] = Query(None, max_length=30),
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        con=Depends(get_con),
        u=Depends(usuario_actual),
    ):
        cond, args = [], []
        for col, op, val in (("origen", "=", origen), ("estado", "=", estado),
                             ("fecha_hora", ">=", desde), ("fecha_hora", "<=", hasta)):
            if val:
                cond.append(f"{col} {op} ?"); args.append(val)
        where = ("WHERE " + " AND ".join(cond)) if cond else ""
        total = con.execute(f"SELECT COUNT(*) FROM reportes {where}", args).fetchone()[0]
        rows = con.execute(
            f"SELECT * FROM reportes {where} ORDER BY recibida DESC LIMIT ? OFFSET ?", [*args, limit, offset]
        ).fetchall()
        return {"total": total, "items": [reporte_dict(r, u["rol"]) for r in rows]}

    def _reporte(con, id_reporte):
        r = con.execute("SELECT * FROM reportes WHERE id_reporte = ?", (id_reporte,)).fetchone()
        if not r:
            raise HTTPException(404, "reporte no encontrado")
        return r

    @app.get("/reportes/{id_reporte}")
    def ver_reporte(id_reporte: str, con=Depends(get_con), u=Depends(usuario_actual)):
        return reporte_dict(_reporte(con, id_reporte), u["rol"])

    @app.get("/reportes/{id_reporte}/imagen")
    def imagen_reporte(id_reporte: str, con=Depends(get_con), _=Depends(usuario_actual)):
        r = _reporte(con, id_reporte)
        if not r["imagen_path"] or not Path(r["imagen_path"]).is_file():
            raise HTTPException(404, "imagen no disponible")
        return FileResponse(r["imagen_path"], media_type="image/jpeg")

    # propagación: modelo M2 (continuación de un foco ya detectado). Lo usa el mapa.
    @app.post("/propagacion/m2")
    def propagacion_m2(data: m2.M2In, _=Depends(usuario_actual)):
        try:
            return m2.predecir(data)
        except m2.M2NoDisponible as e:
            raise HTTPException(503, str(e))
        except ValueError as e:
            raise HTTPException(422, str(e))

    return app
