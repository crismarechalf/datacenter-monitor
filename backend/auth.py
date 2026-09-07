"""Autenticacion y autorizacion (seccion 8 de CLAUDE.md).

Nada queda abierto:
- El dashboard exige login; las passwords se guardan hasheadas con bcrypt.
- El ingreso de lecturas exige credencial del dispositivo/simulador.
- Los secretos (clave JWT, credenciales) salen solo del .env.
"""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from dotenv import load_dotenv
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from db import Usuario, get_db
from schemas import TokenData

load_dotenv()

# --- Configuracion (solo desde el entorno; nunca hardcodeada) ---------------

JWT_SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", "")
JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
MINUTOS_EXPIRACION_TOKEN: int = int(os.getenv("MINUTOS_EXPIRACION_TOKEN", "60"))

# API key del simulador / dispositivo para POST /lecturas.
DEVICE_API_KEY: str = os.getenv("DEVICE_API_KEY", "")

if not JWT_SECRET_KEY:
    raise RuntimeError(
        "Falta JWT_SECRET_KEY en el entorno. Copia .env.example a .env y "
        "genera una clave: python -c \"import secrets; print(secrets.token_urlsafe(48))\""
    )

# bcrypt: las passwords nunca se guardan ni se comparan en texto plano.
contexto_password = CryptContext(schemes=["bcrypt"], deprecated="auto")

# tokenUrl apunta al endpoint de login; habilita el boton "Authorize" en /docs.
esquema_oauth2 = OAuth2PasswordBearer(tokenUrl="auth/login", auto_error=False)


# --- Passwords --------------------------------------------------------------


def hashear_password(password: str) -> str:
    """Devuelve el hash bcrypt de una password en claro."""
    return contexto_password.hash(password)


def verificar_password(password: str, password_hash: str) -> bool:
    """Compara una password en claro contra su hash almacenado."""
    return contexto_password.verify(password, password_hash)


# --- Tokens JWT -------------------------------------------------------------


def crear_token_acceso(username: str, rol: str = "encargado") -> str:
    """Firma un JWT con el usuario, su rol y la expiracion."""
    expira = datetime.now(timezone.utc) + timedelta(minutes=MINUTOS_EXPIRACION_TOKEN)
    payload = {"sub": username, "rol": rol, "exp": expira}
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def decodificar_token(token: str) -> Optional[TokenData]:
    """Valida firma y expiracion. Devuelve None si el token no sirve."""
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except JWTError:
        return None
    username = payload.get("sub")
    if not username:
        return None
    return TokenData(username=username, rol=payload.get("rol", "encargado"))


# --- Usuarios ---------------------------------------------------------------


def obtener_usuario(db: Session, username: str) -> Optional[Usuario]:
    """Busca un usuario por username."""
    return db.scalar(select(Usuario).where(Usuario.username == username))


def crear_usuario(
    db: Session, username: str, password: str, rol: str = "encargado"
) -> Usuario:
    """Crea un usuario con la password ya hasheada."""
    usuario = Usuario(
        username=username, password_hash=hashear_password(password), rol=rol
    )
    db.add(usuario)
    db.commit()
    db.refresh(usuario)
    return usuario


def autenticar_usuario(db: Session, username: str, password: str) -> Optional[Usuario]:
    """Valida credenciales de login. Devuelve None si no calzan.

    Si el usuario no existe igual se hashea una password dummy, para que el
    tiempo de respuesta no delate que usuarios existen y cuales no.
    """
    usuario = obtener_usuario(db, username)
    if usuario is None:
        contexto_password.dummy_verify()
        return None
    if not verificar_password(password, usuario.password_hash):
        return None
    return usuario


def asegurar_usuario_inicial(db: Session) -> None:
    """Crea el usuario encargado inicial si la tabla esta vacia.

    Toma ADMIN_USER / ADMIN_PASSWORD del .env. Sin esas variables no crea
    nada: preferible no poder entrar que dejar una credencial por defecto.
    """
    username = os.getenv("ADMIN_USER", "")
    password = os.getenv("ADMIN_PASSWORD", "")
    if not username or not password:
        return
    if obtener_usuario(db, username) is None:
        crear_usuario(db, username, password, rol="encargado")


# --- Dependencias de FastAPI ------------------------------------------------

_NO_AUTORIZADO = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Credenciales invalidas o ausentes",
    headers={"WWW-Authenticate": "Bearer"},
)


def usuario_actual(
    token: Optional[str] = Depends(esquema_oauth2),
    db: Session = Depends(get_db),
) -> Usuario:
    """Protege los endpoints del dashboard: exige un JWT valido."""
    if not token:
        raise _NO_AUTORIZADO
    datos = decodificar_token(token)
    if datos is None:
        raise _NO_AUTORIZADO
    usuario = obtener_usuario(db, datos.username)
    if usuario is None:
        raise _NO_AUTORIZADO
    return usuario


def verificar_dispositivo(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    token: Optional[str] = Depends(esquema_oauth2),
    db: Session = Depends(get_db),
) -> str:
    """Protege POST /lecturas. No existe version sin autenticacion.

    Acepta dos credenciales, y devuelve quien envio la lectura (queda en el
    log de auditoria):
    - `X-API-Key` con la key del dispositivo (el simulador de P1 y el
      emisor por red de P2), comparada en tiempo constante.
    - o un JWT de usuario valido, para poder disparar lecturas desde el
      panel ya autenticado o desde /docs durante la demo.
    """
    if x_api_key and DEVICE_API_KEY and secrets.compare_digest(x_api_key, DEVICE_API_KEY):
        return "dispositivo"
    if token:
        datos = decodificar_token(token)
        if datos is not None and obtener_usuario(db, datos.username) is not None:
            return datos.username
    raise _NO_AUTORIZADO
