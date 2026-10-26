"""Reglas de integridad de la bóveda: un id identifica una sola tarea."""

from collections.abc import Iterable
from dataclasses import dataclass

from lifeos.domain.errors import DomainError
from lifeos.domain.ids import TaskId


@dataclass(frozen=True, slots=True)
class Location:
    path: str
    line_index: int


class DuplicateTaskIdError(DomainError):
    """Varias tareas comparten id. Se reporta; nunca se resuelve automáticamente."""

    def __init__(self, task_id: TaskId, locations: tuple[Location, ...]) -> None:
        where = ", ".join(f"{loc.path}:{loc.line_index + 1}" for loc in locations)
        super().__init__(f"id duplicado {task_id.value} en {where}")
        self.task_id = task_id
        self.locations = locations


def find_duplicate_ids(
    entries: Iterable[tuple[TaskId, Location]],
) -> tuple[DuplicateTaskIdError, ...]:
    grouped: dict[TaskId, list[Location]] = {}
    for task_id, location in entries:
        grouped.setdefault(task_id, []).append(location)
    return tuple(
        DuplicateTaskIdError(task_id, tuple(locations))
        for task_id, locations in grouped.items()
        if len(locations) > 1
    )
