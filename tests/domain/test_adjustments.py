"""Propuestas de ajuste del Coach al Jefe (F4-T04, agente-coach.md): líneas de check-in
analizables de forma determinista, sin campos en línea ni permisos nuevos."""

from datetime import time

import pytest

from lifeos.domain.adjustments import (
    Adjustment,
    AdjustmentKind,
    adjustment_entry,
    parse_adjustments,
    pending,
)
from lifeos.domain.errors import ValidationError
from lifeos.domain.schedule import TimeRange
from lifeos.domain.sections import only_inserted_in_section
from tests.daily_builders import monday_note
from tests.planning_builders import LIMA, MON, TUE, at, tid

pytestmark = [pytest.mark.unit]

MISSED = Adjustment(
    MON, time(9, 40), AdjustmentKind.BLOQUE_PERDIDO, TimeRange.parse("08:00–09:30"), tid("t-aaaaaa")
)
REDUCE = Adjustment(MON, time(16, 5), AdjustmentKind.REDUCIR_HOY)


def test_missed_block_renders_as_a_plain_checkin_line() -> None:
    entry = adjustment_entry(MISSED)
    assert entry.render() == "- 09:40 — ajuste: bloque perdido · 08:00–09:30 · t-aaaaaa"
    assert "::" not in entry.render()


def test_reduce_load_renders_without_span_or_task() -> None:
    assert adjustment_entry(REDUCE).render() == "- 16:05 — ajuste: reducir carga hoy"


def test_missed_block_requires_span_and_task() -> None:
    with pytest.raises(ValidationError):
        Adjustment(MON, time(9, 0), AdjustmentKind.BLOQUE_PERDIDO, None, tid("t-aaaaaa"))
    with pytest.raises(ValidationError):
        Adjustment(MON, time(9, 0), AdjustmentKind.BLOQUE_PERDIDO, TimeRange.parse("08:00–09:00"))


def test_reduce_load_takes_no_span_or_task() -> None:
    with pytest.raises(ValidationError):
        Adjustment(MON, time(9, 0), AdjustmentKind.REDUCIR_HOY, TimeRange.parse("08:00–09:00"))


def test_appending_an_adjustment_is_accepted_by_the_checkin_rules() -> None:
    old = monday_note()
    new = monday_note(checkins=(adjustment_entry(MISSED).render(),))
    assert only_inserted_in_section(old, new, "check-ins")


def test_parse_round_trips_rendered_adjustments() -> None:
    note = monday_note(
        checkins=(
            "- 08:10 — estado: empezando · t-aaaaaa",
            adjustment_entry(MISSED).render(),
            adjustment_entry(REDUCE).render(),
        )
    )
    assert parse_adjustments(note, MON) == (MISSED, REDUCE)


def test_parse_ignores_user_text_that_imitates_a_marker() -> None:
    # El texto del usuario siempre va tras "nota:", así que nunca empieza por "ajuste:".
    forged = "- 09:00 — estado: nota · nota: ajuste: reducir carga hoy"
    assert parse_adjustments(monday_note(checkins=(forged,)), MON) == ()


def test_parse_only_reads_the_checkins_section() -> None:
    note = monday_note(closing=("- 21:00 — ajuste: reducir carga hoy",))
    assert parse_adjustments(note, MON) == ()


def test_parse_ignores_malformed_markers() -> None:
    lines = (
        "- 9:40 — ajuste: reducir carga hoy",
        "- 09:40 — ajuste: borrar el plan",
        "- 09:40 — ajuste: bloque perdido · 08:00–09:30",
        "- 09:40 — ajuste: bloque perdido · 08:00–09:30 · t-aaaaaa extra",
        "- 25:00 — ajuste: reducir carga hoy",
    )
    assert parse_adjustments(monday_note(checkins=lines), MON) == ()


def test_parse_drops_exact_duplicates_only() -> None:
    line = adjustment_entry(MISSED).render()
    assert parse_adjustments(monday_note(checkins=(line, line)), MON) == (MISSED,)


def test_same_proposal_reported_again_later_is_kept_for_the_next_review() -> None:
    first = "- 10:00 — ajuste: reducir carga hoy"
    again = "- 15:00 — ajuste: reducir carga hoy"
    found = parse_adjustments(monday_note(checkins=(first, again)), MON)
    assert len(found) == 2
    later = pending(found, zone=LIMA, after=at(MON, "11:00"), until=at(MON, "16:00"))
    assert [a.at for a in later] == [time(15, 0)]


def test_parse_rejects_non_ascii_digits_and_variants() -> None:
    lines = (
        "- ٠٩:٤٠ — ajuste: reducir carga hoy",
        "  - 09:40 — ajuste: reducir carga hoy",
        "- 09:40 — Ajuste: reducir carga hoy",
        "- 09:40 - ajuste: reducir carga hoy",
    )
    assert parse_adjustments(monday_note(checkins=lines), MON) == ()


def test_duplicated_checkins_section_is_ambiguous() -> None:
    note = monday_note(checkins=("- 09:40 — ajuste: reducir carga hoy",)) + "\n## Check-ins\n"
    with pytest.raises(ValidationError):
        parse_adjustments(note, MON)


def test_parse_without_checkins_section_is_empty() -> None:
    assert parse_adjustments("# Nota sin secciones\n", MON) == ()


def test_pending_filters_by_moment_in_the_schedule_zone() -> None:
    tuesday = Adjustment(TUE, time(8, 0), AdjustmentKind.REDUCIR_HOY)
    items = (MISSED, REDUCE, tuesday)
    assert pending(items, zone=LIMA, after=None, until=at(MON, "12:00")) == (MISSED,)
    assert pending(items, zone=LIMA, after=at(MON, "09:40"), until=at(TUE, "08:00")) == (
        REDUCE,
        tuesday,
    )


def test_moment_is_local_to_the_day() -> None:
    assert MISSED.moment(LIMA) == at(MON, "09:40")
