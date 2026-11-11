"""Revisión de las propuestas de los especialistas por el propietario (F8-T02, RULES C.3, C.5).

Solo el propietario acepta o rechaza. Aceptar aplica el cambio con los casos de uso normales:
un cambio de prioridad edita esa tarea (abierta y del área de la propuesta) y una tarea nueva
se crea en `<carpeta del área>/tareas.md`. Después se marca el elemento en la nota, con control
de versión. El Jefe planifica con las tareas tal como quedan: solo lo aceptado le llega.

Si el cambio se aplica pero la marca no se puede guardar, el error lo dice para no repetirlo.
"""

import uuid
from dataclasses import dataclass

from lifeos.application.tasks import (
    DEFAULT_PROTECTED_ROOTS,
    PROPOSALS_ROOT,
    ApplicationError,
    TaskNotFoundError,
    create_task,
    find_task,
    update_task,
)
from lifeos.domain.area_proposals import (
    ProposalItem,
    ProposalKind,
    ProposalRef,
    ProposalStatus,
    parse_proposal_note,
    proposal_area,
    with_status,
)
from lifeos.domain.areas import area_folder
from lifeos.domain.errors import LifeOSError, ValidationError
from lifeos.domain.ids import TaskId
from lifeos.domain.task import Area, Priority, TaskStatus
from lifeos.domain.weeks import IsoWeek
from lifeos.ports.clock import Clock
from lifeos.ports.logs import AuditLog, AuditRecord, AuditResult
from lifeos.ports.vault import NoteNotFoundError, VaultPath, VaultStore, Version

OWNER_ACTOR = "propietario"
NEW_TASKS_NOTE = "tareas.md"
_NOTE_DEPTH = 3
_CLOSED = frozenset({TaskStatus.COMPLETADA, TaskStatus.CANCELADA})


class ProposalReviewError(ApplicationError):
    """La propuesta no se puede aplicar tal como está; el mensaje dice por qué."""


class ProposalAppliedButUnmarkedError(ApplicationError):
    """El cambio se aplicó pero la nota no se pudo marcar: no repetir la aceptación."""


@dataclass(frozen=True, slots=True)
class ReviewItem:
    ref: ProposalRef
    area: Area | None
    item: ProposalItem
    task_title: str | None  # tarea afectada por un cambio de prioridad, si existe
    current_priority: Priority | None


@dataclass(frozen=True, slots=True)
class AcceptResult:
    ref: ProposalRef
    task_id: TaskId | None  # tarea creada o cambiada


class ProposalReview:
    def __init__(
        self,
        vault: VaultStore,
        audit: AuditLog,
        clock: Clock,
        *,
        protected: tuple[VaultPath, ...] = DEFAULT_PROTECTED_ROOTS,
    ) -> None:
        self._vault = vault
        self._audit = audit
        self._clock = clock
        self._protected = protected

    # --- Lectura --------------------------------------------------------------------------------

    def _notes(self, week: IsoWeek | None) -> tuple[VaultPath, ...]:
        prefix = PROPOSALS_ROOT.join(VaultPath(str(week))) if week else PROPOSALS_ROOT
        try:
            notes = self._vault.list_notes(prefix)
        except NoteNotFoundError:
            return ()
        return tuple(sorted((n for n in notes if n.name.endswith(".md")), key=lambda n: n.value))

    def _review_item(self, path: VaultPath, area: Area | None, item: ProposalItem) -> ReviewItem:
        parts = path.parts
        ref = ProposalRef(IsoWeek.parse(parts[-2]), parts[-1].removesuffix(".md"), item.key)
        title = priority = None
        if item.task_id is not None:
            try:
                task = find_task(self._vault, item.task_id).task
                title, priority = task.title, task.priority
            except LifeOSError:
                pass  # la tarea pudo desaparecer: aceptar lo dirá con un error claro
        return ReviewItem(ref, area, item, title, priority)

    def pending(self, week: IsoWeek | None = None) -> tuple[ReviewItem, ...]:
        """Elementos pendientes de una semana (o de todas), con el contexto de su tarea."""
        found = []
        for path in self._notes(week):
            if len(path.parts) != _NOTE_DEPTH:  # Propuestas/<semana>/<nota>.md
                continue
            try:
                IsoWeek.parse(path.parts[1])
                text = self._vault.read(path).text
            except (LifeOSError, ValueError):
                continue
            area = proposal_area(text)
            found += [
                self._review_item(path, area, item)
                for item in parse_proposal_note(text)
                if item.status is ProposalStatus.PENDIENTE
            ]
        return tuple(found)

    # --- Escritura ------------------------------------------------------------------------------

    def _log(
        self,
        correlation: str,
        action: str,
        ref: ProposalRef,
        result: AuditResult,
        reason: str | None = None,
    ) -> None:
        self._audit.append(
            AuditRecord(
                self._clock.now(), correlation, OWNER_ACTOR, action, ref.path, result, reason
            )
        )

    def _load(self, ref: ProposalRef) -> tuple[str, Version, ProposalItem, Area | None]:
        try:
            snapshot = self._vault.read(VaultPath(ref.path))
        except NoteNotFoundError:
            raise ProposalReviewError(f"no existe la nota {ref.path}") from None
        items = {item.key: item for item in parse_proposal_note(snapshot.text)}
        item = items.get(ref.key)
        if item is None:
            raise ValidationError("propuesta", ref.key, "no existe en la nota")
        if item.status is not ProposalStatus.PENDIENTE:
            raise ValidationError("propuesta", ref.key, f"ya está {item.status}")
        return snapshot.text, snapshot.version, item, proposal_area(snapshot.text)

    def _apply(self, item: ProposalItem, area: Area | None) -> TaskId | None:
        if area is None:
            raise ProposalReviewError("la nota no declara un área con especialista válida")
        if item.kind is ProposalKind.PRIORIDAD:
            return self._change_priority(item, area)
        if item.title is None or item.effort is None:  # el dominio ya lo impide
            raise ProposalReviewError("la tarea nueva está incompleta")
        path = VaultPath(area_folder(area)).join(VaultPath(NEW_TASKS_NOTE))
        created = create_task(
            self._vault,
            path,
            title=item.title,
            area=area,
            effort=item.effort,
            due=item.due,
            priority=item.priority,
            protected=self._protected,
        )
        return created.task.id

    def _change_priority(self, item: ProposalItem, area: Area) -> TaskId | None:
        task_id = item.task_id
        if task_id is None:
            raise ProposalReviewError("el cambio de prioridad no indica la tarea")
        try:
            located = find_task(self._vault, task_id)
        except TaskNotFoundError:
            raise ProposalReviewError(f"la tarea {task_id} ya no existe; recházala") from None
        if located.task.area is not area:
            raise ProposalReviewError(f"la tarea {task_id} no es del área {area}")
        if located.task.status in _CLOSED or located.archived:
            raise ProposalReviewError(f"la tarea {task_id} está cerrada; recházala")
        update_task(self._vault, task_id, fields={"prioridad": item.priority.value})
        return task_id

    def _mark(self, ref: ProposalRef, text: str, version: Version, status: ProposalStatus) -> None:
        self._vault.write(
            VaultPath(ref.path),
            with_status(text, ref.key, status),
            expected=version,
        )

    def accept(self, ref: ProposalRef) -> AcceptResult:
        correlation = uuid.uuid4().hex
        action = "propuesta_aceptar"
        text, version, item, area = self._load(ref)
        self._log(correlation, action, ref, AuditResult.INTENTO)
        try:
            task_id = self._apply(item, area)
        except LifeOSError as error:
            self._log(correlation, action, ref, AuditResult.RECHAZADO, type(error).__name__)
            raise
        try:
            self._mark(ref, text, version, ProposalStatus.ACEPTADA)
        except LifeOSError as error:
            self._log(correlation, action, ref, AuditResult.ERROR, "sin_marcar")
            raise ProposalAppliedButUnmarkedError(
                f"se aplicó {ref} pero no se pudo marcar como aceptada: márcala a mano"
            ) from error
        self._log(correlation, action, ref, AuditResult.OK, str(task_id) if task_id else None)
        return AcceptResult(ref, task_id)

    def reject(self, ref: ProposalRef) -> None:
        correlation = uuid.uuid4().hex
        action = "propuesta_rechazar"
        text, version, _, _ = self._load(ref)
        self._log(correlation, action, ref, AuditResult.INTENTO)
        try:
            self._mark(ref, text, version, ProposalStatus.RECHAZADA)
        except LifeOSError as error:
            self._log(correlation, action, ref, AuditResult.ERROR, type(error).__name__)
            raise
        self._log(correlation, action, ref, AuditResult.OK)
