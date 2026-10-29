"""Replanificación de una semana aprobada (F4-T04, ADR-0011).

La revisión parte de un corte (el minuto siguiente a "ahora"):
- el trabajo que ya empezó antes del corte es **historia**: se conserva tal cual, no se vuelve a
  validar y consume el presupuesto de su día;
- lo nuevo se planifica desde el suelo (el corte, o el fin de un bloque en curso);
- las propuestas del Coach ajustan la capacidad: "reducir carga hoy" deja el día sin trabajo
  nuevo y un bloque perdido no vuelve a ocupar su mismo hueco.

El validador determinista sigue siendo el que decide si la revisión es viable.
"""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import replace
from datetime import date, datetime

from lifeos.domain.adjustments import Adjustment, AdjustmentKind
from lifeos.domain.capacity import DayCapacity, Interval, budget, local_datetime, subtract
from lifeos.domain.plan import Block, BlockKind, Conflict, ConflictCode, PlanningContext
from lifeos.domain.schedule import Schedule


def split_history(blocks: Iterable[Block], cutoff: datetime) -> tuple[Block, ...]:
    """Bloques de trabajo que empezaron antes del corte, en orden."""
    cut = cutoff.timestamp()
    chosen = [
        b for b in blocks if b.kind is BlockKind.TRABAJO and b.interval.start.timestamp() < cut
    ]
    return tuple(sorted(chosen, key=lambda b: b.interval.start.timestamp()))


def planning_floor(history: Iterable[Block], cutoff: datetime) -> datetime:
    """Primer instante planificable: el corte, o el fin del bloque que sigue en curso."""
    floor = cutoff
    for block in history:
        if block.interval.end.timestamp() > floor.timestamp():
            floor = block.interval.end
    return floor


def _missed_intervals(
    adjustments: Iterable[Adjustment], day: date, context: PlanningContext
) -> list[Interval]:
    spans = []
    for adjustment in adjustments:
        if adjustment.kind is AdjustmentKind.BLOQUE_PERDIDO and adjustment.day == day:
            span = adjustment.span
            if span is not None:
                spans.append(
                    Interval(
                        local_datetime(day, span.start, context.zone),
                        local_datetime(day, span.end, context.zone),
                    )
                )
    return spans


def _adjust_day(
    capacity: DayCapacity,
    schedule: Schedule,
    context: PlanningContext,
    *,
    used: int,
    adjustments: Sequence[Adjustment],
) -> DayCapacity:
    free = subtract(
        capacity.free, _missed_intervals(adjustments, capacity.day, context), context.zone
    )
    free_minutes = sum(interval.minutes for interval in free)
    allowed = min(capacity.budget_minutes, budget(schedule, free_minutes))
    reduce = any(
        a.kind is AdjustmentKind.REDUCIR_HOY and a.day == capacity.day for a in adjustments
    )
    left = 0 if reduce else max(0, min(allowed, schedule.limits.focus_max - used))
    return replace(capacity, free=free, budget_minutes=left)


def adjust_context(
    context: PlanningContext,
    schedule: Schedule,
    *,
    history: Sequence[Block],
    adjustments: Sequence[Adjustment],
) -> PlanningContext:
    """Contexto nuevo con la historia descontada y las propuestas del Coach aplicadas."""
    used: dict[date, int] = defaultdict(int)
    for block in history:
        used[context.local_day(block.interval.start)] += block.interval.minutes
    days = tuple(
        _adjust_day(capacity, schedule, context, used=used[capacity.day], adjustments=adjustments)
        for capacity in context.days
    )
    return replace(context, days=days)


def _signature(block: Block) -> tuple[object, ...]:
    return (
        block.kind,
        block.title,
        block.task_id,
        block.interval.start.timestamp(),
        block.interval.end.timestamp(),
    )


def check_history(
    history: Sequence[Block], expected: Sequence[Block], new_work: Sequence[Block]
) -> tuple[Conflict, ...]:
    """La historia de la revisión es la del plan revisado y no se solapa con lo nuevo."""
    conflicts = []
    if sorted(map(_signature, history), key=str) != sorted(map(_signature, expected), key=str):
        conflicts.append(
            Conflict(
                ConflictCode.HISTORIAL_ALTERADO,
                "los bloques anteriores al corte no coinciden con el plan revisado: "
                "lo que ya ocurrió no se edita; vuelve a replanificar",
            )
        )
    for past in history:
        for block in new_work:
            if past.interval.overlaps(block.interval):
                conflicts.append(
                    Conflict(
                        ConflictCode.SOLAPAMIENTO,
                        f"'{block.title}' se solapa con el bloque ya ocurrido '{past.title}'",
                    )
                )
    return tuple(conflicts)
