"""Contraseñas (bcrypt), tokens de sesión y claves de cámara."""
import hashlib
import secrets
import time

import bcrypt

SESSION_TTL = 12 * 3600  # segundos


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def check_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("ascii"))
    except ValueError:
        return False


def new_secret() -> str:
    return secrets.token_urlsafe(32)


def digest(secret: str) -> str:
    """Las claves y tokens se guardan solo como hash SHA-256 (son aleatorios y largos)."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def now() -> float:
    return time.time()
