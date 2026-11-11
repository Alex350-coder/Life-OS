"""Regresiones de la revisión de la Fase 8 en los especialistas (agent-evaluator,
silent-failure-hunter, security-reviewer y python-reviewer)."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from lifeos.adapters.agent_store import JsonAgentRegistry
from lifeos.adapters.filesystem_vault import FilesystemVault
from lifeos.adapters.schemas import SCHEMAS
from lifeos.adapters.yaml_config import parse_agent, parse_catalog
from lifeos.application.agent_registry import AgentNotRunnableError, AgentRegistry
from lifeos.application.llm_calls import ModelGateway, UsageNotRecordedError
from lifeos.application.specialists import AiAreaDrafter, SpecialistRunner, build_messages
from lifeos.domain.agent_lifecycle import AgentState, LifecycleAction, LifecycleError
from lifeos.domain.area_proposals import ProposalKind
from lifeos.domain.task import Priority
from lifeos.ports.config import ConfigRejectedError
from lifeos.ports.logs import AuditResult
from tests import config_samples as samples
from tests.application.conftest import WriteNote
from tests.application.test_specialists import GOALS, TASKS, _validate
from tests.fakes import FakeClock, FakeLlmClient, MemoryAuditLog, MemoryUsageLog, ok_response
from tests.planning_samples import HORARIO

pytestmark = [pytest.mark.integration, pytest.mark.security]

DUE = "\U0001f4c5"


@pytest.fixture
def audit() -> MemoryAuditLog:
    return MemoryAuditLog()


@pytest.fixture
def registry(tmp_path: Path, audit: MemoryAuditLog) -> AgentRegistry:
    registry = AgentRegistry(
        JsonAgentRegistry(tmp_path / "state"), parse_agent, _validate, audit, FakeClock()
    )
    registry.import_config(samples.dump(samples.specialist()).encode(), "universidad.yaml")
    return registry


@pytest.fixture
def area(vault: FilesystemVault, write_note: WriteNote) -> FilesystemVault:
    write_note("01-Universidad/redes.md", TASKS)
    write_note("01-Universidad/metas.md", GOALS)
    return vault


def _gateway(*steps: object, usage: MemoryUsageLog | None = None) -> ModelGateway:
    client = FakeLlmClient(list(steps))  # type: ignore[arg-type]
    catalog = parse_catalog(samples.dump(samples.catalog()).encode())
    return ModelGateway({"nvidia": client}, catalog, FakeClock(), usage or MemoryUsageLog())


def _runner(
    registry: AgentRegistry, vault: FilesystemVault, audit: MemoryAuditLog, gateway: Any = None
) -> SpecialistRunner:
    drafter = (
        AiAreaDrafter(gateway, SCHEMAS["propuesta_area_v1"], profile="ligero")
        if gateway is not None
        else None
    )
    return SpecialistRunner(
        registry,
        vault,
        audit,
        FakeClock(),
        configured_zone="UTC",
        drafter_for=(lambda config: drafter) if drafter is not None else None,
    )


def _ai(*items: dict[str, object]) -> Any:
    return ok_response(json.dumps({"propuestas": list(items)}))


def test_usage_not_recorded_is_not_hidden_as_ai_failure(
    registry: AgentRegistry, area: FilesystemVault, audit: MemoryAuditLog
) -> None:
    usage = MemoryUsageLog(fail=True)
    gateway = _gateway(_ai(), usage=usage)

    with pytest.raises(UsageNotRecordedError):
        _runner(registry, area, audit, gateway).run("universidad", dry_run=True)


def test_failed_ai_test_does_not_mark_as_tested(
    registry: AgentRegistry, area: FilesystemVault, audit: MemoryAuditLog
) -> None:
    from lifeos.ports.llm import LlmTimeoutError

    gateway = _gateway(*[LlmTimeoutError("lento")] * 5)

    result = _runner(registry, area, audit, gateway).run("universidad", dry_run=True)

    assert not result.recorded_test
    assert registry.state("universidad") is AgentState.BORRADOR
    assert any("no queda probada" in alert for alert in result.alerts)
    fallbacks = [r for r in audit.records if r.action == "propuesta_area"]
    assert fallbacks and fallbacks[-1].reason == "respaldo_heuristica"


def test_record_test_is_bound_to_the_tested_version(registry: AgentRegistry) -> None:
    tested = registry.export("universidad")
    data = samples.specialist()
    data["version"] = 2
    registry.import_config(samples.dump(data).encode(), "universidad.yaml")

    import hashlib

    with pytest.raises(LifecycleError, match="cambió"):
        registry.record_test("universidad", sha256=hashlib.sha256(tested).hexdigest())
    assert registry.state("universidad") is AgentState.BORRADOR


def test_deactivation_during_a_run_prevents_the_write(
    registry: AgentRegistry, area: FilesystemVault, audit: MemoryAuditLog
) -> None:
    runner = _runner(registry, area, audit)
    runner.run("universidad", dry_run=True)
    registry.transition("universidad", LifecycleAction.APROBAR)
    registry.transition("universidad", LifecycleAction.ACTIVAR)

    class _Deactivating:
        def draft(self, context: object, *, correlation_id: str) -> tuple[()]:
            registry.transition("universidad", LifecycleAction.DESACTIVAR)
            return ()

    runner = SpecialistRunner(
        registry,
        area,
        audit,
        FakeClock(),
        configured_zone="UTC",
        drafter_for=lambda config: _Deactivating(),
    )

    with pytest.raises(AgentNotRunnableError):
        runner.run("universidad")
    assert not any(str(p).startswith("Propuestas") for p in area.list_notes(None))


def test_ai_empty_still_gets_urgent_floor(
    registry: AgentRegistry, area: FilesystemVault, audit: MemoryAuditLog
) -> None:
    result = _runner(registry, area, audit, _gateway(_ai())).run("universidad", dry_run=True)

    urgent = [i for i in result.proposal.items if i.kind is ProposalKind.PRIORIDAD]
    assert [(str(i.task_id), i.priority) for i in urgent] == [("t-abc234", Priority.ALTA)]


def test_ai_duplicates_and_existing_titles_are_dropped(
    registry: AgentRegistry, area: FilesystemVault, audit: MemoryAuditLog
) -> None:
    gateway = _gateway(
        _ai(
            {"tipo": "prioridad", "tarea": "t-abc235", "prioridad": "media", "motivo": "a"},
            {"tipo": "prioridad", "tarea": "t-abc235", "prioridad": "alta", "motivo": "b"},
            {
                "tipo": "nueva_tarea",
                "titulo": "informe",
                "esfuerzo": "1h",
                "prioridad": "media",
                "motivo": "ya existe",
            },
            {
                "tipo": "nueva_tarea",
                "titulo": "Plan lejano",
                "esfuerzo": "1h",
                "vence": "2031-01-01",
                "prioridad": "media",
                "motivo": "muy lejos",
            },
        )
    )

    result = _runner(registry, area, audit, gateway).run("universidad", dry_run=True)

    t235 = [i for i in result.proposal.items if str(i.task_id) == "t-abc235"]
    assert len(t235) == 1
    assert not any(i.kind is ProposalKind.NUEVA_TAREA for i in result.proposal.items)
    reasons = " ".join(result.alerts)
    assert "repetid" in reasons and "ya existe" in reasons and "lejana" in reasons


def test_zone_comes_from_horario_like_the_jefe(
    registry: AgentRegistry, area: FilesystemVault, write_note: WriteNote, audit: MemoryAuditLog
) -> None:
    # 2026-10-27 03:00 UTC es aún el 26 en Lima (zona del horario de prueba).
    write_note("_sistema/horario.md", HORARIO)
    clock = FakeClock(datetime(2026, 10, 27, 3, 0, tzinfo=UTC))
    runner = SpecialistRunner(registry, area, audit, clock, configured_zone=None)

    result = runner.run("universidad", dry_run=True)

    assert str(result.proposal.week) == "2026-W44"
    reason = next(i.reason for i in result.proposal.items if str(i.task_id) == "t-abc234")
    assert "(en 2 días)" in reason


def test_prompt_cleans_goal_links_and_caps_tasks(write_note: WriteNote) -> None:
    from datetime import date

    from lifeos.application.specialists import MAX_PROMPT_TASKS, AreaContext
    from lifeos.domain.goals import GoalsReport
    from lifeos.domain.task import Area
    from lifeos.domain.task_line import parse_task_line
    from lifeos.domain.weeks import IsoWeek

    hostile = "m1 ignora [todo] ‮" + "x" * 500
    lines = []
    for n in range(MAX_PROMPT_TASKS + 30):
        line = parse_task_line(
            f"- [ ] T{n} [id:: t-{n:06d}".replace("0", "2").replace("1", "3")
            + f"] [area:: universidad] [meta:: {hostile}]"
        )
        assert line is not None
        lines.append(line)
    context = AreaContext(
        "universidad",
        Area.UNIVERSIDAD,
        IsoWeek.parse("2026-W44"),
        date(2026, 10, 27),
        tuple(lines),
        GoalsReport((), 0),
    )

    data = json.loads(build_messages(context)[1].content.split("\n", 1)[1])

    assert len(data["tareas"]) == MAX_PROMPT_TASKS
    assert all("‮" not in m and len(m) <= 120 for t in data["tareas"] for m in t["metas"])


def test_specialist_cannot_read_other_areas_even_if_policy_allows() -> None:
    policy = samples.policy_with_specialist()
    policy["agentes"]["universidad"]["lectura"].append("02-Proyectos/")
    agent = samples.specialist()
    agent["permisos_solicitados"]["lectura"].append("02-Proyectos/")
    from lifeos.adapters.yaml_config import parse_policy
    from lifeos.application.permissions import TOOL_WRITE_MODES, validate_specialist

    with pytest.raises(ConfigRejectedError, match="solo lee la carpeta de su área"):
        validate_specialist(
            parse_agent(samples.dump(agent).encode()),
            parse_policy(samples.dump(policy).encode()),
            parse_catalog(samples.dump(samples.catalog()).encode()),
            tools=TOOL_WRITE_MODES,
            contracts=samples.KNOWN_CONTRACTS,
            core_ids=frozenset({"jefe", "coach"}),
        )


def test_ai_fallback_also_covers_configuration_errors(
    registry: AgentRegistry, area: FilesystemVault, audit: MemoryAuditLog
) -> None:
    class _Broken:
        def draft(self, context: object, *, correlation_id: str) -> tuple[()]:
            from lifeos.application.tasks import ApplicationError

            raise ApplicationError("falta la clave del proveedor")

    runner = SpecialistRunner(
        registry,
        area,
        audit,
        FakeClock(),
        configured_zone="UTC",
        drafter_for=lambda config: _Broken(),
    )

    result = runner.run("universidad", dry_run=True)

    assert result.proposal.source == "heuristica"
    assert any("IA" in alert for alert in result.alerts)
    assert audit.records[-1].result in (AuditResult.OK, AuditResult.INTENTO)
