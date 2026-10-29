"""Propuestas de ajuste del Coach al Jefe (F4-T04, agente-coach.md, ADR-0011).

El Coach no tiene permisos sobre el plan: sus propuestas son líneas de check-in con un marcador
fijo, `- HH:MM — ajuste: bloque perdido · HH:MM–HH:MM · t-xxxxxx` o
`- HH:MM — ajuste: reducir carga hoy`. No llevan campos en línea, así que la regla de anexado
las acepta sin cambios, y el Jefe (que lee `Diario/`) las analiza con una expresión regular
exacta. Las entradas que el Coach escribe con texto del usuario empiezan por `estado:`, de modo
que ese texto nunca puede pasar por una propuesta.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time
from enum import StrEnum
from zoneinfo import ZoneInfo

from lifeos.domain.capacity import local_datetime
from lifeos.domain.checkin import CHECKIN_SECTION, CheckInEntry
from lifeos.domain.errors import ValidationError
from lifeos.domain.ids import TaskId
from lifeos.domain.schedule import TimeRange
from lifeos.domain.sections import section_lines

MARKER = "ajuste:"
MINUTES_PER_HOUR = 60


class AdjustmentKind(StrEnum):
    BLOQUE_PERDIDO = "bloque perdido"
    REDUCIR_HOY = "reducir carga hoy"


_ENTRY_RE = re.compile(
    rf"- ([01][0-9]|2[0-3]):([0-5][0-9]) — {MARKER} "
    rf"(?P<kind>{AdjustmentKind.BLOQUE_PERDIDO.value}|{AdjustmentKind.REDUCIR_HOY.value})"
    r"(?: · (?P<span>[0-9]{2}:[0-9]{2}–[0-9]{2}:[0-9]{2}) · (?P<task>t-[23456789a-z]{6}))?"
)


@dataclass(frozen=True, slots=True)
class Adjustment:
    day: date
    at: time
    kind: AdjustmentKind
    span: TimeRange | None = None
    task_id: TaskId | None = None

    def __post_init__(self) -> None:
        missed = self.kind is AdjustmentKind.BLOQUE_PERDIDO
        has_block = self.span is not None and self.task_id is not None
        if missed and not has_block:
            raise ValidationError("ajuste", self.kind, "un bloque perdido necesita horario y tarea")
        if not missed and (self.span is not None or self.task_id is not None):
            raise ValidationError("ajuste", self.kind, "no lleva horario ni tarea")

    def moment(self, zone: ZoneInfo) -> datetime:
        return local_datetime(self.day, self.at.hour * MINUTES_PER_HOUR + self.at.minute, zone)


def adjustment_entry(adjustment: Adjustment) -> CheckInEntry:
    parts = [f"{MARKER} {adjustment.kind.value}"]
    if adjustment.span is not None and adjustment.task_id is not None:
        parts += [adjustment.span.render(), str(adjustment.task_id)]
    return CheckInEntry(adjustment.at, " · ".join(parts))


def _parse(line: str, day: date) -> Adjustment | None:
    match = _ENTRY_RE.fullmatch(line.rstrip())
    if match is None:
        return None
    kind = AdjustmentKind(match["kind"])
    has_block = match["span"] is not None
    if (kind is AdjustmentKind.BLOQUE_PERDIDO) != has_block:
        return None
    try:
        return Adjustment(
            day,
            time(int(match[1]), int(match[2])),
            kind,
            TimeRange.parse(match["span"]) if has_block else None,
            TaskId(match["task"]) if has_block else None,
        )
    except ValidationError:
        return None  # un horario imposible no es una propuesta


def _key(adjustment: Adjustment) -> tuple[object, ...]:
    # La hora forma parte de la clave: la misma propuesta repetida tras una revisión es nueva.
    return adjustment.day, adjustment.at, adjustment.kind, adjustment.span, adjustment.task_id


def parse_adjustments(text: str, day: date) -> tuple[Adjustment, ...]:
    """Propuestas de la sección `## Check-ins` de `day`, sin repetidas exactas.

    Una sección `Check-ins` duplicada es ambigua y se rechaza (`ValidationError`).
    """
    lines = section_lines(text, CHECKIN_SECTION) or ()
    found: dict[tuple[object, ...], Adjustment] = {}
    for line in lines[1:]:
        adjustment = _parse(line, day)
        if adjustment is not None:
            found.setdefault(_key(adjustment), adjustment)
    return tuple(found.values())


def pending(
    adjustments: Iterable[Adjustment],
    *,
    zone: ZoneInfo,
    after: datetime | None,
    until: datetime,
) -> tuple[Adjustment, ...]:
    """Propuestas registradas en (`after`, `until`]: las que aún no atendió ninguna revisión."""
    chosen = []
    for adjustment in adjustments:
        moment = adjustment.moment(zone)
        if (after is None or moment > after) and moment <= until:
            chosen.append(adjustment)
    return tuple(chosen)
