"""Capacidad disponible por día (F3-T01, planificador.md).

libre = disponibilidad − no disponible − compromisos fijos − tiempo protegido (desde `not_before`)
presupuesto de trabajo = mín(foco_max_dia, libre · (100 − margen) %, libre − libre_min_dia)

Las reglas están en hora local; las duraciones se miden en tiempo real (UTC), así que un día con
cambio de horario dura 23 o 25 h. Una hora inexistente o ambigua por el cambio de horario se
rechaza (fallo cerrado) en lugar de adivinar.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo

from lifeos.domain.errors import ValidationError
from lifeos.domain.schedule import MAX_PERCENT, RuleKind, Schedule, ScheduleRule
from lifeos.domain.weeks import IsoWeek

SECONDS_PER_MINUTE = 60


def _require_aware(field: str, moment: datetime) -> None:
    if moment.utcoffset() is None:
        raise ValidationError(field, moment, "requiere zona horaria")


def _utc(moment: datetime) -> datetime:
    return moment.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class Interval:
    """Intervalo real semiabierto [start, end) con zona horaria."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        _require_aware("inicio", self.start)
        _require_aware("fin", self.end)
        if _utc(self.start) >= _utc(self.end):
            raise ValidationError("intervalo", self.start, "duración cero o negativa")

    @property
    def minutes(self) -> int:
        return int((_utc(self.end) - _utc(self.start)).total_seconds()) // SECONDS_PER_MINUTE

    def overlaps(self, other: "Interval") -> bool:
        return _utc(self.start) < _utc(other.end) and _utc(other.start) < _utc(self.end)

    def contains(self, other: "Interval") -> bool:
        return _utc(self.start) <= _utc(other.start) and _utc(other.end) <= _utc(self.end)


@dataclass(frozen=True, slots=True)
class Event:
    """Compromiso fijo o tiempo protegido de un día concreto."""

    kind: RuleKind
    title: str
    interval: Interval


@dataclass(frozen=True, slots=True)
class DayCapacity:
    day: date
    available: tuple[Interval, ...]
    fixed: tuple[Event, ...]
    protected: tuple[Event, ...]
    free: tuple[Interval, ...]
    budget_minutes: int

    @property
    def free_minutes(self) -> int:
        return sum(interval.minutes for interval in self.free)


def local_datetime(day: date, minute: int, zone: tzinfo) -> datetime:
    """Instante real de `day` + `minute` en hora local; 1440 es la medianoche siguiente."""
    naive = datetime.combine(day, time()) + timedelta(minutes=minute)
    local = naive.replace(tzinfo=zone)
    label = f"{day.isoformat()} {minute // 60:02d}:{minute % 60:02d}"
    if _utc(local).astimezone(zone).replace(tzinfo=None) != naive:
        raise ValidationError("hora", label, "no existe en esa fecha por el cambio de horario")
    if local.replace(fold=1).utcoffset() != local.utcoffset():
        raise ValidationError("hora", label, "es ambigua en esa fecha por el cambio de horario")
    return local


def _interval(rule: ScheduleRule, day: date, zone: tzinfo) -> Interval:
    return Interval(
        local_datetime(day, rule.range.start, zone), local_datetime(day, rule.range.end, zone)
    )


def _merge(intervals: Iterable[Interval], zone: tzinfo) -> tuple[Interval, ...]:
    merged: list[tuple[datetime, datetime]] = []
    for start, end in sorted((_utc(i.start), _utc(i.end)) for i in intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return tuple(Interval(start.astimezone(zone), end.astimezone(zone)) for start, end in merged)


def subtract(
    base: Iterable[Interval], removed: Iterable[Interval], zone: tzinfo
) -> tuple[Interval, ...]:
    """Partes de `base` que no se solapan con ningún intervalo de `removed`."""
    pieces = [(_utc(i.start), _utc(i.end)) for i in _merge(base, zone)]
    for cut in _merge(removed, zone):
        cut_start, cut_end = _utc(cut.start), _utc(cut.end)
        next_pieces = []
        for start, end in pieces:
            if cut_end <= start or end <= cut_start:
                next_pieces.append((start, end))
                continue
            if start < cut_start:
                next_pieces.append((start, cut_start))
            if cut_end < end:
                next_pieces.append((cut_end, end))
        pieces = next_pieces
    return tuple(Interval(start.astimezone(zone), end.astimezone(zone)) for start, end in pieces)


def _after(intervals: tuple[Interval, ...], moment: datetime, zone: tzinfo) -> tuple[Interval, ...]:
    cut = _utc(moment)
    kept = []
    for interval in intervals:
        if _utc(interval.end) <= cut:
            continue
        start = max(_utc(interval.start), cut).astimezone(zone)
        kept.append(Interval(start, interval.end))
    return tuple(kept)


def _events(schedule: Schedule, day: date, kind: RuleKind) -> tuple[Event, ...]:
    return tuple(
        Event(kind, rule.title, _interval(rule, day, schedule.zone))
        for rule in schedule.rules_for(day, kind)
    )


def budget(schedule: Schedule, free_minutes: int) -> int:
    limits = schedule.limits
    with_margin = free_minutes * (MAX_PERCENT - limits.margin_percent) // MAX_PERCENT
    return max(0, min(limits.focus_max, with_margin, free_minutes - limits.free_min))


def day_capacity(
    schedule: Schedule, day: date, *, not_before: datetime | None = None
) -> DayCapacity:
    zone = schedule.zone
    if not_before is not None:
        _require_aware("desde", not_before)
    available = subtract(
        (_interval(rule, day, zone) for rule in schedule.rules_for(day, RuleKind.DISPONIBLE)),
        (_interval(rule, day, zone) for rule in schedule.rules_for(day, RuleKind.NO_DISPONIBLE)),
        zone,
    )
    fixed = _events(schedule, day, RuleKind.FIJO)
    protected = _events(schedule, day, RuleKind.PROTEGIDO)
    free = subtract(available, (event.interval for event in (*fixed, *protected)), zone)
    if not_before is not None:
        free = _after(free, not_before, zone)
    free_minutes = sum(interval.minutes for interval in free)
    return DayCapacity(
        day=day,
        available=available,
        fixed=fixed,
        protected=protected,
        free=free,
        budget_minutes=budget(schedule, free_minutes),
    )


def week_capacity(
    schedule: Schedule, week: IsoWeek, *, not_before: datetime | None = None
) -> tuple[DayCapacity, ...]:
    return tuple(day_capacity(schedule, day, not_before=not_before) for day in week.days())
