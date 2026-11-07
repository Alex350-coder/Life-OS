"""Cuándo se puede notificar (spec rituales-y-notificaciones; fase 6, criterio de silencio).

- Silencio duro: fuera de la disponibilidad del horario (sueño) y en `No molestar`. Lo que cae
  en `No molestar` se descarta; fuera de la disponibilidad, se espera a que empiece si llega
  dentro del plazo.
- Silencio blando: compromisos fijos y tiempo protegido. Se aplaza a su final, salvo que eso pase
  el plazo o caiga en silencio duro.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo
from enum import StrEnum

from lifeos.domain.capacity import DayCapacity, Interval, local_datetime, subtract
from lifeos.domain.errors import ValidationError
from lifeos.domain.schedule import TimeRange

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


def _day_bounds(day: date, zone: tzinfo) -> Interval:
    # Sin validar la hora local: en zonas con cambio de horario a medianoche, 00:00 puede no
    # existir, y el día sigue teniendo un principio y un final reales.
    start = datetime.combine(day, time(), tzinfo=zone)
    end = datetime.combine(day + timedelta(days=1), time(), tzinfo=zone)
    return Interval(start, end)


def _unchecked(day: date, minute: int, zone: tzinfo) -> datetime:
    return datetime.combine(day, time(), tzinfo=zone) + timedelta(minutes=minute)


def _dnd(day: date, ranges: Sequence[TimeRange], zone: tzinfo) -> list[QuietWindow]:
    windows = []
    for r in ranges:
        try:
            interval = Interval(
                local_datetime(day, r.start, zone), local_datetime(day, r.end, zone)
            )
        except ValidationError:
            # El rango toca una hora que no existe o es ambigua ese día (cambio de horario): se
            # usa la hora de pared sin validar, para que el silencio se mantenga (falla cerrado).
            interval = Interval(_unchecked(day, r.start, zone), _unchecked(day, r.end, zone))
        windows.append(QuietWindow(interval, True, DO_NOT_DISTURB))
    return windows


def quiet_windows(
    capacity: DayCapacity, do_not_disturb: Sequence[TimeRange], day: date, zone: tzinfo
) -> tuple[QuietWindow, ...]:
    whole = _day_bounds(day, zone)
    # Lo que se aplace más allá del día cae en el sueño del día siguiente: silencio duro.
    after = Interval(whole.end, whole.end + timedelta(days=1))
    hard = [
        QuietWindow(interval, True, UNAVAILABLE)
        for interval in (*subtract((whole,), capacity.available, zone), after)
    ]
    hard += _dnd(day, do_not_disturb, zone)
    soft = [
        QuietWindow(event.interval, False, event.title)
        for event in (*capacity.fixed, *capacity.protected)
    ]
    return (*hard, *soft)


def _containing(windows: Sequence[QuietWindow], moment: datetime) -> list[QuietWindow]:
    return [w for w in windows if w.interval.start <= moment < w.interval.end]


def is_quiet(moment: datetime, windows: Sequence[QuietWindow]) -> bool:
    """El propietario está en silencio (duro o blando) en este instante."""
    return bool(_containing(windows, moment))


def _blocks(window: QuietWindow, deadline: datetime) -> bool:
    """Silencio que descarta. Fuera de la disponibilidad se espera a que empiece si eso llega a
    tiempo (p. ej., el aviso de un bloque que empieza justo al despertar); no molestar descarta."""
    if not window.hard:
        return False
    return not (window.reason == UNAVAILABLE and window.interval.end <= deadline)


def plan_delivery(at: datetime, deadline: datetime, windows: Sequence[QuietWindow]) -> Delivery:
    moment = at
    for _ in range(len(windows) + 1):  # cada aplazamiento sale de al menos una ventana
        inside = _containing(windows, moment)
        if not inside:
            break
        hard = next((w for w in inside if _blocks(w, deadline)), None)
        if hard is not None:
            return Delivery(DeliveryKind.DESCARTAR, None, hard.reason)
        moment = max(w.interval.end for w in inside)
    if moment > deadline:
        return Delivery(DeliveryKind.DESCARTAR, None, LATE)
    kind = DeliveryKind.ENVIAR if moment == at else DeliveryKind.APLAZAR
    return Delivery(kind, moment)
