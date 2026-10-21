"""Cliente de la API de FireWatch (ver firewatch-project-backend/CONTRATO_API.md)."""
import os

import requests

BASE_URL = os.environ.get("FIREWATCH_API", "http://127.0.0.1:8100").rstrip("/")
TIMEOUT = 6


class ApiError(Exception):
    """Error legible para mostrar al operador."""


def _verify():
    """Con HTTPS: verifica el certificado del servidor. Si usa un certificado propio (autofirmado), apunta
    FIREWATCH_CA_BUNDLE al archivo cert.pem del backend. Nunca se desactiva la verificación."""
    return os.environ.get("FIREWATCH_CA_BUNDLE", "").strip() or True


def _request(method, path, token=None, **kw):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        r = requests.request(method, BASE_URL + path, headers=headers, timeout=TIMEOUT, verify=_verify(), **kw)
    except requests.RequestException as e:
        if type(e).__name__ == "SSLError":
            raise ApiError("No se pudo verificar el certificado HTTPS del servidor. Instala su cert.pem "
                           "o define FIREWATCH_CA_BUNDLE con la ruta de ese archivo.")
        raise ApiError("No hay conexión con el servidor FireWatch.")
    if r.status_code >= 400:
        try:
            msg = r.json().get("error", r.text)
        except ValueError:
            msg = r.text
        raise ApiError(str(msg)[:200])
    return r


def login(username, password):
    """Devuelve {'username', 'nombre_completo', 'rol', 'token'}."""
    d = _request("POST", "/login", json={"username": username, "password": password}).json()
    return {"username": username, "nombre_completo": d["nombre_completo"], "rol": d["rol"], "token": d["token"]}


def alertas(token, estado=None, limit=100):
    params = {"limit": limit}
    if estado:
        params["estado"] = estado
    return _request("GET", "/alertas", token, params=params).json()["items"]


def alertas_resueltas(token, limit=200):
    """Historial: todo lo que ya no está pendiente."""
    return [a for a in alertas(token, limit=limit) if a["estado"] != "PENDIENTE"]


def imagen(token, id_alerta):
    return _request("GET", f"/alertas/{id_alerta}/imagen", token).content


def resolver(token, id_alerta, estado, motivo=None):
    body = {"estado": estado}
    if motivo:
        body["motivo_descarte"] = motivo
    return _request("PATCH", f"/alertas/{id_alerta}", token, json=body).json()
