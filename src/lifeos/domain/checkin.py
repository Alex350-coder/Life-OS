"""Entrada de check-in del Coach (spec agente-coach): una línea con hora, siempre como dato.

El formato `- HH:MM — texto` nunca coincide con una tarea (`- [ ]`) ni con un título, y el texto
no puede contener campos en línea (`::`), así que un check-in no puede alterar avances,
esfuerzos ni ids aunque el texto lo intente.
"""

import unicodedata
from dataclasses import dataclass
from datetime import time

from lifeos.domain.errors import ValidationError

CHECKIN_SECTION = "check-ins"  # identificador de sección en la política (comparado sin mayúsculas)
CHECKIN_HEADING = "Check-ins"  # título que se escribe si la nota aún no tiene la sección
MAX_CHECKIN_CHARS = 500
_INVISIBLE_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})


@dataclass(frozen=True, slots=True)
class CheckInEntry:
    at: time
    text: str

    def __post_init__(self) -> None:
        cleaned = self.text.strip()
        if not cleaned:
            raise ValidationError("check-in", self.text, "no puede estar vacío")
        if len(cleaned) > MAX_CHECKIN_CHARS:
            raise ValidationError(
                "check-in", len(cleaned), f"máximo {MAX_CHECKIN_CHARS} caracteres"
            )
        if any(unicodedata.category(c) in _INVISIBLE_CATEGORIES for c in cleaned):
            raise ValidationError("check-in", "…", "contiene saltos o caracteres de control")
        if "::" in cleaned:
            raise ValidationError("check-in", "…", "no puede contener campos en línea ('::')")
        object.__setattr__(self, "text", cleaned)

    def render(self) -> str:
        return f"- {self.at:%H:%M} — {self.text}"
