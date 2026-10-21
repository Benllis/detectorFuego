"""Salvaguarda de red del arranque y generación del certificado HTTPS."""
import importlib.util
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app import servidor

RAIZ = Path(__file__).resolve().parent.parent


def cargar_generador():
    spec = importlib.util.spec_from_file_location("generar_certificado", RAIZ / "tools" / "generar_certificado.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_por_defecto_solo_este_equipo():
    assert servidor.configurar({}) == {"host": "127.0.0.1", "port": 8100}


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.50", "::", "mi-servidor.local"])
def test_se_niega_a_exponer_en_la_red_sin_cifrado(host):
    with pytest.raises(servidor.ConfigError, match="sin cifrado"):
        servidor.configurar({"FIREWATCH_HOST": host})


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_loopback_sin_https_esta_bien(host):
    assert servidor.configurar({"FIREWATCH_HOST": host})["host"] == host


def test_permitir_http_explicito():
    kw = servidor.configurar({"FIREWATCH_HOST": "0.0.0.0", "FIREWATCH_PERMITIR_HTTP": "1"})
    assert kw["host"] == "0.0.0.0" and "ssl_certfile" not in kw


def test_cert_y_llave_van_juntas_y_deben_existir(tmp_path):
    with pytest.raises(servidor.ConfigError, match="van juntas"):
        servidor.configurar({"FIREWATCH_SSL_CERT": "a.pem"})
    with pytest.raises(servidor.ConfigError, match="no existe"):
        servidor.configurar({"FIREWATCH_SSL_CERT": str(tmp_path / "a.pem"), "FIREWATCH_SSL_KEY": str(tmp_path / "b.pem")})
    with pytest.raises(servidor.ConfigError, match="FIREWATCH_PORT"):
        servidor.configurar({"FIREWATCH_PORT": "abc"})


def test_certificado_generado_sirve_para_https_real(tmp_path):
    """Genera el certificado, levanta el backend con HTTPS de verdad y lo consulta verificando el certificado."""
    gen = cargar_generador()
    cert, key = gen.generar(["192.168.1.50"], ["fire.local"], 30, tmp_path / "certs")
    # no pisa certificados existentes sin --forzar
    with pytest.raises(FileExistsError):
        gen.generar([], [], 30, tmp_path / "certs")
    with pytest.raises(ValueError):
        gen.generar(["no-es-ip"], [], 30, tmp_path / "otro")

    from cryptography import x509
    san = x509.load_pem_x509_certificate(cert.read_bytes()).extensions.get_extension_for_class(
        x509.SubjectAlternativeName).value
    assert "192.168.1.50" in [str(i) for i in san.get_values_for_type(x509.IPAddress)]
    assert "fire.local" in san.get_values_for_type(x509.DNSName)

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        puerto = s.getsockname()[1]
    env = {"FIREWATCH_PORT": str(puerto), "FIREWATCH_SSL_CERT": str(cert), "FIREWATCH_SSL_KEY": str(key),
           "FIREWATCH_DB": str(tmp_path / "x.db"), "FIREWATCH_UPLOADS": str(tmp_path / "f"), "ADMIN_PASSWORD": "clave-admin-123"}
    import os
    proc = subprocess.Popen([sys.executable, "-m", "app.servidor"], cwd=RAIZ, env={**os.environ, **env},
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        import requests
        for _ in range(60):
            try:
                r = requests.get(f"https://localhost:{puerto}/salud", verify=str(cert), timeout=2)
                break
            except requests.RequestException:
                time.sleep(0.5)
        else:
            proc.terminate()
            pytest.fail("el servidor HTTPS no respondió:\n" + proc.communicate(timeout=10)[0].decode(errors="replace"))
        assert r.status_code == 200 and r.json()["ok"] is True
        # sin confiar en el certificado, un cliente que verifica debe rechazarlo
        with pytest.raises(requests.exceptions.SSLError):
            requests.get(f"https://localhost:{puerto}/salud", timeout=3)
        # HTTP plano en ese puerto no debe responder la API
        with pytest.raises(requests.RequestException):
            requests.get(f"http://localhost:{puerto}/salud", timeout=3)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
