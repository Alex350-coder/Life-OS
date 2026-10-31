"""Cierre diario (F4-T03, agente-coach.md, RULES §D4).

El propietario responde por cada bloque de trabajo del día: hecha, parcial (avance y/o esfuerzo
restante) o no realizada. Solo lo que responde explícitamente cambia; lo que queda sin
respuesta se registra como tal y nunca se da por completado.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import time
from enum import StrEnum

from lifeos.domain.checkin import CheckInEntry
from lifeos.domain.errors import ValidationError
from lifeos.domain.ids import TaskId
from lifeos.domain.task import Checkbox, Effort, Progress

UNKNOWN = "?"


class AnswerKind(StrEnum):
    HECHA = "hecha"
    PARCIAL = "parcial"
    NO = "no"


@dataclass(frozen=True, slots=True)
class ClosingAnswer:
    task_id: TaskId
    kind: AnswerKind
    progress: Progress | None = None
    remaining: Effort | None = None

    def __post_init__(self) -> None:
        has_numbers = self.progress is not None or self.remaining is not None
        if self.kind is AnswerKind.PARCIAL and not has_numbers:
            raise ValidationError(
                "cierre", str(self.task_id), "una respuesta parcial necesita avance o restante"
            )
        if self.kind is not AnswerKind.PARCIAL and has_numbers:
            raise ValidationError(
                "cierre", str(self.task_id), f"'{self.kind}' no lleva avance ni restante"
            )


@dataclass(frozen=True, slots=True)
class TaskChange:
    """Cambio que se aplicará a una tarea, con sus valores actuales para revisarlo antes."""

    task_id: TaskId
    title: str
    kind: AnswerKind
    progress_before: Progress | None
    progress_after: Progress | None
    remaining_before: Effort | None
    remaining_after: Effort | None


def parse_progress(text: str) -> Progress:
    """Avance escrito por el propietario: `40` o `40%` (CLI y web)."""
    value = text.strip()
    return Progress.parse(value if value.endswith("%") else f"{value}%")


def answer_from_input(
    task_id: TaskId, kind: str, progress: str | None = None, remaining: str | None = None
) -> ClosingAnswer:
    """Respuesta del cierre a partir de texto (formulario web); las reglas son las de siempre."""
    try:
        answer_kind = AnswerKind(kind)
    except ValueError:
        raise ValidationError("cierre", kind, "tipo esperado: hecha, parcial o no") from None
    return ClosingAnswer(
        task_id,
        answer_kind,
        parse_progress(progress) if progress else None,
        Effort.parse(remaining) if remaining else None,
    )


def check_answers(answers: Iterable[ClosingAnswer]) -> None:
    seen: set[TaskId] = set()
    for answer in answers:
        if answer.task_id in seen:
            raise ValidationError("cierre", str(answer.task_id), "respuesta repetida")
        seen.add(answer.task_id)


def task_change(answer: ClosingAnswer) -> tuple[Checkbox | None, dict[str, str]]:
    """Casilla y campos que cambian en la línea de la tarea (nada si no se hizo)."""
    if answer.kind is AnswerKind.HECHA:
        return Checkbox.DONE, {}
    fields: dict[str, str] = {}
    if answer.progress is not None:
        fields["avance"] = answer.progress.render()
    if answer.remaining is not None:
        fields["esfuerzo"] = answer.remaining.render()
    return None, fields


def _render(value: Progress | Effort | None) -> str:
    return value.render() if value is not None else UNKNOWN


def _summary(change: TaskChange) -> str:
    if change.kind is AnswerKind.HECHA:
        return f"hecha: {change.task_id}"
    if change.kind is AnswerKind.NO:
        return f"no realizada: {change.task_id} (propuesta de reubicación al Jefe)"
    text = f"avance: {change.task_id}"
    if change.progress_after is not None:
        text += f" {_render(change.progress_before)} → {change.progress_after.render()}"
    if change.remaining_after is not None:
        text += (
            f" · restante {_render(change.remaining_before)} → {change.remaining_after.render()}"
        )
    return text


def summary_entries(
    changes: Sequence[TaskChange], unanswered: Sequence[TaskId], at: time
) -> tuple[CheckInEntry, ...]:
    """Líneas para `## Cierre`: una por respuesta y una por tarea sin respuesta."""
    entries = [CheckInEntry(at, _summary(change)) for change in changes]
    entries += [
        CheckInEntry(at, f"sin respuesta: {task_id} (sin cambios)") for task_id in unanswered
    ]
    return tuple(entries)
