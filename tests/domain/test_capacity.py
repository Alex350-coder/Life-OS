"""Capacidad disponible por día y por semana (F3-T01): zona horaria, DST, límites y margen."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from lifeos.domain.capacity import Interval, day_capacity, local_datetime, week_capacity
from lifeos.domain.errors import ValidationError
from lifeos.domain.horario_note import parse_horario
from lifeos.domain.weeks import IsoWeek
from tests.planning_samples import HORARIO, horario

pytestmark = pytest.mark.unit
LIMA = ZoneInfo("America/Lima")
MADRID = ZoneInfo("Europe/Madrid")
MONDAY = date(2026, 11, 2)


def at(day: date, hhmm: str, zone: ZoneInfo = LIMA) -> datetime:
    hours, minutes = hhmm.split(":")
    return datetime.combine(day, time(int(hours), int(minutes)), tzinfo=zone)


# --- Intervalos y hora local ------------------------------------------------------------------


def test_interval_requires_aware_and_ordered_datetimes() -> None:
    with pytest.raises(ValidationError):
        Interval(datetime(2026, 11, 2, 8), datetime(2026, 11, 2, 9))
    with pytest.raises(ValidationError):
        Interval(at(MONDAY, "09:00"), at(MONDAY, "09:00"))


def test_interval_minutes_are_measured_in_real_time() -> None:
    # 2026-03-29 en Madrid dura 23 h: de 00:00 a 24:00 locales pasan 1380 minutos.
    day = date(2026, 3, 29)
    span = Interval(local_datetime(day, 0, MADRID), local_datetime(day, 1440, MADRID))
    assert span.minutes == 23 * 60


def test_local_datetime_rejects_nonexistent_time() -> None:
    with pytest.raises(ValidationError, match="no existe"):
        local_datetime(date(2026, 3, 29), 2 * 60 + 30, MADRID)


def test_local_datetime_rejects_ambiguous_time() -> None:
    with pytest.raises(ValidationError, match="ambigua"):
        local_datetime(date(2026, 10, 25), 2 * 60 + 30, MADRID)


def test_local_datetime_end_of_day_is_next_midnight() -> None:
    assert local_datetime(MONDAY, 1440, LIMA) == at(MONDAY + timedelta(days=1), "00:00")


# --- Capacidad de un día ----------------------------------------------------------------------


def test_monday_capacity_subtracts_fixed_and_applies_limits() -> None:
    schedule = parse_horario(HORARIO)
    capacity = day_capacity(schedule, MONDAY)
    # Disponible 08–20 (720) − clase 10–12 (120) = 600 libres.
    assert capacity.free_minutes == 600
    # min(foco 240, 600·80 % = 480, 600 − 60 = 540) = 240.
    assert capacity.budget_minutes == 240
    assert [event.title for event in capacity.fixed] == ["Clase de redes"]
    assert [(i.start, i.end) for i in capacity.free] == [
        (at(MONDAY, "08:00"), at(MONDAY, "10:00")),
        (at(MONDAY, "12:00"), at(MONDAY, "20:00")),
    ]


def test_margin_limits_capacity_when_it_is_the_tightest_rule() -> None:
    schedule = parse_horario(
        horario(
            limites="[foco_max_dia:: 8h] [libre_min_dia:: 0m] [margen:: 50%]",
            disponibilidad="- lun 08:00–12:00",
        )
    )
    assert day_capacity(schedule, MONDAY).budget_minutes == 120


def test_free_time_minimum_limits_capacity_when_it_is_the_tightest_rule() -> None:
    schedule = parse_horario(
        horario(
            limites="[foco_max_dia:: 8h] [libre_min_dia:: 3h] [margen:: 0%]",
            disponibilidad="- lun 08:00–12:00",
        )
    )
    assert day_capacity(schedule, MONDAY).budget_minutes == 60


def test_free_time_minimum_larger_than_free_time_gives_zero_capacity() -> None:
    schedule = parse_horario(
        horario(
            limites="[foco_max_dia:: 8h] [libre_min_dia:: 5h] [margen:: 0%]",
            disponibilidad="- lun 08:00–12:00",
        )
    )
    assert day_capacity(schedule, MONDAY).budget_minutes == 0


def test_day_without_availability_has_zero_capacity() -> None:
    capacity = day_capacity(parse_horario(HORARIO), date(2026, 11, 8))  # domingo
    assert capacity.free == ()
    assert capacity.budget_minutes == 0
    assert [event.title for event in capacity.protected] == ["Descanso"]


def test_unavailable_day_removes_availability() -> None:
    capacity = day_capacity(parse_horario(HORARIO), date(2026, 11, 6))  # viaje
    assert capacity.free_minutes == 0
    assert capacity.budget_minutes == 0


def test_protected_time_is_not_free_time() -> None:
    capacity = day_capacity(parse_horario(HORARIO), date(2026, 11, 3))  # martes, gym 18–19:30
    assert capacity.free_minutes == 720 - 90
    assert [event.title for event in capacity.protected] == ["Gym"]


def test_fixed_commitment_outside_availability_does_not_reduce_free_time() -> None:
    schedule = parse_horario(
        horario(disponibilidad="- lun 08:00–12:00", fijos="- lun 19:00–21:00 · Curso nocturno")
    )
    capacity = day_capacity(schedule, MONDAY)
    assert capacity.free_minutes == 240
    assert [event.title for event in capacity.fixed] == ["Curso nocturno"]


def test_overlapping_availability_lines_are_merged() -> None:
    schedule = parse_horario(horario(disponibilidad="- lun 08:00–12:00\n- lun 11:00–13:00"))
    assert day_capacity(schedule, MONDAY).free_minutes == 300


def test_not_before_trims_the_past_part_of_today() -> None:
    schedule = parse_horario(HORARIO)
    capacity = day_capacity(schedule, MONDAY, not_before=at(MONDAY, "15:30"))
    assert [(i.start, i.end) for i in capacity.free] == [(at(MONDAY, "15:30"), at(MONDAY, "20:00"))]
    assert capacity.free_minutes == 270


def test_not_before_in_another_zone_is_converted() -> None:
    schedule = parse_horario(HORARIO)
    now = datetime(2026, 11, 2, 20, 30, tzinfo=UTC)  # 15:30 en Lima
    assert day_capacity(schedule, MONDAY, not_before=now).free_minutes == 270


def test_not_before_requires_a_zone() -> None:
    with pytest.raises(ValidationError):
        day_capacity(parse_horario(HORARIO), MONDAY, not_before=datetime(2026, 11, 2, 8))


def test_dst_day_capacity_counts_real_minutes() -> None:
    schedule = parse_horario(
        horario(
            zona="Europe/Madrid",
            limites="[foco_max_dia:: 24h] [libre_min_dia:: 0m] [margen:: 0%]",
            disponibilidad="- dom todo el día",
        )
    )
    capacity = day_capacity(schedule, date(2026, 3, 29))
    assert capacity.free_minutes == 23 * 60


def test_rule_on_a_nonexistent_dst_time_fails_closed() -> None:
    schedule = parse_horario(horario(zona="Europe/Madrid", disponibilidad="- dom 02:30–05:00"))
    with pytest.raises(ValidationError, match="no existe"):
        day_capacity(schedule, date(2026, 3, 29))


# --- Capacidad de la semana -------------------------------------------------------------------


def test_week_capacity_covers_seven_days() -> None:
    days = week_capacity(parse_horario(HORARIO), IsoWeek(2026, 45))
    assert [capacity.day for capacity in days] == list(IsoWeek(2026, 45).days())
    assert sum(capacity.budget_minutes for capacity in days) == 240 * 4 + 180


def test_week_capacity_past_days_have_no_free_time() -> None:
    now = at(date(2026, 11, 4), "09:00")  # miércoles
    days = week_capacity(parse_horario(HORARIO), IsoWeek(2026, 45), not_before=now)
    assert [capacity.free_minutes for capacity in days[:2]] == [0, 0]
    assert days[2].free == (
        Interval(at(date(2026, 11, 4), "09:00"), at(date(2026, 11, 4), "10:00")),
        Interval(at(date(2026, 11, 4), "12:00"), at(date(2026, 11, 4), "20:00")),
    )
    # Los compromisos fijos ya pasados siguen formando parte del día.
    assert [event.title for event in days[0].fixed] == ["Clase de redes"]
