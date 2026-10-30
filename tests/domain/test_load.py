"""Carga y tiempo protegido por día (F5-T02): lo que muestra la vista Semana.

Solo resume la capacidad que calcula el código y los bloques del plan; no decide nada (el
validador sigue siendo la autoridad).
"""

from zoneinfo import ZoneInfo

import pytest

from lifeos.domain.capacity import week_capacity
from lifeos.domain.horario_note import parse_horario
from lifeos.domain.load import DayLoad, day_loads
from lifeos.domain.plan import Block, BlockKind
from lifeos.domain.text import printable
from tests.planning_builders import FRI, LIMA, MON, SUN, TUE, WEEK, span, work
from tests.planning_samples import HORARIO

pytestmark = pytest.mark.unit

CAPACITY = week_capacity(parse_horario(HORARIO), WEEK)
NBSP = chr(0xA0)


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


def test_free_minutes_come_from_capacity() -> None:
    load = day_loads(CAPACITY, (), LIMA)[0]
    assert load.free_minutes == CAPACITY[0].free_minutes
    assert not load.over_budget


def test_more_planned_than_budget_is_over_budget() -> None:
    over = tuple(
        work(MON, text, "t-aaaaaa") for text in ("08:00–10:00", "12:00–14:00", "14:00–16:00")
    )
    load = day_loads(CAPACITY, over, LIMA)[0]
    assert load.planned_minutes == 360 > load.budget_minutes
    assert load.over_budget


def test_exactly_the_budget_is_not_over_budget() -> None:
    blocks = (work(TUE, "08:00–10:00", "t-aaaaaa"), work(TUE, "12:00–14:00", "t-aaaaaa"))
    load = day_loads(CAPACITY, blocks, LIMA)[1]
    assert load.planned_minutes == load.budget_minutes == 240  # foco_max_dia de prueba
    assert not load.over_budget


def test_any_work_on_a_day_without_budget_is_over_budget() -> None:
    friday = day_loads(CAPACITY, (work(FRI, "08:00–09:00", "t-aaaaaa"),), LIMA)[4]
    assert friday.budget_minutes == 0  # viaje todo el día
    assert friday.over_budget


def test_blocks_are_assigned_to_the_local_day_of_the_given_zone() -> None:
    late = (work(MON, "19:30–20:00", "t-aaaaaa"),)  # 00:30 del martes en UTC
    assert day_loads(CAPACITY, late, LIMA)[0].planned_minutes == 30
    in_utc = day_loads(CAPACITY, late, ZoneInfo("UTC"))
    assert (in_utc[0].planned_minutes, in_utc[1].planned_minutes) == (0, 30)


def test_blocks_outside_the_week_are_ignored() -> None:
    outside = (work(MON.replace(day=1), "08:00–09:00", "t-aaaaaa"),)
    assert sum(load.planned_minutes for load in day_loads(CAPACITY, outside, LIMA)) == 0


@pytest.mark.parametrize(
    "hidden",
    [chr(c) for c in (0x1B, 0x09, 0x202E, 0x200B, 0x2028, 0x2029, 0xD800, 0xE000, 0x0378)],
)
def test_printable_removes_each_hidden_category(hidden: str) -> None:
    assert printable(f"a{hidden}b") == "ab"


def test_printable_keeps_visible_text() -> None:
    assert printable(f"📅 Título – normal{NBSP}[x]") == f"📅 Título – normal{NBSP}[x]"


def test_cli_uses_the_shared_printable() -> None:
    from lifeos.cli import main

    assert main.printable is printable  # type: ignore[attr-defined]
