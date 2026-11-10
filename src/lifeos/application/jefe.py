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
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from lifeos.application.calendar_tools import CalendarTools
from lifeos.application.plan_publication import PlanPublication, PlanRejectedError
from lifeos.application.tasks import ApplicationError, TaskQuery
from lifeos.application.tools import ActorContext, ToolRegistry
from lifeos.domain.adaptive_load import WINDOW_DAYS, LoadAdvice, advise, closing_score
from lifeos.domain.adjustments import (
    Adjustment,
    adjustment_entry,
    matching_plan,
    parse_adjustments,
    pending,
)
from lifeos.domain.capacity import local_datetime, week_capacity
from lifeos.domain.errors import ValidationError
from lifeos.domain.external import ExternalBusy, first_overlap
from lifeos.domain.heuristic import propose, unplanned_after
from lifeos.domain.horario_note import HORARIO_NOTE, parse_horario
from lifeos.domain.load import DayLoad, day_loads
from lifeos.domain.plan import (
    Block,
    BlockKind,
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
    parse_daily_blocks,
    parse_weekly,
    revision_path,
    weekly_path,
)
from lifeos.domain.replan import adjust_context, planning_floor, split_history
from lifeos.domain.schedule import MINUTES_PER_DAY, Schedule
from lifeos.domain.weeks import IsoWeek
from lifeos.ports.calendar import CalendarError
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
    warning: str | None = None  # el calendario externo no respondió (solo al proponer)


@dataclass(frozen=True, slots=True)
class PlanOutcome:
    proposal: PlanProposal
    conflicts: tuple[Conflict, ...]
    path: VaultPath | None  # None si se rechazó: no se guardó nada
    status: PlanStatus | None
    zone: ZoneInfo  # zona del horario con la que se calculó el plan

    @property
    def accepted(self) -> bool:
        return self.path is not None


@dataclass(frozen=True, slots=True)
class WeekView:
    """Último plan de la cadena de una semana y su carga por día (solo lectura, F5-T02)."""

    week: IsoWeek
    zone: ZoneInfo
    path: str | None  # None si la semana no tiene plan
    status: PlanStatus | None
    origin: PlanOrigin | None
    revision: int
    generated: datetime | None
    justification: str
    blocks: tuple[Block, ...]
    load: tuple[DayLoad, ...]
    versions: int  # plan original más revisiones


@dataclass(frozen=True, slots=True)
class ExternalView:
    """Intervalos ocupados externos de la semana y los bloques del plan vigente que pisan
    (F7-T02). Solo lectura."""

    week: IsoWeek
    zone: ZoneInfo
    path: str | None  # plan vigente, si existe
    status: PlanStatus | None
    external: tuple[ExternalBusy, ...]
    clashes: tuple[tuple[Block, ExternalBusy], ...]


class JefePlanner:
    def __init__(
        self,
        tools: ToolRegistry,
        clock: Clock,
        *,
        drafter: PlanDrafter | None = None,
        configured_zone: str | None = None,
        calendar: CalendarTools | None = None,
    ) -> None:
        self._tools = tools
        self._clock = clock
        self._drafter = drafter
        self._configured_zone = configured_zone
        self._calendar = calendar

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

    def _external(
        self, actor: ActorContext, schedule: Schedule, week: IsoWeek, *, strict: bool
    ) -> tuple[tuple[ExternalBusy, ...], str | None]:
        """Intervalos ocupados del calendario externo (ADR-0017).

        Al proponer, un calendario que no responde deja una alerta y el plan sigue sin esos
        eventos. Al aprobar (`strict`) falla cerrado: no se aprueba sin comprobarlos."""
        if self._calendar is None or not self._calendar.can_read:
            return (), None
        days = week.days()
        start = local_datetime(days[0], 0, schedule.zone)
        end = local_datetime(days[-1], MINUTES_PER_DAY, schedule.zone)
        try:
            return self._calendar.busy(actor, start, end), None
        except CalendarError as error:
            if strict:
                raise ApplicationError(
                    f"{error}; la aprobación necesita comprobar los eventos externos: reinténtalo "
                    "o desconecta el calendario con 'lifeos-calendario revocar'"
                ) from None
            return (), f"Google Calendar no disponible ({error}): no se comprobaron los eventos"

    def _context(
        self,
        actor: ActorContext,
        schedule: Schedule,
        week: IsoWeek,
        now: datetime,
        external: tuple[ExternalBusy, ...] = (),
    ) -> PlanningContext:
        located = self._tools.listar_tareas(actor, TaskQuery())
        return build_context(
            schedule, week, (item.task for item in located), now=now, external=external
        )

    def context(self, week: IsoWeek, *, now: datetime | None = None) -> PlanningContext:
        """Contexto de planificación tal como lo vería ahora el Jefe."""
        actor = self.new_actor()
        schedule = self._schedule(actor)
        external, _warning = self._external(actor, schedule, week, strict=False)
        return self._context(actor, schedule, week, now or self._clock.now(), external)

    def _load_advice(self, actor: ActorContext, schedule: Schedule, now: datetime) -> LoadAdvice:
        """Cierres de los últimos días (el Jefe ya lee `Diario/`), para la carga adaptativa."""
        today = now.astimezone(schedule.zone).date()
        scores = []
        for offset in range(WINDOW_DAYS):
            try:
                text = self._tools.leer_nota(actor, daily_path(today - timedelta(days=offset)))
            except NoteNotFoundError:
                continue
            score = closing_score(text)
            if score is not None:
                scores.append(score)
        return advise(schedule.limits.focus_max, scores)

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

    def _adjustments(
        self, actor: ActorContext, week: IsoWeek, zone: ZoneInfo
    ) -> tuple[Adjustment, ...]:
        """Propuestas del Coach en las notas diarias de la semana (el Jefe lee `Diario/`).

        DT-11: un "bloque perdido" que no coincide con un bloque de trabajo de esa nota (tarea y
        horario) no se aplica: nunca se replanifica trabajo que no estaba en el plan."""
        found: list[Adjustment] = []
        for day in week.days():
            try:
                text = self._tools.leer_nota(actor, daily_path(day))
            except NoteNotFoundError:
                continue
            try:
                proposals = parse_adjustments(text, day)
                blocks = parse_daily_blocks(text, day, zone)
            except ValidationError as error:
                raise ApplicationError(f"{daily_path(day)}: {error.reason}") from error
            except PlanNoteError as error:
                raise ApplicationError(f"{daily_path(day)}: {error}") from error
            kept, _dropped = matching_plan(proposals, blocks, zone)
            found += kept
        return tuple(found)

    # --- Vista (solo lectura) ----------------------------------------------------------------

    def semana(self, week: IsoWeek | None = None) -> WeekView:
        """Plan vigente de la semana tal como está en la bóveda, sin escribir nada."""
        actor = self.new_actor()
        schedule = self._schedule(actor)
        target = self._week(week, schedule)
        chain = self._chain(actor, target)
        latest = chain[-1] if chain else None
        blocks = latest.note.blocks if latest else ()
        return WeekView(
            week=target,
            zone=schedule.zone,
            path=latest.path if latest else None,
            status=latest.note.status if latest else None,
            origin=latest.note.origin if latest else None,
            revision=latest.note.revision if latest else 0,
            generated=latest.note.generated if latest else None,
            justification=latest.note.justification if latest else "",
            blocks=blocks,
            load=day_loads(week_capacity(schedule, target), blocks, schedule.zone),
            versions=len(chain),
        )

    def eventos_externos(self, week: IsoWeek | None = None) -> ExternalView:
        """Disponibilidad externa y choques con el plan vigente, sin escribir nada."""
        if self._calendar is None or not self._calendar.can_read:
            raise ApplicationError(
                "Google Calendar no está conectado para lectura: usa "
                "'lifeos-calendario conectar --lectura'"
            )
        actor = self.new_actor()
        schedule = self._schedule(actor)
        target = self._week(week, schedule)
        external, _ = self._external(actor, schedule, target, strict=True)
        view = self.semana(target)
        clashes = tuple(
            (block, hit)
            for block in view.blocks
            if block.kind is BlockKind.TRABAJO
            and (hit := first_overlap(block.interval, external)) is not None
        )
        return ExternalView(target, schedule.zone, view.path, view.status, external, clashes)

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
        advice = self._load_advice(actor, schedule, now)
        # DT-01: solo la propuesta usa el foco ajustado. La aprobación valida contra el límite del
        # propietario, que es un techo: un plan más ligero siempre lo cumple.
        adapted = replace(schedule, limits=replace(schedule.limits, focus_max=advice.focus_max))
        external, warning = self._external(actor, schedule, target, strict=False)
        context = self._context(actor, adapted, target, now, external)
        proposal = _with_alert(self._draft(context, origin, actor), warning)
        justification = "; ".join(p for p in (proposal.justification, advice.reason) if p)
        publication = PlanPublication(
            proposal=replace(proposal, justification=justification),
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
        external, _ = self._external(actor, schedule, target, strict=True)
        context = self._context(actor, schedule, target, now, external)
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
        strict: bool,
    ) -> _Revision:
        history = split_history(base.blocks, cutoff)
        floor = planning_floor(history, max(cutoff, now))
        everything = pending(
            self._adjustments(actor, week, schedule.zone),
            zone=schedule.zone,
            after=None,
            until=cutoff,
        )
        # Se aplican todas las propuestas de la semana (un "reducir carga hoy" sigue valiendo en
        # una segunda revisión del mismo día); la nota lista solo las nuevas desde la anterior.
        new = pending(everything, zone=schedule.zone, after=base.cutoff, until=cutoff)
        external, warning = self._external(actor, schedule, week, strict=strict)
        context = adjust_context(
            self._context(actor, schedule, week, floor, external),
            schedule,
            history=history,
            adjustments=everything,
        )
        return _Revision(history, new, context, warning)

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
        data = self._revision_data(
            actor, schedule, target, base.note, cutoff=cutoff, now=now, strict=False
        )
        publication = PlanPublication(
            proposal=_with_alert(self._draft(data.context, origin, actor), data.warning),
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
        data = self._revision_data(
            actor, schedule, week, base.note, cutoff=note.cutoff, now=now, strict=True
        )
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
            return PlanOutcome(
                publication.proposal, rejected.conflicts, None, None, publication.context.zone
            )
        return PlanOutcome(
            publication.proposal, (), paths[-1], publication.status, publication.context.zone
        )


def _with_alert(proposal: PlanProposal, warning: str | None) -> PlanProposal:
    if warning is None:
        return proposal
    return replace(proposal, alerts=(*proposal.alerts, warning))


def _rendered(adjustments: tuple[Adjustment, ...]) -> tuple[str, ...]:
    return tuple(f"{a.day} {a.at:%H:%M} · {adjustment_entry(a).text}" for a in adjustments)


def _note(snapshot: Snapshot) -> WeeklyNote:
    try:
        return parse_weekly(snapshot.text)
    except PlanNoteError as error:
        raise ApplicationError(
            f"la nota semanal no es un plan válido; corrígela o archívala: {error}"
        ) from error
