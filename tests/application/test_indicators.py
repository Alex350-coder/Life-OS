"""Indicadores de utilidad de PLAN §13 y carga de notificaciones (F9-T03).

Reglas deterministas: cada indicador sale de datos reales y lo que no se puede medir se dice,
nunca se rellena con 0.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from lifeos.application.indicators import (
    MAX_WINDOW_DAYS,
    PlanWeek,
    Window,
    deliveries,
    notification_load,
    plan_acceptance,
    plan_adherence,
    ritual_costs,
    usage_frequency,
)
from lifeos.domain.adaptive_load import DayScore
from lifeos.domain.errors import ValidationError
from lifeos.domain.task import Checkbox, Task
from lifeos.ports.logs import AuditRecord, AuditResult, CostStatus, UsageOutcome, UsageRecord

pytestmark = pytest.mark.unit

LIMA = ZoneInfo("America/Lima")
TODAY = date(2026, 11, 13)
WINDOW = Window.ending(TODAY, days=14)


def _task(title: str, checkbox: Checkbox, due: date | None) -> Task:
    return Task(title=title, checkbox=checkbox, due=due)


def _audit(
    ts: datetime,
    *,
    actor: str = "jefe",
    action: str = "publicar_plan",
    target: str = "Semanal/2026-W46.md",
    result: AuditResult = AuditResult.OK,
) -> AuditRecord:
    return AuditRecord(ts, "c" * 32, actor, action, target, result)


def _usage(
    *,
    ritual: str = "propuesta",
    latency: int = 1000,
    cost: Decimal | None = Decimal("0.01"),
    outcome: UsageOutcome = UsageOutcome.OK,
    correlation: str = "a",
) -> UsageRecord:
    status = CostStatus.DESCONOCIDO if cost is None else CostStatus.ESTIMADO
    return UsageRecord(
        ts=datetime(2026, 11, 10, 15, tzinfo=UTC),
        correlation_id=correlation,
        provider="nvidia",
        model="m",
        profile="p",
        agent="jefe",
        ritual=ritual,
        attempt=1,
        latency_ms=latency,
        tokens_prompt=10,
        tokens_completion=5,
        cost_usd=cost,
        cost_status=status,
        outcome=outcome,
        http_status=200,
    )


class TestWindow:
    def test_ending_covers_the_last_days_including_today(self) -> None:
        window = Window.ending(TODAY, days=7)
        assert (window.start, window.end) == (date(2026, 11, 7), TODAY)
        assert len(window.days()) == 7

    @pytest.mark.parametrize("days", [0, -1, MAX_WINDOW_DAYS + 1])
    def test_rejects_days_out_of_range(self, days: int) -> None:
        with pytest.raises(ValidationError):
            Window.ending(TODAY, days=days)

    def test_rejects_start_after_end(self) -> None:
        with pytest.raises(ValidationError):
            Window(TODAY, TODAY - timedelta(days=1))


class TestDeliveries:
    def test_counts_due_completed_and_forgotten(self) -> None:
        tasks = [
            _task("hecha a tiempo", Checkbox.DONE, date(2026, 11, 10)),
            _task("vencida abierta", Checkbox.PENDING, date(2026, 11, 11)),
            _task("vence mañana", Checkbox.PENDING, date(2026, 11, 14)),
            _task("cancelada", Checkbox.CANCELLED, date(2026, 11, 9)),
            _task("sin fecha", Checkbox.PENDING, None),
            _task("vieja olvidada", Checkbox.PENDING, date(2026, 9, 1)),
        ]
        stats = deliveries(tasks, WINDOW, TODAY)
        assert stats.due == 2  # vencen dentro de la ventana y hasta hoy, sin canceladas
        assert stats.completed == 1
        assert stats.forgotten == 2  # abiertas con fecha límite pasada, de cualquier fecha
        assert stats.percent == 50

    def test_without_due_tasks_the_percent_is_unknown_not_zero(self) -> None:
        stats = deliveries([_task("sin fecha", Checkbox.PENDING, None)], WINDOW, TODAY)
        assert (stats.due, stats.percent) == (0, None)

    def test_a_task_due_today_still_open_is_not_forgotten(self) -> None:
        stats = deliveries([_task("hoy", Checkbox.PENDING, TODAY)], WINDOW, TODAY)
        assert (stats.due, stats.completed, stats.forgotten) == (1, 0, 0)


class TestPlanAdherence:
    def test_ratio_of_points_over_answered(self) -> None:
        result = plan_adherence([DayScore(2.0, 4), DayScore(1.5, 2)])
        assert result.closed_days == 2
        assert result.percent == 58  # 3,5 de 6

    def test_without_closings_it_is_unknown(self) -> None:
        assert plan_adherence([]).percent is None


class TestUsageFrequency:
    def test_counts_distinct_local_days_without_system_actor(self) -> None:
        records = [
            _audit(datetime(2026, 11, 10, 14, tzinfo=UTC)),
            _audit(datetime(2026, 11, 10, 20, tzinfo=UTC), actor="coach"),
            # 03:00 UTC del 12 son las 22:00 del 11 en Lima.
            _audit(datetime(2026, 11, 12, 3, tzinfo=UTC), actor="propietario"),
            _audit(datetime(2026, 11, 12, 15, tzinfo=UTC), actor="sistema", action="ritual"),
            _audit(datetime(2026, 10, 1, 15, tzinfo=UTC)),  # fuera de la ventana
        ]
        result = usage_frequency(records, WINDOW, LIMA)
        assert result.active_days == 2
        assert result.days_per_week == 1.0  # 2 días en 14 días

    def test_attempts_without_result_are_not_usage(self) -> None:
        record = _audit(datetime(2026, 11, 10, 14, tzinfo=UTC), result=AuditResult.INTENTO)
        assert usage_frequency([record], WINDOW, LIMA).active_days == 0


class TestPlanAcceptance:
    def test_minor_changes_mean_at_most_one_revision(self) -> None:
        weeks = [
            PlanWeek("2026-W44", approved=True, revisions=0),
            PlanWeek("2026-W45", approved=True, revisions=1),
            PlanWeek("2026-W46", approved=True, revisions=3),
            PlanWeek("2026-W47", approved=False, revisions=0),
        ]
        result = plan_acceptance(weeks)
        assert (result.proposed, result.approved, result.minor_changes) == (4, 3, 2)
        assert result.percent == 67

    def test_without_approved_plans_the_percent_is_unknown(self) -> None:
        assert plan_acceptance([PlanWeek("2026-W47", approved=False, revisions=0)]).percent is None


class TestRitualCosts:
    def test_groups_by_agent_and_ritual_with_latency_percentiles(self) -> None:
        records = [
            _usage(latency=100, correlation="a"),
            _usage(latency=300, correlation="b"),
            _usage(latency=900, correlation="c", cost=None),
            _usage(ritual="checkin", latency=50, outcome=UsageOutcome.TIMEOUT),
        ]
        rows = {(row.agent, row.ritual): row for row in ritual_costs(records)}
        propuesta = rows[("jefe", "propuesta")]
        assert (propuesta.calls, propuesta.errors) == (3, 0)
        assert propuesta.latency_median_ms == 300
        assert propuesta.latency_p95_ms == 900
        assert propuesta.cost_estimated == Decimal("0.02")
        assert propuesta.cost_unknown == 1
        assert rows[("jefe", "checkin")].errors == 1

    def test_names_are_sanitized(self) -> None:
        record = _usage(ritual="pro\x1bpuesta")
        assert ritual_costs([record])[0].ritual == "pro puesta"


class TestNotificationLoad:
    def test_counts_sent_failed_and_skipped_per_local_day(self) -> None:
        def ritual(hour: int, day: int, kind: str, result: AuditResult) -> AuditRecord:
            return _audit(
                datetime(2026, 11, day, hour, tzinfo=UTC),
                actor="sistema",
                action="ritual",
                target=f"{kind}:2026-11-{day:02d}",
                result=result,
            )

        records = [
            ritual(12, 10, "briefing", AuditResult.OK),
            ritual(18, 10, "recordatorio", AuditResult.OK),
            ritual(23, 10, "cierre", AuditResult.OK),
            ritual(12, 11, "briefing", AuditResult.OK),
            ritual(13, 11, "recordatorio", AuditResult.ERROR),
            ritual(14, 11, "recordatorio", AuditResult.RECHAZADO),
            _audit(datetime(2026, 11, 11, 15, tzinfo=UTC)),  # no es un ritual
        ]
        load = notification_load(records, WINDOW, LIMA)
        assert (load.sent, load.failed, load.skipped) == (4, 1, 1)
        assert load.max_per_day == 3
        assert load.days_with_notifications == 2
        assert load.per_active_day == 2.0
        assert dict(load.by_kind) == {"briefing": 2, "cierre": 1, "recordatorio": 1}

    def test_no_rituals_gives_zero_counts_and_unknown_average(self) -> None:
        load = notification_load([], WINDOW, LIMA)
        assert (load.sent, load.max_per_day, load.per_active_day) == (0, 0, None)
