"""Ejecutor de rituales (F6-T01, F6-T02; ADR-0014, spec rituales-y-notificaciones).

En cada vuelta revisa las ocurrencias de ayer y de hoy. Una ocurrencia se ejecuta una sola vez:
cuando llega su hora (o el final del silencio blando que la aplaza) y el propietario no está en
silencio. Pasado su margen se omite. Todo resultado se audita y se guarda en el estado antes de
seguir, para que un reinicio no repita lo ya enviado.
"""

import uuid
from dataclasses import dataclass
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

MAX_ATTEMPTS = 3
STATE_RETENTION = timedelta(days=7)
SYSTEM_ACTOR = "sistema"
AUDIT_ACTION = "ritual"
SKIPPED_REASON = "omitido: fuera de su margen"


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


def _retryable(mark: RitualMark) -> bool:
    return mark.status is RitualStatus.FALLIDO and mark.attempts < MAX_ATTEMPTS


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
        except NoDailyPlanError:
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
        marks = dict(self._state.load())
        reports: list[RitualReport] = []
        for day in (previous, current):
            for occurrence in day.occurrences:
                report = self._step(occurrence, day.windows, current.windows, marks, now)
                if report is not None:
                    reports.append(report)
        kept = {key: mark for key, mark in marks.items() if mark.updated >= now - STATE_RETENTION}
        if kept.keys() != marks.keys():
            self._state.save(kept)
        return tuple(reports)

    def _step(
        self,
        occurrence: Occurrence,
        windows: tuple[QuietWindow, ...],
        now_windows: tuple[QuietWindow, ...],
        marks: dict[str, RitualMark],
        now: datetime,
    ) -> RitualReport | None:
        mark = marks.get(occurrence.key)
        if mark is not None and not _retryable(mark):
            return None
        if now < occurrence.at:
            return None
        if now > occurrence.deadline:
            return self._finish(occurrence, RitualStatus.OMITIDO, SKIPPED_REASON, marks, now)
        delivery = plan_delivery(occurrence.at, occurrence.deadline, windows)
        if delivery.kind is DeliveryKind.DESCARTAR:
            reason = f"descartado: {delivery.reason}"
            return self._finish(occurrence, RitualStatus.DESCARTADO, reason, marks, now)
        if delivery.at is None or now < delivery.at or is_quiet(now, now_windows):
            return None  # aplazado, o recuperación tardía mientras el propietario descansa
        attempts = (mark.attempts if mark else 0) + 1
        try:
            text = self._compose(occurrence)
            if text is not None:
                self._notifier.send(text)
        except LifeOSError as error:
            reason = f"{type(error).__name__}: {error}"
            return self._finish(occurrence, RitualStatus.FALLIDO, reason, marks, now, attempts)
        return self._finish(occurrence, RitualStatus.HECHO, None, marks, now, attempts)

    def _finish(
        self,
        occurrence: Occurrence,
        status: RitualStatus,
        reason: str | None,
        marks: dict[str, RitualMark],
        now: datetime,
        attempts: int = 0,
    ) -> RitualReport:
        result = {
            RitualStatus.HECHO: AuditResult.OK,
            RitualStatus.FALLIDO: AuditResult.ERROR,
        }.get(status, AuditResult.RECHAZADO)
        self._audit.append(
            AuditRecord(
                ts=now,
                correlation_id=uuid.uuid4().hex,
                actor=SYSTEM_ACTOR,
                action=AUDIT_ACTION,
                target=occurrence.key,
                result=result,
                reason=reason,
            )
        )
        marks[occurrence.key] = RitualMark(status, attempts, now, reason)
        self._state.save(marks)  # antes de seguir: un reinicio no repite lo ya enviado
        return RitualReport(occurrence.key, status, reason)

    # --- Acciones -----------------------------------------------------------------------------

    def _compose(self, occurrence: Occurrence) -> str | None:
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
                return self._weekly_proposal(occurrence.day)

    def _weekly_proposal(self, day: date) -> str | None:
        week = IsoWeek.containing(day + timedelta(days=1))
        if self._vault.exists(VaultPath.parse(weekly_path(week))):
            return None  # ya hay plan (propuesto o aprobado): no se toca
        return messages.proposal(week, self._jefe.proponer(week))
