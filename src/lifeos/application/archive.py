"""Archivo histórico reversible (F1-T06).

Archivar mueve la línea a `Archivo/<misma ruta>` y restaurar la devuelve a su nota. Primero se
copia al destino y después se quita del origen: si el proceso se interrumpe entre ambos pasos,
repetir la operación la completa sin duplicar ni perder la tarea. Nunca se borran archivos.
"""

from lifeos.application.tasks import (
    ARCHIVE_ROOT,
    ApplicationError,
    LocatedTask,
    TaskNotFoundError,
    append_to_note,
    remove_from_note,
    scan,
)
from lifeos.domain.ids import TaskId
from lifeos.domain.integrity import DuplicateTaskIdError, Location
from lifeos.ports.vault import VaultPath, VaultStore


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _occurrences(vault: VaultStore, task_id: TaskId) -> tuple[list[LocatedTask], list[LocatedTask]]:
    found = [t for t in scan(vault).tasks if t.task.id == task_id]
    return [t for t in found if not t.archived], [t for t in found if t.archived]


def _single(task_id: TaskId, candidates: list[LocatedTask]) -> LocatedTask:
    if len(candidates) > 1:
        locations = tuple(Location(t.path.value, t.index) for t in candidates)
        raise DuplicateTaskIdError(task_id, locations)
    return candidates[0]


def _reject_nested_subtasks(vault: VaultStore, located: LocatedTask) -> None:
    lines = vault.read(located.path).text.splitlines()
    following = lines[located.index + 1] if located.index + 1 < len(lines) else ""
    if following.strip() and _indent(following) > _indent(located.line.raw):
        raise ApplicationError(f"la tarea {located.task.id} tiene subtareas anidadas")


def archive_task(vault: VaultStore, task_id: TaskId) -> VaultPath:
    active, archived = _occurrences(vault, task_id)
    if not active:
        if archived:
            return _single(task_id, archived).path  # ya archivada: no hay nada que hacer
        raise TaskNotFoundError(f"no existe la tarea {task_id}")
    source = _single(task_id, active)
    target = ARCHIVE_ROOT.join(source.path)
    _reject_nested_subtasks(vault, source)
    if not any(entry.path == target for entry in archived):
        append_to_note(vault, target, source.line.render())
    remove_from_note(vault, source)
    return target


def restore_task(vault: VaultStore, task_id: TaskId) -> VaultPath:
    active, archived = _occurrences(vault, task_id)
    if not archived:
        raise TaskNotFoundError(f"la tarea {task_id} no está archivada")
    entry = _single(task_id, archived)
    original = entry.path.relative_to(ARCHIVE_ROOT)
    if not any(task.path == original for task in active):
        append_to_note(vault, original, entry.line.render())
    remove_from_note(vault, entry)
    return original
