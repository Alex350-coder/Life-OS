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
    outcome: str
    http_status: int | None


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
