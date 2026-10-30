"""Carga y tiempo protegido por día (F5-T02): lo que muestra la vista Semana.

Solo resume la capacidad que calcula el código y los bloques del plan; no decide nada (el
validador sigue siendo la autoridad).
"""

import pytest

from lifeos.domain.capacity import week_capacity
from lifeos.domain.horario_note import parse_horario
from lifeos.domain.load import DayLoad, day_loads
from lifeos.domain.plan import Block, BlockKind
from lifeos.domain.text import printable
from tests.planning_builders import LIMA, MON, SUN, TUE, WEEK, span, work
from tests.planning_samples import HORARIO

pytestmark = pytest.mark.unit

CAPACITY = week_capacity(parse_horario(HORARIO), WEEK)


def _by_day(loads: tuple[DayLoad, ...]) -> dict[str, DayLoad]:
    return {load.day.isoformat(): load for load in loads}


def test_one_load_per_day_with_the_code_budget() -> None:
    loads = day_loads(CAPACITY, (), LIMA)
    assert [load.day for load in loads] == list(WEEK.days())
    assert [load.budget_minutes for load in loads] == [c.budget_minutes for c in CAPACITY]
    assert all(load.planned_minutes == 0 for load in loads)


def test_planned_minutes_count_only_work_blocks_of_that_day() -> None:
    blocks = (
        work(MON, "08:00–09:30", "t-aaaaaa"),
        work(MON, "13:00–14:00", "t-bbbbbb"),
        work(TUE, "08:00–09:00", "t-aaaaaa"),
        Block(BlockKind.FIJO, "Clase", span(MON, "10:00–12:00")),
    )
    loads = _by_day(day_loads(CAPACITY, blocks, LIMA))
    assert loads[MON.isoformat()].planned_minutes == 150
    assert loads[TUE.isoformat()].planned_minutes == 60


def test_fixed_and_protected_minutes_come_from_the_schedule() -> None:
    loads = _by_day(day_loads(CAPACITY, (), LIMA))
    assert loads[MON.isoformat()].fixed_minutes == 120  # clase de redes
    assert loads[TUE.isoformat()].protected_minutes == 90  # gym
    assert loads[SUN.isoformat()].protected_minutes == 24 * 60  # descanso todo el día


def test_free_minutes_and_over_budget_flag() -> None:
    monday = CAPACITY[0]
    over = tuple(
        work(MON, text, "t-aaaaaa") for text in ("08:00–10:00", "12:00–14:00", "14:00–16:00")
    )
    load = day_loads(CAPACITY, over, LIMA)[0]
    assert load.free_minutes == monday.free_minutes
    assert load.planned_minutes == 360 > load.budget_minutes
    assert load.over_budget
    assert not day_loads(CAPACITY, (), LIMA)[0].over_budget


def test_printable_removes_controls_bidi_and_line_separators() -> None:
    assert printable("ok\x1b[31m‮ fin\tx") == "ok[31mfinx"
    assert printable("Título – normal") == "Título – normal"
