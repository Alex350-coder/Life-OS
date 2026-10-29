"""Respuestas del cierre diario (F4-T03): qué cambia en cada tarea y cómo se resume."""

from datetime import time

import pytest
from lifeos.domain.closing import (
    AnswerKind,
    ClosingAnswer,
    TaskChange,
    check_answers,
    summary_entries,
    task_change,
)

from lifeos.domain.errors import ValidationError
from lifeos.domain.task import Checkbox, Effort, Progress
from tests.planning_builders import tid

pytestmark = [pytest.mark.unit]

A, B, C = tid("t-aaaaaa"), tid("t-bbbbbb"), tid("t-cccccc")


def test_done_marks_the_checkbox_only() -> None:
    assert task_change(ClosingAnswer(A, AnswerKind.HECHA)) == (Checkbox.DONE, {})


def test_partial_updates_only_the_given_fields() -> None:
    answer = ClosingAnswer(B, AnswerKind.PARCIAL, progress=Progress(40))
    assert task_change(answer) == (None, {"avance": "40%"})
    both = ClosingAnswer(B, AnswerKind.PARCIAL, Progress(40), Effort(60))
    assert task_change(both) == (None, {"avance": "40%", "esfuerzo": "1h"})


def test_not_done_changes_nothing() -> None:
    assert task_change(ClosingAnswer(C, AnswerKind.NO)) == (None, {})


def test_partial_needs_progress_or_remaining_effort() -> None:
    with pytest.raises(ValidationError):
        ClosingAnswer(B, AnswerKind.PARCIAL)


@pytest.mark.parametrize("kind", [AnswerKind.HECHA, AnswerKind.NO])
def test_done_or_not_done_take_no_numbers(kind: AnswerKind) -> None:
    with pytest.raises(ValidationError):
        ClosingAnswer(A, kind, progress=Progress(50))


def test_duplicate_answers_for_one_task_are_rejected() -> None:
    with pytest.raises(ValidationError, match="t-aaaaaa"):
        check_answers((ClosingAnswer(A, AnswerKind.HECHA), ClosingAnswer(A, AnswerKind.NO)))


def test_summary_lists_every_answer_and_the_unanswered_tasks() -> None:
    changes = (
        TaskChange(A, "Informe", AnswerKind.HECHA, None, None, Effort(180), None),
        TaskChange(
            B, "Lectura", AnswerKind.PARCIAL, Progress(25), Progress(40), Effort(120), Effort(60)
        ),
        TaskChange(C, "SQL", AnswerKind.NO, None, None, None, None),
    )
    entries = summary_entries(changes, (tid("t-dddddd"),), time(21, 0))
    assert [e.render() for e in entries] == [
        "- 21:00 — hecha: t-aaaaaa",
        "- 21:00 — avance: t-bbbbbb 25% → 40% · restante 2h → 1h",
        "- 21:00 — no realizada: t-cccccc (propuesta de reubicación al Jefe)",
        "- 21:00 — sin respuesta: t-dddddd (sin cambios)",
    ]


def test_summary_entries_are_valid_appendable_lines() -> None:
    change = TaskChange(B, "Lectura", AnswerKind.PARCIAL, None, Progress(10), None, None)
    (entry,) = summary_entries((change,), (), time(21, 0))
    assert "::" not in entry.render()
    assert entry.render() == "- 21:00 — avance: t-bbbbbb ? → 10%"
