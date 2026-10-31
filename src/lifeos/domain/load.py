"""Carga y tiempo protegido por día (F5-T02): resumen de la capacidad y de los bloques del plan.

Solo informa. La autoridad sobre si un plan cabe sigue siendo el validador (`plan_validator`).
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from zoneinfo import ZoneInfo

from lifeos.domain.capacity import DayCapacity, Event
from lifeos.domain.plan import Block, BlockKind


@dataclass(frozen=True, slots=True)
class DayLoad:
    day: date
    budget_minutes: int  # foco máximo del día según límites, margen y tiempo libre mínimo
    planned_minutes: int  # trabajo planificado ese día
    fixed_minutes: int
    protected_minutes: int
    free_minutes: int  # disponible sin compromisos fijos ni tiempo protegido

    @property
    def over_budget(self) -> bool:
        return self.planned_minutes > self.budget_minutes


def _minutes(events: Iterable[Event]) -> int:
    return sum(event.interval.minutes for event in events)


def day_loads(
    days: Sequence[DayCapacity], blocks: Iterable[Block], zone: ZoneInfo
) -> tuple[DayLoad, ...]:
    planned: dict[date, int] = {}
    for block in blocks:
        if block.kind is BlockKind.TRABAJO:
            day = block.interval.start.astimezone(zone).date()
            planned[day] = planned.get(day, 0) + block.interval.minutes
    return tuple(
        DayLoad(
            day=capacity.day,
            budget_minutes=capacity.budget_minutes,
            planned_minutes=planned.get(capacity.day, 0),
            fixed_minutes=_minutes(capacity.fixed),
            protected_minutes=_minutes(capacity.protected),
            free_minutes=capacity.free_minutes,
        )
        for capacity in days
    )
