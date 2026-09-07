"""Conexion a PostgreSQL, modelos SQLAlchemy y sesion.

Capa *Load* del ETL: aca vive el historico (`lecturas`) y el registro de
eventos / log de auditoria (`alertas`), segun el modelo de datos de la
seccion 6 de CLAUDE.md.

Toda la configuracion sale del entorno (.env) — ningun secreto va
hardcodeado (seccion 8).
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Iterator

from dotenv import load_dotenv
from sqlalchemy import Boolean, DateTime, Enum as SQLEnum, Float, String, create_engine, func
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from reglas import Estado

load_dotenv()

# En docker-compose el host es el servicio `db`; en local, localhost.
# La URL completa siempre viene del .env: no hay credenciales en el codigo.
DATABASE_URL: str = os.getenv(
    "DATABASE_URL", "postgresql+psycopg2://monitor:monitor@db:5432/datacenter"
)

# pool_pre_ping evita conexiones muertas cuando Postgres arranca despues
# que la app dentro de docker-compose.
engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)

SesionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

# Enum de estado compartido con reglas.py. native_enum=False lo guarda como
# VARCHAR + CHECK: mismo dato legible en la tabla, sin tipos ENUM de Postgres
# que compliquen migraciones en P3.
EstadoSQL = SQLEnum(
    Estado,
    name="estado_severidad",
    native_enum=False,
    length=12,
    values_callable=lambda enum: [e.value for e in enum],
)


class Base(DeclarativeBase):
    """Base declarativa de todos los modelos."""


class Usuario(Base):
    """Encargado que entra al dashboard. Password siempre hasheada (bcrypt)."""

    __tablename__ = "usuarios"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    rol: Mapped[str] = mapped_column(String(20), nullable=False, default="encargado")
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<Usuario {self.username} ({self.rol})>"


class Lectura(Base):
    """Historico: una fila por cada lectura recibida desde el simulador."""

    __tablename__ = "lecturas"

    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    temperatura: Mapped[float] = mapped_column(Float, nullable=False)
    humedad: Mapped[float] = mapped_column(Float, nullable=False)
    carga_ups: Mapped[float] = mapped_column(Float, nullable=False)
    humo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    puerta_abierta: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    mantencion: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    estado_general: Mapped[Estado] = mapped_column(EstadoSQL, nullable=False)

    def __repr__(self) -> str:
        return f"<Lectura {self.id} {self.estado_general} {self.timestamp}>"


class Alerta(Base):
    """Registro de eventos criticos / advertencias. Sirve de log de auditoria."""

    __tablename__ = "alertas"

    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    variable: Mapped[str] = mapped_column(String(30), nullable=False)
    severidad: Mapped[Estado] = mapped_column(EstadoSQL, nullable=False)
    mensaje: Mapped[str] = mapped_column(String(255), nullable=False)
    reconocida: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    def __repr__(self) -> str:
        return f"<Alerta {self.id} {self.variable} {self.severidad}>"


def crear_tablas() -> None:
    """Crea las tablas si no existen. Se llama al arrancar la app."""
    Base.metadata.create_all(bind=engine)


def get_db() -> Iterator[Session]:
    """Dependencia de FastAPI: entrega una sesion y la cierra siempre."""
    sesion = SesionLocal()
    try:
        yield sesion
    finally:
        sesion.close()
