"""Informe de indicadores sobre una bóveda temporal y logs JSONL reales (F9-T03).

Datos inventados y aislados en `tmp_path`: nunca datos del propietario.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from lifeos.adapters.filesystem_vault import FilesystemVault
from lifeos.adapters.jsonl_logs import (
    JsonlAuditLog,
    JsonlAuditReader,
    JsonlUsageLog,
    JsonlUsageReader,
)
from lifeos.application.indicator_report import NOT_MEASURED, measure_indicators
from lifeos.application.tasks import ApplicationError
from lifeos.ports.logs import AuditRecord, AuditResult, CostStatus, UsageOutcome, UsageRecord
from tests.conftest import WriteNote
from tests.daily_builders import daily_text
from tests.planning_samples import HORARIO

pytestmark = pytest.mark.integration

TODAY = date(2026, 11, 13)
APPROVED_WEEK = "---\ntipo: semanal\nestado_plan: aprobado\n---\n"
PROPOSED_WEEK = "---\ntipo: semanal\nestado_plan: propuesto\n---\n"


def _usage(ts: datetime, latency: int) -> UsageRecord:
    return UsageRecord(
        ts=ts,
        correlation_id=f"u{latency}",
        provider="nvidia",
        model="m",
        profile="p",
        agent="jefe",
        ritual="propuesta",
        attempt=1,
        latency_ms=latency,
        tokens_prompt=None,
        tokens_completion=None,
        cost_usd=None,
        cost_status=CostStatus.DESCONOCIDO,
        outcome=UsageOutcome.OK,
        http_status=200,
    )


def _audit(ts: datetime, actor: str, action: str, target: str) -> AuditRecord:
    return AuditRecord(ts, "c" * 32, actor, action, target, AuditResult.OK)


@pytest.fixture
def logs(tmp_path: Path) -> Path:
    path = tmp_path / "logs"
    usage, audit = JsonlUsageLog(path), JsonlAuditLog(path)
    usage.append(_usage(datetime(2026, 11, 10, 15, tzinfo=UTC), 1200))
    usage.append(_usage(datetime(2026, 10, 1, 15, tzinfo=UTC), 9999))  # fuera de la ventana
    audit.append(_audit(datetime(2026, 11, 10, 15, tzinfo=UTC), "coach", "anexar_checkin", "x"))
    audit.append(_audit(datetime(2026, 11, 11, 12, tzinfo=UTC), "sistema", "ritual", "briefing:x"))
    # Registro del mes anterior dentro de la ventana de 28 días.
    audit.append(_audit(datetime(2026, 10, 20, 15, tzinfo=UTC), "jefe", "publicar_plan", "y"))
    return path


def test_report_reads_vault_and_logs(
    vault: FilesystemVault, write_note: WriteNote, logs: Path
) -> None:
    write_note("_sistema/horario.md", HORARIO)
    write_note(
        "01-Universidad/tareas.md",
        "- [x] Informe [id:: t-aaaaaa] 📅 2026-11-10\n- [ ] Ensayo [id:: t-bbbbbb] 📅 2026-11-12\n",
    )
    write_note(
        "Diario/2026-11-10.md",
        daily_text(date(2026, 11, 10), closing=("- 21:00 · hecha: Informe",)),
    )
    write_note("Semanal/2026-W45.md", APPROVED_WEEK)
    write_note("Semanal/2026-W46.md", PROPOSED_WEEK)
    write_note("Semanal/2026-W46-r1.md", APPROVED_WEEK)

    report = measure_indicators(
        vault, JsonlUsageReader(logs), JsonlAuditReader(logs), TODAY, days=28
    )

    assert (report.deliveries.due, report.deliveries.completed) == (2, 1)
    assert report.deliveries.forgotten == 1
    assert (report.adherence.closed_days, report.adherence.percent) == (1, 100)
    assert report.usage.active_days == 2
    assert (report.acceptance.approved, report.acceptance.minor_changes) == (2, 2)
    assert [(c.calls, c.latency_median_ms) for c in report.costs] == [(1, 1200)]
    assert report.costs[0].cost_estimated == Decimal(0)
    assert report.costs[0].cost_unknown == 1
    assert report.notifications.sent == 1
    assert report.not_measured == NOT_MEASURED
    assert report.quality.malformed_log_lines == 0


def test_report_never_writes(vault: FilesystemVault, write_note: WriteNote, logs: Path) -> None:
    write_note("_sistema/horario.md", HORARIO)
    before = sorted(p.value for p in vault.list_notes())
    log_files = sorted(p.read_bytes() for p in logs.iterdir())

    measure_indicators(vault, JsonlUsageReader(logs), JsonlAuditReader(logs), TODAY, days=7)

    assert sorted(p.value for p in vault.list_notes()) == before
    assert sorted(p.read_bytes() for p in logs.iterdir()) == log_files


def test_counts_malformed_log_lines(
    vault: FilesystemVault, write_note: WriteNote, logs: Path
) -> None:
    write_note("_sistema/horario.md", HORARIO)
    target = next(logs.glob("audit-*"))
    target.write_text(target.read_text(encoding="utf-8") + "{no es json\n", encoding="utf-8")

    report = measure_indicators(
        vault, JsonlUsageReader(logs), JsonlAuditReader(logs), TODAY, days=28
    )

    assert report.quality.malformed_log_lines == 1


def test_missing_horario_is_a_clear_error(vault: FilesystemVault, logs: Path) -> None:
    with pytest.raises(ApplicationError, match=r"falta _sistema/horario.md"):
        measure_indicators(vault, JsonlUsageReader(logs), JsonlAuditReader(logs), TODAY, days=7)


def test_invalid_horario_is_a_clear_error(
    vault: FilesystemVault, write_note: WriteNote, logs: Path
) -> None:
    write_note("_sistema/horario.md", "sin frontmatter")
    with pytest.raises(ApplicationError, match="no se pudo usar"):
        measure_indicators(vault, JsonlUsageReader(logs), JsonlAuditReader(logs), TODAY, days=7)
