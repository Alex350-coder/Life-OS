"""Reglas de integridad de la bóveda: un id identifica una sola tarea."""

from collections.abc import Iterable
from dataclasses import dataclass

from lifeos.domain.errors import DomainError, ValidationError
from lifeos.domain.ids import TaskId

MIN_DUPLICATE_LOCATIONS = 2


@dataclass(frozen=True, slots=True)
class Location:
    path: str
    line_index: int

    def __post_init__(self) -> None:
        if self.line_index < 0:
            raise ValidationError("línea", self.line_index, "no puede ser negativa")


class DuplicateTaskIdError(DomainError):
    """Varias tareas comparten id. Se reporta; nunca se resuelve automáticamente."""

    def __init__(self, task_id: TaskId, locations: tuple[Location, ...]) -> None:
        if len(locations) < MIN_DUPLICATE_LOCATIONS:
            raise ValueError("un id duplicado necesita al menos dos ubicaciones")
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
        if len(locations) >= MIN_DUPLICATE_LOCATIONS
    )
