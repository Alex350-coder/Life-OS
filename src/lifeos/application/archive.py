"""Archivo histórico reversible (F1-T06).

Archivar mueve la línea a `Archivo/<misma ruta>` y restaurar la devuelve a su nota. Primero se
copia al destino y después se quita del origen: si el proceso se interrumpe entre ambos pasos,
repetir la operación la completa. La copia ya existente solo se reutiliza si es idéntica a la
línea de origen; si difieren (el usuario editó una de las dos), se informa un conflicto en vez
de descartar cambios. Nunca se borran archivos.
"""

from lifeos.application.tasks import (
    ARCHIVE_ROOT,
    DEFAULT_PROTECTED_ROOTS,
    ApplicationError,
    LocatedTask,
    TaskNotFoundError,
    TaskQuery,
    append_to_note,
    check_writable,
    query_tasks,
    remove_from_note,
    scan,
)
from lifeos.domain.ids import TaskId
from lifeos.domain.integrity import DuplicateTaskIdError, Location
from lifeos.domain.note import split_lines
from lifeos.ports.vault import VaultPath, VaultStore, VersionConflictError


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _occurrences(vault: VaultStore, task_id: TaskId) -> tuple[list[LocatedTask], list[LocatedTask]]:
    found = [t for t in scan(vault).tasks if t.task.id == task_id]
    return [t for t in found if not t.archived], [t for t in found if t.archived]


def _single(task_id: TaskId, candidates: list[LocatedTask]) -> LocatedTask:
    if not candidates:
        raise TaskNotFoundError(f"no existe la tarea {task_id}")
    if len(candidates) > 1:
        locations = tuple(Location(t.path.value, t.index) for t in candidates)
        raise DuplicateTaskIdError(task_id, locations)
    return candidates[0]


def _reject_nested_subtasks(vault: VaultStore, located: LocatedTask) -> None:
    # Si la nota cambió tras esta lectura, remove_from_note lo detectará y no escribirá.
    lines = split_lines(vault.read(located.path).text)
    following = lines[located.index + 1] if located.index + 1 < len(lines) else ""
    if following.strip() and _indent(following) > _indent(located.line.raw):
        raise ApplicationError(f"la tarea {located.task.id} tiene subtareas anidadas")


def _move(
    vault: VaultStore, source: LocatedTask, target: VaultPath, copies: list[LocatedTask]
) -> None:
    """Copia `source` a `target` (o reutiliza una copia idéntica ya existente) y la quita."""
    if any(copy.path != target for copy in copies):
        raise ApplicationError(f"la tarea {source.task.id} aparece en una nota inesperada")
    if copies:
        if copies[0].line.raw != source.line.raw:
            raise VersionConflictError(
                f"la tarea {source.task.id} difiere entre {source.path} y {target}"
            )
    else:
        append_to_note(vault, target, source.line.render())
    remove_from_note(vault, source)


def archive_task(vault: VaultStore, task_id: TaskId) -> VaultPath:
    active, archived = _occurrences(vault, task_id)
    if not active:
        return _single(task_id, archived).path  # ya archivada: no hay nada que hacer
    source = _single(task_id, active)
    _reject_nested_subtasks(vault, source)
    target = ARCHIVE_ROOT.join(source.path)
    _move(vault, source, target, archived)
    return target


def restore_task(
    vault: VaultStore,
    task_id: TaskId,
    *,
    protected: tuple[VaultPath, ...] = DEFAULT_PROTECTED_ROOTS,
) -> VaultPath:
    active, archived = _occurrences(vault, task_id)
    if not archived:
        raise TaskNotFoundError(f"la tarea {task_id} no está archivada")
    entry = _single(task_id, archived)
    original = entry.path.relative_to(ARCHIVE_ROOT)
    check_writable(original, protected)
    _move(vault, entry, original, active)
    return original


def archive_matching(vault: VaultStore, query: TaskQuery) -> tuple[TaskId, ...]:
    """Cierra un ciclo: archiva las tareas activas con id que cumplen la consulta.

    Conserva el estado de cada tarea (una pendiente no pasa a completada). Las tareas sin id
    no se tocan: primero hay que asignarles uno con `ensure_id`.
    """
    ids = [t.task.id for t in query_tasks(vault, query) if not t.archived]
    moved = tuple(task_id for task_id in ids if task_id is not None)
    for task_id in moved:
        archive_task(vault, task_id)
    return moved
