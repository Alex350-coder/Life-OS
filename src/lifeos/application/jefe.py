"""Caso de uso del Jefe (F3-T03, agente-jefe.md, planificador.md "Flujo obligatorio").

1. Lee horario y tareas de la bóveda con sus permisos (herramientas auditadas).
2. Calcula la capacidad (código).
3. Genera la propuesta: heurística determinista o, a petición, un redactor con IA.
4. Valida (código, obligatorio): lo hace la herramienta `publicar_plan` antes de escribir.
5. Si no es viable devuelve los conflictos y no guarda nada.
6. Un plan válido queda `propuesto` hasta que el propietario lo aprueba (MVP).
7. Al aprobar se vuelve a leer la nota, se valida contra la bóveda y la hora actuales y se
   publica el render canónico del plan (ADR-0009).
8. Cada publicación o rechazo queda en la auditoría, con el mismo identificador de correlación
   que el registro de uso del modelo.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from lifeos.application.plan_publication import PlanPublication, PlanRejectedError
from lifeos.application.tasks import ApplicationError, TaskQuery
from lifeos.application.tools import ActorContext, ToolRegistry
from lifeos.domain.heuristic import propose, unplanned_after
from lifeos.domain.horario_note import HORARIO_NOTE, parse_horario
from lifeos.domain.plan import (
    Conflict,
    PlanningContext,
    PlanOrigin,
    PlanProposal,
    PlanStatus,
    build_context,
)
from lifeos.domain.plan_note import PlanNoteError, WeeklyNote, parse_weekly, weekly_path
from lifeos.domain.schedule import Schedule
from lifeos.domain.weeks import IsoWeek
from lifeos.ports.clock import Clock
from lifeos.ports.vault import NoteNotFoundError, Snapshot, VaultPath

JEFE_ID = "jefe"


class PlanNotFoundError(ApplicationError):
    """No hay plan propuesto para esa semana."""


class PlanAlreadyApprovedError(ApplicationError):
    """El plan ya está aprobado: no se reemplaza (la replanificación llega en la Fase 4)."""


class PlanDrafter(Protocol):
    """Genera una propuesta (p. ej., con IA). Siempre pasa después por el validador."""

    def draft(self, context: PlanningContext, *, correlation_id: str) -> PlanProposal: ...


@dataclass(frozen=True, slots=True)
class PlanOutcome:
    proposal: PlanProposal
    conflicts: tuple[Conflict, ...]
    path: VaultPath | None  # None si se rechazó: no se guardó nada
    status: PlanStatus | None

    @property
    def accepted(self) -> bool:
        return self.path is not None


class JefePlanner:
    def __init__(
        self,
        tools: ToolRegistry,
        clock: Clock,
        *,
        drafter: PlanDrafter | None = None,
        configured_zone: str | None = None,
    ) -> None:
        self._tools = tools
        self._clock = clock
        self._drafter = drafter
        self._configured_zone = configured_zone

    @property
    def has_drafter(self) -> bool:
        """Hay redactor con IA (pasarela y clave configuradas)."""
        return self._drafter is not None

    def new_actor(self) -> ActorContext:
        """Contexto del Jefe con un identificador de correlación nuevo para una operación."""
        return ActorContext(agent_id=JEFE_ID, correlation_id=uuid.uuid4().hex)

    # --- Lectura del contexto ---------------------------------------------------------------

    def _schedule(self, actor: ActorContext) -> Schedule:
        try:
            text = self._tools.leer_nota(actor, HORARIO_NOTE)
        except NoteNotFoundError:
            raise ApplicationError(
                f"falta {HORARIO_NOTE}: declara disponibilidad, compromisos y límites (D-13)"
            ) from None
        schedule = parse_horario(text)
        configured = self._configured_zone
        if configured and configured != schedule.zone.key:
            raise ApplicationError(
                f"la zona del horario ({schedule.zone.key}) no coincide con LIFEOS_TIMEZONE "
                f"({configured})"
            )
        return schedule

    def _context(
        self, actor: ActorContext, schedule: Schedule, week: IsoWeek, now: datetime
    ) -> PlanningContext:
        located = self._tools.listar_tareas(actor, TaskQuery())
        return build_context(schedule, week, (item.task for item in located), now=now)

    def context(self, week: IsoWeek, *, now: datetime | None = None) -> PlanningContext:
        """Contexto de planificación tal como lo vería ahora el Jefe."""
        actor = self.new_actor()
        return self._context(actor, self._schedule(actor), week, now or self._clock.now())

    def _week(self, week: IsoWeek | None, schedule: Schedule) -> IsoWeek:
        if week is not None:
            return week
        return IsoWeek.containing(self._clock.now().astimezone(schedule.zone).date())

    def _snapshot(self, actor: ActorContext, path: str) -> Snapshot | None:
        try:
            return self._tools.leer_nota_versionada(actor, path)
        except NoteNotFoundError:
            return None

    # --- Propuesta ------------------------------------------------------------------------

    def _draft(
        self, context: PlanningContext, origin: PlanOrigin, actor: ActorContext
    ) -> PlanProposal:
        if origin is PlanOrigin.HEURISTICA:
            return propose(context)
        if self._drafter is None:
            raise ApplicationError("la propuesta con IA no está configurada (falta la pasarela)")
        return self._drafter.draft(context, correlation_id=actor.correlation_id)

    def proponer(
        self, week: IsoWeek | None = None, *, origin: PlanOrigin = PlanOrigin.HEURISTICA
    ) -> PlanOutcome:
        actor = self.new_actor()
        schedule = self._schedule(actor)
        target = self._week(week, schedule)
        existing = self._snapshot(actor, weekly_path(target))
        if existing is not None and _note(existing).status is PlanStatus.APROBADO:
            raise PlanAlreadyApprovedError(f"el plan de {target} ya está aprobado")
        now = self._clock.now()
        context = self._context(actor, schedule, target, now)
        publication = PlanPublication(
            proposal=self._draft(context, origin, actor),
            context=context,
            status=PlanStatus.PROPUESTO,
            generated=now,
            weekly_expected=existing.version if existing else None,
        )
        return self._publish(actor, publication)

    # --- Aprobación -----------------------------------------------------------------------

    def aprobar(self, week: IsoWeek | None = None) -> PlanOutcome:
        actor = self.new_actor()
        schedule = self._schedule(actor)
        target = self._week(week, schedule)
        snapshot = self._snapshot(actor, weekly_path(target))
        if snapshot is None:
            raise PlanNotFoundError(f"no hay plan propuesto para {target}: usa primero 'semana'")
        note = _note(snapshot)
        if note.status is PlanStatus.APROBADO:
            raise PlanAlreadyApprovedError(f"el plan de {target} ya está aprobado")
        now = self._clock.now()
        # Revalidación con la bóveda y la hora actuales: un plan con bloques ya pasados (o con
        # una fecha de generación editada) no se aprueba; hay que volver a proponerlo.
        context = self._context(actor, schedule, target, now)
        unplanned, alerts = unplanned_after(context, note.blocks)
        proposal = PlanProposal(
            week=target,
            blocks=note.blocks,
            unplanned=unplanned,
            justification=note.justification,
            origin=note.origin,
            alerts=alerts,
        )
        publication = PlanPublication(
            proposal=proposal,
            context=context,
            status=PlanStatus.APROBADO,
            generated=note.generated,
            weekly_expected=snapshot.version,
            approved_at=now,
        )
        return self._publish(actor, publication)

    def _publish(self, actor: ActorContext, publication: PlanPublication) -> PlanOutcome:
        try:
            paths = self._tools.publicar_plan(actor, publication)
        except PlanRejectedError as rejected:
            return PlanOutcome(publication.proposal, rejected.conflicts, None, None)
        return PlanOutcome(publication.proposal, (), paths[-1], publication.status)


def _note(snapshot: Snapshot) -> WeeklyNote:
    try:
        return parse_weekly(snapshot.text)
    except PlanNoteError as error:
        raise ApplicationError(
            f"la nota semanal no es un plan válido; corrígela o archívala: {error}"
        ) from error
