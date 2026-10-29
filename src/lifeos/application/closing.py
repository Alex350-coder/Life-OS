"""Cierre diario con confirmación (F4-T03, agente-coach.md, RULES §D4).

Dos pasos, como `semana`/`aprobar`:
- `preview` valida todas las respuestas y muestra qué cambiaría. No escribe ni audita nada.
- `apply` vuelve a construir la vista previa (así falla antes de escribir) y aplica solo lo
  respondido:
  1. actualiza cada tarea (actor `propietario`, auditado);
  2. anexa el resumen en `## Cierre`;
  3. el Coach propone al Jefe reubicar cada bloque no realizado.

Las tareas sin respuesta no cambian nunca. Son operaciones del propietario sobre su bóveda, como
`capturar` y `tareas`; el Coach no recibe permisos nuevos (SEC-04).
"""

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, time
from functools import partial
from zoneinfo import ZoneInfo

from lifeos.application.checkin import append_to_section
from lifeos.application.coach import CoachSession, NoDailyPlanError, schedule_zone
from lifeos.application.tasks import ApplicationError, find_task, update_task
from lifeos.domain.adjustments import Adjustment
from lifeos.domain.closing import (
    AnswerKind,
    ClosingAnswer,
    TaskChange,
    check_answers,
    summary_entries,
    task_change,
)
from lifeos.domain.errors import LifeOSError
from lifeos.domain.horario_note import HORARIO_NOTE
from lifeos.domain.ids import TaskId
from lifeos.domain.plan import Block, BlockKind
from lifeos.domain.plan_note import CLOSING_HEADING, PlanNoteError, daily_path, parse_daily_blocks
from lifeos.ports.clock import Clock
from lifeos.ports.logs import AuditLog, AuditRecord, AuditResult, LogWriteError
from lifeos.ports.vault import NoteNotFoundError, VaultPath, VaultStore

OWNER = "propietario"
CLOSING_SECTION = "cierre"


class ClosingIncompleteError(ApplicationError):
    """Se aplicó parte del cierre y luego algo falló. `written` dice qué quedó escrito."""

    def __init__(self, written: tuple[str, ...], cause: Exception) -> None:
        names = ", ".join(written)
        super().__init__(
            f"cierre incompleto: se aplicó {names} y luego falló ({type(cause).__name__}); "
            "revisa la nota y repite solo lo que falte"
        )
        self.written = written


@dataclass(frozen=True, slots=True)
class ClosingPreview:
    day: date
    path: VaultPath
    changes: tuple[TaskChange, ...]
    unanswered: tuple[TaskId, ...]
    missed: tuple[Block, ...]  # bloques de las tareas "no realizadas": se proponen al Jefe
    zone: ZoneInfo


@dataclass(frozen=True, slots=True)
class ClosingResult:
    preview: ClosingPreview
    written: tuple[str, ...]
    adjustments: tuple[Adjustment, ...]


@dataclass(frozen=True, slots=True)
class _Today:
    day: date
    path: VaultPath
    work: tuple[Block, ...]
    zone: ZoneInfo


class DailyClosing:
    def __init__(
        self,
        vault: VaultStore,
        coach: CoachSession,
        clock: Clock,
        audit: AuditLog,
        *,
        configured_zone: str | None = None,
    ) -> None:
        self._vault = vault
        self._coach = coach
        self._clock = clock
        self._audit = audit
        self._configured_zone = configured_zone

    # --- Lectura ----------------------------------------------------------------------------

    def _today(self) -> _Today:
        try:
            horario = self._vault.read(VaultPath.parse(HORARIO_NOTE)).text
        except NoteNotFoundError:
            horario = None
        zone = schedule_zone(horario, self._configured_zone)
        day = self._clock.now().astimezone(zone).date()
        path = VaultPath.parse(daily_path(day))
        try:
            text = self._vault.read(path).text
        except NoteNotFoundError:
            raise NoDailyPlanError(
                f"no hay plan para {day} en {path}: no hay nada que cerrar"
            ) from None
        try:
            blocks = parse_daily_blocks(text, day, zone)
        except PlanNoteError as error:
            raise ApplicationError(
                f"la nota de hoy no sigue el formato del plan: {error}"
            ) from error
        work = tuple(b for b in blocks if b.kind is BlockKind.TRABAJO)
        return _Today(day, path, work, zone)

    def _change(self, answer: ClosingAnswer) -> TaskChange:
        located = find_task(self._vault, answer.task_id)
        if located.archived:
            raise ApplicationError(f"la tarea {answer.task_id} está archivada: restáurala antes")
        task = located.task
        return TaskChange(
            task_id=answer.task_id,
            title=task.title,
            kind=answer.kind,
            progress_before=task.progress,
            progress_after=answer.progress,
            remaining_before=task.effort,
            remaining_after=answer.remaining,
        )

    def preview(self, answers: Sequence[ClosingAnswer]) -> ClosingPreview:
        check_answers(answers)
        today = self._today()
        planned = list(dict.fromkeys(b.task_id for b in today.work if b.task_id is not None))
        for answer in answers:
            if answer.task_id not in planned:
                raise ApplicationError(f"{answer.task_id} no está en el plan de hoy ({today.day})")
        answered = {answer.task_id for answer in answers}
        not_done = {a.task_id for a in answers if a.kind is AnswerKind.NO}
        return ClosingPreview(
            day=today.day,
            path=today.path,
            changes=tuple(self._change(answer) for answer in answers),
            unanswered=tuple(task_id for task_id in planned if task_id not in answered),
            missed=tuple(b for b in today.work if b.task_id in not_done),
            zone=today.zone,
        )

    # --- Aplicación -------------------------------------------------------------------------

    def _log(self, correlation: str, action: str, target: str, result: AuditResult) -> None:
        self._audit.append(
            AuditRecord(
                ts=self._clock.now(),
                correlation_id=correlation,
                actor=OWNER,
                action=action,
                target=target,
                result=result,
            )
        )

    def _audited(
        self, correlation: str, action: str, target: str, operation: Callable[[], object]
    ) -> None:
        self._log(correlation, action, target, AuditResult.INTENTO)
        try:
            operation()
        except Exception:
            self._log(correlation, action, target, AuditResult.ERROR)
            raise
        self._log(correlation, action, target, AuditResult.OK)

    def apply(self, answers: Sequence[ClosingAnswer]) -> ClosingResult:
        if not answers:
            raise ApplicationError("indica al menos una respuesta (--hecho, --avance, --no…)")
        preview = self.preview(answers)
        correlation = uuid.uuid4().hex
        local = self._clock.now().astimezone(preview.zone)
        at = time(local.hour, local.minute)
        written: list[str] = []
        try:
            for answer in answers:
                checkbox, fields = task_change(answer)
                if checkbox is None and not fields:
                    continue
                self._audited(
                    correlation,
                    "actualizar_tarea",
                    str(answer.task_id),
                    partial(
                        update_task, self._vault, answer.task_id, checkbox=checkbox, fields=fields
                    ),
                )
                written.append(str(answer.task_id))
            entries = summary_entries(preview.changes, preview.unanswered, at)
            self._audited(
                correlation,
                "cierre",
                str(preview.path),
                lambda: append_to_section(
                    self._vault, preview.path, CLOSING_SECTION, CLOSING_HEADING, entries
                ),
            )
            written.append(str(preview.path))
            adjustments = self._coach.record_missed(preview.day, preview.missed)
        except (LifeOSError, LogWriteError) as error:
            if not written:
                raise
            raise ClosingIncompleteError(tuple(written), error) from error
        return ClosingResult(preview, tuple(written), adjustments)
