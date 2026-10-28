"""Propuestas que un modelo puede devolver ya validadas (contratos de salida de la Fase 2)."""

from dataclasses import dataclass
from datetime import date

from lifeos.domain.errors import ValidationError
from lifeos.domain.ids import TaskId

MAX_DRAFT_CHARS = 20_000  # mismo límite que una captura manual


@dataclass(frozen=True, slots=True)
class CaptureDraft:
    """Texto que el agente propone guardar en el Inbox (contrato `captura_v1`)."""

    text: str

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValidationError("captura", "", "no puede estar vacía")
        if len(self.text) > MAX_DRAFT_CHARS:
            raise ValidationError("captura", len(self.text), f"máximo {MAX_DRAFT_CHARS} caracteres")


MAX_PLAN_BLOCKS = 200
MAX_JUSTIFICATION_CHARS = 2_000
MINUTES_PER_DAY = 24 * 60


@dataclass(frozen=True, slots=True)
class DraftBlock:
    """Bloque de trabajo propuesto por un modelo: tarea, día y minutos locales [start, end)."""

    task_id: TaskId
    day: date
    start: int
    end: int

    def __post_init__(self) -> None:
        if not 0 <= self.start < self.end <= MINUTES_PER_DAY:
            raise ValidationError("bloque", (self.start, self.end), "duración cero o negativa")


@dataclass(frozen=True, slots=True)
class PlanDraft:
    """Propuesta de un modelo (contrato `plan_v1`). Solo trabajo: fijos y protegido los pone el
    código, y el validador decide si el plan es viable."""

    blocks: tuple[DraftBlock, ...]
    justification: str

    def __post_init__(self) -> None:
        if len(self.blocks) > MAX_PLAN_BLOCKS:
            raise ValidationError("bloques", len(self.blocks), f"máximo {MAX_PLAN_BLOCKS}")
        if len(self.justification) > MAX_JUSTIFICATION_CHARS:
            raise ValidationError(
                "justificación", len(self.justification), f"máximo {MAX_JUSTIFICATION_CHARS}"
            )
