"""Replanificación con historial (F4-T04, ADR-0011): historia, suelo de planificación,
contexto ajustado por las propuestas del Coach y notas de revisión `-rN`."""

from collections.abc import Iterable
from datetime import date, time

import pytest

from lifeos.domain.adjustments import Adjustment, AdjustmentKind
from lifeos.domain.capacity import DayCapacity
from lifeos.domain.heuristic import propose
from lifeos.domain.horario_note import parse_horario
from lifeos.domain.plan import ConflictCode, PlanStatus
from lifeos.domain.plan_note import (
    MAX_REVISIONS,
    PlanNoteError,
    parse_weekly,
    render_weekly,
    revision_path,
)
from lifeos.domain.replan import adjust_context, check_history, planning_floor, split_history
from lifeos.domain.schedule import TimeRange
from tests.planning_builders import MON, NOW, TUE, WEEK, at, context, task, tid, work
from tests.planning_samples import HORARIO

pytestmark = [pytest.mark.unit]

SCHEDULE = parse_horario(HORARIO)
INFORME = task("- [ ] Informe [id:: t-aaaaaa] [area:: universidad] [esfuerzo:: 3h]")
LECTURA = task("- [ ] Lectura [id:: t-bbbbbb] [area:: universidad] [esfuerzo:: 1h]")
EARLY = work(MON, "08:00–09:30", "t-aaaaaa", "Informe")
NOON = work(MON, "12:00–13:00", "t-bbbbbb", "Lectura")
TUESDAY = work(TUE, "08:00–10:00", "t-aaaaaa", "Informe")


# --- Historia y suelo -------------------------------------------------------------------------


def test_history_is_the_work_that_started_before_the_cutoff() -> None:
    blocks = (EARLY, NOON, TUESDAY)
    assert split_history(blocks, at(MON, "12:30")) == (EARLY, NOON)
    assert split_history(blocks, at(MON, "08:00")) == ()


def test_floor_waits_for_a_block_in_progress() -> None:
    assert planning_floor((EARLY, NOON), at(MON, "12:30")) == at(MON, "13:00")
    assert planning_floor((EARLY,), at(MON, "11:00")) == at(MON, "11:00")


# --- Contexto ajustado ------------------------------------------------------------------------


def _today_budget(days: Iterable[DayCapacity], day: date) -> int:
    return next(d.budget_minutes for d in days if d.day == day)


def test_history_consumes_the_daily_budget() -> None:
    base = context(INFORME, LECTURA, now=at(MON, "09:30"))
    adjusted = adjust_context(base, SCHEDULE, history=(EARLY,), adjustments=())
    assert _today_budget(adjusted.days, MON) == _today_budget(base.days, MON) - 90


def test_reduce_load_today_leaves_no_new_work_today() -> None:
    base = context(INFORME, now=at(MON, "13:00"))
    tired = Adjustment(MON, time(13, 0), AdjustmentKind.REDUCIR_HOY)
    adjusted = adjust_context(base, SCHEDULE, history=(), adjustments=(tired,))
    assert _today_budget(adjusted.days, MON) == 0
    assert _today_budget(adjusted.days, TUE) == _today_budget(base.days, TUE)
    assert all(b.interval.start.date() != MON for b in propose(adjusted).work_blocks())


def test_missed_future_span_is_not_reused() -> None:
    base = context(INFORME, now=at(MON, "07:30"))
    missed = Adjustment(
        MON,
        time(7, 30),
        AdjustmentKind.BLOQUE_PERDIDO,
        TimeRange.parse("08:00–09:30"),
        tid("t-aaaaaa"),
    )
    adjusted = adjust_context(base, SCHEDULE, history=(), adjustments=(missed,))
    for block in propose(adjusted).work_blocks():
        assert not block.interval.overlaps(EARLY.interval)


def test_adjusted_context_is_a_new_object() -> None:
    base = context(INFORME, now=at(MON, "09:30"))
    adjust_context(base, SCHEDULE, history=(EARLY,), adjustments=())
    assert _today_budget(base.days, MON) == _today_budget(
        context(INFORME, now=at(MON, "09:30")).days, MON
    )


# --- Comprobación de la historia --------------------------------------------------------------


def test_unchanged_history_has_no_conflicts() -> None:
    assert check_history((EARLY,), (EARLY,), (TUESDAY,)) == ()


def test_edited_history_is_rejected() -> None:
    moved = work(MON, "08:00–10:00", "t-aaaaaa", "Informe")
    (conflict,) = check_history((moved,), (EARLY,), ())
    assert conflict.code is ConflictCode.HISTORIAL_ALTERADO


def test_removed_history_is_rejected() -> None:
    (conflict,) = check_history((), (EARLY,), ())
    assert conflict.code is ConflictCode.HISTORIAL_ALTERADO


def test_new_work_overlapping_history_is_rejected() -> None:
    overlap = work(MON, "09:00–10:00", "t-bbbbbb", "Lectura")
    codes = {c.code for c in check_history((EARLY,), (EARLY,), (overlap,))}
    assert ConflictCode.SOLAPAMIENTO in codes


# --- Notas de revisión ------------------------------------------------------------------------


def test_revision_paths_are_numbered() -> None:
    assert revision_path(WEEK, 1) == "Semanal/2026-W45-r1.md"
    assert MAX_REVISIONS == 20
    with pytest.raises(ValueError):
        revision_path(WEEK, 0)
    with pytest.raises(ValueError):
        revision_path(WEEK, MAX_REVISIONS + 1)


def _revision_text(status: PlanStatus = PlanStatus.PROPUESTO) -> str:
    ctx = context(INFORME, LECTURA, now=at(MON, "09:31"))
    proposal = propose(ctx)
    return render_weekly(
        proposal,
        ctx,
        status=status,
        generated=at(MON, "09:30"),
        revision=1,
        revises="Semanal/2026-W45.md",
        cutoff=at(MON, "09:31"),
        history=(EARLY,),
        adjustments=("ajuste: bloque perdido · 08:00–09:30 · t-aaaaaa",),
    )


def test_revision_round_trips_with_history_and_cutoff() -> None:
    text = _revision_text()
    assert "revision: 1" in text and "revisa: Semanal/2026-W45.md" in text
    assert "## Ajustes atendidos" in text
    note = parse_weekly(text)
    assert note.revision == 1
    assert note.revises == "Semanal/2026-W45.md"
    assert note.cutoff == at(MON, "09:31")
    assert note.history() == (EARLY,)
    assert EARLY not in note.plan_blocks()
    assert all(b.interval.start >= at(MON, "09:31") for b in note.plan_blocks() if b.task_id)


def test_original_plan_has_no_revision_fields() -> None:
    ctx = context(INFORME, now=NOW)
    text = render_weekly(propose(ctx), ctx, status=PlanStatus.PROPUESTO, generated=NOW)
    assert "revision:" not in text and "Ajustes atendidos" not in text
    note = parse_weekly(text)
    assert note.revision == 0 and note.cutoff is None and note.history() == ()


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("revision: 1", "revision: 0"),
        ("revision: 1", "revision: x"),
        ("revision: 1", "revision: 21"),
    ],
)
def test_bad_revision_number_is_rejected(old: str, new: str) -> None:
    with pytest.raises(PlanNoteError):
        parse_weekly(_revision_text().replace(old, new))


def test_revision_without_cutoff_is_rejected() -> None:
    text = "\n".join(line for line in _revision_text().split("\n") if not line.startswith("desde:"))
    with pytest.raises(PlanNoteError):
        parse_weekly(text)
