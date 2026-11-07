"""Cuándo se puede notificar (spec rituales-y-notificaciones; fase 6, criterio de silencio).

- Silencio duro: fuera de la disponibilidad del horario (sueño) y en `No molestar`. Lo que cae
  ahí se descarta.
- Silencio blando: compromisos fijos y tiempo protegido. Se aplaza a su final, salvo que eso pase
  el plazo o caiga en silencio duro.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo
from enum import StrEnum

from lifeos.domain.capacity import DayCapacity, Interval, local_datetime, subtract
from lifeos.domain.schedule import MINUTES_PER_DAY, TimeRange

UNAVAILABLE = "fuera de la disponibilidad"
DO_NOT_DISTURB = "no molestar"
LATE = "fuera de plazo"


class DeliveryKind(StrEnum):
    ENVIAR = "enviar"
    APLAZAR = "aplazar"
    DESCARTAR = "descartar"


@dataclass(frozen=True, slots=True)
class QuietWindow:
    interval: Interval
    hard: bool
    reason: str  # motivo del silencio o título del bloque protegido


@dataclass(frozen=True, slots=True)
class Delivery:
    kind: DeliveryKind
    at: datetime | None  # instante de envío; None si se descarta
    reason: str | None = None


def _local(day: date, minute: int, zone: tzinfo) -> datetime:
    return local_datetime(day, minute, zone)


def quiet_windows(
    capacity: DayCapacity, do_not_disturb: Sequence[TimeRange], day: date, zone: tzinfo
) -> tuple[QuietWindow, ...]:
    whole = Interval(_local(day, 0, zone), _local(day, MINUTES_PER_DAY, zone))
    # Lo que se aplace más allá del día cae en el sueño del día siguiente: silencio duro.
    after = Interval(whole.end, whole.end + timedelta(days=1))
    hard = [
        QuietWindow(interval, True, UNAVAILABLE)
        for interval in (*subtract((whole,), capacity.available, zone), after)
    ]
    hard += [
        QuietWindow(
            Interval(_local(day, r.start, zone), _local(day, r.end, zone)), True, DO_NOT_DISTURB
        )
        for r in do_not_disturb
    ]
    soft = [
        QuietWindow(event.interval, False, event.title)
        for event in (*capacity.fixed, *capacity.protected)
    ]
    return (*hard, *soft)


def _containing(windows: Sequence[QuietWindow], moment: datetime) -> list[QuietWindow]:
    return [w for w in windows if w.interval.start <= moment < w.interval.end]


def plan_delivery(at: datetime, deadline: datetime, windows: Sequence[QuietWindow]) -> Delivery:
    moment = at
    for _ in range(len(windows) + 1):  # cada aplazamiento sale de al menos una ventana
        inside = _containing(windows, moment)
        if not inside:
            break
        hard = next((w for w in inside if w.hard), None)
        if hard is not None:
            return Delivery(DeliveryKind.DESCARTAR, None, hard.reason)
        moment = max(w.interval.end for w in inside)
    if moment > deadline:
        return Delivery(DeliveryKind.DESCARTAR, None, LATE)
    kind = DeliveryKind.ENVIAR if moment == at else DeliveryKind.APLAZAR
    return Delivery(kind, moment)
