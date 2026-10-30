"""Campos de planificación de una tarea: `[depende::]` y `[divisible::]` (planificador.md)."""

import pytest

from lifeos.domain.errors import ValidationError
from lifeos.domain.ids import TaskId
from lifeos.domain.task_line import TaskLine, parse_task_line, with_field

pytestmark = pytest.mark.unit
BASE = "- [ ] Informe [id:: t-7f3k2m] [area:: universidad] [esfuerzo:: 3h]"


def parse(line: str) -> TaskLine:
    parsed = parse_task_line(line)
    assert parsed is not None
    return parsed


def test_defaults_without_planning_fields() -> None:
    task = parse(BASE).task
    assert task.depends_on == ()
    assert task.divisible is True


def test_depends_on_one_or_several_tasks() -> None:
    task = parse(f"{BASE} [depende:: t-aaaaaa, t-bbbbbb]").task
    assert task.depends_on == (TaskId("t-aaaaaa"), TaskId("t-bbbbbb"))
    assert parse(BASE + " [depende:: t-aaaaaa]").extra_fields == ()


@pytest.mark.parametrize(
    "value", ["", "t-AAAAAA", "t-aaaaaa,,t-bbbbbb", "t-aaaaaa, t-aaaaaa", "otra tarea"]
)
def test_invalid_dependencies_are_rejected(value: str) -> None:
    with pytest.raises(ValidationError):
        parse_task_line(f"{BASE} [depende:: {value}]")


def test_a_task_cannot_depend_on_itself() -> None:
    with pytest.raises(ValidationError, match="sí misma"):
        parse_task_line(f"{BASE} [depende:: t-7f3k2m]")


@pytest.mark.parametrize(("value", "expected"), [("no", False), ("sí", True), ("si", True)])
def test_divisible_values(value: str, expected: bool) -> None:
    assert parse(f"{BASE} [divisible:: {value}]").task.divisible is expected


def test_invalid_divisible_value_is_rejected() -> None:
    with pytest.raises(ValidationError):
        parse_task_line(f"{BASE} [divisible:: quizás]")


def test_render_is_still_byte_for_byte() -> None:
    line = f"{BASE} [depende:: t-aaaaaa] [divisible:: no]"
    assert parse(line).render() == line
    assert with_field(parse(line), "divisible", "sí").task.divisible is True
