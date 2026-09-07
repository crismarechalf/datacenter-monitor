"""Envio de alertas por correo (SMTP).

Cuando el estado general sube a CRITICO (flanco de subida, no en cada
lectura), el sistema avisa al encargado sin que nadie este mirando la
pantalla. Toda la configuracion sale del .env (seccion 8).

Regla de oro: enviar correo nunca puede voltear la ingesta de lecturas.
Si el SMTP falla, se registra en el log y el sistema sigue funcionando.
"""

from __future__ import annotations

import logging
import os
from email.message import EmailMessage
from typing import Optional

import aiosmtplib
from dotenv import load_dotenv

from schemas import EstadoSistema

load_dotenv()

logger = logging.getLogger(__name__)

# --- Configuracion (solo desde el entorno) ----------------------------------

SMTP_HOST: str = os.getenv("SMTP_HOST", "")
SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER: str = os.getenv("SMTP_USER", "")
SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
SMTP_STARTTLS: bool = os.getenv("SMTP_STARTTLS", "true").lower() == "true"

CORREO_REMITENTE: str = os.getenv("CORREO_REMITENTE", SMTP_USER)
CORREO_DESTINO: str = os.getenv("CORREO_DESTINO", "")

# Interruptor para la demo: sin SMTP configurado el sistema igual corre,
# solo deja el aviso en el log en vez de mandar el correo.
CORREO_HABILITADO: bool = os.getenv("CORREO_HABILITADO", "true").lower() == "true"


def correo_configurado() -> bool:
    """True si hay lo minimo para enviar: host, remitente y destino."""
    return bool(CORREO_HABILITADO and SMTP_HOST and CORREO_REMITENTE and CORREO_DESTINO)


def _cuerpo_alerta(estado: EstadoSistema) -> str:
    """Arma el texto del correo con el detalle de la lectura critica."""
    lectura = estado.lectura
    acceso = (
        "autorizado (mantencion)"
        if estado.acceso_autorizado
        else ("ABIERTA SIN MANTENCION" if lectura.puerta_abierta else "cerrada")
    )
    detalle = "\n".join(
        f"  - [{a.severidad.value}] {a.variable}: {a.mensaje}" for a in estado.alertas
    ) or "  (sin detalle)"

    return f"""ALERTA CRITICA - Sala de networking / datacenter

Fecha y hora: {lectura.timestamp:%Y-%m-%d %H:%M:%S}
Estado general: {estado.estado_general.value}
Incidentes criticos acumulados: {estado.incidentes_criticos}

Lectura de los sensores
  Temperatura .... {lectura.temperatura:g} C     [{estado.estados.temperatura.value}]
  Humedad ........ {lectura.humedad:g} %      [{estado.estados.humedad.value}]
  Carga UPS/PDU .. {lectura.carga_ups:g} %      [{estado.estados.carga_ups.value}]
  Humo ........... {'DETECTADO' if lectura.humo else 'sin humo'}   [{estado.estados.humo.value}]
  Puerta ......... {acceso}   [{estado.estados.acceso.value}]
  Mantencion ..... {'activa' if lectura.mantencion else 'inactiva'}

Condiciones fuera de rango
{detalle}

Revise el dashboard del monitor para reconocer la alerta.
Mensaje automatico del Monitor de Datacenter - no responder.
"""


def _armar_mensaje(estado: EstadoSistema) -> EmailMessage:
    """Construye el EmailMessage listo para enviar."""
    mensaje = EmailMessage()
    variables = ", ".join(a.variable for a in estado.alertas) or "estado general"
    mensaje["Subject"] = f"[CRITICO] Datacenter: {variables}"
    mensaje["From"] = CORREO_REMITENTE
    mensaje["To"] = CORREO_DESTINO
    mensaje.set_content(_cuerpo_alerta(estado))
    return mensaje


async def enviar_alerta_critica(estado: EstadoSistema) -> bool:
    """Manda el correo de alerta critica. Devuelve True si se envio.

    Pensada para llamarse desde un BackgroundTask de FastAPI, para no
    bloquear la respuesta de POST /lecturas. Nunca propaga excepciones.
    """
    if not correo_configurado():
        logger.warning(
            "Correo no configurado o deshabilitado; alerta critica solo en log: %s",
            [a.mensaje for a in estado.alertas],
        )
        return False

    try:
        await aiosmtplib.send(
            _armar_mensaje(estado),
            hostname=SMTP_HOST,
            port=SMTP_PORT,
            username=SMTP_USER or None,
            password=SMTP_PASSWORD or None,
            start_tls=SMTP_STARTTLS,
            timeout=15,
        )
    except Exception as error:  # noqa: BLE001 - el correo nunca voltea la ingesta
        logger.error("No se pudo enviar la alerta critica por correo: %s", error)
        return False

    logger.info("Alerta critica enviada a %s", CORREO_DESTINO)
    return True


async def enviar_prueba(destino: Optional[str] = None) -> bool:
    """Correo de prueba para verificar la configuracion SMTP en la demo."""
    if not correo_configurado():
        logger.warning("Correo no configurado o deshabilitado.")
        return False

    mensaje = EmailMessage()
    mensaje["Subject"] = "[PRUEBA] Monitor de Datacenter"
    mensaje["From"] = CORREO_REMITENTE
    mensaje["To"] = destino or CORREO_DESTINO
    mensaje.set_content(
        "Correo de prueba del Monitor de Datacenter.\n"
        "Si lo estas leyendo, la configuracion SMTP quedo operativa."
    )

    try:
        await aiosmtplib.send(
            mensaje,
            hostname=SMTP_HOST,
            port=SMTP_PORT,
            username=SMTP_USER or None,
            password=SMTP_PASSWORD or None,
            start_tls=SMTP_STARTTLS,
            timeout=15,
        )
    except Exception as error:  # noqa: BLE001
        logger.error("Fallo el correo de prueba: %s", error)
        return False
    return True
