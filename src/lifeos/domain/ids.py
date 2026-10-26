"""Identificadores estables de tarea (D-11): `t-` + 6 símbolos de un alfabeto sin ambigüedades."""

import re
import secrets
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass

from lifeos.domain.errors import ValidationError

ALPHABET = "23456789abcdefghjkmnpqrstvwxyz"
ID_LENGTH = 6
DEFAULT_MAX_ATTEMPTS = 20
_ID_RE = re.compile(rf"t-[{ALPHABET}]{{{ID_LENGTH}}}")

Choice = Callable[[Sequence[str]], str]


@dataclass(frozen=True, slots=True)
class TaskId:
    value: str

    def __post_init__(self) -> None:
        if not _ID_RE.fullmatch(self.value):
            raise ValidationError("id", self.value, f"formato esperado t-<{ID_LENGTH} símbolos>")

    def __str__(self) -> str:
        return self.value


def new_task_id(choice: Choice = secrets.choice) -> TaskId:
    return TaskId("t-" + "".join(choice(ALPHABET) for _ in range(ID_LENGTH)))


def new_unique_task_id(
    taken: Collection[TaskId],
    choice: Choice = secrets.choice,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> TaskId:
    for _ in range(max_attempts):
        candidate = new_task_id(choice)
        if candidate not in taken:
            return candidate
    raise ValidationError("id", len(taken), "no se pudo generar un id único")
