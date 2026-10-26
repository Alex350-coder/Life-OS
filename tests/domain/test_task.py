"""Entidad Task, value objects y estados derivados (RULES §D3, spec formato-de-tareas)."""

import dataclasses
from datetime import date

import pytest

from lifeos.domain.errors import ValidationError
from lifeos.domain.ids import TaskId
from lifeos.domain.task import (
    Area,
    Checkbox,
    Effort,
    ExplicitState,
    Priority,
    Progress,
    Task,
    TaskStatus,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("text", "minutes"), [("30m", 30), ("1.5h", 90), ("4h", 240), ("0.25h", 15), ("90m", 90)]
)
def test_effort_parses_minutes_and_decimal_hours(text: str, minutes: int) -> None:
    assert Effort.parse(text).minutes == minutes


@pytest.mark.parametrize("text", ["0h", "-1h", "abc", "1.5", "", "0m", "1.01h", "h", "2 h", "1,5h"])
def test_effort_rejects_zero_negative_and_garbage(text: str) -> None:
    with pytest.raises(ValidationError, match="esfuerzo"):
        Effort.parse(text)


@pytest.mark.parametrize(("minutes", "text"), [(30, "30m"), (90, "1.5h"), (240, "4h"), (100, "100m")])
def test_effort_renders_canonical_text(minutes: int, text: str) -> None:
    assert Effort(minutes).render() == text


def test_effort_rejects_non_positive_minutes() -> None:
    with pytest.raises(ValidationError):
        Effort(0)


@pytest.mark.parametrize(("text", "value"), [("0%", 0), ("25%", 25), ("100%", 100), ("40", 40)])
def test_progress_parses_percentages(text: str, value: int) -> None:
    assert Progress.parse(text).percent == value


@pytest.mark.parametrize("text", ["-1%", "101%", "abc", "", "12.5%"])
def test_progress_rejects_outside_0_100(text: str) -> None:
    with pytest.raises(ValidationError, match="avance"):
        Progress.parse(text)


def test_area_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError, match="area"):
        Area.parse("trabajo")


def test_area_and_priority_parse_known_values() -> None:
    assert Area.parse("universidad") is Area.UNIVERSIDAD
    assert Priority.parse("alta") is Priority.ALTA


def test_priority_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError, match="prioridad"):
        Priority.parse("urgente")


def test_explicit_state_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError, match="estado"):
        ExplicitState.parse("archivada")


def _task(**changes: object) -> Task:
    base = Task(
        title="Preparar evaluación",
        checkbox=Checkbox.PENDING,
        id=TaskId("t-7f3k2m"),
        area=Area.UNIVERSIDAD,
        effort=Effort(240),
    )
    return dataclasses.replace(base, **changes)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({}, TaskStatus.PROPUESTA),
        ({"scheduled": date(2026, 10, 28)}, TaskStatus.PROGRAMADA),
        ({"state": ExplicitState.BLOQUEADA}, TaskStatus.BLOQUEADA),
        ({"state": ExplicitState.CONFIRMADA, "scheduled": date(2026, 10, 28)}, TaskStatus.CONFIRMADA),
        ({"checkbox": Checkbox.DONE}, TaskStatus.COMPLETADA),
        ({"checkbox": Checkbox.DONE, "state": ExplicitState.BLOQUEADA}, TaskStatus.COMPLETADA),
        ({"checkbox": Checkbox.CANCELLED}, TaskStatus.CANCELADA),
    ],
)
def test_status_is_derived_from_checkbox_state_and_schedule(
    changes: dict[str, object], expected: TaskStatus
) -> None:
    assert _task(**changes).status is expected


def test_due_and_scheduled_are_independent() -> None:
    task = _task(due=date(2026, 10, 30))
    assert task.due == date(2026, 10, 30)
    assert task.scheduled is None
    assert task.status is TaskStatus.PROPUESTA


def test_task_requires_non_blank_title() -> None:
    with pytest.raises(ValidationError, match="título"):
        _task(title="   ")


def test_task_is_immutable() -> None:
    task = _task()
    with pytest.raises(dataclasses.FrozenInstanceError):
        task.title = "otro"  # type: ignore[misc]


def test_missing_required_fields_are_reported() -> None:
    assert _task().missing_required_fields() == ()
    assert _task(area=None, effort=None).missing_required_fields() == ("area", "esfuerzo")


def test_checkbox_parses_marks() -> None:
    assert Checkbox.parse(" ") is Checkbox.PENDING
    assert Checkbox.parse("x") is Checkbox.DONE
    assert Checkbox.parse("X") is Checkbox.DONE
    assert Checkbox.parse("-") is Checkbox.CANCELLED
    with pytest.raises(ValidationError, match="casilla"):
        Checkbox.parse("?")


def test_validation_error_exposes_field_value_and_reason() -> None:
    error = ValidationError("esfuerzo", "0h", "debe ser mayor que cero")
    assert (error.field, error.value, error.reason) == ("esfuerzo", "0h", "debe ser mayor que cero")
    assert "esfuerzo" in str(error) and "0h" in str(error)
