"""Casos de uso de tareas: escanear, consultar, crear y actualizar (F1-T05)."""

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date

from lifeos.domain.errors import LifeOSError, ValidationError
from lifeos.domain.ids import TaskId, new_unique_task_id
from lifeos.domain.integrity import DuplicateTaskIdError, Location, find_duplicate_ids
from lifeos.domain.note import (
    append_line,
    parse_note,
    remove_line,
    replace_line,
    split_lines,
)
from lifeos.domain.task import Area, Checkbox, Effort, Priority, Task, TaskStatus
from lifeos.domain.task_line import (
    DUE,
    SCHEDULED,
    TaskLine,
    parse_task_line,
    with_checkbox,
    with_field,
    with_id,
)
from lifeos.ports.vault import (
    NoteNotFoundError,
    VaultIOError,
    VaultPath,
    VaultStore,
    VersionConflictError,
    within_any,
)

ARCHIVE_ROOT = VaultPath("Archivo")
INBOX_ROOT = VaultPath("00-Inbox")
# Carpetas que no se leen como tareas: perfil del propietario, auditoría y capturas sin
# procesar (el texto capturado es un dato y no debe ocupar ni duplicar ids de tareas).
SKIPPED_ROOTS = (VaultPath("_sistema"), VaultPath("_logs"), INBOX_ROOT)
# Mínimo de RULES E.12: ningún agente escribe aquí. La política puede ampliarlo, no reducirlo.
RULES_FORBIDDEN_ROOTS = (VaultPath("_sistema"), VaultPath("_logs"), ARCHIVE_ROOT)
# Carpetas que los casos de uso de tareas nunca escriben si no se indica otra lista.
DEFAULT_PROTECTED_ROOTS = (*RULES_FORBIDDEN_ROOTS, INBOX_ROOT)
NOTE_SUFFIX = ".md"
# Ids presentes en líneas mal formadas: siguen ocupados aunque la línea no se pueda analizar.
_RAW_ID_RE = re.compile(r"\[id::\s*(t-[0-9a-z]{6})\s*\]")


class ApplicationError(LifeOSError):
    """Un caso de uso no puede completarse con el estado actual de la bóveda."""


class TaskNotFoundError(ApplicationError):
    """No existe ninguna tarea con ese id (o en esa línea)."""


@dataclass(frozen=True, slots=True)
class LocatedTask:
    path: VaultPath
    index: int
    line: TaskLine

    @property
    def task(self) -> Task:
        return self.line.task

    @property
    def archived(self) -> bool:
        return is_archived(self.path)


@dataclass(frozen=True, slots=True)
class NoteProblem:
    """Nota (line_index None) o línea que no se pudo analizar; se informa, no se oculta."""

    path: VaultPath
    line_index: int | None
    error: LifeOSError


@dataclass(frozen=True, slots=True)
class ScanReport:
    tasks: tuple[LocatedTask, ...]
    problems: tuple[NoteProblem, ...]
    duplicates: tuple[DuplicateTaskIdError, ...]
    # Ids que aparecen en líneas con errores de formato: no se pueden reutilizar.
    reserved_ids: frozenset[TaskId] = frozenset()


@dataclass(frozen=True, slots=True)
class TaskQuery:
    area: Area | None = None
    status: TaskStatus | None = None
    due_from: date | None = None
    due_to: date | None = None
    text: str | None = None  # búsqueda por tema en el título, sin distinguir mayúsculas
    include_archived: bool = False

    def __post_init__(self) -> None:
        if self.due_from and self.due_to and self.due_from > self.due_to:
            raise ValidationError("fecha", self.due_from, "el inicio es posterior al final")


def is_archived(path: VaultPath) -> bool:
    return within_any(path, (ARCHIVE_ROOT,))


# --- Lectura ----------------------------------------------------------------------------------


def _scan_note(
    vault: VaultStore, path: VaultPath
) -> tuple[list[LocatedTask], list[NoteProblem], set[TaskId]]:
    try:
        text = vault.read(path).text
    except (VaultIOError, NoteNotFoundError) as error:  # ilegible o borrada al listar
        return [], [NoteProblem(path, None, error)], set()
    parsed = parse_note(text)
    tasks = [LocatedTask(path, item.index, item.line) for item in parsed.tasks]
    problems = [NoteProblem(path, p.index, p.error) for p in parsed.problems]
    reserved = {TaskId(m) for p in parsed.problems for m in _RAW_ID_RE.findall(p.content)}
    return tasks, problems, reserved


def scan(vault: VaultStore) -> ScanReport:
    """Recorre la bóveda; los errores se acumulan en el informe sin detener el escaneo."""
    tasks: list[LocatedTask] = []
    problems: list[NoteProblem] = []
    reserved: set[TaskId] = set()
    for path in vault.list_notes():
        if within_any(path, SKIPPED_ROOTS):
            continue
        note_tasks, note_problems, note_reserved = _scan_note(vault, path)
        tasks.extend(note_tasks)
        problems.extend(note_problems)
        reserved.update(note_reserved)
    duplicates = find_duplicate_ids(
        (t.task.id, Location(t.path.value, t.index)) for t in tasks if t.task.id is not None
    )
    return ScanReport(tuple(tasks), tuple(problems), duplicates, frozenset(reserved))


def _matches(task: LocatedTask, query: TaskQuery) -> bool:
    if task.archived and not query.include_archived:
        return False
    if query.area is not None and task.task.area is not query.area:
        return False
    if query.status is not None and task.task.status is not query.status:
        return False
    if query.text is not None and query.text.casefold() not in task.task.title.casefold():
        return False
    due = task.task.due
    if query.due_from is not None and (due is None or due < query.due_from):
        return False
    return not (query.due_to is not None and (due is None or due > query.due_to))


def query_tasks(vault: VaultStore, query: TaskQuery) -> tuple[LocatedTask, ...]:
    """Consulta tareas. Por defecto excluye las archivadas (spec memoria-y-vigencia)."""
    return tuple(task for task in scan(vault).tasks if _matches(task, query))


def find_task(vault: VaultStore, task_id: TaskId) -> LocatedTask:
    report = scan(vault)
    found = [task for task in report.tasks if task.task.id == task_id]
    if not found:
        if task_id in report.reserved_ids:
            raise ApplicationError(f"la tarea {task_id} tiene errores de formato; corrígelos")
        raise TaskNotFoundError(f"no existe la tarea {task_id}")
    if len(found) > 1:
        raise DuplicateTaskIdError(task_id, tuple(Location(t.path.value, t.index) for t in found))
    return found[0]


# --- Escritura --------------------------------------------------------------------------------


def check_writable(
    path: VaultPath, protected: tuple[VaultPath, ...] = DEFAULT_PROTECTED_ROOTS
) -> None:
    """Rechaza carpetas protegidas (sin distinguir mayúsculas) y archivos que no son notas.

    `protected` lo calcula la raíz de composición con `permissions.protected_roots(policy)`;
    el mínimo de las reglas se aplica siempre, aunque se pase una lista vacía.
    """
    if within_any(path, (*DEFAULT_PROTECTED_ROOTS, *protected)):
        raise ApplicationError(f"{path} está en una carpeta protegida")
    if not path.name.lower().endswith(NOTE_SUFFIX):
        raise ApplicationError(f"{path} no es una nota Markdown")


def append_to_note(vault: VaultStore, path: VaultPath, line: str) -> int:
    """Añade una línea al final de la nota (creándola si falta) y devuelve su índice."""
    if vault.exists(path):
        snapshot = vault.read(path)
        text = append_line(snapshot.text, line)
        vault.write(path, text, expected=snapshot.version)
    else:
        text = append_line("", line)
        vault.write(path, text, expected=None)
    return len(split_lines(text)) - 1


def remove_from_note(vault: VaultStore, located: LocatedTask) -> None:
    """Quita la línea de la tarea si sigue intacta; si cambió, se reporta un conflicto."""
    snapshot = vault.read(located.path)
    _require_same_line(snapshot.text, located)
    vault.write(located.path, remove_line(snapshot.text, located.index), expected=snapshot.version)


def _require_same_line(text: str, located: LocatedTask) -> None:
    current = {item.index: item.line.raw for item in parse_note(text).tasks}
    if current.get(located.index) != located.line.raw:
        raise VersionConflictError(f"{located.path} cambió: la tarea ya no está en su línea")


def _rewrite_line(
    vault: VaultStore, path: VaultPath, index: int, transform: Callable[[TaskLine], TaskLine]
) -> LocatedTask:
    snapshot = vault.read(path)
    lines = {item.index: item.line for item in parse_note(snapshot.text).tasks}
    if index not in lines:
        raise TaskNotFoundError(f"la línea {index + 1} de {path} no es una tarea")
    updated = transform(lines[index])
    new_text = replace_line(snapshot.text, index, updated.render())
    vault.write(path, new_text, expected=snapshot.version)
    return LocatedTask(path, index, updated)


def _taken_ids(vault: VaultStore) -> set[TaskId]:
    report = scan(vault)
    return {t.task.id for t in report.tasks if t.task.id is not None} | report.reserved_ids


def _render_new_task(
    title: str,
    task_id: TaskId,
    area: Area,
    effort: Effort,
    *,
    due: date | None,
    scheduled: date | None,
    priority: Priority,
) -> str:
    parts = [f"- [ ] {title}", f"[id:: {task_id}]", f"[area:: {area}]"]
    parts.append(f"[esfuerzo:: {effort.render()}]")
    if priority is not Priority.MEDIA:
        parts.append(f"[prioridad:: {priority}]")
    if due is not None:
        parts.append(f"{DUE} {due.isoformat()}")
    if scheduled is not None:
        parts.append(f"{SCHEDULED} {scheduled.isoformat()}")
    return " ".join(parts)


def create_task(
    vault: VaultStore,
    path: VaultPath,
    *,
    title: str,
    area: Area,
    effort: Effort,
    due: date | None = None,
    scheduled: date | None = None,
    priority: Priority = Priority.MEDIA,
    protected: tuple[VaultPath, ...] = DEFAULT_PROTECTED_ROOTS,
) -> LocatedTask:
    """Crea una tarea con id único al final de la nota indicada."""
    check_writable(path, protected)
    task_id = new_unique_task_id(_taken_ids(vault))
    line = _render_new_task(
        title, task_id, area, effort, due=due, scheduled=scheduled, priority=priority
    )
    parsed = parse_task_line(line)
    if parsed is None or parsed.task.title != " ".join(title.split()) or parsed.extra_fields:
        raise ValidationError("título", title, "no puede contener campos, fechas ni saltos")
    index = append_to_note(vault, path, line)
    return LocatedTask(path, index, parsed)


def update_task(
    vault: VaultStore,
    task_id: TaskId,
    *,
    checkbox: Checkbox | None = None,
    fields: Mapping[str, str] | None = None,
) -> LocatedTask:
    """Cambia la casilla y/o campos de una tarea activa, sin tocar el resto de la nota."""
    changes = dict(fields or {})
    if "id" in changes:
        raise ValidationError("id", changes["id"], "el id de una tarea es inmutable")
    located = find_task(vault, task_id)
    if located.archived:
        raise ApplicationError(f"la tarea {task_id} está archivada: restáurala antes")

    def transform(line: TaskLine) -> TaskLine:
        if line.task.id != task_id:
            raise VersionConflictError(f"{located.path} cambió: la tarea {task_id} se movió")
        if checkbox is not None:
            line = with_checkbox(line, checkbox)
        for key, value in changes.items():
            line = with_field(line, key, value)
        return line

    return _rewrite_line(vault, located.path, located.index, transform)


def ensure_id(
    vault: VaultStore,
    path: VaultPath,
    index: int,
    *,
    protected: tuple[VaultPath, ...] = DEFAULT_PROTECTED_ROOTS,
) -> LocatedTask:
    """Asigna un id estable a la tarea de esa línea si no lo tiene; si ya lo tiene, no escribe."""
    check_writable(path, protected)
    lines = {item.index: item.line for item in parse_note(vault.read(path).text).tasks}
    if index not in lines:
        raise TaskNotFoundError(f"la línea {index + 1} de {path} no es una tarea")
    if lines[index].task.id is not None:
        return LocatedTask(path, index, lines[index])
    new_id = new_unique_task_id(_taken_ids(vault))
    return _rewrite_line(vault, path, index, lambda line: with_id(line, new_id))
