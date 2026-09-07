"""Motor de reglas del Monitor de Datacenter (SIY6122).

Capa *Transform* del ETL: recibe una lectura cruda de las 5 variables,
clasifica cada una segun los umbrales cerrados de CLAUDE.md (seccion 4)
y resuelve el estado general con la regla "gana el peor" mas las
excepciones de humo e intrusion (seccion 5).

Este modulo es puro: no toca base de datos, ni red, ni correo. Eso lo
hace `main.py`. Asi se puede probar solo y se reutiliza igual en P2 y P3.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Estado(str, Enum):
    """Severidad de una variable o del sistema completo."""

    NORMAL = "NORMAL"
    ADVERTENCIA = "ADVERTENCIA"
    CRITICO = "CRITICO"


# Orden de severidad: NORMAL < ADVERTENCIA < CRITICO.
_SEVERIDAD: dict[Estado, int] = {
    Estado.NORMAL: 0,
    Estado.ADVERTENCIA: 1,
    Estado.CRITICO: 2,
}

# --- Umbrales (seccion 4 de CLAUDE.md; cerrados, no tocar) -------------------
# Los limites de NORMAL y ADVERTENCIA son inclusivos; CRITICO es estricto
# (> o <), tal como esta escrito en la tabla del proyecto.
TEMP_NORMAL_MIN, TEMP_NORMAL_MAX = 18.0, 27.0
TEMP_CRITICO_MIN, TEMP_CRITICO_MAX = 15.0, 32.0

HUM_NORMAL_MIN, HUM_NORMAL_MAX = 40.0, 60.0
HUM_CRITICO_MIN, HUM_CRITICO_MAX = 20.0, 70.0

UPS_ADVERTENCIA_MIN = 70.0
UPS_CRITICO_MAX = 90.0


def peor(*estados: Estado) -> Estado:
    """Devuelve el estado mas severo de los recibidos ("gana el peor")."""
    if not estados:
        return Estado.NORMAL
    return max(estados, key=lambda e: _SEVERIDAD[e])


# --- Clasificacion por variable ---------------------------------------------


def clasificar_temperatura(temperatura: float) -> Estado:
    """Variable 1 — temperatura del aire en C.

    NORMAL 18–27 | ADVERTENCIA 27–32 o 15–18 | CRITICO > 32 o < 15.
    """
    if temperatura > TEMP_CRITICO_MAX or temperatura < TEMP_CRITICO_MIN:
        return Estado.CRITICO
    if TEMP_NORMAL_MIN <= temperatura <= TEMP_NORMAL_MAX:
        return Estado.NORMAL
    return Estado.ADVERTENCIA


def clasificar_humedad(humedad: float) -> Estado:
    """Variable 2 — humedad relativa en %.

    NORMAL 40–60 | ADVERTENCIA 20–40 o 60–70 | CRITICO < 20 o > 70.
    """
    if humedad < HUM_CRITICO_MIN or humedad > HUM_CRITICO_MAX:
        return Estado.CRITICO
    if HUM_NORMAL_MIN <= humedad <= HUM_NORMAL_MAX:
        return Estado.NORMAL
    return Estado.ADVERTENCIA


def clasificar_carga_ups(carga_ups: float) -> Estado:
    """Variable 3 — carga electrica UPS/PDU en %.

    NORMAL < 70 | ADVERTENCIA 70–90 | CRITICO > 90.
    """
    if carga_ups > UPS_CRITICO_MAX:
        return Estado.CRITICO
    if carga_ups >= UPS_ADVERTENCIA_MIN:
        return Estado.ADVERTENCIA
    return Estado.NORMAL


def clasificar_humo(humo: bool) -> Estado:
    """Variable 4 — humo / incendio. Evento: sin humo o CRITICO."""
    return Estado.CRITICO if humo else Estado.NORMAL


def acceso_autorizado(puerta_abierta: bool, mantencion: bool) -> bool:
    """True si la puerta abierta esta respaldada por modo mantencion."""
    return puerta_abierta and mantencion


def clasificar_acceso(puerta_abierta: bool, mantencion: bool) -> Estado:
    """Variable 5 — acceso fisico (puerta + mantencion).

    Puerta cerrada = NORMAL. Puerta abierta CON mantencion = acceso
    autorizado (NORMAL, solo evento informativo). Puerta abierta SIN
    mantencion = intrusion (CRITICO).
    """
    if puerta_abierta and not mantencion:
        return Estado.CRITICO
    return Estado.NORMAL


# --- Evaluacion de la lectura completa --------------------------------------


@dataclass(frozen=True)
class Alerta:
    """Alerta derivada de una variable fuera de rango.

    Se mapea 1:1 con la tabla `alertas` del modelo de datos (seccion 6).
    """

    variable: str
    severidad: Estado
    mensaje: str


@dataclass(frozen=True)
class ResultadoEvaluacion:
    """Salida del motor de reglas para una lectura."""

    estado_general: Estado
    estados: dict[str, Estado]
    alertas: list[Alerta] = field(default_factory=list)
    acceso_autorizado: bool = False

    @property
    def es_critico(self) -> bool:
        return self.estado_general is Estado.CRITICO


def _mensaje_continua(nombre: str, valor: float, unidad: str, estado: Estado) -> str:
    return f"{nombre} en {estado.value}: {valor:g}{unidad}"


def evaluar_lectura(
    temperatura: float,
    humedad: float,
    carga_ups: float,
    humo: bool = False,
    puerta_abierta: bool = False,
    mantencion: bool = False,
) -> ResultadoEvaluacion:
    """Clasifica las 5 variables y resuelve el estado general.

    Regla (seccion 5): humo detectado o acceso no autorizado fuerzan
    CRITICO siempre; en el resto gana el peor estado de las 5 variables.
    Como humo e intrusion ya se clasifican como CRITICO, "gana el peor"
    las arrastra por si solo — el resultado es el mismo por ambos caminos.
    """
    estado_temperatura = clasificar_temperatura(temperatura)
    estado_humedad = clasificar_humedad(humedad)
    estado_carga_ups = clasificar_carga_ups(carga_ups)
    estado_humo = clasificar_humo(humo)
    estado_acceso = clasificar_acceso(puerta_abierta, mantencion)

    estados: dict[str, Estado] = {
        "temperatura": estado_temperatura,
        "humedad": estado_humedad,
        "carga_ups": estado_carga_ups,
        "humo": estado_humo,
        "acceso": estado_acceso,
    }

    # Excepciones explicitas: no se promedian ni se esperan.
    if humo or (puerta_abierta and not mantencion):
        estado_general = Estado.CRITICO
    else:
        estado_general = peor(*estados.values())

    alertas: list[Alerta] = []
    if estado_temperatura is not Estado.NORMAL:
        alertas.append(
            Alerta(
                "temperatura",
                estado_temperatura,
                _mensaje_continua("Temperatura", temperatura, " C", estado_temperatura),
            )
        )
    if estado_humedad is not Estado.NORMAL:
        alertas.append(
            Alerta(
                "humedad",
                estado_humedad,
                _mensaje_continua("Humedad relativa", humedad, "%", estado_humedad),
            )
        )
    if estado_carga_ups is not Estado.NORMAL:
        alertas.append(
            Alerta(
                "carga_ups",
                estado_carga_ups,
                _mensaje_continua("Carga UPS/PDU", carga_ups, "%", estado_carga_ups),
            )
        )
    if estado_humo is Estado.CRITICO:
        alertas.append(
            Alerta("humo", Estado.CRITICO, "Humo detectado en la sala")
        )
    if estado_acceso is Estado.CRITICO:
        alertas.append(
            Alerta(
                "acceso",
                Estado.CRITICO,
                "Intrusion: puerta abierta sin modo mantencion",
            )
        )

    return ResultadoEvaluacion(
        estado_general=estado_general,
        estados=estados,
        alertas=alertas,
        acceso_autorizado=acceso_autorizado(puerta_abierta, mantencion),
    )


def hubo_flanco_critico(
    estado_anterior: Optional[Estado], estado_actual: Estado
) -> bool:
    """True solo en el flanco de subida a CRITICO (seccion 5).

    Sirve para contar incidentes y disparar el correo una vez por
    incidente, no en cada lectura mientras el sistema sigue critico.
    """
    return estado_actual is Estado.CRITICO and estado_anterior is not Estado.CRITICO
