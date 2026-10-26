"""Regresiones de la revisión de la Fase 1 (dominio): invariantes y casos límite de formato."""

from datetime import date

import pytest

from lifeos.domain.errors import ValidationError
from lifeos.domain.ids import IdExhaustedError, TaskId, new_unique_task_id
from lifeos.domain.integrity import DuplicateTaskIdError, Location
from lifeos.domain.note import append_line, parse_note, split_lines
from lifeos.domain.task import Checkbox, Effort, Progress, Task
from lifeos.domain.task_line import TaskLine, parse_task_line

pytestmark = pytest.mark.unit


# --- TaskLine se valida a sí misma (type-design-analyzer) ----------------------------------


def test_task_line_rejects_raw_that_disagrees_with_task() -> None:
    with pytest.raises(ValidationError, match="línea"):
        TaskLine(raw="- [ ] a", task=Task("b", Checkbox.DONE))


def test_task_line_rejects_raw_that_is_not_a_task() -> None:
    with pytest.raises(ValidationError, match="línea"):
        TaskLine(raw="texto", task=Task("texto", Checkbox.PENDING))


# --- Value objects estrictos ----------------------------------------------------------------


@pytest.mark.parametrize("value", [1.5, True])
def test_effort_rejects_non_integer_minutes(value: object) -> None:
    with pytest.raises(ValidationError, match="esfuerzo"):
        Effort(value)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [50.5, True])
def test_progress_rejects_non_integer_percent(value: object) -> None:
    with pytest.raises(ValidationError, match="avance"):
        Progress(value)  # type: ignore[arg-type]


def test_duplicate_error_requires_two_locations() -> None:
    with pytest.raises(ValueError, match="dos ubicaciones"):
        DuplicateTaskIdError(TaskId("t-222222"), (Location("a.md", 0),))


def test_location_rejects_negative_index() -> None:
    with pytest.raises(ValidationError, match="línea"):
        Location("a.md", -1)


def test_exhausted_ids_raise_dedicated_error() -> None:
    with pytest.raises(IdExhaustedError):
        new_unique_task_id({TaskId("t-222222")}, choice=lambda seq: seq[0], max_attempts=2)


# --- Fechas mal escritas no desaparecen en silencio (silent-failure-hunter) -----------------


@pytest.mark.parametrize(
    ("line", "field"),
    [
        ("- [ ] Pagar 📅 mañana", "fecha límite"),
        ("- [ ] Pagar ⏳ pronto", "fecha programada"),
    ],
)
def test_date_emoji_without_valid_date_is_a_problem(line: str, field: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        parse_task_line(line)
    assert exc_info.value.field == field


def test_date_emoji_without_space_is_still_recognized() -> None:
    parsed = parse_task_line("- [ ] Pagar 📅2026-11-05")

    assert parsed is not None
    assert parsed.task.due == date(2026, 11, 5)
    assert parsed.render() == "- [ ] Pagar 📅2026-11-05"


# --- Saltos de línea: solo \n y \r\n (code-reviewer) ----------------------------------------


@pytest.mark.parametrize("separator", ["\x85", "\x0c", " ", "\x1e"])
def test_unicode_separators_do_not_create_phantom_lines(separator: str) -> None:
    text = f"x{separator}- [ ] fantasma\n"

    assert parse_note(text).tasks == ()
    assert split_lines(text) == (f"x{separator}- [ ] fantasma",)


def test_split_lines_keeps_crlf_semantics() -> None:
    assert split_lines("a\r\nb\nc") == ("a", "b", "c")
    assert split_lines("") == ()


def test_append_line_does_not_mutate_semantics_for_trailing_cr() -> None:
    assert append_line("a\r\n", "b") == "a\r\nb\r\n"


def test_problem_keeps_line_content_for_diagnostics() -> None:
    parsed = parse_note("- [ ] Mala [id:: t-222222] [esfuerzo:: 0h]\n")

    assert parsed.problems[0].content == "- [ ] Mala [id:: t-222222] [esfuerzo:: 0h]"
