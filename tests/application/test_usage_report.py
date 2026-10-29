"""Resumen de uso de modelos (F4-T05, PLAN §11): llamadas, intentos, errores, tokens y costo.

Lo que no se conoce se informa como desconocido; nunca se suma como 0.
"""

from decimal import Decimal

import pytest
from lifeos.application.usage_report import summarize

from lifeos.ports.logs import CostStatus, UsageOutcome, UsageScan
from tests.adapters.test_usage_reader import record

pytestmark = [pytest.mark.unit]


def test_groups_by_agent_and_ritual_and_counts_calls_by_correlation() -> None:
    scan = UsageScan(
        records=(
            record(
                correlation_id="c1",
                outcome=UsageOutcome.LIMITE_TASA,
                tokens_prompt=None,
                tokens_completion=None,
                http_status=429,
            ),
            record(correlation_id="c1", attempt=2),
            record(correlation_id="c2"),
            record(
                correlation_id="j1",
                agent="jefe",
                ritual="plan_semanal",
                profile="grande",
                cost_usd=Decimal("0.01"),
                cost_status=CostStatus.ESTIMADO,
            ),
        ),
        malformed=1,
        files=1,
    )
    summary = summarize(scan)
    coach, jefe = summary.rows
    assert (coach.agent, coach.ritual) == ("coach", "checkin")
    assert (coach.calls, coach.attempts, coach.errors) == (2, 3, 1)
    assert (coach.tokens_prompt, coach.tokens_completion, coach.tokens_unknown) == (20, 10, 1)
    assert (coach.cost_estimated, coach.cost_unknown) == (Decimal("0"), 3)
    assert (jefe.agent, jefe.calls, jefe.cost_estimated, jefe.cost_unknown) == (
        "jefe",
        1,
        Decimal("0.01"),
        0,
    )
    assert summary.total.calls == 3
    assert summary.total.attempts == 4
    assert summary.total.cost_unknown == 3
    assert summary.malformed == 1


def test_empty_scan_has_no_rows_and_zero_totals() -> None:
    summary = summarize(UsageScan(records=(), malformed=0, files=0))
    assert summary.rows == ()
    assert summary.total.calls == 0
    assert summary.total.cost_unknown == 0


def test_names_are_cleaned_for_display() -> None:
    scan = UsageScan(
        records=(record(agent="coach\x1b[31m", ritual="check\nin"),), malformed=0, files=1
    )
    (row,) = summarize(scan).rows
    assert "\x1b" not in row.agent and "\n" not in row.ritual
