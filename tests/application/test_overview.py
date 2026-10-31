"""Lecturas de la web (F5-T02, F5-T04): tareas próximas, historial de cambios y agentes.

Todo es de solo lectura y sale de la bóveda, de la auditoría y de la configuración validada;
nada se inventa ni se simula.
"""

import dataclasses
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from lifeos.adapters.jsonl_logs import JsonlAuditLog, JsonlAuditReader
from lifeos.adapters.yaml_config import YamlConfigSource
from lifeos.application.agents_overview import agents_overview
from lifeos.application.history import recent_changes
from lifeos.application.permissions import TOOL_WRITE_MODES, resolve_effective
from lifeos.application.tasks import ARCHIVE_ROOT, upcoming_tasks
from lifeos.application.usage_report import summarize
from lifeos.domain.errors import ValidationError
from lifeos.ports.logs import (
    AuditRecord,
    AuditResult,
    AuditScan,
    CostStatus,
    UsageOutcome,
    UsageRecord,
    UsageScan,
)
from lifeos.ports.vault import VaultStore
from tests.application.conftest import WriteNote

PROJECT = Path(__file__).resolve().parent.parent.parent
TODAY = date(2026, 11, 2)
NOW = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)

TASKS = (
    "- [ ] Atrasada [id:: t-aaaabz] [area:: universidad] [esfuerzo:: 1h] 📅 2026-10-30\n"
    "- [ ] Mañana [id:: t-aaaab2] [area:: universidad] [esfuerzo:: 1h] 📅 2026-11-03\n"
    "- [ ] Hoy B [id:: t-aaaab3] [area:: proyectos] [esfuerzo:: 1h] 📅 2026-11-02\n"
    "- [ ] Hoy A [id:: t-aaaab4] [area:: proyectos] [esfuerzo:: 1h] 📅 2026-11-02\n"
    "- [ ] Lejana [id:: t-aaaab5] [area:: universidad] [esfuerzo:: 1h] 📅 2026-12-20\n"
    "- [ ] Sin fecha [id:: t-aaaab6] [area:: universidad] [esfuerzo:: 1h]\n"
    "- [x] Hecha [id:: t-aaaab7] [area:: universidad] [esfuerzo:: 1h] 📅 2026-11-03\n"
    "- [-] Cancelada [id:: t-aaaab8] [area:: universidad] [esfuerzo:: 1h] 📅 2026-11-03\n"
)
RLO = chr(0x202E)  # bidi: invierte el texto que sigue
ARCHIVED = "- [ ] Archivada [id:: t-aaaab9] [area:: universidad] [esfuerzo:: 1h] 📅 2026-11-03\n"


# --- Tareas próximas --------------------------------------------------------------------------


@pytest.mark.integration
def test_upcoming_lists_open_tasks_due_soon_or_overdue_in_order(
    vault: VaultStore, write_note: WriteNote
) -> None:
    write_note("01-Universidad/tareas.md", TASKS)
    titles = [item.task.title for item in upcoming_tasks(vault, TODAY, days=7)]
    assert titles == ["Atrasada", "Hoy A", "Hoy B", "Mañana"]


@pytest.mark.integration
def test_upcoming_excludes_archived_tasks(vault: VaultStore, write_note: WriteNote) -> None:
    write_note(f"{ARCHIVE_ROOT}/01-Universidad/viejas.md", ARCHIVED)
    assert upcoming_tasks(vault, TODAY, days=7) == ()


@pytest.mark.integration
def test_upcoming_has_no_lower_bound_for_overdue(vault: VaultStore, write_note: WriteNote) -> None:
    write_note("01-Universidad/viejas.md", TASKS + ARCHIVED.replace("2026-11-03", "2025-01-01"))
    titles = [item.task.title for item in upcoming_tasks(vault, TODAY, days=1)]
    assert titles[0] == "Archivada"


@pytest.mark.integration
def test_upcoming_window_includes_its_last_day(vault: VaultStore, write_note: WriteNote) -> None:
    write_note("01-Universidad/tareas.md", TASKS)
    assert upcoming_tasks(vault, TODAY, days=48)[-1].task.title == "Lejana"
    assert "Lejana" not in [i.task.title for i in upcoming_tasks(vault, TODAY, days=47)]


@pytest.mark.integration
@pytest.mark.parametrize("days", [1, 60])
def test_upcoming_window_accepts_its_bounds(vault: VaultStore, days: int) -> None:
    assert upcoming_tasks(vault, TODAY, days=days) == ()


@pytest.mark.integration
@pytest.mark.parametrize("days", [0, -1, 61])
def test_upcoming_window_is_bounded(vault: VaultStore, days: int) -> None:
    with pytest.raises(ValidationError):
        upcoming_tasks(vault, TODAY, days=days)


# --- Historial de cambios ---------------------------------------------------------------------


def _audit(
    minutes: int,
    result: AuditResult,
    *,
    correlation: str = "c1",
    target: str = "Diario/2026-11-02.md",
    reason: str | None = None,
) -> AuditRecord:
    return AuditRecord(
        ts=NOW - timedelta(minutes=minutes),
        correlation_id=correlation,
        actor="coach",
        action="anexar_checkin",
        target=target,
        result=result,
        reason=reason,
    )


class StubAuditReader:
    """Lector de auditoría en memoria por mes (aislado para pruebas)."""

    def __init__(self, months: dict[tuple[int, int], AuditScan]) -> None:
        self.months = months
        self.asked: list[tuple[int, int]] = []

    def read_month(self, year: int, month: int) -> AuditScan:
        self.asked.append((year, month))
        return self.months.get((year, month), AuditScan((), 0, 0))


@pytest.mark.unit
def test_history_is_newest_first_and_hides_attempts_that_finished() -> None:
    records = (
        _audit(30, AuditResult.INTENTO),
        _audit(29, AuditResult.OK),
        _audit(10, AuditResult.RECHAZADO, correlation="c2", target="_sistema/perfil.md"),
    )
    history = recent_changes(StubAuditReader({(2026, 11): AuditScan(records, 0, 1)}), NOW)
    assert [(e.result, e.target) for e in history.entries] == [
        (AuditResult.RECHAZADO, "_sistema/perfil.md"),
        (AuditResult.OK, "Diario/2026-11-02.md"),
    ]
    assert all(e.completed for e in history.entries)


@pytest.mark.unit
def test_history_flags_an_attempt_without_result() -> None:
    records = (_audit(5, AuditResult.INTENTO, correlation="c9"),)
    history = recent_changes(StubAuditReader({(2026, 11): AuditScan(records, 0, 1)}), NOW)
    assert [(e.result, e.completed) for e in history.entries] == [(AuditResult.INTENTO, False)]


@pytest.mark.unit
def test_history_reads_this_month_and_the_previous_one_and_limits() -> None:
    october = AuditScan(tuple(_audit(60 * 24 * 10 + i, AuditResult.OK) for i in range(3)), 2, 1)
    november = AuditScan((_audit(1, AuditResult.OK, correlation="n1"),), 1, 1)
    reader = StubAuditReader({(2026, 10): october, (2026, 11): november})
    history = recent_changes(reader, NOW, limit=2)
    assert set(reader.asked) == {(2026, 10), (2026, 11)}
    assert len(history.entries) == 2 and history.truncated
    assert history.entries[0].ts > history.entries[1].ts
    assert history.malformed == 3
    assert not recent_changes(reader, NOW, limit=4).truncated


@pytest.mark.unit
def test_history_does_not_hide_an_attempt_finished_on_another_target() -> None:
    records = (_audit(30, AuditResult.INTENTO), _audit(29, AuditResult.OK, target="otro.md"))
    history = recent_changes(StubAuditReader({(2026, 11): AuditScan(records, 0, 1)}), NOW)
    assert {(e.target, e.completed) for e in history.entries} == {
        ("Diario/2026-11-02.md", False),
        ("otro.md", True),
    }


@pytest.mark.unit
def test_history_pairs_attempt_and_result_across_months() -> None:
    attempt = AuditScan((_audit(60 * 24 * 3, AuditResult.INTENTO),), 0, 1)
    done = AuditScan((_audit(1, AuditResult.OK),), 0, 1)
    reader = StubAuditReader({(2026, 10): attempt, (2026, 11): done})
    assert [e.result for e in recent_changes(reader, NOW).entries] == [AuditResult.OK]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("now", "months"),
    [
        (datetime(2026, 10, 31, 22, 0, tzinfo=ZoneInfo("America/Lima")), {(2026, 10), (2026, 11)}),
        (datetime(2027, 1, 5, tzinfo=UTC), {(2026, 12), (2027, 1)}),
    ],
)
def test_history_months_are_utc(now: datetime, months: set[tuple[int, int]]) -> None:
    reader = StubAuditReader({})
    recent_changes(reader, now)
    assert set(reader.asked) == months


@pytest.mark.unit
def test_history_needs_an_aware_now() -> None:
    with pytest.raises(ValidationError):
        recent_changes(StubAuditReader({}), datetime(2026, 11, 2, 12, 0))


@pytest.mark.unit
def test_history_text_is_cleaned() -> None:
    record = _audit(
        1,
        AuditResult.RECHAZADO,
        target=f"Diario/{RLO}x.md",
        reason=f"fuera{RLO} de\x1b[2J permisos",
    )
    history = recent_changes(StubAuditReader({(2026, 11): AuditScan((record,), 0, 1)}), NOW)
    assert history.entries[0].reason == "fuera de[2J permisos"
    assert history.entries[0].target == "Diario/x.md"


@pytest.mark.unit
@pytest.mark.parametrize("limit", [0, 501])
def test_history_limit_is_bounded(limit: int) -> None:
    with pytest.raises(ValidationError):
        recent_changes(StubAuditReader({}), NOW, limit=limit)


@pytest.mark.integration
def test_audit_reader_reads_what_the_audit_log_wrote(tmp_path: Path) -> None:
    log = JsonlAuditLog(tmp_path)
    first = _audit(2, AuditResult.INTENTO)
    written = [
        dataclasses.replace(first, ts=first.ts.astimezone(ZoneInfo("America/Lima"))),
        _audit(1, AuditResult.OK, reason="motivo"),
    ]
    for record in written:
        log.append(record)
    with (tmp_path / "audit-2026-11.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("{no es json\n")
        handle.write(json.dumps({"actor": "x"}) + "\n")
        last = (tmp_path / "audit-2026-11.jsonl").read_text(encoding="utf-8").splitlines()[1]
        handle.write(json.dumps({**json.loads(last), "result": "XYZ"}) + "\n\n")
    scan = JsonlAuditReader(tmp_path).read_month(2026, 11)
    assert list(scan.records) == written
    assert scan.records[0].reason is None
    assert scan.malformed == 3 and scan.files == 1


@pytest.mark.integration
def test_audit_reader_without_file_is_empty(tmp_path: Path) -> None:
    assert JsonlAuditReader(tmp_path).read_month(2026, 1) == AuditScan((), 0, 0)


@pytest.mark.integration
@pytest.mark.parametrize("month", [0, 13])
def test_audit_reader_rejects_an_invalid_month(tmp_path: Path, month: int) -> None:
    with pytest.raises(ValueError):
        JsonlAuditReader(tmp_path).read_month(2026, month)


# --- Agentes en solo lectura ------------------------------------------------------------------


def _usage(agent: str, *, outcome: UsageOutcome = UsageOutcome.OK, cid: str = "u1") -> UsageRecord:
    return UsageRecord(
        ts=NOW,
        correlation_id=cid,
        provider="nvidia",
        model="m",
        profile="ligero",
        agent=agent,
        ritual="checkin",
        attempt=1,
        latency_ms=10,
        tokens_prompt=10,
        tokens_completion=5,
        cost_usd=Decimal("0.001"),
        cost_status=CostStatus.ESTIMADO,
        outcome=outcome,
        http_status=200,
    )


@pytest.fixture
def shipped() -> YamlConfigSource:
    return YamlConfigSource(PROJECT / "config")


@pytest.mark.integration
def test_agents_show_config_effective_permissions_and_usage(shipped: YamlConfigSource) -> None:
    policy, catalog, configs = shipped.load_policy(), shipped.load_catalog(), shipped.load_agents()
    permissions = resolve_effective(
        policy,
        catalog,
        configs,
        tools=TOOL_WRITE_MODES,
        contracts=frozenset({"captura_v1", "checkin_v1", "plan_v1", "coach_v1"}),
    )
    usage = summarize(
        UsageScan((_usage("coach"), _usage("coach", outcome=UsageOutcome.TIMEOUT)), 0, 1)
    )
    overview = agents_overview(configs, permissions, policy, catalog, usage)
    agents = {agent.id: agent for agent in overview.agents}
    assert set(agents) == {"jefe", "coach"}
    coach = agents["coach"]
    assert coach.name == "Coach" and coach.status == "activo"
    assert coach.model == catalog.profiles[coach.profile].model
    assert "anexar_checkin" in coach.tools and "publicar_plan" not in coach.tools
    assert ("Diario/", "anexar_seccion", "check-ins") in {
        (w.root, w.mode, w.section) for w in coach.writes
    }
    assert coach.plan_writes == ()
    assert coach.usage is not None
    assert (coach.usage.calls, coach.usage.attempts, coach.usage.errors) == (1, 2, 1)
    assert agents["jefe"].usage is None
    assert agents["jefe"].plan_writes != ()
    assert overview.plan_owner == policy.plan_owner
    assert "_sistema/" in overview.forbidden_write
