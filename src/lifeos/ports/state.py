"""Estado de los rituales: qué ocurrencias se hicieron, omitieron, descartaron o fallaron."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from lifeos.domain.errors import LifeOSError


class RitualStatus(StrEnum):
    HECHO = "hecho"
    OMITIDO = "omitido"  # pasó su margen sin ejecutarse (p. ej., PC apagado)
    DESCARTADO = "descartado"  # caía en silencio duro o fuera de plazo
    FALLIDO = "fallido"  # se reintenta hasta el máximo


@dataclass(frozen=True, slots=True)
class RitualMark:
    status: RitualStatus
    attempts: int
    updated: datetime
    reason: str | None = None


class StateError(LifeOSError):
    """El estado no se pudo leer o guardar."""


class RitualStateStore(Protocol):
    def load(self) -> Mapping[str, RitualMark]: ...

    def save(self, marks: Mapping[str, RitualMark]) -> None: ...
