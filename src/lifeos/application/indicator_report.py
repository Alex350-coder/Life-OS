"""Informe de indicadores (F9-T03): lee la bóveda y los logs y aplica `indicators`.

Solo lectura: no escribe en la bóveda ni en los logs. Los indicadores de PLAN §13 que el sistema
no puede medir se listan con su motivo para que el propietario los valore a mano.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field
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
from lifeos.domain.adaptive_load import CLOSING_SECTION, DayScore, closing_score
from lifeos.domain.horario_note import HORARIO_NOTE, HorarioError, parse_horario
from lifeos.domain.plan_note import MAX_REVISIONS, daily_path, is_approved, revision_path
from lifeos.domain.plan_note import weekly_path as weekly_note
from lifeos.domain.schedule import Schedule
from lifeos.domain.sections import section_lines
from lifeos.domain.weeks import IsoWeek
from lifeos.ports.logs import AuditReader, AuditRecord, UsageReader, UsageRecord
from lifeos.ports.vault import NoteNotFoundError, VaultIOError, VaultPath, VaultStore

MONTHS_PER_YEAR = 12
MAX_STREAK_DAYS = 365  # tope de lectura hacia atrás para la racha

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
    unreadable_notes: int  # notas que no se pudieron leer (p. ej., OneDrive sin descargar)
    invalid_task_lines: int  # líneas de tareas con formato no válido
    unreadable_closings: int  # días con «## Cierre» sin ninguna respuesta reconocible


@dataclass(frozen=True, slots=True)
class IndicatorReport:
    window: Window
    deliveries: DeliveryStats
    adherence: Adherence
    streak: int  # días seguidos con cierre, aunque empiecen antes de la ventana (hasta 365)
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


@dataclass(slots=True)
class _Notes:
    """Lector de notas que cuenta las ilegibles en vez de detener el informe."""

    vault: VaultStore
    unreadable: set[str] = field(default_factory=set)
    bad_closings: int = 0

    def read(self, path: str) -> str | None:
        try:
            return self.vault.read(VaultPath.parse(path)).text
        except NoteNotFoundError:
            return None
        except VaultIOError:
            self.unreadable.add(path)
            return None

    def closing(self, day: date) -> DayScore | None:
        text = self.read(daily_path(day))
        if text is None:
            return None
        score = closing_score(text)
        lines = section_lines(text, CLOSING_SECTION)
        if score is None and lines is not None and len(lines) > 1:
            self.bad_closings += 1  # hay cierre, pero ninguna línea se entiende: se avisa
        return score


def _closing_scores(notes: _Notes, window: Window) -> dict[date, DayScore]:
    scores: dict[date, DayScore] = {}
    for day in window.days():
        score = notes.closing(day)
        if score is not None:
            scores[day] = score
    return scores


def _streak(notes: _Notes, scores: dict[date, DayScore], window: Window, today: date) -> int:
    """La racha no depende de la ventana: sigue hacia atrás hasta el primer día sin cierre."""
    closed = set(scores)
    streak = closing_streak(closed, today)
    first = today if today in closed else today - timedelta(days=1)
    if streak == 0 or first - timedelta(days=streak) >= window.start:
        return streak  # se cortó dentro de la ventana
    day = window.start - timedelta(days=1)
    while streak < MAX_STREAK_DAYS and notes.closing(day) is not None:
        streak += 1
        day -= timedelta(days=1)
    return streak


def _weeks(window: Window) -> list[IsoWeek]:
    weeks: list[IsoWeek] = []
    for day in window.days():
        week = IsoWeek.containing(day)
        if week not in weeks:
            weeks.append(week)
    return weeks


def _plan_weeks(notes: _Notes, window: Window) -> Iterator[PlanWeek]:
    for week in _weeks(window):
        base = notes.read(weekly_note(week))
        if base is None:
            continue
        revisions = [
            text
            for n in range(1, MAX_REVISIONS + 1)
            if (text := notes.read(revision_path(week, n))) is not None
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


def _schedule(notes: _Notes) -> Schedule:
    try:
        text = notes.vault.read(VaultPath.parse(HORARIO_NOTE)).text
        return parse_horario(text)
    except NoteNotFoundError:
        raise ApplicationError(
            f"falta {HORARIO_NOTE}: los indicadores usan su zona horaria"
        ) from None
    except (VaultIOError, HorarioError) as error:
        raise ApplicationError(f"no se pudo usar {HORARIO_NOTE}: {error}") from error


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
    notes = _Notes(vault)
    schedule = _schedule(notes)
    zone = schedule.zone
    today = now.astimezone(zone).date()
    window = Window.ending(today, days=days)
    report = scan(vault)
    scores = _closing_scores(notes, window)
    streak = _streak(notes, scores, window, today)
    weeks = tuple(_plan_weeks(notes, window))
    usage, audit, malformed = _logs(usage_reader, audit_reader, window)
    start = datetime.combine(window.start, time(), zone).astimezone(UTC)
    end = datetime.combine(window.end + timedelta(days=1), time(), zone).astimezone(UTC)
    broken = {p.path.value for p in report.problems if p.line_index is None}
    return IndicatorReport(
        window=window,
        deliveries=deliveries(
            (t.task for t in report.tasks if not t.archived),
            window,
            today,
            archived=(t.task for t in report.tasks if t.archived),
        ),
        adherence=plan_adherence(scores.values()),
        streak=streak,
        focus_max_minutes=schedule.limits.focus_max,
        usage=usage_frequency(audit, window, zone),
        acceptance=plan_acceptance(weeks),
        costs=ritual_costs(r for r in usage if _in_window(r, start, end)),
        notifications=notification_load(audit, window, zone),
        not_measured=NOT_MEASURED,
        quality=DataQuality(
            malformed_log_lines=malformed,
            unreadable_notes=len(broken | notes.unreadable),
            invalid_task_lines=sum(1 for p in report.problems if p.line_index is not None),
            unreadable_closings=notes.bad_closings,
        ),
    )
