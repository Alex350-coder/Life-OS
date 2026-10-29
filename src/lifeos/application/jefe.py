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

Replanificación (F4-T04, ADR-0011): una semana aprobada no se reescribe. `replanificar` propone
una revisión `Semanal/AAAA-Wnn-rN.md` desde ahora, con la historia anterior al corte y las
propuestas del Coach; `aprobar` la vuelve a validar y republica solo hoy y los días siguientes.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from lifeos.application.plan_publication import PlanPublication, PlanRejectedError
from lifeos.application.tasks import ApplicationError, TaskQuery
from lifeos.application.tools import ActorContext, ToolRegistry
from lifeos.domain.adjustments import Adjustment, adjustment_entry, parse_adjustments, pending
from lifeos.domain.capacity import local_datetime
from lifeos.domain.errors import ValidationError
from lifeos.domain.heuristic import propose, unplanned_after
from lifeos.domain.horario_note import HORARIO_NOTE, parse_horario
from lifeos.domain.plan import (
    Block,
    Conflict,
    PlanningContext,
    PlanOrigin,
    PlanProposal,
    PlanStatus,
    build_context,
    next_minute,
)
from lifeos.domain.plan_note import (
    MAX_REVISIONS,
    PlanNoteError,
    WeeklyNote,
    daily_path,
    parse_weekly,
    revision_path,
    weekly_path,
)
from lifeos.domain.replan import adjust_context, planning_floor, split_history
from lifeos.domain.schedule import MINUTES_PER_DAY, Schedule
from lifeos.domain.weeks import IsoWeek
from lifeos.ports.clock import Clock
from lifeos.ports.vault import NoteNotFoundError, Snapshot, VaultPath

JEFE_ID = "jefe"


class PlanNotFoundError(ApplicationError):
    """No hay plan propuesto para esa semana."""


class PlanAlreadyApprovedError(ApplicationError):
    """El plan ya está aprobado: no se reemplaza; se revisa con `replanificar` (ADR-0011)."""


class PlanDrafter(Protocol):
    """Genera una propuesta (p. ej., con IA). Siempre pasa después por el validador."""

    def draft(self, context: PlanningContext, *, correlation_id: str) -> PlanProposal: ...


@dataclass(frozen=True, slots=True)
class _Version:
    """Una nota semanal de la cadena: el plan original o una revisión."""

    path: str
    snapshot: Snapshot
    note: WeeklyNote


@dataclass(frozen=True, slots=True)
class _Revision:
    """Datos de una revisión que se vuelven a calcular igual al proponer y al aprobar."""

    history: tuple[Block, ...]
    adjustments: tuple[Adjustment, ...]
    context: PlanningContext


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

    def _chain(self, actor: ActorContext, week: IsoWeek) -> list[_Version]:
        """Plan original y revisiones existentes, en orden (se detiene en la primera que falta)."""
        versions: list[_Version] = []
        paths = [weekly_path(week)] + [revision_path(week, n) for n in range(1, MAX_REVISIONS + 1)]
        missing: str | None = None
        for path in paths:
            snapshot = self._snapshot(actor, path)
            if snapshot is None:
                missing = missing or path
                continue
            if missing is not None:
                # Una revisión tras un hueco no se puede ordenar: no se adivina cuál vale.
                raise ApplicationError(
                    f"falta {missing} pero existe {path}: restaura la nota que falta o archiva "
                    "las posteriores antes de seguir"
                )
            versions.append(_Version(path, snapshot, _note(snapshot)))
        return versions

    def _adjustments(self, actor: ActorContext, week: IsoWeek) -> tuple[Adjustment, ...]:
        """Propuestas del Coach en las notas diarias de la semana (el Jefe lee `Diario/`)."""
        found: list[Adjustment] = []
        for day in week.days():
            try:
                text = self._tools.leer_nota(actor, daily_path(day))
            except NoteNotFoundError:
                continue
            try:
                found += parse_adjustments(text, day)
            except ValidationError as error:
                raise ApplicationError(f"{daily_path(day)}: {error.reason}") from error
        return tuple(found)

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
        chain = self._chain(actor, target)
        if any(v.note.status is PlanStatus.APROBADO for v in chain):
            raise PlanAlreadyApprovedError(
                f"el plan de {target} ya está aprobado: usa 'semana --replanificar'"
            )
        existing = chain[0].snapshot if chain else None
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
        chain = self._chain(actor, target)
        if not chain:
            raise PlanNotFoundError(f"no hay plan propuesto para {target}: usa primero 'semana'")
        latest = chain[-1]
        if latest.note.status is PlanStatus.APROBADO:
            raise PlanAlreadyApprovedError(
                f"el plan de {target} ya está aprobado: usa 'semana --replanificar'"
            )
        if latest.note.revision:
            return self._approve_revision(actor, schedule, target, chain)
        snapshot, note = latest.snapshot, latest.note
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

    # --- Replanificación (ADR-0011) ----------------------------------------------------------

    def _revision_data(
        self,
        actor: ActorContext,
        schedule: Schedule,
        week: IsoWeek,
        base: WeeklyNote,
        *,
        cutoff: datetime,
        now: datetime,
    ) -> _Revision:
        history = split_history(base.blocks, cutoff)
        floor = planning_floor(history, max(cutoff, now))
        everything = pending(
            self._adjustments(actor, week), zone=schedule.zone, after=None, until=cutoff
        )
        # Se aplican todas las propuestas de la semana (un "reducir carga hoy" sigue valiendo en
        # una segunda revisión del mismo día); la nota lista solo las nuevas desde la anterior.
        new = pending(everything, zone=schedule.zone, after=base.cutoff, until=cutoff)
        context = adjust_context(
            self._context(actor, schedule, week, floor),
            schedule,
            history=history,
            adjustments=everything,
        )
        return _Revision(history, new, context)

    def replanificar(
        self, week: IsoWeek | None = None, *, origin: PlanOrigin = PlanOrigin.HEURISTICA
    ) -> PlanOutcome:
        """Propone una revisión de la semana aprobada desde ahora, sin tocar lo aprobado."""
        actor = self.new_actor()
        schedule = self._schedule(actor)
        target = self._week(week, schedule)
        chain = self._chain(actor, target)
        approved = [v for v in chain if v.note.status is PlanStatus.APROBADO]
        if not approved:
            raise ApplicationError(
                f"no hay plan aprobado de {target} que replanificar: usa 'semana' y 'aprobar'"
            )
        base, latest = approved[-1], chain[-1]
        replacing = latest.note.status is PlanStatus.PROPUESTO and latest.note.revision > 0
        number = latest.note.revision if replacing else latest.note.revision + 1
        if number > MAX_REVISIONS:
            raise ApplicationError(f"la semana {target} ya tiene {MAX_REVISIONS} revisiones")
        now = self._clock.now()
        cutoff = next_minute(now)
        week_end = local_datetime(target.days()[-1], MINUTES_PER_DAY, schedule.zone)
        if cutoff >= week_end:
            raise ApplicationError(f"la semana {target} ya terminó: planifica la siguiente")
        data = self._revision_data(actor, schedule, target, base.note, cutoff=cutoff, now=now)
        publication = PlanPublication(
            proposal=self._draft(data.context, origin, actor),
            context=data.context,
            status=PlanStatus.PROPUESTO,
            generated=now,
            weekly_expected=latest.snapshot.version if replacing else None,
            revision=number,
            revises=base.path,
            cutoff=cutoff,
            history=data.history,
            expected_history=data.history,
            adjustments=_rendered(data.adjustments),
        )
        return self._publish(actor, publication)

    def _approve_revision(
        self, actor: ActorContext, schedule: Schedule, week: IsoWeek, chain: list[_Version]
    ) -> PlanOutcome:
        latest = chain[-1]
        note = latest.note
        base = next((v for v in chain if v.path == note.revises), None)
        if base is None or base.note.status is not PlanStatus.APROBADO or note.cutoff is None:
            raise ApplicationError(
                f"{latest.path} no revisa un plan aprobado de la cadena: vuelve a replanificar"
            )
        now = self._clock.now()
        data = self._revision_data(actor, schedule, week, base.note, cutoff=note.cutoff, now=now)
        blocks = note.plan_blocks()
        unplanned, alerts = unplanned_after(data.context, blocks)
        proposal = PlanProposal(
            week=week,
            blocks=blocks,
            unplanned=unplanned,
            justification=note.justification,
            origin=note.origin,
            alerts=alerts,
        )
        publication = PlanPublication(
            proposal=proposal,
            context=data.context,
            status=PlanStatus.APROBADO,
            generated=note.generated,
            weekly_expected=latest.snapshot.version,
            approved_at=now,
            revision=note.revision,
            revises=base.path,
            cutoff=note.cutoff,
            history=note.history(),
            expected_history=data.history,
            adjustments=_rendered(data.adjustments),
        )
        return self._publish(actor, publication)

    def _publish(self, actor: ActorContext, publication: PlanPublication) -> PlanOutcome:
        try:
            paths = self._tools.publicar_plan(actor, publication)
        except PlanRejectedError as rejected:
            return PlanOutcome(publication.proposal, rejected.conflicts, None, None)
        return PlanOutcome(publication.proposal, (), paths[-1], publication.status)


def _rendered(adjustments: tuple[Adjustment, ...]) -> tuple[str, ...]:
    return tuple(f"{a.day} {a.at:%H:%M} · {adjustment_entry(a).text}" for a in adjustments)


def _note(snapshot: Snapshot) -> WeeklyNote:
    try:
        return parse_weekly(snapshot.text)
    except PlanNoteError as error:
        raise ApplicationError(
            f"la nota semanal no es un plan válido; corrígela o archívala: {error}"
        ) from error
