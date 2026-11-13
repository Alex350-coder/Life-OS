"""Informe de indicadores (F9-T03): lee la bóveda y los logs y aplica `indicators`.

Solo lectura: no escribe en la bóveda ni en los logs. Los indicadores de PLAN §13 que el sistema
no puede medir se listan con su motivo para que el propietario los valore a mano.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from lifeos.application.indicators import (
    Acceptance,
    Adherence,
    DeliveryStats,
    NotificationLoad,
    PlanWeek,
    RitualCost,
    UsageFrequency,
    Window,
    closing_streak,
    deliveries,
    notification_load,
    plan_acceptance,
    plan_adherence,
    ritual_costs,
    usage_frequency,
)
from lifeos.application.tasks import ApplicationError, scan
from lifeos.domain.adaptive_load import DayScore, closing_score
from lifeos.domain.horario_note import HORARIO_NOTE, HorarioError, parse_horario
from lifeos.domain.plan_note import MAX_REVISIONS, daily_path, is_approved, revision_path
from lifeos.domain.plan_note import weekly_path as weekly_note
from lifeos.domain.weeks import IsoWeek
from lifeos.ports.logs import AuditReader, AuditRecord, UsageReader, UsageRecord
from lifeos.ports.vault import NoteNotFoundError, VaultIOError, VaultPath, VaultStore

MONTHS_PER_YEAR = 12

# Indicadores de PLAN §13 sin fuente automática: se declaran, no se inventan.
NOT_MEASURED: tuple[tuple[str, str], ...] = (
    (
        "Tiempo para decidir qué hacer",
        "no hay un evento que marque el inicio y el fin de la decisión; lo valora el propietario",
    ),
    (
        "Desviación entre esfuerzo estimado y real",
        "el cierre registra el avance y lo que queda, no los minutos trabajados",
    ),
    (
        "Falsas detecciones de compromisos",
        "Life OS no detecta compromisos por su cuenta: el propietario los escribe en horario.md",
    ),
    ("Utilidad y nivel de estrés", "valoración subjetiva del propietario en la revisión"),
)


@dataclass(frozen=True, slots=True)
class DataQuality:
    malformed_log_lines: int
    unreadable_notes: int  # notas o líneas de tareas que no se pudieron analizar


@dataclass(frozen=True, slots=True)
class IndicatorReport:
    window: Window
    deliveries: DeliveryStats
    adherence: Adherence
    streak: int  # días seguidos con cierre (Oficina, panel «Mi día»)
    focus_max_minutes: int  # foco_max_dia de horario.md
    usage: UsageFrequency
    acceptance: Acceptance
    costs: tuple[RitualCost, ...]
    notifications: NotificationLoad
    not_measured: tuple[tuple[str, str], ...]
    quality: DataQuality


def _months(window: Window) -> list[tuple[int, int]]:
    """Meses UTC que pueden contener registros de la ventana (un día de margen por la zona)."""
    first = window.start - timedelta(days=1)
    last = window.end + timedelta(days=1)
    months: list[tuple[int, int]] = []
    year, month = first.year, first.month
    while (year, month) <= (last.year, last.month):
        months.append((year, month))
        year, month = (year + 1, 1) if month == MONTHS_PER_YEAR else (year, month + 1)
    return months


def _read(vault: VaultStore, path: str) -> str | None:
    try:
        return vault.read(VaultPath.parse(path)).text
    except NoteNotFoundError:
        return None


def _closing_scores(vault: VaultStore, window: Window) -> dict[date, DayScore]:
    scores: dict[date, DayScore] = {}
    for day in window.days():
        text = _read(vault, daily_path(day))
        score = closing_score(text) if text is not None else None
        if score is not None:
            scores[day] = score
    return scores


def _weeks(window: Window) -> list[IsoWeek]:
    weeks: list[IsoWeek] = []
    for day in window.days():
        week = IsoWeek.containing(day)
        if week not in weeks:
            weeks.append(week)
    return weeks


def _plan_weeks(vault: VaultStore, window: Window) -> Iterator[PlanWeek]:
    for week in _weeks(window):
        base = _read(vault, weekly_note(week))
        if base is None:
            continue
        revisions = [
            text
            for n in range(1, MAX_REVISIONS + 1)
            if (text := _read(vault, revision_path(week, n))) is not None
        ]
        approved = is_approved(base) or any(is_approved(text) for text in revisions)
        yield PlanWeek(week.render(), approved=approved, revisions=len(revisions))


def _logs(
    usage_reader: UsageReader, audit_reader: AuditReader, window: Window
) -> tuple[list[UsageRecord], list[AuditRecord], int]:
    usage: list[UsageRecord] = []
    audit: list[AuditRecord] = []
    malformed = 0
    for year, month in _months(window):
        usage_scan = usage_reader.read_month(year, month)
        audit_scan = audit_reader.read_month(year, month)
        usage += usage_scan.records
        audit += audit_scan.records
        malformed += usage_scan.malformed + audit_scan.malformed
    return usage, audit, malformed


def _in_window(record: UsageRecord, start: datetime, end: datetime) -> bool:
    return start <= record.ts.astimezone(UTC) < end


def measure_indicators(
    vault: VaultStore,
    usage_reader: UsageReader,
    audit_reader: AuditReader,
    now: datetime,
    *,
    days: int,
) -> IndicatorReport:
    """Indicadores de los últimos `days` días locales (zona de horario.md) hasta hoy incluido."""
    if now.utcoffset() is None:
        raise ApplicationError("la hora actual necesita zona horaria")
    try:
        text = _read(vault, HORARIO_NOTE)
        if text is None:
            raise ApplicationError(f"falta {HORARIO_NOTE}: los indicadores usan su zona horaria")
        schedule = parse_horario(text)
    except (VaultIOError, HorarioError) as error:
        raise ApplicationError(f"no se pudo usar {HORARIO_NOTE}: {error}") from error
    zone = schedule.zone
    today = now.astimezone(zone).date()
    window = Window.ending(today, days=days)
    report = scan(vault)
    scores = _closing_scores(vault, window)
    usage, audit, malformed = _logs(usage_reader, audit_reader, window)
    start = datetime.combine(window.start, time(), zone).astimezone(UTC)
    end = datetime.combine(window.end + timedelta(days=1), time(), zone).astimezone(UTC)
    return IndicatorReport(
        window=window,
        deliveries=deliveries((t.task for t in report.tasks), window, today),
        adherence=plan_adherence(scores.values()),
        streak=closing_streak(scores, today),
        focus_max_minutes=schedule.limits.focus_max,
        usage=usage_frequency(audit, window, zone),
        acceptance=plan_acceptance(_plan_weeks(vault, window)),
        costs=ritual_costs(r for r in usage if _in_window(r, start, end)),
        notifications=notification_load(audit, window, zone),
        not_measured=NOT_MEASURED,
        quality=DataQuality(malformed, len(report.problems)),
    )
