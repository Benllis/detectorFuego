"""Arranque del backend con HTTPS opcional y una salvaguarda: `python -m app.servidor`.

Reglas (variables en .env o en el entorno):
- FIREWATCH_HOST  (por defecto 127.0.0.1): en qué interfaz escucha. 127.0.0.1 = solo este equipo.
- FIREWATCH_PORT  (por defecto 8100).
- FIREWATCH_SSL_CERT y FIREWATCH_SSL_KEY: rutas del certificado y la llave. Con ambos, sirve HTTPS.
- FIREWATCH_PERMITIR_HTTP=1: permite escuchar en la red SIN cifrado (solo para pruebas en una red que controlas).

La salvaguarda: si el servidor va a quedar visible en la red (host distinto de loopback) y no hay certificado,
se niega a arrancar. Así no se expone por descuido un servicio con contraseñas y tokens en texto plano.
"""
import ipaddress
import os
import sys
from pathlib import Path

from .main import load_env

LOOPBACK = {"localhost"}


class ConfigError(Exception):
    pass


def es_loopback(host: str) -> bool:
    if host.lower() in LOOPBACK:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def configurar(env=None) -> dict:
    """Devuelve los argumentos para uvicorn.run o lanza ConfigError con un mensaje que dice qué corregir."""
    env = os.environ if env is None else env
    host = env.get("FIREWATCH_HOST", "127.0.0.1").strip() or "127.0.0.1"
    try:
        port = int(env.get("FIREWATCH_PORT", "8100"))
    except ValueError:
        raise ConfigError("FIREWATCH_PORT debe ser un número")
    cert, key = env.get("FIREWATCH_SSL_CERT", "").strip(), env.get("FIREWATCH_SSL_KEY", "").strip()
    if bool(cert) != bool(key):
        raise ConfigError("FIREWATCH_SSL_CERT y FIREWATCH_SSL_KEY van juntas: falta una de las dos")
    kw = {"host": host, "port": port}
    if cert:
        for etiqueta, ruta in (("certificado", cert), ("llave", key)):
            if not Path(ruta).is_file():
                raise ConfigError(f"no existe el archivo del {etiqueta}: {ruta} (genéralo con tools/generar_certificado.py)")
        kw.update(ssl_certfile=cert, ssl_keyfile=key)
    elif not es_loopback(host) and env.get("FIREWATCH_PERMITIR_HTTP") != "1":
        raise ConfigError(
            f"FIREWATCH_HOST={host} dejaría el servidor visible en la red sin cifrado. "
            "Configura FIREWATCH_SSL_CERT/FIREWATCH_SSL_KEY (ver README, sección HTTPS), "
            "o usa 127.0.0.1, o pon FIREWATCH_PERMITIR_HTTP=1 si es una red que controlas."
        )
    return kw


def main() -> int:
    load_env()
    try:
        kw = configurar()
    except ConfigError as e:
        print(f"[FireWatch] {e}", file=sys.stderr)
        return 2
    import uvicorn

    esquema = "https" if "ssl_certfile" in kw else "http"
    print(f"[FireWatch] Escuchando en {esquema}://{kw['host']}:{kw['port']}")
    uvicorn.run("app.main:create_app", factory=True, **kw)
    return 0


if __name__ == "__main__":
    sys.exit(main())
