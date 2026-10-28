"""Caso de uso del Jefe (F3-T03, agente-jefe.md, planificador.md "Flujo obligatorio").

1. Lee horario y tareas de la bóveda con sus permisos (herramientas auditadas).
2. Calcula la capacidad (código).
3. Genera la propuesta: heurística determinista o, a petición, un redactor con IA.
4. Valida (código, obligatorio): lo hace la herramienta `publicar_plan` antes de escribir.
5. Si no es viable devuelve los conflictos y no guarda nada.
6. Un plan válido queda `propuesto` hasta que el propietario lo aprueba (MVP).
7. Al aprobar se vuelve a leer y a validar contra el estado actual y se publica.
8. Cada publicación o rechazo queda en la auditoría.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from lifeos.application.plan_publication import NoteWrite, PlanPublication, PlanRejectedError
from lifeos.application.tasks import ApplicationError, TaskQuery
from lifeos.application.tools import ActorContext, ToolRegistry
from lifeos.domain.heuristic import propose
from lifeos.domain.horario_note import HORARIO_NOTE, parse_horario
from lifeos.domain.plan import (
    Conflict,
    PlanningContext,
    PlanOrigin,
    PlanProposal,
    PlanStatus,
    build_context,
)
from lifeos.domain.plan_note import (
    PlanNoteError,
    approve_weekly,
    daily_path,
    parse_weekly,
    render_daily,
    render_weekly,
    weekly_path,
)
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

    def draft(self, context: PlanningContext) -> PlanProposal: ...


@dataclass(frozen=True, slots=True)
class PlanOutcome:
    proposal: PlanProposal
    conflicts: tuple[Conflict, ...]
    path: VaultPath | None
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

    @property
    def actor(self) -> ActorContext:
        """Contexto del Jefe con un identificador de correlación nuevo por operación."""
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
        actor = self.actor
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

    def _draft(self, context: PlanningContext, origin: PlanOrigin) -> PlanProposal:
        if origin is PlanOrigin.HEURISTICA:
            return propose(context)
        if self._drafter is None:
            raise ApplicationError("la propuesta con IA no está configurada (falta la pasarela)")
        return self._drafter.draft(context)

    def proponer(
        self, week: IsoWeek | None = None, *, origin: PlanOrigin = PlanOrigin.HEURISTICA
    ) -> PlanOutcome:
        actor = self.actor
        schedule = self._schedule(actor)
        target = self._week(week, schedule)
        path = weekly_path(target)
        existing = self._snapshot(actor, path)
        if existing is not None and _status_of(existing) is PlanStatus.APROBADO:
            raise PlanAlreadyApprovedError(f"el plan de {target} ya está aprobado")
        now = self._clock.now()
        context = self._context(actor, schedule, target, now)
        proposal = self._draft(context, origin)
        text = render_weekly(proposal, context, status=PlanStatus.PROPUESTO, generated=now)
        weekly = (VaultPath.parse(path), text, existing.version if existing else None)
        return self._publish(
            actor, PlanPublication(proposal, context, weekly), PlanStatus.PROPUESTO
        )

    # --- Aprobación -----------------------------------------------------------------------

    def aprobar(self, week: IsoWeek | None = None) -> PlanOutcome:
        actor = self.actor
        schedule = self._schedule(actor)
        target = self._week(week, schedule)
        path = weekly_path(target)
        snapshot = self._snapshot(actor, path)
        if snapshot is None:
            raise PlanNotFoundError(f"no hay plan propuesto para {target}: usa primero 'semana'")
        try:
            note = parse_weekly(snapshot.text)
        except PlanNoteError as error:
            raise ApplicationError(f"{path} no es un plan válido: {error}") from error
        if note.status is PlanStatus.APROBADO:
            raise PlanAlreadyApprovedError(f"el plan de {target} ya está aprobado")
        # Se valida contra el estado actual de la bóveda, desde el momento de la propuesta.
        context = self._context(actor, schedule, target, note.generated)
        proposal = PlanProposal(week=target, blocks=note.blocks, origin=note.origin)
        weekly = (
            VaultPath.parse(path),
            approve_weekly(snapshot.text, approved_at=self._clock.now()),
            snapshot.version,
        )
        daily = self._daily_notes(actor, proposal, context)
        return self._publish(
            actor, PlanPublication(proposal, context, weekly, daily), PlanStatus.APROBADO
        )

    def _daily_notes(
        self, actor: ActorContext, proposal: PlanProposal, context: PlanningContext
    ) -> tuple[NoteWrite, ...]:
        days = sorted({context.local_day(block.interval.start) for block in proposal.blocks})
        writes = []
        for day in days:
            path = daily_path(day)
            existing = self._snapshot(actor, path)
            text = render_daily(
                day,
                proposal.blocks,
                zone=context.zone,
                week=proposal.week,
                existing=existing.text if existing else None,
            )
            writes.append((VaultPath.parse(path), text, existing.version if existing else None))
        return tuple(writes)

    def _publish(
        self, actor: ActorContext, publication: PlanPublication, status: PlanStatus
    ) -> PlanOutcome:
        try:
            self._tools.publicar_plan(actor, publication)
        except PlanRejectedError as rejected:
            return PlanOutcome(publication.proposal, rejected.conflicts, None, None)
        return PlanOutcome(publication.proposal, (), publication.weekly[0], status)


def _status_of(snapshot: Snapshot) -> PlanStatus:
    try:
        return parse_weekly(snapshot.text).status
    except PlanNoteError as error:
        raise ApplicationError(
            f"la nota semanal existente no es válida; corrígela o archívala: {error}"
        ) from error
