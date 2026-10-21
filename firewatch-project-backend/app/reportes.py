"""Reportes manuales (CONTRATO_API.md §5): formularios de ciudadanos y bomberos.

Lo que vive aquí es lo que no depende de FastAPI: el modelo de datos con su validación y el límite de envíos.
Las rutas están en main.py.

Por qué tanto cuidado: `POST /reportes` puede recibirse SIN iniciar sesión (formulario público), así que
cualquiera en la red puede llamarlo. Se limita por IP y en total, se acota el tamaño de todo y los textos se
guardan limpios de caracteres de control. El cliente que los muestre igual debe escaparlos (no confiar en el texto).
"""
import re
import threading
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

MAX_IMG_REPORTE = 3 * 1024 * 1024         # el formulario público no necesita más; las alertas de cámara sí admiten 5 MB
MAX_CUERPO_EXTRA = 64 * 1024              # margen del multipart (campos de texto) sobre el tamaño de la imagen
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")  # control salvo \t \n \r


def limpiar(texto: str) -> str:
    return _CTRL.sub("", texto).strip()


class ReporteIn(BaseModel):
    origen: Literal["USUARIO", "BOMBERO"]
    descripcion: str = Field(min_length=5, max_length=1000)
    gps: list[float] = Field(min_length=2, max_length=2)
    contacto: Optional[str] = Field(default=None, max_length=120)

    @field_validator("descripcion")
    @classmethod
    def _desc(cls, v: str) -> str:
        v = limpiar(v)
        if len(v) < 5:
            raise ValueError("la descripción es demasiado corta")
        return v

    @field_validator("contacto")
    @classmethod
    def _contacto(cls, v: Optional[str]) -> Optional[str]:
        v = limpiar(v) if v else ""
        return v or None

    @field_validator("gps")
    @classmethod
    def _gps(cls, v: list[float]) -> list[float]:
        lat, lon = v
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError("gps fuera de rango: [lat entre -90 y 90, lon entre -180 y 180]")
        return v


class LimiteEnvios:
    """Ventana deslizante por clave (la IP) más un tope global.

    El tope global frena a quien rota de IP. Todo vive en memoria: se reinicia con el servidor, lo que alcanza
    para un servicio de una sola instancia. Si hubiera varias instancias habría que moverlo a la base o a un proxy.
    """

    def __init__(self, maximo: int, ventana_s: float, maximo_global: int):
        self.maximo, self.ventana, self.maximo_global = maximo, ventana_s, maximo_global
        self._por_clave: dict[str, list[float]] = {}
        self._global: list[float] = []
        self._lock = threading.Lock()

    def intentar(self, clave: str, ahora: float) -> Optional[int]:
        """Registra un envío. Devuelve None si pasa, o los segundos de espera si se excede el límite."""
        corte = ahora - self.ventana
        with self._lock:
            self._global = [t for t in self._global if t > corte]
            if len(self._global) >= self.maximo_global:
                return max(1, int(self._global[0] + self.ventana - ahora) + 1)
            veces = [t for t in self._por_clave.get(clave, []) if t > corte]
            if len(veces) >= self.maximo:
                self._por_clave[clave] = veces
                return max(1, int(veces[0] + self.ventana - ahora) + 1)
            veces.append(ahora)
            self._por_clave[clave] = veces
            self._global.append(ahora)
            if len(self._por_clave) > 5000:  # no crecer sin límite si atacan con muchas IP
                self._por_clave = {k: v for k, v in self._por_clave.items() if v and v[-1] > corte}
            return None
