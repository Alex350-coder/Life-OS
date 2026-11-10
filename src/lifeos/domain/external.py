"""Eventos externos que ocupan tiempo (F7-T02, ADR-0017).

Del calendario externo solo se conoce el intervalo ocupado (scope `calendar.freebusy`): nunca el
título ni la descripción. Cada intervalo guarda su fuente y un nivel de confianza; el código lo
trata como un compromiso fijo al calcular la capacidad, y el validador rechaza el trabajo que
lo pise.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from zoneinfo import ZoneInfo

from lifeos.domain.capacity import Interval
from lifeos.domain.errors import ValidationError

# Un "ocupado" de medio día o más suele ser un viaje o un evento de todo el día marcado como
# ocupado: se respeta, pero con confianza media para que el propietario lo confirme.
LONG_BUSY_MINUTES = 12 * 60
MAX_SOURCE_CHARS = 80
_SOURCE_RE = re.compile(r"[a-z0-9][a-z0-9:._@-]*", re.ASCII)


class Confidence(StrEnum):
    ALTA = "alta"  # intervalo corto marcado como ocupado por el calendario
    MEDIA = "media"  # intervalo largo: puede ser un aviso de todo el día; confirmar


@dataclass(frozen=True, slots=True)
class ExternalBusy:
    """Intervalo ocupado de un calendario externo, sin contenido del evento."""

    interval: Interval
    source: str  # p. ej. "google:primary"; etiqueta inerte, nunca texto del evento
    confidence: Confidence

    def __post_init__(self) -> None:
        if len(self.source) > MAX_SOURCE_CHARS or not _SOURCE_RE.fullmatch(self.source):
            raise ValidationError("fuente", self.source[:MAX_SOURCE_CHARS], "etiqueta no válida")


def confidence_of(interval: Interval) -> Confidence:
    return Confidence.MEDIA if interval.minutes >= LONG_BUSY_MINUTES else Confidence.ALTA


def busy_from(interval: Interval, source: str) -> ExternalBusy:
    return ExternalBusy(interval, source, confidence_of(interval))


def busy_on(busy: Iterable[ExternalBusy], day: date, zone: ZoneInfo) -> tuple[ExternalBusy, ...]:
    """Intervalos que tocan `day` (en la zona del horario), en orden."""
    found = [
        item
        for item in busy
        if item.interval.start.astimezone(zone).date()
        <= day
        <= item.interval.end.astimezone(zone).date()
    ]
    return tuple(sorted(found, key=lambda item: item.interval.start.timestamp()))


def first_overlap(interval: Interval, busy: Iterable[ExternalBusy]) -> ExternalBusy | None:
    return next((item for item in busy if item.interval.overlaps(interval)), None)
