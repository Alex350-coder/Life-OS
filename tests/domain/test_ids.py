"""Identificadores estables de tarea (spec formato-de-tareas, D-11)."""

import pytest

from lifeos.domain.errors import ValidationError
from lifeos.domain.ids import (
    ALPHABET,
    IdExhaustedError,
    TaskId,
    new_task_id,
    new_unique_task_id,
)

pytestmark = pytest.mark.unit


def test_alphabet_excludes_ambiguous_characters() -> None:
    assert len(ALPHABET) == 30
    assert not set("ilou01") & set(ALPHABET)


@pytest.mark.parametrize("value", ["t-7f3k2m", "t-222222", "t-zzzzzz"])
def test_task_id_accepts_valid_values(value: str) -> None:
    assert TaskId(value).value == value


@pytest.mark.parametrize(
    "value",
    ["", "t-7f3k2", "t-7f3k2mm", "x-7f3k2m", "t-7F3K2M", "t-7f3k2o", "t-7f3k21", " t-7f3k2m"],
)
def test_task_id_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValidationError, match="id"):
        TaskId(value)


def test_new_task_id_uses_alphabet_and_prefix() -> None:
    ids = {new_task_id().value for _ in range(200)}
    assert len(ids) > 190
    assert all(v.startswith("t-") and set(v[2:]) <= set(ALPHABET) for v in ids)


def test_new_task_id_is_deterministic_with_injected_choice() -> None:
    assert new_task_id(choice=lambda seq: seq[0]).value == "t-222222"


def test_new_unique_task_id_retries_on_collision() -> None:
    sequence = iter("2" * 6 + "3" * 6)
    taken = {TaskId("t-222222")}
    result = new_unique_task_id(taken, choice=lambda _seq: next(sequence))
    assert result == TaskId("t-333333")


def test_new_unique_task_id_gives_up_after_max_attempts() -> None:
    taken = {TaskId("t-222222")}
    with pytest.raises(IdExhaustedError, match="único"):
        new_unique_task_id(taken, choice=lambda seq: seq[0], max_attempts=3)
