"""Genera un certificado HTTPS autofirmado para el backend en una red local.

    python tools/generar_certificado.py --ip 192.168.1.50 [--ip 192.168.1.51] [--dns fire.local] [--dias 365]

Crea certs/cert.pem (público: se instala en los equipos que se conectan) y certs/key.pem (PRIVADO: no se sube a git,
no se comparte). El certificado vale para localhost, 127.0.0.1 y las IP/nombres que indiques: el cliente solo lo
acepta si la dirección a la que se conecta figura aquí, así que pasa la IP real del servidor.

Requiere la librería `cryptography` (pip install cryptography).
"""
import argparse
import datetime
import ipaddress
import os
import sys
from pathlib import Path


def generar(ips, dns, dias, carpeta: Path, forzar=False):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    cert_p, key_p = carpeta / "cert.pem", carpeta / "key.pem"
    if (cert_p.exists() or key_p.exists()) and not forzar:
        raise FileExistsError(f"ya existen {cert_p} o {key_p}; usa --forzar para reemplazarlos")
    carpeta.mkdir(parents=True, exist_ok=True)

    nombres = [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
    nombres += [x509.DNSName(d) for d in dns]
    nombres += [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]  # ValueError si la IP es inválida

    llave = ec.generate_private_key(ec.SECP256R1())
    sujeto = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "FireWatch backend")])
    ahora = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(sujeto).issuer_name(sujeto)
        .public_key(llave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(ahora - datetime.timedelta(minutes=5))
        .not_valid_after(ahora + datetime.timedelta(days=dias))
        .add_extension(x509.SubjectAlternativeName(nombres), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False,
                                     data_encipherment=False, key_agreement=True, key_cert_sign=False,
                                     crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(llave, hashes.SHA256())
    )
    key_p.write_bytes(llave.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
    try:
        os.chmod(key_p, 0o600)  # en Windows no hace nada; en Linux/Mac deja la llave solo para tu usuario
    except OSError:
        pass
    cert_p.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_p, key_p


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ip", action="append", default=[], help="IP del servidor en la red (repetible)")
    ap.add_argument("--dns", action="append", default=[], help="nombre del servidor (repetible)")
    ap.add_argument("--dias", type=int, default=365)
    ap.add_argument("--salida", default="certs", help="carpeta de salida (por defecto certs/)")
    ap.add_argument("--forzar", action="store_true", help="reemplazar si ya existen")
    a = ap.parse_args(argv)
    try:
        cert_p, key_p = generar(a.ip, a.dns, a.dias, Path(a.salida), a.forzar)
    except (FileExistsError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    except ImportError:
        print("Falta la librería: pip install cryptography", file=sys.stderr)
        return 2
    print(f"Certificado: {cert_p}\nLlave privada: {key_p}  (NO la subas a git ni la compartas)")
    print("En .env:\n  FIREWATCH_SSL_CERT=%s\n  FIREWATCH_SSL_KEY=%s" % (cert_p, key_p))
    return 0


if __name__ == "__main__":
    sys.exit(main())
