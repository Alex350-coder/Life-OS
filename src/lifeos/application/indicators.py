"""Indicadores de utilidad de PLAN §13 y carga de notificaciones (F9-T03, spec `indicadores.md`).

Cálculos puros y deterministas sobre datos ya leídos: tareas de la bóveda, cierres diarios,
planes semanales y registros de uso y de auditoría (ADR-0008). Un indicador sin datos se
devuelve como `None` (desconocido), nunca como 0 (PLAN §11, RULES F.4).
"""

from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from math import ceil
from zoneinfo import ZoneInfo

from lifeos.domain.adaptive_load import DayScore
from lifeos.domain.errors import ValidationError
from lifeos.domain.plan import clean
from lifeos.domain.task import Task, TaskStatus
from lifeos.ports.logs import AuditRecord, AuditResult, CostStatus, UsageOutcome, UsageRecord

MAX_WINDOW_DAYS = 90
DEFAULT_WINDOW_DAYS = 28
DAYS_PER_WEEK = 7
PERCENT = 100
P95 = 0.95
MAX_NAME_CHARS = 40
MINOR_REVISIONS = 1  # aprobado sin revisiones o con una sola: "cambios menores" (PLAN §13)
SYSTEM_ACTOR = "sistema"
OWNER_ACTOR = "propietario"
# Escrituras que solo nacen de algo que hizo el propietario (CLI, web o Telegram): capturar y
# registrar un check-in. Las del Jefe o los especialistas pueden venir de un ritual programado.
OWNER_DRIVEN_ACTIONS = frozenset({"crear_captura", "anexar_checkin"})
RITUAL_ACTION = "ritual"
_CLOSED = frozenset({TaskStatus.COMPLETADA, TaskStatus.CANCELADA})


@dataclass(frozen=True, slots=True)
class Window:
    """Días locales consecutivos, ambos extremos incluidos."""

    start: date
    end: date

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValidationError("ventana", self.start, "el inicio es posterior al final")
        if (self.end - self.start).days + 1 > MAX_WINDOW_DAYS:
            raise ValidationError("ventana", self.start, f"máximo {MAX_WINDOW_DAYS} días")

    @classmethod
    def ending(cls, today: date, *, days: int = DEFAULT_WINDOW_DAYS) -> "Window":
        if not 1 <= days <= MAX_WINDOW_DAYS:
            raise ValidationError("días", days, f"debe estar entre 1 y {MAX_WINDOW_DAYS}")
        return cls(today - timedelta(days=days - 1), today)

    def days(self) -> tuple[date, ...]:
        length = (self.end - self.start).days + 1
        return tuple(self.start + timedelta(days=n) for n in range(length))

    def __contains__(self, day: object) -> bool:
        return isinstance(day, date) and self.start <= day <= self.end


def _percent(part: float, whole: float) -> int | None:
    return round(part * PERCENT / whole) if whole else None


def _name(text: str) -> str:
    return clean("".join(ch if ch.isprintable() else " " for ch in text), MAX_NAME_CHARS)


# --- Entregas y tareas olvidadas ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DeliveryStats:
    due: int  # fecha límite en la ventana y hasta hoy, sin canceladas
    completed: int  # de esas, completadas
    forgotten: int  # abiertas con la fecha límite ya pasada, de cualquier fecha
    percent: int | None


def deliveries(
    tasks: Iterable[Task], window: Window, today: date, *, archived: Iterable[Task] = ()
) -> DeliveryStats:
    """Una tarea archivada cuenta como entrega, nunca como olvidada: el propietario ya la apartó."""
    due = completed = forgotten = 0
    marked = [*((task, False) for task in tasks), *((task, True) for task in archived)]
    for task, is_archived in marked:
        if task.due is None or task.status is TaskStatus.CANCELADA:
            continue
        if task.due in window:  # la ventana termina hoy
            due += 1
            completed += task.status is TaskStatus.COMPLETADA
        if task.due < today and task.status not in _CLOSED and not is_archived:
            forgotten += 1
    return DeliveryStats(due, completed, forgotten, _percent(completed, due))


# --- Cumplimiento del plan diario -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Adherence:
    closed_days: int
    answered: int
    percent: int | None  # hecha = 1, avance = 0,5, no realizada = 0 (carga-adaptativa.md)


def plan_adherence(scores: Iterable[DayScore]) -> Adherence:
    days = list(scores)
    answered = sum(day.answered for day in days)
    points = sum(day.points for day in days)
    return Adherence(len(days), answered, _percent(points, answered))


def closing_streak(closed_days: Iterable[date], today: date) -> int:
    """Días seguidos con cierre hasta hoy; si hoy aún no se cerró, hasta ayer (sin castigar)."""
    closed = set(closed_days)
    day = today if today in closed else today - timedelta(days=1)
    streak = 0
    while day in closed:
        streak += 1
        day -= timedelta(days=1)
    return streak


# --- Frecuencia de uso ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class UsageFrequency:
    active_days: int
    days_per_week: float


def _local_day(record: AuditRecord, zone: ZoneInfo) -> date:
    return record.ts.astimezone(zone).date()


def _owner_used(record: AuditRecord) -> bool:
    if record.result is not AuditResult.OK:
        return False  # intentos, rechazos (p. ej., mensajes de otro chat) y errores no son uso
    return record.actor == OWNER_ACTOR or record.action in OWNER_DRIVEN_ACTIONS


def usage_frequency(
    records: Iterable[AuditRecord], window: Window, zone: ZoneInfo
) -> UsageFrequency:
    """Días con alguna acción correcta del propietario; los rituales y sus lecturas no cuentan."""
    days = {_local_day(r, zone) for r in records if _owner_used(r)}
    active = sum(1 for day in days if day in window)
    weeks = len(window.days()) / DAYS_PER_WEEK
    return UsageFrequency(active, round(active / weeks, 1))


# --- Planes aceptados con cambios menores ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlanWeek:
    week: str
    approved: bool
    revisions: int


@dataclass(frozen=True, slots=True)
class Acceptance:
    proposed: int
    approved: int
    minor_changes: int
    percent: int | None  # aprobados con cambios menores sobre los aprobados


def plan_acceptance(weeks: Iterable[PlanWeek]) -> Acceptance:
    items = list(weeks)
    approved = [w for w in items if w.approved]
    minor = sum(1 for w in approved if w.revisions <= MINOR_REVISIONS)
    return Acceptance(len(items), len(approved), minor, _percent(minor, len(approved)))


# --- Costo y latencia por ritual ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RitualCost:
    agent: str
    ritual: str
    calls: int
    errors: int  # llamadas cuyo último intento no fue correcto
    latency_median_ms: int
    latency_p95_ms: int
    cost_estimated: Decimal
    cost_unknown: int  # intentos sin costo conocido: no se suman como 0


def _nearest_rank(values: Sequence[int], fraction: float) -> int:
    ordered = sorted(values)
    return ordered[max(ceil(fraction * len(ordered)), 1) - 1]


def _cost_row(agent: str, ritual: str, items: Sequence[UsageRecord]) -> RitualCost:
    calls: dict[str, list[UsageRecord]] = defaultdict(list)
    for record in items:
        calls[record.correlation_id].append(record)
    last = [max(attempts, key=lambda r: r.attempt) for attempts in calls.values()]
    # Latencia por llamada: la suma de sus intentos es lo que espera el propietario.
    latencies = [sum(r.latency_ms for r in attempts) for attempts in calls.values()]
    return RitualCost(
        agent=agent,
        ritual=ritual,
        calls=len(calls),
        errors=sum(1 for r in last if r.outcome is not UsageOutcome.OK),
        latency_median_ms=_nearest_rank(latencies, 0.5),
        latency_p95_ms=_nearest_rank(latencies, P95),
        cost_estimated=sum((r.cost_usd or Decimal(0) for r in items), Decimal(0)),
        cost_unknown=sum(1 for r in items if r.cost_status is CostStatus.DESCONOCIDO),
    )


def ritual_costs(records: Iterable[UsageRecord]) -> tuple[RitualCost, ...]:
    groups: dict[tuple[str, str], list[UsageRecord]] = defaultdict(list)
    for record in records:
        groups[(_name(record.agent), _name(record.ritual))].append(record)
    return tuple(
        _cost_row(agent, ritual, groups[(agent, ritual)]) for agent, ritual in sorted(groups)
    )


# --- Carga de notificaciones ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NotificationLoad:
    sent: int
    failed: int
    skipped: int  # omitidas por quedar fuera de su margen
    days_with_notifications: int
    max_per_day: int
    per_active_day: float | None
    by_kind: tuple[tuple[str, int], ...]  # solo las enviadas


def notification_load(
    records: Iterable[AuditRecord], window: Window, zone: ZoneInfo
) -> NotificationLoad:
    rituals = [
        r
        for r in records
        if r.actor == SYSTEM_ACTOR and r.action == RITUAL_ACTION and _local_day(r, zone) in window
    ]
    sent = [r for r in rituals if r.result is AuditResult.OK]
    per_day = Counter(_local_day(r, zone) for r in sent)
    kinds = Counter(_name(r.target.split(":", 1)[0]) for r in sent)
    return NotificationLoad(
        sent=len(sent),
        failed=sum(1 for r in rituals if r.result is AuditResult.ERROR),
        skipped=sum(1 for r in rituals if r.result is AuditResult.RECHAZADO),
        days_with_notifications=len(per_day),
        max_per_day=max(per_day.values(), default=0),
        per_active_day=round(len(sent) / len(per_day), 1) if per_day else None,
        by_kind=tuple(sorted(kinds.items())),
    )
