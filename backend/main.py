"""API del Monitor de Datacenter: rutas REST, WebSocket y estaticos.

Orquesta el ETL completo (seccion 3 de CLAUDE.md):
  panel.html -> POST /lecturas -> reglas.py -> PostgreSQL -> correo
                                                    |
                                                    v
                                        dashboard.html <- WS /ws
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Optional

from dotenv import load_dotenv
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

import correo
from auth import (
    asegurar_usuario_inicial,
    autenticar_usuario,
    crear_token_acceso,
    decodificar_token,
    obtener_usuario,
    usuario_actual,
    verificar_dispositivo,
)
from db import Alerta, Lectura, SesionLocal, Usuario, crear_tablas, get_db
from reglas import Estado, evaluar_lectura, hubo_flanco_critico
from schemas import (
    AlertaSalida,
    EstadoSistema,
    EstadosVariables,
    LecturaEntrada,
    LecturaSalida,
    LoginRequest,
    Token,
    UsuarioSalida,
)

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("monitor")

DIRECTORIO_FRONTEND = Path(__file__).resolve().parent.parent / "frontend"

# CORS restringido a los origenes propios (seccion 8): nunca "*".
ORIGENES_PERMITIDOS: list[str] = [
    origen.strip()
    for origen in os.getenv(
        "CORS_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000"
    ).split(",")
    if origen.strip()
]


# --- Estado en memoria: flanco critico y contador de incidentes -------------


class EstadoMonitor:
    """Recuerda el ultimo estado para contar incidentes por flanco de subida."""

    def __init__(self) -> None:
        self.estado_anterior: Optional[Estado] = None
        self.incidentes_criticos: int = 0

    def registrar(self, estado_actual: Estado) -> bool:
        """Actualiza el estado y devuelve True si hubo flanco a CRITICO."""
        flanco = hubo_flanco_critico(self.estado_anterior, estado_actual)
        if flanco:
            self.incidentes_criticos += 1
        self.estado_anterior = estado_actual
        return flanco

    def sembrar_desde_historico(self, estados: list[Estado]) -> None:
        """Reconstruye el contador al arrancar, recorriendo el historico."""
        anterior: Optional[Estado] = None
        for estado in estados:
            if hubo_flanco_critico(anterior, estado):
                self.incidentes_criticos += 1
            anterior = estado
        self.estado_anterior = anterior


monitor = EstadoMonitor()


# --- Conexiones WebSocket ---------------------------------------------------


class GestorConexiones:
    """Mantiene los dashboards conectados y les empuja el estado."""

    def __init__(self) -> None:
        self.activas: set[WebSocket] = set()

    async def conectar(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.activas.add(websocket)
        logger.info("Dashboard conectado (%d activos)", len(self.activas))

    def desconectar(self, websocket: WebSocket) -> None:
        self.activas.discard(websocket)
        logger.info("Dashboard desconectado (%d activos)", len(self.activas))

    async def difundir(self, payload: dict) -> None:
        """Envia el payload a todos; descarta las conexiones caidas."""
        caidas: list[WebSocket] = []
        for conexion in list(self.activas):
            try:
                await conexion.send_json(payload)
            except Exception:  # noqa: BLE001 - una conexion muerta no frena al resto
                caidas.append(conexion)
        for conexion in caidas:
            self.desconectar(conexion)


gestor = GestorConexiones()


# --- Ciclo de vida ----------------------------------------------------------


@asynccontextmanager
async def ciclo_de_vida(app: FastAPI) -> AsyncIterator[None]:
    """Al arrancar: crea tablas, usuario inicial y siembra el contador."""
    crear_tablas()
    with SesionLocal() as sesion:
        asegurar_usuario_inicial(sesion)
        estados = list(
            sesion.scalars(select(Lectura.estado_general).order_by(Lectura.timestamp))
        )
    monitor.sembrar_desde_historico(estados)
    logger.info(
        "Monitor iniciado. Incidentes criticos historicos: %d", monitor.incidentes_criticos
    )
    yield


app = FastAPI(
    title="Monitor de Datacenter",
    description="Monitoreo de sala de networking: 5 variables, alertas y tiempo real.",
    version="1.0.0",
    lifespan=ciclo_de_vida,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ORIGENES_PERMITIDOS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key"],
)


# --- Helpers ----------------------------------------------------------------


def _armar_estado_sistema(
    lectura: Lectura, alertas: list[Alerta], acceso_autorizado: bool
) -> EstadoSistema:
    """Recalcula la clasificacion de una lectura y arma el payload comun."""
    resultado = evaluar_lectura(
        temperatura=lectura.temperatura,
        humedad=lectura.humedad,
        carga_ups=lectura.carga_ups,
        humo=lectura.humo,
        puerta_abierta=lectura.puerta_abierta,
        mantencion=lectura.mantencion,
    )
    return EstadoSistema(
        lectura=LecturaSalida.model_validate(lectura),
        estado_general=lectura.estado_general,
        estados=EstadosVariables(**resultado.estados),
        alertas=[AlertaSalida.model_validate(a) for a in alertas],
        acceso_autorizado=acceso_autorizado,
        incidentes_criticos=monitor.incidentes_criticos,
    )


# --- Autenticacion ----------------------------------------------------------


@app.post("/auth/login", response_model=Token, tags=["auth"])
async def login(request: Request, db: Session = Depends(get_db)) -> Token:
    """Valida credenciales y devuelve el JWT.

    Acepta JSON (lo que manda el frontend) o form-urlencoded, para que el
    boton Authorize de /docs sirva en la demo.
    """
    tipo = request.headers.get("content-type", "")
    if tipo.startswith("application/json"):
        crudo = await request.json()
    else:
        crudo = dict(await request.form())

    try:
        credenciales = LoginRequest(**crudo)
    except (ValidationError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Faltan username o password",
        )

    usuario = autenticar_usuario(db, credenciales.username, credenciales.password)
    if usuario is None:
        logger.warning("Login fallido para '%s'", credenciales.username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario o password incorrectos",
            headers={"WWW-Authenticate": "Bearer"},
        )

    logger.info("Login correcto: %s", usuario.username)
    return Token(access_token=crear_token_acceso(usuario.username, usuario.rol))


@app.get("/auth/yo", response_model=UsuarioSalida, tags=["auth"])
async def yo(usuario: Usuario = Depends(usuario_actual)) -> Usuario:
    """Devuelve el usuario autenticado; el dashboard lo usa para validar sesion."""
    return usuario


# --- Lecturas ---------------------------------------------------------------


@app.post("/lecturas", response_model=EstadoSistema, tags=["lecturas"])
async def recibir_lectura(
    entrada: LecturaEntrada,
    tareas: BackgroundTasks,
    db: Session = Depends(get_db),
    emisor: str = Depends(verificar_dispositivo),
) -> EstadoSistema:
    """Ingesta de una lectura. Exige credencial: no existe sin autenticacion.

    Corre reglas, guarda lectura y alertas, dispara correo si hay flanco
    critico y hace broadcast por WebSocket.
    """
    resultado = evaluar_lectura(
        temperatura=entrada.temperatura,
        humedad=entrada.humedad,
        carga_ups=entrada.carga_ups,
        humo=entrada.humo,
        puerta_abierta=entrada.puerta_abierta,
        mantencion=entrada.mantencion,
    )

    lectura = Lectura(
        temperatura=entrada.temperatura,
        humedad=entrada.humedad,
        carga_ups=entrada.carga_ups,
        humo=entrada.humo,
        puerta_abierta=entrada.puerta_abierta,
        mantencion=entrada.mantencion,
        estado_general=resultado.estado_general,
    )
    db.add(lectura)

    # Log de auditoria: queda quien envio la lectura que gatillo la alerta.
    alertas = [
        Alerta(
            variable=a.variable,
            severidad=a.severidad,
            mensaje=f"{a.mensaje} (origen: {emisor})",
        )
        for a in resultado.alertas
    ]
    db.add_all(alertas)
    db.commit()
    db.refresh(lectura)
    for alerta in alertas:
        db.refresh(alerta)

    flanco = monitor.registrar(resultado.estado_general)
    estado = _armar_estado_sistema(lectura, alertas, resultado.acceso_autorizado)

    if flanco:
        logger.warning(
            "Incidente critico #%d: %s",
            monitor.incidentes_criticos,
            [a.mensaje for a in resultado.alertas],
        )
        # En segundo plano: el correo no bloquea la respuesta al simulador.
        tareas.add_task(correo.enviar_alerta_critica, estado)

    await gestor.difundir(estado.model_dump(mode="json"))
    return estado


@app.get("/lecturas/ultima", response_model=EstadoSistema, tags=["lecturas"])
async def ultima_lectura(
    db: Session = Depends(get_db), usuario: Usuario = Depends(usuario_actual)
) -> EstadoSistema:
    """Estado actual completo; el dashboard lo pide al cargar, antes del WS."""
    lectura = db.scalar(select(Lectura).order_by(Lectura.id.desc()).limit(1))
    if lectura is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Todavia no hay lecturas"
        )
    alertas = list(
        db.scalars(
            select(Alerta).where(Alerta.reconocida.is_(False)).order_by(Alerta.id.desc())
        )
    )
    acceso_autorizado = lectura.puerta_abierta and lectura.mantencion
    return _armar_estado_sistema(lectura, alertas, acceso_autorizado)


@app.get("/lecturas/historial", response_model=list[LecturaSalida], tags=["lecturas"])
async def historial(
    limite: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(usuario_actual),
) -> list[Lectura]:
    """Historico de lecturas, de la mas reciente a la mas antigua."""
    return list(db.scalars(select(Lectura).order_by(Lectura.id.desc()).limit(limite)))


# --- Alertas ----------------------------------------------------------------


@app.get("/alertas", response_model=list[AlertaSalida], tags=["alertas"])
async def listar_alertas(
    solo_activas: bool = Query(default=False, description="Solo las no reconocidas"),
    limite: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(usuario_actual),
) -> list[Alerta]:
    """Registro de eventos / log de auditoria."""
    consulta = select(Alerta).order_by(Alerta.id.desc()).limit(limite)
    if solo_activas:
        consulta = (
            select(Alerta)
            .where(Alerta.reconocida.is_(False))
            .order_by(Alerta.id.desc())
            .limit(limite)
        )
    return list(db.scalars(consulta))


@app.post("/alertas/{alerta_id}/reconocer", response_model=AlertaSalida, tags=["alertas"])
async def reconocer_alerta(
    alerta_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(usuario_actual),
) -> Alerta:
    """Marca una alerta como reconocida por el encargado."""
    alerta = db.get(Alerta, alerta_id)
    if alerta is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Alerta no encontrada"
        )
    alerta.reconocida = True
    db.commit()
    db.refresh(alerta)
    logger.info("Alerta %d reconocida por %s", alerta_id, usuario.username)
    return alerta


# --- WebSocket --------------------------------------------------------------


@app.websocket("/ws")
async def canal_tiempo_real(websocket: WebSocket, token: str = Query(default="")) -> None:
    """Canal de tiempo real. Tambien exige token: no hay canal abierto.

    El token va por query string (`/ws?token=...`) porque la API de
    WebSocket del navegador no permite mandar headers.
    """
    datos = decodificar_token(token) if token else None
    if datos is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    with SesionLocal() as sesion:
        if obtener_usuario(sesion, datos.username) is None:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

    await gestor.conectar(websocket)
    try:
        while True:
            # No esperamos mensajes del cliente; esto mantiene viva la conexion.
            await websocket.receive_text()
    except WebSocketDisconnect:
        gestor.desconectar(websocket)
    except Exception:  # noqa: BLE001
        gestor.desconectar(websocket)


# --- Frontend estatico ------------------------------------------------------


@app.get("/", include_in_schema=False)
async def raiz() -> FileResponse:
    """Dashboard del encargado."""
    return FileResponse(DIRECTORIO_FRONTEND / "dashboard.html")


@app.get("/panel", include_in_schema=False)
async def panel() -> FileResponse:
    """Simulador de sensores."""
    return FileResponse(DIRECTORIO_FRONTEND / "panel.html")


if DIRECTORIO_FRONTEND.is_dir():
    app.mount("/static", StaticFiles(directory=DIRECTORIO_FRONTEND), name="static")
