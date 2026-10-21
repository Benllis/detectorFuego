import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS usuarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    nombre_completo TEXT NOT NULL,
    rol TEXT NOT NULL DEFAULT 'OPERADOR'
);
CREATE TABLE IF NOT EXISTS sesiones (
    token_hash TEXT PRIMARY KEY,
    usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
    expira REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS camaras (
    camara_id TEXT PRIMARY KEY,
    nombre TEXT NOT NULL,
    ubicacion TEXT DEFAULT '',
    lat REAL, lon REAL,
    azimut INTEGER, orientacion TEXT,
    clave_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alertas (
    id_alerta TEXT PRIMARY KEY,
    camara_id TEXT NOT NULL,
    camara_nombre TEXT,
    ubicacion TEXT,
    lat REAL, lon REAL,
    tipo TEXT NOT NULL,
    timestamp REAL,
    fecha_hora TEXT NOT NULL,
    fuego_detectado INTEGER DEFAULT 0,
    humo_detectado INTEGER DEFAULT 0,
    confianza REAL,
    imagen_path TEXT,
    estado TEXT NOT NULL DEFAULT 'PENDIENTE',
    operador_resolucion TEXT,
    fecha_resolucion TEXT,
    motivo_descarte TEXT,
    recibida REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alertas_estado ON alertas(estado, recibida);
CREATE TABLE IF NOT EXISTS reportes (
    id_reporte TEXT PRIMARY KEY,
    origen TEXT NOT NULL,
    descripcion TEXT NOT NULL,
    lat REAL NOT NULL, lon REAL NOT NULL,
    contacto TEXT,
    estado TEXT NOT NULL,
    fecha_hora TEXT NOT NULL,
    imagen_path TEXT,
    usuario TEXT,
    ip TEXT,
    recibida REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reportes_estado ON reportes(estado, recibida);
"""


def connect(path: str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, check_same_thread=False)  # una conexión por petición
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init(path: str) -> None:
    con = connect(path)
    con.executescript(SCHEMA)
    con.commit()
    con.close()
