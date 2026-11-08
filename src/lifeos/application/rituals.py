"""Ejecutor de rituales (F6-T01, F6-T02; ADR-0014, spec rituales-y-notificaciones).

En cada vuelta revisa las ocurrencias de ayer y de hoy. Una ocurrencia se ejecuta una sola vez:
cuando llega su hora (o el final del silencio que la aplaza) y el propietario no está en
silencio. Pasado su margen se omite. Cada resultado se marca primero en memoria y después se
guarda en el estado y se audita: si el disco falla, lo ya enviado no se repite en este proceso y
el fallo se informa al terminar la vuelta, después de atender las demás ocurrencias.
"""

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from lifeos.application import ritual_messages as messages
from lifeos.application.coach import CoachSession, NoDailyPlanError
from lifeos.application.jefe import JefePlanner
from lifeos.application.tasks import ApplicationError
from lifeos.domain.capacity import day_capacity
from lifeos.domain.errors import LifeOSError
from lifeos.domain.horario_note import HORARIO_NOTE, parse_horario
from lifeos.domain.plan import Block
from lifeos.domain.plan_note import weekly_path
from lifeos.domain.quiet import DeliveryKind, QuietWindow, is_quiet, plan_delivery, quiet_windows
from lifeos.domain.rituals import (
    RITUALES_NOTE,
    Occurrence,
    RitualConfig,
    RitualKind,
    parse_rituales,
    reminder_occurrences,
    ritual_occurrences,
)
from lifeos.domain.schedule import Schedule
from lifeos.domain.weeks import IsoWeek
from lifeos.ports.clock import Clock
from lifeos.ports.logs import AuditLog, AuditRecord, AuditResult
from lifeos.ports.notify import Notifier
from lifeos.ports.state import RitualMark, RitualStateStore, RitualStatus
from lifeos.ports.vault import NoteNotFoundError, VaultPath, VaultStore

MAX_ATTEMPTS = 5
# Espera antes de cada reintento (unos 15 min en total): un corte breve no agota los intentos.
RETRY_DELAYS = tuple(timedelta(minutes=m) for m in (1, 2, 4, 8))
STATE_RETENTION = timedelta(days=7)
SYSTEM_ACTOR = "sistema"
AUDIT_ACTION = "ritual"
SKIPPED_REASON = "omitido: fuera de su margen"
_AUDIT_RESULT = {RitualStatus.HECHO: AuditResult.OK, RitualStatus.FALLIDO: AuditResult.ERROR}


@dataclass(frozen=True, slots=True)
class RitualReport:
    key: str
    status: RitualStatus
    reason: str | None


@dataclass(frozen=True, slots=True)
class _Day:
    day: date
    windows: tuple[QuietWindow, ...]
    occurrences: tuple[Occurrence, ...]


@dataclass(slots=True)
class _Turn:
    now: datetime
    marks: dict[str, RitualMark]
    now_windows: tuple[QuietWindow, ...]
    failures: list[LifeOSError] = field(default_factory=list)


def _retryable(mark: RitualMark, now: datetime) -> bool:
    if mark.status is not RitualStatus.FALLIDO or mark.attempts >= MAX_ATTEMPTS:
        return False
    delay = RETRY_DELAYS[min(max(mark.attempts, 1), len(RETRY_DELAYS)) - 1]
    return now >= mark.updated + delay


class RitualRunner:
    def __init__(
        self,
        vault: VaultStore,
        jefe: JefePlanner,
        coach: CoachSession,
        notifier: Notifier,
        state: RitualStateStore,
        audit: AuditLog,
        clock: Clock,
        *,
        configured_zone: str | None = None,
    ) -> None:
        self._vault = vault
        self._jefe = jefe
        self._coach = coach
        self._notifier = notifier
        self._state = state
        self._audit = audit
        self._clock = clock
        self._configured_zone = configured_zone
        self._memory: dict[str, RitualMark] = {}  # marcas de este proceso (ver docstring)

    # --- Lectura de la configuración ----------------------------------------------------------

    def _read(self, path: str) -> str:
        try:
            return self._vault.read(VaultPath.parse(path)).text
        except NoteNotFoundError:
            raise ApplicationError(f"falta {path}: los rituales lo necesitan") from None

    def _schedule(self) -> Schedule:
        schedule = parse_horario(self._read(HORARIO_NOTE))
        if self._configured_zone and self._configured_zone != schedule.zone.key:
            raise ApplicationError(
                f"la zona del horario ({schedule.zone.key}) no coincide con LIFEOS_TIMEZONE "
                f"({self._configured_zone})"
            )
        return schedule

    def _work_blocks(self, day: date) -> tuple[Block, ...]:
        try:
            briefing = self._coach.briefing(day)
        except LifeOSError:
            # Sin plan o con la nota ilegible no hay recordatorios; los demás rituales siguen y el
            # briefing explicará el problema.
            return ()
        return briefing.work if briefing.approved and briefing.readable else ()

    def _day(self, schedule: Schedule, config: RitualConfig, day: date, *, today: bool) -> _Day:
        zone = schedule.zone
        windows = quiet_windows(day_capacity(schedule, day), config.quiet_for(day), day, zone)
        occurrences = ritual_occurrences(config, day, zone)
        if today:  # los recordatorios de ayer ya pasaron su plazo: no hace falta revisarlos
            occurrences += reminder_occurrences(config, day, self._work_blocks(day))
        return _Day(day, windows, tuple(sorted(occurrences, key=lambda o: o.at)))

    # --- Vuelta -------------------------------------------------------------------------------

    def tick(self) -> tuple[RitualReport, ...]:
        now = self._clock.now()
        schedule = self._schedule()
        config = parse_rituales(self._read(RITUALES_NOTE))
        today = now.astimezone(schedule.zone).date()
        current = self._day(schedule, config, today, today=True)
        previous = self._day(schedule, config, today - timedelta(days=1), today=False)
        turn = _Turn(now, {**self._state.load(), **self._memory}, current.windows)
        reports = [
            report
            for day in (previous, current)
            for occurrence in day.occurrences
            if (report := self._step(occurrence, day.windows, turn)) is not None
        ]
        self._prune(turn)
        if turn.failures:  # las demás ocurrencias ya se atendieron: se informa el primer fallo
            raise turn.failures[0]
        return tuple(reports)

    def _prune(self, turn: _Turn) -> None:
        limit = turn.now - STATE_RETENTION
        kept = {key: mark for key, mark in turn.marks.items() if mark.updated >= limit}
        self._memory = {key: mark for key, mark in self._memory.items() if key in kept}
        if kept.keys() != turn.marks.keys():
            try:
                self._state.save(kept)
            except LifeOSError as error:
                turn.failures.append(error)

    def _step(
        self, occurrence: Occurrence, windows: tuple[QuietWindow, ...], turn: _Turn
    ) -> RitualReport | None:
        mark = turn.marks.get(occurrence.key)
        if mark is not None and not _retryable(mark, turn.now):
            return None
        if turn.now < occurrence.at:
            return None
        delivery = plan_delivery(occurrence.at, occurrence.deadline, windows)
        if delivery.kind is DeliveryKind.DESCARTAR:
            reason = f"descartado: {delivery.reason}"
            return self._record(occurrence, RitualStatus.DESCARTADO, reason, turn)
        if turn.now > occurrence.deadline:
            return self._record(occurrence, RitualStatus.OMITIDO, SKIPPED_REASON, turn)
        if delivery.at is None or turn.now < delivery.at or is_quiet(turn.now, turn.now_windows):
            return None  # aplazado, o recuperación tardía mientras el propietario descansa
        attempts = (mark.attempts if mark else 0) + 1
        try:
            text = self._compose(occurrence, retry=mark is not None)
            if text is not None:
                self._notifier.send(text)
        except LifeOSError as error:
            reason = f"{type(error).__name__}: {error}"
            return self._record(occurrence, RitualStatus.FALLIDO, reason, turn, attempts)
        return self._record(occurrence, RitualStatus.HECHO, None, turn, attempts)

    def _record(
        self,
        occurrence: Occurrence,
        status: RitualStatus,
        reason: str | None,
        turn: _Turn,
        attempts: int = 0,
    ) -> RitualReport:
        mark = RitualMark(status, attempts, turn.now, reason)
        turn.marks[occurrence.key] = mark
        self._memory[occurrence.key] = mark  # primero en memoria: nada se repite en este proceso
        try:
            self._state.save(turn.marks)
        except LifeOSError as error:
            turn.failures.append(error)
        try:
            self._audit.append(
                AuditRecord(
                    ts=turn.now,
                    correlation_id=uuid.uuid4().hex,
                    actor=SYSTEM_ACTOR,
                    action=AUDIT_ACTION,
                    target=occurrence.key,
                    result=_AUDIT_RESULT.get(status, AuditResult.RECHAZADO),
                    reason=reason,
                )
            )
        except LifeOSError as error:
            turn.failures.append(error)
        return RitualReport(occurrence.key, status, reason)

    # --- Acciones -----------------------------------------------------------------------------

    def _compose(self, occurrence: Occurrence, *, retry: bool) -> str | None:
        match occurrence.kind:
            case RitualKind.BRIEFING:
                try:
                    return messages.briefing(self._coach.briefing(occurrence.day))
                except NoDailyPlanError:
                    return messages.no_plan(occurrence.day)
            case RitualKind.CIERRE:
                return messages.closing(occurrence.day)
            case RitualKind.RECORDATORIO:
                if occurrence.block is None:
                    return None
                return messages.reminder(occurrence.block, occurrence.at.tzinfo)
            case RitualKind.PROPUESTA_SEMANAL:
                return self._weekly_proposal(occurrence.day, retry=retry)

    def _weekly_proposal(self, day: date, *, retry: bool) -> str | None:
        week = IsoWeek.containing(day + timedelta(days=1))
        if self._vault.exists(VaultPath.parse(weekly_path(week))):
            # Si este ritual ya la propuso y falló el aviso, se avisa del plan existente. Si el plan
            # viene de otro lado (propietario, web), no se toca ni se avisa.
            return messages.existing_proposal(self._jefe.semana(week)) if retry else None
        return messages.proposal(week, self._jefe.proponer(week))
