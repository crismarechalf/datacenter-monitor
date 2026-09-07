"""Modelos Pydantic: contratos de entrada y salida de la API.

Separan lo que viaja por HTTP/WebSocket de los modelos SQLAlchemy de
`db.py`. Los rangos declarados aca son de *plausibilidad del sensor*
(que el dato tenga sentido fisico), no los umbrales de negocio: quien
clasifica NORMAL/ADVERTENCIA/CRITICO es siempre `reglas.py`.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from reglas import Estado

# --- Autenticacion (seccion 8) ----------------------------------------------


class LoginRequest(BaseModel):
    """Credenciales del encargado. La password nunca se guarda en claro."""

    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=4, max_length=128)


class Token(BaseModel):
    """Respuesta de POST /auth/login."""

    access_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    """Contenido util del JWT ya validado."""

    username: str
    rol: str = "encargado"


class UsuarioSalida(BaseModel):
    """Usuario expuesto por la API: sin password_hash, nunca."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    rol: str
    creado_en: datetime


# --- Lecturas (seccion 6 y 7) -----------------------------------------------


class LecturaEntrada(BaseModel):
    """Lo que manda el simulador (panel.html) a POST /lecturas."""

    temperatura: float = Field(ge=-50, le=100, description="Temperatura del aire en C")
    humedad: float = Field(ge=0, le=100, description="Humedad relativa en %")
    carga_ups: float = Field(ge=0, le=100, description="Carga de la UPS/PDU en %")
    humo: bool = Field(default=False, description="Humo / incendio detectado")
    puerta_abierta: bool = Field(default=False, description="Puerta de la sala abierta")
    mantencion: bool = Field(default=False, description="Modo mantencion activo")


class LecturaSalida(BaseModel):
    """Una lectura ya guardada, tal como sale de la base."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    timestamp: datetime
    temperatura: float
    humedad: float
    carga_ups: float
    humo: bool
    puerta_abierta: bool
    mantencion: bool
    estado_general: Estado


# --- Alertas ----------------------------------------------------------------


class AlertaSalida(BaseModel):
    """Una alerta del registro de eventos / log de auditoria."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    timestamp: datetime
    variable: str
    severidad: Estado
    mensaje: str
    reconocida: bool


# --- Estado del sistema (respuesta de POST /lecturas y push por WebSocket) ---


class EstadosVariables(BaseModel):
    """Clasificacion individual de las 5 variables (seccion 4)."""

    temperatura: Estado
    humedad: Estado
    carga_ups: Estado
    humo: Estado
    acceso: Estado


class EstadoSistema(BaseModel):
    """Foto completa del sistema: es lo que consume el dashboard.

    Se devuelve en POST /lecturas y GET /lecturas/ultima, y es el mismo
    payload que se empuja por WS /ws en cada lectura nueva.
    """

    lectura: LecturaSalida
    estado_general: Estado
    estados: EstadosVariables
    alertas: list[AlertaSalida] = Field(default_factory=list)
    acceso_autorizado: bool = False
    incidentes_criticos: int = Field(
        default=0, description="Incidentes criticos contados por flanco de subida"
    )
