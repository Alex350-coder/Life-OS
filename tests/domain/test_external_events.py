"""Eventos externos en la disponibilidad (F7-T02, fase-7.md).

Un intervalo ocupado de Google Calendar descuenta capacidad como un compromiso fijo, guarda su
fuente y su nivel de confianza, y el validador rechaza un bloque de trabajo que lo pise.
"""

from datetime import timedelta

import pytest

from lifeos.domain.capacity import Interval
from lifeos.domain.errors import ValidationError
from lifeos.domain.external import (
    LONG_BUSY_MINUTES,
    Confidence,
    ExternalBusy,
    busy_from,
)
from lifeos.domain.horario_note import parse_horario
from lifeos.domain.plan import ConflictCode, PlanProposal, PlanStatus, build_context
from lifeos.domain.plan_note import parse_weekly, render_weekly
from lifeos.domain.plan_validator import validate_plan
from tests.planning_builders import MON, NOW, TUE, WEEK, at, context, span, task, work
from tests.planning_samples import HORARIO

pytestmark = pytest.mark.unit
SOURCE = "google:primary"
TASK = task("- [ ] Informe [id:: t-aaaaaa] [esfuerzo:: 2h] [vence:: 2026-11-20]")


def busy(day_span: str, day=MON) -> ExternalBusy:  # type: ignore[no-untyped-def]
    return busy_from(span(day, day_span), SOURCE)


def test_short_busy_interval_has_high_confidence() -> None:
    item = busy("14:00–15:00")
    assert (item.source, item.confidence) == (SOURCE, Confidence.ALTA)


def test_long_busy_interval_needs_confirmation() -> None:
    # Un "ocupado" de medio día o más suele ser un viaje o un evento de todo el día: se informa
    # con confianza media para que el propietario lo confirme.
    start = at(MON, "08:00")

    item = busy_from(Interval(start, start + timedelta(minutes=LONG_BUSY_MINUTES)), SOURCE)
    assert item.confidence is Confidence.MEDIA


@pytest.mark.parametrize("source", ["", " ", "x" * 81, "google:\nprimary", "a[b]"])
def test_source_must_be_a_short_inert_label(source: str) -> None:
    with pytest.raises(ValidationError):
        ExternalBusy(span(MON, "14:00–15:00"), source, Confidence.ALTA)


def test_external_busy_reduces_free_time_and_budget() -> None:
    plain = context(TASK)
    busy_ctx = build_context(
        parse_horario(HORARIO), WEEK, (TASK,), now=NOW, external=(busy("14:00–15:00"),)
    )
    before, after = plain.day(MON), busy_ctx.day(MON)
    assert before is not None and after is not None
    assert after.free_minutes == before.free_minutes - 60
    assert after.budget_minutes <= before.budget_minutes
    assert busy_ctx.external == (busy("14:00–15:00"),)
    # Los demás días no cambian.
    assert busy_ctx.day(TUE) == plain.day(TUE)


def test_busy_outside_the_week_is_ignored() -> None:
    outside = busy_from(span(WEEK.days()[0] - timedelta(days=1), "14:00–15:00"), SOURCE)
    ctx = build_context(parse_horario(HORARIO), WEEK, (TASK,), now=NOW, external=(outside,))
    assert ctx.external == ()


def test_work_block_over_an_external_event_is_rejected() -> None:
    ctx = build_context(
        parse_horario(HORARIO), WEEK, (TASK,), now=NOW, external=(busy("14:00–15:00"),)
    )
    proposal = PlanProposal(WEEK, (work(MON, "14:00–15:00", "t-aaaaaa", "Informe"),))
    codes = {c.code for c in validate_plan(proposal, ctx)}
    assert ConflictCode.EVENTO_EXTERNO in codes
    assert ConflictCode.FUERA_DE_DISPONIBILIDAD not in codes  # el motivo es el evento, no otro


def test_conflict_message_names_source_and_confidence_but_no_title() -> None:
    ctx = build_context(
        parse_horario(HORARIO), WEEK, (TASK,), now=NOW, external=(busy("14:00–15:00"),)
    )
    proposal = PlanProposal(WEEK, (work(MON, "14:30–15:30", "t-aaaaaa", "Informe"),))
    conflict = next(
        c for c in validate_plan(proposal, ctx) if c.code is ConflictCode.EVENTO_EXTERNO
    )
    assert SOURCE in conflict.message and "alta" in conflict.message
    assert conflict.day == MON


def test_weekly_note_records_external_events_with_source_and_confidence() -> None:
    ctx = build_context(
        parse_horario(HORARIO), WEEK, (TASK,), now=NOW, external=(busy("14:00–15:00"),)
    )
    text = render_weekly(PlanProposal(WEEK, ()), ctx, status=PlanStatus.PROPUESTO, generated=NOW)
    assert "## Eventos externos" in text
    assert "14:00–15:00 · ocupado · fuente: google:primary · confianza: alta" in text
    parse_weekly(text)  # la sección nueva no rompe la lectura de la nota


def test_weekly_note_without_external_events_says_none() -> None:
    text = render_weekly(
        PlanProposal(WEEK, ()), context(TASK), status=PlanStatus.PROPUESTO, generated=NOW
    )
    section = text.split("## Eventos externos", 1)[1].split("##", 1)[0]
    assert "- Ninguno" in section


def test_multi_day_busy_interval_shows_both_dates() -> None:

    long = busy_from(Interval(at(MON, "18:00"), at(TUE, "09:00")), SOURCE)
    ctx = build_context(parse_horario(HORARIO), WEEK, (TASK,), now=NOW, external=(long,))
    text = render_weekly(PlanProposal(WEEK, ()), ctx, status=PlanStatus.PROPUESTO, generated=NOW)
    assert "2026-11-02 18:00 → 2026-11-03 09:00 · ocupado" in text
    assert "confianza: media" in text
