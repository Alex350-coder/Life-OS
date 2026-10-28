"""Propuestas que un modelo puede devolver ya validadas (contratos de salida de la Fase 2)."""

from dataclasses import dataclass

from lifeos.domain.errors import ValidationError

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
