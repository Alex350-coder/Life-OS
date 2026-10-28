"""Horario, límites y semanas ISO (F3-T01, spec horario-y-limites)."""

from datetime import date
from zoneinfo import ZoneInfo

import pytest

from lifeos.domain.errors import ValidationError
from lifeos.domain.schedule import (
    MINUTES_PER_DAY,
    Limits,
    RuleKind,
    Schedule,
    ScheduleRule,
    TimeRange,
    Vigencia,
    Weekday,
    parse_days,
)
from lifeos.domain.weeks import IsoWeek

pytestmark = pytest.mark.unit
LIMA = ZoneInfo("America/Lima")
LIMITS = Limits(focus_max=240, free_min=60, margin_percent=20)


def rule(kind: RuleKind, days: str, span: str, title: str = "x") -> ScheduleRule:
    return ScheduleRule(kind=kind, range=TimeRange.parse(span), title=title, days=parse_days(days))


# --- Días -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("lun", {Weekday.LUN}),
        ("mié", {Weekday.MIE}),
        ("mie", {Weekday.MIE}),
        ("SÁB", {Weekday.SAB}),
        ("sab", {Weekday.SAB}),
        ("lun-mié", {Weekday.LUN, Weekday.MAR, Weekday.MIE}),
        ("lun,jue", {Weekday.LUN, Weekday.JUE}),
        ("lun-mar,dom", {Weekday.LUN, Weekday.MAR, Weekday.DOM}),
    ],
)
def test_parse_days(text: str, expected: set[Weekday]) -> None:
    assert parse_days(text) == frozenset(expected)


@pytest.mark.parametrize("text", ["", "lunes", "vie-lun", "lun,", "lun--mar", "x"])
def test_parse_days_rejects_invalid_text(text: str) -> None:
    with pytest.raises(ValidationError):
        parse_days(text)


def test_weekday_of_a_date() -> None:
    assert Weekday.of(date(2026, 11, 2)) is Weekday.LUN
    assert Weekday.of(date(2026, 11, 8)) is Weekday.DOM


# --- Rangos horarios --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "start", "end"),
    [
        ("08:00–20:00", 480, 1200),
        ("08:00-20:00", 480, 1200),
        ("08:00 – 09:30", 480, 570),
        ("22:00–24:00", 1320, MINUTES_PER_DAY),
        ("todo el día", 0, MINUTES_PER_DAY),
    ],
)
def test_time_range_parse(text: str, start: int, end: int) -> None:
    span = TimeRange.parse(text)
    assert (span.start, span.end) == (start, end)
    assert span.minutes == end - start


@pytest.mark.parametrize(
    "text",
    ["10:00–09:00", "10:00–10:00", "25:00–26:00", "08:60–09:00", "8:00–9:00", "24:00–24:30", "x"],
)
def test_time_range_rejects_zero_negative_or_invalid(text: str) -> None:
    with pytest.raises(ValidationError):
        TimeRange.parse(text)


def test_time_range_render_round_trip() -> None:
    assert TimeRange.parse("08:00–09:30").render() == "08:00–09:30"
    assert TimeRange(0, MINUTES_PER_DAY).render() == "00:00–24:00"


def test_time_range_overlap_is_half_open() -> None:
    first = TimeRange.parse("08:00–10:00")
    assert first.overlaps(TimeRange.parse("09:59–11:00"))
    assert not first.overlaps(TimeRange.parse("10:00–11:00"))


# --- Límites ----------------------------------------------------------------------------------


def test_limits_defaults_for_blocks() -> None:
    assert (LIMITS.block_min, LIMITS.block_max) == (30, 120)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"focus_max": 0},
        {"focus_max": -30},
        {"free_min": -1},
        {"margin_percent": 100},
        {"margin_percent": -5},
        {"block_min": 0},
        {"block_min": 90, "block_max": 60},
        {"focus_max": MINUTES_PER_DAY + 1},
    ],
)
def test_limits_reject_incoherent_values(kwargs: dict[str, int]) -> None:
    values = {"focus_max": 240, "free_min": 60, "margin_percent": 20, **kwargs}
    with pytest.raises(ValidationError):
        Limits(**values)


# --- Reglas y horario -------------------------------------------------------------------------


def test_rule_requires_days_or_a_date_but_not_both() -> None:
    span = TimeRange.parse("08:00–09:00")
    with pytest.raises(ValidationError):
        ScheduleRule(kind=RuleKind.FIJO, range=span, title="x")
    with pytest.raises(ValidationError):
        ScheduleRule(
            kind=RuleKind.FIJO,
            range=span,
            title="x",
            days=frozenset({Weekday.LUN}),
            on=date(2026, 11, 2),
        )


def test_rule_applies_to_weekday_or_exact_date() -> None:
    weekly = rule(RuleKind.FIJO, "lun", "08:00–09:00")
    dated = ScheduleRule(
        kind=RuleKind.NO_DISPONIBLE,
        range=TimeRange.parse("todo el día"),
        title="Viaje",
        on=date(2026, 11, 4),
    )
    assert weekly.applies_to(date(2026, 11, 2))
    assert not weekly.applies_to(date(2026, 11, 3))
    assert dated.applies_to(date(2026, 11, 4))
    assert not dated.applies_to(date(2026, 11, 11))


def test_titled_rules_need_a_title() -> None:
    with pytest.raises(ValidationError):
        rule(RuleKind.FIJO, "lun", "08:00–09:00", title="  ")


def test_schedule_rejects_overlapping_fixed_or_protected_rules() -> None:
    with pytest.raises(ValidationError, match="se solapan"):
        Schedule(
            zone=LIMA,
            limits=LIMITS,
            rules=(
                rule(RuleKind.FIJO, "lun,mié", "10:00–12:00", "Clase"),
                rule(RuleKind.PROTEGIDO, "mié", "11:00–12:30", "Gym"),
            ),
        )


def test_schedule_ignores_non_active_rules_when_checking_overlaps() -> None:
    old = ScheduleRule(
        kind=RuleKind.FIJO,
        range=TimeRange.parse("10:00–12:00"),
        title="Antigua",
        days=parse_days("lun"),
        vigencia=Vigencia.SUSTITUIDA,
    )
    schedule = Schedule(
        zone=LIMA, limits=LIMITS, rules=(old, rule(RuleKind.FIJO, "lun", "11:00–12:00", "Nueva"))
    )
    assert [r.title for r in schedule.rules_for(date(2026, 11, 2), RuleKind.FIJO)] == ["Nueva"]


def test_schedule_overlap_between_weekly_and_dated_rule() -> None:
    with pytest.raises(ValidationError):
        Schedule(
            zone=LIMA,
            limits=LIMITS,
            rules=(
                rule(RuleKind.FIJO, "lun", "10:00–12:00", "Clase"),
                ScheduleRule(
                    kind=RuleKind.FIJO,
                    range=TimeRange.parse("11:00–13:00"),
                    title="Examen",
                    on=date(2026, 11, 2),
                ),
            ),
        )


# --- Semanas ISO ------------------------------------------------------------------------------


def test_iso_week_parse_render_and_days() -> None:
    week = IsoWeek.parse("2026-W45")
    assert week.render() == "2026-W45"
    assert week.days()[0] == date(2026, 11, 2)
    assert week.days()[-1] == date(2026, 11, 8)
    assert week.next() == IsoWeek(2026, 46)


def test_iso_week_53_and_year_change() -> None:
    week = IsoWeek.parse("2026-W53")
    assert week.days()[-1] == date(2027, 1, 3)
    assert week.next() == IsoWeek(2027, 1)
    assert IsoWeek.containing(date(2027, 1, 1)) == week


@pytest.mark.parametrize("text", ["2025-W53", "2026-W00", "2026-W54", "2026-45", "W45", "2026-W5"])
def test_iso_week_rejects_invalid(text: str) -> None:
    with pytest.raises(ValidationError):
        IsoWeek.parse(text)
