import sqlite3
import hashlib
import os

DB_NAME = "firewatch_central.db"


def hash_password(password: str) -> str:
    """Genera un hash SHA-256 para almacenar contraseñas de forma segura."""
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def init_db():
    """Inicializa la base de datos y crea las tablas si no existen."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # 1. Tabla de Usuarios
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            nombre_completo TEXT NOT NULL,
            rol TEXT DEFAULT 'OPERADOR'
        )
    """)

    # 2. Tabla de Registro Histórico de Alertas
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS registro_alertas (
            id_alerta TEXT PRIMARY KEY,
            tipo TEXT NOT NULL,
            fecha_hora TEXT NOT NULL,
            camara_id TEXT NOT NULL,
            orientacion TEXT,
            azimut INTEGER,
            direccion TEXT,
            latitud REAL,
            longitud REAL,
            imagen_path TEXT,
            estado TEXT DEFAULT 'PENDIENTE',
            operador_resolucion TEXT,
            fecha_resolucion TEXT,
            motivo_descarte TEXT
        )
    """)

    # 3. Crear usuario administrador por defecto si la tabla está vacía
    cursor.execute("SELECT COUNT(*) FROM usuarios")
    if cursor.fetchone()[0] == 0:
        admin_pass = hash_password("admin123")
        cursor.execute("""
            INSERT INTO usuarios (username, password_hash, nombre_completo, rol)
            VALUES (?, ?, ?, ?)
        """, ("admin", admin_pass, "Administrador de Central", "ADMIN"))
        conn.commit()
        print("[DATABASE] Usuario por defecto creado: admin / admin123")

    conn.close()


def verify_user(username, password):
    """Verifica las credenciales del usuario en la base de datos."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    pass_hash = hash_password(password)
    cursor.execute("""
        SELECT username, nombre_completo, rol FROM usuarios
        WHERE username = ? AND password_hash = ?
    """, (username, pass_hash))

    user = cursor.fetchone()
    conn.close()

    if user:
        return {
            "username": user[0],
            "nombre_completo": user[1],
            "rol": user[2]
        }
    return None


if __name__ == "__main__":
    init_db()
    print("[DATABASE] Base de datos inicializada correctamente.")