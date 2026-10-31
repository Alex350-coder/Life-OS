"""Registros de uso de modelos y de auditoría (ADR-0008). Solo campos tipados, nunca contenido."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from lifeos.domain.errors import LifeOSError


class CostStatus(StrEnum):
    ESTIMADO = "estimado"
    DESCONOCIDO = "desconocido"  # faltan tokens o tarifa fechada: nunca se registra 0


class UsageOutcome(StrEnum):
    OK = "ok"
    TRUNCADA = "truncada"  # el modelo alcanzó max_tokens: el texto puede estar incompleto
    TIMEOUT = "timeout"
    LIMITE_TASA = "limite_tasa"
    ERROR_PROVEEDOR = "error_proveedor"
    ERROR_CONEXION = "error_conexion"
    RESPUESTA_MALFORMADA = "respuesta_malformada"
    SALIDA_INVALIDA = "salida_invalida"
    ERROR_INTERNO = "error_interno"


class AuditResult(StrEnum):
    INTENTO = "intento"  # se registra antes de escribir: sin él, la escritura no ocurre
    OK = "ok"
    RECHAZADO = "rechazado"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class UsageRecord:
    """Un intento de llamada a un modelo (RULES F.3)."""

    ts: datetime
    correlation_id: str
    provider: str
    model: str
    profile: str
    agent: str
    ritual: str
    attempt: int
    latency_ms: int
    tokens_prompt: int | None
    tokens_completion: int | None
    cost_usd: Decimal | None
    cost_status: CostStatus
    outcome: UsageOutcome
    http_status: int | None

    def __post_init__(self) -> None:
        if self.attempt < 1 or self.latency_ms < 0:
            raise ValueError("intento >= 1 y latencia >= 0")
        if any(t is not None and t < 0 for t in (self.tokens_prompt, self.tokens_completion)):
            raise ValueError("los tokens no pueden ser negativos")
        if (self.cost_usd is None) != (self.cost_status is CostStatus.DESCONOCIDO):
            raise ValueError("un costo desconocido no tiene importe y uno estimado sí")
        if self.cost_usd is not None and self.cost_usd < 0:
            raise ValueError("el costo no puede ser negativo")


@dataclass(frozen=True, slots=True)
class AuditRecord:
    """Una operación sobre la bóveda (RULES E.7): actor, acción, destino, hora y resultado."""

    ts: datetime
    correlation_id: str
    actor: str
    action: str
    target: str
    result: AuditResult
    reason: str | None = None


class LogWriteError(LifeOSError):
    """No se pudo registrar. Quien llama no debe continuar como si se hubiera registrado."""


class UsageLog(Protocol):
    def append(self, record: UsageRecord) -> None: ...


class AuditLog(Protocol):
    def append(self, record: AuditRecord) -> None: ...


@dataclass(frozen=True, slots=True)
class UsageScan:
    """Registros de uso de un mes. `malformed` cuenta las líneas que no se pudieron leer."""

    records: tuple[UsageRecord, ...]
    malformed: int
    files: int


class UsageReader(Protocol):
    """Lectura del registro de uso (solo lectura; `lifeos uso`)."""

    def read_month(self, year: int, month: int) -> UsageScan: ...


@dataclass(frozen=True, slots=True)
class AuditScan:
    """Registros de auditoría de un mes. `malformed` cuenta las líneas que no se pudieron leer."""

    records: tuple[AuditRecord, ...]
    malformed: int
    files: int


class AuditReader(Protocol):
    """Lectura de la auditoría (solo lectura; historial de cambios de la web, F5-T04)."""

    def read_month(self, year: int, month: int) -> AuditScan: ...
